"""Prometheus metrics for the openai backend agent."""

import prometheus_client
from env import parse_bool_env

_enabled = parse_bool_env("METRICS_ENABLED")

# Deprecated-metrics emission gate (#940). Defaults ON so pre-migration
# dashboards keep working; operators flip to "0"/"false" once they have
# re-pointed panels and alerts at backend_hooks_denials_total. One release
# after default flips to off, the backend-specific hook-denial aliases
# will be removed outright. Print the resolved posture at import time so
# the migration window is visible in kubectl logs.
_EMIT_DEPRECATED_HOOK_METRICS = parse_bool_env("EMIT_DEPRECATED_HOOK_METRICS", default=True)

# Service-level metrics
backend_up: prometheus_client.Gauge | None = None
backend_info: prometheus_client.Info | None = None
# Underlying SDK version info (#1092).
backend_sdk_info: prometheus_client.Info | None = None
backend_uptime_seconds: prometheus_client.Gauge | None = None
backend_startup_duration_seconds: prometheus_client.Gauge | None = None
backend_event_loop_lag_seconds: prometheus_client.Histogram | None = None
backend_health_checks_total: prometheus_client.Counter | None = None
backend_task_restarts_total: prometheus_client.Counter | None = None

# A2A request metrics
backend_a2a_requests_total: prometheus_client.Counter | None = None
backend_a2a_request_duration_seconds: prometheus_client.Histogram | None = None
backend_a2a_last_request_timestamp_seconds: prometheus_client.Gauge | None = None

# Task execution metrics
backend_tasks_total: prometheus_client.Counter | None = None
backend_task_duration_seconds: prometheus_client.Histogram | None = None
backend_task_error_duration_seconds: prometheus_client.Histogram | None = None
backend_task_last_success_timestamp_seconds: prometheus_client.Gauge | None = None
backend_task_last_error_timestamp_seconds: prometheus_client.Gauge | None = None
backend_task_timeout_headroom_seconds: prometheus_client.Histogram | None = None
backend_task_cancellations_total: prometheus_client.Counter | None = None
backend_running_tasks: prometheus_client.Gauge | None = None
backend_concurrent_queries: prometheus_client.Gauge | None = None

# Session metrics
backend_active_sessions: prometheus_client.Gauge | None = None
backend_session_starts_total: prometheus_client.Counter | None = None
backend_session_evictions_total: prometheus_client.Counter | None = None
backend_session_age_seconds: prometheus_client.Histogram | None = None
backend_session_idle_seconds: prometheus_client.Histogram | None = None
backend_lru_cache_utilization_percent: prometheus_client.Gauge | None = None
backend_session_history_save_errors_total: prometheus_client.Counter | None = None

# Prompt / response size metrics
backend_prompt_length_bytes: prometheus_client.Histogram | None = None
backend_response_length_bytes: prometheus_client.Histogram | None = None
backend_empty_responses_total: prometheus_client.Counter | None = None
backend_empty_prompts_total: prometheus_client.Counter | None = None
# #1620: prompt-size cap rejections. Bumps each time execute() rejects a
# prompt whose UTF-8 byte length exceeds MAX_PROMPT_BYTES. Operators alert
# on a non-zero rate to detect runaway clients before they OOM the pod.
backend_prompt_too_large_total: prometheus_client.Counter | None = None
# #1497: counts turns where result.final_output differed from the streamed
# deltas. When non-zero, the executor now returns final_output (the SDK's
# authoritative answer) rather than the streamed intermediate content.
backend_final_output_divergence_total: prometheus_client.Counter | None = None
backend_stderr_lines_per_task: prometheus_client.Histogram | None = None
backend_tasks_with_stderr_total: prometheus_client.Counter | None = None

# Model / backend routing metrics
backend_model_requests_total: prometheus_client.Counter | None = None

# Logging subsystem metrics
backend_log_bytes_total: prometheus_client.Counter | None = None
backend_log_entries_total: prometheus_client.Counter | None = None
backend_log_write_errors_total: prometheus_client.Counter | None = None
# Per-logger write errors (#626 / #804). Non-breaking complement to
# backend_log_write_errors_total — carries a `logger` label so operators can
# distinguish tool-audit vs conversation vs trace write failures.
backend_log_write_errors_by_logger_total: prometheus_client.Counter | None = None

# SDK metrics
backend_sdk_query_duration_seconds: prometheus_client.Histogram | None = None
backend_sdk_query_error_duration_seconds: prometheus_client.Histogram | None = None
backend_sdk_time_to_first_message_seconds: prometheus_client.Histogram | None = None
backend_sdk_session_duration_seconds: prometheus_client.Histogram | None = None
backend_sdk_messages_per_query: prometheus_client.Histogram | None = None
backend_sdk_turns_per_query: prometheus_client.Histogram | None = None
backend_text_blocks_per_query: prometheus_client.Histogram | None = None
backend_sdk_tokens_per_query: prometheus_client.Histogram | None = None
backend_streaming_events_emitted_total: prometheus_client.Counter | None = None
backend_streaming_chunks_dropped_total: prometheus_client.Counter | None = None

# MCP config metrics (parity with claude — #432)
backend_mcp_config_errors_total: prometheus_client.Counter | None = None
backend_mcp_config_reloads_total: prometheus_client.Counter | None = None
backend_mcp_servers_active: prometheus_client.Gauge | None = None
backend_mcp_command_rejected_total: prometheus_client.Counter | None = None
# /mcp transport observability — parity with claude / gemini (#962).
backend_mcp_requests_total: prometheus_client.Counter | None = None
backend_mcp_request_duration_seconds: prometheus_client.Histogram | None = None

# SDK error classification metrics (parity with claude — #431)
backend_sdk_errors_total: prometheus_client.Counter | None = None
backend_sdk_result_errors_total: prometheus_client.Counter | None = None
backend_sdk_client_errors_total: prometheus_client.Counter | None = None
backend_sdk_context_fetch_errors_total: prometheus_client.Counter | None = None
backend_sdk_subprocess_spawn_duration_seconds: prometheus_client.Histogram | None = None
backend_session_path_mismatch_total: prometheus_client.Counter | None = None

# Retry / recovery metrics (parity with claude — #803)
backend_task_retries_total: prometheus_client.Counter | None = None

# File watcher metrics
backend_watcher_events_total: prometheus_client.Counter | None = None
backend_file_watcher_restarts_total: prometheus_client.Counter | None = None

# AGENTS.md hot-reload rollout tracking (#1097). The `revision` label carries
# the first 12 hex chars of the SHA-256 of the currently-active AGENTS.md
# content; the gauge value is always 1 when set. On hot-reload the previous
# revision's label set is removed so only the live revision reports 1.
# Operators correlate this with the per-query span attribute
# `openai.agent_md_revision` to verify an AGENTS.md rollout has propagated.
backend_agent_md_revision: prometheus_client.Gauge | None = None

# Context window metrics
backend_context_tokens: prometheus_client.Histogram | None = None
backend_context_tokens_remaining: prometheus_client.Histogram | None = None
backend_context_usage_percent: prometheus_client.Histogram | None = None
backend_context_exhaustion_total: prometheus_client.Counter | None = None
backend_context_warnings_total: prometheus_client.Counter | None = None

# Tool-call metrics
backend_sdk_tool_calls_total: prometheus_client.Counter | None = None
backend_sdk_tool_calls_per_query: prometheus_client.Histogram | None = None
backend_sdk_tool_duration_seconds: prometheus_client.Histogram | None = None
backend_sdk_tool_errors_total: prometheus_client.Counter | None = None
backend_sdk_tool_call_input_size_bytes: prometheus_client.Histogram | None = None
backend_sdk_tool_result_size_bytes: prometheus_client.Histogram | None = None

# Token budget metrics
backend_budget_exceeded_total: prometheus_client.Counter | None = None

# Hooks / tool-audit (#586) — shell-only baseline scope.
# Non-shell enforcement and the rest of the backend_hooks_* family stay deferred
# until a tool-wrapping proxy design is validated against the Agents SDK.
backend_openai_hooks_denials_total: prometheus_client.Counter | None = None
backend_codex_hooks_denials_total: prometheus_client.Counter | None = None
# Canonical cross-backend denial counter (#789). Shares the same label
# schema as claude/gemini so cross-backend dashboards can union by
# (agent, agent_id, backend). Backend-specific aliases stay in place to
# avoid breaking existing scrapers during the codex -> openai rename; all
# aliases increment on each deny.
backend_hooks_denials_total: prometheus_client.Counter | None = None
backend_hooks_shed_total: prometheus_client.Counter | None = None
# #1052: counts hook.decision emissions where _current_session_id
# fell through to its "" default. Normal task dispatch seeds the
# ContextVar in _run_inner; an empty value means an edge-dispatch path
# (warmup, lifespan, /mcp tools/call) reached the baseline without a
# session seed — worth a WARN + an alertable metric.
backend_hook_session_missing_total: prometheus_client.Counter | None = None
# Peer-parity hook metric family (#800) — matches the claude superset so
# cross-backend dashboards don't drop the series. openai's hook path is
# shell-baseline-only today (#586/#799 deferred) so warnings/config-*
# stay at their registered zero value until non-shell enforcement lands,
# but declaring them keeps PromQL join(on=backend) from excluding openai
# silently.
backend_hooks_warnings_total: prometheus_client.Counter | None = None
backend_hooks_config_reloads_total: prometheus_client.Counter | None = None
backend_hooks_config_errors_total: prometheus_client.Counter | None = None
backend_hooks_enforcement_mode: prometheus_client.Gauge | None = None
backend_hooks_active_rules: prometheus_client.Gauge | None = None
backend_hooks_evaluations_total: prometheus_client.Counter | None = None
backend_tool_audit_entries_total: prometheus_client.Counter | None = None
# Per-entry byte histogram + rotation-pressure counter for tool-activity.jsonl (#1102).
backend_tool_audit_bytes_per_entry: prometheus_client.Histogram | None = None
backend_tool_audit_rotation_pressure_total: prometheus_client.Counter | None = None
# shared/session_binding fallback counter — #1103.
backend_session_binding_fallback_total: prometheus_client.Counter | None = None
# Outbound MCP tool HTTP metric family (#1104).
backend_mcp_outbound_requests_total: prometheus_client.Counter | None = None
backend_mcp_outbound_duration_seconds: prometheus_client.Histogram | None = None

# #1687: cross-backend parity placeholders. claude is the documented
# superset (AGENTS.md "Metrics landscape"); peers track placeholders
# for every claude metric so dashboards that union over
# (agent, agent_id, backend) don't lose label sets when a metric only
# emits on one backend. The placeholders below are declared but never
# .inc()/.observe()'d by openai's executor — the underlying mechanism
# (e.g. PreToolUse hook engine, MCP caller cardinality tracker, SQLite
# task-store lock instrumentation, settings.json reload watcher) lives
# only on claude.
backend_allowed_tools_reload_total: prometheus_client.Counter | None = None
backend_hooks_blocked_total: prometheus_client.Counter | None = None
backend_session_caller_cardinality: prometheus_client.Gauge | None = None
backend_sqlite_task_store_lock_wait_seconds: prometheus_client.Histogram | None = None

if _enabled:
    backend_up = prometheus_client.Gauge("backend_up", "Backend agent is running", ["agent", "agent_id", "backend"])
    backend_info = prometheus_client.Info("backend", "Static backend agent metadata.")
    backend_sdk_info = prometheus_client.Info(
        "backend_sdk",
        "Underlying SDK package + version (resolved via importlib.metadata). "
        "Lets dashboards catch SDK drift across backends without shelling "
        "in. See #1092.",
    )
    backend_uptime_seconds = prometheus_client.Gauge(
        "backend_uptime_seconds",
        "Backend agent uptime in seconds, computed on each Prometheus scrape.",
        ["agent", "agent_id", "backend"],
    )
    backend_startup_duration_seconds = prometheus_client.Gauge(
        "backend_startup_duration_seconds",
        "Time from process start to ready state in seconds.",
        ["agent", "agent_id", "backend"],
    )
    backend_event_loop_lag_seconds = prometheus_client.Histogram(
        "backend_event_loop_lag_seconds",
        "Excess delay beyond expected sleep duration, measuring asyncio event loop congestion.",
        ["agent", "agent_id", "backend"],
        buckets=(0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0),
    )
    backend_health_checks_total = prometheus_client.Counter(
        "backend_health_checks_total",
        "Total HTTP health endpoint hits by probe type.",
        ["agent", "agent_id", "backend", "probe"],
    )
    backend_task_restarts_total = prometheus_client.Counter(
        "backend_task_restarts_total",
        "Total worker restarts by the _guarded() loop after an unexpected exception.",
        ["agent", "agent_id", "backend", "task"],
    )

    # A2A
    backend_a2a_requests_total = prometheus_client.Counter(
        "backend_a2a_requests_total",
        "Total A2A HTTP requests by outcome.",
        ["agent", "agent_id", "backend", "status"],
    )
    backend_a2a_request_duration_seconds = prometheus_client.Histogram(
        "backend_a2a_request_duration_seconds",
        "Wall-clock duration of each A2A execute() call.",
        ["agent", "agent_id", "backend"],
        buckets=(0.1, 0.5, 1, 5, 10, 30, 60, 120, 300, 600),
    )
    backend_a2a_last_request_timestamp_seconds = prometheus_client.Gauge(
        "backend_a2a_last_request_timestamp_seconds",
        "Unix epoch of the most recent A2A request received.",
        ["agent", "agent_id", "backend"],
    )

    # Tasks
    backend_tasks_total = prometheus_client.Counter(
        "backend_tasks_total",
        "Total agent tasks processed by outcome.",
        ["agent", "agent_id", "backend", "status"],
    )
    backend_task_duration_seconds = prometheus_client.Histogram(
        "backend_task_duration_seconds",
        "Duration of agent tasks in seconds.",
        ["agent", "agent_id", "backend"],
        buckets=(0.1, 0.5, 1, 5, 10, 30, 60, 120, 300, 600),
    )
    backend_task_error_duration_seconds = prometheus_client.Histogram(
        "backend_task_error_duration_seconds",
        "Wall-clock seconds for tasks that end in error or timeout.",
        ["agent", "agent_id", "backend"],
        buckets=(0.1, 0.5, 1, 5, 10, 30, 60, 120, 300, 600),
    )
    backend_task_last_success_timestamp_seconds = prometheus_client.Gauge(
        "backend_task_last_success_timestamp_seconds",
        "Unix epoch of the most recent successful task execution.",
        ["agent", "agent_id", "backend"],
    )
    backend_task_last_error_timestamp_seconds = prometheus_client.Gauge(
        "backend_task_last_error_timestamp_seconds",
        "Unix epoch of the most recent failed task execution.",
        ["agent", "agent_id", "backend"],
    )
    backend_task_timeout_headroom_seconds = prometheus_client.Histogram(
        "backend_task_timeout_headroom_seconds",
        "Remaining timeout budget when a task completes successfully.",
        ["agent", "agent_id", "backend"],
        buckets=(0.1, 0.5, 1, 5, 10, 30, 60, 120, 300, 600),
    )
    backend_task_cancellations_total = prometheus_client.Counter(
        "backend_task_cancellations_total",
        "Total task cancellation requests.",
        ["agent", "agent_id", "backend"],
    )
    backend_running_tasks = prometheus_client.Gauge(
        "backend_running_tasks",
        "Number of currently in-progress tasks.",
        ["agent", "agent_id", "backend"],
    )
    backend_concurrent_queries = prometheus_client.Gauge(
        "backend_concurrent_queries",
        "Number of run() calls currently in flight.",
        ["agent", "agent_id", "backend"],
    )

    # Sessions
    backend_active_sessions = prometheus_client.Gauge(
        "backend_active_sessions",
        "Number of active sessions tracked in the LRU cache.",
        ["agent", "agent_id", "backend"],
    )
    backend_session_starts_total = prometheus_client.Counter(
        "backend_session_starts_total",
        "Total session starts by type.",
        ["agent", "agent_id", "backend", "type"],
    )
    backend_session_evictions_total = prometheus_client.Counter(
        "backend_session_evictions_total",
        "Total session evictions due to LRU cap.",
        ["agent", "agent_id", "backend"],
    )
    backend_session_age_seconds = prometheus_client.Histogram(
        "backend_session_age_seconds",
        "Seconds since last use when a session is evicted from the LRU cache.",
        ["agent", "agent_id", "backend"],
        buckets=(60, 300, 900, 1800, 3600, 7200, 14400, 28800, 86400),
    )
    backend_session_idle_seconds = prometheus_client.Histogram(
        "backend_session_idle_seconds",
        "Seconds a session was idle before being resumed.",
        ["agent", "agent_id", "backend"],
        buckets=(60, 300, 900, 1800, 3600, 7200, 14400, 28800, 86400),
    )
    backend_lru_cache_utilization_percent = prometheus_client.Gauge(
        "backend_lru_cache_utilization_percent",
        "LRU session cache utilization as a percentage of MAX_SESSIONS. "
        "Values range 0..100 (percent), not 0..1 (ratio) — the metric name "
        "uses the `_percent` suffix per the established convention (#1292). "
        "Divide by 100 in PromQL if a ratio is required.",
        ["agent", "agent_id", "backend"],
    )
    backend_session_history_save_errors_total = prometheus_client.Counter(
        "backend_session_history_save_errors_total",
        "Total failures to initialise or write the session SQLite store.",
        ["agent", "agent_id", "backend"],
    )
    # Session path layout drift (#530 / #796). Registered as a zero-value
    # placeholder so cross-backend dashboards filtering backend=~".*"
    # don't drop a label set. openai does not currently self-probe SDK
    # on-disk layout; a future self-test can bump this without touching
    # dashboard schemas.
    backend_session_path_mismatch_total = prometheus_client.Counter(
        "backend_session_path_mismatch_total",
        "Total startup self-test observations that the SDK on-disk layout "
        "has drifted from the conventions the backend assumes (#530).",
        ["agent", "agent_id", "backend", "reason"],
    )
    # SDK cold-start timing (#796). Registered as a zero-value placeholder
    # today — openai uses the OpenAI Agents SDK in-process so there is no
    # subprocess spawn; this exists so dashboards carry the series across
    # all three backends.
    backend_sdk_subprocess_spawn_duration_seconds = prometheus_client.Histogram(
        "backend_sdk_subprocess_spawn_duration_seconds",
        "Time to initialize the backend client/SDK (placeholder on openai).",
        ["agent", "agent_id", "backend", "model"],
        buckets=(0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60),
    )

    # Prompt / response
    backend_prompt_length_bytes = prometheus_client.Histogram(
        "backend_prompt_length_bytes",
        "Byte length of incoming prompts passed to run().",
        ["agent", "agent_id", "backend"],
        buckets=(100, 500, 1_000, 5_000, 10_000, 50_000, 100_000, 500_000, 1_000_000, 5_000_000),
    )
    backend_response_length_bytes = prometheus_client.Histogram(
        "backend_response_length_bytes",
        "Byte length of responses returned by run().",
        ["agent", "agent_id", "backend"],
        buckets=(100, 500, 1_000, 5_000, 10_000, 50_000, 100_000, 500_000, 1_000_000, 5_000_000),
    )
    backend_empty_responses_total = prometheus_client.Counter(
        "backend_empty_responses_total",
        "Total tasks that produced no text output.",
        ["agent", "agent_id", "backend"],
    )
    backend_empty_prompts_total = prometheus_client.Counter(
        "backend_empty_prompts_total",
        "Total execute() invocations rejected because the resolved prompt was empty or whitespace-only (#544 / #801).",
        ["agent", "agent_id", "backend"],
    )
    # #1620: oversized-prompt rejections. Cap is configured via
    # MAX_PROMPT_BYTES env (default 10 MiB). A 1 GB prompt would otherwise
    # OOM the pod before the SDK could produce a response.
    backend_prompt_too_large_total = prometheus_client.Counter(
        "backend_prompt_too_large_total",
        "Total execute() invocations rejected because the prompt exceeded MAX_PROMPT_BYTES (#1620).",
        ["agent", "agent_id", "backend"],
    )
    backend_final_output_divergence_total = prometheus_client.Counter(
        "backend_final_output_divergence_total",
        "Total turns where result.final_output differed from the streamed deltas (#1497).",
        ["agent", "agent_id", "backend"],
    )
    # Per-task SDK error/noise (#802). Parity with claude's subprocess-stderr
    # metric surface. OpenAI runs the OpenAI Agents SDK in-process so there is
    # no literal subprocess stderr; instead these metrics tally SDK-side
    # error/exception events observed during a single run() invocation so
    # operator dashboards can union across backends.
    backend_stderr_lines_per_task = prometheus_client.Histogram(
        "backend_stderr_lines_per_task",
        "Number of SDK-side error/noise events captured per run() invocation.",
        ["agent", "agent_id", "backend"],
        buckets=(0, 1, 2, 5, 10, 20, 50, 100),
    )
    backend_tasks_with_stderr_total = prometheus_client.Counter(
        "backend_tasks_with_stderr_total",
        "Total task executions that produced any SDK-side error/noise output.",
        ["agent", "agent_id", "backend"],
    )

    # Model routing
    backend_model_requests_total = prometheus_client.Counter(
        "backend_model_requests_total",
        "Total requests per resolved model.",
        ["agent", "agent_id", "backend", "model"],
    )

    # Logging
    backend_log_bytes_total = prometheus_client.Counter(
        "backend_log_bytes_total",
        "Total bytes written by the logging subsystem.",
        ["agent", "agent_id", "backend", "logger"],
    )
    backend_log_entries_total = prometheus_client.Counter(
        "backend_log_entries_total",
        "Total log entries written by logger type.",
        ["agent", "agent_id", "backend", "logger"],
    )
    backend_log_write_errors_total = prometheus_client.Counter(
        "backend_log_write_errors_total",
        "Total I/O failures in the conversation/trace logging subsystem.",
        ["agent", "agent_id", "backend"],
    )
    # Per-logger write errors (#626 / #804). Non-breaking complement to
    # backend_log_write_errors_total — carries the `logger` label so operators
    # can distinguish tool-audit vs conversation vs trace write failures.
    backend_log_write_errors_by_logger_total = prometheus_client.Counter(
        "backend_log_write_errors_by_logger_total",
        "Total log write errors attributed to a specific logger.",
        ["agent", "agent_id", "backend", "logger"],
    )

    # SDK
    backend_sdk_query_duration_seconds = prometheus_client.Histogram(
        "backend_sdk_query_duration_seconds",
        "Raw backend query time in seconds inside run_query().",
        ["agent", "agent_id", "backend", "model"],
        buckets=(0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60),
    )
    backend_sdk_query_error_duration_seconds = prometheus_client.Histogram(
        "backend_sdk_query_error_duration_seconds",
        "Wall-clock seconds for run_query() calls that end in error.",
        ["agent", "agent_id", "backend", "model"],
        buckets=(0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60),
    )
    backend_sdk_time_to_first_message_seconds = prometheus_client.Histogram(
        "backend_sdk_time_to_first_message_seconds",
        "Seconds from query submission to the first response message.",
        ["agent", "agent_id", "backend", "model"],
        buckets=(0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60),
    )
    backend_sdk_session_duration_seconds = prometheus_client.Histogram(
        "backend_sdk_session_duration_seconds",
        "Backend session/connection lifetime in seconds.",
        ["agent", "agent_id", "backend", "model"],
        buckets=(0.1, 0.5, 1, 5, 10, 30, 60, 120, 300, 600),
    )
    backend_sdk_messages_per_query = prometheus_client.Histogram(
        "backend_sdk_messages_per_query",
        "Number of backend messages received per run_query() call.",
        ["agent", "agent_id", "backend", "model"],
        buckets=(1, 2, 5, 10, 20, 50, 100, 200),
    )
    backend_sdk_turns_per_query = prometheus_client.Histogram(
        "backend_sdk_turns_per_query",
        "Number of assistant turns per run_query() invocation.",
        ["agent", "agent_id", "backend", "model"],
        buckets=(1, 2, 3, 5, 10, 20, 50, 100),
    )
    backend_text_blocks_per_query = prometheus_client.Histogram(
        "backend_text_blocks_per_query",
        "Number of text blocks returned per run_query() invocation.",
        ["agent", "agent_id", "backend", "model"],
        buckets=(0, 1, 2, 5, 10, 20, 50, 100),
    )
    backend_sdk_tokens_per_query = prometheus_client.Histogram(
        "backend_sdk_tokens_per_query",
        "Total tokens consumed per run_query() invocation (parity with claude — #459).",
        ["agent", "agent_id", "backend", "model"],
        buckets=(100, 500, 1_000, 5_000, 10_000, 25_000, 50_000, 100_000, 200_000, 500_000),
    )
    backend_streaming_events_emitted_total = prometheus_client.Counter(
        "backend_streaming_events_emitted_total",
        "Total partial agent_text_message events enqueued during streaming. "
        "Equals the number of text deltas the executor pushed to the A2A "
        "event_queue mid-stream (#430).",
        ["agent", "agent_id", "backend", "model"],
    )
    # Streaming chunks dropped due to the on_chunk consumer exceeding
    # STREAM_CHUNK_TIMEOUT_SECONDS (#724). Label schema mirrors
    # streaming_events_emitted so dashboards can union the two series.
    backend_streaming_chunks_dropped_total = prometheus_client.Counter(
        "backend_streaming_chunks_dropped_total",
        "Total streaming chunks dropped because the A2A consumer's on_chunk "
        "callback exceeded STREAM_CHUNK_TIMEOUT_SECONDS. The final-flush "
        "aggregated text still fires at response completion so clients see "
        "the complete output (#724).",
        ["agent", "agent_id", "backend", "model"],
    )

    # MCP config (parity with claude — #432)
    backend_mcp_config_errors_total = prometheus_client.Counter(
        "backend_mcp_config_errors_total",
        "Total errors loading the MCP config file (mcp.json). Counts both "
        "missing-file silently-ignored cases (no increment) and parse / "
        "I/O failures (incremented).",
        ["agent", "agent_id", "backend"],
    )
    backend_mcp_config_reloads_total = prometheus_client.Counter(
        "backend_mcp_config_reloads_total",
        "Total successful reloads of mcp.json triggered by the file watcher.",
        ["agent", "agent_id", "backend"],
    )
    backend_mcp_servers_active = prometheus_client.Gauge(
        "backend_mcp_servers_active",
        "Number of MCP servers currently loaded from mcp.json (gauge).",
        ["agent", "agent_id", "backend"],
    )
    # Command allow-list rejections (#720 — parity with claude #711).
    backend_mcp_command_rejected_total = prometheus_client.Counter(
        "backend_mcp_command_rejected_total",
        "Total MCP server entries rejected by the command allow-list, by reason.",
        ["agent", "agent_id", "backend", "reason"],
    )
    # /mcp transport observability (#962 — parity with claude's peer pair).
    # Same label schema as claude so dashboards union without rewriting labels.
    backend_mcp_requests_total = prometheus_client.Counter(
        "backend_mcp_requests_total",
        "Total MCP JSON-RPC requests received on the /mcp endpoint by method and outcome.",
        ["agent", "agent_id", "backend", "method", "status"],
    )
    backend_mcp_request_duration_seconds = prometheus_client.Histogram(
        "backend_mcp_request_duration_seconds",
        "Wall-clock duration of each MCP JSON-RPC request handled on /mcp.",
        ["agent", "agent_id", "backend", "method"],
    )

    # SDK error classification (parity with claude — #431)
    backend_sdk_errors_total = prometheus_client.Counter(
        "backend_sdk_errors_total",
        "Total stderr/error lines emitted by the backend subprocess.",
        ["agent", "agent_id", "backend", "model"],
    )
    backend_sdk_result_errors_total = prometheus_client.Counter(
        "backend_sdk_result_errors_total",
        "Total backend result errors returned during run_query().",
        ["agent", "agent_id", "backend", "model"],
    )
    backend_sdk_client_errors_total = prometheus_client.Counter(
        "backend_sdk_client_errors_total",
        "Total backend client connection-level failures (setup/teardown).",
        ["agent", "agent_id", "backend", "model"],
    )
    # Context-usage fetch failures (#803). Parity with claude. OpenAI currently
    # reads token totals from the Agents SDK result object, so this counter
    # bumps whenever that extraction raises or returns a malformed payload.
    backend_sdk_context_fetch_errors_total = prometheus_client.Counter(
        "backend_sdk_context_fetch_errors_total",
        "Total context usage fetch failures.",
        ["agent", "agent_id", "backend", "model"],
    )
    # Task retries (#803). Parity with claude's retry-on-session-collision
    # path. OpenAI does not currently retry internally, so this counter ships
    # as a zero-value placeholder so dashboards can union across backends
    # without missing-label gaps; any future retry path can bump it.
    backend_task_retries_total = prometheus_client.Counter(
        "backend_task_retries_total",
        "Total task retries due to session already in use.",
        ["agent", "agent_id", "backend"],
    )

    # File watchers
    backend_watcher_events_total = prometheus_client.Counter(
        "backend_watcher_events_total",
        "Total file watcher change events observed by backend watchers.",
        ["agent", "agent_id", "backend", "watcher"],
    )
    backend_file_watcher_restarts_total = prometheus_client.Counter(
        "backend_file_watcher_restarts_total",
        "Total file watcher restarts after watcher exits unexpectedly.",
        ["agent", "agent_id", "backend", "watcher"],
    )

    # AGENTS.md revision rollout gauge (#1097). The `revision` label is the
    # SHA-256 hex prefix (first 12 chars) of the active AGENTS.md content;
    # value is always 1 when set. On hot-reload the old revision's label set
    # is cleared via .remove(...) before the new one is stamped so only the
    # live revision reports 1.
    backend_agent_md_revision = prometheus_client.Gauge(
        "backend_agent_md_revision",
        "Currently-active AGENTS.md revision (SHA-256 hex prefix, first 12 chars). "
        "Gauge value is 1 when set; previous revision is cleared on hot-reload. "
        "Pair with the `openai.agent_md_revision` per-query span attribute to "
        "verify a behavioral rollout has propagated. See #1097.",
        ["agent", "agent_id", "backend", "revision"],
    )

    # Context window
    backend_context_tokens = prometheus_client.Histogram(
        "backend_context_tokens",
        "Token count used per query (from SDK usage response).",
        ["agent", "agent_id", "backend"],
        buckets=(100, 500, 1_000, 5_000, 10_000, 25_000, 50_000, 100_000, 200_000, 500_000),
    )
    backend_context_tokens_remaining = prometheus_client.Histogram(
        "backend_context_tokens_remaining",
        "Remaining token budget (max_tokens - used) per query.",
        ["agent", "agent_id", "backend"],
        buckets=(1000, 5000, 10000, 25000, 50000, 100000, 150000),
    )
    backend_context_usage_percent = prometheus_client.Histogram(
        "backend_context_usage_percent",
        "Context window utilization percentage per query. "
        "Values range 0..100 (percent), not 0..1 (ratio) — the metric name "
        "uses the `_percent` suffix per the established convention (#1292); "
        "the histogram buckets are chosen on that 0..100 scale.",
        ["agent", "agent_id", "backend"],
        buckets=(50, 70, 80, 90, 95, 99, 100),
    )
    backend_context_exhaustion_total = prometheus_client.Counter(
        "backend_context_exhaustion_total",
        "Total context window exhaustion events (usage >= 100%).",
        ["agent", "agent_id", "backend"],
    )
    backend_context_warnings_total = prometheus_client.Counter(
        "backend_context_warnings_total",
        "Total context usage threshold warnings (usage >= 80%).",
        ["agent", "agent_id", "backend"],
    )

    # Tool calls
    backend_sdk_tool_calls_total = prometheus_client.Counter(
        "backend_sdk_tool_calls_total",
        "Total tool calls by tool name.",
        ["agent", "agent_id", "backend", "tool"],
    )
    backend_sdk_tool_calls_per_query = prometheus_client.Histogram(
        "backend_sdk_tool_calls_per_query",
        "Number of tool calls per run_query() invocation.",
        # model label aligned with claude (#795) so cross-backend queries
        # don't lose the per-model dimension on openai.
        ["agent", "agent_id", "backend", "model"],
        buckets=(0, 1, 2, 5, 10, 20, 50),
    )
    backend_sdk_tool_duration_seconds = prometheus_client.Histogram(
        "backend_sdk_tool_duration_seconds",
        "Duration of individual tool calls in seconds.",
        ["agent", "agent_id", "backend", "tool"],
        buckets=(0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60),
    )
    backend_sdk_tool_errors_total = prometheus_client.Counter(
        "backend_sdk_tool_errors_total",
        "Total tool call errors by tool name.",
        ["agent", "agent_id", "backend", "tool"],
    )
    backend_sdk_tool_call_input_size_bytes = prometheus_client.Histogram(
        "backend_sdk_tool_call_input_size_bytes",
        "Byte size of tool call input arguments.",
        ["agent", "agent_id", "backend", "tool"],
        buckets=(100, 500, 1_000, 5_000, 10_000, 50_000, 100_000, 500_000, 1_000_000, 5_000_000),
    )
    backend_sdk_tool_result_size_bytes = prometheus_client.Histogram(
        "backend_sdk_tool_result_size_bytes",
        "Byte size of tool call result output.",
        ["agent", "agent_id", "backend", "tool"],
        buckets=(100, 500, 1_000, 5_000, 10_000, 50_000, 100_000, 500_000, 1_000_000, 5_000_000),
    )

    # Token budget
    backend_budget_exceeded_total = prometheus_client.Counter(
        "backend_budget_exceeded_total",
        "Total token budget exceeded events (max_tokens limit hit during execution).",
        ["agent", "agent_id", "backend"],
    )

    # Hooks / tool-audit (#586) — shell-only PreToolUse deny baseline.
    # Narrowly scoped: the `rule` label enumerates the shell baseline rule
    # names that mirror claude's baseline (baseline-rm-rf-root,
    # baseline-git-force-push-main, baseline-curl-pipe-shell,
    # baseline-chmod-777, baseline-dd-device). Non-shell enforcement is not
    # covered by this counter yet — see #586 for the deferred design.
    if _EMIT_DEPRECATED_HOOK_METRICS:
        backend_openai_hooks_denials_total = prometheus_client.Counter(
            "backend_openai_hooks_denials_total",
            "DEPRECATED alias for backend_hooks_denials_total (#789, #940). "
            "Gated on EMIT_DEPRECATED_HOOK_METRICS (default true; flip off "
            "after migrating dashboards). Label cardinality (agent, "
            "agent_id, backend, rule) intentionally narrower than the "
            "canonical series — non-shell enforcement will not backfill.",
            ["agent", "agent_id", "backend", "rule"],
        )
        backend_codex_hooks_denials_total = prometheus_client.Counter(
            "backend_codex_hooks_denials_total",
            "DEPRECATED pre-rename alias for backend_hooks_denials_total "
            "(#789, #940). Gated on EMIT_DEPRECATED_HOOK_METRICS (default "
            "true; flip off after migrating dashboards). Label cardinality "
            "(agent, agent_id, backend, rule) intentionally narrower than "
            "the canonical series — non-shell enforcement will not backfill.",
            ["agent", "agent_id", "backend", "rule"],
        )
    # Canonical cross-backend denial counter (#789). Label schema matches
    # claude's (tool, source, rule); openai's shell baseline always fills
    # tool='shell' and source='baseline' since non-shell enforcement is
    # still deferred (#586).
    backend_hooks_denials_total = prometheus_client.Counter(
        "backend_hooks_denials_total",
        "Total tool calls denied by a PreToolUse hook, labelled by tool name, "
        "rule source (baseline|extension), and the rule name that matched. "
        "Canonical name across claude/openai/gemini backends.",
        ["agent", "agent_id", "backend", "tool", "source", "rule"],
    )
    # Parity with claude.backend_hooks_shed_total (#957). openai shares
    # shared/hook_events.schedule_post with the other backends; without
    # registering + passing a shed_counter the module's one-shot WARN
    # fires once and goes silent, so sustained shedding is invisible on
    # dashboards. Labels match claude's (agent, agent_id, backend).
    backend_hooks_shed_total = prometheus_client.Counter(
        "backend_hooks_shed_total",
        "Total hook.decision POSTs shed because the bounded in-flight "
        "cap (HOOK_POST_MAX_INFLIGHT, default 32) was reached (#712, #957). "
        "Non-zero rate indicates the harness is unreachable or slow while "
        "shell baseline denials fire; the backend would otherwise OOM.",
        ["agent", "agent_id", "backend"],
    )
    # #1052: track emissions that fell back to empty session_id.
    backend_hook_session_missing_total = prometheus_client.Counter(
        "backend_hook_session_missing_total",
        "Total hook.decision emissions where _current_session_id was empty; "
        "indicates a baseline check fired outside a task-dispatch path. See #1052.",
        ["agent", "agent_id", "backend", "tool", "source"],
    )
    backend_tool_audit_entries_total = prometheus_client.Counter(
        "backend_tool_audit_entries_total",
        "Total rows written to tool-audit.jsonl by openai PostToolUse audit.",
        ["agent", "agent_id", "backend", "tool"],
    )
    # Size / rotation observability on tool-activity.jsonl (#1102).
    backend_tool_audit_bytes_per_entry = prometheus_client.Histogram(
        "backend_tool_audit_bytes_per_entry",
        "Per-row byte size of tool-activity.jsonl entries. See #1102.",
        ["agent", "agent_id", "backend", "tool"],
        buckets=(64, 256, 1024, 4096, 16384, 65536, 262144, 1048576, 4194304),
    )
    backend_tool_audit_rotation_pressure_total = prometheus_client.Counter(
        "backend_tool_audit_rotation_pressure_total",
        "Total opportunistic checks that found tool-activity.jsonl above "
        "TOOL_ACTIVITY_ROTATION_PRESSURE_BYTES. See #1102.",
        ["agent", "agent_id", "backend", "reason"],
    )
    # #1103: shared/session_binding fallback path counter.
    backend_session_binding_fallback_total = prometheus_client.Counter(
        "backend_session_binding_fallback_total",
        "Total derive_session_id() calls that fell back to legacy uuid5 "
        "derivation instead of the HMAC-bound per-caller binding. See #1103.",
        ["agent", "agent_id", "backend", "reason"],
    )
    # #1104: outbound MCP tool request metric family.
    backend_mcp_outbound_requests_total = prometheus_client.Counter(
        "backend_mcp_outbound_requests_total",
        "Total outbound MCP tool invocations issued by this backend as an "
        "MCP client. Separate from backend_sdk_tool_calls_total. See #1104.",
        ["agent", "agent_id", "backend", "server", "tool", "outcome"],
    )
    backend_mcp_outbound_duration_seconds = prometheus_client.Histogram(
        "backend_mcp_outbound_duration_seconds",
        "Wall-clock duration of an outbound MCP tool call. See #1104.",
        ["agent", "agent_id", "backend", "server", "tool", "outcome"],
        buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0),
    )
    # Peer-parity hook surface (#796): claude's hook surface exposes
    # backend_hooks_active_rules and backend_hooks_evaluations_total.
    # openai reports its active shell-baseline count, while broader
    # non-shell evaluation remains deferred until the Agents SDK exposes
    # a universal pre-tool interposition point.
    backend_hooks_active_rules = prometheus_client.Gauge(
        "backend_hooks_active_rules",
        "Number of currently active hook rules, by rule source.",
        ["agent", "agent_id", "backend", "source"],
    )
    # Peer-parity placeholders (#800). Same label schema as claude so
    # cross-backend dashboards can union by (agent, agent_id, backend).
    backend_hooks_warnings_total = prometheus_client.Counter(
        "backend_hooks_warnings_total",
        "Total tool calls flagged (but not denied) by a PreToolUse hook.",
        ["agent", "agent_id", "backend", "tool", "source", "rule"],
    )
    backend_hooks_config_reloads_total = prometheus_client.Counter(
        "backend_hooks_config_reloads_total",
        "Total reloads of hooks.yaml by the hooks config watcher.",
        ["agent", "agent_id", "backend"],
    )
    backend_hooks_config_errors_total = prometheus_client.Counter(
        "backend_hooks_config_errors_total",
        "Total hooks.yaml parse/reload/validation errors by reason.",
        ["agent", "agent_id", "backend", "reason"],
    )
    backend_hooks_enforcement_mode = prometheus_client.Gauge(
        "backend_hooks_enforcement_mode",
        "PreToolUse hook enforcement mode. 0=partial/skeleton, 1=enforcing, -1=disabled. "
        "OpenAI reports 0 while enforcement is shell-baseline-only.",
        ["agent", "agent_id", "backend"],
    )
    backend_hooks_evaluations_total = prometheus_client.Counter(
        "backend_hooks_evaluations_total",
        "Total PreToolUse hook evaluations, grouped by final decision.",
        ["agent", "agent_id", "backend", "tool", "decision"],
    )

    # #1687: cross-backend parity placeholders. Declared so cross-backend
    # PromQL joins don't drop label sets where claude emits and openai
    # doesn't. None of these mechanisms exist on openai today; the
    # placeholders sit at zero unless/until parity work fills them in.
    backend_allowed_tools_reload_total = prometheus_client.Counter(
        "backend_allowed_tools_reload_total",
        "Total settings.json ALLOWED_TOOLS reloads observed (claude-only "
        "mechanism — placeholder on openai for cross-backend join parity).",
        ["agent", "agent_id", "backend", "direction"],
    )
    backend_hooks_blocked_total = prometheus_client.Counter(
        "backend_hooks_blocked_total",
        "DEPRECATED alias for backend_hooks_denials_total (#789). Placeholder "
        "on openai so dashboards pinned to the claude-specific name still join.",
        ["agent", "agent_id", "backend", "tool", "source", "rule"],
    )
    backend_session_caller_cardinality = prometheus_client.Gauge(
        "backend_session_caller_cardinality",
        "Distinct caller_identity values observed on /mcp since process start "
        "(claude-only tracker — placeholder on openai for parity, see #1049).",
        ["agent", "agent_id", "backend"],
    )
    backend_sqlite_task_store_lock_wait_seconds = prometheus_client.Histogram(
        "backend_sqlite_task_store_lock_wait_seconds",
        "Seconds waited to acquire the SqliteTaskStore asyncio.Lock, by op "
        "(claude-only instrumentation — placeholder on openai for parity).",
        ["agent", "agent_id", "backend", "op"],
        buckets=(0.0001, 0.0005, 0.001, 0.005, 0.01, 0.05, 0.1, 0.5, 1.0, 5.0),
    )
