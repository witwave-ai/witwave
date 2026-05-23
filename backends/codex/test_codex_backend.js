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

const {
  buildAgentCard,
  deriveSessionId,
  extractRequestMetadata,
  extractPrompt,
  handleA2A,
  handleRequest,
  isShellCommandAllowed,
  maxOutputTokensForRequest,
  maxTokensForRequest,
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

test("deriveSessionId binds the same raw session to different callers when secret is set", () => {
  const raw = "shared-session";
  const alice = deriveSessionId(raw, "alice", "secret");
  const aliceAgain = deriveSessionId(raw, "alice", "secret");
  const bob = deriveSessionId(raw, "bob", "secret");
  assert.equal(alice, aliceAgain);
  assert.notEqual(alice, bob);
  assert.match(alice, /^[0-9a-f-]{36}$/);
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
          const dataLine = frame
            .split("\n")
            .find((line) => line.startsWith("data: "));
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
