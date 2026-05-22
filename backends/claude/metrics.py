"""Prometheus metrics for the claude backend agent."""

import prometheus_client
from env import parse_bool_env

_enabled = parse_bool_env("METRICS_ENABLED")

# Service-level metrics
backend_up: prometheus_client.Gauge | None = None
backend_info: prometheus_client.Info | None = None
# Underlying SDK version info (#1092). AGENT_VERSION tracks container
# version, not the claude-agent-sdk / openai-agents / google-genai pin
# — a bad SDK upgrade would otherwise require shelling in to diagnose.
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
# Cardinality of distinct caller_identity values observed on /mcp (#1049).
# When CONVERSATIONS_AUTH_TOKEN is deployed as a single backend-wide token,
# every caller hashes to the same bucket and this gauge sits at 1 — the
# session-hijack protection in derive_session_id degrades to a no-op.
# Operators can alert on "gauge == 1 with non-trivial /mcp traffic" to
# flag deployments that need per-caller tokens.
backend_session_caller_cardinality: prometheus_client.Gauge | None = None
backend_session_starts_total: prometheus_client.Counter | None = None
backend_session_evictions_total: prometheus_client.Counter | None = None
backend_session_age_seconds: prometheus_client.Histogram | None = None
backend_session_idle_seconds: prometheus_client.Histogram | None = None
backend_lru_cache_utilization_percent: prometheus_client.Gauge | None = None

# Prompt / response size metrics
backend_prompt_length_bytes: prometheus_client.Histogram | None = None
backend_response_length_bytes: prometheus_client.Histogram | None = None
backend_empty_responses_total: prometheus_client.Counter | None = None
backend_empty_prompts_total: prometheus_client.Counter | None = None

# Model / backend routing metrics
backend_model_requests_total: prometheus_client.Counter | None = None

# Logging subsystem metrics
backend_log_bytes_total: prometheus_client.Counter | None = None
backend_log_entries_total: prometheus_client.Counter | None = None
backend_log_write_errors_total: prometheus_client.Counter | None = None

# Session history persistence metrics
backend_session_history_save_errors_total: prometheus_client.Counter | None = None

# Session path layout drift metrics (#530)
backend_session_path_mismatch_total: prometheus_client.Counter | None = None

# Claude SDK / subprocess metrics
backend_sdk_subprocess_spawn_duration_seconds: prometheus_client.Histogram | None = None
backend_sdk_query_duration_seconds: prometheus_client.Histogram | None = None
backend_sdk_query_error_duration_seconds: prometheus_client.Histogram | None = None
backend_sdk_time_to_first_message_seconds: prometheus_client.Histogram | None = None
backend_sdk_session_duration_seconds: prometheus_client.Histogram | None = None
backend_sdk_messages_per_query: prometheus_client.Histogram | None = None
backend_sdk_turns_per_query: prometheus_client.Histogram | None = None
backend_sdk_tokens_per_query: prometheus_client.Histogram | None = None
backend_sdk_errors_total: prometheus_client.Counter | None = None
backend_sdk_result_errors_total: prometheus_client.Counter | None = None
backend_sdk_client_errors_total: prometheus_client.Counter | None = None
backend_sdk_context_fetch_errors_total: prometheus_client.Counter | None = None

# Tool call metrics
backend_sdk_tool_calls_total: prometheus_client.Counter | None = None
backend_sdk_tool_calls_per_query: prometheus_client.Histogram | None = None
backend_sdk_tool_duration_seconds: prometheus_client.Histogram | None = None
backend_sdk_tool_errors_total: prometheus_client.Counter | None = None
backend_sdk_tool_call_input_size_bytes: prometheus_client.Histogram | None = None
backend_sdk_tool_result_size_bytes: prometheus_client.Histogram | None = None
backend_text_blocks_per_query: prometheus_client.Histogram | None = None
backend_streaming_events_emitted_total: prometheus_client.Counter | None = None
# Streaming chunks dropped due to on_chunk consumer exceeding
# STREAM_CHUNK_TIMEOUT_SECONDS — parity with codex #724, added #1091.
backend_streaming_chunks_dropped_total: prometheus_client.Counter | None = None
backend_stderr_lines_per_task: prometheus_client.Histogram | None = None
backend_tasks_with_stderr_total: prometheus_client.Counter | None = None
backend_task_retries_total: prometheus_client.Counter | None = None

# Context window metrics
backend_context_tokens: prometheus_client.Histogram | None = None
backend_context_tokens_remaining: prometheus_client.Histogram | None = None
backend_context_usage_percent: prometheus_client.Histogram | None = None
backend_context_exhaustion_total: prometheus_client.Counter | None = None
backend_context_warnings_total: prometheus_client.Counter | None = None

# Token budget metrics
backend_budget_exceeded_total: prometheus_client.Counter | None = None

# MCP metrics
backend_mcp_config_errors_total: prometheus_client.Counter | None = None
backend_mcp_command_rejected_total: prometheus_client.Counter | None = None
backend_mcp_config_reloads_total: prometheus_client.Counter | None = None
backend_mcp_servers_active: prometheus_client.Gauge | None = None
# Per-request metrics for the /mcp JSON-RPC endpoint (#790). Peer parity
# with gemini so operators can alert on /mcp request rate and p95 latency
# on every backend without special-casing.
backend_mcp_requests_total: prometheus_client.Counter | None = None
backend_mcp_request_duration_seconds: prometheus_client.Histogram | None = None

# File watcher metrics
backend_watcher_events_total: prometheus_client.Counter | None = None
backend_file_watcher_restarts_total: prometheus_client.Counter | None = None

# Hooks / tool-audit metrics (#467)
backend_hooks_blocked_total: prometheus_client.Counter | None = None
# Canonical cross-backend name for the same count (#789). Declared
# alongside backend_hooks_blocked_total so existing claude dashboards
# keep working while operators migrate to the backend-agnostic series.
backend_hooks_denials_total: prometheus_client.Counter | None = None
backend_hooks_warnings_total: prometheus_client.Counter | None = None
backend_tool_audit_entries_total: prometheus_client.Counter | None = None
# Per-entry byte histogram + rotation-pressure counter for tool-activity.jsonl (#1102).
backend_tool_audit_bytes_per_entry: prometheus_client.Histogram | None = None
backend_tool_audit_rotation_pressure_total: prometheus_client.Counter | None = None
# shared/session_binding fallback counter — #1103.
backend_session_binding_fallback_total: prometheus_client.Counter | None = None
# Outbound MCP tool HTTP metric family (#1104). Distinct from
# backend_sdk_tool_calls_total (which lumps SDK-internal tools with
# mcp__* tools) and from the MCP-tool-server-side family in
# shared/mcp_metrics.py (which observes work on the tool server, not
# the caller). Labelled by {server, tool, outcome}.
backend_mcp_outbound_requests_total: prometheus_client.Counter | None = None
backend_mcp_outbound_duration_seconds: prometheus_client.Histogram | None = None
backend_hooks_config_reloads_total: prometheus_client.Counter | None = None
backend_hooks_active_rules: prometheus_client.Gauge | None = None
backend_hooks_evaluations_total: prometheus_client.Counter | None = None
backend_hooks_shed_total: prometheus_client.Counter | None = None
backend_allowed_tools_reload_total: prometheus_client.Counter | None = None
backend_hooks_config_errors_total: prometheus_client.Counter | None = None

# Per-logger log write errors (#626). Complementary to backend_log_write_errors_total
# (kept unchanged for backward compatibility with existing alerts that aggregate
# without a `logger` label).
backend_log_write_errors_by_logger_total: prometheus_client.Counter | None = None

# SqliteTaskStore lock-wait histogram (#552 / #791). Mirrors gemini's metric so
# dashboards can union across backends on (agent, agent_id, backend, op).
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
    backend_session_caller_cardinality = prometheus_client.Gauge(
        "backend_session_caller_cardinality",
        "Distinct caller_identity values observed on /mcp since process start. "
        "Dependency signal for SESSION_ID_SECRET: when CONVERSATIONS_AUTH_TOKEN "
        "is backend-wide every caller collapses to a single bucket and this "
        "gauge sits at 1 — derive_session_id's caller-bound protection is "
        "nominal. Operators should alert on prolonged 1 with non-trivial /mcp "
        "traffic and migrate to per-caller tokens. See #1049.",
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
        "Total execute() invocations rejected because the resolved prompt was empty or whitespace-only (#544).",
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

    # Session history persistence
    backend_session_history_save_errors_total = prometheus_client.Counter(
        "backend_session_history_save_errors_total",
        "Total failures when detecting or accessing session files on disk.",
        ["agent", "agent_id", "backend"],
    )

    # Session path layout drift (#530)
    backend_session_path_mismatch_total = prometheus_client.Counter(
        "backend_session_path_mismatch_total",
        "Total startup self-test observations that the Claude Agent SDK "
        "on-disk layout has drifted from the conventions the backend assumes "
        "in _session_file_path. Labelled by probe outcome. A non-zero counter "
        "means _session_file_path may resolve to the wrong path — eviction "
        "and timeout unlinks will no-op and disk usage can grow silently.",
        ["agent", "agent_id", "backend", "reason"],
    )

    # SDK / subprocess
    backend_sdk_subprocess_spawn_duration_seconds = prometheus_client.Histogram(
        "backend_sdk_subprocess_spawn_duration_seconds",
        "Time to initialize the backend client/subprocess.",
        ["agent", "agent_id", "backend", "model"],
        buckets=(0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60),
    )
    backend_sdk_query_duration_seconds = prometheus_client.Histogram(
        "backend_sdk_query_duration_seconds",
        "Raw backend query time in seconds inside run_query().",
        ["agent", "agent_id", "backend", "model"],
        buckets=(0.1, 0.5, 1, 5, 10, 30, 60, 120, 300, 600),
    )
    backend_sdk_query_error_duration_seconds = prometheus_client.Histogram(
        "backend_sdk_query_error_duration_seconds",
        "Wall-clock seconds for run_query() calls that end in error.",
        ["agent", "agent_id", "backend", "model"],
        buckets=(0.1, 0.5, 1, 5, 10, 30, 60, 120, 300, 600),
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
    backend_sdk_tokens_per_query = prometheus_client.Histogram(
        "backend_sdk_tokens_per_query",
        "Aggregate token count per run_query() invocation.",
        ["agent", "agent_id", "backend", "model"],
        buckets=(100, 500, 1_000, 5_000, 10_000, 25_000, 50_000, 100_000, 200_000, 500_000),
    )
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
    backend_sdk_context_fetch_errors_total = prometheus_client.Counter(
        "backend_sdk_context_fetch_errors_total",
        "Total context usage fetch failures.",
        ["agent", "agent_id", "backend", "model"],
    )

    # Tools
    backend_sdk_tool_calls_total = prometheus_client.Counter(
        "backend_sdk_tool_calls_total",
        "Total tool calls by tool name.",
        ["agent", "agent_id", "backend", "tool"],
    )
    backend_sdk_tool_calls_per_query = prometheus_client.Histogram(
        "backend_sdk_tool_calls_per_query",
        "Number of tool calls per run_query() invocation.",
        ["agent", "agent_id", "backend", "model"],
        buckets=(0, 1, 2, 5, 10, 20, 50, 100, 200),
    )
    backend_sdk_tool_duration_seconds = prometheus_client.Histogram(
        "backend_sdk_tool_duration_seconds",
        "Wall-clock seconds per tool call.",
        ["agent", "agent_id", "backend", "tool"],
        buckets=(0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60),
    )
    backend_sdk_tool_errors_total = prometheus_client.Counter(
        "backend_sdk_tool_errors_total",
        "Total tool execution errors by tool name.",
        ["agent", "agent_id", "backend", "tool"],
    )
    backend_sdk_tool_call_input_size_bytes = prometheus_client.Histogram(
        "backend_sdk_tool_call_input_size_bytes",
        "Byte length of each tool call input payload by tool name.",
        ["agent", "agent_id", "backend", "tool"],
        buckets=(100, 500, 1_000, 5_000, 10_000, 50_000, 100_000, 500_000, 1_000_000, 5_000_000),
    )
    backend_sdk_tool_result_size_bytes = prometheus_client.Histogram(
        "backend_sdk_tool_result_size_bytes",
        "Byte length of each tool result by tool name.",
        ["agent", "agent_id", "backend", "tool"],
        buckets=(100, 500, 1_000, 5_000, 10_000, 50_000, 100_000, 500_000, 1_000_000, 5_000_000),
    )
    backend_text_blocks_per_query = prometheus_client.Histogram(
        "backend_text_blocks_per_query",
        "Number of text blocks returned per run_query() invocation.",
        ["agent", "agent_id", "backend", "model"],
        buckets=(0, 1, 2, 5, 10, 20, 50, 100),
    )
    backend_streaming_events_emitted_total = prometheus_client.Counter(
        "backend_streaming_events_emitted_total",
        "Total partial agent_text_message events enqueued during streaming. "
        "Equals the number of TextBlocks/chunks the executor pushed to the "
        "A2A event_queue mid-stream — see #430.",
        ["agent", "agent_id", "backend", "model"],
    )
    # Back-pressure signal — parity with codex (#724/#1091). A non-zero
    # rate here means the SSE consumer (A2A event_queue) stalled and the
    # executor dropped chunks. The final aggregated response still fires
    # at completion so clients see the full output, but intermediate
    # streaming is lossy while the drops are elevated.
    backend_streaming_chunks_dropped_total = prometheus_client.Counter(
        "backend_streaming_chunks_dropped_total",
        "Total streaming chunks dropped because the A2A consumer's on_chunk "
        "callback exceeded STREAM_CHUNK_TIMEOUT_SECONDS. The final-flush "
        "aggregated text still fires at response completion so clients see "
        "the complete output — see #724/#1091.",
        ["agent", "agent_id", "backend", "model"],
    )
    backend_stderr_lines_per_task = prometheus_client.Histogram(
        "backend_stderr_lines_per_task",
        "Number of SDK stderr lines captured per run() invocation.",
        ["agent", "agent_id", "backend"],
        buckets=(0, 1, 2, 5, 10, 20, 50, 100),
    )
    backend_tasks_with_stderr_total = prometheus_client.Counter(
        "backend_tasks_with_stderr_total",
        "Total task executions that produced any SDK stderr output.",
        ["agent", "agent_id", "backend"],
    )
    backend_task_retries_total = prometheus_client.Counter(
        "backend_task_retries_total",
        "Total task retries due to session already in use.",
        ["agent", "agent_id", "backend"],
    )

    # Context window
    backend_context_tokens = prometheus_client.Histogram(
        "backend_context_tokens",
        "Absolute token count from get_context_usage() per SDK turn.",
        ["agent", "agent_id", "backend"],
        buckets=(100, 500, 1_000, 5_000, 10_000, 25_000, 50_000, 100_000, 200_000, 500_000),
    )
    backend_context_tokens_remaining = prometheus_client.Histogram(
        "backend_context_tokens_remaining",
        "Remaining token budget (maxTokens - totalTokens) per get_context_usage() call.",
        ["agent", "agent_id", "backend"],
        buckets=(1000, 5000, 10000, 25000, 50000, 100000, 150000),
    )
    backend_context_usage_percent = prometheus_client.Histogram(
        "backend_context_usage_percent",
        "Context window utilization percentage per SDK turn. "
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
        "Total context usage threshold warnings.",
        ["agent", "agent_id", "backend"],
    )

    # Token budget
    backend_budget_exceeded_total = prometheus_client.Counter(
        "backend_budget_exceeded_total",
        "Total token budget exceeded events (max_tokens limit hit during execution).",
        ["agent", "agent_id", "backend"],
    )

    # MCP
    backend_mcp_config_errors_total = prometheus_client.Counter(
        "backend_mcp_config_errors_total",
        "Total MCP config file parse/load failures.",
        ["agent", "agent_id", "backend"],
    )
    backend_mcp_config_reloads_total = prometheus_client.Counter(
        "backend_mcp_config_reloads_total",
        "Total MCP config file reload events.",
        ["agent", "agent_id", "backend"],
    )
    backend_mcp_servers_active = prometheus_client.Gauge(
        "backend_mcp_servers_active",
        "Number of currently loaded MCP servers.",
        ["agent", "agent_id", "backend"],
    )
    # Peer parity with gemini (#790). Same label schema so dashboards
    # union across backends without rewriting labels.
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
    # Command allow-list rejections (#711). Counts entries rejected by
    # the mcp.json validator because their ``command`` falls outside
    # the configured allow-list (non-absolute path, basename not on
    # MCP_ALLOWED_COMMANDS, etc.). `reason` is a short category key
    # so operators can alert on sudden spikes without parsing logs.
    backend_mcp_command_rejected_total = prometheus_client.Counter(
        "backend_mcp_command_rejected_total",
        "Total MCP server entries rejected by the command allow-list, by reason.",
        ["agent", "agent_id", "backend", "reason"],
    )

    # File watchers
    backend_watcher_events_total = prometheus_client.Counter(
        "backend_watcher_events_total",
        "Total raw file-system change events detected by each watcher.",
        ["agent", "agent_id", "backend", "watcher"],
    )
    backend_file_watcher_restarts_total = prometheus_client.Counter(
        "backend_file_watcher_restarts_total",
        "Total file watcher restart events due to missing or deleted directory.",
        ["agent", "agent_id", "backend", "watcher"],
    )

    # Hooks / tool-audit (#467)
    backend_hooks_blocked_total = prometheus_client.Counter(
        "backend_hooks_blocked_total",
        "DEPRECATED alias for backend_hooks_denials_total (#789). Kept for "
        "dashboards pinned to the claude-specific name pre-unification; "
        "retain through one release cycle then delete.",
        ["agent", "agent_id", "backend", "tool", "source", "rule"],
    )
    # Canonical cross-backend denial counter (#789) — same label schema as
    # the legacy name above. Both counters increment on every deny so
    # existing dashboards and migration queries read the same values.
    backend_hooks_denials_total = prometheus_client.Counter(
        "backend_hooks_denials_total",
        "Total tool calls denied by a PreToolUse hook, labelled by tool name, "
        "rule source (baseline|extension), and the rule name that matched. "
        "Canonical name across claude/openai/gemini backends.",
        ["agent", "agent_id", "backend", "tool", "source", "rule"],
    )
    backend_hooks_warnings_total = prometheus_client.Counter(
        "backend_hooks_warnings_total",
        "Total tool calls flagged (but not denied) by a PreToolUse hook.",
        ["agent", "agent_id", "backend", "tool", "source", "rule"],
    )
    backend_hooks_shed_total = prometheus_client.Counter(
        "backend_hooks_shed_total",
        "Total hook.decision POSTs shed because the bounded in-flight cap "
        "(HOOK_POST_MAX_INFLIGHT, default 32) was reached (#712). Non-zero "
        "rate indicates the harness is unreachable or slow while tool calls "
        "fire rapidly — the backend would otherwise OOM.",
        ["agent", "agent_id", "backend"],
    )
    # settings.json hot-reload of ALLOWED_TOOLS (#934). The reload
    # mutates ALLOWED_TOOLS in place but pre-existing sessions retain
    # the ClaudeAgentOptions built at first-turn — the new set only
    # applies to sessions created after the reload. The `direction`
    # label lets dashboards distinguish a tightening reload (prev set
    # ⊋ new set) from a widening one, and `active_sessions` records
    # how many live sessions still hold the old value at reload time.
    backend_allowed_tools_reload_total = prometheus_client.Counter(
        "backend_allowed_tools_reload_total",
        "Total settings.json ALLOWED_TOOLS reloads observed, split by "
        "direction (tighten|widen|rotate) so dashboards can alert when a "
        "tightening reload coincides with a high active_sessions count — "
        "those sessions keep the old permission set until they evict.",
        ["agent", "agent_id", "backend", "direction"],
    )
    backend_tool_audit_entries_total = prometheus_client.Counter(
        "backend_tool_audit_entries_total",
        "Total rows written to tool-audit.jsonl by the PostToolUse hook.",
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
        "MCP client (i.e. ToolUseBlocks with names of the form "
        "mcp__<server>__<tool>). Separate from backend_sdk_tool_calls_total, "
        "which lumps SDK-internal tools with MCP tools. See #1104.",
        ["agent", "agent_id", "backend", "server", "tool", "outcome"],
    )
    backend_mcp_outbound_duration_seconds = prometheus_client.Histogram(
        "backend_mcp_outbound_duration_seconds",
        "Wall-clock duration of an outbound MCP tool call from ToolUseBlock to ToolResultBlock. See #1104.",
        ["agent", "agent_id", "backend", "server", "tool", "outcome"],
        buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0),
    )
    backend_hooks_config_reloads_total = prometheus_client.Counter(
        "backend_hooks_config_reloads_total",
        "Total reloads of hooks.yaml by the hooks config watcher.",
        ["agent", "agent_id", "backend"],
    )
    backend_hooks_active_rules = prometheus_client.Gauge(
        "backend_hooks_active_rules",
        "Number of currently active hook rules, by rule source.",
        ["agent", "agent_id", "backend", "source"],
    )
    # Total PreToolUse evaluations — denominator for deny/warn rates (#620).
    # The `decision` label is the intended grouping (allow|warn|deny); other
    # per-match labels (source, rule) stay on backend_hooks_blocked_total /
    # backend_hooks_warnings_total and are intentionally NOT duplicated here.
    backend_hooks_evaluations_total = prometheus_client.Counter(
        "backend_hooks_evaluations_total",
        "Total PreToolUse hook evaluations, grouped by final decision.",
        ["agent", "agent_id", "backend", "tool", "decision"],
    )
    # hooks.yaml parse / reload failures (#623). ``reason`` is a closed enum:
    #   yaml_reload_failed       — hooks_config_watcher caught an exception.
    #   missing_name             — rule entry had empty/missing name.
    #   non_string_tool          — rule `tool` was not a string.
    #   no_pattern               — rule had neither deny_if_match nor warn_if_match.
    #   both_patterns            — rule had both deny_if_match and warn_if_match.
    #   non_string_pattern       — pattern field was not a string.
    #   invalid_regex            — regex failed to compile.
    #   non_mapping_entry        — extensions list contained a non-mapping.
    #   file_load_failed         — load_extension_rules failed to read/parse file.
    #   not_mapping              — top-level YAML was not a mapping.
    #   non_list_extensions      — `extensions` key was not a list.
    # Do not extend this list without updating operator dashboards / docs.
    backend_hooks_config_errors_total = prometheus_client.Counter(
        "backend_hooks_config_errors_total",
        "Total hooks.yaml parse/reload/validation errors by reason.",
        ["agent", "agent_id", "backend", "reason"],
    )
    # Per-logger log write errors (#626). Non-breaking complement to
    # backend_log_write_errors_total — carries the `logger` label so operators can
    # distinguish tool-audit vs conversation vs trace write failures.
    backend_log_write_errors_by_logger_total = prometheus_client.Counter(
        "backend_log_write_errors_by_logger_total",
        "Total log write errors attributed to a specific logger.",
        ["agent", "agent_id", "backend", "logger"],
    )

    # SqliteTaskStore lock-contention observability (#552 / #791). Measures
    # wait time to acquire the single asyncio.Lock that serializes
    # save/get/delete. claude sees more tool traffic than gemini — mirror
    # gemini's metric so operators can diff lock pressure across backends.
    backend_sqlite_task_store_lock_wait_seconds = prometheus_client.Histogram(
        "backend_sqlite_task_store_lock_wait_seconds",
        "Seconds waited to acquire the SqliteTaskStore asyncio.Lock, by operation.",
        ["agent", "agent_id", "backend", "op"],
        buckets=(0.0001, 0.0005, 0.001, 0.005, 0.01, 0.05, 0.1, 0.5, 1.0, 5.0),
    )
