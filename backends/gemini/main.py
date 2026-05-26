import asyncio
import hashlib
import hmac as hmac_mod
import logging
import os
import time
from contextlib import AsyncExitStack, asynccontextmanager
from datetime import datetime, timezone

import prometheus_client
import uvicorn
from a2a.server.apps import A2AStarletteApplication
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.tasks import InMemoryTaskStore
from a2a.types import (
    AgentCapabilities,
    AgentCard,
    AgentSkill,
)
from conversations import (
    auth_disabled_escape_hatch,
    make_conversations_handler,
    make_trace_handler,
)
from env import parse_bool_env
from executor import AgentExecutor
from mcp_body_cap import read_capped_body  # #1674
from metrics import (
    backend_event_loop_lag_seconds,
    backend_health_checks_total,
    backend_info,
    backend_mcp_request_duration_seconds,
    backend_mcp_requests_total,
    backend_sdk_info,
    backend_session_binding_fallback_total,
    backend_startup_duration_seconds,
    backend_task_restarts_total,
    backend_up,
    backend_uptime_seconds,
)
from session_binding import derive_session_id
from session_binding import set_fallback_counter as _set_session_binding_fallback_counter
from sqlite_task_store import SqliteTaskStore
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Mount, Route
from validation import parse_max_tokens

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

AGENT_NAME = os.environ.get("AGENT_NAME", "gemini")
AGENT_HOST = os.environ.get("AGENT_HOST", "0.0.0.0")
BACKEND_PORT = int(os.environ.get("BACKEND_PORT", "8000"))
AGENT_URL = os.environ.get("AGENT_URL", f"http://localhost:{BACKEND_PORT}/")
AGENT_VERSION = os.environ.get("AGENT_VERSION", "0.1.0")
CONVERSATION_LOG = os.environ.get("CONVERSATION_LOG", "/home/agent/logs/conversation.jsonl")
TRACE_LOG = os.environ.get("TRACE_LOG", "/home/agent/logs/tool-activity.jsonl")
# gemini surfaces AFC tool_use / tool_result rows on tool-activity.jsonl (#640).
# Audit rows would share the same file with event_type='tool_audit' once
# a PostToolUse hook path is wired in (AFC runs inside the SDK — see #640).
AGENT_OWNER = os.environ.get("AGENT_OWNER") or AGENT_NAME
# #1340: fall back to HOSTNAME for uniqueness (see claude comment).
AGENT_ID = os.environ.get("AGENT_ID") or os.environ.get("HOSTNAME") or "gemini"
_BACKEND_ID = "gemini"
metrics_enabled = parse_bool_env("METRICS_ENABLED")
WORKER_MAX_RESTARTS = int(os.environ.get("WORKER_MAX_RESTARTS", "5"))
CONVERSATIONS_AUTH_TOKEN = os.environ.get("CONVERSATIONS_AUTH_TOKEN", "")

_ready: bool = False
_startup_mono: float = 0.0
start_time: datetime = datetime.now(timezone.utc)


def load_agent_description() -> str:
    """Return the agent's description string for the A2A agent card / MCP tool description.

    Prefers the contents of ``/home/agent/.gemini/agent-card.md`` (mounted
    from a ConfigMap or volume), falling back to the ``AGENT_DESCRIPTION``
    env var and then a generic ``"A Gemini backend agent."`` literal so
    the card is always populated even when the file mount is missing.
    Mirror of the claude / codex backends' loader so cross-backend
    behaviour stays uniform.
    """
    try:
        with open("/home/agent/.gemini/agent-card.md") as f:
            return f.read()
    except OSError:
        return os.environ.get("AGENT_DESCRIPTION", "A Gemini backend agent.")


def build_agent_card() -> AgentCard:
    """Return the A2A :class:`AgentCard` advertised at ``/.well-known``.

    Uses the module-level ``AGENT_NAME`` / ``AGENT_URL`` / ``AGENT_VERSION``
    constants (resolved from env at import time) and the description from
    :func:`load_agent_description`. Declares a single ``general`` skill and
    sets ``streaming=False``: the per-chunk Message emission path was
    removed for blocking-call correctness — see the equivalent comment
    in ``backends/claude/main.py``.
    """
    return AgentCard(
        name=AGENT_NAME,
        description=load_agent_description(),
        url=AGENT_URL,
        version=AGENT_VERSION,
        # streaming=False — see backends/claude/main.py for the
        # rationale. Per-chunk Message emission was removed for
        # blocking-call correctness; the agent card now reflects
        # that wire behaviour honestly.
        capabilities=AgentCapabilities(streaming=False),
        default_input_modes=["text/plain"],
        default_output_modes=["text/plain"],
        skills=[
            AgentSkill(
                id="general",
                name="General",
                description="General-purpose task execution via Gemini.",
                tags=["general", "gemini"],
            )
        ],
    )


# #1099: expose subsystem counters on /health for richer liveness signal.
# Populated by main() after AgentExecutor construction so the handler can
# read from it without needing the executor instance plumbed through.
_health_executor_ref: "AgentExecutor | None" = None


def _set_health_executor(executor: "AgentExecutor") -> None:
    """Register the running executor for /health subsystem introspection (#1099)."""
    global _health_executor_ref
    _health_executor_ref = executor


async def health_start(request: Request) -> JSONResponse:
    """Kubernetes STARTUP probe handler — serves ``/health/start`` (#1686).

    Returns HTTP 200 once :data:`_ready` has been flipped to True by
    :func:`_set_ready_when_started` (i.e. uvicorn has bound the listener)
    and HTTP 503 with ``{"status": "starting"}`` before that. Mirrors the
    harness + claude backends' ``health_start`` so the three-probe
    contract is uniform across the platform. Increments
    ``backend_health_checks_total`` with ``probe="start"``.
    """
    # #1686: /health/start is the STARTUP probe — 200 once _ready, 503
    # with {"status": "starting"} while warming up. Closes the
    # three-probe parity gap with the harness (docs/product-vision.md:74).
    if backend_health_checks_total is not None:
        backend_health_checks_total.labels(
            agent=AGENT_OWNER, agent_id=AGENT_ID, backend=_BACKEND_ID, probe="start"
        ).inc()
    if _ready:
        return JSONResponse({"status": "ok"})
    return JSONResponse({"status": "starting"}, status_code=503)


async def health(request: Request) -> JSONResponse:
    """Kubernetes LIVENESS probe handler — serves ``/health`` and ``/health/live`` (#1608, #1672).

    Returns HTTP 200 unconditionally so kubelet does not CrashLoopBackOff
    a degraded pod — pre-ready state is surfaced via the body's
    ``status`` field (``"starting"`` vs ``"ok"``) rather than the HTTP
    code. For readiness gating point K8s readinessProbe at
    :func:`health_ready` instead. Body also exposes:

    - ``hooks_enforcement_mode`` — currently always ``"skeleton"`` because
      Gemini's automatic function calling (AFC) bypasses the hooks engine
      (#736); flips to a real mode once #640 disables AFC and hand-rolls
      the tool loop.
    - ``mcp_servers_active`` / ``session_cache_utilization_percent`` /
      ``history_save_failed`` — subsystem dimensions read from the
      executor registered via :func:`_set_health_executor` (#1099),
      snapshotted via ``dict(...)`` / ``set(...)`` before counting to
      stay safe against concurrent mutators (#1515). Each read is
      best-effort; a degraded executor still answers the probe.
    - ``agent_owner`` / ``agent_id`` — metric-label join parity (#1341).

    Increments ``backend_health_checks_total`` with ``probe="health"``.
    """
    if backend_health_checks_total is not None:
        backend_health_checks_total.labels(
            agent=AGENT_OWNER, agent_id=AGENT_ID, backend=_BACKEND_ID, probe="health"
        ).inc()
    # Hook enforcement mode (#736). Surfacing it here lets dashboards,
    # operators, and smoke tests confirm whether PreToolUse rules are
    # actually enforced on this backend. Gemini's AFC currently
    # bypasses the hooks engine so the value is "skeleton" until #640
    # disables AFC and hand-rolls the tool loop.
    hook_mode = "skeleton"
    # #1099: subsystem dimensions for parity with claude's /health.
    # Snapshot under best-effort try/except so a degraded executor can
    # still answer the liveness probe.
    mcp_servers_active = 0
    session_cache_utilization_percent = 0.0
    history_save_failed_count = 0
    _exec = _health_executor_ref
    if _exec is not None:
        try:
            mcp_servers_active = len(getattr(_exec, "_live_mcp_servers", []) or [])
        except Exception:
            pass
        try:
            from executor import MAX_SESSIONS as _MAX

            _sessions = getattr(_exec, "_sessions", None)
            if _sessions is not None and _MAX > 0:
                # #1515: snapshot via dict(...) before len() so a
                # concurrent mutator (session touch / LRU evict) can't
                # reshape the dict mid-read. CPython's dict.__len__ is
                # currently atomic under the GIL but the invariant is
                # not language-guaranteed; this keeps the health handler
                # safe against future threaded paths and against the
                # general "RuntimeError: dictionary changed size during
                # iteration" class if we ever swap to an iterator view.
                session_cache_utilization_percent = round((len(dict(_sessions)) / _MAX) * 100.0, 2)
        except Exception:
            pass
        try:
            _hsf = getattr(_exec, "_history_save_failed", set()) or set()
            # Same snapshot pattern for the failed-save set (#1515).
            history_save_failed_count = len(set(_hsf))
        except Exception:
            pass
    subsystem = {
        "hooks_enforcement_mode": hook_mode,
        "mcp_servers_active": mcp_servers_active,
        "session_cache_utilization_percent": session_cache_utilization_percent,
        "history_save_failed": history_save_failed_count,
    }
    # #1608 + #1672: /health is the LIVENESS probe — it returns 200 as
    # soon as the process is up so kubelet does not CrashLoopBackOff a
    # pod that is merely degraded. For readiness gating (i.e. removing a
    # degraded pod from Service endpoints) point K8s readinessProbe at
    # /health/ready instead. README documents /health/ready as universal
    # but gemini didn't implement it pre-#1672 — operators relied on
    # /health which conflated liveness with readiness.
    elapsed = (datetime.now(timezone.utc) - start_time).total_seconds()
    # #1341: surface agent_owner + agent_id for metric-label parity.
    return JSONResponse(
        {
            "status": "ok" if _ready else "starting",
            "agent": AGENT_NAME,
            "agent_owner": AGENT_OWNER,
            "agent_id": AGENT_ID,
            "uptime_seconds": elapsed,
            **subsystem,
        }
    )


async def health_ready(request: Request) -> JSONResponse:
    """Kubernetes READINESS probe handler — serves ``/health/ready`` (#1608, #1672).

    Returns HTTP 503 when :data:`_ready` is False — either still starting
    OR readiness was dropped by :func:`_guarded` after a critical-task
    crash (the gemini variant of _guarded flips ``_ready=False`` on
    persistent watcher failures). Returns HTTP 200 with ``status="ready"``
    once the gate passes. Operators upgrading from <=v0.5.0 must repoint
    their K8s readinessProbe at this path; previously ``/health``
    conflated liveness with readiness. Increments
    ``backend_health_checks_total`` with ``probe="ready"``.
    """
    # #1608 + #1672: /health/ready is the READINESS probe — it returns
    # 503 when ``_ready`` is False (still starting or readiness dropped
    # by a critical-task crash via _guarded()) and 200 once fully ready.
    # Mirror of the cycle-1 claude #1608 fix and the codex #1672 follow-
    # up; gemini's /health previously conflated liveness with readiness
    # so kubelet could CrashLoopBackOff a pod that should only have been
    # removed from Service endpoints.
    if backend_health_checks_total is not None:
        backend_health_checks_total.labels(
            agent=AGENT_OWNER, agent_id=AGENT_ID, backend=_BACKEND_ID, probe="ready"
        ).inc()
    if not _ready:
        return JSONResponse({"status": "starting"}, status_code=503)
    elapsed = (datetime.now(timezone.utc) - start_time).total_seconds()
    return JSONResponse(
        {
            "status": "ready",
            "agent": AGENT_NAME,
            "agent_owner": AGENT_OWNER,
            "agent_id": AGENT_ID,
            "uptime_seconds": elapsed,
        }
    )


@asynccontextmanager
async def _sub_app_lifespan(app):
    """Drive the ASGI lifespan protocol on a sub-app."""
    loop = asyncio.get_running_loop()
    startup: asyncio.Future[bool] = loop.create_future()
    do_shutdown: asyncio.Event = asyncio.Event()
    shutdown: asyncio.Future[None] = loop.create_future()

    async def receive() -> dict:
        if not startup.done():
            return {"type": "lifespan.startup"}
        await do_shutdown.wait()
        return {"type": "lifespan.shutdown"}

    async def send(message: dict) -> None:
        t = message.get("type", "")
        if t == "lifespan.startup.complete" and not startup.done():
            startup.set_result(True)
        elif t == "lifespan.startup.failed" and not startup.done():
            startup.set_exception(RuntimeError(message.get("message", "lifespan startup failed")))
        elif t == "lifespan.shutdown.complete" and not shutdown.done():
            shutdown.set_result(None)

    async def _run() -> None:
        try:
            await app({"type": "lifespan", "asgi": {"version": "3.0"}}, receive, send)
        except Exception as exc:
            if not startup.done():
                startup.set_exception(exc)
            if not shutdown.done():
                shutdown.set_result(None)
        finally:
            # App exited without sending startup.complete → no lifespan support.
            if not startup.done():
                startup.set_result(False)
            if not shutdown.done():
                shutdown.set_result(None)

    task = asyncio.create_task(_run())
    try:
        supported = await startup
    except Exception:
        # Sub-app startup failed; cancel and drain the _run task before propagating
        # so we don't leak a suspended coroutine waiting on do_shutdown.wait().
        # #1512: wrap the await in asyncio.shield so an outer cancellation
        # can't abandon the task reference mid-teardown. Previously a caller
        # cancelling us during this branch would propagate CancelledError
        # into `await task` and leave _run still running, holding the
        # sub-app lifespan open and leaking the asyncio.Task object.
        task.cancel()
        try:
            await asyncio.shield(task)
        except (asyncio.CancelledError, Exception):
            pass
        raise
    if not supported:
        # App does not implement lifespan — proceed normally, matching agent/main.py behaviour.
        yield
        return

    try:
        yield
    finally:
        do_shutdown.set()
        try:
            await shutdown
        except Exception as exc:
            logger.warning("Sub-app lifespan shutdown error: %s", exc)
        # Drain _run then propagate any captured exception (#1197).
        try:
            await task
        except Exception:
            pass
        _task_exc: BaseException | None = None
        if task.done() and not task.cancelled():
            try:
                _task_exc = task.exception()
            except (asyncio.CancelledError, asyncio.InvalidStateError):
                _task_exc = None
        if _task_exc is not None:
            raise _task_exc


async def _guarded(coro_fn, *args, restart_delay: float = 5.0, critical: bool = False) -> None:
    """Run a coroutine function in a restart loop, catching unexpected exceptions.

    The consecutive restart counter resets whenever a run lasts at least restart_delay
    seconds, so transient failures spread over time do not accumulate toward the threshold.
    """
    global _ready
    consecutive_restarts = 0
    while True:
        _attempt_start = time.monotonic()
        try:
            await coro_fn(*args)
            return
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if time.monotonic() - _attempt_start >= restart_delay:
                if critical and consecutive_restarts >= WORKER_MAX_RESTARTS and not _ready:
                    logger.info(
                        f"Task {coro_fn.__name__!r} ran cleanly for >= {restart_delay}s — restoring agent ready"
                    )
                    _ready = True
                consecutive_restarts = 0
            consecutive_restarts += 1
            logger.error(
                f"Task {coro_fn.__name__!r} crashed: {exc!r} — restarting in {restart_delay}s (consecutive restart #{consecutive_restarts})"  # noqa: E501
            )
            if backend_task_restarts_total is not None:
                backend_task_restarts_total.labels(
                    agent=AGENT_OWNER, agent_id=AGENT_ID, backend=_BACKEND_ID, task=coro_fn.__name__
                ).inc()
            if critical and consecutive_restarts >= WORKER_MAX_RESTARTS:
                logger.error(
                    f"Task {coro_fn.__name__!r} has crashed {consecutive_restarts} consecutive times — marking agent not ready"  # noqa: E501
                )
                _ready = False
            await asyncio.sleep(restart_delay)


async def _event_loop_monitor() -> None:
    _interval = 1.0
    while True:
        _before = time.monotonic()
        await asyncio.sleep(_interval)
        lag = time.monotonic() - _before - _interval
        if lag > 0 and backend_event_loop_lag_seconds is not None:
            backend_event_loop_lag_seconds.labels(agent=AGENT_OWNER, agent_id=AGENT_ID, backend=_BACKEND_ID).observe(
                lag
            )


async def _set_ready_when_started(server: uvicorn.Server) -> None:
    while not server.started:
        await asyncio.sleep(0.05)
    global _ready
    _ready = True
    if backend_startup_duration_seconds is not None:
        backend_startup_duration_seconds.labels(agent=AGENT_OWNER, agent_id=AGENT_ID, backend=_BACKEND_ID).set(
            time.monotonic() - _startup_mono
        )
    logger.info(f"Backend agent {AGENT_NAME} is ready")


async def main():
    """Process entry point — boot the Gemini backend's A2A + MCP + metrics surface.

    Bootstraps in a fixed order so each subsystem sees the state it needs:

    1. Bind the running asyncio loop for cross-thread event publishers
       (OTel span processor worker thread; #1144) BEFORE OTel init.
    2. Initialise OTel if ``OTEL_ENABLED`` is truthy (#469).
    3. Build the agent card + executor, then call
       :meth:`executor.bind_to_event_loop` (#1509) so loop-scoped
       primitives like ``_mcp_servers_lock`` are constructed against
       the serving loop rather than lazily on the first request.
    4. Register the executor with :func:`_set_health_executor` so the
       liveness handler can surface ``mcp_servers_active`` /
       ``session_cache_utilization_percent`` / ``history_save_failed``
       (#1099) without threading the executor through globals.
    5. Select :class:`SqliteTaskStore` when ``TASK_STORE_PATH`` is set,
       else :class:`InMemoryTaskStore` with a WARN about lost in-flight
       task state on restart.
    6. Register Prometheus startup gauges (``backend_up``,
       ``backend_info``, ``backend_sdk_info`` from
       ``importlib.metadata.version('google-genai')`` for #1092 SDK
       drift detection) when ``metrics_enabled``.
    7. Wire ``/conversations`` + ``/trace`` + ``/mcp`` + ``/api/traces``
       + ``/api/sessions/{id}/stream`` routes, then mount the built A2A
       sub-app at ``/``. Metrics live on a dedicated ``METRICS_PORT``
       listener, NOT on the main app (#643, #648), started inside the
       lifespan hook.
    8. The ``lifespan`` context drives the A2A sub-app's lifespan
       protocol via :func:`_sub_app_lifespan` and closes the executor
       on shutdown.
    9. Spawn the (currently empty) MCP watcher list via :func:`_guarded`
       for structural parity with claude, plus the session_stream
       registry idle sweeper (#1735) to prevent multi-day uptime OOM.
    10. ``asyncio.gather`` the uvicorn server, event-loop-lag monitor,
        sweeper, and :func:`_set_ready_when_started` so a failure in any
        coroutine propagates immediately.
    """
    global start_time, _startup_mono
    start_time = datetime.now(timezone.utc)
    _startup_mono = time.monotonic()

    # Bind the running event loop for cross-thread event publishers
    # (OTel span processor worker thread) so trace.span events reach
    # the harness event channel instead of being silently dropped by
    # ``asyncio.get_running_loop`` raising on a worker thread (#1144).
    try:
        from hook_events import bind_event_loop as _bind_event_loop

        _bind_event_loop(asyncio.get_running_loop())
    except Exception as _bind_exc:  # pragma: no cover — best-effort
        logger.warning("hook_events.bind_event_loop failed: %r", _bind_exc)

    # Initialise OTel before the executor (#469). No-op when OTEL_ENABLED is falsy.
    from otel import init_otel_if_enabled

    init_otel_if_enabled(
        service_name=os.environ.get("OTEL_SERVICE_NAME") or f"gemini-{os.environ.get('AGENT_OWNER', 'unknown')}"
    )

    agent_card = build_agent_card()
    executor = AgentExecutor()
    # #1509: bind loop-scoped primitives (_mcp_servers_lock) on the
    # serving loop now so they aren't lazily constructed against
    # whichever loop happens to be running on the first /mcp or
    # hot-reload request.
    await executor.bind_to_event_loop()
    # #1099: register the executor so the module-level /health handler can
    # surface mcp_servers_active / session_cache_utilization_percent /
    # history_save_failed without needing the executor threaded through.
    _set_health_executor(executor)
    _task_store_path = os.environ.get("TASK_STORE_PATH", "")
    if _task_store_path:
        logger.info("Using SqliteTaskStore at %s", _task_store_path)
        task_store = SqliteTaskStore(_task_store_path)
    else:
        logger.warning(
            "TASK_STORE_PATH is not set — using InMemoryTaskStore. "
            "In-flight A2A task state will be lost on process restart. "
            "Set TASK_STORE_PATH to a file path (e.g. /home/agent/logs/tasks.db) "
            "to enable persistence."
        )
        task_store = InMemoryTaskStore()
    request_handler = DefaultRequestHandler(
        agent_executor=executor,
        task_store=task_store,
    )
    a2a_app = A2AStarletteApplication(
        agent_card=agent_card,
        http_handler=request_handler,
    )
    a2a_built = a2a_app.build()

    if metrics_enabled:
        if backend_up is not None:
            backend_up.labels(agent=AGENT_OWNER, agent_id=AGENT_ID, backend=_BACKEND_ID).set(1.0)
        if backend_info is not None:
            backend_info.info(
                {"version": AGENT_VERSION, "agent": AGENT_OWNER, "agent_id": AGENT_ID, "backend": _BACKEND_ID}
            )
        # Register the shared session-binding fallback counter (#1103).
        if backend_session_binding_fallback_total is not None:
            _set_session_binding_fallback_counter(
                backend_session_binding_fallback_total,
                {"agent": AGENT_OWNER, "agent_id": AGENT_ID, "backend": _BACKEND_ID},
            )
        # Resolve the underlying SDK version once at startup (#1092) so
        # dashboards can catch google-genai drift without shelling in.
        if backend_sdk_info is not None:
            try:
                from importlib.metadata import PackageNotFoundError
                from importlib.metadata import version as _pkg_version

                try:
                    _sdk_ver = _pkg_version("google-genai")
                except PackageNotFoundError:
                    _sdk_ver = "unknown"
                backend_sdk_info.info({"sdk": "google-genai", "version": _sdk_ver})
            except Exception as _exc:
                logger.warning("backend_sdk_info: failed to resolve google-genai version: %r", _exc)
        if backend_uptime_seconds is not None:
            backend_uptime_seconds.labels(agent=AGENT_OWNER, agent_id=AGENT_ID, backend=_BACKEND_ID).set_function(
                lambda: (datetime.now(timezone.utc) - start_time).total_seconds()
            )
        logger.info("Prometheus metrics enabled at /metrics")
    else:
        logger.warning(
            "METRICS_ENABLED is not set — Prometheus metrics are disabled. "
            "Set METRICS_ENABLED=1 to enable /metrics and all instrumentation."
        )

    async def metrics_handler(request: Request) -> Response:
        body = prometheus_client.exposition.generate_latest()
        return Response(content=body, media_type=prometheus_client.exposition.CONTENT_TYPE_LATEST)

    conversations_handler = make_conversations_handler(CONVERSATIONS_AUTH_TOKEN, CONVERSATION_LOG)
    trace_handler = make_trace_handler(CONVERSATIONS_AUTH_TOKEN, TRACE_LOG)
    # Per-session SSE drill-down stream (#1110 phase 4).
    from session_stream import make_session_stream_handler

    session_stream_handler = make_session_stream_handler(CONVERSATIONS_AUTH_TOKEN, agent_id=AGENT_OWNER)

    _agent_description = load_agent_description()

    async def mcp_handler(request: Request) -> JSONResponse:
        """Minimal MCP JSON-RPC server: initialize / tools/list / tools/call."""
        # Gate on the same bearer token used by /conversations and /trace when
        # configured. Without this, any network caller could drive the LLM via
        # tools/call -> ask_agent and burn the operator's API key (#516).
        #
        # Transport-level observability (#560): wrap the full handler in a
        # try/finally so every exit path records a request counter + latency
        # histogram. The status taxonomy is bounded to keep cardinality low:
        # success | unauthorized | parse_error | method_not_found |
        # unknown_tool | missing_prompt | internal_error.
        _mcp_start = time.monotonic()
        _mcp_method = "unknown"
        _mcp_status = "internal_error"
        try:
            # #961: Fail-closed parity with claude (#718). Previously an
            # empty CONVERSATIONS_AUTH_TOKEN silently disabled auth and any
            # network caller could drive the LLM via tools/call → ask_agent,
            # burning operator API keys. CONVERSATIONS_AUTH_DISABLED is the
            # only acknowledged escape hatch for local dev.
            if not CONVERSATIONS_AUTH_TOKEN:
                if not auth_disabled_escape_hatch():
                    _mcp_status = "auth_not_configured"
                    return JSONResponse({"error": "auth not configured"}, status_code=503)
            else:
                header = request.headers.get("Authorization", "")
                if not hmac_mod.compare_digest(f"Bearer {CONVERSATIONS_AUTH_TOKEN}", header):
                    _mcp_status = "unauthorized"
                    return JSONResponse({"error": "unauthorized"}, status_code=401)
            # #1315 / #1674: reject oversize MCP bodies. The
            # Content-Length check is a cheap fast-path that rejects an
            # honest caller declaring a too-large payload before any
            # body is read. The streaming check below is the actual
            # enforcement — a hostile or buggy caller can declare a
            # small (or absent) Content-Length and then send arbitrarily
            # many bytes (e.g. under chunked transfer), so we MUST count
            # actual bytes received and abort BEFORE buffering them into
            # json().
            _MCP_BODY_CAP = 4 * 1024 * 1024
            try:
                declared_len = int(request.headers.get("Content-Length", "") or "-1")
            except ValueError:
                declared_len = -1
            if declared_len > _MCP_BODY_CAP:
                _mcp_status = "body_too_large"
                return JSONResponse(
                    {"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "body too large"}},
                    status_code=413,
                )
            # Stream-read into a bounded buffer so the cap is enforced
            # on actual bytes-on-the-wire, not on the caller's declared
            # length.
            _raw, _reason = await read_capped_body(request, _MCP_BODY_CAP)
            if _reason == "body_too_large":
                _mcp_status = "body_too_large"
                return JSONResponse(
                    {"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "body too large"}},
                    status_code=413,
                )
            if _reason == "parse_error" or _raw is None:
                _mcp_status = "parse_error"
                return JSONResponse(
                    {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}}, status_code=400
                )
            try:
                import json as _json_for_mcp

                body = _json_for_mcp.loads(_raw.decode("utf-8"))
            except Exception:
                _mcp_status = "parse_error"
                return JSONResponse(
                    {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}}, status_code=400
                )
            rpc_id = body.get("id")
            method = body.get("method", "")
            params = body.get("params") or {}
            if method in ("initialize", "tools/list", "tools/call"):
                _mcp_method = method

            if method == "initialize":
                _mcp_status = "success"
                # #1288: negotiate protocolVersion with the caller. Echo
                # whichever supported version the caller asked for, else
                # respond with the highest we support so clients pinned to
                # an older spec still interoperate and newer clients get
                # the latest shape. #1297: advertise tools.listChanged
                # explicitly so clients know whether to poll — we never
                # notify on tool-list change, so declare false.
                SUPPORTED_MCP_VERSIONS = ("2024-11-05", "2025-03-26")
                _client_version = params.get("protocolVersion")
                if _client_version in SUPPORTED_MCP_VERSIONS:
                    _negotiated_version = _client_version
                else:
                    _negotiated_version = SUPPORTED_MCP_VERSIONS[-1]
                return JSONResponse(
                    {
                        "jsonrpc": "2.0",
                        "id": rpc_id,
                        "result": {
                            "protocolVersion": _negotiated_version,
                            "capabilities": {"tools": {"listChanged": False}},
                            "serverInfo": {"name": AGENT_NAME, "version": AGENT_VERSION},
                        },
                    }
                )

            if method == "tools/list":
                _mcp_status = "success"
                return JSONResponse(
                    {
                        "jsonrpc": "2.0",
                        "id": rpc_id,
                        "result": {
                            "tools": [
                                {
                                    "name": "ask_agent",
                                    "description": _agent_description,
                                    "inputSchema": {
                                        "type": "object",
                                        "properties": {
                                            "prompt": {
                                                "type": "string",
                                                "description": "The prompt to send to the agent.",
                                            },
                                            # Advertise session_id + max_tokens parity with claude (#1090) so
                                            # generic MCP clients don't silently drop continuation / budget
                                            # controls when switching backends.
                                            "session_id": {
                                                "type": "string",
                                                "description": "Optional session identifier for conversation continuity. "  # noqa: E501
                                                "A valid UUID is passed through verbatim; any other string is "
                                                "hashed via uuid5(NAMESPACE_URL, value). Omit for a fresh session.",
                                            },
                                            "max_tokens": {
                                                "type": "integer",
                                                "minimum": 1,
                                                "description": "Optional per-call token budget. Positive integers only; "  # noqa: E501
                                                "non-positive or invalid values are logged and ignored.",
                                            },
                                        },
                                        "required": ["prompt"],
                                    },
                                }
                            ]
                        },
                    }
                )

            if method == "tools/call":
                tool_name = params.get("name", "")
                if tool_name != "ask_agent":
                    _mcp_status = "unknown_tool"
                    # #1283: tool-level failures use result.isError=true with
                    # a content block, not a JSON-RPC error envelope. Keeps
                    # the MCP contract uniform across claude/codex/gemini.
                    return JSONResponse(
                        {
                            "jsonrpc": "2.0",
                            "id": rpc_id,
                            "result": {
                                "isError": True,
                                "content": [{"type": "text", "text": f"Unknown tool: {tool_name!r}"}],
                            },
                        }
                    )
                arguments = params.get("arguments") or {}
                prompt = arguments.get("prompt", "")
                if not prompt:
                    _mcp_status = "missing_prompt"
                    return JSONResponse(
                        {
                            "jsonrpc": "2.0",
                            "id": rpc_id,
                            "error": {"code": -32602, "message": "Missing required argument: prompt"},
                        }
                    )
                # Optional max_tokens — same parsing semantics as the A2A path
                # (positive int; non-positive or invalid is logged and dropped).
                # Shared helper lives in shared/validation.py (#537, #460).
                mcp_max_tokens = parse_max_tokens(
                    arguments.get("max_tokens"),
                    logger=logger,
                    source="MCP tools/call",
                )
                # Caller-bound session_id on gemini /mcp (#941, #997).
                # The A2A path (executor.execute) reads caller_identity
                # from metadata.caller_id stamped by the harness, so two
                # entrypoints must resolve the same id for the same
                # logical principal or session continuity fractures
                # across /mcp and A2A. Resolution order:
                #   1. arguments["caller_id"] if the MCP caller stamped
                #      one explicitly (transport-agnostic, matches the
                #      A2A metadata claim byte-for-byte)
                #   2. sha256(bearer token) — authoritative fallback
                #      documented at #997; uniform across
                #      claude/codex/gemini /mcp for operators that do
                #      not stamp caller_id.
                _raw_sid = "".join(c for c in str(arguments.get("session_id") or "").strip()[:256] if c >= " ")
                # #1333: caller_identity is derived from the bearer ONLY
                # (parity with claude/codex). The previous code accepted
                # arguments["caller_id"] which let any bearer-holding
                # caller hijack another caller's session binding, defeating
                # #935/#941 HMAC binding.
                _bearer_header = request.headers.get("Authorization", "")
                _bearer_token = _bearer_header[len("Bearer ") :] if _bearer_header.startswith("Bearer ") else ""
                _caller_identity: str | None = (
                    hashlib.sha256(_bearer_token.encode("utf-8")).hexdigest() if _bearer_token else None
                )
                session_id = derive_session_id(_raw_sid, caller_identity=_caller_identity)
                # Acquire the MCP stack under refcount for the entire
                # call (#946). The previous snapshot-only pattern could
                # have a hot-reload aclose the underlying stack mid-call,
                # tearing down the stdio subprocess and surfacing an
                # opaque BrokenResourceError as MCP -32603. Mirrors the
                # A2A execute() path's acquire/release bracket.
                _live_servers, _held_stack = await executor._acquire_mcp_stack()
                try:
                    # #966: Named span nested under the inbound traceparent
                    # so cross-agent /api/traces joins see MCP-driven work
                    # instead of orphaned spans at the A2A boundary.
                    from otel import set_span_error as _set_span_error
                    from otel import start_span as _start_span

                    try:
                        with _start_span(
                            "backend.mcp.tools_call",
                            kind="server",
                            attributes={
                                "tool.name": tool_name,
                                "session.id": session_id,
                                "agent.id": AGENT_ID,
                            },
                        ) as _mcp_span:
                            try:
                                from executor import run as _run_for_mcp

                                response = await _run_for_mcp(
                                    prompt,
                                    session_id,
                                    executor._sessions,
                                    executor._agent_md_content,
                                    executor._session_locks,
                                    history_save_failed=executor._history_save_failed,
                                    model=None,
                                    max_tokens=mcp_max_tokens,
                                    live_mcp_servers=_live_servers,
                                )
                            except Exception as _span_exc:
                                _set_span_error(_mcp_span, _span_exc)
                                raise
                    except Exception as exc:
                        logger.error(f"MCP tools/call error: {exc!r}")
                        _mcp_status = "internal_error"
                        # #1283: tool execution failure is a tool-level error;
                        # report as result.isError=true with a generic text
                        # block. Full exception detail is logged server-side
                        # (above) but not leaked to MCP clients (#455).
                        return JSONResponse(
                            {
                                "jsonrpc": "2.0",
                                "id": rpc_id,
                                "result": {
                                    "isError": True,
                                    "content": [{"type": "text", "text": "Internal server error"}],
                                },
                            }
                        )
                finally:
                    try:
                        await executor._release_mcp_stack(_held_stack)
                    except Exception as _rel_exc:
                        logger.warning(
                            "MCP stack release error on /mcp call: %r",
                            _rel_exc,
                        )
                _mcp_status = "success"
                return JSONResponse(
                    {
                        "jsonrpc": "2.0",
                        "id": rpc_id,
                        "result": {"content": [{"type": "text", "text": response}]},
                    }
                )

            _mcp_status = "method_not_found"
            return JSONResponse(
                {
                    "jsonrpc": "2.0",
                    "id": rpc_id,
                    "error": {"code": -32601, "message": f"Method not found: {method!r}"},
                }
            )
        finally:
            if backend_mcp_requests_total is not None:
                backend_mcp_requests_total.labels(
                    agent=AGENT_OWNER,
                    agent_id=AGENT_ID,
                    backend=_BACKEND_ID,
                    method=_mcp_method,
                    status=_mcp_status,
                ).inc()
            if backend_mcp_request_duration_seconds is not None:
                backend_mcp_request_duration_seconds.labels(
                    agent=AGENT_OWNER,
                    agent_id=AGENT_ID,
                    backend=_BACKEND_ID,
                    method=_mcp_method,
                ).observe(time.monotonic() - _mcp_start)

    # OTel in-memory span store (#otel-in-cluster). Serves the Jaeger v1
    # shape so the harness's fan-out aggregator can merge backend spans.
    #
    # #961: Gated on CONVERSATIONS_AUTH_TOKEN (parity with claude #709).
    # Spans carry session IDs (bearer-equivalent), tool input-derived
    # fields, and agent identity — leaving this open was an information
    # disclosure surface across pods/tenants. Fail-closed on empty token
    # unless CONVERSATIONS_AUTH_DISABLED=true is explicitly set.
    def _require_traces_auth(request: Request) -> JSONResponse | None:
        if not CONVERSATIONS_AUTH_TOKEN:
            if auth_disabled_escape_hatch():
                return None
            return JSONResponse({"error": "auth not configured"}, status_code=503)
        header = request.headers.get("Authorization", "")
        if not hmac_mod.compare_digest(f"Bearer {CONVERSATIONS_AUTH_TOKEN}", header):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        return None

    async def otel_traces_list_handler(request: Request) -> JSONResponse:
        unauthorized = _require_traces_auth(request)
        if unauthorized is not None:
            return unauthorized
        # #1094: clamp limit against the in-memory span cap so an
        # authenticated caller can't request limit=10_000_000 and force a
        # pathological response assembly. Matches claude/main.py's
        # defence added for the same endpoint.
        try:
            _cap = int(os.environ.get("OTEL_IN_MEMORY_SPANS") or 1000)
        except ValueError:
            _cap = 1000
        if _cap <= 0:
            _cap = 1000
        try:
            limit_raw = request.query_params.get("limit")
            limit = int(limit_raw) if limit_raw else 20
        except ValueError:
            limit = 20
        if limit <= 0:
            limit = 20
        if limit > _cap:
            limit = _cap
        # Offset-based pagination (#1101). Parity with claude / codex.
        try:
            offset_raw = request.query_params.get("offset")
            offset = int(offset_raw) if offset_raw else 0
        except ValueError:
            offset = 0
        if offset < 0:
            offset = 0
        try:
            from otel import get_in_memory_traces  # type: ignore

            traces = get_in_memory_traces()
        except Exception:
            traces = []
        return JSONResponse(
            {
                "data": traces[offset : offset + limit],
                "total": len(traces),
                "offset": offset,
                "limit": limit,
            }
        )

    async def otel_traces_detail_handler(request: Request) -> JSONResponse:
        unauthorized = _require_traces_auth(request)
        if unauthorized is not None:
            return unauthorized
        trace_id = request.path_params.get("trace_id") or ""
        try:
            from otel import get_in_memory_traces  # type: ignore

            traces = get_in_memory_traces()
        except Exception:
            traces = []
        match = next((t for t in traces if t.get("traceID") == trace_id), None)
        if match is None:
            return JSONResponse({"data": [], "total": 0}, status_code=404)
        return JSONResponse({"data": [match], "total": 1})

    _routes = [
        # #1608 + #1672: split liveness vs readiness. /health (liveness)
        # returns 200 once the process is up so kubelet does not
        # CrashLoopBackOff a degraded pod. /health/ready (readiness)
        # returns 503 while starting or while _ready was dropped by
        # _guarded()'s critical-task circuit-breaker. Operators upgrading
        # from <=v0.5.0 must repoint their K8s readinessProbe at
        # /health/ready.
        Route("/health/start", health_start),  # #1686
        Route("/health", health),
        Route("/health/live", health),  # alias of /health — uniform liveness surface across harness/backends/MCP tools
        Route("/health/ready", health_ready),
        Route("/conversations", conversations_handler, methods=["GET"]),
        Route("/trace", trace_handler, methods=["GET"]),
        Route("/mcp", mcp_handler, methods=["GET", "POST"]),
        Route("/api/traces", otel_traces_list_handler, methods=["GET"]),
        Route("/api/traces/{trace_id}", otel_traces_detail_handler, methods=["GET"]),
        Route("/api/sessions/{session_id}/stream", session_stream_handler, methods=["GET"]),
    ]
    # Metrics on dedicated :METRICS_PORT listener (#643, #648).
    _routes.append(Mount("/", app=a2a_built))

    @asynccontextmanager
    async def lifespan(_app: Starlette):
        async with AsyncExitStack() as stack:
            for route in _routes:
                if isinstance(route, Mount) and route.path == "/":
                    await stack.enter_async_context(_sub_app_lifespan(route.app))
            if metrics_enabled:
                from metrics_server import start_metrics_server

                start_metrics_server(metrics_handler, logger=logger)
            try:
                yield
            finally:
                await executor.close()

    full_app = Starlette(routes=_routes, lifespan=lifespan)
    # Wrap with the ASGI middleware that extracts the inbound traceparent
    # so the A2A SDK's @trace_class spans become children of the harness
    # trace rather than orphaned roots (#otel-cross-pod).
    from otel import TraceparentASGIMiddleware

    full_app = TraceparentASGIMiddleware(full_app)

    logger.info(f"Starting {AGENT_NAME} on {AGENT_HOST}:{BACKEND_PORT}")
    config = uvicorn.Config(full_app, host=AGENT_HOST, port=BACKEND_PORT)
    server = uvicorn.Server(config)

    # Start MCP watcher tasks (none for gemini, but kept for structural parity)
    for _w in executor._mcp_watchers():
        _mcp_task = asyncio.create_task(_guarded(_w))
        _mcp_task.add_done_callback(
            lambda t, _wn=_w.__name__: (
                logger.error(f"MCP watcher {_wn!r} exited unexpectedly: {t.exception()!r}")
                if not t.cancelled() and t.exception() is not None
                else None
            )
        )
        executor._mcp_watcher_tasks.append(_mcp_task)

    # #1735: periodic session_stream registry sweeper. Without this,
    # _registry in shared/session_stream.py grows unbounded — multi-day
    # uptime with caller churn ends in OOMKill.
    from session_stream import run_idle_sweeper as _run_session_stream_sweeper

    await asyncio.gather(
        server.serve(),
        _guarded(_event_loop_monitor),
        _guarded(_run_session_stream_sweeper),
        _set_ready_when_started(server),
    )


if __name__ == "__main__":
    asyncio.run(main())
