import asyncio
import hashlib
import hmac as hmac_mod
import logging
import os
import time
import uuid
from contextlib import AsyncExitStack, asynccontextmanager
from datetime import datetime, timezone

import executor as _executor_module
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
from mcp_body_cap import read_capped_body  # #1673
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

AGENT_NAME = os.environ.get("AGENT_NAME", "openai")
AGENT_HOST = os.environ.get("AGENT_HOST", "0.0.0.0")
BACKEND_PORT = int(os.environ.get("BACKEND_PORT", "8000"))
AGENT_URL = os.environ.get("AGENT_URL", f"http://localhost:{BACKEND_PORT}/")
AGENT_VERSION = os.environ.get("AGENT_VERSION", "0.1.0")
CONVERSATION_LOG = os.environ.get("CONVERSATION_LOG", "/home/agent/logs/conversation.jsonl")
TRACE_LOG = os.environ.get("TRACE_LOG", "/home/agent/logs/tool-activity.jsonl")
AGENT_OWNER = os.environ.get("AGENT_OWNER") or AGENT_NAME
# #1340: fall back to HOSTNAME for uniqueness (see claude comment).
AGENT_ID = os.environ.get("AGENT_ID") or os.environ.get("HOSTNAME") or "openai"
_BACKEND_ID = "openai"
metrics_enabled = parse_bool_env("METRICS_ENABLED")
WORKER_MAX_RESTARTS = int(os.environ.get("WORKER_MAX_RESTARTS", "5"))
CONVERSATIONS_AUTH_TOKEN = os.environ.get("CONVERSATIONS_AUTH_TOKEN", "")

_ready: bool = False
_startup_mono: float = 0.0
start_time: datetime = datetime.now(timezone.utc)


def load_agent_description() -> str:
    for path in ("/home/agent/.openai/agent-card.md", "/home/agent/.codex/agent-card.md"):
        try:
            with open(path) as f:
                return f.read()
        except OSError:
            continue
    return os.environ.get("AGENT_DESCRIPTION", "An OpenAI backend agent.")


def build_agent_card() -> AgentCard:
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
                description="General-purpose task execution via OpenAI.",
                tags=["general", "openai"],
            )
        ],
    )


async def health_start(request: Request) -> JSONResponse:
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
    # #1672: /health is the LIVENESS probe — it returns 200 as soon as the
    # process is up so kubelet does not CrashLoopBackOff a pod that is
    # merely degraded (e.g. an MCP watcher exited normally per #1630). For
    # readiness gating (i.e. removing a degraded pod from Service
    # endpoints) point K8s readinessProbe at /health/ready instead. Mirror
    # of the cycle-1 claude #1608 fix — README docs /health/ready as a
    # universal route but only claude implemented it; openai had the
    # readiness flag flip path in #1630 but no route to surface it.
    if backend_health_checks_total is not None:
        backend_health_checks_total.labels(
            agent=AGENT_OWNER, agent_id=AGENT_ID, backend=_BACKEND_ID, probe="health"
        ).inc()
    elapsed = (datetime.now(timezone.utc) - start_time).total_seconds()
    # #1341: surface agent_owner + agent_id for metric-label parity.
    # #1672: /health is liveness-only and stays 200 once the process is
    # up; the previous "503 while starting" semantic moved to /health/ready.
    return JSONResponse(
        {
            "status": "ok" if _ready else "starting",
            "agent": AGENT_NAME,
            "agent_owner": AGENT_OWNER,
            "agent_id": AGENT_ID,
            "uptime_seconds": elapsed,
        }
    )


async def health_ready(request: Request) -> JSONResponse:
    # #1608 + #1630 + #1672: /health/ready is the READINESS probe — it
    # returns 503 when ``_ready`` is False (process still starting OR an
    # MCP watcher exited normally per #1630, dropping readiness so the
    # pod is removed from Service endpoints without restarting). 200 only
    # when fully ready. Mirror of the cycle-1 claude #1608 fix; openai had
    # the readiness-drop branch (#1630) but no route to surface it, so
    # K8s readinessProbe could never observe the degraded state.
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
        # so we don't leak a suspended coroutine waiting on do_shutdown.wait() (#444).
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass
        raise
    if not supported:
        # App does not implement lifespan — proceed normally, matching claude
        # behaviour (#444, #1278). Do NOT cancel the helper task here: _run()
        # already reached the `finally` that set startup(False) and shutdown(None)
        # before exiting, so the task will complete on its own. Cancelling it
        # races the natural completion and can surface as a spurious
        # CancelledError in logs.
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
    global start_time, _startup_mono
    start_time = datetime.now(timezone.utc)
    _startup_mono = time.monotonic()

    # Initialize _computer_lock here, inside asyncio.run(), so it is always
    # created within the running event loop.  Module-level asyncio.Lock() causes
    # a DeprecationWarning in Python 3.10+ and wrong-loop attachment in 3.12+
    # (#378).  Initializing eagerly before any request arrives eliminates the
    # race in the former lazy check-and-assign inside _build_tools() (#402).
    _executor_module._computer_lock = asyncio.Lock()
    # Mirror the eager init for _sessions_lock (#725) so the single
    # _get_sessions_lock() helper is guaranteed to return the same
    # instance from first request onwards — no double-checked lazy init
    # duplicated across call sites.
    _executor_module._get_sessions_lock()

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
        service_name=os.environ.get("OTEL_SERVICE_NAME") or f"openai-{os.environ.get('AGENT_OWNER', 'unknown')}"
    )

    agent_card = build_agent_card()
    executor = AgentExecutor()
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
        # dashboards can catch openai-agents drift without shelling in.
        if backend_sdk_info is not None:
            try:
                from importlib.metadata import PackageNotFoundError
                from importlib.metadata import version as _pkg_version

                try:
                    _sdk_ver = _pkg_version("openai-agents")
                except PackageNotFoundError:
                    _sdk_ver = "unknown"
                backend_sdk_info.info({"sdk": "openai-agents", "version": _sdk_ver})
            except Exception as _exc:
                logger.warning("backend_sdk_info: failed to resolve openai-agents version: %r", _exc)
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

    # #962: per-request /mcp observability — parity with the claude and
    # gemini transports. The inner/outer split lets every early-return
    # (auth, parse, method-not-found) still stamp
    # backend_mcp_requests_total and observe
    # backend_mcp_request_duration_seconds without peppering metric calls
    # across each branch.
    _MCP_METRIC_LABELS = {
        "agent": AGENT_OWNER,
        "agent_id": AGENT_ID,
        "backend": _BACKEND_ID,
    }

    async def mcp_handler(request: Request) -> JSONResponse:
        """Minimal MCP JSON-RPC server: initialize / tools/list / tools/call.

        Wrapped with per-request metrics (#962): every return path records
        ``backend_mcp_requests_total{method,status}`` and
        ``backend_mcp_request_duration_seconds{method}`` so operators can
        alert on rate / p95 latency using the same dashboard rules they
        use for claude and gemini.
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
        # #961: Fail-closed parity with claude (#718). Previously an empty
        # CONVERSATIONS_AUTH_TOKEN silently disabled auth and any network
        # caller could drive the LLM via tools/call → ask_agent, burning
        # operator API keys. The escape hatch (CONVERSATIONS_AUTH_DISABLED)
        # is the only acknowledged path for local dev.
        if not CONVERSATIONS_AUTH_TOKEN:
            if not auth_disabled_escape_hatch():
                _status_box[0] = "auth_not_configured"
                return JSONResponse({"error": "auth not configured"}, status_code=503)
        else:
            header = request.headers.get("Authorization", "")
            if not hmac_mod.compare_digest(f"Bearer {CONVERSATIONS_AUTH_TOKEN}", header):
                _status_box[0] = "unauthorized"
                return JSONResponse({"error": "unauthorized"}, status_code=401)
        # #1315 / #1673: reject oversize MCP bodies. The Content-Length
        # check is a cheap fast-path that rejects an honest caller
        # declaring a too-large payload before any body is read. The
        # streaming check below is the actual enforcement — a hostile
        # or buggy caller can declare a small (or absent)
        # Content-Length and then send arbitrarily many bytes (e.g.
        # under chunked transfer), so we MUST count actual bytes
        # received and abort BEFORE buffering them into json().
        _MCP_BODY_CAP = 4 * 1024 * 1024
        try:
            declared_len = int(request.headers.get("Content-Length", "") or "-1")
        except ValueError:
            declared_len = -1
        if declared_len > _MCP_BODY_CAP:
            _status_box[0] = "body_too_large"
            return JSONResponse(
                {"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "body too large"}},
                status_code=413,
            )
        # Stream-read into a bounded buffer so the cap is enforced on
        # actual bytes-on-the-wire, not on the caller's declared length.
        _raw, _reason = await read_capped_body(request, _MCP_BODY_CAP)
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
        # #1345: parity with claude — only capture string methods so
        # `{"method": {...}}` doesn't land in telemetry as a repr.
        if isinstance(method, str) and method:
            _method_box[0] = method
        params = body.get("params") or {}

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
                                        # Advertise session_id + max_tokens parity with claude (#1090) so
                                        # generic MCP clients don't silently drop continuation / budget
                                        # controls when switching backends.
                                        "session_id": {
                                            "type": "string",
                                            "description": "Optional session identifier for conversation continuity. "
                                            "A valid UUID is passed through verbatim; any other string is "
                                            "hashed via uuid5(NAMESPACE_URL, value). Omit for a fresh session.",
                                        },
                                        "max_tokens": {
                                            "type": "integer",
                                            "minimum": 1,
                                            "description": "Optional per-call token budget. Positive integers only; "
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
                # #1282: tool-level failures use result.isError=true with
                # a content block, not a JSON-RPC error envelope. Keeps
                # the MCP contract uniform across claude/openai/gemini.
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
            # (positive int; non-positive or invalid is logged and dropped).
            # Shared helper lives in shared/validation.py (#537, #460, #555).
            mcp_max_tokens = parse_max_tokens(
                arguments.get("max_tokens"),
                logger=logger,
                source="MCP tools/call",
            )
            # Caller-bound session_id on openai /mcp (#935). OpenAI currently
            # mints a fresh UUID per call (single-shot sessions — see the
            # cleanup in `finally` below), so there's no ambient resumption.
            # But without derive_session_id wiring, any future resumption
            # work would re-introduce #710/#733 cross-caller hijack on this
            # entrypoint. Route through derive_session_id with a bearer
            # fingerprint caller_identity so the multi-tenant invariant
            # holds uniformly with the A2A path and with claude/gemini /mcp.
            _raw_sid = "".join(c for c in str(arguments.get("session_id") or "").strip()[:256] if c >= " ")
            _bearer_header = request.headers.get("Authorization", "")
            _bearer_token = _bearer_header[len("Bearer ") :] if _bearer_header.startswith("Bearer ") else ""
            _caller_identity = hashlib.sha256(_bearer_token.encode("utf-8")).hexdigest() if _bearer_token else None
            # #1096: multi-turn /mcp parity with claude. When a caller
            # provides an explicit session_id, honour it as a continuation
            # — skip the single-shot nonce-mix and the post-call cleanup
            # so the next call with the same bound session_id resumes the
            # same conversation. When the caller omits session_id, preserve
            # the pre-#1096 single-shot behaviour (nonce-mix + cleanup) so
            # ephemeral MCP clients don't accumulate SQLite rows (#986).
            _caller_requested_continuation = bool(_raw_sid)
            if _caller_requested_continuation:
                _raw_sid_for_derive = _raw_sid
            else:
                # Mix a per-request nonce into the empty raw_sid so
                # derive_session_id yields a distinct id per invocation,
                # matching the pre-#1096 single-shot semantics.
                _request_nonce = uuid.uuid4().hex
                _raw_sid_for_derive = f"\x00{_request_nonce}"
            session_id = derive_session_id(_raw_sid_for_derive, caller_identity=_caller_identity)
            response: str | None = None
            _failed = False
            # #966: Named span nested under the inbound traceparent so
            # cross-agent /api/traces joins see MCP-driven work rather
            # than a dead-ended request-span.
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
                        # #1391: include RPC id so slow calls can be
                        # correlated across the conversation.jsonl /
                        # trace UI without manual joining.
                        "rpc.id": str(rpc_id) if rpc_id is not None else "",
                    },
                ) as _mcp_span:
                    # #1493: route through _acquire_mcp_stack so a
                    # concurrent hot-reload can't aclose() the stack
                    # mid-request. Without the refcount held, a
                    # snapshot alone defeats the #667 reload safety.
                    _mcp_servers_snapshot, _mcp_stack_held = await executor._acquire_mcp_stack()
                    try:
                        from executor import run as _run_for_mcp

                        response = await _run_for_mcp(
                            prompt,
                            session_id,
                            executor._sessions,
                            executor._agent_md_content,
                            model=None,
                            max_tokens=mcp_max_tokens,
                            live_mcp_servers=_mcp_servers_snapshot,
                        )
                    except Exception as exc:
                        _set_span_error(_mcp_span, exc)
                        raise
                    finally:
                        await executor._release_mcp_stack(_mcp_stack_held)
            except Exception as exc:
                _failed = True
                logger.error(f"MCP tools/call error: {exc!r}")
            finally:
                # MCP tools/call sessions are single-shot (#723) by default:
                # each request without a caller-supplied session_id mints
                # a fresh id, so the SQLite session row has no legitimate
                # reuse on a subsequent call. Without explicit cleanup the
                # LRU cache only kicks in for the in-memory dict — the
                # on-disk agent_sessions table grows unbounded for every
                # errored invocation. Delete the row + drop the in-memory
                # entry here so the storage footprint stays O(1).
                #
                # #1096: when the caller opted in to continuation by
                # supplying a session_id, preserve the session state for
                # the next call — skip cleanup on that path.
                if not _caller_requested_continuation:
                    # #1494: serialise the pop under _get_sessions_lock so
                    # we cannot interleave with _track_session's
                    # popitem/move_to_end on the shared OrderedDict and
                    # skew the #506/#725 session gauges.
                    try:
                        async with executor._get_sessions_lock():
                            executor._sessions.pop(session_id, None)
                    except Exception:
                        pass
                    # Import the already-resolved DB path from executor
                    # (#877): re-reading os.environ here drifts from the
                    # module-level constant captured at executor import
                    # time, so if OPENAI_SESSION_DB was mutated in between
                    # (tests, reload paths) the cleanup would run against
                    # a DIFFERENT path than the writes.
                    from executor import OPENAI_SESSION_DB as _db_path

                    if _db_path and _db_path != ":memory:":
                        try:
                            from executor import _delete_sqlite_session as _del_mcp

                            await asyncio.to_thread(_del_mcp, session_id, _db_path)
                        except Exception as _cleanup_exc:
                            logger.warning(
                                "MCP tools/call: failed to clean up session row for %r: %s",
                                session_id,
                                _cleanup_exc,
                            )
            if _failed:
                _status_box[0] = "error"
                # #1282: tool execution failure is a tool-level error;
                # report as result.isError=true with a generic text block.
                # Full exception detail is logged server-side (above) but
                # not leaked to MCP clients (#455).
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

        _status_box[0] = "method_not_found"
        return JSONResponse(
            {
                "jsonrpc": "2.0",
                "id": rpc_id,
                "error": {"code": -32601, "message": f"Method not found: {method!r}"},
            }
        )

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
        # Offset-based pagination (#1101). Parity with claude / gemini.
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
        # #1608 + #1630 + #1672: split liveness vs readiness. /health
        # (liveness) returns 200 once the process is up so kubelet does
        # not CrashLoopBackOff a degraded pod. /health/ready (readiness)
        # returns 503 while starting OR while _ready was dropped by the
        # #1630 normal-exit watcher branch, removing the pod from
        # Service endpoints. Operators upgrading from <=v0.5.0 must
        # repoint their K8s readinessProbe at /health/ready.
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
    # Metrics on dedicated :METRICS_PORT listener (#643, #647).
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
                # executor.close() owns the _browser_pool teardown and is now
                # idempotent (guarded by executor.closed); the previous
                # pre-close here produced a double-close hazard (#555).
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

    # #1095: synchronously populate AGENTS.md + mcp.json + tool config
    # before readiness/server.serve() so a request landing in the first
    # ~100ms after bind doesn't observe empty executor state. Mirrors
    # claude #869. Bounded so a slow/stuck filesystem can't indefinitely
    # delay startup — the watchers will fill in asynchronously on timeout.
    _INITIAL_LOADS_TIMEOUT_S = float(os.environ.get("INITIAL_LOADS_TIMEOUT_SECONDS", "10"))
    try:
        await asyncio.wait_for(
            executor.perform_initial_loads(),
            timeout=_INITIAL_LOADS_TIMEOUT_S,
        )
    except asyncio.TimeoutError:
        logger.warning(
            "perform_initial_loads exceeded %.1fs — proceeding with startup; "
            "AGENTS.md/mcp.json/tool_config watchers will fill in asynchronously (#1095).",
            _INITIAL_LOADS_TIMEOUT_S,
        )
    except Exception as exc:
        logger.error("perform_initial_loads failed: %r — watchers will retry", exc)

    # #1502: the previous comment claimed "none for openai" but
    # _mcp_watchers() returns four watchers (AGENTS.md, mcp.json,
    # config.toml, api_key_file). Start each as a guarded task and
    # install a done_callback that distinguishes normal cancellation
    # (shutdown) from unexpected exits with or without an exception.
    def _make_watcher_done_cb(_wn: str):
        def _cb(t: asyncio.Task) -> None:
            if t.cancelled():
                # Normal shutdown path — nothing to report.
                return
            exc = t.exception()
            if exc is not None:
                logger.error(
                    "MCP watcher %r exited unexpectedly with exception: %r",
                    _wn,
                    exc,
                )
            else:
                # #1630: non-exception, non-cancelled exit means the
                # watcher's while-True loop returned normally (e.g. early
                # return on an unset env var). Log at WARNING and drop
                # readiness so the pod is removed from Service endpoints
                # via /health/ready, mirroring the cycle-1 claude #1608
                # fix. _guarded() does not restart on a normal return, so
                # without this the pod would silently serve traffic with
                # a missing background task.
                logger.warning(
                    "MCP watcher %r exited normally (no exception, not cancelled) — "
                    "background task will not restart on its own; dropping readiness (#1630).",
                    _wn,
                )
                global _ready
                _ready = False

        return _cb

    for _w in executor._mcp_watchers():
        _mcp_task = asyncio.create_task(_guarded(_w))
        _mcp_task.add_done_callback(_make_watcher_done_cb(_w.__name__))
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
