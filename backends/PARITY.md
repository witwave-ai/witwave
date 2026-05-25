# Backend Parity

This document tracks the shared backend contract for Witwave's LLM-backed backends and the current gaps that still need
work. `claude` remains the reference implementation because it has the longest production history and the richest hook,
trace, and metric surface. `openai`, `codex`, and `gemini` should converge on the same platform contract where the
underlying SDK makes that practical.

`echo` is intentionally excluded from parity. It is a no-cost smoke backend for harness/operator tests, not a real LLM
execution layer.

## Legend

| Status  | Meaning                                                                |
| ------- | ---------------------------------------------------------------------- |
| Full    | Implemented as part of the shared backend contract.                    |
| Partial | Implemented, but with narrower behavior or an SDK-specific limitation. |
| Gap     | Missing or materially weaker than the reference behavior.              |
| N/A     | Intentionally out of scope for that backend.                           |

## Contract Matrix

| Capability                            | Claude  | OpenAI  | Codex   | Gemini  | Notes                                                                                                  |
| ------------------------------------- | ------- | ------- | ------- | ------- | ------------------------------------------------------------------------------------------------------ |
| A2A discovery and task endpoint       | Full    | Full    | Full    | Full    | All expose agent discovery and JSON-RPC task handling.                                                 |
| Split liveness/readiness routes       | Full    | Full    | Full    | Full    | `/health` and `/health/ready` are present on all LLM backends.                                         |
| Dedicated Prometheus listener         | Full    | Full    | Full    | Full    | All expose `/metrics` on the dedicated metrics port when enabled.                                      |
| Protected conversation and trace APIs | Full    | Full    | Full    | Full    | `/conversations`, `/trace`, `/api/traces`, and session SSE are present.                                |
| Caller-bound session IDs              | Full    | Full    | Full    | Full    | All use caller identity or bearer fingerprint binding when configured.                                 |
| Persistent session continuity         | Full    | Full    | Full    | Full    | The persistence mechanism differs by SDK/runtime.                                                      |
| Conversation and tool/activity logs   | Full    | Full    | Full    | Full    | Codex now writes Claude-shaped tool trace rows for its owned tools.                                    |
| Primary identity document             | Full    | Full    | Full    | Full    | `CLAUDE.md`, `.openai/AGENTS.md`, `.codex/AGENTS.md`, and `GEMINI.md`.                                 |
| Identity revision metric              | Gap     | Full    | Full    | Full    | Codex reports the active `AGENTS.md` revision; Claude has not adopted this metric yet.                 |
| Skill-folder convention               | Full    | Partial | Partial | Gap     | Claude has the strongest convention; OpenAI/Codex can mirror docs, Gemini lacks a native skill folder. |
| Memory surface                        | Full    | Partial | Full    | Partial | Codex has bounded memory tools; Gemini persists sessions but lacks native file-memory tooling.         |
| MCP consumption                       | Full    | Full    | Partial | Partial | Codex supports URL-shaped MCP tools; SDK-backed transports differ by backend.                          |
| Hook enforcement                      | Full    | Partial | Partial | Partial | Claude is full PreToolUse/PostToolUse; Codex gates owned tools; Gemini still has AFC limits.           |
| OpenTelemetry trace integration       | Full    | Full    | Full    | Full    | All expose trace APIs and continue inbound trace context.                                              |
| Prompt-size rejection                 | Full    | Full    | Full    | Full    | `MAX_PROMPT_BYTES` now covers all LLM-backed A2A paths.                                                |
| Token budget handling                 | Full    | Full    | Full    | Full    | `max_tokens` style budget checks exist across the LLM backends.                                        |
| Task-level metrics                    | Full    | Full    | Full    | Full    | Codex maps A2A work to backend task counters, durations, and last-run timestamps.                      |
| Context/token metrics                 | Full    | Full    | Full    | Full    | Codex reports budget-derived usage, warning, and exhaustion counters.                                  |
| SDK/tool metrics                      | Full    | Full    | Partial | Full    | Codex covers query/session/tool-audit/error families; some SDK-specific series remain thinner.         |
| File watcher/reload metrics           | Full    | Full    | Partial | Full    | Codex exposes MCP config reload state but does not yet run Python-style file watchers.                 |
| SQLite task-store metrics             | Full    | Full    | Gap     | Full    | Codex uses a JSON response-session store rather than the shared SQLite task-store path.                |
| Focused regression tests              | Partial | Full    | Partial | Full    | Codex now has split auth, health, MCP, hooks, memory, trace/session-stream, and metrics coverage.      |

## Highest-Value Gaps

1. Codex remaining metric gaps: decide which Python-specific watcher/task-store series should become Codex placeholders
   and which should stay documented as runtime-specific.
2. Codex test shape: keep splitting the remaining large Node test file into focused contract tests for core unit
   coverage.
3. Hook semantics: keep Claude as the reference, but make each backend's hook boundary explicit so users know what is
   denied before execution and what is only audited.
4. MCP transport documentation: tighten backend docs so the distinction between stdio SDK MCP and in-cluster HTTP MCP is
   clear and consistent.
5. Memory semantics: document which backends have native file-memory tools, which only persist session history, and
   which should rely on workspace memory plus identity instructions.

## Practical Direction

Parity does not mean every backend must be identical internally. It means the named agent behaves predictably from the
outside: same A2A contract, same health posture, same protected inspection APIs, compatible metrics labels, durable
session behavior, and clear safety boundaries.

The best next slices are small and contract-driven: close one gap, add one regression test, and update this matrix when
the behavior changes. That keeps backend parity moving without turning Codex, OpenAI, or Gemini into fragile copies of
Claude.
