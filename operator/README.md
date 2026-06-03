# witwave-operator

A Kubernetes operator for the witwave platform. Provides the `WitwaveAgent` custom resource, which deploys one named
agent — a harness orchestrator plus one or more backend sidecars (claude, openai, codex, gemini) — as a `Deployment` +
`Service` + optional `ConfigMap`, `HPA`, `PDB`, and `PVC`.

Built with Operator SDK v1.42 (Go). Mirrors the deployment shape of the [witwave Helm chart](../charts/witwave/) and is
intended as an alternative install path once the CRD is stable. The Helm chart remains the supported install method
while the operator is in `v1alpha1`.

> **Status:** v1alpha1, published to GHCR, installable via the `ww` CLI or raw Helm. `WitwaveAgent`, `WitwavePrompt`,
> and `WitwaveWorkspace` resources are all in-tree. Git-sync sidecars, dashboard `Deployment`/`Service`/ `Ingress`
> (#1741), per-MCP-tool and per-dashboard NetworkPolicies (#1743), and an optional per-agent `PrometheusRule` (#1746)
> all render as part of the operator's reconcile surface today.

## Requirements

- `kubectl` against a cluster (kind, minikube, EKS, etc.) — the runtime target.
- For developing the controller itself: Go 1.24+ and Operator SDK v1.42+.

## Getting Started — the `ww` CLI path (recommended for users)

The fastest install path is via the [`ww`](../clients/ww/) CLI, which ships with the operator chart embedded. No Helm
repo, no `helm` binary, no clone of this repo required:

```bash
curl -fsSL https://github.com/witwave-ai/witwave/releases/latest/download/install.sh | sh   # or: brew install witwave-ai/homebrew-ww/ww
ww operator install              # installs into witwave-system
ww operator status               # verify
```

`ww operator install` runs singleton detection (refuses when another release exists), RBAC preflight via
`SelfSubjectAccessReview`, and Helm install of the embedded chart. `ww operator upgrade` runs a CRD server-side-apply
pre-step so new CRD fields land before the pod rolls. Paired diagnostics:

```bash
ww operator logs      # tail operator pod logs
ww operator events    # Kubernetes events for operator + CRs
```

Both `install` and `upgrade` accept `-f/--values`, `--set`, and `--set-string` to overlay chart values, with the same
semantics as `helm` (later files win; `--set` overrides files; `--set-string` forces string typing). Use them to enable
optional chart features without leaving the CLI:

```bash
ww operator install -f my-operator-values.yaml          # fresh install with overrides
ww operator upgrade -f my-operator-values.yaml          # add/change overrides on an existing release
```

`upgrade` reuses the values already applied to the release (Helm reset-then-reuse): a plain `ww operator upgrade` keeps
every override you set earlier while still adopting the embedded chart's new defaults — including the operator image
tag, which tracks the embedded chart's `appVersion` rather than being reset. So a value enabled once survives later
upgrades without re-passing `-f`. (Enabling the `agentImagePatchPolicy` for an Agent-Resources agent is the canonical
use — see [Kubernetes API access](#kubernetes-api-access).)

See [`clients/ww/README.md`](../clients/ww/README.md#operator-management) for the full command surface (`--kubeconfig`,
`--context`, `--namespace`, `--yes`, `--dry-run`, `--values`, `--set`, `--set-string`, `--adopt`, `--delete-crds`,
`--force`, `--watch`, `--warnings`, `--tail`).

## Getting Started — Helm directly

For users who prefer Helm (GitOps pipelines, custom values, chart forks), the chart is published to GHCR:

```bash
helm install witwave-operator oci://ghcr.io/witwave-ai/charts/witwave-operator \
  --version <tag> --namespace witwave-system --create-namespace
```

See [charts/witwave-operator/README.md](../charts/witwave-operator/README.md) for the full values reference.

## Getting Started — developing the controller

Build and install CRDs against the current kubeconfig context:

```bash
make generate           # regenerate DeepCopy
make manifests          # regenerate CRD YAML + RBAC
make install            # apply CRDs to the cluster
```

Run the controller locally (outside the cluster) against the current context:

```bash
make run
```

Build and push the manager image, then deploy it into the cluster:

```bash
make docker-build docker-push IMG=<registry>/operator:<tag>
make deploy IMG=<registry>/operator:<tag>
```

Apply the sample resources:

```bash
kubectl apply -k config/samples/
kubectl get witwaveagent,witwaveprompt,witwaveworkspace
```

The bundled samples create an `adam` `WitwaveAgent`, a `health-check` `WitwavePrompt` that targets `adam`, and a
standalone `shared` `WitwaveWorkspace` example.

Uninstall (development target):

```bash
kubectl delete -k config/samples/
make undeploy
make uninstall
```

For a ww-installed operator, use `ww operator uninstall` instead.

## Migrating from the agent chart (`charts/witwave`)

The operator and the agent Helm chart (`charts/witwave`) are two independent deployment paths that both produce agent
Deployments. Running BOTH against overlapping agent names in the same namespace produces duplicate resources — one named
`{release}-{agent}` (from the chart) and one named `{agent}` (from the operator) — each with its own pods, HPA, PDB,
configs. No silent corruption happens, but you'll have doubled resources split-brained across two controllers. Pick one.

Tracked caveat: this is a known footgun on deliberate migration, not a defect in either path. Tracked for polish as
[#1478](https://github.com/witwave-ai/witwave/issues/1478). The procedure below is the supported migration path today.

### Chart → operator

Premise: you have agents running via `helm install <release> ./charts/witwave -f values.yaml` and want to adopt the
operator.

1. **Inventory what you're migrating.** List Deployments, Services, ConfigMaps, PVCs, and Secrets produced by the chart
   for each agent you're moving. `kubectl get all,cm,pvc,secret -l app.kubernetes.io/part-of=witwave -n <ns>` covers
   most of it.

2. **Preserve PVC data if any.** Backend conversation logs and session memory live on per-agent PVCs. The chart's PVCs
   survive `helm uninstall` by default — verify your PVC reclaim policy is `Retain` (not `Delete`) before step 3.
   `kubectl get pvc -n <ns>` → check each PVC's StorageClass reclaim policy.

3. **Uninstall the chart.** `helm uninstall <release> -n <ns>`. This removes Deployments + Services + ConfigMaps but
   leaves PVCs intact (per step 2). Agent pods stop serving traffic at this moment.

4. **Install the operator** if you haven't already: `ww operator install` (see the ww CLI path above).

5. **Write a `WitwaveAgent` CR per agent**, naming the CR with exactly the bare agent name — NOT the prefixed
   `{release}-{name}`. Point `spec.storage.existingClaim` at each preserved PVC from step 2 so the new operator-managed
   pod adopts the existing conversation + memory data.

   ```yaml
   apiVersion: witwave.ai/v1alpha1
   kind: WitwaveAgent
   metadata:
     name: iris # bare name; NO `prod-` prefix
     namespace: <ns>
   spec:
     backends:
       - name: claude
         storage:
           existingClaim: prod-iris-claude-memory # your surviving PVC
         credentials:
           existingSecret: ... # pre-provisioned
   ```

6. **Apply the CRs.** `kubectl apply -f witwaveagent-*.yaml`. Operator reconciles each, creates new Deployments (named
   bare `iris`, not `prod-iris`), mounts the preserved PVCs.

7. **Verify with `ww operator status`** — pods Running, CRs Ready, no leftover chart-rendered resources in
   `kubectl get deployments`.

### What's preserved vs lost on migration

| Preserved                                                                           | Lost                                                |
| ----------------------------------------------------------------------------------- | --------------------------------------------------- |
| Backend conversation logs on PVC                                                    | In-memory session state (pods restart)              |
| `.witwave/` prompts materialised via git-sync if the new pod mounts the same source | A2A request queues mid-flight during step 3         |
| Referenced Secrets (if `existingSecret`)                                            | Nothing that wasn't already lost on any pod restart |

No data copying required — the storage class does the work. The downtime is however long it takes to apply the CRs and
let the operator reconcile: typically 30 seconds to a few minutes.

### Operator → chart (reverse migration)

Rare, but the shape is the same with sides swapped. Delete `WitwaveAgent` CRs (the operator GC's owned resources on CR
delete), then `helm install <release> ./charts/witwave` with matching agent names + `existingClaim` pointing at the
surviving PVCs. The new resources will be named `{release}-{agent}` per the chart convention.

### Why both paths produce different names

The agent chart prefixes Deployments with the Helm release name because multiple chart installs coexist on one cluster
(e.g. `prod` + `test` namespaces with different release names). The operator skips the prefix because it's a
singleton-per-cluster controller whose CR `metadata.name` already uniquely identifies the agent. The two conventions
don't interoperate; migration is always a rename.

## The `WitwaveAgent` resource

One `WitwaveAgent` corresponds to one named agent (e.g. `iris`, `nova`, `kira`). Its spec mirrors the per-agent shape
used by the Helm chart's `agents[]` list. See `config/samples/witwave_v1alpha1_witwaveagent.yaml` for a minimal example
and `api/v1alpha1/witwaveagent_types.go` for the full schema.

Owned resources per `WitwaveAgent`:

| Resource                                 | When                                                                                                                                                                    |
| ---------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `Deployment`                             | always (when `spec.enabled != false`)                                                                                                                                   |
| `Service`                                | always; type set by `spec.serviceType` (default ClusterIP)                                                                                                              |
| `ConfigMap` (agent)                      | when `spec.config` is non-empty                                                                                                                                         |
| `ConfigMap` (per backend)                | when a backend's `config` is non-empty (and `enabled != false`)                                                                                                         |
| `PersistentVolumeClaim`                  | when a backend's `storage.enabled` is true (and `enabled != false`)                                                                                                     |
| `HorizontalPodAutoscaler`                | when `spec.autoscaling.enabled` is true                                                                                                                                 |
| `PodDisruptionBudget`                    | when `spec.podDisruptionBudget.enabled` is true                                                                                                                         |
| Dashboard `Deployment`/`Service`         | when `spec.dashboard.enabled` is true (#470)                                                                                                                            |
| Dashboard `Ingress`                      | when `spec.dashboard.enabled` and `spec.dashboard.ingress.enabled` are both true (#1741); `auth.mode=basic` stamps nginx auth annotations against `basicAuthSecretName` |
| Per-MCP-tool / dashboard `NetworkPolicy` | when `spec.networkPolicy.enabled` is true; renders sibling NetworkPolicies for each enabled MCP tool and the dashboard pod alongside the agent's own (#1743)            |
| `PrometheusRule`                         | when `spec.prometheusRule.enabled` is true (#1746); ships the chart's default alert set, gated on a `monitoring.coreos.com/v1` CRD-presence probe                       |
| `ServiceAccount`/`Role`/`RoleBinding`    | when `spec.kubernetesApiAccess.enabled` is true; renders a namespace-scoped Kubernetes API identity for in-pod `kubectl` / client-go use                                |

When `spec.enabled` is explicitly false, every owned resource above is torn down (only resources owned via
`IsControlledBy` are touched). Per-backend `backends[].enabled: false` skips that backend's container, PVC, and
ConfigMap while leaving the rest of the agent untouched.

Pod-level Prometheus scrape annotations are emitted onto the Pod template when
`spec.metrics.enabled && spec.metrics.podAnnotations` is true; the Service-level equivalents are gated on
`spec.metrics.serviceAnnotations` (default true).

All owned resources carry `ownerReferences` pointing at the `WitwaveAgent`, so deleting the CR cascades their deletion.

### Kubernetes API access

`spec.kubernetesApiAccess.enabled: true` creates a per-agent `ServiceAccount`, namespace-scoped `Role`, and
`RoleBinding`, then wires the agent pod to that ServiceAccount. Supported presets:

- `mode: readOnly` grants `get/list/watch` on common namespace-local workload resources and Witwave CRs plus `get` on
  `pods/log`. It does not grant secrets, nodes, persistent volumes, namespaces, or any mutating verbs.
- `mode: namespaceWrite` adds bounded namespace-local remediation verbs: manage ConfigMaps, Services, Deployments, Jobs,
  and CronJobs; delete or evict Pods for restart-style remediation. It still does not grant secrets, RBAC mutation, raw
  Pod creation, nodes, namespaces, persistent volumes, or cluster-scoped resources.
- `mode: agentLifecycle` is `namespaceWrite` plus `patch` on `witwaveagents`, so an Agent-Resources agent can run
  `ww agent upgrade` against its peers. The patch verb is resource-scoped; pair it with the `agentImagePatchPolicy`
  ValidatingAdmissionPolicy (gated in `charts/witwave-operator/values.yaml`, disabled by default) to limit those patches
  to image tags/digests — image repositories and backend wiring stay locked. Enable it durably by overlaying the release
  values through the CLI — `ww operator upgrade -f <values>.yaml` with `agentImagePatchPolicy.enabled: true` and
  `constrainedServiceAccounts: [<agent SA>]` — so the policy is owned by the Helm release and survives reinstalls. The
  self-team's overlay is checked in at [`.agents/self/operator-values.yaml`](../.agents/self/operator-values.yaml); its
  enable + adoption runbook is in [`.agents/self/bootstrap.md`](../.agents/self/bootstrap.md).

When this field is omitted or disabled, the operator renders the namespace `default` ServiceAccount with
`automountServiceAccountToken: false`, so the pod keeps the no-token posture while still converging cleanly under
server-side apply.

This field is intentionally separate from backend image composition. The `toolbox` image can put `kubectl`, `ww`, `gh`,
Helm, and analyzers on the backend filesystem, but those binaries have no Kubernetes authority unless this field (or an
explicit `serviceAccountName`) mounts credentials with matching RBAC.

### Health probes

Backend pods follow the three-probe split documented in `AGENTS.md` (#1719):

- `startupProbe` → `/health/start`
- `livenessProbe` → `/health`
- `readinessProbe` → `/health/ready`

`spec.probes.{startup,liveness,readiness}.*` mirror the chart's `probes.*` block; defaults give a ~5-minute cold-start
budget (`startup.failureThreshold=30 × period=10s`). The `echo` backend retains its `/health`-only behaviour per its
[intentional-non-scope list](../backends/echo/README.md) and gets no `startupProbe`.

### CORS

`spec.cors.{allowOrigins[], allowWildcard}` (#1748) mirror the chart's `cors.*` block. The operator stamps
`CORS_ALLOW_ORIGINS` (and `CORS_ALLOW_WILDCARD=true` when explicitly opted in) on the harness container env. The
validating webhook refuses an `allowOrigins` list containing `"*"` without `allowWildcard: true` so the chart's #763
fail-render guard is unreachable on the operator path too.

### MCP tool security

`spec.mcpTools[].*` accepts the same security knobs the chart renders (#1737):

- `serviceAccountName` — point at a pre-provisioned SA with the right RBAC instead of relying on the in-namespace
  default.
- `automountServiceAccountToken` — three-state pod-level flag (default preserves Kubernetes' `true` default); set to
  `false` when running alongside IRSA / workload-identity that injects creds another way.
- `imagePullSecrets`, `podSecurityContext`, `securityContext` — passthroughs that override the hardened defaults the
  reconciler stamps when the fields are unset (`runAsNonRoot=true`, `readOnlyRootFilesystem=true`, drop `ALL`
  capabilities, `seccompProfile: RuntimeDefault`). `emptyDir` volumes for `/tmp` + `/home/agent/.cache` coexist with the
  read-only root.

Per-tool RBAC sibling rendering (Role + RoleBinding + ServiceAccount with rules) is deliberately deferred — operators
can use `serviceAccountName` to point at a pre-provisioned SA in the meantime.

## The `WitwavePrompt` resource

One `WitwavePrompt` declares a single prompt definition that binds to one or more `WitwaveAgent`s. The operator
reconciles a `ConfigMap` per `(WitwavePrompt, agent)` pair; the WitwaveAgent pod mounts each ConfigMap as a subPath file
at `/home/agent/.witwave/<kind>/witwaveprompt-<name>.md` (or `HEARTBEAT.md` for kind=heartbeat) so the harness scheduler
picks them up alongside anything gitSync dropped into the same directory.

See `config/samples/witwave_v1alpha1_witwaveprompt.yaml` for a runnable example and
`api/v1alpha1/witwaveprompt_types.go` for the full schema.

### Kinds

Each kind maps to one of the harness scheduler directories (or the singleton heartbeat file):

| `spec.kind`    | Target path                                                  | Required frontmatter                                                      |
| -------------- | ------------------------------------------------------------ | ------------------------------------------------------------------------- |
| `job`          | `/home/agent/.witwave/jobs/witwaveprompt-<name>.md`          | `schedule` (cron)                                                         |
| `task`         | `/home/agent/.witwave/tasks/witwaveprompt-<name>.md`         | none (`window-start` enables calendar scheduling; omitted means run-once) |
| `trigger`      | `/home/agent/.witwave/triggers/witwaveprompt-<name>.md`      | `endpoint`                                                                |
| `continuation` | `/home/agent/.witwave/continuations/witwaveprompt-<name>.md` | `continues-after` (string or list)                                        |
| `webhook`      | `/home/agent/.witwave/webhooks/witwaveprompt-<name>.md`      | `url`                                                                     |
| `heartbeat`    | `/home/agent/.witwave/HEARTBEAT.md` (singleton per agent)    | none                                                                      |

### Multi-bind

`spec.agentRefs[]` lists every WitwaveAgent the prompt binds to. The operator renders one ConfigMap per agent (name
pattern `witwaveprompt-<crname>-<agent>`) with owner-reference cascade and stale- binding garbage collection. An
optional `filenameSuffix` on each ref disambiguates when the same CR binds to multiple agents that already have a
gitSync-managed prompt sharing the default filename.

### Admission webhook invariants

The `ValidatingWebhookConfiguration` enforces:

- Kind-specific required frontmatter keys (see table above)
- `continues-after` must be a non-empty string or list of strings
- Duplicate `agentRefs` entries rejected
- `kind: heartbeat` is singleton-per-agent — no two WitwavePrompts can both target the same agent with heartbeat, since
  the harness reads a single `HEARTBEAT.md` and the writes would race

When admission webhooks are disabled (cert-manager not installed + no BYO-cert set — see "Admission webhook TLS" below)
the CRD's structural schema is the only validation and these invariants are not enforced.

### Status

Each reconcile writes `.status` via the subresource:

- `observedGeneration` — spec generation most recently reconciled
- `readyCount` — number of bindings whose ConfigMap applied cleanly
- `bindings[]` — one entry per `spec.agentRefs`, keyed by `agentName`, with `configMapName`, `filename`, `ready`, and a
  `message` when a binding failed (e.g. "target WitwaveAgent not found")
- `conditions[]` — one `Ready` condition, `True` when every binding is ready

The reconciler tracks materialization (did the ConfigMap apply?) — NOT runtime execution (did the prompt actually
fire?). Execution telemetry lives in Prometheus / conversation.jsonl / tool-activity.jsonl / the dashboard views. See
request [#642](https://github.com/witwave-ai/witwave/issues/642) for the runtime-status proposal.

## The `WitwaveWorkspace` resource

A `WitwaveWorkspace` provisions shared volumes, projects pre-created Secrets, and renders ConfigMap-backed files that
the operator stamps onto every `WitwaveAgent` whose `spec.workspaceRefs[]` references it. Unlike
`WitwaveAgentSpec.GitSyncs` (per-agent _config_ delivery) and `WitwaveAgentSpec.SharedStorage` (per-agent storage),
`WitwaveWorkspace` is the primitive for **shared resources that multiple agents collaborate over** — source trees,
datasets, accumulated memory pools, anything where agents need the same files visible at the same paths.

See `api/v1alpha1/witwaveworkspace_types.go` for the full schema and the design context in `tmp/workspace-crd.md`.

### Spec

```yaml
apiVersion: witwave.ai/v1alpha1
kind: WitwaveWorkspace
metadata:
  name: shared
  namespace: witwave
spec:
  volumes:
    - name: source
      size: 50Gi
      storageClassName: efs-sc
      # mountPath defaults to /workspaces/<workspace>/<volume.name>
      # accessMode defaults to ReadWriteMany; use ReadWriteOnce for
      # single-node dev clusters whose storage class cannot satisfy RWM
      # reclaimPolicy defaults to Delete; flip to Retain for stateful volumes
  secrets:
    - name: shared-github-token
      envFrom: true
    - name: shared-registry-creds
      mountPath: /home/agent/.docker
  configFiles:
    - configMap: shared-claude-md
      mountPath: /home/agent/.claude/workspaces/shared/CLAUDE.md
      subPath: CLAUDE.md
    - inline:
        name: workspace-info
        path: workspace.yaml
        content: |
          repos:
            - name: witwave
              url: https://github.com/witwave-ai/witwave
      mountPath: /workspaces/shared/workspace.yaml
```

Three list-shaped fields, all optional, all loose. The operator provisions; it does not interpret what the volumes,
secrets, or files are for.

| Field         | Shape                   | Notes                                                                                                                                                                                                                                                                                                                                                                                        |
| ------------- | ----------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `volumes`     | `[]WorkspaceVolume`     | One PVC per entry (`<workspace>-vol-<name>`). `storageType` enum is `pvc` or `hostPath`; only `pvc` is honoured in v1alpha1 — the admission webhook rejects `hostPath` so the enum stays stable for a future v1.x. `accessMode` defaults to `ReadWriteMany`; `ReadWriteOnce` and `ReadWriteOncePod` are accepted for constrained dev or single-pod cases. `reclaimPolicy: Delete \| Retain`. |
| `secrets`     | `[]WorkspaceSecret`     | Existing-Secret references only — no inline data, by design. Each entry is mutually-exclusive `mountPath` OR `envFrom: true`; the webhook rejects entries that set both.                                                                                                                                                                                                                     |
| `configFiles` | `[]WorkspaceConfigFile` | Each entry references either a pre-created `configMap` (operator never writes to it) or an `inline` block the operator renders into a project-owned ConfigMap. Exactly one of the two must be set.                                                                                                                                                                                           |

`MaxItems` caps: `volumes` 20, `secrets` 50, `configFiles` 50. Volume names must be DNS-1123 label-safe so the rendered
PVC names stay valid.

### Membership — agent-owned

Membership flows from agent to workspace, not the other way around: a `WitwaveAgent` declares the workspaces it
participates in via `spec.workspaceRefs[]`, and the workspace controller maintains `Status.BoundAgents` as an inverted
index by watching agent CR changes. Eventually consistent, no source-of-truth duality. v1alpha1 only matches
same-namespace agents; the recorded `Namespace` field in `Status.BoundAgents` documents the assumption and keeps the
door open for a cross-namespace shape without status churn later.

### RBAC

The chart's manager ClusterRole grants the standard CRUD verbs on `workspaces`, plus `get/patch/update` on
`workspaces/status` and `update` on `workspaces/finalizers`. The operator does **not** request Secret write verbs from
this surface — workspace-scoped Secrets are existing-Secret refs only, so the operator stays out of the secrets-write
trust boundary. The same `rbac.secretsWrite=false` lever the witwave-operator chart already exposes covers the rest.

### Admission webhook

A `vwitwaveworkspace.kb.io` validating webhook lands in the existing `ValidatingWebhookConfiguration` alongside
`vwitwaveagent.kb.io` and `vwitwaveprompt.kb.io`, and shares the same `webhooks.validatingFailurePolicy` knob (default
`Ignore`). The webhook rejects:

- `volumes[].storageType: hostPath` (reserved for v1.x).
- `volumes[].accessMode` outside `ReadWriteMany`, `ReadWriteOnce`, or `ReadWriteOncePod`.
- `secrets[]` entries that set both `mountPath` and `envFrom`.
- `configFiles[]` entries that set neither `configMap` nor `inline`, or both.
- Mount path collisions across `volumes`, `secrets`, and `configFiles` within the same WitwaveWorkspace.

### WitwaveWorkspace status

The reconciler writes the following `.status` fields:

- `observedGeneration`
- `boundAgents[]` — the inverted index over `WitwaveAgent.spec.workspaceRefs[]`.
- `conditions[]` — `Ready`, `VolumesProvisioned`, `BoundAgentsTracked`, `ConfigMapsRendered`, and `DeletionBlocked` (set
  when a delete is in flight but agents still reference the workspace).

A refuse-delete finalizer keeps a WitwaveWorkspace from being removed while any agent still references it. Use
`ww workspace unbind <agent> <workspace>` (or drop the entry from `spec.workspaceRefs[]` directly) to clear the block.

### Out of scope for v1alpha1

The CRD is deliberately thin. None of the following is on the v1 surface:

- No `repos[]`, no clone bootstrapping, no operator-managed git plumbing — agents that need git use a credential from
  `secrets[]` and a URL from `configFiles[]` (or their own knowledge).
- No agent-side bootstrap. Volumes start empty; agents handle initial population.
- No `hostPath` storage type — the enum value exists for a future v1.x but the webhook rejects it today.
- No storage-class capability probing — the operator accepts the requested access mode and lets Kubernetes/CSI report
  whether the cluster can satisfy the PVC.
- No operator-enforced editing locks or per-agent worktree partitioning. Coordination is the agents' problem (A2A
  messages, OS file locks, etc.).
- No workspace-level scheduled jobs / heartbeats / tasks, no workspace-scoped MCP-tool gating.

## Credentials

Both `GitSyncSpec.credentials` and `BackendSpec.credentials` accept the same three-mode resolver (parity with the Helm
chart's `witwave.resolveCredentials` helper):

```yaml
# Production: reference a pre-created Secret
gitSyncs:
  - name: witwave
    repo: https://github.com/org/repo
    credentials:
      existingSecret: agent-github-credentials   # must contain GITSYNC_USERNAME + GITSYNC_PASSWORD

# Dev: inline values. Operator reconciles a Secret named
# <agent>-<entry>-gitsync-credentials owned by the WitwaveAgent (GC'd on
# removal). acknowledgeInsecureInline MUST be true or the admission
# webhook rejects the CR — inline values land in etcd + `kubectl get
# witwaveagent -o yaml`, so the ack flag is the explicit opt-in.
gitSyncs:
  - name: witwave
    repo: https://github.com/org/repo
    credentials:
      username: dev-user
      token: ghp_...
      acknowledgeInsecureInline: true

# Legacy: raw envFrom (unchanged — works when credentials is empty)
gitSyncs:
  - name: witwave
    envFrom:
      - secretRef:
          name: my-existing-secret
```

The backend equivalent (`BackendSpec.credentials`) uses a `secrets:` map instead of `username`/`token` so each backend
can set its own env-var shape (`CLAUDE_CODE_OAUTH_TOKEN`, `OPENAI_API_KEY`, `GOOGLE_API_KEY`).

Operator-managed credential Secrets carry `app.kubernetes.io/component: credentials` and are dual-checked (label +
`IsControlledBy`) before any update or delete — user-created Secrets that collide by name are never touched.

## Admission webhook TLS

The controller-manager's admission webhook server (port 9443) needs a TLS cert. The `witwave-operator` Helm chart
supports two modes:

- **cert-manager** (default): chart renders a Certificate + Issuer, CA bundle auto-injected into the webhook configs
- **BYO Secret**: set `webhooks.existingSecret` + `webhooks.caBundle` to reference a pre-created Secret with `tls.crt` +
  `tls.key`. Use this when cert-manager isn't available (air-gapped, service mesh, Vault PKI, strict RBAC).

Full setup in `charts/witwave-operator/README.md` — both modes are covered with copy-paste snippets.

## `WitwaveAgent` status

The controller writes the following status fields:

- `phase` — one of `Pending`, `Ready`, `Degraded`, `Error` (shown as a printer column).
- `readyReplicas` — mirrored from the Deployment's `status.readyReplicas`.
- `observedGeneration` — the spec generation most recently reconciled.
- `conditions` — `Available`, `Progressing`, and `ReconcileSuccess` following the standard Kubernetes condition
  convention.

## Field indexers and controller-runtime wiring

Two indexers feed the reconciler's reverse-lookup paths without triggering full-list fan-outs:

- `WitwavePromptAgentRefIndex` — indexes each `WitwavePrompt.spec.agentRefs[].name`, so a `WitwaveAgent` reconcile can
  enumerate every WitwavePrompt bound to it in O(1) for rebind / teardown.
- `WitwaveAgentTeamIndex` — indexes `WitwaveAgent` by team label so cross-agent views can resolve team membership
  without listing every WitwaveAgent in the namespace.

Leader election is on by default (`--leader-elect=true`). Multi-replica operator rollouts are safe without additional
flags.

## Per-container metrics port

Every managed container (harness, backends, MCP tools) exposes `/metrics` on `app_port + 1000` by default, matching the
chart (#687) and removing the need for per-container `MetricsPort` overrides on the WitwaveAgent CR. The CRD's
`MetricsPort` field is deprecated — set only if you need to override the convention.

## WitwavePrompt CRD installation

The WitwavePrompt CRD is wired into `config/crd/kustomization.yaml` alongside WitwaveAgent, so `make install` now
applies both CRDs in one pass.

## Chart / operator feature fidelity

The Helm chart (`charts/witwave`) and this operator render equivalent workloads for the same inputs. Remaining by-design
asymmetries:

| Concept                                                    | Chart | Operator | Notes                                                                                                                                                                               |
| ---------------------------------------------------------- | ----- | -------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `WitwavePrompt` CRD                                        | —     | ✓        | Operator-only; chart path uses gitSync mappings to materialise prompts.                                                                                                             |
| Trigger Ingress (external webhooks reaching `/triggers/*`) | —     | —        | Neither path emits it. Users hand-roll or use service mesh routing. Design discussion is [request #trigger-ingress](https://github.com/witwave-ai/witwave/issues) (pending filing). |

Tracked open requests (not gaps):

| Topic                                        | Issue                                                    | State                 |
| -------------------------------------------- | -------------------------------------------------------- | --------------------- |
| WitwavePrompt runtime execution status on CR | [#642](https://github.com/witwave-ai/witwave/issues/642) | request, Ready: false |

## Metrics

The manager exposes `/metrics` (controller-runtime default) on the port configured by `--metrics-bind-address`. Standard
reconcile / workqueue / client-go counters come for free.

WitwaveAgent-specific domain metrics added on top (#471):

| Metric                                       | Type    | Labels              | Meaning                                                                                                                                                                                                         |
| -------------------------------------------- | ------- | ------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `witwaveagent_phase_transitions_total`       | counter | `from`, `to`        | Status.phase transitions (Pending → Ready, Ready → Degraded, etc.)                                                                                                                                              |
| `witwaveagent_pvc_build_errors_total`        | counter | `backend`           | Backend PVC entries skipped due to invalid spec (e.g. `storage.size` parse fail)                                                                                                                                |
| `witwaveagent_dashboard_enabled`             | gauge   | `namespace`, `name` | 1 when `spec.dashboard.enabled=true`, 0 otherwise. `sum()` for cluster total.                                                                                                                                   |
| `witwaveagent_teardown_step_errors_total`    | counter | `kind`, `reason`    | Per-kind teardown failures when `spec.enabled=false` or the CR is deleted; useful for alerting when cascade cleanup is partial                                                                                  |
| `witwaveprompt_status_patch_conflicts_total` | counter | `namespace`, `name` | `WitwavePrompt` status subresource patch 409 conflicts retried with fresh `resourceVersion` (#950). Sustained non-zero rate points at a noisy reconciler (too many concurrent writers) or cache lag under load. |
| `witwaveworkspace_reconcile_total`           | counter | `outcome`           | `WitwaveWorkspace` reconcile passes labelled by outcome (`success`, `error`, `delete_blocked`, `deleted`).                                                                                                      |
| `witwaveworkspace_volumes_provisioned`       | gauge   | `namespace`, `name` | Number of PVCs provisioned for the WitwaveWorkspace's `spec.volumes[]`. Sum across instances for cluster-wide totals.                                                                                           |
| `witwaveworkspace_bound_agents`              | gauge   | `namespace`, `name` | Cardinality of `Status.BoundAgents` per WitwaveWorkspace — the inverted index over `WitwaveAgent.spec.workspaceRefs[]`.                                                                                         |

WitwavePrompt binding-outcome metrics (label schema `namespace`, `name`) track per-binding ConfigMap apply results so
operators can alert on chronically unready bindings.

The gauges (`witwaveagent_dashboard_enabled`, WitwavePrompt binding gauges) are dropped on resource deletion; the
counters persist (Prometheus convention — counters are monotonic).

### Prometheus Operator integration

The operator reconciles two optional CRs when the `monitoring.coreos.com/v1` API group is installed on the cluster:

- `ServiceMonitor` via `spec.serviceMonitor.enabled` (#476) — scrapes the harness's `/metrics` endpoint through the
  per-agent Service
- `PodMonitor` via `spec.podMonitor.enabled` (#582) — scrapes each backend container's `/metrics` directly on the pod
  via the named `<backend>-metrics` port (backend-level telemetry like tokens, tool use, context usage)

Both use an unstructured client so the operator build has no hard dependency on prometheus-operator Go types. When the
CRD is absent the reconciler logs once per reconcile and no-ops; reconciliation resumes automatically once the CRDs
appear.

## Tracing (OpenTelemetry)

When `OTEL_ENABLED=true`, the manager emits one server span per `Reconcile()` call (`witwaveagent.reconcile`) attributed
with `witwave.namespace`, `witwave.name`, and the resulting `witwave.phase`. Errors are recorded on the span so
collectors flag them red. W3C trace-context propagator matches the Python side (#468/#469) so inbound `traceparent`
headers propagate across the agent boundary.

Standard OTel env vars apply: `OTEL_EXPORTER_OTLP_ENDPOINT`, `OTEL_SERVICE_NAME` (defaults to `witwave-operator`),
`OTEL_TRACES_SAMPLER`. `POD_NAMESPACE` and `POD_NAME` (set via downward API in the chart's pod spec) feed
`k8s.namespace` and `k8s.pod.name` resource attributes.

When `OTEL_ENABLED` is unset/false (default), the no-op tracer takes over and the per-reconcile overhead is a single
branch + interface dispatch.

**The operator does not deploy an OpenTelemetry Collector.** Matching the idiomatic pattern across Strimzi,
cert-manager, Istio, Elastic ECK, Knative, Argo, Crossplane, and grafana-operator, witwave-operator emits OTLP to a
user-provided endpoint and delegates collector deployment to something purpose-built for that job:

- **Recommended:** install the [opentelemetry-operator](https://github.com/open-telemetry/opentelemetry-operator) and
  create an `OpenTelemetryCollector` CR, then point `OTEL_EXPORTER_OTLP_ENDPOINT` at the resulting Service.
- **Alternative:** point at any OTLP-compatible target directly — Jaeger, Tempo, Honeycomb, Grafana Cloud, Datadog.

Wiring per WitwaveAgent:

```yaml
spec:
  env:
    - name: OTEL_ENABLED
      value: "true"
    - name: OTEL_EXPORTER_OTLP_ENDPOINT
      value: http://otel-collector.observability:4318
```

Client-go HTTP transport instrumentation (so Kubernetes API calls emit child spans) is a separate enhancement — not yet
implemented; tracked follow-on if needed.

## License

Apache 2.0 — see [LICENSE](../LICENSE) (once present) for the full text.
