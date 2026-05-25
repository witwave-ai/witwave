import assert from "node:assert/strict";
import fs from "node:fs";
import http from "node:http";
import os from "node:os";
import path from "node:path";
import test from "node:test";

const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "witwave-codex-auth-test-"));
process.env.CONVERSATION_LOG = path.join(tmp, "conversation.jsonl");
process.env.TRACE_LOG = path.join(tmp, "tool-activity.jsonl");
process.env.CODEX_STUB_MODE = "true";
process.env.CONVERSATIONS_AUTH_TOKEN = "codex-auth-test-token";
process.env.CONVERSATIONS_AUTH_DISABLED = "";
process.env.OTEL_IN_MEMORY_SPANS = "0";
process.env.CODEX_CONFIG_TOML = path.join(tmp, "config.toml");
process.env.CODEX_AGENT_MD = path.join(tmp, "AGENTS.md");
fs.writeFileSync(process.env.CODEX_CONFIG_TOML, 'model = "gpt-5.5"\nreasoning_effort = "xhigh"\n', "utf8");
fs.writeFileSync(process.env.CODEX_AGENT_MD, "# Auth contract test identity\n", "utf8");
fs.writeFileSync(
  process.env.CONVERSATION_LOG,
  JSON.stringify({ timestamp: "2026-05-25T00:00:00.000Z", role: "user", text: "hello" }) + "\n",
  "utf8",
);
fs.writeFileSync(
  process.env.TRACE_LOG,
  JSON.stringify({ timestamp: "2026-05-25T00:00:00.000Z", endpoint: "/trace", status: "ok" }) + "\n",
  "utf8",
);

const { handleRequest } = await import("./main.js");

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

async function getJson(port, requestPath, headers = {}) {
  return await new Promise((resolve, reject) => {
    const req = http.get(`http://127.0.0.1:${port}${requestPath}`, { headers }, (res) => {
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

const authHeaders = { Authorization: "Bearer codex-auth-test-token" };

test("protected inspection endpoints require the configured bearer token", async () => {
  await withTestServer(async (port) => {
    const missing = await getJson(port, "/conversations");
    assert.equal(missing.status, 401);
    assert.match(missing.body.error, /Authorization: Bearer/);

    const wrong = await getJson(port, "/trace", { Authorization: "Bearer wrong" });
    assert.equal(wrong.status, 401);

    const conversations = await getJson(port, "/conversations", authHeaders);
    assert.equal(conversations.status, 200);
    assert.equal(conversations.body.length, 1);

    const trace = await getJson(port, "/trace", authHeaders);
    assert.equal(trace.status, 200);
    assert.equal(trace.body.length, 1);

    const traces = await getJson(port, "/api/traces", authHeaders);
    assert.equal(traces.status, 200);
    assert.deepEqual(Object.keys(traces.body).sort(), ["data", "limit", "offset", "total"]);
  });
});

test("MCP endpoint shares the protected endpoint auth posture", async () => {
  await withTestServer(async (port) => {
    const missing = await postJson(port, "/mcp", {
      jsonrpc: "2.0",
      id: "missing-auth",
      method: "tools/list",
    });
    assert.equal(missing.status, 401);

    const listed = await postJson(
      port,
      "/mcp",
      {
        jsonrpc: "2.0",
        id: "list-tools",
        method: "tools/list",
      },
      authHeaders,
    );
    assert.equal(listed.status, 200);
    assert.equal(listed.body.result.tools[0].name, "ask_agent");
  });
});
