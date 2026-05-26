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
from metrics import (
    backend_event_loop_lag_seconds,
    backend_health_checks_total,
    backend_info,
    backend_mcp_request_duration_seconds,
    backend_mcp_requests_total,
    backend_sdk_info,
    backend_session_binding_fallback_total,
    backend_session_caller_cardinality,
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

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

AGENT_NAME = os.environ.get("AGENT_NAME", "claude")
AGENT_HOST = os.environ.get("AGENT_HOST", "0.0.0.0")
BACKEND_PORT = int(os.environ.get("BACKEND_PORT", "8000"))
AGENT_URL = os.environ.get("AGENT_URL", f"http://localhost:{BACKEND_PORT}/")
AGENT_VERSION = os.environ.get("AGENT_VERSION", "0.1.0")
CONVERSATION_LOG = os.environ.get("CONVERSATION_LOG", "/home/agent/logs/conversation.jsonl")
TRACE_LOG = os.environ.get("TRACE_LOG", "/home/agent/logs/tool-activity.jsonl")
AGENT_OWNER = os.environ.get("AGENT_OWNER") or AGENT_NAME
# #1340: fall back to HOSTNAME for uniqueness instead of the literal
# backend name; multiple agents (iris-claude/nova-claude/kira-claude)
# with env misconfig would otherwise all report agent_id="claude" and
# collide on cross-agent metric joins.
AGENT_ID = os.environ.get("AGENT_ID") or os.environ.get("HOSTNAME") or "claude"
_BACKEND_ID = "claude"
metrics_enabled = parse_bool_env("METRICS_ENABLED")
WORKER_MAX_RESTARTS = int(os.environ.get("WORKER_MAX_RESTARTS", "5"))
CONVERSATIONS_AUTH_TOKEN = os.environ.get("CONVERSATIONS_AUTH_TOKEN", "")
# #1609: cap on MCP request body size, env-var-overridable. Default 4 MiB.
# Enforced both as a fast-path Content-Length check and as a streaming
# byte-counter so a hostile/buggy caller can't lie about Content-Length
# and force the backend to buffer arbitrary bytes into json().
try:
    _MCP_MAX_BODY_BYTES = int(os.environ.get("MCP_MAX_BODY_BYTES", str(4 * 1024 * 1024)))
except ValueError:
    _MCP_MAX_BODY_BYTES = 4 * 1024 * 1024
if _MCP_MAX_BODY_BYTES <= 0:
    _MCP_MAX_BODY_BYTES = 4 * 1024 * 1024

# #1618: bound the wait for a sub-app's lifespan.shutdown.complete so a
# faulty / hung sub-app cannot stall pod termination indefinitely.
# Default 10s — env-overridable via SUB_APP_SHUTDOWN_TIMEOUT_SEC. On
# timeout we log a WARN (operators expect this on bad rollouts) and
# proceed with the rest of the lifespan teardown so the pod still drains
# in bounded time.
try:
    _SUB_APP_SHUTDOWN_TIMEOUT_SEC = float(os.environ.get("SUB_APP_SHUTDOWN_TIMEOUT_SEC", "10.0"))
except ValueError:
    _SUB_APP_SHUTDOWN_TIMEOUT_SEC = 10.0
if _SUB_APP_SHUTDOWN_TIMEOUT_SEC <= 0:
    _SUB_APP_SHUTDOWN_TIMEOUT_SEC = 10.0

_ready: bool = False
# #1368: surface boot-degraded state on /health so operators can alert
# on backends that came up with empty MCP/agent_md/hooks after the
# perform_initial_loads timeout path.
_boot_degraded_reason: str | None = None
_startup_mono: float = 0.0
start_time: datetime = datetime.now(timezone.utc)
_health_executor_ref: AgentExecutor | None = None


def _set_health_executor(executor: AgentExecutor) -> None:
    """Register the running executor for lightweight /health introspection."""
    global _health_executor_ref
    _health_executor_ref = executor


def _hook_enforcement_mode_for_health() -> str:
    executor = _health_executor_ref
    if executor is None:
        return "unknown"
    try:
        return executor.hooks_enforcement_mode_for_health()
    except Exception:
        return "unknown"


# /mcp caller-identity cardinality tracker (#1049).  Holds the hex SHA256
# fingerprints of distinct caller bearer tokens observed since process
# start, capped to prevent unbounded growth on per-request tokens. The
# gauge is updated each request to reflect len(_mcp_caller_identities).
_MCP_CALLER_CARDINALITY_CAP: int = 10_000
_mcp_caller_identities: set[str] = set()
# #1486: serialise the check-then-add on _mcp_caller_identities so two
# concurrent /mcp handlers at cardinality cap-1 cannot both pass the cap
# check and both .add(), exceeding the intended cap. Lazy-init to avoid
# binding the lock to the wrong loop on module import.
_mcp_caller_identities_lock: "asyncio.Lock | None" = None


def _get_mcp_caller_identities_lock() -> asyncio.Lock:
    """Lazy accessor for the cardinality-tracker lock (#1486)."""
    global _mcp_caller_identities_lock
    if _mcp_caller_identities_lock is None:
        _mcp_caller_identities_lock = asyncio.Lock()
    return _mcp_caller_identities_lock


# #1609 / #1673 / #1674: streaming body-cap helper now lives in
# shared/mcp_body_cap.py so codex and gemini can consume the exact same
# defense — a hostile/buggy caller can lie about (or omit)
# Content-Length under chunked transfer, and only the streaming check
# bounds actual bytes received before json() buffers them.
from mcp_body_cap import read_capped_body as _read_capped_body  # noqa: E402


def load_agent_description() -> str:
    """Return the agent's description string for the A2A agent card / MCP tool description.

    Prefers the contents of ``/home/agent/.claude/agent-card.md`` (mounted
    from a ConfigMap or volume), falling back to the ``AGENT_DESCRIPTION``
    env var and then a generic ``"A Claude backend agent."`` literal so
    the card is always populated even when the file mount is missing.
    """
    try:
        with open("/home/agent/.claude/agent-card.md") as f:
            return f.read()
    except OSError:
        return os.environ.get("AGENT_DESCRIPTION", "A Claude backend agent.")


def build_agent_card() -> AgentCard:
    """Return the A2A :class:`AgentCard` advertised at ``/.well-known``.

    Uses the module-level ``AGENT_NAME`` / ``AGENT_URL`` / ``AGENT_VERSION``
    constants (resolved from env at import time) and the description from
    :func:`load_agent_description`. Declares a single ``general`` skill and
    sets ``streaming=False``: per-chunk Message emission was removed for
    blocking-call correctness (see ``executor.py``'s ``_emit_chunk`` note),
    so the card now reflects the actual wire behaviour. If chunk emission
    via ``TaskStatusUpdateEvent`` is reintroduced, flip this back to True
    alongside the executor change.
    """
    return AgentCard(
        name=AGENT_NAME,
        description=load_agent_description(),
        url=AGENT_URL,
        version=AGENT_VERSION,
        # streaming=False reflects the actual wire behaviour after the
        # per-chunk Message emission was removed (see executor.py
        # _emit_chunk comment). The previous streaming=True was honest
        # only as long as #430's per-chunk emission was active. If
        # streaming gets reintroduced — emitting chunks as
        # TaskStatusUpdateEvent so blocking callers aren't tripped —
        # flip this back to True alongside the executor change.
        capabilities=AgentCapabilities(streaming=False),
        default_input_modes=["text/plain"],
        default_output_modes=["text/plain"],
        skills=[
            AgentSkill(
                id="general",
                name="General",
                description="General-purpose task execution via Claude.",
                tags=["general", "claude"],
            )
        ],
    )


async def health_start(request: Request) -> JSONResponse:
    """Kubernetes STARTUP probe handler — serves ``/health/start`` (#1686).

    Returns HTTP 200 once :data:`_ready` has been flipped to True by
    :func:`_set_ready_when_started` (i.e. uvicorn has bound the listener
    and initial loads have completed) and HTTP 503 with
    ``{"status": "starting"}`` before that. Mirrors the harness's
    ``health_start`` so the documented three-probe contract holds across
    the platform. Increments ``backend_health_checks_total`` with
    ``probe="start"``.
    """
    # #1686: /health/start is the STARTUP probe — it returns 200 once
    # the process has finished initial loads (_ready=True) and 503 with
    # `{"status": "starting"}` while still warming up. K8s startupProbe
    # should target this endpoint; once it succeeds, kubelet starts
    # gating on liveness/readiness independently. Mirrors the harness's
    # health_start at harness/main.py:490 so the documented
    # three-probe contract (docs/product-vision.md:74) holds across
    # the whole platform — not just the harness.
    if backend_health_checks_total is not None:
        backend_health_checks_total.labels(
            agent=AGENT_OWNER, agent_id=AGENT_ID, backend=_BACKEND_ID, probe="start"
        ).inc()
    if _ready:
        return JSONResponse({"status": "ok"})
    return JSONResponse({"status": "starting"}, status_code=503)


async def health(request: Request) -> JSONResponse:
    """Kubernetes LIVENESS probe handler — serves ``/health`` and ``/health/live``.

    Returns HTTP 200 unconditionally so kubelet does not CrashLoopBackOff
    a pod that is merely starting or boot-degraded — pre-ready state is
    surfaced via the body's ``status`` field (``"starting"`` vs ``"ok"``)
    rather than the HTTP code, and boot-degraded state via the optional
    ``boot_degraded`` field (#1368). For readiness gating point K8s
    readinessProbe at :func:`health_ready` instead. Also exposes
    ``hooks_enforcement_mode`` (the executor's PreToolUse posture) and
    ``agent_owner``/``agent_id`` for metric-label join parity (#1341).
    Increments ``backend_health_checks_total`` with ``probe="health"``.
    """
    # #1608 + 5e5d5a9b unification: /health is the LIVENESS probe — it
    # returns 200 unconditionally so kubelet does not CrashLoopBackOff a
    # pod that is merely starting or degraded. Pre-ready state is surfaced
    # via the body's `status` field ("starting" vs "ok"); boot-degraded
    # state is surfaced informationally via `boot_degraded`. Neither flips
    # the status code. For readiness gating (i.e. removing a pod from
    # Service endpoints) point K8s readinessProbe at /health/ready
    # instead. This mirrors openai/main.py:136 and gemini/main.py:136 and
    # closes the unification gap left by `5e5d5a9b` — claude's previous
    # implementation returned 503 while `_ready=False`, which under slow
    # startup (exceeding kubelet's initialDelaySeconds) would
    # CrashLoopBackOff a backend that should have been merely removed
    # from Service endpoints.
    if backend_health_checks_total is not None:
        backend_health_checks_total.labels(
            agent=AGENT_OWNER, agent_id=AGENT_ID, backend=_BACKEND_ID, probe="health"
        ).inc()
    elapsed = (datetime.now(timezone.utc) - start_time).total_seconds()
    # #1341: expose both fields so consumers that cross-reference with
    # Prometheus metric labels (which use AGENT_OWNER as `agent`) can
    # join cleanly. `agent` preserves the container-local name for
    # backwards compat; `agent_owner` + `agent_id` match metric labels.
    _resp = {
        "status": "ok" if _ready else "starting",
        "agent": AGENT_NAME,
        "agent_owner": AGENT_OWNER,
        "agent_id": AGENT_ID,
        "hooks_enforcement_mode": _hook_enforcement_mode_for_health(),
        "uptime_seconds": elapsed,
    }
    # #1368: surface boot-degraded state so operators alert on
    # backends that came up with empty MCP/agent_md/hooks.
    if _boot_degraded_reason is not None:
        _resp["boot_degraded"] = _boot_degraded_reason
    return JSONResponse(_resp)


async def health_ready(request: Request) -> JSONResponse:
    """Kubernetes READINESS probe handler — serves ``/health/ready`` (#1608).

    Returns HTTP 503 when the process is still starting (:data:`_ready`
    is False) OR when boot finished in a degraded state
    (:data:`_boot_degraded_reason` is set, e.g.
    ``perform_initial_loads`` timed out and the executor came up with
    empty MCP/agent_md/hooks). 503 here removes the pod from Service
    endpoints without restarting it; the degraded flag is not cleared
    automatically and requires manual intervention or a pod restart.
    Returns HTTP 200 with ``status="ready"`` once both gates pass.
    Increments ``backend_health_checks_total`` with ``probe="ready"``.
    """
    # #1608: /health/ready is the READINESS probe — it returns 503 when
    # the process is still starting (_ready is False) OR when boot
    # finished in a degraded state (_boot_degraded_reason is set, e.g.
    # perform_initial_loads timed out and the executor came up with
    # empty MCP/agent_md/hooks). 503 here removes the pod from Service
    # endpoints without restarting it; the watchers continue trying to
    # fill in config asynchronously and the pod will become ready once
    # the degraded reason clears (currently never automatically — manual
    # restart or operator intervention required to reset the flag).
    if backend_health_checks_total is not None:
        backend_health_checks_total.labels(
            agent=AGENT_OWNER, agent_id=AGENT_ID, backend=_BACKEND_ID, probe="ready"
        ).inc()
    if not _ready:
        return JSONResponse({"status": "starting"}, status_code=503)
    if _boot_degraded_reason is not None:
        return JSONResponse(
            {
                "status": "degraded",
                "agent": AGENT_NAME,
                "agent_owner": AGENT_OWNER,
                "agent_id": AGENT_ID,
                "boot_degraded": _boot_degraded_reason,
            },
            status_code=503,
        )
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
        task.cancel()
        try:
            await task
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
        # #1370: collect shutdown + task exceptions separately and raise
        # as ExceptionGroup so operators see BOTH when a cascading
        # shutdown fails on multiple legs. Previously only the first
        # exception propagated and the underlying cause was masked.
        _errs: list[BaseException] = []
        # #1618: bound the wait for shutdown.complete. A faulty sub-app
        # that never emits the message must not be able to stall pod
        # termination — log a WARN and continue with the rest of the
        # teardown so the pod still drains in bounded time.
        try:
            await asyncio.wait_for(shutdown, timeout=_SUB_APP_SHUTDOWN_TIMEOUT_SEC)
        except asyncio.TimeoutError:
            _sub_name = getattr(app, "__name__", None) or getattr(app, "name", None) or repr(app)
            logger.warning(
                "Sub-app lifespan shutdown timed out after %.1fs; proceeding with teardown (sub_app=%s)",
                _SUB_APP_SHUTDOWN_TIMEOUT_SEC,
                _sub_name,
            )
        except Exception as exc:
            logger.warning("Sub-app lifespan shutdown error: %s", exc)
            _errs.append(exc)
        try:
            await asyncio.wait_for(task, timeout=_SUB_APP_SHUTDOWN_TIMEOUT_SEC)
        except asyncio.TimeoutError:
            _sub_name = getattr(app, "__name__", None) or getattr(app, "name", None) or repr(app)
            logger.warning(
                "Sub-app lifespan task did not exit within %.1fs after shutdown; cancelling (sub_app=%s)",
                _SUB_APP_SHUTDOWN_TIMEOUT_SEC,
                _sub_name,
            )
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        except Exception as exc:
            _errs.append(exc)
        _task_exc: BaseException | None = None
        if task.done() and not task.cancelled():
            try:
                _task_exc = task.exception()
            except (asyncio.CancelledError, asyncio.InvalidStateError):
                _task_exc = None
        if _task_exc is not None and _task_exc not in _errs:
            _errs.append(_task_exc)
        if len(_errs) == 1:
            raise _errs[0]
        elif len(_errs) > 1:
            # ExceptionGroup is py3.11+; fall back to first exception with
            # others chained via __context__ on older runtimes.
            try:
                raise ExceptionGroup("sub-app lifespan errors", _errs)  # noqa: F821
            except NameError:
                _primary = _errs[0]
                for _e in _errs[1:]:
                    logger.error("sub-app lifespan additional error: %r", _e)
                raise _primary from None


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
    """Process entry point — boot the Claude backend's A2A + MCP + metrics surface.

    Bootstraps in a fixed order so each subsystem sees the state it needs:

    1. Bind the running asyncio loop for cross-thread event publishers
       (OTel span processor worker thread; #1144) BEFORE OTel init so the
       first span's ``on_end`` callback already has a loop reference.
    2. Initialise OTel if ``OTEL_ENABLED`` is truthy (#469).
    3. Build the agent card + executor, select :class:`SqliteTaskStore`
       when ``TASK_STORE_PATH`` is set (else :class:`InMemoryTaskStore`
       with a WARN about lost in-flight task state on restart), and
       register the executor with :func:`_set_health_executor` so the
       liveness handler can read its enforcement mode.
    4. Register Prometheus startup gauges (``backend_up``,
       ``backend_info``, ``backend_sdk_info`` from
       ``importlib.metadata.version('claude-agent-sdk')`` for #1092
       drift detection) when ``metrics_enabled``.
    5. Wire ``/conversations`` + ``/trace`` + ``/mcp`` + ``/api/traces``
       + ``/api/sessions/{id}/stream`` routes, then mount the built A2A
       sub-app at ``/``. Metrics live on a dedicated ``METRICS_PORT``
       listener, NOT on the main app (#643, #646), started inside the
       lifespan hook.
    6. The ``lifespan`` context drives the A2A sub-app's lifespan
       protocol via :func:`_sub_app_lifespan` and closes the executor
       + task_store (if it has a ``close()``; #713) on shutdown.
    7. Run :meth:`executor.perform_initial_loads` synchronously with a
       ``INITIAL_LOADS_TIMEOUT_SECONDS`` (default 10s; #985) bound so a
       wedged ConfigMap mount can't stall startup. On timeout / error
       set :data:`_boot_degraded_reason` so ``/health/ready`` returns
       503 (#1368).
    8. Spawn MCP / hooks / agent_md watcher tasks via
       :func:`_guarded` with ``critical=True`` so persistent crashes
       take readiness down via ``WORKER_MAX_RESTARTS`` rather than
       silently looping (#585), plus the session_stream registry idle
       sweeper (#1735) to prevent multi-day uptime OOM.
    9. ``asyncio.gather`` the uvicorn server, event-loop-lag monitor,
       sweeper, and :func:`_set_ready_when_started` so a failure in any
       coroutine propagates immediately.
    """
    global start_time, _startup_mono
    start_time = datetime.now(timezone.utc)
    _startup_mono = time.monotonic()

    # Bind the running event loop so cross-thread event publishers
    # (notably the OTel span processor's worker thread) can still
    # reach the harness event channel (#1144).  Must happen before
    # OTel init so the very first span's on_end callback already has
    # a loop reference to fall back to.
    try:
        from hook_events import bind_event_loop as _bind_event_loop

        _bind_event_loop(asyncio.get_running_loop())
    except Exception as _bind_exc:  # pragma: no cover — best-effort
        logger.warning("hook_events.bind_event_loop failed: %r", _bind_exc)

    # Initialise OTel before the executor so every request gets a span if
    # enabled (#469). No-op when OTEL_ENABLED is falsy.
    from otel import init_otel_if_enabled

    init_otel_if_enabled(
        service_name=os.environ.get("OTEL_SERVICE_NAME") or f"claude-{os.environ.get('AGENT_OWNER', 'unknown')}"
    )

    agent_card = build_agent_card()
    executor = AgentExecutor()
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
        # dashboards can catch claude-agent-sdk drift without shelling in.
        if backend_sdk_info is not None:
            try:
                from importlib.metadata import PackageNotFoundError
                from importlib.metadata import version as _pkg_version

                try:
                    _sdk_ver = _pkg_version("claude-agent-sdk")
                except PackageNotFoundError:
                    _sdk_ver = "unknown"
                backend_sdk_info.info({"sdk": "claude-agent-sdk", "version": _sdk_ver})
            except Exception as _exc:
                logger.warning("backend_sdk_info: failed to resolve claude-agent-sdk version: %r", _exc)
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
    # Per-session SSE drill-down stream (#1110 phase 4). Serves live
    # conversation.chunk / conversation.turn / tool.use / trace.span
    # envelopes for one session. Auth parity with /conversations.
    from session_stream import make_session_stream_handler

    session_stream_handler = make_session_stream_handler(CONVERSATIONS_AUTH_TOKEN, agent_id=AGENT_OWNER)

    _agent_description = load_agent_description()

    # Label schema shared by the per-request metrics (#790). Matches
    # gemini so cross-backend dashboards can union by (agent, agent_id,
    # backend, method).
    _MCP_METRIC_LABELS = {
        "agent": AGENT_OWNER,
        "agent_id": AGENT_ID,
        "backend": _BACKEND_ID,
    }

    async def mcp_handler(request: Request) -> JSONResponse:
        """Minimal MCP JSON-RPC server: initialize / tools/list / tools/call.

        Wrapped with per-request metrics (#790): every return path records
        ``backend_mcp_requests_total{method,status}`` and
        ``backend_mcp_request_duration_seconds{method}`` so operators can
        alert on rate / p95 latency without rewriting labels vs gemini.
        """
        import time as _time_for_mcp

        _mcp_start = _time_for_mcp.monotonic()
        _method_box: list[str] = ["unknown"]
        _status_box: list[str] = ["ok"]
        try:
            return await _mcp_handler_inner(request, _method_box, _status_box)
        except Exception:
            _status_box[0] = "error"
            raise
        finally:
            _elapsed = _time_for_mcp.monotonic() - _mcp_start
            try:
                if backend_mcp_requests_total is not None:
                    backend_mcp_requests_total.labels(
                        **_MCP_METRIC_LABELS,
                        method=_method_box[0],
                        status=_status_box[0],
                    ).inc()
                if backend_mcp_request_duration_seconds is not None:
                    backend_mcp_request_duration_seconds.labels(
                        **_MCP_METRIC_LABELS,
                        method=_method_box[0],
                    ).observe(_elapsed)
            except Exception:
                pass

    async def _mcp_handler_inner(
        request: Request,
        _method_box: list[str],
        _status_box: list[str],
    ) -> JSONResponse:
        # Gate on the same bearer token used by /conversations and /trace.
        # Fail-closed when the token is missing (#718) unless the operator
        # explicitly set CONVERSATIONS_AUTH_DISABLED=true for local dev;
        # previously an empty token silently disabled auth and any network
        # caller could drive the LLM via tools/call -> ask_agent (#518).
        if not CONVERSATIONS_AUTH_TOKEN:
            if not auth_disabled_escape_hatch():
                _status_box[0] = "auth_not_configured"
                return JSONResponse({"error": "auth not configured"}, status_code=503)
        else:
            header = request.headers.get("Authorization", "")
            if not hmac_mod.compare_digest(f"Bearer {CONVERSATIONS_AUTH_TOKEN}", header):
                _status_box[0] = "unauthorized"
                return JSONResponse({"error": "unauthorized"}, status_code=401)
        # #1315 / #1609: reject oversize MCP bodies. The Content-Length
        # check is a cheap fast-path: an honest caller declaring a too-
        # large payload is rejected before we read a byte. The streaming
        # check below is the actual enforcement — a hostile or buggy
        # caller can declare a small (or absent) Content-Length and then
        # send arbitrarily many bytes, so we MUST count actual bytes
        # received and abort BEFORE buffering them into json().
        try:
            declared_len = int(request.headers.get("Content-Length", "") or "-1")
        except ValueError:
            declared_len = -1
        if declared_len > _MCP_MAX_BODY_BYTES:
            _status_box[0] = "body_too_large"
            return JSONResponse(
                {"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "body too large"}},
                status_code=413,
            )
        # Stream-read into a bounded buffer so the cap is enforced on
        # actual bytes-on-the-wire, not on the caller's declared length.
        _raw, _reason = await _read_capped_body(request, _MCP_MAX_BODY_BYTES)
        if _reason == "body_too_large":
            _status_box[0] = "body_too_large"
            return JSONResponse(
                {"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "body too large"}},
                status_code=413,
            )
        if _reason == "parse_error" or _raw is None:
            _status_box[0] = "parse_error"
            return JSONResponse(
                {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}}, status_code=400
            )
        try:
            import json as _json_for_mcp

            body = _json_for_mcp.loads(_raw.decode("utf-8"))
        except Exception:
            _status_box[0] = "parse_error"
            return JSONResponse(
                {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}}, status_code=400
            )
        rpc_id = body.get("id")
        method = body.get("method", "")
        params = body.get("params") or {}
        # Populate method for the outer observer — captured AFTER the
        # parse succeeds so malformed bodies land under method='unknown'.
        if isinstance(method, str) and method:
            _method_box[0] = method

        if method == "initialize":
            # #1288: negotiate protocolVersion with the caller. Echo
            # whichever supported version the caller asked for, else
            # respond with the highest we support so clients pinned to an
            # older spec still interoperate and newer clients get the
            # latest shape. #1297: advertise tools.listChanged explicitly
            # so clients know whether to poll — we currently never notify
            # on tool-list change, so declare false.
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
                                        "prompt": {"type": "string", "description": "The prompt to send to the agent."},
                                        "session_id": {
                                            "type": "string",
                                            "description": "Optional session identifier for conversation continuity (#596). "  # noqa: E501
                                            "A valid UUID is passed through verbatim; any other string is "
                                            "hashed via uuid5(NAMESPACE_URL, value). Omit for a fresh session.",
                                        },
                                        "max_tokens": {
                                            "type": "integer",
                                            "minimum": 1,
                                            "description": "Optional per-call token budget (#460). Positive integers only; "  # noqa: E501
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
                # #1281: MCP tools/call conveys tool-level failures as
                # result.isError=true with a content block, not a
                # JSON-RPC error object (JSON-RPC errors are reserved
                # for protocol-level failures like bad method / bad
                # params). Clients that branch on isError see the
                # failure; clients that ignore it still see a well-formed
                # result payload.
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
                return JSONResponse(
                    {
                        "jsonrpc": "2.0",
                        "id": rpc_id,
                        "error": {"code": -32602, "message": "Missing required argument: prompt"},
                    }
                )
            # Optional max_tokens — same parsing semantics as the A2A path
            # (positive int; non-positive or invalid is logged and dropped) (#460).
            _max_tokens_raw = arguments.get("max_tokens")
            mcp_max_tokens: int | None = None
            if _max_tokens_raw is not None:
                try:
                    _parsed = int(_max_tokens_raw)
                    if _parsed <= 0:
                        logger.warning("MCP tools/call: max_tokens=%s is non-positive; ignoring (#460).", _parsed)
                    else:
                        mcp_max_tokens = _parsed
                except (ValueError, TypeError):
                    logger.warning("MCP tools/call: invalid max_tokens %r; ignoring.", _max_tokens_raw)
            # Session continuity (#596) + caller-bound derivation (#867).
            # Route /mcp session_id through shared.session_binding.derive_session_id
            # with a bearer-token fingerprint as caller_identity so two /mcp
            # callers presenting the same raw session_id do NOT collide when
            # SESSION_ID_SECRET is set. On endpoints without caller auth, or
            # without the secret, derive_session_id falls back to the legacy
            # uuid5 derivation for backward compatibility.
            _raw_sid = "".join(c for c in str(arguments.get("session_id") or "").strip()[:256] if c >= " ")
            _bearer_header = request.headers.get("Authorization", "")
            _bearer_token = _bearer_header[len("Bearer ") :] if _bearer_header.startswith("Bearer ") else ""
            _caller_identity = hashlib.sha256(_bearer_token.encode("utf-8")).hexdigest() if _bearer_token else None
            # #982: the "no-bearer on /mcp" case silently downgrades to
            # the legacy uuid5 derivation (re-exposing the #710 cross-
            # caller collision risk). The shared counter in
            # shared.session_binding only distinguishes reasons like
            # "secret_unset" / "caller_identity_missing" without an
            # endpoint label, so a /mcp-specific alert wasn't possible.
            # Bump a dedicated reason so operators can alert on
            # unauthenticated /mcp hits even when SESSION_ID_SECRET is
            # unset. Logged at WARNING (re-armed by the shared path).
            # #1490: under the sanctioned local-dev escape hatch
            # (CONVERSATIONS_AUTH_DISABLED=true), no bearer is expected;
            # bumping mcp_no_bearer fires false-positive alerts for the
            # operator-acknowledged no-auth mode. Skip the counter in
            # that case — the startup log already flagged the escape
            # hatch loudly.
            if (
                _caller_identity is None
                and backend_session_binding_fallback_total is not None
                and not auth_disabled_escape_hatch()
            ):
                try:
                    backend_session_binding_fallback_total.labels(
                        agent=AGENT_OWNER,
                        agent_id=AGENT_ID,
                        backend=_BACKEND_ID,
                        reason="mcp_no_bearer",
                    ).inc()
                except Exception:
                    pass
            # Update caller-cardinality gauge so operators can detect
            # single-tenant token collapse (gauge == 1 with non-trivial
            # /mcp traffic means SESSION_ID_SECRET's per-caller binding
            # is a no-op; migrate to per-caller tokens). See #1049.
            if _caller_identity is not None and backend_session_caller_cardinality is not None:
                try:
                    # #1486: serialise the check-then-add to keep the set
                    # strictly bounded by _MCP_CALLER_CARDINALITY_CAP.
                    async with _get_mcp_caller_identities_lock():
                        if (
                            _caller_identity not in _mcp_caller_identities
                            and len(_mcp_caller_identities) < _MCP_CALLER_CARDINALITY_CAP
                        ):
                            _mcp_caller_identities.add(_caller_identity)
                        _cardinality = len(_mcp_caller_identities)
                    backend_session_caller_cardinality.labels(
                        agent=AGENT_OWNER, agent_id=AGENT_ID, backend=_BACKEND_ID
                    ).set(_cardinality)
                except Exception:
                    pass
            session_id = derive_session_id(_raw_sid, caller_identity=_caller_identity)
            # #966: MCP-initiated invocations now get a named span nested
            # under the inbound traceparent (TraceparentASGIMiddleware has
            # already attached the caller's context). Previously the
            # MCP→executor hop produced orphaned spans, so /api/traces
            # cross-agent joins missed tools/call work entirely.
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
                        from executor import run as _run_query_for_mcp

                        response = await _run_query_for_mcp(
                            prompt,
                            session_id,
                            executor._sessions,
                            executor._mcp_servers,
                            executor._agent_md_content,
                            model=None,
                            max_tokens=mcp_max_tokens,
                        )
                    except Exception as _exc:
                        _set_span_error(_mcp_span, _exc)
                        raise
            except Exception as exc:
                logger.error(f"MCP tools/call error: {exc!r}")
                # #1281: tool execution failure is a tool-level error;
                # report it as result.isError=true with a generic text
                # block. The full exception detail is logged server-side
                # above but not leaked to MCP clients (#455).
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
            return JSONResponse(
                {
                    "jsonrpc": "2.0",
                    "id": rpc_id,
                    "result": {"content": [{"type": "text", "text": response}]},
                }
            )

        return JSONResponse(
            {
                "jsonrpc": "2.0",
                "id": rpc_id,
                "error": {"code": -32601, "message": f"Method not found: {method!r}"},
            }
        )

    # OTel in-memory span store (#otel-in-cluster). Serves the Jaeger v1
    # shape so the harness's fan-out aggregator can merge backend spans
    # into the cross-pod trace view.
    #
    # Gated on CONVERSATIONS_AUTH_TOKEN (#709) to match /conversations, /trace,
    # and /mcp. Span attributes carry session IDs (bearer-equivalent), tool
    # input-derived fields, and agent identity — information disclosure across
    # pods/tenants was possible when these routes were unauthenticated.
    def _require_traces_auth(request: Request) -> JSONResponse | None:
        # Fail-closed when the token is missing unless the escape hatch is set (#718).
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
        # /api/traces is a backend-operator endpoint (#932). The trace ring is
        # backend-global and CONVERSATIONS_AUTH_TOKEN is a backend-wide bearer,
        # not a per-A2A-principal credential — any caller holding the bearer
        # can read every trace in the ring. This surface is intended for
        # operator tooling (the harness fan-out aggregator and the dashboard
        # running as the same operator); it is NOT safe to expose to untrusted
        # multi-tenant A2A principals. Mitigations applied here:
        #   * Clamp `limit` to the in-memory cap (OTEL_IN_MEMORY_SPANS, default
        #     1000) so a caller cannot force arbitrarily large responses.
        #   * Reject negative / zero limits that previously produced empty or
        #     undefined slices.
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
        # Offset-based pagination (#1101). The ring holds up to _cap spans
        # so dashboards that used ever-growing `limit` to page through the
        # ring can now walk it with `offset` instead. Negative / invalid
        # offsets clamp to 0; offset past end yields an empty slice.
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
        # #1608: split liveness vs readiness. /health (liveness) returns 200
        # once the process is up, even when boot finished degraded — kubelet
        # must not CrashLoopBackOff a pod that just has empty MCP/agent_md.
        # /health/ready (readiness) returns 503 while starting OR while
        # _boot_degraded_reason is set, removing the pod from Service
        # endpoints. Operators upgrading from <=v0.5.0 must point their K8s
        # readinessProbe at /health/ready (BREAKING change for probe paths).
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
    # Metrics live on a dedicated port (:9000 by default, configurable via
    # METRICS_PORT), NOT on the main app listener (#643, #646). Started
    # inside the lifespan hook below.
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
                # Flush the SQLite WAL and release the connection on
                # graceful shutdown (#713). Guarded on close() presence
                # so InMemoryTaskStore (no close method) still works.
                _close = getattr(task_store, "close", None)
                if callable(_close):
                    try:
                        await _close()
                    except Exception as _close_exc:
                        logger.warning("task_store close error: %r", _close_exc)

    full_app = Starlette(routes=_routes, lifespan=lifespan)
    # Wrap with the ASGI middleware that extracts the inbound traceparent
    # so the A2A SDK's @trace_class spans become children of the harness
    # trace rather than orphaned roots (#otel-cross-pod).
    from otel import TraceparentASGIMiddleware

    full_app = TraceparentASGIMiddleware(full_app)

    logger.info(f"Starting {AGENT_NAME} on {AGENT_HOST}:{BACKEND_PORT}")
    config = uvicorn.Config(full_app, host=AGENT_HOST, port=BACKEND_PORT)
    server = uvicorn.Server(config)

    # Pre-load MCP config, CLAUDE.md, and hooks.yaml before readiness flips
    # (#869). Previously the first parse happened inside each watcher task,
    # so a request arriving immediately after pod start could observe empty
    # MCP servers / empty agent_md / baseline-only hooks. Running the initial
    # loads synchronously on this startup path guarantees the executor state
    # matches on-disk config by the time _set_ready_when_started flips ready.
    #
    # #985: bound total duration with a timeout so a wedged ConfigMap
    # projection or stuck fs mount can't stall startup past the kubelet
    # liveness deadline. On timeout we proceed with whatever the watchers
    # pick up asynchronously — the worst case is a brief window where the
    # executor sees partial config, which is strictly better than an
    # indefinite bind-time hang + pod restart loop.
    _INITIAL_LOADS_TIMEOUT_S = float(os.environ.get("INITIAL_LOADS_TIMEOUT_SECONDS", "10"))
    # #1368: track boot-degraded state so operators can alert on
    # backends that came up with empty MCP/agent_md/hooks. Dashboard
    # reads this via /health.
    global _boot_degraded_reason
    try:
        await asyncio.wait_for(
            executor.perform_initial_loads(),
            timeout=_INITIAL_LOADS_TIMEOUT_S,
        )
    except asyncio.TimeoutError:
        logger.warning(
            "perform_initial_loads exceeded %.1fs — proceeding with startup; "
            "MCP/agent_md/hooks watchers will fill in asynchronously (#985).",
            _INITIAL_LOADS_TIMEOUT_S,
        )
        _boot_degraded_reason = "initial_loads_timeout"
    except Exception as exc:
        logger.error("perform_initial_loads failed: %r — watchers will retry", exc)
        _boot_degraded_reason = f"initial_loads_error:{type(exc).__name__}"

    # Start MCP watcher tasks
    # These watchers (hooks/MCP/agent_md reloaders) are required for correct
    # operation — a persistently crashing watcher should take readiness down
    # via WORKER_MAX_RESTARTS rather than silently crash-loop forever (#585).
    for _w in executor._mcp_watchers():
        _mcp_task = asyncio.create_task(_guarded(_w, critical=True))
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
