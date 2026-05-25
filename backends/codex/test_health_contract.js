import assert from "node:assert/strict";
import fs from "node:fs";
import http from "node:http";
import os from "node:os";
import path from "node:path";
import test from "node:test";

const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "witwave-codex-health-test-"));
process.env.CONVERSATION_LOG = path.join(tmp, "conversation.jsonl");
process.env.TRACE_LOG = path.join(tmp, "tool-activity.jsonl");
process.env.CODEX_STUB_MODE = "true";
process.env.CONVERSATIONS_AUTH_DISABLED = "true";
process.env.OTEL_IN_MEMORY_SPANS = "0";
process.env.CODEX_CONFIG_TOML = path.join(tmp, "config.toml");
process.env.CODEX_AGENT_MD = path.join(tmp, "AGENTS.md");
fs.writeFileSync(process.env.CODEX_CONFIG_TOML, 'model = "gpt-5.5"\nreasoning_effort = "xhigh"\n', "utf8");
fs.writeFileSync(process.env.CODEX_AGENT_MD, "# Health contract test identity\n", "utf8");

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

async function getJson(port, requestPath) {
  return await new Promise((resolve, reject) => {
    const req = http.get(`http://127.0.0.1:${port}${requestPath}`, (res) => {
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

test("health routes split liveness, readiness, and startup semantics", async () => {
  await withTestServer(async (port) => {
    const live = await getJson(port, "/health");
    assert.equal(live.status, 200);
    assert.equal(live.body.status, "starting");
    assert.equal(live.body.hooks_enforcement_mode, "enforcing");

    const liveAlias = await getJson(port, "/health/live");
    assert.equal(liveAlias.status, 200);

    const ready = await getJson(port, "/health/ready");
    assert.equal(ready.status, 503);
    assert.equal(ready.body.status, "starting");

    const startup = await getJson(port, "/health/start");
    assert.equal(startup.status, 503);
    assert.equal(startup.body.status, "starting");
  });
});
