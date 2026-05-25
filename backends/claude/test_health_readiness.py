"""Unit tests for /health (liveness) vs /health/ready (readiness) split (#1608).

These tests verify the readiness-gating behaviour added when
``perform_initial_loads`` times out and sets ``_boot_degraded_reason``:

1. ``/health`` (liveness) returns 200 even when boot finished degraded —
   kubelet must NOT CrashLoopBackOff a pod that merely came up with empty
   MCP/agent_md/hooks. Boot-degraded state is surfaced informationally in
   the body, but does not flip the status code.
2. ``/health/ready`` (readiness) returns 503 while ``_ready`` is False
   (process still starting) OR while ``_boot_degraded_reason`` is set
   (boot finished degraded). Returns 200 only when fully ready and not
   degraded.

Operators upgrading from <=v0.5.0 must repoint their K8s readinessProbe
from ``/health`` to ``/health/ready`` to retain readiness gating
semantics — this is the BREAKING change introduced by the split.
"""

from __future__ import annotations

import asyncio
import os
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import MagicMock

_HERE = Path(__file__).resolve().parent
_REPO_ROOT = _HERE.parent.parent
sys.path.insert(0, str(_HERE))
sys.path.insert(0, str(_REPO_ROOT / "shared"))

os.environ.setdefault("AGENT_NAME", "claude-test")
os.environ.setdefault("AGENT_OWNER", "test")
os.environ.setdefault("AGENT_ID", "claude")

# Redirect log paths off /home/agent so log helpers don't ENOENT-spam.
import tempfile as _tempfile

_log_tmp_dir = _tempfile.mkdtemp(prefix="claude-test-")
os.environ.setdefault("CONVERSATION_LOG", os.path.join(_log_tmp_dir, "conversation.jsonl"))
os.environ.setdefault("TRACE_LOG", os.path.join(_log_tmp_dir, "tool-activity.jsonl"))


def _install_stub(name: str, mod: types.ModuleType) -> None:
    sys.modules.setdefault(name, mod)


# ---------------------------------------------------------------------------
# prometheus_client stub
# ---------------------------------------------------------------------------
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

    _pc.Counter = _Metric
    _pc.Gauge = _Metric
    _pc.Histogram = _Metric
    _pc.Info = _Metric
    _pc.CollectorRegistry = lambda *a, **kw: MagicMock()
    _pc.REGISTRY = MagicMock()
    _install_stub("prometheus_client", _pc)


# ---------------------------------------------------------------------------
# uvicorn stub (main imports it at module scope but we never .run it here)
# ---------------------------------------------------------------------------
if "uvicorn" not in sys.modules:
    _uv = types.ModuleType("uvicorn")

    class _Config:
        def __init__(self, *a, **kw):
            pass

    class _Server:
        def __init__(self, *a, **kw):
            self.started = False

        async def serve(self):
            return None

    _uv.Config = _Config
    _uv.Server = _Server
    _install_stub("uvicorn", _uv)


# ---------------------------------------------------------------------------
# a2a-sdk stubs — only what main.py touches at import.
# ---------------------------------------------------------------------------
def _install_a2a_stubs() -> None:
    if "a2a" in sys.modules:
        return
    _a2a = types.ModuleType("a2a")
    _server = types.ModuleType("a2a.server")
    _apps = types.ModuleType("a2a.server.apps")
    _rh = types.ModuleType("a2a.server.request_handlers")
    _tasks = types.ModuleType("a2a.server.tasks")
    _ae = types.ModuleType("a2a.server.agent_execution")
    _events = types.ModuleType("a2a.server.events")
    _types = types.ModuleType("a2a.types")
    _utils = types.ModuleType("a2a.utils")

    class _A2AStarletteApplication:
        def __init__(self, *a, **kw):
            pass

        def build(self, *a, **kw):
            from starlette.applications import Starlette

            return Starlette()

    class _DefaultRequestHandler:
        def __init__(self, *a, **kw):
            pass

    class _InMemoryTaskStore:
        pass

    class _AgentExecutorBase:
        pass

    class _RequestContext:
        pass

    class _EventQueue:
        pass

    class _AgentCard:
        def __init__(self, **kw):
            for k, v in kw.items():
                setattr(self, k, v)

    class _AgentSkill:
        def __init__(self, **kw):
            for k, v in kw.items():
                setattr(self, k, v)

    class _AgentCapabilities:
        def __init__(self, **kw):
            for k, v in kw.items():
                setattr(self, k, v)

    _apps.A2AStarletteApplication = _A2AStarletteApplication
    _rh.DefaultRequestHandler = _DefaultRequestHandler
    _tasks.InMemoryTaskStore = _InMemoryTaskStore
    _ae.AgentExecutor = _AgentExecutorBase
    _ae.RequestContext = _RequestContext
    _events.EventQueue = _EventQueue
    _types.AgentCard = _AgentCard
    _types.AgentSkill = _AgentSkill
    _types.AgentCapabilities = _AgentCapabilities
    _utils.new_agent_text_message = lambda text: {"text": text}

    _install_stub("a2a", _a2a)
    _install_stub("a2a.server", _server)
    _install_stub("a2a.server.apps", _apps)
    _install_stub("a2a.server.request_handlers", _rh)
    _install_stub("a2a.server.tasks", _tasks)
    _install_stub("a2a.server.agent_execution", _ae)
    _install_stub("a2a.server.events", _events)
    _install_stub("a2a.types", _types)
    _install_stub("a2a.utils", _utils)


_install_a2a_stubs()


# ---------------------------------------------------------------------------
# yaml stub — shared/hooks_engine.py imports ``yaml`` at module load.
# ---------------------------------------------------------------------------
if "yaml" not in sys.modules:
    _yaml = types.ModuleType("yaml")

    class _YAMLError(Exception):
        pass

    _yaml.YAMLError = _YAMLError
    _yaml.safe_load = lambda _s: {}
    _install_stub("yaml", _yaml)


# ---------------------------------------------------------------------------
# Stub backends/claude siblings that main.py imports but we don't need to
# exercise (executor, conversations, sqlite_task_store, session_binding,
# metrics).
# ---------------------------------------------------------------------------
def _stub_sibling(name: str, attrs: dict) -> None:
    if name in sys.modules:
        return
    m = types.ModuleType(name)
    for k, v in attrs.items():
        setattr(m, k, v)
    sys.modules[name] = m


_stub_sibling("executor", {"AgentExecutor": type("AgentExecutor", (), {})})
_stub_sibling(
    "conversations",
    {
        "auth_disabled_escape_hatch": lambda *a, **kw: False,
        "make_conversations_handler": lambda *a, **kw: lambda r: None,
        "make_trace_handler": lambda *a, **kw: lambda r: None,
    },
)
_stub_sibling("sqlite_task_store", {"SqliteTaskStore": type("SqliteTaskStore", (), {})})
_stub_sibling(
    "session_binding",
    {
        "derive_session_id": lambda *a, **kw: "sid",
        "set_fallback_counter": lambda *a, **kw: None,
    },
)


class _NoopMetric:
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


_metric = _NoopMetric()
_stub_sibling(
    "metrics",
    {
        "backend_event_loop_lag_seconds": _metric,
        "backend_health_checks_total": _metric,
        "backend_info": _metric,
        "backend_mcp_request_duration_seconds": _metric,
        "backend_mcp_requests_total": _metric,
        "backend_sdk_info": _metric,
        "backend_session_binding_fallback_total": _metric,
        "backend_session_caller_cardinality": _metric,
        "backend_startup_duration_seconds": _metric,
        "backend_task_restarts_total": _metric,
        "backend_up": _metric,
        "backend_uptime_seconds": _metric,
    },
)


# Now the import is safe.
import importlib  # noqa: E402

main = importlib.import_module("main")


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro) if False else asyncio.run(coro)


def _make_request():
    # Minimal Request stand-in — handlers don't read from it beyond passing
    # it through; they only use module globals.
    return MagicMock()


class HealthReadinessSplitTests(unittest.TestCase):
    def setUp(self):
        # Reset module-level state per test.
        main._ready = False
        main._boot_degraded_reason = None
        main._set_health_executor(
            types.SimpleNamespace(hooks_enforcement_mode_for_health=lambda: "enforcing")
        )

    def test_health_ready_503_when_boot_degraded(self):
        """/health/ready returns 503 when _boot_degraded_reason is set."""
        main._ready = True
        main._boot_degraded_reason = "initial_loads_timeout"
        resp = _run(main.health_ready(_make_request()))
        self.assertEqual(resp.status_code, 503)
        # Body should surface the degraded reason for operator visibility.
        import json

        body = json.loads(resp.body)
        self.assertEqual(body["status"], "degraded")
        self.assertEqual(body["boot_degraded"], "initial_loads_timeout")

    def test_health_ready_503_when_not_ready(self):
        """/health/ready returns 503 while _ready is False (still starting)."""
        main._ready = False
        main._boot_degraded_reason = None
        resp = _run(main.health_ready(_make_request()))
        self.assertEqual(resp.status_code, 503)

    def test_health_ready_200_when_fully_ready(self):
        """/health/ready returns 200 when ready and not degraded."""
        main._ready = True
        main._boot_degraded_reason = None
        resp = _run(main.health_ready(_make_request()))
        self.assertEqual(resp.status_code, 200)
        import json

        body = json.loads(resp.body)
        self.assertEqual(body["status"], "ready")

    def test_health_liveness_200_when_degraded(self):
        """/health (liveness) returns 200 even with boot_degraded_reason set.

        This is the critical liveness/readiness split: a degraded pod must
        stay alive (no CrashLoopBackOff) while being removed from Service
        endpoints via the separate readiness probe.
        """
        main._ready = True
        main._boot_degraded_reason = "initial_loads_timeout"
        resp = _run(main.health(_make_request()))
        self.assertEqual(resp.status_code, 200)
        import json

        body = json.loads(resp.body)
        self.assertEqual(body["status"], "ok")
        self.assertEqual(body["hooks_enforcement_mode"], "enforcing")
        # Degraded reason still surfaces informationally.
        self.assertEqual(body["boot_degraded"], "initial_loads_timeout")

    def test_health_liveness_200_when_not_ready(self):
        """/health (liveness) returns 200 with status="starting" while _ready is False.

        Per the 5e5d5a9b unification (and the original cycle-1 #1608
        intent), liveness is always-200-once-the-process-is-up: relying on
        kubelet's initialDelaySeconds to absorb pre-ready time would
        CrashLoopBackOff a slow-starting pod that exceeds the delay.
        Pre-ready state is surfaced via the body's status field; the
        separate /health/ready probe (validated above) gates Service
        endpoint removal during the same window.

        This test pinned the pre-unification 503 behaviour; it was the
        last drift point in claude's /health surface relative to
        openai/main.py:136 and gemini/main.py:136.
        """
        import json

        main._ready = False
        resp = _run(main.health(_make_request()))
        self.assertEqual(resp.status_code, 200)
        body = json.loads(resp.body)
        self.assertEqual(body["status"], "starting")
        self.assertEqual(body["hooks_enforcement_mode"], "enforcing")


if __name__ == "__main__":
    unittest.main()
