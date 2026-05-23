import assert from "node:assert/strict";
import fs from "node:fs";
import http from "node:http";
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
  handleFunctionCall,
  handleRequest,
  isShellCommandAllowed,
  evaluatePreToolUse,
  loadCodexConfigFromText,
  loadHookExtensionRulesFromText,
  mcpFunctionName,
  mcpServerEntriesFromConfig,
  mcpToolResultText,
  maxOutputTokensForRequest,
  maxTokensForRequest,
  publishSessionChunk,
  renderMetrics,
  resolveMemoryPath,
  runMemoryTool,
} = await import("./main.js");

async function withTestServer(fn) {
  const server = http.createServer((req, res) => {
    handleRequest(req, res).catch((error) => {
      res.writeHead(500, { "content-type": "application/json" });
      res.end(JSON.stringify({ error: error?.message || String(error) }));
    });
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  try {
    return await fn(server.address().port);
  } finally {
    server.closeAllConnections?.();
    await new Promise((resolve) => server.close(resolve));
  }
}

async function postJson(port, path, payload, headers = {}) {
  const body = typeof payload === "string" ? payload : JSON.stringify(payload);
  return await new Promise((resolve, reject) => {
    const req = http.request(
      `http://127.0.0.1:${port}${path}`,
      {
        method: "POST",
        headers: {
          "content-type": "application/json",
          "content-length": Buffer.byteLength(body),
          ...headers,
        },
      },
      (res) => {
        let data = "";
        res.setEncoding("utf8");
        res.on("data", (chunk) => {
          data += chunk;
        });
        res.on("end", () => {
          resolve({ status: res.statusCode, body: data ? JSON.parse(data) : undefined });
        });
      },
    );
    req.on("error", reject);
    req.end(body);
  });
}

async function getJson(port, path, headers = {}) {
  return await new Promise((resolve, reject) => {
    const req = http.get(`http://127.0.0.1:${port}${path}`, { headers }, (res) => {
      let data = "";
      res.setEncoding("utf8");
      res.on("data", (chunk) => {
        data += chunk;
      });
      res.on("end", () => {
        resolve({ status: res.statusCode, body: data ? JSON.parse(data) : undefined });
      });
    });
    req.on("error", reject);
  });
}

async function openSessionStream(port, sessionId, headers = {}) {
  return await new Promise((resolve, reject) => {
    const req = http.get(`http://127.0.0.1:${port}/api/sessions/${sessionId}/stream`, { headers }, (res) => {
      req.on("error", () => {
        // Tests intentionally close long-lived SSE requests.
      });
      resolve({ req, res });
    });
    req.on("error", reject);
  });
}

async function readResponseBody(res) {
  return await new Promise((resolve) => {
    let body = "";
    res.setEncoding("utf8");
    res.on("data", (chunk) => {
      body += chunk;
    });
    res.on("end", () => resolve(body));
  });
}

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

test("A2A calls emit OpenTelemetry spans visible through /api/traces", async () => {
  const traceId = "22222222222222222222222222222222";
  await handleA2A({
    jsonrpc: "2.0",
    id: "otel",
    method: "message/send",
    params: {
      message: {
        role: "user",
        metadata: {
          session_id: "otel-session",
          traceparent: `00-${traceId}-3333333333333333-01`,
        },
        parts: [{ kind: "text", text: "trace me" }],
      },
    },
  });

  await withTestServer(async (port) => {
    const result = await getJson(port, `/api/traces/${traceId}`);
    assert.equal(result.status, 200);
    assert.equal(result.body.data[0].traceID, traceId);
    const backendSpan = result.body.data[0].spans.find((span) => span.operationName === "backend.a2a.execute");
    assert.ok(backendSpan, "expected backend.a2a.execute span");
    const backendTags = Object.fromEntries(backendSpan.tags.map((tag) => [tag.key, tag.value]));
    assert.equal(backendTags["llm.request.reasoning_effort"], "xhigh");
  });
});

test("session stream endpoint publishes user and final assistant chunks", async () => {
  const server = http.createServer((req, res) => {
    handleRequest(req, res).catch((error) => {
      res.writeHead(500, { "content-type": "application/json" });
      res.end(JSON.stringify({ error: error?.message || String(error) }));
    });
  });

  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  try {
    const { port } = server.address();
    const sessionId = "00000000-0000-4000-8000-000000000001";
    const received = [];
    const streamReq = http.get(`http://127.0.0.1:${port}/api/sessions/${sessionId}/stream`);
    let streamReadyResolve;
    const streamReady = new Promise((resolve) => {
      streamReadyResolve = resolve;
    });
    streamReq.on("error", () => {
      // The test closes the long-lived SSE request once both expected chunks arrive.
    });

    streamReq.on("response", (res) => {
      streamReadyResolve();
      res.setEncoding("utf8");
      let buffer = "";
      res.on("data", (chunk) => {
        buffer += chunk;
        const frames = buffer.split("\n\n");
        buffer = frames.pop() || "";
        for (const frame of frames) {
          const dataLine = frame.split("\n").find((line) => line.startsWith("data: "));
          if (dataLine) {
            received.push(JSON.parse(dataLine.slice("data: ".length)));
          }
        }
        if (received.length >= 2) {
          streamReq.destroy();
        }
      });
    });

    await streamReady;
    await handleA2A({
      jsonrpc: "2.0",
      id: "stream",
      method: "message/send",
      params: {
        message: {
          role: "user",
          metadata: { session_id: sessionId },
          parts: [{ kind: "text", text: "stream hello" }],
        },
      },
    });

    await new Promise((resolve, reject) => {
      const timeout = setTimeout(() => reject(new Error("timed out waiting for session stream chunks")), 1000);
      const poll = () => {
        if (received.length >= 2) {
          clearTimeout(timeout);
          resolve();
          return;
        }
        setTimeout(poll, 25);
      };
      poll();
    });

    assert.equal(received[0].type, "conversation.chunk");
    assert.equal(received[0].payload.role, "user");
    assert.equal(received[0].payload.content, "stream hello");
    assert.equal(received[1].payload.role, "assistant");
    assert.match(received[1].payload.content, /codex backend scaffold/i);
  } finally {
    server.closeAllConnections?.();
    await new Promise((resolve) => server.close(resolve));
  }
});

test("session stream caps concurrent streams per caller and releases on close", async () => {
  await withTestServer(async (port) => {
    const opened = [];
    const headers = { Authorization: "Bearer cap-test" };
    try {
      for (let i = 0; i < 8; i += 1) {
        const sessionId = `00000000-0000-4000-8000-00000000010${i}`;
        const stream = await openSessionStream(port, sessionId, headers);
        assert.equal(stream.res.statusCode, 200);
        opened.push(stream);
      }

      const capped = await openSessionStream(port, "00000000-0000-4000-8000-000000000199", headers);
      assert.equal(capped.res.statusCode, 429);
      assert.match(await readResponseBody(capped.res), /too many concurrent streams/);
    } finally {
      for (const { req, res } of opened) {
        req.destroy();
        res.destroy();
      }
    }

    await new Promise((resolve) => setTimeout(resolve, 25));
    const released = await openSessionStream(port, "00000000-0000-4000-8000-0000000001aa", headers);
    assert.equal(released.res.statusCode, 200);
    released.req.destroy();
    released.res.destroy();
  });
});

test("MCP tools/list advertises the backend-neutral ask_agent tool name", async () => {
  await withTestServer(async (port) => {
    const { body: result } = await postJson(port, "/mcp", {
      jsonrpc: "2.0",
      id: "tools",
      method: "tools/list",
    });

    const tool = result.result.tools[0];
    assert.equal(tool.name, "ask_agent");
    assert.ok(tool.inputSchema.properties.session_id);
    assert.ok(tool.inputSchema.properties.max_tokens);
    const metricsBody = renderMetrics();
    assert.match(metricsBody, /backend_mcp_requests_total\{.*method="tools\/list".*status="ok"/);
    assert.match(metricsBody, /backend_mcp_request_duration_seconds_count\{.*method="tools\/list"/);
  });
});

test("MCP initialize negotiates supported protocol version", async () => {
  await withTestServer(async (port) => {
    const { body: result } = await postJson(port, "/mcp", {
      jsonrpc: "2.0",
      id: "init",
      method: "initialize",
      params: { protocolVersion: "2024-11-05" },
    });
    assert.equal(result.result.protocolVersion, "2024-11-05");
    assert.equal(result.result.capabilities.tools.listChanged, false);
  });
});

test("MCP tools/call rejects missing prompt and oversized bodies", async () => {
  await withTestServer(async (port) => {
    const missingPrompt = await postJson(port, "/mcp", {
      jsonrpc: "2.0",
      id: "missing",
      method: "tools/call",
      params: { name: "ask_agent", arguments: {} },
    });
    assert.equal(missingPrompt.status, 200);
    assert.equal(missingPrompt.body.error.code, -32602);

    const oversized = await postJson(port, "/mcp", "", {
      "content-length": String(4 * 1024 * 1024 + 1),
    });
    assert.equal(oversized.status, 413);
    assert.equal(oversized.body.error.message, "body too large");
  });
});

test("MCP tools/call carries inbound traceparent into trace records", async () => {
  const traceId = "11111111111111111111111111111111";
  await withTestServer(async (port) => {
    const result = await postJson(
      port,
      "/mcp",
      {
        jsonrpc: "2.0",
        id: "trace",
        method: "tools/call",
        params: { name: "ask_agent", arguments: { prompt: "trace me" } },
      },
      { traceparent: `00-${traceId}-2222222222222222-01` },
    );
    assert.equal(result.status, 200);
  });

  const entries = fs
    .readFileSync(process.env.TRACE_LOG, "utf8")
    .trim()
    .split("\n")
    .map((line) => JSON.parse(line));
  assert.ok(entries.some((entry) => entry.endpoint === "/mcp" && entry.trace_id === traceId));
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

test("evaluatePreToolUse applies Codex aliases for shared baseline hook rules", () => {
  const denied = evaluatePreToolUse("run_shell_command", { command: "rm -rf /" });
  assert.equal(denied.decision, "deny");
  assert.equal(denied.rule.name, "baseline-rm-rf-root");

  const allowed = evaluatePreToolUse("run_shell_command", { command: "kubectl get pods -n witwave-self" });
  assert.equal(allowed.decision, "allow");
});

test("loadHookExtensionRulesFromText parses hooks.yaml extension rules", () => {
  const rules = loadHookExtensionRulesFromText(`
extensions:
  - name: deny-memory-marker
    tool: write_memory_file
    deny_if_match: "DO_NOT_WRITE"
    reason: "test extension deny"
`);
  assert.equal(rules.length, 1);
  assert.equal(rules[0].name, "deny-memory-marker");
  assert.equal(rules[0].action, "deny");
});

test("handleFunctionCall gates Codex function tools through hooks.yaml", async () => {
  fs.writeFileSync(
    process.env.HOOKS_CONFIG_PATH,
    `
extensions:
  - name: deny-memory-marker
    tool: write_memory_file
    deny_if_match: "DO_NOT_WRITE"
    reason: "test extension deny"
`,
    "utf8",
  );

  const result = await handleFunctionCall(
    {
      name: "write_memory_file",
      call_id: "call-deny-memory-marker",
      arguments: JSON.stringify({ path: "platform-health/hook.md", content: "DO_NOT_WRITE" }),
    },
    "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    { sessionId: "session-hook-trace", model: "gpt-5.5" },
  );

  assert.equal(result.ok, false);
  assert.equal(result.refused, true);
  assert.equal(result.hook_denied, true);
  assert.equal(result.rule, "deny-memory-marker");

  const traceRows = fs
    .readFileSync(process.env.TRACE_LOG, "utf8")
    .trim()
    .split("\n")
    .map((line) => JSON.parse(line));
  const useRow = traceRows.find((row) => row.event_type === "tool_use" && row.id === "call-deny-memory-marker");
  const resultRow = traceRows.find(
    (row) => row.event_type === "tool_result" && row.tool_use_id === "call-deny-memory-marker",
  );
  const auditRow = traceRows.find(
    (row) => row.event_type === "tool_audit" && row.tool_use_id === "call-deny-memory-marker",
  );
  assert.equal(useRow.name, "write_memory_file");
  assert.equal(useRow.session_id, "session-hook-trace");
  assert.equal(useRow.model, "gpt-5.5");
  assert.equal(resultRow.is_error, true);
  assert.equal(auditRow.tool_name, "write_memory_file");
  assert.equal(auditRow.decision, "deny");

  const body = renderMetrics();
  assert.match(body, /backend_hooks_denials_total\{.*tool="write_memory_file".*source="extension".*rule="deny-memory-marker".*\} 1/);
  assert.match(body, /backend_hooks_evaluations_total\{.*tool="write_memory_file".*decision="deny".*\} 1/);
  assert.match(body, /backend_hooks_active_rules\{.*source="extension".*\} 1/);
  assert.match(body, /backend_sdk_tool_calls_total\{.*tool="write_memory_file".*\} 1/);
  assert.match(body, /backend_sdk_tool_errors_total\{.*tool="write_memory_file".*\} 1/);
  assert.match(body, /backend_sdk_tool_duration_seconds_count\{.*tool="write_memory_file".*\} 1/);
  assert.match(body, /backend_sdk_tool_call_input_size_bytes_count\{.*tool="write_memory_file".*\} 1/);
  assert.match(body, /backend_sdk_tool_result_size_bytes_count\{.*tool="write_memory_file".*\} 1/);
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
