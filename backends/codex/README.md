# Codex Backend

`codex` is Witwave's Codex-native backend. It is a standalone A2A server implemented in Node.js and intended to wrap
OpenAI's Codex-optimized models through the Responses API.

This backend is intentionally separate from `backends/openai/`:

- `openai` is the general OpenAI Agents SDK backend.
- `codex` is the coding-agent backend reserved for Codex-specific model defaults and runtime behavior.

## Current Scope

The first implementation is contract-first:

- A2A discovery at `/.well-known/agent.json` and `/.well-known/agent-card.json`
- A2A JSON-RPC `message/send` and `tasks/send` handling at `/`
- health routes (`/health`, `/health/live`, `/health/ready`, `/health/start`)
- a dedicated Prometheus listener on `METRICS_PORT` when `METRICS_ENABLED` is set
- conversation and trace inspection surfaces guarded by `CONVERSATIONS_AUTH_TOKEN`
- per-session SSE updates at `/api/sessions/<session_id>/stream`
- Responses API text deltas streamed into the session SSE feed before the final A2A response
- bounded memory tools rooted at `CODEX_MEMORY_ROOT`
- PreToolUse-style hook gating for Codex-owned function tools (`run_shell_command`, memory tools, and URL-shaped MCP
  tools)
- persisted Responses API session continuity via `previous_response_id`
- caller-bound session derivation when `SESSION_ID_SECRET` is set
- per-dispatch `max_tokens` budget checks from Responses API usage
- a minimal `/mcp` surface with the backend-neutral `ask_agent` tool name
- backend-local MCP client bridging for URL-shaped `.codex/mcp.json` entries
- streaming-delta Prometheus counters with bounded `model` labels
- common model request, request-duration, and JSONL log-write metrics for cross-backend dashboards
- runtime posture metrics for active model, reasoning effort, default token budget, tool-iteration cap, streaming, and
  stub mode
- zero-value placeholders for runtime-specific Claude/Python metric families that do not apply to the Node backend
- active `AGENTS.md` revision metrics for rollout verification
- OpenTelemetry spans for A2A execution, MCP `tools/call`, Responses API calls, and function tools
- Claude-shaped tool trace rows for Codex-owned function tools (`tool_use`, `tool_result`, and `tool_audit`)

When `OPENAI_API_KEY` is unset, the backend runs in stub mode by default so CI and image smoke tests can verify the
runtime contract without spending tokens. Set `OPENAI_API_KEY` and `CODEX_STUB_MODE=false` to execute prompts through
the OpenAI Responses API.

## Environment

| Variable                            | Default                                      | Purpose                                                         |
| ----------------------------------- | -------------------------------------------- | --------------------------------------------------------------- |
| `CODEX_CONFIG_TOML`                 | `/home/agent/.codex/config.toml`             | Optional runtime config loaded before env defaults              |
| `CODEX_MODEL`                       | `gpt-5.5`                                    | Responses API model used when A2A metadata does not override it |
| `CODEX_REASONING_EFFORT`            | `xhigh`                                      | Reasoning effort sent to supported Codex models                 |
| `CODEX_RESPONSES_STREAMING`         | `true`                                       | Streams Responses API text deltas into session SSE updates      |
| `CODEX_STUB_MODE`                   | auto (`true` without API key)                | Force stub mode on/off                                          |
| `CODEX_SHELL_ENABLED`               | unset                                        | Enables the bounded `run_shell_command` function tool           |
| `CODEX_SHELL_CWD`                   | `/workspaces/witwave-self/source/witwave`    | Working directory for shell tool calls                          |
| `CODEX_SHELL_TIMEOUT_SECONDS`       | `30`                                         | Per-command timeout                                             |
| `CODEX_SHELL_MAX_OUTPUT_BYTES`      | `12000`                                      | Per-stream output cap before truncation                         |
| `CODEX_MAX_TOOL_ITERATIONS`         | `6`                                          | Maximum Responses API function-call loop iterations             |
| `CODEX_DEFAULT_MAX_TOKENS`          | unset                                        | Default total-token budget when request metadata omits one      |
| `CODEX_MEMORY_ENABLED`              | `true`                                       | Enables bounded memory file tools                               |
| `CODEX_MEMORY_ROOT`                 | `/home/agent/.codex/memory`                  | Root directory for memory file tools                            |
| `CODEX_MEMORY_MAX_BYTES`            | `65536`                                      | Maximum bytes per memory read/write/append payload              |
| `CODEX_MEMORY_MAX_LIST_ENTRIES`     | `200`                                        | Maximum file paths returned by `list_memory_files`              |
| `MCP_CONFIG_PATH`                   | `/home/agent/.codex/mcp.json`                | URL-shaped MCP server config loaded as backend-local tools      |
| `MCP_TOOL_AUTH_TOKEN`               | unset                                        | Bearer token auto-stamped onto MCP server calls                 |
| `HOOKS_CONFIG_PATH`                 | `/home/agent/.codex/hooks.yaml`              | Optional PreToolUse extension rules                             |
| `HOOKS_BASELINE_ENABLED`            | `true`                                       | Enables built-in baseline deny rules                            |
| `CODEX_MCP_CLIENT_TIMEOUT_SECONDS`  | `30`                                         | Timeout for MCP connect/list/call operations                    |
| `CODEX_MCP_MAX_OUTPUT_BYTES`        | `12000`                                      | Maximum bytes returned to the model from one MCP tool call      |
| `CODEX_SESSION_STORE_PATH`          | `/home/agent/.codex/sessions/responses.json` | Persistent session → `previous_response_id` map                 |
| `MAX_SESSIONS`                      | `10000`                                      | Maximum persisted Codex response sessions before LRU eviction   |
| `CODEX_AGENT_MD`                    | `/home/agent/.codex/AGENTS.md`               | Primary identity/instruction document                           |
| `CONVERSATION_LOG`                  | `/home/agent/logs/conversation.jsonl`        | Conversation JSONL output                                       |
| `TRACE_LOG`                         | `/home/agent/logs/tool-activity.jsonl`       | Trace/activity JSONL output                                     |
| `CONVERSATION_STREAM_KEEPALIVE_SEC` | `15`                                         | Per-session SSE keepalive interval                              |
| `CONVERSATION_STREAM_GRACE_SEC`     | `60`                                         | Idle session stream cleanup grace period                        |
| `CONVERSATION_STREAM_RING_MAX`      | `200`                                        | Replay buffer size per live session stream                      |
| `SESSION_STREAM_MAX_PER_CALLER`     | `8`                                          | Concurrent session stream cap per bearer fingerprint            |
| `SESSION_ID_SECRET`                 | unset                                        | HMAC key for caller-bound session IDs                           |
| `SESSION_ID_SECRET_PREV`            | unset                                        | Previous HMAC key for session-secret rotation                   |
| `MCP_MAX_BODY_BYTES`                | `4194304`                                    | Maximum accepted `/mcp` request body size                       |
| `MAX_PROMPT_BYTES`                  | `10485760`                                   | Inbound prompt byte ceiling                                     |
| `METRICS_ENABLED`                   | unset                                        | Enables the dedicated metrics listener                          |
| `METRICS_PORT`                      | `9000`                                       | Dedicated Prometheus listener port                              |
| `OTEL_ENABLED`                      | unset                                        | Enables OTLP/HTTP span export                                   |
| `OTEL_EXPORTER_OTLP_ENDPOINT`       | SDK default                                  | OTLP/HTTP collector endpoint when `OTEL_ENABLED=true`           |
| `OTEL_IN_MEMORY_SPANS`              | `1000`                                       | In-memory trace ring size for `/api/traces`                     |
| `OTEL_SERVICE_NAME`                 | `codex-<agent>`                              | Service name for emitted spans                                  |

A2A metadata may set `model`, `reasoning_effort`, `max_output_tokens`, or `max_tokens` for a single request. Invalid or
non-positive token values are ignored. `max_output_tokens` is sent to the Responses API as an output cap; `max_tokens`
is Witwave's per-dispatch total-token budget and is checked against Responses API usage.

`CODEX_CONFIG_TOML` gives mounted `.codex/config.toml` files a real runtime role. Environment variables still win, then
config values, then built-in defaults. The supported shape is intentionally small:

```toml
model = "gpt-5.5"
reasoning_effort = "xhigh"

[tools]
shell = true
memory = true
mcp = true

[runtime]
max_tool_iterations = 6
default_max_tokens = 30000

[paths]
memory_root = "/workspaces/witwave-self/memory/agents/mira"
```

Additional path overrides are available under `[paths]`: `memory_root`, `mcp_config`, `hooks_config`, and
`session_store`. Memory caps can be set under `[memory]` with `max_bytes` and `max_list_entries`. A default token budget
can be set with `[runtime].default_max_tokens`, `[budget].default_max_tokens`, `[budget].max_tokens`, or
`CODEX_DEFAULT_MAX_TOKENS`.

The backend stores the final `response.id` for each A2A session in `CODEX_SESSION_STORE_PATH` and sends it back as
`previous_response_id` on the next turn. The harness also sends `metadata.session_id` on first-turn A2A calls so Codex
has a stable session key before `contextId` is present.

## Cost Guardrails

Codex cost control is layered rather than owned by one knob:

- Use scheduler frontmatter `max-tokens:` on heartbeats, jobs, tasks, triggers, and continuations to set a hard
  per-dispatch token budget. The harness forwards this as `metadata.max_tokens`, and Codex stops once observed Responses
  API usage reaches the budget.
- Set `CODEX_DEFAULT_MAX_TOKENS` or `[runtime].default_max_tokens` when a Codex agent should have a fallback budget for
  ad-hoc A2A requests that do not arrive through a scheduled prompt file.
- Keep routine autonomous checks on an explicit cadence. For example, Mira's platform-health heartbeat runs daily, not
  continuously, because it is an observation loop rather than an incident-response loop.
- Set `CODEX_MAX_TOOL_ITERATIONS` or `[runtime].max_tool_iterations` to bound function-tool loops. This is especially
  useful for shell, memory, and MCP-heavy diagnostic prompts.
- Treat `CODEX_REASONING_EFFORT=xhigh` as a deliberate high-quality posture. For always-on agents, pair it with a
  conservative schedule, token budget, and tool-iteration cap before increasing cadence.

The cheapest release smoke is still no-token: run
`ww doctor release --agent <namespace>/<agent> --require-backend codex --strict-agent-tags` to prove the deployed agent
carries Codex and matches the operator appVersion before firing real prompts.

When metrics are enabled for the agent, `ww doctor release` can also prove the live pod is using the intended Codex
posture:

```bash
ww doctor release --skip-harness \
  --agent witwave-self/mira \
  --require-backend codex \
  --strict-agent-tags \
  --expect-runtime-model gpt-5.5 \
  --expect-runtime-reasoning-effort xhigh \
  --expect-runtime-default-max-tokens 30000 \
  --expect-runtime-max-tool-iterations 10 \
  --expect-runtime-streaming true \
  --expect-runtime-stub-mode false
```

For ad-hoc inspection, scrape the agent metrics and filter the runtime series:

```bash
ww agent metrics mira --namespace witwave-self \
  | rg 'backend_runtime_(config_info|default_max_tokens|max_tool_iterations|responses_streaming_enabled|stub_mode_enabled)'
```

For Mira's normal active posture, expect `backend_runtime_config_info{...,model="gpt-5.5",reasoning_effort="xhigh"} 1`,
`backend_runtime_default_max_tokens 30000`, `backend_runtime_max_tool_iterations 10`,
`backend_runtime_responses_streaming_enabled 1`, and `backend_runtime_stub_mode_enabled 0`.

When `SESSION_ID_SECRET` is set, caller-supplied session IDs are HMAC-bound to `metadata.caller_id` on A2A requests and
to the bearer-token fingerprint on `/mcp` requests. This matches the Claude/Gemini posture: two callers presenting the
same raw `session_id` do not collide into the same backend session. `SESSION_ID_SECRET_PREV` enables a rotation window
for existing sessions.

When `CODEX_SHELL_ENABLED=true`, the backend exposes one function tool: `run_shell_command`. It only accepts a single
allowlisted diagnostic command at a time, rejects shell metacharacters and secret-like terms, redacts common token
shapes from output, and records tool activity to `TRACE_LOG`.

When `CODEX_MEMORY_ENABLED=true`, the backend exposes `read_memory_file`, `write_memory_file`, `append_memory_file`, and
`list_memory_files`. All memory paths must be relative to `CODEX_MEMORY_ROOT`; absolute paths and `..` escapes are
refused. Write and append calls reject over-large payloads and raw credential-shaped content, then record activity to
`TRACE_LOG`.

When `MCP_CONFIG_PATH` points at a `.codex/mcp.json` file, URL-shaped entries are loaded as backend-local function tools
named `mcp__<server>__<tool>`. The Codex container connects to the MCP server directly, so in-cluster service URLs such
as `http://witwave-mcp-kubernetes:8000` remain private to the cluster. Command/stdio MCP entries are ignored by this
backend for now; use URL-shaped streamable-http MCP servers for parity with the Kubernetes deployment model.

Codex evaluates PreToolUse-style policy before executing any function tool it owns. The built-in baseline denies common
destructive shell patterns (`rm -rf /`, `git push --force main`, `curl | sh`, `chmod 777`, `dd of=/dev/...`) and
system-path write attempts through the same rule names used by the shared hook vocabulary. Optional extension rules are
loaded from `HOOKS_CONFIG_PATH` using the familiar `hooks.yaml` shape:

```yaml
extensions:
  - name: deny-example-memory-marker
    tool: write_memory_file
    deny_if_match: "DO_NOT_WRITE"
    reason: "example extension deny"
```

Tool aliases keep common cross-backend rules useful: `run_shell_command` also matches `tool: Bash`, and memory writes
also match `tool: Write`. Denied calls return a refused function result to the model, emit paired `tool_use`,
`tool_result`, and `tool_audit` rows in `TRACE_LOG`, and increment `backend_hooks_*` metrics. This gate covers
Codex-owned function tools; it does not make the Node backend a drop-in clone of Claude's SDK hook surface.
`backend_hooks_enforcement_mode` reports `1` when baseline or extension rules are active for that owned-tool surface.

Codex writes Claude-shaped tool trace rows for every function tool it executes. `tool_use` rows carry `id`, `name`,
`input`, `session_id`, and `model`; `tool_result` rows carry the matching `tool_use_id`, `content`, and `is_error`;
`tool_audit` rows carry `tool_name`, `tool_input`, `tool_response_preview`, and the final decision. This lets the
dashboard's Tool Trace view pair Codex tool calls the same way it pairs Claude tool calls.

Codex also mirrors Claude's core tool metric names for cross-backend dashboards: `backend_sdk_tool_calls_total`,
`backend_sdk_tool_calls_per_query`, `backend_sdk_tool_duration_seconds`, tool input/result byte summaries,
`backend_sdk_tool_errors_total`, and outbound MCP tool request/duration summaries.

Some Claude metric families describe Python- or Claude-SDK-specific runtime behavior, such as asyncio event-loop lag,
file watcher restarts, SDK subprocess stderr, and SQLite task-store lock wait. Codex emits zero-value placeholder series
for those names so dashboards can union across `(agent, agent_id, backend)` without dropping the Codex backend. The
placeholder is intentional: it preserves the metric contract without pretending the Node runtime has the same internal
mechanism.

OpenTelemetry is active when either `OTEL_ENABLED=true` or `OTEL_IN_MEMORY_SPANS` is positive. OTLP export is opt-in;
the in-memory ring is enabled by default so `/api/traces` can show recent backend spans without requiring a collector.
Inbound `traceparent` values from A2A metadata or MCP HTTP headers are continued so Codex spans join the harness trace.
Responses API spans include `llm.request.model`, `llm.request.reasoning_effort`, and `llm.request.streaming`, making the
live trace surface the quickest way to verify which model and reasoning tier a deployed Codex backend actually used.

## Local Test

```bash
npm install --prefix backends/codex
npm test --prefix backends/codex
```

The Node test suite is split by backend contract surface so parity regressions are easy to localize:

- `test_a2a_contract.js` — A2A request/response shape, metadata extraction, redacted conversation logging, and token
  budget metadata.
- `test_auth_contract.js` — protected inspection endpoint auth posture.
- `test_health_contract.js` — liveness, readiness, and startup route semantics.
- `test_hooks_contract.js` — PreToolUse-style hook policy and Codex-owned tool gating.
- `test_mcp_contract.js` — backend-local MCP endpoint and URL-shaped MCP tool calls.
- `test_memory_contract.js` — bounded file-memory tools.
- `test_metrics_contract.js` — common backend Prometheus label shape and streaming counters.
- `test_trace_stream_contract.js` — OpenTelemetry trace lookup and per-session SSE streaming.
- `test_codex_backend.js` — small core helper/config units that do not need a full contract fixture.

## Local Run

```bash
CODEX_STUB_MODE=true METRICS_ENABLED=true npm start --prefix backends/codex
```
