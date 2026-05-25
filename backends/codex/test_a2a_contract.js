import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";

const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "witwave-codex-a2a-test-"));
process.env.CONVERSATION_LOG = path.join(tmp, "conversation.jsonl");
process.env.TRACE_LOG = path.join(tmp, "tool-activity.jsonl");
process.env.CODEX_STUB_MODE = "true";
process.env.CODEX_MEMORY_ROOT = path.join(tmp, "memory");
process.env.CONVERSATIONS_AUTH_DISABLED = "true";
process.env.LOG_REDACT = "true";
process.env.CODEX_CONFIG_TOML = path.join(tmp, "config.toml");
process.env.CODEX_AGENT_MD = path.join(tmp, "AGENTS.md");
fs.writeFileSync(process.env.CODEX_AGENT_MD, "# A2A contract test identity\n", "utf8");
fs.writeFileSync(process.env.CODEX_CONFIG_TOML, 'model = "gpt-5.5"\nreasoning_effort = "xhigh"\n', "utf8");

const { extractRequestMetadata, extractPrompt, handleA2A, maxOutputTokensForRequest, maxTokensForRequest } =
  await import("./main.js");

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
