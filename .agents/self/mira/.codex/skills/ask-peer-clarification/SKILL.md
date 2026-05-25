---
name: ask-peer-clarification
description:
  Ask a peer agent ONE focused read-only question to enrich an already-detected platform anomaly before handing off to
  Zora. Wraps `call-peer` with strict framing — you're asking for the peer's subjective context about their own state,
  not delegating work. Returns the peer's reply for inclusion in the Zora handoff under `peer_followup.responses`.
  Trigger from `platform-health` Step 8 only — never as routine polling.
version: 0.1.0
---

# ask-peer-clarification

A focused wrapper around `call-peer` for the specific case where Mira has detected a platform anomaly that points at
one specific peer AND the external evidence alone doesn't fully explain the cause. The peer's subjective context about
their own state often clarifies whether the external signal is a real fault or a benign artifact, and that enriches
the Zora handoff.

The distinction from `call-peer` matters: every other peer's `call-peer` use is for delegating WORK ("Iris, please
push these commits"). Mira's use is purely for INFORMATION ("Evan, your pod restarted at 14:32Z with OOMKilled — are
you aware of any heavy operation around that time?"). The framing tells the recipient peer to answer briefly with the
fact, not to launch a skill or take an action.

## Inputs

- **`peer`** — the agent the anomaly points at: `iris` / `kira` / `nova` / `evan` / `finn` / `felix` / `piper` / `zora`.
  Required. Use the peer whose pod / activity / memory the external signal references — don't ask Iris about Evan's
  restart.
- **`anomaly`** — one-line summary of the external evidence: what Mira observed, where, when. Required.
- **`question`** — the one specific question. Required. Should be answerable in ≤ 50 words. Read-only framing only
  ("are you aware of X?", "do you have context for Y?") — never "please do Z."

## Instructions

### 1. Verify the read-first checklist before invoking

This skill is only allowed AFTER Mira has gathered the external evidence and decided the handoff to Zora would
materially benefit from the peer's subjective context. Run through the checklist first:

1. The peer's own `MEMORY.md` index + relevant findings file (often answers the question without a ping).
2. Their `decision_log.md` or skill-specific log if they keep one.
3. Recent `git log` entries authored by the peer.
4. Logs from the peer's pod for the relevant time window (`kubectl logs ... --since=10m`).
5. Mira's own snapshot history — has this signal appeared before, and did the peer surface anything about it then?

Only invoke `ask-peer-clarification` if ALL THREE gates pass:

1. **The information is critical** — the Zora handoff is meaningfully better with the peer's context, not just
   nice-to-have detail.
2. **You can't derive it from any read source** — the read-first checklist genuinely came up empty.
3. **The peer is the authoritative source** — they alone know the subjective context (e.g., "did I notice my last 3
   dispatches taking 2× longer than usual?").

If any gate fails, hand off to Zora with the external evidence alone. The targeted question is enrichment, not a
requirement for the handoff.

### 2. Compose the call-peer prompt

Use this template — be explicit that you're requesting INFORMATION only, no action:

```text
Hi <Peer> — Mira here, doing a platform-health investigation. I observed an anomaly that points at your
pod / activity / memory and need one quick clarification before I escalate to Zora. Not asking you to
do anything; just need any subjective context you have.

What I observed: <anomaly>

Question: <question>

Please reply briefly (≤ 50 words). If you don't have any context on this, say so — I'll hand off to
Zora with the external evidence alone.
```

### 3. Dispatch via call-peer

```text
call-peer peer=<peer> prompt=<the composed text above>
```

`call-peer` is synchronous over A2A; you'll get the peer's reply in the response. Wrap in a short timeout
(`--timeout 60s` or similar at the call site) so a slow peer doesn't block your hourly tick beyond the next heartbeat.

### 4. Handle the reply

Three useful shapes:

- **Subjective context that clarifies the anomaly** — fold into the Zora handoff body under
  `Peer follow-up (if any)` with attribution: "Asked Evan: ... / Reply: 'I dispatched a deep risk-work
  sweep at 14:30Z that touched the full source tree; OOMKilled was probably memory pressure from that.'"
  This is the highest-value shape — Zora now has both the external evidence + the peer's inside view.
- **"I don't have context on this"** — proceed with the Zora handoff using external evidence only. Note
  in the handoff that the peer was asked and didn't have additional context (so Zora knows the evidence
  is the full picture).
- **Pushback on framing** — the peer says "your interpretation of X is wrong; the actual situation is
  Y." Update the handoff with the corrected framing. Optionally note in the handoff that the peer
  clarified the framing.

### 5. Log the clarification round-trip

Append to the `peer_followup` section of the current snapshot in
`/workspaces/witwave-self/memory/agents/mira/platform-health/snapshots/YYYY-MM-DD.jsonl`:

```yaml
- questions_sent: [{peer: <name>, anomaly: <one-line>, question: <one-line>}]
- responses: [{peer: <name>, reply: <peer's-reply-summary, ≤ 30 words>, outcome: <inline | reframed | no-context>}]
```

This way a human auditing Mira's handoff history can see what was asked, what came back, and how it shaped the
Zora handoff.

## Use sparingly — read-first is your default

The point of this skill is to AVOID speculating about peer-internal state in handoffs to Zora. But every clarification
round-trip interrupts the peer (who is doing real work) and adds token cost — exactly the cost concern that drove
Zora's cadence-tuning work. **Default mode: don't ask.** Only invoke after the read-first checklist comes up empty AND
all three gates pass.

Most ticks will ask zero peers anything. That's the design — Mira is read-mostly, the team is work-mostly, the channels
are quiet by default.

## Out of scope for this skill

- **Asking peers to do work.** That's `call-peer` directly with a different framing — and it's not Mira's lane anyway.
  Only Zora dispatches work.
- **Multi-question conversations.** One question per anomaly. If you need a second clarification after the first reply,
  hand off to Zora with what you have; she can route a richer follow-up via the right peer.
- **Polling peers for status.** Their MEMORY.md and findings files are the polled surface; `ask-peer-clarification` is
  for live questions about specific detected anomalies. Don't use it as a "how are you doing?" check-in.
- **Asking peers about decisions outside their domain.** Don't ask Iris about why Evan flagged a bug; ask Evan. Don't
  ask Evan about why a release pipeline failed; ask Iris.
- **Routine cross-checks during normal Green ticks.** Skip this skill entirely when the platform is Green — there's
  no anomaly to enrich.
