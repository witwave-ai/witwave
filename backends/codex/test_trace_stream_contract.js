import assert from "node:assert/strict";
import fs from "node:fs";
import http from "node:http";
import os from "node:os";
import path from "node:path";
import test from "node:test";

const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "witwave-codex-trace-test-"));
process.env.CONVERSATION_LOG = path.join(tmp, "conversation.jsonl");
process.env.TRACE_LOG = path.join(tmp, "tool-activity.jsonl");
process.env.CODEX_STUB_MODE = "true";
process.env.CONVERSATIONS_AUTH_DISABLED = "true";
process.env.LOG_REDACT = "true";
process.env.CODEX_CONFIG_TOML = path.join(tmp, "config.toml");
process.env.CODEX_AGENT_MD = path.join(tmp, "AGENTS.md");
fs.writeFileSync(process.env.CODEX_CONFIG_TOML, 'model = "gpt-5.5"\nreasoning_effort = "xhigh"\n', "utf8");
fs.writeFileSync(process.env.CODEX_AGENT_MD, "# Trace contract test identity\n", "utf8");

const { handleA2A, handleRequest } = await import("./main.js");

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

async function openSessionStream(port, sessionId, headers = {}) {
  return await new Promise((resolve, reject) => {
    const req = http.get(`http://127.0.0.1:${port}/api/sessions/${sessionId}/stream`, { headers }, (res) => {
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
