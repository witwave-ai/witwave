"""Unit test for #1753 — openai sqlite_task_store records lock_wait_seconds.

Mirrors the parity gap that existed before #1753: openai used to register
``backend_sqlite_task_store_lock_wait_seconds`` in metrics.py but never
call ``.observe()``, so cross-backend dashboards saw a permanent zero
on the openai bucket. The fix wires ``_observe_lock_wait`` around the
to_thread-dispatched save / get / delete ops; this test asserts the
helper is invoked on each path.

Stubs follow the same shape as
``backends/claude/test_sqlite_task_store_close_race.py`` so the test
runs without the real a2a / prometheus_client wheels installed.
"""

from __future__ import annotations

import json as _json
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

_HERE = Path(__file__).resolve().parent
_REPO_ROOT = _HERE.parent.parent
sys.path.insert(0, str(_HERE))
sys.path.insert(0, str(_REPO_ROOT / "shared"))

os.environ.setdefault("AGENT_NAME", "openai-test")
os.environ.setdefault("AGENT_OWNER", "test")
os.environ.setdefault("AGENT_ID", "openai")


# prometheus_client stub.
if "prometheus_client" not in sys.modules:
    _pc = types.ModuleType("prometheus_client")

    class _Metric:
        def __init__(self, *a, **kw):
            pass

        def labels(self, *a, **kw):
            return self

        def inc(self, *a, **kw):
            pass

        def dec(self, *a, **kw):
            pass

        def set(self, *a, **kw):
            pass

        def observe(self, *a, **kw):
            pass

        def info(self, *a, **kw):
            pass

        def set_function(self, *a, **kw):
            pass

    for _name in (
        "Counter",
        "Gauge",
        "Histogram",
        "Summary",
        "Info",
        "CollectorRegistry",
    ):
        setattr(_pc, _name, _Metric)

    def _generate_latest(*a, **kw):
        return b""

    _pc.generate_latest = _generate_latest
    _pc.CONTENT_TYPE_LATEST = "text/plain"
    sys.modules["prometheus_client"] = _pc


# a2a stubs.
class _StubServerCallContext:
    pass


class _StubTaskStore:
    pass


class _StubTask:
    def __init__(self, id: str, payload: str = "") -> None:
        self.id = id
        self.payload = payload

    def model_dump_json(self) -> str:
        return _json.dumps({"id": self.id, "payload": self.payload})

    @classmethod
    def model_validate_json(cls, raw: str) -> _StubTask:
        obj = _json.loads(raw)
        return cls(id=obj["id"], payload=obj.get("payload", ""))


def _ensure_module(name: str) -> types.ModuleType:
    mod = sys.modules.get(name)
    if mod is None:
        mod = types.ModuleType(name)
        sys.modules[name] = mod
    return mod


_a2a = _ensure_module("a2a")
_a2a_server = _ensure_module("a2a.server")
_a2a_server_context = _ensure_module("a2a.server.context")
_ensure_module("a2a.server.tasks")
_a2a_server_tasks_ts = _ensure_module("a2a.server.tasks.task_store")
_a2a_types = _ensure_module("a2a.types")

if not hasattr(_a2a_server_context, "ServerCallContext"):
    _a2a_server_context.ServerCallContext = _StubServerCallContext
if not hasattr(_a2a_server_tasks_ts, "TaskStore"):
    _a2a_server_tasks_ts.TaskStore = _StubTaskStore
if not hasattr(_a2a_types, "Task"):
    _a2a_types.Task = _StubTask


# metrics stub — only need the histogram surface to exist.
_metrics_mod = sys.modules.get("metrics")
if _metrics_mod is None:
    _metrics_mod = types.ModuleType("metrics")
    sys.modules["metrics"] = _metrics_mod
if not hasattr(_metrics_mod, "backend_sqlite_task_store_lock_wait_seconds"):
    _metrics_mod.backend_sqlite_task_store_lock_wait_seconds = None


sys.modules.pop("sqlite_task_store", None)
import sqlite_task_store as sts  # noqa: E402
from a2a.types import Task  # noqa: E402


class OpenAILockWaitObservedTests(unittest.IsolatedAsyncioTestCase):
    """Verify save / get / delete each invoke _observe_lock_wait once."""

    async def test_save_get_delete_observe_lock_wait(self):
        observed: list[tuple[str, float]] = []

        def _capture(op: str, wait_seconds: float) -> None:
            observed.append((op, wait_seconds))

        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "tasks.db")
            store = sts.SqliteTaskStore(path)

            with patch.object(sts, "_observe_lock_wait", side_effect=_capture):
                t = Task(id="t1", payload="p")
                await store.save(t)
                got = await store.get("t1")
                self.assertIsNotNone(got)
                await store.delete("t1")

        ops = sorted(op for op, _ in observed)
        self.assertEqual(ops, ["delete", "get", "save"], f"observed={observed}")
        for op, wait in observed:
            self.assertGreaterEqual(wait, 0.0, f"{op} wait must be non-negative")


if __name__ == "__main__":
    unittest.main()
