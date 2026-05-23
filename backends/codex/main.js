import crypto from "node:crypto";
import { spawn } from "node:child_process";
import fs from "node:fs";
import http from "node:http";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { Client as McpClient } from "@modelcontextprotocol/sdk/client/index.js";
import { SSEClientTransport } from "@modelcontextprotocol/sdk/client/sse.js";
import { StreamableHTTPClientTransport } from "@modelcontextprotocol/sdk/client/streamableHttp.js";
import { extractOtelContext, getInMemoryTraces, initOtelIfEnabled, runWithSpan } from "./otel.js";
import YAML from "yaml";

const STARTED_AT = new Date();
const START_MONO = performance.now();

const AGENT_NAME = process.env.AGENT_NAME || "codex";
const AGENT_HOST = process.env.AGENT_HOST || "0.0.0.0";
const BACKEND_PORT = Number.parseInt(process.env.BACKEND_PORT || "8000", 10);
const AGENT_URL = process.env.AGENT_URL || `http://localhost:${BACKEND_PORT}/`;
const AGENT_VERSION = process.env.AGENT_VERSION || "0.1.0";
const AGENT_OWNER = process.env.AGENT_OWNER || AGENT_NAME;
const AGENT_ID = process.env.AGENT_ID || process.env.HOSTNAME || "codex";
const BACKEND_ID = "codex";
const CODEX_CONFIG_TOML = process.env.CODEX_CONFIG_TOML || "/home/agent/.codex/config.toml";
const CODEX_CONFIG = loadCodexConfig(CODEX_CONFIG_TOML);

initOtelIfEnabled({
  serviceName: process.env.OTEL_SERVICE_NAME || `${BACKEND_ID}-${AGENT_OWNER}`,
  resourceAttributes: {
    agent: AGENT_OWNER,
    agent_id: AGENT_ID,
    backend: BACKEND_ID,
  },
});

const CONVERSATION_LOG = process.env.CONVERSATION_LOG || "/home/agent/logs/conversation.jsonl";
const TRACE_LOG = process.env.TRACE_LOG || "/home/agent/logs/tool-activity.jsonl";
const CODEX_AGENT_MD = process.env.CODEX_AGENT_MD || "/home/agent/.codex/AGENTS.md";
const CODEX_MODEL = configString([["model"], ["model", "name"]], ["CODEX_MODEL", "OPENAI_MODEL"], "gpt-5.5");
const CODEX_REASONING_EFFORT = configString(
  [["reasoning_effort"], ["model", "reasoning_effort"], ["model", "effort"]],
  ["CODEX_REASONING_EFFORT"],
  "xhigh",
);
const CODEX_RESPONSES_STREAMING =
  process.env.CODEX_RESPONSES_STREAMING === undefined ? true : parseBool(process.env.CODEX_RESPONSES_STREAMING);
const MAX_PROMPT_BYTES = Number.parseInt(process.env.MAX_PROMPT_BYTES || String(10 * 1024 * 1024), 10);
const METRICS_ENABLED = parseBool(process.env.METRICS_ENABLED);
const METRICS_PORT = Number.parseInt(process.env.METRICS_PORT || "9000", 10);
const CONVERSATIONS_AUTH_TOKEN = process.env.CONVERSATIONS_AUTH_TOKEN || "";
const CONVERSATIONS_AUTH_DISABLED = parseBool(process.env.CONVERSATIONS_AUTH_DISABLED);
const LOG_REDACT = parseBool(process.env.LOG_REDACT);
const CODEX_SHELL_ENABLED = configBool([["tools", "shell"]], "CODEX_SHELL_ENABLED", false);
const CODEX_SHELL_TIMEOUT_SECONDS = configInteger(
  [["tools", "shell_timeout_seconds"], ["runtime", "shell_timeout_seconds"]],
  "CODEX_SHELL_TIMEOUT_SECONDS",
  30,
);
const CODEX_SHELL_MAX_OUTPUT_BYTES = configInteger(
  [["tools", "shell_max_output_bytes"], ["runtime", "shell_max_output_bytes"]],
  "CODEX_SHELL_MAX_OUTPUT_BYTES",
  12000,
);
const CODEX_MAX_TOOL_ITERATIONS = configInteger(
  [["runtime", "max_tool_iterations"], ["tools", "max_iterations"]],
  "CODEX_MAX_TOOL_ITERATIONS",
  6,
);
const CODEX_MEMORY_ENABLED = configBool([["tools", "memory"]], "CODEX_MEMORY_ENABLED", true);
const CODEX_MEMORY_ROOT = configString([["paths", "memory_root"], ["memory", "root"]], ["CODEX_MEMORY_ROOT"], "/home/agent/.codex/memory");
const CODEX_MEMORY_MAX_BYTES = configInteger([["memory", "max_bytes"]], "CODEX_MEMORY_MAX_BYTES", 65536);
const CODEX_MEMORY_MAX_LIST_ENTRIES = configInteger(
  [["memory", "max_list_entries"]],
  "CODEX_MEMORY_MAX_LIST_ENTRIES",
  200,
);
const CODEX_MCP_ENABLED = configBool([["tools", "mcp"]], "CODEX_MCP_ENABLED", true);
const MCP_CONFIG_PATH = configString([["paths", "mcp_config"], ["mcp", "config_path"]], ["MCP_CONFIG_PATH"], "/home/agent/.codex/mcp.json");
const MCP_TOOL_AUTH_TOKEN = process.env.MCP_TOOL_AUTH_TOKEN || "";
const HOOKS_CONFIG_PATH = configString([["paths", "hooks_config"], ["hooks", "config_path"]], ["HOOKS_CONFIG_PATH"], "/home/agent/.codex/hooks.yaml");
const HOOKS_BASELINE_ENABLED =
  process.env.HOOKS_BASELINE_ENABLED === undefined
    ? configBool([["hooks", "baseline_enabled"]], "HOOKS_BASELINE_ENABLED", true)
    : parseBool(process.env.HOOKS_BASELINE_ENABLED);
const CODEX_MCP_CLIENT_TIMEOUT_MS = Math.max(
  1000,
  Math.round(Number.parseFloat(process.env.CODEX_MCP_CLIENT_TIMEOUT_SECONDS || "30") * 1000) || 30000,
);
const CODEX_MCP_MAX_OUTPUT_BYTES = Math.max(
  1,
  Number.parseInt(process.env.CODEX_MCP_MAX_OUTPUT_BYTES || "12000", 10) || 12000,
);
const CODEX_SESSION_STORE_PATH = configString(
  [["paths", "session_store"], ["sessions", "store_path"]],
  ["CODEX_SESSION_STORE_PATH"],
  "/home/agent/.codex/sessions/responses.json",
);
const MAX_SESSIONS = Math.max(1, Number.parseInt(process.env.MAX_SESSIONS || "10000", 10) || 10000);
const CONVERSATION_STREAM_KEEPALIVE_SEC = Number.parseFloat(process.env.CONVERSATION_STREAM_KEEPALIVE_SEC || "15");
const CONVERSATION_STREAM_GRACE_SEC = Number.parseFloat(process.env.CONVERSATION_STREAM_GRACE_SEC || "60");
const CONVERSATION_STREAM_RING_MAX = Math.max(
  1,
  Number.parseInt(process.env.CONVERSATION_STREAM_RING_MAX || "200", 10) || 200,
);
const SESSION_STREAM_MAX_PER_CALLER = parseNonNegativeInt(process.env.SESSION_STREAM_MAX_PER_CALLER, 8);
const SESSION_ID_SECRET = process.env.SESSION_ID_SECRET || "";
const SESSION_ID_SECRET_PREV = process.env.SESSION_ID_SECRET_PREV || "";
const MCP_MAX_BODY_BYTES = Math.max(
  1,
  Number.parseInt(process.env.MCP_MAX_BODY_BYTES || String(4 * 1024 * 1024), 10) || 4 * 1024 * 1024,
);
const CODEX_SHELL_ALLOWED_PREFIXES = splitList(
  process.env.CODEX_SHELL_ALLOWED_PREFIXES ||
    [
      "date",
      "pwd",
      "ls",
      "find .",
      "command -v ",
      "rg ",
      "cat ",
      "sed -n ",
      "head ",
      "tail ",
      "git status",
      "git log",
      "git show",
      "git diff",
      "git rev-parse",
      "kubectl get",
      "kubectl describe",
      "kubectl logs",
      "kubectl top",
      "kubectl auth can-i",
      "kubectl rollout status",
      "kubectl rollout history",
      "ww agent",
      "ww operator",
      "ww team",
      "ww workspace",
      "ww update",
      "ww version",
      "gh run",
      "gh release",
      "gh workflow",
      "gh auth status",
      "gh api repos/witwave-ai/witwave",
      "helm list",
      "helm status",
    ].join("\n"),
);
const CODEX_SHELL_CWD =
  process.env.CODEX_SHELL_CWD || process.env.WORKSPACE_DIR || "/workspaces/witwave-self/source/witwave";

let ready = false;
let startupDurationSeconds = 0;
let openaiClientPromise = null;

const metrics = {
  healthChecks: new Map(),
  a2aRequests: new Map(),
  a2aDurationCount: 0,
  a2aDurationSum: 0,
  modelRequests: new Map(),
  mcpRequests: new Map(),
  mcpDurationCounts: new Map(),
  mcpDurations: new Map(),
  logEntries: new Map(),
  logBytes: new Map(),
  logWriteErrorsTotal: 0,
  logWriteErrorsByLogger: new Map(),
  toolCalls: new Map(),
  promptBytesCount: 0,
  promptBytesSum: 0,
  responseBytesCount: 0,
  responseBytesSum: 0,
  emptyPromptsTotal: 0,
  promptTooLargeTotal: 0,
  budgetExceededTotal: 0,
  contextTokensCount: 0,
  contextTokensSum: 0,
  contextTokensRemainingCount: 0,
  contextTokensRemainingSum: 0,
  sessionStartsTotal: 0,
  sessionEvictionsTotal: 0,
  streamingEventsEmitted: new Map(),
  streamingChunksDropped: new Map(),
  hookDenials: new Map(),
  hookWarnings: new Map(),
  hookEvaluations: new Map(),
  hookConfigErrors: new Map(),
  sdkToolCalls: new Map(),
  sdkToolErrors: new Map(),
  sdkToolDurationCounts: new Map(),
  sdkToolDurationSums: new Map(),
  sdkToolInputBytesCounts: new Map(),
  sdkToolInputBytesSums: new Map(),
  sdkToolResultBytesCounts: new Map(),
  sdkToolResultBytesSums: new Map(),
  sdkToolCallsPerQueryCount: 0,
  sdkToolCallsPerQuerySum: 0,
  mcpOutboundRequests: new Map(),
  mcpOutboundDurationCounts: new Map(),
  mcpOutboundDurationSums: new Map(),
  lastA2ARequestTimestamp: 0,
};

let codexSessions = null;
const sessionStreams = new Map();
const sessionStreamCallerCounts = new Map();
let mcpToolCache = {
  fingerprint: "",
  clients: new Map(),
  tools: [],
  toolIndex: new Map(),
};
let mcpDiscoveryPromise = null;

function parseBool(value) {
  if (value === undefined || value === null || value === "") {
    return false;
  }
  return ["1", "true", "yes", "on"].includes(String(value).trim().toLowerCase());
}

function stripTomlComment(line) {
  let quote = "";
  for (let i = 0; i < line.length; i += 1) {
    const char = line[i];
    if ((char === '"' || char === "'") && line[i - 1] !== "\\") {
      quote = quote === char ? "" : quote || char;
      continue;
    }
    if (char === "#" && !quote) {
      return line.slice(0, i);
    }
  }
  return line;
}

function parseTomlValue(rawValue) {
  const raw = String(rawValue || "").trim();
  if (/^(true|false)$/i.test(raw)) {
    return raw.toLowerCase() === "true";
  }
  if ((raw.startsWith('"') && raw.endsWith('"')) || (raw.startsWith("'") && raw.endsWith("'"))) {
    return raw.slice(1, -1);
  }
  if (/^-?\d+$/.test(raw)) {
    return Number.parseInt(raw, 10);
  }
  return raw;
}

export function loadCodexConfigFromText(text) {
  const root = {};
  let current = root;
  for (const rawLine of String(text || "").split(/\r?\n/)) {
    const line = stripTomlComment(rawLine).trim();
    if (!line) {
      continue;
    }
    const tableMatch = line.match(/^\[([A-Za-z0-9_.-]+)\]$/);
    if (tableMatch) {
      current = root;
      for (const part of tableMatch[1].split(".")) {
        if (!current[part] || typeof current[part] !== "object" || Array.isArray(current[part])) {
          current[part] = {};
        }
        current = current[part];
      }
      continue;
    }
    const assignmentMatch = line.match(/^([A-Za-z0-9_.-]+)\s*=\s*(.*)$/);
    if (!assignmentMatch) {
      continue;
    }
    current[assignmentMatch[1]] = parseTomlValue(assignmentMatch[2]);
  }
  return root;
}

function loadCodexConfig(filePath) {
  const raw = readTextIfExists(filePath);
  if (!raw.trim()) {
    return {};
  }
  try {
    return loadCodexConfigFromText(raw);
  } catch (error) {
    console.warn(`codex backend: failed to parse config at ${filePath}: ${error?.message || error}`);
    return {};
  }
}

function configValue(pathParts) {
  let current = CODEX_CONFIG;
  for (const part of pathParts) {
    if (!current || typeof current !== "object" || Array.isArray(current) || !(part in current)) {
      return undefined;
    }
    current = current[part];
  }
  return current;
}

function configString(paths, envNames, defaultValue) {
  for (const envName of envNames) {
    const envValue = process.env[envName];
    if (typeof envValue === "string" && envValue.trim()) {
      return envValue.trim();
    }
  }
  for (const pathParts of paths) {
    const value = configValue(pathParts);
    if (typeof value === "string" && value.trim()) {
      return value.trim();
    }
  }
  return defaultValue;
}

function configBool(paths, envName, defaultValue) {
  if (process.env[envName] !== undefined) {
    return parseBool(process.env[envName]);
  }
  for (const pathParts of paths) {
    const value = configValue(pathParts);
    if (typeof value === "boolean") {
      return value;
    }
  }
  return defaultValue;
}

function configInteger(paths, envName, defaultValue) {
  const envValue = process.env[envName];
  if (envValue !== undefined && envValue !== "") {
    const parsed = Number.parseInt(String(envValue), 10);
    return Number.isFinite(parsed) ? parsed : defaultValue;
  }
  for (const pathParts of paths) {
    const value = configValue(pathParts);
    if (typeof value === "number" && Number.isFinite(value)) {
      return value;
    }
    if (typeof value === "string" && value.trim()) {
      const parsed = Number.parseInt(value, 10);
      if (Number.isFinite(parsed)) {
        return parsed;
      }
    }
  }
  return defaultValue;
}

function splitList(value) {
  return String(value || "")
    .split(/[\n,]+/)
    .map((item) => item.trim())
    .filter(Boolean);
}

function parseNonNegativeInt(value, defaultValue) {
  const parsed = Number.parseInt(String(value ?? ""), 10);
  return Number.isFinite(parsed) && parsed >= 0 ? parsed : defaultValue;
}

function parsePositiveInt(value, defaultValue) {
  const parsed = Number.parseInt(String(value ?? ""), 10);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : defaultValue;
}

function inc(map, key, amount = 1) {
  map.set(key, (map.get(key) || 0) + amount);
}

function byteLength(text) {
  return Buffer.byteLength(text || "", "utf8");
}

function jsonByteLength(value) {
  try {
    return byteLength(JSON.stringify(value ?? {}));
  } catch {
    return byteLength(String(value ?? ""));
  }
}

function observeSummary(countMap, sumMap, key, value) {
  const observed = Number.isFinite(value) ? value : 0;
  inc(countMap, key);
  sumMap.set(key, (sumMap.get(key) || 0) + observed);
}

function observeSdkToolCall(toolName, toolInput, result, durationSeconds) {
  const name = toolName || "unknown";
  const isError = result?.ok === false || result?.refused === true || result?.is_error === true;
  inc(metrics.sdkToolCalls, name);
  if (isError) {
    inc(metrics.sdkToolErrors, name);
  }
  observeSummary(metrics.sdkToolDurationCounts, metrics.sdkToolDurationSums, name, durationSeconds);
  observeSummary(metrics.sdkToolInputBytesCounts, metrics.sdkToolInputBytesSums, name, jsonByteLength(toolInput));
  observeSummary(metrics.sdkToolResultBytesCounts, metrics.sdkToolResultBytesSums, name, jsonByteLength(result));
}

function observeMcpOutboundCall(serverName, toolName, outcome, durationSeconds) {
  const key = mapKey(serverName || "unknown", toolName || "unknown", outcome || "error");
  inc(metrics.mcpOutboundRequests, key);
  observeSummary(metrics.mcpOutboundDurationCounts, metrics.mcpOutboundDurationSums, key, durationSeconds);
}

function sanitizeModelLabel(value) {
  const raw = String(value || "");
  return /^[a-zA-Z0-9._-]{1,64}$/.test(raw) ? raw : "unknown";
}

function mapKey(...parts) {
  return parts.map((part) => String(part ?? "")).join("\0");
}

function mapKeyParts(key) {
  return String(key).split("\0");
}

const SYSTEM_PATH_PREFIXES = ["/etc", "/boot", "/bin", "/sbin", "/usr", "/lib", "/lib64", "/sys", "/proc", "/dev"];

const CODEX_BASELINE_HOOK_RULES = [
  {
    name: "baseline-rm-rf-root",
    tool: "Bash",
    source: "baseline",
    action: "deny",
    reason: "rm -rf targeting root or a system directory — refusing by baseline policy.",
    matches: (input) => {
      const command = normalizeCommand(input?.command);
      return /\brm\b(?=[^;&|`<>]*\s-r?f|[^;&|`<>]*\s-f?r)[^;&|`<>]*(\s|=)(\/|\/\*|~|\$HOME|\/etc\b|\/var\b|\/usr\b|\/boot\b|\/lib\b|\/bin\b|\/sbin\b)/i.test(
        command,
      );
    },
  },
  {
    name: "baseline-git-force-push-main",
    tool: "Bash",
    source: "baseline",
    action: "deny",
    reason: "git push --force to main/master — refusing by baseline policy.",
    matches: (input) => {
      const command = normalizeCommand(input?.command);
      return /\bgit\s+push\b(?=[^;&|`<>]*(--force\b|-f\b|--force-with-lease\b))[^;&|`<>]*(\bmain\b|\bmaster\b|:main\b|:master\b)/i.test(
        command,
      );
    },
  },
  {
    name: "baseline-curl-pipe-shell",
    tool: "Bash",
    source: "baseline",
    action: "deny",
    reason: "curl/wget piped to a shell — refusing by baseline policy.",
    matches: (input) => {
      const command = normalizeCommand(input?.command);
      return /\b(curl|wget)\b[^|]*\|\s*(sh|bash|zsh|python3?)\b|\bbash\s+<\(\s*(curl|wget)\b/i.test(command);
    },
  },
  {
    name: "baseline-chmod-777",
    tool: "Bash",
    source: "baseline",
    action: "deny",
    reason: "chmod world-writable (0777 or a+w/o+w) — refusing by baseline policy.",
    matches: (input) => {
      const command = normalizeCommand(input?.command);
      return /\bchmod\b\s+(-R\s+)?([0-7]*777\b|[ao]\+w\b|a=.*w)/i.test(command);
    },
  },
  {
    name: "baseline-dd-device",
    tool: "Bash",
    source: "baseline",
    action: "deny",
    reason: "dd to a block device — refusing by baseline policy.",
    matches: (input) => {
      const command = normalizeCommand(input?.command);
      return /\bdd\b[^;&|`<>]*\bof=\/dev\/(sd|nvme|disk|hd|vd|xvd|mmcblk|loop|mapper\/|md|dm-)/i.test(command);
    },
  },
  {
    name: "baseline-write-system-path",
    tool: "Write",
    source: "baseline",
    action: "deny",
    reason: "Write/Edit targeting a system path (/etc, /usr, /bin, …) — refusing by baseline policy.",
    matches: (input) => {
      const rawPath = input?.file_path || input?.path || input?.notebook_path || "";
      if (typeof rawPath !== "string" || !rawPath || !path.posix.isAbsolute(rawPath)) {
        return false;
      }
      const normalized = path.posix.normalize(rawPath);
      return SYSTEM_PATH_PREFIXES.some((prefix) => normalized === prefix || normalized.startsWith(`${prefix}/`));
    },
  },
];

let hookExtensionRuleCache = {
  signature: "",
  rules: [],
};

function hookConfigSignature() {
  try {
    const stat = fs.statSync(HOOKS_CONFIG_PATH);
    return `${stat.mtimeMs}:${stat.size}`;
  } catch (error) {
    if (error?.code !== "ENOENT") {
      inc(metrics.hookConfigErrors, "stat_failed");
      console.warn(`codex backend: failed to stat hooks config at ${HOOKS_CONFIG_PATH}: ${error?.message || error}`);
    }
    return "missing";
  }
}

function parseHookExtensionRule(raw) {
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) {
    inc(metrics.hookConfigErrors, "non_mapping_entry");
    return undefined;
  }
  const name = typeof raw.name === "string" && raw.name.trim() ? raw.name.trim() : "";
  if (!name) {
    inc(metrics.hookConfigErrors, "missing_name");
    return undefined;
  }
  const rawTool = typeof raw.tool === "string" && raw.tool.trim() ? raw.tool.trim() : undefined;
  const tool = rawTool === "*" ? undefined : rawTool;
  let denyPattern = typeof raw.deny_if_match === "string" ? raw.deny_if_match : "";
  let warnPattern = typeof raw.warn_if_match === "string" ? raw.warn_if_match : "";
  if (denyPattern && warnPattern) {
    inc(metrics.hookConfigErrors, "both_patterns");
    warnPattern = "";
  }
  if (!denyPattern && !warnPattern) {
    inc(metrics.hookConfigErrors, "no_pattern");
    return undefined;
  }
  try {
    return {
      name,
      tool,
      source: "extension",
      action: denyPattern ? "deny" : "warn",
      reason: typeof raw.reason === "string" && raw.reason.trim() ? raw.reason.trim() : `blocked by extension rule ${name}`,
      pattern: new RegExp(denyPattern || warnPattern),
    };
  } catch (error) {
    inc(metrics.hookConfigErrors, "invalid_regex");
    console.warn(`codex backend: invalid hooks.yaml regex for rule ${name}: ${error?.message || error}`);
    return undefined;
  }
}

export function loadHookExtensionRulesFromText(text) {
  if (!String(text || "").trim()) {
    return [];
  }
  let parsed;
  try {
    parsed = YAML.parse(text) || {};
  } catch (error) {
    inc(metrics.hookConfigErrors, "file_load_failed");
    throw error;
  }
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
    inc(metrics.hookConfigErrors, "not_mapping");
    return [];
  }
  const extensions = parsed.extensions || [];
  if (!Array.isArray(extensions)) {
    inc(metrics.hookConfigErrors, "non_list_extensions");
    return [];
  }
  return extensions.map(parseHookExtensionRule).filter(Boolean);
}

function loadHookExtensionRules() {
  const signature = hookConfigSignature();
  if (signature === hookExtensionRuleCache.signature) {
    return hookExtensionRuleCache.rules;
  }
  if (signature === "missing") {
    hookExtensionRuleCache = { signature, rules: [] };
    return hookExtensionRuleCache.rules;
  }
  const raw = readTextIfExists(HOOKS_CONFIG_PATH);
  try {
    const rules = loadHookExtensionRulesFromText(raw);
    hookExtensionRuleCache = { signature, rules };
    return rules;
  } catch (error) {
    console.warn(`codex backend: failed to load hooks config at ${HOOKS_CONFIG_PATH}: ${error?.message || error}`);
    // Keep the last valid rules on parse failure, matching the safer hot-reload posture used by the Python backends.
    return hookExtensionRuleCache.rules;
  }
}

function hookActiveRules() {
  return [...(HOOKS_BASELINE_ENABLED ? CODEX_BASELINE_HOOK_RULES : []), ...loadHookExtensionRules()];
}

function hookToolAliases(toolName) {
  const aliases = new Set([String(toolName || "")]);
  if (toolName === "run_shell_command") {
    aliases.add("Bash");
    aliases.add("ShellTool");
  }
  if (toolName === "write_memory_file" || toolName === "append_memory_file") {
    aliases.add("Write");
    aliases.add("Edit");
  }
  if (String(toolName || "").startsWith("mcp__")) {
    aliases.add("MCP");
  }
  return aliases;
}

function hookRuleMatchesTool(rule, toolName) {
  if (!rule.tool) {
    return true;
  }
  return hookToolAliases(toolName).has(rule.tool);
}

function hookInputHaystack(input) {
  try {
    return JSON.stringify(input || {}, (_, value) => (typeof value === "bigint" ? String(value) : value));
  } catch {
    return String(input || "");
  }
}

function ruleMatchesInput(rule, input) {
  if (typeof rule.matches === "function") {
    return Boolean(rule.matches(input || {}));
  }
  if (rule.pattern instanceof RegExp) {
    return rule.pattern.test(hookInputHaystack(input));
  }
  return false;
}

export function evaluatePreToolUse(toolName, toolInput = {}, rules = hookActiveRules()) {
  let firstWarn;
  for (const rule of rules) {
    if (!hookRuleMatchesTool(rule, toolName)) {
      continue;
    }
    try {
      if (!ruleMatchesInput(rule, toolInput)) {
        continue;
      }
    } catch (error) {
      inc(metrics.hookConfigErrors, "predicate_runtime");
      console.warn(`codex backend: hook rule ${rule.name} raised during evaluation: ${error?.message || error}`);
      continue;
    }
    if (rule.action === "deny") {
      return { decision: "deny", rule };
    }
    if (rule.action === "warn" && !firstWarn) {
      firstWarn = rule;
    }
  }
  if (firstWarn) {
    return { decision: "warn", rule: firstWarn };
  }
  return { decision: "allow", rule: undefined };
}

function safeToolInputPreview(input) {
  return truncateBytes(redactText(hookInputHaystack(input)), 4096);
}

function safeTraceValue(value, maxBytes = 8192) {
  const redacted = redactText(hookInputHaystack(value));
  if (byteLength(redacted) > maxBytes) {
    return {
      preview: truncateBytes(redacted, maxBytes),
      truncated: true,
    };
  }
  try {
    return JSON.parse(redacted);
  } catch {
    return redacted;
  }
}

function toolTraceContext(context = {}) {
  return {
    toolUseId: context.toolUseId || `codex-${crypto.randomUUID()}`,
    sessionId: context.sessionId || "",
    model: context.model || "",
  };
}

function appendToolUseEvent(toolName, toolInput, traceId, context = {}) {
  const ctx = toolTraceContext(context);
  appendJsonl(TRACE_LOG, {
    ts: new Date().toISOString(),
    agent: AGENT_NAME,
    agent_id: AGENT_ID,
    session_id: ctx.sessionId,
    event_type: "tool_use",
    model: ctx.model,
    id: ctx.toolUseId,
    name: toolName,
    input: safeTraceValue(toolInput, 4096),
    ...(traceId ? { trace_id: traceId } : {}),
  });
}

function toolResultContent(result) {
  if (result?.output !== undefined) {
    return result.output;
  }
  if (result?.stdout !== undefined || result?.stderr !== undefined) {
    return {
      stdout: result.stdout || "",
      stderr: result.stderr || "",
      exit_code: result.exit_code,
      signal: result.signal,
      timed_out: Boolean(result.timed_out),
    };
  }
  return result;
}

function appendToolResultEvent(toolName, result, traceId, context = {}) {
  const ctx = toolTraceContext(context);
  appendJsonl(TRACE_LOG, {
    ts: new Date().toISOString(),
    agent: AGENT_NAME,
    agent_id: AGENT_ID,
    session_id: ctx.sessionId,
    event_type: "tool_result",
    model: ctx.model,
    tool_use_id: ctx.toolUseId,
    content: safeTraceValue(toolResultContent(result), 8192),
    is_error: result?.ok === false || result?.refused === true || result?.is_error === true,
    ...(traceId ? { trace_id: traceId } : {}),
  });
}

function appendToolAuditEvent(toolName, toolInput, result, traceId, context = {}) {
  const ctx = toolTraceContext(context);
  const isError = result?.ok === false || result?.refused === true || result?.is_error === true;
  appendJsonl(TRACE_LOG, {
    ts: new Date().toISOString(),
    agent: AGENT_NAME,
    agent_id: AGENT_ID,
    session_id: ctx.sessionId,
    event_type: "tool_audit",
    model: ctx.model,
    tool_use_id: ctx.toolUseId,
    tool_name: toolName,
    tool_input: safeTraceValue(toolInput, 4096),
    tool_response_preview: truncateBytes(redactText(hookInputHaystack(result)), 2048),
    decision: isError ? "error" : "allow",
    ...(traceId ? { trace_id: traceId } : {}),
  });
}

function recordHookDecision(toolName, toolInput, traceId, context = {}) {
  const { decision, rule } = evaluatePreToolUse(toolName, toolInput);
  inc(metrics.hookEvaluations, mapKey(toolName, decision));
  if (!rule) {
    return undefined;
  }
  const metricKey = mapKey(toolName, rule.source || "extension", rule.name || "unknown");
  if (decision === "deny") {
    inc(metrics.hookDenials, metricKey);
  } else if (decision === "warn") {
    inc(metrics.hookWarnings, metricKey);
  }
  appendJsonl(TRACE_LOG, {
    ts: new Date().toISOString(),
    backend: BACKEND_ID,
    event_type: "tool_audit",
    agent: AGENT_NAME,
    agent_id: AGENT_ID,
    session_id: context.sessionId || "",
    model: context.model || "",
    tool_use_id: context.toolUseId || "",
    tool_name: toolName,
    tool: toolName,
    tool_input: safeTraceValue(toolInput, 4096),
    decision,
    rule: rule.name,
    source: rule.source,
    reason: rule.reason,
    input_preview: safeToolInputPreview(toolInput),
    ...(traceId ? { trace_id: traceId } : {}),
  });
  return { decision, rule };
}

function preToolUseGate(toolName, toolInput, traceId, context = {}) {
  const decision = recordHookDecision(toolName, toolInput, traceId, context);
  if (!decision || decision.decision !== "deny") {
    return undefined;
  }
  const reason = decision.rule?.reason || "tool call denied by PreToolUse policy";
  inc(metrics.toolCalls, `${toolName}:refused`);
  return {
    ok: false,
    refused: true,
    hook_denied: true,
    tool: toolName,
    ...(traceId ? { trace_id: traceId } : {}),
    rule: decision.rule?.name || "unknown",
    reason,
  };
}

function sessionHash(sessionId) {
  if (!sessionId) {
    return "000000000000";
  }
  return crypto.createHash("sha256").update(sessionId).digest("hex").slice(0, 12);
}

function uuidBytes(uuid) {
  const hex = String(uuid || "").replace(/-/g, "");
  return Buffer.from(hex, "hex");
}

function formatUuid(bytes) {
  const hex = Buffer.from(bytes).toString("hex");
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}

function uuid5(namespace, name) {
  const hash = crypto
    .createHash("sha1")
    .update(uuidBytes(namespace))
    .update(String(name || ""))
    .digest();
  const bytes = Buffer.from(hash.subarray(0, 16));
  bytes[6] = (bytes[6] & 0x0f) | 0x50;
  bytes[8] = (bytes[8] & 0x3f) | 0x80;
  return formatUuid(bytes);
}

function legacySessionId(rawSessionId) {
  const raw = String(rawSessionId || "").trim();
  if (/^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$/.test(raw)) {
    return raw.toLowerCase();
  }
  return uuid5("6ba7b811-9dad-11d1-80b4-00c04fd430c8", raw);
}

function sanitizeRawSessionId(raw) {
  return String(raw || "")
    .trim()
    .slice(0, 256)
    .split("")
    .filter((char) => char >= " ")
    .join("");
}

export function deriveSessionId(rawSessionId, callerIdentity, secret = SESSION_ID_SECRET) {
  const raw = sanitizeRawSessionId(rawSessionId);
  if (!raw) {
    return crypto.randomUUID();
  }
  if (!secret) {
    return legacySessionId(raw);
  }
  if (!callerIdentity) {
    console.warn(
      "codex backend: SESSION_ID_SECRET is set but no caller identity is available; using legacy session derivation",
    );
    return legacySessionId(raw);
  }
  const callerHash = crypto.createHash("sha256").update(String(callerIdentity)).digest("hex");
  const mac = crypto.createHmac("sha256", secret).update(`${callerHash}\0${raw}`).digest("hex");
  return uuid5("6ba7b811-9dad-11d1-80b4-00c04fd430c8", mac);
}

function deriveSessionCandidates(rawSessionId, callerIdentity) {
  const current = deriveSessionId(rawSessionId, callerIdentity, SESSION_ID_SECRET);
  const candidates = [current];
  const raw = sanitizeRawSessionId(rawSessionId);
  if (raw && callerIdentity && SESSION_ID_SECRET_PREV && SESSION_ID_SECRET_PREV !== SESSION_ID_SECRET) {
    const previous = deriveSessionId(raw, callerIdentity, SESSION_ID_SECRET_PREV);
    if (!candidates.includes(previous)) {
      candidates.push(previous);
    }
  }
  return candidates;
}

function callerIdentityFromRequest(req) {
  const header = req?.headers?.authorization || "";
  const token = header.startsWith("Bearer ") ? header.slice("Bearer ".length) : "";
  return token ? crypto.createHash("sha256").update(token).digest("hex") : undefined;
}

function sessionStreamCallerFingerprint(req) {
  const bearer = String(req?.headers?.authorization || "").slice(0, 128);
  return crypto.createHash("sha256").update(bearer).digest("hex").slice(0, 16);
}

function nowIsoMs() {
  return new Date().toISOString();
}

function getSessionStream(sessionId, { create = false } = {}) {
  const cleanSessionId = String(sessionId || "").trim();
  if (!cleanSessionId) {
    return undefined;
  }
  let stream = sessionStreams.get(cleanSessionId);
  if (!stream && create) {
    stream = {
      sessionId: cleanSessionId,
      nextId: 0,
      ring: [],
      subscribers: new Set(),
      cleanupTimer: null,
    };
    sessionStreams.set(cleanSessionId, stream);
  }
  if (stream?.cleanupTimer) {
    clearTimeout(stream.cleanupTimer);
    stream.cleanupTimer = null;
  }
  return stream;
}

function scheduleSessionStreamCleanup(stream) {
  if (!stream || stream.subscribers.size > 0 || stream.cleanupTimer) {
    return;
  }
  const graceMs = Math.max(0, CONVERSATION_STREAM_GRACE_SEC) * 1000;
  stream.cleanupTimer = setTimeout(() => {
    if (stream.subscribers.size === 0) {
      sessionStreams.delete(stream.sessionId);
    }
  }, graceMs);
  stream.cleanupTimer.unref?.();
}

function sessionStreamEnvelope(stream, type, payload) {
  const id = String(stream.nextId);
  stream.nextId += 1;
  return {
    type,
    version: 1,
    id,
    ts: nowIsoMs(),
    agent_id: AGENT_OWNER,
    payload,
  };
}

function sseSerialize(envelope) {
  return `event: ${envelope.type}\nid: ${envelope.id}\ndata: ${JSON.stringify(envelope)}\n\n`;
}

export function publishSessionChunk(sessionId, { role, seq, content, final, model }) {
  const stream = getSessionStream(sessionId, { create: true });
  if (!stream) {
    return undefined;
  }
  const roleValue = String(role || "assistant");
  const contentValue = String(content || "");
  const finalValue = Boolean(final);
  const isAssistantDelta = roleValue === "assistant" && !finalValue && contentValue;
  const modelLabel = sanitizeModelLabel(model || CODEX_MODEL);
  const envelope = sessionStreamEnvelope(stream, "conversation.chunk", {
    session_id_hash: sessionHash(sessionId),
    role: roleValue,
    seq: Number.isFinite(Number(seq)) ? Number(seq) : 0,
    content: contentValue,
    final: finalValue,
  });
  stream.ring.push(envelope);
  if (isAssistantDelta) {
    inc(metrics.streamingEventsEmitted, modelLabel);
  }
  while (stream.ring.length > CONVERSATION_STREAM_RING_MAX) {
    stream.ring.shift();
  }
  for (const res of [...stream.subscribers]) {
    try {
      res.write(sseSerialize(envelope));
    } catch {
      stream.subscribers.delete(res);
      if (isAssistantDelta) {
        inc(metrics.streamingChunksDropped, modelLabel);
      }
    }
  }
  scheduleSessionStreamCleanup(stream);
  return envelope;
}

function traceIdForMetadata(metadata) {
  const direct = String(metadata?.trace_id || "").trim();
  if (/^[0-9a-fA-F]{32}$/.test(direct) && direct !== "0".repeat(32)) {
    return direct.toLowerCase();
  }
  const traceparent = String(metadata?.traceparent || "").trim();
  const match = traceparent.match(/^[0-9a-fA-F]{2}-([0-9a-fA-F]{32})-[0-9a-fA-F]{16}-[0-9a-fA-F]{2}$/);
  if (match && match[1] !== "0".repeat(32)) {
    return match[1].toLowerCase();
  }
  return undefined;
}

function ensureParent(filePath) {
  fs.mkdirSync(path.dirname(filePath), { recursive: true });
}

function jsonlLoggerName(filePath) {
  if (filePath === CONVERSATION_LOG) {
    return "conversation";
  }
  if (filePath === TRACE_LOG) {
    return "trace";
  }
  return "jsonl";
}

function appendJsonl(filePath, record) {
  const loggerName = jsonlLoggerName(filePath);
  try {
    ensureParent(filePath);
    const line = JSON.stringify(record);
    fs.appendFileSync(filePath, `${line}\n`, "utf8");
    inc(metrics.logEntries, loggerName);
    inc(metrics.logBytes, loggerName, byteLength(line));
  } catch (error) {
    metrics.logWriteErrorsTotal += 1;
    inc(metrics.logWriteErrorsByLogger, loggerName);
    console.error(`codex backend: failed to append ${filePath}:`, error);
  }
}

function readTextIfExists(filePath) {
  try {
    return fs.readFileSync(filePath, "utf8");
  } catch {
    return "";
  }
}

function loadSessionStore() {
  if (codexSessions instanceof Map) {
    return codexSessions;
  }
  const raw = readTextIfExists(CODEX_SESSION_STORE_PATH);
  const sessions = new Map();
  if (raw.trim()) {
    try {
      const parsed = JSON.parse(raw);
      for (const [sessionId, value] of Object.entries(parsed.sessions || {})) {
        if (!sessionId || !value || typeof value !== "object") {
          continue;
        }
        const previousResponseId = String(value.previous_response_id || "").trim();
        if (!previousResponseId) {
          continue;
        }
        sessions.set(sessionId, {
          previous_response_id: previousResponseId,
          created_at: value.created_at || new Date().toISOString(),
          updated_at: value.updated_at || new Date().toISOString(),
          model: value.model || "",
        });
      }
    } catch (error) {
      console.error(`codex backend: failed to parse ${CODEX_SESSION_STORE_PATH}:`, error);
    }
  }
  codexSessions = sessions;
  return codexSessions;
}

function saveSessionStore() {
  if (!(codexSessions instanceof Map)) {
    return;
  }
  try {
    ensureParent(CODEX_SESSION_STORE_PATH);
    const sessions = Object.fromEntries(codexSessions.entries());
    fs.writeFileSync(
      CODEX_SESSION_STORE_PATH,
      JSON.stringify({ version: 1, updated_at: new Date().toISOString(), sessions }, null, 2),
      "utf8",
    );
  } catch (error) {
    console.error(`codex backend: failed to write ${CODEX_SESSION_STORE_PATH}:`, error);
  }
}

function sessionForRequest(metadata, contextId) {
  const raw = metadata?.session_id || metadata?.sessionId || contextId;
  const rawSessionId = sanitizeRawSessionId(raw);
  const callerIdentity =
    typeof metadata?.caller_id === "string" && metadata.caller_id.trim() ? metadata.caller_id.trim() : undefined;
  const candidateSessionIds = deriveSessionCandidates(rawSessionId, callerIdentity);
  return {
    sessionId: candidateSessionIds[0],
    candidateSessionIds,
  };
}

function getStoredSessionFromCandidates(sessionIds = []) {
  const sessions = loadSessionStore();
  for (const sessionId of sessionIds) {
    const stored = sessions.get(sessionId);
    if (stored) {
      return { sessionId, stored };
    }
  }
  return { sessionId: sessionIds[0], stored: undefined };
}

function recordSessionResponse(sessionId, responseId, model) {
  if (!sessionId || !responseId) {
    return;
  }
  const sessions = loadSessionStore();
  const existing = sessions.get(sessionId);
  const now = new Date().toISOString();
  if (!existing) {
    metrics.sessionStartsTotal += 1;
  }
  sessions.set(sessionId, {
    previous_response_id: responseId,
    created_at: existing?.created_at || now,
    updated_at: now,
    model: model || existing?.model || "",
  });
  while (sessions.size > MAX_SESSIONS) {
    const oldest = sessions.keys().next().value;
    if (oldest === undefined) {
      break;
    }
    sessions.delete(oldest);
    metrics.sessionEvictionsTotal += 1;
  }
  saveSessionStore();
}

function timestampForEntry(entry) {
  const raw = entry?.ts || entry?.timestamp;
  if (typeof raw === "number") {
    return new Date(raw * 1000);
  }
  if (typeof raw !== "string" || !raw.trim()) {
    return undefined;
  }
  const date = new Date(raw);
  return Number.isNaN(date.getTime()) ? undefined : date;
}

function readJsonlEntries(filePath, { since, limit } = {}) {
  const sinceDate = since ? new Date(since) : undefined;
  if (since && Number.isNaN(sinceDate.getTime())) {
    const error = new Error("invalid since");
    error.code = "INVALID_SINCE";
    throw error;
  }
  let limitCount;
  if (limit !== undefined && limit !== null && limit !== "") {
    limitCount = Number.parseInt(String(limit), 10);
    if (!Number.isFinite(limitCount) || limitCount <= 0) {
      const error = new Error("invalid limit");
      error.code = "INVALID_LIMIT";
      throw error;
    }
  }

  const raw = readTextIfExists(filePath);
  const entries = [];
  for (const line of raw.split(/\n/)) {
    const trimmed = line.trim();
    if (!trimmed) {
      continue;
    }
    let entry;
    try {
      entry = JSON.parse(trimmed);
    } catch {
      continue;
    }
    if (sinceDate) {
      const ts = timestampForEntry(entry);
      if (!ts || ts < sinceDate) {
        continue;
      }
    }
    entries.push(entry);
  }
  return limitCount ? entries.slice(-limitCount) : entries;
}

function loadAgentDescription() {
  const card = readTextIfExists("/home/agent/.codex/agent-card.md").trim();
  if (card) {
    return card;
  }
  return process.env.AGENT_DESCRIPTION || "A Codex-native backend agent.";
}

function loadInstructions() {
  return readTextIfExists(CODEX_AGENT_MD).trim();
}

export function buildAgentCard() {
  return {
    name: AGENT_NAME,
    description: loadAgentDescription(),
    url: AGENT_URL,
    version: AGENT_VERSION,
    capabilities: { streaming: false },
    defaultInputModes: ["text/plain"],
    defaultOutputModes: ["text/plain"],
    skills: [
      {
        id: "general",
        name: "General",
        description: "General-purpose task execution via Codex.",
        tags: ["general", "codex"],
      },
    ],
  };
}

function collectTextParts(parts) {
  if (!Array.isArray(parts)) {
    return [];
  }
  return parts
    .filter((part) => part && typeof part === "object")
    .map((part) => {
      if (typeof part.text === "string") {
        return part.text;
      }
      if (part.kind === "text" && typeof part.content === "string") {
        return part.content;
      }
      return "";
    })
    .filter(Boolean);
}

export function extractPrompt(payload) {
  const params = payload?.params || {};
  const message = params.message || {};
  const candidates = [
    collectTextParts(message.parts),
    collectTextParts(message.content?.parts),
    collectTextParts(params.parts),
  ];
  const fromParts = candidates.find((items) => items.length > 0);
  if (fromParts) {
    return fromParts.join("\n");
  }
  for (const value of [message.text, params.text, params.prompt]) {
    if (typeof value === "string") {
      return value;
    }
  }
  return "";
}

export function extractRequestMetadata(payload) {
  const params = payload?.params || {};
  const message = params.message || {};
  const metadata = message.metadata || params.metadata || {};
  return {
    message,
    metadata,
    contextId:
      message.contextId || params.contextId || metadata.session_id || metadata.sessionId || crypto.randomUUID(),
    messageId: message.messageId || params.messageId || crypto.randomUUID(),
  };
}

function a2aError(id, code, message, status = 200) {
  return {
    status,
    body: {
      jsonrpc: "2.0",
      id: id ?? null,
      error: { code, message },
    },
  };
}

function a2aMessage(id, text, contextId, metadata = {}) {
  return {
    jsonrpc: "2.0",
    id: id ?? null,
    result: {
      kind: "message",
      role: "agent",
      messageId: crypto.randomUUID(),
      contextId,
      parts: [{ kind: "text", text }],
      metadata,
    },
  };
}

function modelForRequest(metadata) {
  const raw = metadata?.model;
  return typeof raw === "string" && raw.trim() ? raw.trim() : CODEX_MODEL;
}

function reasoningForRequest(metadata) {
  const raw = metadata?.reasoning_effort || metadata?.reasoningEffort || CODEX_REASONING_EFFORT;
  if (typeof raw !== "string" || !raw.trim()) {
    return undefined;
  }
  return { effort: raw.trim() };
}

export function maxOutputTokensForRequest(metadata) {
  const raw = metadata?.max_output_tokens || metadata?.maxOutputTokens;
  if (raw === undefined || raw === null || raw === "") {
    return undefined;
  }
  const parsed = Number.parseInt(String(raw), 10);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : undefined;
}

export function maxTokensForRequest(metadata) {
  const raw = metadata?.max_tokens || metadata?.maxTokens;
  if (raw === undefined || raw === null || raw === "") {
    return undefined;
  }
  const parsed = Number.parseInt(String(raw), 10);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : undefined;
}

function maxToolIterationsForRequest(metadata) {
  const raw = metadata?.max_tool_iterations || metadata?.maxToolIterations || CODEX_MAX_TOOL_ITERATIONS;
  const parsed = Number.parseInt(String(raw), 10);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : CODEX_MAX_TOOL_ITERATIONS;
}

function shellToolDefinition() {
  return {
    type: "function",
    name: "run_shell_command",
    description:
      "Run one bounded read-only diagnostic shell command in the agent workspace. " +
      "Use this for platform health checks, git inspection, Kubernetes status, and release diagnostics.",
    parameters: {
      type: "object",
      properties: {
        command: {
          type: "string",
          description:
            "A single diagnostic command. Shell metacharacters, secret access, and non-allowlisted command prefixes are refused.",
        },
      },
      required: ["command"],
      additionalProperties: false,
    },
    strict: true,
  };
}

function memoryToolDefinitions() {
  return [
    {
      type: "function",
      name: "read_memory_file",
      description:
        "Read one UTF-8 memory file from the Codex memory root. " +
        "The path must be relative to the memory root and cannot escape it.",
      parameters: {
        type: "object",
        properties: {
          path: {
            type: "string",
            description: "Relative memory path, for example platform-health/baseline.md.",
          },
        },
        required: ["path"],
        additionalProperties: false,
      },
      strict: true,
    },
    {
      type: "function",
      name: "write_memory_file",
      description:
        "Overwrite one UTF-8 memory file under the Codex memory root. " +
        "Use this for concise operational notes, baselines, and small markdown memory records.",
      parameters: {
        type: "object",
        properties: {
          path: {
            type: "string",
            description: "Relative memory path, for example platform-health/baseline.md.",
          },
          content: {
            type: "string",
            description: "Complete file content. Raw secrets and over-large payloads are refused.",
          },
        },
        required: ["path", "content"],
        additionalProperties: false,
      },
      strict: true,
    },
    {
      type: "function",
      name: "append_memory_file",
      description:
        "Append UTF-8 content to one memory file under the Codex memory root. " +
        "Use this for JSONL snapshots, health logs, and append-only observation history.",
      parameters: {
        type: "object",
        properties: {
          path: {
            type: "string",
            description: "Relative memory path, for example platform-health/snapshots/2026-05-22.jsonl.",
          },
          content: {
            type: "string",
            description: "Content to append. Include a trailing newline when writing JSONL or markdown log entries.",
          },
        },
        required: ["path", "content"],
        additionalProperties: false,
      },
      strict: true,
    },
    {
      type: "function",
      name: "list_memory_files",
      description:
        "List files below a memory directory under the Codex memory root. " +
        "Use this before reading historical snapshots or agent memory notes.",
      parameters: {
        type: "object",
        properties: {
          path: {
            type: "string",
            description: "Relative memory directory path. Use . for the memory root.",
          },
        },
        required: ["path"],
        additionalProperties: false,
      },
      strict: true,
    },
  ];
}

function mcpHeadersForConfig(config, authToken = MCP_TOOL_AUTH_TOKEN) {
  const headers = {};
  const configured = config && typeof config === "object" ? config.headers : undefined;
  if (configured && typeof configured === "object" && !Array.isArray(configured)) {
    for (const [key, value] of Object.entries(configured)) {
      if (typeof key === "string" && typeof value === "string") {
        headers[key] = value;
      }
    }
  }
  const hasAuth = Object.keys(headers).some((key) => key.toLowerCase() === "authorization");
  if (authToken && !hasAuth) {
    headers.Authorization = `Bearer ${authToken}`;
  }
  return headers;
}

export function mcpServerEntriesFromConfig(data, authToken = MCP_TOOL_AUTH_TOKEN) {
  if (!data || typeof data !== "object" || Array.isArray(data)) {
    return [];
  }
  const servers =
    data.mcpServers && typeof data.mcpServers === "object" && !Array.isArray(data.mcpServers) ? data.mcpServers : data;
  const entries = [];
  for (const [name, config] of Object.entries(servers)) {
    if (!config || typeof config !== "object" || Array.isArray(config)) {
      continue;
    }
    const url = typeof config.url === "string" ? config.url.trim() : "";
    if (!url) {
      continue;
    }
    entries.push({
      name,
      url,
      headers: mcpHeadersForConfig(config, authToken),
    });
  }
  return entries;
}

function mcpConfigEntriesFromDisk() {
  const raw = readTextIfExists(MCP_CONFIG_PATH);
  if (!raw.trim()) {
    return [];
  }
  try {
    return mcpServerEntriesFromConfig(JSON.parse(raw));
  } catch (error) {
    console.warn(`codex backend: failed to parse MCP config at ${MCP_CONFIG_PATH}: ${error?.message || error}`);
    return undefined;
  }
}

function hashObject(value) {
  return crypto.createHash("sha256").update(JSON.stringify(value)).digest("hex");
}

function sanitizeToolNamePart(value, fallback) {
  const cleaned = String(value || "")
    .replace(/[^A-Za-z0-9_-]+/g, "_")
    .replace(/^_+|_+$/g, "");
  return cleaned || fallback;
}

export function mcpFunctionName(serverName, toolName) {
  const base = `mcp__${sanitizeToolNamePart(serverName, "server")}__${sanitizeToolNamePart(toolName, "tool")}`;
  if (base.length <= 64) {
    return base;
  }
  const suffix = crypto.createHash("sha256").update(`${serverName}\0${toolName}`).digest("hex").slice(0, 8);
  return `${base.slice(0, 55)}_${suffix}`;
}

function mcpInputSchemaToParameters(inputSchema) {
  if (inputSchema && typeof inputSchema === "object" && inputSchema.type === "object") {
    return inputSchema;
  }
  return { type: "object", properties: {}, additionalProperties: true };
}

function mcpFunctionToolDefinition(entry, tool, functionName) {
  return {
    type: "function",
    name: functionName,
    description:
      tool?.description ||
      `Call the ${tool?.name || "selected"} tool on the ${entry.name} MCP server from inside the Codex backend.`,
    parameters: mcpInputSchemaToParameters(tool?.inputSchema),
  };
}

async function closeMcpClients(clients) {
  await Promise.allSettled(
    [...clients.values()].map(async (client) => {
      await client.close();
    }),
  );
}

async function connectMcpServer(entry) {
  const client = new McpClient({ name: "witwave-codex-backend", version: AGENT_VERSION }, { capabilities: {} });
  const options = Object.keys(entry.headers || {}).length > 0 ? { requestInit: { headers: entry.headers } } : {};
  const transport = new StreamableHTTPClientTransport(new URL(entry.url), options);
  try {
    await client.connect(transport, { timeout: CODEX_MCP_CLIENT_TIMEOUT_MS });
    return client;
  } catch (streamableError) {
    await client.close().catch(() => undefined);
    const fallback = new McpClient({ name: "witwave-codex-backend", version: AGENT_VERSION }, { capabilities: {} });
    const sseOptions =
      Object.keys(entry.headers || {}).length > 0
        ? { requestInit: { headers: entry.headers }, eventSourceInit: { headers: entry.headers } }
        : {};
    const sseTransport = new SSEClientTransport(new URL(entry.url), sseOptions);
    try {
      await fallback.connect(sseTransport, { timeout: CODEX_MCP_CLIENT_TIMEOUT_MS });
      return fallback;
    } catch (sseError) {
      await fallback.close().catch(() => undefined);
      throw new Error(
        `streamable-http failed (${streamableError?.message || streamableError}); ` +
          `sse fallback failed (${sseError?.message || sseError})`,
      );
    }
  }
}

async function discoverMcpFunctionToolsInner() {
  const entries = mcpConfigEntriesFromDisk();
  if (entries === undefined) {
    return mcpToolCache.tools;
  }
  const fingerprint = hashObject(entries);
  if (fingerprint === mcpToolCache.fingerprint) {
    return mcpToolCache.tools;
  }

  const previousClients = mcpToolCache.clients;
  const clients = new Map();
  const tools = [];
  const toolIndex = new Map();
  for (const entry of entries) {
    let client;
    try {
      client = await connectMcpServer(entry);
      clients.set(entry.name, client);
      const listed = await client.listTools({}, { timeout: CODEX_MCP_CLIENT_TIMEOUT_MS });
      for (const tool of listed.tools || []) {
        const functionName = mcpFunctionName(entry.name, tool.name);
        if (toolIndex.has(functionName)) {
          console.warn(
            `codex backend: duplicate MCP function name ${functionName}; skipping ${entry.name}/${tool.name}`,
          );
          continue;
        }
        toolIndex.set(functionName, {
          client,
          serverName: entry.name,
          toolName: tool.name,
        });
        tools.push(mcpFunctionToolDefinition(entry, tool, functionName));
      }
    } catch (error) {
      if (client) {
        await client.close().catch(() => undefined);
      }
      clients.delete(entry.name);
      console.warn(`codex backend: MCP server ${entry.name} (${entry.url}) unavailable: ${error?.message || error}`);
    }
  }

  mcpToolCache = { fingerprint, clients, tools, toolIndex };
  await closeMcpClients(previousClients);
  return tools;
}

async function discoverMcpFunctionTools() {
  if (!mcpDiscoveryPromise) {
    mcpDiscoveryPromise = discoverMcpFunctionToolsInner().finally(() => {
      mcpDiscoveryPromise = null;
    });
  }
  return await mcpDiscoveryPromise;
}

async function codexToolDefinitions() {
  const tools = [];
  if (CODEX_SHELL_ENABLED) {
    tools.push(shellToolDefinition());
  }
  if (CODEX_MEMORY_ENABLED) {
    tools.push(...memoryToolDefinitions());
  }
  if (CODEX_MCP_ENABLED) {
    tools.push(...(await discoverMcpFunctionTools()));
  }
  return tools;
}

function shellCwd() {
  for (const candidate of [CODEX_SHELL_CWD, "/home/agent/workspace", process.cwd()]) {
    if (candidate && fs.existsSync(candidate)) {
      return candidate;
    }
  }
  return process.cwd();
}

function normalizeCommand(command) {
  return String(command || "")
    .replace(/\s+/g, " ")
    .trim();
}

export function isShellCommandAllowed(command, prefixes = CODEX_SHELL_ALLOWED_PREFIXES) {
  const normalized = normalizeCommand(command);
  if (!normalized) {
    return { ok: false, reason: "empty command" };
  }
  if (normalized.length > 2000) {
    return { ok: false, reason: "command too long" };
  }
  if (/[;&|`$<>]/.test(normalized)) {
    return { ok: false, reason: "shell metacharacters are not allowed" };
  }
  const lowered = normalized.toLowerCase();
  const deniedTerms = [
    ".sops",
    " sops ",
    " secret",
    "secrets",
    " token",
    "tokens",
    "password",
    "api_key",
    "apikey",
    "openai_api_key",
    "claude_code_oauth_token",
    "github_token",
    "gitsync_password",
    "printenv",
    " env",
    "set ",
  ];
  const denied = deniedTerms.find((term) => lowered.includes(term));
  if (denied) {
    return { ok: false, reason: `refused secret-like command term: ${denied.trim()}` };
  }
  const allowed = prefixes.some((prefix) => {
    const clean = normalizeCommand(prefix);
    if (!clean) {
      return false;
    }
    return normalized === clean || normalized.startsWith(clean.endsWith(" ") ? clean : `${clean} `);
  });
  if (!allowed) {
    return { ok: false, reason: "command prefix is not allowlisted" };
  }
  return { ok: true, reason: "allowed" };
}

function memoryRoot() {
  return path.resolve(CODEX_MEMORY_ROOT);
}

export function resolveMemoryPath(relativePath, root = CODEX_MEMORY_ROOT) {
  const raw = String(relativePath || "").trim();
  if (!raw) {
    const error = new Error("memory path is required");
    error.code = "MEMORY_PATH_INVALID";
    throw error;
  }
  if (raw.includes("\0")) {
    const error = new Error("memory path contains a NUL byte");
    error.code = "MEMORY_PATH_INVALID";
    throw error;
  }
  if (path.isAbsolute(raw)) {
    const error = new Error("memory path must be relative to the memory root");
    error.code = "MEMORY_PATH_INVALID";
    throw error;
  }

  const resolvedRoot = path.resolve(root);
  const target = path.resolve(resolvedRoot, raw);
  if (target !== resolvedRoot && !target.startsWith(`${resolvedRoot}${path.sep}`)) {
    const error = new Error("memory path escapes the memory root");
    error.code = "MEMORY_PATH_INVALID";
    throw error;
  }
  return target;
}

function relativeMemoryPath(filePath) {
  return path.relative(memoryRoot(), filePath) || ".";
}

function hasSecretLikeValue(text) {
  return redactText(text) !== String(text || "");
}

function ensureMemoryEnabled() {
  if (!CODEX_MEMORY_ENABLED) {
    const error = new Error("Codex memory tools are disabled");
    error.code = "MEMORY_DISABLED";
    throw error;
  }
}

function capMemoryBytes(content) {
  const maxBytes =
    Number.isFinite(CODEX_MEMORY_MAX_BYTES) && CODEX_MEMORY_MAX_BYTES > 0 ? CODEX_MEMORY_MAX_BYTES : 65536;
  const bytes = byteLength(content);
  if (bytes > maxBytes) {
    const error = new Error(`memory content is ${bytes} bytes; limit is ${maxBytes}`);
    error.code = "MEMORY_CONTENT_TOO_LARGE";
    throw error;
  }
}

function memoryResultError(name, error, traceId, started) {
  const result = {
    ok: false,
    tool: name,
    ...(traceId ? { trace_id: traceId } : {}),
    error: error?.message || String(error),
    duration_seconds: (performance.now() - started) / 1000,
  };
  appendJsonl(TRACE_LOG, {
    timestamp: new Date().toISOString(),
    backend: BACKEND_ID,
    tool: name,
    ...result,
  });
  inc(metrics.toolCalls, `${name}:error`);
  return result;
}

export async function runMemoryTool(name, args = {}, traceId) {
  const started = performance.now();
  try {
    ensureMemoryEnabled();
    const target = resolveMemoryPath(args.path || ".");
    const rel = relativeMemoryPath(target);

    if (name === "read_memory_file") {
      const raw = fs.readFileSync(target, "utf8");
      const content = truncateBytes(redactText(raw), CODEX_MEMORY_MAX_BYTES);
      const result = {
        ok: true,
        path: rel,
        ...(traceId ? { trace_id: traceId } : {}),
        content,
        truncated: byteLength(raw) > byteLength(content),
        duration_seconds: (performance.now() - started) / 1000,
      };
      appendJsonl(TRACE_LOG, {
        timestamp: new Date().toISOString(),
        backend: BACKEND_ID,
        tool: name,
        ...result,
        content: undefined,
      });
      inc(metrics.toolCalls, `${name}:ok`);
      return result;
    }

    if (name === "write_memory_file" || name === "append_memory_file") {
      const content = String(args.content ?? "");
      capMemoryBytes(content);
      if (hasSecretLikeValue(content)) {
        const error = new Error("memory content appears to contain a raw credential");
        error.code = "MEMORY_CONTENT_SECRET";
        throw error;
      }
      ensureParent(target);
      if (name === "write_memory_file") {
        fs.writeFileSync(target, content, "utf8");
      } else {
        fs.appendFileSync(target, content, "utf8");
      }
      const result = {
        ok: true,
        path: rel,
        ...(traceId ? { trace_id: traceId } : {}),
        bytes_written: byteLength(content),
        duration_seconds: (performance.now() - started) / 1000,
      };
      appendJsonl(TRACE_LOG, {
        timestamp: new Date().toISOString(),
        backend: BACKEND_ID,
        tool: name,
        ...result,
      });
      inc(metrics.toolCalls, `${name}:ok`);
      return result;
    }

    if (name === "list_memory_files") {
      const stat = fs.statSync(target);
      const entries = [];
      if (stat.isFile()) {
        entries.push(rel);
      } else if (stat.isDirectory()) {
        const walk = (dir) => {
          for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
            if (entries.length >= CODEX_MEMORY_MAX_LIST_ENTRIES) {
              return;
            }
            const fullPath = path.join(dir, entry.name);
            if (entry.isDirectory()) {
              walk(fullPath);
            } else if (entry.isFile()) {
              entries.push(relativeMemoryPath(fullPath));
            }
          }
        };
        walk(target);
      } else {
        const error = new Error("memory path is neither a file nor a directory");
        error.code = "MEMORY_PATH_UNSUPPORTED";
        throw error;
      }
      const result = {
        ok: true,
        path: rel,
        ...(traceId ? { trace_id: traceId } : {}),
        entries,
        truncated: entries.length >= CODEX_MEMORY_MAX_LIST_ENTRIES,
        duration_seconds: (performance.now() - started) / 1000,
      };
      appendJsonl(TRACE_LOG, {
        timestamp: new Date().toISOString(),
        backend: BACKEND_ID,
        tool: name,
        ...result,
      });
      inc(metrics.toolCalls, `${name}:ok`);
      return result;
    }

    const error = new Error(`unknown memory tool: ${name}`);
    error.code = "MEMORY_TOOL_UNKNOWN";
    throw error;
  } catch (error) {
    return memoryResultError(name, error, traceId, started);
  }
}

function redactText(text) {
  return String(text || "")
    .replace(/sk-[A-Za-z0-9_-]{16,}/g, "[REDACTED_OPENAI_KEY]")
    .replace(/github_pat_[A-Za-z0-9_]{20,}/g, "[REDACTED_GITHUB_PAT]")
    .replace(/gh[pousr]_[A-Za-z0-9_]{20,}/g, "[REDACTED_GITHUB_TOKEN]")
    .replace(/xox[baprs]-[A-Za-z0-9-]{20,}/g, "[REDACTED_SLACK_TOKEN]");
}

function logText(text) {
  return LOG_REDACT ? "[REDACTED]" : redactText(text);
}

function conversationBaseRecord({ ts, sessionId, contextId, messageId, model, status, traceId }) {
  return {
    ts,
    timestamp: ts,
    agent: AGENT_OWNER,
    agent_id: AGENT_ID,
    backend: BACKEND_ID,
    session_id_hash: sessionHash(sessionId),
    session_id: sessionId,
    context_id: contextId,
    message_id: messageId,
    model,
    status,
    ...(traceId ? { trace_id: traceId } : {}),
  };
}

function truncateBytes(text, maxBytes = CODEX_SHELL_MAX_OUTPUT_BYTES) {
  const raw = String(text || "");
  const bytes = Buffer.byteLength(raw, "utf8");
  if (!Number.isFinite(maxBytes) || maxBytes <= 0 || bytes <= maxBytes) {
    return raw;
  }
  const truncated = Buffer.from(raw, "utf8").subarray(0, maxBytes).toString("utf8");
  return `${truncated}\n[truncated ${bytes - Buffer.byteLength(truncated, "utf8")} bytes]`;
}

async function runShellCommand(command, traceId) {
  const normalized = normalizeCommand(command);
  const allowed = isShellCommandAllowed(normalized);
  if (!allowed.ok) {
    inc(metrics.toolCalls, "run_shell_command:refused");
    const result = {
      ok: false,
      command: normalized,
      refused: true,
      ...(traceId ? { trace_id: traceId } : {}),
      reason: allowed.reason,
    };
    appendJsonl(TRACE_LOG, {
      timestamp: new Date().toISOString(),
      backend: BACKEND_ID,
      tool: "run_shell_command",
      ...result,
    });
    return result;
  }

  const cwd = shellCwd();
  const started = performance.now();
  const timeoutMs = Math.max(1, CODEX_SHELL_TIMEOUT_SECONDS) * 1000;
  return await new Promise((resolve) => {
    let stdout = "";
    let stderr = "";
    let timedOut = false;
    const child = spawn("/bin/bash", ["-lc", normalized], {
      cwd,
      env: process.env,
      stdio: ["ignore", "pipe", "pipe"],
    });
    const timer = setTimeout(() => {
      timedOut = true;
      child.kill("SIGTERM");
      setTimeout(() => child.kill("SIGKILL"), 1000).unref();
    }, timeoutMs);

    child.stdout.on("data", (chunk) => {
      stdout += chunk.toString("utf8");
    });
    child.stderr.on("data", (chunk) => {
      stderr += chunk.toString("utf8");
    });
    child.on("error", (error) => {
      clearTimeout(timer);
      const result = {
        ok: false,
        command: normalized,
        cwd,
        ...(traceId ? { trace_id: traceId } : {}),
        error: error?.message || String(error),
        duration_seconds: (performance.now() - started) / 1000,
      };
      appendJsonl(TRACE_LOG, {
        timestamp: new Date().toISOString(),
        backend: BACKEND_ID,
        tool: "run_shell_command",
        ...result,
      });
      inc(metrics.toolCalls, "run_shell_command:error");
      resolve(result);
    });
    child.on("close", (code, signal) => {
      clearTimeout(timer);
      const result = {
        ok: code === 0 && !timedOut,
        command: normalized,
        cwd,
        ...(traceId ? { trace_id: traceId } : {}),
        exit_code: code,
        signal,
        timed_out: timedOut,
        stdout: truncateBytes(redactText(stdout)),
        stderr: truncateBytes(redactText(stderr)),
        duration_seconds: (performance.now() - started) / 1000,
      };
      appendJsonl(TRACE_LOG, {
        timestamp: new Date().toISOString(),
        backend: BACKEND_ID,
        tool: "run_shell_command",
        ...result,
      });
      inc(metrics.toolCalls, `run_shell_command:${result.ok ? "ok" : "error"}`);
      resolve(result);
    });
  });
}

export function mcpToolResultText(result) {
  const chunks = [];
  for (const item of result?.content || []) {
    if (item?.type === "text" && typeof item.text === "string") {
      chunks.push(item.text);
    } else if (item?.type === "resource" && typeof item.resource?.text === "string") {
      chunks.push(item.resource.text);
    } else if (item) {
      chunks.push(JSON.stringify(item));
    }
  }
  if (result?.structuredContent !== undefined) {
    chunks.push(JSON.stringify(result.structuredContent));
  }
  if (chunks.length === 0) {
    chunks.push(JSON.stringify(result || {}));
  }
  return truncateBytes(chunks.join("\n"), CODEX_MCP_MAX_OUTPUT_BYTES);
}

async function runMcpTool(functionName, args = {}, traceId) {
  const started = performance.now();
  try {
    await discoverMcpFunctionTools();
    const tool = mcpToolCache.toolIndex.get(functionName);
    if (!tool) {
      throw new Error(`unknown MCP function: ${functionName}`);
    }
    const result = await tool.client.callTool({ name: tool.toolName, arguments: args }, undefined, {
      timeout: CODEX_MCP_CLIENT_TIMEOUT_MS,
    });
    const output = mcpToolResultText(result);
    const response = {
      ok: !result?.isError,
      server: tool.serverName,
      tool: tool.toolName,
      ...(traceId ? { trace_id: traceId } : {}),
      output,
      duration_seconds: (performance.now() - started) / 1000,
    };
    appendJsonl(TRACE_LOG, {
      timestamp: new Date().toISOString(),
      backend: BACKEND_ID,
      tool: functionName,
      mcp_server: tool.serverName,
      mcp_tool: tool.toolName,
      ok: response.ok,
      ...(traceId ? { trace_id: traceId } : {}),
      duration_seconds: response.duration_seconds,
    });
    inc(metrics.toolCalls, `${functionName}:${response.ok ? "ok" : "error"}`);
    return response;
  } catch (error) {
    const response = {
      ok: false,
      tool: functionName,
      ...(traceId ? { trace_id: traceId } : {}),
      error: error?.message || String(error),
      duration_seconds: (performance.now() - started) / 1000,
    };
    appendJsonl(TRACE_LOG, {
      timestamp: new Date().toISOString(),
      backend: BACKEND_ID,
      tool: functionName,
      ...response,
    });
    inc(metrics.toolCalls, `${functionName}:error`);
    return response;
  }
}

export async function handleFunctionCall(call, traceId, context = {}) {
  const withToolSpan = async (fn) =>
    await runWithSpan(
      call?.name === "run_shell_command" ? "shell" : "tool.call",
      {
        kind: "internal",
        attributes: {
          "tool.name": call?.name || "",
          "trace.id": traceId || "",
          "agent.id": AGENT_ID,
          backend: BACKEND_ID,
        },
      },
      fn,
    );
  const buildContext = (toolName) =>
    toolTraceContext({
      ...context,
      toolUseId: call?.call_id || call?.id || context.toolUseId || `${toolName}-${crypto.randomUUID()}`,
    });

  if (call?.name === "run_shell_command") {
    let args = {};
    try {
      args = JSON.parse(call.arguments || "{}");
    } catch (error) {
      return {
        ok: false,
        refused: true,
        reason: `invalid function arguments: ${error?.message || String(error)}`,
      };
    }
    const toolInput = { command: args.command };
    const toolContext = buildContext("run_shell_command");
    const toolStarted = performance.now();
    appendToolUseEvent("run_shell_command", toolInput, traceId, toolContext);
    const denied = preToolUseGate("run_shell_command", toolInput, traceId, toolContext);
    if (denied) {
      appendToolResultEvent("run_shell_command", denied, traceId, toolContext);
      observeSdkToolCall("run_shell_command", toolInput, denied, (performance.now() - toolStarted) / 1000);
      return denied;
    }
    const result = await withToolSpan(async () => await runShellCommand(args.command, traceId));
    appendToolResultEvent("run_shell_command", result, traceId, toolContext);
    appendToolAuditEvent("run_shell_command", toolInput, result, traceId, toolContext);
    observeSdkToolCall("run_shell_command", toolInput, result, (performance.now() - toolStarted) / 1000);
    return result;
  }

  if (["read_memory_file", "write_memory_file", "append_memory_file", "list_memory_files"].includes(call?.name)) {
    let args = {};
    try {
      args = JSON.parse(call.arguments || "{}");
    } catch (error) {
      return {
        ok: false,
        refused: true,
        reason: `invalid function arguments: ${error?.message || String(error)}`,
      };
    }
    const toolContext = buildContext(call.name);
    const toolStarted = performance.now();
    appendToolUseEvent(call.name, args, traceId, toolContext);
    const denied = preToolUseGate(call.name, args, traceId, toolContext);
    if (denied) {
      appendToolResultEvent(call.name, denied, traceId, toolContext);
      observeSdkToolCall(call.name, args, denied, (performance.now() - toolStarted) / 1000);
      return denied;
    }
    const result = await withToolSpan(async () => await runMemoryTool(call.name, args, traceId));
    appendToolResultEvent(call.name, result, traceId, toolContext);
    appendToolAuditEvent(call.name, args, result, traceId, toolContext);
    observeSdkToolCall(call.name, args, result, (performance.now() - toolStarted) / 1000);
    return result;
  }

  if (mcpToolCache.toolIndex.has(call?.name)) {
    let args = {};
    try {
      args = JSON.parse(call.arguments || "{}");
    } catch (error) {
      return {
        ok: false,
        refused: true,
        reason: `invalid function arguments: ${error?.message || String(error)}`,
      };
    }
    const tool = mcpToolCache.toolIndex.get(call.name);
    const toolInput = { ...args, mcp_server: tool?.serverName || "", mcp_tool: tool?.toolName || "" };
    const toolContext = buildContext(call.name);
    const toolStarted = performance.now();
    appendToolUseEvent(call.name, toolInput, traceId, toolContext);
    const denied = preToolUseGate(
      call.name,
      toolInput,
      traceId,
      toolContext,
    );
    if (denied) {
      appendToolResultEvent(call.name, denied, traceId, toolContext);
      observeSdkToolCall(call.name, toolInput, denied, (performance.now() - toolStarted) / 1000);
      return denied;
    }
    const result = await withToolSpan(async () => await runMcpTool(call.name, args, traceId));
    appendToolResultEvent(call.name, result, traceId, toolContext);
    appendToolAuditEvent(call.name, toolInput, result, traceId, toolContext);
    observeSdkToolCall(call.name, toolInput, result, (performance.now() - toolStarted) / 1000);
    observeMcpOutboundCall(tool?.serverName, tool?.toolName, result?.ok === false ? "error" : "ok", result?.duration_seconds);
    return result;
  }

  {
    return {
      ok: false,
      refused: true,
      reason: `unknown function: ${call?.name || ""}`,
    };
  }
}

function extractOutputText(response) {
  if (response?.output_text) {
    return response.output_text;
  }
  const chunks = [];
  for (const item of response?.output || []) {
    if (item?.type !== "message") {
      continue;
    }
    for (const part of item.content || []) {
      if (typeof part?.text === "string") {
        chunks.push(part.text);
      }
    }
  }
  return chunks.join("\n");
}

function usageTotalTokens(response) {
  const usage = response?.usage;
  const candidates = [
    usage?.total_tokens,
    usage?.totalTokens,
    usage?.input_tokens !== undefined || usage?.output_tokens !== undefined
      ? Number(usage.input_tokens || 0) + Number(usage.output_tokens || 0)
      : undefined,
  ];
  for (const candidate of candidates) {
    const parsed = Number.parseInt(String(candidate), 10);
    if (Number.isFinite(parsed) && parsed >= 0) {
      return parsed;
    }
  }
  return 0;
}

function budgetResult(response, maxTokens) {
  const totalTokens = usageTotalTokens(response);
  if (totalTokens > 0) {
    metrics.contextTokensCount += 1;
    metrics.contextTokensSum += totalTokens;
  }
  if (maxTokens !== undefined && totalTokens > 0) {
    metrics.contextTokensRemainingCount += 1;
    metrics.contextTokensRemainingSum += Math.max(0, maxTokens - totalTokens);
  }
  return {
    total_tokens: totalTokens,
    max_tokens: maxTokens,
    exceeded: maxTokens !== undefined && totalTokens >= maxTokens,
  };
}

function appendBudgetNotice(text, budget) {
  if (!budget?.exceeded) {
    return text;
  }
  const notice = `Token budget exceeded: ${budget.total_tokens} tokens used of ${budget.max_tokens} limit.`;
  return text ? `${text}\n\n${notice}` : notice;
}

async function getOpenAIClient() {
  if (!openaiClientPromise) {
    openaiClientPromise = import("openai").then((mod) => new mod.default());
  }
  return openaiClientPromise;
}

function shouldUseStub() {
  if (process.env.CODEX_STUB_MODE !== undefined) {
    return parseBool(process.env.CODEX_STUB_MODE);
  }
  return !process.env.OPENAI_API_KEY;
}

export async function collectStreamingResponse(stream, onTextDelta) {
  let completedResponse;
  for await (const event of stream) {
    if (event?.type === "response.output_text.delta" && typeof event.delta === "string" && event.delta) {
      onTextDelta?.(event.delta);
    } else if (event?.type === "response.completed") {
      completedResponse = event.response;
    } else if (event?.type === "response.failed") {
      const message = event?.response?.error?.message || event?.error?.message || "Responses stream failed";
      throw new Error(message);
    }
  }
  if (!completedResponse) {
    throw new Error("Responses stream ended without a completed response");
  }
  return completedResponse;
}

async function createResponse(client, request, onTextDelta, spanAttributes = {}) {
  return await runWithSpan(
    "llm.request",
    {
      kind: "client",
      attributes: {
        "llm.provider": "openai",
        "llm.request.model": request.model,
        "llm.request.reasoning_effort": request.reasoning?.effort || "",
        "llm.request.streaming": CODEX_RESPONSES_STREAMING && onTextDelta ? "true" : "false",
        ...spanAttributes,
      },
    },
    async () => {
      if (CODEX_RESPONSES_STREAMING && onTextDelta) {
        const stream = await client.responses.create({ ...request, stream: true });
        return await collectStreamingResponse(stream, onTextDelta);
      }
      return await client.responses.create(request);
    },
  );
}

async function createResponseWithSessionFallback(client, request, sessionId, onTextDelta, spanAttributes = {}) {
  try {
    return await createResponse(client, request, onTextDelta, spanAttributes);
  } catch (error) {
    if (!request.previous_response_id) {
      throw error;
    }
    const message = String(error?.message || error);
    if (!isRecoverableSessionResumeError(message)) {
      throw error;
    }
    loadSessionStore().delete(sessionId);
    saveSessionStore();
    const retry = { ...request };
    delete retry.previous_response_id;
    return await createResponse(client, retry, onTextDelta, { ...spanAttributes, "llm.request.session_retry": "true" });
  }
}

export function isRecoverableSessionResumeError(message) {
  return /previous[_ ]response|previous_response_id|not found|expired|no tool output found for function call/i.test(
    String(message || ""),
  );
}

export function responseFunctionCalls(response) {
  return (response?.output || []).filter((item) => item?.type === "function_call");
}

function recordSessionIfComplete(sessionId, response, model) {
  if (response?.id && responseFunctionCalls(response).length === 0) {
    recordSessionResponse(sessionId, response.id, model);
  }
}

async function runCodex(prompt, metadata, sessionId, candidateSessionIds = [sessionId], hooks = {}) {
  const model = modelForRequest(metadata);
  const instructions = loadInstructions();
  const traceId = traceIdForMetadata(metadata);
  const maxTokens = maxTokensForRequest(metadata);
  if (shouldUseStub()) {
    return {
      model,
      text:
        "codex backend scaffold — prompt received, but CODEX_STUB_MODE is active or OPENAI_API_KEY is unset. " +
        "Set OPENAI_API_KEY and CODEX_STUB_MODE=false to execute through the OpenAI Responses API.",
      total_tokens: 0,
      budget_exceeded: false,
    };
  }

  const client = await getOpenAIClient();
  const request = {
    model,
    input: prompt,
  };
  if (instructions) {
    request.instructions = instructions;
  }
  const reasoning = reasoningForRequest(metadata);
  if (reasoning) {
    request.reasoning = reasoning;
  }
  const maxOutputTokens = maxOutputTokensForRequest(metadata);
  if (maxOutputTokens !== undefined) {
    request.max_output_tokens = maxOutputTokens;
  }
  const { sessionId: storedSessionId, stored: storedSession } = getStoredSessionFromCandidates(candidateSessionIds);
  if (storedSession?.previous_response_id) {
    request.previous_response_id = storedSession.previous_response_id;
  }
  const tools = await codexToolDefinitions();
  if (tools.length > 0) {
    request.tools = tools;
    request.parallel_tool_calls = false;
  }

  let response = await createResponseWithSessionFallback(
    client,
    request,
    storedSessionId || sessionId,
    hooks.onAssistantDelta,
    { "session.id_hash": sessionHash(sessionId), "response.previous_id": request.previous_response_id || "" },
  );
  recordSessionIfComplete(sessionId, response, model);
  let budget = budgetResult(response, maxTokens);
  let toolCallsThisQuery = 0;
  if (budget.exceeded) {
    metrics.budgetExceededTotal += 1;
  }
  if (tools.length > 0) {
    const maxIterations = maxToolIterationsForRequest(metadata);
    for (let iteration = 0; iteration < maxIterations; iteration += 1) {
      if (budget.exceeded) {
        break;
      }
      const calls = responseFunctionCalls(response);
      if (calls.length === 0) {
        break;
      }
      toolCallsThisQuery += calls.length;
      const input = [];
      for (const call of calls) {
        const output = await handleFunctionCall(call, traceId, { sessionId, model });
        input.push({
          type: "function_call_output",
          call_id: call.call_id,
          output: JSON.stringify(output),
        });
      }
      response = await createResponse(
        client,
        {
          model,
          input,
          previous_response_id: response.id,
          tools,
          parallel_tool_calls: false,
          ...(instructions ? { instructions } : {}),
          ...(reasoning ? { reasoning } : {}),
          ...(maxOutputTokens !== undefined ? { max_output_tokens: maxOutputTokens } : {}),
        },
        hooks.onAssistantDelta,
        { "session.id_hash": sessionHash(sessionId), "response.previous_id": response.id || "", "tool.loop": "true" },
      );
      recordSessionIfComplete(sessionId, response, model);
      budget = budgetResult(response, maxTokens);
      if (budget.exceeded) {
        metrics.budgetExceededTotal += 1;
      }
    }
  }
  metrics.sdkToolCallsPerQueryCount += 1;
  metrics.sdkToolCallsPerQuerySum += toolCallsThisQuery;
  const pendingCalls = responseFunctionCalls(response);
  if (pendingCalls.length > 0) {
    throw new Error(
      `tool iteration limit exceeded after ${toolCallsThisQuery} tool call(s); ${pendingCalls.length} tool call(s) still pending`,
    );
  }
  const text = extractOutputText(response) || JSON.stringify(response.output || response);
  return {
    model,
    text: appendBudgetNotice(text, budget),
    total_tokens: budget.total_tokens,
    budget_exceeded: budget.exceeded,
  };
}

export async function handleA2A(payload) {
  const id = payload?.id ?? null;
  if (!payload || payload.jsonrpc !== "2.0") {
    return a2aError(id, -32600, "Invalid JSON-RPC request");
  }
  if (!["message/send", "tasks/send"].includes(payload.method)) {
    return a2aError(id, -32601, `Unsupported method: ${payload.method || ""}`);
  }

  const requestStarted = performance.now();
  const { metadata, contextId, messageId } = extractRequestMetadata(payload);
  const { sessionId, candidateSessionIds } = sessionForRequest(metadata, contextId);
  const prompt = extractPrompt(payload).trim();
  const promptBytes = byteLength(prompt);
  const traceId = traceIdForMetadata(metadata);
  const otelParentContext =
    typeof metadata?.traceparent === "string" && metadata.traceparent
      ? extractOtelContext({ traceparent: metadata.traceparent })
      : undefined;
  metrics.promptBytesCount += 1;
  metrics.promptBytesSum += promptBytes;
  metrics.lastA2ARequestTimestamp = Date.now() / 1000;
  if (prompt) {
    publishSessionChunk(sessionId, { role: "user", seq: 0, content: prompt, final: true });
  }

  let status = "ok";
  let responseText = "";
  let model = modelForRequest(metadata);
  inc(metrics.modelRequests, sanitizeModelLabel(model));
  let totalTokens = 0;
  let assistantSeq = 1;
  const publishAssistantDelta = (content) => {
    publishSessionChunk(sessionId, { role: "assistant", seq: assistantSeq, content, final: false, model });
    assistantSeq += 1;
  };
  try {
    if (!prompt) {
      status = "error";
      metrics.emptyPromptsTotal += 1;
      responseText = "codex backend — received an empty prompt. Send text for Codex to work on.";
    } else if (MAX_PROMPT_BYTES > 0 && promptBytes > MAX_PROMPT_BYTES) {
      status = "error";
      metrics.promptTooLargeTotal += 1;
      responseText = `codex backend — prompt of ${promptBytes} bytes exceeds MAX_PROMPT_BYTES=${MAX_PROMPT_BYTES}.`;
    } else {
      const result = await runWithSpan(
        "backend.a2a.execute",
        {
          kind: "server",
          parentContext: otelParentContext,
          attributes: {
            "agent.id": AGENT_ID,
            "session.id_hash": sessionHash(sessionId),
            "a2a.method": payload.method,
            "llm.request.model": model,
            "llm.request.reasoning_effort": reasoningForRequest(metadata)?.effort || "",
          },
        },
        async () =>
          await runCodex(prompt, metadata, sessionId, candidateSessionIds, {
            onAssistantDelta: publishAssistantDelta,
          }),
      );
      model = result.model;
      responseText = result.text;
      totalTokens = result.total_tokens || 0;
      if (result.budget_exceeded) {
        status = "budget_exceeded";
      }
    }
  } catch (error) {
    status = "error";
    responseText = `codex backend error: ${error?.message || String(error)}`;
  } finally {
    inc(metrics.a2aRequests, status);
    metrics.a2aDurationCount += 1;
    metrics.a2aDurationSum += (performance.now() - requestStarted) / 1000;
  }

  metrics.responseBytesCount += 1;
  metrics.responseBytesSum += byteLength(responseText);
  const ts = new Date().toISOString();
  const baseRecord = conversationBaseRecord({ ts, sessionId, contextId, messageId, model, status, traceId });
  appendJsonl(CONVERSATION_LOG, {
    ...baseRecord,
    role: "user",
    tokens: null,
    text: logText(prompt),
    prompt: logText(prompt),
  });
  appendJsonl(CONVERSATION_LOG, {
    ...baseRecord,
    role: "agent",
    tokens: totalTokens > 0 ? totalTokens : null,
    text: logText(responseText),
    response: logText(responseText),
  });
  if (responseText) {
    publishSessionChunk(sessionId, { role: "assistant", seq: assistantSeq, content: responseText, final: true });
  }

  return {
    status: 200,
    body: a2aMessage(id, responseText, contextId, {
      backend: BACKEND_ID,
      model,
      status,
      session_id_hash: sessionHash(sessionId),
      ...(traceId ? { trace_id: traceId } : {}),
    }),
  };
}

function jsonResponse(res, status, body) {
  const raw = JSON.stringify(body);
  res.writeHead(status, {
    "content-type": "application/json; charset=utf-8",
    "content-length": Buffer.byteLength(raw),
  });
  res.end(raw);
}

function textResponse(res, status, body, contentType = "text/plain; charset=utf-8") {
  res.writeHead(status, {
    "content-type": contentType,
    "content-length": Buffer.byteLength(body),
  });
  res.end(body);
}

export function constantTimeBearerTokenMatches(authorizationHeader, expectedToken) {
  if (!expectedToken) {
    return false;
  }
  const expected = `Bearer ${expectedToken}`;
  const presented = String(authorizationHeader || "");
  const expectedDigest = crypto.createHash("sha256").update(expected).digest();
  const presentedDigest = crypto.createHash("sha256").update(presented).digest();
  return crypto.timingSafeEqual(expectedDigest, presentedDigest);
}

export function conversationsAuthConfigWarning(
  authToken = CONVERSATIONS_AUTH_TOKEN,
  authDisabled = CONVERSATIONS_AUTH_DISABLED,
) {
  if (authToken) {
    return "";
  }
  if (authDisabled) {
    return (
      "codex backend: CONVERSATIONS_AUTH_DISABLED=true - authentication is DISABLED and " +
      "logs are readable by any caller. Use only for local development."
    );
  }
  return (
    "codex backend: CONVERSATIONS_AUTH_TOKEN is unset or empty and CONVERSATIONS_AUTH_DISABLED " +
    "is not set - protected endpoints will fail closed (503). Set a non-empty token, or set " +
    "CONVERSATIONS_AUTH_DISABLED=true to acknowledge disabled auth for local dev."
  );
}

function authOk(req) {
  if (CONVERSATIONS_AUTH_DISABLED) {
    return true;
  }
  if (!CONVERSATIONS_AUTH_TOKEN) {
    return false;
  }
  return constantTimeBearerTokenMatches(req.headers.authorization, CONVERSATIONS_AUTH_TOKEN);
}

function protectedRoute(req, res) {
  if (CONVERSATIONS_AUTH_DISABLED || (CONVERSATIONS_AUTH_TOKEN && authOk(req))) {
    return false;
  }
  if (!CONVERSATIONS_AUTH_TOKEN) {
    jsonResponse(res, 503, { error: "auth not configured" });
  } else {
    jsonResponse(res, 401, {
      error: "protected endpoint requires Authorization: Bearer <CONVERSATIONS_AUTH_TOKEN>",
    });
  }
  return true;
}

async function readBody(req, maxBytes = 10 * 1024 * 1024) {
  const chunks = [];
  let total = 0;
  for await (const chunk of req) {
    total += chunk.length;
    if (total > maxBytes) {
      const error = new Error("body too large");
      error.code = "BODY_TOO_LARGE";
      throw error;
    }
    chunks.push(chunk);
  }
  return Buffer.concat(chunks).toString("utf8");
}

async function handleMcp(req, res) {
  if (protectedRoute(req, res)) {
    return;
  }
  const started = performance.now();
  let status = "ok";
  let methodLabel = "unknown";
  let rpcId = null;
  const inboundTraceId = traceIdForMetadata({ traceparent: req.headers.traceparent });
  try {
    const declaredLength = Number.parseInt(String(req.headers["content-length"] || "-1"), 10);
    if (Number.isFinite(declaredLength) && declaredLength > MCP_MAX_BODY_BYTES) {
      status = "body_too_large";
      return jsonResponse(res, 413, {
        jsonrpc: "2.0",
        id: null,
        error: { code: -32600, message: "body too large" },
      });
    }
    const raw = await readBody(req, MCP_MAX_BODY_BYTES);
    const payload = JSON.parse(raw || "{}");
    rpcId = payload.id ?? null;
    const method = payload.method || "";
    if (typeof method === "string" && method) {
      methodLabel = method;
    }
    let result;
    if (method === "initialize") {
      const supportedVersions = ["2024-11-05", "2025-03-26"];
      const clientVersion = payload.params?.protocolVersion;
      const protocolVersion = supportedVersions.includes(clientVersion)
        ? clientVersion
        : supportedVersions[supportedVersions.length - 1];
      result = {
        protocolVersion,
        capabilities: { tools: { listChanged: false } },
        serverInfo: { name: "witwave-codex-backend", version: AGENT_VERSION },
      };
    } else if (method === "tools/list") {
      result = {
        tools: [
          {
            name: "ask_agent",
            description: "Ask the Codex backend to respond to a prompt.",
            inputSchema: {
              type: "object",
              properties: {
                prompt: { type: "string" },
                session_id: {
                  type: "string",
                  description: "Optional session identifier for conversation continuity.",
                },
                max_tokens: {
                  type: "integer",
                  minimum: 1,
                  description: "Optional per-call total-token budget.",
                },
              },
              required: ["prompt"],
            },
          },
        ],
      };
    } else if (method === "tools/call") {
      const name = payload.params?.name;
      if (!["ask_agent", "ask_codex"].includes(name)) {
        status = "unknown_tool";
        jsonResponse(res, 200, {
          jsonrpc: "2.0",
          id: payload.id ?? null,
          result: {
            isError: true,
            content: [{ type: "text", text: `Unknown tool: ${name || ""}` }],
          },
        });
        return;
      }
      const prompt = payload.params?.arguments?.prompt || "";
      if (!prompt) {
        status = "missing_prompt";
        jsonResponse(res, 200, {
          jsonrpc: "2.0",
          id: rpcId,
          error: { code: -32602, message: "Missing required argument: prompt" },
        });
        return;
      }
      const args = { ...(payload.params?.arguments || {}) };
      if (req.headers.traceparent && !args.traceparent) {
        args.traceparent = req.headers.traceparent;
      }
      const rawSessionId = sanitizeRawSessionId(args.session_id || "");
      const callerIdentity = callerIdentityFromRequest(req);
      const candidateSessionIds = deriveSessionCandidates(rawSessionId, callerIdentity);
      const resultText = await runWithSpan(
        "backend.mcp.tools_call",
        {
          kind: "server",
          parentContext: req.headers.traceparent
            ? extractOtelContext({ traceparent: String(req.headers.traceparent) })
            : undefined,
          attributes: {
            "tool.name": name,
            "agent.id": AGENT_ID,
            "session.id_hash": sessionHash(candidateSessionIds[0]),
          },
        },
        async () => await runCodex(String(prompt), args, candidateSessionIds[0], candidateSessionIds),
      );
      result = { content: [{ type: "text", text: resultText.text }] };
    } else {
      status = "method_not_found";
      jsonResponse(res, 200, {
        jsonrpc: "2.0",
        id: rpcId,
        error: { code: -32601, message: `Unsupported method: ${method}` },
      });
      return;
    }
    jsonResponse(res, 200, { jsonrpc: "2.0", id: rpcId, result });
  } catch (error) {
    status = error?.code === "BODY_TOO_LARGE" ? "body_too_large" : "error";
    jsonResponse(res, error?.code === "BODY_TOO_LARGE" ? 413 : 400, {
      jsonrpc: "2.0",
      id: rpcId,
      error: {
        code: error?.code === "BODY_TOO_LARGE" ? -32600 : -32700,
        message: error?.code === "BODY_TOO_LARGE" ? "body too large" : error?.message || "Parse error",
      },
    });
  } finally {
    inc(metrics.mcpRequests, `${methodLabel}:${status}`);
    inc(metrics.mcpDurationCounts, methodLabel);
    inc(metrics.mcpDurations, methodLabel, (performance.now() - started) / 1000);
    appendJsonl(TRACE_LOG, {
      timestamp: new Date().toISOString(),
      agent: AGENT_OWNER,
      agent_id: AGENT_ID,
      backend: BACKEND_ID,
      endpoint: "/mcp",
      status,
      ...(inboundTraceId ? { trace_id: inboundTraceId } : {}),
      duration_seconds: (performance.now() - started) / 1000,
    });
  }
}

function handleHealth(probe, res) {
  inc(metrics.healthChecks, probe);
  const uptime = (Date.now() - STARTED_AT.getTime()) / 1000;
  const body = {
    status: probe === "ready" && !ready ? "starting" : ready ? "ok" : "starting",
    agent: AGENT_NAME,
    agent_owner: AGENT_OWNER,
    agent_id: AGENT_ID,
    backend: BACKEND_ID,
    uptime_seconds: uptime,
  };
  jsonResponse(res, probe === "ready" && !ready ? 503 : 200, body);
}

function handleConversations(req, res) {
  if (protectedRoute(req, res)) {
    return;
  }
  const url = new URL(req.url || "/", "http://localhost");
  try {
    jsonResponse(
      res,
      200,
      readJsonlEntries(CONVERSATION_LOG, {
        since: url.searchParams.get("since"),
        limit: url.searchParams.get("limit"),
      }),
    );
  } catch (error) {
    jsonResponse(res, error?.code === "INVALID_LIMIT" || error?.code === "INVALID_SINCE" ? 400 : 500, {
      error: error?.message || String(error),
    });
  }
}

function handleTrace(req, res) {
  if (protectedRoute(req, res)) {
    return;
  }
  const url = new URL(req.url || "/", "http://localhost");
  try {
    jsonResponse(
      res,
      200,
      readJsonlEntries(TRACE_LOG, {
        since: url.searchParams.get("since"),
        limit: url.searchParams.get("limit"),
      }),
    );
  } catch (error) {
    jsonResponse(res, error?.code === "INVALID_LIMIT" || error?.code === "INVALID_SINCE" ? 400 : 500, {
      error: error?.message || String(error),
    });
  }
}

function handleApiTraces(req, res) {
  if (protectedRoute(req, res)) {
    return;
  }
  const url = new URL(req.url || "/", "http://localhost");
  const traceMatch = url.pathname.match(/^\/api\/traces\/([0-9a-fA-F]{32})$/);
  try {
    const traces = getInMemoryTraces();
    if (traceMatch) {
      const traceId = traceMatch[1].toLowerCase();
      const match = traces.find((traceItem) => traceItem.traceID === traceId);
      if (!match) {
        return jsonResponse(res, 404, { data: [], total: 0 });
      }
      return jsonResponse(res, 200, { data: [match], total: 1, limit: 1, offset: 0 });
    }
    const cap = parsePositiveInt(process.env.OTEL_IN_MEMORY_SPANS, 1000);
    const limitRaw = url.searchParams.get("limit");
    const offsetRaw = url.searchParams.get("offset");
    const limit = limitRaw ? Number.parseInt(limitRaw, 10) : 20;
    const offset = offsetRaw ? Number.parseInt(offsetRaw, 10) : 0;
    const safeLimit = Number.isFinite(limit) && limit > 0 ? Math.min(limit, cap) : 20;
    const safeOffset = Number.isFinite(offset) && offset > 0 ? offset : 0;
    jsonResponse(res, 200, {
      data: traces.slice(safeOffset, safeOffset + safeLimit),
      total: traces.length,
      limit: safeLimit,
      offset: safeOffset,
    });
  } catch (error) {
    jsonResponse(res, 500, { error: error?.message || String(error) });
  }
}

function handleSessionStream(req, res, sessionId) {
  if (protectedRoute(req, res)) {
    return;
  }
  const cleanSessionId = String(sessionId || "").trim();
  if (!cleanSessionId) {
    return jsonResponse(res, 400, { error: "missing session_id" });
  }

  const url = new URL(req.url || "/", "http://localhost");
  const callerFingerprint = sessionStreamCallerFingerprint(req);
  let released = false;
  const releaseCallerSlot = () => {
    if (SESSION_STREAM_MAX_PER_CALLER <= 0 || released) {
      return;
    }
    released = true;
    const current = sessionStreamCallerCounts.get(callerFingerprint) || 0;
    if (current <= 1) {
      sessionStreamCallerCounts.delete(callerFingerprint);
    } else {
      sessionStreamCallerCounts.set(callerFingerprint, current - 1);
    }
  };
  if (SESSION_STREAM_MAX_PER_CALLER > 0) {
    const current = sessionStreamCallerCounts.get(callerFingerprint) || 0;
    if (current >= SESSION_STREAM_MAX_PER_CALLER) {
      return jsonResponse(res, 429, { error: "too many concurrent streams for this caller" });
    }
    sessionStreamCallerCounts.set(callerFingerprint, current + 1);
  }

  const stream = getSessionStream(cleanSessionId, { create: true });
  const lastEventId = req.headers["last-event-id"] || url.searchParams.get("last_event_id");
  const replay = lastEventId ? stream.ring.filter((envelope) => Number(envelope.id) > Number(lastEventId)) : [];
  const keepaliveMs = Math.max(1, CONVERSATION_STREAM_KEEPALIVE_SEC) * 1000;

  res.writeHead(200, {
    "content-type": "text/event-stream",
    "cache-control": "no-cache",
    "x-accel-buffering": "no",
    connection: "keep-alive",
  });
  res.write(": stream-start\n\n");
  for (const envelope of replay) {
    res.write(sseSerialize(envelope));
  }

  stream.subscribers.add(res);
  const keepalive = setInterval(() => {
    try {
      res.write(": keepalive\n\n");
    } catch {
      clearInterval(keepalive);
      stream.subscribers.delete(res);
      releaseCallerSlot();
      scheduleSessionStreamCleanup(stream);
    }
  }, keepaliveMs);
  keepalive.unref?.();

  req.on("close", () => {
    clearInterval(keepalive);
    stream.subscribers.delete(res);
    releaseCallerSlot();
    scheduleSessionStreamCleanup(stream);
  });
  return undefined;
}

function labels(extra = {}) {
  return { agent: AGENT_OWNER, agent_id: AGENT_ID, backend: BACKEND_ID, ...extra };
}

function escapeLabel(value) {
  return String(value).replace(/\\/g, "\\\\").replace(/"/g, '\\"').replace(/\n/g, "\\n");
}

function metricLine(name, value, labelValues = {}) {
  const entries = Object.entries(labelValues);
  const suffix = entries.length ? `{${entries.map(([key, val]) => `${key}="${escapeLabel(val)}"`).join(",")}}` : "";
  return `${name}${suffix} ${value}`;
}

export function renderMetrics() {
  const uptime = (Date.now() - STARTED_AT.getTime()) / 1000;
  const lines = [
    "# HELP backend_up Whether the backend process is running.",
    "# TYPE backend_up gauge",
    metricLine("backend_up", ready ? 1 : 0, labels()),
    "# HELP backend_info Backend identity and version.",
    "# TYPE backend_info gauge",
    metricLine("backend_info", 1, labels({ version: AGENT_VERSION })),
    "# HELP backend_uptime_seconds Backend process uptime in seconds.",
    "# TYPE backend_uptime_seconds gauge",
    metricLine("backend_uptime_seconds", uptime.toFixed(3), labels()),
    "# HELP backend_startup_duration_seconds Backend startup duration in seconds.",
    "# TYPE backend_startup_duration_seconds gauge",
    metricLine("backend_startup_duration_seconds", startupDurationSeconds.toFixed(3), labels()),
    "# HELP backend_health_checks_total Total health checks by probe.",
    "# TYPE backend_health_checks_total counter",
  ];

  for (const [probe, value] of metrics.healthChecks.entries()) {
    lines.push(metricLine("backend_health_checks_total", value, labels({ probe })));
  }

  lines.push(
    "# HELP backend_a2a_requests_total Total A2A requests by terminal status.",
    "# TYPE backend_a2a_requests_total counter",
  );
  for (const [status, value] of metrics.a2aRequests.entries()) {
    lines.push(metricLine("backend_a2a_requests_total", value, labels({ status })));
  }

  lines.push(
    "# HELP backend_model_requests_total Total requests per resolved model.",
    "# TYPE backend_model_requests_total counter",
  );
  for (const [model, value] of metrics.modelRequests.entries()) {
    lines.push(metricLine("backend_model_requests_total", value, labels({ model })));
  }

  lines.push(
    "# HELP backend_a2a_last_request_timestamp_seconds Unix timestamp of the most recent A2A request.",
    "# TYPE backend_a2a_last_request_timestamp_seconds gauge",
    metricLine("backend_a2a_last_request_timestamp_seconds", metrics.lastA2ARequestTimestamp || 0, labels()),
    "# HELP backend_a2a_request_duration_seconds A2A request duration summary.",
    "# TYPE backend_a2a_request_duration_seconds summary",
    metricLine("backend_a2a_request_duration_seconds_count", metrics.a2aDurationCount, labels()),
    metricLine("backend_a2a_request_duration_seconds_sum", metrics.a2aDurationSum, labels()),
    "# HELP backend_log_entries_total Total entries written to backend JSONL logs.",
    "# TYPE backend_log_entries_total counter",
  );
  for (const [logger, value] of metrics.logEntries.entries()) {
    lines.push(metricLine("backend_log_entries_total", value, labels({ logger })));
  }
  lines.push(
    "# HELP backend_log_bytes_total Total bytes written to backend JSONL logs.",
    "# TYPE backend_log_bytes_total counter",
  );
  for (const [logger, value] of metrics.logBytes.entries()) {
    lines.push(metricLine("backend_log_bytes_total", value, labels({ logger })));
  }
  lines.push(
    "# HELP backend_log_write_errors_total Total I/O failures in the conversation/trace logging subsystem.",
    "# TYPE backend_log_write_errors_total counter",
    metricLine("backend_log_write_errors_total", metrics.logWriteErrorsTotal, labels()),
    "# HELP backend_log_write_errors_by_logger_total Total log write errors grouped by logger.",
    "# TYPE backend_log_write_errors_by_logger_total counter",
  );
  for (const [logger, value] of metrics.logWriteErrorsByLogger.entries()) {
    lines.push(metricLine("backend_log_write_errors_by_logger_total", value, labels({ logger })));
  }
  lines.push(
    "# HELP backend_prompt_length_bytes Prompt byte length summary.",
    "# TYPE backend_prompt_length_bytes summary",
    metricLine("backend_prompt_length_bytes_count", metrics.promptBytesCount, labels()),
    metricLine("backend_prompt_length_bytes_sum", metrics.promptBytesSum, labels()),
    "# HELP backend_response_length_bytes Response byte length summary.",
    "# TYPE backend_response_length_bytes summary",
    metricLine("backend_response_length_bytes_count", metrics.responseBytesCount, labels()),
    metricLine("backend_response_length_bytes_sum", metrics.responseBytesSum, labels()),
    "# HELP backend_empty_prompts_total Empty prompts rejected.",
    "# TYPE backend_empty_prompts_total counter",
    metricLine("backend_empty_prompts_total", metrics.emptyPromptsTotal, labels()),
    "# HELP backend_prompt_too_large_total Prompts rejected by MAX_PROMPT_BYTES.",
    "# TYPE backend_prompt_too_large_total counter",
    metricLine("backend_prompt_too_large_total", metrics.promptTooLargeTotal, labels()),
    "# HELP backend_budget_exceeded_total Requests stopped after exceeding max_tokens.",
    "# TYPE backend_budget_exceeded_total counter",
    metricLine("backend_budget_exceeded_total", metrics.budgetExceededTotal, labels()),
    "# HELP backend_context_tokens Context tokens observed from model usage.",
    "# TYPE backend_context_tokens summary",
    metricLine("backend_context_tokens_count", metrics.contextTokensCount, labels()),
    metricLine("backend_context_tokens_sum", metrics.contextTokensSum, labels()),
    "# HELP backend_context_tokens_remaining Remaining tokens before max_tokens budget.",
    "# TYPE backend_context_tokens_remaining summary",
    metricLine("backend_context_tokens_remaining_count", metrics.contextTokensRemainingCount, labels()),
    metricLine("backend_context_tokens_remaining_sum", metrics.contextTokensRemainingSum, labels()),
    "# HELP backend_active_sessions Active persisted Codex response sessions.",
    "# TYPE backend_active_sessions gauge",
    metricLine("backend_active_sessions", loadSessionStore().size, labels()),
    "# HELP backend_session_starts_total Sessions first seen by this backend process.",
    "# TYPE backend_session_starts_total counter",
    metricLine("backend_session_starts_total", metrics.sessionStartsTotal, labels()),
    "# HELP backend_session_evictions_total Sessions evicted due to MAX_SESSIONS.",
    "# TYPE backend_session_evictions_total counter",
    metricLine("backend_session_evictions_total", metrics.sessionEvictionsTotal, labels()),
    "# HELP backend_streaming_events_emitted_total Total partial assistant text chunks enqueued during streaming.",
    "# TYPE backend_streaming_events_emitted_total counter",
  );
  for (const [model, value] of metrics.streamingEventsEmitted.entries()) {
    lines.push(metricLine("backend_streaming_events_emitted_total", value, labels({ model })));
  }
  lines.push(
    "# HELP backend_streaming_chunks_dropped_total Total streaming chunks dropped after subscriber write failures.",
    "# TYPE backend_streaming_chunks_dropped_total counter",
  );
  for (const [model, value] of metrics.streamingChunksDropped.entries()) {
    lines.push(metricLine("backend_streaming_chunks_dropped_total", value, labels({ model })));
  }
  lines.push(
    "# HELP backend_mcp_requests_total MCP requests by terminal status.",
    "# TYPE backend_mcp_requests_total counter",
  );
  for (const [status, value] of metrics.mcpRequests.entries()) {
    const [method, terminalStatus] = status.split(":", 2);
    lines.push(metricLine("backend_mcp_requests_total", value, labels({ method, status: terminalStatus })));
  }
  lines.push(
    "# HELP backend_mcp_request_duration_seconds MCP request duration summary.",
    "# TYPE backend_mcp_request_duration_seconds summary",
  );
  for (const [method, value] of metrics.mcpDurationCounts.entries()) {
    lines.push(metricLine("backend_mcp_request_duration_seconds_count", value, labels({ method })));
    lines.push(
      metricLine("backend_mcp_request_duration_seconds_sum", metrics.mcpDurations.get(method) || 0, labels({ method })),
    );
  }
  lines.push(
    "# HELP backend_tool_calls_total Total backend tool calls by tool and status.",
    "# TYPE backend_tool_calls_total counter",
  );
  for (const [key, value] of metrics.toolCalls.entries()) {
    const [tool, status] = key.split(":", 2);
    lines.push(metricLine("backend_tool_calls_total", value, labels({ tool, status })));
  }
  lines.push(
    "# HELP backend_sdk_tool_calls_total Total Codex function-tool calls by tool name.",
    "# TYPE backend_sdk_tool_calls_total counter",
  );
  for (const [tool, value] of metrics.sdkToolCalls.entries()) {
    lines.push(metricLine("backend_sdk_tool_calls_total", value, labels({ tool })));
  }
  lines.push(
    "# HELP backend_sdk_tool_calls_per_query Number of tool calls per Codex query.",
    "# TYPE backend_sdk_tool_calls_per_query summary",
    metricLine("backend_sdk_tool_calls_per_query_count", metrics.sdkToolCallsPerQueryCount, labels()),
    metricLine("backend_sdk_tool_calls_per_query_sum", metrics.sdkToolCallsPerQuerySum, labels()),
    "# HELP backend_sdk_tool_duration_seconds Wall-clock seconds per Codex function-tool call.",
    "# TYPE backend_sdk_tool_duration_seconds summary",
  );
  for (const [tool, value] of metrics.sdkToolDurationCounts.entries()) {
    lines.push(metricLine("backend_sdk_tool_duration_seconds_count", value, labels({ tool })));
    lines.push(
      metricLine("backend_sdk_tool_duration_seconds_sum", metrics.sdkToolDurationSums.get(tool) || 0, labels({ tool })),
    );
  }
  lines.push(
    "# HELP backend_sdk_tool_errors_total Total Codex function-tool calls that returned an error or refusal.",
    "# TYPE backend_sdk_tool_errors_total counter",
  );
  for (const [tool, value] of metrics.sdkToolErrors.entries()) {
    lines.push(metricLine("backend_sdk_tool_errors_total", value, labels({ tool })));
  }
  lines.push(
    "# HELP backend_sdk_tool_call_input_size_bytes Byte length of Codex function-tool input payloads.",
    "# TYPE backend_sdk_tool_call_input_size_bytes summary",
  );
  for (const [tool, value] of metrics.sdkToolInputBytesCounts.entries()) {
    lines.push(metricLine("backend_sdk_tool_call_input_size_bytes_count", value, labels({ tool })));
    lines.push(
      metricLine(
        "backend_sdk_tool_call_input_size_bytes_sum",
        metrics.sdkToolInputBytesSums.get(tool) || 0,
        labels({ tool }),
      ),
    );
  }
  lines.push(
    "# HELP backend_sdk_tool_result_size_bytes Byte length of Codex function-tool result payloads.",
    "# TYPE backend_sdk_tool_result_size_bytes summary",
  );
  for (const [tool, value] of metrics.sdkToolResultBytesCounts.entries()) {
    lines.push(metricLine("backend_sdk_tool_result_size_bytes_count", value, labels({ tool })));
    lines.push(
      metricLine(
        "backend_sdk_tool_result_size_bytes_sum",
        metrics.sdkToolResultBytesSums.get(tool) || 0,
        labels({ tool }),
      ),
    );
  }
  lines.push(
    "# HELP backend_mcp_outbound_requests_total Total outbound MCP tool invocations issued by this backend.",
    "# TYPE backend_mcp_outbound_requests_total counter",
  );
  for (const [key, value] of metrics.mcpOutboundRequests.entries()) {
    const [server, tool, outcome] = mapKeyParts(key);
    lines.push(metricLine("backend_mcp_outbound_requests_total", value, labels({ server, tool, outcome })));
  }
  lines.push(
    "# HELP backend_mcp_outbound_duration_seconds Wall-clock duration of outbound MCP tool calls.",
    "# TYPE backend_mcp_outbound_duration_seconds summary",
  );
  for (const [key, value] of metrics.mcpOutboundDurationCounts.entries()) {
    const [server, tool, outcome] = mapKeyParts(key);
    const labelSet = labels({ server, tool, outcome });
    lines.push(metricLine("backend_mcp_outbound_duration_seconds_count", value, labelSet));
    lines.push(
      metricLine("backend_mcp_outbound_duration_seconds_sum", metrics.mcpOutboundDurationSums.get(key) || 0, labelSet),
    );
  }
  lines.push(
    "# HELP backend_hooks_denials_total Total tool calls denied by a PreToolUse hook.",
    "# TYPE backend_hooks_denials_total counter",
  );
  for (const [key, value] of metrics.hookDenials.entries()) {
    const [tool, source, rule] = mapKeyParts(key);
    lines.push(metricLine("backend_hooks_denials_total", value, labels({ tool, source, rule })));
  }
  lines.push(
    "# HELP backend_hooks_warnings_total Total tool calls flagged but allowed by a PreToolUse hook.",
    "# TYPE backend_hooks_warnings_total counter",
  );
  for (const [key, value] of metrics.hookWarnings.entries()) {
    const [tool, source, rule] = mapKeyParts(key);
    lines.push(metricLine("backend_hooks_warnings_total", value, labels({ tool, source, rule })));
  }
  lines.push(
    "# HELP backend_hooks_evaluations_total Total PreToolUse hook evaluations grouped by final decision.",
    "# TYPE backend_hooks_evaluations_total counter",
  );
  for (const [key, value] of metrics.hookEvaluations.entries()) {
    const [tool, decision] = mapKeyParts(key);
    lines.push(metricLine("backend_hooks_evaluations_total", value, labels({ tool, decision })));
  }
  lines.push(
    "# HELP backend_hooks_config_errors_total Total hooks.yaml parse/reload/validation errors by reason.",
    "# TYPE backend_hooks_config_errors_total counter",
  );
  for (const [reason, value] of metrics.hookConfigErrors.entries()) {
    lines.push(metricLine("backend_hooks_config_errors_total", value, labels({ reason })));
  }
  const extensionRuleCount = loadHookExtensionRules().length;
  lines.push(
    "# HELP backend_hooks_active_rules Number of currently active PreToolUse rules by source.",
    "# TYPE backend_hooks_active_rules gauge",
    metricLine(
      "backend_hooks_active_rules",
      HOOKS_BASELINE_ENABLED ? CODEX_BASELINE_HOOK_RULES.length : 0,
      labels({ source: "baseline" }),
    ),
    metricLine("backend_hooks_active_rules", extensionRuleCount, labels({ source: "extension" })),
  );
  return `${lines.join("\n")}\n`;
}

export async function handleRequest(req, res) {
  const url = new URL(req.url || "/", "http://localhost");
  if (req.method === "GET" && (url.pathname === "/health" || url.pathname === "/health/live")) {
    return handleHealth("health", res);
  }
  if (req.method === "GET" && url.pathname === "/health/ready") {
    return handleHealth("ready", res);
  }
  if (req.method === "GET" && url.pathname === "/health/start") {
    return handleHealth("start", res);
  }
  if (
    req.method === "GET" &&
    (url.pathname === "/.well-known/agent.json" || url.pathname === "/.well-known/agent-card.json")
  ) {
    return jsonResponse(res, 200, buildAgentCard());
  }
  if (req.method === "GET" && url.pathname === "/conversations") {
    return handleConversations(req, res);
  }
  if (req.method === "GET" && url.pathname === "/trace") {
    return handleTrace(req, res);
  }
  if (req.method === "GET" && url.pathname.startsWith("/api/traces")) {
    return handleApiTraces(req, res);
  }
  const sessionStreamMatch = url.pathname.match(/^\/api\/sessions\/([^/]+)\/stream$/);
  if (req.method === "GET" && sessionStreamMatch) {
    return handleSessionStream(req, res, decodeURIComponent(sessionStreamMatch[1]));
  }
  if ((req.method === "GET" || req.method === "POST") && url.pathname === "/mcp") {
    return handleMcp(req, res);
  }
  if (req.method === "POST" && url.pathname === "/") {
    try {
      const raw = await readBody(req, MAX_PROMPT_BYTES + 1024 * 1024);
      const payload = JSON.parse(raw || "{}");
      const result = await handleA2A(payload);
      return jsonResponse(res, result.status, result.body);
    } catch (error) {
      return jsonResponse(res, error?.code === "BODY_TOO_LARGE" ? 413 : 400, {
        jsonrpc: "2.0",
        id: null,
        error: { code: -32700, message: error?.message || "Parse error" },
      });
    }
  }
  return jsonResponse(res, 404, { error: "not found" });
}

function startMetricsServer() {
  if (!METRICS_ENABLED) {
    return null;
  }
  const server = http.createServer((req, res) => {
    const url = new URL(req.url || "/", "http://localhost");
    if (req.method === "GET" && url.pathname === "/metrics") {
      return textResponse(res, 200, renderMetrics(), "text/plain; version=0.0.4; charset=utf-8");
    }
    return jsonResponse(res, 404, { error: "not found" });
  });
  server.listen(METRICS_PORT, AGENT_HOST, () => {
    console.log(`codex metrics listening on ${AGENT_HOST}:${METRICS_PORT}`);
  });
  return server;
}

export function start() {
  ensureParent(CONVERSATION_LOG);
  ensureParent(TRACE_LOG);
  const authWarning = conversationsAuthConfigWarning();
  if (authWarning) {
    console.error(authWarning);
  }
  const appServer = http.createServer((req, res) => {
    handleRequest(req, res).catch((error) => {
      console.error("codex backend request error:", error);
      jsonResponse(res, 500, { error: "internal server error" });
    });
  });
  const metricsServer = startMetricsServer();
  appServer.listen(BACKEND_PORT, AGENT_HOST, () => {
    startupDurationSeconds = (performance.now() - START_MONO) / 1000;
    ready = true;
    console.log(`codex backend listening on ${AGENT_HOST}:${BACKEND_PORT}`);
  });
  return { appServer, metricsServer };
}

const entrypoint = process.argv[1] ? fileURLToPath(import.meta.url) === path.resolve(process.argv[1]) : false;
if (entrypoint) {
  start();
}
