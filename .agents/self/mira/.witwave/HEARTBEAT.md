---
description: >-
  Runs Mira's bounded platform-health heartbeat. The check is read-only, records one compact snapshot, prioritizes
  self-team pod restart/readiness signals, and escalates only clear fix-needed findings. The cadence runs daily so the
  reliability loop stays alive without turning monitoring into a major token sink.
schedule: "17 14 * * *"
enabled: true
---

Run your `platform-health` skill in bounded heartbeat mode.

Goal: finish one lightweight platform observation tick, record a compact JSONL snapshot, and return a concise status. Do
not turn this into a broad investigation unless the snapshot shows a restart delta or a clearly degraded object.

Hard limits:

1. Use at most 10 tool calls unless a restart delta is detected.
2. Prefer `kubectl` over `ww` for cluster reads from inside the pod; `ww operator status` may not have kubeconfig.
3. If a command is forbidden by RBAC, record the access gap once and move on. Do not keep trying equivalent broad-scope
   commands.
4. Inspect logs or `kubectl describe` only for pods/containers with new restart deltas or non-ready status.
5. Do not message Zora unless the finding is clearly fix-needed, repeated, or Red.

Minimum snapshot:

1. Current UTC timestamp.
2. Self-team pod readiness and per-container restart counts.
3. `WitwaveAgent` readiness/status in `witwave-self`.
4. Recent warning events in `witwave-self`.
5. Latest release/CI signal if available quickly; skip if GitHub access is slow or unavailable.
6. Previous snapshot comparison for restart deltas when memory is available.
7. Append one compact JSON object to `platform-health/snapshots/YYYY-MM-DD.jsonl`.

Return exactly this shape:

```text
Status: Green | Yellow | Red
Changed: <one short sentence>
Evidence: <two or three short bullets>
Handoff: none | sent-to-zora | recommended
```

Do not mutate cluster state from the heartbeat. No restarts, patches, upgrades, rollbacks, tag pushes, release reruns,
or source changes unless a human explicitly approves that action in the triggering request.
