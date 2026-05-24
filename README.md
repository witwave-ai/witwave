# Witwave

Witwave is a cloud-native autonomous agent framework for AI agent teams. Visit [witwave.ai](https://witwave.ai) for the
public overview, whitepapers, team roster, and Quick Start.

The project is built around agent-native engineering: named agents with durable identity, backend choice, schedules,
triggers, memory, observability, MCP tool access, shared workspaces, and human-governed escalation. The goal is not to
replace judgment with automation. The goal is to make agents behave more like managed software participants than
isolated chat sessions.

The primary use case is autonomous software development: agents that can triage issues, implement features, fix bugs,
evaluate their work, coordinate with other agents, and help improve the system they run on. The same framework can be
pointed at other software projects, and the same deployment model can support a single specialized agent or a team of
agents.

What Witwave includes:

- A **harness** that routes work, schedules heartbeats/jobs/tasks, handles triggers, and chains continuations.
- **Backend agents** for Claude, OpenAI, Codex, Gemini, and a zero-dependency `echo` stub for onboarding and smoke
  tests.
- Shared **MCP tools** for Kubernetes, Helm, and Prometheus.
- A Kubernetes **operator**, Helm charts, and the `ww` CLI for installing and managing agents.
- Persistent per-agent identity, memory, conversation logs, metrics, traces, and workspace bindings.

**This project is also an experiment in AI-operated open source.** Every line of code here is written by AI. Every bug
is diagnosed and fixed with AI. Documentation, releases, website copy, and public updates are part of the same operating
model. Humans set direction, own risk, and make strategic calls; agents do the day-to-day implementation work and make
that work inspectable. See [`CONTRIBUTING.md`](CONTRIBUTING.md) for the full model (including the
current-state-vs-target breakdown), and [`docs/product-vision.md`](docs/product-vision.md) for why this is a first-class
project goal rather than a convention.

---

Built on the [A2A protocol](https://a2a-protocol.org). Each named agent is a set of containers: a **harness**
infrastructure layer (A2A relay, heartbeat scheduler, job scheduler) and one or more **backend agent** containers that
do the actual LLM work (Claude Agent SDK via `claude`, OpenAI Agents SDK via `openai`, Codex-native Node backend via
`codex`, Google Gemini SDK via `gemini`). The `echo` backend ships as a zero-dependency stub — it returns a canned
response quoting the caller's prompt and is the hello-world default for `ww agent create` when no API key is configured.

Multiple agents can collaborate as a team, but the named agent (harness + its backend agents) is the deployable unit.

## Agent Model

Three tiers to keep straight:

1. **A2A agent** — any server that publishes `/.well-known/agent.json`. The protocol's unit of identity. Both the
   harness and each backend agent qualify.
2. **Backend agent** — the LLM-wrapping worker. One image per backend family (`claude`, `openai`, `codex`, `gemini`),
   plus the zero-dependency `echo` stub. Each owns its own session state, memory, conversation log, and metrics, and is
   callable standalone over A2A.
3. **Named agent** — the deployable unit (`iris`, `nova`, `kira`, …). From outside it presents as a single A2A agent via
   the harness's endpoint. Inside, the harness orchestrates one or more backend agents using routing rules in
   `.witwave/backend.yaml`.

Named agents may additionally bind to one or more **workspaces** — the operator-level primitive for shared resources
multiple agents collaborate over (shared volumes, scoped Secrets, ConfigMap-backed files). See [Workspaces](#workspaces)
below for the concept; workspaces are agent-owned via `WitwaveAgent.spec.workspaceRefs[]` and purely additive (an agent
with zero workspace memberships runs perfectly).

A named agent is both an agent **and** an orchestrator of sub-agents. Because the harness treats any A2A URL as a valid
dispatch target, peer named agents are reachable the same way local backend agents are — teams of named agents are just
agents all the way down.

The split of responsibilities:

- **Autonomy** (when and why work happens) lives in the harness: heartbeats, jobs, tasks, triggers, continuations,
  webhooks.
- **Intelligence** (what to say, what to do) lives in the backend agents: LLM SDK wrappers that turn prompts into
  responses.

Remove the harness and you have reactive LLM servers that only respond when called — not autonomous. Remove the backend
agents and you have a scheduler with nothing to dispatch to — no intelligence. Together they form an autonomous agent.

## Components

| Component            | Directory                  | Type                | Description                                                                                                                                          |
| -------------------- | -------------------------- | ------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Harness**          | `harness/`                 | Orchestrator agent  | Scheduling, triggering, chaining, A2A relay. No LLM of its own.                                                                                      |
| **Claude backend**   | `backends/claude/`         | Backend agent       | Executes prompts via the Claude Agent SDK.                                                                                                           |
| **OpenAI backend**   | `backends/openai/`         | Backend agent       | Executes prompts via the OpenAI Agents SDK. Supports web search and headless browser via Playwright.                                                 |
| **Codex backend**    | `backends/codex/`          | Backend agent       | Node.js backend reserved for Codex-optimized coding-agent execution through the Responses API.                                                       |
| **Gemini backend**   | `backends/gemini/`         | Backend agent       | Executes prompts via the Google Gemini SDK.                                                                                                          |
| **Echo backend**     | `backends/echo/`           | Backend agent       | Zero-dependency stub. Returns a canned response quoting the prompt. Hello-world default + reference.                                                 |
| **MCP tools**        | `tools/`                   | Tool infrastructure | `mcp-kubernetes`, `mcp-helm`, `mcp-prometheus` — shared MCP servers backends opt into.                                                               |
| **Dashboard**        | `clients/dashboard/`       | Web client          | Vue 3 + PrimeVue web UI.                                                                                                                             |
| **ww CLI**           | `clients/ww/`              | Client              | Go + cobra command-line interface (`curl -fsSL https://github.com/witwave-ai/witwave/releases/latest/download/install.sh \| sh`, or Homebrew).       |
| **Operator**         | `operator/`                | Kubernetes operator | Go controller that reconciles `WitwaveAgent`, `WitwavePrompt`, and `WitwaveWorkspace` CRDs.                                                          |
| **WitwaveWorkspace** | `operator/api/v1alpha1/`   | Shared-resource CRD | Operator-reconciled bundle of shared volumes, projected Secrets, and ConfigMap files that participating agents mount. See [Workspaces](#workspaces). |
| **Agent chart**      | `charts/witwave/`          | Deployment          | Helm chart that deploys Witwave agents via templated manifests.                                                                                      |
| **Operator chart**   | `charts/witwave-operator/` | Deployment          | Helm chart that installs the operator + CRD.                                                                                                         |

The harness routes work to backend agents but does no LLM execution itself. Client surfaces (dashboard + ww) provide
visibility and interaction; they don't participate in agent workflows. The operator and its chart are an alternative
install path to the agent chart; both target the same per-agent deployment shape.

## Workspaces

A `WitwaveWorkspace` is the operator-level primitive for shared resources multiple agents collaborate over. Each
WitwaveWorkspace declares a list of shared PVC-backed volumes, a list of pre-created Secret references, and a list of
ConfigMap-backed files; the operator provisions and projects them onto every `WitwaveAgent` whose `spec.workspaceRefs[]`
references the WitwaveWorkspace. The CRD is intentionally generic — source trees, datasets, video pipelines, accumulated
memory pools, anything where teams of agents need the same files visible at the same paths. Membership is agent-owned:
an agent with zero `workspaceRefs[]` runs perfectly; participation is purely additive.

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
---
apiVersion: witwave.ai/v1alpha1
kind: WitwaveAgent
metadata:
  name: iris
  namespace: witwave
spec:
  workspaceRefs:
    - name: shared
```

Mount paths default to `/workspaces/<workspace>/<volume.name>` so cross-agent paths line up without operator-supplied
glue. Manage workspaces from the CLI with `ww workspace { create, list, get, status, delete, bind, unbind }` — see
[`clients/ww/README.md`](clients/ww/README.md#witwaveworkspace-management). Full CRD schema and reconciler details live
in [`operator/README.md`](operator/README.md#the-witwaveworkspace-resource).

## How It Works

Operational details that complement the Agent Model above:

- Each named agent has its own identity, memory, and configuration — none baked into the image. Behavioral instructions
  for each backend agent come from a mounted file (`CLAUDE.md` for claude, `AGENTS.md` for openai/codex, `GEMINI.md` for
  gemini), and A2A identity comes from a mounted `agent-card.md`.
- Every container (harness and each backend agent) exposes `/health` for probes and `/metrics` for Prometheus on a
  dedicated port (9000 by default) alongside its A2A endpoint.

## Requirements

- Docker
- A Claude Code OAuth token (`claude setup-token`) or Anthropic API key (for `claude`)
- An OpenAI API key (for `openai`)
- An OpenAI API key with Codex model access (for `codex`)
- A Gemini API key (for `gemini`)
- Nothing extra for `echo` — the stub backend runs without credentials or network access

## Container Images

Published images are available on GitHub Container Registry. Every image listed below is built and pushed automatically
on every release tag.

| Image            | Registry path                                     |
| ---------------- | ------------------------------------------------- |
| `harness`        | `ghcr.io/witwave-ai/images/harness:latest`        |
| `claude`         | `ghcr.io/witwave-ai/images/claude:latest`         |
| `openai`         | `ghcr.io/witwave-ai/images/openai:latest`         |
| `codex`          | `ghcr.io/witwave-ai/images/codex:latest`          |
| `gemini`         | `ghcr.io/witwave-ai/images/gemini:latest`         |
| `echo`           | `ghcr.io/witwave-ai/images/echo:latest`           |
| `backend-base`   | `ghcr.io/witwave-ai/images/backend-base:latest`   |
| `dashboard`      | `ghcr.io/witwave-ai/images/dashboard:latest`      |
| `operator`       | `ghcr.io/witwave-ai/images/operator:latest`       |
| `git-sync`       | `ghcr.io/witwave-ai/images/git-sync:latest`       |
| `mcp-kubernetes` | `ghcr.io/witwave-ai/images/mcp-kubernetes:latest` |
| `mcp-helm`       | `ghcr.io/witwave-ai/images/mcp-helm:latest`       |
| `mcp-prometheus` | `ghcr.io/witwave-ai/images/mcp-prometheus:latest` |

`backend-base` is the shared backend base image for common runtimes and CLI/analyzer tooling (Go, Node, `kubectl`, `ww`,
`gh`, Helm, formatters, linters, and scanners). It is image composition only: Kubernetes permissions still come from
`WitwaveAgent.spec.kubernetesApiAccess` or an explicit ServiceAccount/RBAC binding.

The `ww` CLI ships through three install paths — pick whichever fits your environment:

```bash
# Universal (Linux + macOS) — POSIX-sh installer with SHA256 verification.
curl -fsSL https://github.com/witwave-ai/witwave/releases/latest/download/install.sh | sh

# macOS via the witwave-ai/homebrew-ww tap (Homebrew cask).
brew install witwave-ai/homebrew-ww/ww

# From source (developers).
go install github.com/witwave-ai/witwave/clients/ww@latest
```

Standalone binaries are also published on [GitHub Releases](https://github.com/witwave-ai/witwave/releases). See
[clients/ww/README.md](clients/ww/README.md#install) for the full flag and env-var reference for the curl installer
(version pinning, beta channel, custom prefix, cosign verification, uninstall).

`ww` checks for newer releases on startup and surfaces a one-line banner (configurable via
`ww config set update.mode ...`). To upgrade explicitly at any time:

```bash
ww update              # check + upgrade if newer
ww update --check      # check only
ww update --force      # run the upgrade unconditionally
```

Pull a specific image version with a semver tag, e.g. `ghcr.io/witwave-ai/images/harness:0.23.15`. The latest released
tag is visible on the [GitHub Releases](https://github.com/witwave-ai/witwave/releases) page; substitute it for the
version below.

## Helm Charts

Two Helm charts are published to GHCR alongside the images on every release tag. The fastest install for the
**operator** is the `ww` CLI — it embeds the chart so you don't need `helm` on PATH or any repo configured:

```bash
# Install `ww` then use it to install the operator.
curl -fsSL https://github.com/witwave-ai/witwave/releases/latest/download/install.sh | sh   # or: brew install witwave-ai/homebrew-ww/ww
ww operator install                 # into witwave-system namespace
ww operator status                  # verify
```

See [clients/ww/README.md](clients/ww/README.md#operator-management) for the full `ww operator` surface.

For direct Helm installs (GitOps workflows, non-Homebrew environments, or the main agent chart which isn't yet
CLI-managed):

```bash
# Agent chart — deploys Witwave agents directly via templated manifests.
helm install witwave oci://ghcr.io/witwave-ai/charts/witwave --version 0.23.15 --namespace witwave --create-namespace

# Operator chart — installs the witwave-operator controller and the WitwaveAgent CRD.
helm install witwave-operator oci://ghcr.io/witwave-ai/charts/witwave-operator --version 0.23.15 --namespace witwave-system --create-namespace
```

See [charts/witwave/README.md](charts/witwave/README.md) and
[charts/witwave-operator/README.md](charts/witwave-operator/README.md) for full installation instructions.

## Getting Started

### 1. Pull or build the images

Pull published images:

```bash
docker pull ghcr.io/witwave-ai/images/harness:latest
docker pull ghcr.io/witwave-ai/images/claude:latest
docker pull ghcr.io/witwave-ai/images/openai:latest
docker pull ghcr.io/witwave-ai/images/codex:latest
docker pull ghcr.io/witwave-ai/images/gemini:latest
docker pull ghcr.io/witwave-ai/images/echo:latest
docker pull ghcr.io/witwave-ai/images/backend-base:latest
```

Or build locally:

```bash
docker build -f images/backend-base/Dockerfile -t backend-base:latest .
docker build -f harness/Dockerfile -t harness:latest .
docker build --build-arg BACKEND_BASE_IMAGE=backend-base:latest -f backends/claude/Dockerfile -t claude:latest .
docker build --build-arg BACKEND_BASE_IMAGE=backend-base:latest -f backends/openai/Dockerfile -t openai:latest .
docker build --build-arg BACKEND_BASE_IMAGE=backend-base:latest -f backends/codex/Dockerfile -t codex:latest .
docker build --build-arg BACKEND_BASE_IMAGE=backend-base:latest -f backends/gemini/Dockerfile -t gemini:latest .
docker build -f backends/echo/Dockerfile -t echo:latest .
```

### 2. Configure Credentials

SOPS is the committed encrypted source for repo-managed team secrets. The policy in `.sops.yaml` applies to
`*.sops.env`, `*.sops.yaml`, `*.sops.yml`, and `*.sops.json` anywhere in the repo. A more specific
`.secrets/*.partial.sops.yaml` rule keeps non-secret metadata readable while encrypting secret-shaped keys (`password`,
`token`, `secret`, `client_secret`, `api_key`, `private_key`). Current encrypted mirrors:

- `.agents/self/team.sops.env` — shared self-team credentials.
- `.agents/self/<agent>/agent.sops.env` — per-agent GitHub identity, with in-container names like `GITHUB_TOKEN` and
  `GITHUB_USER`.
- `.agents/test/team.sops.env` — shared test-team credentials.
- `.secrets/secrets.sops.yaml` — root-level encrypted holding area for non-bootstrap / in-progress secrets.
- `.secrets/*.partial.sops.yaml` — mixed metadata/secret records where only secret-like keys are encrypted.

Decrypt locally through mise/SOPS when you need to inspect one of those files:

```bash
mise exec -- sops -d .agents/self/team.sops.env
mise exec -- sops -d .agents/self/piper/agent.sops.env
```

Run commands with SOPS dotenv files loaded into the process environment:

```bash
mise exec -- scripts/sops-exec-env.py .agents/test/team.sops.env -- \
  sh -lc 'test -n "$CLAUDE_CODE_OAUTH_TOKEN" && test -n "$GITSYNC_USERNAME"'
```

### 3. Start the test agents

```bash
ww operator install --yes

# Then follow the Bob/Fred `ww agent create` commands in:
sed -n '1,240p' .agents/test/bootstrap.md
```

### 4. Verify

```bash
# In one terminal:
kubectl port-forward svc/bob 8099:8000 -n witwave-test

# In another terminal:
curl http://localhost:8099/.well-known/agent.json
curl http://localhost:8099/health/ready
```

## Agent Structure

Self-managing agents are defined under `.agents/self/` (the agents that maintain this repo). Each named agent has its
own directory containing Witwave config, backend instances, logs, and memory.

```text
.agents/
├── self/
│   ├── iris/          # Git plumbing + releases
│   ├── kira/          # Documentation hygiene + research
│   ├── nova/          # Code-internal hygiene
│   ├── evan/          # Bugs + security-oriented risk work
│   ├── finn/          # Gap-filling work
│   ├── felix/         # Feature work
│   ├── piper/         # Outreach
│   └── zora/          # Team coordination
└── test/
    ├── bob/           # Bob  (Claude-first smoke agent; openai/gemini fixtures parked)
    ├── fred/          # Fred (small Claude-only second-agent sanity check)
    ├── jack/          # OpenAI-only scaffold
    └── luke/          # Gemini-only scaffold
```

CLI/operator-created agents use the `ww` port convention by default: harness on `8000`, backend sidecars on
`8001..8050`, and metrics on `9000`. Ports are not hardcoded in any image. Each container reads its own port from an
environment variable (`HARNESS_PORT`, `BACKEND_PORT`, `METRICS_PORT`) and can be remapped per deployment via Helm values
or the `WitwaveAgent` CRD.

Each agent directory contains:

```text
<agent>/
├── agent.sops.env      # Encrypted per-agent secret mirror (when the agent has agent-specific credentials)
├── .witwave/              # Runtime config (agent-card.md, backend.yaml, HEARTBEAT.md, jobs/)
├── .claude/           # Claude backend config (CLAUDE.md, mcp.json, settings.json, skills/)
├── .openai/            # OpenAI backend config (AGENTS.md, config.toml)
├── .codex/            # Codex backend config (AGENTS.md, config.toml)
├── .gemini/           # Gemini backend config (GEMINI.md)
├── logs/              # harness logs (runtime, not committed)
├── claude/         # Claude backend instance
│   ├── logs/          # Conversation log (runtime, not committed)
│   └── memory/        # Persistent memory (runtime, not committed)
├── openai/          # OpenAI backend instance
│   ├── logs/
│   └── memory/
├── codex/          # Codex backend instance
│   ├── logs/
│   └── memory/
└── gemini/         # Gemini backend instance
    ├── logs/
    └── memory/
        └── sessions/  # JSON conversation history per session
```

## Routing Configuration

Each agent's `backend.yaml` (under `.witwave/`) controls where Witwave routes each type of work:

```yaml
backend:
  agents:
    - id: claude
      url: http://localhost:8010

    - id: openai
      url: http://localhost:8011

    - id: codex
      url: http://localhost:8012

    - id: gemini
      url: http://localhost:8013

  routing:
    default: claude # fallback backend when no per-concern override matches
    a2a: claude # handles incoming A2A requests
    heartbeat: claude # handles heartbeat-triggered work
    job: claude # handles job execution
    task: claude # handles task execution
    trigger: claude # handles inbound HTTP trigger requests
    continuation: claude # handles continuation-fired prompts
```

Routing values can be a plain agent ID string or an object with `agent:` and optional `model:` fields. Model resolution
order: per-message override → routing entry `model:` → per-backend config `model:`.

## Consensus Mode

Set `consensus:` in any prompt file's frontmatter to a list of backend entries. Each entry specifies a `backend` glob
pattern and an optional `model` override. The prompt is dispatched to every matched `(backend, model)` pair in parallel,
then the responses are aggregated:

- **Binary responses** (yes / no / agree / disagree variants): majority vote. The default backend breaks ties.
- **Freeform responses**: a synthesis prompt is dispatched to the default backend, which merges the collected responses
  into a single coherent answer.

```yaml
consensus:
  - backend: "claude"
    model: "claude-opus-4-7"
  - backend: "openai*" # glob — matches any backend whose id starts with "openai"
  - backend: "claude"
    model: "claude-haiku-4-5" # same backend, different model = two parallel calls
```

An empty list (the default when `consensus:` is omitted) disables consensus — the prompt is dispatched to the single
routing target. The same backend can appear twice with different models to compare outputs from different model sizes.

Use consensus mode for high-stakes decisions where you want more than one model family's perspective.

## Token Budget (max-tokens)

Set `max-tokens` in a job, task, or trigger frontmatter to cap cumulative token usage for that dispatch. When the
backend reports that usage has reached the limit, it stops processing and returns any partial response collected so far.
A `system` entry is written to `conversation.jsonl` recording how many tokens were consumed and what the limit was.

```yaml
---
name: daily-summary
schedule: "0 8 * * *"
max-tokens: 4000
---
Summarise the day's key events.
```

The value must be a positive integer. Invalid values are logged and ignored. The limit applies per-dispatch (not across
sessions), so each job/task/trigger invocation gets a fresh budget. LLM-backed runtimes enforce it from their provider
usage stream or final usage summary:

| Backend  | Token source                                                  |
| -------- | ------------------------------------------------------------- |
| `claude` | `get_context_usage()` after each assistant turn               |
| `openai` | `event.data.usage.total_tokens` on response events            |
| `codex`  | Responses API `usage.total_tokens` after each response create |
| `gemini` | `chunk.usage_metadata.total_token_count` per chunk            |

## Adding an Agent

1. Copy an existing agent directory:

   ```bash
   cp -r .agents/self/iris .agents/self/<name>
   ```

2. Update the agent's `agent-card.md` in `.witwave/` (mounted at `/home/agent/.witwave/agent-card.md`) with the agent's
   identity and role

3. Update the backend instruction files: `CLAUDE.md` (at `/home/agent/.claude/CLAUDE.md`), `AGENTS.md` (at
   `/home/agent/.openai/AGENTS.md` or `/home/agent/.codex/AGENTS.md`), and `GEMINI.md` (at
   `/home/agent/.gemini/GEMINI.md`) with backend-specific behavioral instructions

4. Update `.agents/self/<name>/.witwave/backend.yaml` with the new agent's backend service names and URLs

5. Deploy it with `ww agent create`, usually using `--workspace <workspace>` to bind it to the right team workspace,
   `--gitsync-bundle` to point at the agent directory, and `--with-persistence` for backend memory/session storage

6. Deploy:

   ```bash
   ww agent create <name> --namespace <namespace> --workspace <workspace> --backend claude --with-persistence \
     --gitsync-bundle https://github.com/witwave-ai/witwave.git@main:.agents/self/<name>
   ```

## Communication

Agents communicate over the [A2A protocol](https://a2a-protocol.org) via JSON-RPC. Each Witwave agent exposes:

- `/.well-known/agent.json` — agent card (identity and capabilities)
- `/` — A2A JSON-RPC endpoint (`message/send`)
- `GET /health/start` — startup probe: 200 once ready, 503 while initializing
- `GET /health/live` — liveness probe: always 200 with `{"status": "ok", "agent": ..., "uptime_seconds": ...}`
- `GET /health/ready` — readiness probe: 200/`{"status": "ready"}`; 503/`{"status": "starting"}` while initializing;
  503/`{"status": "degraded"}` when a backend is unhealthy
- `GET /agents` — own agent card plus agent cards from all configured backends
- `GET /jobs` — structured snapshot of all registered scheduled jobs (name, cron, backend, running state)
- `GET /tasks` — structured snapshot of all registered scheduled tasks (name, days, window, running state)
- `GET /webhooks` — structured snapshot of all registered webhook subscriptions (name, url, filters, active deliveries)
- `GET /continuations` — structured snapshot of all registered continuation items (name, continues-after, filters,
  active fires)
- `GET /triggers` — structured snapshot of all registered inbound trigger endpoints (name, endpoint, description,
  session, backend, running state)
- `GET /heartbeat` — current heartbeat configuration from `HEARTBEAT.md`
- `GET /conversations` — merged conversation log from all backends
- `GET /trace` — merged trace log from all backends
- `GET /.well-known/agent-triggers.json` — discovery array of all enabled trigger descriptors

Cross-agent views (`/team`, `/proxy/<name>`, `/conversations/<name>`, `/trace/<name>`) were retired in beta.46 — the
dashboard pod fans out directly to each agent and owns cross-agent routing (#470).

Each backend container additionally exposes:

- `GET /health/start` — startup probe: 200/`{"status": "ok"}` once the process has finished initial loads (`_ready` is
  True) and 503/`{"status": "starting"}` while still warming up. Mirrors the harness's `/health/start` so the
  three-probe model documented in `docs/product-vision.md` holds across the platform (#1686). K8s `startupProbe` should
  target this endpoint.
- `GET /health` — liveness check: 200/`{"status": "ok", "agent": ..., "uptime_seconds": ...}` once the process is up.
  Returns 200 even while initializing — does NOT flip to 503. Liveness-only by design (cycle-1 #1608, #1672); use the
  readiness endpoint below for gating LB rotation.
- `GET /health/ready` — readiness probe: 200 when fully ready, 503/`{"status": "starting"}` while initializing or in a
  boot-degraded state (claude #1608, openai+gemini #1672). Operators using K8s `readinessProbe` should point at
  `/health/ready`, not `/health`.
- `GET /metrics` — Prometheus metrics (when `METRICS_ENABLED` is set)
- `POST /mcp` — MCP JSON-RPC server (`initialize`, `tools/list`, `tools/call` with a backend-specific ask tool); allows
  MCP hosts (Claude Desktop, Cursor, VS Code extensions) to invoke the agent as a tool without going through harness.
  LLM-backed backends require a bearer token (`CONVERSATIONS_AUTH_TOKEN`) on `/mcp` (#510, #516, #518); the shared token
  guard also gates `/conversations` and `/trace`. If the env var is left empty the backend logs a startup warning (#517)
  or refuses protected endpoints unless `CONVERSATIONS_AUTH_DISABLED=true` is set for local/dev use. The Python SDK
  backends bind `session_id` through `shared/session_binding.derive_session_id` with a bearer-token fingerprint before
  lookup/insert (#867 claude, #929 openai, #935 gemini, #941 shared path); set `SESSION_ID_SECRET` in production to
  HMAC-derive the bound ID.

## Memory

Each backend agent manages its own memory at `.agents/<env>/<name>/<backend>/memory/`. For `claude` and `openai`, memory
files are markdown documents. The `codex` scaffold exposes bounded memory tools rooted at `CODEX_MEMORY_ROOT` (default
`/home/agent/.codex/memory`) and persists Responses API `previous_response_id` mappings at `CODEX_SESSION_STORE_PATH`
(default `/home/agent/.codex/sessions/responses.json`). For `gemini`, conversation history is stored as JSON in
`memory/sessions/`. Memory files are not committed to source control. harness has no memory layer of its own.

Workspace-backed memory is a separate shared volume. When a workspace declares a `memory` volume, bound agents see it at
`/workspaces/<workspace-name>/memory`; the self team uses `witwave-self/memory`, and the test team mirrors the same
namespace/index contract under `witwave-test/memory`.

## Authentication

| Service | Method             | Environment variable                 |
| ------- | ------------------ | ------------------------------------ |
| claude  | Claude Max (OAuth) | `CLAUDE_CODE_OAUTH_TOKEN`            |
| claude  | Anthropic API key  | `ANTHROPIC_API_KEY`                  |
| openai  | OpenAI API key     | `OPENAI_API_KEY`                     |
| codex   | OpenAI API key     | `OPENAI_API_KEY`                     |
| gemini  | Gemini API key     | `GEMINI_API_KEY` or `GOOGLE_API_KEY` |

## Security

Protected endpoints use `Authorization: Bearer <token>` throughout. Two distinct harness tokens:

- **`CONVERSATIONS_AUTH_TOKEN`** — read / observe endpoints (`/conversations`, `/trace`, `/mcp`, `/api/traces`,
  `/events/stream`, `/api/sessions/<id>/stream`). Reused on the harness for inbound and on each backend for its own
  protected surface.
- **`ADHOC_RUN_AUTH_TOKEN`** — ad-hoc action endpoints (`POST /heartbeat/run`, `/jobs/<name>/run`,
  `/tasks/<name>/run`, `/validate`).

Both are default-closed — the server refuses requests when the token is unset. `CONVERSATIONS_AUTH_DISABLED=true` is a
documented escape hatch for local dev; startup logs a loud warning when it's set.

Session IDs on multi-tenant surfaces are HMAC-bound to the caller via `SESSION_ID_SECRET`. Rotation uses a probe-list
window via `SESSION_ID_SECRET_PREV`: writes go to the current-secret derivation; reads probe `[current, prev]` and emit
a one-shot WARN on prev-hits so operators know when they can drop the prev secret.

MCP stdio entries are gated by a per-backend command allow-list (`MCP_ALLOWED_COMMANDS`, `MCP_ALLOWED_COMMAND_PREFIXES`,
`MCP_ALLOWED_CWD_PREFIXES`); rejections bump `backend_mcp_command_rejected_total{reason}`. Every MCP tool container
enforces its own bearer (`MCP_TOOL_AUTH_TOKEN`) via `shared/mcp_auth.py`. Outbound webhooks go through an SSRF-resistant
URL check that re-resolves the hostname at delivery time.

The `witwave-operator` chart runs with a split RBAC surface (`rbac.secretsWrite=false` drops Secret write verbs while
keeping reads). Credential Secrets are dual-checked (label + `IsControlledBy`) before any update/delete so the operator
never touches user-created Secrets.

See `AGENTS.md` → "Conventions" for the full auth / redaction / MCP / RBAC posture, `shared/redact.py` for the
conversation-log redaction rules (idempotent merge-spans with UUID / OTel-trace shielding), and each chart's
`values.yaml` for the full surface of security-affecting knobs.

## Configuration

### harness environment variables

| Variable                                    | Default                             | Description                                                                                                                                                                                                                              |
| ------------------------------------------- | ----------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `AGENT_NAME`                                | `witwave`                           | Agent display name (e.g. `iris`)                                                                                                                                                                                                         |
| `HARNESS_HOST`                              | `0.0.0.0`                           | Interface the harness binds to                                                                                                                                                                                                           |
| `HARNESS_PORT`                              | `8000`                              | HTTP port the harness listens on                                                                                                                                                                                                         |
| `HARNESS_URL`                               | `http://localhost:$HARNESS_PORT/`   | Public URL published on the A2A agent card                                                                                                                                                                                               |
| `BACKEND_CONFIG_PATH`                       | `/home/agent/.witwave/backend.yaml` | Path to the backend routing config file                                                                                                                                                                                                  |
| `METRICS_ENABLED`                           | _(unset)_                           | Set to any non-empty value to expose `/metrics`                                                                                                                                                                                          |
| `METRICS_PORT`                              | `9000`                              | Dedicated port the metrics listener binds to (split from the app port so NetworkPolicy + auth can differ, #643)                                                                                                                          |
| `METRICS_AUTH_TOKEN`                        | _(unset)_                           | Bearer token required to access `/metrics` (recommended in production)                                                                                                                                                                   |
| `METRICS_CACHE_TTL`                         | `15`                                | Seconds to cache aggregated backend metrics between scrapes                                                                                                                                                                              |
| `CONVERSATIONS_AUTH_TOKEN`                  | _(unset)_                           | Bearer token required to access `/conversations` and `/trace` (inbound)                                                                                                                                                                  |
| `BACKEND_CONVERSATIONS_AUTH_TOKEN`          | _(unset)_                           | Bearer token forwarded to backend `/conversations`, `/trace`, and `/api/traces` endpoints (set if backends require auth)                                                                                                                 |
| `TRIGGERS_AUTH_TOKEN`                       | _(unset)_                           | Bearer token required for inbound trigger requests (fallback when no per-trigger HMAC secret is set)                                                                                                                                     |
| `HOOK_EVENTS_AUTH_TOKEN`                    | _(unset)_                           | Canonical bearer token on `/internal/events/hook-decision` (bound to the metrics listener, #924). `HARNESS_EVENTS_AUTH_TOKEN` is a back-compat alias that logs a deprecation warning when used alone (#859). Unset = refuse (#712, #933) |
| `SESSION_ID_SECRET`                         | _(unset — permissive)_              | HMAC key for `shared/session_binding.derive_session_id` used on `/mcp` session-id binding across SDK backends (#867/#929/#935/#941). Leave unset only in single-tenant dev; set to a 256-bit random value in production                  |
| `ADHOC_RUN_AUTH_TOKEN`                      | _(unset)_                           | Bearer token required for `POST /heartbeat/run`, `/jobs/<name>/run`, `/tasks/<name>/run`, `/validate`; unset = refuse (#700)                                                                                                             |
| `CORS_ALLOW_ORIGINS`                        | _(unset)_                           | Comma-separated list of allowed CORS origins; when unset, all cross-origin requests are denied (logs a warning)                                                                                                                          |
| `CORS_ALLOW_WILDCARD`                       | `false`                             | Explicit acknowledgement for `CORS_ALLOW_ORIGINS=*`; template refuses the wildcard otherwise (#701)                                                                                                                                      |
| `A2A_MAX_PROMPT_BYTES`                      | `1048576`                           | Reject inbound A2A prompts above this byte size at ingress; set to `0` to disable (#783)                                                                                                                                                 |
| `CONTINUATION_MAX_CONCURRENT_FIRES_GLOBAL`  | `0` (unlimited)                     | Hard cap on in-flight continuation fires across all items; protects against fan-out storms (#781)                                                                                                                                        |
| `TASK_STORE_PATH`                           | _(unset)_                           | Path for SQLite A2A task store; defaults to in-memory (state lost on restart)                                                                                                                                                            |
| `WORKER_MAX_RESTARTS`                       | `5`                                 | Consecutive crash limit before a critical worker marks the agent not-ready                                                                                                                                                               |
| `WEBHOOK_MAX_CONCURRENT_DELIVERIES`         | `50`                                | Maximum number of in-flight webhook delivery tasks across all subscriptions; deliveries beyond this cap are shed and counted                                                                                                             |
| `WEBHOOK_MAX_CONCURRENT_DELIVERIES_PER_SUB` | `10`                                | Per-subscription cap on concurrent in-flight deliveries; also settable per webhook via `max-concurrent-deliveries` frontmatter                                                                                                           |
| `WEBHOOK_EXTRACTION_TIMEOUT`                | `120`                               | Maximum seconds to wait for a single LLM extraction call inside a webhook delivery; prevents a slow backend from holding a delivery slot indefinitely                                                                                    |
| `WEBHOOK_URL_ALLOWED_HOSTS`                 | _(unset)_                           | Comma-separated `host` or `host:port` entries that are allowed to override the SSRF guard on private / loopback / reserved destinations (#524)                                                                                           |
| `JOBS_MAX_CONCURRENT`                       | `0` (unlimited)                     | Maximum number of jobs that may run concurrently; `0` disables the limit                                                                                                                                                                 |
| `TASKS_MAX_CONCURRENT`                      | `0` (unlimited)                     | Maximum number of tasks that may run concurrently; `0` disables the limit                                                                                                                                                                |
| `TASK_TIMEOUT_SECONDS`                      | `300`                               | Task timeout in seconds, applied to A2A backend requests                                                                                                                                                                                 |
| `MANIFEST_PATH`                             | `/home/agent/manifest.json`         | Path to the team manifest file listing all agents by name and URL                                                                                                                                                                        |
| `BACKENDS_READY_WARN_AFTER`                 | `120`                               | Seconds to wait before logging a warning that backends have not become healthy                                                                                                                                                           |
| `LOG_PROMPT_MAX_BYTES`                      | `200`                               | Maximum bytes of the prompt logged at INFO level; set to `0` to suppress prompt logging entirely                                                                                                                                         |
| `A2A_BACKEND_MAX_RETRIES`                   | `3`                                 | Maximum retry attempts for transient backend errors (429, 502, 503, 504, connection errors); must be >= 1                                                                                                                                |
| `A2A_BACKEND_RETRY_BACKOFF`                 | `1.0`                               | Base backoff in seconds for retry delay (exponential with jitter); multiplied by 2^attempt                                                                                                                                               |

### Backend (claude / openai / codex / gemini) environment variables

| Variable                       | Default                            | Description                                                                                                                                      |
| ------------------------------ | ---------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------ |
| `AGENT_NAME`                   | `claude`/`openai`/`codex`/`gemini` | Backend instance name (e.g. `iris-claude`)                                                                                                       |
| `AGENT_OWNER`                  | _(same as `AGENT_NAME`)_           | Named agent this backend belongs to (e.g. `iris`); used in metric labels                                                                         |
| `AGENT_ID`                     | `claude`/`openai`/`codex`/`gemini` | Backend slot identifier (e.g. `claude`); used in metric labels                                                                                   |
| `AGENT_URL`                    | `http://localhost:8000/`           | Public A2A endpoint URL for the agent card                                                                                                       |
| `BACKEND_PORT`                 | `8000`                             | HTTP port the backend listens on (internal)                                                                                                      |
| `METRICS_ENABLED`              | _(unset)_                          | Set to any non-empty value to expose `/metrics`                                                                                                  |
| `METRICS_PORT`                 | `9000`                             | Dedicated port the metrics listener binds to (#643; same semantics as harness)                                                                   |
| `CONVERSATIONS_AUTH_TOKEN`     | _(unset — warn on empty)_          | Bearer token required to access `/conversations`, `/trace`, `/mcp`, and `/api/traces[/<id>]` on all LLM-backed backends (#510, #516, #517, #518) |
| `CONVERSATIONS_AUTH_DISABLED`  | _(unset)_                          | Explicit escape hatch to run without the auth guard; loud startup log for visibility (#718). Intended for local dev only.                        |
| `LOG_REDACT`                   | _(unset)_                          | When truthy, conversation and response logs redact user-prompt / agent-response content (#714)                                                   |
| `GEMINI_MAX_HISTORY_BYTES`     | _(gemini only)_                    | Byte ceiling on the JSON session-history file gemini persists per session; older turns are truncated to fit                                      |
| `MCP_ALLOWED_COMMANDS`         | _(per-backend default)_            | Comma-separated allow-list of basenames for stdio entries parsed from `mcp.json`                                                                 |
| `MCP_ALLOWED_COMMAND_PREFIXES` | _(per-backend default)_            | Comma-separated allow-list of absolute-path prefixes for stdio entries                                                                           |
| `MCP_ALLOWED_CWD_PREFIXES`     | _(per-backend default)_            | Comma-separated allow-list of working-directory prefixes for stdio entries (rejections counted on `backend_mcp_command_rejected_total`)          |
| `TASK_STORE_PATH`              | _(unset)_                          | Path for SQLite A2A task store; defaults to in-memory (state lost on restart)                                                                    |
| `WORKER_MAX_RESTARTS`          | `5`                                | Consecutive crash limit before a critical worker marks the backend not-ready                                                                     |
| `LOG_PROMPT_MAX_BYTES`         | `200`                              | Maximum bytes of the prompt logged at INFO level; `0` suppresses prompt logging entirely                                                         |

Codex-specific runtime knobs include `CODEX_MODEL`, `CODEX_REASONING_EFFORT`, `CODEX_STUB_MODE`, `CODEX_SHELL_ENABLED`,
`CODEX_MEMORY_ENABLED`, `CODEX_MEMORY_ROOT`, and `CODEX_SESSION_STORE_PATH`. `CODEX_MEMORY_ROOT` bounds the Codex memory
file tools; paths passed to those tools are relative to that root and cannot escape it.

## Metrics

When `METRICS_ENABLED` is set, Prometheus metrics are served at `/metrics` on a **dedicated port** (9000 by default,
configurable via `METRICS_PORT`) on every container. The metrics listener is split from the app listener so
NetworkPolicy and auth posture can diverge cleanly between app traffic and monitoring scrapes.

Each backend exposes `backend_*`-prefixed metrics; **claude is the superset** and peers track placeholders so
cross-backend PromQL joins don't lose label sets. Harness exposes `harness_*`-prefixed infrastructure metrics. The
harness `/metrics` endpoint also aggregates all backend `/metrics` endpoints with a `backend="<id>"` label injected per
sample, so a single scrape captures the full deployment.

For the full catalog, read each component's `metrics.py`. For the rendered view, see `charts/witwave/dashboards/`
(Grafana sidecar) and `charts/witwave/templates/prometheusrule.yaml` (default alerts).

```bash
curl -s http://localhost:9000/metrics | head
```

## Prompt env-var interpolation (#473)

Scheduler prompt bodies (`HEARTBEAT.md`, `jobs/*.md`, `tasks/*.md`, `triggers/*.md`, `continuations/*.md`) support
`{{env.VAR}}` interpolation so the same markdown can ship across dev / staging / prod without forking:

```yaml
# jobs/daily-status.md
---
schedule: "0 9 * * *"
---
Send a daily status update. Environment: {{env.DEPLOYMENT_ENV}}.
Dashboard: https://{{env.DASHBOARD_HOST}}/team.
```

Two env vars control the feature, both set on the harness container:

| Variable               | Default | Description                                                                                             |
| ---------------------- | ------- | ------------------------------------------------------------------------------------------------------- |
| `PROMPT_ENV_ENABLED`   | unset   | Master toggle. When unset/false, prompt bodies pass through verbatim. Operators opt in.                 |
| `PROMPT_ENV_ALLOWLIST` | empty   | Comma-separated prefixes or globs (`WITWAVE_*,DEPLOY_*`). References outside the allowlist become `""`. |

Missing vars (and non-allowlisted references) are substituted with an empty string and a warning is logged once per
variable. For triggers specifically, interpolation is applied to the operator-authored `.md` body **only** — inbound
HTTP bodies are never interpolated, so callers who can hit the trigger endpoint cannot use the template engine to read
local env vars.

## Outbound Webhooks

Webhooks fire after a prompt completes. Each webhook subscription is a markdown file under `.witwave/webhooks/` with
frontmatter fields:

| Field                | Required | Description                                                                        |
| -------------------- | -------- | ---------------------------------------------------------------------------------- |
| `name`               | yes      | Subscription name (used in metrics labels)                                         |
| `url`                | yes\*    | POST target URL                                                                    |
| `url-env-var`        | yes\*    | Environment variable holding the URL (alternative to `url`)                        |
| `notify-when`        | no       | `always`, `on_success` (default), or `on_error`                                    |
| `notify-on-kind`     | no       | Glob list of prompt kinds to match (e.g. `a2a`, `job:*`, `heartbeat`); default `*` |
| `notify-on-response` | no       | Glob list of patterns matched against the response text; default `*`               |
| `secret`             | no       | HMAC secret — adds `X-Hub-Signature-256` header when set                           |
| `content-type`       | no       | `Content-Type` header; default `application/json`                                  |

\* Either `url` or `url-env-var` is required.

### URL safety (#524)

- The `url:` template may only reference the built-in variables listed below. `{{env.VAR}}` references and
  extraction-defined variables are **not** substituted in the URL field — env-derived URLs must be placed in a single
  env var and read via `url-env-var`. **Migration:** any webhook previously using `url: http://{{env.FOO}}/…` must
  switch to `url-env-var: FOO` — render fails loudly otherwise.
- Only `http` and `https` URLs are accepted. Schemes like `file://`, `gopher://`, `ftp://` are rejected.
- URLs whose host is a loopback / link-local / private / reserved IP literal (e.g. `127.0.0.1`, `169.254.169.254`,
  `10.0.0.5`) are rejected to prevent SSRF to cloud metadata endpoints and internal services. Operators can opt specific
  internal hosts into the allow-list via the `WEBHOOK_URL_ALLOWED_HOSTS` env var on harness (comma-separated `host` or
  `host:port` entries).

The markdown body is the POST payload. Use `{{variable}}` placeholders for substitution in the body and header values
(not in the URL — see above):

| Variable               | Value                                          |
| ---------------------- | ---------------------------------------------- |
| `{{agent}}`            | Agent name (e.g. `iris`)                       |
| `{{kind}}`             | Prompt kind (`a2a`, `heartbeat`, `job:<name>`) |
| `{{session_id}}`       | Session/context ID                             |
| `{{source}}`           | Source name (job name, trigger endpoint, etc.) |
| `{{model}}`            | Model used for the prompt                      |
| `{{success}}`          | `True` or `False`                              |
| `{{error}}`            | Error message, or empty string on success      |
| `{{response_preview}}` | First 2048 chars of the response text          |
| `{{duration_seconds}}` | Prompt execution time in seconds               |
| `{{timestamp}}`        | ISO 8601 UTC timestamp of delivery             |
| `{{delivery_id}}`      | UUID unique to this delivery attempt           |

If the body is empty, a default JSON envelope is sent.

## Observability

Prometheus metrics are opt-in per-agent; see `charts/witwave` values for `metrics.*`, `serviceMonitor.*`, and
`podMonitor.*`.

Distributed tracing (OpenTelemetry) is also opt-in and spans harness + backends + operator when enabled. The pod-side
SDK bootstraps already honour the standard OTel env vars (`shared/otel.py`, `operator/internal/tracing/otel.go`); the
Helm charts own the wiring end-to-end (#634):

- `charts/witwave` — `observability.tracing.enabled` + `observability.tracing.collector.enabled` deploys an in-cluster
  OpenTelemetry Collector and points every agent pod at it. Set `observability.tracing.endpoint` to forward to an
  out-of-band collector instead.
- `charts/witwave-operator` — matching `observability.tracing.*` block; wire the same endpoint to trace the reconciler
  alongside the agents.

See `charts/witwave/README.md` → "Enabling distributed tracing" for Jaeger and Tempo recipes.
