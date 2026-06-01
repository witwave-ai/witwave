# Changelog

All notable changes to this project are documented here. Format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/). The project is pre-1.0 — minor version bumps may introduce
user-visible behaviour changes; they are called out explicitly in the **Changed** section of each entry.

## [Unreleased]

## [0.34.0] — 2026-06-01

Adds **Milo**, the team's Agent-Resources (HR) agent, and the platform plumbing that lets an agent safely keep the team
on the latest version: a bounded `agentLifecycle` Kubernetes-access preset, an image-patch admission policy,
machine-readable agent versions in the CLI, and a per-agent reasoning-effort knob on the Claude backend.

### Added

- **cli**: `ww agent list` and `ww agent status` gain `--json` (with harness + per-backend image versions) and a VERSION
  column; `--kubernetes-api-access` accepts the new `agentLifecycle` mode.
- **operator**: new `agentLifecycle` kubernetesApiAccess preset — `namespaceWrite` plus `patch` on `witwaveagents`, so
  an Agent-Resources agent can drive `ww agent upgrade` against its peers.
- **charts**: gated `agentImagePatchPolicy` ValidatingAdmissionPolicy (witwave-operator) that constrains a chosen
  ServiceAccount's WitwaveAgent patches to image tags/digests — locking image repositories and backend wiring.
- **claude**: `CLAUDE_EFFORT` (low/medium/high/max) forwarded to the Claude Agent SDK reasoning-effort setting, so a
  single agent can run at higher effort without affecting the rest of the team.

### Agent identity

- **milo**: new Agent-Resources agent — keeps a queryable team roster (`roster-audit`) and safely keeps the team on the
  latest release as a self-first canary (`team-upgrade`). Deploys on Opus 4.8 at max effort; the `team-upgrade` job
  ships disabled.
- **zora**: reconcile the dispatch-loop docs (CLAUDE.md + `dispatch-team`) to the current 60-minute cadence.

## [0.33.6] — 2026-05-30

Quiet documentation patch — three competitive-landscape research refreshes capturing the OpenClaw v2026.5.28 beta train,
A2A v1.0.1, and langgraph-sdk v0.4.0, a prettier fix-forward to unblock `CI — Docs` on main, and continued docstring
backfill across the harness and shared event / session-stream public surfaces. No runtime behaviour change.

### Fixed

- **ci**: Re-apply prettier to `docs/competitive-landscape.md` after the prior research dispatch landed unformatted,
  unblocking `CI — Docs` on main.

### Documentation

- **harness + shared**: Add docstrings to the events, A2A backend, and session-stream public surfaces —
  `EventEnvelope.to_dict`, `EventStream.subscriber_count` / `ring_size`, `A2ABackend.close`,
  `SessionStreamEnvelope.to_dict`, and `SessionStreamBroadcaster.subscriber_count` / `ring_size` — each derived from the
  function body (shallow-payload copy on `to_dict` variants, `len()` target on the introspection properties, `is_closed`
  idempotency guard on `close`).
- **research**: Twenty-seventh through twenty-ninth-pass refreshes of `competitive-landscape.md` capturing OpenClaw
  v2026.5.28-beta.1 / beta.2 / beta.3 (subagent cwd/workspace separation, prompt-local hook context, session-lock
  release on timeout abort, plus Claude Opus 4.8, Fal Krea, NVIDIA, MiniMax provider entries and GitHub Copilot
  agent-runtime integration), A2A v1.0.0 → v1.0.1 (HTTP-binding / transcoding / TaskStatus bug-fix release), and
  langgraph-sdk v0.4.0 with companion langgraph-cli v0.4.27 (SDK-level SSE / websocket stream transports, async
  reconnect, scoped subgraph handles).

## [0.33.5] — 2026-05-29

Quiet patch — an embedded-chart drift remediation in the `ww` CLI plus continued docstring backfill across the Python
backend and harness public surface, helm-docs-style comments on the chart values surface, and the routine
competitive-landscape research refresh and README install-example version bump. No runtime behaviour change.

### Fixed

- **clients/ww**: Sync the embedded operator chart's `values.yaml` with the canonical copy in `charts/witwave-operator/`
  after the helm-docs comment backfill drifted them apart, unblocking `CI — ww CLI`'s embedded-chart-drift check on
  main.

### Documentation

- **charts/{witwave,witwave-operator}**: Add helm-docs-style comments to `podMonitor`, `podAnnotations`, and `podLabels`
  values so each knob carries its template-usage intent next to the field.
- **backends/{claude,gemini,openai}**: Add docstrings to the gemini and openai executors' `run` wrappers — completing
  the three-backend symmetry started in v0.33.2 / v0.33.3 — and to the claude executor's `mcp_config_watcher`.
- **backends/{echo,openai} + harness + shared**: Add docstrings across remaining public Python symbols on the executor,
  config, session-stream, hooks-engine, and tool-audit surfaces.
- **research**: Twenty-sixth-pass refresh of `competitive-landscape.md` against current upstream state — OpenClaw
  v2026.5.27 GA, CrewAI 1.14.6 GA, Microsoft Agent Framework python-1.7.0.
- **readme**: Bump versioned install examples from 0.33.3 → 0.33.4 so quickstart copies pull a current release.

## [0.33.4] — 2026-05-28

Quiet documentation patch — docstring backfill across the three Python backend executors' public surface, a
competitive-landscape research refresh, and a README install-example version bump. A prettier fix-forward on the
landscape file rounds out the release. No runtime behaviour change.

### Fixed

- **ci**: Re-apply prettier to `docs/competitive-landscape.md` after the prior research dispatch landed unformatted,
  unblocking `CI — Docs` on main.

### Documentation

- **backends/{claude,gemini,openai}**: Ground the executor `run_query` entrypoint with docstrings covering the request
  lifecycle, streaming contract, and shutdown ordering — completing the three-backend symmetry started in v0.33.2 /
  v0.33.3.
- **backends/{gemini,openai}**: Add docstrings to the remaining executor public surface (helpers + lifecycle hooks),
  bringing parity with the claude executor backfilled in v0.33.3.
- **research**: Twenty-fourth-pass refresh of `competitive-landscape.md` against current upstream state.
- **readme**: Bump versioned install examples from 0.23.15 → 0.33.3 so quickstart copies pull a current release.

## [0.33.3] — 2026-05-27

Risk-work patch closing the standing gosec carries — three commits convert previously-mitigated G115/G404 findings from
silent gauntlet-drops to in-source `//nolint:gosec` annotations that bake the design rationale next to the code. A ruff
fix-forward, a docstring backfill on the openai/claude backends, and a competitive-landscape refresh round out the
release. No runtime behaviour change.

### Fixed

- **clients/ww**: Annotate the two `math/rand.Int63n` retry-jitter sites (`internal/client.(*Client).sleep`,
  `cmd/tail.nextBackoff`) with `//nolint:gosec` G404 plus the non-crypto-jitter rationale, closing the standing G404
  carry. Also annotate the 8 `int64`/`int`→`int32` port conversions across
  `internal/agent/backend_{add,remove,rename}.go` and `defaults.go` with `//nolint:gosec` G115 plus the CRD-schema bound
  rationale (`spec.backends` capped at 50, `BackendPort` range `[8001, 8050]`), closing the standing G115 carry on the
  agent surface.
- **operator**: Annotate the two `int`→`int32` status-counter conversions in `witwaveprompt_controller` (`desiredCount`,
  `readyCount`) with `//nolint:gosec` G115 plus the K8s API list-size bound rationale, closing the standing G115 carry
  on the operator.
- **ci**: Re-apply `ruff format` to `backends/openai/main.py` so the docstring continuation indentation matches the
  formatter, unblocking `CI — Python Services` and `Publish — Social Website` after the prior dispatch added docstrings
  without running the formatter.

### Documentation

- **backends/claude**: Ground the claude executor's load-bearing public surface (`log_entry`, `log_trace`,
  `AgentExecutor`, `AgentExecutor.execute`, `AgentExecutor.cancel`) with module-level docstrings covering the gates,
  streaming contract, MCP/hook state model, and shutdown ordering.
- **backends/openai**: Add docstrings to the public surface (`load_agent_description`, `build_agent_card`,
  `health_start`, `health`, `health_ready`, `main`) plus the non-obvious `PlaywrightComputer` methods (`screenshot`,
  `click`, `wait`, `close`), completing the three-backend symmetry started on claude/gemini.
- **harness**: Add a docstring to `main.events_stream_handler`, closing the route-handler-closure batch deferred from
  the prior docstring dispatch.
- **research**: Twenty-third-pass refresh of `competitive-landscape.md` against the current upstream state — graduate
  OpenClaw v2026.5.22→v2026.5.26 GA (retiring the beta head reference), refresh OpenHands stars 72,500+→75,000+ to match
  live drift.

## [0.33.2] — 2026-05-27

Security-focused patch closing 9 govulncheck-reachable HIGH CVEs via batched `golang.org/x/{net,crypto}` upgrades across
`clients/ww` and the operator. Routine docstring backfill on the claude/gemini backends and a competitive-landscape
research refresh round out the release.

### Fixed

- **clients/ww**: Bump `golang.org/x/net` 0.53.0→0.55.0 and `golang.org/x/crypto` 0.50.0→0.52.0 to close 8 reachable
  HIGH-severity CVEs — GO-2026-5026 (idna.ToASCII Punycode-label rejection, reachable through `internal/client`'s HTTP
  surface) plus GO-2026-{5013,5015,5017,5018,5019,5020,5021} covering ssh/knownhosts auth-bypass, DoS, panic, and
  deadlock paths reachable through `internal/agent.pushBranch`. Post-bump govulncheck reports zero reachable CVEs.
- **operator**: Bump `golang.org/x/net` 0.53.0→0.55.0 to close GO-2026-5026 (idna.ToASCII Punycode-label rejection)
  reachable via `WitwaveAgentReconciler.Reconcile`. Post-bump govulncheck reports zero reachable CVEs.

### Documentation

- **backends/{claude,gemini}**: Add module-level docstrings to the public surface (`load_agent_description`,
  `build_agent_card`, `health_start`, `health`, `health_ready`, `main`), mirroring the docstring style already in place
  on `backends/echo/main.py`.
- **research**: Refresh `competitive-landscape.md` against current upstream state — LangGraph v1.2.1→v1.2.2, OpenClaw
  beta head v2026.5.25-beta.1→v2026.5.26-beta.1, with a downward re-pin of the OpenClaw star/fork counts to preserve the
  `+` semantics.

## [0.33.1] — 2026-05-26

Quiet security patch — pin `fastapi` to `0.136.1` across the Python surface to substantively mitigate MAL-2026-4750
(HIGH). Routine ruff/prettier formatting and a CI-docs ergonomics tweak round out the release; no runtime behaviour
change beyond the dependency pin.

### Fixed

- **deps**: Pin `fastapi==0.136.1` across the harness and Python services (5 sections) to close MAL-2026-4750 (HIGH).

## [0.33.0] — 2026-05-26

Observability-heavy minor: the codex backend gains another round of Prometheus surface coverage (event-loop lag, session
store save errors, stale-session retry attempts, unsupported stdio MCP entries, session binding fallback, MCP caller
cardinality, runtime cost guardrails), `claude` exposes an identity-revision gauge, and `ww doctor release` now verifies
runtime posture before declaring readiness. A dashboard test-mock typing fix and a backend docs refresh round out the
release.

### Added

- **backends/codex**: Track event-loop lag, session-store save errors, stale-session retry attempts, unsupported stdio
  MCP entries, session binding fallback metrics, MCP caller cardinality, and runtime cost guardrails so the codex
  Prometheus surface aligns with the wider backend fleet.
- **backends/claude**: Expose an identity-revision metric so consumers can see when the backend has picked up a fresh
  agent identity without scraping the agent-card directly.
- **ww**: `ww doctor release` now verifies runtime posture before declaring readiness, catching configuration drift that
  would otherwise only surface at request time.

### Fixed

- **backends/codex**: Mirror hook denial alias metrics so denial counters land under both the canonical and alias label,
  matching what the claude backend already emits.
- **backends/openai**: Add the missing `backend_agent_md_revision` sibling test that the other backends already carry,
  closing a coverage gap on a public surface.
- **dashboard**: Type the canvas context mock so the test suite stops emitting type warnings under stricter TS configs.

### Documentation

- **ww**: Note that the agent metrics endpoint is optional so consumers can plan around backends that don't expose it.
- **backends**: Clarify memory semantics and MCP transport shapes so the contract between the harness and each backend
  is explicit.

## [0.32.1] — 2026-05-26

Quiet patch — a dashboard dependency audit on the markdown renderer, a CLI/TUI docs refresh, and routine self-team
hygiene. No runtime behaviour change.

### Fixed

- **dashboard**: Audit the markdown renderer dependency chain and refresh the lockfile.

### Documentation

- **ww**: Refresh the CLI README and TUI help text so the documented surface matches current command behaviour.

### Agent identity

- **self-team**: Slow Mira's heartbeat; routine self-tidy across Piper and Zora.

## [0.32.0] — 2026-05-25

Backends now publish their hook enforcement mode through both a dedicated endpoint and the health surface, so the
harness and operators can see which mode each backend is running in without inspecting the hook config directly.

### Added

- **backends**: Expose the active hook enforcement mode on each backend (claude, codex, gemini, openai) and surface the
  same value as a field in the health/readiness payload so platform-level tooling can read it through the existing probe
  channels.

## [0.31.1] — 2026-05-25

Quiet patch sweeping up lint, CI, and a medium-severity dependency CVE. Internal-only fixes — no runtime behaviour
change in well-formed code.

### Fixed

- **backends/codex**: Bump `yaml` 2.8.1 → 2.8.3 to patch CVE-2026-33532 (MEDIUM) — a deeply-nested-YAML parsing DoS
  reachable through the user-supplied `HOOKS_CONFIG_PATH`.
- **harness**: Mark every `zip(...)` paired with `asyncio.gather` results as `strict=True` across `conversations_proxy`,
  `main`, and `metrics_proxy` so any future refactor that breaks the `len(results) == len(backends)` invariant fails
  loud instead of silently dropping metrics labels, agent-card entries, or degraded-readiness signals.
- **ci**: Re-sort Python imports and reformat tests to the newer ruff baseline so `CI — Python Services` runs clean
  against the latest `ruff` that CI pulls.

## [0.31.0] — 2026-05-25

Codex backend reaches Prometheus-and-trace parity with the Claude backend, and `ww` gains a one-shot way to pull every
agent's metrics surface through the apiserver pod proxy. Self-team identity churn continues to settle in parallel.

### Added

- **backends/codex**: Broaden the Prometheus surface to match the Claude backend — audit and SDK-error counters,
  task-level execution metrics, agent-identity revision gauge, hook-config-reload counters, and runtime metric
  placeholders so dashboards stay consistent across backends.
- **ww**: Add `ww agent metrics <name>` to scrape every Prometheus `/metrics` endpoint owned by an agent pod, including
  the harness and enabled backend containers, through the Kubernetes apiserver pod proxy.

### Fixed

- **backends/codex**: Align the startup health probe semantics with the rest of the platform so the readiness signal
  flips at the correct lifecycle point.
- **backends**: Add a Claude prompt-size parity guard so oversized prompts fail loudly at the backend boundary instead
  of silently truncating downstream.

### Documentation

- **backends**: Clarify hook enforcement boundaries so the contract between the harness and each backend is unambiguous.

### Agent identity

- **self-team**: Fix `.claude/agent-card.md` drift across all 9 peers; slow Zora and Piper heartbeats from 30 min to 60
  min; add Mira platform-health v0.3.0 with `ask-peer-clarification` and the `.openai` drop; routine self-tidy across
  the team.

## [0.30.3] — 2026-05-24

Release Doctor clarity patch: disabled agents now show as parked instead of being counted as active Ready capacity when
their CR status still contains the last running deployment state.

### Changed

- **ww**: `ww agent list` now includes an `ENABLED` column so scaled-down agents are obvious.
- **ww**: `ww agent status` now prints the effective `spec.enabled` value.
- **ww**: `ww team status` and `ww doctor release` now treat `spec.enabled=false` agents as disabled even when stale
  status still says Ready.

## [0.30.2] — 2026-05-24

Release Doctor patch: the harness-side health probe now skips backend sidecar entries advertised by `/agents`, avoiding
false failures when `ww doctor release` is run through the normal local port-forward path.

### Fixed

- **ww**: Skip `/agents` entries with `role=backend` during Release Doctor health probes because their URLs are
  pod-local sidecar addresses, not laptop-reachable endpoints.

## [0.30.1] — 2026-05-24

Release-operations patch: `ww` now includes a read-only Release Doctor that stitches together local CLI metadata,
harness reachability, operator health, CRDs, and `WitwaveAgent` readiness into one post-release diagnostic pass.

### Added

- **ww**: Add `ww doctor release` with human, JSON, and YAML output for post-release validation.
- **ww**: Add rollout-gate flags for required agents, strict image-tag checks, and harness/cluster-only partial
  diagnostics.

## [0.30.0] — 2026-05-24

`ww` CLI minor: scheduler files can now be exercised one-shot from the CLI without waiting on cron, and the `validate`
command now hard-fails when a scheduler file is broken instead of silently passing.

### Added

- **ww**: Add ad-hoc scheduler run commands so a scheduler file can be invoked once from the CLI for testing / manual
  triggers, decoupled from its cron schedule.

### Fixed

- **ww**: `ww validate` now fails on invalid scheduler files instead of returning success — broken scheduler configs are
  surfaced at validate time rather than at the next scheduled run.

## [0.29.2] — 2026-05-23

Codex observability proof patch: deployed Codex backends now include the effective Responses API reasoning effort in
their OpenTelemetry traces, giving operators a live, non-secret way to verify that a Codex agent is actually running at
the intended reasoning tier.

### Added

- **backends/codex**: Add `llm.request.reasoning_effort` to the top-level A2A execution span and each Responses API
  request span.
- **backends/codex**: Extend trace coverage tests so the default `xhigh` reasoning effort is asserted through
  `/api/traces`.

## [0.29.1] — 2026-05-23

Codex-backend parity patch: the JavaScript codex backend now exposes live response deltas to the same session stream
surface the rest of the platform watches, records bounded streaming metrics, and emits OpenTelemetry spans into the
existing trace inspection API. This is the first release suitable for testing a Codex-only live agent without losing the
observability surfaces we rely on during backend rollout.

### Added

- **backends/codex**: Stream OpenAI Responses API deltas into `/api/sessions/<id>/stream` when
  `CODEX_RESPONSES_STREAMING=true`, while preserving the existing non-streaming path as the default.
- **backends/codex**: Add bounded streaming telemetry for response deltas, stream duration, and stream failures so Codex
  activity can be graphed alongside the other backends.
- **backends/codex**: Add Node OpenTelemetry support with optional OTLP export and an in-memory span ring powering
  `/api/traces` and `/api/traces/<trace_id>`.

### Fixed

- **backends/codex**: Preserve trace context through A2A requests and streamed response sessions so live Codex tests can
  be inspected without depending on external telemetry infrastructure.

## [0.29.0] — 2026-05-23

Codex-backend maturation minor: the JavaScript `backends/codex` scaffold introduced in v0.28.0 grows up. Sessions are
now bound per-caller with stream-count caps, MCP protocol handling / metrics / trace-context propagation are aligned
with the rest of the backend fleet, and configured MCP servers are bridged through as tools on the codex side. Two
auth-path hardening fixes round out the cycle.

### Added

- **backends/codex**: Bridge configured MCP servers through as tools so the codex backend can call them on the model's
  behalf — closes the MCP-as-tools parity gap with the other backends.
- **backends/codex**: Bind response sessions to the calling identity so sessions can't be reused across callers, and cap
  concurrent session streams per caller to bound resource use.
- **backends/codex**: Propagate trace context across MCP calls, align MCP-emitted metrics with the fleet conventions,
  and align MCP protocol handling so error envelopes / version negotiation match the other backends.
- **backends/codex**: Initial session-stream parity work — the underlying scaffolding the per-caller binding and the
  stream cap build on.

### Fixed

- **backends/codex**: Warn loudly when codex auth is misconfigured instead of silently degrading, and harden the bearer
  auth check so malformed `Authorization` headers no longer slip through.

## [0.28.0] — 2026-05-23

Backend-architecture minor: the Python backend that wrapped OpenAI's Responses API under the **codex** name is renamed
to its truer name **openai**, freeing the **codex** slot for a new JavaScript-based parity scaffold that targets the
actual `codex` CLI. The new JS backend ships with response-session persistence and a CI workflow. Also continues the
content-cycle work on the social website's blog and whitepaper presentation.

### Added

- **backends/codex**: New JavaScript-based codex backend — `backends/codex/{main.js,package.json,test_codex_backend.js}`
  plus Dockerfile, README, and `.github/workflows/ci-codex-backend.yml`. Includes session persistence so codex response
  sessions survive across invocations.
- **backends/openai**: New `openai` backend home (renamed from the former `backends/codex/` Python tree) — same
  executor, metrics, task store, and test suite, now correctly named for the OpenAI Responses API it wraps. Adds
  `test_gpt55_tool_support.py` and a dedicated Dockerfile.

### Changed

- **backends**: Rename the Python `codex` backend → `openai`. The previous `backends/codex/` Python implementation moves
  to `backends/openai/` (executor, main, metrics, sqlite_task_store, tests all preserved); the new `backends/codex/` is
  the JavaScript scaffold described above. Memory test renamed `tests/025.b-memory-codex.md` →
  `tests/025.b-memory-openai.md`.
- **website**: Whitepaper presentation refresh — rows on `whitepapers/index.html` now generate from the catalog via
  `scripts/render-social-whitepaper-lists.mjs`; blog authorship and summary formatting polished; blog script cache
  busted.

### Documentation

- **backends**: New `agent.md` and `README.md` for both `backends/codex/` (JS) and `backends/openai/` (Python).

## [0.27.8] — 2026-05-22

Content-cycle patch publishing the **Autonomy Boundary** whitepaper through the social website, with supporting
infrastructure (whitepaper PDF build pipeline, simplified download controls) and additional public-symbol docstrings on
the harness orchestration layer. No changes to the `ww` CLI, operator runtime, images, or charts — release artifacts are
functionally identical to v0.27.7 modulo the embedded version stamp.

### Added

- **whitepaper**: Publish "The Autonomy Boundary" whitepaper — `social/papers/the-autonomy-boundary.md` source plus the
  matching `social/website/whitepapers/` entry, content-catalog record, and sitemap / llms.txt / index updates.
- **website**: Add a whitepaper PDF build pipeline — `scripts/build-whitepaper-pdfs.sh`,
  `scripts/print-whitepaper-pdf.mjs`, PDF stylesheet (`social/papers/whitepaper-pdf.css`), root-level `package.json` /
  `package-lock.json` for the Node toolchain, and CI wiring (`ci-social-website.yml`, `publish-social-website.yml`).

### Changed

- **website**: Simplify the whitepaper download controls on the social website to streamline reader interaction.

### Documentation

- **harness**: Author one-paragraph docstrings on public symbols in the harness orchestration layer.
- **social**: Refresh the social website's whitepaper catalog to include the autonomy-boundary entry.

## [0.27.7] — 2026-05-19

Test-gap + docs-research patch. Fills the untested-public-API gap on the `ww` agent's `KubernetesApiAccessEnable` /
`KubernetesApiAccessDisable` entrypoints (the in-place helpers were unit-tested but the exported full-flow paths were
not), and refreshes the competitive-landscape research note against today's upstream state (OpenClaw v2026.5.18 stable +
v2026.5.18-beta.1, CrewAI v1.14.5 graduation). No changes to the `ww` CLI runtime, operator, images, or charts — release
artifacts are functionally identical to v0.27.6 modulo the embedded version stamp.

### Fixed

- **ww**: Add full-flow tests for `KubernetesApiAccessEnable` and `KubernetesApiAccessDisable` in
  `clients/ww/internal/agent/kubernetes_api_access_test.go` — 14 net-new test cases covering Add / Update / Already-
  Configured / Not-Found / mode-validation / input-validation paths against the package's existing fake-client fixtures.
- **docs**: Reflow `docs/competitive-landscape.md` to the prettier@3.4.2 prose-wrap so `CI — Docs` and
  `Publish — Social Website` pass on the research-refresh commit.

### Documentation

- **research**: Refresh `docs/competitive-landscape.md` against current industry state — OpenClaw stable bumped to
  v2026.5.18 (Android Talk Mode, Mac redesign, CLI plugin system) and beta to v2026.5.18-beta.1; CrewAI v1.14.5
  graduated to stable with the `CrewAgentExecutor` deprecation as the headline.

## [0.27.6] — 2026-05-18

Documentation + identity-scaffolding patch — re-cut of v0.27.5. The v0.27.5 tag (2026-05-18 15:05Z) was published but
its three release workflows (Container Images, ww CLI, Helm Charts) all hard-failed at their `wait-for-sibling-ci` gate
because `CI — Docs` on the tag SHA refused a `prettier@3.4.2 --check` formatting warning in the auto-generated CHANGELOG
entry. No artifacts were published for v0.27.5. v0.27.6 ships the identical content from a green main HEAD
(`.agents/self/iris/.claude/skills/release/SKILL.md` now pins `prettier@3.4.2 --write` as a mandatory pre-flight to
close the recurring pattern). Substance: normalised "Witwave" casing across the repo and website content, authored
one-paragraph docstrings on public Python symbols in the harness scheduler and the four backends, and added header
comments to shell-script functions in the installer and operator parity check. Also scaffolds the new **milo** agent
(resources / team-ops role) under `.agents/self/milo/` with matching team-roster surfacing on the social website. No
changes to the `ww` CLI, operator runtime, images, or charts — release artifacts are functionally identical to v0.27.4
modulo the embedded version stamp.

### Added

- **agents**: Scaffold the **milo** agent identity (resources / team-ops role) — `.agents/self/milo/` Claude config,
  witwave backend, agent card, heartbeat, and avatar.
- **website**: Add Milo to the team roster on the social website (`content/team.json`, team page, avatar asset).

### Documentation

- **branding**: Standardise and normalise "Witwave" casing across documentation, runbooks, READMEs, architecture,
  supply-chain, competitive-landscape, and social website content.
- **harness/backends**: Author one-paragraph docstrings on public Python symbols in `harness/` (bus message + send /
  receive, SQLite task store, heartbeat runner) and the four backends (echo, claude, codex, gemini) whose signatures
  lacked self-explanatory descriptions.
- **scripts**: Add header comments to shell-script functions in `scripts/install.sh` (`detect_platform`,
  `download_and_verify`, `do_install`), the operator's `check-prometheusrule-parity.sh` (`extract_chart`,
  `extract_operator`), and `operator/.devcontainer/post-install.sh`.

## [0.27.4] — 2026-05-18

Recovery patch release re-publishing v0.27.3's artifacts. The v0.27.3 tag fired against a commit whose `CHANGELOG.md`
entry was not Prettier-reflowed to the 120-char prose-wrap; `CI — Docs` failed on the tag commit and all four
release-window workflows aborted before publishing any container images, `ww` CLI binaries, Helm charts, or the social
website update. This release tags the reflow-fixed tip of `main` so v0.27.3's tool-boundaries whitepaper content cycle
actually reaches consumers. No code or content changes since v0.27.3 — the only commit is the CHANGELOG reflow fix.

### Fixed

- **changelog**: Reflow the v0.27.3 entry to the 120-char Prettier prose-wrap so `CI — Docs` passes and the release
  pipeline can publish artifacts.

## [0.27.3] — 2026-05-18

Patch release covering a content cycle on the tool-boundaries whitepaper: seven editorial passes on the `social/` source
draft (clarifying the backend toolchain boundary, softening the MCP framing, consolidating the argument, tightening the
current-architecture section, and final polish), then publication of the paper through the website (`social/website/`)
with the matching index, sitemap, llms.txt, and styling updates. No changes to the `ww` CLI, operator, images, or charts
— all release artifacts are functionally identical to v0.27.2.

### Documentation

- **social**: Seven editorial passes on the tool-boundaries-for-containerized-agents paper draft — clarifying the
  backend toolchain boundary, softening the MCP toolchain framing, revising and polishing the argument, consolidating
  it, and tightening the current-architecture section.
- **website**: Publish the tool-boundaries whitepaper through `social/website/` — symlinked content entry, whitepapers
  index/JSON, sitemap, llms.txt, styles, and homepage updates.

## [0.27.2] — 2026-05-18

Patch release covering a social-content cycle (Piper field notes for 2026-05-17 plus a new tool-boundaries paper draft).
No changes to the `ww` CLI, operator, images, or charts — all release artifacts are functionally identical to v0.27.1.

### Documentation

- **social**: Two Piper field notes for 2026-05-17 (including "reading the diff") with a follow-up timing/phrasing fix,
  and a new draft paper on tool boundaries for containerized agents. Includes a Prettier reflow fix on the
  reading-the-diff post to satisfy the `CI — Docs` prose-wrap check.

## [0.27.1] — 2026-05-17

Patch release for the shared backend base image introduced in v0.27.0. No API changes.

### Fixed

- **images**: The `ww` binary bundled into `backend-base` now receives the release `VERSION`, git commit, and build
  timestamp through Go ldflags. Backend containers still build from the same shared base image, but `ww version` inside
  those containers now reports the concrete release instead of the development fallback.

## [0.27.0] — 2026-05-17

Minor release introducing a shared backend base image for the Python services, plus two robustness fixes (operator
plan-header error handling, build-artifact ignore) and a documentation clarification on the toolbox/backend image
boundary. No user-facing CLI or operator API changes.

### Added

- **images**: New shared backend base image collects the common Python runtime layer that backend services build on,
  reducing duplication across per-service Dockerfiles and shrinking the surface that has to drift in lockstep when the
  base toolchain moves.

### Fixed

- **operator**: `renderAgent` now propagates the error returned by the plan-header `fmt.Fprintf` write at
  `operator/cmd/plan/main.go`, matching the surrounding emit/Write error-wrapping pattern. Prevents silent continuation
  on broken-pipe / closed-FD failures when the YAML stream's downstream consumer goes away mid-render.
- **tooling**: `.gitignore` now anchors `/operator/plan` so the 55 MB `go build ./cmd/plan` / `go test ./cmd/plan`
  binary droppings from `operator/` don't leak into peer dirty-tree gates. Sits alongside the existing `/ww` and
  `/clients/ww/ww` entries in the agent / audit scratch-binaries section.

### Documentation

- **images**: README clarifies the toolbox / backend base image boundary so contributors choose the right base layer
  when adding a service.

## [0.26.1] — 2026-05-17

Patch release with a single user-facing fix on top of v0.26.0, plus internal test-coverage and tooling-hygiene gap fills
and mira agent-card refreshes. No runtime behaviour changes.

### Fixed

- **ww**: `renderBackendYAML` godoc listed 6 of 7 routing keys — `default` was missing from the parenthetical even
  though the template emits it. Doc now matches the template body.

### Agent identity

- **mira**: Self-tidy 2026-05-16 refreshes the agent-card "Current state" section from "scaffolded but not deployed" to
  the deployed posture plus the open portforward-RBAC posture gap (with zora as the 18:17Z escalation). Follow-up commit
  reflows the new paragraph to satisfy Prettier prose-wrap.

## [0.26.0] — 2026-05-16

Minor release extending the `ww` CLI with first-class support for managing the operator's `kubernetesApiAccess` spec on
agents. Pairs with the operator-side provisioning shipped in v0.24.2 / v0.24.3 / v0.25.0 — the CLI can now configure
agents to use the per-agent ServiceAccount/Role/RoleBinding plumbing without hand-editing `WitwaveAgent` manifests.

### Added

- **ww**: Manage agent Kubernetes API access from `ww agent` — set `spec.kubernetesApiAccess.enabled` and `mode`
  (`readOnly` / `namespaceWrite`) when creating or updating agents, surfacing the same bounded presets the operator
  enforces server-side. New `kubernetes_api_access` helper isolates the access-spec construction and is covered by unit
  tests; the `agent create` flow and build helpers are updated together, and the README documents the new flags.

### Agent identity

- **zora**: Self-tidy 2026-05-16 syncs the agent-card numerical surface against CLAUDE.md after the 2026-05-15 cadence
  relax (heartbeat 15→30 min; evan bug 1.5h→3h, risk 4h→8h; nova every 4h→8h; kira every 3h→6h, research ≥1d→≥2d; finn
  every 3h→6h). Follow-up commit wraps the new heartbeat bullet to satisfy the 120-char Prettier prose-wrap.

## [0.25.1] — 2026-05-16

Recovery patch release re-publishing v0.25.0's artifacts. The v0.25.0 tag fired against a commit whose `CHANGELOG.md`
was not Prettier-formatted; `CI — Docs` failed and the release workflows aborted before publishing any container images,
`ww` CLI binaries, or Helm charts. This release tags the formatting-fixed tip of `main` so the operator's
`spec.kubernetesApiAccess.mode: namespaceWrite` work from v0.25.0 actually reaches consumers. No code changes since
v0.25.0 — the only commit is the CHANGELOG formatting fix.

### Fixed

- **changelog**: Prettier-format the v0.25.0 entry so `CI — Docs` passes and the release pipeline can publish artifacts.

## [0.25.0] — 2026-05-16

Minor release extending the operator's `spec.kubernetesApiAccess` provisioning with a second bounded preset:
`mode: namespaceWrite`. Building on v0.24.2's `readOnly` mode and v0.24.3's RBAC fix, this preset lets per-agent
ServiceAccounts mutate workload resources within their own namespace (deployments, statefulsets, configmaps, pods, jobs,
services, ingresses, etc.) while keeping the hard guardrails in place — secrets remain unreadable and unwritable, RBAC
resources cannot be mutated, raw pod creation is blocked (pods are still mutable via owner controllers), and all
cluster-scoped resources stay out of reach. Unblocks namespace-scoped reconciliation work on Mira.

### Added

- **operator**: `spec.kubernetesApiAccess.mode: namespaceWrite` grants the per-agent Role mutating verbs
  (`create/update/patch/delete`) on common namespace workload resources alongside the existing `readOnly` read verbs.
  Secrets, RBAC mutation, raw pod creation, and cluster-scoped resources are explicitly withheld. CRD schema, both Helm
  chart copies (operator + embedded), reconciler logic, RBAC templates, and tests are updated together; docs describe
  the new preset and its boundary.

## [0.24.3] — 2026-05-16

Patch release fixing the operator's RBAC posture so `spec.kubernetesApiAccess` provisioning works end-to-end. v0.24.2
shipped the `WitwaveAgent` field and the per-agent ServiceAccount/Role/RoleBinding plumbing, but the operator's own
ClusterRole/Role lacked the read-only diagnostic permissions it needs to delegate to namespace agents — so
`mode: readOnly` reconciles failed against live clusters with an RBAC escalation error. This release grants the operator
the matching read-only permissions plus the bind verb it needs to attach the per-agent Role, unblocking retesting on
Mira.

### Fixed

- **operator**: Grant the operator ClusterRole/Role the read-only diagnostic permissions it delegates to per-agent
  Roles, plus the `bind` verb required to attach those Roles. Resolves the RBAC escalation failure that prevented
  `spec.kubernetesApiAccess.mode: readOnly` from reconciling on v0.24.2.

## [0.24.2] — 2026-05-16

Patch release adding opt-in namespace-scoped Kubernetes API access to the operator — `spec.kubernetesApiAccess.enabled`
on `WitwaveAgent` provisions a per-agent ServiceAccount, Role, and RoleBinding so the agent pod can run in-cluster
`kubectl` / client-go calls against its own namespace. The first supported preset is `mode: readOnly`; secrets, nodes,
persistent volumes, namespaces, and mutating verbs are all withheld. When the field is omitted or disabled the pod
continues to mount the namespace `default` ServiceAccount with `automountServiceAccountToken: false`, preserving the
no-token posture.

### Added

- **operator**: `spec.kubernetesApiAccess.enabled` on `WitwaveAgent` provisions a namespace-scoped
  ServiceAccount/Role/RoleBinding and wires the agent pod to it. First supported preset `mode: readOnly` grants
  `get/list/watch` on common namespace workload resources and Witwave CRs plus `get` on `pods/log`; secrets, nodes,
  persistent volumes, namespaces, and mutating verbs are withheld.

## [0.24.1] — 2026-05-16

Patch release fixing the `ww` CLI's `/heartbeat` parser to accept the harness's flat-object response shape (the sibling
test now pins the contract), plus sibling-pattern parity for the `finn` and `piper` agents' `.claude/mcp.json`
scaffolding.

### Fixed

- **ww**: Parse the harness `/heartbeat` flat-object shape correctly; a sibling test now pins the contract so future
  drift fails loudly.

### Agent identity

- **finn, piper**: Add `.claude/mcp.json` to bring both agents to sibling-pattern parity with the rest of the team's
  scaffolding.

## [0.24.0] — 2026-05-16

Minor release adding `kubectl` to the backend toolchains so agents can manage Kubernetes resources directly from their
toolbox containers — unblocking platform-reliability work that needs first-class cluster CLI access — alongside the new
`mira` platform-reliability agent scaffold and a Zora cadence trim halving the team's recurring peer-skill compute.

### Added

- **images (toolbox, backends)**: Bundle `kubectl` into the shared `toolbox` image and the claude/codex/gemini/echo
  backend Dockerfiles so agents have first-class Kubernetes CLI access from their toolbox containers without per-agent
  installation.
- **agents**: Add the `mira` platform-reliability agent scaffold.

### Agent identity

- **mira**: Prioritize restart-snapshot guidance in mira's memory layout so platform-reliability state survives pod
  restarts cleanly.
- **zora**: Double peer cadence floors to halve the team's recurring peer-skill compute (worst-case dispatch / outreach
  latency rises in exchange for fewer ticks/day).

## [0.23.23] — 2026-05-16

Recovery release republishing v0.23.22's `ww agent storage enable` shipment after the v0.23.22 tag's `CI — Docs` gate
failed on a prettier version mismatch (local 3.8.3 vs CI-pinned 3.4.2 on `CHANGELOG.md`) and gated all three release
workflows — no artifacts published for v0.23.22. Cut from `main` HEAD after the prettier reflow followup landed and the
docs gate cleared. No new user-visible changes versus v0.23.22; the runtime-storage CLI command is unchanged.

## [0.23.22] — 2026-05-16

Patch release adding a `ww agent storage enable` CLI command that turns on runtime storage for an existing WitwaveAgent
— replacing the manual `kubectl patch` path that previously sat between agent creation and persistent workspace + memory
across pod restarts.

### Added

- **ww**: `ww agent storage enable <agent>` enables runtime storage on an existing agent, wiring the same fields
  surfaced at `ww agent create` time so storage can be turned on after the fact without hand-crafting the CR patch.

## [0.23.21] — 2026-05-16

Patch release introducing persistent agent runtime state — the operator and `witwave` chart now provision a PVC per
WitwaveAgent and mount it into the harness so agents survive pod restarts with their workspace and memory intact — plus
a hotfix to the new shared `toolbox` image so its bundled pure-Python tools (yamllint, pytest, pip-audit, bandit) can
actually execute.

### Added

- **operator, charts, ww**: Persist agent runtime state across pod restarts. WitwaveAgent CRDs gain runtime-storage
  fields; the operator provisions a PVC per agent and wires it into the harness Deployment; the `witwave` chart adds
  matching template support; and `ww agent create` learns the corresponding flags.

### Fixed

- **harness (toolbox)**: Install `python3` in the toolbox image's final stage so the venv shebangs for yamllint, pytest,
  pip-audit, and bandit resolve. The stage 5 venv's `bin/python3` symlink pointed at `/usr/bin/python3` which wasn't
  apt-installed in the final stage; pure-Python tools failed to exec on both linux/amd64 and linux/arm64.

### Agent identity

- **piper**: Self-tidy pass on 2026-05-15 — align agent-card cadence with the 30-min team-pulse tick and reframe the
  threshold-scaling bullet against the current <15min / <1h / >4h band model.

## [0.23.19] — 2026-05-15

Patch release introducing a shared `toolbox` image as phase 1 of the backend Dockerfile consolidation — the hygiene and
analyzer toolchain that currently duplicates across the claude/codex/gemini backends now publishes from a single source
of truth, with the `ww` binary build folded in alongside. Plus two new `ww` CLI affordances, an agent-cadence trim to
halve the dispatch + outreach tick rate, and routine agent-identity upkeep.

### Added

- **ww**: Add a `team status --watch` mode that streams live team-status updates instead of one-shot rendering.
- **ww**: Persist backend conversation logs so the CLI retains conversation history across invocations.
- **toolbox**: Publish a new `ghcr.io/witwave-ai/images/toolbox` image (phase 1) consolidating the hygiene + analyzer
  toolchain (goimports, staticcheck, errcheck, ineffassign, controller-gen, govulncheck, gosec, shfmt, actionlint,
  hadolint, gitleaks, trivy, helm, shellcheck, plus a tools-venv with ruff/yamllint/pytest/pip-audit/bandit) currently
  duplicated across the claude/codex/gemini backends. Backends switch to `FROM toolbox` in phase 2.
- **site**: Add search favicon assets.

### Changed

- **toolbox**: Fold the `ww` CLI build into the toolbox image via a shared `go-toolchain` base stage so it compiles once
  per release rather than three times across the backend Dockerfiles.
- **agents**: Halve Zora's dispatch-team and Piper's team-pulse cadence from 15 min to 30 min, cutting the team's two
  heaviest recurring ticks from 96 ticks/day each to 48 ticks/day each at the cost of worst-case 30 min
  dispatch/outreach latency.
- **agents(self)**: Align the self-team bootstrap conversation-inspection plumbing across agents.

### Agent identity

- **piper, zora**: Prettier reflow on `piper/.claude/CLAUDE.md` and `zora/.witwave/HEARTBEAT.md` to unbreak the docs +
  social-website CI workflows.

## [0.23.18] — 2026-05-15

Patch release validating the sibling-CI gate fix landed in v0.23.17's wake — the prior cut deadlocked on its own
siblings and cancelled mid-flight, so this release re-exercises the full pipeline — plus a new `ww` team-activity
command and additional pure-helper test coverage on `root.go`.

### Added

- **ww**: Add a `team activity` command that surfaces what each agent on the team is currently working on.

### Fixed

- **ci**: Stop the sibling-CI gate from deadlocking on its own siblings — the previous gate read pending runs as
  "waiting" even when those runs were themselves the tag-push runs that got cancelled by the tag-push, blocking every
  release behind itself.
- **ww**: Cover `root.go` pure helpers (`extract`, `transportErr`, `logicalErr`) under the untested-pure-helper
  gap-class to close finn's coverage gap.

## [0.23.17] — 2026-05-15

Patch release tightening the release CI pipeline — gating all release workflows on sibling CI success via a shared
composite action, building release images natively on arm64 with cross-release buildx caching — plus routine agent-card
upkeep and a competitive-landscape research refresh.

### Changed

- **ci**: Factor the sibling-CI gate into a composite action and apply it to every release workflow as well as the
  social-website publish workflow, so a tag push never produces artifacts off a tree where the per-component CI is red.
- **ci**: Build release container images on native arm64 runners and cache buildx layers between releases to cut
  release-pipeline wall time.

### Fixed

- **ci**: Resolve actionlint complaints on the release.yaml refactor and drop an unused field name from the sibling-CI
  gate read.
- **changelog**: Reformat `CHANGELOG.md` with `prettier --write` after the v0.23.16 cut so the file stays clean against
  the repo's standing prettier scope.

### Agent identity

- **zora**: Self-tidy pass on 2026-05-15 plus a prettier reflow on the cadence table in her agent-card.

### Documentation

- **research**: Refresh `competitive-landscape.md` against current industry state.

## [0.23.16] — 2026-05-15

Patch release publishing the social website to its custom domain, scaffolding the SOPS-based secrets workflow used by
the team's CI and per-agent env files, adding Piper's blog generation skill alongside her first field note, and a broad
sweep of repo-hygiene + lint-CI work to keep the tree quiet for cadence-driven releases.

### Added

- **social**: Add RSS feed generation for blog posts so readers can subscribe via standard feed clients.
- **social**: Add an `llms.txt` guide for the website surface to make agentic crawlers and assistants legible.
- **social**: Add static page generation for the public site so search engines and link previews see real titles and
  per-article metadata instead of the SPA shell.
- **social**: Add a public GitHub Discussions strip with category cards on the homepage and refine the discussion-link
  design so community surfaces are discoverable from the public site.
- **piper**: Add a blog generation skill that lets Piper draft field-note posts from her own observations and publish
  them into `social/posts/`, and publish Piper's first field note for 2026-05-15 through it.
- **sops**: Add an age-based SOPS policy, a smoke example, and a `CI — SOPS` decrypt smoke workflow so encrypted secrets
  are verified on every push; add encrypted SOPS env files for the self and test teams plus a Google Search Console
  secret entry.
- **ci**: Add a `CI — Workflows` lint job that validates every workflow file under `.github/workflows/` and normalises
  workflow display names so the per-workflow CI summary reads consistently.
- **operator**: Wire an RBAC drift guard so the operator chart's RBAC stays aligned with the controller's actual
  Kubernetes API needs.
- **assets**: Add test team avatars and additional agent avatar assets.

### Changed

- **social**: Polish website SEO metadata and article surfaces across pages so titles, descriptions, and OG tags are
  coherent and complete on a per-article basis.
- **operator**: Refresh embedded operator-chart copy and bootstrap env docs; include a sample `prompt` in operator
  example manifests and clarify operator packaging and scaffold comments.
- **workspace**: Make the workspace sample dev-friendly and align workspace access-mode docs.
- **codex**: Switch the repo's Codex config to GPT-5.5.
- **repo**: Clean up repo hygiene drift, rename the root SOPS secrets file, remove the root env bootstrap dependency,
  retire obsolete local-request skills, and refresh stale TODO tracking.
- **README**: Refine project framing and smooth the website link in the top-level README.

### Fixed

- **harness**: Add the Piper self-tidy cron trigger to close a `peer-missing-cron` gap-class finding.
- **harness**: Add a `.witwave/agent-card.md` mirror for felix to close a `sibling-pattern` gap-class finding.
- **trace**: Fix trace auth and doc drift surfaced during the social-website rollout.
- **ci**: Fix the charts CI path that previously broke when no `test-values.yaml` was present.
- **lint**: Finish the markdown lint cleanup pass, reduce changelog markdownlint noise, and strengthen docs/operator CI
  lint coverage.

### Agent identity

- **piper, felix**: Adopt the hold-label-respect and external-trigger principles so both honour the team's HOLD posture
  during paused windows.
- **team**: Align the self and test agent teams so identical roster scaffolding lives across both environments.

### Documentation

- **sops**: Document the SOPS-based credential workflows end-to-end, track in-flight SOPS secrets, document the SOPS
  secret-mirror pattern, and format the SOPS credential docs for consistency.
- **social**: Scaffold `social/website/` as the GitHub Pages-ready source for the public site, featuring the two
  foundational whitepapers, a buildless Markdown reader, plus blog and positioning placeholders, with a publishing
  workflow for `witwave-ai/witwave-ai.github.io`.
- **social**: Add a public Team page for the self-team roster, roles, and avatars, plus a homepage preview that makes
  the agentic-team operating model tangible.
- **social**: Run the social website publisher on every push to `main` during high-iteration site work, while keeping
  no-change publishes as a no-op.
- **social**: Add a manifest-driven Markdown blog index and reader backed by `social/posts/`, with frontmatter-based
  publishing controls, dated post filenames, Piper's first field note, distribution-link metadata, and an explicit
  no-posts empty state.
- **social**: Resolve blog content through the latest `main` commit SHA to reduce stale `posts.json` reads, and move the
  subdued project-steward card to the upper-left of the Team grid.
- **social**: Add a Project page that explains the witwave framework, links to GitHub, and frames the self-maintaining
  agent team as part of the project rather than a separate demo.
- **social**: Add future-seat placeholders to the Team page for planned but not-yet-deployed agent roles.
- **social**: Restyle the public website with a darker, higher-tech visual system while preserving the existing content
  structure.
- **social**: Fold the standalone About positioning points into the Project page and simplify the primary site
  navigation.
- **social**: Switch the public website header to an icon-only witwave mark.
- **social**: Temper the public website hero heading scale so top-level copy reads as bold instead of oversized.
- **social**: Restyle the homepage lead team avatar card so Zora's highlight fits the dark visual system.
- **social**: Slightly reduce the icon-only header logo scale after the larger-logo pass.
- **social**: Move planned future agent cards into a distinct section below the active Team roster.
- **social**: Add GitHub profile links to active Team cards.
- **social**: Move Team ahead of Blog in the public website navigation.
- **social**: Bottom-align Team card profile badges and put Scott's LinkedIn badge before GitHub.
- **social**: Add Piper's X profile badge to the public Team page.
- **social**: Put Piper's GitHub Team badge before her X badge.
- **social**: Tighten narrow mobile website layouts so navigation, heroes, and reader sidebars do not overflow.
- **social**: Move blog post titles into the main article pane so the blog reader sidebar stays focused on controls.
- **social**: Move whitepaper reader titles out of the sidebar so the main paper pane owns the document title.
- **social**: Fine-tune the icon-only header mark slightly smaller.
- **social**: Make blog and whitepaper reader sidebar buttons full-width for a uniform control stack.
- **social**: Remove the Markdown download action from blog posts so field notes stay focused on reading and social
  distribution.
- **social**: Shorten the whitepaper reader download action to `Download MD`.
- **social**: Soften the public AI-adoption thesis so existing and hybrid agent workflows are described as valuable,
  while lifecycle-native integration is framed as the compounding step.
- **social**: Polish website copy by hiding empty blog social placeholders, softening whitepaper teaser language,
  replacing public `self team` wording, and retiring the hidden positioning page.
- **social**: Add website planning notes for a prominent CLI Quick Start and lightweight GitHub Discussions links.
- **social**: Add a prominent Quick Start page for installing `ww`, verifying Kubernetes access, installing the
  operator, and creating a no-key echo agent, with copyable code windows and a highlighted navigation action.

## [0.23.13] — 2026-05-11

Patch release continuing the finn-driven `untested-api` gap-class sweep on the `ww` CLI — eight more pure helpers gain
coverage across flag parsers, column formatters, GitOps URL parsing, backend-spec resolution, snapshot column rendering,
events-row rendering, and helm-release status defense.

### Fixed

- **ww**: Cover eight previously-untested pure helpers — `--gitsync-map` / `--gitsync-secret` flag parsers, `--persist`
  / `--persist-mount` flag parsers + defaults resolver, `FormatAge` duration column formatter, `ParseGitOps` +
  `splitURLBranch` SSH-aware parsing, `ParseBackendSpecs` / `PrimaryBackend` flag contract, `snapshotEntry.pickField`
  column-render contract, `events.go` row-renderer pure helpers, and `helmReleaseStatus` nil-`Info` defense (#1550) —
  closing more `untested-api` gap-class findings finn surfaced.

### Documentation

- **research**: Refresh the competitive-landscape brief against the current SDK / protocol state.

## [0.23.12] — 2026-05-11

Patch release continuing the finn-driven `untested-pure-helper` gap-class sweep on the `ww` CLI — nine more pure helpers
gain coverage across `tabwriter.go`, `stream.go`, `preflight.go`, and `release.go` — alongside phase 1 of embedding the
`ww` CLI inside the claude backend image and a piper post-twitter skill scaffold.

### Fixed

- **ww**: Cover nine previously-untested pure helpers — `tabwriter.go::{KV,Table}`,
  `stream.go::{FormatTS,FormatTSCompact}`, `preflight.go::{FormatMissingRBAC,InstallRBACRequirements}`, and
  `release.go::{unstructuredSlice,isCRDNotFound,parseRevisionLabel}` — closing the `untested-pure-helper` gap-class
  findings finn surfaced.

### Changed

- **backends/claude**: Embed the `ww` CLI binary in the claude backend image (phase 1 — binary only).

### Agent identity

- **piper**: Scaffold the `post-twitter` skill; dormant until X credentials are wired.

### Documentation

- **research**: Refresh the competitive-landscape brief against the current industry state.

## [0.23.11] — 2026-05-11

Patch release bundling two more finn-driven `untested-cobra-helper` gap-class fills on the `ww` CLI, a nova-style sweep
of code-doc annotations across Helm chart values, Python public symbols, and Go exports, and a clutch of new social
content — two whitepapers and a reorganised `social/` tree.

### Fixed

- **ww**: Cover two more previously-untested cobra helpers — `snapshot.go::parseSnapshot` and `config.go::isSecretKey` —
  closing the gap-class finn surfaced as `untested-cobra-helper` findings.

### Documentation

- **charts**: Add helm-docs-style comments to chart values for downstream `helm-docs` consumption.
- **Python sources**: Add docstrings to public Python symbols across the shared surface.
- **Go sources**: Add godoc comments to undocumented exports.
- **social**: Add the _Three Phases of Agentic AI Adoption in Software Engineering_ and _Anatomy of an Agentic Team_
  whitepapers, and reorganise the `social/` tree by content type (`papers/` + `posts/` subfolders), with a README spec
  for the new layout.

## [0.23.10] — 2026-05-11

Patch release continuing the finn-driven gap-class sweeps on the `ww` CLI — three more rounds of `convention-drift`
Long-help additions plus a small `untested-cobra-helper` test fill — alongside a zora cadence tightening and a new
autonomous autotune loop.

### Fixed

- **ww**: Add `Long` help to the `version`, `config path`, `config list-keys`, and four remaining `agent.go` cobra
  subcommands, continuing the `convention-drift` sweep across the CLI surface.
- **ww**: Cover the previously-untested `status.go::sameOrigin` cobra helper, closing the `untested-cobra-helper`
  gap-class finding finn surfaced.

### Agent identity

- **zora**: Tighten cadences and add an autonomous autotune loop.

## [0.23.9] — 2026-05-10

Patch release closing a finn-surfaced `convention-drift` gap-class sweep — six `ww` cobra subcommand groups were missing
Long help, leaving `ww <cmd> --help` short on the context the rest of the CLI provides.

### Fixed

- **ww**: Add `Long` help to the `continuations`, `jobs`, `tasks`, `triggers`, `heartbeat`, and `validate` cobra
  subcommand groups, restoring help-text parity across the CLI surface.

## [0.23.8] — 2026-05-10

Security-driven patch release retiring two starlette CVEs on the shared Python surface, plus a mechanical
`ruff format` + `ruff check --fix` pass that recovered CI — Python from drift.

### Fixed

- **shared**: Bump `starlette` 0.46.1 → 0.49.1 to retire CVE-2025-62727 (HIGH) and CVE-2025-54121 (MEDIUM) on
  `shared/requirements.txt`.

### Changed

- **Python sources**: Mechanical `ruff format` + `ruff check --fix` pass restoring CI — Python to green after a 0.15.x
  format-style drift.

### Agent identity

- **nova**: Routine self-tidy on 2026-05-10.

## [0.23.7] — 2026-05-10

Patch release bundling a finn-driven test-coverage fill across four pure cobra helpers in the `ww` CLI plus a Piper
identity expansion — the `respond-to-comments` skill is renamed `discuss-comments`, two new `discuss-*` skills land, and
the heartbeat is loosened from 5 to 15 minutes now that voice + filter + Guard 0 have proven stable.

### Fixed

- **ww**: Cover four previously-untested pure cobra helpers — `send.go::extractText`, `update.go` pure helpers,
  `operator.go::cmpDisplay`, and `snapshot.go` pure helpers — closing the gap-class finn surfaced as
  `untested-cobra-helper` findings.

### Agent identity

- **piper**: Rename `respond-to-comments` → `discuss-comments` to align the skill name with the broader `discuss-*`
  family.
- **piper**: Add `discuss-bugs` skill — investigation-driven bug triage with Zora-routed handoff.
- **piper**: Add `discuss-questions` skill, with Guard 0 autonomous moderation.
- **piper**: Loosen the heartbeat from 5 minutes to 15 minutes now that voice, filter, and Guard 0 have proven stable in
  the wild.

## [0.23.6] — 2026-05-10

Patch release recovering CI — Python from a ruff 0.15.x format-style drift, plus the scaffolding of the team's seventh
agent — Piper, the outward-facing voice on GitHub Discussions.

### Fixed

- **ci (Python)**: Reformat `tools/{kubernetes,prometheus}/test_server.py` to ruff 0.15.x's parenthesised multi-line
  assert-message style so `ruff format --check` passes on main again. Pure cosmetic reflow; no semantic change. The
  underlying local-vs-CI ruff drift (CI installs unpinned, dev pinned at 0.6.x) is flagged for a follow-up hygiene pass.

### Changed

- **Python sources**: Mechanical `ruff format` + `ruff check --fix` pass over `harness/`, `backends/`, `tools/`,
  `shared/`, `tests/`. One unfixable F841 (`tools/helm/test_server.py:527`) logged for human review.

### Agent identity

- **piper**: Scaffold the team's seventh agent — outward-facing outreach to GitHub Discussions. Read-only on source,
  5-min heartbeat with a 0–10 substantive-score gate (announcements 9–10, progress 5–8 with a 30-min cooldown, silent
  <5) plus a time-since-last-post multiplier. Voice is informative + warm, no marketing hype, bad news posted plainly.
  Only `call-peer` use is `ask-peer-clarification` for information-only questions — never dispatches work.
- **piper**: Burn in the non-intrusive default — exhaust local reads (peer MEMORY.md, commit bodies, zora's
  decision_log, escalations, source) before pinging peers; three gates (information critical, can't be derived from
  reads, peer is authoritative) must all pass before `ask-peer-clarification`.
- **piper**: Identity files reflect deployed reality — `piper-agent-witwave` GitHub account and PAT live in the
  `piper-claude` Secret; the "creation pending" qualifier is removed. Draft-only fallback still applies defensively if
  the Secret is empty or malformed at runtime.
- **piper**: Burn in three layered spiral-prevention guards before v2 reply support exists — author filter (drop
  self-authored comments unconditionally), mention-required gate (`@piper-agent-witwave` from a non-Piper author), and
  per-thread cooldown (1 reply / 5 min, 3 / UTC day). Activates the moment v2 lands.
- **piper**: Promote v1 from post-only to post + reply with multi-person thread awareness. New `respond-to-comments`
  skill applies the spiral-prevention guards plus an engagement-value filter (acknowledgement-only replies suppressed)
  and posts within the 5-min heartbeat. Voice gains a brevity directive (1–2 short paragraphs default). New "The
  founder" user-context section captures Scott Keith Thomas Jr. (`@skthomasjr`) with name variants.
- **piper**: Match the conversational register on replies — pleasantry → pleasantry, question → factual answer,
  correction → acknowledgement + record-correction, bare @-mention with no question → silence. Burns in the 2026-05-10
  lesson where Piper tacked an unprompted v0.23.5 status update onto a "Welcome to the team!" pleasantry.

## [0.23.5] — 2026-05-10

Security-driven patch release bumping `golang.org/x/net` to v0.53.0 across both Go components (the `ww` CLI and the
operator) to clear a SEC:HIGH advisory in the previous pin. Also includes an evan-coordinated self-tidy and a
zora-identity tweak codifying throughput-tuning authority in the dispatch loop.

### Fixed

- **ww**: Bump `golang.org/x/net` to v0.53.0 to clear a SEC:HIGH advisory affecting the previous pin.
- **operator**: Bump `golang.org/x/net` to v0.53.0 to clear a SEC:HIGH advisory affecting the previous pin.

### Agent identity

- **zora**: Codify throughput-tuning authority in identity files so the manager's cadence-control scope is explicit at
  dispatch time.
- **evan**: Self-tidy refresh (2026-05-09) on the bug-class agent's memory + agent-card surfaces.

## [0.23.4] — 2026-05-09

Patch release completing the `/health` liveness unification on the claude backend (the missing piece from 5e5d5a9b in
v0.23.1) so a slow-booting pod is no longer CrashLoopBackOff'd by kubelet, plus a small batch of documentation
maintenance — three broken internal anchors fixed and a twelfth-pass refresh of the competitive-landscape research note.

### Fixed

- **backends/claude**: `/health` liveness always returns 200 once the process is up, with the body's `status` field
  reflecting readiness (`starting` pre-ready, `ok` once ready). Removes a 503 fork that contradicted the same-file
  contract comment, the CHANGELOG-documented "liveness, always 200 once up" semantic, and the codex/gemini sibling
  implementations brought to that contract by the 5e5d5a9b unification. Symptom: a slow boot exceeding the kubelet
  livenessProbe `initialDelaySeconds` would CrashLoopBackOff a claude backend that should have been merely removed from
  Service endpoints via the separate `/health/ready` probe.

### Documentation

- **research**: Twelfth-pass refresh of `competitive-landscape.md` — pinned three drifted upstream entries (OpenClaw
  v2026.5.7, Microsoft Agent Framework python-1.3.0 / dotnet-1.5.0, OpenHands canonical URL post-rename to
  `OpenHands/OpenHands`). No new competitors added.
- **READMEs**: Three Cat C anchor mismatches fixed in `README.md`, `clients/ww/README.md`, and
  `charts/witwave/README.md` so cross-document links resolve.

## [0.23.3] — 2026-05-09

Recovery patch for the v0.23.2 container build, which failed under the Go 1.26.2 toolchain: `staticcheck@2024.1.1` and
`errcheck@v1.7.0` transitively pulled an old `golang.org/x/tools` whose `internal/tokeninternal/tokeninternal.go`
contains a constant-overflow expression Go 1.26.2 hard-rejects. Bumping both analyzers clears the build and lets the
container release pipeline ship the v0.23.x line cleanly.

### Fixed

- **backends/claude, backends/codex, backends/gemini**: Bump `STATICCHECK_VERSION` 2024.1.1 → 2026.1 and
  `ERRCHECK_VERSION` v1.7.0 → v1.10.0 in each backend's Dockerfile so the analyzer pins compile under Go 1.26.2 (the
  toolchain v0.23.2 introduced).

## [0.23.2] — 2026-05-09

Patch release shipping a chronological-order fix for `ww conversation list --expand`, plus a batch of post-tag
stabilisation: Go toolchain bump to unblock `staticcheck` + `errcheck` on the analyzer surface, test-isolation hardening
across the codex / gemini backends, a `NameError` fix in the kubernetes MCP tool tests, and agent-identity coordination
tweaks for the cross-agent push and dispatch loops.

### Fixed

- **ww**: `ww conversation list --expand` returns sessions in chronological order (oldest → newest) so the rendered tail
  matches the natural reading order.
- **backends (Go toolchain)**: Bump `GO_VERSION` 1.23.4 → 1.26.2 to unblock `staticcheck` + `errcheck` on the analyzer
  surface — the older Go was rejecting type inference patterns the analyzers needed.
- **backends/codex, backends/gemini**: Test-suite stabilisation — codex switches to the stable
  `prometheus_client.Counter.collect()` API instead of reaching into `._value` internals; gemini hardens four
  test-isolation seams (`a2a.server` stub guard tightened to `a2a.server.apps`, prompt-size-cap test isolated from a
  sibling test's `prometheus_client` stub, `_emit_chunk` metric-order test rebased onto the post-enqueue-removal
  surface, `WriterDoneEventRaceTests` patches the correct write helper).
- **tools/kubernetes**: Fix a broken comprehension that raised `NameError` on every test run.

### Agent identity

- **iris, zora**: Close the stuck-commits cascade wedge so cross-agent push delegation fails fast instead of silently
  wedging the team.
- **nova, kira**: Document explicit caller-return semantics for iris-delegated push so siblings know exactly what shape
  they get back.
- **zora, finn**: Polish-tier ladder for finn defaults to depth 3 (not 1) so the gap-fixer surfaces meaningful work on
  first dispatch.
- **evan, zora**: `risk-work` default depth bumps 3 → 5; `dispatch-team` gains a paranoid placeholder check before
  firing evan against risk findings.
- **zora**: `dispatch-team` handles iris's `[release-workflow-pending]` outcome by holding cadence without escalating,
  instead of treating a pending workflow as a failure.

## [0.23.1] — 2026-05-08

Patch release unifying the `/health` endpoint surface across the harness, claude/codex/gemini backends, and MCP servers
so callers can rely on one `/health/live` + `/health/ready` contract. Companion `ww` CLI fixes catch up with the new
endpoint shape, three latent F821 ruff errors in `harness`, `shared`, and `backends/claude` are resolved by promoting
late-imports to module scope, and zora's `dispatch-team` active-liveness probe is wired through to a working
`/.well-known/agent.json` endpoint (the initial `/health` target 404'd on agents that don't expose the harness endpoint
directly).

### Fixed

- **harness, backends, mcp**: Unify the `/health` surface across the harness, the claude/codex/gemini backends, and the
  MCP servers (kubernetes, helm, prometheus). Replaces ad-hoc per-tier endpoints with a consistent `/health/live` +
  `/health/ready` shape so callers (ww, zora's active probe) can rely on one contract.
- **harness, shared, backends/claude**: Resolve F821 ruff errors by promoting late/conditional imports (`threading`,
  `pathlib`, executor forward-ref types) to module scope. Latent in all three modules — the imports were reachable at
  runtime but broke type-hint resolution and tripped CI's ruff gate.
- **ww**: `ww status` probe targets `/health/live` (the bare `/health` returned 404 after the unification above).
- **ww**: `ww send --async` tail hint points at `ww conversation list`, not `show` — `show` is single-session whereas
  the async-send flow wants the multi-session list.

### Agent identity

- **zora**: `dispatch-team` Step 2d uses an active `/.well-known/agent.json` liveness probe instead of `/health` (the
  latter 404'd on agents that don't surface the harness endpoint directly). The probe itself was added in this same
  release window. Companion tweak: kira's `docs-research` cadence floor tightened from 7d → 3d so documentation drift
  gets caught more frequently.
- **finn**: Add the missing `.claude/settings.json` so finn's Claude Code harness boots with a real config instead of
  silently no-opping.

## [0.23.0] — 2026-05-08

Adds non-blocking send mode for `ww send` so callers can queue prompts without waiting on the LLM response, and caps
concurrent SSE streams in `ww conversation list --follow` to keep multi-session tails stable when scope spans many
active sessions. Companion agent-identity work broadens evan's `risk-work` skill from a security-only sweep to all five
risk categories (pulling reliability / performance / observability out of finn so the gap-fixer stays focused on its
orphan/half-landed scope), and lands kira's Check E so `docs-consistency` keeps `TEAM.md` ↔ `agents/bootstrap.md` in
sync as the team topology evolves.

### Added

- **ww**: `ww send --async` returns immediately after the message is queued instead of blocking on the LLM response.
  Useful for fire-and-forget prompts and scripted send-then-tail flows where the caller doesn't need the synchronous
  reply.

### Fixed

- **ww**: `ww conversation list --follow` caps the number of concurrent SSE streams it opens, preventing the
  multi-session tail from exhausting harness/backend connection budgets when scope includes many active sessions.

### Agent identity

- **evan, finn, zora**: `risk-work` broadens from a security-only sweep to all five risk categories — security,
  reliability, performance, observability, supply-chain. Reliability / performance / observability move out of finn's
  scope so the gap-fixer stays focused on orphan TODOs, half-landed refactors, and dangling references; zora's
  `dispatch-team` is aligned to the broadened surface so dispatching reflects the new ownership.
- **kira**: `docs-consistency` gains Check E — `TEAM.md` ↔ `agents/bootstrap.md` roster sync — so the documented team
  topology stays aligned across both files. Companion frontmatter and scope polish reflects the new check.

## [0.22.0] — 2026-05-08

Unbreaks `ww update` for macOS users who installed via Homebrew Cask — the cask path was being misclassified as the
binary/curl install method, so `ww update` was dropping phantom binaries into `/usr/local/bin` instead of running
`brew upgrade ww`. On the agents-bootstrap side, the team's sixth member — **finn** (gap-fixer) — lands as scaffolded
identity + backend wiring, completing the documented six-agent topology.

### Fixed

- **ww**: `ww update` now detects Homebrew Cask installs (executable resolves under `$(brew --prefix)/Caskroom/`) and
  routes through `brew upgrade --cask ww` instead of the binary/curl path. The detector also ignores any stale
  `~/.local/share/ww/install-method` Curl marker when the running binary lives inside Caskroom — earlier curl-script
  installs left that marker behind, and Cask installs that ran on top would inherit it and silently take the wrong
  upgrade path. Bug surfaced when `ww update` shipped phantom binaries into `/usr/local/bin` for brew-cask users instead
  of upgrading the cask in place.

### Agent identity

- **finn**: Sixth team member scaffolded as **gap-fixer** — the agent that fills what's missing across the team's output
  (orphan TODOs, half-landed refactors, dangling references no one else has claimed). Companion
  `agents/finn/backend.yaml` wires finn into the harness backend alongside the other five agents, and
  `agents/bootstrap.md` is updated to cover all six (iris, kira, nova, evan, zora, finn) so onboarding documents match
  the deployed topology.

## [0.21.0] — 2026-05-08

`ww send` and `ww tail` gain auto port-forward parity with the rest of the CLI — agents addressed by name route over a
transparent in-process tunnel instead of requiring a manually-prepared `kubectl port-forward`. Extends the v0.18.0 →
v0.20.0 `ww conversation` port-forward-first posture across the remaining direct-to-agent commands so agents can talk by
name everywhere.

### Added

- **ww**: `ww send` and `ww tail` automatically open a port-forward to the targeted agent's harness, matching the
  auto-pf behaviour `ww conversation` already provides. Agents can now be addressed by name from these commands without
  a pre-existing tunnel; `-n <ns>` / `-A` / `--agent <name>` scoping is unchanged.

## [0.20.0] — 2026-05-08

Completes the `ww conversation` surface with `list --follow` (`-f`) — the multi-session counterpart to v0.19.0's
`show --follow` single-session tail. The conversation triad (one-shot list / single-session live show / multi-session
live tail) is now feature-complete on top of the v0.18.0 port-forward + harness HTTP infrastructure, with no new
dependencies.

### Added

- **ww**: `ww conversation list --follow` (`-f`) renders the current list (table or `--expand` cards) and then
  live-tails every session in scope concurrently. One port-forward per agent — N sessions on an agent multiplex over
  that agent's tunnel — and a single stdout mutex keeps multi-line envelope renders atomic across concurrent streams.
  Each live envelope is prefixed with `↻ <short-id> · <agent> · [HH:MM:SS] role:` so the multiplexed output stays
  legible. Composes with `--expand`, `--full-text`, `-A` (cluster-wide), and `--agent <name>` (filtered). Flag named
  `--follow` to match `kubectl logs -f` / `docker logs -f` / `journalctl -f` and the existing `show --follow`; Ctrl-C is
  the clean success exit.

## [0.19.0] — 2026-05-08

`ww conversation` lands its v2 surface — live-tail (`show --follow`), expanded card-style listing (`list --expand`),
compact timestamps, and short session ids — building on the v1 port-forward + harness HTTP infrastructure with no new
dependencies. A CRITICAL grpc CVE bump (CVE-2026-33186, v1.72.2 → v1.79.3) closes the last open finding from evan's
risk-work sweep, completing the recovery of evan's stashed auto-fix WIP from 2026-05-07. Two shared-harness
`fix(shared):` items clear ruff F821/F841 latent bugs in `hook_events.py`.

### Added

- **ww**: `ww conversation show --follow` (`-f`) live-tails a session via the backend container's
  `/api/sessions/<id>/stream` SSE endpoint (port 8001, distinct from the harness 8000 used for one-shot list/show).
  After the historical transcript renders, the CLI prints `─── live ───` and streams new envelopes in the same
  `[ts] role:` shape so the conversation reads continuously; Ctrl-C exits cleanly. Required infra:
  `portforward.OpenPort` parameterised to target either harness or backend, and `conversation.StreamSession` parsing
  typed `StreamEnvelope` frames (with keepalive + `stream.overrun` terminal handling).
- **ww**: `ww conversation list --expand` renders each session as a box-drawn card (short-id, agent, turn count, start →
  last, source) with per-entry text wrapped at ~76 cols and capped at 500 chars (`--full-text` disables the cap).
  Default list output now shows the first 8 chars of the session UUID (git short-SHA convention); full id is still
  available in `--expand` card headers and `show` output.
- **ww**: Compact timestamp formatters strip the harness's `+00:00` / microseconds noise (UTC is the only timezone the
  harness emits): `FormatTS` → `YYYY-MM-DD HH:MM:SS` for `show` and the default list table, `FormatTSCompact` →
  `HH:MM:SS` for `--expand` where the date already lives in the card header. Parse failures pass through unchanged.

### Fixed

- **security**: `google.golang.org/grpc` bumped v1.72.2 → v1.79.3 (clients/ww), closing CVE-2026-33186 (CRITICAL).
  Companion `go mod tidy` bumps for transitive consistency: `golang.org/x/oauth2` v0.30.0 → v0.34.0,
  `google.golang.org/genproto/googleapis/rpc` → 20251202230838-ff82c1b0f217, `google.golang.org/protobuf` v1.36.8 →
  v1.36.10. Completes evan's risk-work WIP that stalled on 2026-05-07 mid-bump and was recovered locally where the Go
  1.26.2 toolchain is current.
- **shared**: `_done_cb` annotation in `shared/hook_events.py` rebound from the never-bound bare
  `concurrent.futures.Future[Any]` to the existing `_cf_mod.Future[Any]` alias — `from __future__ import annotations`
  had been masking a latent NameError that any `typing.get_type_hints(_done_cb)` caller would have hit. Closes ruff
  F821.
- **shared**: Dead `last_exc` variable removed from `_post_once_to` in `shared/hook_events.py` (vestigial from an
  earlier retry-logging design that never landed). Retry control flow unchanged — the outer `except Exception as exc`
  remains the sole post-loop consumer. Closes ruff F841.

## [0.18.0] — 2026-05-08

`ww` gains a `conversation` subcommand for cross-agent transcript inspection. Two `fix:` items close the v0.17.0
fallout: a Go 1.26 gofmt regression that had `CI — ww CLI` red across three commits, and the `go-git/v5` CVE bump evan
flagged on his risk-work sweep. The release-pipeline gate itself hardens — iris's pre-flight CI check now covers every
workflow's latest run on `main` (not just runs on the release commit), and the post-tag watch is mandatory with a
structured `[release-workflow-failed]` reply zora's Priority 1 handler can act on.

### Added

- **ww**: `ww conversation list` and `ww conversation show <session-id>` read agent LLM-exchange transcripts (prompt,
  reply, tool calls, model, tokens, trace ids) from each agent's harness `/conversations` endpoint. CLI handles
  port-forward + auth plumbing automatically — one command fans out across every agent in scope. `-n <ns>`/`-A` follow
  the DESIGN.md NS-1/NS-2/NS-3 convention; `--agent <name>` filters within scope; `--since` and `--limit` for `list`,
  `--format=text|json|jsonl` for `show`. Per-agent bearer comes from the `<agent>-claude` Secret's
  `CONVERSATIONS_AUTH_TOKEN` key with `--token` override. Partial-failure UX: an unreachable agent doesn't kill the list
  — what's reachable renders, with a footer naming the unreachable ones and why. Three new internal packages back this:
  `internal/portforward/` (generic SPDY port-forward helper, ephemeral local port via `:0`), `internal/conversation/`
  (typed client over `/conversations`, fan-out, `DiscoverAgents` via `dynamic.NewForConfig`), and `cmd/conversation.go`
  (cobra wiring). Live-tail via SSE deferred to v2 (lives on backend port 8001, not harness 8000; second port-forward
  shape required).

### Fixed

- **ww**: `shellQuoteSingle`'s doc comment rewritten to describe the POSIX escape sequence in prose rather than embed
  the backtick-wrapped `'\''` literal, which Go 1.26's gofmt was silently normalising to a Unicode smart-quote on every
  run. Closes the recurring `CI — ww CLI` failure that had been red since `e1e97efe` (2026-05-07's `ww update`
  self-upgrade landing). Function body, behaviour, and tests unchanged.
- **security**: `github.com/go-git/go-git/v5` bumped 5.13.2 → 5.18.0 (clients/ww), closing the three Medium CVEs evan
  flagged on his risk-work sweep. Completes evan's auto-fix WIP that stalled mid-bump on 2026-05-08 before `go mod tidy`
  ran.

### Agent identity

- **iris**: release skill's pre-flight CI gate switches from "runs on this commit" to "latest run per workflow on
  `main`" — path-filtered workflows (e.g. `CI — ww CLI` only fires on `clients/ww/**` changes) no longer slip past the
  gate when the release commit doesn't trigger them. Closes the exact gap that let v0.17.0 cut on 2026-05-07 while
  `CI — ww CLI` had been red for ~1h45m on three earlier commits. Step 11 watch-to-conclusion is now mandatory: the
  skill DOES NOT return success until every release workflow concludes; failures surface as a structured
  `[release-workflow-failed]` reply listing what published and what didn't. Iris does not auto-retry the failed
  workflow, doesn't delete the tag, doesn't try to recover — the contract is iris-reports / zora-decides.
- **zora**: "Never leave a broken build" load-bearing principle added right after Team Mission. Frames red CI as the
  team's fire alarm, outranking every other priority including critical CVEs. Author-agnostic; human commits and peer
  commits get the same treatment. Especially after a release: red CI on the next commit poisons every subsequent release
  until fixed. Extended to cover post-tag release-workflow failures with the same fire-alarm framing — freeze cadence,
  redirect to fix, surface visibly via `escalations.md`, hold release-warranted. `dispatch-team` Priority 1 grows two
  new handlers: red-CI auto-dispatches evan with the failing-job log + breaking commit (capped at 2 attempts before hard
  escalation), and failed- release-workflow branches into transient-infra (ask iris to `gh run rerun --failed`) vs
  real-bug-in-source (dispatch evan, then either iris re-run or cut vX.Y.Z+1). Stuck-peer escalation becomes
  time-bounded (T+30m iris auto-recovery, T+1h `[needs-human]` surface, T+2h pause-mode) so a single stalled peer can't
  freeze the team for hours waiting on human resolution. CI check now iterates `git rev-list v<latest>..origin/main` so
  failures on any commit since the latest tag count as red, regardless of how many commits later HEAD is.

## [0.17.0] — 2026-05-07

`ww` gains a working self-upgrade path for standalone-binary installs and an "all containers, prefixed" default for
`ww agent logs`, closing two of the more visible operator-experience gaps from the v0.16 line. A METRICS_ENABLED=0
startup crash in the harness is fixed. Backend images (codex, gemini) pick up Node.js 20 to match claude, and evan's
bug-work scope expands across the day-one toolchain table to cover charts and the dashboard.

### Added

- **ww**: `ww update` now upgrades standalone-binary installs by reusing the canonical `install.sh` pipeline with
  `--install-dir` pointed at the running binary's directory. Pre-flight write check surfaces "re-run with sudo" up-front
  rather than failing mid-download. Closes the gap surfaced after v0.16.5 cut autonomously and `ww update` reported "no
  automatic upgrade path." `ww update --check` now says "To upgrade: ww update" instead of pointing at the tarball URL.
- **ww**: `ww agent logs <name>` defaults to tailing every container in the pod (harness + backend(s) + git-sync)
  interleaved, with each line prefixed by `[<container>]`. Multi-pod scope (rollouts, `--pod` ambiguous match) widens
  the prefix to `[<short-pod-suffix>/<container>]`. `-c <name>` still filters to one container; unknown container names
  surface a clean error listing what's available.
- **backends**: `backends/codex` and `backends/gemini` Dockerfiles install Node.js 20 (claude already had it), so any
  agent on any backend can run any skill that shells out to a Node-based CLI.

### Fixed

- **harness**: METRICS_ENABLED=0 startup no longer crashes with `NameError: name 'app' is not defined`. The lifespan
  closure's metrics-disabled branch was calling `app.router.add_route(...)` at lines 2227/2232; the closure parameter
  had been renamed to `_app` upstream (commit `e027504c`, #924) but the rename wasn't propagated. Both call sites now
  reference `_app`. Surfaced by evan's bug-work depth=5 sweep at 2026-05-07T18:00Z.

### Agent identity

- **evan**: bug-work day-one toolchain table picks up `charts/witwave` and `charts/witwave-operator` (helm lint +
  yamllint) and `clients/dashboard` (vue-tsc --noEmit + hadolint). The "deferred to v2" list shrinks to empty — every
  section the repo currently exposes is now in evan's scan scope. `all-day-one` count: 14 → 17. Chart and dashboard
  toolchains were already installed in the backend images; the SKILL was just stale.
- **nova, kira, zora**: findings-marker schema adopted team-wide. `code-verify`, `docs-verify`, and `docs-consistency`
  SKILLs now end findings bullets with `[pending]` / `[flagged: <reason>]` / `[fixed: <SHA>]` markers — matching the
  schema evan was already using. Zora's `dispatch-team` per-peer backlog adapter reframes accordingly: marker count on
  sections dated 2026-05-07 onward, narrative-bullet count only on legacy pre-cutoff sections. Once legacy sections age
  out, the adapter degenerates to a uniform marker count across all peers.

## [0.16.5] — 2026-05-07

Closes the harness `-32603` empty-response bug that had been corrupting synchronous JSON-RPC replies whenever an agent
returned a clean empty string (observed across evan empty-sweeps, kira docs work, and nova code-cleanup runs since
2026-05-06). Alongside that fix, zora's policy gains depth-control across evan / nova / kira so polish-tier sweeps
escalate when cheap-pass tiers exhaust, and the team picks up its first per-agent self-maintenance loop with `self-tidy`
running once per 24h on a staggered cron across all five peers.

### Fixed

- **harness**: `executor.execute()` now always enqueues a final A2A Message event, defaulting to empty text on empty
  responses, so the SDK's `DefaultRequestHandler` always has at least one event to serialise into a JSON-RPC success
  reply. Previously the `if _response:` gate skipped the enqueue on empty-string returns, leaving the SDK with nothing
  to serialise and surfacing `-32603` to the caller despite a clean execute. Adds `logger.exception()` at the catch site
  so future exception paths capture full traceback instead of getting re-raised opaquely. Scoped to
  `harness/executor.py:execute()`; zero changes to backend interfaces or A2A SDK pinning.

### Documentation

- **repo-wide**: markdownlint + prettier auto-fixes applied across the docs surface — formatting-only, no semantic
  changes.

### Agent identity

- **team**: `self-tidy` skill added — each peer runs a byte-identical per-agent daily self-maintenance pass
  (memory-index consolidation, cross-agent awareness refresh, agent-card drift check) on a staggered 24h cron (iris
  02:15Z, kira 06:30Z, nova 10:45Z, evan 15:00Z, zora 19:15Z). Sibling to zora's `team-tidy` (cross-cutting, zora-only);
  self-tidy is per-agent and stays scoped to the running agent's own files. Cap: 1 commit per agent per day, ≤50 lines,
  atomic. Same iris-delegated push contract every other peer follows.
- **zora**: direct `gh` CI-read auth wired (`zora-claude` secret now carries `GITHUB_TOKEN` + `GITHUB_USER`) so the
  release-warranted check can call `gh run list --branch main` directly instead of inferring CI state from indirect
  signals; gh writes stay strictly in iris's lane. Per-peer backlog adapter added to step 2c so evan's canonical
  `[pending]` / `[flagged: ...]` schema and nova/kira's narrative tally formats both feed the dispatch tiebreaker.
- **zora**: polish-tier depth control extended across the team. evan dispatches now pick the depth tier per cadence
  (reset to 3 on fresh source, advance 3 → 5 → 7 → 9 after 2 consecutive 0/0/0 runs, hold otherwise) and the polish
  baseline raises 3 → 5 once cheap-pass ground has been swept. Same advance/reset/hold logic extends to nova
  (`code-cleanup` ↔ `code-document` one-shot) and kira (`docs-cleanup` ↔ `docs-research` one-shot) — deeper passes
  fire as one-shots when the default tier returns 0/0/0 for 2 consecutive runs on stable source, then flip back. State
  tracked per skill in `team_state.md`. Dispatches/hour cap raised 5 → 8 to give the tightened cadence floors breathing
  room. kira `docs-cleanup` cadence floor tightens 24h → 6h so docs sweeps keep pace with the team's commit rate.

## [0.16.4] — 2026-05-07

Operator-regen drift closure plus a team-cadence sharpening. The operator-section probes (evan bug-work, evan risk-work,
nova code-cleanup) had been dirtying the working tree on every run because two committed files diverged from
current-toolchain output, costing the team's dispatch cap on motion-without-progress; this release commits the
regenerated state and adds a working-tree restore around the probes themselves so the failure can't recur. Alongside
that, zora's release policy moves from a count-based daily cap to a velocity-driven release-warranted check, and evan's
bug-class scope broadens to pull pyflakes-class items off nova's deferred shelf.

### Fixed

- **operator**: `operator/go.mod` and `operator/config/webhook/manifests.yaml` regenerated against the pinned
  `controller-gen v0.18.0` + `go mod tidy` so committed state matches what the toolchain produces. `go-logr/logr` flips
  from indirect to direct (operator imports it directly); webhook manifest is a pure YAML emitter-style flip
  (sequence-dash positioning, 81/81 lines, zero semantic change). Combined with the probe-restore wrap below, this
  closes the regen-drift escalation that had been burning the team's dispatch cap.

### Agent identity

- **team**: each peer's CLAUDE.md gains a one-line cross-reference to `.agents/self/TEAM.md` right after the team-roster
  block, so the canonical roster + topology + future-roles overview is reachable from any agent's identity file instead
  of being browse-only. Unblocks zora's `team-tidy` skill, which had been escalation-blocked for ~10h on this exact gap.
- **evan**: bug-work probes wrap operator-section toolchain runs (`make manifests`, `go vet`, `go mod` side effects)
  with `git checkout -- operator/go.mod operator/config/` after capturing drift results, so probe residue can't dirty
  the tree for the next peer. Bug-class scope broadens from `B` to `B,F` — pyflakes (F821 undefined-name, F811
  redefinition, F823 referenced-before-assignment, F841 unused-variable-masking-typos) IS bug-class and was being
  filtered out incorrectly; items currently sitting in nova's deferred-findings memory under those rules will flow
  through evan's fix-bar pipeline on his next sweep.
- **zora**: release policy switches from a count-based cap (max 4/day, ≥1h floor) to a velocity-driven release-warranted
  check that fires when weighted commits since the latest tag cross 3.0 (feat=2.0, fix=1.0, docs=0.5,
  chore/style/refactor/test=0.25) or a `fix(security):` / critical commit lands. Hygiene floor of 15 min between
  releases prevents same-tick double-fires; hard cap of 20 releases/day acts as a runaway guard only. Heartbeat tightens
  30 min → 15 min so release latency tracks the new velocity policy. Cadence floors for evan bug-work (6h → 3h), evan
  risk-work (12h → 8h), and nova code-cleanup (12h → 8h) tighten alongside so the produce side keeps pace with the
  publish side.

## [0.16.3] — 2026-05-07

CI hygiene patch. Single fix from evan's bug-work catalogue (SP-4 unused-loop-var rename) in the install-script CI
workflow.

### Fixed

- **workflows**: unused loop variable `i` renamed to `_i` in the install-script CI poll loop
  (`.github/workflows/ci-install-script.yml`) — count-controlled retry idiom; pure variable rename in unused position,
  no runtime effect.

## [0.16.2] — 2026-05-07

Documentation-only release. The team's self-agent roster gets a written reference: a new `TEAM.md` under `.agents/self/`
captures the five-agent team's shape (iris, kira, nova, evan, zora) and adds a priority-ordered "Proposed future
members" section sketching where the team might grow next (CTO, devops, security, architecture, testing, PR roles).

### Agent identity

- **team**: `.agents/self/TEAM.md` added — overview of the current five-agent team plus a proposed-future-members
  roster, reordered by priority and extended over the cycle to cover CTO, devops, security, architecture, testing, and
  PR roles.

## [0.16.1] — 2026-05-07

Manager arc: the team gains a fifth self-agent — **zora** — owning team-level dispatching and release cadence across the
four domain-specialist peers (iris, kira, nova, evan). She decides what work happens when, not how; domain decisions
stay with each peer. Initial responsibilities are scaffolded alongside a `team-tidy` skill that keeps the team's shared
posture (CLAUDE.md tone, skill structure, memory hygiene) consistent over time. A nova-driven `ruff format` pass sweeps
the Python sources clean as a baseline.

### Agent identity

- **zora**: scaffolded as the team's fifth self-agent and team coordinator — identity (CLAUDE.md + agent-card),
  team-participation files, and the standard cross-agent skills inherited from the family. Sits above the four domain
  peers and dispatches work via A2A; all peers' CLAUDE.md updated to describe her as a valid caller into their skills,
  not a gate — direct user invocation still works.
- **zora/team-tidy**: skill scaffolded to keep team-shared posture consistent — anchored on the team's overall mission
  so consistency-and-self-improvement work targets the right shape rather than drifting into local cleanup.

### Changed

- **python-sources**: `ruff format` pass across the Python tree (nova) — formatting-only, no behaviour change.

## [0.16.0] — 2026-05-07

Risk-work arc: evan gains a sibling skill to `bug-work` — `risk-work` — owning identification of exploitable risk
(vulnerabilities, secrets, supply-chain exposure) across the project's source. The supporting backend image work
installs the day-one risk toolchain (govulncheck, gosec, pip-audit, bandit, gitleaks, trivy) uniformly across all three
backends so risk scans run identically wherever evan is deployed. Bug-work itself gains a safe-pattern catalogue so
auto-fixes at depth ≥ 5 land on rails for shapes the team has already vetted.

### Agent identity

- **evan/risk-work**: scaffolded as bug-work's sibling — same 7-step shape (scan, validate, reason as set, decide
  fix-vs-flag, fix with web-search + scoped local tests, log, push + CI watch through iris), same depth dial, same
  batch-revert posture on red CI, but the discovery target is exploitable risk rather than correctness defects. Skill
  scaffold landed first, then the procedural body was fleshed out and the toolchain installed across the backends.
- **evan/bug-work**: safe-pattern catalogue added to the step-4 fix-bar — a curated list of "we've seen this shape
  before and the canonical fix is X" patterns (B904 chain-vs-suppress, B010 setattr-with-constant, SC2086
  quote-GOOS/GOARCH, SC2034 rename-unused-loop-var, etc.) so depth-≥5 auto-fixes land on rails instead of asking the web
  every time. Companion changes: `bug-sweep` renamed to `bug-work` (leading the verb pair); depth reframed as "how hard
  we hunt"; Step 0.5 added to recover stuck commits before scanning; CLAUDE.md + SKILL.md refactor for clarity;
  fix-forward semantics on local-test + CI failures; candidate list persists to memory immediately after scan so a
  crashed pass doesn't lose its work.

### Added

- **backends**: evan's risk-work toolchain installed uniformly in claude, codex, and gemini images — govulncheck and
  gosec (Go), pip-audit and bandit (Python), gitleaks (secret scanning), trivy (filesystem + container scans). Sits
  alongside the existing bug-work toolchain from v0.15.0. Echo image stays minimal by design.

### Fixed

Tonight's evan bug-work pass closed five issues; the morning's pass had landed a larger batch which was reverted on red
CI (`ci-ww.yml`) and then reworked and re-landed cleanly.

- **harness**: TimeoutError chain suppressed when raising QueueFull (B904). Int-conversion context suppressed when
  re-raising as ValueError (B904). Unused loop variable renamed in `_resolve_host_to_private_check` (SC2034-equivalent
  Python).
- **tools/helm**: bash `pipefail` SHELL set so pipe failures surface in helm-chart make targets (DL4006). idna failure
  chained when raising HelmError on bad hostname (B904).
- **tools/kubernetes**: loop variables bound at iteration time in apply-commit lambda (closure-over-loop-var).
- **backends/claude**: NameError context suppressed when re-raising primary lifespan error (B904); setattr-with-constant
  replaced with direct attribute assignment (B010, two call sites).
- **backends/codex**: TimeoutExpired chained when raising ShellTimeoutError (B904).
- **backends/gemini**: setattr-with-constant replaced with direct attribute assignment (B010).
- **workflows**: GOOS/GOARCH expansion quoted in `ci-ww.yml` build step (SC2086). Option parsing terminated in the SLSA
  archive `sha256sum` invocation (SC2035) so subjects with leading dashes are preserved verbatim. SC2016 false positive
  suppressed on the bcrypt htpasswd single-quoted literal in `release.yaml`. SC2034 silenced on the unused index in the
  fake-dist server poll loop (rename to `_`).

### Changed

- **ww**: embedded operator chart values resynced to track upstream chart edits.

## [0.15.0] — 2026-05-06

Bug-discovery arc: evan lands as the team's fourth self-agent — owner of correctness across the project's source code,
with a single-pass `bug-sweep` skill and a 17-section addressable namespace driving where he looks. The supporting
backend image work installs his Go and Python bug-class toolchains uniformly across all three backends so day-one
analyzer + local-test gating runs identically wherever evan is deployed.

### Agent identity

- **evan**: scaffolded as the team's fourth self-agent — owner of correctness bug discovery across Python, Go,
  Dockerfile, shell, and GitHub Actions sources. Identity (CLAUDE.md + agent-card), team-participation files, and four
  skills: `bug-sweep` (the work skill — 7-step end-to-end pass: scan, validate through an eight-concern
  intentional-design gauntlet, reason as set, decide fix-vs-flag, fix with web-search + scoped local tests, log, push +
  CI watch — both delegated to iris), and `git-identity` / `call-peer` / `discover-peers` (cross-agent skills copied
  byte-identical from kira/nova). 17-section addressable namespace (14 day-one + 3 deferred to v2) with aliases
  (`all-python`, `all-go`, `all-backends`, `all-tools`, `all-day-one`); single 1-10 depth dial gates both step-2
  validation rigor and step-4 fix-bar stringency, with auto-fix only at depth ≥ 5. Batch-revert posture on red CI per
  trunk-based-dev contract. State lives in commits + memory only — no GitHub issues, no labels, no funnel.
- **evan → iris CI-watch delegation**: framed as team contract, not workaround. Step 7 of `bug-sweep` delegates BOTH the
  push AND the CI watch to iris in one `call-peer` round-trip; iris reports back, evan acts on the report. The same
  shape applies to future siblings (security, test-coverage, etc.) — iris owns all git/GitHub authority for the team,
  every other agent stays focused on its domain.

### Added

- **backends**: evan's bug-class Go toolchain installed uniformly in claude, codex, and gemini images — staticcheck
  2024.1.1, errcheck v1.7.0, ineffassign v0.1.0, controller-gen v0.16.5. Consolidates with nova's existing goimports
  install into a single five-binary `go install` step. ~80–120 MB image growth per backend. Echo image stays minimal by
  design.
- **backends**: evan's bug-class Python toolchain installed alongside nova's existing ruff/yamllint pip block in claude,
  codex, and gemini images — pytest 8.3.3, pytest-asyncio 0.24.0, httpx 0.27.2, python-kubernetes 31.0.0. Lets
  `bug-sweep` run scoped local tests (`pytest <section>/`) without per-workflow ad-hoc installs.

### Fixed

- **evan**: closed five gaps from end-to-end review of the v1 design — pytest tooling missing from backend images;
  GitHub PAT placeholder breaks Step 7 CI watch (now delegated to iris by design); controller-gen drift command
  regenerated only deepcopy files, replaced with `make manifests` + git diff against `operator/config/crd/bases/`;
  memory directory parent might not exist on first run, added idempotent `mkdir -p`; CLAUDE.md / SKILL.md cd-path
  inconsistency for scoped tests aligned around `<checkout>/<section>`.

### Changed

- **tooling**: `.prettierignore` excludes controller-gen output (`charts/witwave-operator/crds/`,
  `operator/config/crd/bases/`, `operator/config/rbac/role.yaml`) so nova's code-format passes stop reverting it on
  every run. `.editorconfig` pins shell-script indent to 2-space (matching 7 of 9 `scripts/*.sh`); `install.sh` and
  `smoke-ww-agent.sh` keep 4-space via per-pattern override to avoid ~700-line reflows.

## [0.14.0] — 2026-05-06

Team-participation arc: every self-agent gains generic A2A peer discovery + call skills, nova lands as the third
self-agent (full code-hygiene identity, skill set, and image toolchain), kira's docs surface grows a research skill plus
Tier 2 verify/consistency checks under a new `docs-cleanup` orchestrator, and `ww` gains a `--harness-env` flag that
closes the cross-agent timeout trap where `TASK_TIMEOUT_SECONDS` set on the backend alone left the harness retrying
long-running relays at the 5-minute default.

### Added

- **ww**: `--harness-env <KEY>=<VALUE>` flag on `ww agent create`, repeatable. Stamps `spec.env[]` on the harness
  container so settings like `TASK_TIMEOUT_SECONDS` propagate to the harness's A2A relay timeout (read at startup as
  `max(TASK_TIMEOUT_SECONDS - 10, 10)`). Closes the failure mode where iris→kira docs-scan calls retried mid-relay and
  produced duplicate work because the backend's longer timeout wasn't matched on the harness.
- **backends**: nova's code-hygiene toolchain installed uniformly in claude, codex, and gemini images — Go 1.23.4 +
  goimports, shfmt 3.10.0, shellcheck, actionlint 1.7.7, hadolint 2.12.0, helm 3.16.3, ruff 0.6.9, yamllint 1.35.1. ~205
  MB image growth (Go is the bulk). Echo image stays minimal by design.

### Agent identity

- **self (iris/kira/nova)**: `discover-peers` + `call-peer` skills installed identically across all three agents.
  Discovery introspects Kubernetes-injected service env vars (`<NAME>_SERVICE_HOST` / `_PORT`) and probes each
  candidate's `/.well-known/agent.json`, caching confirmed peers as reference-type memory entries. `call-peer` builds a
  JSON-RPC `message/send` envelope (blocking, fresh messageId), POSTs, parses the response, and surfaces typed
  diagnostics for the standard `-32001` / `-32603` error codes. Same-namespace only; pod restart required to discover
  newly-deployed siblings.
- **nova**: scaffolded as the team's third self-agent — owner of the CODE-INTERNAL COMPREHENSION SUBSTRATE (code
  comments as the way future contributors and AI agents understand existing behaviour). Identity (CLAUDE.md +
  agent-card), team-participation files (HEARTBEAT, backend.yaml, git-identity, git-push), and four code-domain skills:
  `code-format` (Tier 1 mechanical fixes via ruff/gofmt/goimports/shfmt/prettier/yamllint), `code-verify` (Tier 2 read-
  only cross-checks of docstrings vs signatures, godoc vs exported APIs, helm-docs comments vs template usage),
  `code-document` (Tier 3 grounded authoring of missing comments — every claim must be derivable from the code body),
  and `code-cleanup` (Tier 1 + 2 orchestrator that delegates publishing to iris). Helm chart values are first-class
  targets. Infrastructure source — Dockerfiles, shell scripts, GitHub Actions workflows — is included in scope across
  every nova skill (shellcheck, hadolint, actionlint added to Tier 1; new check classes D/E/F in Tier 2; new authoring
  categories D/E/F in Tier 3).
- **kira**: `docs-research` skill — the only docs skill that reaches outside the repo. Targets forward-looking Cat C
  documents (`competitive-landscape.md`, `product-vision.md`, `architecture.md`); verifies existing URLs still resolve,
  rechecks named projects for currency, and adds up to 3 new competitive-landscape entries per run. Citation discipline
  is the load-bearing rule: every new claim must end with `(source: <url>, accessed YYYY-MM-DD)`. One commit per target
  doc; push delegated to iris.
- **kira**: Tier 2 docs skills — `docs-verify` cross-checks Cat C documentation against current code (broken paths,
  command examples, env-var names, version numbers, identifiers); `docs-consistency` runs cross-doc agreement checks on
  Cat C (versions, subproject README claims, command-surface drift). Both are memory-log only — every finding is an
  "update doc OR fix code" judgment call. New `docs-cleanup` orchestrator runs the full sweep (Tier 1 on all `.md`, Tier
  2 on Cat C only) and delegates publishing to iris. `docs-validate` extended to wrap long YAML frontmatter
  `description:` strings via folded scalars so the `printWidth=120` convention applies there too. `docs-scan` updated to
  delegate push via `call-peer` instead of invoking `git-push` directly — kira-commits / iris-pushes contract now
  applied uniformly across both orchestrators.
- **kira**: identity tightened around the docs-as-communication-channel framing (CLAUDE.md + agent-card). Two distinct
  audiences — humans reading the repo and downstream automated processes that ingest forward-looking docs for feature
  discovery / planning — make drift propagate downstream as wrong code changes or miscalibrated planning. Stale claims
  dropped from agent-card ("every 6 hours" cadence, "pushes the batch herself", AGENTS.md/CLAUDE.md autofix scope).
- **iris**: identity tightened around the choke-point framing (CLAUDE.md + agent-card). Iris is the team's choke point
  for everything reaching `origin/main` — every commit by any agent reaches origin through iris; every tagged release
  happens through iris. The kira-commits / iris-pushes `call-peer` pattern is now made explicit in Responsibility 2.
  agent-card rewritten end-to-end to drop the source-tree-as-user-capability framing (iris owns it autonomously as a
  precondition).

### Documentation

- **bootstrap**: Step 5 added for nova deploy — mirrors Step 4 (kira) with nova-specific env-var lifts
  (`GITHUB_TOKEN_NOVA` / `GITHUB_USER_NOVA`) and the paired `TASK_TIMEOUT_SECONDS=2700` on both harness and backend.
  Goal section, env-vars block, verify section ("three rows"), and teardown updated for the three-agent footprint.

## [0.13.0] — 2026-05-02

A2A correctness arc: blocking-call response truncation fixed, hook-event field corrected, and the streaming capability
flag flipped to match actual wire behaviour. `ww` gains `--backend-env` for non-secret per-backend env overrides and
stops clobbering operator-overlaid `backend.yaml` when a harness gitMapping owns the `.witwave/` directory. Kira's
identity scaffolding fleshes out with Tier 1 docs skills, a liveness heartbeat, and a docs-drift sibling.

### Added

- **ww**: `--backend-env <backend>:<KEY>=<VALUE>` flag on `ww agent create`, repeatable per (backend, KEY). Stamps
  `spec.backends[].env[]` directly on the CR for non-sensitive tunables (`TASK_TIMEOUT_SECONDS`, `LOG_LEVEL`,
  `STREAM_CHUNK_TIMEOUT_SECONDS`, etc.). Secret values continue to flow through `--auth-set` /
  `--backend-secret-from-env`.

### Fixed

- **backends**: blocking A2A callers (`message/send` with `configuration.blocking=true`) now receive the agent's
  complete output instead of just the first turn. The A2A SDK's result aggregator treats every `Message` event as
  terminal; reverting per-chunk emission to a single final aggregated `Message` closes the truncation. (Follow-up to
  #430 — chunks belong on `TaskStatusUpdateEvent` if streaming consumers ever appear.)
- **backends**: hook-decision events send the backend id (`claude` / `codex` / `gemini`) instead of the named-agent name
  (`iris` / `kira`). Ends the per-turn `hook.decision SSE drop: unknown agent` warning that was swallowing real policy
  signal. (#1149)
- **backends**: `AgentCapabilities.streaming=False` on agent cards, matching the post-revert wire behaviour. Clients
  querying `/.well-known/agent.json` no longer see a dishonest streaming flag. Per-chunk dashboard drill-down is
  unaffected — that's a separate `_sess_stream` SSE channel.
- **ww**: `ww agent create` skips emitting the inline `backend.yaml` Config entry when a harness-level `gitMapping`
  covers `/home/agent/.witwave/`. Closes the rsync-delete race where the gitSync sidecar's `rsync --delete` was wiping
  operator-overlaid `backend.yaml` on every pull (the operator subPath wasn't present in the synced source, so rsync
  removed it from the dest).

### Changed

- **codex**: removed the now-unreachable drop-recovery machinery in `executor.py` (`_attempted_texts`, `_emitted_texts`,
  `_stream_state`, and the elif drop-recovery branches in `execute()`). With per-chunk emission gone, the chunk-enqueue
  timeout it guarded against can no longer fire. ~60 lines deleted.

### Agent identity

- **kira**: Tier 1 docs skills scaffolded and named in CLAUDE.md; `docs-validate` skill fixed to invoke
  `markdownlint-cli` (no `2`). Doc-categories section added to CLAUDE.md.
- **kira**: 30-minute heartbeat added — liveness signal only, does not trigger a docs scan.
- **kira**: docs-drift sibling identity scaffolded.
- **iris**: identity updated to the `iris-agent-witwave` GitHub account; commits now link to that account via the
  matching verified email.
- **iris**: Memory section added to CLAUDE.md — file-based memory on the shared workspace volume with private
  (`agents/iris/`) and team (top-level) namespaces.
- **iris**: `backend.yaml` lands as gitSync content under `.agents/self/iris/.witwave/`. Pairs with the `ww` fix above
  so iris no longer needs an operator-overlaid `backend.yaml`.

### Documentation

- **bootstrap**: kira deploy step added.

## [0.12.0] — 2026-05-01

This release is entirely infrastructure for the self-agent ecosystem: iris gains a full release-captain build-out, all
three self-agents are promoted to `bypassPermissions` mode, and gh CLI lands in the backend images to enable
workflow-query tooling. No changes to `ww`, the operator, the harness, or backend runtime behaviour.

### Added

- **backends**: gh CLI installed in claude, codex, and gemini images, enabling `gh run list` and other GitHub workflow
  queries inside backend containers. (`GH_PROMPT_DISABLED=1` was already set in anticipation of this install.)

### Agent identity

- **iris**: `release` skill added — covers CI-green verification, bump inference (patch / minor / explicit), CHANGELOG
  generation, annotated tagging, and tag push. Pre-1.0 semantics: `feat:` and breaking markers fold into a minor bump;
  major is reserved for the deliberate `v1.0.0` cut. Skill gaps closed post-scaffolding: git identity is pinned before
  the changelog commit, stable-tag filtering ensures beta-cycle commits aren't lost on graduation, and
  `BREAKING CHANGE:` entries fold into **Changed** without a bold prefix in the pre-1.0 period.
- **iris**: `git-push` skill added — narrow push-only skill for publishing already-made commits to `main`; handles the
  sibling- pushed-first race via pull-rebase + one retry.
- **iris**: Identity contract moved to CLAUDE.md — `user.name` / `user.email` declared per-agent; all skills read values
  from the agent's own prose so the same skill files work across iris, nova, and kira without modification.
- **iris**: Skills reorganised into folder-per-skill layout; HTTP triggers removed in favour of A2A as the exclusive
  inter-agent channel. `sync-source` renamed to `git-sync-source`.
- **iris**: A2A agent-card rewritten to surface actual capabilities (git plumber + release captain); Responsibilities
  section added to CLAUDE.md scoping the role explicitly.
- **agents**: iris, nova, and kira promoted to `bypassPermissions` mode; `settings.json` trimmed to just `defaultMode`.

### Documentation

- Changelog backfilled from commit history covering v0.8.2 through v0.11.16.

## [0.11.16] — 2026-04-30

Single-fix patch on `ww agent upgrade` — closes a regression where the merge-patch path dropped existing
`spec.backends[].image.repository` on push, causing admission to reject with "spec.backends[i].image .repository:
Required value."

### Fixed

- **ww**: `ww agent upgrade` swaps merge-patch → Update with the full CR. Preserves every field on the existing CR
  (image repository, env, volumes, gitMappings) so admission sees a valid object. Verified end-to-end on a live cluster.

## [0.11.15] — 2026-04-30

First-cut `ww agent upgrade` — in-place image-tag rollout for a single agent.

### Added

- **ww**: `ww agent upgrade <name>` patches `spec.image.tag` (harness) and each `spec.backends[].image.tag` on the
  WitwaveAgent CR. The operator reconciles the change and rolls the Deployment via the standard kubelet rollout —
  pod-local PVC state survives the roll. Tag resolution priority: `--tag X` (everything) > `--harness-tag` /
  `--backend-tag <name>=X` (per-container) > brewed CLI's own version. Idempotent fast-path (no-op when tags match),
  `--force` to roll regardless, dev-build guard, unknown-backend guard, event dump on timeout. Bulk paths (`--all`,
  `--all-namespaces`) deferred to a follow-up.

## [0.11.14] — 2026-04-30

Operator + cross-component fixes to make hook events flow correctly out of the box. Metrics enabled by default on every
operator- provisioned agent; new CLI opt-out.

### Added

- **shared**: `shared/env.py` boundary-safe parsers — `parse_bool_env`, `parse_int_env`, `parse_float_env`. Centralises
  env-var parsing across every Python component with case- insensitive truthy/falsy vocabulary and loud-failure on
  typos.
- **ww**: `--no-metrics` flag on `ww agent create` to opt out of the new default-on metrics posture.

### Changed

- **operator**: `MetricsSpec.Enabled` switched to `*bool` with kubebuilder default `true`. Every agent the operator
  provisions has metrics on by default; explicit `false` opts out.
- **charts**: `metrics.enabled` default flipped from `false` → `true` for chart-installed agents (parity with operator
  default).
- **All Python components**: 16 sites of `bool(os.environ.get(...))` replaced with `parse_bool_env`. The classic Python
  anti-pattern (`bool("false") == True`) had been silently flipping `METRICS_ENABLED=false` to `True` everywhere.

### Fixed

- **operator**: `HARNESS_EVENTS_URL` stamped at the metrics port when metrics are enabled, matching where the
  `/internal/events/{hook-decision,publish}` routes actually live under #924's NetworkPolicy posture. Combined with the
  bool-parse fix above, closes the silent 404 storm on hook event delivery (#1781).
- **shared**: import env helpers via top-level name (`from env import ...`) — the previous `from shared.env import` form
  was broken because `shared/` is on PYTHONPATH but isn't a package.

### Agent identity

- **iris**: heartbeat probes CLAUDE.md loading via name-substitution check (HEARTBEAT_OK <name>); throttled back to
  every 30 minutes after validation runs.

## [0.11.13] — 2026-04-30

Operator-side auto-mint of the per-agent internal-state Secret. Closes the silent-401 storm on
`/internal/events/hook-decision` POSTs.

### Added

- **operator**: per-agent `<agent>-internal` Secret reconciler. Always-on, idempotent — generates a random
  `HOOK_EVENTS_AUTH_TOKEN` on first reconcile (32 bytes, base64url-encoded; ~43 chars) and preserves it across
  subsequent reconciles. Cascade-deletes with the agent via OwnerReference. Stamped as envFrom on harness + every
  backend container so user-supplied envFrom can still override on key collision.

### Agent identity

- **iris**: per-minute liveness heartbeat (later throttled back to `*/30 * * * *` after validation).

### Chore

- **ww**: gofmt fix on `gitsync_auth_env_test.go`.

## [0.11.12] — 2026-04-30

`--gitsync*` flag-family closure on `ww agent create`. Six commits flatten the long-form into a single coherent prefix.

### Added

- **ww**: `--gitsync-from-env <USER_VAR>:<PASS_VAR>` (then renamed to `--gitsync-secret-from-env`) — lifts shell vars
  into a per-agent gitSync credential Secret. Closes the .env-driven gap for private-repo gitSync.
- **ww**: `--timeout` default on `ww agent create` bumped from 2m → 5m; recent CR + pod events dumped on timeout for
  actionable diagnostics.

### Changed

- **ww**: `--gitops` → `--gitsync-bundle`. Convention-driven sugar joins the `--gitsync*` family.
- **ww**: `--gitmap` → `--gitsync-map`. The flag is conceptually subordinate to a `--gitsync` entry; the prefix reflects
  the hierarchy.
- **ww**: `--gitsync-from-env` → `--gitsync-secret-from-env`. Symmetry with the backend half.
- **ww**: `--secret-from-env` → `--backend-secret-from-env`. Target type now in the flag name; mirrors
  `--gitsync-secret-from-env`.

## [0.11.11] — 2026-04-30

Backend-credential Secret cleanup on agent delete is default-on. CLI sugar additions for gitOps and persistence.

### Added

- **ww**: `--gitops` short-form on `ww agent create` — convention- driven sugar over `--gitsync` + N `--gitmap` entries.
- **ww**: `--with-persistence` on `ww agent create` — fans out type- derived persistence defaults to every declared
  backend without per-backend `--persist` enumeration.
- **ww**: `--delete-backend-secrets` on `ww agent delete` (later flipped to default-on in this same release).

### Changed

- **ww**: backend-Secret cleanup on `ww agent delete` is default-on; `--keep-backend-secrets` opts out. Same label-gated
  safety regardless: Secrets without `app.kubernetes.io/managed-by=ww` are never touched.
- **ww**: minted backend Secret name drops the `-credentials` suffix. `iris-claude` instead of
  `iris-claude-credentials`. The suffix oversold what's in the Secret (arbitrary envFrom material via
  `--secret-from-env`, not just credentials) and collided visually with the operator-side
  `<agent>-<backend>-backend- credentials` naming for the inline-CR path.

### Documentation

- **bootstrap**: collapsed to a down-and-dirty single-claude walkthrough; dropped EKS / future-platform commitments;
  added a Tear-it-down section.

### Agent identity

- **iris**: removed stale `.echo-1` / `.echo-2` scaffolds.

## [0.11.10] — 2026-04-30

`--auth-from-env` rename and rename-form support on `ww agent create`.

### Added

- **ww**: `--auth-from-env` accepts the `<SRC>:<DEST>` rename form so agent-suffixed shell vars
  (`GITHUB_TOKEN_IRIS:GITHUB_TOKEN`) can land as stable in-container names without the per-agent prefix.

### Changed

- **ww**: `--auth-from-env` → `--secret-from-env` family-wide. Multi- line accumulation supported (multiple
  `--secret-from-env` entries for the same backend merge into one resolver). Old name dropped without deprecation
  aliases.

### Documentation

- **bootstrap**: switched to `--secret-from-env`; demonstrates combined and multi-line forms; uses real iris-suffixed
  shell vars from `.env`.

## [0.11.9] — 2026-04-29

### Added

- **ww**: `--persist-mount <name>=<subpath>:<mountpath>` on `ww agent create` for explicit PVC mount overrides.
  Replace-on- presence — any `--persist-mount` for a backend takes ownership of its full mount list (type-derived
  defaults are skipped).

### Documentation

- **bootstrap**: `--persist-mount` documented in the long-hand block.

## [0.11.8] — 2026-04-29

### Added

- **ww**: `--persist` accepts echo backends with a symbolic `memory` mount.

### Documentation

- **bootstrap**: `--persist` flag documented in Step 3 with a forward-looking concrete example.

## [0.11.7] — 2026-04-29

### Added

- **ww**: `--persist <name>=<size>[@<storage-class>]` on `ww agent create` — provisions a per-backend PVC for
  session/memory persistence (`<agent>-<backend>-data`).

### Agent identity

- **iris**: dropped `sync-test.md` scratch file.

## [0.11.6] — 2026-04-29

### Added

- **ww**: `--gitsync` / `--gitmap` / `--gitsync-secret` long-form on `ww agent create` for per-entry gitSync wiring.

### Fixed

- **operator**: probe selection now uses image-repo-basename instead of the backend's Name. `--backend echo-1:echo` no
  longer gets stamped with `/health/start` 404s because the operator now recognises the underlying image type.

### Documentation

- **bootstrap**: Step 3 focuses on iris with two backends; Step 2 prose aligned with the two-volume workspace.

## [0.11.5] — 2026-04-29

Reverts the workspace-mount-on-harness change from v0.11.4.

### Reverted

- **operator**: workspace volumes no longer mounted on the harness container; they stay scoped to backend containers
  only.

### Fixed

- **echo**: `WORKDIR /home/agent/workspace` to match the other backends.

## [0.11.4] — 2026-04-29

### Fixed

- **harness**: `/health/ready` probe falls back to `/health` on 404 — supports echo backends that don't expose the
  readiness endpoint.
- **operator**: stamped workspace mounts onto the harness container (subsequently reverted in v0.11.5).

## [0.11.3] — 2026-04-29

### Added

- **ww**: `--workspace` flag on `ww agent create` — bind to a WitwaveWorkspace at creation time; equivalent to a
  follow-up `ww workspace bind`.

### Documentation

- **bootstrap**: documented `--gitsync` / `--gitmap` explicit form; collapsed Step 3 to one command per agent.

### Chore

- **scripts**: embedded-chart drift check uses `--checksum` so mtime drift no longer false-positives.

## [0.11.2] — 2026-04-28

Multi-arch container images.

### Added

- **release**: container-image builds now produce `linux/amd64` + `linux/arm64` manifest lists. Fixes
  `ImagePullBackOff: no matching manifest for linux/arm64/v8` on Apple Silicon and AWS Graviton clusters.

### Fixed

- **ww**: `ww operator status` enumerates all three CRDs (WitwaveAgent, WitwavePrompt, WitwaveWorkspace).

### Changed

- **layout**: `.agents/active/` renamed to `.agents/self/`.

## [0.11.1] — 2026-04-28

### Added

- **ww**: `--create-namespace` on `ww operator install` — provisions the target namespace if missing. Parity with
  `ww workspace create` and `ww agent create`.

### Documentation

- **bootstrap**: Step 3 walkthrough for iris/nova/kira deployment.

## [0.11.0] — 2026-04-28

WitwaveWorkspace CRD — shared per-namespace volume / Secret / ConfigMap envelope every agent in the namespace can bind
to. End- to-end CRD + operator + admission webhook + chart + CLI surface.

### Added

- **operator**: WitwaveWorkspace CRD with shared volumes (PVC- backed, per-volume access modes), Secret projection
  (existingSecret pass-through), and ConfigMap-backed file rendering. Inverted-index `Status.BoundAgents` reconciled
  from each agent's `Spec.WorkspaceRefs`. Same-namespace binding only in v1alpha1.
- **operator**: WitwaveWorkspace admission webhook gates the CR shape.
- **operator**: WitwaveAgent grows `Spec.WorkspaceRefs[]` for declarative binding; agent reconcile stamps workspace
  volumes / envFrom / configmap mounts on the pod.
- **operator**: ReadWriteOncePod accepted alongside ReadWriteOnce for single-node clusters (Docker Desktop, kind).
- **ww**: `ww workspace { create, list, get, status, delete, bind, unbind }` subcommand tree.
- **charts**: WitwaveWorkspace CRD bundled, RBAC + webhook config plumbed.

### Fixed

- **mcp-kubernetes**: retry `apply()` and `delete()` on 401 token rotation (#1816, #1817).
- **operator**: defensive logging on workspace-watch and agent-list errors (#1805, #1806, #1810).
- **harness**: re-raise TimeoutError from `_bounded` so callers see timeouts (#1769); consolidate `bus.send` cleanup
  (#1770).
- **codex**: log when `_release_mcp_stack` matches no tracked stack (#1781) or refcount underflows (#1780).
- **shared**: best-effort graceful shutdown for the metrics-server thread (#1818).
- **ww**: guard `Target()` against nil context value (#1800); close stdout pipe when `cmd.Start` fails in credential
  fill (#1796).

### Documentation

- **bootstrap**: new `docs/bootstrap.md` for self-hosting witwave on witwave (the witwave-self ecosystem).
- **workflow**: project formally adopts trunk-based development — commits land directly on main, no feature branches,
  fix or revert immediately on break.

### Agent identity

- **layout**: scaffolded iris, nova, kira directories under `.agents/self/`.

### Skills

- Bake intentional-design checklist into bug-discover, bug-refine, risk, and gap skills.

## [0.10.0] — 2026-04-27

Cycle 17 — operator hardening, observability finishing touches, and security follow-ups across MCP tools, backends, and
the dashboard.

### Added

- **operator**: per-agent PrometheusRule reconciled from chart defaults (#1746); sibling NetworkPolicies for MCP tools +
  dashboard (#1743); dashboard Ingress + auth annotations (#1741); MCPToolSpec CRD additions for security parity
  (#1737); CORS\_\* env stamped on harness (#1748).
- **harness**: `_MD_CACHE_EVICTIONS` exported as Prometheus counter (#1747); `PROMPT_ENV_MAX_BYTES` cap on resolved
  bodies (#1744).

### Fixed

- **gemini**: hot-reload revision gauge registered (#1751); `_pre_tool_use_gate` engine signature corrected (#1724);
  per- logger + tool-audit metrics wired (#1755, #1756); MAX_SESSIONS clamped to ≥1 (#1718); chunk metric increment
  timing (#1721); skip tool_duration histogram for prefix-paired AFC frames (#1727); inbound prompt UTF-8 byte length
  capped at A2A entry (#1730); `mcp_command_args_safe` invoked on MCP config load (#1734).
- **claude**: `mcp_command_args_safe` on MCP config load (#1734); `task_last_success_timestamp` gated on budget_exceeded
  (#1729).
- **codex**: `mcp_command_args_safe` on MCP config load (#1734); `MCP_CONFIG_PATH` realpath allow-list (#1731); observe
  `backend_sqlite_task_store_lock_wait_seconds` (#1753).
- **operator**: backend three-probe model with echo carve-out (#1719); paginate WitwavePromptReconciler ConfigMap GC
  List (#1726) and applyBackendPVCs cleanup List (#1725); paginatedList routed through APIReader (#1738);
  renew_failures_total increment on involuntary lease loss (#1739); MCP tool Service exposes the metrics port (#1722);
  hardened security context + SA fields stamped on MCP tool pods (#1737).
- **harness**: stream-read `/validate` bodies via shared cap helper (#1736); cross-backend timestamp normalised in
  `/trace` proxy sort (#1728).
- **ww**: timeout-free `streamHC` for SSE callers (#1733); prerequisite Service Get bounded by `--timeout` (#1720).
- **mcp**: `/info.features.read_only` honours per-tool env (#1759).
- **dashboard**: CSV formula injection neutralised in `csvEscape` (#1732).
- **shared**: schedule session_stream sweeper + LRU-cap registry insertion (#1735).
- **charts**: unified mcp-tools Service/Pod metrics gate (#1723).

### Documentation

- **harness**: drop unimplemented `POST /triggers/{name}/run` references (#1745).
- **supply-chain**: per-artifact verification recipes + supply-chain overview (#1598 item 5).
- **echo**: three-probe health model added to intentional-non-scope list.
- **layout**: removed stale Docker Compose references — K8s-only platform now.

## [0.9.6] — 2026-04-27

### Added

- **release**: SLSA L3 provenance for ww binaries + embedded chart bridge (#1598 items 2 + 4).

## [0.9.5] — 2026-04-27

### Fixed

- **CI**: docker login alongside helm login for cosign chart sign (#1598).

## [0.9.4] — 2026-04-27

### Added

- **release**: cosign-sign published Helm charts (#1598 item 3).

## [0.9.3] — 2026-04-27

### Fixed

- **CI**: buildx setup before image build — provenance / SBOM prereq (#1598).

## [0.9.2] — 2026-04-27

### Added

- **release**: SLSA provenance + SBOM emitted for every published image (#1598).

### Changed

- **CI**: prettier + markdownlint enforced on changed `*.md` files (#1481). MD018 disabled — `#NNN` issue refs at line
  start are project convention.

## [0.9.1] — 2026-04-27

### Changed

- **ww**: goreleaser `brews` migrated to `homebrew_casks` (#1446).

### Fixed

- **CI**: guard `release.extra_files` against parent-dir globs.

## [0.9.0] — 2026-04-27

Cycle 16 — universal curl installer, supply-chain hardening groundwork, ~15 metrics + observability fixes across harness
/ backends / operator, and broad test coverage across cli / operator / dashboard / shared.

### Added

- **ww**: universal curl installer + post-release validation. Polish parity with uv-class installers — existing-install
  detection, SHELL-aware advice, post-install version exec. `-o yaml` output parity for snapshot commands (#1707).
- **charts**: #1416 harness env vars plumbed as first-class values (#1691); MCP tool env vars documented under
  `mcpTools.*` (#1692).
- **backends**: metric superset parity placeholders for codex + gemini (#1687); `/health/start` probe added to claude /
  codex / gemini (#1686).

### Fixed

- **operator**: admission gates for existingSecret presence + inline- creds RBAC (#1683, #1685); cross-watches gated
  between WitwaveAgent and WitwavePrompt (#1684); WitwavePrompt ConfigMap name made injective (#1676);
  `ObservedGeneration` preserved when spec moves mid-retry (#1677).
- **harness**: `tasks.py` duration metric covers `resolve_prompt_env` (#1675); YAML-list `continues-after` parses
  correctly (#1689).
- **mcp**: surface describe events-fetch failures to callers (#1680); redact stdin-delivered helm values in error
  messages (#1681).
- **codex / gemini**: bound `/mcp` body size on the wire (#1673, #1674).
- **dashboard**: clear stale terminal-failed error on `open()` (#1702); always overwrite `error.value` on
  terminal-failed state.
- **charts**: CI checks for alert↔runbook coverage (#1698) and PrometheusRule metric names declared in source (#1682);
  operator namespace Role monitoring verbs gated on `metrics.enabled` (#1678).
- **CI**: skip SBOM and sign steps in snapshot-install goreleaser run; fetch tag history; shellcheck/gofmt drift in
  curl-installer follow-ups.

### Changed

- **terminology**: replaced "agenda item" with "prompt" across documentation and wire contracts.

## [0.8.2] — 2026-04-26

Brand asset refresh; dashboard test fixture follow-on.

### Added

- **brand**: witwave brand assets bundled.

### Fixed

- **dashboard**: always overwrite `error.value` on terminal-failed state. Test timeouts adjusted after #1605 + #1615
  production changes.

### Documentation

- **README**: witwave logo embedded (then reverted to non-inline form) at top; FUNDING.yml updated with Buy Me A Coffee
  sponsorship link.

## [0.8.1] — 2026-04-26

Patch release re-shipping the artifacts that v0.8.0 missed.

v0.8.0's ww binaries + helm charts published cleanly, but the container-image matrix (harness, claude, codex, gemini,
echo, dashboard, operator, mcp-\*, git-sync) was cancelled when the dashboard's Docker build failed on a vue-tsc type
error. The fake stream fixture in `clients/dashboard/tests/unit/timelineStore.spec.ts` was missing the
`droppedEventCount` and `parseFailureCount` fields added in cycle-1 #1606 and cycle-2 #1634; local vitest runs would
have caught it, but vitest was sandbox-blocked on the cycle commits and the production code still type-checked.

This release re-runs the full matrix with the fixture fixed.

### Fixed

- **dashboard test fixture**: FakeStream now satisfies `UseEventStreamReturn` (#1606 + #1634 follow-on); unblocks Docker
  build → unblocks container image publication.

### Changed

- **CI**: removed `gitleaks-action` workflow. `gitleaks-action@v2` requires a paid license for organization repos;
  relying on GitHub-native secret scanning + Push Protection instead. See commit message for re-enablement options.

### Chore

- **ww**: gofmt re-sort imports after the v0.8.0 module-path rewrite (alphabetical: `spf13/cobra` now sorts before
  `witwave-ai/witwave/...`).

## [0.8.0] — 2026-04-26

First release under the `witwave-ai/witwave` org (transferred from `skthomasjr/witwave` on 2026-04-26). New container
images and helm charts are published to `ghcr.io/witwave-ai/...`; the old GHCR namespace becomes a frozen archive going
forward. The `ww` CLI's update-check now points at the new Releases endpoint. Existing clones can follow GitHub's HTTP
redirect or run `git remote set-url origin git@github.com:witwave-ai/witwave.git`.

Autonomous bug + risk cycle output (74 commits, 73 closed issues across 10 cycles). The work was driven by the develop
skill's discover/refine/approve/implement loop applied to bugs and risks only — gaps, features, and docs phases were
skipped per the session scope. Issues are grouped by component below; full provenance lives in the GitHub issue history
(#1599–#1672) and linked commits.

### Security

- **mcp-prometheus: refuse to start on cloud-metadata bearer** (#1652). When `PROMETHEUS_BEARER_TOKEN` is set and
  `PROMETHEUS_URL` host resolves to a cloud-provider instance- metadata endpoint (169.254.169.254, fd00:ec2::254,
  metadata.google.internal, metadata.azure.com), startup raises — regardless of `PROMETHEUS_ALLOW_PLAINTEXT_BEARER`. The
  metadata IP is privileged regardless of transport.
- **mcp-prometheus: response body redacted from non-200 logs** (#1639). The WARN log emitted on upstream errors no
  longer includes the body snippet (kept the status code + byte count).
- **mcp-helm: validate `--repo` URL scheme on install / upgrade / diff** (#1638, #1664). Rejects file://, javascript://,
  and other non-http(s) schemes that previously slipped past `_reject_flag_like()`.
- **mcp-helm: port-aware allowlist matching for `repo_add`** (#1601). `MCP_HELM_REPO_URL_ALLOWLIST` entries now match
  hostname AND port; bare-host entries match URLs with no explicit port or the scheme's default; `host:port` entries
  require exact match. Backwards compatible for default-port URLs.
- **mcp tool Dockerfiles: HEALTHCHECK switched to exec-form** (#1651). Removes shell-form interpolation of `MCP_PORT` so
  a malformed env value can't escape into a shell context.
- **mcp shared: `mcp-prometheus` added to `DEFAULT_MCP_ALLOWED_COMMANDS`** (#1640). All three shipped MCP tool binaries
  are now accepted by default.
- **claude / shared: bearer-token decode hardened to `errors='strict'`** (#1617). Invalid UTF-8 returns a JSON-RPC 400
  instead of silently coalescing distinct token byte sequences onto the same caller-identity hash.
- **codex: Chromium `--no-sandbox` is now opt-in** via `CHROMIUM_SANDBOX_DISABLED` (#1619). Default-off so the host
  kernel sandbox runs where supported.
- **codex: prompt-size cap (`MAX_PROMPT_BYTES`, default 10 MiB)** rejecting oversized requests at the executor entry
  (#1620). Mirrored in echo at 1 MiB (#1650).
- **claude: `/mcp` body cap streams instead of trusting `Content-Length`** (#1609). New `MCP_MAX_BODY_BYTES` env
  (default 4 MiB); HTTP 413 on overflow with a clean JSON-RPC error body.
- **gemini: `MCP_CONFIG_PATH` realpath-prefix validation** (#1610). Refuses to load files outside
  `MCP_CONFIG_PATH_ALLOWED_PREFIX` (default `/home/agent/`).
- **shared: `prompt_env.substitutions_total` cardinality bounded** (#1668). Dropped the attacker-influenced `var` label;
  per-var detail still surfaces in WARN logs at miss/deny time.
- **ww: `ww config get` redacts secret-key values** (#1646). Mirrors the existing `ww config set` posture; secret keys
  print `<redacted>` to stdout.
- **ww: config Save uses atomic CreateTemp + Chmod + Rename** (#1607, #1654). Bearer tokens never observable at any mode
  but 0o600.
- **ww: brew/git/gh shell-outs wrapped in `context.WithTimeout`** (#1616) and credential env vars stripped before exec
  via `sanitizeShellEnv`.
- **operator: ClusterRole RBAC split for Secrets read/write** resolved at the canonical source (#1613). Scripts/ added a
  drift-check helper.
- **operator: validating webhook emits warning on inline-credential use under restrictive RBAC posture** (#1623).
- **operator chart: monitoring CRDs RBAC gated on `metrics.enabled`** (#1659). No verbs requested when metrics are off.
- **operator chart: CRDs carry `helm.sh/resource-policy: keep` at the canonical source** (#1614, #1647).
  `helm uninstall` no longer cascades into deletion of WitwaveAgent / WitwavePrompt CRs.
- **agent chart: image digest pinning supported across harness, backends, and dashboard** (#1612, #1665). Mirrors the
  existing mcpTools digest support.
- **agent chart: ingress basic-auth empty-htpasswd validation** (#1626). Render fails fast when auth is enabled but no
  htpasswd / existingSecret is provided.

### Reliability

- **claude / codex / gemini: split `/health` (liveness, always 200 once up) from `/health/ready` (readiness, 503 while
  initialising or boot-degraded)** (#1608 claude, #1672 codex + gemini). Operators using K8s readinessProbe should point
  at `/health/ready`; `/health` remains for livenessProbe. The agent chart's backend probes were updated to match
  (cycle-10 follow-on); the harness's own readiness gate now probes backend `/health/ready` (cycle-9 follow-on).
- **claude: bounded sub-app lifespan shutdown wait** via `SUB_APP_SHUTDOWN_TIMEOUT_SEC` (default 10s) (#1618). Faulty
  sub-apps no longer stall pod termination.
- **claude: `SqliteTaskStore.close()` race fix** (#1649). New `_closing` sentinel prevents `_get_conn()` from spawning a
  fresh connection during teardown.
- **codex: `MAX_SESSIONS=0` no longer crashes the LRU eviction path** (#1629). Clamped at parse to `max(1, ...)`.
- **codex: MCP watcher normal-exit drops readiness** (#1630). Mirrors the claude readiness-gate pattern.
- **codex: `backend_task_last_success_timestamp_seconds` gated on `_budget_exceeded`** (#1662). Budget-exceeded
  responses no longer mask as success in the gauge.
- **codex: token-budget check uses `total_tokens` only** (#1600). Dropped the `output_tokens` fallback that
  under-counted prompt
  - cached-input tokens against the cap.
- **gemini: `max_tokens=0` no longer raises ZeroDivisionError** (#1602). Tightened guard to also require `> 0`.
- **gemini: API-key rotation atomicity** (#1621). Build-then-swap the new client; on failure preserve the
  previously-cached one so transient credential blips don't take down the backend.
- **gemini: session-history file unlink races eviction** (#1611). Backpressure branch waits up to
  `_EVICT_BACKPRESSURE_SAVE_WAIT_SEC` (default 30s) on the per- session done-event before attempting removal.
- **gemini: history-save force-split fallback** (#1622). When no safe AFC boundary exists in the trim window, cut at the
  earliest user-role entry so long mid-AFC tails can't keep the on-disk file oversized indefinitely.
- **harness: `A2A_SESSION_CONTEXT_CACHE_MAX` validated at module import** (#1648). Non-int or `< 1` fails fast with a
  CRITICAL log.
- **harness: rate-limited WARN on background-task shed path** (#1644). New `BACKGROUND_SHED_LOG_WINDOW_SEC` (default
  10s). Sustained drops surface to operator logs without spam.
- **harness: shed-path `coro.close()` exception logged** (#1670). Replaces the silent `except Exception: pass` with a
  WARN that carries the source label and exception repr.
- **operator: `teardownDisabledAgent` cleans up NetworkPolicy and MCP tool resources** (#1635). Disabling a CR no longer
  leaves stale per-agent NetworkPolicy + mcp-`<tool>` Deployments to await OwnerRef GC.
- **operator: dashboard reconcile no longer panics when dashboard is disabled** (#1660). Nil-guard mirrors the ConfigMap
  and Service paths.
- **operator: WitwavePrompt status retry refreshes `reconciledGeneration` after re-Get** (#1636). Fixes a one-cycle
  observedGeneration lag under concurrent spec writes + 409 conflicts.
- **operator: List operations on cleanup paths now paginate** via a shared `paginatedList` helper (`Limit=500` +
  `Continue`) (#1656). Bounds memory + apiserver load on namespaces with many agents.
- **operator: leader-election timing flags validated at startup** (#1657). `leaseDuration > renewDeadline > retryPeriod`
  is now enforced; misconfigs fail fast instead of silently deadlocking election.
- **operator: validating webhook enforces port upper bounds** (#1669). Rejects configs whose metrics-port reservation
  (port + 1000) would overflow 1..65535.
- **operator: webhook validating `failurePolicy=Ignore` by default** (#1624). Mutating webhook stays on Fail (sets
  reconciler-critical defaults). New `webhooks.validatingFailurePolicy` knob.
- **operator: WitwaveAgent CR teardown adds IsControlledBy guard on shared manifest CM** (#1599). Foreign-owned CMs are
  no longer deleted on the empty-membership branch.
- **agent chart: `podDisruptionBudget.enabled=true` by default** (#1625). Safe at `replicaCount=1` (PDB delays drain
  until reschedule); behaviour change for new installs only.
- **operator chart: same default flip on `podDisruptionBudget.enabled`** (#1628). Operator is a control- plane
  component; PDBs ship on by default.
- **operator chart: `probes.startup.failureThreshold` raised 30 → 60** (#1627, #1642). 600s grace covers cold-start
  under leader-election + multi-replica.
- **agent chart: MCP tool pods get default resource requests/limits** (#1658). Removes the QoS=BestEffort eviction
  vulnerability.
- **dashboard: SSE reconnect floor raised 50ms → 500ms** (#1615); added `MIN_TRUSTED_SERVER_RETRY_MS=100` for clamping
  suspect server `retry:` hints; added `MAX_CONSECUTIVE_FAILURES=30` terminal-failed state with reset on `open()`
  (#1653).
- **dashboard: SSE per-stream rate cap (200 evts/sec)** with reactive `droppedEventCount` for observability (#1606); new
  `parseFailureCount` for malformed payload visibility (#1634).
- **dashboard: `useChat.loadHistory` clears `loadingHistory` on every error path** (#1633). No more stuck spinner.
- **dashboard: `ConversationsView` watchers stopped in `teardownStream`** (#1661). Fixes stale callbacks firing on
  closed streams during filter switches.
- **dashboard: `seenIds` Set bounded at 5000 entries with LRU-ish eviction** (#1605). Long-lived tabs no longer
  accumulate unbounded dedup state.
- **dashboard: DOMPurify link-rel hook regression test** (#1604). Pins `target=_blank rel="noopener noreferrer"`
  injection so a future `removeAllHooks()` call breaks CI rather than silently regressing the phishing mitigation.
- **ww: TUI modal lifecycle context plumbed end-to-end** (#1631). Quitting the app cancels in-flight
  create/delete/scaffold/send operations instead of letting them dangle.
- **ww: TUI log-tail goroutines drained on cycle/close** (#1663). No more apiserver-connection leak on rapid 'c' presses
  in aggregate mode.
- **ww: TUI send-modal stale-frame draw fix** (#1603). Active flag gates the QueueUpdateDraw.
- **ww: TUI preflight banner rendered before agent list per DESIGN.md KC-4** (#1632).
- **ww: helm-uninstall ctx-cancel observability** (#1655). 60s background waiter logs WARN if the in-flight goroutine
  doesn't settle.

### Changed (operator-visible behaviour)

- **K8s probes: agent + operator chart defaults updated** (see Reliability above). Operators with custom values that
  override `readinessProbe.path` to `/health` should review — the chart now ships `/health/ready` for backends
  post-#1672.
- **`acknowledgeInsecureInline=true` triggers an admission warning in the operator validating webhook** (#1623). Guides
  operators toward `existingSecret` references when the operator's `secretsWrite` permissions are restricted.
- **`MCP_HELM_REPO_URL_ALLOWLIST` entry format extended** (#1601). `host:port` syntax now supported; bare-host entries
  unchanged but only match default-port URLs.

### Operator chart RBAC drift checker

`scripts/check-rbac-drift.sh` added (#1613). Renders the chart ClusterRole and diffs against
`operator/config/rbac/role.yaml` to catch future regressions of the `secretsWrite` split. Manual + CI ready (`chmod +x`
once after pulling).

### Removed

(none)

## [0.7.19] — 2026-04-25

### Added

- **`ww tui` create modal — Secret KEY fields are now combo boxes** with autocomplete suggesting the conventional
  env-var names for the selected backend type. Type "AUTH" → AWS*\*, AZURE*\*, GITHUB_AUTH_TOKEN-style entries surface;
  pick one with ↑↓+Enter or keep typing for a custom name (the suggestions are hints, never constraints). Powered by
  tview's `InputField.SetAutocompleteFunc`; substring match (case- insensitive) so credential names sharing common stems
  are easy to discover.

  Built-in catalog ships per backend type:

  - claude: `ANTHROPIC_API_KEY`, `CLAUDE_CODE_OAUTH_TOKEN`, `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`,
    `AWS_SESSION_TOKEN`, `AWS_REGION`
  - codex: `OPENAI_API_KEY`, `OPENAI_ORG`, `OPENAI_PROJECT`, `AZURE_OPENAI_API_KEY`, `AZURE_OPENAI_ENDPOINT`,
    `AZURE_OPENAI_API_VERSION`
  - gemini: `GEMINI_API_KEY`, `GOOGLE_API_KEY`, `GOOGLE_APPLICATION_CREDENTIALS`, `GOOGLE_CLOUD_PROJECT`
  - echo: nothing — popup stays hidden for the no-credentials hello-world case

  User-extensible via a new `[tui.expected_env_vars]` block in `~/.witwave/config.toml`:

  ```toml
  [tui.expected_env_vars]
  claude = ["MY_CUSTOM_VAR"]
  codex  = ["MY_OPENAI_PROXY_KEY"]
  ```

  Custom entries MERGE with the built-ins (dedup + sort) — adding your own can never accidentally drop the canonical
  suggestions. Removing built-ins isn't supported yet (block-list semantics would land if anyone asks).

  The autocomplete closure reads `cf.state.backend` on every keystroke, so changing the Backend dropdown updates
  suggestions live for any unfocused KEY field — no rebuild required.

## [0.7.18] — 2026-04-25

### Changed

- **`ww tui` create modal — secrets section is now dynamic per-pair** (Phase 2). Replaces the multi-line "Backend
  secrets" TextArea with a list of editable `KEY` / `VALUE` InputField pairs that grows and shrinks at runtime. Two new
  buttons in the form's button row:

  - `[+ Secret]` appends a fresh empty pair and lands focus on the new pair's KEY field so the user can type
    immediately.
  - `[− Secret]` pops the trailing pair (no-op when empty) and lands focus on the previous pair's VALUE — or on the
    Existing-Secret field when no pairs remain.

  Per-pair removal beyond the tail: clear the row's KEY field; empty-KEY pairs are silently skipped at submit so the row
  effectively disappears from the resulting Secret without needing a tear-down.

  Values prefixed with `$` still mean "lift from shell env" — same convention Phase 1 introduced. Empty value on a
  non-empty KEY is refused with a hint pointing at "clear the KEY to drop the pair."

  On-disk shape changes: `[tui.create_defaults]` now stores secrets as a TOML list of `"KEY=VALUE"` strings
  (`secrets = ["KEY1=value1", "KEY2=$VAR"]`) instead of the previous `secrets_block` multi-line string. Hand-editable,
  round-trips cleanly. Existing config files with the old `secrets_block` key are silently ignored on read; users get
  fresh state on next successful create. Pre-1.0; the migration surface is small.

  resolveTUISecrets walks the typed pairs slice instead of parsing a string block. Submit-time validation: empty KEY =
  drop, empty value on non-empty KEY = error, duplicate KEY across pairs = error, `$VAR` = env-lift with actionable
  error on unset.

## [0.7.17] — 2026-04-25

### Changed

- **`ww tui` create modal — secrets redesign (Phase 1)**. Dropped the Auth mode dropdown entirely. The four old modes
  (none / profile / from-env / existing-secret / set-inline) collapse into two more focused fields:

  - **Existing Secret name (optional)** — single-line. When set, references a pre-built K8s Secret as-is (verified,
    never modified). Wins over the secrets block.
  - **Backend secrets** — multi-line. One `KEY=VALUE` per line. Values prefixed with `$` are lifted from the shell
    environment at submit time; everything else is literal. Empty in both fields = no Secret minted (legitimate for
    echo).

  Examples in the placeholder cover both shapes:

  ```text
  ANTHROPIC_API_KEY=sk-ant-literal-value
  GITHUB_TOKEN=$GITHUB_PAT     (leading $ → read from shell env)
  CUSTOM_HEADER=hello-world
  ```

  Old `[tui.create_defaults]` schema keys (`auth_mode`, `auth_value`) are no longer read. Users with a saved file from
  earlier versions get fresh fallback defaults on next launch and new state on next successful create. Pre-1.0; the
  migration surface is small. The `WW_TUI_DEFAULT_AUTH_MODE` and `WW_TUI_DEFAULT_AUTH_VALUE` env vars are removed; new
  `WW_TUI_DEFAULT_EXISTING_SECRET` env var pins the new field. `WW_TUI_DEFAULT_SECRETS_BLOCK` deliberately not added —
  multi- line values don't pair well with shell env vars; users wanting a pinned set of secrets edit
  `~/.witwave/config.toml` directly.

  Phase 2 — per-row UI with checkbox + env-var dropdown — remains on the roadmap as its own follow-up.

## [0.7.16] — 2026-04-25

### Changed

- **`ww tui` create modal — Auth value field is now a multi-line TextArea** (4 rows tall) instead of a single-line
  InputField. Set-inline mode naturally takes one `KEY=VALUE` per line — no more cramming five pairs into a
  comma-separated single line that scrolls horizontally. Parser accepts BOTH newlines (the natural shape with the
  multi-line field) and commas (back-compat with the earlier single-line shape, and convenient when pasting a
  dotenv-style snippet from another doc); blank lines and trailing separators are trimmed. Placeholder text refreshed to
  a multi- line example so the expected shape is visible on first open. Modal height bumped 30 → 34 to fit the taller
  field without form-internal scroll.

## [0.7.15] — 2026-04-25

### Added

- **`ww agent send --backend <name>`** — bypasses the harness's default routing and dispatches the prompt directly to
  the named backend sidecar via the A2A `metadata.backend_id` hint. Empty flag preserves the existing
  no-metadata-no-routing-hint behaviour so calls without it are bit-for-bit identical to before.
  `agent.SendOptions.BackendID` carries the field for programmatic callers.

- **`ww tui` send modal (`s` on the list)** — keybinding opens a scoped send-message modal for the selected agent. Form
  has a Target dropdown (`(agent — harness routes)` first, then each declared backend), a prompt input with placeholder
  hint, and a scrollable response view that fills the lower half of the modal. Long replies open at the top so the lede
  is visible without scrolling. In-flight Send is guarded by a mutex + sending flag — impatient Enter-mashing can't
  stack parallel goroutines against a hung apiserver proxy. Errors stay inline with an ERROR: marker so the user can
  adjust + retry without re-typing the prompt. Same arrow-key translation the create/delete modals use; ESC and Cancel
  both close cleanly.

  Footer on the list updated to include the new key:
  `↑/↓ · a add · d delete · s send · l logs · r refresh · ↵ details (soon) · q/esc quit`.

## [0.7.14] — 2026-04-25

### Added

- **`--auth-set` — fourth backend-credential mode** alongside the existing `--auth` (named profile), `--auth-from-env`
  (lift from shell), and `--auth-secret` (reference existing Secret). Stamps literal `KEY=VALUE` pairs onto the
  backend's credential Secret at command time. Wired on both `ww agent create` (form `<backend>:<KEY>=<VALUE>` since
  multi-backend per command) and `ww agent backend add` (form `<KEY>=<VALUE>` since the backend is already positional).
  Repeatable per `(backend, KEY)`; duplicate-KEY-within-same-backend is a hard error rather than silent last-write-wins.
  Mutually exclusive with the other three auth modes per backend. SECURITY: command-line values land in shell history +
  ps output; for production tokens prefer `--auth-secret` (pre-create with `kubectl create secret --from-env-file`) or
  `--auth-from-env` (lift from a sourced env file). The minted Secret's `created-by` annotation records key NAMES only —
  values never leak into metadata that `kubectl get secret -o yaml` would surface.
- **`ww tui` create-modal `set-inline` mode** — TUI parity for `--auth-set`. The Auth-mode dropdown grows a fifth option
  (`set-inline`); the Auth-value field accepts a comma-separated list of `KEY=VALUE` pairs, equivalent to one
  `--auth-set <backend>:KEY=VALUE` per pair on the CLI side. Same dup-key rejection + empty-value rejection as the CLI
  parser.

### Documentation

- README backend-credentials section grows from three paths to four; cheatsheet line gains a `--auth-set` example.
- WALKTHROUGH § 3 (Multi-model consensus for real) credentials table updated to four modes; § 5a (Backend Add) gains a
  `--auth-set` snippet showing the no-prefix form. § 9 (What's next) lists the `ww tui` surface for the first time and
  adds the planned `ww agent backend auth set/unset/list/show` subtree to the roadmap.
- README adds a new **Interactive TUI** section covering keymap + the layered defaults (env > saved > fallback) the
  create modal uses.

## [0.7.13] — 2026-04-24

### Added

- **`ww tui` · delete modal (`d` on the list)** — orange-bordered confirmation dialog naming the target agent +
  namespace, with three checkboxes mapping directly to the CLI flags: `Remove repo folder`,
  `Delete ww-managed credential Secret(s)`, and `Purge` (the convenience superset). Ticking `Purge` auto-ticks the two
  granular flags so the form reflects the actual blast radius. Submit invokes `agent.Delete` asynchronously; the list's
  poll loop renders the row disappearing within milliseconds (refreshNow ping) rather than on the next 2s tick.

### Changed

- **`ww tui` · long-form create modal** — the 5-field skeleton grows three more (Auth mode dropdown, Auth value input,
  GitOps repo input). When `--repo` is set, submit runs three sequential phases — `agent.Create` → `agent.Scaffold` →
  `agent.GitAdd` — each with its own banner state ("creating CR…", "scaffolding repo…", "attaching gitSync…") so the
  user sees progress. Failures short-circuit and the error strip names the failing phase plus a CLI command to retry the
  rest from. Modal height bumped 18 → 22 so the new fields fit without form-internal scroll.

- **`ww tui` · `l` opens logs, Enter reserved for details** — re-bound the list keymap so Enter is free for the upcoming
  per-agent details view (status / events / conversation log / send-prompt). `l` takes over the one-shot "tail logs"
  action; matches the k9s convention of lowercase verbs for one-shot actions and Enter for "drill in." Until the details
  view lands, Enter flashes a 3-second hint in the footer naming the agent + pointing at `l` and `ww agent status` from
  the CLI.

### Fixed

- **`ww tui` · ESC in the logs view returns to the list** instead of quitting the app. The app-level `SetInputCapture`
  was catching `KeyEscape` and calling `app.Stop()` before per-page handlers could run; ESC is now page-local (logs view
  → back to list, create / delete modal → cancel). Ctrl-C remains the app-level emergency bail; `q` still quits from
  anywhere.

## [0.7.12] — 2026-04-24

### Changed

- **`ww tui` · logs default to aggregate across all containers** — Enter on a row now opens in "all containers" mode
  rather than dropping straight into harness. Fans out one tail goroutine per real container (harness + each declared
  backend); each writes through an `io.Writer` decorator that prepends `[<container>]` so the interleaved body reads as
  `[harness] routing → echo` / `[echo] received prompt "ping"`. `c` still cycles, but the rotation now starts with the
  aggregate view and then proceeds through each individual container (`all → harness → echo → … → all`). One shared
  cancel context tears every tail down atomically on ESC / cycle — no goroutine leaks on rapid key-mashing. Error
  reporting stamps the failing container name in aggregate mode so you can tell which tail died when the others are
  healthy.

## [0.7.11] — 2026-04-24

### Added

- **`ww tui` · per-agent logs drill-down (Enter on a row)** — replaces the "drill down (soon)" stub with a live
  log-tailing view. Header shows the agent identity + current container + stream status; body autoscrolls new log lines
  from the apiserver's `/logs?follow=true` stream; footer lists the two navigation keys. `c` cycles through the agent's
  containers (harness + each declared backend); ESC cancels the stream and returns to the list with the
  previously-selected row still highlighted. Reuses `agent.Logs` under the hood so buffer size, SinceTime/TailLines, and
  multi-pod fan-in semantics match the CLI exactly. Writes from the log goroutine are copied out of bufio's reusable
  scanner buffer and queued onto tview's UI thread via QueueUpdateDraw — no interleaved rendering. MaxLines capped at
  5000 for multi-hour tails; above that, `kubectl logs --since` is the right tool.

- **`ww agent backend add <agent> <name>[:<type>]`** — completes the backend-lifecycle trio (add / remove / rename) that
  was missing its third leg. Appends a backend to a running agent without the delete+recreate dance that used to lose
  gitSync wiring, team membership, and credentials.

  Reuses the `BackendAuthResolver` + profile catalog from `ww agent create --auth`. The three auth flags on
  `backend add` drop the `<backend>=<value>` prefix because the backend is already named positionally: `--auth oauth`,
  `--auth-from-env VAR[,VAR2]`, `--auth-secret <name>`. Missing credentials on an LLM backend surfaces a warning in the
  preflight banner rather than silently allowing a broken pod.

  Port picking: auto-assigns the first free slot in [8001, 8050]; fills gaps in sparse layouts rather than appending at
  end. CRD `MaxItems: 50` cap caught with a nicer diagnostic than the apiserver's schema-validation blob.

  When the agent has exactly one gitSync wired, also scaffolds `.agents/<…>/.<name>/agent-card.md` (+ behavioural stub
  for LLM backends) to the repo and regenerates `.witwave/backend.yaml` to list the new backend. Routing stays put — new
  backend is present but idle until the user redistributes. Pass `--no-repo-folder` for a CR-only change. 11 fake-client
  tests cover the full matrix (happy path, duplicate-name, unknown type, invalid name, missing agent, dry-run
  non-mutation, auth profile mint, no-auth LLM warning, sparse-port gap fill, inline backend.yaml regeneration,
  50-backend cap).

### Changed

- Drop duplicate `Cloning <repo> …` log line in four repo-touching verbs (`backend add`, `backend remove`,
  `backend rename`, `agent delete`). `cloneOrInit()` already prints it; every caller was re-printing the same message,
  producing doubled log output. No information lost — `scope.repoDisplay` and `ref.Display` resolve to the same string.

### Fixed

- **claude backend: SyntaxError on import at `executor.py:2546`** — #1491's rebind fix placed `global ALLOWED_TOOLS`
  mid-function (inside `settings_watcher`), AFTER earlier reads of the name. Python requires any `global` to come before
  every reference in the same scope, so the module failed to parse at all and every claude sidecar built from the #1491
  merge crash-looped at container start. Hoisted the declaration to the top of `settings_watcher` (just after the
  docstring) and dropped the duplicate at the rebind site. `py_compile` clean; new `claude:latest` image built from this
  release picks up the fix.

## [0.7.10] — 2026-04-24

### Added

- **`ww tui` · `a` to add an agent** — keybinding opens a centered modal form (Name / Namespace / Backend / Team /
  Create namespace if missing). Submit invokes `agent.Create()` asynchronously (Wait=false) so the TUI doesn't freeze;
  the list's poll loop shows the new row appearing and its Pending → Ready transition live. DNS-1123 validation on
  name + team surfaces inline; apiserver errors (AlreadyExists, RBAC denied, etc.) populate an error strip above the
  form so the user can fix and resubmit without retyping. ESC/Cancel closes cleanly; form state is reset on every open
  so a cancelled submission doesn't leak values into the next one. Footer updated:
  `↑/↓ move · a add · r refresh · ↵ drill down (soon) · q/esc quit`.

  Design notes: `AssumeYes=true` because `k8s.Confirm` can't prompt over a tview canvas; banner chatter discarded via a
  local writer so `agent.Create` stdout doesn't leak under the surface. Auth
  (`--auth / --auth-from-env / --auth-secret`) deliberately NOT on the form — typing tokens into a TUI form is the wrong
  UX; users who need credentials stay on the CLI until a richer credential picker lands alongside the drill-down view.

## [0.7.9] — 2026-04-24

### Added

- **`ww tui` — live agent list** (replaces the welcome stub). Polls the apiserver every 2 seconds and renders
  WitwaveAgents across every namespace the caller can read, in a `k9s`-style table with `NAMESPACE`, `TEAM`, `NAME`,
  `PHASE`, `READY`, `BACKENDS`, `AGE` columns. Agents created / deleted / transitioning out-of-band (via another CLI
  session, kubectl, Helm) update in place without a keystroke. Header strip shows cluster + context + a rollup
  (`Ready N · Degraded N · Pending N`); footer shows keybindings. `r` forces an immediate refresh; selection survives
  each snapshot swap by `(namespace, name)` identity so the highlighted row doesn't jump when rows shift above it. Empty
  / no-cluster / fetch-error states all render inline — never a black screen. Drill-down (Enter on a row) is still a
  stub pointing at #1450 — per-agent logs/events/send panels land in a follow-up. `agent.ListAgents()` shipped alongside
  as the render-ready data path shared by CLI and TUI.

- **`ww agent create --auth / --auth-from-env / --auth-secret`** — three repeatable, per-backend credential flags that
  close the last "CLI-only" gap. Previously users had to `kubectl create secret` and `kubectl patch` the CR after
  `ww agent create`; now a Claude agent with an OAuth token or API key is a single invocation:
  `ww agent create iris --backend claude --auth claude=oauth` reads `$CLAUDE_CODE_OAUTH_TOKEN` from the shell, mints a
  ww-labelled Secret in the namespace, and stamps `spec.backends[].credentials. existingSecret` on the CR so the
  operator wires it into the backend container's envFrom at reconcile time. Profiles ship for claude (`api-key`,
  `oauth`); more per-backend profiles and Vertex/Bedrock shapes land as follow-ups. Pre-existing Secrets are referenced
  verbatim via `--auth-secret` (verified, never modified). Arbitrary env vars are liftable via `--auth-from-env` for
  custom setups not covered by a named profile.

- **`ww agent team {join, leave, list, show}`** — first-class CLI surface for the operator's existing team-membership
  plumbing. Team membership is the label `witwave.ai/team` on the WitwaveAgent CR; the operator reconciles one
  `witwave-manifest-<team>` ConfigMap per distinct value and mounts it at `/home/agent/manifest.json`. Verbs are a pure
  label patch — no CRD schema change, no pod restart. `join` is idempotent for same-team joins and explicit about
  cross-team moves (was → now); `leave` drops the label so the agent falls back to the namespace-wide manifest; `list`
  renders a tree of teams → members (with an `(ungrouped)` bucket); `show` prints an agent's team + sorted teammates.
- **`ww agent create --team <team>`** — stamp `witwave.ai/team=<team>` at creation time. Avoids the race where a
  follow-up `team join` briefly drops the agent into the namespace-wide manifest before landing the label. Low-key flag:
  no default, not promoted in onboarding docs.

### Changed

- **`ww agent list` now defaults to cluster-wide scope** (was: context namespace only). The `kubectl get pods -A` idiom
  most operators reach for anyway is now the default; narrow to a single namespace with `--namespace`. The NAMESPACE
  column is always shown regardless of scope so grep/sort pipelines work uniformly. `-A` is preserved for kubectl parity
  but is now functionally redundant. DESIGN.md NS-3 updated to codify the read-verb carve-out from NS-1's context-first
  resolution.
- DESIGN.md gains a **TEAM-1..5 rules block** codifying the team-membership contract: label-based (not a CRD field), no
  default team, per-namespace scope, operator-owned cleanup, `--team` deliberately not a prominent flag.
- README.md agent-cheatsheet backfilled to cover the full verb surface (git, backend, team, delete with `--purge`), plus
  the `default` → `witwave` namespace fallback correction (was stale since 0.7.8) and a `--create-namespace` mention
  that was missing from the prose.

## [0.7.8] — 2026-04-24

### Added

- **`ww agent create --create-namespace`** — mirrors `helm install --create-namespace`. Provisions the target namespace
  before the CR apply when it doesn't exist (labelled `app.kubernetes.io/managed-by: ww` so teardown tooling can tell
  ww-created namespaces from hand-authored ones); no-op otherwise. Lets a virgin cluster go zero-to-agent in a single
  invocation.
- **`ww agent delete --remove-repo-folder`** — clones the (single) wired gitSync repo, `git rm -r`s the agent's
  `.agents/<…>/` subtree, commits, pushes. Runs BEFORE the CR delete so a repo-side failure leaves cluster state intact
  and the user can retry. Hard- fails on multi-gitSync ambiguity; soft-skips when no gitSync is wired.
- **`ww agent delete --delete-git-secret`** — after the CR is gone, reaps every ww-managed credential Secret referenced
  by the CR's gitSyncs[]. User-created Secrets preserved via the managed-by label gate.
- **`ww agent delete --purge`** — convenience flag: `--remove-repo-folder --delete-git-secret`. For decommissioning an
  agent permanently in one command.
- **End-to-end walkthrough** (`clients/ww/WALKTHROUGH.md`) — zero-to- gitOps-wired-multi-backend-agent narrative with
  every verb exercised. Long-form flags, multi-line snippets, copy-pasteable throughout.
- **Smoke Phase 5 (gitOps round-trip)** in `scripts/smoke-ww-agent.sh` — gated on `WW_SMOKE_GITHUB_REPO`. Exercises
  scaffold → create multi-backend → git add → rename → remove `--remove-repo-folder` → delete `--purge` end-to-end
  against a real repo.
- Fake-client unit tests for every CR-mutation verb (`GitAdd`, `GitList`, `GitRemove`, `BackendRemove`, `BackendRename`,
  `Delete` including all its new cleanup modes).

### Changed

- **Default namespace is now `witwave`** (was `default`). When neither `--namespace` nor the kubeconfig context pins
  one, every `ww agent *` verb falls back to `witwave` via the new `agent.DefaultAgentNamespace` constant. Rationale:
  ww-managed resources benefit from a dedicated blast radius, and landing in `default` by accident invites cross-tenancy
  incidents on shared clusters. Breaks kubectl parity by design — see DESIGN.md NS-1.
- **Namespace-source log line** — the `Using namespace: <ns> (<source>)` banner now distinguishes
  `(from kubeconfig context)` from `(ww default)` so operators can tell an inherited namespace from a quiet fallback
  (DESIGN.md NS-2).
- DESIGN.md NS-1/NS-2 rewritten; new NS-5 codifies the `--create-namespace` contract.

### Unlocks

Virgin cluster + virgin repo to a fully-wired agent, then full teardown, in flat flags:

```bash
ww agent create consensus \
    --namespace witwave \
    --create-namespace \
    --backend echo-1:echo \
    --backend echo-2:echo

ww agent scaffold consensus --repo <you>/my-witwave-config \
    --backend echo-1:echo \
    --backend echo-2:echo

ww agent git add consensus \
    --namespace witwave \
    --repo <you>/my-witwave-config \
    --auth-from-gh

# Later, when you're done:
ww agent delete consensus \
    --namespace witwave \
    --purge \
    --yes
```

## [0.7.7] — 2026-04-23

### Added

- **`ww agent backend remove <agent> <backend>`** — drops a backend from `spec.backends[]`, regenerates the inline
  `spec.config` backend.yaml when ww owns it (agents: list + routing no longer reference the removed entry), and refuses
  to remove the last backend (CRD minItems: 1). Pass `--remove-repo-folder` to also delete the corresponding
  `.agents/<…>/.<backend>/` folder from the gitSync repo and rewrite the repo's backend.yaml to drop the removed entry —
  one atomic commit, one push, same auth story as `ww agent scaffold`.
- **`ww agent backend rename <agent> <old> <new>`** — renames a backend atomically across the CR, harness + per-backend
  gitMappings, inline backend.yaml, AND the gitSync repo. The repo-side move uses git's native rename detection
  (`git mv`), regenerates the repo's `.witwave/backend.yaml` with the new name, and pushes in a single commit.
  `--no-repo-rename` skips the repo phase. Refuses on DNS-1123 violations, same-name no-ops, and collisions with an
  existing backend of the target name.
- Repo-side cleanup + rename for both verbs is best-effort from the user's perspective: the CR update lands first, so a
  push failure prints a manual-recovery recipe instead of reverting cluster state.

### Unlocks

Lifecycle management on multi-backend agents without hand-editing either the CR or the repo:

```bash
ww agent create consensus --backend claude --backend codex --backend echo
ww agent backend rename consensus echo smoke                   # echo → smoke
ww agent backend remove consensus smoke --remove-repo-folder   # drop it cleanly
```

## [0.7.6] — 2026-04-23

### Added

- **Multi-backend agents** — `ww agent create` and `ww agent scaffold` both gain a repeatable `--backend` flag. Two
  shapes accepted per entry:

  - `<type>` — name = type (e.g. `--backend claude`), the single- backend shortcut
  - `<name>:<type>` — explicit name + type pair (e.g. `--backend echo-1:echo --backend echo-2:echo`), required when two
    backends of the same type must coexist on one agent Each declared backend gets a distinct container name, distinct
    port (8001, 8002, …), and a distinct folder under `.agents/<agent>/.<name>/` in the gitOps repo. The generated
    `backend.yaml` enumerates every backend under `agents:` and routes every concern (a2a, heartbeat, jobs, tasks,
    triggers, continuations) to the **first** backend by default — operators redistribute routing by editing the file
    post-scaffold. This unlocks the multi-model consensus pattern the framework has always supported at the CRD level
    but that the CLI couldn't express until now:

  ```bash
  ww agent create consensus --backend claude --backend codex
  ```

### Backward compat

- `ww agent create hello` (no flags) and `ww agent scaffold hello --repo owner/repo` continue to produce a single
  default-echo backend — identical CR + repo output to 0.7.5.
- `--backend echo` (single bare type) still works for users who don't need multi-backend naming.
- `ww agent git add` needed no changes — its mapping generator already walked `spec.backends[]` by name, so
  multi-backend agents auto-derive per-backend gitMappings on attach.

## [0.7.5] — 2026-04-23

### Changed

- **`ww agent git add` gitSync default name now derives from the repo.** Previously the default `gitSyncs[].name` was
  the hardcoded label `witwave`, producing `/git/witwave/` on the pod regardless of which repo was wired. The new
  default sanitises the repo's basename to DNS-1123 (lowercase, `.`/`_`/`+` → `-`, trim hyphens), so
  `--repo skthomasjr/witwave-test` produces a gitSync named `witwave-test` and a filesystem at `/git/witwave-test/…` —
  matching what the user typed. Pass `--sync-name <name>` explicitly when wiring two repos with the same basename, or
  two branches of the same repo. The literal `witwave` label remains as a terminal fallback (exposed as
  `FallbackGitSyncName`) when sanitisation produces an empty string.
- **`ww agent git remove` auto-selects the sole gitSync.** When the agent has exactly one sync configured and
  `--sync-name` isn't passed, remove picks that one automatically. Zero → "nothing to remove" error. Multiple → refuse
  with the list of names so the caller can disambiguate. Eliminates the "what was that sync-name I used?" round-trip via
  `git list`.

## [0.7.4] — 2026-04-23

### Fixed

- **Operator git-sync wiring was broken for CR-based gitOps.** The mapping helper anchored rsync sources at
  `/git/<gs.Name>/<src>` but the init + sidecar containers never told git-sync to symlink at that path. git-sync v4
  defaults `--link` to `HEAD`, so the actual symlink lived at `/git/HEAD/` and every mapping hit ENOENT → `git-map-init`
  crash-looped indefinitely. Fix: pass `--link=<gs.Name>` in both args builders so the init's and sidecar's symlink
  names match the path the helper constructs.

## [0.7.3] — 2026-04-23

### Added

- **`ww agent scaffold <name> --repo <…>`** — seeds a ww-conformant agent directory structure on a remote git repo using
  the user's existing system git credentials. No ww-managed credential store; auth resolution walks env tokens →
  `gh auth token` → `git credential fill` → ssh-agent in that order. Empty-repo bootstrap is handled (go- git's
  `PlainInit` path). Phase 1 of the gitOps wiring plan.
- **Scaffold seeds hourly `HEARTBEAT.md`** by default — gives every scaffolded agent a self-exercising proof-of-life
  signal from the moment it's wired up. Pass `--no-heartbeat` to opt out. Documented exception to DESIGN.md SUB-4; every
  other dormant subsystem remains absent.
- **Scaffold branch auto-detection** — `--branch` defaults to empty; scaffold queries the remote's HEAD symref
  (`git ls-remote --symref`) and uses the repo's real default branch. Covers `master`/`develop`/ `trunk` without
  requiring the flag. Falls back to `main` on empty repos.
- **Scaffold merges on existing agents** — re-running `ww agent scaffold <existing>` no longer refuses. Missing files
  land; identical files are silent; drifted files are **preserved** (kubectl-apply-style merge). `--force` overwrites
  drifted files only — never touches user-added content outside the skeleton list.
- **`ww agent git {add,list,remove}`** — Phase 2 gitOps verbs. `git add` attaches a gitSync sidecar +
  harness/per-backend gitMappings to an existing WitwaveAgent CR. Three mutually-exclusive auth paths:
  `--auth-secret <name>` (reference pre-created K8s Secret, production), `--auth-from-gh` (mint from `gh auth token`,
  dev laptops), `--auth-from-env <VAR>` (mint from a named env var, CI/CD / .env). ww-minted Secrets carry
  `app.kubernetes.io/managed-by: ww` so `remove --delete-secret` can distinguish them from user-managed Secrets and
  refuse to clobber the latter.

### Fixed

- **Release pipeline built the operator with `DefaultImageTag=v<ver>`** (the raw `github.ref_name`) while
  `docker-metadata-action` published images with the `v` stripped. The operator then rendered pods that requested e.g.
  `git-sync:v0.7.2` and got GHCR 404 → ImagePullBackOff. `.github/workflows/release.yaml` now derives a stripped version
  (`${GITHUB_REF_NAME#v}`) and passes that as the `VERSION` build-arg. Non-tag runs (branch pushes) have no `v` prefix
  so the strip is a no-op.

## [0.7.2] — 2026-04-23

### Changed

- **Harness watchers go quiet when their directories are absent.** The five optional subsystems (jobs, tasks, triggers,
  continuations, webhooks) used to INFO-log `"<name> directory not found — retrying in 10s"` every 10 seconds forever
  when content was missing. That's 30 lines/minute of noise on a hello-world agent that legitimately uses none of those
  subsystems. Missing-directory logs now fire at DEBUG (visible under `-v`, silent by default). The _missing → present_
  transition — when content actually materialises, e.g. via a gitSync pull or a later ConfigMap mount — is preserved as
  a single INFO line so operators see the moment a subsystem comes online.

  The readiness gate (`/health/ready`) is unchanged: it continues to depend on backend routing config, not on
  optional-subsystem content. A dormant agent is now correctly both quiet AND schema-Ready.

### Added

- **DESIGN.md — SUB-1..4** codify the "file-presence-as-enablement" architectural property. An agent's enabled
  subsystems are expressed through content on disk under `.witwave/`, not through CRD fields. The absence of content is
  a normal, expected state that means "this agent intentionally does not use this subsystem." Future CLI verbs that
  enable a subsystem (e.g. `ww agent add-job <file>`) will do so by materialising content — no CRD bit-flipping, one
  source of truth.
- **`harness/test_run_awatch_loop_logging.py`** — 3 tests covering the dormant-directory contract: DEBUG-only on every
  miss, INFO exactly once on transition missing → present, and no transition-INFO when the directory exists on the first
  iteration (boot).

## [0.7.1] — 2026-04-23

### Fixed

- **`ww agent create` produced unhealthy pods.** The CR builder put both the harness and backend sidecar on port 8000.
  Pods share one network namespace, so one container's readiness probe hit the other's HTTP server and failed. Fixed by
  offsetting backends to 8001-8050 (one port per CRD-allowed backend slot). Codified as DESIGN.md PORT-1..4.
- **Harness never flipped Ready without an inline routing config.** The minimal CR from `ww agent create` didn't include
  `.witwave/backend.yaml`, so `/health/ready` stayed 503 with reason `no-backends-configured` (harness/main.py:524-534).
  The builder now stamps an inline config entry rendering a single-backend routing YAML that points the harness at the
  sidecar.

### Added

- **`ww operator install --if-missing`** — new flag that makes install idempotent. When the operator is already
  installed, logs a one-line no-op instead of refusing with `ErrPreflightRefused`. Useful for "ensure the operator is
  here" flows in scripts and CI.
- **`scripts/smoke-ww-agent.sh`** self-heals via `--if-missing` if the operator isn't installed when the smoke begins.
  Smoke is now truly turn-key: just `./scripts/smoke-ww-agent.sh` against any cluster.

### Design rules

DESIGN.md gains **PORT-1..4** codifying the agent-pod port contract: harness on 8000 (hard-coded), backends on 8001-8050
(CRD cap fits the range exactly), metrics on 9000 (dedicated listener), callers may override via explicit CR fields but
ww's builder enforces PORT-1..3 on generated CRs.

## [0.7.0] — 2026-04-23

### Added

- **`ww agent` subtree** — new command family for managing WitwaveAgent custom resources from the CLI. Closes the
  hello-world loop: a new user can go from zero to a working agent round-trip in two commands
  (`ww operator install && ww agent create hello && ww agent send hello "ping"`) with no API keys required.
  - `ww agent create <name>` — apply a minimum-viable WitwaveAgent CR. Defaults to the echo backend (no credentials
    required). Waits up to `--timeout` (default 2m) for the operator to report Ready; `--no-wait` opts out.
    `--backend echo|claude|codex|gemini` selects the backend; `--dry-run` renders the preflight banner and exits.
  - `ww agent list [-A]` — kubectl-style table with phase, ready count, backends, age. `-A` lists cluster-wide.
  - `ww agent status <name>` — curated describe: phase, ready replicas, backend summary, last-5 reconcile history.
  - `ww agent delete <name>` — deletes the CR; the operator cascades pod/Service cleanup via owner refs.
  - `ww agent send <name> "<prompt>"` — A2A `message/send` round-trip via the Kubernetes apiserver's built-in Service
    proxy. Works with any ClusterIP Service (no port-forward lifecycle, no external LoadBalancer required). `--raw`
    prints the full JSON-RPC envelope.
  - `ww agent logs <name>` — multi-pod container log streaming. Default container `harness`; `-c <name>` for
    backend/sidecar containers.
  - `ww agent events <name>` — scoped event snapshot: CR events + events on pods matching
    `app.kubernetes.io/name=<agent-name>`. `--warnings`, `--since`.
- **DESIGN.md — namespace rules NS-1..4** codify tenant-subtree namespace handling: default to the context's namespace
  with fallback to `default` (NS-1), always print the resolved namespace (NS-2), `-A` only on read verbs (NS-3),
  `create` exempt from the "explicit `-n` required for mutations" discipline for hello-world ergonomics (NS-4).

### Implementation notes

- Package layout mirrors `internal/operator/`: one file per concern (create, list, status, delete, send, logs, events)
  plus pure helpers (types, defaults, validate, build). Uses dynamic client + `unstructured.Unstructured` rather than a
  typed generated client — same pattern as `internal/operator/install.go`, avoids cross-module dependency on
  `operator/api/v1alpha1`.
- 30+ test assertions cover pure-function surface: DNS-1123 name validation + 50-char length cap, image-ref resolution
  (release / dev / empty versions, port-in-registry edge case), namespace precedence, CR builder invariants.

## [0.6.0] — 2026-04-23

### Added

- **`echo` backend** — a fourth backend image (`backends/echo/`) that ships as a zero-dependency stub A2A server.
  Returns a canned response quoting the caller's prompt; requires no API keys or external services. Serves two purposes:
  (1) the hello-world default for `ww agent create` so a new user can deploy a live agent with "access to a Kubernetes
  cluster and the CLI" as the only prerequisites, and (2) a reference implementation of the common A2A backend contract
  — demonstrates the dedicated-port metrics listener, the common `backend_*` metric baseline, and the
  contract-conformance pytest template for future backend types. See `backends/echo/README.md` for the in-scope vs
  intentional-non-scope list.
- **Release matrix** (`.github/workflows/release.yaml`) now publishes `ghcr.io/witwave-ai/images/echo` on every tag.
- **Chart integration** — `charts/witwave/values.yaml` defines proportionally small resource defaults for echo (~1/10th
  the envelope of an LLM-backed sidecar) and includes a commented `backends[]` example.
  `operator/config/samples/witwave_v1alpha1_witwaveagent.yaml` and the operator chart README now reference echo as a
  valid backend.
- **Events schema** (`docs/events/events.schema.json`) extended the `HookDecision.backend` and `AgentLifecycle.backend`
  enums to accept `echo` — prevents runtime validation rejection of echo-sourced events.
- **Dashboard** — `BackendType` now accepts `echo`; `BackendBubble.vue` and `tokens.css` carry a neutral slate palette
  entry for echo (`--witwave-brand-echo`), visually distinguishing it from the vendor-branded LLM backends.

## [0.5.8] — 2026-04-20

### Added

- **`ww tui` subcommand** (#1450 stub). Launches an interactive terminal UI: welcome banner, "what's coming" bullets,
  tracking-issue pointer, and live confirmation of the target kubeconfig context (cluster, context, namespace).
  Kubeconfig resolution is best-effort — if it fails the TUI still launches and shows a "No cluster configured"
  diagnostic in place of the context block. Exit with `q`, `esc`, or `ctrl-c`. No feature panels yet; the point of this
  release is to establish the framework. `--kubeconfig` / `--context` / `--namespace` flags mirror the `ww operator *`
  surface.

### Changed

- **TUI framework locked in as `rivo/tview`.** Shipped the stub on `charmbracelet/bubbletea` in one intermediate commit,
  then switched to tview before release on reflection that the long-term UX target is k9s-style (agent list → drill in →
  watch logs/events/sessions), and k9s runs on tview. Matching the framework means users who know k9s carry muscle
  memory across for free. The stub is small enough that the swap was nearly free; the same swap after real feature
  panels shipped would have been expensive.
- **Competitive landscape doc updated** to reflect fresh research on OpenClaw (20+ chat-platform integrations, macOS
  menu-bar companion with voice wake, TypeScript/Node.js implementation, MIT license, calendar-versioned release
  cadence) and to capture the "witwave is OpenClaw for teams with Kubernetes clusters" positioning frame in the
  Reference Products entry. Marked OpenClaw explicitly as "primary open-source competitor" in the section heading and
  restructured Relative-standing into explicit differentiator lists (5 in witwave's favor, 4 in OpenClaw's).

### Deps

- Added: `github.com/rivo/tview`, `github.com/gdamore/tcell/v2` (tview + the low-level terminal library it builds on).
- Net go.sum reduction — tview's transitive graph is lighter than the Charm ecosystem chain we temporarily added and
  then removed.

## [0.5.7] — 2026-04-20

Docs-only release. No code changes, no behaviour changes. Closes out the doc audit + prettier/markdownlint conformance
work that surfaced during session wrap-up.

### Fixed

- Documentation audit across the 8 most-read markdown files (README, SECURITY, CHANGELOG, operator + chart READMEs, ww +
  dashboard READMEs, runbooks): 12 stale-version / broken-link / wrong- endpoint / docs-vs-code-drift issues corrected.
  Notably: README Helm-install chart version 0.5.2 → 0.5.6; `ww operator status` sample output switched to `<X.Y.Z>`
  placeholders so it no longer goes stale every release; `docs/runbooks.md` `/tool-audit` → `/trace?decision=deny` (the
  former endpoint doesn't exist); `harness/README.md` AGENT_NAME default corrected to `witwave` (code default, not the
  documented `local-agent`).
- Table column realignment on the `tools/kubernetes/README.md` Tools table after the `read_secret_value` row was added
  in commit 423ae13.

### Changed

- Applied `prettier --write` + `markdownlint` across all 8 audit-pass files. Respects the repo's `.prettierrc.yaml`
  (proseWrap: always, printWidth: 120) and `.markdownlint.yaml` (MD013 line length, MD034 bare URLs, MD040 fenced-code
  language tags, MD051 link fragments). Largest diffs come from reflowing paragraphs that had been manually wrapped at
  ~80 chars; no content change beyond the six markdownlint fixes listed in the commit.
- New request filed: #1481 (enforce markdown linting in CI). The tools sat as standards-documents rather than gates,
  which is how the drift accumulated. Tracking issue captures the design trade-offs (pre-merge vs main-only scans,
  repo-wide cleanup vs incremental enforcement).

## [0.5.6] — 2026-04-20

Follow-up to v0.5.5. Three real changes — the LLM-billing defensive work, a cosign verify-recipe fix surfaced during
v0.5.5 validation, and the chart ↔ operator migration documentation.

### Added

- **A2A retry-policy guard** (#1457): new `A2A_RETRY_POLICY=fast-only|always|never` env (default `fast-only`) +
  `A2A_RETRY_FAST_ONLY_MS` threshold (default 5000ms). Under the default, 5xx responses that came back AFTER the
  threshold are refused instead of retried — the theory being that a slow 5xx almost always means the backend's LLM call
  ran to completion and only failed on the return path, so retrying would bill the prompt a second time.
  `harness_a2a_backend_slow_5xx_no_retry_total{backend,status}` counts every refused retry. Policy `always` restores
  legacy behaviour; `never` is the strictest no-double-bill posture.
- **Outer-timeout cancellation observability** (#1457): structured WARN on `asyncio.TimeoutError` with `session_id`,
  `backend`, `elapsed_seconds`, `prompt_bytes`, `trace_id` — the fields operators need to audit LLM billing for
  potential double-charges. New dedicated counter `harness_task_outer_timeout_cancel_total{backend}` distinct from the
  generic `harness_tasks_total{status="timeout"}`.
- **Chart ↔ operator migration documentation** (#1478): full seven-step procedure in `operator/README.md`, pointer in
  `charts/witwave/README.md`. Covers PVC preservation, CR authoring with `existingClaim`, verification, and the explicit
  "pick one" constraint.
- **Reduced-RBAC footgun callout** (#1461 Option A): explicit ⚠ callout in the operator chart README naming the
  `rbac.secretsWrite: false` + inline credentials incompatibility that today produces a silent reconcile loop, with
  concrete `existingSecret` guidance and a forward-reference to #1461 for the apply-time surfacing work.

### Fixed

- **Cosign verify recipe in SECURITY.md**: images strip the leading `v` from release tags (docker/metadata-action@v5
  default semver normalisation), so `v0.5.5` in the image path returned MANIFEST_UNKNOWN. Recipe now uses the bare
  `0.5.5` tag and includes an inline comment explaining the convention.
- **ConnectionError propagation** in `harness/backends/a2a.py`: added a narrow `except ConnectionError: raise` above the
  generic exception handler so our deliberate error surfaces (slow-5xx guard, response-size cap) propagate with their
  intended metric labels instead of being reclassified as `"unexpected error"`.

### Follow-ups tracked

- **#1479** Idempotency-Key header on A2A retries for backend-side dedupe (closes the remaining fast-5xx double-bill
  window; needs backend cooperation).
- **#1480** A2A cancellation verb (closes the outer-timeout window; blocked on upstream A2A spec work).
- **#1461** Options B (reconciler short-circuit) and C (admission webhook) remain deferred as the security-posture UX
  improvements they are, not bugs.

## [0.5.5] — 2026-04-20

Substantial follow-up to v0.5.4 — ten issues closed across security, observability, operator scale-readiness, and docs.
No user-visible behaviour changes on the happy path; all additions are opt-in or silent hardening.

### Added

- **Cosign keyless signing on every container image** (#1460). All ten images published under
  `ghcr.io/witwave-ai/images/*` on a tag release are now signed via Sigstore's OIDC flow — no long-lived signing key in
  the repo. Verification is opt-in; `docker pull` / `helm install` / `ww operator install` continue to work identically.
  See SECURITY.md § Verifying signed release artefacts for the `cosign verify` recipe.
- **Gitleaks pre-merge secrets scan** (#1462). New workflow `.github/workflows/ci-secrets.yml` runs on every PR + main
  push plus a weekly cron sweep. Allow-list lives at `.gitleaks.toml`; policy is zero tolerance for real secrets.
- **Four new Prometheus alerts** (#1465, #1467, #1469, plus #1466): `WitwavePVCFillWarning` + `WitwavePVCFillCritical`
  (70% / 90% kubelet_volume_stats thresholds), `WitwaveA2ALatencyHigh` (p99 harness → backend latency),
  `WitwaveEventValidationErrors` (non-zero schema validation failure rate), and `WitwaveWebhookRetryBytesHalfFull`
  (early warning before retry-bytes shedding begins). All eleven alerts (five existing + six new across this release)
  now carry `runbook_url` annotations pointing at `docs/runbooks.md` (#1468).
- **Webhook retry-bytes in-flight gauges** (#1466). `harness_webhooks_retry_bytes_in_flight{subscription}`,
  `harness_webhooks_retry_bytes_in_flight_total`, and `harness_webhooks_retry_bytes_budget_bytes{scope}` — the gauge
  signal that was missing when the shed counter was the only observable retry-bytes signal.
- **Operator leader-election flag surface + metric** (#1475). New `--leader-election-lease-duration` /
  `--leader-election-renew-deadline` / `--leader-election-retry-period` flags exposed by the operator and plumbed
  through `charts/witwave-operator/values.yaml`. New metric `witwaveagent_leader_election_renew_failures_total` declared
  for the alert-key slot; wiring to the renewal-error hook is a follow-up.
- **CRD `MaxItems` / `MaxProperties` caps** (#1471) on `WitwaveAgent.spec.Backends`, `GitSyncs`, `Config`,
  `PodAnnotations`, `PodLabels`, and `WitwavePrompt.spec.AgentRefs` — apiserver-side rejection of pathological CRs
  before they hit etcd's 1MB object-size ceiling.
- **Credential-watch secondary indexer** (#1474) — `WitwaveAgentCredentialSecretRefsIndex` field indexer narrows a
  Secret rotation's enqueue set to exactly the agents that reference it. At 100+ agents + bulk rotation, eliminates the
  reconcile thundering herd the old full-List mapper produced.
- **`checksum/config` + `checksum/manifest` pod annotations** on the harness Deployment (#1476). `kubectl edit cm` /
  `helm upgrade` with ConfigMap-only changes now roll the pod, instead of silently no-op'ing from the pod's perspective
  on config files the backends_watcher doesn't hot-reload.
- **CHANGELOG.md** (#1472) at the repo root, format follows Keep a Changelog 1.1.0. Backfilled entries for v0.4.0 →
  v0.5.5.
- **Token + secret rotation procedures** in SECURITY.md (#1463, #1464). Covers `HOMEBREW_TAP_GITHUB_TOKEN`
  (release-to-tap PAT; 90-day rotation cadence) and `SESSION_ID_SECRET` (MCP session-ID HMAC binding; two-secret
  grace-window rotation).
- **Event schema versioning policy** in docs/events/README.md (#1473). Documents additive-vs-breaking bump rules, compat
  windows across major-version transitions, and the subscriber contract.
- **`operator/MIGRATION.md`** (#1470) — CRD deprecation policy (2 minor-version served-version overlap),
  conversion-webhook architecture, manual `jq`-based fallback, test matrix contract. Sets expectations now so the first
  `v1alpha1` → `v1beta1` transition doesn't surprise anyone.
- **`docs/runbooks.md`** (#1468) — on-call playbook per alert.

### Fixed

- Gitleaks config schema (`.gitleaks.toml`) — original commit used `[[allowlist]]` (array of tables) syntax from a newer
  gitleaks release; gitleaks 8.24 bundled by `gitleaks-action@v2` wants a single `[allowlist]` map. Config refactored to
  match.

## [0.5.4] — 2026-04-20

### Fixed

- Operator startup log: "Failed to initialize metrics certificate watcher" now reads as a proper verb phrase rather than
  the grammar-broken "to initialize …" that made grep patterns and structured logging noisier. Duplicate `err` field in
  the same log call removed (closes #58ec91b in part).
- `charts/witwave-operator/Chart.yaml` `home:` URL retargeted from a non-existent `witwave-ai/witwave-operator` repo to
  the canonical `skthomasjr/witwave` source. Helm clients and artifacthub now render a working link.

### Changed

- `operator/README.md` and `charts/witwave/README.md` status blurbs updated to reflect shipped reality — the "first
  pass" / "work in progress" notices were written before the v0.4 chart releases and the operator's CRD + ww-CLI
  plumbing all landed.

## [0.5.3] — 2026-04-20

### Added

- `ww operator logs` — tails pods matching `app.kubernetes.io/name=witwave-operator` in the operator namespace.
  `--tail N` (default 100), `--since DUR`, `--no-follow`, `--pod NAME`. Scanner buffer bumped to 1 MiB so
  controller-runtime's long structured-log lines don't truncate.
- `ww operator events` — renders three event sources merged into one sorted table: events on `WitwaveAgent` CRs
  (cluster-wide), events on `WitwavePrompt` CRs, and events in the operator's own namespace (covers pod scheduling
  failures, image-pull errors, crash loops). `--watch` / `-w`, `--warnings`, `--since DUR` (default 1h).

## [0.5.2] — 2026-04-20

### Fixed

- `ww operator status` now reports the real chart version and appVersion — embedded chart's `Chart.yaml` is rewritten by
  a goreleaser pre-hook (`scripts/bump-embedded-chart-version.sh`) before `go:embed` bakes the binary, so the shipped ww
  reports the release tag instead of the canonical `0.1.0` placeholder on main.

## [0.5.1] — 2026-04-20

### Fixed

- `ww update` on Homebrew installs now runs `brew update` before `brew upgrade ww` and verifies via
  `brew list --versions ww` that the installed version actually changed. Previously, `HOMEBREW_NO_AUTO_UPDATE=1` or a
  "recent" brew cache could leave the user on the old version while `ww` printed a lying "Upgraded." line.

## [0.5.0] — 2026-04-20

### Added

- `ww operator install / upgrade / status / uninstall` — first-class Kubernetes management in the CLI. Operator chart is
  embedded via `go:embed`; no `helm` required on PATH. Singleton detection refuses installs when a release exists
  cluster-wide; `--adopt` takes over a cluster whose CRDs were installed manually. RBAC preflight via
  `SelfSubjectAccessReview` fails fast with a readable missing-verbs list. `upgrade` server-side-applies CRDs **before**
  `helm upgrade --skip-crds`, working around Helm's "crds/ is install-only" semantics so new CRD fields land before the
  Deployment rolls. `uninstall` preserves CRDs + CRs by default; `--delete-crds` + `--force` cascade-delete live CRs
  with a loud banner warning.
- Preflight confirmation banner with a local-vs-production cluster heuristic. Skips the prompt on `kind-*` / `minikube`
  / `docker-desktop` / `rancher-desktop` / `orbstack` / `k3d-*` / `colima` / localhost servers; always prompts on EKS /
  GKE / AKS ARNs. `--yes` / `WW_ASSUME_YES=true` / `--dry-run` overrides.
- `scripts/sync-embedded-chart.sh [--check]` — CI-guarded drift check so the embedded copy can't silently diverge from
  the canonical chart.

### Changed

- **Behaviour change:** when a backend's `auth_env` is configured but the referenced env var is empty, harness now
  raises `ConnectionError` at request time with a clear diagnostic. Previously sent unauthenticated requests silently,
  producing "backend instability" dashboards when the real cause was a token misconfig. Existing deployments with a
  latent token-env-var typo will start failing fast after upgrading; clear `auth_env` in `backend.yaml` if auth isn't
  required, otherwise set the referenced env var to a real token.

## [0.4.4] — 2026-04-20

### Fixed

- A2A response buffering now streams via `client.stream()` + `aiter_bytes()` with a configurable cap
  (`A2A_MAX_RESPONSE_BYTES`, default 256 MiB). Prevents harness OOM from a pathological backend streaming a multi-GB
  response.
- Harness proxy fetch endpoints (`/conversations`, `/trace`, `/tool-audit`) gained a matching
  `HARNESS_PROXY_MAX_RESPONSE_BYTES` (default 64 MiB) via a `_capped_get_json` streaming helper.
- `_extract_text` emits a structured WARN log surfacing the response's top-level keys when no A2A fallback shape
  matched, so SDK schema drift is diagnosable without enabling debug logs.
- Operator seeds `Replicas = minReplicas` on first-install when autoscaling is enabled — previously Kubernetes defaulted
  to 1 during the window between Deployment create and HPA create, producing transient under-provisioning.

### Added

- `harness_consensus_backend_errors_total` carries a bounded `reason` label (`timeout|connection|backend_error|other`)
  so dashboards can separate deadline hits from network blips.
- Dashboard `Agent` TypeScript interface gained `model?: string` — closes a silent schema drift vs
  `harness/main.py:team_handler`.

## [0.4.3] — 2026-04-19

### Fixed

- `ww update` cache no longer pins users to the previously-cached `latest` tag for 24h after upgrading. When
  `cached_latest == current`, the check re-fetches instead of returning a stale "you're current" answer.

## [0.4.2] — 2026-04-19

### Fixed

- `dashboard-ci` workflow added — 112 dashboard unit tests now run on every PR instead of being silently skipped.
- Dashboard `TeamView` test isolation — module-level state in `useTeam` now reset between tests via an
  `__resetForTesting` export.
- `charts-ci` fix: `/tmp/charts/` is created before `helm template` redirects into it.

## [0.4.1] — 2026-04-19

### Added

- `ww update` subcommand with `--force` and `--check` flags; in-place self-upgrade via Homebrew or `go install`.
  Configurable via `update.mode = off | notify | prompt | auto` in `ww`'s config file.

### Fixed

- `ww` config writer now pre-creates the file with `O_CREATE|O_EXCL|0o600` to close a chmod race on first config write.
- `WW_PROFILE=<typo>` now emits a stderr warning listing known profiles instead of silently falling back to defaults.

## [0.4.0] — 2026-04-19

### Added

- Homebrew distribution via the [witwave-ai/homebrew-ww](https://github.com/witwave-ai/homebrew-ww) tap.
  `brew install witwave-ai/homebrew-ww/ww` is now the primary install path.
- Operator chart + controller shipped to GHCR at every tag. `ww` and chart version numbers track each release.
- Cosign signing on `ww` CLI binaries via OIDC (GitHub Actions). Container images remain unsigned for now (see #1460).

### Changed

- Brand rename from the prior experimental name to **witwave**. All Go module paths, Python imports, chart names,
  directory references (`.witwave/`), and environment variables (`WITWAVE_*`) migrated in one sweep on 2026-04-19
  (commit b966b40).

[Unreleased]: https://github.com/witwave-ai/witwave/compare/v0.5.8...HEAD
[0.5.8]: https://github.com/witwave-ai/witwave/releases/tag/v0.5.8
[0.5.7]: https://github.com/witwave-ai/witwave/releases/tag/v0.5.7
[0.5.6]: https://github.com/witwave-ai/witwave/releases/tag/v0.5.6
[0.5.5]: https://github.com/witwave-ai/witwave/releases/tag/v0.5.5
[0.5.4]: https://github.com/witwave-ai/witwave/releases/tag/v0.5.4
[0.5.3]: https://github.com/witwave-ai/witwave/releases/tag/v0.5.3
[0.5.2]: https://github.com/witwave-ai/witwave/releases/tag/v0.5.2
[0.5.1]: https://github.com/witwave-ai/witwave/releases/tag/v0.5.1
[0.5.0]: https://github.com/witwave-ai/witwave/releases/tag/v0.5.0
[0.4.4]: https://github.com/witwave-ai/witwave/releases/tag/v0.4.4
[0.4.3]: https://github.com/witwave-ai/witwave/releases/tag/v0.4.3
[0.4.2]: https://github.com/witwave-ai/witwave/releases/tag/v0.4.2
[0.4.1]: https://github.com/witwave-ai/witwave/releases/tag/v0.4.1
[0.4.0]: https://github.com/witwave-ai/witwave/releases/tag/v0.4.0
