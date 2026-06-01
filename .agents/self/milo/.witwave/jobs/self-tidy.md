---
name: self-tidy
description:
  Daily per-agent self-maintenance. Runs the byte-identical `self-tidy` skill — grooms own memory namespace, refreshes
  peer-awareness reference memos, checks own agent-card for drift. Staggered across the team to avoid simultaneous
  fires.
schedule: "0 13 * * *"
enabled: true
---

Run your `self-tidy` skill. This is your daily self-maintenance pass:

1. Verify checkout + pin git identity.
2. Memory consolidation (your own namespace only).
3. Cross-agent awareness — refresh `reference_peer_<name>.md` memos with current peer state.
4. Public-presentation drift check — verify your agent-card.md matches your current skill surface.
5. Apply changes (atomic, ≤50 lines, single commit) — or commit nothing if no drift.
6. Delegate push to iris.
7. Watch CI; fix-forward then revert on red.
8. Log the run to `self_tidy_log.md`.

Cadence is one fire per 24h, staggered across the team. Boundary: you only edit your own namespace + your own
agent-card. Cross-cutting changes are zora's team-tidy lane.
