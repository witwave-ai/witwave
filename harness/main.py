import asyncio
import hashlib
import hmac as hmac_mod
import logging
import os
import random
import time
import uuid
from contextlib import AsyncExitStack, asynccontextmanager
from datetime import datetime, timezone

import prometheus_client
import prometheus_client.exposition
import uvicorn
from a2a.server.apps import A2AStarletteApplication
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.tasks import InMemoryTaskStore
from a2a.types import (
    AgentCapabilities,
    AgentCard,
    AgentSkill,
)
from bus import (
    HookDecisionEvent,
    Message,
    MessageBus,
    _emit_hook_decision_event_stream,
    publish_hook_decision,
    subscribe_hook_decision,
)
from continuations import ContinuationRunner
from executor import AgentExecutor
from executor import _guarded as _guarded_from_executor
from executor import run as executor_run
from executor import run_consensus as executor_run_consensus
from heartbeat import heartbeat_runner, load_heartbeat
from jobs import JobRunner
from metrics import (
    harness_adhoc_fires_total,
    harness_backend_reachable,
    harness_bus_consumer_idle_seconds,
    harness_bus_error_processing_duration_seconds,
    harness_bus_errors_total,
    harness_bus_last_processed_timestamp_seconds,
    harness_bus_messages_total,
    harness_bus_processing_duration_seconds,
    harness_bus_wait_seconds,
    harness_event_loop_lag_seconds,
    harness_event_stream_inbound_rejected_total,
    harness_health_checks_total,
    harness_info,
    harness_prompt_env_substitutions_total,
    harness_startup_duration_seconds,
    harness_triggers_requests_total,
    harness_up,
    harness_uptime_seconds,
)
from sqlite_task_store import SqliteTaskStore
from tasks import TaskRunner
from triggers import TriggerItem, TriggerRunner
from webhooks import WebhookRunner

# Wire shared/prompt_env.substitutions_total (#1089) so every
# resolve_prompt_env() call feeds the Counter. Idempotent — a None
# counter (METRICS_ENABLED unset) leaves the module's fallback in
# place and the _bump() helper no-ops.
try:
    import prompt_env as _prompt_env_mod  # shared/prompt_env.py

    _prompt_env_mod.substitutions_total = harness_prompt_env_substitutions_total
except Exception:
    pass
from conversations import (
    auth_disabled_escape_hatch,
    make_proxy_conversations_handler,
    make_proxy_trace_handler,
)
from conversations_proxy import (
    fetch_backend_conversations,
    fetch_backend_trace,
)
from env import parse_bool_env
from metrics_proxy import fetch_backend_metrics
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Mount, Route

# Log format includes trace_id (#625) so `kubectl logs` lines correlate
# directly to the trace_ids emitted into conversation JSONL / Jaeger / Tempo.
# The trailing-suffix placement keeps legacy parsers that split on `:` intact.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s trace_id=%(trace_id)s",
)
# Install the filter *after* basicConfig so it attaches to the default
# StreamHandler basicConfig created above; without this the %(trace_id)s
# reference raises KeyError on records that never passed through a filter.
from tracing import install_trace_id_log_filter  # noqa: E402 — must run after basicConfig

install_trace_id_log_filter()
logger = logging.getLogger(__name__)

AGENT_NAME = os.environ.get("AGENT_NAME", "witwave")
HARNESS_HOST = os.environ.get("HARNESS_HOST", "0.0.0.0")
HARNESS_PORT = int(os.environ.get("HARNESS_PORT", "8000"))
HARNESS_URL = os.environ.get("HARNESS_URL", f"http://localhost:{HARNESS_PORT}/")
AGENT_VERSION = os.environ.get("AGENT_VERSION", "0.1.0")
metrics_enabled = parse_bool_env("METRICS_ENABLED")
WORKER_MAX_RESTARTS = int(os.environ.get("WORKER_MAX_RESTARTS", "5"))
TASK_TIMEOUT_SECONDS = int(os.environ.get("TASK_TIMEOUT_SECONDS", "300"))

# CORS_ALLOW_ORIGINS: comma-separated list of allowed origins (e.g.
# "http://localhost:3000,https://ui.example.com").  When unset the server
# falls back to the permissive wildcard and logs a warning at startup so
# operators know it is not restricted to known origins.
_cors_env = os.environ.get("CORS_ALLOW_ORIGINS", "")
CORS_ALLOW_ORIGINS: list[str] = [o.strip() for o in _cors_env.split(",") if o.strip()] if _cors_env else []

_ready: bool = False
_executor: "AgentExecutor | None" = None
_startup_mono: float = 0.0
start_time: datetime = datetime.now(timezone.utc)

# How long (seconds) to cache aggregated backend metrics to avoid fan-out HTTP
# calls on every Prometheus scrape.  Override via METRICS_CACHE_TTL env var.
METRICS_CACHE_TTL = float(os.environ.get("METRICS_CACHE_TTL", "15"))
_metrics_cache_body: str = ""
_metrics_cache_expires: float = 0.0
# Single-flight lock: serialises concurrent refreshes at the cache-expiry
# boundary so N concurrent Prometheus scrapers do not all fan out to every
# backend's /metrics endpoint (see #536).  The lock guards only the refresh
# critical section; the cheap witwave-own generate_latest() call stays outside.
_metrics_cache_lock: asyncio.Lock = asyncio.Lock()

# Short-TTL cache for the health_ready backend sweep (#542).  Readiness probes
# typically fire every 5-10s from Kubernetes plus dashboard polling and any
# external monitor; without this cache each probe fans out a fresh HTTP GET to
# every backend's /health.  1s TTL (tightened in #703) still collapses a burst
# of concurrent probes into a single sweep while keeping the window in which a
# freshly-dead backend can masquerade as healthy to ~1s. Override via
# HEALTH_READY_CACHE_TTL. Cache is also invalidated explicitly via
# invalidate_health_ready_cache() when an observed A2A error says a backend
# just flipped unreachable.
HEALTH_READY_CACHE_TTL = float(os.environ.get("HEALTH_READY_CACHE_TTL", "1"))
_health_ready_cache: "tuple[int, dict] | None" = None
_health_ready_expires: float = 0.0
_health_ready_lock: asyncio.Lock = asyncio.Lock()


def invalidate_health_ready_cache() -> None:
    """Drop the health-ready cache so the next probe re-sweeps backends.

    Called from A2A backend error paths (#703) so a downstream crash
    surfaces on the very next readiness probe rather than waiting out
    the TTL. Safe to call from any task — no await, no lock (we simply
    reset the expiry sentinel; the single-flight lock is re-acquired
    when the next probe runs).
    """
    global _health_ready_cache, _health_ready_expires
    _health_ready_cache = None
    _health_ready_expires = 0.0


def _check_trigger_auth(request: Request, item: TriggerItem, body_bytes: bytes) -> bool:
    """Validate trigger request auth. HMAC takes priority over Bearer token.

    At least one auth mechanism must be configured (secret-env-var on the trigger
    definition or TRIGGERS_AUTH_TOKEN in the environment).  When neither is present
    the request is rejected so that unconfigured triggers are not open to any caller.
    """
    if item.secret_env_var:
        secret = os.environ.get(item.secret_env_var, "")
        if not secret:
            logger.warning(
                f"Trigger '{item.name}': 'secret-env-var' is set to {item.secret_env_var!r} "
                "but the environment variable is absent or empty — request rejected."
            )
            return False
        expected = "sha256=" + hmac_mod.new(secret.encode(), body_bytes, hashlib.sha256).hexdigest()
        return hmac_mod.compare_digest(expected, request.headers.get("X-Hub-Signature-256", ""))

    # #1272: fail-closed on whitespace-only values (common operator footgun
    # via `kubectl create secret --from-literal` with a trailing newline).
    auth_token = os.environ.get("TRIGGERS_AUTH_TOKEN", "")
    auth_token_effective = auth_token.strip()
    if auth_token and not auth_token_effective:
        logger.warning(
            f"Trigger '{item.name}': TRIGGERS_AUTH_TOKEN is whitespace-only — "
            "treating as unset (fail-closed). Fix the env var to enable auth."
        )
    if auth_token_effective:
        header = request.headers.get("Authorization", "")
        return hmac_mod.compare_digest(f"Bearer {auth_token_effective}", header)

    logger.warning(
        f"Trigger '{item.name}': no authentication is configured (set secret-env-var in the "
        "trigger file or set TRIGGERS_AUTH_TOKEN) — request rejected."
    )
    return False


#: Anti-CSRF header required on all ad-hoc run endpoints (#927). Browsers
#: treat this as a non-simple header, forcing a CORS preflight and — when the
#: origin is not explicitly trusted — blocking the request before the bearer
#: ever leaves the page. A stored-XSS payload on the dashboard origin can
#: still mint this header, but combined with stricter CORS this closes the
#: third-party CSRF path that existed when only the bearer was required.
ADHOC_CSRF_HEADER = "X-Ad-Hoc-Run"
ADHOC_CSRF_VALUE = "1"


def _check_adhoc_csrf(request: Request) -> bool:
    """Return True when the ad-hoc request carries the anti-CSRF header."""
    return request.headers.get(ADHOC_CSRF_HEADER, "") == ADHOC_CSRF_VALUE


def _check_adhoc_auth(request: Request) -> bool:
    """Validate ad-hoc run-endpoint auth.

    Requires a distinct ``ADHOC_RUN_AUTH_TOKEN`` bearer token (#700). Falling
    back to ``TRIGGERS_AUTH_TOKEN`` previously let the compromise of a single
    shared secret unlock heterogeneous privileged endpoints; the dedicated
    env var keeps blast radii scoped per endpoint family. When neither the
    dedicated token is configured nor the legacy variable is present the
    request is rejected so these endpoints are never open by default.
    """
    auth_token = os.environ.get("ADHOC_RUN_AUTH_TOKEN", "")
    if not auth_token:
        # Migration aid (#700): surface an ERROR if the caller presents a
        # bearer that only matches the legacy shared TRIGGERS_AUTH_TOKEN so
        # operators notice the dropped fallback before rollout.
        legacy = os.environ.get("TRIGGERS_AUTH_TOKEN", "")
        if legacy:
            header = request.headers.get("Authorization", "")
            if header and hmac_mod.compare_digest(f"Bearer {legacy}", header):
                logger.error(
                    "Ad-hoc run endpoint rejected: ADHOC_RUN_AUTH_TOKEN is not "
                    "configured and the implicit TRIGGERS_AUTH_TOKEN fallback "
                    "was removed in #700. Set ADHOC_RUN_AUTH_TOKEN to re-enable."
                )
            else:
                logger.warning("Ad-hoc run endpoint rejected: ADHOC_RUN_AUTH_TOKEN is not configured.")
        else:
            logger.warning("Ad-hoc run endpoint rejected: ADHOC_RUN_AUTH_TOKEN is not configured.")
        return False
    header = request.headers.get("Authorization", "")
    return hmac_mod.compare_digest(f"Bearer {auth_token}", header)


async def hook_decision_event_handler(request: Request) -> JSONResponse:
    """Receive a backend-originated hook.decision event (#641).

    Backends (claude today, openai/gemini in a follow-up) POST
    the structured :class:`HookDecisionEvent` shape here whenever a
    PreToolUse hook finalises a decision.  The handler authenticates the
    caller with a bearer token, parses the body into the dataclass, and
    publishes to the in-process side-channel via ``publish_hook_decision``
    so the ``WebhookRunner`` subscription installed at startup fans the
    event out to every webhook that opted in to ``hook.decision``.

    The endpoint is fail-safe: when no auth token is configured it rejects
    every request rather than silently accepting internal traffic.
    """
    if not HOOK_EVENTS_AUTH_TOKEN:
        # #700: log ERROR when the caller presents a bearer that only matches
        # the legacy TRIGGERS_AUTH_TOKEN fallback so operators notice the
        # dropped implicit fallback before rollout.
        legacy = os.environ.get("TRIGGERS_AUTH_TOKEN", "")
        if legacy:
            header = request.headers.get("Authorization", "")
            if header and hmac_mod.compare_digest(f"Bearer {legacy}", header):
                logger.error(
                    "POST /internal/events/hook-decision rejected: "
                    "HOOK_EVENTS_AUTH_TOKEN is not configured and the implicit "
                    "TRIGGERS_AUTH_TOKEN fallback was removed in #700. Set "
                    "HOOK_EVENTS_AUTH_TOKEN to re-enable."
                )
                return JSONResponse({"error": "unauthorized"}, status_code=401)
        logger.warning(
            "POST /internal/events/hook-decision rejected: no auth token configured (set HOOK_EVENTS_AUTH_TOKEN)."
        )
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    header = request.headers.get("Authorization", "")
    if not hmac_mod.compare_digest(f"Bearer {HOOK_EVENTS_AUTH_TOKEN}", header):
        return JSONResponse({"error": "unauthorized"}, status_code=401)

    # Bounded read: backends send a small JSON blob; reject oversize bodies
    # before buffering rather than letting an adversarial caller pin memory.
    try:
        declared_len = int(request.headers.get("Content-Length", "") or "-1")
    except ValueError:
        declared_len = -1
    if declared_len > MAX_HOOK_EVENT_BODY_BYTES:
        return JSONResponse({"error": "request body too large"}, status_code=413)

    chunks: list[bytes] = []
    total = 0
    async for chunk in request.stream():
        if not chunk:
            continue
        total += len(chunk)
        if total > MAX_HOOK_EVENT_BODY_BYTES:
            return JSONResponse({"error": "request body too large"}, status_code=413)
        chunks.append(chunk)
    body_bytes = b"".join(chunks)

    import json as _json

    try:
        payload = _json.loads(body_bytes.decode("utf-8") or "{}")
    except (UnicodeDecodeError, _json.JSONDecodeError) as exc:
        logger.warning("hook-decision event: malformed JSON body: %r", exc)
        return JSONResponse({"error": "malformed json"}, status_code=400)
    if not isinstance(payload, dict):
        return JSONResponse({"error": "payload must be a JSON object"}, status_code=400)

    # Required fields.  Missing or non-string values are rejected so the
    # downstream listener (WebhookRunner.fire_hook_decision) always sees
    # the shape it documents.
    #
    # Per-field length caps (#924): even with bearer auth, the body
    # fields flow into webhook payloads and structured logs. Cap each
    # string so a compromised/abusive backend cannot drive
    # log-injection or swell outbound webhook bodies. 4 KiB per field
    # is well above any legitimate tool / rule / reason string.
    def _cap(raw: object, limit: int = 4096) -> str:
        s = str(raw or "")
        if len(s) > limit:
            return s[:limit] + "...[truncated]"
        return s

    try:
        event = HookDecisionEvent(
            agent=_cap(payload.get("agent"), 256),
            session_id=_cap(payload.get("session_id"), 256),
            tool=_cap(payload.get("tool"), 256),
            decision=_cap(payload.get("decision"), 64),
            rule_name=_cap(payload.get("rule_name"), 256),
            reason=_cap(payload.get("reason"), 4096),
            source=_cap(payload.get("source"), 256),
            traceparent=(_cap(payload["traceparent"], 256) if payload.get("traceparent") else None),
        )
    except Exception as exc:  # defensive — dataclass construction is trivial
        logger.warning("hook-decision event: failed to build HookDecisionEvent: %r", exc)
        return JSONResponse({"error": "malformed event"}, status_code=400)

    if not event.decision:
        return JSONResponse({"error": "decision is required"}, status_code=400)

    publish_hook_decision(event)
    return JSONResponse({"status": "accepted"}, status_code=202)


# Hard cap on the POST /internal/events/publish body (#1110 phase 3).
# Event payloads are small JSON blobs (envelope + a type-specific payload
# with a handful of fields). 64 KiB is comfortable headroom while still
# rejecting clearly-abusive bodies before any parse / validate cost.
MAX_EVENT_PUBLISH_BODY_BYTES = 65_536
# Per-field string cap applied to payload fields before fan-out (#924
# mirror). Prevents a compromised backend from driving log-injection or
# swelling outbound SSE bodies.
MAX_EVENT_PUBLISH_FIELD_BYTES = 4096


def _bump_inbound_rejected(reason: str) -> None:
    try:
        if harness_event_stream_inbound_rejected_total is not None:
            harness_event_stream_inbound_rejected_total.labels(reason=reason).inc()
    except Exception:
        pass


async def event_publish_handler(request: Request) -> JSONResponse:
    """Receive a backend-originated generic event (#1110 phase 3).

    Mirrors ``/internal/events/hook-decision`` for auth + body bounding
    but instead of publishing via the hook.decision side-channel it
    validates the envelope against ``shared/event_schema.py`` and, on
    success, republishes through the in-process SSE stream via
    :func:`events.get_event_stream().publish`.

    Returns:
    * 204 on successful fan-out.
    * 401 when the bearer token is missing/wrong.
    * 400 on malformed JSON / failing schema validation.
    * 413 when the body exceeds :data:`MAX_EVENT_PUBLISH_BODY_BYTES`.

    The endpoint is fail-safe: no token configured → every request is
    rejected rather than silently accepted.
    """
    if not HOOK_EVENTS_AUTH_TOKEN:
        logger.warning("POST /internal/events/publish rejected: no auth token configured (set HOOK_EVENTS_AUTH_TOKEN).")
        _bump_inbound_rejected("auth")
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    header = request.headers.get("Authorization", "")
    if not hmac_mod.compare_digest(f"Bearer {HOOK_EVENTS_AUTH_TOKEN}", header):
        _bump_inbound_rejected("auth")
        return JSONResponse({"error": "unauthorized"}, status_code=401)

    # Bounded read.
    try:
        declared_len = int(request.headers.get("Content-Length", "") or "-1")
    except ValueError:
        declared_len = -1
    if declared_len > MAX_EVENT_PUBLISH_BODY_BYTES:
        _bump_inbound_rejected("over_cap")
        return JSONResponse({"error": "request body too large"}, status_code=413)

    chunks: list[bytes] = []
    total = 0
    async for chunk in request.stream():
        if not chunk:
            continue
        total += len(chunk)
        if total > MAX_EVENT_PUBLISH_BODY_BYTES:
            _bump_inbound_rejected("over_cap")
            return JSONResponse({"error": "request body too large"}, status_code=413)
        chunks.append(chunk)
    body_bytes = b"".join(chunks)

    # Delegate parsing + validation + fan-out to the testable kernel in
    # harness/events.py. Keeping the handler thin here means the core
    # contract can be exercised from tests that don't need to import
    # the A2A SDK / uvicorn / prometheus_client layer.
    from events import parse_and_publish_envelope as _parse_publish

    status, err = _parse_publish(
        body_bytes,
        rejected_counter=harness_event_stream_inbound_rejected_total,
    )
    if status == 204:
        return Response(status_code=204)
    return JSONResponse({"error": err or "invalid"}, status_code=status)


# Hard cap on the body size the backend→harness hook.decision POST will buffer (#641).
# The shape is small (agent + session_id + tool + decision + rule_name + reason +
# source + optional traceparent), so 64 KiB is comfortably above worst-case
# payloads while rejecting anything that could only be abusive.
MAX_HOOK_EVENT_BODY_BYTES = 65_536

# Bearer token expected on POST /internal/events/hook-decision (#641, #700).
# Requires a dedicated env var with no implicit fallback to TRIGGERS_AUTH_TOKEN —
# compromise of one shared secret must not unlock heterogeneous privileged
# endpoints.  The endpoint is fail-safe: when no token is configured every
# request is rejected rather than silently open.
HOOK_EVENTS_AUTH_TOKEN = os.environ.get("HOOK_EVENTS_AUTH_TOKEN", "")


# Hard cap on the body size the ad-hoc run endpoints will buffer. These endpoints
# do not use the body for prompt content (the prompt comes from the item's
# frontmatter/content in the .md file), so any body is effectively ignored —
# but we still drain a bounded amount to avoid silently buffering large payloads
# or leaving the connection in a weird state.
MAX_ADHOC_BODY_BYTES = 65_536


def load_agent_description() -> str:
    """Return the agent's free-form description text.

    Reads ``/home/agent/.witwave/agent-card.md`` when present; on any
    ``OSError`` (file missing, unreadable, etc.) falls back to the
    ``AGENT_DESCRIPTION`` environment variable, then to a generic
    ``"A Claude Code agent."`` literal. The returned string becomes the
    ``description`` field of the A2A ``AgentCard`` served at
    ``/.well-known/agent.json``.
    """
    try:
        with open("/home/agent/.witwave/agent-card.md") as f:
            return f.read()
    except OSError:
        return os.environ.get("AGENT_DESCRIPTION", "A Claude Code agent.")


def build_agent_card() -> AgentCard:
    """Construct the A2A ``AgentCard`` advertised by this harness.

    Composes the card from module-level globals (``AGENT_NAME``,
    ``HARNESS_URL``, ``AGENT_VERSION``) and the description loaded via
    :func:`load_agent_description`. Capabilities advertise streaming;
    a single ``general`` skill is registered. Callable repeatedly:
    nothing is cached, so each call re-reads the agent-card markdown
    file and so reflects in-place edits without a restart.
    """
    return AgentCard(
        name=AGENT_NAME,
        description=load_agent_description(),
        url=HARNESS_URL,
        version=AGENT_VERSION,
        capabilities=AgentCapabilities(streaming=True),
        default_input_modes=["text/plain"],
        default_output_modes=["text/plain"],
        skills=[
            AgentSkill(
                id="general",
                name="General",
                description="General-purpose task execution.",
                tags=["general"],
            )
        ],
    )


async def health_start(request: Request) -> JSONResponse:
    """Kubernetes startup-probe handler.

    Returns 200 ``{"status": "ok"}`` once the module-level ``_ready``
    flag has been set (which :func:`_set_ready_when_started` flips
    after uvicorn reports started); returns 503
    ``{"status": "starting"}`` otherwise. Increments the
    ``harness_health_checks_total{probe="start"}`` counter on every
    call when metrics are enabled.
    """
    if harness_health_checks_total is not None:
        harness_health_checks_total.labels(probe="start").inc()
    if _ready:
        return JSONResponse({"status": "ok"})
    return JSONResponse({"status": "starting"}, status_code=503)


async def health_live(request: Request) -> JSONResponse:
    """Kubernetes liveness-probe handler.

    Always returns 200 with ``{"status": "ok", "agent": AGENT_NAME,
    "uptime_seconds": <float>}``. Liveness is deliberately decoupled
    from backend reachability — a transiently unreachable backend
    must not cause the kubelet to restart the harness pod (that's
    what readiness is for). Increments the
    ``harness_health_checks_total{probe="live"}`` counter when
    metrics are enabled.
    """
    if harness_health_checks_total is not None:
        harness_health_checks_total.labels(probe="live").inc()
    elapsed = (datetime.now(timezone.utc) - start_time).total_seconds()
    return JSONResponse({"status": "ok", "agent": AGENT_NAME, "uptime_seconds": elapsed})


async def health_ready(request: Request) -> JSONResponse:
    """Kubernetes readiness-probe handler.

    Composes readiness from three signals:

    1. Module-level ``_ready`` (uvicorn has started) — 503 ``starting``
       otherwise.
    2. ``executor.backends_reload_consecutive_failures`` against the
       ``BACKEND_RELOAD_FAILURE_THRESHOLD`` environment variable (#702)
       — 503 ``backends-config-stale`` once the threshold is reached.
    3. The set of configured backend URLs is non-empty — 503
       ``no-backends-configured`` otherwise (#864).
    4. Per-backend reachability probed via ``GET /health/ready``
       (falling back to ``/health`` on 404 so the documented echo-only
       exception still passes) — 503 ``degraded`` listing the unhealthy
       backend ids when any probe fails.

    Probe results are cached for ``HEALTH_READY_CACHE_TTL`` seconds and
    refreshed under ``_health_ready_lock`` so N concurrent probes
    collapse into one backend fan-out. Stamps
    ``harness_backend_reachable{backend=<id>}`` once per refresh.
    """
    global _health_ready_cache, _health_ready_expires
    if harness_health_checks_total is not None:
        harness_health_checks_total.labels(probe="ready").inc()
    if not _ready:
        return JSONResponse({"status": "starting"}, status_code=503)
    # Surface stale backend.yaml (#702). After the reload watcher sees
    # repeated parse/validation failures, readiness flips to 503 so the
    # operator cannot miss the mis-config via HTTP probes alone.
    _reload_failure_threshold = int(os.environ.get("BACKEND_RELOAD_FAILURE_THRESHOLD", "3"))
    if _executor is not None and _executor.backends_reload_consecutive_failures >= _reload_failure_threshold:
        return JSONResponse(
            {
                "status": "degraded",
                "reason": "backends-config-stale",
                "consecutive_failures": _executor.backends_reload_consecutive_failures,
            },
            status_code=503,
        )
    backend_configs = [b._config for b in _executor._backends.values() if b._config.url] if _executor else []
    if not backend_configs:
        # Zero usable backend URLs means every A2A request will raise
        # "No backend configured" — the pod is not ready for traffic
        # (#864). Returning 200 here let the Service endpoint accept
        # connections that could never succeed; flip to 503 so callers
        # and the Service controller both see the degraded state.
        return JSONResponse(
            {"status": "degraded", "reason": "no-backends-configured"},
            status_code=503,
        )

    # Fast path: serve from cache when not yet expired.  Single-flight lock
    # serialises refreshes so N concurrent probes collapse into one sweep.
    now = time.monotonic()
    if _health_ready_cache is not None and now < _health_ready_expires:
        status_code, body = _health_ready_cache
        return JSONResponse(body, status_code=status_code)

    async with _health_ready_lock:
        now = time.monotonic()
        if _health_ready_cache is not None and now < _health_ready_expires:
            status_code, body = _health_ready_cache
            return JSONResponse(body, status_code=status_code)

        import httpx

        async def _probe(backend, client) -> bool:
            # Probe /health/ready (readiness) not /health (liveness). The
            # cycle-1 #1608 + #1672 split made /health liveness-only — it
            # returns 200 even while a backend is still initialising.
            # Querying /health here would pretend the harness is ready
            # before its backends actually are, defeating the readiness
            # contract documented in README. Use /health/ready instead so
            # the harness's own readiness reflects backend readiness.
            #
            # `echo` is the documented exception (backends/echo/README.md
            # intentional-non-scope list — A2A + /health only, no
            # /health/ready). A 404 here means the backend doesn't ship
            # /health/ready but might still be alive — fall back to
            # /health so an echo-only agent doesn't hang at 1/2 Running
            # forever. Other transport errors still mean unreachable.
            base = backend.url.rstrip("/")
            try:
                resp = await client.get(base + "/health/ready")
            except Exception:
                return False
            if resp.status_code == 200:
                return True
            if resp.status_code == 404:
                try:
                    resp = await client.get(base + "/health")
                    return resp.status_code == 200
                except Exception:
                    return False
            return False

        async with httpx.AsyncClient(timeout=3.0) as client:
            results = await asyncio.gather(*[_probe(b, client) for b in backend_configs])

        # Stamp per-backend reachability gauge (#619). This runs inside the
        # refresh critical section (once per TTL) rather than on every probe,
        # so the metric cost scales with refresh frequency, not probe rate.
        if harness_backend_reachable is not None:
            for b, ok in zip(backend_configs, results, strict=True):
                harness_backend_reachable.labels(backend=b.id).set(1 if ok else 0)

        if not all(results):
            unhealthy = [b.id for b, ok in zip(backend_configs, results, strict=True) if not ok]
            body = {"status": "degraded", "unhealthy_backends": unhealthy}
            status_code = 503
        else:
            body = {"status": "ready"}
            status_code = 200

        _health_ready_cache = (status_code, body)
        _health_ready_expires = time.monotonic() + HEALTH_READY_CACHE_TTL

    return JSONResponse(body, status_code=status_code)


@asynccontextmanager
async def _sub_app_lifespan(app):
    """Drive the ASGI lifespan protocol on a sub-app.

    Uses the standard ASGI lifespan scope rather than Starlette-private
    attributes, so this remains correct across Starlette version upgrades.
    Apps that do not support the lifespan scope return before sending
    ``lifespan.startup.complete``; the finally block detects this and skips
    propagation without raising.
    """
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
        await task


def _guarded(coro_fn, *args, restart_delay: float = 5.0, critical: bool = False):
    """Thin wrapper around executor._guarded that wires up readiness callbacks.

    When critical=True, on_not_ready marks the agent not-ready after
    WORKER_MAX_RESTARTS consecutive crashes; on_recovered restores readiness
    once the worker runs long enough without crashing (#363).
    """

    def _on_not_ready():
        global _ready
        logger.error(
            f"Task {coro_fn.__name__!r} has crashed {WORKER_MAX_RESTARTS} consecutive times — marking agent not ready"
        )
        _ready = False

    def _on_recovered():
        global _ready
        if not _ready:
            logger.info(f"Task {coro_fn.__name__!r} recovered — marking agent ready")
            _ready = True

    return _guarded_from_executor(
        coro_fn,
        *args,
        restart_delay=restart_delay,
        max_restarts=WORKER_MAX_RESTARTS,
        on_not_ready=_on_not_ready if critical else None,
        on_recovered=_on_recovered if critical else None,
    )


async def _event_loop_monitor() -> None:
    _interval = 1.0
    while True:
        _before = time.monotonic()
        await asyncio.sleep(_interval)
        lag = time.monotonic() - _before - _interval
        if lag > 0 and harness_event_loop_lag_seconds is not None:
            harness_event_loop_lag_seconds.observe(lag)


async def bus_worker(bus: MessageBus, executor: AgentExecutor) -> None:
    """Long-running consumer that drains ``MessageBus`` into the executor.

    Loops forever awaiting :meth:`MessageBus.receive` and dispatches
    each message through :meth:`AgentExecutor.process_bus`. Records
    per-kind metrics: ``harness_bus_messages_total``,
    ``harness_bus_consumer_idle_seconds``, ``harness_bus_wait_seconds``,
    ``harness_bus_processing_duration_seconds`` (and the
    ``_error_processing_duration_seconds`` counterpart on the failure
    path), plus ``harness_bus_errors_total`` and
    ``harness_bus_last_processed_timestamp_seconds``.

    Exceptions raised by ``process_bus`` are logged and swallowed so a
    single bad message cannot kill the worker. In the ``finally`` block
    the function cancels any unresolved ``message.result`` future and
    releases the bus's dedup slot for ``message.kind`` (#514) — this
    runs unconditionally so error paths cannot starve subsequent
    ``try_send`` calls for the same kind.
    """
    logger.info("Message bus worker started.")
    _idle_start = time.monotonic()
    while True:
        message = await bus.receive()
        if harness_bus_consumer_idle_seconds is not None:
            harness_bus_consumer_idle_seconds.observe(time.monotonic() - _idle_start)
        if harness_bus_messages_total is not None:
            harness_bus_messages_total.labels(kind=message.kind).inc()
        if harness_bus_wait_seconds is not None and message.enqueued_at:
            harness_bus_wait_seconds.labels(kind=message.kind).observe(time.monotonic() - message.enqueued_at)
        t0 = time.monotonic()
        try:
            await executor.process_bus(message)
        except Exception as e:
            logger.error("Bus worker error processing message kind=%r: %s", message.kind, e, exc_info=True)
            if harness_bus_errors_total is not None:
                harness_bus_errors_total.inc()
            if harness_bus_error_processing_duration_seconds is not None:
                harness_bus_error_processing_duration_seconds.labels(kind=message.kind).observe(time.monotonic() - t0)
        finally:
            if message.result is not None and not message.result.done():
                message.result.cancel()
            # Release the dedup slot only after process_bus finishes (#514).
            # Holding it across the backend call lets try_send correctly skip
            # a second scheduled fire while the first is still in-flight. This
            # must run in finally so error paths also clear the slot; otherwise
            # a failed message would starve all future try_send for that kind.
            bus.release_pending(message.kind)
            if harness_bus_processing_duration_seconds is not None:
                harness_bus_processing_duration_seconds.labels(kind=message.kind).observe(time.monotonic() - t0)
            if harness_bus_last_processed_timestamp_seconds is not None:
                harness_bus_last_processed_timestamp_seconds.set(time.time())
            _idle_start = time.monotonic()


async def _set_ready_when_started(server: uvicorn.Server) -> None:
    while not server.started:
        await asyncio.sleep(0.05)
    global _ready
    _ready = True
    if harness_startup_duration_seconds is not None:
        harness_startup_duration_seconds.set(time.monotonic() - _startup_mono)
    logger.info(f"Agent {AGENT_NAME} is ready")


async def main():
    """Harness async entrypoint — wire and run the witwave agent process.

    Bootstrap sequence:

    1. Stamps ``start_time`` / ``_startup_mono`` so uptime metrics and
       startup-duration measurements are anchored to the same instant.
    2. Validates ``MANIFEST_PATH`` (default
       ``/home/agent/manifest.json``) and warns on malformed entries —
       does not abort; team features simply remain unavailable.
    3. Initialises OpenTelemetry via ``tracing.init_otel_if_enabled``
       before the executor is constructed (#469) so the first emitted
       span is exported correctly.
    4. Constructs the singleton ``MessageBus``, ``AgentExecutor``, and
       a task store (``SqliteTaskStore`` when ``TASK_STORE_PATH`` is
       set, ``InMemoryTaskStore`` otherwise with a warning about
       restart-loss).
    5. Builds the Starlette ASGI app: mounts the A2A application,
       wires ``/metrics`` (optionally bearer-auth-gated by
       ``METRICS_AUTH_TOKEN``), and registers the agent/health/
       conversations/triggers/jobs/tasks/heartbeat/otel/events/hooks
       route handlers as nested closures over the executor + bus.
    6. Spawns scheduler tasks (``heartbeat_runner``, ``job_runner``,
       ``task_runner``, ``trigger_runner``, ``continuation_runner``,
       ``webhook_runner``, hook-decision dispatcher), the critical
       ``bus_worker`` task, watcher tasks (event-loop lag monitor,
       backends watcher, MCP watchers), and helper tasks
       (``_set_ready_when_started``, ``_wait_for_backends``). All run
       under :func:`_guarded` so transient failures restart with
       backoff.
    7. Runs ``uvicorn.Server.serve()`` in the foreground; on exit
       performs the phased shutdown documented inline (#780): cancel
       schedulers → cancel bus worker → ``executor.drain_background``
       → cancel watchers + helpers → drain webhook deliveries (#923)
       → drain continuations (#1275) → ``executor.close_backends``
       (#861).
    """
    global start_time, _startup_mono
    start_time = datetime.now(timezone.utc)
    _startup_mono = time.monotonic()

    # Startup manifest validation — surface misconfiguration early.
    import json as _startup_json

    _manifest_path_startup = os.environ.get("MANIFEST_PATH", "/home/agent/manifest.json")
    try:
        with open(_manifest_path_startup) as _mf:
            _manifest_startup = _startup_json.load(_mf)
        for _idx, _entry in enumerate(_manifest_startup.get("team", [])):
            if not isinstance(_entry, dict):
                logger.warning(
                    "manifest team[%d]: entry is not a dict (got %r) — check manifest.json", _idx, type(_entry).__name__
                )
                continue
            _name = _entry.get("name")
            _url = _entry.get("url")
            if not isinstance(_name, str) or not _name:
                logger.warning("manifest team[%d]: missing or non-string 'name' field (got %r)", _idx, _name)
            if not isinstance(_url, str) or not _url:
                logger.warning("manifest team[%d]: missing or non-string 'url' field (got %r)", _idx, _url)
    except FileNotFoundError:
        logger.info("Manifest file not found at %s — team features will be unavailable", _manifest_path_startup)
    except Exception as _manifest_exc:
        logger.warning("Could not validate manifest at startup: %s", _manifest_exc)

    # Initialise OTel before constructing the executor so the first span
    # emitted by AgentExecutor.__init__ or any eager watcher gets exported
    # through the configured OTLP pipeline (#469). No-op when OTEL_ENABLED
    # is falsy, which is the default.
    from tracing import init_otel_if_enabled

    init_otel_if_enabled(service_name=os.environ.get("OTEL_SERVICE_NAME") or "harness")

    bus = MessageBus()
    agent_card = build_agent_card()
    executor = AgentExecutor()
    global _executor
    _executor = executor
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
        if harness_up is not None:
            harness_up.labels(agent=AGENT_NAME).set(1.0)
        if harness_info is not None:
            harness_info.info({"version": AGENT_VERSION, "agent": AGENT_NAME})
        if harness_uptime_seconds is not None:
            harness_uptime_seconds.set_function(lambda: (datetime.now(timezone.utc) - start_time).total_seconds())
        logger.info("Prometheus metrics enabled at /metrics")
    else:
        logger.warning(
            "METRICS_ENABLED is not set — Prometheus metrics are disabled. "
            "Set METRICS_ENABLED=1 to enable /metrics and all instrumentation."
        )

    _metrics_auth_token = os.environ.get("METRICS_AUTH_TOKEN", "")

    async def metrics_handler(request: Request) -> Response:
        """Return witwave metrics plus relabelled metrics from all reachable backends.

        Backend metrics are cached for METRICS_CACHE_TTL seconds to avoid
        making N outbound HTTP calls on every Prometheus scrape.
        """
        global _metrics_cache_body, _metrics_cache_expires
        if _metrics_auth_token:
            header = request.headers.get("Authorization", "")
            if not hmac_mod.compare_digest(f"Bearer {_metrics_auth_token}", header):
                return Response(
                    content="Unauthorized",
                    status_code=401,
                    headers={"WWW-Authenticate": 'Bearer realm="metrics"'},
                )
        witwave_output = prometheus_client.exposition.generate_latest().decode("utf-8")
        now = time.monotonic()
        if now >= _metrics_cache_expires:
            # Single-flight refresh (#536): N concurrent scrapers at the cache
            # boundary would otherwise each fan out to every backend's
            # /metrics.  The first waiter through the lock does the fetch; the
            # rest re-check expiry under the lock and return the refreshed
            # cache without issuing their own fan-out.
            async with _metrics_cache_lock:
                now = time.monotonic()
                if now >= _metrics_cache_expires:
                    # Use the live executor backends rather than re-reading
                    # backend.yaml from disk.  After a hot-reload,
                    # executor._backends reflects the current routing state;
                    # re-reading the file would fan out to a potentially
                    # stale set of backends.
                    backend_configs = [b._config for b in executor._backends.values()]
                    _metrics_cache_body = await fetch_backend_metrics(backend_configs)
                    _metrics_cache_expires = now + METRICS_CACHE_TTL
        body = witwave_output + _metrics_cache_body
        return Response(
            content=body,
            media_type=prometheus_client.exposition.CONTENT_TYPE_LATEST,
        )

    async def agents_handler(request: Request) -> JSONResponse:
        """Return this harness's agent card plus every backend's card.

        Emits one entry per known agent. The first entry is the harness's
        own card (``role: "witwave"``) built via :func:`build_agent_card`;
        the rest are produced by fetching ``/.well-known/agent.json`` from
        each backend whose ``url`` is configured. Backend fetches fan out
        concurrently with a 5s timeout (#360) — an unreachable backend
        appears in the response with ``card: null`` rather than failing
        the whole call. The live ``executor._backends`` map is consulted
        (not a re-read of ``backend.yaml``) so the result reflects the
        same routing state as ``/metrics`` after a hot-reload (#288).
        """
        # Use the live executor backends so this endpoint reflects the same state
        # as /metrics after a hot-reload (consistent with the fix in #288).
        backend_configs = [b._config for b in executor._backends.values()]
        # Own card
        own_card = build_agent_card()
        agents = [
            {
                "id": AGENT_NAME,
                "url": HARNESS_URL,
                "role": "witwave",
                "card": own_card.model_dump() if hasattr(own_card, "model_dump") else vars(own_card),
            }
        ]
        # Fan out backend card fetches concurrently so that one slow or
        # unreachable backend does not delay all others (#360).
        import httpx

        reachable_backends = [b for b in backend_configs if b.url]

        async def _fetch_card(backend, client) -> dict:
            entry = {"id": backend.id, "url": backend.url, "role": "backend", "model": backend.model, "card": None}
            try:
                resp = await client.get(backend.url.rstrip("/") + "/.well-known/agent.json")
                if resp.status_code == 200:
                    entry["card"] = resp.json()
            except Exception as exc:
                logger.debug(f"Backend {backend.id!r} agent card unreachable: {exc!r}")
            return entry

        async with httpx.AsyncClient(timeout=5.0) as client:
            backend_entries = await asyncio.gather(
                *[_fetch_card(b, client) for b in reachable_backends],
                return_exceptions=True,
            )
            for backend, result in zip(reachable_backends, backend_entries, strict=True):
                if isinstance(result, Exception):
                    logger.debug(f"Backend {backend.id!r} agent card fetch raised: {result!r}")
                    agents.append(
                        {"id": backend.id, "url": backend.url, "role": "backend", "model": backend.model, "card": None}
                    )
                else:
                    agents.append(result)
        return JSONResponse(agents)

    _conversations_auth_token = os.environ.get("CONVERSATIONS_AUTH_TOKEN", "")
    _backend_conversations_auth_token = os.environ.get("BACKEND_CONVERSATIONS_AUTH_TOKEN", "")

    def _require_conversations_auth(request: Request) -> JSONResponse | None:
        if not _conversations_auth_token:
            if not auth_disabled_escape_hatch():
                return JSONResponse({"error": "auth not configured"}, status_code=503)
            return None
        header = request.headers.get("Authorization", "")
        if not hmac_mod.compare_digest(f"Bearer {_conversations_auth_token}", header):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        return None

    async def _fetch_conversations(since: str | None, limit: int | None) -> list[dict]:
        # Use the live executor backends (consistent with metrics_handler, fixed in #288).
        backend_configs = [b._config for b in executor._backends.values()]
        return await fetch_backend_conversations(
            backend_configs,
            since=since,
            limit=limit,
            auth_token=_backend_conversations_auth_token or None,
        )

    async def _fetch_trace(since: str | None, limit: int | None) -> list[dict]:
        # Use the live executor backends (consistent with metrics_handler, fixed in #288).
        backend_configs = [b._config for b in executor._backends.values()]
        return await fetch_backend_trace(
            backend_configs,
            since=since,
            limit=limit,
            auth_token=_backend_conversations_auth_token or None,
        )

    conversations_handler = make_proxy_conversations_handler(_conversations_auth_token, _fetch_conversations)
    trace_handler = make_proxy_trace_handler(_conversations_auth_token, _fetch_trace)

    # The /proxy/{agent_name}, /conversations/{agent_name}, /trace/{agent_name},
    # and /team endpoints were removed in beta.46 — the dashboard owns cross-
    # agent routing now (it fans out to each agent's own /agents, /conversations,
    # /trace, and A2A root). This harness only speaks for itself.

    backends_ready = asyncio.Event()

    trigger_runner = TriggerRunner()
    job_runner = JobRunner(bus, backends_ready)
    task_runner = TaskRunner(bus, backends_ready)
    webhook_runner = WebhookRunner()
    continuation_runner = ContinuationRunner()

    # Fan bus-published HookDecisionEvents out to matching webhook
    # subscriptions (#641).  Closes the loop started by #633 (bus side-channel +
    # WebhookRunner.fire_hook_decision) now that POST /internal/events/hook-decision
    # accepts backend-originated events.  The listener is synchronous — it
    # schedules delivery tasks via asyncio.create_task — so publish_hook_decision
    # never blocks the HTTP handler that called it.
    def _fanout_hook_decision(event: HookDecisionEvent) -> None:
        webhook_runner.fire_hook_decision(
            event.agent,
            event.session_id,
            event.tool,
            event.decision,
            event.rule_name,
            event.reason,
            event.source,
            event.traceparent,
        )

    subscribe_hook_decision(_fanout_hook_decision)
    # Re-emit each hook.decision onto the SSE event stream (#1110). Added
    # alongside the webhook fan-out so dashboards see the same decision
    # the existing subscription sink sees, just on the live event channel.
    subscribe_hook_decision(_emit_hook_decision_event_stream)

    async def triggers_discovery(request: Request) -> JSONResponse:
        """Advertise registered trigger endpoints (``/.well-known/agent-triggers.json``).

        Returns a JSON array — one entry per :class:`TriggerItem` known
        to ``trigger_runner`` — exposing its ``endpoint`` path,
        operator-supplied ``name`` and ``description``, the fixed
        ``methods: ["POST"]`` (every trigger is POST-only — see the
        route table comment), and the trigger's ``session_id``. Used by
        external systems (dashboards, integration setup pages) to
        discover what trigger URLs the agent exposes without having to
        re-parse the operator config.
        """
        items = trigger_runner.items_by_endpoint()
        payload = [
            {
                "endpoint": item.endpoint,
                "name": item.name,
                "description": item.description,
                "methods": ["POST"],
                "session_id": item.session_id,
            }
            for item in items.values()
        ]
        return JSONResponse(payload)

    async def runs_discovery(request: Request) -> JSONResponse:
        """Advertise the runnable ad-hoc entrypoints (#788).

        Clients can discover the heartbeat/jobs/tasks ad-hoc run endpoints
        from a single well-known document, the way /.well-known/agent-triggers.json
        advertises inbound trigger endpoints.  All three endpoints require
        the distinct ``ADHOC_RUN_AUTH_TOKEN`` bearer-token (#700) — advertising
        TRIGGERS_AUTH_TOKEN here would send clients onto the wrong bearer and
        every request would 401 (#956).
        """
        payload: list[dict] = [
            {
                "kind": "heartbeat",
                "name": "heartbeat",
                "endpoint": "/heartbeat/run",
                "methods": ["POST"],
                "auth": "bearer:ADHOC_RUN_AUTH_TOKEN",
            },
        ]
        try:
            for job in job_runner.items():
                name = (job.get("name") if isinstance(job, dict) else getattr(job, "name", "")) or ""
                payload.append(
                    {
                        "kind": "job",
                        "name": name,
                        "endpoint": f"/jobs/{name}/run",
                        "methods": ["POST"],
                        "auth": "bearer:ADHOC_RUN_AUTH_TOKEN",
                    }
                )
        except Exception:
            pass
        try:
            for task in task_runner.items():
                name = (task.get("name") if isinstance(task, dict) else getattr(task, "name", "")) or ""
                payload.append(
                    {
                        "kind": "task",
                        "name": name,
                        "endpoint": f"/tasks/{name}/run",
                        "methods": ["POST"],
                        "auth": "bearer:ADHOC_RUN_AUTH_TOKEN",
                    }
                )
        except Exception:
            pass
        # Read-only introspection surfaces (#1086). Webhooks and
        # continuations don't have run endpoints — they're reactive —
        # but their snapshot endpoints are part of the "introspection"
        # surface a dashboard / remote-tooling client needs to bootstrap
        # off a single well-known. Listing them alongside the run
        # entries keeps /.well-known/agent-runs.json as the canonical
        # discovery doc rather than requiring a second well-known.
        payload.append(
            {
                "kind": "webhooks",
                "name": "webhooks",
                "endpoint": "/webhooks",
                "methods": ["GET"],
                "auth": "none",
                "reactive": True,
            }
        )
        payload.append(
            {
                "kind": "continuations",
                "name": "continuations",
                "endpoint": "/continuations",
                "methods": ["GET"],
                "auth": "none",
                "reactive": True,
            }
        )
        return JSONResponse(payload)

    async def trigger_handler(request: Request) -> JSONResponse:
        """Dispatch an inbound trigger request through to the executor.

        Per-endpoint POST handler for every operator-defined trigger.
        Enforces, in order:

        1. **Warmup shield** (#785) — 503 + ``Retry-After: 5`` while
           ``backends_ready`` is unset so external callers (webhooks,
           GitHub Apps) back off rather than dispatching into a
           still-initialising backend.
        2. **Lookup** — 404 if the endpoint isn't registered, 409 if a
           run for this endpoint is already in flight.
        3. **Slot claim** — adds ``endpoint`` to ``trigger_runner._running``
           BEFORE the first ``await`` to close the concurrent-409 race;
           ``CancelledError`` paths release the slot so a client
           disconnect mid-stream doesn't pin the endpoint forever (#866).
        4. **Pre-auth gate** (#529) — rejects with 401 before touching
           the body unless the appropriate auth header is present, and
           (#1318) validates the bearer VALUE pre-buffer so callers can't
           force a 1 MiB body allocation with an arbitrary header.
        5. **Body cap** — short-circuits on ``Content-Length`` > 1 MiB,
           else streams chunks with a running-total cap (HMAC paths still
           need the full body up to the cap).
        6. **Full auth check** — :func:`_check_trigger_auth` validates
           HMAC (if ``secret_env_var`` set) or the global bearer.
        7. **Dispatch** — builds the prompt by concatenating an
           operator-authored, env-interpolated body (#473 — untrusted
           inbound bytes are never interpolated) with the scrubbed
           request headers (#1269) and a truncated body (262144 bytes
           UTF-8, falling back to a 4096-byte hex dump on decode error),
           all inside ``<untrusted-request-*>`` fences. Fires the work
           as a background task; the slot release moves to the task's
           own ``finally`` block.

        Returns 202 with ``delivery_id`` / ``session_id`` / ``endpoint``
        on success. Every outcome increments
        ``harness_triggers_requests_total{method, code}`` with the
        appropriate response code so dispatch-vs-reject ratios are
        observable per trigger.
        """
        endpoint = request.path_params["endpoint"]

        # Warmup shield (#785): while backends_ready is unset, return 503
        # with Retry-After so load balancers back off rather than
        # dispatching into a backend that is still /health-warming and
        # would 503 downstream anyway. Triggers fire from external
        # systems (webhooks / GitHub Apps) whose retry semantics make a
        # structured 503 the safer response.
        if not backends_ready.is_set():
            if harness_triggers_requests_total is not None:
                harness_triggers_requests_total.labels(method=request.method, code="503").inc()
            return JSONResponse(
                {"error": "backends not ready", "endpoint": endpoint},
                status_code=503,
                headers={"Retry-After": "5"},
            )

        items = trigger_runner.items_by_endpoint()
        item = items.get(endpoint)
        if item is None:
            if harness_triggers_requests_total is not None:
                harness_triggers_requests_total.labels(method=request.method, code="404").inc()
            return JSONResponse({"error": "not found", "endpoint": endpoint}, status_code=404)

        if endpoint in trigger_runner._running:
            if harness_triggers_requests_total is not None:
                harness_triggers_requests_total.labels(method=request.method, code="409").inc()
            return JSONResponse({"error": "already running", "endpoint": endpoint}, status_code=409)

        # Claim the slot before any await to prevent concurrent requests from
        # both passing the 409 check above. The body-read try below
        # catches asyncio.CancelledError so a client disconnect
        # during the streaming read releases the slot (#866);
        # previously CancelledError propagated past the existing
        # `except Exception` guard and left the endpoint pinned in
        # _running forever, so every subsequent call returned 409
        # until process restart.
        trigger_runner._running.add(endpoint)

        # Pre-auth gate (#529): before touching the body, require the caller to
        # present the header appropriate to the trigger's configured auth
        # mechanism.  This keeps unauthenticated callers from forcing the
        # harness to buffer up to MAX_TRIGGER_BODY_BYTES per concurrent request.
        MAX_TRIGGER_BODY_BYTES = 1_048_576
        # #1318: validate the bearer VALUE (not mere presence) before we
        # buffer the body, so an unauth caller sending `Authorization:
        # anything` cannot force a 1 MiB buffer allocation before rejection.
        # Signature-auth paths still require the signature header at this
        # gate because the HMAC check needs the body; for those we keep
        # the presence-only pre-check but reject oversize Content-Length
        # early below.
        auth_header_present = False
        auth_token_validated = False
        if item.secret_env_var:
            auth_header_present = bool(request.headers.get("X-Hub-Signature-256"))
        else:
            _global_token = os.environ.get("TRIGGERS_AUTH_TOKEN", "").strip()
            if _global_token:
                _hdr = request.headers.get("Authorization", "")
                auth_header_present = bool(_hdr)
                if _hdr and hmac_mod.compare_digest(f"Bearer {_global_token}", _hdr):
                    auth_token_validated = True
            else:
                # No token configured — _check_trigger_auth will reject anyway.
                auth_header_present = bool(request.headers.get("Authorization"))
        if not auth_header_present:
            trigger_runner._running.discard(endpoint)
            if harness_triggers_requests_total is not None:
                harness_triggers_requests_total.labels(method=request.method, code="401").inc()
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        # Bearer auth: reject BEFORE buffering if token doesn't match.
        if not item.secret_env_var and os.environ.get("TRIGGERS_AUTH_TOKEN", "").strip() and not auth_token_validated:
            trigger_runner._running.discard(endpoint)
            if harness_triggers_requests_total is not None:
                harness_triggers_requests_total.labels(method=request.method, code="401").inc()
            return JSONResponse({"error": "unauthorized"}, status_code=401)

        # Cheap Content-Length short-circuit: if the client advertised an
        # oversized body, reject before reading any of it.
        try:
            declared_len = int(request.headers.get("Content-Length", "") or "-1")
        except ValueError:
            declared_len = -1
        if declared_len > MAX_TRIGGER_BODY_BYTES:
            trigger_runner._running.discard(endpoint)
            if harness_triggers_requests_total is not None:
                harness_triggers_requests_total.labels(method=request.method, code="413").inc()
            return JSONResponse({"error": "request body too large"}, status_code=413)

        # Streaming read with a hard cap: abort as soon as the accumulated size
        # exceeds MAX_TRIGGER_BODY_BYTES rather than buffering the whole payload
        # first (the pre-#529 behaviour).  HMAC verification still needs the
        # full body, so we keep the full buffer up to the cap.
        try:
            chunks: list[bytes] = []
            total = 0
            oversized = False
            async for chunk in request.stream():
                if not chunk:
                    continue
                total += len(chunk)
                if total > MAX_TRIGGER_BODY_BYTES:
                    oversized = True
                    break
                chunks.append(chunk)
        except asyncio.CancelledError:
            # Client disconnect mid-stream (#866). Release the slot so
            # the endpoint doesn't stay pinned forever — subsequent
            # calls would 409 until process restart. Don't bump the
            # 500 counter: this is a client action, not a server
            # fault. Re-raise so the request's cancellation framing
            # semantics are unchanged.
            trigger_runner._running.discard(endpoint)
            raise
        except Exception:
            trigger_runner._running.discard(endpoint)
            if harness_triggers_requests_total is not None:
                harness_triggers_requests_total.labels(method=request.method, code="500").inc()
            raise

        if oversized:
            trigger_runner._running.discard(endpoint)
            if harness_triggers_requests_total is not None:
                harness_triggers_requests_total.labels(method=request.method, code="413").inc()
            return JSONResponse({"error": "request body too large"}, status_code=413)

        body_bytes = b"".join(chunks)

        if not _check_trigger_auth(request, item, body_bytes):
            trigger_runner._running.discard(endpoint)
            if harness_triggers_requests_total is not None:
                harness_triggers_requests_total.labels(method=request.method, code="401").inc()
            return JSONResponse({"error": "unauthorized"}, status_code=401)

        # Build prompt from request.
        # #1269: Strip CR/LF from header values and wrap headers in their
        # own <untrusted-request-headers> fence so a caller-controlled
        # header value cannot smuggle instructions into the system prompt
        # outside a fenced block.
        def _scrub_header_value(_v: str) -> str:
            return _v.replace("\r", " ").replace("\n", " ")

        filtered_headers = "\n".join(
            f"{k}: {_scrub_header_value(v)}"
            for k, v in request.headers.items()
            if k.lower() not in ("authorization", "x-hub-signature-256", "cookie")
        )
        try:
            body_text = body_bytes[:262144].decode("utf-8")
        except (UnicodeDecodeError, ValueError):
            body_text = body_bytes[:4096].hex()

        from prompt_env import resolve_prompt_env  # noqa: E402

        # #473: interpolate env vars in the OPERATOR-AUTHORED body only.
        # The untrusted inbound HTTP body is never interpolated; it would
        # let any caller who can hit the endpoint read local env vars.
        _interpolated_content = resolve_prompt_env(item.content)
        prompt = (
            f"Trigger: {item.name}\n\n"
            f"Request:\n"
            f"{request.method} {request.url.path}\n\n"
            f"<untrusted-request-headers>\n"
            f"{filtered_headers}\n"
            f"</untrusted-request-headers>\n\n"
            f"<untrusted-request-body>\n"
            f"{body_text}\n"
            f"</untrusted-request-body>\n\n"
            f"---\n\n"
            f"{_interpolated_content}"
        )

        delivery_id = str(uuid.uuid4())

        async def _fire() -> None:
            _fire_start = time.monotonic()
            _response = ""
            _success = False
            _error: str | None = None
            _model = None
            backend_id = None
            try:
                _entry = executor._routing_entry_for_kind(f"trigger:{endpoint}")
                backend_id = item.backend_id or (_entry.agent if _entry else None)
                _resolved_id = backend_id or executor._default_backend_id
                _model = executor._resolve_model(item.model, _entry, _resolved_id)
                if item.consensus:
                    _response = await executor_run_consensus(
                        prompt,
                        item.session_id,
                        executor._sessions,
                        executor._backends,
                        executor._default_backend_id,
                        consensus_entries=item.consensus,
                        max_tokens=item.max_tokens,
                    )
                else:
                    _response = await executor_run(
                        prompt,
                        item.session_id,
                        executor._sessions,
                        executor._backends,
                        executor._default_backend_id,
                        backend_id=backend_id,
                        model=_model,
                        max_tokens=item.max_tokens,
                    )
                _success = True
            except Exception as exc:
                _error = repr(exc)
                logger.error(f"Trigger '{item.name}' execution error: {exc!r}")
            finally:
                trigger_runner._running.discard(endpoint)
                # Fan a trigger.fired event onto the SSE event stream (#1110).
                try:
                    _tg_payload: dict = {
                        "name": item.name,
                        "endpoint": endpoint,
                        "duration_ms": int((time.monotonic() - _fire_start) * 1000),
                        "outcome": "success" if _success else "error",
                    }
                    if not _success and _error:
                        _tg_payload["error"] = _error[:512]
                    from events import get_event_stream as _ges

                    _ges().publish("trigger.fired", _tg_payload, agent_id=AGENT_NAME)
                except Exception:  # pragma: no cover
                    pass
                executor.track_background(
                    executor.on_prompt_completed(
                        source="trigger",
                        kind=f"trigger:{endpoint}",
                        session_id=item.session_id,
                        success=_success,
                        response=_response,
                        duration_seconds=time.monotonic() - _fire_start,
                        error=_error,
                        model=_model,
                    ),
                    source="trigger",
                )

        try:
            _scheduled = executor.track_background(_fire(), source="trigger-fire")
            if _scheduled is None:
                trigger_runner._running.discard(endpoint)
                logger.error(f"Trigger '{item.name}': shed — background-task cap reached")
                if harness_triggers_requests_total is not None:
                    harness_triggers_requests_total.labels(method=request.method, code="503").inc()
                return JSONResponse({"error": "overloaded"}, status_code=503)
            # _fire() owns the slot release from here on via its own
            # finally block. (#1344: dead _scheduled_fire removed.)
        except Exception as exc:
            trigger_runner._running.discard(endpoint)
            logger.error(f"Trigger '{item.name}': failed to schedule background task: {exc!r}")
            if harness_triggers_requests_total is not None:
                harness_triggers_requests_total.labels(method=request.method, code="500").inc()
            return JSONResponse({"error": "internal error"}, status_code=500)

        if harness_triggers_requests_total is not None:
            harness_triggers_requests_total.labels(method=request.method, code="202").inc()
        return JSONResponse(
            {"delivery_id": delivery_id, "session_id": item.session_id, "endpoint": endpoint},
            status_code=202,
        )

    async def _drain_adhoc_body(request: Request) -> bool:
        """Drain up to MAX_ADHOC_BODY_BYTES from the request body.

        Returns False if the client advertised or streamed more than the cap,
        so the caller can respond with 413. Body content is discarded — ad-hoc
        endpoints derive their prompt from the item's .md file, not the request
        body.
        """
        try:
            declared_len = int(request.headers.get("Content-Length", "") or "-1")
        except ValueError:
            declared_len = -1
        if declared_len > MAX_ADHOC_BODY_BYTES:
            return False
        total = 0
        async for chunk in request.stream():
            if not chunk:
                continue
            total += len(chunk)
            if total > MAX_ADHOC_BODY_BYTES:
                return False
        return True

    def _find_job_item(name: str):
        for item in job_runner._items.values():
            if item.name == name:
                return item
        return None

    def _find_task_item(name: str):
        for item in task_runner._items.values():
            if item.name == name:
                return item
        return None

    async def _dispatch_adhoc_job(item) -> str:
        """Route ad-hoc job runs through the same _execute_job pipeline the
        cron loop uses (#1293).

        Previously this path built a Message and called bus.send directly,
        bypassing checkpoint writes, running-gauge increments, semaphore
        gating, and the full success/error telemetry _execute_job emits.
        Routing through _execute_job keeps ad-hoc fires indistinguishable
        from scheduled fires at the observability layer.
        """
        from jobs import _execute_job  # noqa: E402 — scoped import keeps import graph lazy

        delivery_id = str(uuid.uuid4())
        executor.track_background(
            _execute_job(item, bus, getattr(job_runner, "_semaphore", None)),
            source="adhoc-job-fire",
        )
        return delivery_id

    async def _dispatch_adhoc_task(item) -> str:
        """Route ad-hoc task runs through tasks.run_task (#1298).

        Mirrors #1293 for tasks: the previous path constructed a Message and
        called bus.send directly, bypassing the checkpoint, running-gauge,
        semaphore, and telemetry run_task owns. run_task derives the
        session_id itself (run-once vs. day-window), so we no longer
        compute it inline here.
        """
        from tasks import run_task  # noqa: E402 — scoped import keeps import graph lazy

        delivery_id = str(uuid.uuid4())
        executor.track_background(
            run_task(item, bus, getattr(task_runner, "_semaphore", None)),
            source="adhoc-task-fire",
        )
        return delivery_id

    def _adhoc_warmup_shield(kind: str, name: str) -> JSONResponse | None:
        """Return 503+Retry-After when ad-hoc runs race the warmup gate (#925).

        /triggers already had this shield (#785); the ad-hoc run endpoints
        added in #788 bypassed it so an operator smoke-test during pod
        warmup produced spurious 503s downstream and a confusing audit
        trail. Applied uniformly to /jobs/{n}/run, /tasks/{n}/run, and
        /heartbeat/run so all ad-hoc dispatch paths surface the same
        structured 503 as external triggers.
        """
        if backends_ready.is_set():
            return None
        if harness_adhoc_fires_total is not None:
            harness_adhoc_fires_total.labels(kind=kind, name=name, code="503").inc()
        return JSONResponse(
            {"error": "backends not ready", "kind": kind, "name": name},
            status_code=503,
            headers={"Retry-After": "5"},
        )

    async def jobs_run_handler(request: Request) -> JSONResponse:
        """Ad-hoc-fire the named scheduled job (#788).

        ``POST /jobs/{name}/run`` — operator-facing endpoint that
        triggers a scheduled job outside its cron window. Gates the
        request through the standard ad-hoc dispatch ladder:
        :func:`_check_adhoc_auth` (distinct ``ADHOC_RUN_AUTH_TOKEN``
        bearer, #700), :func:`_check_adhoc_csrf` (anti-CSRF header to
        defang XSS-to-CSRF chains, #927), :func:`_adhoc_warmup_shield`
        (503 + ``Retry-After`` while backends are still warming, #925),
        and :func:`_drain_adhoc_body` (drop bodies > 64 KiB — the
        prompt is derived from the job's ``.md`` file, not the
        request body). Returns 404 if no job matches ``name``, 409 if
        the job is already running, 500 on dispatch failure, else 202
        with ``delivery_id`` / ``session_id`` / ``kind`` / ``name`` and
        delegates execution to :func:`_dispatch_adhoc_job` so the
        ad-hoc fire flows through the same ``_execute_job`` pipeline
        the cron loop uses (#1293). Every response code increments
        ``harness_adhoc_fires_total{kind="job", name, code}``.
        """
        name = request.path_params["name"]
        if not _check_adhoc_auth(request):
            if harness_adhoc_fires_total is not None:
                harness_adhoc_fires_total.labels(kind="job", name=name, code="401").inc()
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        if not _check_adhoc_csrf(request):
            # #927: Missing anti-CSRF header → treat as forgery attempt. A
            # browser cross-origin POST cannot add X-Ad-Hoc-Run without a
            # CORS preflight; requiring it defangs XSS-to-CSRF chains even
            # when the bearer is recoverable from the dashboard origin.
            if harness_adhoc_fires_total is not None:
                harness_adhoc_fires_total.labels(kind="job", name=name, code="403").inc()
            return JSONResponse({"error": "missing anti-CSRF header"}, status_code=403)
        if (resp := _adhoc_warmup_shield("job", name)) is not None:
            return resp
        if not await _drain_adhoc_body(request):
            if harness_adhoc_fires_total is not None:
                harness_adhoc_fires_total.labels(kind="job", name=name, code="413").inc()
            return JSONResponse({"error": "request body too large"}, status_code=413)
        item = _find_job_item(name)
        if item is None:
            if harness_adhoc_fires_total is not None:
                harness_adhoc_fires_total.labels(kind="job", name=name, code="404").inc()
            return JSONResponse({"error": "not found", "name": name}, status_code=404)
        if item.running:
            if harness_adhoc_fires_total is not None:
                harness_adhoc_fires_total.labels(kind="job", name=name, code="409").inc()
            return JSONResponse({"error": "already running", "name": name}, status_code=409)
        try:
            delivery_id = await _dispatch_adhoc_job(item)
        except Exception as exc:
            logger.error(f"Ad-hoc job '{name}' dispatch failed: {exc!r}")
            if harness_adhoc_fires_total is not None:
                harness_adhoc_fires_total.labels(kind="job", name=name, code="500").inc()
            return JSONResponse({"error": "internal error"}, status_code=500)
        if harness_adhoc_fires_total is not None:
            harness_adhoc_fires_total.labels(kind="job", name=name, code="202").inc()
        return JSONResponse(
            {"delivery_id": delivery_id, "session_id": item.session_id, "kind": "job", "name": name},
            status_code=202,
        )

    async def tasks_run_handler(request: Request) -> JSONResponse:
        """Ad-hoc-fire the named scheduled task (#788).

        ``POST /tasks/{name}/run`` — the task counterpart to
        :func:`jobs_run_handler`. Runs the same auth → CSRF → warmup-
        shield → body-cap ladder, looks up the task by name (404 if
        missing), rejects 409 if the task is already running, and on
        success hands off to :func:`_dispatch_adhoc_task` which routes
        through :func:`tasks.run_task` so ad-hoc fires share the
        checkpoint / running-gauge / semaphore / telemetry the
        scheduled path uses (#1298). Returns 202 with ``delivery_id``
        / ``kind`` / ``name`` on success; ``session_id`` is omitted in
        the response because :func:`run_task` derives it itself
        (run-once vs. day-window). Every outcome increments
        ``harness_adhoc_fires_total{kind="task", name, code}``.
        """
        name = request.path_params["name"]
        if not _check_adhoc_auth(request):
            if harness_adhoc_fires_total is not None:
                harness_adhoc_fires_total.labels(kind="task", name=name, code="401").inc()
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        if not _check_adhoc_csrf(request):
            if harness_adhoc_fires_total is not None:
                harness_adhoc_fires_total.labels(kind="task", name=name, code="403").inc()
            return JSONResponse({"error": "missing anti-CSRF header"}, status_code=403)
        if (resp := _adhoc_warmup_shield("task", name)) is not None:
            return resp
        if not await _drain_adhoc_body(request):
            if harness_adhoc_fires_total is not None:
                harness_adhoc_fires_total.labels(kind="task", name=name, code="413").inc()
            return JSONResponse({"error": "request body too large"}, status_code=413)
        item = _find_task_item(name)
        if item is None:
            if harness_adhoc_fires_total is not None:
                harness_adhoc_fires_total.labels(kind="task", name=name, code="404").inc()
            return JSONResponse({"error": "not found", "name": name}, status_code=404)
        if item.running:
            if harness_adhoc_fires_total is not None:
                harness_adhoc_fires_total.labels(kind="task", name=name, code="409").inc()
            return JSONResponse({"error": "already running", "name": name}, status_code=409)
        try:
            delivery_id = await _dispatch_adhoc_task(item)
        except Exception as exc:
            logger.error(f"Ad-hoc task '{name}' dispatch failed: {exc!r}")
            if harness_adhoc_fires_total is not None:
                harness_adhoc_fires_total.labels(kind="task", name=name, code="500").inc()
            return JSONResponse({"error": "internal error"}, status_code=500)
        if harness_adhoc_fires_total is not None:
            harness_adhoc_fires_total.labels(kind="task", name=name, code="202").inc()
        return JSONResponse(
            {"delivery_id": delivery_id, "kind": "task", "name": name},
            status_code=202,
        )

    async def heartbeat_run_handler(request: Request) -> JSONResponse:
        """Ad-hoc-fire the agent's heartbeat (#788).

        ``POST /heartbeat/run`` — runs the same auth → CSRF → warmup-
        shield → body-cap ladder as the job/task ad-hoc handlers, then
        loads ``HEARTBEAT.md`` via :func:`heartbeat.load_heartbeat`.
        Returns 404 with ``"heartbeat not configured or disabled"`` if
        the file is missing or disabled; otherwise builds a
        :class:`Message` with the env-interpolated heartbeat body, the
        constant ``HEARTBEAT_SESSION`` id, and the model / backend /
        consensus / max-tokens loaded from ``HEARTBEAT.md``, then
        delivers it via ``bus.try_send``. ``try_send`` preserves the
        scheduled-heartbeat dedup semantic (#514) — if a scheduled
        heartbeat is already in-flight, the ad-hoc fire is rejected with
        409 rather than stacking. Returns 202 with ``delivery_id`` and
        ``HEARTBEAT_SESSION`` on success. Every outcome increments
        ``harness_adhoc_fires_total{kind="heartbeat", name="heartbeat",
        code}``.
        """
        if not _check_adhoc_auth(request):
            if harness_adhoc_fires_total is not None:
                harness_adhoc_fires_total.labels(kind="heartbeat", name="heartbeat", code="401").inc()
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        if not _check_adhoc_csrf(request):
            if harness_adhoc_fires_total is not None:
                harness_adhoc_fires_total.labels(kind="heartbeat", name="heartbeat", code="403").inc()
            return JSONResponse({"error": "missing anti-CSRF header"}, status_code=403)
        if (resp := _adhoc_warmup_shield("heartbeat", "heartbeat")) is not None:
            return resp
        if not await _drain_adhoc_body(request):
            if harness_adhoc_fires_total is not None:
                harness_adhoc_fires_total.labels(kind="heartbeat", name="heartbeat", code="413").inc()
            return JSONResponse({"error": "request body too large"}, status_code=413)
        try:
            loaded = load_heartbeat()
        except Exception as exc:
            logger.warning(f"Ad-hoc heartbeat: failed to load HEARTBEAT.md: {exc!r}")
            loaded = None
        if not loaded:
            if harness_adhoc_fires_total is not None:
                harness_adhoc_fires_total.labels(kind="heartbeat", name="heartbeat", code="404").inc()
            return JSONResponse({"error": "heartbeat not configured or disabled"}, status_code=404)
        _schedule, content, model, backend_id, consensus, max_tokens = loaded
        from heartbeat import HEARTBEAT_SESSION
        from prompt_env import resolve_prompt_env  # noqa: E402

        delivery_id = str(uuid.uuid4())
        prompt = resolve_prompt_env(f"Heartbeat check. Follow these instructions:\n\n{content}")
        message = Message(
            prompt=prompt,
            session_id=HEARTBEAT_SESSION,
            kind="heartbeat",
            model=model,
            backend_id=backend_id,
            consensus=consensus,
            max_tokens=max_tokens,
        )
        # Preserve heartbeat dedup semantics (#514): if a scheduled heartbeat is
        # already in-flight, reject the ad-hoc fire rather than stacking a
        # second one. Matches heartbeat.py:129.
        if not bus.try_send(message):
            if harness_adhoc_fires_total is not None:
                harness_adhoc_fires_total.labels(kind="heartbeat", name="heartbeat", code="409").inc()
            return JSONResponse({"error": "heartbeat already pending"}, status_code=409)
        if harness_adhoc_fires_total is not None:
            harness_adhoc_fires_total.labels(kind="heartbeat", name="heartbeat", code="202").inc()
        return JSONResponse(
            {"delivery_id": delivery_id, "session_id": HEARTBEAT_SESSION, "kind": "heartbeat"},
            status_code=202,
        )

    async def jobs_handler(request: Request) -> JSONResponse:
        """Return a snapshot of currently registered scheduled jobs."""
        return JSONResponse(job_runner.items())

    async def tasks_handler(request: Request) -> JSONResponse:
        """Return a snapshot of currently registered scheduled tasks."""
        return JSONResponse(task_runner.items())

    async def webhooks_handler(request: Request) -> JSONResponse:
        """Return a snapshot of currently registered webhook subscriptions."""
        return JSONResponse(webhook_runner.items())

    async def continuations_handler(request: Request) -> JSONResponse:
        """Return a snapshot of currently registered continuation items."""
        return JSONResponse(continuation_runner.items())

    async def heartbeat_handler(request: Request) -> JSONResponse:
        """Return the current heartbeat configuration from HEARTBEAT.md."""
        try:
            loaded = load_heartbeat()
        except Exception as exc:
            logger.warning("heartbeat_handler: failed to load HEARTBEAT.md: %s", exc)
            loaded = None
        import heartbeat as _hb_module  # late import — matches load_heartbeat pattern

        _fire_snap = _hb_module.snapshot() if hasattr(_hb_module, "snapshot") else {}
        if not loaded:
            return JSONResponse(
                {
                    "enabled": False,
                    "schedule": None,
                    "model": None,
                    "backend_id": None,
                    "consensus": [],
                    "max_tokens": None,
                    **_fire_snap,
                }
            )
        schedule, _content, model, backend_id, consensus, max_tokens = loaded
        from dataclasses import asdict

        return JSONResponse(
            {
                "enabled": True,
                "schedule": schedule,
                "model": model,
                "backend_id": backend_id,
                "consensus": [asdict(e) for e in consensus],
                "max_tokens": max_tokens,
                # #1087 — fire bookkeeping for dashboard "when next?" column.
                **_fire_snap,
            }
        )

    async def triggers_handler(request: Request) -> JSONResponse:
        """Return a snapshot of currently registered trigger endpoints."""
        return JSONResponse(trigger_runner.items())

    async def validate_handler(request: Request) -> JSONResponse:
        """Dry-run parse of a supplied .md config (#1088).

        Accepts a JSON body of the shape::

            {"kind": "job|task|trigger|continuation|webhook|heartbeat",
             "content": "---\\n...frontmatter...\\n---\\n...body..."}

        and returns ``{"ok": bool, "errors": [...], "parsed": {...}}``
        without registering or firing anything. Gives operators a curl
        one-liner for CI + pre-merge gitSync flows.

        Guarded by the same ADHOC_RUN_AUTH_TOKEN as /jobs/<name>/run so
        a misconfig scanner can share the existing bearer token rather
        than introducing a new one.
        """
        # Auth guard — parity with ad-hoc run handlers. Refuses when
        # ADHOC_RUN_AUTH_TOKEN is unset so /validate never opens a
        # world-readable parsing surface by default.
        if not _check_adhoc_auth(request):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        # #1316 + #1736: reject oversize bodies BEFORE buffering. The
        # Content-Length fast-path catches honest declarations; the
        # streaming read_capped_body call (shared with /mcp on every
        # backend, see closed #1609/#1673/#1674) is the actual
        # enforcement against chunked-transfer requests that omit
        # Content-Length entirely or lie about it.
        try:
            from utils import PARSE_FRONTMATTER_MAX_FILE_BYTES as _MAX
        except Exception:
            _MAX = 1_048_576
        try:
            declared_len = int(request.headers.get("Content-Length", "") or "-1")
        except ValueError:
            declared_len = -1
        if declared_len > _MAX:
            return JSONResponse(
                {"ok": False, "errors": [f"body exceeds {_MAX} bytes"]},
                status_code=413,
            )
        # Stream-read into a bounded buffer so the cap is enforced on
        # actual bytes-on-the-wire, not on the caller's declared length.
        try:
            from mcp_body_cap import read_capped_body as _read_capped_body
        except Exception:  # pragma: no cover — defensive
            from shared.mcp_body_cap import read_capped_body as _read_capped_body  # type: ignore[no-redef]
        _raw, _reason = await _read_capped_body(request, _MAX)
        if _reason == "body_too_large":
            return JSONResponse(
                {"ok": False, "errors": [f"body exceeds {_MAX} bytes"]},
                status_code=413,
            )
        if _reason == "parse_error" or _raw is None:
            return JSONResponse({"ok": False, "errors": ["request body could not be read"]}, status_code=400)
        try:
            import json as _json_for_validate

            body = _json_for_validate.loads(_raw.decode("utf-8"))
        except Exception:
            return JSONResponse({"ok": False, "errors": ["request body must be JSON"]}, status_code=400)
        if not isinstance(body, dict):
            return JSONResponse({"ok": False, "errors": ["body must be a JSON object"]}, status_code=400)
        kind = str(body.get("kind") or "").strip().lower()
        content = body.get("content")
        if not kind:
            return JSONResponse({"ok": False, "errors": ["missing 'kind'"]}, status_code=400)
        if not isinstance(content, str):
            return JSONResponse({"ok": False, "errors": ["missing or non-string 'content'"]}, status_code=400)
        # Cap at the same whole-file limit read_md_bounded applies
        # (#1038) so a caller can't weaponise /validate against the
        # harness's own memory budget.
        try:
            from utils import PARSE_FRONTMATTER_MAX_FILE_BYTES
        except Exception:
            PARSE_FRONTMATTER_MAX_FILE_BYTES = 128 * 1024
        if len(content.encode("utf-8", errors="replace")) > PARSE_FRONTMATTER_MAX_FILE_BYTES:
            return JSONResponse(
                {"ok": False, "errors": [f"content exceeds {PARSE_FRONTMATTER_MAX_FILE_BYTES} bytes"]},
                status_code=413,
            )

        from utils import FrontmatterTooLarge, parse_frontmatter, parse_frontmatter_raw

        errors: list[str] = []
        parsed: dict[str, object] = {}
        try:
            fields, body_text = parse_frontmatter(content)
            raw_fields, _ = parse_frontmatter_raw(content)
            parsed = {"fields": fields, "body_len": len(body_text)}
        except FrontmatterTooLarge as exc:
            errors.append(f"frontmatter too large: {exc}")
            return JSONResponse({"ok": False, "errors": errors}, status_code=400)
        except Exception as exc:
            errors.append(f"frontmatter parse error: {exc!r}")
            return JSONResponse({"ok": False, "errors": errors}, status_code=400)

        # Per-kind semantic checks. Keep additive / non-authoritative:
        # the canonical validation path is still the runner's parse_*
        # but those unconditionally touch disk, logs, and the runner's
        # registration state — unsuitable for dry-run.
        if kind in {"job", "task", "trigger", "continuation", "webhook"}:
            if not raw_fields:
                errors.append("frontmatter is empty — nothing to validate")
        if kind == "job":
            if not raw_fields.get("schedule") and not raw_fields.get("run-once"):
                errors.append("job: missing 'schedule' (cron) or 'run-once'")
        elif kind == "task":
            if not raw_fields.get("days"):
                errors.append("task: missing 'days'")
        elif kind == "trigger":
            if not raw_fields.get("endpoint"):
                errors.append("trigger: missing 'endpoint'")
        elif kind == "continuation":
            if not raw_fields.get("continues-after") and not raw_fields.get("continues_after"):
                errors.append("continuation: missing 'continues-after'")
        elif kind == "webhook":
            if not raw_fields.get("url"):
                errors.append("webhook: missing 'url'")
        elif kind == "heartbeat":
            if not raw_fields.get("schedule"):
                errors.append("heartbeat: missing 'schedule'")
        else:
            errors.append(f"unknown kind={kind!r}; expected one of job/task/trigger/continuation/webhook/heartbeat")

        return JSONResponse({"ok": not errors, "errors": errors, "parsed": parsed})

    async def routing_handler(request: Request) -> JSONResponse:
        """Return a read-only view of the backend.yaml routing config (#638).

        Shape mirrors the structure of `.witwave/backend.yaml`'s `routing:` block
        so clients (e.g. the dashboard chat selector in #597) can discover
        which backend handles each kind and the default fallback without
        needing to re-parse the YAML themselves.
        """
        if _conversations_auth_token:
            header = request.headers.get("Authorization", "")
            if not hmac_mod.compare_digest(f"Bearer {_conversations_auth_token}", header):
                return JSONResponse({"error": "unauthorized"}, status_code=401)
        _routing = getattr(_executor, "_routing", None) if _executor else None
        _default_id = getattr(_executor, "_default_backend_id", None) if _executor else None

        def _entry_json(entry):
            if entry is None:
                return None
            return {"agent": entry.agent, "model": entry.model}

        _kinds = ("a2a", "heartbeat", "job", "task", "trigger", "continuation")
        _routing_json = {k: _entry_json(getattr(_routing, k, None) if _routing else None) for k in _kinds}
        return JSONResponse(
            {
                "default": _default_id,
                "default_routing": _entry_json(_routing.default if _routing else None),
                "routing": _routing_json,
            }
        )

    # OTel Traces — in-cluster span store (#otel-in-cluster). Serves
    # the Jaeger v1 query shape the dashboard's Traces view already
    # understands (/api/traces[?limit=N&service=…], /api/traces/<id>).
    # Aggregates spans from the harness's own ring buffer AND from every
    # configured sibling backend (claude/openai/gemini) by fetching their
    # /api/traces endpoints. Backends run in the same pod, so the fan-out
    # is localhost-only and fast.
    async def _fetch_remote_traces(url: str) -> list[dict]:
        import httpx

        try:
            async with httpx.AsyncClient(timeout=1.5) as client:
                headers: dict[str, str] = {}
                if _backend_conversations_auth_token:
                    headers["Authorization"] = f"Bearer {_backend_conversations_auth_token}"
                r = await client.get(f"{url.rstrip('/')}/api/traces", params={"limit": 500}, headers=headers)
                if r.status_code != 200:
                    return []
                return (r.json() or {}).get("data") or []
        except Exception:
            return []

    async def _fetch_remote_trace_by_id(url: str, trace_id: str) -> list[dict]:
        """Fetch a single trace by id from one backend (#708).

        Avoids the O(backends * 500 traces) fan-out the detail handler
        used to do just to find one trace — backends expose
        /api/traces/{id} so we ask for the specific trace instead.
        """
        import httpx

        try:
            async with httpx.AsyncClient(timeout=1.5) as client:
                headers: dict[str, str] = {}
                if _backend_conversations_auth_token:
                    headers["Authorization"] = f"Bearer {_backend_conversations_auth_token}"
                r = await client.get(f"{url.rstrip('/')}/api/traces/{trace_id}", headers=headers)
                if r.status_code != 200:
                    return []
                return (r.json() or {}).get("data") or []
        except Exception:
            return []

    # Short-TTL cache for /api/traces list responses (#708). Multiple
    # dashboard tabs polling this endpoint used to fan out fresh HTTP
    # GETs to every backend per request; a 2s cache collapses bursts
    # into a single sweep while keeping the list fresh enough for UX.
    OTEL_TRACES_LIST_CACHE_TTL = float(os.environ.get("OTEL_TRACES_LIST_CACHE_TTL", "2"))
    _otel_traces_list_cache: tuple[float, list[dict]] | None = None
    _otel_traces_list_lock = asyncio.Lock()

    def _merge_trace_lists(groups: list[list[dict]]) -> list[dict]:
        by_tid: dict[str, dict] = {}
        for group in groups:
            for t in group:
                tid = t.get("traceID")
                if not tid:
                    continue
                existing = by_tid.get(tid)
                if not existing:
                    by_tid[tid] = {
                        "traceID": tid,
                        "spans": list(t.get("spans") or []),
                        "processes": dict(t.get("processes") or {}),
                    }
                    continue
                seen = {s.get("spanID") for s in existing["spans"]}
                for s in t.get("spans") or []:
                    if s.get("spanID") not in seen:
                        existing["spans"].append(s)
                existing["processes"].update(t.get("processes") or {})
        return list(by_tid.values())

    def _configured_backend_urls() -> list[str]:
        try:
            if _executor is None:
                return []
            be = _executor._backends  # type: ignore[attr-defined]
            urls: list[str] = []
            # _backends is a dict[id, backend]; each backend exposes ._config
            # or is itself a BackendConfig. Handle both shapes defensively.
            iterable = be.values() if hasattr(be, "values") else be
            for entry in iterable:
                cfg = getattr(entry, "_config", entry)
                u = getattr(cfg, "url", None)
                if u:
                    urls.append(u)
            return urls
        except Exception:
            return []

    async def otel_traces_list_handler(request: Request) -> JSONResponse:
        """Serve the Jaeger-shape OTel trace list (``GET /api/traces``).

        Merges the harness's own in-memory span ring (via
        :func:`otel.get_in_memory_traces`) with the same endpoint on
        every configured backend, fanned out concurrently. The merged
        result is cached for ``OTEL_TRACES_LIST_CACHE_TTL`` seconds
        behind a single-flight lock (#708) so the hot path serves
        ``?limit=`` / ``?service=`` variants without re-fanning out.
        Optional query params:

        - ``limit`` — number of traces to return, clamped to ``[1,
          1000]`` so ``?limit=-5`` doesn't slice ``traces[:-5]`` and
          drop the newest, and ``?limit=99999999`` can't pressure
          memory (#1412). Default 20.
        - ``service`` — case-sensitive filter against each span's
          ``process.serviceName``; a trace matches if any of its
          spans does.

        Bearer-gated with ``CONVERSATIONS_AUTH_TOKEN`` for parity with
        ``/conversations`` / ``/trace`` and sibling backend
        ``/api/traces`` endpoints (#1267). Returns the post-filter
        traces sorted newest-first (by the minimum ``startTime``
        across the trace's spans), truncated to ``limit``, alongside
        the pre-truncation count.
        """
        # #1267: bearer-gate parity with /conversations + /trace + sibling
        # backend /api/traces endpoints (CONVERSATIONS_AUTH_TOKEN).
        unauthorized = _require_conversations_auth(request)
        if unauthorized is not None:
            return unauthorized
        nonlocal _otel_traces_list_cache
        try:
            limit_raw = request.query_params.get("limit")
            limit = int(limit_raw) if limit_raw else 20
        except ValueError:
            limit = 20
        # #1412: clamp limit so ?limit=-5 doesn't slice traces[:-5]
        # (drops newest) and ?limit=99999999 can't cause memory pressure
        # on a long-lived harness. Match the OTEL_IN_MEMORY_SPANS ring cap.
        limit = max(1, min(limit, 1000))
        service = request.query_params.get("service") or ""

        # Serve merged trace list from a short-lived cache when fresh
        # (#708). Cache holds the merged-across-backends result pre-
        # filter/pre-sort so the hot path for limit/service variants
        # still works without a fresh fan-out.
        import asyncio as _asyncio

        now = time.monotonic()
        traces: list[dict] | None = None
        if _otel_traces_list_cache is not None:
            exp, cached = _otel_traces_list_cache
            if now < exp:
                traces = cached

        if traces is None:
            async with _otel_traces_list_lock:
                now = time.monotonic()
                if _otel_traces_list_cache is not None:
                    exp, cached = _otel_traces_list_cache
                    if now < exp:
                        traces = cached
                if traces is None:
                    try:
                        from otel import get_in_memory_traces  # type: ignore

                        local = get_in_memory_traces()
                    except Exception as exc:  # pragma: no cover - defensive
                        logger.warning("otel_traces_list_handler failed: %r", exc)
                        local = []
                    backend_urls = _configured_backend_urls()
                    remote_lists: list[list[dict]] = []
                    if backend_urls:
                        remote_lists = list(
                            await _asyncio.gather(
                                *[_fetch_remote_traces(u) for u in backend_urls],
                                return_exceptions=False,
                            )
                        )
                    traces = _merge_trace_lists([local, *remote_lists])
                    _otel_traces_list_cache = (
                        time.monotonic() + OTEL_TRACES_LIST_CACHE_TTL,
                        traces,
                    )
        if service:
            traces = [
                t
                for t in traces
                if any((s.get("process") or {}).get("serviceName") == service for s in t.get("spans") or [])
            ]

        # Newest-first by the earliest span start time in each trace.
        def _start(t: dict) -> int:
            return min((s.get("startTime") or 0) for s in (t.get("spans") or [{"startTime": 0}]))

        traces.sort(key=_start, reverse=True)
        return JSONResponse({"data": traces[:limit], "total": len(traces)})

    async def otel_traces_detail_handler(request: Request) -> JSONResponse:
        """Serve a single OTel trace by id (``GET /api/traces/{trace_id}``).

        Validates ``trace_id`` as a 32-hex W3C trace id and 400s
        anything else — this closes the path-smuggling surface that
        would otherwise let a caller inject path / query / fragment
        segments into the backend URL templates (#1268). Filters the
        local in-memory span ring to spans of the requested trace and
        fans out per-id fetches to every configured backend
        concurrently (per-id rather than 500-trace list fan-out, #708),
        then merges the results. Returns 404 with ``{"data": [],
        "total": 0}`` if no merged trace matches; otherwise returns the
        merged trace wrapped in ``{"data": [<trace>], "total": 1}``.
        Same ``CONVERSATIONS_AUTH_TOKEN`` bearer gate as
        :func:`otel_traces_list_handler` (#1267).
        """
        # #1267: bearer-gate parity.
        unauthorized = _require_conversations_auth(request)
        if unauthorized is not None:
            return unauthorized
        trace_id = request.path_params.get("trace_id") or ""
        # #1268: refuse anything that isn't a 32-hex W3C trace_id to close
        # the path-smuggling surface that otherwise lets callers inject
        # query/fragment/path segments into the backend URL template.
        import re as _re

        if not _re.fullmatch(r"[0-9a-fA-F]{32}", trace_id):
            return JSONResponse({"error": "trace_id must be 32 hex chars"}, status_code=400)
        try:
            from otel import get_in_memory_traces  # type: ignore

            local_all = get_in_memory_traces()
        except Exception:
            local_all = []
        # Filter local to the requested trace up front — no need to
        # merge the entire local ring just to find one id (#708).
        local = [t for t in local_all if t.get("traceID") == trace_id]
        import asyncio as _asyncio

        backend_urls = _configured_backend_urls()
        remote_lists: list[list[dict]] = []
        if backend_urls:
            # Per-id fetch instead of 500-trace fan-out (#708).
            remote_lists = list(
                await _asyncio.gather(
                    *[_fetch_remote_trace_by_id(u, trace_id) for u in backend_urls],
                    return_exceptions=False,
                )
            )
        merged = _merge_trace_lists([local, *remote_lists])
        match = next((t for t in merged if t.get("traceID") == trace_id), None)
        if match is None:
            return JSONResponse({"data": [], "total": 0}, status_code=404)
        return JSONResponse({"data": [match], "total": 1})

    # ---- SSE event stream (#1110) -----------------------------------
    # Publishes the phase-1 event set documented in docs/events/README.md
    # over a long-lived Server-Sent Events connection. Clients reconnect
    # with Last-Event-ID to resume from the in-memory ring. Shares the
    # CONVERSATIONS_AUTH_TOKEN bearer so dashboards don't need a separate
    # credential to browse live events alongside /conversations + /trace.
    from events import get_event_stream as _get_event_stream  # noqa: E402
    from metrics import (  # noqa: E402
        harness_event_stream_events_dropped_total,
        harness_event_stream_events_published_total,
        harness_event_stream_overruns_total,
        harness_event_stream_ring_size,
        harness_event_stream_subscribers,
        harness_event_stream_validation_errors_total,
    )

    _get_event_stream().attach_metrics(
        subscribers=harness_event_stream_subscribers,
        published_total=harness_event_stream_events_published_total,
        dropped_total=harness_event_stream_events_dropped_total,
        overruns_total=harness_event_stream_overruns_total,
        validation_errors_total=harness_event_stream_validation_errors_total,
        ring_size=harness_event_stream_ring_size,
    )

    EVENT_STREAM_KEEPALIVE_SEC = float(os.environ.get("EVENT_STREAM_KEEPALIVE_SEC", "15"))

    async def events_stream_handler(request: Request):
        from starlette.responses import (
            StreamingResponse,  # local import keeps startup path identical on older Starlette
        )

        # Auth: reuse CONVERSATIONS_AUTH_TOKEN. Fail-closed unless the
        # explicit escape hatch is set, matching the parity pattern used
        # by /conversations and /trace.
        if not _conversations_auth_token:
            try:
                from conversations import auth_disabled_escape_hatch  # type: ignore[attr-defined]
            except Exception:
                auth_disabled_escape_hatch = None  # type: ignore[assignment]
            if auth_disabled_escape_hatch is None or not auth_disabled_escape_hatch():
                return JSONResponse({"error": "auth not configured"}, status_code=503)
        else:
            header = request.headers.get("Authorization", "")
            if not hmac_mod.compare_digest(f"Bearer {_conversations_auth_token}", header):
                return JSONResponse({"error": "unauthorized"}, status_code=401)

        last_id = request.headers.get("Last-Event-ID") or request.query_params.get("last_event_id")
        stream = _get_event_stream()

        async def _gen():
            import json as _json

            # #1229: Subscribe FIRST so no event published during replay is
            # lost. Snapshot the stream's current _next_id right before
            # subscribing; replay covers ids <= snapshot, live covers ids
            # > snapshot. Track delivered ids to de-dup the race window.
            _replay_snapshot_id = stream._next_id  # noqa: SLF001 (intentional race-window snapshot)
            sub_iter = stream.subscribe()

            _delivered_ids: set[str] = set()
            for envelope in stream.replay_from(last_id):
                # Filter: only emit envelopes at-or-below the snapshot
                # (anything newer will arrive via the live subscription).
                try:
                    if int(envelope.id) > _replay_snapshot_id:
                        continue
                except Exception:
                    # Non-numeric ids (e.g. synthetic "{n}.overrun") keep
                    # the existing behaviour; replay emits them as-is.
                    pass
                _delivered_ids.add(envelope.id)
                yield (
                    f"event: {envelope.type}\n"
                    f"id: {envelope.id}\n"
                    f"data: {_json.dumps(envelope.to_dict(), separators=(',', ':'))}\n\n"
                ).encode()

            # Live subscription with a keepalive ticker running alongside.
            sub_task: asyncio.Task = asyncio.ensure_future(sub_iter.__anext__())
            ka_task: asyncio.Task = asyncio.ensure_future(asyncio.sleep(EVENT_STREAM_KEEPALIVE_SEC))
            try:
                while True:
                    done, _pending = await asyncio.wait(
                        {sub_task, ka_task},
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    if sub_task in done:
                        try:
                            envelope = sub_task.result()
                        except StopAsyncIteration:
                            # Subscriber closed (evicted or shutdown).
                            return
                        # #1229: skip ids already delivered via replay
                        # during the subscribe/replay overlap window.
                        if envelope.id in _delivered_ids:
                            _delivered_ids.discard(envelope.id)
                            sub_task = asyncio.ensure_future(sub_iter.__anext__())
                            continue
                        yield (
                            f"event: {envelope.type}\n"
                            f"id: {envelope.id}\n"
                            f"data: {_json.dumps(envelope.to_dict(), separators=(',', ':'))}\n\n"
                        ).encode()
                        sub_task = asyncio.ensure_future(sub_iter.__anext__())
                    if ka_task in done:
                        yield b": keepalive\n\n"
                        ka_task = asyncio.ensure_future(asyncio.sleep(EVENT_STREAM_KEEPALIVE_SEC))
            except asyncio.CancelledError:
                raise
            finally:
                for t in (sub_task, ka_task):
                    if not t.done():
                        t.cancel()
                # #1276: await the cancelled tasks so cleanup runs
                # synchronously and the subscriber slot in EventStream
                # is released before aclose().
                try:
                    await asyncio.gather(sub_task, ka_task, return_exceptions=True)
                except Exception:  # pragma: no cover
                    pass
                try:
                    await sub_iter.aclose()  # type: ignore[attr-defined]
                except Exception:
                    pass

        headers = {
            "Content-Type": "text/event-stream",
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        }
        return StreamingResponse(_gen(), headers=headers, media_type="text/event-stream")

    _routes = [
        Route("/health/start", health_start),
        Route(
            "/health", health_live
        ),  # bare /health alias for liveness — uniform surface across harness/backends/MCP tools
        Route("/health/live", health_live),
        Route("/health/ready", health_ready),
        Route("/.well-known/agent-triggers.json", triggers_discovery, methods=["GET"]),
        Route("/.well-known/agent-runs.json", runs_discovery, methods=["GET"]),
        # Intentionally POST-only: trigger endpoints dispatch work. Discovery
        # and metadata live on GET /triggers and /.well-known/agent-triggers.json.
        # Leaving HEAD unsupported avoids creating a second readiness/auth
        # contract for per-trigger URLs.
        Route("/triggers/{endpoint}", trigger_handler, methods=["POST"]),
        Route("/jobs/{name}/run", jobs_run_handler, methods=["POST"]),
        Route("/tasks/{name}/run", tasks_run_handler, methods=["POST"]),
        Route("/heartbeat/run", heartbeat_run_handler, methods=["POST"]),
        Route("/agents", agents_handler, methods=["GET"]),
        Route("/jobs", jobs_handler, methods=["GET"]),
        Route("/tasks", tasks_handler, methods=["GET"]),
        Route("/webhooks", webhooks_handler, methods=["GET"]),
        Route("/continuations", continuations_handler, methods=["GET"]),
        Route("/heartbeat", heartbeat_handler, methods=["GET"]),
        Route("/triggers", triggers_handler, methods=["GET"]),
        Route("/validate", validate_handler, methods=["POST"]),
        Route("/routing", routing_handler, methods=["GET"]),
        Route("/conversations", conversations_handler, methods=["GET"]),
        Route("/trace", trace_handler, methods=["GET"]),
        # OTel in-memory span store (#otel-in-cluster). Serves the
        # Jaeger-compatible JSON shape the dashboard's OTel Traces view
        # expects, so operators see distributed traces without needing
        # an external Jaeger/Tempo. Returns empty when OTEL_ENABLED is
        # off or OTEL_IN_MEMORY_SPANS=0.
        Route("/api/traces", otel_traces_list_handler, methods=["GET"]),
        Route("/api/traces/{trace_id}", otel_traces_detail_handler, methods=["GET"]),
        # SSE event stream (#1110). Long-lived text/event-stream; shares
        # the CONVERSATIONS_AUTH_TOKEN bearer with /conversations + /trace.
        Route("/events/stream", events_stream_handler, methods=["GET"]),
    ]
    # Metrics live on a dedicated port (:9000 by default, configurable via
    # METRICS_PORT), NOT on the main app listener (#643). The split lets
    # NetworkPolicy + auth posture diverge cleanly between app traffic
    # (A2A/triggers/conversations) and monitoring scrapes. The listener is
    # started inside the lifespan hook below; nothing is registered on the
    # main app's route table here.
    _routes.append(Mount("/", app=a2a_built))

    @asynccontextmanager
    async def lifespan(_app: Starlette):
        """Forward ASGI lifespan events to all mounted sub-apps.

        Starlette's Router collects on_startup/on_shutdown from mounted apps
        via Mount.on_startup, but does not forward the ASGI lifespan scope.
        A mounted app that registers lifecycle hooks via the lifespan context
        manager API would have those hooks silently skipped.  Iterating the
        routes and driving each sub-app's lifespan via the standard ASGI
        lifespan scope protocol ensures startup and shutdown are propagated
        regardless of which API is used, and remains correct across Starlette
        version upgrades and as new mounts are added in the future.
        """
        async with AsyncExitStack() as stack:
            for route in _routes:
                if isinstance(route, Mount):
                    await stack.enter_async_context(_sub_app_lifespan(route.app))
            # Start the dedicated :METRICS_PORT listener (#643). Started
            # inside lifespan so it shares the main event loop and gets
            # cancelled cleanly on shutdown. Capture the returned task
            # (#863) and cancel+await it in the finally block so its
            # socket releases before container exit — otherwise uvicorn
            # emits "Task was destroyed but it is pending" on SIGTERM
            # and the :9000 socket hangs briefly across restarts.
            _metrics_task: asyncio.Task | None = None
            if metrics_enabled:
                from metrics_server import start_metrics_server

                # Host /internal/events/hook-decision on the dedicated
                # metrics listener (#924) so a NetworkPolicy that restricts
                # the metrics port to the Prometheus scraper + same-pod
                # backends cannot be bypassed by in-pod peers spoofing the
                # bearer on the public app port. When metrics is disabled
                # the route remains on the app listener for backward
                # compatibility (register_app_internal_route below).
                _metrics_task = start_metrics_server(
                    metrics_handler,
                    logger=logger,
                    extra_routes=[
                        Route(
                            "/internal/events/hook-decision",
                            hook_decision_event_handler,
                            methods=["POST"],
                        ),
                        # Generic backend→harness event channel (#1110 phase 3).
                        # Same auth/port posture as hook-decision so NetworkPolicy
                        # and scraper posture applies uniformly.
                        Route(
                            "/internal/events/publish",
                            event_publish_handler,
                            methods=["POST"],
                        ),
                    ],
                )
            else:
                # Metrics disabled — fall back to exposing the hook
                # event receiver on the app listener so backend→harness
                # hook.decision forwarding still works. Operators who
                # care about the spoofing exposure (#924) should enable
                # the metrics port so the route moves off the app port.
                _app.router.add_route(
                    "/internal/events/hook-decision",
                    hook_decision_event_handler,
                    methods=["POST"],
                )
                _app.router.add_route(
                    "/internal/events/publish",
                    event_publish_handler,
                    methods=["POST"],
                )
                logger.warning(
                    "METRICS_ENABLED=0: /internal/events/hook-decision + "
                    "/internal/events/publish are bound to the public app "
                    "listener. Enable the metrics listener to move the routes "
                    "off the app port (#924)."
                )
            try:
                yield
            finally:
                # Phased-shutdown ordering (#861, #976): executor background
                # drain moved to AFTER bus_task cancel in main's supervisor
                # (see Phase 2.5 below). Draining here while the bus worker
                # is still scheduling new on_prompt_completed tasks allowed
                # late-added tasks to race close_backends() and hit a closed
                # httpx client.
                if _metrics_task is not None and not _metrics_task.done():
                    _metrics_task.cancel()
                    try:
                        await _metrics_task
                    except (asyncio.CancelledError, Exception):
                        # Metrics listener cancellation is best-effort; we
                        # don't want a stray exception from uvicorn's
                        # server.serve() to mask a real shutdown failure.
                        pass

    # Fail-closed CORS policy (#701). CORS_ALLOW_ORIGINS="*" combined
    # with the sensitive /triggers/*, /jobs/*/run, /trace, and
    # /conversations endpoints is a CSRF liability: a victim's browser
    # opening any page can fire authenticated-callers' credentials at
    # this harness.  The wildcard path is therefore only permitted when
    # the operator explicitly acknowledges it via
    # CORS_ALLOW_WILDCARD=true.  With the default (false) a wildcard
    # value is downgraded to "no origins allowed" AND a startup error
    # is logged so the operator notices.  allow_headers is also
    # tightened from "*" to an explicit small list so a hostile origin
    # can't smuggle custom headers through preflight.
    _cors_wildcard_ack = os.environ.get("CORS_ALLOW_WILDCARD", "").lower() in ("1", "true", "yes")
    _effective_cors = CORS_ALLOW_ORIGINS
    if CORS_ALLOW_ORIGINS == ["*"] and not _cors_wildcard_ack:
        logger.error(
            "CORS_ALLOW_ORIGINS=* refused without CORS_ALLOW_WILDCARD=true "
            "acknowledgement (#701). Falling back to 'no origins allowed'. "
            "Set CORS_ALLOW_ORIGINS to an explicit origin list, OR set "
            "CORS_ALLOW_WILDCARD=true if you truly intend a public-browser "
            "deployment."
        )
        _effective_cors = []
    if not _effective_cors:
        logger.warning(
            "CORS_ALLOW_ORIGINS resolved to empty — cross-origin browser requests will be denied. "
            "Set CORS_ALLOW_ORIGINS to a comma-separated list of allowed origins "
            "(e.g. 'http://localhost:3002') to permit browser access."
        )
    elif _effective_cors == ["*"]:
        logger.warning(
            "CORS is configured to allow all origins (CORS_ALLOW_ORIGINS=*). "
            "CORS_ALLOW_WILDCARD=true acknowledged; /triggers/*, /jobs/*/run, "
            "/trace, /conversations are reachable from ANY browser origin. "
            "Prefer an explicit origin list for production."
        )
    else:
        logger.info("CORS allowed origins: %s", _effective_cors)

    _CORS_ALLOWED_HEADERS = [
        "Authorization",
        "Content-Type",
        "Accept",
        "traceparent",
        "tracestate",
        "baggage",
    ]

    full_app = Starlette(
        routes=_routes,
        lifespan=lifespan,
        middleware=[
            Middleware(
                CORSMiddleware,
                allow_origins=_effective_cors,
                allow_methods=["GET", "POST", "OPTIONS"],
                allow_headers=_CORS_ALLOWED_HEADERS,
            ),
        ],
    )

    logger.info(f"Starting {AGENT_NAME} on {HARNESS_HOST}:{HARNESS_PORT}")
    config = uvicorn.Config(full_app, host=HARNESS_HOST, port=HARNESS_PORT)
    server = uvicorn.Server(config)

    executor.set_continuation_runner(continuation_runner, bus)
    executor.set_webhook_runner(webhook_runner)

    # Start MCP watcher tasks as tracked background tasks so backends_watcher
    # can cancel and replace them when backends are hot-reloaded.
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

    _backends_ready_warn_after = float(os.environ.get("BACKENDS_READY_WARN_AFTER", "120"))

    async def _wait_for_backends() -> None:
        """Poll backend /health endpoints until all pass, then set backends_ready.

        Logs a warning after BACKENDS_READY_WARN_AFTER seconds if backends are
        still not healthy, but continues polling indefinitely so that slow-starting
        backends (e.g. large image pulls on first run) are not prematurely released.
        """
        import httpx

        logger.info("Waiting for all backends to become healthy before firing run-once jobs/tasks.")
        warn_deadline = time.monotonic() + _backends_ready_warn_after
        _warned = False
        _attempt = 0
        _BACKOFF_BASE = 2.0
        _BACKOFF_MAX = 30.0
        # Share a single AsyncClient across sweeps (#1277). Prior code created and
        # tore down an httpx client every iteration, paying per-attempt TCP+TLS
        # cost and losing connection pooling. One client, closed once at exit.
        client = httpx.AsyncClient(
            timeout=3.0,
            limits=httpx.Limits(max_keepalive_connections=16, max_connections=32),
        )
        try:
            while True:
                backend_configs = [b._config for b in executor._backends.values() if b._config.url]
                if not backend_configs:
                    # No backends configured yet — wait for backends_watcher to load them.
                    await asyncio.sleep(1)
                    continue
                results = await asyncio.gather(
                    *[client.get(b.url.rstrip("/") + "/health") for b in backend_configs],
                    return_exceptions=True,
                )
                all_ok = all(not isinstance(r, Exception) and r.status_code == 200 for r in results)
                if all_ok:
                    logger.info("All backends healthy — releasing run-once jobs/tasks.")
                    backends_ready.set()
                    return
                if not _warned and time.monotonic() >= warn_deadline:
                    _warned = True
                    unhealthy = [
                        b.id
                        for b, r in zip(backend_configs, results, strict=True)
                        if isinstance(r, Exception) or r.status_code != 200
                    ]
                    logger.warning(
                        "Backends not healthy after %.0fs — still waiting. Unhealthy: %s",
                        _backends_ready_warn_after,
                        unhealthy,
                    )
                delay = min(_BACKOFF_BASE * (2**_attempt), _BACKOFF_MAX)
                delay += random.uniform(0, delay * 0.1)
                _attempt += 1
                await asyncio.sleep(delay)
        finally:
            await client.aclose()

    # Coordinated shutdown (#780). Previously `asyncio.gather` ran
    # server.serve() alongside the bus worker, scheduler runners, and
    # watchers as unstructured siblings; SIGTERM propagated cancellation
    # through all of them simultaneously, with no ordering guarantee
    # between "stop accepting new work" and "drain in-flight work".
    # Now we create each runner as a named task, run server.serve() in
    # the foreground, and cancel the runners in phases once the server
    # exits: schedulers first (stop accepting new triggers / heartbeat
    # ticks / job fires), then the bus worker (so the final batch of
    # in-flight prompts sees cancellation), then watchers.
    scheduler_tasks: list[asyncio.Task] = [
        asyncio.create_task(
            # Pass backends_ready so the heartbeat loop blocks its first
            # fire until /health passes (#785).
            _guarded(heartbeat_runner, bus, backends_ready),
            name="heartbeat_runner",
        ),
        asyncio.create_task(_guarded(job_runner.run), name="job_runner"),
        asyncio.create_task(_guarded(task_runner.run), name="task_runner"),
        asyncio.create_task(_guarded(trigger_runner.run), name="trigger_runner"),
        asyncio.create_task(_guarded(continuation_runner.run), name="continuation_runner"),
        asyncio.create_task(_guarded(webhook_runner.run), name="webhook_runner"),
    ]
    # Dedicated hook.decision dispatch task (#928): the /internal/events/
    # hook-decision HTTP handler enqueues events and returns 202; this
    # task drains the queue and fans out to listeners so a listener that
    # grows a sync-blocking step cannot push backpressure upstream into
    # the backend's hook-posting thread.
    from bus import start_hook_decision_dispatcher as _start_hook_dispatch

    _hook_decision_dispatch_task = _start_hook_dispatch(asyncio.get_running_loop())
    scheduler_tasks.append(_hook_decision_dispatch_task)
    bus_task = asyncio.create_task(
        _guarded(bus_worker, bus, executor, critical=True),
        name="bus_worker",
    )
    watcher_tasks: list[asyncio.Task] = [
        asyncio.create_task(_guarded(_event_loop_monitor), name="event_loop_monitor"),
        asyncio.create_task(_guarded(executor.backends_watcher), name="backends_watcher"),
    ]
    helper_tasks: list[asyncio.Task] = [
        asyncio.create_task(_set_ready_when_started(server), name="set_ready"),
        asyncio.create_task(_wait_for_backends(), name="wait_for_backends"),
    ]

    try:
        await server.serve()
    finally:
        # Phase 1: schedulers stop firing new work.
        for t in scheduler_tasks:
            t.cancel()
        await asyncio.gather(*scheduler_tasks, return_exceptions=True)
        # Phase 2: bus worker drains (cancellation propagates into
        # in-flight process_bus calls so they don't get stuck on a
        # shut-down backend transport).
        bus_task.cancel()
        await asyncio.gather(bus_task, return_exceptions=True)
        # Phase 2.5: drain executor-owned background work (MCP watchers
        # + fire-and-forget on_prompt_completed continuations, webhook
        # fan-outs, etc.) AFTER the bus worker has stopped scheduling
        # new ones (#976). Running this in the lifespan finally allowed
        # bus_worker to enqueue late tasks that then raced close_backends.
        try:
            await executor.drain_background()
        except Exception as exc:  # noqa: BLE001 — shutdown must continue
            logger.warning("executor.drain_background() failed during shutdown: %r", exc)
        # Phase 3: watchers and helper tasks. These observe filesystem
        # / health state and have nothing to drain once schedulers and
        # bus worker are done.
        for t in (*watcher_tasks, *helper_tasks):
            t.cancel()
        await asyncio.gather(*watcher_tasks, *helper_tasks, return_exceptions=True)
        # Phase 4: drain in-flight webhook deliveries (#923). The
        # scheduler_tasks cancel above stops WebhookRunner.run() (the
        # dispatch loop), but the retry tasks it had spawned into
        # webhook_runner._active_deliveries are NOT in scheduler_tasks
        # — they were cancelled mid-POST only when the kubelet sent
        # SIGKILL at the end of the termination grace. Call
        # webhook_runner.close() so those tasks drain (with their own
        # internal timeout) before we teardown the httpx clients below.
        # close() is idempotent and safe to invoke even when
        # scheduler_tasks cancellation already ended the dispatch loop.
        try:
            await webhook_runner.close()
        except Exception as exc:  # noqa: BLE001 — shutdown must continue
            logger.warning("webhook_runner.close() failed during shutdown: %r", exc)
        # #1275: drain in-flight continuation fires before bus-worker exit.
        try:
            await continuation_runner.close(timeout=float(os.environ.get("CONTINUATIONS_SHUTDOWN_DRAIN_TIMEOUT", "5")))
        except Exception as exc:  # noqa: BLE001 — shutdown must continue
            logger.warning("continuation_runner.close() failed during shutdown: %r", exc)
        # Phase 5: backend httpx clients (#861). Close only AFTER the
        # bus worker has drained — otherwise in-flight process_bus calls
        # see a closed client and surface as "client has been closed".
        try:
            await executor.close_backends()
        except Exception as exc:  # noqa: BLE001 — shutdown must continue
            logger.warning("executor.close_backends() failed during shutdown: %r", exc)


if __name__ == "__main__":
    asyncio.run(main())
