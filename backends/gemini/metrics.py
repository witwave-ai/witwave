"""Prometheus metrics for the gemini backend agent."""

import prometheus_client
from env import parse_bool_env

_enabled = parse_bool_env("METRICS_ENABLED")

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

# MCP request metrics (#560)
backend_mcp_requests_total: prometheus_client.Counter | None = None
backend_mcp_request_duration_seconds: prometheus_client.Histogram | None = None

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

# Prompt / response size metrics
backend_prompt_length_bytes: prometheus_client.Histogram | None = None
backend_response_length_bytes: prometheus_client.Histogram | None = None
backend_empty_responses_total: prometheus_client.Counter | None = None
backend_empty_prompts_total: prometheus_client.Counter | None = None
backend_prompt_too_large_total: prometheus_client.Counter | None = None

# Model / backend routing metrics
backend_model_requests_total: prometheus_client.Counter | None = None

# Logging subsystem metrics
backend_log_bytes_total: prometheus_client.Counter | None = None
backend_log_entries_total: prometheus_client.Counter | None = None
backend_log_write_errors_total: prometheus_client.Counter | None = None

# Session persistence metrics
backend_session_history_save_errors_total: prometheus_client.Counter | None = None
backend_session_history_reset_total: prometheus_client.Counter | None = None
# #1622: byte-cap trim corruption recovery — fires when no safe AFC
# boundary exists in the trim window and the saver force-splits at the
# earliest user-role boundary instead of preserving an oversized payload.
backend_history_force_split_total: prometheus_client.Counter | None = None

# SDK metrics
backend_sdk_subprocess_spawn_duration_seconds: prometheus_client.Histogram | None = None
backend_sdk_query_duration_seconds: prometheus_client.Histogram | None = None
backend_sdk_query_error_duration_seconds: prometheus_client.Histogram | None = None
backend_sdk_time_to_first_message_seconds: prometheus_client.Histogram | None = None
backend_sdk_session_duration_seconds: prometheus_client.Histogram | None = None
backend_sdk_messages_per_query: prometheus_client.Histogram | None = None
backend_sdk_turns_per_query: prometheus_client.Histogram | None = None
backend_sdk_tokens_per_query: prometheus_client.Histogram | None = None
backend_text_blocks_per_query: prometheus_client.Histogram | None = None

# SDK error classification (parity with claude / codex — #445)
backend_sdk_errors_total: prometheus_client.Counter | None = None
backend_sdk_result_errors_total: prometheus_client.Counter | None = None
backend_sdk_client_errors_total: prometheus_client.Counter | None = None

# Streaming events emitted (parity with claude / codex — #430)
backend_streaming_events_emitted_total: prometheus_client.Counter | None = None

# File watcher metrics
backend_watcher_events_total: prometheus_client.Counter | None = None
backend_file_watcher_restarts_total: prometheus_client.Counter | None = None

# GEMINI.md hot-reload revision gauge (#1751). Mirrors codex #1097.
# `revision` label is the SHA-256 hex prefix (first 12 chars) of the
# currently-active GEMINI.md content; the gauge value is always 1 when
# set. On hot-reload the previous revision's label set is removed so
# only the live revision reports 1. Operators correlate this with the
# per-query span attribute `gemini.agent_md_revision` to verify a
# behavioral rollout has propagated.
backend_agent_md_revision: prometheus_client.Gauge | None = None

# Context window metrics
backend_context_tokens: prometheus_client.Histogram | None = None
backend_context_tokens_remaining: prometheus_client.Histogram | None = None
backend_context_usage_percent: prometheus_client.Histogram | None = None
backend_context_exhaustion_total: prometheus_client.Counter | None = None
backend_context_warnings_total: prometheus_client.Counter | None = None

# Token budget metrics
backend_budget_exceeded_total: prometheus_client.Counter | None = None

# SqliteTaskStore lock-contention observability (#552)
backend_sqlite_task_store_lock_wait_seconds: prometheus_client.Histogram | None = None

# Hooks / tool-audit (#631 — parity with claude #467). Populated lazily
# when the gemini tool-call path is wired (currently blocked on #640);
# declaring the module-level names now keeps import-time references safe
# and gives operators a single known contract regardless of whether hooks
# are actively evaluated yet.
backend_hooks_denials_total: prometheus_client.Counter | None = None
backend_hooks_warnings_total: prometheus_client.Counter | None = None
backend_tool_audit_entries_total: prometheus_client.Counter | None = None
# #1100: scaffold for the eventual gemini allow-list enforcement. Once
# #640 hand-rolls the AFC tool loop, this counter will fire on every
# ALLOWED_TOOLS hot-reload. Parity with claude's backend_allowed_tools_reload_total
# label schema so cross-backend dashboards can union by (agent, agent_id,
# backend, direction).
backend_allowed_tools_reload_total: prometheus_client.Counter | None = None
# Per-entry byte histogram + rotation-pressure counter for tool-activity.jsonl (#1102).
backend_tool_audit_bytes_per_entry: prometheus_client.Histogram | None = None
backend_tool_audit_rotation_pressure_total: prometheus_client.Counter | None = None
# shared/session_binding fallback counter — #1103.
backend_session_binding_fallback_total: prometheus_client.Counter | None = None
# Outbound MCP tool HTTP metric family (#1104).
backend_mcp_outbound_requests_total: prometheus_client.Counter | None = None
backend_mcp_outbound_duration_seconds: prometheus_client.Histogram | None = None
backend_hooks_config_errors_total: prometheus_client.Counter | None = None
backend_hooks_config_reloads_total: prometheus_client.Counter | None = None
# Enforcement-mode gauge (#736). Reports whether PreToolUse hooks are
# actually evaluated on tool calls. On gemini the answer is "skeleton"
# until #640 lands (google-genai's AFC runs the tool loop internally so
# evaluate_pre_tool_use never fires even when hooks.yaml is loaded).
# Dashboards and alerts can read this gauge to tell the difference
# between "hooks loaded and enforcing" and "hooks loaded but bypassed".
backend_hooks_enforcement_mode: prometheus_client.Gauge | None = None
backend_hooks_active_rules: prometheus_client.Gauge | None = None
backend_hooks_evaluations_total: prometheus_client.Counter | None = None
backend_session_path_mismatch_total: prometheus_client.Counter | None = None

# MCP config watcher metrics (#640 — parity with codex #432, #526).
backend_mcp_config_errors_total: prometheus_client.Counter | None = None
backend_mcp_command_rejected_total: prometheus_client.Counter | None = None
backend_mcp_config_reloads_total: prometheus_client.Counter | None = None
backend_mcp_servers_active: prometheus_client.Gauge | None = None
# Per-server liveness gauge and exit counter (#816). Without these a
# crashed MCP stdio subprocess silently disappears from operator
# visibility until the next hot-reload observes missing capabilities.
backend_mcp_server_up: prometheus_client.Gauge | None = None
backend_mcp_server_exits_total: prometheus_client.Counter | None = None

# Tool call metrics (#640 — parity with codex #445). Populated on each
# function_call observed in the google-genai AFC history.
backend_sdk_tool_calls_total: prometheus_client.Counter | None = None
backend_sdk_tool_errors_total: prometheus_client.Counter | None = None
# Per-query tool-call histogram (#795). Parity with claude / codex label
# schema. Ships as a registered zero-value placeholder today since gemini
# does not yet surface per-run_query tool-call counts; wiring lands with
# the tool-call path enhancements in #640.
backend_sdk_tool_calls_per_query: prometheus_client.Histogram | None = None
backend_sdk_tool_duration_seconds: prometheus_client.Histogram | None = None
# Per-call payload size histograms (#811). Peer parity with claude so
# cross-backend dashboards can chart input/result size distributions
# without backend-specific metric names.
backend_sdk_tool_call_input_size_bytes: prometheus_client.Histogram | None = None
backend_sdk_tool_result_size_bytes: prometheus_client.Histogram | None = None

# #1687: cross-backend parity placeholders. claude is the documented
# superset (AGENTS.md "Metrics landscape"); peers track placeholders
# for every claude metric so dashboards that union over
# (agent, agent_id, backend) don't lose label sets when a metric only
# emits on one backend. The placeholders below are declared but never
# .inc()/.observe()'d by gemini's executor — the underlying mechanism
# (PreToolUse hooks, hook-shed accounting, per-logger error tally,
# context-fetch errors, MCP caller cardinality, stderr capture, stream
# chunk drops, task retry on session conflict) lives on claude (and
# in some cases codex) only.
backend_hooks_blocked_total: prometheus_client.Counter | None = None
backend_hooks_shed_total: prometheus_client.Counter | None = None
backend_log_write_errors_by_logger_total: prometheus_client.Counter | None = None
backend_sdk_context_fetch_errors_total: prometheus_client.Counter | None = None
backend_session_caller_cardinality: prometheus_client.Gauge | None = None
backend_stderr_lines_per_task: prometheus_client.Histogram | None = None
backend_streaming_chunks_dropped_total: prometheus_client.Counter | None = None
backend_task_retries_total: prometheus_client.Counter | None = None
backend_tasks_with_stderr_total: prometheus_client.Counter | None = None

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
    )
    backend_a2a_last_request_timestamp_seconds = prometheus_client.Gauge(
        "backend_a2a_last_request_timestamp_seconds",
        "Unix epoch of the most recent A2A request received.",
        ["agent", "agent_id", "backend"],
    )

    # MCP (#560)
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
    )
    backend_task_error_duration_seconds = prometheus_client.Histogram(
        "backend_task_error_duration_seconds",
        "Wall-clock seconds for tasks that end in error or timeout.",
        ["agent", "agent_id", "backend"],
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

    # Prompt / response
    backend_prompt_length_bytes = prometheus_client.Histogram(
        "backend_prompt_length_bytes",
        "Byte length of incoming prompts passed to run().",
        ["agent", "agent_id", "backend"],
    )
    backend_response_length_bytes = prometheus_client.Histogram(
        "backend_response_length_bytes",
        "Byte length of responses returned by run().",
        ["agent", "agent_id", "backend"],
    )
    backend_empty_responses_total = prometheus_client.Counter(
        "backend_empty_responses_total",
        "Total tasks that produced no text output.",
        ["agent", "agent_id", "backend"],
    )
    backend_empty_prompts_total = prometheus_client.Counter(
        "backend_empty_prompts_total",
        "Total execute() invocations rejected because the resolved prompt was empty or whitespace-only (#544 / #812).",
        ["agent", "agent_id", "backend"],
    )
    backend_prompt_too_large_total = prometheus_client.Counter(
        "backend_prompt_too_large_total",
        "Total execute() invocations rejected because the inbound prompt's UTF-8 byte length exceeded MAX_PROMPT_BYTES (#1620 / #1730).",  # noqa: E501
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

    # Session persistence
    backend_session_history_save_errors_total = prometheus_client.Counter(
        "backend_session_history_save_errors_total",
        "Total permanent session history save failures after all retries are exhausted.",
        ["agent", "agent_id", "backend"],
    )
    # #1000: track the follow-up path where _load_history is skipped
    # because a prior save permanently failed. Operators could not
    # previously distinguish "save failed once but the user continued
    # normally" from "user's session was silently reset from scratch";
    # this counter ticks exactly once per reset.
    backend_session_history_reset_total = prometheus_client.Counter(
        "backend_session_history_reset_total",
        "Total sessions reset to empty history because a prior save permanently "
        "failed and the stale on-disk file was discarded. See #1000.",
        ["agent", "agent_id", "backend"],
    )
    # #1622: byte-cap trim corruption recovery counter. See module-level
    # declaration for the corruption recovery semantics.
    backend_history_force_split_total = prometheus_client.Counter(
        "backend_history_force_split_total",
        "Total byte-cap trims that force-split a mid-AFC pair because no safe "
        "boundary existed within the target window. Corruption recovery path; "
        "non-zero values indicate runaway AFC chains that exceed the byte cap. "
        "See #1622.",
        ["agent", "agent_id", "backend"],
    )

    # SDK
    backend_sdk_subprocess_spawn_duration_seconds = prometheus_client.Histogram(
        "backend_sdk_subprocess_spawn_duration_seconds",
        "Time to initialize the backend client/SDK (genai.Client cold start).",
        ["agent", "agent_id", "backend", "model"],
        buckets=(0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60),
    )
    backend_sdk_query_duration_seconds = prometheus_client.Histogram(
        "backend_sdk_query_duration_seconds",
        "Raw backend query time in seconds inside run_query().",
        ["agent", "agent_id", "backend", "model"],
    )
    backend_sdk_query_error_duration_seconds = prometheus_client.Histogram(
        "backend_sdk_query_error_duration_seconds",
        "Wall-clock seconds for run_query() calls that end in error.",
        ["agent", "agent_id", "backend", "model"],
    )
    backend_sdk_time_to_first_message_seconds = prometheus_client.Histogram(
        "backend_sdk_time_to_first_message_seconds",
        "Seconds from query submission to the first response message.",
        ["agent", "agent_id", "backend", "model"],
    )
    backend_sdk_session_duration_seconds = prometheus_client.Histogram(
        "backend_sdk_session_duration_seconds",
        "Backend session/connection lifetime in seconds.",
        ["agent", "agent_id", "backend", "model"],
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
    backend_text_blocks_per_query = prometheus_client.Histogram(
        "backend_text_blocks_per_query",
        "Number of text blocks returned per run_query() invocation.",
        ["agent", "agent_id", "backend", "model"],
        buckets=(0, 1, 2, 5, 10, 20, 50, 100),
    )

    # SDK error classification (parity with claude / codex — #445)
    backend_sdk_errors_total = prometheus_client.Counter(
        "backend_sdk_errors_total",
        "Total stderr/error lines emitted by the backend SDK.",
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

    # Streaming events emitted (parity with claude / codex — #430)
    backend_streaming_events_emitted_total = prometheus_client.Counter(
        "backend_streaming_events_emitted_total",
        "Total partial agent_text_message events enqueued during streaming. "
        "Equals the number of chunks the executor pushed to the A2A "
        "event_queue mid-stream (#430).",
        ["agent", "agent_id", "backend", "model"],
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

    # GEMINI.md revision rollout gauge (#1751, mirrors codex #1097). The
    # `revision` label is the SHA-256 hex prefix (first 12 chars) of the
    # currently-active GEMINI.md content; value is always 1 when set. On
    # hot-reload the old revision's label set is cleared via .remove(...)
    # before the new one is stamped so only the live revision reports 1.
    backend_agent_md_revision = prometheus_client.Gauge(
        "backend_agent_md_revision",
        "Currently-active GEMINI.md revision (SHA-256 hex prefix, first 12 chars). "
        "Gauge value is 1 when set; previous revision is cleared on hot-reload. "
        "Pair with the `gemini.agent_md_revision` per-query span attribute to "
        "verify a behavioral rollout has propagated. See #1751 / #1097.",
        ["agent", "agent_id", "backend", "revision"],
    )

    # Context window
    backend_context_tokens = prometheus_client.Histogram(
        "backend_context_tokens",
        "Token count used per query (from Gemini usage_metadata).",
        ["agent", "agent_id", "backend"],
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

    # Token budget
    backend_budget_exceeded_total = prometheus_client.Counter(
        "backend_budget_exceeded_total",
        "Total token budget exceeded events (max_tokens limit hit during execution).",
        ["agent", "agent_id", "backend"],
    )

    # SqliteTaskStore lock-contention observability (#552)
    # Measures wait time to acquire the single asyncio.Lock that serializes
    # save/get/delete. With WAL (#523) in place, SQLite itself can serve
    # concurrent readers alongside writers; the Python-level lock is retained
    # because sqlite3.Connection is not safe for concurrent use. This metric
    # lets us see, empirically, whether contention is a real bottleneck before
    # investing in a connection-pool or per-call-connect refactor.
    backend_sqlite_task_store_lock_wait_seconds = prometheus_client.Histogram(
        "backend_sqlite_task_store_lock_wait_seconds",
        "Seconds waited to acquire the SqliteTaskStore asyncio.Lock, by operation.",
        ["agent", "agent_id", "backend", "op"],
        buckets=(0.0001, 0.0005, 0.001, 0.005, 0.01, 0.05, 0.1, 0.5, 1.0, 5.0),
    )

    # Hooks / tool-audit (#631). Label schema mirrors claude's superset
    # (#467) so dashboards can union across backends on (agent, agent_id,
    # backend, tool, rule, source). The `rule` label is the matched rule
    # name from hooks_engine; `tool` is the tool-name string presented to
    # ``evaluate_pre_tool_use``; `source` is "baseline"|"extension" so
    # operators can separate shipped deny rules from per-agent YAML
    # extensions (#794 — was explicitly omitted pending #640 wiring).
    backend_hooks_denials_total = prometheus_client.Counter(
        "backend_hooks_denials_total",
        "Total tool calls denied by a PreToolUse hook.",
        ["agent", "agent_id", "backend", "tool", "rule", "source"],
    )
    backend_hooks_warnings_total = prometheus_client.Counter(
        "backend_hooks_warnings_total",
        "Total tool calls flagged (but not denied) by a PreToolUse hook.",
        ["agent", "agent_id", "backend", "tool", "rule", "source"],
    )
    backend_tool_audit_entries_total = prometheus_client.Counter(
        "backend_tool_audit_entries_total",
        "Total rows written to tool-audit.jsonl by the PostToolUse hook.",
        ["agent", "agent_id", "backend", "tool"],
    )
    # #1100: ALLOWED_TOOLS hot-reload counter. Label schema matches
    # claude's so cross-backend dashboards can union by (agent, agent_id,
    # backend, direction). Gemini currently increments on parse-time
    # allow-list construction so operators can see the scaffold wired;
    # the reload pathway lands with the hand-rolled AFC loop (#640).
    backend_allowed_tools_reload_total = prometheus_client.Counter(
        "backend_allowed_tools_reload_total",
        "Total ALLOWED_TOOLS hot-reloads observed; direction label "
        "distinguishes tighten/widen/rotate. Parity with claude's "
        "counter. See #1100.",
        ["agent", "agent_id", "backend", "direction"],
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
    backend_hooks_config_errors_total = prometheus_client.Counter(
        "backend_hooks_config_errors_total",
        "Total hooks.yaml parse/reload/validation errors by reason.",
        ["agent", "agent_id", "backend", "reason"],
    )
    backend_hooks_config_reloads_total = prometheus_client.Counter(
        "backend_hooks_config_reloads_total",
        "Total reloads of hooks.yaml by the hooks config watcher.",
        ["agent", "agent_id", "backend"],
    )
    backend_hooks_enforcement_mode = prometheus_client.Gauge(
        "backend_hooks_enforcement_mode",
        "PreToolUse hook enforcement mode. 0=partial/skeleton (rules loaded but "
        "AFC bypasses them), 1=enforcing, -1=disabled. Gemini reports 0 "
        "until #640 disables AFC and hand-rolls the tool loop (#736).",
        ["agent", "agent_id", "backend"],
    )
    # Peer-parity placeholders (#796): claude's hook surface exposes
    # active_rules and evaluations_total; register them on gemini so
    # cross-backend dashboards don't drop the series. Gemini's hook
    # path is skeleton-only today (see enforcement_mode=0) so these
    # stay at zero until #640 wires live evaluation.
    backend_hooks_active_rules = prometheus_client.Gauge(
        "backend_hooks_active_rules",
        "Number of currently active hook rules, by rule source.",
        ["agent", "agent_id", "backend", "source"],
    )
    backend_hooks_evaluations_total = prometheus_client.Counter(
        "backend_hooks_evaluations_total",
        "Total PreToolUse hook evaluations, grouped by final decision.",
        ["agent", "agent_id", "backend", "tool", "decision"],
    )
    # Session path layout drift (#530 / #796). Registered so dashboards
    # filtering backend=~".*" keep the series; gemini does not
    # self-probe SDK on-disk layout today.
    backend_session_path_mismatch_total = prometheus_client.Counter(
        "backend_session_path_mismatch_total",
        "Total startup self-test observations that the SDK on-disk layout "
        "has drifted from the conventions the backend assumes (#530).",
        ["agent", "agent_id", "backend", "reason"],
    )

    # MCP config (parity with codex — #432, #526; landed for gemini in #640).
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
    # Per-server up gauge + exits counter (#816). Operators alert on
    # sum by(server)(backend_mcp_server_up) dropping to 0 or on
    # rate(backend_mcp_server_exits_total[5m]) > 0.
    backend_mcp_server_up = prometheus_client.Gauge(
        "backend_mcp_server_up",
        "1 when the MCP stdio ClientSession for this server initialised and is live, 0 otherwise.",
        ["agent", "agent_id", "backend", "server"],
    )
    backend_mcp_server_exits_total = prometheus_client.Counter(
        "backend_mcp_server_exits_total",
        "Total MCP stdio subprocess exits, partitioned by reason (init_failed|normal_close|reload).",
        ["agent", "agent_id", "backend", "server", "reason"],
    )
    backend_mcp_servers_active = prometheus_client.Gauge(
        "backend_mcp_servers_active",
        "Number of MCP servers currently live in the lifespan-scoped stack.",
        ["agent", "agent_id", "backend"],
    )
    # Command allow-list rejections (#730 — parity with claude #711 / codex #720).
    backend_mcp_command_rejected_total = prometheus_client.Counter(
        "backend_mcp_command_rejected_total",
        "Total MCP server entries rejected by the command allow-list, by reason.",
        ["agent", "agent_id", "backend", "reason"],
    )

    # Tool calls (#640 — parity with codex / claude / #793). Emitted per
    # function_call observed in the google-genai AFC history. Aligned to
    # the claude/codex schema: a plain call-count counter; error rates come
    # from the sibling backend_sdk_tool_errors_total (#793).
    backend_sdk_tool_calls_total = prometheus_client.Counter(
        "backend_sdk_tool_calls_total",
        "Total tool calls dispatched via the backend SDK, by tool name.",
        ["agent", "agent_id", "backend", "tool"],
    )
    backend_sdk_tool_errors_total = prometheus_client.Counter(
        "backend_sdk_tool_errors_total",
        "Total tool execution errors by tool name.",
        ["agent", "agent_id", "backend", "tool"],
    )
    backend_sdk_tool_calls_per_query = prometheus_client.Histogram(
        "backend_sdk_tool_calls_per_query",
        "Number of tool calls per run_query() invocation.",
        # model label aligned with claude / codex (#795).
        ["agent", "agent_id", "backend", "model"],
        buckets=(0, 1, 2, 5, 10, 20, 50, 100, 200),
    )
    backend_sdk_tool_duration_seconds = prometheus_client.Histogram(
        "backend_sdk_tool_duration_seconds",
        "Duration of individual tool calls in seconds.",
        ["agent", "agent_id", "backend", "tool"],
        buckets=(0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60),
    )
    # Per-call payload size histograms (#811). Bucket edges match claude's
    # backend_sdk_tool_call_input_size_bytes / *_result_size_bytes so
    # cross-backend p95 / p99 heatmaps line up without rescaling.
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

    # #1687: cross-backend parity placeholders. Declared so cross-backend
    # PromQL joins don't drop label sets where claude (or codex) emits
    # and gemini doesn't. The underlying mechanisms (PreToolUse hooks,
    # hook-shed accounting, per-logger error tally, etc.) live on
    # claude only; placeholders sit at zero on gemini.
    backend_hooks_blocked_total = prometheus_client.Counter(
        "backend_hooks_blocked_total",
        "DEPRECATED alias for backend_hooks_denials_total (#789). Placeholder "
        "on gemini so dashboards pinned to the claude-specific name still join.",
        ["agent", "agent_id", "backend", "tool", "source", "rule"],
    )
    backend_hooks_shed_total = prometheus_client.Counter(
        "backend_hooks_shed_total",
        "Total hook.decision POSTs shed because the bounded in-flight cap "
        "was reached (claude-only mechanism — placeholder on gemini for parity).",
        ["agent", "agent_id", "backend"],
    )
    backend_log_write_errors_by_logger_total = prometheus_client.Counter(
        "backend_log_write_errors_by_logger_total",
        "Total log write errors attributed to a specific logger (claude-only "
        "instrumentation — placeholder on gemini for parity).",
        ["agent", "agent_id", "backend", "logger"],
    )
    backend_sdk_context_fetch_errors_total = prometheus_client.Counter(
        "backend_sdk_context_fetch_errors_total",
        "Total context usage fetch failures (claude-only mechanism — placeholder on gemini for parity).",
        ["agent", "agent_id", "backend", "model"],
    )
    backend_session_caller_cardinality = prometheus_client.Gauge(
        "backend_session_caller_cardinality",
        "Distinct caller_identity values observed on /mcp since process start "
        "(claude-only tracker — placeholder on gemini for parity, see #1049).",
        ["agent", "agent_id", "backend"],
    )
    backend_stderr_lines_per_task = prometheus_client.Histogram(
        "backend_stderr_lines_per_task",
        "Number of SDK stderr lines captured per run() invocation (claude-only "
        "instrumentation — placeholder on gemini for parity).",
        ["agent", "agent_id", "backend"],
        buckets=(0, 1, 2, 5, 10, 20, 50, 100),
    )
    backend_streaming_chunks_dropped_total = prometheus_client.Counter(
        "backend_streaming_chunks_dropped_total",
        "Total streaming chunks dropped because the A2A consumer's on_chunk "
        "callback exceeded its timeout (claude/codex mechanism — placeholder "
        "on gemini for parity, see #724/#1091).",
        ["agent", "agent_id", "backend", "model"],
    )
    backend_task_retries_total = prometheus_client.Counter(
        "backend_task_retries_total",
        "Total task retries due to session already in use (claude-only path — placeholder on gemini for parity).",
        ["agent", "agent_id", "backend"],
    )
    backend_tasks_with_stderr_total = prometheus_client.Counter(
        "backend_tasks_with_stderr_total",
        "Total task executions that produced any SDK stderr output (claude-only "
        "instrumentation — placeholder on gemini for parity).",
        ["agent", "agent_id", "backend"],
    )
