import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";

const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "witwave-codex-metrics-test-"));
process.env.CONVERSATION_LOG = path.join(tmp, "conversation.jsonl");
process.env.TRACE_LOG = path.join(tmp, "tool-activity.jsonl");
process.env.CODEX_STUB_MODE = "true";
process.env.CODEX_MEMORY_ROOT = path.join(tmp, "memory");
process.env.CONVERSATIONS_AUTH_DISABLED = "true";
process.env.CODEX_CONFIG_TOML = path.join(tmp, "config.toml");
process.env.CODEX_AGENT_MD = path.join(tmp, "AGENTS.md");
fs.writeFileSync(process.env.CODEX_AGENT_MD, "# Metrics contract test identity\n", "utf8");
fs.writeFileSync(process.env.CODEX_CONFIG_TOML, 'model = "gpt-5.5"\nreasoning_effort = "xhigh"\n', "utf8");

const { handleA2A, publishSessionChunk, renderMetrics } = await import("./main.js");

test("streaming delta metrics use bounded model labels", () => {
  publishSessionChunk("00000000-0000-4000-8000-000000000201", {
    role: "assistant",
    seq: 1,
    content: "delta",
    final: false,
    model: "gpt-5.5",
  });
  publishSessionChunk("00000000-0000-4000-8000-000000000202", {
    role: "assistant",
    seq: 1,
    content: "delta",
    final: false,
    model: "bad model label",
  });

  const body = renderMetrics();
  assert.match(body, /backend_streaming_events_emitted_total\{.*model="gpt-5\.5".*\} 1/);
  assert.match(body, /backend_streaming_events_emitted_total\{.*model="unknown".*\} 1/);
  assert.match(body, /backend_streaming_chunks_dropped_total/);
});

test("renderMetrics exposes the common backend label shape", async () => {
  await handleA2A({
    jsonrpc: "2.0",
    id: "metric",
    method: "message/send",
    params: { message: { parts: [{ kind: "text", text: "metric probe" }] } },
  });
  const body = renderMetrics();
  assert.match(body, /backend_up/);
  assert.match(body, /backend_agent_md_revision\{.*revision="[a-f0-9]{12}".*\} 1/);
  assert.match(body, /backend_a2a_requests_total/);
  assert.match(body, /backend_model_requests_total\{.*model="gpt-5.5".*\} [1-9]/);
  assert.match(body, /backend_a2a_request_duration_seconds_count\{.*backend="codex".*\} [1-9]/);
  assert.match(body, /backend_tasks_total\{.*status="success".*\} [1-9]/);
  assert.match(body, /backend_task_duration_seconds_count\{.*backend="codex".*\} [1-9]/);
  assert.match(body, /backend_task_error_duration_seconds_count\{.*backend="codex".*\}/);
  assert.match(body, /backend_task_last_success_timestamp_seconds\{.*backend="codex".*\}/);
  assert.match(body, /backend_task_last_error_timestamp_seconds\{.*backend="codex".*\}/);
  assert.match(body, /backend_task_cancellations_total\{.*backend="codex".*\}/);
  assert.match(body, /backend_log_entries_total\{.*logger="conversation".*\} [1-9]/);
  assert.match(body, /backend_log_bytes_total\{.*logger="conversation".*\} [1-9]/);
  assert.match(body, /backend_log_write_errors_total\{.*backend="codex".*\} 0/);
  assert.match(body, /backend_prompt_length_bytes_count/);
  assert.match(body, /backend_empty_responses_total/);
  assert.match(body, /backend_active_sessions/);
  assert.match(body, /backend_concurrent_queries\{.*backend="codex".*\} 0/);
  assert.match(body, /backend_running_tasks\{.*backend="codex".*\} 0/);
  assert.match(body, /backend_sdk_query_duration_seconds_count\{.*model="gpt-5.5".*\} [1-9]/);
  assert.match(body, /backend_sdk_time_to_first_message_seconds_count\{.*model="gpt-5.5".*\} [1-9]/);
  assert.match(body, /backend_sdk_session_duration_seconds_count\{.*model="gpt-5.5".*\} [1-9]/);
  assert.match(body, /backend_sdk_messages_per_query_count\{.*model="gpt-5.5".*\} [1-9]/);
  assert.match(body, /backend_sdk_turns_per_query_count\{.*model="gpt-5.5".*\} [1-9]/);
  assert.match(body, /backend_sdk_tokens_per_query_count\{.*model="gpt-5.5".*\} [1-9]/);
  assert.match(body, /backend_sdk_errors_total/);
  assert.match(body, /backend_sdk_result_errors_total/);
  assert.match(body, /backend_sdk_client_errors_total/);
  assert.match(body, /backend_text_blocks_per_query_count\{.*model="gpt-5.5".*\} [1-9]/);
  assert.match(body, /backend_context_usage_percent_count/);
  assert.match(body, /backend_context_warnings_total/);
  assert.match(body, /backend_context_exhaustion_total/);
  assert.match(body, /backend_session_age_seconds_count/);
  assert.match(body, /backend_session_idle_seconds_count/);
  assert.match(body, /backend_lru_cache_utilization_percent/);
  assert.match(body, /backend_mcp_config_reloads_total/);
  assert.match(body, /backend_mcp_servers_active/);
  assert.match(body, /backend_tool_audit_rotation_pressure_total/);
  assert.match(body, /backend_budget_exceeded_total/);
  for (const placeholderMetric of [
    "backend_sdk_info",
    "backend_event_loop_lag_seconds_count",
    "backend_task_restarts_total",
    "backend_task_timeout_headroom_seconds_count",
    "backend_session_history_save_errors_total",
    "backend_session_path_mismatch_total",
    "backend_sdk_subprocess_spawn_duration_seconds_count",
    "backend_sdk_context_fetch_errors_total",
    "backend_stderr_lines_per_task_count",
    "backend_tasks_with_stderr_total",
    "backend_task_retries_total",
    "backend_mcp_command_rejected_total",
    "backend_watcher_events_total",
    "backend_file_watcher_restarts_total",
    "backend_hooks_blocked_total",
    "backend_hooks_shed_total",
    "backend_allowed_tools_reload_total",
    "backend_session_binding_fallback_total",
    "backend_session_caller_cardinality",
    "backend_sqlite_task_store_lock_wait_seconds_count",
  ]) {
    assert.ok(body.includes(placeholderMetric), `expected ${placeholderMetric} placeholder metric`);
  }
  assert.match(body, /agent="/);
  assert.match(body, /backend="codex"/);
});
