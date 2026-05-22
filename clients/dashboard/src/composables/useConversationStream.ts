import { getCurrentInstance, onBeforeUnmount, ref, watch, type Ref } from "vue";
import { useEventStream, type EventEnvelope, type UseEventStreamOptions } from "./useEventStream";

// Phase-5 per-session conversation SSE consumer (#1110). Wraps the shared
// `useEventStream` client pointed at a backend's
// `GET /api/sessions/<session_id>/stream` endpoint (shipped phase 4) and
// reassembles `conversation.chunk` / `conversation.turn` envelopes into
// the `ConversationTurn[]` shape the ConversationsView renders.
//
// Why a dedicated composable and not a direct store subscription:
//   - Chunk reassembly needs per-turn state (running content buffer, seq
//     tracking) that's meaningless outside the drill-down view.
//   - The backend stream is scoped to a single `(agent, session_id)` pair;
//     there is no shared fleet ring to merge into, so a module-level
//     store would waste teardown ceremony on a per-mount connection.
//   - The view still wants connection-pill state + a clean `close()` hook;
//     exposing those directly keeps view code small.
//
// Accumulation rules (from the task scope for phase 5):
//   - `conversation.chunk` role=user: push a fresh user turn (complete=true).
//   - `conversation.chunk` role=assistant seq>0 AND last turn is an
//     in-progress assistant turn: append chunk content to that turn and
//     carry `complete` from the envelope's `final`.
//   - Otherwise (seq=0, previous assistant turn already complete, or last
//     turn is user): push a new assistant turn.
//   - `conversation.turn` role=assistant: mark the most-recent in-progress
//     assistant turn complete=true (the finalise signal from the backend).
//   - `stream.overrun`: reported via the existing `error` ref; the
//     underlying event stream handles reconnect/resume on its own.

export type ConversationTurnRole = "user" | "assistant";

export interface ConversationTurn {
  // Stable key across chunk updates of the same turn so Vue's v-for
  // reuses the same DOM node while content grows.
  turnId: string;
  role: ConversationTurnRole;
  // Accumulated content. For assistant turns this grows as chunks land;
  // for user turns it's the single chunk text.
  content: string;
  // `true` once the turn is closed either by a chunk with `final=true`
  // or by a `conversation.turn` envelope. `false` while content is
  // still streaming.
  complete: boolean;
  // Timestamp of the FIRST chunk of this turn. Stays stable across
  // appends so merging against backlog rows stays consistent.
  ts: string;
  // Server-identity fingerprint for the turn — used only to gate
  // `canAppend` so two parallel assistant turns (e.g. concurrent job
  // dispatches against one session) don't collide into a shared row.
  // Opaque to the view; `turnId` remains the stable rendering key.
  // Optional on the public interface so backlog-merge callers that
  // synthesise turns without stream state don't have to fabricate a
  // key. (#1240)
  turnKey?: string;
}

export interface ToolUseEvent {
  id: string;
  name: string;
  ts: string;
  payload: Record<string, unknown>;
}

export interface UseConversationStreamOptions
  extends Pick<UseEventStreamOptions, "token" | "autoConnect" | "fetchImpl" | "initialDelayMs" | "maxDelayMs"> {
  // Maximum number of turns to retain. The backlog call owns history;
  // the stream only supplies live tail, so a modest cap is fine.
  maxTurns?: number;
}

export interface UseConversationStreamReturn {
  turns: Ref<ConversationTurn[]>;
  toolUses: Ref<ToolUseEvent[]>;
  connected: Ref<boolean>;
  reconnecting: Ref<boolean>;
  error: Ref<string>;
  close: () => void;
}

// Note: `window.__WITWAVE_CONFIG__` is declared globally in
// `src/stores/timeline.ts`; declaring it again here with a differently-
// shaped intersection type trips TS2717, so we read the field through
// an indirected access pattern instead.

const DEFAULT_MAX_TURNS = 500;

// Auth scope (#1543): per-session SSE streams live on the per-agent
// backend under `/api/agents/<agent>/api/sessions/<id>/stream`, which
// the harness proxies through to the backend's own
// `CONVERSATIONS_AUTH_TOKEN`-guarded endpoint. In today's default
// deployment the harness and every backend share one
// `CONVERSATIONS_AUTH_TOKEN`, so `harnessBearerToken` IS the
// backend-scope token — reuse is intentional, not a layering violation.
// Deployments that split tokens per backend can override via
// `__WITWAVE_CONFIG__.backendBearerToken`; when set, the dashboard sends
// that in preference to `harnessBearerToken` on per-session streams.
// Explicit `options.token` still wins over both so callers/tests stay
// deterministic.
function resolveToken(explicit?: string): string | undefined {
  if (explicit) return explicit;
  if (typeof window === "undefined") return undefined;
  const cfg = (
    window as unknown as {
      __WITWAVE_CONFIG__?: {
        harnessBearerToken?: string;
        backendBearerToken?: string;
      };
    }
  ).__WITWAVE_CONFIG__;
  // Prefer a backend-specific token when deployments have split auth.
  const backendTok = cfg?.backendBearerToken;
  if (typeof backendTok === "string" && backendTok.length > 0) {
    return backendTok;
  }
  const tok = cfg?.harnessBearerToken;
  return typeof tok === "string" && tok.length > 0 ? tok : undefined;
}

// Per-session stream URL. Each backend (claude/openai/gemini) exposes
// `/api/sessions/<session_id>/stream` mounted inside the
// `/api/agents/<agent>/` proxy, so the dashboard-facing path is:
//   /api/agents/<agent>/api/sessions/<session_id>/stream
export function conversationStreamUrl(agent: string, sessionId: string): string {
  return `/api/agents/${encodeURIComponent(agent)}/api/sessions/${encodeURIComponent(sessionId)}/stream`;
}

interface ChunkPayload {
  session_id_hash?: string;
  role?: string;
  seq?: number;
  content?: string;
  final?: boolean;
  // Turn-identity fields. The backends have flirted with several names
  // as the per-turn contract has evolved; we probe them in order and
  // fall back to `session_id_hash + turn_sequence` so parallel
  // assistant turns (e.g. two jobs firing concurrently against one
  // session) don't append into a single shared row. (#1240)
  turn_id?: string;
  turnId?: string;
  assistant_turn_id?: string;
  turn_sequence?: number;
}

interface TurnPayload {
  session_id_hash?: string;
  role?: string;
  content_bytes?: number;
  model?: string;
}

interface ToolUsePayload {
  id?: string;
  name?: string;
  [k: string]: unknown;
}

function makeTurnId(role: ConversationTurnRole, ts: string, sessionHash: string | undefined, counter: number): string {
  // Stable across chunks for one turn. `counter` disambiguates
  // back-to-back same-role turns that happen to share the ms-truncated ts.
  const hashPart = (sessionHash ?? "").slice(0, 6);
  return `${role}-${ts}-${hashPart}-${counter}`;
}

// NOTE (#1157, #1383): `agent` and `sessionId` are baseline values captured
// at instantiation. They are embedded into the stream URL here and will NOT
// be re-read if the caller later mutates a ref passed in as one of these
// arguments — the stream URL is frozen for the lifetime of the composable.
//
// CONTRACT: callers OUTSIDE a Vue component setup() (e.g. a Pinia store,
// a utility function) MUST explicitly call the returned `close()` when
// the stream is no longer needed. The auto-teardown via `onBeforeUnmount`
// only fires inside a setup() scope; calling `useConversationStream` in
// a module-level or non-component scope will leak the SSE connection
// on session switch unless `close()` is invoked manually.
//
// To switch agent or sessionId, callers MUST call `close()` on the
// existing composable and create a new one. Reassigning the argument or
// mutating reactive refs from which these were derived will have no
// effect on the in-flight stream.
export function useConversationStream(
  agent: string,
  sessionId: string,
  options: UseConversationStreamOptions = {},
): UseConversationStreamReturn {
  const maxTurns = options.maxTurns ?? DEFAULT_MAX_TURNS;
  const token = resolveToken(options.token);

  const stream = useEventStream(conversationStreamUrl(agent, sessionId), {
    token,
    autoConnect: options.autoConnect,
    fetchImpl: options.fetchImpl,
    initialDelayMs: options.initialDelayMs,
    maxDelayMs: options.maxDelayMs,
    // Large enough to hold a bursty assistant turn with many small
    // chunks; the composable downstream trims by turn count, not event
    // count, so this just bounds the intermediate buffer.
    maxEvents: Math.max(maxTurns * 8, 200),
  });

  const turns = ref<ConversationTurn[]>([]) as Ref<ConversationTurn[]>;
  const toolUses = ref<ToolUseEvent[]>([]) as Ref<ToolUseEvent[]>;
  // Counter drives unique turnIds even when two turns share the same
  // first-chunk timestamp (rare but possible with coarse-clock hosts).
  let turnCounter = 0;
  // Id-keyed dedupe for processed envelopes. Positional indexing breaks
  // down when the underlying stream's ring evicts from the left — the
  // array shrinks, our index lands on what looks like a "new" envelope,
  // and we reprocess already-seen chunks, producing duplicate turns.
  // Mirror the timeline store's `seenIds` pattern instead. (#1156)
  const processedIds = new Set<string>();
  // Cap on the Set's size so it doesn't grow unbounded on long sessions.
  // When we hit the cap we drop the oldest entries (insertion order is
  // iteration order for a Set).
  const PROCESSED_IDS_CAP = 2000;

  function markProcessed(id: string): void {
    processedIds.add(id);
    if (processedIds.size > PROCESSED_IDS_CAP) {
      // Evict the oldest half in one pass — amortises the cost of
      // rebuilding iteration state vs. shedding one id at a time.
      const drop = processedIds.size - PROCESSED_IDS_CAP;
      let dropped = 0;
      for (const key of processedIds) {
        if (dropped >= drop) break;
        processedIds.delete(key);
        dropped += 1;
      }
    }
  }

  function pushTurn(turn: ConversationTurn): void {
    const next = turns.value.slice();
    next.push(turn);
    if (next.length > maxTurns) {
      next.splice(0, next.length - maxTurns);
    }
    turns.value = next;
  }

  function appendToLastAssistant(content: string, final: boolean): void {
    const next = turns.value.slice();
    const last = next[next.length - 1];
    if (!last || last.role !== "assistant" || last.complete) return;
    next[next.length - 1] = {
      ...last,
      content: last.content + content,
      complete: last.complete || final,
    };
    turns.value = next;
  }

  function markLastAssistantComplete(): void {
    const next = turns.value.slice();
    for (let i = next.length - 1; i >= 0; i -= 1) {
      if (next[i].role !== "assistant") continue;
      if (next[i].complete) return; // already finalised
      next[i] = { ...next[i], complete: true };
      turns.value = next;
      return;
    }
  }

  function deriveTurnKey(p: ChunkPayload, role: ConversationTurnRole): string {
    // Prefer an explicit server-side turn id when the backend provides
    // one; callers probed in order of increasing schema age. When no
    // explicit id is carried we synthesise one from `session_id_hash +
    // turn_sequence`, which is the closest thing the current envelope
    // schema offers to a turn-scoped identifier. If NEITHER is present
    // we fall back to a per-role synthetic key that lets the previous
    // behaviour (append to any open same-role turn) hold — this path
    // is mostly hit in tests + legacy fixtures. (#1240)
    if (typeof p.turn_id === "string" && p.turn_id) return p.turn_id;
    if (typeof p.turnId === "string" && p.turnId) return p.turnId;
    if (typeof p.assistant_turn_id === "string" && p.assistant_turn_id) {
      return p.assistant_turn_id;
    }
    const sessionHash = p.session_id_hash ?? "";
    if (typeof p.turn_sequence === "number") {
      return `${sessionHash}:${p.turn_sequence}`;
    }
    return `legacy:${role}:${sessionHash}`;
  }

  function handleChunk(env: EventEnvelope): void {
    const p = (env.payload ?? {}) as ChunkPayload;
    const role = p.role === "user" ? "user" : "assistant";
    const content = typeof p.content === "string" ? p.content : "";
    const seq = typeof p.seq === "number" ? p.seq : 0;
    const final = p.final === true;
    const sessionHash = p.session_id_hash;
    const chunkTurnKey = deriveTurnKey(p, role);

    if (role === "user") {
      // User chunks are always single-shot turns — the backends don't
      // split user prompts across chunks today, but if one ever does we
      // still surface each chunk as its own row rather than silently
      // concatenating into the previous user turn.
      turnCounter += 1;
      pushTurn({
        turnId: makeTurnId("user", env.ts, sessionHash, turnCounter),
        role: "user",
        content,
        complete: true,
        ts: env.ts,
        turnKey: chunkTurnKey,
      });
      return;
    }

    // Assistant chunk. Decide: append to the open assistant turn, or
    // start a fresh one. When both the open turn and the incoming
    // chunk carry a turnKey, they must match — two parallel assistant
    // turns otherwise silently concatenate into a single row. Missing
    // keys (backlog-synthesised turns) fall back to the legacy
    // "append to any open same-role turn" behaviour. (#1240)
    const last = turns.value[turns.value.length - 1];
    const keysAgree = !last?.turnKey || !chunkTurnKey || last.turnKey === chunkTurnKey;
    const canAppend = !!last && last.role === "assistant" && !last.complete && seq > 0 && keysAgree;

    if (canAppend) {
      appendToLastAssistant(content, final);
      return;
    }

    turnCounter += 1;
    pushTurn({
      turnId: makeTurnId("assistant", env.ts, sessionHash, turnCounter),
      role: "assistant",
      content,
      complete: final,
      ts: env.ts,
      turnKey: chunkTurnKey,
    });
  }

  function handleTurn(env: EventEnvelope): void {
    const p = (env.payload ?? {}) as TurnPayload;
    if (p.role !== "assistant") return;
    markLastAssistantComplete();
  }

  function handleToolUse(env: EventEnvelope): void {
    const p = (env.payload ?? {}) as ToolUsePayload;
    const id = typeof p.id === "string" ? p.id : env.id;
    const name = typeof p.name === "string" ? p.name : "";
    const next = toolUses.value.slice();
    next.push({ id, name, ts: env.ts, payload: p as Record<string, unknown> });
    if (next.length > maxTurns) {
      next.splice(0, next.length - maxTurns);
    }
    toolUses.value = next;
  }

  function handleOverrun(env: EventEnvelope): void {
    // Surface for operator visibility; the stream layer handles resume.
    // eslint-disable-next-line no-console
    console.warn("[useConversationStream] stream.overrun — reconnecting", env.payload);
  }

  // Process every envelope that we haven't handled yet, keyed by `id`.
  // Survives ring evictions in the underlying stream — each envelope is
  // processed exactly once regardless of positional shifts. (#1156)
  // #1534: capture the stop handle returned by `watch()` so `close()`
  // can tear it down on session switch. Previously the watcher leaked
  // across every session switch and kept firing against the closed
  // stream's refs.
  const stopEventsWatcher = watch(
    stream.events,
    (arr) => {
      const events = arr as EventEnvelope[];
      for (const env of events) {
        if (!env || !env.id) continue;
        if (processedIds.has(env.id)) continue;
        markProcessed(env.id);
        switch (env.type) {
          case "conversation.chunk":
            handleChunk(env);
            break;
          case "conversation.turn":
            handleTurn(env);
            break;
          case "tool.use":
            handleToolUse(env);
            break;
          case "stream.overrun":
            handleOverrun(env);
            break;
          default:
            // Ignore event types unrelated to the per-session view; the
            // backend may publish other envelopes (e.g. trace.span) on
            // the same stream and we let them pass silently.
            break;
        }
      }
    },
    { immediate: true, deep: false },
  );

  function close(): void {
    try {
      stream.close();
    } catch {
      // ignore — close is best-effort.
    }
    // #1534: stop the events watcher so a session switch cannot leak
    // a live reactive subscription across composable lifetimes.
    try {
      stopEventsWatcher();
    } catch {
      // ignore — watcher may already be stopped.
    }
    // Reset dedupe state so a later reopen (same composable instance is
    // unusual, but callers can still trigger the path indirectly via the
    // underlying stream's `open()`) doesn't carry over old ids. (#1156)
    processedIds.clear();
  }

  if (getCurrentInstance()) {
    onBeforeUnmount(() => close());
  }

  return {
    turns,
    toolUses,
    connected: stream.connected,
    reconnecting: stream.reconnecting,
    error: stream.error,
    close,
  };
}
