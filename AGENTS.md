# AGENTS.md

This file provides guidance to Claude Code (<https://claude.ai/code>) and Codex (<https://openai.com/codex/>) when
working with code in this repository.

## Repo Root

The repo root is referred to as `<repo-root>`. For this environment, `<repo-root>` is the directory containing this
file.

## Skills

Backend-local skill documents can live under `.claude/skills/` for Claude, `.openai/skills/` for the OpenAI backend, and
`.codex/skills/` for the Codex backend, but cross-backend behavior that must work on Claude, OpenAI, Codex, and Gemini
should be encoded in the backend's primary identity document: `CLAUDE.md`, `AGENTS.md`, or `GEMINI.md`. Those files are
the guaranteed loaded instruction surface for each backend. Gemini currently persists session history under
`.gemini/memory/`, but this backend does not have an equivalent skill-folder convention.

## Agent Identity

The acting agent is referred to as `<agent-name>`. For containerized workers, `<agent-name>` is the value of the
`AGENT_NAME` environment variable (e.g. `iris`, `nova`, `kira`). When running as a local session (Claude Code, Codex, or
otherwise), `AGENT_NAME` is not set — in that case, `<agent-name>` is `local-agent`.

## Development style

This repo uses **trunk-based development**: changes land directly on `main`, not via feature branches and PRs. The
working assumption is "main is always shippable" — small atomic commits, fast feedback, and immediate revert when
something breaks. Background: paulhammant.com/what-is-trunk-based-development.html; corroborated by DORA / _Accelerate_
as a high-performance practice.

- **Commit directly to `main`.** No feature branch, no PR, no review queue. Push when the work is ready.
- **Keep commits small and atomic.** Each commit should stand alone — bisectable, revertable, with a focused message. If
  a change naturally splits (e.g. a refactor and a follow-up doc), make it two commits.
- **Branches are an exception, not the default.** Reach for one only when the change is genuinely too large or risky to
  land in a single atomic commit (e.g. a cross-cutting rename touching dozens of files). When a branch is justified,
  keep it short-lived (hours, not days), merge fast-forward into `main`, and delete it. Long-lived branches and
  review-gated PR queues are deliberately not part of the workflow here.
- **If you break `main`, fix or revert immediately.** That's the contract that lets the no-PR style work — the next
  commit must not inherit a broken tree. Tests are the primary gate; run them locally before pushing.

For AI coding assistants (Claude Code, Codex):

- Do not run `git commit` unless explicitly asked.
- Do not run `git push` unless explicitly asked.
- When asked to land work, default to a direct commit on `main`. Only suggest a branch when the change is genuinely too
  large to land atomically — and even then, propose it before creating it.

## Project Overview

Witwave is a multi-container autonomous agent platform. Each named agent (iris, nova, kira, …) consists of:

- A **harness** container — the infrastructure layer (A2A relay, heartbeat scheduler, job scheduler). It owns no LLM
  itself; it forwards all work to a backend.
- One or more **backend** containers (`claude`, `openai`, `codex`, `gemini`) — the LLM execution layer. Each backend is
  a full A2A server that manages its own sessions, memory, conversation logs, and Prometheus metrics.
- Zero or more **MCP** containers (`mcp-kubernetes`, `mcp-helm`, …) — the tool layer. Each MCP server exposes a focused
  set of cluster- or system-level capabilities to backends over the Model Context Protocol. All entries under `tools/`
  are equal MCP components; backends opt into them via their own MCP configuration.

Multiple named agents can collaborate as a team via the A2A protocol, but the named Witwave agent (harness + its
backends) is the deployable unit. MCP components are shared infrastructure — one deployment typically serves every agent
in the cluster rather than being replicated per agent.

## Architecture

### harness (router / scheduler)

Each named agent runs a containerized instance of the `harness` image. harness is the infrastructure layer:

- **A2A relay** — receives external A2A requests and forwards them to the configured backend; returns the backend
  response verbatim.
- **Heartbeat scheduler** — fires on the schedule defined in `HEARTBEAT.md`; dispatches the heartbeat prompt to the
  configured backend.
- **Job scheduler** — reads `jobs/*.md` files with cron frontmatter; dispatches triggered items to the configured
  backend.
- **Task scheduler** — reads `tasks/*.md` files with calendar frontmatter (days, time window, date range); dispatches
  triggered items to the configured backend.
- **Trigger handler** — serves `POST /triggers/{endpoint}` HTTP endpoints defined in `triggers/*.md` files; dispatches
  the request payload as a prompt to the configured backend and returns 202 immediately.
- **Continuation runner** — reads `continuations/*.md` files; fires a follow-up prompt whenever a named upstream (job,
  task, trigger, a2a, or another continuation) completes, enabling prompt chaining without hardcoded sequences.
- **Router** — reads `backend.yaml` to decide which named backend handles each concern (a2a, heartbeat, job, task,
  trigger, continuation).

Prompts can land in `.witwave/{jobs,tasks,triggers,continuations,webhooks}/` (or at `HEARTBEAT.md`) via two paths:

1. **gitSync materialisation** — a gitSync sidecar rsyncs `.md` files from a repo.
2. **`WitwavePrompt` CR** (operator-only) — declarative Kubernetes resource that binds one prompt to one or more
   `WitwaveAgent`s; the operator reconciles a ConfigMap per `(WitwavePrompt, agent)` pair that mounts at the same path.
   See `operator/README.md#the-witwaveprompt-resource` and
   `operator/config/samples/witwave_v1alpha1_witwaveprompt.yaml`.

A third operator CRD, `WitwaveWorkspace`, is parallel to `WitwaveAgent` and `WitwavePrompt`: it provisions shared
volumes, projects pre-created Secrets, and renders ConfigMap-backed files that get stamped onto every agent whose
`Spec.WorkspaceRefs` references it. Membership is agent-owned (the agent declares `Spec.WorkspaceRefs[]`; the workspace
controller maintains `Status.BoundAgents` as an inverted index). See `operator/README.md#the-witwaveworkspace-resource`
and `operator/api/v1alpha1/witwaveworkspace_types.go`.

harness retains no LLM of its own. All conversation state, session continuity, memory, and conversation logging live in
the backend container.

Every container in the stack exposes `/metrics` on a **dedicated port** (9000 by default, set via `METRICS_PORT` env /
`metrics.port` chart value / `WitwaveAgentSpec.MetricsPort` CRD field) separate from the app listener (#643). The split
lets NetworkPolicy and auth posture diverge between app traffic and monitoring scrapes. The shared helper
`shared/metrics_server.py` implements both the asyncio-task listener (harness + backends) and the daemon-thread variant
(FastMCP-hosted MCP tools).

### Backend containers

Five backend types exist, each implemented as a standalone A2A server:

- **`claude`** — Claude Agent SDK backend. Source in `backends/claude/`. Image: `claude:latest`.
- **`openai`** — OpenAI Agents SDK backend. Source in `backends/openai/`. Image: `openai:latest`.
- **`codex`** — Codex-native Node.js backend for Codex-optimized coding-agent execution through the Responses API.
  Source in `backends/codex/`. Image: `codex:latest`.
- **`gemini`** — Google Gemini backend (google-genai SDK). Source in `backends/gemini/`. Image: `gemini:latest`.
- **`echo`** — zero-dependency stub backend that returns a canned response. Ships as the default for `ww agent create`
  hello-world onboarding; no API keys required. Also used for harness regression tests and CI smoke tests. Source in
  `backends/echo/`. Image: `echo:latest`. Deliberately minimal: A2A + health/metrics plumbing only — no MCP, no
  conversation persistence, no hooks, no session binding. See `backends/echo/README.md` for the intentional-non-scope
  list.

Each backend:

- Exposes `/.well-known/agent.json` for A2A discovery
- Exposes `/` as the A2A JSON-RPC task endpoint
- Exposes `/health` (liveness — 200 once the process is up, even while initialising) and `/health/ready` (readiness —
  503 while initialising or boot-degraded) per the cycle-1 #1608 + cycle-7 #1672 split. K8s `livenessProbe` should
  target `/health`; `readinessProbe` should target `/health/ready`.
- Exposes `/metrics` for Prometheus scraping (when `METRICS_ENABLED` is set)
- Exposes `/conversations`, `/trace`, `/mcp`, and `/api/traces[/<id>]` guarded by the same bearer token
  (`CONVERSATIONS_AUTH_TOKEN`) — parity across all LLM-backed backends (#510, #516, #518). An empty token logs a startup
  warning (#517) and the shared guard refuses to serve protected endpoints unless the operator opts in with
  `CONVERSATIONS_AUTH_DISABLED=true` (documented escape hatch for local dev, loud startup log)
- On `/mcp`, `session_id` is routed through `shared/session_binding.derive_session_id` with a bearer-token fingerprint
  before lookup/insert — parity across the LLM-backed backends (#867 claude, #929 openai, #935 gemini, #941 shared path;
  codex mirrors the same binding behavior in Node). When `SESSION_ID_SECRET` is set the bound ID is HMAC-derived from
  the caller identity so one caller cannot hijack another's session. Leave unset only in single-tenant dev
- Manages its own session state, conversation log (`conversation.jsonl`), and memory (`/memory/`)
- Receives behavioral instructions via a mounted file (`CLAUDE.md` for claude, `AGENTS.md` for openai/codex, `GEMINI.md`
  for gemini). Backend-specific `agent-card.md` files are optional; the named agent's public card is served by harness
  from `.witwave/agent-card.md`

Codex-owned function tools (`run_shell_command`, memory tools, and URL-shaped MCP tools) pass through a PreToolUse-style
gate before execution. The gate supports the shared baseline rule names plus optional `.codex/hooks.yaml` extension
rules and emits `backend_hooks_*` metrics. Claude remains the full SDK hook reference; Gemini still has an AFC
interposition gap for pre-tool enforcement.

Each named agent has its own dedicated backend instances. For example, iris can have `iris-claude`, `iris-openai`,
`iris-codex`, and `iris-gemini`.

The `backend-base` image (`images/backend-base/`, published as `ghcr.io/witwave-ai/images/backend-base:<version>`) is
the shared base for the Claude/OpenAI/Codex/Gemini backend images. Its job is image composition: common CLIs, runtimes,
and analyzers such as Go, Node, `kubectl`, `ww`, `gh`, Helm, ruff, shellcheck, hadolint, gitleaks, trivy, and test
tooling are version-pinned in one place instead of rebuilt independently in every backend. It does not grant
permissions. In-cluster Kubernetes authority is controlled by `WitwaveAgent.spec.kubernetesApiAccess` or an explicit
ServiceAccount/RBAC binding.

### MCP components

Tool capabilities are delivered as MCP servers. Every subdirectory under `tools/` is an MCP component and is treated
equally regardless of what it wraps. Current MCP components:

- **`mcp-kubernetes`** — Kubernetes API access via the official Python client. Source in `tools/kubernetes/`. Image:
  `mcp-kubernetes:latest`.
- **`mcp-helm`** — Helm release management via the `helm` CLI (Helm has no Python/REST API). Source in `tools/helm/`.
  Image: `mcp-helm:latest`.
- **`mcp-prometheus`** — PromQL query surface wrapping the standard Prometheus HTTP API (#853). Source in
  `tools/prometheus/`. Image: `mcp-prometheus:latest`. Grafana / Loki / OTel surfaces are tracked as follow-ups.

Each MCP component:

- Runs a long-lived HTTP server on port **`8000`** using FastMCP's `streamable-http` transport (#644). The container
  `EXPOSE`s 8000 and Kubernetes addresses it via a per-tool Service (`<release>-mcp-<tool>:8000`). A `/health` endpoint
  is also served on the same port.
- Enforces bearer-token auth via the shared `shared/mcp_auth.py` middleware: when `MCP_TOOL_AUTH_TOKEN` is unset or
  empty and `MCP_TOOL_AUTH_DISABLED` is not explicitly set, the server refuses requests. Set
  `MCP_TOOL_AUTH_DISABLED=true` to acknowledge no-auth mode for local dev (startup log is loud).
- Speaks the Model Context Protocol (not A2A) and is consumed by backends via their MCP configuration (`mcp.json` under
  `.claude/`, `.openai/`, `.codex/`, or `.gemini/` — all LLM-backed backends share the same wire format). Entries point
  at the tool's Service URL, not at a local binary:

  ```json
  {
    "mcpServers": {
      "kubernetes": { "url": "http://witwave-mcp-kubernetes:8000" }
    }
  }
  ```

  OpenAI additionally reads `.openai/config.toml` for built-in tool enablement flags; Codex keeps analogous
  `.codex/config.toml` files with agent identity/config mirrors. Those files are unrelated to MCP server wiring.

- Targets only the cluster where it is deployed; auth is in-cluster ServiceAccount + RBAC, not arbitrary kubeconfigs.
  The chart ships a least-privilege default ClusterRole/Binding per tool when `mcpTools.<name>.rbac.create: true`
  (default). Override with `mcpTools.<name>.serviceAccountName` to reuse an out-of-band SA. Pin the image immutably via
  `mcpTools.<name>.image.digest` in production (#855); toggle the in-pod projected SA token per-tool via
  `mcpTools.<name>.automountServiceAccountToken` (three-state, #856) to coexist with IRSA / workload-identity setups.
- Is independently deployable and **shared across all agents** in a cluster — one Deployment + Service per enabled tool.
  Opt in per tool in the chart's `mcpTools` block (`kubernetes.enabled: true`, `helm.enabled: true`); disabled by
  default.

Add a new MCP component by creating `tools/<name>/` with a `Dockerfile`, `server.py`, and `requirements.txt` (server
must call `mcp.run(transport="streamable-http", host="0.0.0.0", port=8000)`), add an `EXPOSE 8000` to the Dockerfile,
then register a `mcp-<name>:latest` build in the [Building Images](#building-images) section and a `mcpTools.<name>`
block in `charts/witwave/values.yaml`. Tag related issues/PRs with the `mcp` GitHub label.

### Routing configuration

`backend.yaml` (in `.witwave/`) controls which backend handles each concern:

```yaml
backend:
  agents:
    - id: claude
      url: http://localhost:8010
      model: claude-opus-4-7

    - id: openai
      url: http://localhost:8011
      model: gpt-5.5

    - id: codex
      url: http://localhost:8012
      model: gpt-5.5

    - id: gemini
      url: http://localhost:8013

  routing:
    default:
      agent: claude
      model: claude-opus-4-7
    a2a:
      agent: claude
      model: claude-opus-4-7
    heartbeat:
      agent: claude
      model: claude-opus-4-7
    job:
      agent: claude
      model: claude-opus-4-7
    task:
      agent: claude
      model: claude-opus-4-7
    trigger:
      agent: claude
      model: claude-opus-4-7
    continuation:
      agent: claude
      model: claude-opus-4-7
```

Routing values can be a plain agent ID string or an object with `agent:` and optional `model:` fields. Model resolution
order: per-message override → routing entry model → per-backend config model.

The `url` field can be overridden at deploy time via the environment variable `A2A_URL_<ID_UPPERCASED_WITH_UNDERSCORES>`
(e.g. `A2A_URL_IRIS_CLAUDE`). This enables the same config file to work across Kubernetes service DNS and
localhost-sidecar deployment shapes without modification.

### Agent configuration layout

Agent identity and behavior are file-based — nothing is baked into images.

```text
.agents/self/<name>/
├── agent.sops.env          # Encrypted per-agent secret mirror (SOPS; not mounted directly yet)
├── .witwave/                    # Runtime config (mounted into harness)
│   ├── agent-card.md        # A2A identity description exposed by the harness Service
│   ├── backend.yaml         # Backend selection and routing
│   ├── HEARTBEAT.md         # Proactive heartbeat schedule and prompt
│   ├── jobs/                # Scheduled job definitions (*.md with cron frontmatter)
│   ├── tasks/               # Scheduled task definitions (*.md with calendar frontmatter)
│   ├── triggers/            # Inbound HTTP trigger definitions (*.md with endpoint frontmatter)
│   ├── continuations/       # Continuation definitions (*.md with continues-after frontmatter)
│   └── webhooks/            # Outbound webhook subscriptions (*.md with url frontmatter)
├── .claude/                 # Claude backend config (mounted into claude)
│   ├── CLAUDE.md            # Behavioral instructions / system prompt
│   ├── mcp.json             # MCP server configuration
│   ├── hooks.yaml           # Optional PreToolUse/PostToolUse extension rules (#467)
│   ├── settings.json        # Claude Code settings
│   └── skills/              # Skill definitions (*.md)
├── .openai/                  # OpenAI backend config (mounted into openai)
│   ├── AGENTS.md            # Behavioral instructions / system prompt
│   └── config.toml
├── .codex/                  # Codex backend config (mounted into codex)
│   ├── AGENTS.md            # Behavioral instructions / system prompt
│   └── config.toml
├── .gemini/                 # Gemini backend config (mounted into gemini)
│   └── GEMINI.md            # Behavioral instructions / system prompt
├── logs/                    # harness logs (runtime, not committed)
├── claude/               # Claude backend instance for this agent
│   ├── logs/                # Backend conversation.jsonl + tool-activity.jsonl (runtime, not committed)
│   └── memory/              # Backend persistent memory (runtime, not committed)
├── openai/                # OpenAI backend instance for this agent
│   ├── logs/
│   └── memory/
├── codex/                # Codex backend instance for this agent
│   ├── logs/
│   └── memory/
└── gemini/               # Gemini backend instance for this agent
    ├── logs/
    └── memory/              # Includes sessions/ subdir for JSON session history
```

Backend-specific `agent-card.md` files are optional and only affect direct sidecar discovery on backend localhost ports.
The agent Service points at harness, so the public agent card for repo-managed self/test agents lives in
`.witwave/agent-card.md`.

## Project Structure

```text
.agents/
├── self/                    # Self agents; team.sops.env holds shared encrypted secrets
│   └── <name>/              # Per-agent directory (see layout above)
└── test/                    # Test agents; team.sops.env mirrors shared encrypted secrets
    └── <name>/

harness/                     # harness source (router/scheduler)
├── Dockerfile
├── main.py                  # A2A server entrypoint
├── executor.py              # Routes A2A requests to configured backend
├── bus.py                   # Internal async message bus (carries trace_context)
├── heartbeat.py             # Heartbeat scheduler
├── jobs.py                  # Job scheduler
├── tasks.py                 # Task scheduler
├── triggers.py              # Inbound HTTP trigger handler
├── continuations.py         # Continuation runner (fires on upstream completion)
├── webhooks.py              # Outbound webhook delivery (stamps traceparent + OTel span)
├── tracing.py               # W3C trace-context helpers + OTel re-exports (#468, #469)
├── metrics.py               # Prometheus metrics definitions
├── utils.py                 # Shared utilities (frontmatter parser, duration parser, etc.)
└── backends/
    ├── base.py              # AgentBackend abstract base class
    ├── a2a.py               # A2ABackend — forwards requests to remote A2A backend
    └── config.py            # Backend config loader (backend.yaml)

backends/claude/                   # Claude backend source
├── Dockerfile
├── main.py                  # A2A server entrypoint
├── executor.py              # Claude Agent SDK executor; owns sessions, logging, hooks (#467)
├── hooks.py                 # PreToolUse/PostToolUse policy engine + baseline deny rules (#467)
├── metrics.py               # Prometheus metrics (superset of backends/openai and backends/gemini; adds tool, context, MCP, hooks metrics)
└── requirements.txt

backends/openai/                    # OpenAI backend source
├── Dockerfile
├── main.py                  # A2A server entrypoint
├── executor.py              # OpenAI Agents SDK executor; owns sessions and logging
├── metrics.py               # Prometheus metrics (common a2_* set; subset of claude)
└── requirements.txt

backends/codex/                    # Codex backend source
├── Dockerfile
├── main.js                  # A2A server entrypoint
├── package.json             # Node runtime dependencies
└── README.md

backends/gemini/                   # Gemini backend source
├── Dockerfile
├── main.py                  # A2A server entrypoint
├── executor.py              # google-genai SDK executor; owns sessions and logging
├── metrics.py               # Prometheus metrics (common a2_* set; subset of claude)
└── requirements.txt

tools/                       # MCP components (one directory per server)
├── kubernetes/              # mcp-kubernetes — Kubernetes API via Python client
├── helm/                    # mcp-helm — Helm CLI wrapper + helm-diff
└── prometheus/              # mcp-prometheus — PromQL / series / labels queries

clients/                     # User-facing interfaces to the platform
├── dashboard/               # Vue 3 + Vite + PrimeVue web UI
└── ww/                      # Go + cobra CLI (ships via Homebrew tap)

charts/                      # Helm charts
├── witwave/                     # Witwave Helm chart (deploys agents to Kubernetes)
└── witwave-operator/            # witwave-operator Helm chart (deploys the WitwaveAgent controller)

operator/                    # Kubernetes operator (Go) — reconciles WitwaveAgent CRDs

shared/                      # Python modules shared across harness + backends + MCP tools
                             #   otel.py, metrics_server.py, log_utils.py,
                             #   hook_events.py, session_binding.py, redact.py,
                             #   event_schema.py, session_stream.py, …

docs/                        # Canonical reference docs
├── architecture.md          # High-level system diagram + component relationships
└── events/                  # Event stream wire contract (schema + envelope rules)
    ├── README.md
    └── events.schema.json
```

## Building Images

```bash
# harness (router/scheduler)
docker build -f harness/Dockerfile -t harness:latest .

# Backend base image (shared runtime/tooling image)
docker build -f images/backend-base/Dockerfile -t backend-base:latest .

# Claude backend
docker build --build-arg BACKEND_BASE_IMAGE=backend-base:latest -f backends/claude/Dockerfile -t claude:latest .

# OpenAI backend
docker build --build-arg BACKEND_BASE_IMAGE=backend-base:latest -f backends/openai/Dockerfile -t openai:latest .

# Codex backend
docker build --build-arg BACKEND_BASE_IMAGE=backend-base:latest -f backends/codex/Dockerfile -t codex:latest .

# Gemini backend
docker build --build-arg BACKEND_BASE_IMAGE=backend-base:latest -f backends/gemini/Dockerfile -t gemini:latest .

# Echo backend (hello-world default; no API keys required)
docker build -f backends/echo/Dockerfile -t echo:latest .

# Kubernetes MCP tool
docker build -f tools/kubernetes/Dockerfile -t mcp-kubernetes:latest .

# Helm MCP tool
docker build -f tools/helm/Dockerfile -t mcp-helm:latest .

# Prometheus MCP tool (#853)
docker build -f tools/prometheus/Dockerfile -t mcp-prometheus:latest .

# Dashboard image — optional; only built/pushed when dashboard.enabled=true in the chart.
docker build -f clients/dashboard/Dockerfile -t dashboard:latest .

# git-sync helper image (upstream git-sync + rsync). Built from the `helpers/`
# folder alongside any future pod-level helper images (sidecar or init).
docker build -f helpers/git-sync/Dockerfile -t git-sync:latest helpers/git-sync
```

## Running Locally

Use the CLI/operator path for disposable local test agents. The test-team bootstrap creates `bob` and `fred` as
`WitwaveAgent` resources from the files under `.agents/test/`.

```bash
ww operator install --yes
# Then follow the commands in .agents/test/bootstrap.md
```

## Installing the operator via ww

The `ww` CLI ships with the `witwave-operator` chart embedded (v0.5.0+) and is the recommended way to install the
operator. Lifecycle + diagnostics:

```bash
ww operator install              # into witwave-system by default
ww operator status               # release + pods + CRDs + CR counts
ww operator upgrade              # SSA CRDs → helm upgrade
ww operator uninstall            # CRDs + CRs preserved by default
ww operator logs                 # tail operator pod logs
ww operator events               # Kubernetes events for operator + CRs
```

The embedded chart is mirrored from `charts/witwave-operator/` via `scripts/sync-embedded-chart.sh` (CI-guarded drift
check). Release builds also run `scripts/bump-embedded-chart-version.sh` so the embedded chart's version + appVersion
match the release tag, not the canonical `0.1.0` placeholder on main.

See `clients/ww/README.md` for the full command surface including `--yes` / `--dry-run` / `--adopt` / `--delete-crds` +
the local-cluster prompt heuristic.

## Interacting with Agents

Use the `/remote` skill to interact with running agents. Always target the **Witwave agent by name** — Witwave routes
the request internally to its configured backend. Never target backend services directly.

| Shape                            | Harness                 | First backend           | Additional backends     | Metrics |
| -------------------------------- | ----------------------- | ----------------------- | ----------------------- | ------- |
| `ww` / operator default          | 8000                    | 8001                    | 8002..8050              | 9000    |
| historical host-forward examples | varies by local forward | varies by local forward | varies by local forward | 9000    |

Self-managing and test agents run in their own pods with their own localhost, so backend ports can be uniform within
each pod. When a local smoke spec wants stable laptop ports, port-forward the harness Service, for example
`kubectl port-forward svc/bob 8099:8000 -n witwave-test`.

Test agents `jack` (openai-only) and `luke` (gemini-only) exist as filesystem scaffolds under `.agents/test/` and can be
created with `ww agent create` when those parity surfaces are needed. Bob's OpenAI/Gemini directories remain parked
fixtures so they can be re-enabled deliberately once credentials and budget are available.

The `/remote` skill derives the session ID automatically from the current Claude Code session. Pass it explicitly only
when you need to target a specific session.

## Memory

Each backend manages its own memory under `.agents/<env>/<name>/<backend>/memory/` (e.g.
`.agents/self/iris/claude/memory/`). For `claude` and `openai`, memory files are markdown documents. The newer `codex`
scaffold exposes bounded memory tools rooted at `CODEX_MEMORY_ROOT` (default `/home/agent/.codex/memory`) and persists
Responses API `previous_response_id` mappings at `CODEX_SESSION_STORE_PATH` (default
`/home/agent/.codex/sessions/responses.json`). For `gemini`, conversation history is stored as JSON in
`memory/sessions/`. Memory files are not committed to source control. harness has no memory layer of its own.

Workspace-backed memory is separate from backend-local memory. When a `WitwaveWorkspace` declares a `memory` volume,
bound agents see it at `/workspaces/<workspace-name>/memory`; self-team identity docs use
`/workspaces/witwave-self/memory`, and the test identity docs mirror the same namespace/index contract under
`/workspaces/witwave-test/memory`.

## Metrics landscape

The LLM-backed services expose a common `backend_*` metric label shape so cross-backend dashboards can union on
`(agent, agent_id, backend)` without backend-specific series names. **Claude is still the broadest metric superset**,
while OpenAI, Codex, and Gemini fill in the shared process, request, session, hook, MCP, and tool families they can
observe natively. Some backend-specific series remain intentionally different where the underlying SDK/runtime differs
(for example, subprocess metrics on Claude vs. in-process OpenAI/Codex execution). Look at each backend's `metrics.py`
or `main.js` for the live catalog; look at `charts/witwave/dashboards/*.json` for the rendered Grafana views; look at
`charts/witwave/templates/prometheusrule.yaml` for the default alert set.

Harness, operator, and MCP tool metrics use their own prefixes (`harness_*`, `witwaveagent_*`, `witwaveprompt_*`,
`mcp_*`) and are documented in the same per-service `metrics.py` files.

## Conventions

### Auth + secret posture

- SOPS-encrypted secret mirrors are committed as `*.sops.env`, `*.sops.yaml`, `*.sops.yml`, or `*.sops.json` under the
  repo-wide policy in `.sops.yaml`. Current team shape: `.agents/self/team.sops.env` for shared self-team credentials,
  `.agents/self/<name>/agent.sops.env` for per-agent GitHub identity, and `.agents/test/team.sops.env` for shared test
  credentials. Root-level non-bootstrap secrets live in `.secrets/secrets.sops.yaml`. Mixed metadata/secret records can
  use `.secrets/*.partial.sops.yaml`, which encrypts only secret-like keys (`password`, `token`, `secret`,
  `client_secret`, `api_key`, `private_key`) and leaves descriptive metadata readable. Use `mise exec -- sops -d <file>`
  for local inspection and `mise exec -- scripts/sops-exec-env.py <file.sops.env> -- <command>` to run commands with
  decrypted dotenv values in memory. Do not commit plaintext secret files.
- Every protected endpoint uses `Authorization: Bearer <token>` headers. Two harness-scope tokens split by purpose:
  `CONVERSATIONS_AUTH_TOKEN` (read / observe) and `ADHOC_RUN_AUTH_TOKEN` (trigger actions). Backends reuse
  `CONVERSATIONS_AUTH_TOKEN` for their `/conversations` / `/api/traces` / `/api/sessions/<id>/stream` paths.
  `CONVERSATIONS_AUTH_DISABLED=true` is the documented local-dev escape hatch; startup logs a loud warning when it's
  set.
- Session IDs on `/mcp` are HMAC-bound to the caller via `shared/session_binding.derive_session_id` when
  `SESSION_ID_SECRET` is set. `SESSION_ID_SECRET_PREV` provides a rotation window — writes always use the current-secret
  id; reads probe `[current, prev]` and emit a one-shot WARN on prev-hit so operators know when they can drop the prev
  secret.
- Secret-backed tokens never land in chart-rendered ConfigMaps. The dashboard injects its harness bearer via a Secret →
  env → nginx `envsubst` → `sub_filter` chain; the literal token lives only in the pod's
  `/etc/nginx/conf.d/default.conf`, not anywhere `kubectl get cm` can reach.
- A2A relay outbound: when a backend's `auth_env` is configured but the referenced env var is empty, harness raises
  `ConnectionError` at request time with a clear diagnostic (v0.5.0+). The previous behaviour — silently sending
  unauthenticated and letting the backend 401 — masked operator misconfig as "backend instability" in circuit-breaker
  dashboards (#1349 correctly filters 4xx from the breaker). Clear `auth_env` in `backend.yaml` if the backend actually
  doesn't require auth; otherwise set the referenced env var to a real token.

### Redaction + logging

- `shared/redact.py` is the single redaction path. It uses a merge-spans algorithm so
  `redact_text(redact_text(x)) == redact_text(x)` is guaranteed (idempotent by construction). UUID and OTel trace/span
  shapes are shielded before pattern substitution and restored after. The generic high-entropy catch-all is gated behind
  `LOG_REDACT_HIGH_ENTROPY=true` — don't enable it on trace pipelines without understanding the false-positive surface.
- Event envelopes carry `session_id_hash` (SHA-256 prefix 12 chars) instead of raw session ids on any stream that
  crosses trust boundaries (`/events/stream`, session drill-down streams). Raw session ids appear only in URL paths
  where the caller already has them.

### MCP transport posture

- `mcp_command_allowlist` gates every stdio MCP entry against `MCP_ALLOWED_COMMANDS` / `MCP_ALLOWED_COMMAND_PREFIXES` /
  `MCP_ALLOWED_CWD_PREFIXES`. Rejections increment `backend_mcp_command_rejected_total{reason}`. The default allow-list
  is pruned to `mcp-kubernetes, mcp-helm, mcp-prometheus, uv, uvx`; `mcp_command_args_safe()` rejects positional scripts
  (`*.py`, `*.js`, `*.sh`, `-` stdin) unless their cwd falls under the allow-listed prefix.
- MCP tool containers enforce bearer auth via `shared/mcp_auth.py`. Set `MCP_TOOL_AUTH_TOKEN` in deployment;
  `MCP_TOOL_AUTH_DISABLED=true` is the local-dev escape hatch (startup logs a warning).
- Read-only mode: `MCP_READ_ONLY=true` (or `MCP_HELM_READ_ONLY` / `MCP_KUBERNETES_READ_ONLY`) refuses mutating tools
  while keeping query tools available. Useful during cluster maintenance windows.

### Operator RBAC

- Operator runs with a split RBAC surface: `rbac.secretsWrite=false` on `charts/witwave-operator` drops the Secret write
  verbs while keeping reads, letting operators use pre-provisioned Secrets via `existingSecret`. Credential Secrets
  carry `app.kubernetes.io/component: credentials` and are dual-checked (label + `IsControlledBy`) before any update or
  delete — the operator never touches user-created Secrets.

## Reference pointers

AGENTS.md is deliberately high-level. For specifics, go to the source of truth:

- **Chart values + env var reference** — `charts/witwave/values.yaml` and `charts/witwave-operator/values.yaml` carry
  inline comments on every field. Harness production tunables that do not need dedicated chart keys are surfaced through
  `charts/witwave/values.yaml` `harnessEnv.*` and render onto every harness container unless a later per-agent
  `agents[].env` entry overrides them. Common harness keys include `SESSION_STREAM_MAX_PER_CALLER`,
  `CONVERSATION_STREAM_{QUEUE_MAX,RING_MAX,GRACE_SEC,KEEPALIVE_SEC}`, `WEBHOOK_RETRY_BYTES_PER_SUB`,
  `WEBHOOK_ALLOW_LOOPBACK_HOSTS`, `A2A_SESSION_CONTEXT_CACHE_MAX`, `A2A_MAX_RESPONSE_BYTES`, `A2A_RETRY_POLICY`
  (fast-only | always | never; default fast-only), `A2A_RETRY_FAST_ONLY_MS` (default 5000 — 5xx slower than this won't
  be retried under fast-only), `HARNESS_PROXY_MAX_RESPONSE_BYTES`, `TASKS_SHUTDOWN_DRAIN_TIMEOUT`,
  `JOBS_SHUTDOWN_DRAIN_TIMEOUT`, and `CONTINUATIONS_SHUTDOWN_DRAIN_TIMEOUT`. MCP tool tunables are surfaced under each
  `mcpTools.<name>.env` block, including `MCP_HELM_REPO_URL_ALLOWLIST`, `MCP_HELM_ALLOW_ANY_REPO`,
  `MCP_K8S_READ_SECRETS_DISABLED`, and `MCP_PROM_MAX_RESPONSE_BYTES`. Service-specific env vars are also declared in
  each Dockerfile's `ENV` directives. These values are read at startup; no hot-reload is promised.
- **Metric catalog** — per-service `metrics.py`; rendered dashboards at `charts/witwave/dashboards/`; default alert
  thresholds at `charts/witwave/templates/prometheusrule.yaml`.
- **Event stream wire contract** — `docs/events/README.md` + `docs/events/events.schema.json`.
- **HTTP route surface** — each service's `main.py` route declarations; harness `/.well-known/agent-runs.json` for
  runtime discovery of ad-hoc-run endpoints.
- **Architecture decisions + historical context** — `git log -- <file>` on the area you're looking at; GitHub issue
  tracker (component labels match this file's taxonomy:
  `harness, claude, openai, codex, gemini, dashboard, operator, charts, mcp, cli` + cross-cutting).
- **Operator CRD reference** — `operator/api/v1alpha1/*_types.go` Go types (`witwaveagent_types.go`,
  `witwaveprompt_types.go`, `witwaveworkspace_types.go`) + generated CRD schemas under `operator/config/crd/bases/`;
  sample manifests in `operator/config/samples/`.
- **`ww` CLI design rules** — `clients/ww/DESIGN.md` codifies the CLI's design invariants (kubeconfig handling, command
  taxonomy, flag conventions, exit codes). Rules are numbered (`KC-*`, `TAX-*`, …) and should be cited in PRs that touch
  CLI behavior.
