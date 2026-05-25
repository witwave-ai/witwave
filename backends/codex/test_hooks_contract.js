import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";

const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "witwave-codex-hooks-test-"));
process.env.CONVERSATION_LOG = path.join(tmp, "conversation.jsonl");
process.env.TRACE_LOG = path.join(tmp, "tool-activity.jsonl");
process.env.CODEX_MEMORY_ROOT = path.join(tmp, "memory");
process.env.CODEX_STUB_MODE = "true";
process.env.CONVERSATIONS_AUTH_DISABLED = "true";
process.env.OTEL_IN_MEMORY_SPANS = "0";
process.env.CODEX_CONFIG_TOML = path.join(tmp, "config.toml");
process.env.HOOKS_CONFIG_PATH = path.join(tmp, "hooks.yaml");
process.env.HOOKS_BASELINE_ENABLED = "true";
process.env.CODEX_AGENT_MD = path.join(tmp, "AGENTS.md");
fs.writeFileSync(process.env.CODEX_CONFIG_TOML, 'model = "gpt-5.5"\nreasoning_effort = "xhigh"\n', "utf8");
fs.writeFileSync(process.env.CODEX_AGENT_MD, "# Hooks contract test identity\n", "utf8");

const { evaluatePreToolUse, handleFunctionCall, isShellCommandAllowed, loadHookExtensionRulesFromText, renderMetrics } =
  await import("./main.js");

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

  fs.writeFileSync(
    process.env.HOOKS_CONFIG_PATH,
    `
extensions:
  - name: deny-reloaded-memory-marker
    tool: write_memory_file
    deny_if_match: "DO_NOT_WRITE_AFTER_RELOAD"
    reason: "test extension reload deny"
`,
    "utf8",
  );
  const reloaded = evaluatePreToolUse("write_memory_file", {
    path: "platform-health/hook.md",
    content: "DO_NOT_WRITE_AFTER_RELOAD",
  });
  assert.equal(reloaded.decision, "deny");
  assert.equal(reloaded.rule.name, "deny-reloaded-memory-marker");

  const body = renderMetrics();
  assert.match(
    body,
    /backend_hooks_denials_total\{.*tool="write_memory_file".*source="extension".*rule="deny-memory-marker".*\} 1/,
  );
  assert.match(body, /backend_hooks_evaluations_total\{.*tool="write_memory_file".*decision="deny".*\} 1/);
  assert.match(body, /backend_hooks_enforcement_mode\{.*backend="codex".*\} 1/);
  assert.match(body, /backend_hooks_active_rules\{.*source="extension".*\} 1/);
  assert.match(body, /backend_sdk_tool_calls_total\{.*tool="write_memory_file".*\} 1/);
  assert.match(body, /backend_sdk_tool_errors_total\{.*tool="write_memory_file".*\} 1/);
  assert.match(body, /backend_sdk_tool_duration_seconds_count\{.*tool="write_memory_file".*\} 1/);
  assert.match(body, /backend_sdk_tool_call_input_size_bytes_count\{.*tool="write_memory_file".*\} 1/);
  assert.match(body, /backend_sdk_tool_result_size_bytes_count\{.*tool="write_memory_file".*\} 1/);
  assert.match(body, /backend_tool_audit_entries_total\{.*tool="write_memory_file".*\} [1-9]/);
  assert.match(body, /backend_tool_audit_bytes_per_entry_count\{.*tool="write_memory_file".*\} [1-9]/);
  assert.match(body, /backend_hooks_config_reloads_total\{.*backend="codex".*\} [1-9]/);
});
