---
name: discover-peers
description:
  Find every other A2A agent in your Kubernetes namespace by introspecting the service env vars Kubernetes injected at
  pod-start time, probing each candidate's `/.well-known/agent.json`, and caching confirmed peers as reference-type
  memory entries. Idempotent — re-running picks up changes after a pod restart and prunes peers no longer present.
  Trigger when the user says "discover peers", "refresh peers", "who's available?", "find agents", or whenever you need
  to know who you can collaborate with.
version: 0.1.0
---

# discover-peers

Build a current view of every A2A agent reachable in your namespace without any new infrastructure or RBAC. Kubernetes
injects `<SERVICE_NAME>_SERVICE_HOST` and `<SERVICE_NAME>_SERVICE_PORT` env vars into every pod for each Service in the
same namespace at pod-start time — that's the discovery primitive. Filter out the ones that aren't A2A agents by probing
each candidate's `/.well-known/agent.json` endpoint; the responses tell you who's real, what they do, and how to reach
them.

The result is a set of `reference`-type memory entries (one per peer) you can browse to decide who to ask for help. Pair
with the `call-peer` skill to actually send work.

## Instructions

Read these from your own environment and identity:

- **Your own name** — `$AGENT_OWNER` (the named witwave agent: iris, nova, kira, …). Used to skip self in the probe
  loop.
- **Your memory dir** — `/workspaces/witwave-self/memory/agents/<your-name>/` per your CLAUDE.md → Memory section.

### 1. Enumerate candidate peers from the environment

```sh
env | grep -E "_SERVICE_HOST=" | grep -v "^KUBERNETES_" | sort
```

Each line has the shape `<NAME>_SERVICE_HOST=<ip>`. The companion `<NAME>_SERVICE_PORT=<port>` is set in the same env
block. The `<NAME>` portion is the Kubernetes Service name uppercased with dashes converted to underscores — e.g.
service `iris` → env `IRIS_SERVICE_HOST`; service `mcp-kubernetes` → env `MCP_KUBERNETES_SERVICE_HOST`.

Skip:

- `KUBERNETES_*` (the API server, not a peer).
- The pair matching your own name (compare case-insensitively against `$AGENT_OWNER` after dash→underscore + uppercase).

What's left is the **candidate set** — every Service in the namespace that _might_ be an A2A agent. Some won't be (MCP
tools, sidecars, etc.); the probe step filters them out.

### 2. Probe each candidate's agent card

For each candidate `(NAME, HOST, PORT)`:

```sh
curl -sf --max-time 5 \
  "http://${HOST}:${PORT}/.well-known/agent.json"
```

- HTTP 200 + valid JSON with a `name` field → this IS an A2A agent. Capture the full card.
- Anything else (404, timeout, empty body, non-JSON, JSON missing `name`) → NOT an A2A agent. Skip silently.

Tolerate failures one peer at a time — a single unreachable candidate should not abort the whole scan.

### 3. Cache each confirmed peer to memory

For each confirmed peer, write a `reference`-type memory entry to your private namespace:

**File:** `/workspaces/witwave-self/memory/agents/<your-name>/reference_peer_<peer-name>.md`

**Frontmatter shape:**

```markdown
---
name: Peer — <peer-name>
description: <one-line excerpt from peer's agent-card description>
type: reference
---
```

**Body:**

- The peer's in-cluster URL (e.g. `http://kira.witwave-self:8000`) — the form `call-peer` uses to dispatch to them.
- The peer's **declared skills** (from their card's `skills[]`), one bullet per skill with the skill's name +
  description.
- A trimmed copy of their card's `description` field — enough to judge fit without re-fetching the card every time. Cap
  at ~500 characters; the card on disk is the source of truth for the full version.
- The discovery timestamp (ISO-8601, UTC) so future-you knows how fresh this entry is.

Then update `MEMORY.md` in the same directory: ensure there's a one-line index entry for this peer. Format:

```text
- [Peer — <peer-name>](reference_peer_<peer-name>.md) — <one-line summary>
```

If an entry for this peer already exists, **overwrite** it (this is the refresh path). The whole point of re-running
this skill is to pick up changes after a peer's card evolves.

### 4. Prune peers no longer present

After the probe loop, scan your memory dir for `reference_peer_*.md` files. For any whose underlying peer is NOT in the
current candidate set (e.g. an agent that was deleted from the namespace, or a pod that's been bounced and dropped from
your env vars):

- Delete the file
- Remove its line from `MEMORY.md`

This keeps memory honest. A stale entry pointing at a deleted peer would otherwise hand `call-peer` a URL that 404s.

### 5. Report

Return a structured summary to the caller:

- Number of candidates seen in env vars
- Number of confirmed A2A peers (the candidates that returned a valid agent-card)
- Number of non-A2A candidates skipped (and why if useful — 404, timeout, etc.)
- Number of stale peer entries pruned
- Per-peer: name, URL, one-line description excerpt, top skill IDs

## When to invoke

- **Session start** — the user opens a conversation that mentions delegating, finding help, or coordinating with
  siblings. Run this once to populate the cache before any `call-peer` work.
- **On demand** — the user says "who's available?", "discover peers", "refresh peers", or asks about collaborators by
  role.
- **After a pod restart** — env-var injection is a pod-start snapshot. New peers that joined after your pod started
  won't appear in your env until you restart. After a known restart, re-running this skill picks up the fresh set.

## Out of scope for this skill

- **Cross-namespace discovery** — service env vars only cover the pod's own namespace. Cross-namespace peers need a
  different mechanism (operator-rendered peers list, or DNS + an out-of-band peers manifest). When a real
  cross-namespace need appears, scaffold a separate skill rather than overloading this one.
- **Calling a peer** — that's `call-peer`. This skill only discovers + caches.
- **Choosing the best peer for a task** — the LLM does that judgment work directly from the cached card prose; no
  separate skill needed.
- **Granting cluster API access** — explicitly avoided. The whole point of using env vars is to keep the agent's RBAC
  surface zero. If you ever NEED kube-API access, that's a different conversation about security posture.
