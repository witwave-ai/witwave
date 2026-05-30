---
name: team-upgrade
description:
  Keep the team on the latest released version, safely, with Milo as the canary. Each run advances a staged campaign by
  ONE step: upgrade YOURSELF first and soak longest, and only once you are proven on the new version do you cascade it to
  the peers one at a time (lowest blast radius first). The campaign target is pinned once a campaign starts, so newer
  releases are picked up between campaigns, not mid-flight. On any failure, roll that agent back to its last-known-good,
  halt, quarantine the bad version, and wait for a newer release. Spans hours to days by design. Trigger from the
  team-upgrade job, or when the user says "upgrade the team", "roll out the latest", "check for updates", or "is everyone
  on the latest version?".
version: 0.3.0
---

# team-upgrade

Roll the team forward to the latest released version **with yourself as the canary**. You go first, soak the longest,
and only cascade a version to the peers once it has proven itself on you — so a bad release is caught on the
Agent-Resources agent (you), and the team behind you is protected. Then roll the peers one at a time, lowest blast
radius first.

Every upgrade is driven through the **`ww` CLI** (`ww agent upgrade`), never a hand-rolled `kubectl patch` — the CLI
does a targeted image-tag patch that leaves every sibling field of the CR untouched.

## Operating model: one safe step per run, over hours to days

This skill is **resumable and paced by its own cadence**. Each invocation does at most ONE step, then stops. The job
fires it periodically; the gap between runs IS the soak time, so a full rollout deliberately spans **hours to days**.
State lives in memory (below); each run picks up where the last left off — or does nothing when the team is already
current.

A single run, in order — stop at the first one that acts:

1. **Guards.** Kill-switch present, or the in-flight target now quarantined? → stop.
2. **Determine this run's target.** A campaign in flight pins its target; otherwise resolve the latest release and start
   one only if the team has drifted.
3. **Operator check** (best-effort — see below).
4. **Self-canary (you go first).** Are you on the target and proven? If not → upgrade / verify / soak / roll back
   yourself, and stop.
5. **Cascade one peer** (only once you are proven) → upgrade + verify the next peer in risk order, and stop.
6. **Self-evolve** the soak windows from your own campaign history.

## Hard boundaries

- **`ww` CLI only for mutations.** Upgrades and rollbacks go through `ww agent upgrade`. Never `kubectl patch`/`edit` a
  WitwaveAgent CR directly. Always pass `--tag <TARGET>` explicitly (without it, `ww agent upgrade` defaults to the `ww`
  version baked into _your_ container, which may not be the target).
- **You upgrade agent IMAGES only, harness + backends together to one tag.** Not the operator (needs cluster-admin), not
  secrets/RBAC/Deployments/pods, not model/effort config (see "Out of scope"). No per-container partial bumps — an agent
  is "on target" only when its harness tag **and every backend tag** equal `TARGET`, so split bumps would never settle.
- **One agent per run.** Never roll two agents in the same run. Serial, verified progression is the whole safety story.
- **You are first, never behind a peer.** A peer is never upgraded to a version you haven't proven. A newer release is
  picked up as the _next_ campaign once the current one finishes — never mid-cascade (that would thrash and could starve
  the rollout if releases are frequent).
- **Kill switch.** If `team-upgrade/PAUSED` exists, do nothing but report "paused".
- **Honor quarantine.** Never attempt a version in `quarantine.md`; wait for a strictly newer release.

## Inputs

- **Namespace** — default `witwave-self`. **Memory dir** — `/workspaces/witwave-self/memory/agents/milo/team-upgrade/`.
- **`gh`** — authoritative for the latest release. **`ww`** — `ww agent list/status` (reads, `--json`),
  `ww agent upgrade` (the mutation).
- **`date -u`** — for soak-elapsed math and timestamps. **Self-name** — `milo`.

## Instructions

### 1. Determine this run's target (pin per campaign)

Read `state.md`. Is there an **in-flight campaign** — a `campaign_target` that is set, not yet complete, and not
quarantined?

- **Yes** → `TARGET` = that pinned `campaign_target`. Do **not** re-resolve "latest" this run. Finishing the in-flight
  campaign before adopting a newer release is what prevents thrashing. (You stay ahead of the peers either way: you
  proved `TARGET` before any peer received it.)
- **No** (no campaign in flight) → resolve the latest release:

  ```sh
  gh release list --repo witwave-ai/witwave --json tagName,isLatest --jq '.[] | select(.isLatest) | .tagName'
  ```

  `LATEST` = that tag minus a leading `v`. If `gh` is unavailable → report and stop (don't guess). If `LATEST` is in
  `quarantine.md` → stop (wait for a newer release). Confirm its images published (a failed release pipeline means
  `ImagePullBackOff`):

  ```sh
  gh run list --repo witwave-ai/witwave --workflow Release --limit 5
  ```

  If the `Release*` workflows for `LATEST` haven't all succeeded → not-ready; report and stop. Read the team:

  ```sh
  ww agent list --namespace witwave-self --json
  ```

  If **you and every enabled peer** are already on `LATEST` → no-op, report "current". Otherwise **start a campaign**:
  write `campaign_target = LATEST` to `state.md`, set `TARGET = LATEST`, and proceed.

### 2. Operator check — best-effort, never a hard gate you can't run

In principle the operator should never be older than the agents it reconciles (a release can carry CRD-schema changes an
old operator doesn't understand). In practice **your namespace-scoped RBAC usually cannot read the operator** —
`ww operator status` lives in `witwave-system` + cluster-scoped CRDs, which `agentLifecycle` (a Role in `witwave-self`)
does not grant. So treat this as best-effort, not a blocker:

```sh
ww operator status --namespace witwave-system   # may be forbidden — that's expected
```

- **Readable and operator behind `TARGET`** (or a major-skew "upgrade blocked") → escalate to Zora via `call-peer`
  ("operator at `<v>`, target `<TARGET>` — operator upgrade needed before agents roll") and HOLD this campaign. You
  **never** run `ww operator upgrade` yourself — it needs cluster-admin you don't have and has no auto-rollback; it's a
  human step.
- **Forbidden / unreadable** (the normal case) → record the access gap once in `campaign-log.md` and **proceed**. You
  are the canary: if the operator is too old for `TARGET`, _you_ are the first thing that breaks on it, and you roll
  yourself back and quarantine (Step 3) before any peer is touched. The per-agent rollback is the real backstop; keeping
  the operator current is a documented human precondition (see `bootstrap.md` Step 12).

### 3. Self-canary — you go first

Read what you're running: `ww agent status milo --namespace witwave-self --json` (your `harness.tag` + every
`backends[].image.tag`).

- **You are behind `TARGET`** → you are the canary; upgrade yourself first (this is also how you "always have the latest
  `ww` before driving anything"). Write your current tags as `self last-known-good` AND stamp `self_upgrade_time` to
  `state.md` **first** (once you fire the upgrade your pod terminates and nothing after it runs), then:

  ```sh
  ww agent upgrade milo --namespace witwave-self --tag <TARGET> --yes --no-wait
  ```

  `--no-wait` because you cannot watch your own pod roll. If the command **errors** (forbidden, admission-policy reject,
  transient) → it did not happen; report the error and stop. The next run sees you still behind and retries; if the
  self-upgrade fails on **2 consecutive runs**, escalate to Zora/human (your access or the target is wrong) and stop
  retrying. On success → stop; your **next run, on the new image, verifies you** (below).

- **You are on `TARGET` but not yet `self-proven`** → verify, then decide on soak:
  - **Healthy** = `phase: Ready`, `observedGeneration` current, every tag == `TARGET`, zero new restarts
    (`kubectl get pod -n witwave-self -l app.kubernetes.io/instance=milo -o jsonpath='{.items[*].status.containerStatuses[*].restartCount}'`),
    and your own recent heartbeat / `roster-audit` ran. **Not** healthy → **self-rollback**:
    `ww agent upgrade milo --tag <self last-good> --yes --no-wait`, quarantine `TARGET`, clear `campaign_target`,
    escalate to Zora, stop. The team never proceeds.
  - **Healthy but soaking** (`now − self_upgrade_time < canary_soak_hours`, default **24h**) → hold; report "soaking".
    Stop.
  - **Healthy and soak elapsed** → mark `self: proven on <TARGET>` in `state.md`. The cascade begins next run.

You are the deliberate canary: longest soak, and you roll yourself back on trouble. **But** the verify/rollback above
only runs if you came back healthy enough to fire the job at all. A hard brick (the new image CrashLoops your harness,
so no job scheduler fires) means nothing here runs — Mira detects the CrashLoop and a human rolls your CR back to
last-known-good. That is the canary's accepted risk; the team is protected either way.

### 4. Cascade to the peers — one per run, lowest blast radius first

Only once you are `self-proven` on `TARGET`. **Skip disabled agents** (`enabled: false`).

| Cohort            | Agent(s)                                | Why this order                                                                                     |
| ----------------- | --------------------------------------- | -------------------------------------------------------------------------------------------------- |
| 1 — expendable    | `piper`                                 | Read-only on source, outward-facing only; a bad version costs at most a missed post                |
| 2 — observer      | `mira`                                  | Read-only platform observer (and the only `codex` agent — your real canary for codex-only changes) |
| 3 — workers       | `evan`, `nova`, `kira`, `finn`, `felix` | A failure stalls one domain; Zora routes around it, the team keeps shipping                        |
| 4 — critical path | `zora`, then `iris`                     | Single points of failure — last, most soak; keep Iris on known-good longest as the recovery lever  |

Pick the next enabled peer not on `TARGET`, in this order. Record its last-known-good tags, then:

```sh
ww agent upgrade <peer> --namespace witwave-self --tag <TARGET> --yes --timeout 5m
```

Keep the default wait (no `--no-wait`) for peers — the rollout-to-Ready is your first health gate. Then **verify** (same
checks as Step 3: `phase: Ready`, `observedGeneration` current, every tag == `TARGET`, no new restarts, A2A reachable, a
clean post-upgrade tick — confirm via the agent's own heartbeat or a `call-peer` liveness ping).

- **Pass** → record + stop; the next run takes the following peer.
- **Fail** → roll that peer back (`ww agent upgrade <peer> --tag <last-good> --yes --timeout 5m`, which waits for it to
  recover), **halt**: quarantine `TARGET`, clear `campaign_target`, escalate to Zora, stop.

**Campaign complete** when you and **every enabled peer** are on `TARGET` (disabled agents don't count). Mark it done
and clear `campaign_target`; the next run with no campaign in flight (Step 1) will pick up any newer release.

**Backend awareness (not a partial bump).** You always move an agent's harness + backends together to `TARGET`. But note
what the release changed
(`git -C /workspaces/witwave-self/source/witwave diff --stat "v<from>".."v<TARGET>" -- harness/ backends/`): you run the
`claude` backend, so a `codex`-only change isn't truly proven by your canary — `mira` (the only `codex` agent, cohort 2)
is the meaningful canary for it. Weigh her verification accordingly for codex-touching releases.

### 5. Self-evolve the soak

Each run, before choosing soak windows, read `campaign-log.md` and adapt — record the choice + reason in `state.md`:

- **Tighten** (`canary_soak_hours` down toward a 6h floor) after several consecutive campaigns completed clean with zero
  rollbacks — confidence earned.
- **Loosen** (canary soak up toward a 48h ceiling; add an extra verification tick per agent) after any rollback or
  quarantine in the recent window — a version bit us, so be more careful for a while.
- Reset to the 24h default when history is empty or stale.

A light autotune: faster while it's safe, slower right after it isn't.

### 6. Report

```text
Status: current | operator-hold | self-canary | self-soaking | cascading | rolled-back | paused | blocked
Target: <TARGET or none> (operator: <ver | unreadable>)
Self: <on TARGET, proven | soaking Nh/Mh | upgrading | rolled-back | behind>
Peers: <N>/<enabled-total> on target — done: <names> · pending: <names>
This run: <what you did | no-op | paused>
Quarantine: <none | versions> · Next: <agent | soak | newer release | complete>
```

## Memory layout

Under `/workspaces/witwave-self/memory/agents/milo/team-upgrade/`: `state.md` (`campaign_target`, `self_upgrade_time` +
self-state, per-agent last-known-good + status, chosen soak windows), `campaign-log.md` (append-only audit: every
upgrade / verify / rollback / escalation / access-gap with time, agent, from→to, outcome), `quarantine.md` (bad
versions; never retried until a newer release), `PAUSED` (kill switch). Keep entries compact — versions, names, short
status + evidence. No raw command dumps, no secrets.

## Out of scope

- **The operator upgrade** — best-effort detect + escalate only (Step 2). Cluster-admin + no auto-rollback = human-run.
- **Model / effort config rollout to the team** — your own 4.8 + `CLAUDE_EFFORT=max` is the canary for the new
  model/effort, but rolling that to peers is a **config/git change** (it lives in each agent's `backend.yaml` / env, not
  an image tag — and `agentImagePatchPolicy` blocks you from patching non-image CR fields). Once you've proven 4.8/Max
  on yourself, **recommend** the team move to Zora/a human; don't push it via image upgrades.
- **Critical-fix fast-tracking** — a campaign target is pinned, so an urgent newer release waits for the current
  campaign to finish. Jumping the queue is a human override (quarantine the in-flight target or pause + redeploy); don't
  special- case it autonomously.
- **Deep platform triage** — if a rollback doesn't recover an agent, that's Mira/Zora's incident, not your spelunking.
- **Deciding team work** — Zora dispatches; Iris ships; you keep everyone on a current, proven version.
