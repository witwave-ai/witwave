---
name: call-peer
description:
  Send a prompt to another A2A agent in your namespace using JSON-RPC `message/send` and return their reply. Reads peer
  URLs from the `reference_peer_*.md` memory entries that `discover-peers` populated. Use when delegating work to a
  sibling — e.g. "ask iris to push", "have kira scan docs", "tell <peer> to <task>". Idempotent on the wire (each call
  gets a fresh messageId).
version: 0.1.0
---

# call-peer

Send work to another A2A agent and get their reply. Builds the JSON-RPC `message/send` envelope, posts it, parses the
response, and returns the peer's text — codifying the wire format so you don't re-derive it (uuidgen + envelope shape +
blocking config) on every delegation.

This skill assumes the peer has already been discovered. If the peer isn't in your memory cache, run `discover-peers`
first; that skill probes the namespace and writes a `reference_peer_<name>.md` entry per peer with the URL this skill
needs.

## Instructions

You'll be given a **peer name** (e.g. "iris", "kira") and a **prompt** (the actual instruction or question to deliver).
Read these from your own context:

- **Your memory dir** — `/workspaces/witwave-self/memory/agents/<your-name>/` per your CLAUDE.md → Memory section.

### 1. Resolve the peer's URL from memory

Look for `reference_peer_<peer-name>.md` in your memory dir. The body contains the peer's in-cluster URL (e.g.
`http://kira.witwave-self:8000`).

If the file doesn't exist:

> "I don't have a peer entry for `<peer-name>`. Run `discover-peers` first; if that doesn't find the peer either, the
> peer isn't in our namespace or its agent card isn't reachable."

Surface that and stop. Don't guess at URLs.

If multiple peer entries match (unlikely but possible if memory was hand-edited), pick the most recently-written and
note the ambiguity in the response.

### 2. Build the JSON-RPC envelope

The A2A wire format for `message/send`:

```json
{
  "jsonrpc": "2.0",
  "id": "1",
  "method": "message/send",
  "params": {
    "message": {
      "role": "user",
      "parts": [{ "kind": "text", "text": "<your prompt>" }],
      "messageId": "<fresh-uuid>"
    },
    "configuration": {
      "blocking": true,
      "acceptedOutputModes": ["text/plain"]
    }
  }
}
```

- `messageId` MUST be a fresh UUID per call. Use `uuidgen | tr '[:upper:]' '[:lower:]'`. Re-using a messageId across
  calls produces undefined behaviour.
- `blocking: true` means the call waits for the peer to finish and returns the full response in one shot. (After the
  truncation fix in `81d52b4`, this returns the peer's complete multi-turn output, not just the first turn.)
- Don't pre-allocate a `taskId` — let the peer's harness allocate one. Sending a `taskId` that doesn't already exist on
  the peer trips an "unknown task" error.

### 3. POST to the peer

```sh
curl -sS -X POST "<peer-url>/" \
  -H "Content-Type: application/json" \
  --max-time 2700 \
  -d "<envelope-json>"
```

The `--max-time 2700` (45 min) matches the `TASK_TIMEOUT_SECONDS` ceiling on backend tasks. Most peer calls finish in
seconds; the wide bound is for the rare long task (release watching, full doc scan, etc.).

### 4. Parse the response

The peer returns a JSON-RPC response of one of two shapes:

**Success:**

```json
{
  "id": "1",
  "jsonrpc": "2.0",
  "result": {
    "kind": "message",
    "messageId": "<peer's response id>",
    "metadata": { "trace_id": "...", "span_id": "..." },
    "parts": [{ "kind": "text", "text": "<peer's reply>" }],
    "role": "agent"
  }
}
```

Extract `result.parts[0].text` — that's the peer's full reply.

**Error:**

```json
{
  "id": "1",
  "jsonrpc": "2.0",
  "error": {"code": -<int>, "message": "<error text>"}
}
```

Surface the error verbatim. Common codes:

- `-32603` (Internal error) — the peer hit an exception or timeout. Check their conversation log if you need details.
- `-32001` (Task not found) — you sent a `taskId` that doesn't exist on the peer. Drop the `taskId` field; the peer
  allocates one on first send.

### 5. Return the peer's reply

To the caller, return:

- The peer's text response (the contents of `result.parts[0].text`, or the error message on failure).
- The peer's `messageId` and `metadata.trace_id` if present — useful for cross-agent log correlation when the user wants
  to trace a request through both agents.
- If the peer's response looks truncated or weirdly short (suggesting an old image without the truncation fix), flag
  that explicitly so the caller knows to re-fetch via the peer's conversation log.

## When to invoke

- **The user asks for delegation** — "ask iris to push", "have kira run a docs scan", "tell <peer> to <task>". Resolve
  the peer name from the user's prose and the prompt from the remaining instruction.
- **You decide a peer is the right owner of the work** — e.g. during a release flow, you decide kira should re-scan docs
  before tagging. Self-direct the call.
- **A skill explicitly hands off** — e.g. a future workflow where iris's release skill calls kira to verify docs aren't
  drifted before the tag.

## Out of scope for this skill

- **Discovering peers** — that's `discover-peers`. This skill assumes the peer is already cached.
- **Streaming responses** — A2A `message/stream` is not consumed by anything in this codebase today
  (`AgentCapabilities(streaming=False)` per `c39d237`). When a real streaming consumer materializes, scaffold a separate
  `call-peer-stream` skill.
- **Multi-turn conversations** — A2A's task model supports follow-up messages on the same task, but this skill is
  one-shot. A future `continue-with-peer` skill could handle task-id continuity.
- **Auth headers** — A2A peer-to-peer in the same namespace doesn't require bearer auth today (intra-cluster, network
  policy is the boundary). If cross-namespace or cross-cluster A2A becomes a thing, this skill needs an auth flag.
- **Picking the best peer** — the LLM does that judgment work directly from the cached card prose. This skill takes the
  decided peer as input.
