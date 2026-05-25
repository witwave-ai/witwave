# claude

claude is the Claude backend for the autonomous agent platform. It is a standalone A2A server that wraps the Claude
Agent SDK, managing its own sessions, conversation logs, trace logs, and Prometheus metrics.

## What it does

claude receives A2A JSON-RPC requests (forwarded by harness), runs them through Claude via the Claude Agent SDK CLI,
streams back the response, and logs everything to JSONL files.

Each named agent that uses Claude gets its own dedicated instance of this image (e.g. `iris-claude`, `bob-claude`).
Instances are completely isolated — separate sessions, logs, memory, and metrics.

## Key features

**Session continuity** — Sessions are tracked in an in-process LRU cache keyed by session ID. Resuming a session carries
conversation history forward. The SDK handles context window management; claude monitors usage and warns at 90%
utilization.

**MCP server support** — Loads MCP server definitions from a mounted `mcp.json` file. The file is hot-reloaded on each
request, so MCP servers can be added or reconfigured without restarting the container.

**Tool tracing** — Every `tool_use` and `tool_result` event is captured from the SDK stream and written to
`tool-activity.jsonl` alongside summary response events. `PostToolUse` hook rows land in the same file tagged
`event_type: "tool_audit"`, giving operators one feed to tail for all tool activity (raw SDK events plus hook-level
audit context like matched rule, decision, and response preview).

**Model override** — The model used for a given request can be overridden via `metadata.model` in the A2A message.
Resolution order: per-message metadata → routing config model → default model in `backend.yaml`.

**Agent identity** — Claude's system prompt is loaded from `/home/agent/.claude/CLAUDE.md`. The agent's name,
personality, and behavioral constraints all live there. The file is hot-reloaded on change — updating `CLAUDE.md` takes
effect for the next request without restarting the container.

**Metrics** — Exposes the **superset** of the `backend_*` series. Covers context-window token counts, tool-call counts
and errors, per-request `/mcp` observability, SQLite task-store lock-wait, hook evaluations and denials (canonical
`backend_hooks_denials_total{tool,source,rule}`), webhook delivery timeouts, and allowed-tool reloads. Peers (codex,
gemini) track placeholders for the same series so cross-backend PromQL joins stay clean. See `metrics.py` for the live
catalog; see `AGENTS.md` → "Metrics landscape" for the alignment principle.

**Hooks (PreToolUse / PostToolUse)** — A two-layer policy engine wraps every tool call the SDK makes. A conservative
**baseline** of deny rules ships with the executor and blocks the most obvious-dangerous shell patterns (`rm -rf /`,
`git push --force main`, `curl | sh`, `chmod 777`, `dd of=/dev/sdX`). Per-agent **extensions** layered on top live in a
`hooks.yaml` file mounted at `/home/agent/.claude/hooks.yaml` and are hot-reloaded. PostToolUse is always wired and
writes one row per tool call to `logs/tool-activity.jsonl` with `event_type: "tool_audit"` for a forensic trail. See
[Hook configuration](#hook-configuration) below.

## Endpoints

| Endpoint                      | Purpose                                                                                                                                                                                                                                                                                                                                                                                                                                                                                      |
| ----------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `POST /`                      | A2A JSON-RPC task endpoint                                                                                                                                                                                                                                                                                                                                                                                                                                                                   |
| `GET /.well-known/agent.json` | A2A agent discovery                                                                                                                                                                                                                                                                                                                                                                                                                                                                          |
| `GET /health/start`           | Startup probe — 200 once initial loads complete; 503 `{"status":"starting"}` while warming up (#1686). K8s `startupProbe` should target this.                                                                                                                                                                                                                                                                                                                                                |
| `GET /health`                 | Liveness probe — 200 once the process is up, even mid-init                                                                                                                                                                                                                                                                                                                                                                                                                                   |
| `GET /health/ready`           | Readiness probe — 200 when fully ready; 503 while initializing or in a boot-degraded state (#1608)                                                                                                                                                                                                                                                                                                                                                                                           |
| `GET /metrics`                | Prometheus metrics                                                                                                                                                                                                                                                                                                                                                                                                                                                                           |
| `GET /conversations`          | Conversation log (JSONL, filterable by `since`/`limit`)                                                                                                                                                                                                                                                                                                                                                                                                                                      |
| `GET /trace`                  | Tool-activity feed (JSONL, filterable by `since`/`limit`) — carries `tool_use`, `tool_result`, and `tool_audit` event types. Requires `Authorization: Bearer $CONVERSATIONS_AUTH_TOKEN` (shared token gate with `/conversations`, `/mcp`, `/api/traces`)                                                                                                                                                                                                                                     |
| `GET /api/traces`             | OTel trace listing for the dashboard. Requires `Authorization: Bearer $CONVERSATIONS_AUTH_TOKEN` (parity with the other protected endpoints)                                                                                                                                                                                                                                                                                                                                                 |
| `GET /api/traces/{id}`        | Single-trace detail for the dashboard. Requires `Authorization: Bearer $CONVERSATIONS_AUTH_TOKEN`                                                                                                                                                                                                                                                                                                                                                                                            |
| `POST /mcp`                   | MCP JSON-RPC server (`initialize`, `tools/list`, `tools/call`); exposes a single `ask_agent` tool. Requires `Authorization: Bearer $CONVERSATIONS_AUTH_TOKEN` (#518). The request's `session_id` is routed through `shared/session_binding.derive_session_id` with a bearer-token fingerprint before lookup/insert (#867) so a caller cannot hijack another caller's session; `SESSION_ID_SECRET` HMAC-derives the bound ID when set. OTel spans are emitted for `/mcp` request flow (#966). |

## Key files

| File                   | Purpose                                                      |
| ---------------------- | ------------------------------------------------------------ |
| `main.py`              | A2A server entrypoint; registers routes and starts uvicorn   |
| `executor.py`          | Claude Agent SDK executor; session cache, streaming, logging |
| `hooks.py`             | PreToolUse/PostToolUse policy engine and baseline deny rules |
| `metrics.py`           | Prometheus metric definitions                                |
| `sqlite_task_store.py` | SQLite-backed task store (used when TASK_STORE_PATH is set)  |
| `requirements.txt`     | Python dependencies                                          |
| `Dockerfile`           | Container image definition                                   |

## Secrets

Create a Kubernetes secret with the required credentials before deploying:

```bash
kubectl create secret generic <agent>-claude-secrets \
  --from-literal=ANTHROPIC_API_KEY=sk-ant-... \
  --namespace witwave
```

For Claude Max (OAuth), use `CLAUDE_CODE_OAUTH_TOKEN` instead of `ANTHROPIC_API_KEY`.

Reference the secret in your Helm values:

```yaml
backends:
  - name: claude
    envFrom:
      - secretRef:
          name: <agent>-claude-secrets
```

## Runtime

claude mounts:

- `CLAUDE.md` — agent identity (system prompt), at `/home/agent/.claude/CLAUDE.md`
- `mcp.json` — MCP server configuration (optional)
- `logs/conversation.jsonl` — conversation log file (must pre-exist as a file)
- `logs/tool-activity.jsonl` — trace log file (must pre-exist as a file)
- `memory/` — persistent memory directory

Key environment variables: `AGENT_NAME` (instance name), `AGENT_OWNER` (named agent, e.g. `iris`), `AGENT_ID` (backend
slot id, e.g. `claude`), `AGENT_URL`, `BACKEND_PORT`, `ANTHROPIC_API_KEY` (or `CLAUDE_CODE_OAUTH_TOKEN` for Claude Max),
`CLAUDE_MODEL` (model override), `METRICS_ENABLED`, `CONVERSATIONS_AUTH_TOKEN`, `CONVERSATIONS_AUTH_DISABLED` (explicit
escape hatch for no-auth mode, #718), `LOG_REDACT` (conversation redaction toggle, #714), `TASK_STORE_PATH`,
`WORKER_MAX_RESTARTS`, `LOG_PROMPT_MAX_BYTES` (max bytes of prompt logged at INFO; default 200; set to 0 to suppress),
`HOOKS_CONFIG_PATH` (path to `hooks.yaml`; default `/home/agent/.claude/hooks.yaml`), `HOOKS_BASELINE_ENABLED` (default
`true`; set to `false` to disable the baseline deny rules), `MCP_ALLOWED_COMMANDS` / `MCP_ALLOWED_COMMAND_PREFIXES` /
`MCP_ALLOWED_CWD_PREFIXES` (stdio MCP entry allow-list, #711; rejections counted on
`backend_mcp_command_rejected_total{reason}`). The default allow-list is pruned to `mcp-kubernetes,mcp-helm,uv,uvx`
(#862); the absolute-path basename fallback was removed so a path like `/usr/local/bin/uvx` no longer passes on basename
alone — the full path must match `MCP_ALLOWED_COMMAND_PREFIXES`. Interpreter invocations (`python -c`, `node -e`, …) are
additionally vetted by `mcp_command_args_safe()` against an args deny-list (#930).

## Hook configuration

The executor wraps every Claude tool call with PreToolUse (policy) and PostToolUse (audit) hooks (#467).

**Baseline.** A fixed set of deny rules ships in `hooks.py` — see `BASELINE_RULES`. They match against the
JSON-serialised `tool_input` payload and reject obvious-dangerous shell patterns. The list is intentionally small and
narrow to minimise false positives; operators who want a stricter sandbox should add extensions. Set
`HOOKS_BASELINE_ENABLED=false` to turn the baseline off entirely (e.g. during bring-up of a permissive-by-design agent).

**Extensions.** Per-agent opt-in rules are loaded from `/home/agent/.claude/hooks.yaml`. The file is optional; when
present it is hot-reloaded whenever it changes:

```yaml
extensions:
  - name: block-private-key-writes
    tool: "Write" # exact tool name; omit or "*" for any tool
    deny_if_match: "BEGIN PRIVATE KEY"
    reason: "refusing to write private keys"

  - name: warn-on-webfetch
    tool: "WebFetch"
    warn_if_match: ".*"
    reason: "network call"
```

Each rule must have a `name` and exactly one of `deny_if_match` or `warn_if_match` (regex, applied to the JSON
serialisation of `tool_input`). Invalid rules are skipped with a warning; malformed YAML keeps the previous ruleset in
place so an editing mistake cannot accidentally disable policy.

**Audit log.** PostToolUse always appends one row per tool call to `TRACE_LOG` (default
`/home/agent/logs/tool-activity.jsonl`) tagged `event_type: "tool_audit"`, with fields: `ts`, `agent`, `agent_id`,
`session_id`, `model`, `tool_use_id`, `tool_name`, `tool_input`, `tool_response_preview` (capped at 2 KiB), plus
`decision`, `rule`, and `reason` when the hook blocked the call. The audit rows share a file with SDK `tool_use` and
`tool_result` events so operators tail one feed for all tool activity; filter by `event_type` to isolate audit rows for
SIEM/forensics. PostToolUse is not opt-outable — transparency is a guarantee, not a policy choice.

**Metrics.** Canonical cross-backend deny counter `backend_hooks_denials_total{tool,source,rule}` (#789; the legacy
claude-only `backend_hooks_blocked_total` alias is retained for one release cycle), plus
`backend_hooks_warnings_total{tool,source,rule}`, `backend_tool_audit_entries_total{tool}`,
`backend_hooks_evaluations_total{tool,decision}`, `backend_hooks_config_reloads_total`,
`backend_hooks_config_errors_total{reason}`, `backend_hooks_shed_total` (hook POSTs shed at the in-flight cap, #712),
`backend_hooks_enforcement_mode`, and `backend_hooks_active_rules{source}`.

## Tracing (OpenTelemetry)

When `OTEL_ENABLED=true` is set, claude emits a server span for every `execute()` call and continues any trace
propagated by harness via the `metadata.traceparent` field (#469). The OTLP/HTTP exporter reads the standard
`OTEL_EXPORTER_OTLP_ENDPOINT` / `OTEL_SERVICE_NAME` / `OTEL_TRACES_SAMPLER` env vars. Resource attributes
(`service.name`, `agent`, `agent_id`, `backend`) are populated automatically. When `OTEL_ENABLED` is falsy (default) the
OTel call sites are no-ops. The bootstrap lives in `shared/otel.py` and is shared with the other backends and the
harness.
