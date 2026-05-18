"""SQLite-backed TaskStore implementation using Python's built-in sqlite3.

Provides persistence across process restarts without requiring additional
dependencies (SQLAlchemy, aiosqlite, Redis).  All blocking I/O runs through
asyncio.to_thread so the event loop is never blocked.

Usage
-----
Configure via the TASK_STORE_PATH environment variable:

  TASK_STORE_PATH=/home/agent/logs/tasks.db

When the variable is unset, fall back to InMemoryTaskStore.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sqlite3
import threading
import time

from a2a.server.context import ServerCallContext
from a2a.server.tasks.task_store import TaskStore
from a2a.types import Task

logger = logging.getLogger(__name__)

# SQLite busy_timeout in milliseconds. Configures the amount of time a
# call will spin waiting for a BUSY lock to clear before raising
# OperationalError. 5000ms is a generous ceiling for the brief locks
# seen under normal concurrency (and rsync-style backups that briefly
# hold the main DB file). Overridable for extremely contended
# deployments via TASK_STORE_BUSY_TIMEOUT_MS.
_BUSY_TIMEOUT_MS = int(os.environ.get("TASK_STORE_BUSY_TIMEOUT_MS", "5000"))
# Retry budget for transient OperationalError ("database is locked",
# "disk I/O error"). Three retries with 100ms exponential backoff covers
# the typical SQLite contention envelope without masking a genuine
# outage. Overridable via TASK_STORE_RETRY_ATTEMPTS.
_RETRY_ATTEMPTS = max(1, int(os.environ.get("TASK_STORE_RETRY_ATTEMPTS", "3")))
_RETRY_BASE_SLEEP_S = 0.1

try:  # optional metric wiring — falls back silently when prom client absent
    from metrics import harness_task_store_errors_total  # type: ignore
except Exception:  # pragma: no cover - defensive
    harness_task_store_errors_total = None  # type: ignore


def _open_db(path: str) -> sqlite3.Connection:
    """Open (or create) the SQLite database and ensure the tasks table exists.

    Enables WAL journal mode and a busy_timeout so concurrent writers
    and brief filesystem-level contention (rsync backups, disk sync)
    cannot flip the store into the OperationalError path on every
    concurrent save (#704).
    """
    os.makedirs(os.path.dirname(path) if os.path.dirname(path) else ".", exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False)
    # WAL — readers never block writers and vice versa. Safe on POSIX
    # filesystems; the WAL journal is opened alongside the DB file.
    try:
        conn.execute("PRAGMA journal_mode=WAL")
    except sqlite3.OperationalError as e:
        # journal_mode=WAL is a no-op when the DB is on a filesystem
        # that cannot mmap the WAL (some network mounts). Log and
        # continue — the fallback delete-mode journal still works.
        logger.warning("SqliteTaskStore: journal_mode=WAL failed (%s) — continuing on default", e)
    conn.execute(f"PRAGMA busy_timeout={_BUSY_TIMEOUT_MS}")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS tasks (
            id TEXT PRIMARY KEY,
            data TEXT NOT NULL
        )
        """
    )
    conn.commit()
    return conn


def _retry_on_operational(op: str, fn):
    """Run fn with short exponential backoff on SQLite OperationalError (#704).

    Covers the "database is locked" / "database is locked (5)" and
    "disk I/O error (10)" flavours that are typically transient. A
    genuine corruption / misconfig still surfaces after the retry
    budget is spent. ``op`` is the logical operation label used for
    metrics.
    """
    last: Exception | None = None
    for attempt in range(_RETRY_ATTEMPTS):
        try:
            return fn()
        except sqlite3.OperationalError as e:
            last = e
            if harness_task_store_errors_total is not None:
                try:
                    harness_task_store_errors_total.labels(op=op, retry=str(attempt > 0).lower()).inc()
                except Exception:
                    pass
            if attempt + 1 >= _RETRY_ATTEMPTS:
                break
            time.sleep(_RETRY_BASE_SLEEP_S * (2**attempt))
    assert last is not None
    logger.error("SqliteTaskStore op %s failed after %d attempts: %s", op, _RETRY_ATTEMPTS, last)
    raise last


def _db_save(conn: sqlite3.Connection, task_id: str, data: str) -> None:
    def _do():
        conn.execute(
            "INSERT INTO tasks (id, data) VALUES (?, ?) ON CONFLICT(id) DO UPDATE SET data = excluded.data",
            (task_id, data),
        )
        conn.commit()

    _retry_on_operational("save", _do)


def _db_get(conn: sqlite3.Connection, task_id: str) -> str | None:
    def _do():
        row = conn.execute("SELECT data FROM tasks WHERE id = ?", (task_id,)).fetchone()
        return row[0] if row else None

    return _retry_on_operational("get", _do)


def _db_delete(conn: sqlite3.Connection, task_id: str) -> None:
    def _do():
        conn.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
        conn.commit()

    _retry_on_operational("delete", _do)


class SqliteTaskStore(TaskStore):
    """Persistent task store backed by a local SQLite database.

    Task state survives process restarts.  On startup, any tasks that were
    in-flight when the process was killed remain in the store with their last
    known status; clients polling for completion will eventually time out and
    receive a proper error rather than waiting indefinitely.
    """

    def __init__(self, path: str) -> None:
        self._path = path
        # Per-thread connections via threading.local (#1040). The previous
        # design shared one connection + an asyncio.Lock, which serialised
        # every save/get/delete across the entire asyncio.to_thread
        # executor. Under SQLite's WAL + busy_timeout (file-level
        # coordination) a per-thread connection is the standard pattern
        # for concurrent access; the outer asyncio.Lock added no
        # correctness and pessimised the retry path (the lock was held
        # across ~700ms of backoff inside _retry_on_operational).
        self._local = threading.local()
        self._opened_once = False
        self._opened_lock = threading.Lock()

    def _get_conn(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = _open_db(self._path)
            self._local.conn = conn
            # Log the first-ever open at INFO so the "SqliteTaskStore
            # opened at …" line still appears; subsequent per-thread
            # opens go to DEBUG to avoid log spam.
            with self._opened_lock:
                if not self._opened_once:
                    self._opened_once = True
                    logger.info("SqliteTaskStore opened at %s", self._path)
                else:
                    logger.debug("SqliteTaskStore opened per-thread connection at %s", self._path)
        return conn

    async def save(self, task: Task, context: ServerCallContext | None = None) -> None:
        """Persist *task* via an UPSERT on the ``tasks`` table.

        The Task is serialised to JSON via Pydantic, then handed to an
        ``asyncio.to_thread`` worker that resolves the per-thread
        connection and runs the INSERT … ON CONFLICT UPDATE under the
        :func:`_retry_on_operational` budget. ``context`` is accepted
        for protocol compatibility with :class:`TaskStore` and is
        unused.
        """
        data = task.model_dump_json()

        # Resolve the per-thread connection inside the worker thread
        # (#1040) — threading.local is keyed on the running thread, so
        # the lookup has to happen after to_thread has dispatched.
        def _op() -> None:
            _db_save(self._get_conn(), task.id, data)

        await asyncio.to_thread(_op)
        logger.debug("Task %s saved to SQLite store.", task.id)

    async def get(self, task_id: str, context: ServerCallContext | None = None) -> Task | None:
        """Return the stored Task with id *task_id*, or ``None`` if absent.

        Reads a single row via ``asyncio.to_thread`` and reconstructs
        the Task with :meth:`Task.model_validate_json`. A missing row
        is logged at DEBUG and surfaces as ``None`` rather than an
        exception. ``context`` is accepted for protocol compatibility
        with :class:`TaskStore` and is unused.
        """

        def _op() -> str | None:
            return _db_get(self._get_conn(), task_id)

        raw = await asyncio.to_thread(_op)
        if raw is None:
            logger.debug("Task %s not found in SQLite store.", task_id)
            return None
        task = Task.model_validate_json(raw)
        logger.debug("Task %s retrieved from SQLite store.", task_id)
        return task

    async def delete(self, task_id: str, context: ServerCallContext | None = None) -> None:
        """Remove the row for *task_id* if present.

        Deleting an absent row is a no-op (SQLite simply matches zero
        rows). ``context`` is accepted for protocol compatibility with
        :class:`TaskStore` and is unused.
        """

        def _op() -> None:
            _db_delete(self._get_conn(), task_id)

        await asyncio.to_thread(_op)
        logger.debug("Task %s deleted from SQLite store.", task_id)
