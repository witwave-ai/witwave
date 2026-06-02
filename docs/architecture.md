# Architecture

Last updated: 2026-06-02

---

## Purpose

This document describes the current architecture of the autonomous agent platform — how the runtime is structured, how
agents are configured and deployed, how they communicate, and how the skill and issue layers are organized. It also
captures known architectural patterns from the competitive landscape and serves as the reference for evaluating large
structural changes.

When a proposed change is architectural in nature — a new runtime primitive, a significant repo restructuring, a new
protocol layer, a shift in deployment model — it should be discussed here first before becoming a `feature` issue.

---

## Repository Structure

Full file-by-file layout lives in [`AGENTS.md`](../AGENTS.md#project-structure) — that's the canonical reference for
"what file is where" and stays current because coding agents read it first.

At the top level, the repo is split into two buckets plus shared infrastructure:

- **Platform infrastructure** — `harness/`, `backends/{claude,openai,gemini,echo}/`, `operator/`, `tools/` (MCP servers:
  `kubernetes`, `helm`, `prometheus`), `charts/{witwave,witwave-operator}/`, `shared/`.
- **Client surfaces** (under `clients/`) — `clients/dashboard/` (Vue 3 web UI), `clients/ww/` (Go CLI).
- **Agent configs** (`.agents/`) — per-named-agent filesystem config that gets mounted into the platform containers.
  `self/` for the self-managing agents that maintain this repo; `test/` for disposable test fixtures (`bob`, `fred`,
  plus scaffold-only agents such as `jack` and `luke`).
- **Docs + skills** — `docs/` (this document + product-vision + competitive-landscape + event wire contract + smoke
  tests + prompt-type reference), `.claude/skills/` (user-invokable Claude Code skills that drive discovery / refinement
  / implementation loops), `.github/ISSUE_TEMPLATE/`.

The split between infrastructure and clients is intentional: infrastructure is the platform, clients are interchangeable
ways to observe + interact with it. Adding a TUI or a mobile app goes under `clients/`, not as a peer of `harness/`.

---

## Runtime Architecture

### Overview

Each named agent is a cluster of containers:

1. **harness** — the infrastructure layer. Receives external A2A requests, fires heartbeats, runs jobs/tasks, handles
   inbound triggers, fires outbound webhooks, and dispatches continuations. Owns no LLM itself.
2. **claude** (per agent) — a standalone A2A server backed by the Claude Agent SDK. Owns session state, memory, and
   conversation logging.
3. **openai** (per agent) — a standalone A2A server backed by the OpenAI Agents SDK. Same interface as claude.
4. **gemini** (per agent) — a standalone A2A server backed by the Google Gemini SDK. Same interface as claude.
5. **echo** (optional, per agent) — a zero-dependency stub A2A server. Returns a canned response quoting the caller's
   prompt; requires no API keys. Ships as the hello-world default for `ww agent create` and doubles as the reference
   implementation of the common A2A backend contract (see `backends/echo/README.md`).

```text
External A2A caller
        │
        ▼
┌───────────────────────────────────────────┐
│               harness container          │
│                                           │
│  ┌──────────┐  ┌──────────┐  ┌─────────┐ │
│  │Heartbeat │  │  Prompt  │  │  A2A    │ │
│  │Scheduler │  │Scheduler │  │ Server  │ │
│  └────┬─────┘  └────┬─────┘  └────┬────┘ │
│       │              │             │      │
│       └──────────────┴─────────────┘      │
│                      │                   │
│              ┌───────▼────────┐           │
│              │  Message Bus   │           │
│              └───────┬────────┘           │
│                      │                   │
│              ┌───────▼────────┐           │
│              │   Executor     │           │
│              │ (reads routing)│           │
│              └───────┬────────┘           │
│                      │                   │
│              ┌───────▼────────┐           │
│              │  A2ABackend    │           │
│              │ (HTTP forward) │           │
└──────────────┼────────────────┼───────────┘
               │                │
               ▼                ▼
   ┌──────────────────┐  ┌──────────────────┐
   │  claude       │  │  openai        │
   │  (Claude SDK)    │  │  (OpenAI SDK)    │
   │                  │  │                  │
   │  /.well-known/   │  │  /.well-known/   │
   │  agent.json      │  │  agent.json      │
   │  / (A2A)         │  │  / (A2A)         │
   │  /health         │  │  /health         │
   │  /metrics        │  │  /metrics        │
   └──────────────────┘  └──────────────────┘
```

### harness Components

**`main.py`** — The entrypoint. Constructs the `MessageBus`, `AgentExecutor`, `HeartbeatRunner`, `JobRunner`,
`TaskRunner`, `TriggerRunner`, `ContinuationRunner`, `WebhookRunner`, and A2A HTTP server, then runs all of them
concurrently via `asyncio.gather`. A `_guarded` wrapper catches crashes in any background task and restarts it with a
delay.

**`bus.py`** — An async `asyncio.Queue`-backed message bus. Deduplicates in-flight messages by `kind` — if a heartbeat
message is already in-flight, a second heartbeat is dropped rather than queued.

**`heartbeat.py`** — Watches `HEARTBEAT.md` for changes via `awatch`. On each heartbeat interval, enqueues a heartbeat
message on the bus. The executor forwards the heartbeat prompt to the backend named in `routing.heartbeat`.

**`jobs.py`** — Reads `*.md` files from the `jobs/` directory. Each file has YAML frontmatter defining a cron
`schedule`. Fires on schedule by enqueuing messages on the bus. Routed via `routing.job`.

**`tasks.py`** — Reads `*.md` files from the `tasks/` directory. Each file has calendar frontmatter (`days`,
`window-start`, `window-duration`, etc.). Fires within the defined window. Routed via `routing.task`.

**`triggers.py`** — Reads `*.md` files from the `triggers/` directory and serves a `POST /triggers/{endpoint}` HTTP
route for each. Dispatches the request payload as a prompt immediately (202 response). Routed via `routing.trigger`.

**`continuations.py`** — Reads `*.md` files from the `continuations/` directory. After any named upstream (job, task,
trigger, a2a, or another continuation) completes, fires a follow-up prompt. Enables prompt chaining. Routed via
`routing.continuation`.

**`webhooks.py`** — Reads `*.md` files from the `webhooks/` directory. After any prompt completes, evaluates all
subscriptions against three filters (`notify-when`, `notify-on-kind`, `notify-on-response`). Fires matching
subscriptions as async fire-and-forget HTTP POST tasks.

**`executor.py`** — Receives `BusMessage` objects from the bus, resolves the target backend from `routing.*`, and calls
`backend.run_query(prompt, session_id, is_new)`. When `message.consensus` is a non-empty list of `ConsensusEntry`
objects, fans out to each matched `(backend, model)` pair in parallel and aggregates the responses (majority vote for
binary yes/no answers; synthesis pass via the default backend for freeform responses). The same backend can be targeted
twice with different models — each `(backend, model)` pair is a distinct call. On completion, calls
`on_prompt_completed()` which notifies the `ContinuationRunner` and `WebhookRunner`.

**`backends/a2a.py`** — Implements `AgentBackend.run_query` by constructing an A2A `message/send` JSON-RPC payload and
forwarding it to the backend URL. Retries transient errors (HTTP 429/502/503/504 and connection failures) up to
`A2A_BACKEND_MAX_RETRIES` times (default 3, must be >= 1) with exponential backoff. The backend URL can be overridden
per-backend via an environment variable (`A2A_URL_<ID_UPPERCASED>`), enabling Kubernetes sidecar or separate-pod
deployments without config file changes.

**`metrics_proxy.py`** — Fetches `/metrics` from each configured backend on the dedicated metrics port (`METRICS_PORT`,
default 9000 — the backend's app URL is rewritten to swap the port). Injects a `backend="<id>"` label on every sample
line. The harness metrics listener merges its own metrics with all backend metrics, providing a single scrape target for
the full deployment (redundant with PodMonitor-per-container scraping, but preserved for anyone curl-ing the harness
directly).

### Dedicated metrics listener (`shared/metrics_server.py`)

Every container in the stack — harness, each backend, each MCP tool — runs `/metrics` on a **dedicated port** (9000 by
default, set via `METRICS_PORT` env / `metrics.port` chart value / `WitwaveAgentSpec.MetricsPort` CRD field) separate
from the app listener (#643). The split lets NetworkPolicy and auth posture diverge cleanly between app traffic (A2A,
triggers, conversations, MCP) and monitoring scrapes. `shared/metrics_server.py` exposes two entry points: an
asyncio-task variant for containers that own the main event loop (harness, backends), and a daemon-thread variant for
FastMCP-hosted containers (MCP tools) that don't.

### Backend Components (claude, openai, gemini, echo)

All four backends share identical A2A API surface; the three LLM-backed backends (claude, openai, gemini) also share
structure and differ only in their LLM SDK. The echo backend is deliberately stripped — it has no MCP, no conversation
persistence, no hooks, no session binding — because its role is zero-dependency onboarding, not LLM work.

**`main.py`** — Builds the A2A `AgentCard` from the mounted `agent-card.md` file, wires the `AgentExecutor` and task
store (`SqliteTaskStore` when `TASK_STORE_PATH` is set, `InMemoryTaskStore` otherwise), and serves the full Starlette
application with routes for `/.well-known/agent.json`, `/` (A2A), `/health`, `/metrics`, and `/mcp` (MCP JSON-RPC
server).

**`executor.py`** — Implements the A2A `AgentExecutor` interface. Manages session continuity using the session ID passed
in the A2A request metadata. Writes `conversation.jsonl` to the mounted logs directory.

**`metrics.py`** — Prometheus metric definitions with `backend_*` prefix. `claude` exposes a superset including tool
call, context window, and MCP metrics; `openai` also exposes tool-call and context-window metrics; `gemini` exposes
context-window metrics. All four share the common `backend_*` baseline set; `echo` implements only that baseline and
documents it as the reference definition of what a well-behaved backend must emit.

---

## Configuration Model

Agent identity and behavior are entirely file-based. No identity is baked into any image.

### harness config files

| File                 | Location                  | Purpose                                                 |
| -------------------- | ------------------------- | ------------------------------------------------------- |
| `agent-card.md`      | `.witwave/`               | A2A identity description for the harness agent card     |
| `backend.yaml`       | `.witwave/`               | Backend definitions and routing                         |
| `HEARTBEAT.md`       | `.witwave/`               | Heartbeat schedule and prompt                           |
| `jobs/*.md`          | `.witwave/jobs/`          | Scheduled jobs — cron frontmatter                       |
| `tasks/*.md`         | `.witwave/tasks/`         | Calendar tasks — days/window frontmatter                |
| `triggers/*.md`      | `.witwave/triggers/`      | Inbound HTTP trigger definitions                        |
| `continuations/*.md` | `.witwave/continuations/` | Continuation definitions — fires on upstream completion |
| `webhooks/*.md`      | `.witwave/webhooks/`      | Outbound webhook subscriptions                          |

### Backend config files

| File        | Location                | Purpose                                                             |
| ----------- | ----------------------- | ------------------------------------------------------------------- |
| `CLAUDE.md` | `/home/agent/.claude/`  | Behavioral instructions injected into the Claude backend at startup |
| `AGENTS.md` | `/home/agent/.openai/`  | Behavioral instructions injected into the OpenAI backend at startup |
| `AGENTS.md` | `/home/agent/.codex/`   | Behavioral instructions injected into the Codex backend at startup  |
| `GEMINI.md` | `/home/agent/.gemini/`  | Behavioral instructions injected into the Gemini backend at startup |
| `memory/`   | `<name>/claude/memory/` | Persistent markdown memory files for Claude backend                 |
| `memory/`   | `<name>/openai/memory/` | Persistent markdown memory files for OpenAI backend                 |
| `memory/`   | `<name>/codex/memory/`  | Bounded Codex memory-tool files or workspace memory mount target    |
| `memory/`   | `<name>/gemini/memory/` | JSON session history for Gemini backend (`sessions/`)               |

Backend-specific `agent-card.md` files may be mounted for direct backend-sidecar discovery, but the Kubernetes Service
for a named agent targets the harness container. The repo's self/test agent configs therefore treat
`.witwave/agent-card.md` as the public agent-card source of truth.

### Key environment variables

**harness:**

| Variable                                    | Default                             | Description                                                                                                                            |
| ------------------------------------------- | ----------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------- |
| `AGENT_NAME`                                | `witwave`                           | Agent display name (e.g. `iris`)                                                                                                       |
| `HARNESS_HOST`                              | `0.0.0.0`                           | Interface the harness binds to                                                                                                         |
| `HARNESS_PORT`                              | `8000`                              | HTTP port the harness listens on                                                                                                       |
| `HARNESS_URL`                               | `http://localhost:$HARNESS_PORT/`   | Public URL published on the A2A agent card                                                                                             |
| `BACKEND_CONFIG_PATH`                       | `/home/agent/.witwave/backend.yaml` | Path to backend routing config                                                                                                         |
| `METRICS_ENABLED`                           | _(unset)_                           | Enable Prometheus `/metrics`                                                                                                           |
| `METRICS_AUTH_TOKEN`                        | _(unset)_                           | Bearer token required to access `/metrics`                                                                                             |
| `METRICS_CACHE_TTL`                         | `15`                                | Seconds to cache aggregated backend metrics between scrapes                                                                            |
| `CONVERSATIONS_AUTH_TOKEN`                  | _(unset)_                           | Bearer token required to access harness read/observe endpoints such as `/conversations`, `/trace`, `/api/traces`, and `/events/stream` |
| `BACKEND_CONVERSATIONS_AUTH_TOKEN`          | _(unset)_                           | Bearer token forwarded to backend `/conversations`, `/trace`, and `/api/traces` endpoints (set if backends require auth)               |
| `TRIGGERS_AUTH_TOKEN`                       | _(unset)_                           | Bearer token for inbound trigger requests (fallback when no per-trigger HMAC secret is set)                                            |
| `CORS_ALLOW_ORIGINS`                        | _(unset)_                           | Comma-separated allowed CORS origins; when unset, all cross-origin requests are denied (logs a warning)                                |
| `TASK_STORE_PATH`                           | _(unset)_                           | Path for SQLite A2A task store; defaults to in-memory                                                                                  |
| `WORKER_MAX_RESTARTS`                       | `5`                                 | Consecutive crash limit before a critical worker marks the agent not-ready                                                             |
| `WEBHOOK_MAX_CONCURRENT_DELIVERIES`         | `50`                                | Maximum number of in-flight webhook delivery tasks across all subscriptions                                                            |
| `WEBHOOK_MAX_CONCURRENT_DELIVERIES_PER_SUB` | `10`                                | Per-subscription cap on concurrent in-flight deliveries; also settable per webhook via `max-concurrent-deliveries` frontmatter         |
| `WEBHOOK_EXTRACTION_TIMEOUT`                | `120`                               | Seconds to wait for a single LLM extraction call inside a webhook delivery                                                             |
| `JOBS_MAX_CONCURRENT`                       | `0` (unlimited)                     | Maximum number of jobs that may run concurrently; `0` disables the limit                                                               |
| `TASKS_MAX_CONCURRENT`                      | `0` (unlimited)                     | Maximum number of tasks that may run concurrently; `0` disables the limit                                                              |
| `TASK_TIMEOUT_SECONDS`                      | `300`                               | Task timeout in seconds, applied to A2A backend requests                                                                               |
| `MANIFEST_PATH`                             | `/home/agent/manifest.json`         | Path to the team manifest file listing all agents by name and URL                                                                      |
| `BACKENDS_READY_WARN_AFTER`                 | `120`                               | Seconds to wait before logging a warning that backends have not become healthy                                                         |
| `LOG_PROMPT_MAX_BYTES`                      | `200`                               | Maximum bytes of the prompt logged at INFO level; `0` suppresses prompt logging entirely                                               |
| `A2A_BACKEND_MAX_RETRIES`                   | `3`                                 | Maximum retry attempts for transient backend errors (429, 502, 503, 504, connection errors); must be >= 1                              |
| `A2A_BACKEND_RETRY_BACKOFF`                 | `1.0`                               | Base backoff in seconds for retry delay (exponential with jitter)                                                                      |
| `A2A_URL_<ID>`                              | _(unset)_                           | Per-backend URL override (e.g. `A2A_URL_IRIS_CLAUDE`)                                                                                  |

**Backends (claude / openai / gemini):**

| Variable                   | Default                        | Description                                                                                  |
| -------------------------- | ------------------------------ | -------------------------------------------------------------------------------------------- |
| `AGENT_NAME`               | `claude` / `openai` / `gemini` | Backend instance name (e.g. `iris-claude`)                                                   |
| `AGENT_OWNER`              | _(same as `AGENT_NAME`)_       | Named agent this backend belongs to (e.g. `iris`); used in metric labels                     |
| `AGENT_ID`                 | `claude` / `openai` / `gemini` | Backend slot identifier; used in metric labels                                               |
| `AGENT_URL`                | `http://localhost:8000/`       | Public A2A endpoint URL reported in agent card                                               |
| `BACKEND_PORT`             | `8000`                         | HTTP port the backend listens on (internal)                                                  |
| `METRICS_ENABLED`          | _(unset)_                      | Enable Prometheus `/metrics`                                                                 |
| `CONVERSATIONS_AUTH_TOKEN` | _(unset)_                      | Bearer token required to access `/conversations`, `/trace`, `/mcp`, and `/api/traces[/<id>]` |
| `TASK_STORE_PATH`          | _(unset)_                      | Path for SQLite A2A task store; defaults to in-memory                                        |
| `WORKER_MAX_RESTARTS`      | `5`                            | Consecutive crash limit before a critical worker marks the backend not-ready                 |
| `LOG_PROMPT_MAX_BYTES`     | `200`                          | Max bytes of the prompt logged at INFO level; `0` suppresses it entirely                     |

---

## Communication Layer

### A2A Protocol

Agents communicate via the A2A protocol (HTTP/JSON-RPC). External callers always target the **Witwave agent** by its
hostname/port. Witwave reads the `routing.a2a` entry from `backend.yaml` and forwards the request unchanged to the
configured backend. The backend session ID matches the session ID provided by the external caller, preserving
conversation continuity across turns.

Each harness exposes:

- `/.well-known/agent.json` — agent card for discovery
- `/` — task execution endpoint (`message/send`)
- `GET /health/start` — startup probe: 200 once ready, 503 while initializing
- `GET /health/live` — liveness probe: always 200 with `{"status": "ok", "agent": ..., "uptime_seconds": ...}`
- `GET /health/ready` — readiness probe: 200/`{"status": "ready"}`; 503/`{"status": "starting"}` while initializing;
  503/`{"status": "degraded"}` when a backend is unhealthy
- `GET /agents` — own card plus agent cards from all configured backends
- `GET /jobs` — structured snapshot of registered scheduled jobs
- `GET /tasks` — structured snapshot of registered scheduled tasks
- `GET /webhooks` — structured snapshot of registered webhook subscriptions
- `GET /continuations` — structured snapshot of registered continuation items
- `GET /triggers` — structured snapshot of registered inbound trigger endpoints
- `GET /heartbeat` — current heartbeat configuration from `HEARTBEAT.md`
- `GET /conversations` — merged conversation log from all backends
- `GET /trace` — merged trace log from all backends
- `GET /.well-known/agent-triggers.json` — discovery array of all enabled trigger descriptors

Cross-agent aggregation (`/team`, `/proxy/<name>`, `/conversations/<name>`, `/trace/<name>`) was retired in beta.46 —
the dashboard pod fans out directly to each agent's endpoints and owns cross-agent routing (#470).

Each backend exposes the same A2A surface plus:

- `/health/start` — startup probe (#1686): 200 once initial loads complete, 503 `{"status":"starting"}` while warming up
- `/health` — liveness probe: 200 once the process is up
- `/health/ready` — readiness probe: 200 when fully ready, 503 while initializing or boot-degraded
- `/metrics` — Prometheus metrics endpoint
- `/mcp` — MCP JSON-RPC server (`initialize`, `tools/list`, `tools/call`) for MCP hosts (Claude Desktop, Cursor, VS Code
  extensions, etc.)

### Internal Message Bus (harness)

Most internally scheduled work — heartbeat ticks, job/task fires, continuations, and A2A-inbound tasks — flows through
the `MessageBus`. The bus serializes execution: one message processed at a time, deduplicated by kind. HTTP triggers are
the exception: they dispatch through bounded background tasks and return `202 Accepted` immediately, so concurrent
webhook-style deliveries do not wait behind the singleton scheduler lane.

---

## Port Assignments

Operator-created agents follow the `ww` port convention unless a CR explicitly overrides it:

| Container                   | Port       |
| --------------------------- | ---------- |
| harness                     | 8000       |
| first backend sidecar       | 8001       |
| additional backend sidecars | 8002..8050 |
| metrics listener            | 9000       |

Each named agent runs in its own pod with its own localhost, so these ports can be reused across agents. Local smoke
docs may still forward stable laptop ports to the harness Service, for example `localhost:8099 -> svc/bob:8000`, but
that is a client-side convenience rather than an in-cluster port assignment. Bob's OpenAI/Gemini directories remain
parked fixtures so they can be re-enabled deliberately once credentials and budget are available.

---

## Issue and Skill Layer

### GitHub Issue Taxonomy

| Label     | Created by      | Worked by           | Purpose                                                                    |
| --------- | --------------- | ------------------- | -------------------------------------------------------------------------- |
| `bug`     | `bug-discover`  | `bug-implement`     | Defect — code that is broken or behaves incorrectly                        |
| `risk`    | `risk-discover` | `risk-implement`    | Code quality issue — works today but fragile, insecure, or likely to break |
| `gap`     | `gap-discover`  | `gap-implement`     | Missing capability — functionality the system should have but does not     |
| `feature` | humans / agents | `feature-implement` | Intentional enhancement requested by stakeholders                          |

### Develop Loop

The `develop` skill runs a continuous improvement cycle across all issue types:

```text
Phase 1–4:   bug discovery → refinement → approval → implementation
Phase 5–8:   risk discovery → refinement → approval → implementation
Phase 9–12:  gap discovery → refinement → approval → implementation
Phase 13–16: feature discovery → refinement → approval → implementation
Phase 17:    docs refinement
```

---

## Deployment

### How to install

Installation commands live with the artifacts they deploy:

- Local test-team install through `ww` — [`.agents/test/bootstrap.md`](../.agents/test/bootstrap.md)
- Production Witwave agent install (published chart) — [`charts/witwave/README.md`](../charts/witwave/README.md)
- Operator install — [`charts/witwave-operator/README.md`](../charts/witwave-operator/README.md)
- Operator development (`make install` / `make run`) — [`operator/README.md`](../operator/README.md)
- ww CLI — [`clients/ww/README.md`](../clients/ww/README.md)

### Kubernetes is the target

All infrastructure decisions are evaluated against Kubernetes compatibility:

- Health probes follow the three-probe model (`/health/start`, `/health/live`, `/health/ready`) on harness, and
  (`/health/start`, `/health`, `/health/ready`) on backend containers — backends use `/health` (not `/health/live`) for
  the liveness route, but the three-probe shape is consistent across the platform (#1686).
- Configuration injected via env vars and mounted `ConfigMap`/`Secret` volumes.
- Backend URL configurable via `A2A_URL_<ID>` env var — supports same-pod sidecar (`http://localhost:8010`) or
  out-of-pod via Service DNS (`http://claude-svc:8000`) without config file changes.
- Stateless containers at the harness layer (all state lives in backends).
- Standard HTTP endpoints suitable for `Service` and `Ingress`.

Per-agent port assignments live in [`AGENTS.md` → Interacting with Agents](../AGENTS.md#interacting-with-agents).

### git-sync Image

The Helm chart uses an internal git-sync image (`ghcr.io/witwave-ai/images/git-sync`) built from
`helpers/git-sync/Dockerfile`. This image adds `rsync` to the upstream git-sync base image, enabling `rsync --delete`
for correct incremental directory sync. Without rsync, upstream git-sync copies only changed files — deletions and deep
directory removes are not propagated. With rsync, the sync is fully correct: files and directories are added, modified,
and deleted at all depths to match the source exactly.

---

## Architectural Patterns

### Patterns in Use

**Witwave as pure infrastructure.** harness owns the scheduling and relay layer; LLM execution is the sole
responsibility of backend containers. This separation allows each layer to evolve independently and enables swapping LLM
backends without touching the scheduler.

**File-based configuration over compiled-in identity.** A new agent is a new directory with mounted files — not a new
image build. The same image serves any number of identities.

**Named routing over round-robin.** `backend.yaml` routes each concern (a2a, heartbeat, job, task, trigger,
continuation) to a named backend id. Routing is deterministic and explicit — no load-balancing or dynamic selection.

**Per-backend URL override.** The `A2A_URL_<ID>` env var allows the same `backend.yaml` config file to work across
Kubernetes sidecar and separate-pod deployment shapes.

**Message bus serialization.** Scheduled work and A2A-inbound tasks flow through a single async queue per harness
process. This prevents concurrent outbound backend calls for those lanes, enforces deduplication, and provides a single
instrumentation point for scheduler latency and throughput. HTTP triggers intentionally bypass the bus and use bounded
background dispatch so inbound delivery endpoints can acknowledge quickly.

**Guarded restart loop.** Every background task (heartbeat, jobs, tasks, triggers, continuations, webhooks, bus worker)
runs inside `_guarded()` — a crash-restart wrapper that logs the failure, increments a metric, and restarts after a
delay. No task can take down the harness process.

**Skill documents as workflow.** Agent behavior is expressed in markdown skill files, not hardcoded logic. Skills are
hot-swappable without rebuilding the image or restarting the container.

**Theme/slice feature decomposition.** Large features are broken into themes (logical phases) and slices (discrete work
units within a theme). No new theme begins until all slices of the current theme are closed.

### Patterns to Evaluate

The following patterns represent potential architectural directions. Each should be evaluated as an architectural change
proposal before becoming a feature issue:

**Plan-before-code execution mode.** OpenHands (v1.7.0, May 2026) and Devin (2.0+) both offer explicit planning modes,
though neither is a hard enforced two-phase split — both surface a planning step the operator approves before
execution. OpenHands' Plan Mode emits a structured `PLAN.md` the user reviews, then switches to Code Mode for
implementation (source: <https://openhands.dev/blog/openhands-product-update---march-2026>, accessed 2026-06-02).
Devin's Interactive Planning presents an execution plan for confirm/modify before work begins, with v3.0 (2026) adding
dynamic re-planning when the agent hits a roadblock mid-task (source: <https://cognition.ai/blog/introducing-devin-2-2>,
accessed 2026-06-02). The Claude Agent SDK supports `permission_mode="plan"` natively. Applicable to jobs or tasks with
high blast radius.

**In-process custom tools.** The Claude Agent SDK's `@tool()` decorator and `create_sdk_mcp_server()` factory allow
defining tools as plain Python functions inside the harness process — no external MCP server.

**Programmatic subagent definitions.** `AgentDefinition` in `ClaudeAgentOptions` allows defining specialized subagents
programmatically without file-based configuration.

**Hooks system.** The SDK's `HookMatcher` API registers Python callbacks on `PreToolUse`, `PostToolUse`, `Stop`,
`SessionStart`, etc. `PreToolUse` supports `updatedInput` — rewriting tool arguments before execution.

**Structured shared memory.** Competitors use structured persistent memory with semantic search. This project uses flat
markdown files per backend. SQLite FTS5 with LLM-powered summarization is the strongest reference.

**Auto-generated skills.** Hermes Agent writes a new skill document after completing a complex task — a closed learning
loop from execution to capability accumulation.

**Declarative policy engine.** A file-based policy DSL (JSON/YAML) evaluated before every tool call would add guardrails
without requiring Python code changes.

**Webhook-to-trigger chaining.** Outbound webhooks can POST directly to a harness trigger endpoint, enabling
self-contained prompt chains without external infrastructure. A completed job response can fire a webhook that triggers
a second prompt on the same or a different agent.

---

## backend.yaml Reference

`backend.yaml` lives in `.witwave/` and controls which backend handles each concern. It has a top-level `backend:` key
containing an `agents:` list and a `routing:` block.

**Minimal single-backend config:**

```yaml
backend:
  agents:
    - id: claude
      url: http://localhost:8010

  routing:
    default: claude
```

**Multi-backend config with per-concern routing and model overrides:**

```yaml
backend:
  agents:
    - id: claude
      url: http://localhost:8010
      model: claude-opus-4-7

    - id: openai
      url: http://localhost:8011
      model: gpt-5.5

    - id: gemini
      url: http://localhost:8012

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

Routing values can be a plain agent ID string (`default: claude`) or an object with `agent:` and optional `model:`
fields. Model resolution order: per-message override → routing entry model → per-backend config model.

The `url` for any backend can be overridden at deploy time via an environment variable named
`A2A_URL_<ID_UPPERCASED_WITH_UNDERSCORES>` — for example, `A2A_URL_IRIS_CLAUDE`. This lets the same `backend.yaml` work
across Kubernetes service DNS and localhost-sidecar deployment shapes without modification.

---

## Relationship to Other Docs

| Document                                             | Purpose                                                |
| ---------------------------------------------------- | ------------------------------------------------------ |
| [product-vision.md](product-vision.md)               | Target audience, design principles, deployment roadmap |
| [competitive-landscape.md](competitive-landscape.md) | Competitor research, gap analysis, research themes     |
| [prompts/README.md](prompts/README.md)               | Prompt type reference (heartbeat, jobs, tasks, etc.)   |
| `README.md`                                          | Quickstart and technical reference                     |
| `AGENTS.md`                                          | Canonical repo instructions for all coding agents      |
