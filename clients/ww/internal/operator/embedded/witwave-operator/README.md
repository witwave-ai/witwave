# witwave-operator

Helm chart for the witwave operator — deploys the witwave-operator controller manager and the `WitwaveAgent`,
`WitwavePrompt`, and `WitwaveWorkspace` CRDs.

> **Looking for the fastest install?** The [`ww`](../../../../../../clients/ww/) CLI embeds this chart (v0.5.0+). Install `ww`
> (`curl -fsSL https://github.com/witwave-ai/witwave/releases/latest/download/install.sh | sh` or
> `brew install witwave-ai/homebrew-ww/ww`) then `ww operator install` — no Helm repo configuration required. The chart
> instructions below are for users who prefer raw Helm (GitOps pipelines, custom values, forks).

## Prerequisites

- Kubernetes 1.25+
- Helm 3.10+

## Installation

Create the namespace:

```bash
kubectl create namespace witwave
```

If your images are in a private registry (e.g. GHCR), create an image pull secret and reference it in values:

```bash
kubectl create secret docker-registry ghcr-credentials \
  --docker-server=ghcr.io \
  --docker-username=<github-username> \
  --docker-password=<github-token> \
  --namespace witwave
```

```yaml
imagePullSecrets:
  - name: ghcr-credentials
```

Install:

```bash
helm install witwave-operator oci://ghcr.io/witwave-ai/charts/witwave-operator --namespace witwave
```

Install a specific version:

```bash
helm install witwave-operator oci://ghcr.io/witwave-ai/charts/witwave-operator --version <X.Y.Z> --namespace witwave
```

## Uninstall

```bash
helm uninstall witwave-operator --namespace witwave
```

All three CRDs (`WitwaveAgent`, `WitwavePrompt`, `WitwaveWorkspace`) ship under `charts/witwave-operator/crds/` and
carry the `helm.sh/resource-policy: keep` annotation, so they are **not** deleted on uninstall. Existing CRs remain in
the cluster and can be reconciled by a re-installed operator. Delete the CRDs manually if you want to fully tear down:

```bash
kubectl delete crd witwaveagents.witwave.ai
kubectl delete crd witwaveprompts.witwave.ai
kubectl delete crd witwaveworkspaces.witwave.ai
```

## Values

| Parameter                             | Description                                                                                                                                                                                                                                                                                                                                                  | Default                                      |
| ------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | -------------------------------------------- |
| `image.repository`                    | Controller manager image repository                                                                                                                                                                                                                                                                                                                          | `ghcr.io/witwave-ai/images/operator`         |
| `image.tag`                           | Image tag (defaults to `.Chart.AppVersion`)                                                                                                                                                                                                                                                                                                                  | `""`                                         |
| `image.pullPolicy`                    | Image pull policy                                                                                                                                                                                                                                                                                                                                            | `IfNotPresent`                               |
| `imagePullSecrets`                    | Image pull secrets for the controller pod                                                                                                                                                                                                                                                                                                                    | `[]`                                         |
| `replicaCount`                        | Number of controller manager replicas                                                                                                                                                                                                                                                                                                                        | `1`                                          |
| `installCRDs`                         | Deprecated (#1075). CRDs ship under `charts/witwave-operator/crds/` and are installed by Helm ahead of any template, bypassing the install-time webhook/CRD ordering race. The flag is no longer consulted by any template.                                                                                                                                  | `true`                                       |
| `rbac.create`                         | Create the ClusterRole/ClusterRoleBinding and leader-election Role/RoleBinding                                                                                                                                                                                                                                                                               | `true`                                       |
| `rbac.scope`                          | `cluster` (install a ClusterRole + ClusterRoleBinding, operator watches all namespaces) or `namespace` (install a Role + RoleBinding per `rbac.watchNamespaces` entry; ClusterRole is skipped) (#532)                                                                                                                                                        | `cluster`                                    |
| `rbac.watchNamespaces`                | Namespaces the operator watches when `rbac.scope=namespace`. Each entry receives a per-namespace Role + RoleBinding. Empty falls back to the release namespace. Ignored when `rbac.scope=cluster`                                                                                                                                                            | `[]`                                         |
| `rbac.secretsWrite`                   | Toggle Secret write verbs (`create`/`delete`/`patch`/`update`) on the operator's Role/ClusterRole. Read verbs (`get`/`list`/`watch`) are always granted. Set to `false` when all backend credentials are pre-provisioned Secrets referenced via `existingSecret` — removes the biggest post-exploitation primitive from the operator's trust boundary (#761) | `true`                                       |
| `leaderElection.enabled`              | Pass `--leader-elect` to the manager and create a leader-election RoleBinding. Default is now `true` so multi-replica rollouts are safe by default                                                                                                                                                                                                           | `true`                                       |
| `metrics.enabled`                     | Expose controller-runtime metrics and create a ClusterIP Service for them                                                                                                                                                                                                                                                                                    | `false`                                      |
| `metrics.port`                        | Metrics port                                                                                                                                                                                                                                                                                                                                                 | `8443`                                       |
| `metrics.secure`                      | Serve metrics over HTTPS (self-signed unless cert-manager is wired in)                                                                                                                                                                                                                                                                                       | `true`                                       |
| `serviceMonitor.enabled`              | Create a Prometheus Operator `ServiceMonitor` for metrics auto-discovery (requires `metrics.enabled=true` and the Prometheus Operator CRDs installed in the cluster)                                                                                                                                                                                         | `false`                                      |
| `serviceMonitor.scrapeInterval`       | Scrape interval for the ServiceMonitor endpoint                                                                                                                                                                                                                                                                                                              | `30s`                                        |
| `serviceMonitor.scrapeTimeout`        | Scrape timeout for the ServiceMonitor endpoint                                                                                                                                                                                                                                                                                                               | `10s`                                        |
| `serviceMonitor.labels`               | Extra labels merged into the ServiceMonitor (e.g. `release: kube-prometheus-stack` for Prometheus Operator's selector)                                                                                                                                                                                                                                       | `{}`                                         |
| `serviceMonitor.tlsConfig`            | TLS config for the scrape (used only when `metrics.secure=true`); defaults to `insecureSkipVerify: true` for the manager's self-signed cert                                                                                                                                                                                                                  | see `values.yaml`                            |
| `probes.port`                         | Health/liveness probe bind address port                                                                                                                                                                                                                                                                                                                      | `8081`                                       |
| `serviceAccount.create`               | Create a ServiceAccount for the controller                                                                                                                                                                                                                                                                                                                   | `true`                                       |
| `serviceAccount.automount`            | Automount the SA token                                                                                                                                                                                                                                                                                                                                       | `true`                                       |
| `serviceAccount.annotations`          | Annotations to add to the ServiceAccount                                                                                                                                                                                                                                                                                                                     | `{}`                                         |
| `serviceAccount.name`                 | Use a pre-existing ServiceAccount name (auto-derived when empty)                                                                                                                                                                                                                                                                                             | `""`                                         |
| `podAnnotations`                      | Annotations to add to the manager pod                                                                                                                                                                                                                                                                                                                        | `{}`                                         |
| `podLabels`                           | Labels to add to the manager pod                                                                                                                                                                                                                                                                                                                             | `{}`                                         |
| `podSecurityContext`                  | Pod-level securityContext (defaults satisfy PSS "restricted")                                                                                                                                                                                                                                                                                                | see `values.yaml`                            |
| `securityContext`                     | Container-level securityContext                                                                                                                                                                                                                                                                                                                              | `allowPrivilegeEscalation: false` + drop ALL |
| `resources`                           | CPU/memory requests and limits for the manager container                                                                                                                                                                                                                                                                                                     | 10m/64Mi requests, 500m/128Mi limits         |
| `extraArgs`                           | Additional command-line flags passed to `/manager`. Pair with `rbac.scope=namespace` by adding `--watch-namespaces=<csv>` so controller-runtime's cache matches the RBAC blast radius (#532)                                                                                                                                                                 | `[]`                                         |
| `terminationGracePeriodSeconds`       | Pod termination grace period. Must be > `preStop.delaySeconds` so SIGTERM fires with enough remaining time for controller-runtime's graceful shutdown (leader-lease release, in-flight reconcile drain) before SIGKILL (#512)                                                                                                                                | `30`                                         |
| `preStop.enabled`                     | Add a `lifecycle.preStop` sleep on the manager container so in-flight reconciliations drain before SIGTERM (#465)                                                                                                                                                                                                                                            | `false`                                      |
| `preStop.delaySeconds`                | preStop sleep duration in seconds. Keep strictly less than `terminationGracePeriodSeconds`                                                                                                                                                                                                                                                                   | `5`                                          |
| `nodeSelector`                        | Node selector for the controller pod                                                                                                                                                                                                                                                                                                                         | `{}`                                         |
| `tolerations`                         | Tolerations for the controller pod                                                                                                                                                                                                                                                                                                                           | `[]`                                         |
| `affinity`                            | Affinity rules for the controller pod                                                                                                                                                                                                                                                                                                                        | `{}`                                         |
| `topologySpreadConstraints`           | Pod-level topologySpreadConstraints on the controller pod. Parity with the witwave chart (#1119). Useful for spreading leader-elected replicas across zones.                                                                                                                                                                                                 | `[]`                                         |
| `priorityClassName`                   | PriorityClassName on the controller pod (#1119). Set to `system-cluster-critical` (or your equivalent) so the operator isn't evicted first under node pressure — it reconciles every WitwaveAgent.                                                                                                                                                           | `""`                                         |
| `webhooks.enabled`                    | Render the admission webhook Service, Mutating/Validating webhook configs, and cert-manager resources. Requires cert-manager installed in the cluster (#624)                                                                                                                                                                                                 | `false`                                      |
| `webhooks.port`                       | Container port for the webhook server (matches controller-runtime default)                                                                                                                                                                                                                                                                                   | `9443`                                       |
| `webhooks.certDir`                    | Directory the cert-manager-issued TLS pair is mounted at inside the container                                                                                                                                                                                                                                                                                | `/tmp/k8s-webhook-server/serving-certs`      |
| `webhooks.failurePolicy`              | Webhook failure policy. `Fail` rejects admission on error; `Ignore` allows it. Safer default for invariant checks                                                                                                                                                                                                                                            | `Fail`                                       |
| `webhooks.certManager.enabled`        | cert-manager integration toggle. One of two mutually exclusive TLS modes for `webhooks.enabled=true` — leave `true` for cert-manager-issued certs, or set `false` and supply `webhooks.existingSecret` + `webhooks.caBundle` for BYO-cert mode (see "TLS mode 2: BYO Secret" below)                                                                          | `true`                                       |
| `webhooks.certManager.createIssuer`   | Render a chart-owned selfSigned Issuer. Ignored when `webhooks.certManager.issuerRef.name` is set                                                                                                                                                                                                                                                            | `true`                                       |
| `webhooks.certManager.issuerKind`     | Kind of Issuer to render when `createIssuer=true`. `Issuer` or `ClusterIssuer`                                                                                                                                                                                                                                                                               | `Issuer`                                     |
| `webhooks.certManager.issuerRef.name` | Name of an external (Cluster)Issuer to use instead of a chart-owned one                                                                                                                                                                                                                                                                                      | `""`                                         |
| `webhooks.certManager.issuerRef.kind` | Kind of the external issuer                                                                                                                                                                                                                                                                                                                                  | `""`                                         |
| `vpa.enabled`                         | Render a `VerticalPodAutoscaler` for the operator Deployment (#1120). Requires the VerticalPodAutoscaler CRD from the kubernetes-sigs/autoscaler addon — the chart does **not** install it. Template is inert until the CRD is present.                                                                                                                      | `false`                                      |
| `vpa.updateMode`                      | VPA update mode. `Off` (recommend-only, default), `Initial`, `Recreate`, or `Auto`.                                                                                                                                                                                                                                                                          | `"Off"`                                      |
| `vpa.resourcePolicy`                  | Passthrough for `spec.resourcePolicy` — scope the VPA via `containerPolicies`.                                                                                                                                                                                                                                                                               | `{}`                                         |

## Admission webhook (#624)

When `webhooks.enabled=true`, the chart renders a webhook `Service`, `MutatingWebhookConfiguration`, and
`ValidatingWebhookConfiguration` resources. The controller-manager needs a TLS serving cert; the chart supports two
modes.

### TLS mode 1: cert-manager (default)

```yaml
webhooks:
  enabled: true
  certManager:
    enabled: true
    createIssuer: true
```

The chart renders a cert-manager `Certificate` + selfSigned `Issuer` (or references an external `Issuer` /
`ClusterIssuer` via `certManager.issuerRef`). The CA bundle is auto-injected into the webhook configs via the
`cert-manager.io/inject-ca-from` annotation — no base64 blobs in values.

### TLS mode 2: BYO Secret

For air-gapped clusters, service-mesh-managed TLS, existing Vault PKI, or any environment where cert-manager isn't
appropriate:

```yaml
webhooks:
  enabled: true
  existingSecret: my-webhook-serving-cert # Secret pre-created with tls.crt + tls.key
  caBundle: LS0tLS1CRUdJTi... # base64 of the PEM CA that signed tls.crt
  certManager:
    enabled: false
```

Pre-create the Secret:

```bash
# assuming you have ca.crt, tls.crt, tls.key on disk signed by your CA
kubectl create secret generic my-webhook-serving-cert \
  -n witwave-operator-system \
  --from-file=tls.crt=./tls.crt \
  --from-file=tls.key=./tls.key

# then encode the CA for the caBundle value
base64 -w0 < ca.crt
```

When `existingSecret` is set, the chart skips all cert-manager integration (no `Certificate`, no `Issuer`, no
CA-injection annotation) and stamps the user-supplied `caBundle` literally onto the webhook configs' `caBundle` field.
The DNS SANs on `tls.crt` must include `<release>-webhook.<namespace>.svc` and
`<release>-webhook.<namespace>.svc.cluster.local` or the apiserver will refuse to call the webhook.

### Invariants enforced

The mutating webhook (`mwitwaveagent.kb.io`) carries one defaulting rule (populate `spec.port=8000` when unset). The
`ValidatingWebhookConfiguration` carries one entry per CRD: `vwitwaveagent.kb.io`, `vwitwaveprompt.kb.io`, and
`vwitwaveworkspace.kb.io` (#1760). All three validating entries share the `webhooks.validatingFailurePolicy` knob
(default `Ignore`); flip to `Fail` for strict admission once your webhook is HA.

The `vwitwaveworkspace.kb.io` entry rejects: `volumes[].storageType: hostPath` (reserved for v1.x),
`volumes[].accessMode` outside `{ReadWriteMany, ReadWriteOnce, ReadWriteOncePod}` (RWM is the cross-node default; RWO is
the single-node fallback for clusters whose default storage class is RWO-only, e.g. Docker Desktop), `secrets[]` entries
that set both `mountPath` and `envFrom`, `configFiles[]` entries that set neither `configMap` nor `inline` (or both),
and mount-path collisions across the three lists. Further invariants land as follow-up gaps on top of this skeleton.

### Values shape

Mirrors the primary `witwave` chart's cert-manager block (#639) so operators running both charts side-by-side see
identical knobs for `certManager.enabled`, `createIssuer`, `issuerKind`, and `issuerRef.{name,kind}`.

### Disabled (default)

`webhooks.enabled=false` skips every webhook resource entirely; the controller runs without admission webhooks and
`cmd/main.go` logs a note at startup. CR validation falls back to CRD structural-schema checks only.

## Least-privilege Secret writes (#761)

The operator's default RBAC includes Secret write verbs because the inline `BackendSpec.credentials.secrets` path asks
the reconciler to create/update a chart-owned Secret per backend. The `WitwaveWorkspace` CRD's `secrets[]` field, by
contrast, accepts only existing-Secret references — the witwaveworkspace controller stays out of the secrets-write trust
boundary by design (#1760). If every agent in your cluster also uses pre-provisioned Secrets referenced via
`existingSecret`, drop the write verbs:

```yaml
rbac:
  secretsWrite: false
```

Read verbs (`get`/`list`/`watch`) remain granted so the reconciler can still validate referenced Secrets exist.

### ⚠ Inline credentials + `secretsWrite: false` is an incompatible combination

When `rbac.secretsWrite: false` is set AND a `WitwaveAgent` CR carries inline credential literals under
`spec.backends[*].credentials.secrets`, the reconciler tries to `Secrets.Create`, hits a 403, and retries per
controller-runtime's normal backoff — **silently**. The CR stays at `status.phase != Ready` indefinitely; no Kubernetes
Event surfaces the cause at apply-time today. Surfacing this at apply-time is tracked in
[#1461](https://github.com/witwave-ai/witwave/issues/1461).

To avoid the footgun, always pair `secretsWrite: false` with `existingSecret` references:

```yaml
# WitwaveAgent CR — pre-provisioned Secret path
spec:
  backends:
    - name: claude
      credentials:
        existingSecret:
          name: my-prepopulated-anthropic-secret
          keys:
            ANTHROPIC_API_KEY: api_key
    # The echo backend needs no credentials at all — it ships with the
    # chart (ghcr.io/witwave-ai/images/echo) and returns a canned response
    # quoting the caller's prompt. Useful alongside a real backend as a
    # credential-free smoke-test surface. See backends/echo/README.md.
    - name: echo
      image:
        repository: ghcr.io/witwave-ai/images/echo
      port: 8001
```

And pre-create the Secret yourself (via `kubectl create secret`, an ExternalSecrets `SecretStore`, Vault, etc.) before
applying the CR.

If inline credentials are needed, flip `rbac.secretsWrite: true` — accepting the slightly wider trust boundary — and
re-install the operator chart.

## Namespace-scoped RBAC (#532)

By default the operator installs a ClusterRole + ClusterRoleBinding and watches `WitwaveAgent` resources cluster-wide.
For multi-tenant clusters or least-privilege rollouts, switch to namespace-scoped RBAC:

```yaml
rbac:
  scope: namespace
  watchNamespaces:
    - tenant-a
    - tenant-b

extraArgs:
  - --watch-namespaces=tenant-a,tenant-b
```

In namespace mode the chart renders a `Role` + `RoleBinding` pair per entry in `watchNamespaces` (falling back to the
release namespace when the list is empty) and **does not** create a `ClusterRole`/`ClusterRoleBinding`. Always pair it
with `--watch-namespaces` in `extraArgs` so controller-runtime's informer cache matches — otherwise the operator's
watches will hit RBAC errors the moment it tries to list outside the permitted namespaces.

## Constraining agent image patches (`agentImagePatchPolicy`)

The `agentLifecycle` Kubernetes-API-access preset grants an Agent-Resources agent `patch` on `witwaveagents` so it can
run `ww agent upgrade` against its peers. That verb is resource-scoped — it covers the whole spec. The
`agentImagePatchPolicy` block renders a cluster `ValidatingAdmissionPolicy` (+ binding) that narrows what the named
ServiceAccounts may change: only image tags/digests may move; image **repositories** are locked and backends may not be
added, removed, or renamed. Other writers (the operator, humans, CI) are unaffected — the policy's `matchConditions`
scope enforcement to the listed ServiceAccounts only.

Disabled by default, and scoped to nothing even if enabled (the SA list is environment-specific):

```yaml
agentImagePatchPolicy:
  enabled: true
  constrainedServiceAccounts:
    - system:serviceaccount:witwave-self:milo
```

The CEL is enforced by the apiserver, not by `helm template` — validate against a live cluster before relying on it.
With the `ww` CLI, apply it as a release-owned object that survives reinstalls:

```bash
ww operator upgrade -f operator-values.yaml      # values file carrying the block above
```

`ww operator upgrade` reuses previously-applied values, so the policy stays enforcing on later plain upgrades without
re-passing `-f`. If the policy already exists from an out-of-band `kubectl apply`, annotate it for Helm adoption first
(`meta.helm.sh/release-name`, `meta.helm.sh/release-namespace`, `app.kubernetes.io/managed-by=Helm`) so the upgrade
imports it in place rather than failing on the ownership conflict. See
[`.agents/self/bootstrap.md`](../../../../../../.agents/self/bootstrap.md) for the worked self-team runbook.

## Graceful shutdown (#465, #512)

`terminationGracePeriodSeconds` (default `30`) and the optional `preStop.delaySeconds` sleep are parameterised so the
manager has enough time to release its leader lease and drain in-flight reconciles before SIGKILL. Keep
`preStop.delaySeconds` strictly less than `terminationGracePeriodSeconds`.
