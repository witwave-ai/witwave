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
process.env.LOG_REDACT = "true";

const {
  buildAgentCard,
  extractRequestMetadata,
  extractPrompt,
  handleA2A,
  isShellCommandAllowed,
  maxOutputTokensForRequest,
  maxTokensForRequest,
  renderMetrics,
  resolveMemoryPath,
  runMemoryTool,
} = await import("./main.js");

test("buildAgentCard advertises a non-streaming Codex backend", () => {
  const card = buildAgentCard();
  assert.equal(card.name, process.env.AGENT_NAME || "codex");
  assert.equal(card.capabilities.streaming, false);
  assert.equal(card.skills[0].id, "general");
  assert.deepEqual(card.defaultInputModes, ["text/plain"]);
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
  const entry = entries.find((item) => item.message_id === "msg-redact");
  assert.equal(entry.prompt, "[REDACTED]");
  assert.equal(entry.response, "[REDACTED]");
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

test("isShellCommandAllowed permits read-only diagnostics and rejects risky commands", () => {
  assert.equal(isShellCommandAllowed("kubectl get pods -n witwave-self").ok, true);
  assert.equal(isShellCommandAllowed("ww agent status --namespace witwave-self").ok, true);
  assert.equal(isShellCommandAllowed("ww team status --namespace witwave-self --since 1h").ok, true);
  assert.equal(isShellCommandAllowed("ww version").ok, true);
  assert.equal(isShellCommandAllowed("command -v kubectl").ok, true);
  assert.equal(isShellCommandAllowed("kubectl get secrets -n witwave-self").ok, false);
  assert.equal(isShellCommandAllowed("kubectl get pods; cat .agents/self/team.sops.env").ok, false);
  assert.equal(isShellCommandAllowed("printenv OPENAI_API_KEY").ok, false);
});

test("resolveMemoryPath keeps memory tools inside the configured root", () => {
  const resolved = resolveMemoryPath("platform-health/baseline.md");
  assert.equal(resolved, path.join(process.env.CODEX_MEMORY_ROOT, "platform-health", "baseline.md"));
  assert.throws(() => resolveMemoryPath("../outside.md"), /escapes/);
  assert.throws(() => resolveMemoryPath("/tmp/outside.md"), /relative/);
});

test("runMemoryTool can write, append, read, and list memory files", async () => {
  const write = await runMemoryTool("write_memory_file", {
    path: "platform-health/baseline.md",
    content: "# Baseline\n",
  });
  assert.equal(write.ok, true);
  assert.equal(write.path, "platform-health/baseline.md");

  const append = await runMemoryTool("append_memory_file", {
    path: "platform-health/baseline.md",
    content: "- restart pattern: normal\n",
  });
  assert.equal(append.ok, true);

  const read = await runMemoryTool("read_memory_file", { path: "platform-health/baseline.md" });
  assert.equal(read.ok, true);
  assert.match(read.content, /restart pattern/);

  const listed = await runMemoryTool("list_memory_files", { path: "platform-health" });
  assert.equal(listed.ok, true);
  assert.deepEqual(listed.entries, ["platform-health/baseline.md"]);
});

test("runMemoryTool refuses raw credential-shaped content", async () => {
  const result = await runMemoryTool("write_memory_file", {
    path: "platform-health/leak.md",
    content: "sk-testthislookssecret0000000000000000",
  });
  assert.equal(result.ok, false);
  assert.match(result.error, /credential/);
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
  assert.match(body, /backend_a2a_requests_total/);
  assert.match(body, /backend_prompt_length_bytes_count/);
  assert.match(body, /backend_active_sessions/);
  assert.match(body, /backend_budget_exceeded_total/);
  assert.match(body, /agent="/);
  assert.match(body, /backend="codex"/);
});
