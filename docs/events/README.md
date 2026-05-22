# Event stream schema (#1110)

This directory is the contract between the harness event stream (`GET /events/stream`) and any client consuming it — the
web dashboard today, a future CLI / iOS / Android client tomorrow. Schemas are **internal** (not published as a public
contract) but stable within the repo.

## Wire format

Server-Sent Events (SSE). Each message is a JSON object serialised on a single `data:` line. Every message carries a
monotonic `id:` so reconnecting clients can pass `Last-Event-ID` and receive missed events from the server's in-memory
ring.

```text
event: job.fired
id: 4217
data: {"type":"job.fired","version":1,"id":"4217","ts":"2026-04-19T12:00:00.003Z","agent_id":"iris","payload":{"name":"daily-report","schedule":"0 9 * * *","duration_ms":420,"outcome":"success"}}

: keepalive

```

Keepalives (`: keepalive\n\n`) fire every 15s so intermediate proxies don't close idle connections.

## Envelope

Every event has the same top-level envelope:

| Field      | Type              | Notes                                                                     |
| ---------- | ----------------- | ------------------------------------------------------------------------- |
| `type`     | string            | Dotted event type, e.g. `job.fired`, `webhook.delivered`.                 |
| `version`  | integer           | Per-type schema version. Starts at `1`. Bumped on incompatible changes.   |
| `id`       | string            | Monotonic id within the harness process. Used for `Last-Event-ID` resume. |
| `ts`       | string (RFC 3339) | Event timestamp at the emitter, UTC, millisecond precision.               |
| `agent_id` | string \| null    | Scoped event → agent name (e.g. `iris`). Harness-wide event → `null`.     |
| `payload`  | object            | Type-specific fields, validated against the corresponding schema below.   |

Unknown fields in an older client MUST be ignored (forward compat). The server never emits a `type` its running config
doesn't know how to validate; validation is best-effort at emit time with a `WARN` + counter bump on failure rather than
a hard error.

**Forward compatibility (#1286, matches runtime validator from #1145):** Both the envelope and every per-type payload
use `additionalProperties: true` in `events.schema.json`. A publisher that adds a new optional field does NOT require a
lockstep validator update — older clients still validate the envelope and ignore fields they do not recognise. Renaming,
removing, or retyping a field remains a version bump per the rules below.

## Versioning

- `version` is a per-`type` integer. Clients match on `type` + fall back on unknown `version` by treating the payload as
  the latest they understand.
- Additive changes to a payload do NOT bump the version (new optional field).
- Renaming / removing / retyping a payload field **does** bump the version. The server emits both the old and new
  versions side-by-side for one release cycle, so old clients keep working.

## Event types

Phase 1 ships eleven harness-emitted types; phase 3 (#1110) adds three backend-emitted types that flow over the
backend→harness event channel (`POST /internal/events/publish`) and are fanned out on the same SSE stream.

Current event catalog:

- `job.fired` — harness jobs scheduler; payload includes `name`, `schedule`, `duration_ms`, `outcome`, and optional
  `error`.
- `task.fired` — harness tasks scheduler; payload includes `name`, `window`, `duration_ms`, `outcome`, and optional
  `error`.
- `heartbeat.fired` — harness heartbeat; payload includes `schedule`, `duration_ms`, `outcome`, and optional `error`.
- `continuation.fired` — harness continuations; payload includes upstream identity, duration, outcome, and optional
  `error`.
- `trigger.fired` — harness triggers; payload includes `name`, `endpoint`, `duration_ms`, `outcome`, and optional
  `error`.
- `webhook.delivered` — harness webhooks; payload includes `name`, `url_host`, `status_code`, and `duration_ms`.
- `webhook.failed` — harness webhooks; payload includes `name`, `url_host`, `reason`, and `duration_ms`.
- `hook.decision` — backend hook decisions carried through `bus.py`; payload includes backend, session hash, tool,
  decision, and optional rule id.
- `a2a.request.received` — harness A2A relay; payload includes `concern` and optional `model`.
- `a2a.request.completed` — harness A2A relay; payload includes `concern`, `outcome`, and `duration_ms`.
- `agent.lifecycle` — harness or backend lifecycle; payload includes backend and lifecycle event.
- `conversation.turn` — backend conversation summary; payload includes session hash, role, content byte count, and
  optional model.
- `conversation.chunk` — backend per-session drill-down only; payload includes session hash, role, sequence, content,
  and finality.
- `tool.use` — backend tool execution; payload includes session hash, tool, duration, outcome, optional result size, and
  optional error.
- `trace.span` — backend trace span summary; payload includes session hash, span name, duration, status, and service.

See `events.schema.json` for the full JSON Schema.

## What is NOT on this stream

- Per-token conversation chunks — drill-down surface, per-backend stream. Phase 3 emits a single `conversation.turn`
  summary event (content_bytes, not raw content) per turn; raw token streams remain per-backend. Phase 4 (#1110)
  surfaces them on a per-session endpoint, `GET /api/sessions/<session_id>/stream` on each backend — same SSE envelope,
  type `conversation.chunk`, scoped to one session and backend-local (no fan-out to harness). See below.
- Prometheus metrics — polled over `/metrics`, not event-shaped.
- Team / config / schedule snapshots — polled REST surfaces.

## Per-session backend stream (phase 4)

Each backend (`claude`, `openai`, `gemini`) additionally serves `GET /api/sessions/<session_id>/stream` for real-time
drill-down into a single session. Wire format is identical to the harness event stream (SSE, same envelope) but the
published types are limited to `conversation.chunk`, `conversation.turn`, `tool.use`, `trace.span`, and the
`stream.overrun` terminal envelope.

- Auth: `Authorization: Bearer <CONVERSATIONS_AUTH_TOKEN>`, same token used by `/conversations`, `/trace`, `/mcp`,
  `/api/traces`. `CONVERSATIONS_AUTH_DISABLED=true` acts as the documented local-dev escape hatch (loud startup
  warning).
- Scope: backend-local (per pod replica). No cross-pod session sharing — the same session ID routed to a different
  replica will not see events emitted on this replica.
- Ring: bounded by `CONVERSATION_STREAM_RING_MAX` (default 200) for `Last-Event-ID` resume.
- Backpressure: bounded per-subscriber queue `CONVERSATION_STREAM_QUEUE_MAX` (default 500); slow subscriber → terminal
  `stream.overrun` and close.
- Keepalive: `CONVERSATION_STREAM_KEEPALIVE_SEC` (default 15).
- Grace window: after the last subscriber disconnects, the broadcaster lingers for `CONVERSATION_STREAM_GRACE_SEC`
  seconds (default 60) so a brief reconnect can resume without losing the ring. After that the registry entry is
  evicted.

Payloads continue to use `session_id_hash` (SHA-256 prefix, 12 chars) rather than the raw session id; the URL path
carries the actual id because the caller already knows it in order to construct the URL.

Phase 3 (#1110) added backend-emitted `conversation.turn`, `tool.use`, and `trace.span` events over the backend→harness
event channel (`POST /internal/events/publish`, bearer-authed by `HOOK_EVENTS_AUTH_TOKEN`). Raw per-token chunks and the
full OTel span tree are still drill-down surfaces on the per-backend endpoints.

## URL-host redaction

Webhook events carry `url_host` (bare hostname) rather than the full URL. The host is sufficient for dashboards and
avoids exposing path / query / credential fragments if the webhook URL carries any. Full URLs live in the existing
`tool-activity.jsonl` audit path.

## session_id_hash

`hook.decision` events identify the session as a SHA-256 prefix (first 12 chars) rather than the raw derived session id.
Dashboards rendering activity per-session can still group by the hash without ever seeing the HMAC-bound value. Full
session_id stays in the per-backend conversation stream (drill-down only, where auth already scopes access).

## Resume / reconnect

- Server keeps an in-memory ring of the last 1000 events per stream.
- Client reconnecting with `Last-Event-ID: <n>` receives all events with `id > n` still present in the ring.
- If the requested id is older than the ring's oldest, the server sends a single synthetic event
  `{"type":"stream.gap","version":1,...}` so the client knows it missed events and can re-fetch any stale REST snapshots
  to reconcile.

## Backpressure

- Each subscriber has a bounded async queue (default 1000 events).
- If the queue fills, the server emits `{"type":"stream.overrun",...}`, closes the connection, and bumps
  `harness_event_stream_overruns_total{subscriber}`.
- Client treats overrun as a reconnect signal with `Last-Event-ID`; if the missed span is larger than the ring, the gap
  event above fires.

## Auth

`Authorization: Bearer <harness_token>` on every request. No query-param token fallback — clients that can't set headers
natively (browser `EventSource`) use the `fetch` + `ReadableStream` pattern instead.

## Schema Versioning

Every envelope carries a required `version: integer` field (starts at `1`). Bump rules:

### Additive changes — no version bump

- New event `type` value.
- New **optional** field on an existing type's `payload`.
- New optional top-level envelope field (only done with care; the envelope is the cross-type contract).
- Widening an enum to accept a new value AND all existing consumers treat unknown enum values as either
  ignore-with-warning or passthrough.

Schema revision lands in the same version; `docs/events/events.schema.json` is updated, no wire-format version change.
Subscribers SHOULD ignore unknown fields and unknown event types — both are always legal.

### Breaking changes — major version bump

- A previously-optional field becomes required, OR a required field is removed.
- A field's type changes (string → int, scalar → object, etc.).
- An enum value is removed.
- The envelope's shape changes (timestamp format, ID format, agent_id semantics).
- Semantic changes to a field — e.g. "`payload.outcome`: success now means something different than it did in v1."

### Compat window

During a major-version transition, the harness accepts events with `version = N` AND `version = N-1` for at least one
full **minor** ww/harness release. The overlap exists so dashboards / CLIs on the older wire format have time to
upgrade. When the overlap ends, the harness drops support for `version = N-1` and the schema.json file is trimmed back
to the single supported major version.

Concretely: if v1 → v2 lands in harness v0.7.0, harness accepts both v1 and v2 events through v0.8.x; harness v0.9.0
drops v1. The release that introduces the new major bumps the `appVersion` in the charts and is called out in
CHANGELOG.md under **Changed**.

### Subscriber contract

Every subscriber MUST:

- **Ignore unknown event types.** The harness may emit new types without warning; subscribers that explode on unknown
  types forfeit forward compatibility.
- **Ignore unknown fields on known types.** Same rationale — additive changes should not break existing clients.
- **Honour the `version` field.** Subscribers that only know how to process `version = 1` MUST skip events with
  `version >= 2` (not explode, not silently misparse). The dashboard's `useEventStream` composable does this via a
  `knownVersions` sanity check; other clients should mirror the pattern.

### Detection

Subscribers detect a version change by reading `envelope.version` on every frame. There is NO handshake /
capability-negotiation endpoint — the wire format is intentionally stateless. Dashboards render a one-time banner when
they see an envelope with a higher version than their baked-in `knownVersions` max and recommend a reload.

### Deprecation signalling

A future addition (not yet implemented): when a type is scheduled for removal in the next major bump, the harness adds a
`payload.deprecated: true` field plus a `payload.deprecated_since` version. Subscribers can surface this in a
developer-tools warning.

### Where the version lives

Both in code (the `publish()` path in `harness/events.py` validates every envelope against `shared/event_schema.py`) and
in the wire contract (`docs/events/events.schema.json`). The schema is the source of truth; when the schema and code
drift, the code is wrong — tests in `harness/test_events.py` cover the round-trip.
