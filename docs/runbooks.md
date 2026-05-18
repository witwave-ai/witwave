# Runbooks

Operator-facing pages for each default alert shipped in `charts/witwave/templates/prometheusrule.yaml`. The
`runbook_url` annotation on every alert points at the corresponding section here so on-call engineers see a playbook
link in the page / Slack notification itself.

Anchors are the alert name in all-lowercase (Prometheus convention when GitHub renders markdown). New alerts MUST add a
section here with a matching `runbook_url` annotation. CI enforces that coverage via
`scripts/check-runbook-coverage.sh`, and the convention is load-bearing for on-call ergonomics.

---

## `witwavebackenddown`

**What fires it.** A backend pod hasn't reported metrics for `forMinutes` (default 5m). Covers crash-loop, node
eviction, image- pull failure, or a wedged metrics listener.

**First checks.**

```bash
# Which pod is the alert about?
kubectl get pod -n <ns> -l app.kubernetes.io/part-of=witwave -o wide

# Pod events (scheduling failures, image pulls)
ww operator events --warnings

# Pod logs
ww operator logs --pod <pod>
```

**Remediation.**

- **CrashLoopBackOff:** check recent config changes; roll back the last `helm upgrade` if it correlates.
- **ErrImagePull / ImagePullBackOff:** verify `ghcr.io/witwave-ai/images/*` is reachable and that the image tag is real.
- **Node eviction:** check `kubectl describe node <node>` for resource pressure; may indicate PVC fill or memory
  pressure unrelated to Witwave.

**Escalation.** If the backend is down on > 50% of agents, treat as cluster-wide incident (likely registry / node pool
outage).

---

## `witwavehookdenialspike`

**What fires it.** `backend_hooks_denials_total` 5-minute rate exceeds `rateThreshold` (default 0.5/s) — hook policies
are denying tool calls at unusual volume.

**First checks.**

```bash
# Which agent + backend?
# The {agent, backend} labels are on the metric.

# Recent trace rows include tool_audit entries; filter locally for denials:
curl -H "Authorization: Bearer $CONVERSATIONS_AUTH_TOKEN" \
  "$BACKEND_URL/trace?limit=200" \
  | jq '[.[] | select(.event_type == "tool_audit" and .decision == "deny")] | .[:20]'
```

**Remediation.**

- **Misconfigured policy:** a recent change to `.claude/hooks.yaml` may have widened the deny set. Review git history of
  the hooks file and consider rolling back.
- **Jailbreak pressure:** the agent is hammering disallowed tools repeatedly. Check session logs for the prompt that
  triggered the loop. Consider pausing the offending session or tightening the upstream prompt.

**Escalation.** If the denial rate is combined with `witwavemcpauthfailure` on the same backend, suspect a compromised
backend OR a test harness that's gone rogue.

---

## `witwavemcpauthfailure`

**What fires it.** `backend_mcp_command_rejected_total` 5m rate exceeds `rateThreshold` (default 0.1/s) — the MCP
command allow-list is rejecting calls.

**First checks.**

- What `reason` label is attached? The metric carries reasons like `auth`, `cwd`, `binary`, etc.

**Remediation.**

- **`reason=auth`:** the MCP tool container is rejecting the backend's bearer token. Confirm `MCP_TOOL_AUTH_TOKEN` is
  set on both sides; check for recent token rotation that didn't propagate.
- **`reason=binary`:** the backend tried to spawn a stdio MCP command not in `MCP_ALLOWED_COMMANDS` /
  `MCP_ALLOWED_COMMAND_PREFIXES`. Widen the allow-list OR fix the backend's `mcp.json`.
- **`reason=cwd`:** working-directory denied. Check `MCP_ALLOWED_CWD_PREFIXES`.

---

## `witwavewebhooktimeout`

**What fires it.** `harness_webhooks_delivery_total{result="timeout_total"}` 5m rate exceeds `rateThreshold` (default
0.05/s) — webhook deliveries are timing out.

**First checks.**

```bash
# Which subscription? {subscription} label is on the metric.

# Inspect the webhook definition:
cat .witwave/webhooks/<sub-name>.md
```

**Remediation.**

- **Sink unreachable:** curl the webhook URL from inside the harness pod. NetworkPolicy may be blocking egress.
- **Sink slow:** tune `WEBHOOK_DELIVERY_TIMEOUT` or reduce the body size the subscription posts.
- **Retry budget exhausted:** check `harness_webhooks_retry_bytes_evicted_total` for shed events; a slow sink is
  drowning the retry queue.

---

## `witwavelockwaitsaturation`

**What fires it.** `backend_sqlite_task_store_lock_wait_seconds` p99 over 5m exceeds `p99Seconds` (default 1s). SQLite
task store is contending on locks; in-flight tasks are queuing.

**First checks.**

- Which backend? `{agent, backend}` labels identify the specific pod.
- Is the backend high-traffic (many concurrent sessions) or idle (unexpected saturation)?

**Remediation.**

- **Expected load:** horizontally split high-traffic work across additional agents; one backend + SQLite can only
  serialize so many writes.
- **Unexpected saturation on idle backend:** look at `backend_sqlite_task_store_errors_total` and task-store log
  entries; may indicate a disk I/O problem on the PVC.

---

## `witwavepvcfillwarning` / `witwavepvcfillcritical`

**What fires it.** `kubelet_volume_stats_used_bytes / kubelet_volume_stats_capacity_bytes` exceeds the threshold — 70%
for the warning variant, 90% for critical — on any PVC whose name matches the chart's PVC-name regex (default
`.*witwave.*`).

**First checks.**

```bash
# Which PVC is filling?
kubectl get pvc -A | grep -E "<release>|witwave"

# Inside the pod: what's eating space?
kubectl exec -n <ns> <pod> -- du -sh /* 2>/dev/null | sort -h
```

The conversation log (`conversation.jsonl`) is the usual offender — it grows unboundedly on long-lived agents.

**Remediation.**

- **Log rotation not configured:** ensure `MAX_LOG_FILE_SIZE_BYTES` and `MAX_LOG_BACKUP_COUNT` are set in the backend's
  env. Defaults are generous; bump them up for chatty agents or narrow them down to keep disk bounded.
- **Prune old conversations:** delete rotated `*.jsonl.*` files older than your retention window.
- **Resize the PVC:** if log pressure is legitimate, resize the PVC (requires a StorageClass that supports volume
  expansion).

**Escalation.** `pvcFillCritical` at 90% is close to pod eviction. Act quickly — a full disk will wedge the pod's
writes, and the backend will start dropping conversation history.

---

## `witwavewebhookretrybyteshalffull`

**What fires it.**
`harness_webhooks_retry_bytes_in_flight_total / harness_webhooks_retry_bytes_budget_bytes{scope="global"}` exceeds the
ratio threshold (default 0.5 — i.e. 50% of `WEBHOOK_RETRY_BYTES_BUDGET`).

This is an **early-warning** signal. The shed counter (`harness_webhooks_delivery_total{result="shed_retry_bytes"}`) is
a lagging signal — by the time events are being dropped, you've already lost delivery. The gauge-based alert gives
operators headroom to act.

**First checks.**

```promql
# Per-subscription culprit:
harness_webhooks_retry_bytes_in_flight

# Current cap:
harness_webhooks_retry_bytes_budget_bytes{scope="global"}
```

Identify the subscription with the largest `_in_flight` value — it's the one whose sink is slow or unreachable.

**Remediation.**

- **Slow sink:** contact the receiver; verify they're up and processing.
  `harness_webhooks_delivery_total{result="timeout_total"}` spiking confirms sink-side latency.
- **Unreachable sink:** NetworkPolicy blocking egress, DNS failure, or a URL typo. Test with `kubectl exec` + `curl`
  from the harness pod.
- **Legitimate burst:** a real event storm (e.g. dashboards firing on every reconcile) can temporarily saturate the
  budget. Raise `WEBHOOK_RETRY_BYTES_BUDGET` via chart values OR tune the subscription's retry count down if the
  delivery is non-critical.
- **Runaway subscription:** one sub dominating the total is what `WEBHOOK_RETRY_BYTES_PER_SUB` is for — if the per-sub
  cap isn't binding, lower it in chart values.

**Escalation.** If the ratio stays above 50% for > 30 minutes with no per-sub culprit AND the sink is reachable, suspect
a harness-side accounting bug (a decrement path that's not releasing slots). File an issue referencing this alert +
current gauge values.

---

## `witwavea2alatencyhigh`

**What fires it.** `harness_a2a_backend_request_duration_seconds` p99 over 5m exceeds `p99Seconds` (default 30s). The
harness → backend A2A relay is slow; usually pool exhaustion or a backend stuck in a long LLM call.

**First checks.**

- Which backend? `{backend}` label on the metric.
- Is the backend healthy? `ww operator status` + `ww operator logs --pod <backend-pod>`.

**Remediation.**

- **Pool exhaustion:** increase the backend's httpx connection pool via the relevant chart values, or split load across
  more backends.
- **Slow LLM call:** expected for large contexts or tool-heavy sessions. Consider raising `p99Seconds` if this is
  workload-normal.
- **Wedged backend:** if logs show the backend accepting but never replying, restart the pod.

---

## `witwaveeventvalidationerrors`

**What fires it.** `harness_event_stream_validation_errors_total` 5m rate is non-zero — the harness is emitting events
that fail schema validation and being dropped.

**First checks.**

- `type` label identifies the misshapen event type.
- Harness log lines adjacent to each drop include the specific validation error.

**Remediation.**

- **Recent code change:** someone added a new event emission path without updating `shared/event_schema.py` or
  `docs/events/events.schema.json`. Roll back OR land the schema addition.
- **Schema drift:** the validator and the emitter landed in different PRs. Make them agree.

This alert is most useful during active development. If it fires in steady-state production, a bad event is being
emitted silently — fix promptly because the downstream subscribers never see the affected events at all.

**Related policy.** Event schema bump rules live in [`docs/events/README.md#versioning`](events/README.md#versioning).

---

## Adding a new alert

When you add an alert to `charts/witwave/templates/prometheusrule.yaml`:

1. Add a section here with a matching all-lowercase anchor.
2. Set the alert's `runbook_url` annotation to
   `https://github.com/witwave-ai/witwave/blob/main/docs/runbooks.md#<anchor>`.
3. Follow the existing template: **What fires it**, **First checks**, **Remediation**, optional **Escalation**.
4. Add a values.yaml block under `prometheusRule.alerts.<name>` so thresholds are overrideable.

Keeps on-call engineers from having to reverse-engineer the fix from the metric name.
