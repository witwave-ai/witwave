---
name: self-tidy
description:
  Per-agent daily self-maintenance — each agent grooms its own memory namespace + its own agent-card, refreshes its
  cross-agent awareness, and checks its public-presentation surface for drift. Sibling to team-tidy (which is zora-only
  and cross-cutting); self-tidy is byte-identical across all agents and stays scoped to the running agent's own files.
  Trigger on the daily cron (one fire per agent per 24h, staggered) or when the user says "self-tidy", "groom yourself",
  or "do your daily self-maintenance".
version: 0.1.0
---

# self-tidy

Once per 24 hours, every agent runs this skill on themselves to keep their own state coherent. It is **byte-identical
across all agents** — the same SKILL.md is mounted into iris, kira, nova, evan, and zora. Each invocation reads the
acting agent's name from `AGENT_NAME` (or falls back to identity declared in AGENTS.md) and operates on that agent's own
files only.

## Domain — what self-tidy edits

**Strictly own namespace.** This is the boundary that keeps self-tidy safe to run autonomously without coordination:

- ✅ `/workspaces/witwave-self/memory/agents/<own>/**` — your private memory namespace.
- ✅ `.agents/self/<own>/.witwave/agent-card.md` — your harness-side A2A identity card (consumed by `gh-team-list`,
  future `ww team list`, dashboards, and any other surface that reads agent-cards). **Canonical location** — every
  peer has this; it's the one source of truth for what your card says.
- ✅ `.agents/self/<own>/.codex/agent-card.md` — optional backend-local identity card, only if it already exists.
  No peer ships one today; if a future agent adds one, keep it in sync with the canonical `.witwave/` copy.
- ❌ Any other agent's files — that's their lane. If you notice cross-agent drift, log it to your own
  `cross_agent_observations.md` so the next zora team-tidy pass can act on it.
- ❌ Source code (`harness/`, `backends/`, `tools/`, `shared/`, etc.) — never your domain.
- ❌ Your own `AGENTS.md` / your own `.codex/skills/**` — those are team-tidy's territory (cross-cutting changes shaped
  at the team level, not per-agent self-grooming).

The boundary matters because self-tidy fires across all 5 agents on staggered cadences. If two agents tried to edit each
other's files, race conditions would multiply with team size.

## Process

Read these from AGENTS.md before starting:

- **`<own_name>`** — your own agent name (also in `AGENT_NAME` env var)
- **`<peer_names>`** — the other four agents on the team (read from your AGENTS.md "Team coordinator" section)

### 1. Verify the source tree + git identity

```sh
git -C <checkout> rev-parse --show-toplevel
git -C <checkout> status --porcelain
```

If the working tree is missing or dirty, log to your memory's `self_tidy_log.md` and stand down — iris owns checkout
sync.

Pin git identity (idempotent) by invoking the `git-identity` skill.

### 2. Memory consolidation (your own namespace)

Read your `MEMORY.md` index. For each pointer line, verify the target file exists. For each `.md` file in your
namespace, verify it has a corresponding pointer in `MEMORY.md`. Three classes of fix:

- **Orphan file** — exists on disk, no pointer in `MEMORY.md`. Add a pointer, hooked from the file's frontmatter
  `description` field.
- **Dead pointer** — line in `MEMORY.md`, no target file. Remove the pointer.
- **Drift between pointer and file** — pointer description doesn't match the file's `description:` frontmatter. Update
  the pointer to match.

Additional consolidation:

- **Stale `project_*` memos** — open the body of each, look for status markers like `**Pending**`, `**Open**`,
  `**REMIND USER**`. If the underlying issue has been resolved (verifiable from `git log`, current code state, or an
  entry in this agent's findings file marked `[fixed]`), move the memo's status to `**Resolved on YYYY-MM-DD**` and
  update the index entry to reflect closure.
- **Duplicate memos** — if two memos cover the same topic, merge them into the older one (preserving its filename for
  link stability) and remove the newer one. Update the index.

Cap: **edits to memory only**. No source-code mutations. ≤50 lines changed.

### 3. Cross-agent awareness

For each peer in `<peer_names>`:

```sh
PEER_INDEX=/workspaces/witwave-self/memory/agents/<peer>/MEMORY.md
PEER_FINDINGS=/workspaces/witwave-self/memory/agents/<peer>/project_*_findings.md
```

Read each peer's index. Compare to your own `reference_peer_<peer>.md` (if one exists; create if missing). Update your
reference memo with a fresh one-paragraph summary of what that peer is currently working on, what their backlog looks
like, and any in-flight escalations they've logged.

You are read-only on peer namespaces. If a peer's state surfaces something that needs collaboration (e.g., evan's
findings include a candidate that overlaps with nova's lane), file an A2A note via `call-peer` rather than editing their
memory.

If a peer's index is missing or unreadable, note that in your own `cross_agent_observations.md` for the next zora
team-tidy pass.

### 4. Public-presentation drift check

Read your own `.witwave/agent-card.md` (the canonical copy) and any backend-local copy if it exists (e.g.,
`.codex/agent-card.md`) — they should match. Verify:

- **Capability descriptions still match current skills.** List the directories in your `.codex/skills/`. For each,
  verify the agent-card mentions or implies it. Drift = card claims a skill you don't have, or has a skill you've
  deployed but the card doesn't mention.
- **Avatar URL still resolves** (if declared). The convention:
  `Avatar: https://api.dicebear.com/9.x/open-peeps/svg?seed=<own>`. Verify with
  `curl -fsSL --max-time 5 -o /dev/null -w "%{http_code}\n" "<url>"`. Non-200 → flag in `cross_agent_observations.md`,
  don't change the URL yourself.
- **Trigger phrases match what your skills say in their `description:` frontmatter.** If a skill's description has
  drifted from the agent-card's "what you can ask" examples, update the agent-card.

Apply minor drift fixes inline (the agent-card edit is in your scope). Substantive drift (e.g., a skill removed but the
card still describes it as core) flags to `needs-human-review.md` rather than auto-fixing — substantive scope changes
deserve human review.

### 5. Apply changes

Group everything into ONE atomic commit:

- Memory namespace edits (Step 2)
- Reference peer memos updated (Step 3)
- Agent-card drift fixes (Step 4)

Cap: **≤50 lines changed**. Pure single-purpose: `agents(<own>): self-tidy <YYYY-MM-DD>`. If the day's drift adds up
to >50 lines, prioritize most-impactful edits and defer the rest to tomorrow's run (memory consolidation > peer
awareness > agent-card drift).

If nothing has drifted (no orphans, no dead pointers, no peer-summary changes worth reflecting, agent-card matches
current skill set), commit nothing — empty self-tidy passes are healthy.

### 6. Commit + delegate push to iris

```sh
git -C <checkout> add <files-touched>
git -C <checkout> commit -m "agents(<own>): self-tidy <YYYY-MM-DD>"
```

Delegate push via `call-peer` to iris with her `git-push` skill. Same nova-commits / iris-pushes / kira-commits contract
every other peer follows.

### 7. Watch CI; revert on red

After iris reports the push outcome:

- **Green** → done; log success in `self_tidy_log.md` (date, files touched, line count, commit SHA).
- **Red** → fix-forward in one follow-up commit (e.g., a malformed frontmatter field), iris-delegated push, watch again.
  If CI is still red, batch-revert the self-tidy commit via iris and log the failure in `self_tidy_log.md` with
  `[reverted: <reason>]`. Same fix-forward-then-revert semantics evan and nova use.

### 8. Log the run

Write a log entry to your namespace:

```text
/workspaces/witwave-self/memory/agents/<own>/self_tidy_log.md
```

Append-only. One entry per run. Format:

```markdown
## YYYY-MM-DD — self-tidy

**Status:** <complete | empty | reverted: reason>. SHA: `<sha>`. Lines: <N>.

- Memory consolidation: <N orphans removed, M dead pointers, K drifts fixed>.
- Cross-agent awareness: <which peer reference memos updated>.
- Agent-card drift: <none | minor fix described | flagged-to-human-review>.
- Notes: <any observations worth carrying to tomorrow's run>.
```

## Caps

- **Max 1 self-tidy commit per agent per day** (the daily cadence enforces this; explicit cap as a safety floor).
- **≤50 lines changed per commit** (atomic, minimal).
- If two consecutive self-tidy runs produce no commit (genuinely-clean state), skip the next run's memory- consolidation
  step and go straight to peer-awareness + agent-card check (cheaper) — saves LLM time on a durable steady state.

## Out of scope

- **Cross-agent edits.** Other agents' files are their territory. Cross-cutting changes (consistency drift across
  multiple agents, pattern propagation) are zora's `team-tidy` skill, not self-tidy.
- **AGENTS.md / SKILL.md edits.** Those are identity files where cross-cutting consistency matters; team-tidy owns them.
  self-tidy stays in agent-card + memory.
- **Source code edits.** Forever out of scope for self-tidy regardless of agent.
- **Avatar generation.** self-tidy verifies the avatar URL resolves; it doesn't change the URL or generate new avatars.
  Avatar selection is a per-agent decision the user makes by editing the agent-card.
- **GitHub-side identity.** GitHub avatars are uploaded once via the web UI at account creation; self-tidy can't touch
  those. The in-repo agent-card declaration is the team's source of truth for avatar.
