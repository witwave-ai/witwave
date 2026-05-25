---
description: >-
  Drives Piper's continuous outreach loop. Each tick invokes the team-pulse skill, which reads team state, scores recent
  events for substantive-ness, and either posts to GitHub Discussions (Announcements or Progress) or stays silent.
  60-minute cadence (started at 5 min, loosened to 15 min on 2026-05-10, further loosened to 30 min on 2026-05-15,
  relaxed again to 60 min on 2026-05-24 to halve ambient cost; matches Zora's 60-min decision loop).
schedule: "0 * * * *"
enabled: true
---

Run your `team-pulse` skill. This is one tick of your continuous outreach loop:

1. Check pause-mode (you have one too — same `pause_mode.flag` shape as Zora).
2. Read team state — git log since last tick, every peer's MEMORY.md, Zora's decision_log.md + escalations.md +
   team_state.md, recent CI runs, recent releases.
3. Score the events since your last post on the substantive-score 0-10 model (defined in `team-pulse`'s SKILL.md) —
   apply the time-since-last-post multiplier so the bar scales with cadence.
4. Route by score: ≥9 → Announcements; 5-8 → Progress (with 30-min cooldown); <5 → silent stand-down.
5. If posting: ask any clarification questions to peers via `ask-peer-clarification` before drafting, then draft prose
   in your "informative + warm" voice (see CLAUDE.md voice section), then publish via `post-discussion`.
6. Log every tick (post or silent) to your own `pulse_log.md` so future ticks can compute time-since-last-post and your
   audit trail is reviewable.
7. Return a one-paragraph tick summary.

Don't block on peer completion when you call-peer for clarification. Their replies surface in your conversation log;
reconcile them on the next tick if the deadline hits.

Most ticks will be silent — that's the design. Only post when you have something humans-on-GitHub genuinely want to
read.
