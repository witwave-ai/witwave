---
name: roster-audit
description:
  Maintain a current, queryable directory of the agent roster — who is deployed, who is up vs down, what job functions
  each agent has, and who Milo can reach over A2A. Runs every heartbeat to refresh the directory and record a snapshot;
  also answers on-demand human questions. Trigger when the user says "who's up?", "who's down?", "team roster", "what's
  going on with the team?", "is <agent> available?", "who can take care of <X>?", or "roster audit". Read-only — records
  to memory, never mutates the cluster.
version: 0.1.0
---

# roster-audit

Build and keep current the team's **roster directory**: a single source you can ask _who is on the team, what does each
member do, and who is available right now_. This is Milo's core Agent Resources job — the HR-style "who's who and who's
free to take work" view of the team.

The skill has two jobs:

1. **Refresh the roster directory** every time it runs — reconcile the deployed roster, each agent's availability, and
   each agent's declared job functions into one human-readable directory plus a compact snapshot.
2. **Answer roster questions** on demand — "who's up?", "is evan available?", "who can take care of a docs fix?" — from
   the directory the refresh produced.

## What this skill is (and is NOT)

Milo answers the **roster / availability / capability** question: _who is on the team, what do they do, and who can take
work right now?_ Keep the lens there.

- **NOT platform reliability.** Pod restart deltas, OOM kills, PVC capacity, resource pressure, release-pipeline health
  — that is **Mira's** `platform-health`, not this skill. If an agent is down, record it as unavailable and note it; do
  **not** open a deep pod-triage investigation. If a down agent looks like a real platform fault, that is a finding for
  Mira/Zora, not work for Milo.
- **NOT deep roster-consistency auditing.** GitHub profile fields, avatars, website cards, pronoun drift — that is the
  future `roster-consistency-check` skill. This skill notes obvious deployed-vs-documented drift it trips over, but does
  not go chase profile fields.
- **NOT coordination.** Deciding who works on what, dispatching peers, cutting releases — that is **Zora**. Milo is one
  of the sources Zora (or a human) _asks_ "who can take care of this?"; he does not assign the work himself.
- **NOT a repair bot.** This skill is read-only. Milo carries namespace-write access for approved pod lifecycle actions,
  but those belong to a separate, explicitly-authorized lifecycle workflow — never to this audit.

## Hard boundaries

Default posture is read-only. This skill never mutates cluster, GitHub, or source state.

Allowed automatically:

- Read-only `ww`, `kubectl`, `gh`, `git`, `curl`, and shell commands.
- Probe each agent's `/.well-known/agent.json` over A2A (the `discover-peers` mechanism).
- Read repo files under the source checkout and read your own + peers' memory.
- Write the roster directory + snapshots to **your own** memory namespace.

Requires explicit human approval in the triggering request:

- Any pod eviction/deletion, `kubectl patch`/`delete`/`rollout restart`, or other namespace mutation (that is a future
  lifecycle skill, not this one).
- Editing SOPS files, secrets, RBAC, deployment/bootstrap commands, or any source commit/push.
- Messaging peers with instructions that would change team behavior. (A read-only A2A probe is fine; a "please do X" is
  not.)

## Inputs from your environment

- **Your own name** — `$AGENT_OWNER` (should be `milo`). Used to mark yourself in the roster and skip self in probes.
- **Your memory dir** — `/workspaces/witwave-self/memory/agents/milo/` per your CLAUDE.md → Memory section.
- **Namespace** — default `witwave-self` (use the namespace from the request if one is supplied).
- **Source checkout** — `/workspaces/witwave-self/source/witwave` (managed by iris). If missing, continue without the
  README role enrichment and note the gap.

## Instructions

### 1. Refresh who you can reach over A2A

Run the `discover-peers` skill (or, if its cache is fresh this tick, reuse it). It enumerates every A2A agent reachable
in the namespace via the Kubernetes service env vars, probes each `/.well-known/agent.json`, and caches confirmed peers
as `reference_peer_*.md` entries with their **declared skills** and **description**. That cache is your source for two
roster columns:

- **A2A-reachable?** — a confirmed card means Milo can actually communicate with this agent (the literal "who is
  deployed that he can communicate with").
- **Job functions** — each card's `skills[]` + `description` are the agent's self-declared capabilities.

### 2. Read the authoritative deployment roster

```sh
ww agent list --namespace witwave-self --json
```

If `ww` lacks kubeconfig/port-forward from inside the pod (a known posture gap for in-cluster agents), fall back to:

```sh
kubectl get witwaveagents --namespace witwave-self -o json
```

Capture per agent: **name**, **phase / Ready**, **enabled**, **backend(s)**, **age**. This is the backbone "who is
deployed and who is up vs down." An agent that is deployed but not `Ready` is **down** even though its Service (and your
discover-peers env var) still exists.

### 3. Enrich job functions with the canonical role directory (best-effort)

Read the team's own roster prose for the authoritative one-line role per agent:

```sh
sed -n '1,200p' /workspaces/witwave-self/source/witwave/.agents/self/README.md
```

Use it to label each agent's job function in plain terms (e.g. iris → "git plumbing + releases", evan → "code defects +
risks"). If the checkout is missing, fall back to the card `description` from step 1 and note the enrichment gap.

### 4. Build the roster directory (reconcile the sources)

For each agent, merge deployment status (step 2) + reachability & declared skills (step 1) + role one-liner (step 3),
and classify **availability**:

| Availability  | Condition                                                                        |
| ------------- | -------------------------------------------------------------------------------- |
| **available** | Deployed, `Ready`, enabled, and A2A-reachable                                    |
| **degraded**  | `Ready` but A2A-unreachable, OR `enabled: false` (a reachability/intent gap)     |
| **down**      | Deployed but not `Ready` (or pod absent)                                         |
| **planned**   | In the README roster but not deployed (e.g. a future member, or Milo pre-deploy) |

Also flag **drift** — the Agent-Resources signal that the roster has gotten incoherent:

- **Undocumented agent** — present in `ww agent list` but absent from the README roster.
- **Undeployed-but-documented** — described as a deployed member in the README but missing from `ww agent list`.
- **Reachability gap** — `Ready` at the CR level but A2A-unreachable; it cannot actually take dispatched work.

### 5. Record the directory + snapshot to memory

Create these under your namespace when memory is available:

- `/workspaces/witwave-self/memory/agents/milo/roster-audit/roster.md` — the **current directory**, overwritten every
  run. This is the file you read to answer roster questions. Human-readable table, one row per agent:

  ```markdown
  # Team roster — YYYY-MM-DD HH:MM UTC

  | Agent | Role / job function | Backend | Availability | A2A | Notes |
  | ----- | ------------------- | ------- | ------------ | --- | ----- |
  | zora  | manager / decisions | claude  | available    | ok  |       |
  | …     | …                   | …       | …            | …   | …     |

  **Summary:** N deployed (M available, K degraded/down) · P planned **Drift:** <none | one line per drift finding>
  ```

- `/workspaces/witwave-self/memory/agents/milo/roster-audit/snapshots/YYYY-MM-DD.jsonl` — append one compact JSON object
  per run, for history ("evan has been down 3 ticks"):

  ```json
  {
    "ts": "YYYY-MM-DDTHH:MM:SSZ",
    "deployed": 0,
    "available": 0,
    "degraded": [],
    "down": [],
    "planned": [],
    "agents": { "<name>": { "role": "", "backend": "", "ready": true, "a2a": true, "availability": "available" } },
    "drift": { "undocumented": [], "undeployed_documented": [], "reachability_gap": [] }
  }
  ```

- `/workspaces/witwave-self/memory/agents/milo/roster-audit/roster-changes.md` — append a one-line entry **only when
  something changed** vs the previous snapshot (an agent flipped availability, a new agent appeared, one disappeared, a
  role drifted). This is how Milo answers "what changed on the team?".

Keep snapshots small: counts, names, short status strings. No secrets, no raw command dumps.

### 6. Report

Return Milo's house shape:

```text
Status: ok | roster-drift | agents-down
Roster: N deployed (M available, K degraded/down) · P planned
Available: <comma-separated names>
Unavailable: <name — reason>, … | none
Drift: <one line, or none>
```

When the caller asked a **specific** question, answer it directly from the directory first, then optionally append the
summary:

- _"who's up?"_ → list available agents.
- _"is evan available?"_ → evan's row + availability.
- _"who can take care of `<X>`?"_ → reason over the cached roles/`skills[]` and name the best-fit available agent(s)
  (e.g. a docs fix → kira; a bug → evan; a release → iris). If the best-fit agent is down, say so and name the next
  best, or note that nobody available covers it.

## When to invoke

- **Every heartbeat** — the harness fires this as Milo's tick to keep the directory warm (see `HEARTBEAT.md`).
- **On demand** — a human or peer asks "who's up?", "team roster", "is `<agent>` available?", "who can take care of
  `<X>`?". Run a fresh refresh (cheap) so the answer reflects live state, then answer from the directory.

## Out of scope for this skill

- **Platform/pod triage** (restarts, OOM, PVC, resource pressure, release pipeline) → Mira's `platform-health`.
- **Pod lifecycle mutation** (evict/restart a stuck agent) → a future, separately-authorized lifecycle skill. This skill
  is read-only.
- **Deep profile/identity consistency** (GitHub fields, avatars, website) → future `roster-consistency-check`.
- **Onboarding-readiness checklists, pause/decommission planning** → future Agent-Resources skills. This skill tracks
  the live roster; it does not run those workflows yet.
- **Deciding or dispatching work** → Zora. Milo reports availability; he does not assign the task.
