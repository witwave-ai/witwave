import assert from "node:assert/strict";
import fs from "node:fs";
import http from "node:http";
import os from "node:os";
import path from "node:path";
import test from "node:test";

const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "witwave-codex-mcp-test-"));
process.env.CONVERSATION_LOG = path.join(tmp, "conversation.jsonl");
process.env.TRACE_LOG = path.join(tmp, "tool-activity.jsonl");
process.env.CODEX_STUB_MODE = "true";
process.env.CONVERSATIONS_AUTH_DISABLED = "true";
process.env.OTEL_IN_MEMORY_SPANS = "0";
process.env.CODEX_CONFIG_TOML = path.join(tmp, "config.toml");
process.env.CODEX_AGENT_MD = path.join(tmp, "AGENTS.md");
fs.writeFileSync(process.env.CODEX_CONFIG_TOML, 'model = "gpt-5.5"\nreasoning_effort = "xhigh"\n', "utf8");
fs.writeFileSync(process.env.CODEX_AGENT_MD, "# MCP contract test identity\n", "utf8");

const { handleRequest, renderMetrics } = await import("./main.js");

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

async function postJson(port, requestPath, payload, headers = {}) {
  const body = typeof payload === "string" ? payload : JSON.stringify(payload);
  return await new Promise((resolve, reject) => {
    const req = http.request(
      `http://127.0.0.1:${port}${requestPath}`,
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
