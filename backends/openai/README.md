# openai

openai is the OpenAI backend for the autonomous agent platform. It is a standalone A2A server that wraps the OpenAI
Agents SDK, managing its own sessions, conversation logs, trace logs, and Prometheus metrics.

## What it does

openai receives A2A JSON-RPC requests (forwarded by harness), runs them through an OpenAI model via the Agents SDK with
streaming, and logs everything to JSONL files.

Each named agent that uses OpenAI gets its own dedicated instance of this image (e.g. `iris-openai`, `bob-openai`).
Instances are completely isolated — separate sessions, logs, and metrics.

## Key features

**Session persistence** — Sessions are stored in a SQLite database (`logs/openai_sessions.db`). Unlike in-memory caches,
sessions survive container restarts. The Agents SDK uses this to maintain conversation continuity across restarts.

**Streaming** — Uses `Runner.run_streamed()` with async event iteration. Response chunks are collected as they arrive
and assembled into the final text response.

**Tool support** — The agent is configured with `ShellTool` (local shell command execution), `WebSearchTool` (web search
via OpenAI's search index), and optionally `ComputerTool` (headless browser via Playwright). Tools are enabled/disabled
via environment variables and `config.toml`.

**Headless browser** — When computer tool support is enabled, openai manages a Playwright Chromium instance via
`computer.py`. The browser is initialized lazily on first use and reused across tool calls within a session. Each
session gets its own **isolated browser context** (#522) — cookies, storage, and navigation history do not leak between
sessions, even though the underlying Chromium process is shared. Supports screenshot, click, scroll, type, keypress, and
drag operations.

**Model override** — The model for a given request can be set via `metadata.model` in the A2A message. Resolution order:
per-message metadata → routing config model → `OPENAI_MODEL` environment variable.

**Agent identity** — The system prompt is loaded from `/home/agent/.openai/AGENTS.md`. The agent's name and behavioral
constraints live there. The file is hot-reloaded on change — updating `AGENTS.md` takes effect for the next request
without restarting the container.

**MCP servers** — External tools can be wired in via `/home/agent/.openai/mcp.json` (override path with
`MCP_CONFIG_PATH`). Same wire format as the claude `mcp.json` — entries with a `command` field become `MCPServerStdio`
instances, entries with a `url` field become `MCPServerStreamableHttp` instances. Servers are entered via
`AsyncExitStack` per request and passed to `Agent(mcp_servers=[...])`, so MCP-provided tools coexist with the built-in
shell / web search / Playwright computer tools. The file is hot-reloaded on change. Three metrics track config state:
`backend_mcp_config_errors_total`, `backend_mcp_config_reloads_total`, `backend_mcp_servers_active`.

**Metrics** — Exposes the common `backend_*` series. Covers request count / latency, session lifecycle, tool-call
counts + latency + errors + per-query breakdown, context-window tokens, SDK error classification (connection / result /
client / context-fetch), per-task stderr noise and retries, MCP config + server state, streaming-chunk drops,
empty-prompt rejections, and hook evaluation + denials (canonical `backend_hooks_denials_total{tool,source,rule}`; the
backend-specific `backend_openai_hooks_denials_total` alias and pre-rename `backend_codex_hooks_denials_total` alias are
retained during migration and gated by `EMIT_DEPRECATED_HOOK_METRICS`). Claude is the superset; openai tracks
placeholders for its missing series so cross-backend PromQL joins stay clean. `backend_hooks_enforcement_mode` reports
`0` while hook blocking remains shell-baseline-only — `backend_sdk_subprocess_spawn_duration_seconds` is a zero-value
placeholder since the Agents SDK runs in-process. See `metrics.py` for the live catalog.

## Endpoints

| Endpoint                      | Purpose                                                                                                                                                                                                                                                                                                                                                                                                  |
| ----------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `POST /`                      | A2A JSON-RPC task endpoint                                                                                                                                                                                                                                                                                                                                                                               |
| `GET /.well-known/agent.json` | A2A agent discovery                                                                                                                                                                                                                                                                                                                                                                                      |
| `GET /health/start`           | Startup probe — 200 once initial loads complete; 503 `{"status":"starting"}` while warming up (#1686). K8s `startupProbe` should target this.                                                                                                                                                                                                                                                            |
| `GET /health`                 | Liveness probe — 200 once the process is up, even mid-init                                                                                                                                                                                                                                                                                                                                               |
| `GET /health/ready`           | Readiness probe — 200 when fully ready; 503 while initializing or in a boot-degraded state (#1672)                                                                                                                                                                                                                                                                                                       |
| `GET /metrics`                | Prometheus metrics                                                                                                                                                                                                                                                                                                                                                                                       |
| `GET /conversations`          | Conversation log (JSONL, filterable by `since`/`limit`)                                                                                                                                                                                                                                                                                                                                                  |
| `GET /trace`                  | Trace log (JSONL, filterable by `since`/`limit`)                                                                                                                                                                                                                                                                                                                                                         |
| `POST /mcp`                   | MCP JSON-RPC server (`initialize`, `tools/list`, `tools/call`); exposes a single `ask_agent` tool. Requires `Authorization: Bearer $CONVERSATIONS_AUTH_TOKEN` (#510). `session_id` is routed through `shared/session_binding.derive_session_id` with a bearer-token fingerprint before lookup/insert (#929/#941) for parity with claude and gemini. OTel spans now cover the `/mcp` request flow (#966). |

## Key files

| File                   | Purpose                                                            |
| ---------------------- | ------------------------------------------------------------------ |
| `main.py`              | A2A server entrypoint; registers routes and starts uvicorn         |
| `executor.py`          | OpenAI Agents SDK executor; session management, streaming, logging |
| `computer.py`          | PlaywrightComputer — headless Chromium browser implementation      |
| `metrics.py`           | Prometheus metric definitions                                      |
| `sqlite_task_store.py` | SQLite-backed task store (used when TASK_STORE_PATH is set)        |
| `requirements.txt`     | Python dependencies                                                |
| `Dockerfile`           | Container image definition                                         |

## Secrets

Create a Kubernetes secret with the required credentials before deploying:

```bash
kubectl create secret generic <agent>-openai-secrets \
  --from-literal=OPENAI_API_KEY=sk-... \
  --namespace witwave
```

Reference the secret in your Helm values:

```yaml
backends:
  - name: openai
    envFrom:
      - secretRef:
          name: <agent>-openai-secrets
```

## Runtime

openai mounts:

- `AGENTS.md` — agent identity (system prompt), at `/home/agent/.openai/AGENTS.md`
- `config.toml` — tool enablement flags (optional)
- `logs/conversation.jsonl` — conversation log file (must pre-exist as a file)
- `logs/tool-activity.jsonl` — trace log file (must pre-exist as a file)
- `memory/` — persistent memory directory

Key environment variables: `AGENT_NAME` (instance name), `AGENT_OWNER` (named agent, e.g. `iris`), `AGENT_ID` (backend
slot id, e.g. `openai`), `AGENT_URL`, `BACKEND_PORT`, `OPENAI_API_KEY`, `OPENAI_MODEL` (model override, default
`gpt-5.5`), `OPENAI_REASONING_EFFORT` (`minimal`, `low`, `medium`, `high`, or `xhigh`; common spellings of "extra high"
map to `xhigh`), `METRICS_ENABLED`, `CONVERSATIONS_AUTH_TOKEN`, `CONVERSATIONS_AUTH_DISABLED` (explicit escape hatch for
no-auth mode, #718), `LOG_REDACT` (conversation redaction toggle, #714), `TASK_STORE_PATH`, `WORKER_MAX_RESTARTS`,
`COMPUTER_USE_ENABLED` (activates Playwright browser tool for supported models such as `gpt-5.5`),
`LOG_PROMPT_MAX_BYTES` (max bytes of prompt logged at INFO; default 200; set to 0 to suppress), `MCP_ALLOWED_COMMANDS` /
`MCP_ALLOWED_COMMAND_PREFIXES` / `MCP_ALLOWED_CWD_PREFIXES` (stdio MCP entry allow-list, #720; rejections counted on
`backend_mcp_command_rejected_total{reason}`). Default allow-list pruned to `mcp-kubernetes,mcp-helm,uv,uvx` with the
absolute-path basename fallback removed (#862); interpreter args are additionally vetted via `mcp_command_args_safe()`
(#930).

## Tracing (OpenTelemetry)

When `OTEL_ENABLED=true` is set, openai emits a server span for every `execute()` call and continues any trace
propagated by harness via the `metadata.traceparent` field (#469). The OTLP/HTTP exporter reads the standard
`OTEL_EXPORTER_OTLP_ENDPOINT` / `OTEL_SERVICE_NAME` / `OTEL_TRACES_SAMPLER` env vars. When `OTEL_ENABLED` is falsy
(default) the OTel call sites are no-ops. Bootstrap in `shared/otel.py` is shared with the harness and other backends.
