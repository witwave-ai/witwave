"""SQLite-backed TaskStore implementation using Python's built-in sqlite3.

Provides persistence across process restarts without requiring additional
dependencies (SQLAlchemy, aiosqlite, Redis).  All blocking I/O runs through
``asyncio.to_thread`` so the event loop is never blocked.

Concurrency (#726)
------------------
The original implementation serialised every op through a single
``sqlite3.Connection`` guarded by a single ``asyncio.Lock``, which meant a
slow read could block every writer and vice versa. This version keeps WAL
journaling + a 5s ``busy_timeout`` but drops the asyncio.Lock in favour of
per-thread connections. Each worker thread (created on demand by
``asyncio.to_thread``) gets its own ``sqlite3.Connection`` stored in a
``threading.local`` carrier; SQLite's native file locks (readers-don't-
block-readers under WAL, writers wait up to ``busy_timeout``) provide the
serialisation without forcing user-space round-trips.

Usage
-----
Configure via the ``TASK_STORE_PATH`` environment variable::

  TASK_STORE_PATH=/home/agent/logs/tasks.db

When the variable is unset, fall back to ``InMemoryTaskStore``.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sqlite3
import threading
import time

import metrics as _metrics
from a2a.server.context import ServerCallContext
from a2a.server.tasks.task_store import TaskStore
from a2a.types import Task

logger = logging.getLogger(__name__)


def _metric_labels() -> dict:
    """Match the {agent, agent_id, backend} label set the other backends use."""
    agent_name = os.environ.get("AGENT_NAME", "local-agent")
    return {
        "agent": os.environ.get("AGENT_OWNER", agent_name),
        "agent_id": os.environ.get("AGENT_ID", "codex"),
        "backend": "codex",
    }


def _observe_lock_wait(op: str, wait_seconds: float) -> None:
    """Record lock-acquisition wait time for a store op (#1753).

    Mirrors the helpers in backends/claude/sqlite_task_store.py and
    backends/gemini/sqlite_task_store.py so cross-backend dashboards
    that union backend_sqlite_task_store_lock_wait_seconds on
    (agent, agent_id, backend) are no longer flatlined for codex.

    Codex uses per-thread connections (#726) without a user-space
    asyncio.Lock, so the recorded wait is the time between asking the
    threadpool to run the op and the worker actually starting the SQL —
    which captures both threadpool queuing and any SQLite busy_timeout
    contention before the connection acquires its file lock.
    """
    hist = _metrics.backend_sqlite_task_store_lock_wait_seconds
    if hist is None:
        return
    try:
        hist.labels(**_metric_labels(), op=op).observe(wait_seconds)
    except Exception:  # pragma: no cover — never let metrics break persistence
        logger.debug("backend_sqlite_task_store_lock_wait_seconds observe failed", exc_info=True)


# Default SQLite busy_timeout. Tunable per-deployment via env so a slow NFS
# volume or a pathological batch of writes can be given more headroom
# without editing code.
_BUSY_TIMEOUT_MS = int(os.environ.get("SQLITE_TASK_STORE_BUSY_TIMEOUT_MS", "5000"))


def _configure_connection(conn: sqlite3.Connection) -> None:
    """Apply PRAGMAs shared by every per-thread connection (#726).

    WAL must be set at least once against the file (it's persistent), but
    applying it on every open is cheap and idempotent. ``busy_timeout`` and
    ``synchronous`` *are* per-connection and must be re-applied.
    """
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(f"PRAGMA busy_timeout={_BUSY_TIMEOUT_MS}")
    conn.execute("PRAGMA synchronous=NORMAL")


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS tasks (
            id TEXT PRIMARY KEY,
            data TEXT NOT NULL
        )
        """
    )
    conn.commit()


class SqliteTaskStore(TaskStore):
    """Persistent task store backed by a local SQLite database.

    Task state survives process restarts.  On startup, any tasks that were
    in-flight when the process was killed remain in the store with their
    last known status; clients polling for completion will eventually time
    out and receive a proper error rather than waiting indefinitely.

    Under the hood each worker thread (``asyncio.to_thread`` spins up a
    dedicated ``concurrent.futures.ThreadPoolExecutor`` worker) is given
    its own ``sqlite3.Connection`` via a ``threading.local`` carrier so
    readers and writers no longer queue behind a single user-space lock
    (#726). SQLite's own file locks (WAL + ``busy_timeout``) provide
    correctness.
    """

    def __init__(self, path: str) -> None:
        self._path = path
        self._tls = threading.local()
        # One-shot init guard. The first connection opened is used to
        # create the schema + log startup pragmas; subsequent threads
        # skip the logging (schema creation remains IF NOT EXISTS).
        self._init_lock = threading.Lock()
        self._initialised = False

    def _get_conn(self) -> sqlite3.Connection:
        conn = getattr(self._tls, "conn", None)
        if conn is not None:
            return conn
        # First-time open for this thread. Ensure the directory exists
        # before sqlite3.connect touches the filesystem.
        os.makedirs(
            os.path.dirname(self._path) if os.path.dirname(self._path) else ".",
            exist_ok=True,
        )
        conn = sqlite3.connect(self._path, check_same_thread=False)
        _configure_connection(conn)
        with self._init_lock:
            if not self._initialised:
                _ensure_schema(conn)
                self._initialised = True
                logger.info(
                    "SqliteTaskStore opened at %s (WAL, busy_timeout=%sms, per-thread connection pool)",
                    self._path,
                    _BUSY_TIMEOUT_MS,
                )
            else:
                # Still verify schema from this thread's connection.
                # Cheap — IF NOT EXISTS is a no-op when the table is there.
                _ensure_schema(conn)
        self._tls.conn = conn
        return conn

    def _save_sync(self, task_id: str, data: str) -> None:
        conn = self._get_conn()
        conn.execute(
            "INSERT INTO tasks (id, data) VALUES (?, ?) ON CONFLICT(id) DO UPDATE SET data = excluded.data",
            (task_id, data),
        )
        conn.commit()

    def _get_sync(self, task_id: str) -> str | None:
        conn = self._get_conn()
        row = conn.execute("SELECT data FROM tasks WHERE id = ?", (task_id,)).fetchone()
        return row[0] if row else None

    def _delete_sync(self, task_id: str) -> None:
        conn = self._get_conn()
        conn.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
        conn.commit()

    async def save(self, task: Task, context: ServerCallContext | None = None) -> None:
        """Persist *task* via UPSERT on the ``tasks`` table.

        Serialises the Task to JSON with Pydantic, then dispatches to
        ``asyncio.to_thread`` where the worker resolves the
        per-thread connection (``threading.local``) and runs INSERT …
        ON CONFLICT UPDATE. The histogram label ``save`` records
        threadpool-queue + SQLite ``busy_timeout`` wait time measured
        inside the worker callable (#1753), not round-trip latency.
        ``context`` is accepted for :class:`TaskStore` protocol
        compatibility and unused.
        """
        data = task.model_dump_json()
        # #1753: record threadpool-queue + SQLite busy_timeout wait by
        # measuring from before to_thread dispatch until the worker
        # acquires the connection and is about to execute. We use a
        # callable wrapper that captures the wait at the start of the
        # synchronous function rather than total round-trip so the
        # histogram represents waiting (not the SQL op's own duration).
        _wait_start = time.perf_counter()

        def _save_with_wait(task_id: str, payload: str) -> None:
            _observe_lock_wait("save", time.perf_counter() - _wait_start)
            self._save_sync(task_id, payload)

        await asyncio.to_thread(_save_with_wait, task.id, data)
        logger.debug("Task %s saved to SQLite store.", task.id)

    async def get(self, task_id: str, context: ServerCallContext | None = None) -> Task | None:
        """Return the stored Task with id *task_id*, or ``None`` if absent.

        Reads a single row in a worker thread that uses its own
        per-thread connection. Missing rows log at DEBUG and return
        ``None`` instead of raising; the lock-wait histogram is
        labelled ``get``. ``context`` is accepted for protocol
        compatibility and unused.
        """
        _wait_start = time.perf_counter()

        def _get_with_wait(tid: str) -> str | None:
            _observe_lock_wait("get", time.perf_counter() - _wait_start)
            return self._get_sync(tid)

        raw = await asyncio.to_thread(_get_with_wait, task_id)
        if raw is None:
            logger.debug("Task %s not found in SQLite store.", task_id)
            return None
        task = Task.model_validate_json(raw)
        logger.debug("Task %s retrieved from SQLite store.", task_id)
        return task

    async def delete(self, task_id: str, context: ServerCallContext | None = None) -> None:
        """Remove the row for *task_id* if present.

        Dispatches the DELETE to a worker thread on its per-thread
        connection; deleting an absent row is a no-op. The lock-wait
        histogram is labelled ``delete``. ``context`` is accepted for
        protocol compatibility and unused.
        """
        _wait_start = time.perf_counter()

        def _delete_with_wait(tid: str) -> None:
            _observe_lock_wait("delete", time.perf_counter() - _wait_start)
            self._delete_sync(tid)

        await asyncio.to_thread(_delete_with_wait, task_id)
        logger.debug("Task %s deleted from SQLite store.", task_id)
