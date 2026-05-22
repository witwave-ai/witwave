# witwave

Helm chart for the witwave platform — witwave harness and backends (claude, openai, codex, gemini, echo), MCP tool
servers, and optional dashboard. Deploys one pod per named agent with the harness + backends + git-sync sidecars
colocated; MCP tools run as shared cluster-wide Deployments. Released to GHCR on every tag alongside `witwave-operator`.

## Prerequisites

- Kubernetes 1.25+
- Helm 3.10+

## Installation

Create the namespace:

```bash
kubectl create namespace witwave
```

If your images are in a private registry (e.g. GHCR), create an image pull secret:

```bash
kubectl create secret docker-registry ghcr-credentials \
  --docker-server=ghcr.io \
  --docker-username=<github-username> \
  --docker-password=<github-token> \
  --namespace witwave
```

Then reference it in your values:

```yaml
agents:
  - name: adam
    imagePullSecrets:
      - name: ghcr-credentials
```

Create backend secrets for each agent. The required keys depend on which backends you are deploying — see the backend
READMEs for details:

- [claude secrets](../../backends/claude/README.md#secrets) — `ANTHROPIC_API_KEY`
- [openai secrets](../../backends/openai/README.md#secrets) — `OPENAI_API_KEY`
- [gemini secrets](../../backends/gemini/README.md#secrets) — `GEMINI_API_KEY`
- [echo](../../backends/echo/README.md) — no secrets required (stub backend, no LLM)

Example for an agent named `adam` using the Claude backend:

```bash
kubectl create secret generic adam-claude-secrets \
  --from-literal=ANTHROPIC_API_KEY=sk-ant-... \
  --namespace witwave
```

Then reference the secret in your values:

```yaml
agents:
  - name: adam
    backends:
      - name: claude
        envFrom:
          - secretRef:
              name: adam-claude-secrets
```

Install the chart:

```bash
helm install witwave oci://ghcr.io/witwave-ai/charts/witwave --namespace witwave
```

Install a specific version:

```bash
helm install witwave oci://ghcr.io/witwave-ai/charts/witwave --version <X.Y.Z> --namespace witwave
```

## Uninstall

```bash
helm uninstall witwave --namespace witwave
```

PVCs survive by default (reclaim policy permitting) so conversation logs and session memory are preserved across a
`helm uninstall`.

## Migrating to the operator

The `charts/witwave-operator` controller path is an alternative way to deploy agents — same end-state (harness +
backends in a pod), different provisioning mechanism. The two paths use different resource naming conventions: this
chart produces `{release}-{agent}` Deployments, the operator produces bare `{agent}` Deployments. Running BOTH against
overlapping agent names in the same namespace produces doubled resources split-brained across the two controllers — pick
one.

Full migration procedure (PVC preservation, CR authoring, rollback notes) lives in
[`operator/README.md#migrating-from-the-agent-chart-chartswitwave`](../../operator/README.md#migrating-from-the-agent-chart-chartswitwave).

## Values

The authoritative reference is **[`values.yaml`](./values.yaml)** — every field has inline comments explaining its
purpose, default, and any interaction with other fields. `values.schema.json` validates shape at `helm template` /
`helm install --dry-run` time, so typos fail loudly.

**Top-level blocks to know:**

- `agents[]` — the primary axis. Each entry is one harness pod with one or more backend containers. Every agent has
  `name`, `image`, `port`, `env`, `envFrom`, `credentials`, `storage`, `resources`, `backends`, `gitSyncs`, plus
  scheduling (`nodeSelector`, `tolerations`, `affinity`, `topologySpreadConstraints`, `priorityClassName`) that can be
  set per-agent or globally at chart-root level (per-agent **replaces**, not merges).
- `dashboard.*` — dashboard deployment (disabled by default; `enabled: true` to deploy alongside agents).
- `mcpTools.<name>.*` — shared MCP tool Deployments (`kubernetes`, `helm`, `prometheus`). All disabled by default; each
  has its own `enabled`, `image.digest`, `rbac.{create,rules,clusterWide}`, `networkPolicy.ingress.*`, and scheduling
  fields.
- `ingress.*` + `certManager.*` + `ingress.auth.*` — dashboard Ingress. Ingress is default-closed; chart fails template
  render when `ingress.enabled=true` without one of: chart-managed auth, an explicit escape hatch, or a user-supplied
  auth annotation.
- `autoscaling.*`, `podDisruptionBudget.*`, `vpa.*`, `terminationGracePeriodSeconds`, `preStop.*` — reliability +
  scheduling. See the [Reliability](#reliability) section for why `replicas=1` is the intentional default.
- `probes.{startup,liveness,readiness}.*` — health-probe knobs; defaults in `values.yaml`.
- `metrics.*`, `serviceMonitor.*`, `podMonitor.*`, `grafanaDashboards.*`, `prometheusRule.*` — observability.
  `metrics.enabled` is the master toggle; every monitor resource is off by default (no hard dependency on the Prometheus
  Operator / kube-prometheus-stack CRDs).
- `observability.tracing.*` — OTel tracing across harness + backends + operator. See
  [Enabling distributed tracing](#enabling-distributed-tracing-634) for Jaeger / Tempo recipes.
- `networkPolicy.*` — per-pod NetworkPolicies. Off by default; `egressOpen: false` flips to explicit allow-list mode.
- `cors.*`, `storage.retainOnUninstall`, `podSecurity.readOnlyRootFilesystem`, `serviceMesh.*` — cross-cutting posture
  flags.
- `harnessEnv.*` — first-class env-var overrides for harness production tunables that do not need dedicated chart keys.
  Each non-empty key is rendered as a `name`/`value` env entry on every agent's harness container after the
  `CORS_ALLOW_ORIGINS` conditional and before per-agent `.env` (which still wins). Values that ship commented-out in
  `values.yaml` include the session stream, webhook retry, A2A retry/limit, proxy limit, and scheduler drain timeout
  knobs. See `values.yaml` for inline defaults + meaning of each.
- MCP-tool-specific env vars (`MCP_HELM_REPO_URL_ALLOWLIST`, `MCP_HELM_ALLOW_ANY_REPO`, `MCP_K8S_READ_SECRETS_DISABLED`,
  `MCP_PROM_MAX_RESPONSE_BYTES`) are documented under each `mcpTools.<name>.env` block; `mcpTools.<name>.env` renders
  them onto the corresponding tool deployment.
- `defaults.resources.{harness,backend,backends.<name>}` — chart-wide resource-request defaults that per-agent and
  per-backend overrides replace.

**Deploy multiple agents:**

```yaml
agents:
  - name: iris
  - name: nova
  - name: kira
```

## Reliability

### Single-replica default is intentional (#559)

Each agent's Deployment renders `replicas: 1` by default, and both `autoscaling.enabled` and
`podDisruptionBudget.enabled` default to `false`. This is deliberate — not an oversight:

- **harness runs singleton schedulers.** The harness owns the heartbeat scheduler, job scheduler, task scheduler,
  trigger handler, and continuation runner. Running two harness pods for the same agent would cause every scheduled item
  to fire twice.
- **Backends keep local session state.** Conversation logs and per-session memory live on a local volume inside each
  backend pod. Running two backend pods for the same agent can interleave writes.

The trade-off is that with `replicas=1` AND `podDisruptionBudget.enabled=false`, every voluntary disruption
(`kubectl drain`, node upgrade, cluster-autoscaler scale-down) immediately drops the pod. During the ~30s gap until the
replacement is Ready: scheduled jobs and heartbeats miss their fire windows, inbound triggers return 503, in-flight A2A
calls return 5xx, and webhook deliveries are lost.

The chart emits a helm NOTES warning after `helm install`/`helm upgrade` whenever an agent is deployed with replicas=1
AND both HPA and PDB disabled.

### HA opt-in path

The safest production configuration keeps the singleton topology but adds a PodDisruptionBudget:

```yaml
podDisruptionBudget:
  enabled: true
  minAvailable: 1
```

With `replicas=1` this blocks `kubectl drain` until a replacement pod is Ready on another node — instead of dropping the
pod immediately. The drain takes longer, but there is no service gap.

**Do not simply raise `replicas > 1` or `autoscaling.minReplicas >= 2`** — the harness's singleton schedulers and the
backends' local session state are not yet safe for multi-replica operation. A future change will externalize scheduler
state and session storage so the chart can move to a true HA topology.

## Credentials for gitSync + backends

Every `agents[].gitSyncs[]` and `agents[].backends[]` entry supports a three-way `credentials` block with identical
shape between the two. Pick the path that matches your environment:

```yaml
# Chart-global defaults — inherited when per-entry credentials are unset.
gitSync:
  credentials:
    existingSecret: ""
    username: ""
    token: ""
    acknowledgeInsecureInline: false

backends:
  credentials:
    existingSecret: ""
    secrets: {} # map of env-var-name → value
    acknowledgeInsecureInline: false

agents:
  - name: bob
    gitSyncs:
      - name: witwave
        repo: https://github.com/org/repo
        # Per-entry override (omit to inherit chart-global default).
        credentials:
          existingSecret: bob-github-pat # OR inline below, not both.
    backends:
      - name: claude
        credentials:
          secrets:
            CLAUDE_CODE_OAUTH_TOKEN: "sk-ant-oat-xxxxxxxxxxxx"
          acknowledgeInsecureInline: true
```

**Three modes (mutually exclusive, in precedence order):**

1. **`existingSecret`** — reference a Secret you (or a CI pipeline) pre-created in the release namespace. The chart
   emits `envFrom: - secretRef: name: <existingSecret>`. Recommended for production; tokens never touch helm release
   state or values files.

2. **Inline values** (`username` + `token` for gitSync, `secrets: {}` map for backends) — chart auto-renders a Secret
   named `<release>-<agent>-<entry>-{gitsync,backend}-credentials` and wires envFrom. Dev-friendly: a single `--set`
   flag sourced from `.env` sets everything up. **Must** also set `acknowledgeInsecureInline: true` or the chart aborts
   template render with a pointed warning — inline tokens land in etcd release state, `helm get values`, and
   `kubectl describe`. Use this only for short-lived local/dev installs.

3. **Empty (default)** — no auth envFrom rendered. gitSync runs anonymously (fine for public repos); backends start but
   will fail on first LLM call.

**Legacy `envFrom:` escape hatch** remains supported on every entry for custom auth setups (SSH-key secrets, multiple
secrets merged, ConfigMaps) that the `credentials:` block doesn't cover. When both `credentials:` and `envFrom:` are set
on the same entry, `credentials:` wins.

**Do not embed credentials in the repo URL** (#1077). The chart rejects any `gitSyncs[].repo` that matches
`^https?://[^/]*:[^/]*@` (e.g. `https://user:token@github.com/...`) at render time. Token-bearing URLs get persisted in
the pod spec, the helm release Secret (`helm get values`, `sh.helm.release.v1.*`), apiserver audit logs, and every
`kubectl get pod -oyaml`. The chart also moves `--repo` off the initContainer / sidecar args onto a `GITSYNC_REPO`
environment variable for the same reason — operators add secret-scrubbing to env dumps far more reliably than to
arbitrary positional flags.

**Release-state leak on inline credentials.** Even with `acknowledgeInsecureInline: true`, token values are captured
into the `sh.helm.release.v1.<release>.v<N>` Secret Helm writes to etcd — the rendered Secret object is part of the
release manifest and `helm get values` will echo the inline token back. For anything beyond short-lived local/dev work,
prefer the `existingSecret` path.

### Installing with credentials from a SOPS-loaded shell

Helm does not decrypt SOPS files itself, so run `helm upgrade` from a process with the needed secret values already in
the environment. For the repo-managed test credentials, use the SOPS dotenv helper:

```bash
mise exec -- scripts/sops-exec-env.py .agents/test/team.sops.env -- \
  helm upgrade --install witwave-dev ./charts/witwave \
  -f ./my-values.yaml \
  --set-string gitSync.credentials.username="$GITSYNC_USERNAME" \
  --set-string gitSync.credentials.token="$GITSYNC_PASSWORD" \
  --set     gitSync.credentials.acknowledgeInsecureInline=true \
  --set-string backends.credentials.secrets.CLAUDE_CODE_OAUTH_TOKEN="$CLAUDE_CODE_OAUTH_TOKEN" \
  --set     backends.credentials.acknowledgeInsecureInline=true \
  -n witwave-dev --create-namespace
```

Use `--set-string` on any value that might parse as a number / boolean to avoid type coercion (`--set x=01234` becomes
an int). Per-agent or per-entry overrides use dot-paths like `--set agents[0].backends[0].credentials.secrets.FOO=bar`.

## Security

This chart is default-closed. The posture is:

- **Dashboard Ingress fails template render when `ingress.enabled=true` without an auth mechanism.** Either
  chart-managed basic auth (`ingress.auth.enabled=true`, the default — supply `ingress.auth.basic.existingSecret` or
  `ingress.auth.basic.htpasswd`), an explicit escape hatch (`ingress.auth.allowInsecure=true` for isolated clusters with
  a separate auth gateway), or a user-supplied auth annotation (`nginx.ingress.kubernetes.io/auth-url`, `auth-signin`,
  or a traefik middleware). Generate basic-auth entries via `htpasswd -nbB admin 'strong-password'`.
- **Pod Security Standards "restricted"**: every pod sets `seccompProfile: RuntimeDefault` and `runAsNonRoot: true`.
  `podSecurity.readOnlyRootFilesystem: true` narrows post-exploit blast radius; flip it on with appropriate `emptyDir`
  scratch mounts.
- **Bearer-token auth on every protected backend endpoint.** `CONVERSATIONS_AUTH_TOKEN` guards `/conversations` /
  `/trace` / `/mcp` / `/api/traces` / `/events/stream` / per-session streams. `CONVERSATIONS_AUTH_DISABLED=true` is the
  documented escape hatch for local dev.
- **MCP tool containers enforce their own bearer** via `MCP_TOOL_AUTH_TOKEN` (or `MCP_TOOL_AUTH_DISABLED=true` for local
  dev).
- **MCP command + cwd allow-list**: `MCP_ALLOWED_COMMANDS` / `MCP_ALLOWED_COMMAND_PREFIXES` / `MCP_ALLOWED_CWD_PREFIXES`
  on each backend; rejections count on `backend_mcp_command_rejected_total{reason}`.
- **Optional NetworkPolicy** (`networkPolicy.enabled: true`) renders one policy per chart-rendered pod. Default ingress
  when enabled: monitoring namespace on metrics port + in-release dashboard on app ports. Tune via
  `networkPolicy.ingress.{allowDashboard, allowSameNamespace, metricsFrom, additionalFrom}`. Egress stays open by
  default; `networkPolicy.egressOpen: false` flips to explicit-allow-list mode.
- **A2A ingress cap**: `A2A_MAX_PROMPT_BYTES` (default 1 MiB) — the harness rejects oversized A2A prompts before they
  reach a backend. `0` disables.

For the full convention surface (auth model, redaction, session-id binding + rotation, operator RBAC), see `AGENTS.md` →
"Conventions".

## MCP tool Deployments

The chart renders one `Deployment` + `Service` per entry in `mcpTools` that has `enabled: true`. Each tool container
listens on port **`8000`** using FastMCP's `streamable-http` transport, so backends in other pods can reach it by
Service URL (`http://<release>-mcp-<tool>:8000`) without needing a stdio fork/exec.

```yaml
mcpTools:
  kubernetes:
    enabled: true
    image:
      # Pin immutably in production (#855) — MCP pods hold a cluster SA token.
      digest: sha256:abc123...
    # Three-state (#856); set false for IRSA / workload-identity setups.
    # automountServiceAccountToken: true
    serviceAccountName: mcp-kubernetes # BYO SA with cluster-read RBAC
  helm:
    enabled: true
    serviceAccountName: mcp-helm # BYO SA with helm-release RBAC
```

In each agent's `.claude/mcp.json` / `.openai/mcp.json` / `.codex/mcp.json` / `.gemini/mcp.json`, reference the tools by
URL:

```json
{
  "mcpServers": {
    "kubernetes": { "url": "http://<release>-mcp-kubernetes:8000" },
    "helm": { "url": "http://<release>-mcp-helm:8000" }
  }
}
```

The chart renders a minimal default `ServiceAccount` + `ClusterRole` + `ClusterRoleBinding` per MCP tool whenever
`mcpTools.<name>.rbac.create: true` (the default; see `values.yaml` for the baseline `rules`). If you prefer to manage
RBAC out-of-band (e.g. central security team, out-of-cluster IAM, or a reduced verb surface), set `rbac.create: false`,
provide `serviceAccountName: <your-SA>`, and apply the ready-to-use samples in
[`samples/mcp-kubernetes-rbac.yaml`](samples/mcp-kubernetes-rbac.yaml) and
[`samples/mcp-helm-rbac.yaml`](samples/mcp-helm-rbac.yaml) — both are least-privilege starting points that mirror the
chart's in-tree baseline.

Disabled by default. Leave `mcpTools.<name>.enabled: false` (or omit the entry entirely) to skip rendering; backends
configured without the URL just don't call the tool.

## Enabling distributed tracing (#634)

End-to-end OpenTelemetry tracing across harness + backends + operator is opt-in. The pod-side OTel bootstraps
(`shared/otel.py` for Python, `operator/internal/tracing/otel.go` for Go) have shipped since #469/#471 — this chart owns
the env-var wiring.

**This chart does not deploy an OpenTelemetry Collector.** Matching the idiomatic pattern across Strimzi, cert-manager,
Istio, Elastic ECK, Knative, Argo, Crossplane, and grafana-operator, witwave emits OTLP to a user-provided endpoint and
delegates collector deployment to something built for that job.

> **Note:** Earlier chart versions (pre-`0.3.x`) rendered an in-release OTel Collector Deployment + Service + ConfigMap
> gated on `observability.tracing.collector.enabled`. That path was removed — the corresponding values keys
> (`observability.tracing.collector.*`) are gone. Point `observability.tracing.endpoint` at an
> opentelemetry-operator-managed collector or a direct OTLP backend instead.

Options:

- **Recommended:** install the [opentelemetry-operator](https://github.com/open-telemetry/opentelemetry-operator) and
  create an `OpenTelemetryCollector` CR. Point `observability.tracing.endpoint` at the resulting Service.
- **Alternative:** point `observability.tracing.endpoint` at any OTLP-compatible backend directly — Jaeger, Tempo,
  Honeycomb, Grafana Cloud, Datadog, etc.

### Quick start — wire to a collector

```yaml
observability:
  tracing:
    enabled: true
    endpoint: http://otel-collector.observability:4318 # OTLP/HTTP
    # or http://otel-collector.observability:4317 for OTLP/gRPC
    sampler: parentbased_traceidratio
    samplerArg: "0.1" # 10% sampling
```

With that:

- Every harness and backend pod receives `OTEL_ENABLED=true`, `OTEL_EXPORTER_OTLP_ENDPOINT`, and a per-component
  `OTEL_SERVICE_NAME`.
- `OTEL_TRACES_SAMPLER` / `OTEL_TRACES_SAMPLER_ARG` are forwarded verbatim when set.

### Wiring the operator

The `witwave-operator` chart exposes a matching `observability.tracing` block. Point both at the same endpoint to trace
the reconciler alongside the agents:

```yaml
# values for witwave-operator
observability:
  tracing:
    enabled: true
    endpoint: http://otel-collector.observability:4318
```

See `operator/internal/tracing/otel.go` for the full list of OTel env vars the operator honours — the chart forwards the
standard subset (`OTEL_ENABLED`, `OTEL_EXPORTER_OTLP_ENDPOINT`, `OTEL_TRACES_SAMPLER`, `OTEL_TRACES_SAMPLER_ARG`,
`OTEL_SERVICE_NAME`).
