---
name: platform-health
description:
  Read-only operational health check for the witwave platform. Use when asked to check platform health, investigate pod
  restarts, startup issues, operator health, release status, runtime storage, resource pressure, rollout drift, or
  whether it is safe to upgrade agents/operator. Produces a Green/Yellow/Red summary, distills problematic anomalies for
  Zora to route, and never mutates cluster or GitHub state without explicit human approval.
version: 0.2.0
---

# platform-health

Run a read-only reliability check across the self-team platform surface. The goal is to answer: _is anything about the
platform behaving oddly enough that Zora should route a fix?_ Prefer evidence over speculation.

This skill has two jobs:

1. **Record the current platform snapshot** every time it runs.
2. **Analyze historical snapshots** once there is enough history to distinguish normal Kubernetes churn from systemic
   platform issues.

For the first iteration, the primary signal is pod/container restarts: how often they happen, which container restarted,
and what Kubernetes says about the likely cause. Keep the broader snapshot fields, but do not over-invest in new signal
families until restart tracking is reliable.

## Hard boundaries

Default posture is read-only.

Allowed automatically:

- Inspect `ww`, `kubectl`, `gh`, git state, logs, events, PVCs, releases, tags, and workflow runs.
- Read own memory and peer memory.
- Write a concise report to your own memory namespace.
- Send Zora a distilled anomaly report when a finding looks problematic.

Requires explicit human approval in the triggering request:

- `ww operator upgrade`, `ww agent upgrade`, `ww update`, Helm upgrades, release reruns, or tag pushes.
- `kubectl patch`, `kubectl delete`, `kubectl rollout restart`, scale changes, or PVC mutations.
- Messaging peers other than Zora with operational instructions.
- Telling Zora which mutation to perform instead of handing her the evidence-backed finding.
- Any source-code commit or push.

If a fix requires mutation, stop at observation + Zora handoff unless the user already approved that exact mutation.

## Escalation rule

If you detect a systemic issue, repeated anomaly, or any issue that appears to need a fix, report it to Zora. Do not
leave fix-needed findings only in your private snapshot history. The minimum acceptable output for a fix-needed finding
is:

1. Record the evidence in `anomalies.md` or `escalations-to-zora.md`.
2. Send Zora a distilled handoff unless the user explicitly told you not to message agents.
3. Mark the final report with `Zora handoff: sent` or explain why it is only `recommended`.

## Standard check

Use the namespace from the user request if supplied; otherwise default to `witwave-self` for agents and `witwave-system`
for the operator.

### 0. Snapshot discipline

Create these files/directories when memory is available:

- `/workspaces/witwave-self/memory/agents/mira/platform-health/snapshots/YYYY-MM-DD.jsonl`
- `/workspaces/witwave-self/memory/agents/mira/platform-health/baseline.md`
- `/workspaces/witwave-self/memory/agents/mira/platform-health/anomalies.md`
- `/workspaces/witwave-self/memory/agents/mira/platform-health/escalations-to-zora.md`

Every run should append one compact JSON object to the daily `snapshots/*.jsonl` file. Keep it small and comparative:

```json
{
  "ts": "YYYY-MM-DDTHH:MM:SSZ",
  "status": "Green|Yellow|Red",
  "agent_namespace": "witwave-self",
  "operator_namespace": "witwave-system",
  "agents": {
    "ready": 0,
    "total": 0,
    "not_ready": [],
    "quiet": [],
    "backend_mismatch": []
  },
  "pods": {
    "not_ready": [],
    "waiting_reasons": {},
    "restart_deltas": {},
    "recent_restarts": []
  },
  "operator": {
    "ready": true,
    "version": "",
    "warnings": []
  },
  "storage": {
    "pvc_not_bound": [],
    "mount_warnings": [],
    "runtime_path_warnings": []
  },
  "events": {
    "warnings_by_reason": {},
    "notable": []
  },
  "release": {
    "latest": "",
    "failed_runs": [],
    "update_available": false
  },
  "handoff": "none|sent|recommended"
}
```

Do not store raw secrets or huge command output in snapshots. Store counts, names, short reason strings, and the minimum
error snippets needed to compare state over time.

Restart tracking requirements:

- Capture restart counts per pod/container, not just per pod.
- Compare current restart counts with the previous snapshot when available.
- Record restart deltas even if the pod is currently `Ready`.
- Preserve the last termination reason, exit code, finished time, and short message when Kubernetes exposes them.
- Treat `OOMKilled`, repeated non-zero exit codes, repeated probe failures, `Error`, and restart deltas across multiple
  snapshots as likely fix-needed findings for Zora.
- If a restart occurred, do focused triage before reporting: inspect that pod's `describe`, recent events, and relevant
  container logs rather than broad-cluster logs.

### 1. Tool availability

Check what is available before assuming it works:

```sh
command -v ww || true
command -v kubectl || true
command -v gh || true
```

If a tool is missing, continue with the remaining checks and list the missing capability as a platform gap.

### 2. Operator and CLI alignment

```sh
ww version
ww operator status --namespace witwave-system
ww update --check
```

Look for:

- `ww` version matching the operator app/chart version.
- Operator pod Ready.
- CRDs present.
- Managed CR counts plausible.
- Newer `ww` release available.
- Repeated reconciliation failures or version drift that suggests the operator is not applying desired state.

### 3. Agent readiness and activity

```sh
ww agent list --namespace witwave-self
ww team status --namespace witwave-self --since 1h
```

Look for:

- Any agent not `Ready`.
- Agents with no recent activity when they should have heartbeats.
- Surprising token spikes or conversation gaps.
- Backend counts that differ from expected topology.
- Agents that appear healthy at the CR level but stale or silent in activity status.

### 4. Kubernetes health details

```sh
kubectl get pods,pvc,witwaveagents,witwaveworkspaces --namespace witwave-self
kubectl get pods --namespace witwave-self -o json
kubectl get witwaveagents,witwaveworkspaces --namespace witwave-self -o json
kubectl get pvc --namespace witwave-self -o json
kubectl get events --namespace witwave-self --sort-by=.lastTimestamp | tail -40
kubectl get events --namespace witwave-self -o json
kubectl get pods --namespace witwave-system
kubectl get deploy,pods --namespace witwave-system -o json
kubectl get events --namespace witwave-system --sort-by=.lastTimestamp | tail -40
```

Optional if metrics are available:

```sh
kubectl top pods --namespace witwave-self
kubectl top pods --namespace witwave-system
kubectl top nodes
```

If an agent is degraded, inspect just that agent before broadening:

```sh
kubectl describe witwaveagent <agent> --namespace witwave-self
kubectl describe pod -l app.kubernetes.io/instance=<agent> --namespace witwave-self
kubectl logs -l app.kubernetes.io/instance=<agent> --namespace witwave-self --all-containers --tail=120
```

If a restart delta is detected, inspect the restarted pod/container even when readiness is now Green:

```sh
kubectl describe pod <pod> --namespace witwave-self
kubectl get events --namespace witwave-self --field-selector involvedObject.name=<pod> --sort-by=.lastTimestamp
kubectl logs <pod> --namespace witwave-self --container <container> --previous --tail=120
kubectl logs <pod> --namespace witwave-self --container <container> --tail=120
```

When `--previous` logs are unavailable, say so and rely on `lastState.terminated`, events, and current logs.

Do not dump huge logs into the final answer. Summarize the relevant lines and preserve exact error snippets only when
they identify the cause.

### 5. Runtime persistence posture

For each active self agent, verify runtime storage exists and is mounted where conversation/task continuity needs it:

- Harness has `/home/agent/logs` and `/home/agent/state` mounted.
- Backend has `/home/agent/logs` and `/home/agent/state` mounted when backend persistence is enabled.
- `TASK_STORE_PATH` points under `/home/agent/state/`.
- Runtime PVC exists and is Bound.

Use JSONPath or `jq` if available; otherwise use `kubectl describe` and summarize.

### 6. Release and CI posture

```sh
gh release list --repo witwave-ai/witwave --limit 5
gh run list --repo witwave-ai/witwave --limit 15
git -C /workspaces/witwave-self/source/witwave status -sb || true
git -C /workspaces/witwave-self/source/witwave log --oneline --decorate -8 || true
```

Look for:

- Failed release workflows after a tag.
- Red CI on `main`.
- A release published without all expected artifacts.
- Local checkout behind/ahead if the question is about source state.
- Repeated failed/recovered release attempts that point to a process bug.

### 7. Report

Before returning, write the compact snapshot described in step 0. If there are at least three snapshots, or if the
oldest snapshot spans at least 24 hours before the current run, do a historical comparison before deciding whether a
Yellow finding is worth a Zora handoff.

Historical analysis should look for:

- **Repeated restarts** - the same pod/container restarting across multiple snapshots, even if it is currently Ready.
  For now, this is the highest-priority historical signal.
- **Persistent non-readiness** - the same agent, backend, operator pod, or PVC degraded in consecutive snapshots.
- **Warning-event clusters** - repeated `FailedMount`, `BackOff`, `Unhealthy`, `FailedScheduling`, image-pull, or probe
  warnings.
- **Runtime continuity drift** - missing or inconsistent `/home/agent/logs`, `/home/agent/state`, `TASK_STORE_PATH`, or
  runtime PVC posture.
- **Activity silence** - an agent with expected heartbeat activity stays quiet across multiple observation windows.
- **Release/process lag** - failed runs, partial releases, version drift, or `ww update --check` showing an available
  update that has not been acted on after repeated snapshots.
- **Resource pressure trend** - increasing memory/CPU pressure, OOM kills, throttling symptoms, or node pressure
  warnings.
- **Systemic issue candidates** - any repeated pattern that likely needs a repo change, operator/chart change,
  configuration change, resource adjustment, or rollout fix. These should be reported to Zora, even when the immediate
  platform status is only Yellow.

Update `baseline.md` when a repeated signal is confirmed as normal for the environment. Update `anomalies.md` when a
signal is not yet severe enough for Zora but should be watched. Update `escalations-to-zora.md` whenever a handoff is
sent or recommended.

Return a compact report. If the status is Red, if the same Yellow finding repeats across runs, or if the finding appears
to require a fix, distill it into a Zora handoff.

```text
Status: Green | Yellow | Red
Scope: <namespace / release / agent / operator>
What changed: <one paragraph>
Findings:
- <finding with evidence>
- <finding with evidence>
Zora handoff: <none | sent | recommended>
Human approval needed: <yes/no, and for what>
```

Use status consistently:

- **Green** — no action needed; normal activity can continue.
- **Yellow** — degraded, drifting, or needs follow-up, but no immediate platform fire.
- **Red** — failing release, red CI on main, operator unhealthy, agent unavailable, repeated restarts, missing required
  persistence, or data-loss risk.

### 8. Zora handoff

When a finding is systemic, repeated, or problematic enough to require team work, send Zora a concise A2A message via
`call-peer`.

Handoff shape:

```text
Mira platform anomaly report

Status: <Yellow | Red>
Scope: <operator | agent | release | runtime-storage | resource-pressure>
What changed: <one paragraph>
Evidence:
- <command/result/error, summarized>
- <command/result/error, summarized>
Why it matters: <risk to team work, releases, persistence, or recovery>
Suggested owner category: <release | operator | defect | gap | docs | unknown>

Please route this to the right peer or stand the team down if you think it is not actionable.
```

Do not tell Zora to run a specific mutating command. She owns routing. You own evidence quality.

### 9. Memory log

Append a short entry to `/workspaces/witwave-self/memory/agents/mira/platform_health_log.md` when the filesystem is
available:

```markdown
## YYYY-MM-DDTHH:MMZ — platform-health

**Status:** Green | Yellow | Red **Scope:** <scope> **Summary:** <one sentence> **Zora handoff:** <none | sent |
recommended>
```

If memory is unavailable, report that explicitly; do not fail the whole check just because the log write failed.
