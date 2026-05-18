# The witwave Team

The `witwave-ai/witwave` repo is maintained by a team of nine deployed autonomous agents, plus a draft Agent Resources
identity (`milo`) that is scaffolded with credentials/avatar but not deployed. The deployed agents commit directly to
`main` (trunk-based development), coordinate via A2A (agent-to-agent JSON-RPC), and ship continuously — many small
high-quality releases per day rather than infrequent large ones.

Each agent owns one substrate. **Zora** decides what work happens when. **Evan** finds and fixes correctness bugs and
risks (across all five risk categories: security, reliability, performance, observability, and maintainability).
**Nova** keeps the code internally clean. **Kira** keeps the documentation accurate and current. **Finn** finds and
fills functionality gaps — what's missing relative to what should be there. **Felix** authors new features end-to-end —
the team's only generative agent, gated by a strict tier ladder so the highest-blast-radius work stays safe. **Iris** is
the team's git plumber — she pushes everyone's work and drives the release pipeline. **Piper** is the only
outward-facing agent — she narrates the team's progress to humans on GitHub Discussions (scoring events on a substantive
bar so the public surface stays signal-rich) and engages two-way across the Bugs, Questions, and Comments categories,
routing confirmed bugs back to Zora and recurring misconceptions to Kira's docs queue. **Mira** observes platform
reliability across the operator, agents, pod restarts, runtime storage, releases, and resource posture. When a signal
looks problematic, she distills the evidence and sends it to Zora to route the fix. **Milo** is the next draft identity:
Agent Resources for onboarding, roster consistency, profile drift, credentials readiness, and lifecycle hygiene.

The mission: **continuously improve and release the witwave platform — autonomously, around the clock, with quality
gates that catch problems before they land on `main`.**

## Secrets

Self-team secrets are mirrored into SOPS-encrypted dotenv files:

- `team.sops.env` carries shared team credentials such as Claude OAuth, gitSync, and OpenAI keys.
- `<agent>/agent.sops.env` carries each agent's GitHub identity as the in-container names `GITHUB_TOKEN` and
  `GITHUB_USER`.

Bootstrap commands load those SOPS files through `scripts/sops-exec-env.py`, so no repo-root plaintext env file is
required.

```bash
mise exec -- sops -d .agents/self/team.sops.env
mise exec -- sops -d .agents/self/piper/agent.sops.env
mise exec -- scripts/sops-exec-env.py .agents/self/team.sops.env .agents/self/piper/agent.sops.env -- \
  sh -lc 'test -n "$CLAUDE_CODE_OAUTH_TOKEN" && test -n "$GITHUB_TOKEN"'
```

## The team

### Zora — manager

The team's coordinator. She runs a continuous decision loop driven by a 30-minute heartbeat: reads team state, decides
who works on what next via call-peer, and decides when accumulated commits + green CI warrant a release. She doesn't
write code — she dispatches the right peer at the right time. (`.agents/self/zora/`)

### Evan — code defects + risks

Finds and fixes code defects and risks. Two skills: `bug-work` (correctness defects — unchecked errors, null derefs,
format-string mismatches, idempotency gaps) and `risk-work` (code that works today but is fragile under foreseeable
conditions — five categories: security CVEs / secrets / insecure patterns, reliability missing-timeouts / no-retries /
silent-degradation, performance unbounded-growth / blocking-in-async, observability silent-failures /
swallowed-error-context, plus maintainability deep-coupling / undocumented-invariants which is flag-only). His fixes
pass through a strict fix-bar; risky candidates flag for human review instead of auto-fixing. (`.agents/self/evan/`)

### Nova — code hygiene

Keeps the code internally clean. She formats Python with ruff, Go with gofmt + goimports, JSON/YAML/TS/Vue with
prettier; lints shell with shellcheck and Dockerfiles with hadolint; authors missing docstrings, godoc, and helm-docs
comments on undocumented exports. (`.agents/self/nova/`)

### Kira — documentation

Maintains the documentation surface — root README, CHANGELOG, every per-subproject README, the `docs/` tree. She
validates prose against current code state (`docs-verify`), refreshes forward-looking docs against industry reality
(`docs-research`), and catches drift between what the project claims and what it does. (`.agents/self/kira/`)

### Finn — functionality gaps

Finds and fills what's _missing_ relative to what should be there. Eleven gap-source categories per run — doc-vs-code
promises, untested public APIs, TODO/FIXME triage, architectural sibling-pattern gaps, convention drift,
operator↔helm-chart parity, CLI↔dashboard parity, environment-variable claims, helper-module unfinished surface,
configuration claims vs operator behavior, missing error handling. Single skill `gap-work`, single-pass shape parallel
to evan's. **Risk-tier 1-10 ladder** is the load-bearing autonomous-safety knob — starts at tier 1 (cosmetic / orphan
removal, near-zero blast) and walks up `1 → 3 → 5 → 7 → 9` only as each tier's gap pool exhausts clean. Bolder fills
happen later, after low-tier territory is verified safe.

Reliability / performance / observability mitigations are NOT finn's lane — those are **risks**, not gaps, and live in
evan's broadened `risk-work`. The clean line: finn fills what's missing per existing claims; evan addresses what's wrong
(bugs) or fragile (risks). (`.agents/self/finn/`)

### Felix — feature builder

The team's only **generative** agent. Where Evan / Finn / Nova / Kira maintain what exists, Felix authors what doesn't.
She reads feature requests (from user A2A, Zora dispatch, or Piper-routed Discussions), tiers the work against a 1-10
risk-tier ladder, plans the implementation, and ships code + tests + docs in atomic commit series. Single skill:
`feature-work`. Event-driven (not cadence-driven) — she fires on demand or via Zora's routing decisions, with a passive
30-min heartbeat for liveness only.

The clean line that separates Felix from the rest of the team:

- **Felix builds what doesn't exist yet** — new commands, new endpoints, new chart values, new capabilities not yet
  promised anywhere.
- **Finn fills what's promised but missing** — doc-vs-code drift, untested public APIs, sibling-pattern gaps.
- **Evan fixes what's broken or fragile** — correctness defects (bug-work), risk-class issues (risk-work).

If a request crosses the line, Felix hands it back to the right peer.

Feature work is the team's highest-blast-radius activity, so Felix runs under three load-bearing safety mechanisms:

1. **Tier ladder (1-10)**: trivial doc-driven additions at tier 1, single-file helpers at tier 2, multi-file features
   within an existing subsystem at tier 3, new helper modules at tier 4, new endpoints / MCP tools / chart capabilities
   at tier 5, cross-cutting at tier 6+, architectural / breaking changes at tier 7+. **v1 autonomous ceiling: tier 3**.
   Tier 4+ requires explicit human approval per commit until 30 days of clean tier-1/2 output.
2. **Non-waivable fix-bar**: every commit must (a) be genuinely a feature, (b) be correctly tiered within the ceiling,
   (c) ship its own tests in the same commit, (d) pass the local test suite, (e) update affected docs, (f) stay within
   the planned scope, (g) be atomic and revertable. If the bar can't be cleared, the work is deferred — never "landed
   and cleaned up later."
3. **Tier reset**: any of Felix's commits triggering a fix-forward by Evan within 24h drops her autonomous ceiling by 1
   tier for 7 days. Self-correcting safety floor.

(`.agents/self/felix/`)

### Iris — git plumbing + releases

The team's git plumber and release captain. She owns push posture (race handling, conflict surfacing, no-force rules),
watches CI on every push, and drives the full release pipeline when the team's accumulated work is ready to ship. Every
other agent commits locally and delegates the push to iris via `call-peer`. (`.agents/self/iris/`)

### Piper — outreach

The team's only outward-facing agent. She runs a heartbeat-driven outreach loop (every 30 min), reads team state (git
log, peer memories, Zora's decision_log + escalations.md, recent CI runs, recent releases), scores observed events on a
0-10 substantive-score model, and routes each tick to one of three outcomes: Announcements (≥9 — releases, critical
events, user-visible surface changes), Progress (5-8 — substantive dev activity with a 30-min cooldown), or silent (<5 —
most ticks; routine churn doesn't warrant a public post). The threshold scales with cadence so frequent heartbeats don't
flood the GitHub Discussions feed. Voice is informative + warm. **Read-only on source** and writes only to her memory
namespace + GitHub Discussions; doesn't dispatch peers for work, only `call-peer` for clarification questions before
posting publicly.

She also engages with humans across three Discussion surfaces via a discuss-\* skill family (`discuss-comments` on her
own posts, `discuss-bugs` in the Bugs category with deep code-investigation, `discuss-questions` in the General category
for open-ended Q&A). Confirmed user-reported bugs route through Zora via `bugs-from-users.md`; recurring misconceptions
feed Kira's docs queue. Piper has **admin role on the repo and moderates the Discussions surface autonomously** — Guard
0 (the moderation pre-screen running before all reply guards) hides spam / prompt-injection / harassment via
`minimizeComment` and locks abusive threads via `lockLockable` without human-in-the-loop. Hide and lock are reversible;
deletion stays off the autonomous menu by design. (`.agents/self/piper/`)

### Mira — platform reliability observer (scaffolded)

The team's next operational peer. Mira watches the substrate that lets the rest of the team keep working: operator
health, self-agent readiness, pod restarts, runtime storage, PVCs, release workflows, `ww update` availability,
one-agent-at-a-time rollout safety, and resource/anomaly signals. Her default posture is read-only: inspect, summarize,
record compact platform snapshots to memory, compare them historically once enough data exists, and send problematic
findings to Zora as distilled anomaly reports. Any systemic, repeated, or fix-needed issue is reported to Zora rather
than left only in Mira's private history. Zora then decides who should fix the issue.

Mira's first observation priority is pod/container restart behavior: capture restart counts every tick, compare deltas
over time, and triage likely causes from Kubernetes status, events, termination state, and previous/current logs.

Mira intentionally consolidates the earlier "devops" direction into one clearer first role: **platform reliability
observation**. Build/release infrastructure and agent runtime lifecycle are tightly coupled in practice; the same agent
who notices a failed release should also understand whether the operator, pods, storage, and rollouts are healthy enough
to recover. Mira does not own the repair loop; she owns evidence quality and the Zora handoff. (`.agents/self/mira/`)

## Topology

```text
                  ╭──────────────────────────────────────╮
                  │                 ZORA                 │
                  │       manager / decision loop        │
                  │    reads state · dispatches peers    │
                  ╰───────────────────┬──────────────────╯
                                      │
                                      │ call-peer
                ┌──────────┬──────────┼──────────┬──────────┐
                │          │          │          │          │
            ╭───▼────╮ ╭───▼────╮ ╭───▼────╮ ╭───▼────╮ ╭───▼────╮
            │  EVAN  │ │  NOVA  │ │  KIRA  │ │  FINN  │ │ FELIX  │
            │defects │ │hygiene │ │  docs  │ │  gaps  │ │features│
            ╰───┬────╯ ╰───┬────╯ ╰───┬────╯ ╰───┬────╯ ╰───┬────╯
                │          │          │          │          │
                │   commits locally — delegates push via call-peer
                │          │          │          │          │
                └──────────┴──────────┼──────────┴──────────┘
                                      │
                                  ╭───▼───╮
                                  │ IRIS  │
                                  │  git  │
                                  ╰───┬───╯
                                      │ push + CI watch + release
                                      ▼
                                 origin/main
                                      │           ┌──── reads state ────┐
                                      ▼           │                     │
                             release pipeline ✦   │              ╭──────▼──────╮
                                      │           │              │    PIPER    │
                                      ▼           └─────────────▶│  outreach   │
                            ghcr.io · oci · brew                 │  heartbeat  │
                                                                 ╰──┬───────▲──╯
                                                                    │       │
                                                              post  │       │ reply
                                                                    ▼       │
                                                              GitHub Discussions
                                                                   (two-way)
```

Piper sits OUTSIDE the work-coordination loop. She reads team state but doesn't dispatch peers for work; her only A2A
use is `ask-peer-clarification` (information-only questions) before posting publicly.

Mira also sits outside the work-production loop. She watches the platform substrate — operator, agents, releases,
runtime storage, restarts, and resource/anomaly signals — and sends distilled findings to Zora when something looks
problematic. Zora decides whether the finding becomes work for Iris, Evan, Finn, Felix, Kira, Nova, or a human.

## Proposed future members

The team is designed to grow. Mira is the current platform reliability observer. Milo now revives the earlier
agent-resources direction as a separate draft identity focused on agent lifecycle hygiene and bounded pod lifecycle
actions. The remaining roles below are still design-pipeline ideas. Names are tentative and likely to be revisited
before scaffolding.

### 1. Milo — Agent Resources (draft identity)

Owns agent lifecycle hygiene: onboarding readiness, GitHub/profile consistency, credential-readiness checks, avatar and
roster drift, role-boundary clarity, safe pause/decommission paths, and approved pod lifecycle actions when an agent is
stuck or needs a controlled restart. Distinct from Mira: Mira asks whether the platform is healthy enough for agents to
run; Milo asks whether the roster, accounts, identities, and lifecycle surfaces are coherent enough for the team to make
sense. Distinct from the future Process Architect: Process Architect improves how the team works; Milo manages who is on
the team and whether each member is properly provisioned.

Current state: scaffolded under `.agents/self/milo/` with a draft Claude identity, public card, avatar, and encrypted
`agent.sops.env`. The bootstrap path grants `namespaceWrite` Kubernetes API access so Milo can evict/delete pods and
perform bounded namespace-local remediation when an approved lifecycle workflow needs it. His heartbeat remains disabled
until a real lifecycle skill is ready.

### 2. security — likely **vera** or **maya**

Higher-level security work that goes beyond evan's automated `risk-work` lens. Threat modeling against the architecture,
manual audit response, RBAC posture review, supply-chain analysis, secret rotation policy, compliance gap-finding.
Distinct from evan: evan automates CVE/secret/insecure-pattern detection across the codebase; security-agent reasons
about the _system's overall threat posture_ — the work that requires architectural understanding rather than scanner
output. Evan covers the high-volume automated surface today; the architectural-security gap is real but rarer-firing.

### 3. testing — name + scope TBD

At least one testing-focused agent is on the roadmap, but the scope needs a design discussion before scaffolding —
possibilities span "writes new tests where evan's fix-bar flagged untested code paths," "runs existing suites and
surfaces flakiness/regressions," "mutation testing to evaluate test quality," "property-based test generation," "E2E
test maintenance." Each is a different shape of work. The value is high, but the design discussion has to land first —
until we pick a shape, scaffolding is premature.

### 4. software-architecture — likely **theo** or **lyra**

Watches the _shape_ of the system rather than individual files. Detects module-boundary erosion, cross-cutting refactor
opportunities, design-pattern drift, scalability/performance architecture concerns. Distinct from nova (line-level
hygiene) and evan (defect-level fixing) — architecture-agent looks at how the system fits together across components,
surfacing changes that no single file or function would reveal. Many of her findings will be flag-only; substantive
refactor proposals deserve human review before landing. This is useful but lower-autonomy than Mira/security/testing
because many findings are flag-for-human by nature.

### 5. CTO — likely **rhea** or **aria**

Picks big direction changes. Reads the team's accumulated state — open issues, recurring pain points, drift between what
the platform claims and what users want, market/ecosystem shifts (new MCP servers, new model capabilities, adjacent OSS
projects) — and proposes _strategic_ moves: "we should pivot to X," "the next quarter's theme is Y," "this whole
subsystem deserves a rewrite." Output is high-leverage, low-frequency, mostly human-review: design memos, prioritization
proposals, deprecation calls, "let's stop investing in Z." Distinct from zora (who decides _which peer dispatches next_
on the 30-min cadence) and software-architecture (who flags structural decay): CTO sets the _direction_ both of them
then execute against. Direction-setting is highest-leverage but also requires the most accumulated context — better once
the team has months of state to reason over and the platform has real users with real friction points.

## How the loop closes

1. **Zora's heartbeat fires** every 30 min → reads team state → applies priority policy.
2. **Zora dispatches a peer** (urgent first, then cadence floor, then team-tidy, then backlog-weighted) via `call-peer`.
3. **The peer does its domain work** — finds bugs, formats code, refreshes docs, etc. Commits locally with a focused
   message.
4. **The peer delegates the push to Iris** via `call-peer`. Iris pushes; watches CI on the resulting commit.
5. **Iris reports back** to the originating peer with the CI conclusion. Red → fix-forward then revert. Green → work
   landed.
6. **Zora's next tick** sees the new commit on `origin/main`. Independent of peer dispatching, she runs a
   release-warranted check: commits since latest tag + CI green + ≥1h since last release + no critical findings → asks
   Iris to cut a release.
7. **Iris cuts the release** — pre-flight, CHANGELOG, tag, push. The three release workflows fire on the tag. Container
   images, Helm charts, ww CLI artifacts publish.
8. **Mira observes the platform loop** on her own cadence. If she sees a concerning platform bug/anomaly, she distills
   the evidence and sends it to Zora; Zora routes any needed repair.
9. **Loop continues** — there's always more to find, more to fix, more to ship.

## Reading further

- Per-agent identity + skills: `.agents/self/<name>/.claude/CLAUDE.md`
- Per-agent public capability surface: `.agents/self/<name>/.witwave/agent-card.md`
- Bootstrap (deploying the team to a cluster): `.agents/self/bootstrap.md`
- Operational runbooks: `docs/runbooks/`
- Project-level architecture: `docs/architecture.md`, `AGENTS.md`
