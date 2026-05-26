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
process.env.CODEX_SESSION_STORE_PATH = path.join(tmp, "sessions", "responses.json");
fs.writeFileSync(process.env.CODEX_AGENT_MD, "# Metrics contract test identity\n", "utf8");
fs.writeFileSync(
  process.env.CODEX_CONFIG_TOML,
  'model = "gpt-5.5"\nreasoning_effort = "xhigh"\n\n[runtime]\nmax_tool_iterations = 10\ndefault_max_tokens = 30000\n',
  "utf8",
);

const {
  appendBudgetNotice,
  budgetResult,
  createResponseWithSessionFallback,
  deriveSessionId,
  handleA2A,
  publishSessionChunk,
  renderMetrics,
} = await import("./main.js");

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
  assert.match(body, /backend_runtime_config_info\{.*model="gpt-5.5".*reasoning_effort="xhigh".*\} 1/);
  assert.match(body, /backend_runtime_default_max_tokens\{.*backend="codex".*\} 30000/);
  assert.match(body, /backend_runtime_max_tool_iterations\{.*backend="codex".*\} 10/);
  assert.match(body, /backend_runtime_responses_streaming_enabled\{.*backend="codex".*\} 1/);
  assert.match(body, /backend_runtime_stub_mode_enabled\{.*backend="codex".*\} 1/);
  for (const placeholderMetric of [
    "backend_sdk_info",
    "backend_event_loop_lag_seconds_count",
    "backend_task_restarts_total",
    "backend_task_timeout_headroom_seconds_count",
    "backend_session_path_mismatch_total",
    "backend_sdk_subprocess_spawn_duration_seconds_count",
    "backend_sdk_context_fetch_errors_total",
    "backend_stderr_lines_per_task_count",
    "backend_tasks_with_stderr_total",
    "backend_watcher_events_total",
    "backend_file_watcher_restarts_total",
    "backend_hooks_shed_total",
    "backend_allowed_tools_reload_total",
    "backend_sqlite_task_store_lock_wait_seconds_count",
  ]) {
    assert.ok(body.includes(placeholderMetric), `expected ${placeholderMetric} placeholder metric`);
  }
  assert.ok(body.includes("backend_hooks_blocked_total"), "expected deprecated hook-blocked alias metric");
  assert.match(body, /backend_session_caller_cardinality\{.*backend="codex".*\} 0/);
  assert.match(body, /agent="/);
  assert.match(body, /backend="codex"/);
});

test("session binding fallback metrics record Node derivation fallback reasons", () => {
  deriveSessionId("", undefined, "");
  deriveSessionId("legacy-session", "caller-a", "");
  deriveSessionId("shared-session", undefined, "session-secret");

  const body = renderMetrics();
  assert.match(body, /backend_session_binding_fallback_total\{.*reason="secret_unset".*\} [1-9]/);
  assert.match(body, /backend_session_binding_fallback_total\{.*reason="empty_raw_sid".*\} [1-9]/);
  assert.match(body, /backend_session_binding_fallback_total\{.*reason="caller_identity_missing".*\} [1-9]/);
});

test("task retry metric records recoverable Responses session resume retries", async () => {
  let calls = 0;
  const client = {
    responses: {
      create: async (request) => {
        calls += 1;
        if (request.previous_response_id) {
          throw new Error("previous_response_id not found");
        }
        return { id: "resp-after-retry", output: [] };
      },
    },
  };

  const response = await createResponseWithSessionFallback(
    client,
    { model: "gpt-5.5", input: "retry probe", previous_response_id: "resp-stale" },
    "session-retry-probe",
  );

  assert.equal(calls, 2);
  assert.equal(response.id, "resp-after-retry");
  assert.match(renderMetrics(), /backend_task_retries_total\{.*backend="codex".*\} [1-9]/);
});

test("session history save error metric records Responses session store write failures", async () => {
  fs.rmSync(process.env.CODEX_SESSION_STORE_PATH, { force: true, recursive: true });
  fs.mkdirSync(process.env.CODEX_SESSION_STORE_PATH, { recursive: true });

  const client = {
    responses: {
      create: async (request) => {
        if (request.previous_response_id) {
          throw new Error("previous_response_id not found");
        }
        return { id: "resp-after-save-error", output: [] };
      },
    },
  };

  const originalConsoleError = console.error;
  console.error = () => undefined;
  try {
    await createResponseWithSessionFallback(
      client,
      { model: "gpt-5.5", input: "save error probe", previous_response_id: "resp-stale" },
      "session-save-error-probe",
    );
  } finally {
    console.error = originalConsoleError;
  }

  assert.match(renderMetrics(), /backend_session_history_save_errors_total\{.*backend="codex".*\} [1-9]/);
});

test("budget metrics record warning and exhaustion when usage reaches max_tokens", () => {
  const budget = budgetResult({ usage: { input_tokens: 7, output_tokens: 3 } }, 10);

  assert.deepEqual(budget, { total_tokens: 10, max_tokens: 10, exceeded: true });
  assert.match(appendBudgetNotice("done", budget), /Token budget exceeded: 10 tokens used of 10 limit/);

  const body = renderMetrics();
  assert.match(body, /backend_context_warnings_total\{.*backend="codex".*\} [1-9]/);
  assert.match(body, /backend_context_exhaustion_total\{.*backend="codex".*\} [1-9]/);
  assert.match(body, /backend_context_usage_percent_count\{.*backend="codex".*\} [1-9]/);
});
