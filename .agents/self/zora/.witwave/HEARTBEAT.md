---
description: >-
  Drives zora's continuous decision loop. Each tick invokes the dispatch-team skill, which reads team state, applies the
  priority policy from CLAUDE.md, and dispatches the appropriate peer (or stands down). 60-minute cadence — relaxed
  again on 2026-05-24 (was 30 min). Each dispatch-team tick costs ~188k tokens; the upstream gate on peer dispatch rate
  is this heartbeat, so doubling the interval halves the entire team's dispatch frequency without any change to peer
  skill behavior. Release latency worst case ≤60 min; acceptable trade for ~50% additional ambient cost reduction.
schedule: "0 * * * *"
enabled: true
---

Run your `dispatch-team` skill. This is one tick of your continuous decision loop:

1. Check pause-mode.
2. Read team state (git, peer memories, CI, peer health).
3. Apply the priority policy from CLAUDE.md.
4. Dispatch the appropriate peer (or stand down) per the policy.
5. Check release-warranted independently.
6. Apply hard caps before any dispatch.
7. Log decision rationale to your `decision_log.md`.
8. Update `team_state.md` with new last-fire / health / backlog snapshots.
9. Return a one-paragraph tick summary.

Don't block on peer completion. Their results surface in their memory by the next tick. You see and act on them then.
