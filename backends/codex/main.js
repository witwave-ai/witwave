import crypto from "node:crypto";
import { spawn } from "node:child_process";
import fs from "node:fs";
import http from "node:http";
import path from "node:path";
import { fileURLToPath } from "node:url";

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

const CONVERSATION_LOG = process.env.CONVERSATION_LOG || "/home/agent/logs/conversation.jsonl";
const TRACE_LOG = process.env.TRACE_LOG || "/home/agent/logs/tool-activity.jsonl";
const CODEX_AGENT_MD = process.env.CODEX_AGENT_MD || "/home/agent/.codex/AGENTS.md";
const CODEX_MODEL = process.env.CODEX_MODEL || process.env.OPENAI_MODEL || "gpt-5.5";
const CODEX_REASONING_EFFORT = process.env.CODEX_REASONING_EFFORT || "xhigh";
const MAX_PROMPT_BYTES = Number.parseInt(process.env.MAX_PROMPT_BYTES || String(10 * 1024 * 1024), 10);
const METRICS_ENABLED = parseBool(process.env.METRICS_ENABLED);
const METRICS_PORT = Number.parseInt(process.env.METRICS_PORT || "9000", 10);
const CONVERSATIONS_AUTH_TOKEN = process.env.CONVERSATIONS_AUTH_TOKEN || "";
const CONVERSATIONS_AUTH_DISABLED = parseBool(process.env.CONVERSATIONS_AUTH_DISABLED);
const LOG_REDACT = parseBool(process.env.LOG_REDACT);
const CODEX_SHELL_ENABLED = parseBool(process.env.CODEX_SHELL_ENABLED);
const CODEX_SHELL_TIMEOUT_SECONDS = Number.parseInt(process.env.CODEX_SHELL_TIMEOUT_SECONDS || "30", 10);
const CODEX_SHELL_MAX_OUTPUT_BYTES = Number.parseInt(process.env.CODEX_SHELL_MAX_OUTPUT_BYTES || "12000", 10);
const CODEX_MAX_TOOL_ITERATIONS = Number.parseInt(process.env.CODEX_MAX_TOOL_ITERATIONS || "6", 10);
const CODEX_MEMORY_ENABLED = parseBool(process.env.CODEX_MEMORY_ENABLED ?? "true");
const CODEX_MEMORY_ROOT = process.env.CODEX_MEMORY_ROOT || "/home/agent/.codex/memory";
const CODEX_MEMORY_MAX_BYTES = Number.parseInt(process.env.CODEX_MEMORY_MAX_BYTES || "65536", 10);
const CODEX_MEMORY_MAX_LIST_ENTRIES = Number.parseInt(process.env.CODEX_MEMORY_MAX_LIST_ENTRIES || "200", 10);
const CODEX_SESSION_STORE_PATH = process.env.CODEX_SESSION_STORE_PATH || "/home/agent/.codex/sessions/responses.json";
const MAX_SESSIONS = Math.max(1, Number.parseInt(process.env.MAX_SESSIONS || "10000", 10) || 10000);
const CONVERSATION_STREAM_KEEPALIVE_SEC = Number.parseFloat(
  process.env.CONVERSATION_STREAM_KEEPALIVE_SEC || "15",
);
const CONVERSATION_STREAM_GRACE_SEC = Number.parseFloat(process.env.CONVERSATION_STREAM_GRACE_SEC || "60");
const CONVERSATION_STREAM_RING_MAX = Math.max(
  1,
  Number.parseInt(process.env.CONVERSATION_STREAM_RING_MAX || "200", 10) || 200,
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
  mcpRequests: new Map(),
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
  lastA2ARequestTimestamp: 0,
};

let codexSessions = null;
const sessionStreams = new Map();

function parseBool(value) {
  if (value === undefined || value === null || value === "") {
    return false;
  }
  return ["1", "true", "yes", "on"].includes(String(value).trim().toLowerCase());
}

function splitList(value) {
  return String(value || "")
    .split(/[\n,]+/)
    .map((item) => item.trim())
    .filter(Boolean);
}

function inc(map, key, amount = 1) {
  map.set(key, (map.get(key) || 0) + amount);
}

function byteLength(text) {
  return Buffer.byteLength(text || "", "utf8");
}

function sessionHash(sessionId) {
  if (!sessionId) {
    return "000000000000";
  }
  return crypto.createHash("sha256").update(sessionId).digest("hex").slice(0, 12);
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

export function publishSessionChunk(sessionId, { role, seq, content, final }) {
  const stream = getSessionStream(sessionId);
  if (!stream) {
    return undefined;
  }
  const envelope = sessionStreamEnvelope(stream, "conversation.chunk", {
    session_id_hash: sessionHash(sessionId),
    role: String(role || "assistant"),
    seq: Number.isFinite(Number(seq)) ? Number(seq) : 0,
    content: String(content || ""),
    final: Boolean(final),
  });
  stream.ring.push(envelope);
  while (stream.ring.length > CONVERSATION_STREAM_RING_MAX) {
    stream.ring.shift();
  }
  for (const res of [...stream.subscribers]) {
    try {
      res.write(sseSerialize(envelope));
    } catch {
      stream.subscribers.delete(res);
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

function appendJsonl(filePath, record) {
  try {
    ensureParent(filePath);
    fs.appendFileSync(filePath, `${JSON.stringify(record)}\n`, "utf8");
  } catch (error) {
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
  const sessionId = String(raw || "").trim();
  return sessionId || crypto.randomUUID();
}

function getStoredSession(sessionId) {
  return loadSessionStore().get(sessionId);
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

function codexToolDefinitions() {
  const tools = [];
  if (CODEX_SHELL_ENABLED) {
    tools.push(shellToolDefinition());
  }
  if (CODEX_MEMORY_ENABLED) {
    tools.push(...memoryToolDefinitions());
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

function traceSummaryFromEntries(entries, traceId) {
  const matches = entries.filter((entry) => entry?.trace_id === traceId || entry?.traceID === traceId);
  if (matches.length === 0) {
    return undefined;
  }
  const first = matches[0];
  const last = matches[matches.length - 1];
  const firstTs = timestampForEntry(first);
  const lastTs = timestampForEntry(last);
  const duration =
    firstTs && lastTs && lastTs >= firstTs ? Number(((lastTs.getTime() - firstTs.getTime()) / 1000).toFixed(6)) : 0;
  return {
    traceID: traceId,
    spans: matches,
    processes: {},
    duration,
    startTime: firstTs ? firstTs.getTime() * 1000 : undefined,
  };
}

function traceList(limit = 20, offset = 0) {
  const entries = [...readJsonlEntries(CONVERSATION_LOG), ...readJsonlEntries(TRACE_LOG)];
  const byTrace = new Map();
  for (const entry of entries) {
    const traceId = entry?.trace_id || entry?.traceID;
    if (!traceId || !/^[0-9a-fA-F]{32}$/.test(String(traceId))) {
      continue;
    }
    if (!byTrace.has(traceId)) {
      byTrace.set(traceId, []);
    }
    byTrace.get(traceId).push(entry);
  }
  const traces = [];
  for (const [traceId, traceEntries] of byTrace.entries()) {
    const summary = traceSummaryFromEntries(traceEntries, traceId);
    if (summary) {
      traces.push(summary);
    }
  }
  traces.sort((a, b) => (b.startTime || 0) - (a.startTime || 0));
  return {
    data: traces.slice(offset, offset + limit),
    total: traces.length,
    limit,
    offset,
  };
}

async function handleFunctionCall(call, traceId) {
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
    return await runShellCommand(args.command, traceId);
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
    return await runMemoryTool(call.name, args, traceId);
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

async function createResponseWithSessionFallback(client, request, sessionId) {
  try {
    return await client.responses.create(request);
  } catch (error) {
    if (!request.previous_response_id) {
      throw error;
    }
    const message = String(error?.message || error);
    if (!/previous[_ ]response|previous_response_id|not found|expired/i.test(message)) {
      throw error;
    }
    loadSessionStore().delete(sessionId);
    saveSessionStore();
    const retry = { ...request };
    delete retry.previous_response_id;
    return await client.responses.create(retry);
  }
}

async function runCodex(prompt, metadata, sessionId) {
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
  const storedSession = getStoredSession(sessionId);
  if (storedSession?.previous_response_id) {
    request.previous_response_id = storedSession.previous_response_id;
  }
  const tools = codexToolDefinitions();
  if (tools.length > 0) {
    request.tools = tools;
    request.parallel_tool_calls = false;
  }

  let response = await createResponseWithSessionFallback(client, request, sessionId);
  if (response?.id) {
    recordSessionResponse(sessionId, response.id, model);
  }
  let budget = budgetResult(response, maxTokens);
  if (budget.exceeded) {
    metrics.budgetExceededTotal += 1;
  }
  if (tools.length > 0) {
    const maxIterations = maxToolIterationsForRequest(metadata);
    for (let iteration = 0; iteration < maxIterations; iteration += 1) {
      if (budget.exceeded) {
        break;
      }
      const calls = (response.output || []).filter((item) => item?.type === "function_call");
      if (calls.length === 0) {
        break;
      }
      const input = [];
      for (const call of calls) {
        const output = await handleFunctionCall(call, traceId);
        input.push({
          type: "function_call_output",
          call_id: call.call_id,
          output: JSON.stringify(output),
        });
      }
      response = await client.responses.create({
        model,
        input,
        previous_response_id: response.id,
        tools,
        parallel_tool_calls: false,
        ...(instructions ? { instructions } : {}),
        ...(reasoning ? { reasoning } : {}),
        ...(maxOutputTokens !== undefined ? { max_output_tokens: maxOutputTokens } : {}),
      });
      if (response?.id) {
        recordSessionResponse(sessionId, response.id, model);
      }
      budget = budgetResult(response, maxTokens);
      if (budget.exceeded) {
        metrics.budgetExceededTotal += 1;
      }
    }
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

  const { metadata, contextId, messageId } = extractRequestMetadata(payload);
  const sessionId = sessionForRequest(metadata, contextId);
  const prompt = extractPrompt(payload).trim();
  const promptBytes = byteLength(prompt);
  const traceId = traceIdForMetadata(metadata);
  metrics.promptBytesCount += 1;
  metrics.promptBytesSum += promptBytes;
  metrics.lastA2ARequestTimestamp = Date.now() / 1000;
  if (prompt) {
    publishSessionChunk(sessionId, { role: "user", seq: 0, content: prompt, final: true });
  }

  let status = "ok";
  let responseText = "";
  let model = modelForRequest(metadata);
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
      const result = await runCodex(prompt, metadata, sessionId);
      model = result.model;
      responseText = result.text;
      if (result.budget_exceeded) {
        status = "budget_exceeded";
      }
    }
  } catch (error) {
    status = "error";
    responseText = `codex backend error: ${error?.message || String(error)}`;
  } finally {
    inc(metrics.a2aRequests, status);
  }

  metrics.responseBytesCount += 1;
  metrics.responseBytesSum += byteLength(responseText);
  appendJsonl(CONVERSATION_LOG, {
    timestamp: new Date().toISOString(),
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
    prompt: logText(prompt),
    response: logText(responseText),
  });
  if (responseText) {
    publishSessionChunk(sessionId, { role: "assistant", seq: 1, content: responseText, final: true });
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

function authOk(req) {
  if (CONVERSATIONS_AUTH_DISABLED) {
    return true;
  }
  if (!CONVERSATIONS_AUTH_TOKEN) {
    return false;
  }
  return req.headers.authorization === `Bearer ${CONVERSATIONS_AUTH_TOKEN}`;
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
  try {
    const raw = await readBody(req);
    const payload = JSON.parse(raw || "{}");
    const method = payload.method || "";
    let result;
    if (method === "initialize") {
      result = {
        protocolVersion: "2024-11-05",
        capabilities: { tools: {} },
        serverInfo: { name: "witwave-codex-backend", version: AGENT_VERSION },
      };
    } else if (method === "tools/list") {
      result = {
        tools: [
          {
            name: "ask_codex",
            description: "Ask the Codex backend to respond to a prompt.",
            inputSchema: {
              type: "object",
              properties: { prompt: { type: "string" } },
              required: ["prompt"],
            },
          },
        ],
      };
    } else if (method === "tools/call") {
      const name = payload.params?.name;
      if (name !== "ask_codex") {
        throw new Error(`unknown tool: ${name || ""}`);
      }
      const prompt = payload.params?.arguments?.prompt || "";
      const resultText = await runCodex(String(prompt), payload.params?.arguments || {});
      result = { content: [{ type: "text", text: resultText.text }] };
    } else {
      jsonResponse(res, 200, {
        jsonrpc: "2.0",
        id: payload.id ?? null,
        error: { code: -32601, message: `Unsupported method: ${method}` },
      });
      return;
    }
    jsonResponse(res, 200, { jsonrpc: "2.0", id: payload.id ?? null, result });
  } catch (error) {
    status = "error";
    jsonResponse(res, error?.code === "BODY_TOO_LARGE" ? 413 : 400, {
      jsonrpc: "2.0",
      id: null,
      error: { code: -32600, message: error?.message || String(error) },
    });
  } finally {
    inc(metrics.mcpRequests, status);
    appendJsonl(TRACE_LOG, {
      timestamp: new Date().toISOString(),
      agent: AGENT_OWNER,
      agent_id: AGENT_ID,
      backend: BACKEND_ID,
      endpoint: "/mcp",
      status,
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
    if (traceMatch) {
      const traceId = traceMatch[1].toLowerCase();
      const entries = [...readJsonlEntries(CONVERSATION_LOG), ...readJsonlEntries(TRACE_LOG)];
      const summary = traceSummaryFromEntries(entries, traceId);
      if (!summary) {
        return jsonResponse(res, 404, { error: "trace not found" });
      }
      return jsonResponse(res, 200, { data: [summary], total: 1, limit: 1, offset: 0 });
    }
    const limitRaw = url.searchParams.get("limit");
    const offsetRaw = url.searchParams.get("offset");
    const limit = limitRaw ? Number.parseInt(limitRaw, 10) : 20;
    const offset = offsetRaw ? Number.parseInt(offsetRaw, 10) : 0;
    jsonResponse(
      res,
      200,
      traceList(
        Number.isFinite(limit) && limit > 0 ? Math.min(limit, 1000) : 20,
        Number.isFinite(offset) && offset > 0 ? offset : 0,
      ),
    );
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
  const stream = getSessionStream(cleanSessionId, { create: true });
  const lastEventId = req.headers["last-event-id"] || url.searchParams.get("last_event_id");
  const replay = lastEventId
    ? stream.ring.filter((envelope) => Number(envelope.id) > Number(lastEventId))
    : [];
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
      scheduleSessionStreamCleanup(stream);
    }
  }, keepaliveMs);
  keepalive.unref?.();

  req.on("close", () => {
    clearInterval(keepalive);
    stream.subscribers.delete(res);
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
    "# HELP backend_a2a_last_request_timestamp_seconds Unix timestamp of the most recent A2A request.",
    "# TYPE backend_a2a_last_request_timestamp_seconds gauge",
    metricLine("backend_a2a_last_request_timestamp_seconds", metrics.lastA2ARequestTimestamp || 0, labels()),
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
    "# HELP backend_mcp_requests_total MCP requests by terminal status.",
    "# TYPE backend_mcp_requests_total counter",
  );
  for (const [status, value] of metrics.mcpRequests.entries()) {
    lines.push(metricLine("backend_mcp_requests_total", value, labels({ status })));
  }
  lines.push(
    "# HELP backend_tool_calls_total Total backend tool calls by tool and status.",
    "# TYPE backend_tool_calls_total counter",
  );
  for (const [key, value] of metrics.toolCalls.entries()) {
    const [tool, status] = key.split(":", 2);
    lines.push(metricLine("backend_tool_calls_total", value, labels({ tool, status })));
  }
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
  if (req.method === "POST" && url.pathname === "/mcp") {
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
