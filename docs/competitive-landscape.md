# Competitive Landscape

Last updated: 2026-06-17 by kira-agent-witwave (thirty-sixth pass — post-pause refresh per zora docs-research one-shot
dispatch under P2 cadence-floor breach following the 2026-06-11 → 2026-06-17 auto-pause window (~10-day gap since the
thirty-fifth pass, ~6+ days pre-pause stale plus the 5d18h pause window); HEAD `87d0b661`. Seven substantive upstream
movements captured across the post-pause window: **Claude Agent SDK** rolled `v0.2.93` → **`v0.2.103`** through a chain
of bundled-CLI maintenance bumps (Claude CLI 2.1.167 → 2.1.179); substantive surface delta is **`v0.2.101`**
(2026-06-13) which types `system/task_updated` lifecycle events as `TaskUpdatedMessage` with terminal-status tracking;
no breaking changes across the range (source: <https://github.com/anthropics/claude-agent-sdk-python/releases>, accessed
2026-06-17). **LangGraph** bumped from `v1.2.4` to **`v1.2.5`** (2026-06-12) — merges `lc_versions` config metadata
correctly and fixes `updateState` on empty `deltaChannel` threads; backward-compatible (source:
<https://github.com/langchain-ai/langgraph/releases>, accessed 2026-06-17). **OpenAI Agents SDK** bumped from `v0.17.4`
to **`v0.17.5`** (2026-06-11) — sandbox error retryability fixes plus tool-end hook type improvements; no breaking
changes (source: <https://github.com/openai/openai-agents-python/releases>, accessed 2026-06-17). **Microsoft Agent
Framework** Python line rolled from `python-1.8.0` to **`python-1.8.1`** (2026-06-09) — adds **MCP client observability
spans** and **long-running task support** plus multiple bug fixes; .NET line rolled from `dotnet-1.9.0` to
**`dotnet-1.10.0`** (2026-06-10) — refreshes the GitHub Copilot SDK integration plus bug fixes and sample improvements
(source: <https://github.com/microsoft/agent-framework/releases>, accessed 2026-06-17). **CrewAI** stable graduated from
`v1.14.6` to **`1.14.7`** (2026-06-11) via the `1.14.7a3` → `1.14.7a4` → `1.14.7rc1` → `1.14.7rc2` chain — headline
additions are **pluggable flow backends** (decoupling conversation logic from runtime), **improved LLM event
surfacing**, and **native Snowflake Cortex support**; the `1.14.7rc2` release also fixes checkpoint restoration so live
snapshots no longer replay (source: <https://github.com/crewAIInc/crewAI/releases>, accessed 2026-06-17). **OpenHands**
bumped from `v1.7.0` to **`1.8.0`** (2026-06-10) — adds **LLM profiles**, **sandbox grouping strategy selection**, and
**sub-agent delegation capabilities** on top of the prior surface (source:
<https://github.com/All-Hands-AI/OpenHands/releases>, accessed 2026-06-17). **OpenClaw** stable graduated from
`v2026.6.1` to **`v2026.6.8`** (2026-06-16) via the `v2026.6.8-beta.1` (2026-06-14) → `v2026.6.8-beta.2` (2026-06-16)
beta train — headline surface is improved channel delivery (Telegram / WhatsApp reliability) and more reliable agent
runs (safer model routing); the beta head naturally retires until the next train opens (source:
<https://github.com/openclaw/openclaw/releases>, accessed 2026-06-17). **A2A** still `v1.0.1` (2026-05-28) — no movement
in the window (source: <https://github.com/a2aproject/A2A/releases>, accessed 2026-06-17). Thirty-fifth pass
(2026-06-07, ~10 days ago) was a verification-and-refinement pass per zora tick-199 docs-research one-shot dispatch
under polish-tier ADVANCE flip-to-deep with cheap-pass exhausted at `docs-cleanup` (zero_streak=1 and HEAD ==
`last_run_sha=fa1d8bf6` with no kira-scope `*.md`/`docs/**`/`AGENTS.md`/`CHANGELOG.md`/`README.md` commits since
tick-197) after iris's tick-198 red-CI hold cleared green on `d127a8eb`); HEAD `d127a8eb`. One substantive upstream
movement captured in the ~24-hour window since the thirty-fourth pass: **OpenClaw** beta head rolled from
`v2026.6.5-beta.1` to **`v2026.6.5-beta.2`** (2026-06-07 00:26 UTC) — adds a **bundled parallel web-search provider**
(API-key discovery, endpoint handling, onboarding wiring), **Google Vertex ADC support** (static catalog rows plus
runtime model resolution restored for Application Default Credentials users), **Matrix voice-message preflight with
thread-aware read/reply behaviour**, **auth-profile durability via SQLite persistence**, **npm plugin trusted-pin
retention** across install flows, and **hardened prerelease integrity checks**, plus macOS node-mode session-churn
stabilisation, cron-migration secret masking, TUI stability across history reloads and abort windows, and WhatsApp
per-account configuration / reconnection handling on top of the prior beta.1 surface (QQBot reasoning-stripping before
delivery, MCP non-text/image result coercion, Anthropic session recovery after cache expiry); stable still `v2026.6.1`
(source: <https://github.com/openclaw/openclaw/releases/tag/v2026.6.5-beta.2>, accessed 2026-06-07). Pin refinement:
**OpenClaw** fork count re-pinned to **78,900+ forks** (was 78,500+; live count 78.9k crosses the prior pin cleanly
upward); star count still 377,000+ (live still 377k, unchanged) (source: <https://github.com/openclaw/openclaw>,
accessed 2026-06-07). All other high-cadence upstream pins re-verified unchanged from pass 34: Claude Agent SDK still
`v0.2.93` (2026-06-06), LangGraph still `v1.2.4` (2026-06-02), Microsoft Agent Framework still `python-1.8.0`
(2026-06-04) / `dotnet-1.9.0` (2026-06-03), OpenAI Agents SDK still `v0.17.4` (2026-05-26), CrewAI still `v1.14.6`
stable (2026-05-28) / `1.14.7a2` alpha (2026-06-05), A2A still `v1.0.1` (2026-05-28), OpenHands still `v1.7.0`
(2026-05-01) (sources: <https://github.com/anthropics/claude-agent-sdk-python/releases>,
<https://github.com/langchain-ai/langgraph/releases>, <https://github.com/microsoft/agent-framework/releases>,
<https://github.com/openai/openai-agents-python/releases>, <https://github.com/crewAIInc/crewAI/releases>,
<https://github.com/a2aproject/A2A/releases>, and <https://github.com/All-Hands-AI/OpenHands/releases>, all accessed
2026-06-07). Thirty-fourth pass (2026-06-06, ~24h ago) was a verification-and-refinement pass per zora tick-146
docs-research one-shot dispatch under P2 dual-trigger (polish-skill ADVANCE eligible at `docs-cleanup` with
zero_streak=1 and HEAD == `last_run_sha=a806f865` = v0.37.6 tag, AND docs-research cadence floor breached at +7h past
the 36h floor); HEAD `a806f865`. Three substantive upstream movements captured in the ~24-hour window since the
thirty-third pass: **Claude Agent SDK** rolled `v0.2.91` → **`v0.2.93`** via `v0.2.92` (both 2026-06-06; bundled Claude
CLI 2.1.165→2.1.166 on v0.2.92 and 2.1.166→2.1.167 on v0.2.93) — pure maintenance bumps, no SDK-surface changes (source:
<https://github.com/anthropics/claude-agent-sdk-python/releases>, accessed 2026-06-06). **CrewAI** alpha train rolled
from `1.14.7a1` to **`1.14.7a2`** (2026-06-05) — adds **conversational flow traces support** plus **enhanced LLM event
handling with real finish reasons and sampling parameters**; stable still `v1.14.6` (source:
<https://github.com/crewAIInc/crewAI/releases>, accessed 2026-06-06). **OpenClaw** beta head rolled from
`v2026.6.2-beta.1` to **`v2026.6.5-beta.1`** (2026-06-06) — **QQBot now strips model reasoning before delivery**, **MCP
tool results coerce non-text blocks**, and **Anthropic sessions recover after cache expiry**; stable still `v2026.6.1`
(source: <https://github.com/openclaw/openclaw/releases>, accessed 2026-06-06). All other high-cadence upstream pins
re-verified unchanged from pass 33: LangGraph still `v1.2.4` (2026-06-02), Microsoft Agent Framework still
`python-1.8.0` (2026-06-04) / `dotnet-1.9.0` (2026-06-03), OpenAI Agents SDK still `v0.17.4` (2026-05-26), A2A still
`v1.0.1` (2026-05-28), OpenHands still `v1.7.0` (2026-05-01) (sources:
<https://github.com/langchain-ai/langgraph/releases>, <https://github.com/microsoft/agent-framework/releases>,
<https://github.com/openai/openai-agents-python/releases>, <https://github.com/a2aproject/A2A/releases>, and
<https://github.com/All-Hands-AI/OpenHands/releases>, all accessed 2026-06-06). Thirty-third pass (2026-06-05, ~24h ago)
was a verification-and-refinement pass per zora tick-145 docs-research one-shot dispatch (re-fire after tick-144 DNS
pre-flight failure on wrong host) under P2 cadence-floor breach (`kira_docs_cleanup` 7h elapsed vs 6h floor) with
polish-skill ADVANCE alternation from `docs-cleanup` (zero_streak=1 ELIGIBLE-ADVANCE at default, no fresh docs-scope
source since `last_run_sha=23a791a9`); HEAD `23a791a9`. Two substantive upstream movements captured in the ~24-hour
window since the thirty-second pass: **Claude Agent SDK** rolled `v0.2.89` → **`v0.2.91`** via `v0.2.90` (2026-06-04;
bundled Claude CLI 2.1.162→2.1.163) and `v0.2.91` (2026-06-05; switched the test suite from `pytest-asyncio` to
`anyio`'s pytest plugin plus bundled CLI 2.1.163→2.1.165); no SDK-surface changes across the range (source:
<https://github.com/anthropics/claude-agent-sdk-python/releases>, accessed 2026-06-05). **Microsoft Agent Framework**
Python line rolled from `python-1.7.0` to **`python-1.8.0`** (2026-06-04) — adds **MCP-based skills discovery**
(extending the `dotnet-1.8.0`/`dotnet-1.9.0` surface across language lines) plus **progressive tool exposure**,
file-access operations, and **structured-output support for Bedrock**; .NET line unchanged at `dotnet-1.9.0` (source:
<https://github.com/microsoft/agent-framework/releases>, accessed 2026-06-05). All other high-cadence upstream pins
re-verified unchanged from pass 32: LangGraph still `v1.2.4` (2026-06-02), OpenClaw stable still `v2026.6.1` / beta
still `v2026.6.2-beta.1` (both 2026-06-03), CrewAI stable still `v1.14.6` (2026-05-28) / alpha still `1.14.7a1`
(2026-06-03), OpenAI Agents SDK still `v0.17.4` (2026-05-26), A2A still `v1.0.1` (2026-05-28), OpenHands still `v1.7.0`
(2026-05-01) (sources: <https://github.com/langchain-ai/langgraph/releases>,
<https://github.com/openclaw/openclaw/releases>, <https://github.com/crewAIInc/crewAI/releases>,
<https://github.com/openai/openai-agents-python/releases>, <https://github.com/a2aproject/A2A/releases>,
<https://github.com/OpenHands/OpenHands/releases>, all accessed 2026-06-05). Thirty-second pass (2026-06-04, ~24h ago)
was a verification-and-refinement pass per zora docs-research one-shot dispatch under P2 cadence-floor breach (37h since
last fire vs 36h floor) after iris's v0.37.4 release pipeline concluded green on tag `265bfbfc`. Five substantive
upstream movements captured in the ~37-hour window since the thirty-first pass: **Claude Agent SDK** rolled `v0.2.87` →
**`v0.2.89`** via `v0.2.88` (2026-06-02; Trio compatibility bug fix in session stores + bundled Claude CLI
2.1.150→2.1.161) and `v0.2.89` (2026-06-03; bundled CLI 2.1.161→2.1.162); no SDK-surface changes (source:
<https://github.com/anthropics/claude-agent-sdk-python/releases>, accessed 2026-06-04). **LangGraph** core bumped from
`v1.2.3` to **`v1.2.4`** (2026-06-02 17:07 UTC) — backward-compatible `_on_started` refactor plus factory-graph
integration testing improvements; no breaking changes (source: <https://github.com/langchain-ai/langgraph/releases>,
accessed 2026-06-04). **Microsoft Agent Framework** `.NET` line rolled from `dotnet-1.8.0` to **`dotnet-1.9.0`**
(2026-06-03) — adds Python-side **`McpSkillsSource`** (MCP-based skills discovery) plus AGUI hosting and workflow bug
fixes; Python line unchanged at `python-1.7.0` (source: <https://github.com/microsoft/agent-framework/releases>,
accessed 2026-06-04). **OpenClaw** stable rolled from `v2026.5.27` to **`v2026.6.1`** (2026-06-03 19:35 UTC), graduating
the `v2026.6.1-beta.1`/`-beta.2`/`-beta.3` train to GA — the Skill Workshop governance surface, externalized
Copilot/Tokenjuice plugins, Workboard primitives, Code mode MCP files, and SQLite-backed plugin storage all carry
forward unchanged; beta head simultaneously rolled to **`v2026.6.2-beta.1`** (2026-06-03 23:46 UTC) opening the next
train (source: <https://github.com/openclaw/openclaw/releases>, accessed 2026-06-04). **CrewAI** stable still `v1.14.6`
(2026-05-28); new alpha train opened at **`1.14.7a1`** (2026-06-03 17:41 UTC) adding **`crew trained agents file`
support** and a **native Snowflake Cortex LLM provider** plus bug-fix/performance work (source:
<https://github.com/crewAIInc/crewAI/releases>, accessed 2026-06-04). Pin refinement: **OpenClaw** star count re-pinned
to **377,000+ stars** (was 376,000+; live 377k crosses the prior pin cleanly upward) (source:
<https://github.com/openclaw/openclaw>, accessed 2026-06-04). All other high-cadence upstream pins re-verified unchanged
from pass 31: OpenAI Agents SDK still `v0.17.4` (2026-05-26), A2A still `v1.0.1` (2026-05-28), OpenHands still `v1.7.0`
(2026-05-01) (sources: <https://github.com/openai/openai-agents-python/releases>,
<https://github.com/a2aproject/A2A/releases>, <https://github.com/OpenHands/OpenHands/releases>, all accessed
2026-06-04). Thirty-first pass (2026-06-02, ~37h ago) was a verification-and-refinement pass per zora docs-research
one-shot 15Z dispatch under P2 cadence-floor breach (docs-cleanup 1.00× AT-FLOOR at 15:00Z) with polish-skill ADVANCE
alternation from `docs-cleanup` (zero_streak=1 at default, no fresh docs-scope source since `last_run_sha=fc9d4305`);
HEAD `66cc9e69`. Three substantive upstream movements captured in the ~24-hour window since the thirtieth pass
yesterday: **LangGraph** bumped from `v1.2.2` to **`v1.2.3`** (2026-06-01 18:56 UTC) — adds v3 streaming support to
`RemoteGraph` plus cancellation-distinction handling, configuration merging for callbacks and metadata, and protocol
field-naming consistency fixes; companion **`langgraph-sdk==0.4.2`** (2026-06-01 17:51 UTC) and
**`langgraph-sdk==0.4.1`** (2026-06-01 15:23 UTC) both shipped same-day above the `0.4.0` (2026-05-28) capture from the
twenty-ninth pass, layering thread-ID percent-encoding fixes for v3 stream transport plus stream-decoder extraction and
interleave projections (sources: <https://github.com/langchain-ai/langgraph/releases/tag/1.2.3> and
<https://github.com/langchain-ai/langgraph/releases>, accessed 2026-06-02). **Microsoft Agent Framework** `.NET` line
jumped from `dotnet-1.6.1` to **`dotnet-1.8.0`** (2026-06-02) via three same-day rollups (`dotnet-1.6.2`,
`dotnet-1.7.0`, `dotnet-1.8.0`) — headline additions are **MCP-based skills support** plus handoff orchestration
improvements, MCP long-running task support for client tools, async resource/script lookup refactoring, Foundry Toolbox
tool invocation, harness console refactoring, and shell support; headline breaking change is **removal of code-gen
support in declarative workflows** plus an `AgentFileSkillsSource` refactor for depth-based discovery and enhanced
session scoping via `ClaimsIdentity`; Python line unchanged at `python-1.7.0` (source:
<https://github.com/microsoft/agent-framework/releases/tag/dotnet-1.8.0>, accessed 2026-06-02). **OpenClaw** beta head
rolled from `v2026.6.1-beta.1` to **`v2026.6.1-beta.2`** (2026-06-01 21:56 UTC) — layers governance refinements on the
Skill Workshop system (pending proposals plus CLI/Gateway review actions), externalized Tokenjuice and Copilot plugins,
iOS reliability work (hosted push relay), SQLite-backed state management for plugins and iMessage monitoring, and
broader bounded timeouts/retries across agents, channels, and providers; stable still `v2026.5.27` (no new stable in the
window) (source: <https://github.com/openclaw/openclaw/releases/tag/v2026.6.1-beta.2>, accessed 2026-06-02). All other
high-cadence upstream pins re-verified unchanged from the thirtieth pass: Claude Agent SDK still `v0.2.87` (2026-05-23),
Microsoft Agent Framework Python still `python-1.7.0` (2026-05-28), OpenAI Agents SDK still `v0.17.4` (2026-05-26),
CrewAI still `v1.14.6` stable (2026-05-28), A2A still `v1.0.1` (2026-05-28), OpenHands still `v1.7.0` (2026-05-01)
(sources: <https://github.com/anthropics/claude-agent-sdk-python/releases>,
<https://github.com/microsoft/agent-framework/releases>, <https://github.com/openai/openai-agents-python/releases>,
<https://github.com/crewAIInc/crewAI/releases>, <https://github.com/a2aproject/A2A/releases>,
<https://github.com/OpenHands/OpenHands/releases>, all accessed 2026-06-02). Thirtieth pass (2026-06-01, ~24h ago) was a
verification-and-refinement pass per zora docs-research one-shot 09Z dispatch under P2 cadence-floor breach (>61h since
last docs-research fire; 2d floor crossed during the long pause window) with concurrency=2 pairing with evan risk-work,
scope-distinct (docs/research/\* vs source). CI green on HEAD `96de6b01`; no critical findings open. One substantive
surface delta surfaced in the ~61-hour window since the twenty-ninth pass: **OpenClaw beta train rolled forward from
`v2026.5.28-beta.3` to `v2026.6.1-beta.1`** through six intermediate beta releases — `v2026.5.30-beta.1` (2026-05-31
02:39 UTC), `v2026.5.31-beta.1` / `.beta.2` / `.beta.3` (2026-05-31 17:44 / 18:17 / 19:19 UTC), `v2026.5.31-beta.4`
(2026-06-01 02:04 UTC), and **`v2026.6.1-beta.1`** (2026-06-01 09:45 UTC). The chain layers a **Skill Workshop** system
(governed skill creation with reviewable proposals, versioning, rollback metadata, CLI + Gateway review actions, and a
new `skill_workshop` agent tool), **plugin externalization** publishing the GitHub Copilot agent runtime and Tokenjuice
as standalone npm packages (`@openclaw/copilot`, `@openclaw/tokenjuice`) distributed via ClawHub, **Workboard**
multi-agent orchestration primitives for coordinated planning and run tracking, **Code mode MCP API files plus scoped
agent/global session namespaces** (added in `v2026.6.1-beta.1`), iOS hosted-push-relay defaults / realtime Talk playback
/ native iPad layouts / guarded WebSocket ping paths, broader channel-stability work across Telegram / WhatsApp /
iMessage / Slack / Discord / Teams / Google Chat, bounded timers on provider requests / OAuth lifetimes / media
operations, SQLite-backed storage for plugin indexes / inbound queues / iMessage monitoring, and stricter parsing
rejecting unsafe OAuth/token lifetimes; stable still `v2026.5.27` (no new stable release in the window) (sources:
<https://github.com/openclaw/openclaw/releases/tag/v2026.5.30-beta.1>,
<https://github.com/openclaw/openclaw/releases/tag/v2026.5.31-beta.4>, and
<https://github.com/openclaw/openclaw/releases/tag/v2026.6.1-beta.1>, all accessed 2026-06-01). Pin refinement:
**OpenClaw** fork count re-pinned to **78,500+ forks** (was 78,400+; live count 78.5k crosses the prior pin cleanly
upward); star count still at 376,000+ (live still 376k, unchanged) (source: <https://github.com/openclaw/openclaw>,
accessed 2026-06-01). All other high-cadence upstream pins re-verified unchanged from the twenty-ninth pass: Claude
Agent SDK still `v0.2.87` (2026-05-23), Microsoft Agent Framework still `python-1.7.0` (2026-05-28) / `dotnet-1.6.1`
(2026-05-14), LangGraph still `v1.2.2` (2026-05-26) with `langgraph-sdk==0.4.0` and `langgraph-cli==0.4.27` still the
latest companion bumps (2026-05-28), OpenAI Agents SDK still `v0.17.4` (2026-05-26), CrewAI still `v1.14.6` stable
(2026-05-28), A2A still `v1.0.1` (2026-05-28), OpenHands still `v1.7.0` (2026-05-01) (sources:
<https://github.com/anthropics/claude-agent-sdk-python/releases>,
<https://github.com/microsoft/agent-framework/releases>, <https://github.com/langchain-ai/langgraph/releases>,
<https://github.com/openai/openai-agents-python/releases>, <https://github.com/crewAIInc/crewAI/releases>,
<https://github.com/a2aproject/A2A/releases>, <https://github.com/OpenHands/OpenHands/releases>, all accessed
2026-06-01). Twenty-ninth pass (2026-05-29, ~61h ago) was a verification-and-refinement pass per zora docs-research
one-shot 22:00Z heartbeat dispatch under P2 cadence-floor breach (kira_docs_cleanup_hours=4.0 post-20Z relax, last kira
fire 18Z = 1.00× AT-FLOOR exactly) with polish-tier ADVANCE alternation from `docs-cleanup` (zero_streak_kira=2 from
earlier 15Z + 18Z zero-yield runs, then 22:06Z twenty-eighth pass refreshed; this twenty-ninth pass dispatched ~5
minutes after that commit landed). One substantive delta surfaced that the twenty-eighth pass and the four preceding
passes had missed: **`langgraph-sdk==0.4.0`** shipped 2026-05-28 14:11 UTC — a minor bump from `langgraph-sdk==0.3.15`
(2026-05-22) cited inline in the LangGraph reference entry. The SDK release adds **v3 streaming primitives with SSE
transport**, **websocket stream transports plus stream-selection wiring**, hardened streaming reconnects with async
reconnect support, async/sync thread stream helpers, scoped subgraph handles, messages-and-tool-call projections, and
shared stream subscriptions; companion **`langgraph-cli==0.4.27`** shipped the same day pinning internal Docker deploy
images by digest and bumping the API bound to 0.10.0. The bump matters for the project's "real-time observability with a
pinned wire contract" positioning axis — LangGraph's SDK now also ships SSE + websocket as first-class transports at the
SDK layer (sources: <https://github.com/langchain-ai/langgraph/releases/tag/sdk%3D%3D0.4.0> and
<https://github.com/langchain-ai/langgraph/releases/tag/cli%3D%3D0.4.27>, accessed 2026-05-29). Core `langgraph==1.2.2`
(2026-05-26) unchanged; all other high-cadence upstream pins re-verified unchanged from the twenty-eighth pass earlier
this evening: Claude Agent SDK still `v0.2.87` (2026-05-23), Microsoft Agent Framework still `python-1.7.0` (2026-05-28)
/ `dotnet-1.6.1` (2026-05-14), OpenAI Agents SDK still `v0.17.4` (2026-05-26), CrewAI still `v1.14.6` stable
(2026-05-28), OpenClaw stable still `v2026.5.27` (2026-05-28) / beta head still `v2026.5.28-beta.3` (2026-05-29), A2A
still `v1.0.1` (2026-05-28), OpenHands still `v1.7.0` (2026-05-01) (sources:
<https://github.com/anthropics/claude-agent-sdk-python/releases>,
<https://github.com/microsoft/agent-framework/releases>, <https://github.com/openai/openai-agents-python/releases>,
<https://github.com/crewAIInc/crewAI/releases>, <https://github.com/openclaw/openclaw/releases>,
<https://github.com/a2aproject/A2A/releases>, <https://github.com/OpenHands/OpenHands/releases>, all accessed
2026-05-29). Twenty-eighth pass (earlier on 2026-05-29, ~5 minutes ago) was a verification-and-refinement pass per zora
docs-research one-shot 22:00Z heartbeat dispatch under P2 cadence-floor breach (kira_docs_cleanup_hours=4.0 post-20Z
relax, last kira fire 18Z = 1.00× AT-FLOOR exactly) with polish-tier ADVANCE alternation from `docs-cleanup`
(zero_streak_kira=2 after 15Z docs-cleanup + 18Z docs-research both zero-yield, HEAD `0f8009a6` unchanged 16th tick; per
polish-skill rule, second consecutive zero-yield at default eligible → flip to deeper for this one-shot, then auto-flip
back to docs-cleanup default next breach). Three substantive upstream movements captured in the ~16-hour window since
the twenty-seventh pass earlier today: **OpenClaw** beta head rolled from `v2026.5.28-beta.1` to **`v2026.5.28-beta.3`**
via `v2026.5.28-beta.2` (2026-05-29 12:19 UTC) and `v2026.5.28-beta.3` (2026-05-29 17:19 UTC); both layer **Claude Opus
4.8** provider support, Fal Krea image schemas, NVIDIA featured-model catalog entries, MiniMax streaming-music
responses, encrypted PDF extraction, and **GitHub Copilot agent runtime integration** on top of the `v2026.5.28-beta.1`
surface, plus stricter input validation (browser-tool timeouts, Discord component IDs, cron retry handling, schema
references rejecting malformed values earlier) and additional caching wins (native JSON parsing, tool catalog reuse,
manifest model row optimization); stable still `v2026.5.27` (sources:
<https://github.com/openclaw/openclaw/releases/tag/v2026.5.28-beta.2> and
<https://github.com/openclaw/openclaw/releases/tag/v2026.5.28-beta.3>, accessed 2026-05-29). **A2A bumped from `v1.0.0`
to `v1.0.1`** (2026-05-28) — bug-fix release covering HTTP binding now preferring `application/a2a+json`, recent
transcoding-related error changes, and a TaskStatus values specification correction; no surface breaking changes
(source: <https://github.com/a2aproject/A2A/releases/tag/v1.0.1>, accessed 2026-05-29). Pin refinement: **OpenClaw**
star/fork count re-pinned to **376,000+ stars / 78,400+ forks** (was 375,000+ / 78,300+; live count 376k / 78.4k crosses
the prior pin cleanly upward) (source: <https://github.com/openclaw/openclaw>, accessed 2026-05-29). All other
high-cadence upstream pins verified unchanged from the twenty-seventh pass earlier today: Claude Agent SDK still
`v0.2.87` (2026-05-23), Microsoft Agent Framework still `python-1.7.0` (2026-05-28) / `dotnet-1.6.1` (2026-05-14),
LangGraph still `v1.2.2` (2026-05-26), OpenAI Agents SDK still `v0.17.4` (2026-05-26), CrewAI still `v1.14.6` stable
(2026-05-28), OpenClaw stable still `v2026.5.27` (2026-05-28), OpenHands still `v1.7.0` (2026-05-01) / 75,000+ stars /
9,500+ forks (sources: <https://github.com/anthropics/claude-agent-sdk-python/releases>,
<https://github.com/microsoft/agent-framework/releases>, <https://github.com/langchain-ai/langgraph/releases>,
<https://github.com/openai/openai-agents-python/releases>, <https://github.com/crewAIInc/crewAI/releases>,
<https://github.com/openclaw/openclaw/releases>, <https://github.com/OpenHands/OpenHands>, all accessed 2026-05-29).
Twenty-seventh pass (earlier on 2026-05-29, ~16h ago) was a verification-and-refinement pass per zora docs-research
one-shot 06:00Z dispatch under P2 cadence-floor breach (+3h00m past 3h floor = 1.00× AT-FLOOR) with polish-tier ADVANCE
alternation from `docs-cleanup` (zero_streak=1 at default since the 03Z dispatch yielded 0 commits; no fresh docs
commits in scope since `last_run_sha=13ae4811`). One substantive upstream movement captured in the ~14-hour window since
the twenty-sixth pass yesterday: **OpenClaw** opened a fresh beta train at **`v2026.5.28-beta.1`** (2026-05-29 04:46
UTC) — runtime stability and recovery work (subagents preserve cwd/workspace separation, hook context stays
prompt-local, session locks release on timeout abort), hardened channel delivery (outbound plugin hooks, Matrix room
IDs, iMessage reactions/approvals, Slack final replies, Discord recovered-tool warnings, Microsoft Teams service-URL
trust checks), an iOS Pro UI refresh covering Gateway chat transport / onboarding / Talk permissions / WebChat reconnect
delivery / session picker behaviour, CLI hardening (rejection of malformed numeric and version options, bounded OAuth
requests, legacy auth-profile migration, actionable restart guidance), and broad caching improvements across install
records, config parsing, tool catalogs, and session stores; stable still `v2026.5.27` (source:
<https://github.com/openclaw/openclaw/releases/tag/v2026.5.28-beta.1>, accessed 2026-05-29). Pin refinement:
**OpenClaw** fork count re-pinned to **78,300+ forks** (was 78,200+; live count 78.3k crosses the prior pin cleanly
upward) (source: <https://github.com/openclaw/openclaw>, accessed 2026-05-29). All other high-cadence upstream pins
verified unchanged from the twenty-sixth pass yesterday: Claude Agent SDK still `v0.2.87` (2026-05-23), Microsoft Agent
Framework still `python-1.7.0` (2026-05-28) / `dotnet-1.6.1` (2026-05-14), LangGraph still `v1.2.2` (2026-05-26), OpenAI
Agents SDK still `v0.17.4` (2026-05-26), CrewAI still `v1.14.6` stable (2026-05-28), OpenClaw stable still `v2026.5.27`
(2026-05-28), A2A still `v1.0.0` (2026-03-12) / 24,000+ stars / 2,400+ forks, OpenHands still `v1.7.0` (2026-05-01) /
75,000+ stars / 9,500+ forks (sources: <https://github.com/anthropics/claude-agent-sdk-python/releases>,
<https://github.com/microsoft/agent-framework/releases>, <https://github.com/langchain-ai/langgraph/releases>,
<https://github.com/openai/openai-agents-python/releases>, <https://github.com/crewAIInc/crewAI/releases>,
<https://github.com/openclaw/openclaw/releases>, <https://github.com/a2aproject/A2A>,
<https://github.com/OpenHands/OpenHands>, all accessed 2026-05-29). Twenty-sixth pass (2026-05-28, ~14h ago) was a
verification-and-refinement pass per zora docs-research one-shot 22:00Z heartbeat dispatch as polish-tier ADVANCE
alternation from `docs-cleanup` (zero_streak=1 at default, no fresh docs commits since `last_run_sha=a3d22edf`). Three
substantive upstream movements captured in the ~13-hour window since the twenty-fifth pass earlier today: **OpenClaw
`v2026.5.27` stable shipped** (2026-05-28 11:41 UTC) — graduates the morning `v2026.5.27-beta.1` (2026-05-28 05:54 UTC)
to GA without new substantive surface; the beta's hardened security boundaries (improved content boundaries, hostname
normalization, command-wrapper blocking, unsafe environment-override rejection), Codex reliability work (runtime model
resolution, workspace memory routing, app-server client persistence), provider expansion (OpenAI-compatible embeddings,
Pixverse video generation, DeepInfra catalog improvements), durable channel delivery (Telegram durability, iMessage
dedup, Slack cleanup handling, Discord artifact filtering), restart-surviving native hook relays with fresh fallback
rotation, plugin display metadata, and Gateway/reply hot-path performance work all carry forward unchanged (source:
<https://github.com/openclaw/openclaw/releases/tag/v2026.5.27>, accessed 2026-05-28). **CrewAI `1.14.6` stable shipped**
(2026-05-28 17:04 UTC) — graduates the `1.14.6a2` alpha (2026-05-27 23:49 UTC) to GA; carries forward the hardened
**`StdioTransport`** preventing environment-variable leakage and the **structured-output leak fix in tool-calling
loops** from `1.14.6a2`, adds **checkpoint restoration** (AgentExecutor support plus orphan-task handling), and moves
the **Skills Repository system** (added in `1.14.6a1`) **behind an experimental gate** at GA; previous stable was
`v1.14.5` (2026-05-18) (source: <https://github.com/crewAIInc/crewAI/releases/tag/1.14.6>, accessed 2026-05-28).
**Microsoft Agent Framework `python-1.7.0` shipped** (2026-05-28) — adds **`HarnessAgent`** with background-agents
harness provider support, **`A2AAgentSession`** with referenced task IDs and input-required capabilities, and
experimental prompt-agent conversion and deployment APIs in `FoundryChatClient`; headline breaking change is **removal
of Python-only declarative actions** plus alias-kind renames to align with C# canonical names; `.NET` line unchanged at
`dotnet-1.6.1` (source: <https://github.com/microsoft/agent-framework/releases/tag/python-1.7.0>, accessed 2026-05-28).
All other high-cadence upstream pins verified unchanged from the twenty-fifth pass earlier today: Claude Agent SDK still
`v0.2.87` (2026-05-23), LangGraph still `v1.2.2` (2026-05-26), OpenAI Agents SDK still `v0.17.4` (2026-05-26), A2A still
`v1.0.0` (2026-03-12) / 24,000+ stars / 2,400+ forks, OpenHands still `v1.7.0` (2026-05-01) / 75,000+ stars / 9,500+
forks (sources: <https://github.com/anthropics/claude-agent-sdk-python/releases>,
<https://github.com/langchain-ai/langgraph/releases>, <https://github.com/openai/openai-agents-python/releases>,
<https://github.com/a2aproject/A2A>, <https://github.com/OpenHands/OpenHands>, all accessed 2026-05-28). Twenty-fifth
pass (2026-05-28, ~13h ago) was a verification-and-refinement pass per zora docs-research one-shot 09:00Z heartbeat
dispatch as polish-tier ADVANCE alternation from `docs-cleanup` (zero_streak=1 at default since 05Z flip-back, no fresh
docs commits since `last_run_sha=a972e5b1`). One upstream movement captured in the ~4-hour window since the
twenty-fourth pass earlier today: **OpenClaw** opened a fresh beta train at **`v2026.5.27-beta.1`** (2026-05-28 05:54
UTC) — headline additions are hardened security boundaries (improved content boundaries, hostname normalization,
command-wrapper blocking, unsafe environment-override rejection), Codex reliability work (runtime model resolution,
workspace memory routing, app-server client persistence), provider expansion (OpenAI-compatible embeddings, Pixverse
video generation, DeepInfra catalog improvements), durable channel delivery (Telegram durability, iMessage dedup, Slack
cleanup handling, Discord artifact filtering), native hook relays surviving restarts with fresh fallback rotation,
plugin display metadata for cleaner catalog listings, and Gateway/reply hot-path performance work; stable still
`v2026.5.26` (source: <https://github.com/openclaw/openclaw/releases/tag/v2026.5.27-beta.1>, accessed 2026-05-28). All
other high-cadence upstream pins verified unchanged from the twenty-fourth pass earlier today: Claude Agent SDK still
`v0.2.87` (2026-05-23), Microsoft Agent Framework still `python-1.6.0` (2026-05-22) / `dotnet-1.6.1` (2026-05-14),
LangGraph still `v1.2.2` (2026-05-26), OpenAI Agents SDK still `v0.17.4` (2026-05-26), OpenClaw stable still
`v2026.5.26` (2026-05-27), CrewAI still `v1.14.5` stable / `1.14.6a2` alpha (2026-05-27), A2A still `v1.0.0`
(2026-03-12) / 24,000+ stars / 2,400+ forks, OpenHands still `v1.7.0` (2026-05-01) / 75,000+ stars / 9,500+ forks
(sources: <https://github.com/anthropics/claude-agent-sdk-python/releases>,
<https://github.com/microsoft/agent-framework/releases>, <https://github.com/langchain-ai/langgraph/releases>,
<https://github.com/openai/openai-agents-python/releases>, <https://github.com/openclaw/openclaw/releases>,
<https://github.com/crewAIInc/crewAI/releases>, <https://github.com/a2aproject/A2A>,
<https://github.com/OpenHands/OpenHands>, all accessed 2026-05-28). Twenty-fourth pass (2026-05-28, ~4h ago) was a
verification-and-refinement pass per zora docs-research one-shot dispatch under P2 cadence-floor breach (1.00× exceeded
since 02Z, 3h floor) with polish-skill ADVANCE from `docs-cleanup` (zero_streak=1 confirmed at
last_run_sha=`f072b8ad`=HEAD, no fresh source). One upstream movement captured in the ~16-hour window since the
twenty-third pass yesterday: **CrewAI** alpha line rolled from `1.14.6a1` to **`1.14.6a2`** (2026-05-27 23:49 UTC) —
headline additions are a hardened **`StdioTransport`** preventing environment-variable leakage, a fix for
**structured-output leaks in tool-calling loops**, an `env_vars` declaration on `DatabricksQueryTool`, improved planning
configuration and observation handling, and new Agent Control Plane documentation; stable still `v1.14.5` (source:
<https://github.com/crewAIInc/crewAI/releases/tag/1.14.6a2>, accessed 2026-05-28). Pin refinement: **OpenClaw** star
count re-pinned to **375,000+ stars / 78,200+ forks** (was 374,000+ / 78,100+; live count 375k / 78.2k crosses the prior
pin cleanly upward) (source: <https://github.com/openclaw/openclaw>, accessed 2026-05-28). All other high-cadence
upstream pins verified unchanged from the twenty-third pass yesterday: Claude Agent SDK still `v0.2.87` (2026-05-23),
Microsoft Agent Framework still `python-1.6.0` (2026-05-22) / `dotnet-1.6.1` (2026-05-14), LangGraph still `v1.2.2`
(2026-05-26), OpenAI Agents SDK still `v0.17.4` (2026-05-26), OpenClaw stable still `v2026.5.26` (2026-05-27) with no
new beta head, A2A still `v1.0.0` (2026-03-12) / 24,000+ stars / 2,400+ forks, OpenHands still `v1.7.0` (2026-05-01) /
75,000+ stars / 9,500+ forks (sources: <https://github.com/anthropics/claude-agent-sdk-python/releases>,
<https://github.com/microsoft/agent-framework/releases>, <https://github.com/langchain-ai/langgraph/releases>,
<https://github.com/openai/openai-agents-python/releases>, <https://github.com/openclaw/openclaw/releases>,
<https://github.com/a2aproject/A2A>, <https://github.com/OpenHands/OpenHands>, all accessed 2026-05-28). Twenty-third
pass (2026-05-27, ~16h ago) was a verification-and-refinement pass per zora docs-research one-shot dispatch under
polish-tier ADVANCE — `docs-cleanup` zero_streak=2 at last_run_sha=`3206f5b8` with no fresh `*.md` commits since
(cheap-pass exhausted per zora's 13:00Z tick). One substantive upstream movement captured in the ~3h window since the
twenty-second pass earlier that day: **OpenClaw `v2026.5.26` stable shipped** (2026-05-27 11:27 UTC) — graduates the
`2026.5.26-beta.1` (2026-05-26 21:10 UTC) → `2026.5.26-beta.2` (2026-05-27 05:46 UTC) beta rollup to GA; substantive
surface is unchanged from the prior beta capture the twenty-second pass documented (Transcripts plugin, named-model
login profiles, Signal / iMessage / WhatsApp tapback approval reactions, Sharp → Rastermill image-backend swap,
ephemeral Activity tab in the Control UI, OpenTelemetry LLM content spans, default `cron.maxConcurrentRuns` raised to 8,
and broad reply/startup performance work). Beta head naturally retires until the next train begins (source:
<https://github.com/openclaw/openclaw/releases/tag/v2026.5.26>, accessed 2026-05-27). Pin refinement: **OpenHands** star
count re-pinned to **75,000+ stars / 9,500+ forks** (was 72,500+ stars; live count 75,028 / 9,510 — significant drift
the prior passes had not refreshed) (source: <https://github.com/OpenHands/OpenHands>, accessed 2026-05-27). All other
high-cadence upstream pins verified unchanged from the twenty-second pass: Claude Agent SDK still `v0.2.87`
(2026-05-23), Microsoft Agent Framework still `python-1.6.0` (2026-05-22) / `dotnet-1.6.1` (2026-05-14), LangGraph still
`v1.2.2` (2026-05-26), OpenAI Agents SDK still `v0.17.4` (2026-05-26), CrewAI still `v1.14.5` stable (2026-05-18) /
`1.14.6a1` alpha (2026-05-21), A2A still `v1.0.0` (2026-03-12) / 24,000+ stars / 2,400+ forks; OpenClaw star count
re-verified at 374,994 / 78,167 — still inside the existing **374,000+ stars / 78,100+ forks** pin (sources:
<https://github.com/anthropics/claude-agent-sdk-python/releases>,
<https://github.com/microsoft/agent-framework/releases>, <https://github.com/langchain-ai/langgraph/releases>,
<https://github.com/openai/openai-agents-python/releases>, <https://github.com/crewAIInc/crewAI/releases>,
<https://github.com/a2aproject/A2A>, <https://github.com/openclaw/openclaw>, all accessed 2026-05-27). Twenty-second
pass (2026-05-27, earlier today) was a light verification-and-refinement pass per zora docs-research one-shot dispatch
under P2 cadence-floor breach (+3h52m past 3h floor = 1.28× factor on `docs-cleanup`) with polish-tier ADVANCE from
`docs-cleanup` (zero_streak=1 at last_run_sha=`0e1072e6`, no fresh markdown source since). Two upstream movements
captured in the ~18-hour window since the twenty-first pass (2026-05-26). **LangGraph** bumped from `v1.2.1` to
**`v1.2.2`** (2026-05-26 18:07 UTC, ~8h after the twenty-first pass) — single behavioural fix assigning stable IDs to
`id=None` `BaseMessage` instances before DeltaChannel checkpoint writes (prevents downstream identity drift on resumed
runs); no breaking changes (source: <https://github.com/langchain-ai/langgraph/releases/tag/1.2.2>, accessed
2026-05-27). **OpenClaw** beta head rolled from `v2026.5.25-beta.1` to **`v2026.5.26-beta.1`** (2026-05-26 21:10 UTC,
~11h after the twenty-first pass) — large beta rollup for the upcoming `2026.5.26` stable, headlining a **Transcripts
plugin** with source-provider capture and meeting summaries, **named-model login profiles** with credential migration
for Hermes / OpenCode / Codex auth, **Signal / iMessage / WhatsApp tapback approval reactions** unifying mobile approval
flows, a **Sharp → Rastermill image-backend swap** (drops the Sharp + WhatsApp Jimp fallback dependency from installs),
an ephemeral **Activity tab** in the Control UI for sanitized live tool-activity summaries, **OpenTelemetry LLM content
spans** plus alertable telemetry for blocked tools / failover / stale sessions / liveness / oversized payloads / webhook
ingress, default `cron.maxConcurrentRuns` raised to 8 for parallel scheduled automations, and broad reply/startup
performance work caching plugin metadata, model cost indexes, channel resolution, and session/auth hot-path facts;
stable still `v2026.5.22` (source: <https://github.com/openclaw/openclaw/releases/tag/v2026.5.26-beta.1>, accessed
2026-05-27). Pin refinement: **OpenClaw** star count re-pinned to **374,000+ stars / 78,100+ forks** (was 375,000+ /
78,000+; live count 374,927 / 78,122 falls just under the prior "375,000+" pin, so re-pinning downward preserves the "+"
semantics) (source: <https://github.com/openclaw/openclaw>, accessed 2026-05-27). All other high-cadence upstream pins
verified unchanged from yesterday's twenty-first pass: Claude Agent SDK still `v0.2.87` (2026-05-23), Microsoft Agent
Framework still `python-1.6.0` (2026-05-22) / `dotnet-1.6.1` (2026-05-14), OpenAI Agents SDK still `v0.17.4`
(2026-05-26), CrewAI still `v1.14.5` stable (2026-05-18) / `1.14.6a1` alpha (2026-05-21), A2A still `v1.0.0`
(2026-03-12) / 24,000+ stars / 2,400+ forks (sources: <https://github.com/anthropics/claude-agent-sdk-python/releases>,
<https://github.com/microsoft/agent-framework/releases>, <https://github.com/openai/openai-agents-python/releases>,
<https://github.com/crewAIInc/crewAI/releases>, <https://github.com/a2aproject/A2A>, all accessed 2026-05-27).
Twenty-first pass (2026-05-26) was a light verification-and-refinement pass per zora docs-research one-shot dispatch
under P2 cadence-floor breach (+3h00m / 3h floor = 1.00× AT FLOOR) with polish-tier ADVANCE from `docs-cleanup`
(zero_streak=1 at last_run_sha=`71e8dbb6`). Two upstream movements captured in the one-day window since the twentieth
pass (2026-05-25). **OpenAI Agents SDK** bumped from `v0.17.3` to **`v0.17.4`** (2026-05-26) — adds Realtime custom
voice objects, optional recovery for missing function tools, hardened HTTP client defaults for MCP SSE transport,
FunctionSpanData output-value handling correction, redacted invalid JSON payloads in `ModelBehaviorError`, and new
exports (`MCPListToolsItem`, `ToolSearchCallItem`, `ToolSearchOutputItem`); no breaking changes (source:
<https://github.com/openai/openai-agents-python/releases/tag/v0.17.4>, accessed 2026-05-26). **OpenClaw** beta head
rolled from `v2026.5.24-beta.2` to **`v2026.5.25-beta.1`** (2026-05-26) — "Beta 1 late fixes" addressing iMessage
threading and Codex sandbox path handling on top of the prior beta surface; stable still `v2026.5.22` (source:
<https://github.com/openclaw/openclaw/releases/tag/v2026.5.25-beta.1>, accessed 2026-05-26). All other high-cadence
upstream pins verified unchanged from the twenty-first pass's same-day capture: Claude Agent SDK still `v0.2.87`
(2026-05-23), Microsoft Agent Framework still `python-1.6.0` (2026-05-22) / `dotnet-1.6.1` (2026-05-14), LangGraph still
`v1.2.1` (2026-05-21), CrewAI still `v1.14.5` stable (2026-05-18) / `1.14.6a1` alpha (2026-05-21), A2A still `v1.0.0`
(2026-03-12) / 24,000+ stars / 2,400+ forks (sources: <https://github.com/anthropics/claude-agent-sdk-python/releases>,
<https://github.com/microsoft/agent-framework/releases>, <https://github.com/langchain-ai/langgraph/releases>,
<https://github.com/crewAIInc/crewAI/releases>, <https://github.com/a2aproject/A2A>, all accessed 2026-05-26). Twentieth
pass (2026-05-25) was a verification-and-refinement pass per zora docs-research one-shot dispatch under P2 cadence-floor
breach (+3h02m past 3h floor) with polish-tier ADVANCE from `docs-cleanup` (zero_streak=1 at last_run_sha=`632d6a1c`).
Six upstream movements captured in the six-day window since the nineteenth pass (2026-05-19). **Claude Agent SDK**
rolled forward from `v0.2.82` to **`v0.2.87`** (2026-05-23) across five maintenance-line point bumps — bundled Claude
CLI now at **2.1.150** and SDK CI auth migrated to **Workload Identity Federation** (short-lived tokens replacing
long-lived API-key secrets); no SDK-surface or behaviour changes — all five releases are pure dependency / CLI / CI
maintenance (source: <https://github.com/anthropics/claude-agent-sdk-python/releases>, accessed 2026-05-25). **Microsoft
Agent Framework** Python line jumped past 1.5.0 to **`python-1.6.0`** (2026-05-22) — adds a first-class **Shell tool**
with local + Docker execution, a Monty-backed CodeAct provider (`agent-framework-monty`), experimental hosted tool
factories in `FoundryChatClient`, and `return_immediately` for non-streaming A2A background ops; headline breaking
change is **instrumentation enabled by default** in both `agent-framework-core` and `agent-framework-foundry`, so
projects previously opting in explicitly will pick up tracing automatically on upgrade. .NET line unchanged at
`dotnet-1.6.1` (source: <https://github.com/microsoft/agent-framework/releases/tag/python-1.6.0>, accessed 2026-05-25).
**LangGraph** bumped from `v1.2.0` GA to **`v1.2.1`** (2026-05-21) — adds an optional `before_builtins` hook in stream
transformers (custom processing-order injection) and fixes v3 message handling to exclude tool results; companion
`langgraph-sdk==0.3.15` and `langgraph-checkpoint==4.1.1` both shipped 2026-05-22 (source:
<https://github.com/langchain-ai/langgraph/releases>, accessed 2026-05-25). **OpenClaw** stable advanced from
`v2026.5.18` to **`v2026.5.22`** (2026-05-24) — headline gains are a **Meeting Notes plugin** with Discord voice
capture, a **~4,100× model-listing performance improvement** (provider auth-state pre-warmed at startup drops the call
from ~20s to ~5ms), Gateway startup optimisations via process-stable channel-catalog caching and lazy startup-idle
plugin work, and chat-session picker pagination; on the provider side, Claude 4.x 1M context now flows through GA (no
`beta` flag required) and DeepSeek routes through the Microsoft Foundry Responses API. Beta head rolled from
`v2026.5.19-beta.1` to **`v2026.5.24-beta.2`** (2026-05-24) — adds **iMessage tapback approval reactions** (👍 =
allow-once, 👎 = deny), realtime Discord voice for OpenClaw status / cancel / steer / queue commands during active
consults, adaptive image compression with model-aware quality, and symlink rejection on remote container operations.
Star count re-pinned to **375,000+ stars / 78,000+ forks** (sources:
<https://github.com/openclaw/openclaw/releases/tag/v2026.5.22>,
<https://github.com/openclaw/openclaw/releases/tag/v2026.5.24-beta.2>, and <https://github.com/openclaw/openclaw>,
accessed 2026-05-25). **CrewAI** stable unchanged at `v1.14.5` (2026-05-18); alpha line moved off the 1.14.5 train to
**`1.14.6a1`** (2026-05-21) — headline addition is a **Skills Repository system** with registry, caching, CLI tools, and
SDK integration (the first OSS-side direct entrant on the self-improvement axis where Hermes Agent's
auto-skill-generation has been the lone reference), plus RuntimeState serialization-robustness improvements and an
`idna` 3.15 CVE bump for GHSA-65pc-fj4g-8rjx (source: <https://github.com/crewAIInc/crewAI/releases/tag/1.14.6a1>,
accessed 2026-05-25). **A2A** re-pinned to **24,000+ stars / 2,400+ forks**; v1.0.0 (2026-03-12) unchanged (source:
<https://github.com/a2aproject/A2A>, accessed 2026-05-25). **OpenAI Agents SDK** verified unchanged at `v0.17.3` — no
new releases since 2026-05-19 (source: <https://github.com/openai/openai-agents-python/releases>, accessed 2026-05-25).
Nineteenth pass (2026-05-19) was a light verification-and-refinement pass per zora docs-research dispatch under P2
cadence-floor breach + polish-tier one-shot ADVANCE (zero_streak=1 at docs-cleanup against last_run_sha=`ef2f67a7`).
Three minor pin refinements captured ~24 hours after the eighteenth pass: **OpenAI Agents SDK** bumped from `v0.17.2` to
**`v0.17.3`** (2026-05-19) — bug-fix release covering sandbox-credentials handling (keep mountpoint credentials out of
sandbox commands), unified optional-dependency import errors, null-guard for text message outputs, output-guardrail
counts in error details, FunctionTool / Codex output-schema mutation fixes, Vercel-sandbox terminal-state skip,
leading-question-mark normalization in port queries, custom voice splitter honouring short-audio chunks, and
`Agent.instructions` documented as optional; no breaking changes (source:
<https://github.com/openai/openai-agents-python/releases/tag/v0.17.3>, accessed 2026-05-19). **OpenClaw** beta head
advanced same-day from `v2026.5.18-beta.1` to **`v2026.5.19-beta.1`** (released 2026-05-18 22:58 UTC, hours after the
eighteenth pass's beta snapshot) — adds meme-maker skill (template search + render), Python debugging via pdb + debugpy,
typed tool-plugin framework with `defineToolPlugin` and manifest generation, Android Talk Mode Gateway-relay streaming
voice sessions, vector-search JS-fallback bounded-batch scan (prevents multi-second main-thread blocking on large
tables), Gemini 3 tool-signature compatibility, Claude image-input fix, xAI OAuth PKCE handling, and channel stability
fixes (Telegram / Discord / WhatsApp / Signal); stable still `v2026.5.18` (source:
<https://github.com/openclaw/openclaw/releases/tag/v2026.5.19-beta.1>, accessed 2026-05-19). **A2A** star count
re-pinned to **23,900+ stars** (was 23,800+); v1.0.0 (2026-03-12) unchanged (source:
<https://github.com/a2aproject/A2A>, accessed 2026-05-19). All other high-cadence upstream pins verified unchanged from
the eighteenth pass yesterday: Claude Agent SDK still `v0.2.82` (2026-05-15), Microsoft Agent Framework still
`python-1.4.0` (2026-05-15) / `dotnet-1.6.1` (2026-05-14), LangGraph still `v1.2.0` GA (2026-05-12), CrewAI still
`v1.14.5` stable (2026-05-18) with alpha line still at `1.14.5a7`. Eighteenth pass (2026-05-18) was a light
verification-and-refinement pass per zora docs-research dispatch under P2 cadence-floor breach + polish-tier one-shot
ADVANCE (zero_streak=1 against last_run_sha=`5daa7677`); captured two upstream changes that landed on 2026-05-18 itself.
**OpenClaw** bumped stable from `v2026.5.12` to **`v2026.5.18`** (2026-05-18) and beta head from `v2026.5.14-beta.2` to
**`v2026.5.18-beta.1`** (2026-05-18); headline additions are **Android Talk Mode realtime Gateway-relay voice sessions**
(streaming mic input, realtime audio playback, tool-result bridging, on-screen transcripts), a **Mac app redesign** with
consistent card layouts and cleaner permissions / voice / skills / cron / exec / debug panes, a new **meme-maker skill**
and a **Python debugging skill** (pdb + remote attach), Gateway startup optimizations preserving `/readyz` sidecar
gating, new benchmark tooling for restart-readiness / downtime / trace / resource-slope evidence, and a CLI plugin
system with `defineToolPlugin` plus build/validate commands; star count re-pinned to **373,000+ stars / 77,400+ forks**
(sources: <https://github.com/openclaw/openclaw/releases/tag/v2026.5.18> and <https://github.com/openclaw/openclaw>,
accessed 2026-05-18). **CrewAI's `1.14.5` alpha line graduated to stable today** — `v1.14.5` (2026-05-18) ships the
`CrewAgentExecutor` deprecation (Crew agents now default to `AgentExecutor`) as the headline change, plus a
`restore_from_state_id` kickoff parameter for state-resume workflows, Daytona sandbox tool improvements, a memory-leak
fix in git operations, status-endpoint routing fix, and a CLI extracted into a standalone `crewai-cli` package; alpha
line continues at `1.14.5a7` (2026-05-18) (source: <https://github.com/crewAIInc/crewAI/releases/tag/1.14.5>, accessed
2026-05-18). All other high-cadence upstream pins verified unchanged from the seventeenth pass three days earlier:
Claude Agent SDK still `v0.2.82` (2026-05-15), Microsoft Agent Framework still `python-1.4.0` (2026-05-15) /
`dotnet-1.6.1` (2026-05-14), LangGraph still `v1.2.0` GA (2026-05-12), OpenAI Agents SDK still `v0.17.2` (2026-05-12),
A2A still `v1.0.0` (2026-03-12) / 23,800+ stars (sources:
<https://github.com/anthropics/claude-agent-sdk-python/releases>,
<https://github.com/microsoft/agent-framework/releases>, <https://github.com/langchain-ai/langgraph/releases>,
<https://github.com/openai/openai-agents-python/releases>, <https://github.com/a2aproject/A2A>, all accessed
2026-05-18). Seventeenth pass (2026-05-15) was a light verification-and-refinement pass per zora docs-research
re-dispatch (same P2 cadence-floor breach parameters as the sixteenth pass; zora's last-fire record had not yet absorbed
the sixteenth-pass commit `0f58adfe` when she re-dispatched). High-cadence upstream pins were verified unchanged within
hours: Claude Agent SDK `v0.2.82`, OpenClaw stable `v2026.5.12` / beta `v2026.5.14-beta.2`, Microsoft Agent Framework
`python-1.4.0` / `dotnet-1.6.1`, LangGraph `v1.2.0` GA, OpenAI Agents SDK `v0.17.2`, A2A `v1.0.0` / 23,800+ stars. One
refinement applied that pass: CrewAI's **v1.14.5 alpha line** (`1.14.5a4` on 2026-05-08, `1.14.5a5` on 2026-05-12 — the
sixteenth pass missed this), headline being the deprecation of `CrewAgentExecutor` in favour of defaulting Crew agents
to `AgentExecutor` (source: <https://github.com/crewAIInc/crewAI/releases/tag/1.14.5a5>, accessed 2026-05-15 — alpha
line has since graduated to stable `v1.14.5` on 2026-05-18, captured at the top of this preface). Sixteenth pass
(earlier on 2026-05-15) captured multiple version bumps in the four-day window since the fifteenth pass: **Claude Agent
SDK jumped onto the v0.2 line** with `v0.2.82` (2026-05-15), introducing two breaking changes — MCP servers now connect
in the background by default (sessions start immediately with slow servers reporting `status: "pending"`) and headless /
SDK sessions migrate from `TodoWrite` to a new **Task tools** family (`TaskCreate`, `TaskUpdate`, `TaskGet`,
`TaskList`); also exports a public `EffortLevel` type alias and bumps the `mcp` dependency to `>=1.23.0` for
CVE-2025-66416 (DNS rebinding) (source: <https://github.com/anthropics/claude-agent-sdk-python/releases>, accessed
2026-05-15). **LangGraph v1.2.0 shipped GA on 2026-05-12** — the v1.2 alpha series the fifteenth pass mentioned is now
stable, headlining `durable error-handler resume across host crashes`, `set_node_defaults()` on `StateGraph`, and
delta-channel snapshot handling improvements (source: <https://github.com/langchain-ai/langgraph/releases>, accessed
2026-05-15). **Microsoft Agent Framework** moved to `python-1.4.0` (2026-05-15) and `dotnet-1.6.1` (2026-05-14),
continuing the near-weekly minor cadence on the v1 line (source:
<https://github.com/microsoft/agent-framework/releases>, accessed 2026-05-15). **OpenAI Agents SDK** bumped to `v0.17.2`
(2026-05-12) — bug-fix release covering OpenAI Conversations reasoning persistence, realtime tools, and session
management (source: <https://github.com/openai/openai-agents-python/releases>, accessed 2026-05-15). **OpenClaw** rolled
forward: stable now `v2026.5.12` (2026-05-14), latest pre-release `v2026.5.14-beta.2` (2026-05-15); star count re-pinned
to **372,000+ / 77,100+ forks** (sources: <https://github.com/openclaw/openclaw>,
<https://github.com/openclaw/openclaw/releases>, accessed 2026-05-15). **A2A** re-pinned to **23,800+ stars**
(<https://github.com/a2aproject/A2A>, accessed 2026-05-15); v1.0.0 (2026-03-12) unchanged. Fifteenth pass (2026-05-11)
was a light verification-and-refinement pass per zora ADVANCE dispatch (P2 cadence-floor breach, +4h15m / 3h floor):
bumped OpenClaw latest beta to **v2026.5.10-beta.3** (released 2026-05-11, after the fourteenth pass landed earlier
today) and sharpened the "Agent Sandbox" reference to name the underlying **Kubernetes SIG Apps subproject** with its
three new primitives (`Sandbox`, `SandboxTemplate`, `SandboxClaim`) — Google additionally announced a hosted **GKE Agent
Sandbox** variant at Cloud Next '26 with gVisor kernel-level isolation, ~300 sandboxes/sec throughput, and the explicit
posture that any Kubernetes cluster can run it (not GKE-only) (sources: <https://github.com/openclaw/openclaw/releases>
and <https://www.infoq.com/news/2026/05/gke-agent-sandbox-hypercluster/>, accessed 2026-05-11). Fourteenth pass (earlier
on 2026-05-11) was a focused SDK/protocol-axis refresh per zora dispatch: added new Reference Products entry for
**OpenAI Agents SDK** (MIT, **26,200+** GitHub stars, latest `v0.17.1` per the releases page, provider-agnostic via
LiteLLM / any-llm — supports OpenAI Responses + Chat Completions APIs and 100+ other LLMs; sandbox agents, sessions,
handoffs, MCP, guardrails, tracing all first-class), filling a long-standing gap given OpenAI's April 15 2026 evolution
announcement is now ~four weeks old (sources: <https://github.com/openai/openai-agents-python>,
<https://github.com/openai/openai-agents-python/releases>, <https://openai.github.io/openai-agents-python/>, and
<https://techcrunch.com/2026/04/15/openai-updates-its-agents-sdk-to-help-enterprises-build-safer-more-capable-agents/>,
accessed 2026-05-11). Refreshed Claude Agent SDK entry with Q2 2026 additions (latest `v0.1.80` on 2026-05-09):
`session_store_flush="eager"` (0.1.73), deferred tool use + `updatedToolOutput` in PostToolUse + strict MCP config +
hook event streaming (all 0.1.74), and the new `"xhigh"` effort level for Opus 4.7 (source:
<https://github.com/anthropics/claude-agent-sdk-python/releases>, accessed 2026-05-11). Verified A2A repo at 23,700+
stars / v1.0.0 and LangGraph at v1.1.10 + v1.2 alpha line — both still pinned correctly. Thirteenth pass (earlier on
2026-05-11) was a verification-heavy pass with no material upstream changes: re-checked OpenClaw, Microsoft Agent
Framework, OpenHands, CrewAI, LangGraph, and A2A — every version pin from the twelfth pass was still current (OpenClaw
`v2026.5.7` remains latest stable with only beta releases after; Microsoft Agent Framework still `python-1.3.0` /
`dotnet-1.5.0`; OpenHands still `1.7.0`; CrewAI still `1.14.4`; LangGraph still `1.1.10`; A2A still `v1.0.0`); only
re-pinned drift-prone metrics: OpenClaw to `371,000+` stars / `76,600+` forks (was `368,700+` / `75,900+`) and A2A to
`23,700+` stars (was `23,600+`) (sources: <https://github.com/openclaw/openclaw> and
<https://github.com/a2aproject/A2A>, accessed 2026-05-11). Twelfth pass (2026-05-09) bumped OpenClaw to `v2026.5.7`
(2026-05-07), Microsoft Agent Framework to `python-1.3.0` / `dotnet-1.5.0` (both 2026-05-08), and rewrote stale
`All-Hands-AI/OpenHands` URLs to the post-rename canonical `OpenHands/OpenHands` (the GitHub redirect still resolves but
the org has been renamed; sources verified via og:title on the live release pages, accessed 2026-05-09). Eleventh pass
(2026-04-20) rewrote the OpenClaw entry with fresh research from <https://openclaw.ai/> and
<https://github.com/openclaw/openclaw> pulled during a session-end strategic discussion. Captured owner's "Witwave is
OpenClaw for teams with Kubernetes clusters" positioning frame and listed OpenClaw's 20+ chat-platform integrations,
menu-bar voice-wake companion, workspace skills system with agent-written-skill loop, and MIT + calendar-versioned
release cadence. Marked OpenClaw as "primary open-source competitor" in the section heading; added explicit
differentiator lists in both directions. Earlier tenth pass (2026-04-19) aggressively cut Research Themes from deep
research-synthesis bibliography to one-paragraph navigational scaffolding per theme.)

---

## Positioning

Most autonomous agent tools are designed to be driven by a human sitting at a development machine — a CLI you run
locally, a UI you open in a browser, or an IDE extension you trigger manually. This project takes a different approach:
agents run as containerized services on infrastructure, operating autonomously on their own schedules without a human
present to start each task. The unit of deployment is a container, not a developer session. This makes it suitable for
running on remote servers, CI/CD infrastructure, or cloud-hosted environments where no interactive session exists —
closer in spirit to a daemon or a microservice than a developer tool.

That distinction shapes the comparison below. Each reference product is labeled with its autonomy model:

- **Human-driven** — a human initiates every task; the agent is a tool the human wields
- **Semi-autonomous** — can run unattended for a single task, but requires human setup and handoff per run
- **Autonomous** — runs persistently on a schedule without a human present; self-directed within defined boundaries

Most reference products are human-driven tools that happen to use agents internally. This project targets the autonomous
tier — infrastructure that hosts agents rather than a tool a developer runs.

A second positioning axis the project asserts publicly: **this repository is an experiment in AI-operated open source**.
Every line of code is written by AI, every bug is diagnosed and fixed by AI, every issue is answered by AI, every PR is
opened / reviewed / merged by AI. Humans file issues and make strategic calls — that is the contribution model. This is
distinct from "AI-assisted development" (humans write code with AI help) and from the reference products below (which
help a developer do their job). The project and the platform are the same artifact: the agents this platform deploys are
the agents that maintain its code. No comparable reference project asserts this constraint as a design goal. See
`CONTRIBUTING.md` and `docs/product-vision.md` → "AI-Operated Open Source" for the full statement.

A third positioning axis: **real-time observability with a pinned wire contract**. Because agents run as services rather
than developer sessions, operators need a live window into fleet behaviour — not periodic pulls or webhook fan-out. The
platform exposes a versioned Server-Sent Events stream (`/events/stream`) with 14 typed event shapes (`job.fired`,
`webhook.delivered`, `conversation.turn`, `tool.use`, `trace.span`, …), `Last-Event-ID` resume, and per-session
drill-down streams that carry token-level `conversation.chunk` events. Every client (web dashboard, `ww` CLI, future
mobile) consumes the same schema documented in `docs/events/`. Most reference products either don't ship a live
observability stream or couple it to a proprietary UI; publishing the schema as a first-class multi-client contract is a
differentiator.

**Category context (April 2026).** Three market realities shape the comparisons below:

1. **"Agent Fabric / Agent Mesh / Agent Cloud"** has coalesced as the enterprise-category name in the last 60 days —
   Cloudflare, Salesforce, MuleSoft, ServiceNow, Equinix, Nutanix all use one of these three phrases. This project's
   harness + A2A + multi-backend routing sits squarely in that category architecturally, though the project's
   positioning language is still "autonomous-agent infrastructure."
2. **Kubernetes-native agent infrastructure is no longer empty space.** kagent is in the CNCF sandbox, OpenClaw has a
   dedicated operator, OpenHands v1.6 added Kubernetes deployment + RBAC (March 2026), and the **Agent Sandbox**
   Kubernetes SIG Apps subproject (launched at KubeCon NA 2025) introduces three new primitives — `Sandbox`,
   `SandboxTemplate`, `SandboxClaim` — runnable on any Kubernetes cluster. Google additionally announced a hosted **GKE
   Agent Sandbox** at Cloud Next '26 with gVisor kernel-level isolation and ~300 sandboxes/sec throughput (source:
   <https://www.infoq.com/news/2026/05/gke-agent-sandbox-hypercluster/>, accessed 2026-05-11). What was a wide lane is
   now contested — differentiation moves to specifics (multi-backend routing under one identity, scheduler-primitives
   breadth, etc.).
3. **A2A + MCP + OpenTelemetry are now the assumed baseline tripod.** Every 2026 launch — Microsoft Agent Framework,
   kagent, Bedrock AgentCore Gateway, Cloudflare Agent Cloud — leads with all three. Shipping them is no longer a
   differentiator; _how_ they compose (cross-pod topology, per-named-agent routing, published event schema) is where the
   differentiation now lives.

---

## Reference Products

### OpenHands (formerly OpenDevin)

**Autonomy model:** Human-driven (tasks are initiated by a human via CLI or UI; the agent executes the task autonomously
but does not self-schedule)

OpenHands is an open-source autonomous coding platform with a composable Python SDK (`software-agent-sdk`), CLI, local
GUI, and cloud/enterprise deployment. Current version: **v1.7.0 (May 1, 2026)** — an iterative release adding
KVM-accelerated sandbox containers (`SANDBOX_KVM_ENABLED`), exposing the SDK settings schema to OpenHands, and folding
the Tavily search key into MCP settings, plus dependency CVE patches; the v1.6.0 Kubernetes/RBAC narrative below remains
the substantive recent headline. **75,000+ GitHub stars / 9,500+ forks** (live count 75,028 / 9,510 — twenty-third pass
refresh; the previous 72,500+ pin had drifted significantly) (sources:
<https://github.com/OpenHands/OpenHands/releases/tag/1.7.0> accessed 2026-05-09 and
<https://github.com/OpenHands/OpenHands> accessed 2026-05-27; the project's GitHub org was renamed from `All-Hands-AI`
to `OpenHands` — old `All-Hands-AI/OpenHands` URLs still redirect, but the canonical path is now `OpenHands/OpenHands`).
Scores 77.6+ on SWEBench Verified; community benchmarks report 87% of bug tickets resolved same-day. Key
differentiators: multi-LLM support (Claude, GPT, any open-source model), deep integrations with Slack, Jira, Linear,
GitHub, GitLab, Azure DevOps, Bitbucket, and MCP servers.

**v1.5.0 headline feature — Planning Agent (BETA):** Implements a two-phase Plan/Code workflow. In Plan Mode, the agent
has read-only tool access except for a single writable file (`PLAN.md` in the workspace root) — deliberately preventing
premature code changes. The agent produces a structured plan with implementation steps, API signatures, and testing
strategy; for vague prompts it asks clarifying questions. Users then switch to Code Mode in the same conversation to
execute against the plan. Model preferences are configurable per mode (e.g., a stronger reasoning model for planning, a
faster model for coding). A **Task List Panel** provides real-time progress tracking for long-running sessions. A
**slash command menu** (type `/`) surfaces loaded agent skills for rapid selection.

**v1.6.0 — Kubernetes and hook support (March 30, 2026):** Kubernetes deployment with multi-user support and RBAC —
OpenHands can now be deployed as a production Kubernetes workload with access control. Hook support was added to the
platform, giving operators programmatic intercept points over agent execution. The `/clear` command allows starting a
fresh chat while preserving sandbox state. `/new` was added as a slash command. Global skills can be toggled on/off
per-workspace. Code block copy buttons added to the GUI.

**Agent coordination:** Sub-agent delegation is supported via a blocking parallel execution model — a parent agent
spawns sub-agents as independent conversations that inherit workspace context and model config. GUI-level sub-agent
visibility is tracked in GitHub issue #13030 (CLI/API only as of April 2026). **Microagents** — modular knowledge
snippets triggered by keywords in messages — enable repository-aware context injection via `AGENTS.md` files. $18.8M
Series A raised November 2025.

**Relative standing:** OpenHands has more enterprise integrations, multi-LLM flexibility, a planning/task-tracking
layer, and sub-agent coordination than this project. The Planning Agent's two-phase pattern (plan before code) is the
clearest recently-shipped capability this project lacks at the harness level. The Claude Agent SDK's `plan` permission
mode (read-only + plan file) provides the native primitive to implement the same pattern.

### Claude Code / Claude Agent SDK

**Autonomy model:** Human-driven (Claude Code is an interactive CLI; the Agent SDK is a library for building agents, not
an autonomous runtime by itself — this project is the autonomous harness built on top of it)

The Claude Agent SDK (renamed from Claude Code SDK, late 2025) is the runtime this project builds on. **The Claude Agent
SDK for Python was formally released on 2026-04-18** (bundles Claude Code CLI; requires Python 3.10+) — the SDK is now a
first-party supported product line, not a thin wrapper. Claude Code shipped 30+ releases during a five-week sprint in
April 2026. Recent notables:

- **Ultraplan early preview (Apr 6–10, 2026):** Cloud-drafted plans with a web editor; plans can run locally or
  remotely. Pushes Claude Code further toward cloud-hosted agent execution.
- **`ant` CLI:** A new standalone command-line client for the Claude API with native Claude Code integration and
  YAML-versioned API resources — Anthropic's bid on the "agent infrastructure race" positioning.
- **Focus view, stronger permissions + sandbox handling, richer status line, better resume/transcript reliability,
  improved Bash + MCP stability.** Iteration-level polish across every edge of the CLI.

Key SDK capabilities not yet wired into this project:

**Hooks system — Python callback API via `HookMatcher`:** The SDK overview page confirms hooks are registered as Python
callback functions in `ClaudeAgentOptions`, not file-based config. Example from SDK docs:
`hooks={"PostToolUse": [HookMatcher(matcher="Edit|Write", hooks=[log_file_change])]}`. The `matcher` is a regex on tool
names; `hooks` is a list of async callback functions. Available events include `PreToolUse`, `PostToolUse`, `Stop`,
`SessionStart`, `SessionEnd`, `UserPromptSubmit`, and more. Entirely unused by this project. Key underused capabilities
within the hooks system:

- **`updatedInput` in `PreToolUse`**: rewrite tool arguments before execution — not just block or allow, but actively
  transform (e.g., sandbox path redirection, argument normalization, stripping dangerous flags). This enables ACI-style
  constraints at the harness layer without prompting.
- **`async: true` option**: fire-and-forget hooks using `asyncTimeout` — log writes and webhook POSTs don't block the
  agent loop.
- **`systemMessage` output**: any hook can inject model-visible guidance when an action is blocked or modified.

**Budget and turn control:** `task_budget` (v0.1.51) caps token budget per session. `maxTurns` is available as an
`AgentDefinition` field for subagent turn limits. Both are unset in this project — a stuck or looping agent can exhaust
quota with no bound. `get_context_usage()` (0.1.52) exposes real-time token consumption by category, enabling proactive
warnings before context exhaustion causes silent failure.

**In-process custom tools:** The `@tool()` decorator and `create_sdk_mcp_server()` factory allow defining custom tools
as plain Python functions inside the harness process — no subprocess, no IPC overhead, no separate MCP server to manage.
Tools are passed via `mcp_servers={"name": sdk_server}` in `ClaudeAgentOptions`. Entirely unused by this project.
Enables lightweight harness-native tools (e.g., a structured status reporter, a bus-aware escalation tool) without the
operational weight of an external MCP server.

**Session management (0.1.49–0.1.51):** `fork_session()`, `delete_session()`, `tag_session()`, `rename_session()` — not
exposed by this harness. `RateLimitEvent`, `TaskStarted`, `TaskProgress`, `TaskNotification` typed messages also
available.

**Programmatic subagent definitions (0.1.49–0.1.51):** `AgentDefinition` accepts `description`, `prompt`, `tools`,
`disallowedTools`, `maxTurns`, `initialPrompt`, `skills`, `memory`, and `mcpServers`. Passed via
`agents={"name": AgentDefinition(...)}` in `ClaudeAgentOptions`. Enables the harness to define specialized subagents
programmatically without file-based configuration. Entirely unused by this project.

**Advanced execution options:** `enable_file_checkpointing` enables file-change tracking for session rewinding. `effort`
sets thinking depth (`"low"`, `"medium"`, `"high"`, `"max"`, plus `"xhigh"` for Opus 4.7 since v0.1.74 — falls back to
`"high"` on other models). `plugins` accepts a list of `SdkPluginConfig` objects for custom plugins loaded from local
paths. All unused by this project.

**Q2 2026 SDK additions (0.1.73–0.2.93, latest `v0.2.93` on 2026-06-06):** Capabilities shipped since the previous pass
of this doc, all unused by this project today (source: <https://github.com/anthropics/claude-agent-sdk-python/releases>,
accessed 2026-06-06). The SDK crossed onto the **v0.2 line on 2026-05-15** with two breaking changes worth flagging
up-front: (a) **MCP servers connect in the background by default** — sessions start immediately and slow servers report
`status: "pending"` until ready, eliminating the cold-start tax this project's harness currently absorbs; (b) headless
and SDK sessions **migrate from `TodoWrite` to a new Task tools family** (`TaskCreate`, `TaskUpdate`, `TaskGet`,
`TaskList`), a structural rework of in-session task tracking. v0.2.82 also exports a public `EffortLevel` type alias
(`"low"`, `"medium"`, `"high"`, `"max"`, `"xhigh"`) and bumps the `mcp` dep to `>=1.23.0` to pick up CVE-2025-66416 (DNS
rebinding protection). v0.2.83–v0.2.93 (2026-05-21 → 2026-06-06) are pure maintenance bumps — bundled Claude CLI
advanced to **2.1.167**, the SDK's CI auth migrated to **Workload Identity Federation** (short-lived tokens replacing
long-lived API-key secrets), v0.2.88 (2026-06-02) fixed a Trio compatibility bug in session stores, v0.2.91 (2026-06-05)
switched the SDK's own test suite from `pytest-asyncio` to `anyio`'s pytest plugin (test-tooling refactor only; no
public surface change), and v0.2.92/v0.2.93 (both 2026-06-06) advanced the bundled CLI from 2.1.165 → 2.1.166 → 2.1.167;
no SDK-surface changes across the range (source: <https://github.com/anthropics/claude-agent-sdk-python/releases>,
accessed 2026-06-06). Earlier `v0.1.x` capabilities still relevant to this project:

- **`session_store_flush="eager"` (0.1.73)** — opt-in eager session-store flushing in `ClaudeAgentOptions` enables
  live-tailing UIs, cross-process resume, and crash-durability past where batched flushing leaves off. Direct primitive
  for Gap Analysis → Durability beyond the existing stale-checkpoint detection (F-005).
- **Deferred tool use (0.1.74)** — new `"defer"` value for `PreToolUseHookSpecificOutput.permissionDecision` plus a
  `DeferredToolUse` dataclass on `ResultMessage.deferred_tool_use`. Lets the harness park a tool call for later
  resumption rather than allowing or blocking inline — a closer fit for HITL approval queues than the binary allow/deny
  shape.
- **`updatedToolOutput` in PostToolUse (0.1.74)** — symmetric counterpart to `updatedInput` on PreToolUse — rewrite tool
  output after execution (redaction, summarization, truncation) without changing the underlying tool.
- **Strict MCP config (0.1.74)** — `strict_mcp_config=True` ignores project / user / global MCP configurations and
  enforces only the harness-supplied set. Directly applicable to this project's MCP allow-list posture
  (`MCP_ALLOWED_COMMANDS` / `MCP_ALLOWED_CWD_PREFIXES`).
- **Hook event streaming (0.1.74)** — `include_hook_events=True` yields hook execution events (PreToolUse, PostToolUse,
  Stop, …) as `HookEventMessage` records, enabling external observability of hook behaviour without inline coupling.
  Natural fit for the published `/events/stream` schema.
- **`include_hook_events` + `xhigh` effort + Opus 4.7 wiring (0.1.77–0.1.80, May 6–9 2026)** — late-May polish: `xhigh`
  falls back to `high` on models that don't support it; `atexit` handler ensures live CLI subprocesses are terminated on
  harness exit (prevents orphaned `claude` processes during pod restart).
- **`v0.1.81` (2026-05-11)** — interim release on the v0.1 line bridging to v0.2; superseded by v0.2.82 four days later
  (source: <https://github.com/anthropics/claude-agent-sdk-python/releases>, accessed 2026-05-15).

**Permission modes:** Five modes — `default`, `dontAsk`, `acceptEdits`, `bypassPermissions`, `plan` — set via
`permission_mode` in `ClaudeAgentOptions`. The `plan` mode (read-only execution + single writable plan file) is
confirmed in current SDK docs and mirrors OpenHands's Planning Agent pattern exactly. **`AskUserQuestion`** is available
for HITL (main agents only — unavailable to subagents per SDK bug #12890; this project does not use subagents, so not
blocking).

**Relative standing:** This project uses a growing but still narrow slice of the SDK — `ClaudeSDKClient` with
`get_context_usage()`, session resume, MCP config, per-agent model selection, and 70+ Prometheus metrics wrapping the
execution path. The hooks system (Python callback API via `HookMatcher`), `task_budget` for cost control, in-process
custom tools, `permission_mode="plan"` for structured task execution, and `AgentDefinition` for programmatic subagents
are the most actionable gaps. Each is a targeted addition to `executor.py`'s `make_options()` with no structural changes
to the project.

### OpenAI Agents SDK (OpenAI)

**Autonomy model:** Human-driven to semi-autonomous (SDK for building agents — not an autonomous runtime by itself; the
April 2026 sandbox-agent + harness primitives push the SDK toward semi-autonomous when an integrator uses the bundled
harness rather than rolling their own)

The OpenAI Agents SDK is OpenAI's first-party agent-building framework — structurally the closest peer to the Claude
Agent SDK in the vendor-SDK tier. **MIT-licensed, 26,200+ GitHub stars, latest `v0.17.4`** per the releases page
(sources: <https://github.com/openai/openai-agents-python> and
<https://github.com/openai/openai-agents-python/releases>, accessed 2026-05-26). Self-described as "a lightweight yet
powerful framework for building multi-agent workflows" and notably **provider-agnostic** — supports the OpenAI Responses
and Chat Completions APIs plus 100+ other LLMs via the LiteLLM and any-llm adapter ecosystems (source:
<https://github.com/openai/openai-agents-python>, accessed 2026-05-11). Python (`openai-agents-python`) is the primary
SDK; TypeScript support is also available with sandbox-agent and harness features rolling out, with code-mode and
subagents called out as in-flight for both languages.

**April 15 2026 evolution announcement — sandbox agents and harness architecture:** OpenAI announced an expanded SDK
framed as "help[ing] enterprises build safer, more capable agents," addressing enterprise safety and complexity in
long-horizon tasks (source:
<https://techcrunch.com/2026/04/15/openai-updates-its-agents-sdk-to-help-enterprises-build-safer-more-capable-agents/>,
accessed 2026-05-11). Key primitives surfaced in the docs (source: <https://openai.github.io/openai-agents-python/>,
accessed 2026-05-11):

- **Sandbox agents** — specialists run inside real isolated workspaces with manifest-defined files and **resumable
  sandbox sessions**.
- **Sessions** — persistent memory layer maintaining working context within an agent loop.
- **Agents as tools / Handoffs** — built-in delegation primitive for coordinating multiple agents.
- **MCP server tool calling** — first-class MCP integration, treated symmetrically with function tools.
- **Guardrails** — parallel input validation and safety checks that fail fast when checks don't pass.
- **Built-in tracing** — visualization, debugging, and monitoring with native hooks into the OpenAI tracing suite.

Recent point releases (`v0.16.1` / `v0.17.0` / `v0.17.1` / `v0.17.2` / `v0.17.3` / `v0.17.4`, early-to-late May 2026)
have hardened the sandbox model — constraining local sandbox artifact sources to base dir for better isolation,
stabilizing the realtime-session tool-approval flow, defaulting realtime sessions to `gpt-realtime-2`, and tightening
MCP approval-policy validation; `v0.17.2` (2026-05-12) was a bug-fix release covering OpenAI Conversations reasoning
persistence (#3268), realtime tool behaviour, and session management; `v0.17.3` (2026-05-19) extended the sandbox
hardening with the explicit "keep mountpoint credentials out of sandbox commands" fix, normalized leading question marks
in port queries, rejected relative workspace roots, skipped status checks when Vercel sandbox reaches terminal state,
added output-guardrail counts in error details, fixed FunctionTool / Codex output-schema mutations, and documented
`Agent.instructions` as optional — no breaking changes (source:
<https://github.com/openai/openai-agents-python/releases/tag/v0.17.3>, accessed 2026-05-19); `v0.17.4` (2026-05-26) adds
support for Realtime custom voice objects, optional recovery for missing function tools (#3459), hardened HTTP client
defaults for MCP SSE transport, FunctionSpanData output-value handling correction, redacted invalid JSON payloads in
`ModelBehaviorError`, additional missing span-slot entries, expanded tracing function/type exports, and new exports
`MCPListToolsItem` / `ToolSearchCallItem` / `ToolSearchOutputItem` — no breaking changes (source:
<https://github.com/openai/openai-agents-python/releases/tag/v0.17.4>, accessed 2026-05-26).

**Relative standing:** OpenAI Agents SDK is now the third major vendor SDK in this project's reference set — alongside
the Claude Agent SDK and Microsoft Agent Framework — and the most directly comparable to Claude Agent SDK in shape
(Python library + sandbox + sessions + MCP + tracing + guardrails). Its **provider-agnostic posture** is structurally
significant: agents built on OpenAI's SDK can already target Claude / Gemini / 100+ models via LiteLLM (source:
<https://github.com/openai/openai-agents-python>, accessed 2026-05-11), eroding the historical "pick your vendor's SDK,
get locked to their models" framing that pushed teams to either Anthropic or OpenAI camps. The sandbox-agent + harness
pattern overlaps with this project's harness-as-pod model but at a different layer — Witwave runs the harness as a
Kubernetes pod with multi-backend routing inside it (claude / openai / gemini per concern), while OpenAI's sandbox is
per-task workspace isolation inside the SDK process. Net: a category peer whose existence reinforces that "vendor SDK +
sandbox + sessions + MCP + guardrails + tracing" is now the standard shape; Witwave's defensible differentiation narrows
further to **multi-backend routing under one named-agent identity**, **cluster-resident A2A coordination across named
agents**, and **the published `/events/stream` wire contract consumed by multiple independent clients**.

### Devin (Cognition)

**Autonomy model:** Semi-autonomous (a human assigns a task via Slack or web UI; Devin executes it end-to-end
unattended, then surfaces a PR for review — each task is human-initiated, not self-scheduled)

Devin was rebuilt on Claude Sonnet 4.5 in September 2025. MCP support was added, giving access to hundreds of external
tools via a standardized interface. Natively reads tickets from Linear, Jira, Slack, and GitHub; writes the
implementation, runs tests, and opens a PR. The workflow pattern in practice is an **"assign-and-review" loop**: teams
assign backlog items, Devin drafts PRs, engineers review output rather than individual steps and run multiple instances
in parallel. The embedded observable IDE (shell + editor + browser) allows engineers to watch or take over at any point.
Deployed by Goldman Sachs alongside 12,000 human engineers.

**Devin 2.2 (February 24, 2026) — self-verification and computer use:** Devin now implements a complete autonomous
development cycle: plan → code → review → auto-fix → PR — all before a human opens the PR. Computer use testing gives
Devin access to its own Linux desktop to launch and test desktop applications, with screen recordings for review.
Startup time was reduced 3x. The self-verification loop is the most complete closed-loop autonomous development cycle
shipped by any agent product.

**Schedule Devins (March 2026) — self-scheduling and parallel delegation:** Devin can now set up its own recurring
schedules from natural language descriptions, carrying state between runs via persistent notes. A coordinator Devin
delegates to managed Devins — each a full isolated VM — that work in parallel. Architecturally close to this project's
scheduled-prompt + A2A delegation model, except Devin infers the schedule from natural language rather than requiring
explicit cron expressions.

**Devin-in-Windsurf (2026-04-15):** Cognition integrated cloud Devin with Windsurf local dev, letting developers hand
off tasks between a local IDE session and a remote Devin instance on the same repo. Plus progressive web app
installation (desktop + mobile), browser-tab favicon session-status dots, a **PR Digest** (read-only view of
Devin-session PRs for users who haven't yet connected GitHub), **GitHub Enterprise Server support** in the Review flow,
repository-level Review permission enforcement, and IDP (Okta) groups management UI in Enterprise settings. The
IDE-adjacent integration + enterprise identity / review-governance posture is Cognition's 2026-Q2 theme.

**Relative standing:** Devin is a vertical product; this project is infrastructure. Transferable lessons: show the plan
before acting, structure work for parallel execution, make agent actions observable mid-run, and carry state across
scheduled runs. The self-verification loop (plan → code → review → fix) and self-scheduling are the strongest new
patterns. This project's scheduled-prompt system already provides scheduled execution with session continuity; Devin's
"Schedule Devins" validates the model while highlighting the value of event-driven triggers (F-013) and planning mode
(F-012) as complements to cron.

### Hermes Agent (NousResearch)

**Autonomy model:** Autonomous (runs persistently on user-controlled infrastructure; connects to messaging platforms and
operates proactively — the closest architectural peer to this project in the new 2026 open-source landscape)

Hermes Agent (MIT, NousResearch, released February 2026, **v0.10.0 on 2026-04-16**) is built around the thesis that an
agent should learn from completed work and get measurably better the longer it runs. Ships weekly. Key capabilities:
persistent memory via prompt-injected files + SQLite FTS5 with LLM-powered summarization; **auto-generated skills** —
after completing a complex task the agent writes a new skill document for future reuse (FTS5 now indexes 118+ bundled +
generated skills, with top matches prepended to context); six terminal backends (local, Docker, SSH, Daytona,
Singularity, Modal); 40+ built-in tools; **multi-platform messaging gateway — 16 supported platforms** (Telegram,
Discord, Slack, WhatsApp, Signal, iMessage via BlueBubbles, WeChat/WeCom, Android/Termux native, CLI, …).

**v0.9.0 (2026-04-13, "The Everywhere Release"):** Android/Termux native, iMessage via BlueBubbles, WeChat/WeCom
callback mode, Fast Mode (`/fast`), local web dashboard, background-process monitoring, native xAI (Grok) + Xiaomi MiMo
providers, pluggable context engine.

**v0.10.0 (2026-04-16, "The Tool Gateway Release"):** Nous Portal subscribers get web search, image gen, TTS, and
browser automation (Firecrawl, FAL/FLUX 2 Pro, OpenAI TTS, Browser Use) bundled without separate API keys — a
subscription-bundled tool gateway that is a new monetization vector for the category.

**Relative standing:** Hermes Agent is the most direct architectural peer in the open-source world on the consumer /
personal-assistant axis. Its layered memory stack (FTS5 + LLM summarization + pluggable providers) and
auto-skill-generation are materially ahead of this project's flat markdown files and static skill documents. Its
messaging-first gateway is out of scope for this project's A2A/HTTP model. The auto-skill-generation pattern remains the
most transferable idea.

### CrewAI

**Autonomy model:** Human-driven (a crew is instantiated and kicked off by Python code a human runs; event-driven Flows
add reactivity but crews do not self-schedule — they are called)

Multi-agent orchestration framework. **Current stable: v1.14.6 (2026-05-28); alpha train advanced to `1.14.7a2`
(2026-06-05)** from the `1.14.7a1` (2026-06-03 17:41 UTC) opener — `1.14.7a1` added **`crew trained agents file`
support** and a **native Snowflake Cortex LLM provider** plus bug-fix and performance work across file handling, tool
integration, and import optimization; `1.14.7a2` (2026-06-05) layers **conversational flow traces support** plus
**enhanced LLM event handling with real finish reasons and sampling parameters** on top (source:
<https://github.com/crewAIInc/crewAI/releases>, accessed 2026-06-06). Stable v1.14.6 graduates the 1.14.6 alpha line to
stable; carries forward the hardened **`StdioTransport`** preventing environment-variable leakage and the
**structured-output leak fix in tool-calling loops** from `1.14.6a2`, adds **checkpoint restoration** (AgentExecutor
support plus orphan-task handling), and moves the **Skills Repository system** (added in `1.14.6a1`) **behind an
experimental gate** at GA; documentation for the **Agent Control Plane** carries forward from the alpha (source:
<https://github.com/crewAIInc/crewAI/releases/tag/1.14.6>, accessed 2026-05-28). The **previous stable `v1.14.5`**
(2026-05-18) graduated the May v1.14.5 alpha line — headline change was **deprecating `CrewAgentExecutor` in favour of
defaulting Crew agents to `AgentExecutor`**; also added a `restore_from_state_id` kickoff parameter for resuming
workflows from a prior state, improved Daytona sandbox tools, a memory-leak fix in git operations, a status-endpoint
routing fix, and **a CLI extracted into a standalone `crewai-cli` package** (source:
<https://github.com/crewAIInc/crewAI/releases/tag/1.14.5>, accessed 2026-05-18). Alpha-line history feeding the 1.14.6
stable: `1.14.6a1` (2026-05-21) added the **Skills Repository system** with registry, caching, CLI tools, and SDK
integration (the first OSS-side direct entrant on the self-improvement axis — moved behind an experimental gate at GA),
plus RuntimeState serialization-robustness improvements and an `idna` 3.15 CVE bump for GHSA-65pc-fj4g-8rjx; `1.14.6a2`
(2026-05-27 23:49 UTC) then hardened **`StdioTransport`** against environment-variable leakage, fixed
**structured-output leaks in tool-calling loops**, added an `env_vars` declaration on `DatabricksQueryTool`, improved
planning configuration and observation handling, and introduced Agent Control Plane documentation; both alpha trains
graduated into `1.14.6` stable on 2026-05-28 (sources: <https://github.com/crewAIInc/crewAI/releases/tag/1.14.6a1>
accessed 2026-05-25 and <https://github.com/crewAIInc/crewAI/releases/tag/1.14.6a2> accessed 2026-05-28). The previous
stable line `v1.14.4` (2026-04-30) introduced Responses API support for the Azure OpenAI provider, You.com MCP tools
(search / research / content extraction), Tavily Research integration, custom persistence keys for `@persist`, and a
`litellm` bump for an SSTI fix (source: <https://github.com/crewAIInc/crewAI/releases/tag/1.14.4>, accessed 2026-05-06);
the v1.14.0 / v1.14.2 substance detailed below remains intact under v1.14.5. Headline 2025–2026 capabilities: **unified
Memory class** (LLM-inferred hierarchical scopes, composite recall scoring, non-blocking background saves,
`crewai memory` terminal browser), **Tool search** (dynamic tool injection — loads only tools relevant to the current
task rather than the full allow-list), Qdrant Edge for on-device vector storage, Enterprise Control Plane with real-time
tracing.

**v1.14.0 (2026-04-07) — checkpoint/resume primitives:** First-class `CheckpointConfig` auto-checkpointing,
`checkpoint list` / `checkpoint info` CLI, `SqliteProvider` checkpoint store, runtime-state checkpointing with
event-system refactor, `guardrail_type` + name labels on traces. SSRF and path-traversal protections added to RAG tools.
Excluded embedding vectors from memory serialization (token savings). Bumped `litellm ≥1.83.0` to pick up a CVE patch
(CVE-2026-35030).

**v1.14.2 (2026-04-17):** Fix for `flow_finished` event after HITL resume; `cryptography` bump to 46.0.7 for
CVE-2026-39892. The two CVE patches in a single minor cycle signal CrewAI maturing its enterprise-security posture.

**Relative standing:** CrewAI's unified structured memory with composite recall remains the clearest memory gap relative
to this project. Its new tool search (dynamic, task-aware tool injection — loading only tools relevant to the current
prompt) is the state of the art here and a real gap; this project has static `ALLOWED_TOOLS` per agent.
Checkpoint/resume primitives at v1.14.0 advance the durability story. This project uses A2A for coordination
(distributed, standard, network-based); CrewAI uses in-process Python calls (tighter coupling, lower latency).

### LangGraph / LangGraph Platform

**Autonomy model:** Human-driven to semi-autonomous (graphs are triggered by external events or human calls; **LangGraph
Platform** adds persistent deployment + event-driven triggers, pushing toward semi-autonomous)

**Current: LangGraph v1.2.4 (2026-06-02) + LangGraph Platform GA (late 2025).** v1.2.4 follows v1.2.3 (2026-06-01),
v1.2.2 (2026-05-26), v1.2.1 (2026-05-21), and v1.2.0 GA (2026-05-12, which promoted the v1.2 alpha series 1.2.0a1–a7 to
stable). Headline additions from the v1.2 line: **`durable error-handler resume across host crashes`** (lifts the
existing checkpoint primitive into a fully crash-recovering error-handler), `set_node_defaults()` on `StateGraph` for
shared node configuration, and improved delta-channel snapshot handling (force-snapshot after max supersteps since the
last snapshot). v1.2.1 added an optional **`before_builtins` hook in stream transformers** (customising processing-order
injection) and excluded tool results from v3 messages; companion `langgraph-sdk==0.3.15` and
`langgraph-checkpoint==4.1.1` both shipped 2026-05-22. v1.2.2 is a single-fix point release assigning **stable IDs to
`id=None` `BaseMessage` instances before DeltaChannel checkpoint writes** — prevents downstream identity drift on
resumed runs; no breaking changes (source: <https://github.com/langchain-ai/langgraph/releases/tag/1.2.2>, accessed
2026-05-27). Companion package **`langgraph-sdk==0.4.0`** shipped 2026-05-28 — a substantive SDK minor bump (0.3.x →
0.4.0) adding **v3 streaming primitives with SSE transport**, **websocket stream transports plus selection wiring**,
hardened streaming reconnects and async-reconnect support, async/sync thread stream helpers and scoped subgraph handles,
and shared stream subscriptions plus messages-and-tool-call projections; companion `langgraph-cli==0.4.27` shipped the
same day pinning internal Docker deploy images by digest and bumping the API bound to 0.10.0 (sources:
<https://github.com/langchain-ai/langgraph/releases/tag/sdk%3D%3D0.4.0> and
<https://github.com/langchain-ai/langgraph/releases/tag/cli%3D%3D0.4.27>, accessed 2026-05-29). Subsequent day-of bumps
(2026-06-01) advanced core to **`v1.2.3`** (2026-06-01 18:56 UTC) — wires `RemoteGraph.interleave` through to the
`sdk-py` `interleave_projections` plumbing plus point-fixes for event-ID field naming and cancellation-distinction
handling; SDK companion rolled twice the same day to **`langgraph-sdk==0.4.1`** (2026-06-01 15:23 UTC, percent-encoding
improvements) and **`langgraph-sdk==0.4.2`** (2026-06-01 17:51 UTC, stream-decoder extraction). The next day shipped
core **`v1.2.4`** (2026-06-02 17:07 UTC) — backward-compatible `_on_started` refactor plus factory-graph integration
testing improvements; no breaking changes (source: <https://github.com/langchain-ai/langgraph/releases>, accessed
2026-06-04). v1.1.10 (2026-04-27) — the previous stable — was a maintenance release (prebuilt 1.0.12, checkpoint 4.0.3,
dep bumps, a reverted node-level-timeouts experiment, and a `ToolNode` change to allow tools returning
`list[Command | ToolMessage]`) (source: <https://github.com/langchain-ai/langgraph/releases>, accessed 2026-05-15). An
earlier pass of this doc mislabeled deferred nodes and node-level caching as "v2.0" features — they are **v1.x**
features shipped during the 2025 LangGraph Release Week. There is no v2.0 on PyPI as of May 2026; the stable line is
v1.x.

**Key v1.x capabilities (accumulated through v1.1.0):**

- **HITL via `interrupt()`** with structured payloads + resume via `Command(resume=value)`.
- **Checkpointing mandatory** at graph initialization, with PostgreSQL checkpointer pooling for multi-tenant
  deployments.
- **Guardrail nodes as first-class primitives** (content filtering, per-user/per-thread/global rate limiting, audit
  logging with field redaction).
- **MCPToolkit** for standardized MCP integration.
- **Native A2A integration** — cross-framework agent-to-agent over message brokers, confirming A2A as the emerging
  coordination protocol.
- **Deferred nodes** (v1.x) — delay node execution until all upstream paths complete; canonical map-reduce / consensus /
  multi-agent fan-out-fan-in implementation.
- **Node-level caching** (v1.x) — cache individual node results to skip redundant computation during iterative
  development and replay.
- **Type-safe `invoke()` / `stream()` via `version="v2"`** with Pydantic / dataclass coercion of state values.
- **Deploy CLI** (`langgraph deploy`) pushes a graph to LangGraph Platform in one step.

**LangGraph Platform (GA, late 2025):** Purpose-built runtime for long-running, stateful agents. Durable state
persistence, resume-from-interruption, built-in HITL, streaming. **~400 companies running it in production** as of the
March 2026 LangChain newsletter. The Platform — not the library alone — is the right reference for a production-grade
comparable to this project's harness + scheduler surface.

**Relative standing:** LangGraph Platform is now a peer production runtime; its checkpointing model validates F-005
(implemented). Declarative guardrail nodes and A2A integration reinforce F-009 direction. HITL `interrupt()` redesign
reinforces the value of F-001. This project's differentiator vs. LangGraph Platform is **multi-backend routing under one
named-agent identity** (LangGraph Platform is single-framework — agents are LangGraph-authored), plus the full
scheduler-primitive surface (jobs / tasks / triggers / heartbeats / continuations / webhooks) vs. LangGraph's
graph-execution model.

### A2A Protocol (Ecosystem)

**Autonomy model:** Protocol-level (A2A defines how agents communicate regardless of autonomy model; this project uses
it as the coordination layer between autonomous agents)

**A2A v1.0.1 is now the stable version (tagged 2026-05-28, succeeding v1.0.0 of 2026-03-12).** v1.0.1 is a small bug-fix
release: HTTP binding now prefers `application/a2a+json`, recent transcoding-related error changes are incorporated, and
the TaskStatus values specification has been corrected; no surface breaking changes versus v1.0.0. Governance has been
donated to the **Linux Foundation** as an official project; one-year anniversary milestone (2026-04-09) reports 150+
participating organizations. Star count at the `a2aproject/A2A` repo continues to grow — **24,000+ stars / 2,400+ forks
as of 2026-05-29** (live count 24.1k / 2.4k still inside the prior pin) (sources:
<https://github.com/a2aproject/A2A/releases/tag/v1.0.1> and <https://github.com/a2aproject/A2A>, accessed 2026-05-29).
Production deployments include Azure AI Foundry and Amazon Bedrock AgentCore (both of which embed A2A as their native
cross-agent protocol). v1.0 added **Signed Agent Cards** — cryptographic signatures on Agent Cards to prevent forgery
and card-redirect attacks, closing a real multi-tenant security gap; v1.0.1 carries this forward unchanged.

The broader protocol ecosystem continues to be four layers: **MCP** (agent-to-tool), **A2A v1.0** (agent-to-agent),
**ACP** (lightweight async messaging), and **UCP** (agentic commerce — co-developed with Shopify, Visa, Mastercard).
Native A2A support is now present in LangGraph v1.x, Microsoft Foundry Agent Service, kagent, and Amazon Bedrock
AgentCore. The W3C AI Agent Protocol Community Group is working toward official web standards (expected 2026–2027).

**Relative standing:** This project already implements A2A as a first-class citizen — harness routes any inbound message
to backend agents, named agents are reachable from peer named agents over A2A, and the hybrid
orchestrator-plus-local-mesh topology identified in 2026 matches this project's heartbeat + delegation design. v1.0's
Signed Agent Cards is the next conformance milestone — verifying signatures on inbound agent cards before accepting
requests is a straightforward gap to close.

### OpenClaw (Peter Steinberger / community) — primary open-source competitor

**Autonomy model:** Autonomous (self-hosted personal agent, runs 24/7, messaging-driven; the closest philosophical peer
to this project in the open-source world)

**The one-line positioning frame:** Witwave is OpenClaw for teams with Kubernetes clusters — same autonomy model, same
messaging-first interaction surface, same multi-backend LLM routing, but deployed as a cluster-resident multi-agent
platform rather than a personal local daemon. OpenClaw targets the individual running a 24/7 assistant on a Mac Mini;
this project targets the team running coordinated agents as cluster workloads.

OpenClaw originated as "Clawdbot" in November 2025, was renamed "Moltbot" on 2026-01-27 under Anthropic trademark
pressure, and three days later settled on **OpenClaw**. Category-leading install base — **377,000+ GitHub stars and
78,900+ forks as of 2026-06-07** (source: <https://github.com/openclaw/openclaw>, accessed 2026-06-07), with a very
active commit cadence (latest stable release **`v2026.6.1`** on 2026-06-03 19:35 UTC, graduating the
`v2026.6.1-beta.1`/`-beta.2`/`-beta.3` train to GA — Skill Workshop governance, externalized Copilot/Tokenjuice plugins
on ClawHub, Workboard primitives, Code mode MCP files, and SQLite-backed plugin storage all carry forward unchanged from
the betas; beta head subsequently rolled `v2026.6.2-beta.1` (2026-06-03 23:46 UTC) → `v2026.6.5-beta.1` (2026-06-06) →
**`v2026.6.5-beta.2`** (2026-06-07 00:26 UTC), the latter layering a **bundled parallel web-search provider**, **Google
Vertex ADC support** (Application Default Credentials restored), **Matrix voice-message preflight with thread-aware
read/reply**, and **auth-profile durability via SQLite persistence** on top of beta.1's QQBot reasoning-stripping,
non-text MCP tool-result coercion, and Anthropic session recovery after cache expiry; stable still `v2026.6.1` (source:
<https://github.com/openclaw/openclaw/releases>, accessed 2026-06-07). Prior stable **`v2026.5.27`** shipped 2026-05-28
11:41 UTC — graduating the `v2026.5.27-beta.1` (2026-05-28 05:54 UTC) to GA; substantive surface **carries forward
unchanged from beta.1** (captured in the twenty-sixth pass preface above): hardened security boundaries, Codex
reliability work, provider expansion (OpenAI-compatible embeddings, Pixverse video, DeepInfra catalog improvements),
durable channel delivery, restart-surviving native hook relays, plugin display metadata, and Gateway/reply hot-path
performance. The prior stable **`v2026.5.26`** (2026-05-27 11:27 UTC) graduated the `2026.5.26-beta.1` (2026-05-26 21:10
UTC) → `2026.5.26-beta.2` (2026-05-27 05:46 UTC) beta rollup to GA; its substantive surface headlines a **Transcripts
plugin**, **named-model login profiles**, **Signal / iMessage / WhatsApp tapback approval reactions**, the **Sharp →
Rastermill image-backend swap**, an ephemeral **Activity tab** in the Control UI, **OpenTelemetry LLM content spans**,
default `cron.maxConcurrentRuns=8`, and broad reply/startup performance work. The prior `v2026.5.22` stable (2026-05-24)
shipped a **Meeting Notes plugin** with Discord voice capture and auto-start transcript imports, a **~4,100×
model-listing performance improvement** (provider auth-state pre-warmed at startup drops the call from ~20s to ~5ms),
Gateway startup optimisations via process-stable channel-catalog caching and lazy startup-idle plugin work, chat-session
picker pagination, and on the provider side routes Claude 4.x 1M context through GA (no `beta` flag required) and
DeepSeek through the Microsoft Foundry Responses API; the prior `v2026.5.24-beta.2` (2026-05-24) beta surface added
**iMessage tapback approval reactions** (👍 = allow-once, 👎 = deny), realtime Discord voice for OpenClaw status /
cancel / steer / queue commands during active consults, adaptive image compression with model-aware quality, and symlink
rejection on remote container operations; the prior `v2026.5.25-beta.1` (2026-05-26 09:41 UTC) head layered "Beta 1 late
fixes" on top for iMessage threading and Codex sandbox path handling; the `v2026.5.26-beta.1` (2026-05-26 21:10 UTC) and
`v2026.5.26-beta.2` (2026-05-27 05:46 UTC) heads were the larger rollup for the now-shipped `2026.5.26` stable —
substantive surface enumerated above; both graduated into `v2026.5.26` stable on 2026-05-27. A fresh beta train then
opened at `v2026.5.28-beta.1` (2026-05-29 04:46 UTC) layering subagent cwd/workspace separation, prompt-local hook
context, session-lock release on timeout abort, hardened channel delivery (outbound plugin hooks, Matrix room IDs,
iMessage reactions/approvals, Slack final replies, Discord recovered-tool warnings, Microsoft Teams service-URL trust
checks), an iOS Pro UI refresh, CLI hardening (malformed-option rejection, bounded OAuth, legacy auth-profile
migration), and broad caching wins across install records / config / tool catalogs / session stores on top of the
`v2026.5.27` stable surface; the same train then advanced to `v2026.5.28-beta.2` (2026-05-29 12:19 UTC) and
**`v2026.5.28-beta.3`** (2026-05-29 17:19 UTC), which together layer **Claude Opus 4.8** provider support, Fal Krea
image schemas, NVIDIA featured-model catalog entries, MiniMax streaming-music responses, encrypted PDF extraction, and
**GitHub Copilot agent runtime integration**, plus stricter input validation (browser-tool timeouts, Discord component
IDs, cron retry handling, schema-reference rejection) and additional caching wins (native JSON parsing, tool catalog
reuse, manifest model row optimization)) — the exact star number drifts fast, so re-pin before quoting in marketing or
external docs (sources: <https://github.com/openclaw/openclaw>, <https://github.com/openclaw/openclaw/releases>,
<https://github.com/openclaw/openclaw/releases/tag/v2026.5.22>,
<https://github.com/openclaw/openclaw/releases/tag/v2026.5.26-beta.1>,
<https://github.com/openclaw/openclaw/releases/tag/v2026.5.26>,
<https://github.com/openclaw/openclaw/releases/tag/v2026.5.27>,
<https://github.com/openclaw/openclaw/releases/tag/v2026.5.28-beta.1>,
<https://github.com/openclaw/openclaw/releases/tag/v2026.5.28-beta.2>, and
<https://github.com/openclaw/openclaw/releases/tag/v2026.5.28-beta.3>, all accessed 2026-05-29). Subsequent beta train
(~61h later, captured in the thirtieth-pass preface above): six rolls forward from `v2026.5.28-beta.3` to
**`v2026.6.1-beta.1`** (2026-06-01 09:45 UTC) layering a **Skill Workshop** governance system with reviewable proposals
and a `skill_workshop` agent tool, **plugin externalization** of the GitHub Copilot agent runtime and Tokenjuice as
standalone npm packages (`@openclaw/copilot`, `@openclaw/tokenjuice`) distributed via ClawHub, **Workboard** multi-agent
orchestration primitives, **Code mode MCP API files** with scoped agent/global session namespaces, iOS hosted-push-relay
defaults plus native iPad layouts, and broader channel-stability + bounded-timer + SQLite-storage hardening; stable
still `v2026.5.27` (sources: <https://github.com/openclaw/openclaw/releases/tag/v2026.6.1-beta.1>, accessed 2026-06-01).
Beta head subsequently rolled to **`v2026.6.1-beta.2`** (2026-06-01 21:56 UTC) — layering hardened agent recovery from
tool-call interruptions, additional channel-stability work, and **proposal-review capabilities** on the Skill Workshop
governance surface; stable still `v2026.5.27` (source:
<https://github.com/openclaw/openclaw/releases/tag/v2026.6.1-beta.2>, accessed 2026-06-02). Runs on user-controlled
infrastructure (notable community trend: a Mac Mini hardware rush for 24/7 hosting). Connects to Claude, OpenAI,
DeepSeek, and local models. **MIT licensed; calendar-versioned releases (`vYYYY.M.D`) with beta and dev channels; very
active development cadence.**

**Implementation + architecture:** TypeScript / Node.js (v22.16+, v24 recommended). The Gateway is a local control plane
deployed on user machines (macOS, Linux, Windows via WSL2). No cloud requirement; runs entirely on user infrastructure.

**Interface surface — breadth over depth:**

- **CLI (`openclaw …`):** onboard, gateway, agent, message send, pairing, etc. — primary admin surface.
- **20+ chat-platform integrations:** Signal, Telegram, Discord, Slack, WhatsApp, iMessage (via BlueBubbles), Google
  Chat, Matrix, Microsoft Teams, Feishu, LINE, Mattermost, Nextcloud Talk, Nostr, Synology Chat, Tlon, Twitch, Zalo,
  WeChat, QQ, WebChat, IRC, plus a built-in inbox. This is the dashboard replacement for their product category.
- **macOS menu-bar companion app (beta):** voice wake + push-to-talk overlay, WebChat, and remote gateway control — a
  lightweight tray surface complementing the CLI.
- **Mobile (iOS / Android):** optional nodes pair as WebSocket clients for remote access.

**Extensibility:** workspace-based skills (`~/.openclaw/workspace/skills/`) with bundled + managed + custom variants —
and notably the assistant **can write its own skill documents** after completing complex tasks, closing the
execution-to-skill-synthesis loop. Prompt injection points: `AGENTS.md`, `SOUL.md`, `TOOLS.md`. MCP supported as a
standard integration mechanism.

**Kubernetes posture:** A dedicated `openclaw-rocks/openclaw-operator` explicitly offers "production-grade security,
observability, and lifecycle management" — a direct parallel to this project's witwave-operator. AWS published a "Run
OpenClaw on Amazon Lightsail" blog; NVIDIA shipped **NemoClaw** safety tooling for it. Security posture is a
publicly-acknowledged weakness (third-party skills remote-code-execution risk, exposed instances in the wild).

**Relative standing:** The single strongest direct open-source competitor to this project. Ships containerized,
multi-backend (Claude / OpenAI / DeepSeek), operator-managed, 24/7 autonomous — nearly every axis we position around.

_Differentiators in this project's favor:_

1. **Kubernetes-native multi-agent team posture.** Witwave is a cluster-resident platform with A2A-native coordination
   between named agents; OpenClaw targets a single-user, single-machine personal assistant. "Witwave is OpenClaw for
   teams with Kubernetes clusters" captures the split.
2. **Multi-backend routing under one agent identity.** `backend.yaml` routes per-concern (heartbeat → claude, jobs →
   openai, etc.) within one named agent; OpenClaw's multi-model support is per-conversation, not
   per-concern-within-agent.
3. **Stronger safety posture.** Declarative `hooks.yaml` policy + MCP allow-list + session-id HMAC binding address the
   skill-RCE class OpenClaw publicly acknowledges.
4. **Scheduler-primitives breadth.** Jobs / tasks / heartbeats / triggers / continuations / webhooks as first-class
   `.witwave/` frontmatter files. OpenClaw has none of these; scheduling is implicit in the conversation.
5. **Published event-stream wire contract.** `/events/stream` with 14 typed shapes, consumed by multiple independent
   clients; OpenClaw's observability is coupled to its proprietary surfaces.

_OpenClaw's differentiators in its favor:_

1. **Category-leading install base and community** — an order of magnitude more users and a bigger skill ecosystem.
2. **Chat-platform breadth.** 20+ platforms out of the box. Witwave ships none today (trigger + webhook primitives could
   build a subset; real work).
3. **Skill auto-generation.** The execution-to-skill-synthesis loop is shipped; Witwave's skill documents are static
   (see Research Themes → Self-Improvement).
4. **Menu-bar / voice-wake surface.** OpenClaw has a live notification / voice interaction surface; Witwave has a web
   dashboard on the way to maintenance-mode.

**Direction implication.** If Witwave's strategic direction is "CLI-first bootstrap + chat-platform integrations as user
surface + menu-bar/tray for glance-level status + no dashboard" (patterned on OpenClaw), the competitive positioning is:
_OpenClaw for teams deploying agents to shared Kubernetes infrastructure_. The chat-platform integration work (new issue
not yet filed at the time of this pass) is the biggest shared-axis gap.

### kagent (Solo.io / CNCF sandbox)

**Autonomy model:** Autonomous, Kubernetes-native

Open-source framework for building, deploying, and running AI agents on Kubernetes. Initial announce March 2025;
contributed to CNCF sandbox at KubeCon EU 2025; active 2026 development. Built on **A2A + ADK + MCP**, with pre-built
tools for Prometheus, pod logs, and standard Kubernetes APIs — a direct overlap with this project's mcp-kubernetes /
mcp-helm / mcp-prometheus surface. Runtime is Microsoft AutoGen. CNCF backing gives kagent distribution weight this
project doesn't have.

**Relative standing:** Our nearest cloud-native OSS competitor. Both projects are Kubernetes-native and lead with A2A +
MCP; kagent doesn't offer a multi-backend router analogous to this project's `backend.yaml` routing across Claude /
OpenAI / Gemini under one named-agent identity, and uses AutoGen rather than direct SDK wrappers. The clearest question
for our positioning: "multi-backend under one identity" and "scheduler-primitives-first" (jobs + tasks + heartbeats +
triggers + continuations + webhooks) are the defensible differentiators vs. kagent's AutoGen-runtime-plus-prebuilt-tools
approach.

### Amazon Bedrock AgentCore (AWS)

**Autonomy model:** Autonomous, managed cloud

Managed platform for "securely deploy and operate AI agents at any scale" — preview 2025; **Policy GA 2026-03-03,
Evaluations GA 2026-03-31.** Surface includes a runtime, a gateway (tool/MCP access), memory, identity, observability,
policy (governance), and evaluations (quality). Covers the same infrastructure concerns as this project's harness, but
as an AWS-managed service. Locked to Bedrock-hosted models.

**Relative standing:** Mandatory hyperscaler reference. AgentCore's Policy + Evaluations track directly against this
project's hook policy engine + emerging smoke-test surface. Differentiators: we're open-source, self-hosted, and
model-backend-agnostic (Claude / OpenAI / Gemini); AgentCore is closed, managed, Bedrock-only. The competitive dynamic
is hyperscaler-managed-SaaS vs. self-hosted-Kubernetes — classic split.

### Microsoft Agent Framework + Foundry Agent Service (Microsoft)

**Autonomy model:** Semi-autonomous (orchestration framework + managed runtime)

**Agent Framework:** Open-source framework (Python + .NET) for building and orchestrating multi-agent workflows. **GA
1.0 shipped 2026-04-02** (Python + .NET both tagged `1.0.0` the same day); on the v1 line the cadence has been brisk —
**Python `1.8.0` (2026-06-04)** adds **MCP-based skills discovery** (the Python-side counterpart of the
`dotnet-1.8.0`/`dotnet-1.9.0` surface, landing across both language lines now), **progressive tool exposure**,
file-access operations, and **structured-output support for Bedrock**, advancing the Python line past the prior
**`1.7.0` (2026-05-28)** baseline which had introduced **`HarnessAgent`** with background-agents harness provider
support, **`A2AAgentSession`** with referenced task IDs and input-required capabilities, and experimental prompt-agent
conversion and deployment APIs in `FoundryChatClient`; headline breaking change at the `1.7.0` cut was **removal of
Python-only declarative actions** plus alias-kind renames to align with C# canonical names (projects relying on
Python-only action types needed to migrate on upgrade). The earlier **Python `1.6.0` (2026-05-22)** jumped past 1.5.0
and added a first-class **Shell tool** with local + Docker execution, a Monty-backed CodeAct provider
(`agent-framework-monty`), experimental hosted tool factories in `FoundryChatClient`, and `return_immediately` for
non-streaming A2A background ops; its headline breaking change was **instrumentation enabled by default** in both
`agent-framework-core` and `agent-framework-foundry`. **.NET line jumped past `dotnet-1.7.x` to `dotnet-1.8.0`
(2026-06-02)** — adds **MCP-based skills support** and improved **`ForeachExecutor` iteration-state persistence across
checkpoints**, advancing the .NET line's checkpoint-durability story past the hyperlight-integration baseline
`dotnet-1.6.1` (2026-05-14) shipped — and rolled forward same-week to **`dotnet-1.9.0` (2026-06-03)** which adds the
Python-side **`McpSkillsSource`** (MCP-based skills discovery extending the dotnet-1.8.0 surface across language lines)
plus AGUI hosting and workflow bug fixes (source: <https://github.com/microsoft/agent-framework/releases>, accessed
2026-06-05). Both lines ship minor releases on near-weekly cadence — exiting the "public preview" framing the doc
previously used (sources: <https://github.com/microsoft/agent-framework/releases/tag/python-1.8.0> accessed 2026-06-05,
<https://github.com/microsoft/agent-framework/releases/tag/python-1.7.0> accessed 2026-05-28, and
<https://github.com/microsoft/agent-framework/releases/tag/python-1.6.0> accessed 2026-05-25). First-class A2A, MCP, and
OpenTelemetry — exactly the same tripod we ship.

**Foundry Agent Service:** GA announced March 2026. OpenAI Responses-compatible API; hosts DeepSeek, xAI, Meta,
LangChain, LangGraph models (in addition to Azure OpenAI). Directly overlaps this project's cross-backend orchestration.
Differentiator is Azure-first deployment; not Kubernetes-operator-native.

**Relative standing:** The Microsoft entry in the category. A2A + MCP + OTel parity at the framework level forecloses
our "we ship these" differentiator from Option A framing — narrowing to _how_ we compose them is the right response.
Microsoft's strength is Azure distribution and OpenAI Responses compatibility; ours is infrastructure-as-code Kubernetes
posture and multi-backend routing across three distinct LLM vendors rather than a single API surface.

### Cloudflare Agent Cloud (Cloudflare)

**Autonomy model:** Autonomous, managed edge platform

Launched during **Agents Week (2026-04-13 to 2026-04-17)** — the same week this doc is being revised.

- **Cloudflare Mesh** — private-networking "single secure fabric" for agents / humans / multicloud; branded to secure
  the AI agent lifecycle end-to-end.
- **Dynamic Workers** — millisecond-spawn sandboxes for agent-generated code.
- **AI Gateway** — unifies 70+ models across 12+ providers (directly parallel to this project's multi-backend routing —
  but much broader).

**Relative standing:** Category-defining launch in the very week of this research. Cloudflare's positioning of "Agent
Cloud" is itself a category signal — "Agent Fabric / Mesh / Cloud" is consolidating as THE 2026 term for the space. Our
counter-positioning: Cloudflare runs on Workers (edge compute with millisecond spawn), while this project runs
Kubernetes Pods (persistent, stateful, per-agent filesystem). Different deployment models; some workloads need one, some
need the other. The AI Gateway is a serious differentiation challenge to our backend-routing story — Cloudflare covers
vastly more providers.

---

## Category references

These products anchor category vocabulary but aren't primary competitors — noted here so the doc's language aligns with
where the market is converging.

### NVIDIA NeMo Agent Toolkit (NAT)

Previously branded AIQ; renamed NAT in early 2026; **GTC 2026 (March 16–19) partner launch with ~16 platform vendors**
(Adobe, Atlassian, Box, Cadence, Cisco, CrowdStrike, SAP, Salesforce, ServiceNow, Siemens, Synopsys, others)
standardizing on it. Open-source library for connecting / evaluating / accelerating teams of agents; framework-agnostic
instrumentation across LangChain / LlamaIndex / CrewAI / Microsoft Semantic Kernel / Google ADK. **FastMCP Workflow
Publishing** lets NAT workflows publish as MCP servers — crossing the observability-to-tooling boundary. Matters not as
a head-to-head competitor but as a cross-cutting standardization layer that changes how the rest of the landscape
integrates.

### Salesforce Agent Fabric

Agent Fabric with Guided Determinism + centralized governance controls, Flex Gateway, Runtime Fabric support. Positioned
as "trusted agent control plane for a rapidly evolving multi-vendor AI landscape" — automated discovery, authoring, and
centralized LLM governance across vendors. Our harness is architecturally the same role (routing + governance across
multiple backends) in a Kubernetes-native form. Noted here because **"Agent Fabric" is becoming the canonical enterprise
category name** alongside "Agent Mesh" and "Agent Cloud."

---

## Research Themes

Thin navigational scaffolding — one paragraph per theme pointing at the relevant entries in Reference Products and Gap
Analysis for current state. Not a research bibliography; the competitor-specific detail lives with each competitor's
section (which ages on a clear cadence), and industry statistics that were previously inline have been retired because
they drift invisibly and can't be kept honest without quarterly refresh discipline.

### Memory

Persistent structured memory across runs. CrewAI's unified Memory class with LLM-inferred hierarchical scopes +
composite recall is the leading open-source implementation. Hermes Agent's SQLite FTS5 + auto-generated skills is the
consumer-side peer. This project uses flat markdown files, which work for prose notes but are fragile for structured
data needing reliable read/update. Candidate: shared structured memory index (F-003, on hold pending shared-volume
infrastructure).

### Observability

Metrics + OpenTelemetry tracing + event stream. Now table stakes across the category — Bedrock AgentCore, kagent,
Cloudflare, and Microsoft Agent Framework all ship the tripod. This project's remaining edge is the published
multi-client event-stream wire contract (`docs/events/events.schema.json`) consumed by the dashboard + `ww` CLI + future
mobile — most competitors couple event observability to proprietary UIs. See Gap Analysis → Observability.

### Human-in-the-Loop

Approval gates before destructive actions. LangGraph's `interrupt()` with structured payloads is the reference pattern.
The Claude Agent SDK ships `AskUserQuestion` as a built-in HITL tool. Devin shows plan-before-code as a hard checkpoint.
This project has `AskUserQuestion` available but not yet enabled (F-001, open — one-line wiring change in
`executor.py`).

### Guardrails / Safety

Prevention-first control hierarchy: hooks → human intervention → trace log. LangGraph ships declarative guardrail nodes.
The Claude Agent SDK's `PreToolUse` hook supports `updatedInput` for argument rewriting (not just blocking). This
project ships the hook runtime (`hooks.yaml` baseline + per-agent extensions, hot-reloaded) plus MCP command + cwd
allow-lists; see the Claude Code / SDK entry for the specific API surface and Gap Analysis → Safety for what remains.

### Coordination

Multi-agent delegation patterns. A2A v1.0 (Linux Foundation governance, 150+ organizations) is the emerging standard;
LangGraph Platform, Microsoft Agent Framework, and Bedrock AgentCore all integrate it natively. Research shows
hierarchical planner-worker topologies outperform flat "bag of agents" by ~17x on error compounding. This project's
hybrid heartbeat-orchestrator + A2A-delegation model aligns with the winning topology. Implemented: `delegate` skill +
`POST /triggers/{endpoint}` for event-driven dispatch (F-006).

### Durability / Crash Recovery

Checkpointing is mandatory in production systems post-LangGraph-1.x (which made it a hard requirement). Temporal.io's
durable workflow model is the broader 2026 reference architecture. CrewAI's v1.14.0 checkpoint/resume primitives are the
OSS peer reference; **LangGraph v1.2.0 (2026-05-12) extended this to
`durable error-handler resume across host crashes`** — the error-handler itself now survives a host crash, not just
successful graph state (source: <https://github.com/langchain-ai/langgraph/releases>, accessed 2026-05-15). This project
has stale-checkpoint detection on startup (F-005); full session resume past `resume=session_id` remains a longer-term
follow-on.

### Tooling / MCP

MCP is under Linux Foundation governance (donated December 2025). Hundreds of community MCP servers cover browsers,
databases, APIs, system integrations. Native MCP support is ubiquitous across the landscape — table stakes. This project
ships three MCP tool servers (`mcp-kubernetes`, `mcp-helm`, `mcp-prometheus`), each bearer-auth-gated and
call-budget-capped. Dynamic _task-aware_ tool injection (CrewAI's Tool Search — load only the tools relevant to the
current prompt) is the remaining frontier; see Gap Analysis → Tooling.

### Planning / Task Decomposition

Plan-before-code as a hard checkpoint pattern. OpenHands's Planning Agent (read-only until `PLAN.md` is finalized) + the
Claude Agent SDK's `permission_mode="plan"` are the reference implementations. Research confirms planning phases produce
fewer cascading failures. This project has neither a planning mode nor a plan-gate (F-012, open).

### Safety / Governance

Microsoft's Agent Governance Toolkit (MIT license, 2026-04-02) is the first toolkit to address all 10 OWASP Agentic Top
10 risks with deterministic sub-millisecond policy enforcement. EU AI Act high-risk obligations take effect August 2026;
Colorado AI Act becomes enforceable June 2026 (verify specifics before quoting — regulation dates shift). This project's
`hooks.yaml` declarative policy engine provides the enforcement primitive; the gap is OWASP-category labelling on rules
so it becomes a direct comparable to the MS toolkit. See Gap Analysis → Safety for specifics.

### Self-Improvement / Lifelong Learning

The closed learning loop: execution → skill synthesis → future reuse. Hermes Agent auto-generates skill documents after
completing complex tasks; Google's Always-On Memory Agent continuously consolidates in the background. The 2026 frame:
"can the agent remember what it learned yesterday and do it better tomorrow?" This project has Claude-oriented
skill-document infrastructure (`.claude/skills/`) and cross-backend identity documents (`CLAUDE.md`, `AGENTS.md`,
`GEMINI.md`) plus Gemini session-history memory, but no execution-to-skill synthesis path. Candidate: post-task skill
synthesis that evaluates whether a completed run yielded a reusable pattern.

### Cost / Token Management

Token budgeting + context-usage monitoring to prevent runaway bills and silent tail-end degradation. The Claude Agent
SDK ships `task_budget` (per-session cap) and `get_context_usage()` (real-time consumption by category). CrewAI tracks
token usage in `LLMCallCompletedEvent`. Production agents are widely over-resourced (industry finding; verify current
figure before citing). This project has `get_context_usage()` wired; `task_budget` is proposed but unimplemented
(F-010).

---

## Gap Analysis

Last updated: 2026-04-07 by local-agent.

- **Memory and knowledge management:** Flat markdown memory files work for prose notes but are fragile for structured
  data. Hermes Agent (NousResearch, February 2026) ships SQLite FTS5 with LLM-powered summarization and a pluggable
  memory provider interface — the gap vs. this project is widening. CrewAI's documented 2026 limitation (losing
  coordination state when a crew ends) confirms that persistent structured shared memory is a meaningful differentiator.
  F-003 (shared memory index) remains on hold pending shared volume infrastructure.

- **Human-in-the-loop and approval gates:** `AskUserQuestion` is available in the SDK but not yet enabled in this
  project (F-001, open). LangGraph 2.0's redesigned `interrupt()` with structured payloads and Claude Code's
  community-reported demand for approval gates before destructive operations both confirm this is a consistently-wanted
  primitive. Enabling it is a one-line change.

- **Multi-agent coordination and delegation:** A2A-based delegation is implemented (F-006, closed). LangGraph v1.1's
  deferred nodes provide the reference pattern for fan-out/fan-in coordination this project does not yet support. The
  winning production topology (orchestrator + local mesh) aligns with current design; the gap is fan-out task
  distribution with result aggregation.

- **Scheduling and event-driven triggers:** Inbound HTTP triggers and outbound webhooks are implemented — triggers serve
  `POST /triggers/{endpoint}` endpoints with HMAC auth; webhooks deliver filtered outbound HTTP notifications with LLM
  extraction, retry, and HMAC signing. Devin's self-scheduling validates the scheduled-prompt model. The remaining gap
  is dynamic tooling and deeper external system integrations.

- **Observability and debuggability:** Metrics + distributed tracing are now baseline across the space — Bedrock
  AgentCore ships observability by default, kagent includes Prometheus as a pre-built tool, Cloudflare Agent Cloud and
  Microsoft Agent Framework lead with OpenTelemetry. Shipping `backend_*` metrics + `traceparent` propagation is
  therefore no longer a differentiator; it's entry to the category. The actual differentiator is the **published
  event-stream wire contract consumed by multiple independent clients** — `/events/stream` with 14 typed event shapes
  documented in `docs/events/events.schema.json`, same schema consumed by the web dashboard today and by the `ww` CLI +
  future mobile clients. Most competitors' event observability is coupled to their proprietary UI (AgentCore console,
  LangSmith, Devin IDE embed); publishing it as a first-class multi-client contract is the remaining edge. Remaining
  gap: per-agent RED / USE dashboards bundled as default Grafana JSON (partially started via
  `charts/witwave/dashboards/`).

- **Safety and guardrails:** Microsoft released the Agent Governance Toolkit (April 2, 2026, MIT license) — the first
  toolkit to address all 10 OWASP Agentic Top 10 risks with deterministic, sub-millisecond policy enforcement. OWASP
  published the Top 10 for Agentic Applications in December 2025. EU AI Act high-risk obligations take effect
  August 2026. This project has a two-layer declarative policy system: a conservative built-in **baseline** of deny
  rules (shipped in the claude executor) plus **per-agent extensions** loaded from `hooks.yaml` and hot-reloaded at
  runtime. PostToolUse audit writes one row per tool call to `tool-activity.jsonl` for a forensic trail. MCP transport
  is separately gated by command + cwd allow-lists (`MCP_ALLOWED_COMMANDS` / `MCP_ALLOWED_COMMAND_PREFIXES` /
  `MCP_ALLOWED_CWD_PREFIXES` + positional-script rejection in `mcp_command_args_safe()`). The remaining gap is
  **OWASP-category labelling** — rules in `hooks.yaml` are ad-hoc-named today; mapping each to the OWASP Agentic Top 10
  categories (`A01: prompt-injection`, `A02: tool-misuse`, etc.) would turn the declarative layer into a direct
  comparable with Microsoft's toolkit.

- **Tooling and integrations (MCP, webhooks, APIs):** MCP configuration is implemented (F-004, closed). Outbound
  webhooks and inbound triggers are implemented. **Static `ALLOWED_TOOLS` is implemented on claude** (hot-reloadable via
  `settings.json`) and **scaffolded on gemini** (env + reload counter in place, pending the hand-rolled AFC loop). Three
  MCP tool servers ship (`mcp-kubernetes`, `mcp-helm`, `mcp-prometheus`); each enforces its own bearer auth and a
  per-(server, tool) call-budget knob (`mcp_tool_budget_exhausted_total`). Dynamic _task-aware_ tool injection (CrewAI's
  tool search — loading only the tools relevant to the current prompt rather than the full allow-list) is still the open
  frontier.

- **Kubernetes-native agent infrastructure (contested lane, April–May 2026):** The position this project has held is no
  longer uncontested. kagent (CNCF sandbox, Solo.io), OpenClaw's dedicated operator, OpenHands v1.6 Kubernetes + RBAC
  support, and the **Agent Sandbox** Kubernetes SIG Apps subproject (with Google's hosted GKE Agent Sandbox variant
  announced at Cloud Next '26 — gVisor isolation, ~300 sandboxes/sec, three new primitives `Sandbox` / `SandboxTemplate`
  / `SandboxClaim`; source: <https://www.infoq.com/news/2026/05/gke-agent-sandbox-hypercluster/>, accessed 2026-05-11)
  all now occupy the same lane. Differentiators that _do_ hold up head-to-head with these: (1) **multi-backend routing
  under one named agent identity** — Claude / OpenAI / Gemini behind `backend.yaml` routing rules with per-concern
  dispatch (heartbeat to claude, jobs to openai, etc.) is unique in the Kubernetes-native OSS set; competitors are
  mostly single-framework (kagent on AutoGen) or single-model. (2) **Scheduler primitive breadth** — jobs, tasks,
  heartbeats, triggers, continuations, webhooks as first-class `.witwave/` frontmatter files. kagent and OpenClaw don't
  ship equivalents. (3) **Per-agent cross-pod topology** — harness + backends + shared MCP tools is a production-ready
  shape that OpenClaw's single-agent framing doesn't match. (4) **Declarative CRD lifecycle via `WitwaveAgent` +
  `WitwavePrompt`** going through a dedicated operator with status phases, finalizers, and multi-tenant manifest
  ConfigMaps. kagent is closer to ours on this axis but uses CRDs only for agent definition, not prompt lifecycle.

- **Cost and resource management:** `task_budget` env var is proposed (#69, open) but not implemented. Industry finding:
  90% of production agents are over-resourced in 2026; cost control is treated as a first-class architectural concern.
  The per-message-kind budget split (separate caps for heartbeat, scheduled prompts, A2A-triggered runs) is an open
  question in #69 that would unlock fine-grained cost control.

- **Self-improvement and lifelong learning:** Hermes Agent's auto-generated skills (writing a new skill document after
  completing a complex task) and Google's Always-On Memory Agent (continuous ingestion + background consolidation)
  represent a new category this project does not yet address. The project already has a skill document system; closing
  the loop from execution → post-task skill synthesis → capability accumulation is a novel and high-value direction with
  no existing open issue.
