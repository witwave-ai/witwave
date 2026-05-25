import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";

const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "witwave-codex-test-"));
process.env.CONVERSATION_LOG = path.join(tmp, "conversation.jsonl");
process.env.TRACE_LOG = path.join(tmp, "tool-activity.jsonl");
process.env.CODEX_STUB_MODE = "true";
process.env.CODEX_MEMORY_ROOT = path.join(tmp, "memory");
process.env.CONVERSATIONS_AUTH_DISABLED = "true";
process.env.LOG_REDACT = "true";
process.env.CODEX_CONFIG_TOML = path.join(tmp, "config.toml");
process.env.HOOKS_CONFIG_PATH = path.join(tmp, "hooks.yaml");
process.env.HOOKS_BASELINE_ENABLED = "true";
process.env.CODEX_AGENT_MD = path.join(tmp, "AGENTS.md");
fs.writeFileSync(process.env.CODEX_AGENT_MD, "# Codex test identity\n", "utf8");
fs.writeFileSync(
  process.env.CODEX_CONFIG_TOML,
  `
model = "gpt-5.5"
reasoning_effort = "xhigh"

[tools]
shell = true
memory = true
mcp = true
`,
  "utf8",
);

const {
  buildAgentCard,
  collectStreamingResponse,
  constantTimeBearerTokenMatches,
  conversationsAuthConfigWarning,
  deriveSessionId,
  extractRequestMetadata,
  extractPrompt,
  handleA2A,
  isRecoverableSessionResumeError,
  loadCodexConfigFromText,
  mcpFunctionName,
  mcpServerEntriesFromConfig,
  mcpToolResultText,
  maxOutputTokensForRequest,
  maxTokensForRequest,
  publishSessionChunk,
  renderMetrics,
  responseFunctionCalls,
} = await import("./main.js");

test("buildAgentCard advertises a non-streaming Codex backend", () => {
  const card = buildAgentCard();
  assert.equal(card.name, process.env.AGENT_NAME || "codex");
  assert.equal(card.capabilities.streaming, false);
  assert.equal(card.skills[0].id, "general");
  assert.deepEqual(card.defaultInputModes, ["text/plain"]);
});

test("constantTimeBearerTokenMatches requires an exact bearer token match", () => {
  assert.equal(constantTimeBearerTokenMatches("Bearer expected-token", "expected-token"), true);
  assert.equal(constantTimeBearerTokenMatches("Bearer wrong-token", "expected-token"), false);
  assert.equal(constantTimeBearerTokenMatches("bearer expected-token", "expected-token"), false);
  assert.equal(constantTimeBearerTokenMatches("Bearer expected-token ", "expected-token"), false);
  assert.equal(constantTimeBearerTokenMatches(undefined, "expected-token"), false);
  assert.equal(constantTimeBearerTokenMatches("Bearer expected-token", ""), false);
});

test("conversationsAuthConfigWarning mirrors protected endpoint auth posture", () => {
  assert.equal(conversationsAuthConfigWarning("configured-token", false), "");
  assert.match(conversationsAuthConfigWarning("", true), /authentication is DISABLED/);
  assert.match(conversationsAuthConfigWarning("", false), /protected endpoints will fail closed/);
});

test("mcpServerEntriesFromConfig loads URL-shaped MCP servers with bearer headers", () => {
  const entries = mcpServerEntriesFromConfig(
    {
      mcpServers: {
        kubernetes: { url: " http://witwave-mcp-kubernetes:8000 ", headers: { "X-Witwave": "yes" } },
        stdioOnly: { command: "mcp-kubernetes" },
      },
    },
    "tool-token",
  );

  assert.deepEqual(entries, [
    {
      name: "kubernetes",
      url: "http://witwave-mcp-kubernetes:8000",
      headers: { "X-Witwave": "yes", Authorization: "Bearer tool-token" },
    },
  ]);
});

test("mcpFunctionName and mcpToolResultText create Responses-safe function outputs", () => {
  assert.equal(mcpFunctionName("kubernetes", "list_resources"), "mcp__kubernetes__list_resources");
  assert.match(
    mcpFunctionName("server with spaces", "tool/with/slashes"),
    /^mcp__server_with_spaces__tool_with_slashes/,
  );
  assert.ok(mcpFunctionName("a".repeat(80), "b".repeat(80)).length <= 64);
  assert.equal(
    mcpToolResultText({
      content: [{ type: "text", text: "pods are healthy" }],
      structuredContent: { ready: true },
    }),
    'pods are healthy\n{"ready":true}',
  );
});

test("loadCodexConfigFromText parses supported Codex runtime config", () => {
  const config = loadCodexConfigFromText(`
model = "gpt-5.5"
reasoning_effort = "xhigh"

[tools]
shell = true
memory = false
mcp = true

[runtime]
max_tool_iterations = 4

[paths]
memory_root = "/workspaces/witwave-self/memory/agents/mira"
mcp_config = "/home/agent/.codex/mcp.json"
`);

  assert.equal(config.model, "gpt-5.5");
  assert.equal(config.reasoning_effort, "xhigh");
  assert.equal(config.tools.shell, true);
  assert.equal(config.tools.memory, false);
  assert.equal(config.tools.mcp, true);
  assert.equal(config.runtime.max_tool_iterations, 4);
  assert.equal(config.paths.memory_root, "/workspaces/witwave-self/memory/agents/mira");
  assert.equal(config.paths.mcp_config, "/home/agent/.codex/mcp.json");
});

test("extractPrompt reads the A2A message/send text parts shape", () => {
  const payload = {
    jsonrpc: "2.0",
    id: "1",
    method: "message/send",
    params: {
      message: {
        role: "user",
        parts: [
          { kind: "text", text: "first" },
          { kind: "text", text: "second" },
        ],
        messageId: "m1",
      },
    },
  };
  assert.equal(extractPrompt(payload), "first\nsecond");
});

test("deriveSessionId binds the same raw session to different callers when secret is set", () => {
  const raw = "shared-session";
  const alice = deriveSessionId(raw, "alice", "secret");
  const aliceAgain = deriveSessionId(raw, "alice", "secret");
  const bob = deriveSessionId(raw, "bob", "secret");
  assert.equal(alice, aliceAgain);
  assert.notEqual(alice, bob);
  assert.match(alice, /^[0-9a-f-]{36}$/);
});

test("collectStreamingResponse publishes text deltas and returns the completed response", async () => {
  async function* fakeStream() {
    yield { type: "response.created", response: { id: "resp_1", output: [] } };
    yield { type: "response.output_text.delta", delta: "hello " };
    yield { type: "response.output_text.delta", delta: "codex" };
    yield { type: "response.completed", response: { id: "resp_1", output_text: "hello codex" } };
  }

  const deltas = [];
  const response = await collectStreamingResponse(fakeStream(), (delta) => deltas.push(delta));

  assert.deepEqual(deltas, ["hello ", "codex"]);
  assert.deepEqual(response, { id: "resp_1", output_text: "hello codex" });
});

test("collectStreamingResponse fails closed when the stream has no completed response", async () => {
  async function* brokenStream() {
    yield { type: "response.created", response: { id: "resp_1", output: [] } };
    yield { type: "response.output_text.delta", delta: "partial" };
  }

  await assert.rejects(
    () => collectStreamingResponse(brokenStream(), () => undefined),
    /ended without a completed response/,
  );
});

test("isRecoverableSessionResumeError catches stale and orphaned Responses sessions", () => {
  assert.equal(isRecoverableSessionResumeError("previous_response_id not found"), true);
  assert.equal(
    isRecoverableSessionResumeError("400 No tool output found for function call call_yX7dn0aGDFEHe1yjG9CjRZJc."),
    true,
  );
  assert.equal(isRecoverableSessionResumeError("quota exceeded"), false);
});

test("responseFunctionCalls identifies pending Responses function calls", () => {
  const response = {
    output: [
      { type: "reasoning", summary: [] },
      { type: "function_call", name: "run_shell_command", call_id: "call_123" },
      { type: "message", content: [] },
    ],
  };
  assert.equal(responseFunctionCalls(response).length, 1);
  assert.deepEqual(responseFunctionCalls({ output: [{ type: "message", content: [] }] }), []);
});

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

test("handleA2A returns the message response shape harness and ww extract", async () => {
  const response = await handleA2A({
    jsonrpc: "2.0",
    id: "abc",
    method: "message/send",
    params: {
      message: {
        role: "user",
        contextId: "ctx-1",
        messageId: "msg-1",
        parts: [{ kind: "text", text: "hello codex" }],
      },
    },
  });
  assert.equal(response.status, 200);
  assert.equal(response.body.jsonrpc, "2.0");
  assert.equal(response.body.id, "abc");
  assert.equal(response.body.result.kind, "message");
  assert.equal(response.body.result.role, "agent");
  assert.equal(response.body.result.contextId, "ctx-1");
  assert.match(response.body.result.parts[0].text, /codex backend scaffold/i);
});

test("extractRequestMetadata falls back to metadata.session_id for first-turn context", () => {
  const extracted = extractRequestMetadata({
    jsonrpc: "2.0",
    id: "session",
    method: "message/send",
    params: {
      message: {
        role: "user",
        metadata: { session_id: "stable-session" },
        parts: [{ kind: "text", text: "hello codex" }],
      },
    },
  });
  assert.equal(extracted.contextId, "stable-session");
  assert.equal(extracted.metadata.session_id, "stable-session");
});

test("handleA2A rejects unsupported methods as JSON-RPC errors", async () => {
  const response = await handleA2A({
    jsonrpc: "2.0",
    id: "bad",
    method: "message/stream",
    params: {},
  });
  assert.equal(response.status, 200);
  assert.equal(response.body.error.code, -32601);
});

test("handleA2A honors LOG_REDACT for conversation logs", async () => {
  await handleA2A({
    jsonrpc: "2.0",
    id: "redact",
    method: "message/send",
    params: {
      message: {
        contextId: "ctx-redact",
        messageId: "msg-redact",
        parts: [{ kind: "text", text: "please do not log this" }],
      },
    },
  });
  const entries = fs
    .readFileSync(process.env.CONVERSATION_LOG, "utf8")
    .trim()
    .split("\n")
    .map((line) => JSON.parse(line));
  const turns = entries.filter((item) => item.message_id === "msg-redact");
  assert.equal(turns.length, 2);
  assert.deepEqual(
    turns.map((turn) => turn.role),
    ["user", "agent"],
  );
  assert.ok(turns.every((turn) => turn.ts && turn.timestamp === turn.ts));
  assert.equal(turns[0].text, "[REDACTED]");
  assert.equal(turns[0].prompt, "[REDACTED]");
  assert.equal(turns[1].text, "[REDACTED]");
  assert.equal(turns[1].response, "[REDACTED]");
});

test("maxOutputTokensForRequest accepts positive max_output_tokens metadata", () => {
  assert.equal(maxOutputTokensForRequest({ max_output_tokens: "128" }), 128);
  assert.equal(maxOutputTokensForRequest({ maxOutputTokens: 256 }), 256);
  assert.equal(maxOutputTokensForRequest({ max_output_tokens: "0" }), undefined);
  assert.equal(maxOutputTokensForRequest({ max_output_tokens: "not-a-number" }), undefined);
});

test("maxTokensForRequest accepts positive max_tokens metadata", () => {
  assert.equal(maxTokensForRequest({ max_tokens: "2048" }), 2048);
  assert.equal(maxTokensForRequest({ maxTokens: 4096 }), 4096);
  assert.equal(maxTokensForRequest({ max_tokens: "0" }), undefined);
  assert.equal(maxTokensForRequest({ max_tokens: "not-a-number" }), undefined);
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
  assert.match(body, /agent="/);
  assert.match(body, /backend="codex"/);
});
