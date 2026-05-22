"""Prometheus metrics — echo backend.

Echo doubles as a reference implementation for the common ``backend_*``
metric baseline every backend should emit. The series declared here are
deliberately a **subset** of what claude/openai/gemini expose — specifically
the lifecycle, A2A request surface, and prompt-shape metrics that any
well-behaved backend can produce regardless of whether it's LLM-backed.

Metrics that are genuinely LLM-specific (SDK error classes, context window,
session LRU, MCP request surface, tool audit, hooks denials, context
exhaustion) are intentionally NOT declared here — they don't generalise to
non-LLM backends, and declaring them as always-zero placeholders would muddy
dashboards rather than clarify them.

All series share the ``(agent, agent_id, backend)`` label set so cross-
backend dashboards can union across backend types without backend-specific
series names. See the "Metrics landscape" section of AGENTS.md for the
superset story.

Gated on ``METRICS_ENABLED``. When the env var is unset or empty, every
symbol in this module is ``None`` and callers guard with ``if X is not None``.
"""

from __future__ import annotations

import prometheus_client
from env import parse_bool_env

_LABELS = ("agent", "agent_id", "backend")


def _metrics_enabled() -> bool:
    """Evaluate ``METRICS_ENABLED`` at call time rather than import time.

    Capturing the env var at module import creates a fragile ordering
    requirement — any caller that imports ``metrics`` before setting
    ``METRICS_ENABLED`` would see an empty registry forever. Reading at
    call time removes that footgun and costs nothing.
    """
    return parse_bool_env("METRICS_ENABLED")


# ---------------------------------------------------------------------------
# Lifecycle / service-level
# ---------------------------------------------------------------------------

backend_up: prometheus_client.Gauge | None = None
backend_info: prometheus_client.Info | None = None
backend_uptime_seconds: prometheus_client.Gauge | None = None
backend_startup_duration_seconds: prometheus_client.Gauge | None = None
backend_health_checks_total: prometheus_client.Counter | None = None

# ---------------------------------------------------------------------------
# A2A request surface
# ---------------------------------------------------------------------------

backend_a2a_requests_total: prometheus_client.Counter | None = None
backend_a2a_request_duration_seconds: prometheus_client.Histogram | None = None
backend_a2a_last_request_timestamp_seconds: prometheus_client.Gauge | None = None

# ---------------------------------------------------------------------------
# Prompt / response shape
# ---------------------------------------------------------------------------

backend_prompt_length_bytes: prometheus_client.Histogram | None = None
backend_response_length_bytes: prometheus_client.Histogram | None = None
backend_empty_prompts_total: prometheus_client.Counter | None = None
# #1650 (cross-ref #1620): prompt-size cap rejections. Bumps each time
# execute() rejects a prompt whose UTF-8 byte length exceeds
# MAX_PROMPT_BYTES. Mirrors the codex counter so cross-backend dashboards
# can union on the same series name.
backend_prompt_too_large_total: prometheus_client.Counter | None = None


def init_metrics() -> None:
    """Declare the Prometheus series. Idempotent — safe to call twice.

    Must be called before any code path references a metric. Real usage
    is ``if backend_up is not None:`` checks guarding ``.inc()``/``.set()``
    calls, so a disabled registry silently no-ops.
    """
    global backend_up, backend_info, backend_uptime_seconds
    global backend_startup_duration_seconds, backend_health_checks_total
    global backend_a2a_requests_total, backend_a2a_request_duration_seconds
    global backend_a2a_last_request_timestamp_seconds
    global backend_prompt_length_bytes, backend_response_length_bytes
    global backend_empty_prompts_total, backend_prompt_too_large_total

    if not _metrics_enabled() or backend_up is not None:
        return

    backend_up = prometheus_client.Gauge(
        "backend_up",
        "1 when the backend is running and accepting traffic.",
        _LABELS,
    )
    backend_info = prometheus_client.Info(
        "backend",
        "Static metadata about the running backend (version, type).",
    )
    backend_uptime_seconds = prometheus_client.Gauge(
        "backend_uptime_seconds",
        "Seconds since the backend process started.",
        _LABELS,
    )
    backend_startup_duration_seconds = prometheus_client.Gauge(
        "backend_startup_duration_seconds",
        "Seconds from process start to the /health endpoint flipping to ok.",
        _LABELS,
    )
    backend_health_checks_total = prometheus_client.Counter(
        "backend_health_checks_total",
        "Total /health probes served.",
        _LABELS + ("probe",),
    )

    backend_a2a_requests_total = prometheus_client.Counter(
        "backend_a2a_requests_total",
        "Total A2A task requests handled, by terminal status.",
        _LABELS + ("status",),
    )
    backend_a2a_request_duration_seconds = prometheus_client.Histogram(
        "backend_a2a_request_duration_seconds",
        "Duration of A2A task execution (executor.execute) in seconds.",
        _LABELS,
    )
    backend_a2a_last_request_timestamp_seconds = prometheus_client.Gauge(
        "backend_a2a_last_request_timestamp_seconds",
        "Unix timestamp of the most recently completed A2A request.",
        _LABELS,
    )

    backend_prompt_length_bytes = prometheus_client.Histogram(
        "backend_prompt_length_bytes",
        "Distribution of inbound prompt sizes in bytes (UTF-8).",
        _LABELS,
        buckets=(64, 256, 1024, 4096, 16384, 65536, 262144),
    )
    backend_response_length_bytes = prometheus_client.Histogram(
        "backend_response_length_bytes",
        "Distribution of outbound response sizes in bytes (UTF-8).",
        _LABELS,
        buckets=(64, 256, 1024, 4096, 16384, 65536, 262144),
    )
    backend_empty_prompts_total = prometheus_client.Counter(
        "backend_empty_prompts_total",
        "Prompts rejected as empty or whitespace-only.",
        _LABELS,
    )
    # #1650 (cross-ref #1620): oversized-prompt rejections. Cap is configured
    # via MAX_PROMPT_BYTES env (default 1 MiB on echo — echo is a hello-world
    # backend so the cap is tighter than codex's 10 MiB).
    backend_prompt_too_large_total = prometheus_client.Counter(
        "backend_prompt_too_large_total",
        "Total execute() invocations rejected because the prompt exceeded MAX_PROMPT_BYTES (#1650).",
        _LABELS,
    )
