# Triggers

Triggers are HTTP-driven endpoints served directly by harness. Each trigger is defined as a `*.md` file in the agent's
`.witwave/triggers/` directory. When harness receives a `POST /triggers/{endpoint}` request, it builds a prompt from the
request payload and the trigger's markdown body, then dispatches the prompt to the configured backend as a background
task and returns `202 Accepted` immediately.

Triggers are analogous to inbound A2A requests — they run concurrently, bypass the message bus, and maintain session
continuity via a deterministic session ID derived from the agent name and endpoint slug.

**Minimal unsigned trigger** (responds to any POST, verifies trigger routing and backend response):

```markdown
---
name: Ping
description: Minimal unsigned trigger — verifies trigger routing and backend response.
endpoint: ping
enabled: true
---

Respond with TRIGGER_OK.
```

**HMAC-authenticated webhook trigger** (validates `X-Hub-Signature-256` using `BOB_WEBHOOK_SECRET`):

```markdown
---
name: Webhook
description: Parses and summarizes incoming webhook events.
endpoint: webhook
enabled: true
secret-env-var: BOB_WEBHOOK_SECRET
---

A webhook event has arrived. Parse the request body as JSON and summarize the event type and key fields. If the body is
not JSON, describe the raw payload.
```

**Snapshot endpoint** — `GET /triggers` returns a JSON array of all registered triggers with runtime state (name,
endpoint, description, session_id, backend_id, model, and whether the endpoint is currently executing):

```json
[
  {
    "name": "Ping",
    "endpoint": "ping",
    "description": "Minimal unsigned trigger — verifies trigger routing and backend response.",
    "session_id": "...",
    "backend_id": null,
    "model": null,
    "consensus": [],
    "running": false
  }
]
```

**Discovery endpoint** — `GET /.well-known/agent-triggers.json` returns a JSON array of all enabled trigger descriptors.
Per-trigger URLs are intentionally `POST`-only because they dispatch work; use discovery rather than
`HEAD /triggers/{endpoint}` for metadata checks:

```json
[
  {
    "endpoint": "ping",
    "name": "Ping",
    "description": "Minimal unsigned trigger — verifies trigger routing and backend response.",
    "methods": ["POST"],
    "session_id": "..."
  }
]
```

## Frontmatter Fields

| Field            | Required | Description                                                                                                                                                               |
| ---------------- | -------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `endpoint`       | Yes      | URL-safe slug served at `POST /triggers/{endpoint}`. Must match `^[a-z0-9][a-z0-9-]*$`.                                                                                   |
| `name`           | No       | Display name used in logs and discovery. Defaults to the filename stem.                                                                                                   |
| `description`    | No       | Human-readable summary. Included in the discovery endpoint response.                                                                                                      |
| `enabled`        | No       | `false` disables without deleting. Default: `true`.                                                                                                                       |
| `secret-env-var` | No       | Name of an environment variable holding an HMAC-SHA256 secret. When set and the env var is non-empty, harness validates `X-Hub-Signature-256` (GitHub-compatible format). |
| `session`        | No       | Session ID override. Defaults to a deterministic UUID derived from `AGENT_NAME` and `endpoint`.                                                                           |
| `model`          | No       | Model override passed to the backend.                                                                                                                                     |
| `agent`          | No       | Backend ID override (e.g. `openai`); defaults to routing config.                                                                                                          |
| `consensus`      | No       | List of `{backend, model?}` entries to fan out to; empty list (default) disables consensus. Supports glob patterns in `backend`.                                          |
| `max-tokens`     | No       | Token budget for this dispatch. Stop and return partial response when reached.                                                                                            |

## Auth Fallback Order

1. If `secret-env-var` is set and the env var is non-empty → validate `X-Hub-Signature-256` (HMAC-SHA256).
2. Else if `TRIGGERS_AUTH_TOKEN` env var is non-empty → require `Authorization: Bearer <token>`.
3. Else → reject with `401 Unauthorized`. At least one auth mechanism must be configured.

**In-flight deduplication:** if a `POST` arrives while the same endpoint is already processing a prior request, harness
returns `409 Conflict` and does not enqueue a second run.
