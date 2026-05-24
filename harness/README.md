# harness

harness is the orchestration layer for the autonomous agent platform. It owns no LLM of its own — its job is to receive
work, route it to the right backend, and coordinate everything around that: scheduling, triggering, chaining, webhooks,
and observability.

## What it does

Every named agent (iris, nova, kira, …) runs one instance of this image alongside its backend containers. harness acts
as the single entry point for all inbound requests and all scheduled work.

**A2A relay** — Receives A2A JSON-RPC requests and forwards them to the backend configured for the `a2a` routing slot.
Returns the response verbatim. External callers always target witwave; they never talk to backends directly.

**Heartbeat scheduler** — Fires a prompt on a cron schedule defined in `HEARTBEAT.md`. Used to give an agent a regular
opportunity to reflect, check in, or take proactive action.

**Job scheduler** — Reads `jobs/*.md` files with cron frontmatter and fires each on its schedule. Jobs are stateless —
each run gets its own session unless a persistent session ID is configured.

**Task scheduler** — Reads `tasks/*.md` files with calendar-style frontmatter (days of week, time window, date range).
Supports loop mode (repeated firing within a window), checkpoint recovery after restarts, and deterministic session IDs
per agent+task+date.

**Trigger handler** — Serves `POST /triggers/{endpoint}` HTTP endpoints defined in `triggers/*.md`. Validates requests
via HMAC-SHA256 (GitHub-style) or bearer token, dispatches the payload as a prompt to the backend, and returns 202
immediately.

**Continuation runner** — Reads `continuations/*.md` and fires a follow-up prompt whenever a named upstream (job, task,
trigger, a2a, or another continuation) completes. Supports conditional firing (on success/error), substring matching on
the response, and optional delay. Enables prompt chaining without hardcoded sequences.

**Webhook dispatcher** — Reads `webhooks/*.md` and delivers outbound HTTP notifications when work completes. Supports
glob-based filtering, optional LLM extraction passes, HMAC signing, and retry with exponential backoff.

**Agent discovery** — Exposes `GET /agents` (own card + all backend cards). Also exposes
`GET /.well-known/agent-triggers.json` (array of all enabled trigger descriptors with endpoint, name, description, and
session ID). The harness only speaks for itself; cross-agent routing lives in the dashboard pod (#470) — the legacy
`/team`, `/proxy/<name>`, `/conversations/<name>`, and `/trace/<name>` endpoints were retired in beta.46.

**Scheduler discovery** — Exposes `GET /jobs`, `GET /tasks`, `GET /webhooks`, `GET /continuations`, `GET /triggers`, and
`GET /heartbeat`, each returning a structured snapshot of all currently registered items of that type (name,
schedule/window/filters, backend, running or active-fire counts).

**Metrics** — Aggregates Prometheus metrics from all backends at `/metrics` and exposes its own scheduler/queue/routing
metrics.

## Key files

| File                     | Purpose                                                                                     |
| ------------------------ | ------------------------------------------------------------------------------------------- |
| `main.py`                | Starlette HTTP server; wires all routes and starts all background loops                     |
| `executor.py`            | Core routing engine; maintains an LRU session cache; dispatches prompts to backends         |
| `bus.py`                 | Async message queue with deduplication and backpressure                                     |
| `heartbeat.py`           | Periodic heartbeat loop                                                                     |
| `jobs.py`                | Cron-based job scheduler with checkpoint recovery                                           |
| `tasks.py`               | Calendar-based task scheduler with window/loop support                                      |
| `triggers.py`            | Inbound HTTP trigger registration and dispatch                                              |
| `continuations.py`       | Conditional follow-up chaining after upstream completion                                    |
| `webhooks.py`            | Outbound notification delivery                                                              |
| `backends/config.py`     | Loads and hot-reloads `backend.yaml`                                                        |
| `backends/a2a.py`        | Forwards requests to a remote A2A backend over HTTP/JSON-RPC with retry on transient errors |
| `tracing.py`             | W3C trace-context (`traceparent`) parse/mint/child helpers (#468)                           |
| `metrics.py`             | Prometheus metric definitions                                                               |
| `metrics_proxy.py`       | Aggregates backend /metrics with backend= label injection                                   |
| `conversations_proxy.py` | Concurrently fetches and merges /conversations and /trace from all backends                 |
| `sqlite_task_store.py`   | SQLite-backed task store (used when TASK_STORE_PATH is set)                                 |
| `utils.py`               | Frontmatter parser, duration parser, shared helpers                                         |

## Configuration

All configuration is file-based and hot-reloaded — no restart required for most changes.

**`backend.yaml`** — Which backend handles each concern. Routing slots: `default`, `a2a`, `heartbeat`, `job`, `task`,
`trigger`, `continuation`. Each slot can specify a backend ID and an optional model override.

**`HEARTBEAT.md`** — Frontmatter: `schedule` (cron), `agent`/`model` overrides, `consensus` (list of `{backend, model?}`
entries to fan out to; empty list disables), `max-tokens` (per-dispatch token budget). Body: the heartbeat prompt.

**`jobs/*.md`** — Frontmatter: `schedule` (cron), `session` (optional fixed ID), `agent`/`model` overrides, `consensus`
(list of `{backend, model?}` entries; supports glob patterns). Body: the prompt.

**`tasks/*.md`** — Frontmatter: `days` (e.g. `mon-fri`), `start`/`end` time window, `loop`/`gap` for repeated firing,
optional date range, `consensus` (list of `{backend, model?}` entries; supports glob patterns). Body: the prompt.

**`triggers/*.md`** — Frontmatter: `endpoint` (URL slug), `secret-env-var` (name of env var holding the HMAC key),
`agent`/`model` overrides, `consensus` (list of `{backend, model?}` entries; supports glob patterns). Body: system
context prepended to the inbound payload.

**`continuations/*.md`** — Frontmatter: `continues-after` (upstream kind pattern or list of patterns; supports `fnmatch`
glob patterns, e.g. `job:*` to match any job; when a list is given, the continuation fires only after **all** listed
upstreams have completed in the same session — fan-in; state is tracked per session and reset after each fire),
`on-success`/`on-error`, `trigger-when` (substring match on upstream response), `delay`, `agent`/`model` overrides,
`consensus` (list of `{backend, model?}` entries; supports glob patterns), `max-tokens` (per-dispatch token budget),
`max-concurrent-fires` (cap on simultaneous in-flight fires; default `5`). Body: the follow-up prompt.

**`webhooks/*.md`** — Frontmatter: `url` or `url-env-var` (destination), `notify-on-kind` (glob filter),
`signing-secret-env-var` (HMAC key), `extract` (prompt for LLM extraction pass). Body: webhook payload template.

## Tracing

harness propagates W3C [Trace Context](https://www.w3.org/TR/trace-context/) end-to-end without depending on any
external tracing SDK (#468). This is the smallest-viable building block for cross-agent correlation; a later issue
(#469) can layer full OpenTelemetry on top without changing call sites.

**Inbound.** On every A2A request, `executor.execute()` reads the `traceparent` value from `message.metadata` (the A2A
SDK surfaces HTTP headers there, not on the raw request). If valid, the context is continued; otherwise a fresh
`trace_id`/`span_id` pair is minted. The `harness_a2a_traces_received_total{has_inbound=true|false}` counter records
which path was taken.

**Internal.** Scheduled work (heartbeats, jobs, tasks, triggers, continuations) flows through the message bus. Each
`Message` carries an optional `trace_context`; when absent, `process_bus` mints a fresh one so every backend call has a
`trace_id` even for internally scheduled work. The context is also stamped onto conversation log entries
(`trace_id`/`span_id` fields in `conversation.jsonl`) so external log tools can join it with downstream traces.

**Outbound.** Every call through `backends/a2a.py` mints a child span_id and sends it to the downstream backend both as
an HTTP `traceparent` header and inside the JSON-RPC `metadata.traceparent` field (the latter handles backends that only
see the JSON envelope). Webhook deliveries (`webhooks.py`) stamp the same header, so external receivers see the harness
as the immediate parent and stay correlated with the original trace.

**Format.** `trace_id` is 32 hex chars (128-bit), `parent_id`/`span_id` is 16 hex chars (64-bit), flags is always `01`
(sampled). See `tracing.py` for `parse_traceparent`, `new_context`, `context_from_inbound`, and `TraceContext.child()`.

**OpenTelemetry (opt-in).** Set `OTEL_ENABLED=true` to layer OTLP/HTTP span export on top of the W3C plumbing (#469).
When enabled, the harness creates a real server span for every A2A request, client spans for every outbound backend call
and webhook delivery, and the per-backend `execute()` continues the trace via its own server span. Spans are exported
using the standard OTel env vars:

- `OTEL_EXPORTER_OTLP_ENDPOINT` — default `http://localhost:4318`
- `OTEL_SERVICE_NAME` — default `harness`
- `OTEL_TRACES_SAMPLER` — default `parentbased_always_on`

Resource attributes `service.name`, `agent`, `agent_id`, and `backend` are populated automatically from the container's
environment. The `tracing.py` module re-exports the shared OTel helpers from `shared/otel.py`, so backends use the same
bootstrap without code duplication. When `OTEL_ENABLED` is falsy, the OTel call sites are no-ops and only the
lightweight W3C propagation runs.

## Environment variables

Operator-tunable knobs (#960). Unless noted, these are safe to leave at the default; they exist so production
deployments can adjust behaviour without a code change.

| Variable                      | Default                      | Purpose                                                                                                                                                                                                                                                                                                                                               |
| ----------------------------- | ---------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `AGENT_NAME`                  | `witwave`                    | Agent identity; surfaced in conversation logs, Prometheus labels, and cross-agent manifest entries.                                                                                                                                                                                                                                                   |
| `HARNESS_PORT`                | `8000`                       | App listener port.                                                                                                                                                                                                                                                                                                                                    |
| `METRICS_ENABLED`             | `false`                      | Enable the dedicated Prometheus listener on `METRICS_PORT`.                                                                                                                                                                                                                                                                                           |
| `METRICS_PORT`                | `9000`                       | Dedicated metrics/internal-events listener port. `/internal/events/hook-decision` binds here when enabled (#924).                                                                                                                                                                                                                                     |
| `MANIFEST_PATH`               | `/home/agent/manifest.json`  | Team manifest JSON file listing all agents in the deployment.                                                                                                                                                                                                                                                                                         |
| `BACKENDS_READY_WARN_AFTER`   | `120`                        | Seconds of unready backends before the warmup watcher logs a warning; also shapes the warmup-shield 503 path (#785, #925).                                                                                                                                                                                                                            |
| `HOOK_POST_MAX_INFLIGHT`      | `64`                         | Cap on simultaneous hook.decision POSTs from backend → harness; excess calls are shed and counted (#878, #931).                                                                                                                                                                                                                                       |
| `HOOK_EVENTS_AUTH_TOKEN`      | _(unset — fail-safe reject)_ | Canonical bearer token required on `/internal/events/hook-decision` (bound to the metrics listener, #924). `HARNESS_EVENTS_AUTH_TOKEN` is accepted as a back-compat alias with a deprecation warning (#859). When unset every request is 401'd; no implicit fallback to `TRIGGERS_AUTH_TOKEN` (#700, #933).                                           |
| `TRIGGERS_AUTH_TOKEN`         | _(unset)_                    | Bearer token required on `/triggers/{endpoint}` POSTs.                                                                                                                                                                                                                                                                                                |
| `ADHOC_RUN_AUTH_TOKEN`        | _(unset)_                    | Bearer token required on `/heartbeat/run`, `/jobs/{n}/run`, `/tasks/{n}/run`, and `/validate`. Scheduler run handlers honour the `backends_ready` warmup shield — requests during startup return 503 rather than racing the executor (#925, #955). Advertised in `/.well-known/agent-runs.json` (#956).                                         |
| `CONVERSATIONS_AUTH_TOKEN`    | _(unset)_                    | Bearer token required on harness read/observe endpoints such as `/conversations`, `/trace`, `/api/traces`, and `/events/stream`. Empty token fails closed unless `CONVERSATIONS_AUTH_DISABLED=true` is set.                                                                                                                                           |
| `SESSION_ID_SECRET`           | _(unset — permissive)_       | Server-side HMAC key for `derive_session_id` (#867). When set, session IDs are bound to `caller_identity` so a caller cannot hijack another caller's session. Leave unset only in single-tenant dev; set to a 256-bit random value in production. Rotating the secret invalidates in-flight session IDs — coordinate rotation with a backend restart. |
| `CORS_ALLOW_ORIGINS`          | _(empty)_                    | Comma-separated list of allowed Origins on non-internal endpoints. Wildcard `*` is not accepted when ad-hoc-run / trigger tokens are in use (#927).                                                                                                                                                                                                   |
| `LOG_TRACE_CONTENT_MAX_BYTES` | `16384`                      | Per-entry cap on `tool_result` content serialised into trace logs (#939).                                                                                                                                                                                                                                                                             |
| `PROMPT_ENV_MAX_BYTES`        | `65536`                      | Cap on the size of the resolved prompt environment variables merged into dispatched prompts.                                                                                                                                                                                                                                                          |
| `OTEL_ENABLED`                | `false`                      | Layer full OpenTelemetry span export on top of the W3C plumbing (#469).                                                                                                                                                                                                                                                                               |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | `http://localhost:4318`      | OTLP/HTTP collector endpoint when `OTEL_ENABLED=true`.                                                                                                                                                                                                                                                                                                |
| `OTEL_SERVICE_NAME`           | `harness`                    | Service name on exported spans.                                                                                                                                                                                                                                                                                                                       |
| `OTEL_TRACES_SAMPLER`         | `parentbased_always_on`      | OTel trace sampler configuration.                                                                                                                                                                                                                                                                                                                     |

Backend-targeted URL overrides follow the `A2A_URL_<ID_UPPERCASED_WITH_UNDERSCORES>` convention (e.g.
`A2A_URL_IRIS_CLAUDE`) and let a single `backend.yaml` work across Kubernetes Service DNS and localhost-sidecar
deployment shapes. See the repo root `AGENTS.md` under "Routing configuration" for the full list.

## Runtime

harness is a Docker container. It mounts the agent's `.witwave/` directory for configuration and writes conversation and
trace logs to individual files under `logs/`. The `MANIFEST_PATH` environment variable points to the team manifest
(`manifest.json`), which lists all agents in the deployment by name and URL.

## Phased shutdown (#861, #923)

`executor.close()` no longer runs as a single monolithic teardown. The shutdown path now splits cleanly into
`drain_background()` (phases 1–3) and `close_backends()` (phase 4) so a misbehaving long-lived worker cannot wedge the
whole process on SIGTERM:

1. **Phase 1 — stop accepting new work.** The A2A listener, trigger handler, and ad-hoc run endpoints flip to 503 and
   the scheduler wakeup loops are cancelled.
2. **Phase 2 — drain in-flight dispatches.** The heartbeat, job, task, continuation, and webhook bus consumers are
   allowed to finish their current unit of work within `preStop.delaySeconds`.
3. **Phase 3 — drain background runners.** `WebhookRunner.close()` was moved into this phase (#923) so pending outbound
   deliveries either complete or are persisted as `pending` before the backend connections go away.
4. **Phase 4 — close backends.** A2A HTTP clients, trace exporters, and the internal bus are closed last.

Keep `terminationGracePeriodSeconds > preStop.delaySeconds` (the chart enforces this — see `charts/witwave/README.md`).
Breaking the ordering — for example by teardown-ing `WebhookRunner` ahead of the bus consumers — dropped in-flight
deliveries silently before #923.

## Internal events (hook-decision) transport

Backends POST tool-audit decisions back to the harness over an internal channel.

- **Endpoint.** `POST /internal/events/hook-decision` is bound to the dedicated metrics listener on `METRICS_PORT` (not
  the app port, #924). It is never exposed through the Service — traffic stays within the pod's localhost.
- **Auth.** Canonical env var is `HOOK_EVENTS_AUTH_TOKEN` (#859, #933). The old name `HARNESS_EVENTS_AUTH_TOKEN` is
  still accepted as a back-compat alias and logs a deprecation warning on startup; remove the alias once a rollout has
  completed. Unset / empty = every request is 401'd; there is no implicit fallback to `TRIGGERS_AUTH_TOKEN`.
- **Length caps.** Per-field byte caps on the POST body (`tool_name`, `tool_input`, `tool_response_preview`, etc., #924)
  prevent an attacker-controlled tool output from driving harness memory pressure through the internal channel.
- **Backpressure.** `HOOK_POST_MAX_INFLIGHT` caps concurrent POSTs; the async dispatcher queue on codex additionally
  sheds overflow into `backend_hook_post_shed_total` (#928).
