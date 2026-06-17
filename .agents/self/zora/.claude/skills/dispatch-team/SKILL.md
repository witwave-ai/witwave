---
name: dispatch-team
description:
  Single decision-loop pass. Reads team state (git log, peer memories, CI status, peer health), applies the priority
  policy from CLAUDE.md, decides what (if anything) to dispatch this tick, dispatches via call-peer, logs decision
  rationale to memory. The main work skill — runs once per heartbeat. Trigger when the heartbeat fires (the harness's
  heartbeat scheduler invokes this) or when the user says "run a decision pass" / "do your thing" / "tick".
version: 0.1.0
---

# dispatch-team

One decision-loop pass. Run by the heartbeat scheduler every 60 minutes (v1).

## Inputs

None from the prompt. Read state from:

- `git log origin/main` (the canonical source of truth for what's landed)
- Peer `MEMORY.md` indexes + deferred-findings memory files
- Recent CI workflow runs (via shell-out to `gh run list` — read-only; iris owns the auth, but read on `main` is
  unauth-allowed for public repos)
- **Active HTTP probe of each peer's harness `/.well-known/agent.json`** (every tick — Step 2d). The probe is the
  authoritative signal for peer liveness; without it, peer-OFFLINE flags can persist indefinitely against
  actually-running pods (this happened to finn on 2026-05-08 — flag stuck despite finn being 3/3 Running for 3+ hours).
- Your own memory: `decision_log.md` (your last decisions), `team_state.md` (last-fire times + per-peer liveness state),
  `peer_heartbeat_log.md` (probe history, diagnostic only)

## Instructions

### 1. Pause-mode check

If your `pause_mode.flag` file exists in your memory namespace, you're in observation-only mode (per CLAUDE.md → "Pause
control"). Read state, log what you WOULD have decided to `decision_log.md` with a `[paused: would-have]` prefix, then
exit. Do NOT dispatch.

```sh
test -f /workspaces/witwave-self/memory/agents/zora/pause_mode.flag && echo "PAUSED"
```

### 2. Read team state (every tick)

Build a current snapshot:

**Memory-read safety (mandatory).** Your working files (`decision_log.md`, `team_state.md`, `peer_heartbeat_log.md`)
grow over time and can exceed the backend's ~1 MiB JSON message buffer; a whole-file read of an oversized one crashes
the whole tick with JSON-RPC `-32603` ("JSON message exceeded maximum buffer size") and loses the dispatch (this hit
ticks on 2026-06-01). NEVER read these whole — read them bounded:

```sh
# team_state.md: only the most recent snapshot (top block = current state incl. chosen_levers + polish tiers)
awk '/^## .*snapshot/{n++} n>1{exit} {print}' /workspaces/witwave-self/memory/agents/zora/team_state.md
# decision_log.md / peer_heartbeat_log.md: recent tail only
tail -c 200000 /workspaces/witwave-self/memory/agents/zora/decision_log.md
```

#### 2a. Git state

```sh
LATEST_TAG=$(git -C <checkout> describe --tags --abbrev=0 2>/dev/null)
COMMITS_SINCE_TAG=$(git -C <checkout> rev-list --count "${LATEST_TAG}..origin/main" 2>/dev/null)
LAST_RELEASE_TIME=$(git -C <checkout> log -1 --format=%cI "${LATEST_TAG}" 2>/dev/null)
LAST_COMMIT_TIME=$(git -C <checkout> log -1 --format=%cI origin/main 2>/dev/null)
```

#### 2b. CI state across every commit since latest tag

A binary built from a _failing_ commit is broken even if HEAD has moved past it — so checking only HEAD's CI runs the
way the original v1 policy did silently masked multi-hour red windows (the 2026-05-07 ww-CLI gofmt incident: 3
consecutive `CI — ww CLI` failures spanning ~1h45m were invisible to zora because each subsequent commit's "CI — docs"
run on the new HEAD was green, while the prior commits' ww-CLI failure aged out of the HEAD-only filter).

Today's check covers **every commit between `v<latest-tag>` and `origin/main`**:

```sh
LATEST_TAG=$(git -C <checkout> describe --tags --abbrev=0)
COMMITS=$(git -C <checkout> rev-list "${LATEST_TAG}..origin/main")  # newest-first
gh run list --branch main --limit 50 --json name,status,conclusion,headSha
```

For each `(workflow_name, headSha)` pair where `headSha` ∈ COMMITS:

- **Any concluded with `failure`** → mark CI as `[red on <commit_sha[0:8]>: <workflow>]` and feed this signal into
  Priority 1 (red-CI auto-dispatch). Order doesn't matter — one failure on any commit since the tag blocks the whole
  window from being considered "green."
- **Any still `in_progress`** → mark CI as `[settling]`. Don't fire release-warranted; do still proceed with
  cadence-floor dispatches because settling is normal post-push state.
- **All concluded `success`** across every (workflow, commit) pair → mark CI as `[green]`.

Your pod has `GITHUB_TOKEN` + `GITHUB_USER` injected from the `zora-claude` secret (added 2026-05-07 to close the
previous "infer CI from indirect signals" gap). You're read-only on git/gh per your tool posture; iris remains the
team's write authority for push, tag, and gh-API writes.

#### 2c. Peer memories

For each peer in `[iris, nova, kira, evan]`:

```sh
PEER_MEMORY=/workspaces/witwave-self/memory/agents/<peer>/MEMORY.md
PEER_FINDINGS=/workspaces/witwave-self/memory/agents/<peer>/project_*_findings.md
```

Read the index. As of 2026-05-07 all three findings-producing peers (evan / nova / kira) use the same status- marker
schema — `[pending]`, `[flagged: <reason>]`, `[fixed: <SHA>]` — going forward. Sections written before that date are
still in their original narrative format, so the **per-peer adapter** below combines a marker count on recent sections
with a narrative-count fallback on older ones:

| Peer | Findings file                           | Adapter — count "open" entries                                                                                                                                                                                                                                                                                                                                                                                                                                                            |
| ---- | --------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| evan | `project_evan_findings.md`              | Count `[pending]` + `[flagged: …]` markers (canonical schema since day one). Look for `[CRITICAL]` severity markers in risk-work output.                                                                                                                                                                                                                                                                                                                                                  |
| finn | `project_finn_findings.md`              | Count `[pending]` + `[flagged: …]` markers (canonical schema). Look for `[CRITICAL]` markers — these are doc-promised features that don't exist (users hitting missing endpoints / unread env vars). `[CRITICAL]` blocks the medium-quality-bar release gate same as evan's CRITICAL CVEs. `[flagged: above-tier-N]` is the most common flag and is benign — re-attempted on the next higher-tier dispatch.                                                                               |
| nova | `project_code_findings.md`              | For sections dated **2026-05-07 onward**: count `[pending]` + `[flagged: …]` markers (same canonical schema). For sections dated **before 2026-05-07** (legacy narrative format): read the most recent dated narrative section header (`## YYYY-MM-DD`); within it, sum the bullet-list counts nova recorded inline (e.g., `× 94`, `× 90`, "118 remaining diagnostics").                                                                                                                  |
| kira | `project_doc_findings.md`               | Same shape as nova: marker schema on 2026-05-07-or-newer sections; narrative-bullet count on older ones.                                                                                                                                                                                                                                                                                                                                                                                  |
| iris | n/a (service peer) + `stuck_commits.md` | No backlog count, but ALWAYS read `agents/iris/stuck_commits.md` if present. Each `[open]` entry there is a stuck-push state: a peer asked iris to push, iris hit a rebase conflict or second-push rejection on retry-once, and N commits are sitting ahead of `origin/main` on her local checkout with no path forward. Treat as P1 escalation (see "Priority 1 — Urgent" walk). Pause cadence dispatches to the blocked caller-peer until iris flips the entry to `[resolved: HH:MMZ]`. |

The legacy-narrative branch is a transient compatibility shim — once the legacy sections age out (typically as peers run
new sweeps that supersede the older entries' relevance), the adapter degenerates to a pure marker count and the schema
is fully uniform team-wide. Until then, the adapter gets the count _right enough_ — within ±5 — for backlog tiebreaking.

**Stuck-commits triage flow (when iris's `stuck_commits.md` has any `[open]` entry):**

1. **First action this tick:** mirror the entry to `escalations.md` as
   `[escalation: stuck-commits-iris-blocked: <caller-peer>]` so it shows up in `ww escalations` for the user. Include
   iris's verbatim git stderr excerpt and her recovery hint so the human can diagnose without trawling memory. New
   escalation OR existing-but-still-open both count — keep it surfaced on every tick until resolved.
2. **Pause cadence dispatches to the blocked caller-peer.** Don't fire `<caller>'s` cadence-mandated work while their
   commits are stuck — every new commit they produce widens the stuck pile. The skip is silent in `decision_log.md`
   ("evan bug-work cadence breach DEFERRED — iris stuck-commits open since HH:MMZ"). Other peers continue normally if
   their commits aren't entangled.
3. **Don't auto-retry iris.** Stuck-commits state is iris's signal that retry-once already exhausted; firing another
   `git-push` dispatch just produces another `[open]` entry. The retry path runs through the human: they resolve the
   conflict, then ask iris to push again, and iris flips the entry to `[resolved]` on success.
4. **Watch for resolution.** Once the entry flips to `[resolved: HH:MMZ]`, log
   `[escalation: stuck-commits-iris-blocked: <caller-peer>] CLOSED` in `decision_log.md`, mirror the closure to
   `escalations.md`, and resume `<caller>'s` cadence dispatching from the next tick.

#### 2d. Peer health (active probe + heartbeat history)

Two signals combine here. The active probe is authoritative; the heartbeat history is a fallback diagnostic.

**Active probe (every tick).** For each peer in `[iris, nova, kira, evan, finn, piper]`, hit the peer's harness
`/.well-known/agent.json` endpoint via the URL in `reference_peer_<peer>.md`. The probe is a plain HTTP GET with a
5-second timeout; no auth required (this is the A2A discovery path, public by design). Expected response is HTTP 200
with a JSON body describing the agent (name, skills, etc.). We use this endpoint rather than a `/health` route because
the harness proxies A2A discovery but does NOT expose a separate `/health` path — calling `/health` returns 404 on every
peer (verified 2026-05-08 when the first version of this Step 2d wired the wrong path). A 200 from
`/.well-known/agent.json` means the harness is running AND its backend is reachable enough to render the agent card,
which is a slightly stronger liveness signal than a bare `/health` would have provided.

```sh
for peer in iris nova kira evan finn piper; do
  # Match only valid URL characters (NOT [^[:space:]]) so a markdown-backtick-
  # wrapped URL in the reference_peer file (e.g. `http://iris.witwave-self:8000`)
  # doesn't capture the trailing backtick into PEER_URL. That bug made the probe
  # curl a malformed URL and false-report every peer PROBE-FAIL — the root cause
  # of the 2026-06-01 false stuck-peer auto-pause that idled the team ~43h.
  PEER_URL=$(grep -m1 -oE 'http[s]?://[A-Za-z0-9._~:/?#@!$&()*+,;=%-]+' /workspaces/witwave-self/memory/agents/zora/reference_peer_${peer}.md 2>/dev/null)
  if [ -z "$PEER_URL" ]; then continue; fi
  curl -fsS --max-time 5 "${PEER_URL%/}/.well-known/agent.json" >/dev/null 2>&1 && echo "${peer}=ONLINE" || echo "${peer}=PROBE-FAIL"
done
```

**Two-probe confirmation before flipping ONLINE → OFFLINE.** A single failed probe is treated as transient (network
blip, mid-roll, garbage-collection pause). State machine in `team_state.md` per peer:

- **ONLINE** → probe succeeds → stays ONLINE.
- **ONLINE** → probe fails → flip to **PROBE-FAILED-ONCE** (do NOT mark OFFLINE yet; still treat as eligible for
  dispatch this tick). Log the probe failure to `peer_heartbeat_log.md`.
- **PROBE-FAILED-ONCE** → probe succeeds next tick → flip back to ONLINE. Log the recovery.
- **PROBE-FAILED-ONCE** → probe fails again → flip to **OFFLINE**. This is the confirmed state; ~120 minutes total
  elapsed (two consecutive 60-minute ticks).
- **OFFLINE** → probe succeeds → flip back to ONLINE immediately (single-success recovery; we're optimistic on the
  upswing). Log the recovery.
- **OFFLINE** → probe fails → stays OFFLINE.

This asymmetric design (two failures to mark OFFLINE; one success to mark ONLINE) avoids dispatch-flapping while keeping
a recovered peer reachable on the very next tick.

**What to do with confirmed-OFFLINE peers (skip + log).** When a peer is OFFLINE during priority walk:

- **Skip the peer** for cadence-floor dispatches and polish-tier advances. Log it in `decision_log.md` under "Peer
  health snapshot" so the rationale for skipping is visible to humans.
- **Do not** auto-escalate via `escalations.md` for a single OFFLINE peer — that's expected during pod rolls and image
  bumps. Only escalate `[escalation: peer-offline-extended]` to `escalations.md` if a peer has been OFFLINE for **6
  consecutive ticks** (~90 minutes) — at that point the operator should investigate.
- **Special case finn:** if finn is OFFLINE, gap-work cadence breaches accumulate silently. Note that in
  `decision_log.md` ("finn skipped — would have dispatched gap-work, breach window N min") so the gap-work debt is
  visible.

**Heartbeat history (diagnostic only).** `peer_heartbeat_log.md` you've maintained for cycles records when each peer
last replied HEARTBEAT_OK. The harness scheduler runs heartbeats independently; their replies land in YOUR conversation
log when peers respond to your ticks but otherwise are not visible to you. Use this log as a debugging aid when
investigating "why did probe fail?" — but the active probe above is the load-bearing signal, not heartbeat-OK history.
(Heartbeat replies can be stale from peer-side caching; the HTTP probe is authoritative.)

#### 2e. Last-fire times

Read your `team_state.md`: when did you last dispatch each peer for which skill?

#### 2f. Chosen-levers (your throughput tuning)

Read the `chosen_levers:` block in `team_state.md`. This is where you record the throughput knobs you've decided to
tune, with the rationale, so the decision survives pod restarts and image-roll-forward boundaries. See `CLAUDE.md` →
"Throughput targets" for the policy framing and the lever menu.

Schema:

```yaml
chosen_levers:
  set_at: <RFC3339 timestamp of last update>
  rationale: |
    <one-paragraph why — typically references the user directive, recent decision-log
     observations, an autotune fire, or a peer's reply that surfaced "I exhausted the cheap finds
     at this depth.">
  cadence_floors: # null = use default from CLAUDE.md cadence-floors block
    evan_bug_work_hours: 1.5 # float hours (current default; safety floor: 1)
    evan_risk_work_hours: 4 # safety floor: 2
    nova_code_cleanup_hours: 4 # safety floor: 2
    kira_docs_cleanup_hours: 3 # safety floor: 2
    finn_gap_work_hours: 3 # safety floor: 2
    kira_docs_research_days: 1 # safety floor: 0.5
  polish_tier_advance_zero_streak: 1 # consecutive 0/0/0 runs before advancing the ladder; default 1 since 2026-05-10; safety floor: 1
  default_depths: # per-skill default depth at dispatch time
    evan_bug_work: 5
    evan_risk_work: 5
    finn_gap_work: 3 # finn "tier" not "depth" but same semantics — risk tolerance floor
  concurrency_max_per_tick: 2 # max parallel peer dispatches per tick; default 2 since 2026-05-10; v1 ceiling 2
```

If the block is absent, you're at defaults — apply the values shown above. When you decide to tune, update the block
with the new values AND a rationale paragraph; preserve the prior values in `decision_log.md` so the trail of how you
arrived at your current tuning is reviewable. Update `set_at` on every change.

When you READ values from this block during a tick (cadence-floor checks, polish-tier-advance threshold, default-depth
selection), prefer the `chosen_levers` value over the CLAUDE.md default. The CLAUDE.md defaults are the cold-boot
fallback; `chosen_levers` is your active tuning.

#### 2g. Autotune check (stand-down ratio gate)

Every tick, before walking the priority policy, check whether your candidate pool is too thin and autonomously tighten
one lever if so. This is the active form of CLAUDE.md → Throughput targets → "Stand-down ratio" rule.

**Compute stand-down ratio over last 4h:**

```sh
NOW=$(date -u +%s)
FOUR_H_AGO=$(($NOW - 14400))

# Count tick entries in the last 4h
T=$(awk -v cutoff="$FOUR_H_AGO" '
  /^## [0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}.*Z/ {
    # Parse timestamp from header line
    gsub(/^## /, ""); split($0, a, " "); ts = a[1];
    cmd = "date -u -d \"" ts "\" +%s 2>/dev/null"; cmd | getline epoch; close(cmd)
    if (epoch >= cutoff) count++
  }
  END { print count+0 }
' /workspaces/witwave-self/memory/agents/zora/decision_log.md)

# Count stand-down ticks specifically (lines mentioning "STAND DOWN" within those 4h)
S=$(awk -v cutoff="$FOUR_H_AGO" '
  /^## [0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}.*Z/ {
    gsub(/^## /, ""); split($0, a, " "); ts = a[1];
    cmd = "date -u -d \"" ts "\" +%s 2>/dev/null"; cmd | getline epoch; close(cmd)
    in_window = (epoch >= cutoff)
    next
  }
  in_window && /STAND DOWN|stand[- ]down/ { count++; in_window = 0 }
  END { print count+0 }
' /workspaces/witwave-self/memory/agents/zora/decision_log.md)
```

(The grep-based approach is simpler and works fine in practice: `grep -cE` for tick headers and stand-down markers
within the time-window — use whichever shape is reliable. The math is what matters: `stand_down_ratio = S / T`.)

**Autotune trigger conditions (ALL must be true):**

- `T >= 4` — you have at least 4 ticks of recent history (avoids early-boot false alarms)
- `stand_down_ratio > 0.5` — more than half the recent ticks produced no dispatch
- No P1 fires drove the stand-downs in this window — P1 fires aren't stand-downs; they reflect urgent fire-fighting
  where standing down was correct (e.g., release pipeline pending). Check `decision_log.md` for `[priority-1: ...]`
  markers in the same 4h window; if present, exit autotune (the stand-downs were principled, not pool-thinness).
- `chosen_levers.set_at` is >4h old — bound to at most one autotune fire per 4h window so each tightening has time to
  surface findings before the next tightening fires

**Tightening priority (apply ONE lever per autotune fire, in this order — first match wins):**

1. **If `polish_tier_advance_zero_streak > 1`** → drop by 1.

   - Surfaces deeper-tier findings faster.
   - Safety floor: 1.

2. **Else if any `cadence_floors.*` value is above its safety floor** → cut the LARGEST-floor lever by 25% (or to the
   nearest 0.5h, whichever is more aggressive), but not below its safety floor.

   - Largest-floor first because it has the most room to give and currently emits the fewest dispatch windows.
   - Safety floors per lever: evan_bug 1, evan_risk 2, nova 2, kira_docs 2, finn 2, kira_research 0.5.

3. **Else if `concurrency_max_per_tick < 2`** → increment to 2.

   - Parallel dispatches double per-tick throughput when scopes don't entangle.
   - Ceiling: 2 in v1.

4. **Else** — every lever is at its safety floor. Surface `[escalation: at-polish-floor-stand-down-high]` to
   `escalations.md` so the user decides whether to relax bounds or accept the floor. Do NOT push past safety bounds
   autonomously.

**Action when a lever is tightened:**

- Update the `chosen_levers` block in `team_state.md` with the new value
- Set `chosen_levers.set_at` to now (RFC3339 UTC)
- Append paragraph to `chosen_levers.rationale`:

  ```text
  [autotune] <timestamp>: stand_down_ratio=<S/T> over last 4h, no P1 fires. Tightened  <lever-name>: <old-value> → <new-value> (rule <N>: <one-line>). Next autotune eligible  at <set_at + 4h>.
  ```

- Append entry to `decision_log.md` with `[autotune: tightened-<lever>]` marker
- Proceed to Step 3 priority walk with the new values active for this tick

**Action when at floors:**

- Do NOT modify `chosen_levers`
- Append to `escalations.md`:

  ```text
  [escalation: at-polish-floor-stand-down-high] <timestamp>: stand_down_ratio=<S/T> with all  levers at safety floors. Codebase may be at genuine polish floor for current quality bar.  User decides: relax bounds (e.g., concurrency 3, evan_bug 0.5h) or accept floor.
  ```

- Log to `decision_log.md` with `[autotune: at-floor]` marker
- Proceed to Step 3 priority walk

**When autotune does NOT fire (the common path):**

- `T < 4` (boot-warmup) → skip silently, no log entry
- `stand_down_ratio ≤ 0.5` → pool is healthy, skip silently
- P1 fires explain the stand-downs → log `[autotune: skipped-p1-fires]` and skip
- `set_at` <4h old → log `[autotune: cooldown-active]` and skip

Then proceed to Step 3 normally.

### 3. Apply priority policy

Walk these in order. The first match wins; act and exit (after logging).

#### Priority 1 — Urgent

- **Stuck-commits in iris's `stuck_commits.md`** (any `[open]` entry) → **DO NOT auto-retry iris.** Mirror to
  `escalations.md`, pause the blocked caller-peer's cadence dispatches, log the deferral in `decision_log.md`, and
  continue the priority walk (stuck commits don't preempt CI fires or cadence on unaffected peers). See full triage flow
  under "Stuck-commits triage flow" in Step 2c. Treat this BEFORE the cadence-floor walk so the affected peer is
  excluded from the candidate pool naturally.

- **Critical CVE in evan's deferred-findings** → dispatch `evan risk-work` with explicit instruction to fix that
  candidate now (preempt other risk-work work).
- **Red CI on any commit since latest tag** → **dispatch evan to fix it**, regardless of who authored the breaking
  commit. Author-agnostic: a binary built from a failing commit is broken whether the author was a peer or a human;
  treating "human authored = wait for human" is what froze the team for ~1h45m on the 2026-05-07 ww-CLI gofmt incident.
  Procedure:

  1. Fetch the failing job's logs:

     ```sh
     gh run view <run-id> --log-failed
     ```

  2. Extract the failing-step name + a tight context window (~30 lines around the first FAIL).
  3. `call-peer evan` with prompt:

     ```text
     Run bug-work on <failing-workflow> failure on commit <sha[0:8]>. Failing step: <step>. Context: <log excerpt>. Goal: produce a fix commit that turns this workflow green. Use your existing fix-bar; if the fix is out of scope (config / infra / not bug-class), flag and report back.
     ```

     Mark this dispatch with `[priority-1: red-ci-recovery]` so it doesn't share the cadence-driven dispatch budget.

  4. **If evan's attempt hangs (stuck-peer) or returns unable-to-fix, fall back to the commit author BEFORE
     escalating.** Most formatter/lint reds (ruff, prettier) are introduced by — and trivially fixable by — the peer who
     ran the formatter, so a single hung evan must not strand the red. Identify the author:
     `git -C <checkout> log -1 --format=%an <breaking-sha>` → map the git author to the peer (`nova-agent-witwave`→nova,
     `evan-agent-witwave`→evan, `finn-agent-witwave`→finn, etc.). If the author is a dispatchable peer who is NOT the
     one that just hung, `call-peer <author>` with the same red-ci-recovery prompt, marked
     `[priority-1: red-ci-recovery-author-fallback]`. Only after BOTH evan AND the commit author fail (or hang) →
     escalate hard per the time-bounded ladder below; do NOT keep retrying the same peer indefinitely. A single hung
     evan with no second-peer fallback stranded the team ~140h on 2026-06-11.

- **Failed release workflow** (iris's release skill returned `[release-workflow-failed]`, OR a `Release*` workflow on
  the latest tag concluded `failure` / `cancelled` / `timed_out`) → **stop cadence-driven dispatching and redirect the
  team to recover.** A pushed tag is just the start of the release; the three workflows that fire post-tag publish the
  actual artifacts users pull. One failed = partial release = silent breakage downstream. Procedure:

  1. **Freeze regular dispatching** for this tick and subsequent ticks until recovery. Peer cadence floors keep counting
     (they'll fire on the recovery tick), but don't emit the dispatches now — every commit a peer produces during a
     partial-release window risks tangling the recovery.
  2. **Surface immediately**. Append `[escalation: release-workflow-failed]` to
     `/workspaces/witwave-self/memory/escalations.md` with the tag, failing workflow name, run URL, and recovery path.
     User sees this without trawling decision_log.
  3. **Diagnose the failure mode** from iris's reply (or from `gh run view <run-id> --log-failed` if the reply lacks
     detail):

     - **Transient infrastructure** (registry timeout, network blip, runner OOM, GitHub Actions outage) →
       `call-peer iris` with `gh run rerun --failed <run-id>`. If the re-run succeeds, surface
       `[release-workflow-recovered]` in `escalations.md` and resume normal cadence next tick.
     - **Real bug in the workflow's source target** (ww CLI build failed because a code regression got past
       `CI — ww CLI`; Helm chart push failed because chart YAML is malformed; container build failed because a
       Dockerfile regressed) → `call-peer evan` with the failing-job log + breaking commit, same shape as red-CI
       dispatch. After evan lands a fix, two paths:
       - If the workflow can re-target the same tag (re-run picks up the fixed source) → ask iris to re-run.
       - If the workflow's artifact for that tag is permanently published in a broken state → ask iris to cut `vX.Y.Z+1`
         with the fix once ANY commit lands. Tag is poisoned; ship a clean follow-up.

  4. **Two failed iris re-run attempts** OR evan can't fix → escalate hard. Append `[needs-human]` to `escalations.md`
     with the failure log + recovery options for human decision. Enter pause-mode.
  5. **Don't fire any new release-warranted dispatches** until this one is recovered. Otherwise the team layers broken
     release on broken release.

- **Pending release workflow** (iris's release skill returned `[release-workflow-pending]` because her watch step hit
  its 30-min-per-workflow timeout while one or more `Release*` workflows were still running, OR a `Release*` workflow on
  the latest tag is currently `in_progress` / `queued` / `requested` / `waiting`) → **HOLD cadence dispatches but don't
  escalate.** Pending is not failure — the workflow is still doing real work; concurrent cadence-driven commits during
  this window can tangle the in-flight release artifacts (a dashboard image build racing with a code-cleanup commit on
  dashboard source, a Helm chart re-render racing with a chart-touching commit, etc.). Procedure:

  1. **Stand down on cadence-mandated dispatches this tick.** Skip the P2 cadence-floor walk entirely; the breaches
     accumulate but don't fire while the release is in flight. Note in `decision_log.md`:
     `cadence dispatches DEFERRED — release pipeline pending: <run-ids still in-progress>, oldest started HH:MMZ`.
  2. **Continue P1 work** — red-CI recovery, stuck-commits, critical CVEs, peer-offline-extended escalations. Those are
     higher priority than the pending release; the team can fix red CI even with a release in flight (and arguably MUST
     — a red CI mid-release means the next tag is poisoned too).
  3. **Re-check on the next tick.** Use the same gh run list query that detected the pending state initially:

     ```sh
     gh run list --branch main --workflow="Release*" --limit 10 --json status,conclusion,name,databaseId
     ```

     If all release workflows have concluded (any combination of success/failure) — flow into either the "fully
     successful" path (resume cadence) or the `[release-workflow-failed]` path above. If still pending — repeat the hold
     for one more tick.

  4. **Pending-too-long escalation.** If a release workflow has been pending >45 minutes (3 consecutive ticks of
     `[release-workflow-pending]`), append `[escalation: release-workflow-stuck]` to `escalations.md` with the workflow
     name + run-id + initial-fire timestamp. Most release pipelines complete in ≤30min; >45min usually means GitHub
     Actions queueing issue or a hung step worth a human eye. Keep cadence held until either the workflow concludes or
     the user intervenes.

- **Stuck peer** (peer dispatch in flight >1h, OR peer's pod has dirty WIP blocking subsequent dispatches) → follow the
  **time-bounded escalation** ladder below. Don't just stand down forever — past versions of this policy held the team
  idle for 3+ hours waiting for human resolution while every cadence floor breached and zero fix attempts ran. Today's
  policy attempts auto-recovery before paging the user.

  - **T+0** — file `[escalation: stuck-peer]` in `decision_log.md` AND in
    `/workspaces/witwave-self/memory/escalations.md` (team-visible surface). Stop dispatching the stuck peer.
  - **T+30m** — dispatch iris with `git-investigate-and-restore` (or her general git-plumbing surface): "peer <name>'s
    pod tree has dirty WIP `<file-list>`; investigate the diff, decide commit-or-discard based on whether the change
    looks complete and safe, log decision in your memory, restore the tree to clean either way." Iris is the team's git
    plumber — she's the right surface for "investigate, decide, unblock."
  - **T+1h** — if still stuck after iris's recovery attempt: **harder escalation** to user with explicit recovery
    commands. Append a hardened entry to `escalations.md` with `[NEEDS-HUMAN]` prefix that includes:

    - The stuck-peer identity, the task scope, and the diagnostic snapshot (probe state, pod generation, memory mtime,
      reply-file size, last claude session ID)
    - **The exact commands** the user runs to perform each of the three recovery paths. Verbatim, copy-pasteable.
      Format:

      ```text
      Path (a) — INSPECT FIRST (safest):
        kubectl logs -n witwave-self <peer-pod> -c claude --since=3h

      Path (b) — KILL + RE-DISPATCH:
        kubectl rollout restart deployment/<peer> -n witwave-self
        # then either:
        ww send zora -n witwave-self "recover <peer>"
        # or wait for next zora tick — she'll auto-detect the pod-generation increment

      Path (c) — MANUAL BYPASS (release-task only):
        git tag -a v<next> -m "<msg>" <commit-sha>
        git push origin v<next>
      ```

    The explicit commands eliminate the per-incident "how do I do this?" round-trip. The user should see this on their
    next `ww escalations` and be able to execute one of the three paths without further context-gathering.

    **Loud escalation (added 2026-06-17 after a 6-day silent stall).** A passive `escalations.md` entry went unseen for
    6 days on 2026-06-11 — Piper even posted a Discussions announcement, but that channel didn't reach the user. So in
    ADDITION to the file entry, **dispatch iris to file a GitHub issue** so the stall lands in the user's normal
    notification flow. `call-peer iris` and have her run (she owns gh writes; you stay read-only):

    ```sh
    gh label create team-stalled-needs-human --color B60205 --force
    gh issue create --repo witwave-ai/witwave \
      --title 'Team stalled — needs human: <one-line summary>' \
      --label team-stalled-needs-human \
      --body '<the [NEEDS-HUMAN] detail + the verbatim three recovery commands>'
    ```

    Record the returned issue URL in the `escalations.md` entry as `[issue-filed: <url>]` and do NOT re-file while it is
    present; if the escalation is still open 24h later, ask iris to add a comment bumping the issue so it resurfaces.

  - **T+2h** — **scoped stuck-peer exclusion, NOT a global pause.** Mark the stuck peer `[stuck-excluded]` in
    `team_state.md` and skip ONLY that peer in the priority walk (exactly like a confirmed-OFFLINE peer) + hold
    release-warranted; KEEP dispatching every OTHER peer on their normal cadence — their work is unrelated to the stuck
    peer, and red CI still blocks release via iris's pre-flight so no broken artifacts ship. Emit
    `[escalation: stuck-peer-excluded]`. Do NOT touch the global `pause_mode.flag`.
  - **The global `pause_mode.flag` is reserved** for (a) the user's explicit "zora pause" killswitch (Step 1), or (b)
    the genuine team-wide-stuck case: a red CI on `main` that neither evan NOR the commit author could clear (red CI
    blocks all productive work — see Red CI step 4), OR ≥2 peers simultaneously `[stuck-excluded]`. A single stuck peer
    on a green `main` must NEVER freeze the whole team — that auto-pause idled the entire team ~140h on 2026-06-11 while
    iris/nova/kira/finn had unrelated work they could have shipped.
  - **Post-recovery (user signals via `recover <peer>` directive — see CLAUDE.md → "Recovery directives"):** clear the
    pause flag if set, verify the named peer's pod is healthy via A2A probe (pod-generation increment is the canonical
    "kill step completed" signal if you can read it), and fire a fresh dispatch to that peer ON THE SAME TICK with the
    task preserved in the escalations.md entry. Log the re-dispatch with
    `[recovery-redispatch: <peer> per user directive]`. Mark the prior `[NEEDS-HUMAN]` escalation as
    `[RESOLVED-PENDING]`; promote to `CLOSED` when the re-dispatched peer returns success.

  Cadence floors continue counting throughout — when the escalation resolves, breached cadences fire immediately on the
  recovery tick (after the recovery re-dispatch, if one was triggered).

#### Priority 2 — Cadence floor breached (peer dispatch)

For each peer, compute `time-since-last-fire`. If it exceeds the floor in CLAUDE.md → "Priority policy" → dispatch that
peer with a routine task in their domain. Floors:

- evan `bug-work` — 3h (tightened from 6h on 2026-05-07; bug-class drainage drives release velocity)
- evan `risk-work` — 8h (all five risk categories: security, reliability, performance, observability, maintainability —
  last is flag-only)
- nova `code-cleanup` — 8h (tightened from 12h)
- kira `docs-cleanup` — 6h (tightened from 24h on 2026-05-07; docs drift on every team commit)
- kira `docs-research` — 3d (tightened from 7d on 2026-05-08; AI/ML space moves fast enough to warrant)
- finn `gap-work` — 6h (gap-fixer; risk-tier ladder gates fix boldness — see polish-tier section below)

If multiple peers have breached, pick the one with the largest current backlog.

**Choosing depth for evan dispatches (polish-tier control).** evan's `bug-work` and `risk-work` accept a `depth`
argument 1-10. The team works UP the polish ladder `5 → 7 → 9`; each tier exhausts the cheap finds for the next.
Cadence-mandated sweeps start at **depth=5** (per evan's own SKILL "After 1-3 has been run" default), not at the parser
default of 3 — depth 1-3 are reserved for ad-hoc cheap-pass triggered by the user or a peer. The CLAUDE.md priority
policy spells out the principle; this section is the mechanics.

Read the current tier from `team_state.md`:

```text
polish_tier_evan_bug:                <int, default 5>
polish_tier_evan_risk:               <int, default 5>
polish_tier_evan_bug_zero_streak:    <int, default 0>  # consecutive 0-finding runs at current tier
polish_tier_evan_risk_zero_streak:   <int, default 0>
polish_tier_evan_bug_last_run_sha:   <sha, default latest tag at first run>
polish_tier_evan_risk_last_run_sha:  <sha, default latest tag at first run>
```

Decide the tier for THIS dispatch:

1. **Reset check.** Look at `git log <last_run_sha>..HEAD`. If any commits landed in evan's section scope (`harness/`,
   `backends/`, `tools/`, `shared/`, `operator/`, `clients/ww/`, `helpers/`, `scripts/`, `.github/workflows/`) — set
   tier back to **5** and zero the streak. Fresh source has new candidates worth a fresh function-level reasoning sweep.
2. **Advance check.** If no fresh source AND `zero_streak ≥ 2` at the current tier — advance the tier to the next rung
   on the ladder (`5 → 7 → 9`; cap at 9) and zero the streak. The advance encodes "we've exhausted this tier; go
   deeper."
3. **Hold check.** Otherwise keep the tier as-is.

Pass it to evan in the call-peer prompt: `Run your bug-work skill at depth=<tier>, sections=all-day-one`. Same shape for
risk-work (`sections=all-deps` is the default scope for risk-work).

After the dispatch, when evan reports back, update `team_state.md`:

- If evan's run returned 0/0/0 (0 candidates / 0 fixed / 0 flagged) — increment `zero_streak`. Update `last_run_sha` to
  current HEAD either way.
- If evan returned anything substantive (≥1 candidate, fixed or flagged) — zero the streak. Update `last_run_sha` to
  current HEAD.

Log the tier choice + reason in `decision_log.md` on each dispatch:

```text
- evan bug-work dispatched at depth=7 (advanced from 5 — last 2 runs 0/0/0 at depth=5, no fresh source since).
- evan risk-work dispatched at depth=5 (reset from 7 — fresh commits in operator/ since last run).
```

This is how the team becomes _actually_ bug-free / risk-free rather than "0 found at the cheap depth." Treat each tier
as its own ground to cover; only depth=9 across all-day-one with adversarial passes counts as "we've looked hard."

**Substitute the literal integer in evan's call-peer prompt** (e.g.
`Run your bug-work skill at depth=5, sections=all-day-one`). Same paranoid guard as the finn dispatches: if your
dispatch prompt contains the literal string `depth=<N>` or `depth=<n>` or `depth=` followed by a non-digit, abort the
dispatch and log the error in `decision_log.md`. An unsubstituted placeholder makes evan fall back to his SKILL parser
default — which is now `5` for risk-work (matches the cadence floor) and `3` for bug-work (the cheap-pass anchor);
either way, sending the literal placeholder undermines the polish-tier ladder you just decided.

**Choosing risk-tier for finn dispatches (polish-tier control).** Same shape as evan's depth ladder but the integer
denotes **risk tolerance** for a fill, not analysis intensity. Tier 1 is purely cosmetic / orphan removal (zero behavior
change); tier 9-10 is architectural cross-cutting work. The team works UP the ladder `3 → 5 → 7 → 9` as each tier's gap
pool exhausts. **Cautious-by-default — the autonomous safety story for finn rests on this gate.** Bigger fills happen
later, after low-tier territory is verified clean.

**Floor: tier=3, not tier=1.** The ladder used to reset to 1 on every fresh commit. With main receiving commits
constantly, that pinned finn at tier=1 (purely cosmetic) forever — every advance was preempted by another reset. Tier 3
is the safer floor: "bounded scope, sibling-pattern available" (add a missing test mirroring an existing test; add
`defer resp.Body.Close()`; fill an explicit TODO with the sibling validator's pattern). Tier 1-2 work is still reachable
— tier-3 fills include those candidates because the gauntlet + fix-bar at tier=3 trivially clears anything tier 1-2
would.

Read the current tier from `team_state.md`:

```text
polish_tier_finn_gap:               <int, default 3>
polish_tier_finn_gap_zero_streak:   <int, default 0>
polish_tier_finn_gap_last_run_sha:  <sha, default latest tag at first run>
```

Decide the tier for THIS dispatch:

1. **Reset check.** If `git log <last_run_sha>..HEAD` returns any commits in finn's gap-source scope (which is wider
   than evan's section scope — finn reads docs, charts, dashboard, AND code; effectively any commit on main is a
   potential reset trigger) — set tier back to **3** (the floor) and zero the streak. Fresh content surfaces new
   low-risk gaps; restart from the bounded-scope pass, not from the cosmetic-only pass.
2. **Advance check.** If no fresh source AND `zero_streak ≥ 2` at the current tier — advance the tier along the ladder
   (`3 → 5 → 7 → 9`; cap at 9) and zero the streak. The advance encodes "we've exhausted this risk tier; raise the
   boldness floor."
3. **Hold check.** Otherwise keep the tier as-is.

Pass it to finn in the call-peer prompt with the literal integer substituted (NOT the placeholder `<N>`):
`Run your gap-work skill at tier=3, sections=all-day-one` (or `tier=5`, etc.). **If your dispatch prompt contains the
literal string `tier=<N>` or `tier=<n>` or `tier=` followed by a non-digit, abort the dispatch and log the error in
`decision_log.md` — sending an unsubstituted placeholder makes finn fall back to her own default-1 path, which silently
undermines the polish-tier ladder.** Optionally include `focus=operator-parity` etc. — see finn's CLAUDE.md priority
subsystems.

After the dispatch, when finn reports back, update `team_state.md`:

- If finn's run returned 0/0/0 (0 candidates at-or-below tier / 0 filled / 0 newly-flagged) — increment `zero_streak`.
  Update `last_run_sha` to current HEAD.
- If finn returned anything substantive (≥1 fill OR ≥1 new flag) — zero the streak. Update `last_run_sha`.

Log the tier choice + reason in `decision_log.md` on each dispatch:

```text
- finn gap-work dispatched at tier=3 (advanced from 1 — last 2 runs 0/0/0 at tier=1, no fresh source since).
- finn gap-work dispatched at tier=1 (reset from 5 — fresh commits in operator/ since last run; cheap-pass
  first to catch any new low-risk gaps before climbing back).
```

**Critical-gap urgency.** finn's findings file may contain `[CRITICAL]` markers — typically a doc-promised feature that
doesn't exist (users following the docs will hit a missing endpoint / unread env var / broken command). Treat these the
same as evan's `[CRITICAL]` CVEs: they block the medium-quality-bar release gate until flipped to `[fixed: SHA]`. The
recovery path for a critical gap is a fill, not a flag — dispatch finn with an explicit higher tier if needed.

**Choosing the skill for nova / kira dispatches (polish-tier control).** Same advance/reset mechanism as evan, but
instead of a depth integer the "tier" is a skill name. Cheap-pass = the default cleanup skill; deep-pass = the heavier
authoring/research skill.

Read from `team_state.md`:

```text
polish_skill_nova:                <"code-cleanup" | "code-document", default "code-cleanup">
polish_skill_kira:                <"docs-cleanup" | "docs-research", default "docs-cleanup">
polish_skill_nova_zero_streak:    <int, default 0>
polish_skill_kira_zero_streak:    <int, default 0>
polish_skill_nova_last_run_sha:   <sha, default latest tag at first run>
polish_skill_kira_last_run_sha:   <sha, default latest tag at first run>
```

Section scope for the fresh-source check:

- nova: same as evan (source code paths — `harness/`, `backends/`, `tools/`, `shared/`, `operator/`, `clients/ww/`,
  `helpers/`, `scripts/`, `.github/workflows/`, plus `clients/dashboard/src/`).
- kira: docs surface — any `*.md` (root, per-subproject, `docs/**`, `.agents/**`), `AGENTS.md`, `CLAUDE.md`,
  `CHANGELOG.md`, `README.md`. (When kira herself commits docs that land here, the next-dispatch reset is fine — fresh
  docs may have new drift to find.)

Decide the skill for THIS dispatch:

1. **Reset check.** If `git log <last_run_sha>..HEAD -- <scope>` returns any commits, set the skill back to the
   cheap-pass default and zero the streak.
2. **Advance check.** If no fresh source AND `zero_streak ≥ 2` at the cheap-pass — flip to the deep-pass skill for THIS
   dispatch (then auto-flip back to cheap-pass next time, since the deep-pass is one-shot, not steady-state).
3. **Hold check.** Otherwise keep the skill as-is.

Pass it to the peer in the call-peer prompt: `Run your <skill_name> skill` (no depth arg — nova/kira don't accept one).

After the dispatch, when the peer reports back, update `team_state.md`:

- 0 commits / 0 findings → increment `zero_streak`. Update `last_run_sha`.
- ≥1 commit OR ≥1 finding → zero the streak. Update `last_run_sha`.
- If THIS dispatch was a deep-pass (advance fired), zero the streak regardless and flip back to cheap-pass for next
  time. (The deep-pass only fires on advance; it never holds as steady state.)

Log in `decision_log.md`:

```text
- nova code-cleanup dispatched (held — last 2 runs found things, no streak).
- nova code-document dispatched (advanced — code-cleanup returned 0/0/0 twice on stable source; one-shot deeper pass).
- kira docs-research dispatched (advanced — docs-cleanup returned 0/0/0 twice; one-shot research refresh).
```

#### Priority 3 — Cadence floor breached (team-tidy, your own work)

If no priority 1 or priority 2 firing this tick, AND your `team-tidy` cadence floor (6h) has breached, invoke your own
`team-tidy` skill in-process. This is YOUR work — not a call-peer dispatch. The skill reads all identity files, finds
one consistency or small-improvement opportunity (per the strict bar in `team-tidy/SKILL.md`), applies it, commits,
delegates the push to iris, watches CI.

Compute floor:

```sh
LAST_TIDY=$(grep -oE "^## [0-9-]+ [0-9:]+ UTC — team-tidy" /workspaces/witwave-self/memory/agents/zora/decision_log.md | tail -1)
```

Parse the timestamp; if >6h ago (or never), invoke the skill.

Counts toward the team-tidy daily cap (3/day), not the peer-dispatch hourly cap.

#### Priority 4 — Backlog-weighted (peer dispatch)

Within cadence (no floor breached), pick the peer with the largest open backlog (count of `[flagged: ...]` items in
their deferred-findings memory). Dispatch them on the appropriate skill for their domain.

#### Priority 5 — Release-warranted check (velocity-driven)

This runs **independent** of priorities 1-4 — every tick.

**Step 1: compute weighted commits since latest tag.** For each commit in `git log v<latest>..main`:

| Commit prefix                               | Weight |
| ------------------------------------------- | ------ |
| `feat:` / `feat(<scope>):`                  | 2.0    |
| `fix:` / `fix(<scope>):`                    | 1.0    |
| `docs:` / `docs(<scope>):`                  | 0.5    |
| `chore:` / `style:` / `refactor:` / `test:` | 0.25   |
| Anything else (no conventional prefix)      | 0.5    |

**Exclude these from the weighted sum** (release-artifact commits — counting them re-triggers a release for releasing):

- `docs(changelog):` commits.
- Any commit whose message body indicates it was authored by iris during a release cut.

**Step 2: detect critical-fix fast-path.** Scan `git log v<latest>..main` for any commit matching `fix(security):` OR a
body containing the literal word `critical`. If found, set `critical_fix_present = true`.

**Step 3: gate.**

```text
IF (weighted_commits ≥ 3.0 OR critical_fix_present)
AND no CI red on main HEAD
AND no in-flight release pipeline (check gh for running "Release" / "Release — ww CLI" / "Release — Helm charts")
AND no in-flight batch-revert (check git log for recent "Revert evan bug-work batch")
AND ≥ 15 minutes since last release  ← hygiene floor only; not a cadence knob
AND no critical findings open in any peer's deferred-findings (the medium quality bar)
THEN dispatch iris with the release skill
```

Bump kind based on conventional-commit inference of `git log v<latest>..main`:

- Any `BREAKING CHANGE:` / `!:` → major
- Any `feat:` → minor
- Otherwise (only `fix:`, `chore:`, etc.) → patch

**Why velocity-driven.** The previous policy (≥1h floor + max 4 releases/day) double-locked itself the night of
2026-05-06 → 05-07: 4 productive releases burst-shipped in ~6h, then the team stood down for the next 14h with the
release surface frozen. Velocity-driven cadence lets bursty mornings ship 6 releases when there's real content and quiet
stretches batch over hours, without arbitrary daily cliffs.

#### Priority 6 — Stand down

Nothing in any priority bucket fires → log "no action this tick" to decision log, exit cleanly.

### 4. Apply hard caps before dispatching

Before any dispatch in steps 3.1-3.4:

- **Max 8 dispatches/hour:** count entries in `decision_log.md` with timestamp within the last hour. If ≥8, abort the
  dispatch, log `[capped: dispatches/hour]`, exit. (Raised from 5 on 2026-05-07; 5/hr was binding under the tightened
  cadence floors when iris-cleanup chains stacked alongside peer dispatches.)
- **Max 20 releases/day (runaway guard, not cadence policy):** count `[release-dispatched]` entries in `decision_log.md`
  in the last 24h. If ≥20, this is a runaway loop — log `[capped: releases/day]`, enter pause-mode, and escalate to the
  user via `[escalation: release-storm]`. Velocity-driven release-warranted is the everyday knob; this exists only to
  halt a malfunction.
- **Max 3 batch-reverts/day:** count `[revert-detected]` entries. If ≥3, this is systemic — escalate to user via
  `[escalation: revert-storm]` and enter pause-mode automatically.
- **Cycle detection:** for the candidate you're about to dispatch a fix for, check whether the same `[file:line]` has
  appeared in 3+ `[flagged: fix-forward-failed]` or `[reverted]` entries in the last 24h. If yes, mark `[frozen]` in
  evan's findings memory (via call-peer "freeze candidate X") and skip.

### 5. Dispatch (if any priority fired)

Use `call-peer` with a focused prompt that:

- Names the skill explicitly (e.g., "Run your `bug-work` skill")
- Includes the depth + sections (for evan/nova/kira)
- Includes a one-line rationale ("Cadence floor breached" / "Critical CVE re-surfaced" / etc.)
- Does NOT block on completion — fire and forget. The peer's response acknowledges receipt; the actual run state
  surfaces in their memory next tick.

Example dispatch prompt template (substitute `<peer>` and `<rationale>`):

> Hi <peer> — zora here. Dispatching <skill> per <rationale>. <skill-specific args>. Run your usual procedure; commit +
> iris-delegate as designed. I'll see your result in your memory next tick.

### 6. Log decision rationale

Append to `/workspaces/witwave-self/memory/agents/zora/decision_log.md`:

```markdown
## YYYY-MM-DD HH:MM UTC — tick

**State snapshot:**

- Latest tag: `v<X.Y.Z>`. Commits since tag: N.
- CI on main HEAD: <green/red/in-flight>.
- Peer health: iris=<ok|silent>, nova=<...>, kira=<...>, evan=<...>.
- Backlogs: iris=<n>, nova=<n>, kira=<n>, evan=<n>.

**Decision:** <dispatch <peer> <skill> | release-dispatched | stand-down | escalation | capped>

**Rationale:** <one-line reason from the priority policy>

**If dispatched:** prompt sent to <peer> at <time>; awaiting next-tick state read.
```

### 7. Update team state

Update `/workspaces/witwave-self/memory/agents/zora/team_state.md` with:

- New last-fire timestamp for the dispatched peer (if any)
- **Per-peer liveness state from Step 2d's active probe** — one of `ONLINE`, `PROBE-FAILED-ONCE`, `OFFLINE`. Always
  write the current probe result, even when unchanged from last tick (so the timestamp in the file reflects when the
  state was last verified, not just when it last changed).
- Updated backlog counts
- **`chosen_levers:` block** — only write this when you've decided to tune a throughput knob. See Step 2f for the schema
  and CLAUDE.md → "Throughput targets" for the lever menu. Writing this block is what makes your tuning decision survive
  pod restarts. When you write a new value, also log the prior value + rationale in `decision_log.md` under a
  `## YYYY-MM-DDTHH:MMZ — chosen_levers update` heading, so the audit trail of how the team's tuning evolved is
  reviewable.

Append to `peer_heartbeat_log.md` (diagnostic ledger) one line per peer per tick with the probe outcome and any state
transition (e.g., `2026-05-08T22:00Z finn ONLINE → ONLINE (probe-ok in 47ms)` or
`2026-05-08T22:00Z finn PROBE-FAILED-ONCE → OFFLINE (probe-fail #2 in a row, 5s timeout)`). Trim entries older than 7
days during this step to keep the file bounded.

**Keep your working files under the read buffer (mandatory cap).** Unbounded append-growth crashed ticks on 2026-06-01
(`decision_log.md` reached 4.85 MiB, `team_state.md` 1.43 MiB → `-32603` buffer overflow on read, losing the dispatch).
After writing this tick's updates, cap them:

- `team_state.md` — retain only the **3 most-recent `## … snapshot` blocks**; move older blocks to
  `team_state.archive-<UTC>.md`.
- `decision_log.md` — when it exceeds ~300 KB, move the bulk to `decision_log.archive-<UTC>.md` and keep the recent tail
  (`tail -n 1500`).

Archives preserve the full audit trail; the live files stay small enough that even a whole-file read won't trip the
buffer. (A one-time cleanup already archived the pre-existing oversized files on 2026-06-01.)

### 8. Exit cleanly

Return a one-paragraph summary to whoever invoked you (typically the heartbeat scheduler, but the user could invoke this
skill manually for a one-off pass). Format:

> Tick at HH:MM UTC. State: <one-line>. Decision: <one-line>. Next tick at HH:MM UTC.

## Out of scope for this skill

- **Authoring code or doc changes.** Dispatch the appropriate peer.
- **Direct git operations.** Iris owns push; you don't commit code.
- **Direct gh API calls.** Iris owns GitHub authority. You read CI state via `gh run list` (read-only on public repo)
  but do not invoke any mutating gh command.
- **Inventing new priorities or cadence floors on your own.** The policy is what's in CLAUDE.md → "Priority policy". If
  a new pattern emerges, surface it to the user via `[escalation: policy-question]` and let the user edit CLAUDE.md
  before you act.
