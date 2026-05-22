// Types for the harness /team response. Shape mirrors harness/main.py:team_handler:
// an array of team members, each with its own fan-out /agents response.
//
// The dashboard treats these types as the contract with the harness. When the
// harness response changes, update here first and let the compiler find the
// breaks.

export interface AgentCard {
  name?: string;
  description?: string;
  version?: string;
  url?: string;
  protocolVersion?: string;
  skills?: unknown[];
  capabilities?: Record<string, unknown>;
  // Optional executor family surfaced by the harness (e.g. "claude", "openai",
  // "gemini", "echo"). When present, this is the authoritative source for
  // backend classification; backendType() falls back to id-suffix / substring
  // inference only when the field is absent.
  family?: string;
}

export type AgentRole = "witwave" | "backend";

export interface Agent {
  id: string;
  role: AgentRole;
  url?: string;
  // Present when the card fetch succeeded. Absent (or null) when the member or
  // backend is unreachable — the legacy UI uses !!card as the health signal.
  card?: AgentCard | null;
  // Optional executor family. Mirrors AgentCard.family; set here when the
  // harness decides to surface it at the Agent level instead of (or in
  // addition to) the card.
  family?: string;
  // Optional model identifier surfaced by harness/main.py:team_handler from
  // each backend's configured model. Declared here so the type fully
  // describes what the harness actually returns — currently unused by the
  // UI but silently dropped before this field was declared.
  model?: string;
}

export interface TeamMember {
  name: string;
  url: string;
  agents: Agent[];
  // Populated by harness when the /agents fan-out failed for this member.
  error?: string;
}

export type TeamResponse = TeamMember[];

export type BackendType = "claude" | "openai" | "codex" | "gemini" | "echo" | "unknown";

// Known families. Keep in sync with BackendType above.
const KNOWN_FAMILIES: ReadonlySet<BackendType> = new Set(["claude", "openai", "codex", "gemini", "echo"]);

// Structured id-suffix mapping. Matches the canonical backend container names
// (e.g. "iris-claude", "nova-openai", "kira-gemini", "iris-echo") without
// relying on free-form substring matches anywhere in the id.
const BACKEND_ID_SUFFIXES: ReadonlyArray<readonly [string, BackendType]> = [
  ["-claude", "claude"],
  ["-openai", "openai"],
  ["-codex", "codex"],
  ["-gemini", "gemini"],
  ["-echo", "echo"],
];

// Dev-only: warn once per unknown id so drift is visible without flooding
// devtools on every render. Module-level Set is fine — the dashboard is a
// single-page app and this is scoped to the session.
const warnedFallbackIds = new Set<string>();

function fallbackFromId(id: string): BackendType {
  const s = id.toLowerCase();

  // Prefer a structured suffix match first (the canonical naming scheme used
  // by harness-deployed backends).
  for (const [suffix, type] of BACKEND_ID_SUFFIXES) {
    if (s.endsWith(suffix)) return type;
  }

  // Last-resort substring inference, preserved for back-compat with any id
  // that doesn't follow the canonical suffix scheme. Order is stable to keep
  // existing ids classifying the same way.
  if (s.includes("claude")) return "claude";
  if (s.includes("openai") || s.includes("gpt")) return "openai";
  if (s.includes("codex")) return "codex";
  if (s.includes("gemini") || s.includes("google")) return "gemini";
  if (s.includes("echo")) return "echo";
  return "unknown";
}

function isKnownFamily(value: string): value is BackendType {
  return KNOWN_FAMILIES.has(value as BackendType);
}

// Classify a backend into a BackendType.
//
// Prefers an explicit `family` field (on either the Agent or its AgentCard)
// when present and recognized. Falls back to the id-suffix / substring
// inference otherwise, emitting a one-time console.warn per unknown id in dev
// so missing `family` fields surface without flooding the console.
export function backendType(agentOrId: Agent | AgentCard | string | undefined | null): BackendType {
  if (agentOrId == null) return "unknown";

  let id = "";
  let family: string | undefined;

  if (typeof agentOrId === "string") {
    id = agentOrId;
  } else if ("id" in agentOrId && typeof agentOrId.id === "string") {
    // Agent
    id = agentOrId.id;
    family = agentOrId.family ?? agentOrId.card?.family;
  } else {
    // AgentCard
    family = (agentOrId as AgentCard).family;
    id = (agentOrId as AgentCard).name ?? "";
  }

  if (typeof family === "string" && family.length > 0) {
    const f = family.toLowerCase();
    if (isKnownFamily(f)) return f;
    // An explicit-but-unrecognized family is a contract mismatch; log once
    // and fall through to id-based inference rather than silently accepting.
    if (import.meta.env?.DEV && !warnedFallbackIds.has(`family:${f}`)) {
      warnedFallbackIds.add(`family:${f}`);
      // eslint-disable-next-line no-console
      console.warn(`[backendType] unknown family "${family}" — falling back to id inference`);
    }
  }

  const inferred = fallbackFromId(id);

  if (import.meta.env?.DEV && (family === undefined || family === "") && id.length > 0 && !warnedFallbackIds.has(id)) {
    warnedFallbackIds.add(id);
    // eslint-disable-next-line no-console
    console.warn(`[backendType] no "family" on agent-card for "${id}" — inferred "${inferred}" from id`);
  }

  return inferred;
}
