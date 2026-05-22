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
- bounded memory tools rooted at `CODEX_MEMORY_ROOT`
- persisted Responses API session continuity via `previous_response_id`
- per-dispatch `max_tokens` budget checks from Responses API usage
- a minimal `/mcp` surface for parity smoke tests

When `OPENAI_API_KEY` is unset, the backend runs in stub mode by default so CI and image smoke tests can verify the
runtime contract without spending tokens. Set `OPENAI_API_KEY` and `CODEX_STUB_MODE=false` to execute prompts through
the OpenAI Responses API.

## Environment

| Variable                        | Default                                      | Purpose                                                         |
| ------------------------------- | -------------------------------------------- | --------------------------------------------------------------- |
| `CODEX_MODEL`                   | `gpt-5.5`                                    | Responses API model used when A2A metadata does not override it |
| `CODEX_REASONING_EFFORT`        | `xhigh`                                      | Reasoning effort sent to supported Codex models                 |
| `CODEX_STUB_MODE`               | auto (`true` without API key)                | Force stub mode on/off                                          |
| `CODEX_SHELL_ENABLED`           | unset                                        | Enables the bounded `run_shell_command` function tool           |
| `CODEX_SHELL_CWD`               | `/workspaces/witwave-self/source/witwave`    | Working directory for shell tool calls                          |
| `CODEX_SHELL_TIMEOUT_SECONDS`   | `30`                                         | Per-command timeout                                             |
| `CODEX_SHELL_MAX_OUTPUT_BYTES`  | `12000`                                      | Per-stream output cap before truncation                         |
| `CODEX_MAX_TOOL_ITERATIONS`     | `6`                                          | Maximum Responses API function-call loop iterations             |
| `CODEX_MEMORY_ENABLED`          | `true`                                       | Enables bounded memory file tools                               |
| `CODEX_MEMORY_ROOT`             | `/home/agent/.codex/memory`                  | Root directory for memory file tools                            |
| `CODEX_MEMORY_MAX_BYTES`        | `65536`                                      | Maximum bytes per memory read/write/append payload              |
| `CODEX_MEMORY_MAX_LIST_ENTRIES` | `200`                                        | Maximum file paths returned by `list_memory_files`              |
| `CODEX_SESSION_STORE_PATH`      | `/home/agent/.codex/sessions/responses.json` | Persistent session → `previous_response_id` map                 |
| `MAX_SESSIONS`                  | `10000`                                      | Maximum persisted Codex response sessions before LRU eviction   |
| `CODEX_AGENT_MD`                | `/home/agent/.codex/AGENTS.md`               | Primary identity/instruction document                           |
| `CONVERSATION_LOG`              | `/home/agent/logs/conversation.jsonl`        | Conversation JSONL output                                       |
| `TRACE_LOG`                     | `/home/agent/logs/tool-activity.jsonl`       | Trace/activity JSONL output                                     |
| `MAX_PROMPT_BYTES`              | `10485760`                                   | Inbound prompt byte ceiling                                     |
| `METRICS_ENABLED`               | unset                                        | Enables the dedicated metrics listener                          |
| `METRICS_PORT`                  | `9000`                                       | Dedicated Prometheus listener port                              |

A2A metadata may set `model`, `reasoning_effort`, `max_output_tokens`, or `max_tokens` for a single request. Invalid or
non-positive token values are ignored. `max_output_tokens` is sent to the Responses API as an output cap; `max_tokens`
is Witwave's per-dispatch total-token budget and is checked against Responses API usage.

The backend stores the final `response.id` for each A2A session in `CODEX_SESSION_STORE_PATH` and sends it back as
`previous_response_id` on the next turn. The harness also sends `metadata.session_id` on first-turn A2A calls so Codex
has a stable session key before `contextId` is present.

When `CODEX_SHELL_ENABLED=true`, the backend exposes one function tool: `run_shell_command`. It only accepts a single
allowlisted diagnostic command at a time, rejects shell metacharacters and secret-like terms, redacts common token
shapes from output, and records tool activity to `TRACE_LOG`.

When `CODEX_MEMORY_ENABLED=true`, the backend exposes `read_memory_file`, `write_memory_file`, `append_memory_file`, and
`list_memory_files`. All memory paths must be relative to `CODEX_MEMORY_ROOT`; absolute paths and `..` escapes are
refused. Write and append calls reject over-large payloads and raw credential-shaped content, then record activity to
`TRACE_LOG`.

## Local Test

```bash
npm install --prefix backends/codex
npm test --prefix backends/codex
```

## Local Run

```bash
CODEX_STUB_MODE=true METRICS_ENABLED=true npm start --prefix backends/codex
```
