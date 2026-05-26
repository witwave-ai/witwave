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
  isRecoverableSessionResumeError,
  loadCodexConfigFromText,
  mcpFunctionName,
  mcpServerEntriesFromConfig,
  mcpToolResultText,
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
  assert.match(renderMetrics(), /backend_mcp_command_rejected_total\{.*reason="unsupported_stdio".*\} 1/);
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
default_max_tokens = 30000

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
  assert.equal(config.runtime.default_max_tokens, 30000);
  assert.equal(config.paths.memory_root, "/workspaces/witwave-self/memory/agents/mira");
  assert.equal(config.paths.mcp_config, "/home/agent/.codex/mcp.json");
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
