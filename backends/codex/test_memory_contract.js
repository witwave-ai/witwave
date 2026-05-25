import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";

const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "witwave-codex-memory-test-"));
process.env.CONVERSATION_LOG = path.join(tmp, "conversation.jsonl");
process.env.TRACE_LOG = path.join(tmp, "tool-activity.jsonl");
process.env.CODEX_MEMORY_ROOT = path.join(tmp, "memory");
process.env.CODEX_STUB_MODE = "true";
process.env.CONVERSATIONS_AUTH_DISABLED = "true";
process.env.OTEL_IN_MEMORY_SPANS = "0";
process.env.CODEX_CONFIG_TOML = path.join(tmp, "config.toml");
process.env.CODEX_AGENT_MD = path.join(tmp, "AGENTS.md");
fs.writeFileSync(process.env.CODEX_CONFIG_TOML, 'model = "gpt-5.5"\nreasoning_effort = "xhigh"\n', "utf8");
fs.writeFileSync(process.env.CODEX_AGENT_MD, "# Memory contract test identity\n", "utf8");

const { resolveMemoryPath, runMemoryTool } = await import("./main.js");

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
