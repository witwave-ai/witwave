# Backend Guide

Witwave backends are the LLM execution layer behind a named agent. The harness receives A2A work, reads
`.witwave/backend.yaml`, and forwards each concern to one of the configured backend sidecars. A single named agent can
run one backend or several, then route `a2a`, `heartbeat`, `job`, `task`, `trigger`, and `continuation` traffic to
different backends.

Use this page as the quick chooser. The per-backend READMEs remain the source of truth for runtime flags, endpoints,
secrets, and implementation details. For cross-backend contract status and known gaps, see [Backend Parity](PARITY.md).

## Quick Choice

| Choose this backend | When it is the best fit                                                                                                                      | Main nuance                                                                                                                            |
| ------------------- | -------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------- |
| `claude`            | Mature autonomous coding work, strongest hook policy, Claude-specific skills, and the broadest production-hardening surface.                 | Most mature, but tied to Claude credentials/quotas and the Claude Agent SDK execution model.                                           |
| `codex`             | Codex-optimized coding agents on GPT-5.5, especially when we want lower-cost OpenAI execution with live trace proof of model/reasoning tier. | Newer backend; Codex-owned function tools are hook-gated, but some broader tool surfaces are still catching up.                        |
| `openai`            | General OpenAI Agents SDK execution, especially when web search, browser/computer tools, or SDK-native OpenAI agent behavior matter.         | General OpenAI backend, not Codex-specific; heavier tool surface than `codex`, but less aligned to the Codex-native runtime direction. |
| `gemini`            | Provider diversity, Gemini-specific evaluation, and second-opinion / fallback workflows.                                                     | Gemini does not have the same skill-folder convention; memory and tool ergonomics differ from Claude/Codex.                            |
| `echo`              | No-cost smoke tests, CLI onboarding, harness regression tests, and hello-world deployments.                                                  | Not an LLM backend; intentionally minimal and not suitable for real agent work.                                                        |

## Backend Summary

### `claude`

`claude` wraps the Claude Agent SDK. It is the most mature backend today and carries the richest safety and
observability surface.

Use it when:

- The agent needs the most proven autonomous coding behavior.
- You want Claude-oriented skill folders under `.claude/skills/`.
- You need the strongest `hooks.yaml` story, including PreToolUse and PostToolUse enforcement/audit rows.
- You want the metrics superset that the other backends try to align with.

Nuances:

- Identity lives in `CLAUDE.md`.
- Supports Claude API keys or Claude Code OAuth tokens.
- Session behavior is tied to the Claude Agent SDK and Claude's own model/tool semantics.
- This has historically been the safest default for high-value autonomous development, but cost/credit availability can
  make it impractical to run every agent on Claude all the time.

### `codex`

`codex` is the Codex-native backend implemented in Node.js. It uses OpenAI's Responses API with Codex-oriented defaults
such as `gpt-5.5` and `CODEX_REASONING_EFFORT=xhigh`.

Use it when:

- You want a coding-focused OpenAI backend rather than the generic OpenAI Agents SDK backend.
- You want GPT-5.5 / Codex-style behavior with explicit reasoning-tier observability.
- You need per-session SSE updates and live `/api/traces` proof of model, reasoning effort, and streaming mode.
- You want a strong candidate for cost-aware always-on agents like reliability monitors.

Nuances:

- Identity lives in `AGENTS.md` under `.codex/`.
- Runtime is Node.js, unlike the Python `claude`, `openai`, and `gemini` backends.
- It supports bounded shell, memory tools, URL-shaped MCP tools, conversation logs, metrics, OpenTelemetry, and session
  continuity through `previous_response_id`.
- It is newer than `claude`; Codex-owned shell, memory, and URL-shaped MCP function tools now pass through a
  PreToolUse-style hook gate, while broader SDK/tool-surface parity is still maturing.
- `.codex/config.toml` exists for backend-local configuration, but not every Codex CLI-style flag necessarily maps to an
  active Node backend feature yet.

### `openai`

`openai` is the general OpenAI Agents SDK backend. It is intentionally separate from `codex`: `openai` is for the
broader OpenAI Agents SDK surface, while `codex` is reserved for Codex-native coding-agent behavior.

Use it when:

- You want OpenAI model access through the Python Agents SDK.
- You need built-in WebSearchTool or Playwright-backed ComputerTool behavior.
- You want OpenAI SDK-native sessions, streaming, browser contexts, and MCP integration.
- You are testing general OpenAI agent behavior rather than the Codex-specific runtime.

Nuances:

- Identity lives in `AGENTS.md` under `.openai/`.
- It was renamed from the earlier Python `codex` backend, so older docs or tests may still mention that migration
  history.
- It has a richer browser/web-search tool surface than the current Node `codex` backend.
- It is a good tool-heavy OpenAI backend, but not the place to evolve Codex-specific behavior.

### `gemini`

`gemini` wraps Google's `google-genai` SDK. It gives Witwave a non-OpenAI, non-Claude provider path and is useful for
provider diversity.

Use it when:

- You want Gemini model behavior for comparison, fallback, or cost/provider diversity.
- You want to test the platform's cross-backend assumptions against a meaningfully different SDK.
- You want Gemini's Automatic Function Calling path with MCP sessions.

Nuances:

- Identity lives in `GEMINI.md`.
- Gemini does not currently have the same skill-folder convention as Claude.
- Session history is stored as JSON under the Gemini memory/session directory.
- Tool and memory ergonomics are different enough that cross-backend behavior should be encoded in the primary identity
  document, not only in backend-specific skills.

### `echo`

`echo` is a zero-dependency stub backend. It returns a canned response and exists so the platform can be installed and
tested without any LLM credentials.

Use it when:

- You need a hello-world agent from `ww agent create`.
- You are testing harness routing, service creation, health checks, or operator reconciliation.
- You want deterministic CI smoke coverage without spending model tokens.

Nuances:

- No API keys required.
- No real model execution.
- Deliberately omits the expensive backend surfaces: no MCP, no conversation persistence, no hooks, no session binding,
  and no full observability parity.

## Shared Expectations

The LLM-backed backends should converge on these platform contracts:

- A2A discovery at `/.well-known/agent.json`.
- A2A JSON-RPC task handling at `/`.
- Split liveness/readiness health checks.
- Conversation and trace inspection endpoints protected by `CONVERSATIONS_AUTH_TOKEN`, unless explicitly disabled for
  local development.
- Prometheus `backend_*` metrics with stable labels: `agent`, `agent_id`, and `backend`.
- Backend-owned session state, memory, conversation logs, and tool/activity logs.
- Mounted identity documents rather than baked image behavior.
- MCP consumption through backend-local config where supported.

`echo` is the intentional exception. It proves the platform plumbing, not backend parity.

## Hook Boundaries

Hook support is not identical across backends. The important question is where the backend can intercept a tool call
before it executes:

| Backend  | Current hook boundary                                                                                             | Practical meaning                                                                                            |
| -------- | ----------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------ |
| `claude` | Full Claude SDK `PreToolUse` / `PostToolUse` wrapping around SDK tool calls.                                      | Reference implementation for prevention-first policy, audit rows, warnings, denials, and hook metrics.       |
| `codex`  | PreToolUse-style gate before Codex-owned function tools: shell, memory, and URL-shaped MCP tools.                 | Strong for the tools the Node backend owns; not a claim of full Claude SDK hook parity or PostToolUse scope. |
| `openai` | Partial shell-tool baseline enforcement plus scaffolded non-shell hook plumbing.                                  | Useful for obvious-dangerous shell patterns; broader SDK/built-in tool interposition is still narrower.      |
| `gemini` | Hook config and metrics skeleton exists, but google-genai Automatic Function Calling currently bypasses the gate. | Tool calls are observable after the fact; true PreToolUse blocking needs the future hand-rolled tool loop.   |
| `echo`   | None.                                                                                                             | Smoke backend only; no LLM tools and no hook contract.                                                       |

When policy enforcement is the deciding factor, choose `claude` first and `codex` second for Codex-owned tools. When a
backend says it has hook metrics, that means the metric family exists; it does not necessarily mean every SDK tool path
can be blocked before execution.

Prometheus exposes `backend_hooks_enforcement_mode` to make that boundary machine-readable: `1` means active
PreToolUse-style enforcement on the backend's covered tool surface, `0` means partial/skeleton coverage, and `-1` means
no active hook rules.

## Current Practical Defaults

For a production-ish self-managing agent, prefer this order:

1. `claude` when maximum maturity and hook safety matter most.
2. `codex` when cost, GPT-5.5, and Codex-native coding behavior matter most.
3. `openai` when browser/web-search/computer-tool behavior is the deciding factor.
4. `gemini` when provider diversity or Gemini-specific comparison is the deciding factor.
5. `echo` only for bootstrap, smoke tests, and CI.

For mixed-backend agents, keep the role explicit in `.witwave/backend.yaml`. For example, an agent might route normal
A2A work to `codex`, retain `claude` for a high-confidence review path, and keep `gemini` available for independent
second opinions. Avoid enabling multiple expensive backends without a clear route or test purpose.

## Files

| Backend  | Source             | Primary identity document | Main README                 |
| -------- | ------------------ | ------------------------- | --------------------------- |
| `claude` | `backends/claude/` | `.claude/CLAUDE.md`       | `backends/claude/README.md` |
| `codex`  | `backends/codex/`  | `.codex/AGENTS.md`        | `backends/codex/README.md`  |
| `openai` | `backends/openai/` | `.openai/AGENTS.md`       | `backends/openai/README.md` |
| `gemini` | `backends/gemini/` | `.gemini/GEMINI.md`       | `backends/gemini/README.md` |
| `echo`   | `backends/echo/`   | none                      | `backends/echo/README.md`   |
