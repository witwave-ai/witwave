---
description: >-
  Runs Milo's roster-audit heartbeat. Each tick refreshes the team roster directory — who is deployed, who is up vs
  down, what job functions each agent has, and who Milo can reach over A2A — records a compact snapshot, and returns a
  concise status. Read-only; never mutates the cluster. The 60-minute cadence matches the team's self-driving work
  agents (zora, piper); the roster does not churn faster than hourly, so this keeps the directory warm without burning
  tokens.
schedule: "0 * * * *"
enabled: true
max-tokens: 30000
---

Run your `roster-audit` skill in bounded heartbeat mode.

Goal: refresh the team roster directory, record one compact JSONL snapshot, and return a concise status. Do not turn
this into a broad investigation — if an agent is down, record it and (if it looks like a platform fault) note it for
Mira/Zora rather than opening a deep pod triage yourself (that is Mira's `platform-health`).

Hard limits:

1. Read-only. No pod eviction, patch, restart, or any cluster/source mutation from the heartbeat.
2. Use at most 12 tool calls unless an agent changed availability since the last snapshot.
3. Prefer `kubectl get witwaveagents` over `ww` for cluster reads from inside the pod; `ww` may not have kubeconfig.
4. If a command is forbidden by RBAC, record the access gap once and move on — do not retry equivalent broad commands.

Minimum work each tick:

1. Refresh reachable A2A peers (the `discover-peers` mechanism) for reachability + declared skills.
2. Read the deployed roster + readiness (`ww agent list` / `kubectl get witwaveagents`).
3. Reconcile into the roster directory; classify each agent available | degraded | down | planned; flag drift.
4. Overwrite `roster-audit/roster.md`, append one object to `roster-audit/snapshots/YYYY-MM-DD.jsonl`, and append to
   `roster-audit/roster-changes.md` only when something changed.

Return exactly this shape:

```text
Status: ok | roster-drift | agents-down
Roster: N deployed (M available, K degraded/down) · P planned
Available: <names>
Unavailable: <name — reason> | none
Drift: <one line> | none
```
