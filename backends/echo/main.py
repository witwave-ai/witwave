"""Echo backend A2A server entrypoint.

Starts an A2A JSON-RPC server bound to :class:`EchoAgentExecutor`, plus a
``/health`` endpoint on the app port and a dedicated ``/metrics`` listener
on ``METRICS_PORT`` (default 9000) gated on ``METRICS_ENABLED``.

Deliberately stripped of the machinery the LLM-bearing backends need
(MCP, conversation persistence, OTel, session binding, hooks) — echo is
a hello-world default AND a reference implementation of what every
backend should do at the baseline. The metrics + tests are reference-
level; the behaviour is not.

Environment:
  AGENT_NAME       — display name on the agent card (default: echo)
  AGENT_OWNER      — agent label value for metrics (default: AGENT_NAME)
  AGENT_ID         — agent_id label value for metrics (default: HOSTNAME)
  AGENT_HOST       — listen host (default: 0.0.0.0)
  BACKEND_PORT     — A2A listen port (default: 8000)
  METRICS_PORT     — dedicated /metrics listener port (default: 9000)
  METRICS_ENABLED  — any truthy value enables the metrics listener
  AGENT_URL        — public URL advertised on the agent card
                     (default: http://localhost:$BACKEND_PORT/)
  AGENT_VERSION    — version string on the agent card + metrics info
                     (default: 0.1.0)
"""

import asyncio
import logging
import os
import time
from datetime import datetime, timezone

import metrics
import prometheus_client
import uvicorn
from a2a.server.apps import A2AStarletteApplication
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.tasks import InMemoryTaskStore
from a2a.types import AgentCapabilities, AgentCard, AgentSkill
from env import parse_bool_env
from executor import EchoAgentExecutor
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Mount, Route

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

AGENT_NAME = os.environ.get("AGENT_NAME", "echo")
AGENT_OWNER = os.environ.get("AGENT_OWNER") or AGENT_NAME
AGENT_ID = os.environ.get("AGENT_ID") or os.environ.get("HOSTNAME") or "echo"
AGENT_HOST = os.environ.get("AGENT_HOST", "0.0.0.0")
BACKEND_PORT = int(os.environ.get("BACKEND_PORT", "8000"))
AGENT_URL = os.environ.get("AGENT_URL", f"http://localhost:{BACKEND_PORT}/")
AGENT_VERSION = os.environ.get("AGENT_VERSION", "0.1.0")
_BACKEND_ID = "echo"
_metrics_enabled = parse_bool_env("METRICS_ENABLED")

_AGENT_DESCRIPTION = (
    "Echo backend — returns a canned response quoting the caller's prompt. "
    "Zero external dependencies; ships as the default backend for `ww agent create`."
)

_LABELS = {"agent": AGENT_OWNER, "agent_id": AGENT_ID, "backend": _BACKEND_ID}

start_time = datetime.now(timezone.utc)
_start_mono = time.monotonic()
_ready = False


def build_agent_card() -> AgentCard:
    """Return the A2A :class:`AgentCard` advertised at ``/.well-known``.

    Uses the module-level ``AGENT_NAME`` / ``AGENT_URL`` /
    ``AGENT_VERSION`` constants (resolved from env at import time) and
    declares a single ``echo`` skill. Streaming is disabled — the echo
    executor produces a single response synchronously and has no
    incremental delivery surface.
    """
    return AgentCard(
        name=AGENT_NAME,
        description=_AGENT_DESCRIPTION,
        url=AGENT_URL,
        version=AGENT_VERSION,
        capabilities=AgentCapabilities(streaming=False),
        default_input_modes=["text/plain"],
        default_output_modes=["text/plain"],
        skills=[
            AgentSkill(
                id="echo",
                name="Echo",
                description="Return a canned response quoting the input prompt.",
                tags=["echo", "onboarding"],
            )
        ],
    )


async def health(request: Request) -> JSONResponse:
    """Liveness/readiness probe handler — serves ``/health``, ``/health/live``, ``/health/ready``.

    Returns HTTP 200 with ``status: "ok"`` once the startup task has
    flipped ``_ready`` to True (i.e. uvicorn has bound the listener),
    and HTTP 503 with ``status: "starting"`` before that. Bumps the
    ``backend_health_checks_total`` counter and refreshes
    ``backend_uptime_seconds`` on each call so Prometheus sees fresh
    values without a separate scrape path. The same handler backs the
    three probe routes — see :func:`build_app` — because the echo
    backend has no readiness gate beyond "the socket is open".
    """
    if metrics.backend_health_checks_total is not None:
        metrics.backend_health_checks_total.labels(**_LABELS, probe="health").inc()
    if metrics.backend_uptime_seconds is not None:
        metrics.backend_uptime_seconds.labels(**_LABELS).set(
            (datetime.now(timezone.utc) - start_time).total_seconds(),
        )
    body = {
        "status": "ok" if _ready else "starting",
        "agent": AGENT_NAME,
        "agent_owner": AGENT_OWNER,
        "agent_id": AGENT_ID,
        "backend": _BACKEND_ID,
        "uptime_seconds": (datetime.now(timezone.utc) - start_time).total_seconds(),
    }
    return JSONResponse(body, status_code=200 if _ready else 503)


async def metrics_handler(request: Request) -> Response:
    """Prometheus scrape handler — serves the default registry exposition.

    Refreshes pull-time gauges (uptime) immediately before generating
    the exposition so each scrape sees current values rather than the
    value at the last event-time update. Returned content-type matches
    ``prometheus_client.CONTENT_TYPE_LATEST`` so both v0.0.4 and
    OpenMetrics scrapers parse it correctly.
    """
    # Refresh gauges that are pull-time rather than event-time.
    if metrics.backend_uptime_seconds is not None:
        metrics.backend_uptime_seconds.labels(**_LABELS).set(
            (datetime.now(timezone.utc) - start_time).total_seconds(),
        )
    data = prometheus_client.generate_latest()
    return Response(data, media_type=prometheus_client.CONTENT_TYPE_LATEST)


def build_app() -> Starlette:
    """Construct the Starlette app used by both main() and the test suite."""
    metrics.init_metrics()
    if metrics.backend_info is not None:
        metrics.backend_info.info(
            {
                "version": AGENT_VERSION,
                "backend": _BACKEND_ID,
                "agent": AGENT_OWNER,
            }
        )
    if metrics.backend_up is not None:
        metrics.backend_up.labels(**_LABELS).set(1)

    executor = EchoAgentExecutor(labels=_LABELS)
    handler = DefaultRequestHandler(
        agent_executor=executor,
        task_store=InMemoryTaskStore(),
    )
    a2a_app = A2AStarletteApplication(agent_card=build_agent_card(), http_handler=handler)
    a2a_built = a2a_app.build()

    routes = [
        Route("/health", health),
        Route("/health/live", health),  # alias — uniform liveness surface across harness/backends/MCP tools
        Route("/health/ready", health),  # alias — echo has no real readiness gate, so liveness == readiness
        Mount("/", app=a2a_built),
    ]
    return Starlette(routes=routes)


async def main() -> None:
    """Process entry point — boot the Starlette app and uvicorn server.

    Builds the A2A Starlette app via :func:`build_app`, starts uvicorn
    on ``AGENT_HOST:BACKEND_PORT``, optionally starts the side-channel
    metrics server when ``_metrics_enabled`` is True, and runs a
    ``mark_ready_when_started`` companion coroutine that polls
    ``server.started`` and flips the module-level ``_ready`` flag plus
    the ``backend_startup_duration_seconds`` gauge. Both coroutines run
    concurrently via :func:`asyncio.gather` so a startup failure in
    either propagates immediately.
    """
    global _ready

    app = build_app()

    logger.info(f"Starting {AGENT_NAME} on {AGENT_HOST}:{BACKEND_PORT}")
    config = uvicorn.Config(app, host=AGENT_HOST, port=BACKEND_PORT, log_level="info")
    server = uvicorn.Server(config)

    if _metrics_enabled:
        from metrics_server import start_metrics_server

        start_metrics_server(metrics_handler, logger=logger)

    async def mark_ready_when_started() -> None:
        # Poll uvicorn's started flag rather than guess at a fixed delay.
        # Echo has no subsystems to warm up, so readiness lands within
        # a few event-loop ticks of the socket binding.
        while not server.started:
            await asyncio.sleep(0.05)
        global _ready
        _ready = True
        if metrics.backend_startup_duration_seconds is not None:
            metrics.backend_startup_duration_seconds.labels(**_LABELS).set(
                time.monotonic() - _start_mono,
            )
        logger.info(f"{AGENT_NAME} ready")

    await asyncio.gather(server.serve(), mark_ready_when_started())


if __name__ == "__main__":
    asyncio.run(main())
