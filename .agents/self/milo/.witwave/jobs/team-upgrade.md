---
name: team-upgrade
description:
  Advances Milo's self-first staged upgrade campaign by one step per run. Milo is the canary: he upgrades himself first
  and soaks longest, then cascades a proven version to the peers one at a time, lowest blast radius first. Most runs are
  no-ops (the team is already current). The gap between runs is the soak window, so a rollout spans hours to days. The
  only mutation is a ww-CLI image-tag bump. DISABLED by default — flip `enabled: true` once you've watched a run and are
  ready for autonomous rollout.
schedule: "30 */4 * * *"
enabled: false
---

Run your `team-upgrade` skill. Advance the campaign by exactly ONE safe step, in this order — stop at the first that
acts:

1. **Guards.** Stop if `team-upgrade/PAUSED` exists or the target is quarantined.
2. **Target.** A campaign in flight keeps its **pinned** target; otherwise resolve the latest release (`gh`), confirm
   its images published, and start a campaign only if the team has drifted. (Newer releases are adopted between
   campaigns, never mid-cascade.)
3. **Operator check (best-effort).** Try `ww operator status` — your namespace RBAC usually can't read it. If readable
   and the operator is behind the target, escalate to Zora and HOLD; if forbidden, note it once and proceed (your canary
   and rollback are the backstop). You never run `ww operator upgrade` — cluster-admin, human-run.
4. **Self-canary (you first).** If you're behind the target, upgrade yourself
   (`ww agent upgrade milo --tag <target> --yes --no-wait`) and stop — next run verifies you. If you're on target but
   soaking, verify health and hold until the canary soak elapses (default 24h). If unhealthy, self-roll-back +
   quarantine + escalate.
5. **Cascade one peer.** Only once you're proven: upgrade the next peer in risk order (piper → mira → workers → zora →
   iris), verify, record. On failure, roll it back + halt + quarantine + escalate.
6. **Self-evolve.** Tune the soak from `campaign-log.md` history (tighten after clean runs, loosen after a rollback).

Hard limits:

1. `ww` CLI only for mutations — never `kubectl patch` a WitwaveAgent CR.
2. One agent per run. You are always first and never skipped.
3. Stop and report "paused" if the kill-switch file exists; never retry a quarantined version.
4. Image versions only — not the operator, not model/effort config (that's a separate config/git change you recommend
   after proving it on yourself).

Return the team-upgrade status shape (Status / Target / Self / Peers / This run / Quarantine / Next).
