# Self Team Bootstrap

This repo is maintained by witwave agents running on a witwave cluster. This document is the meta-loop: it walks through
using the `ww` CLI plus local credentials to stand up the WitwaveWorkspace and WitwaveAgents that manage and maintain
_this_ repo. SOPS-encrypted dotenv files now live beside the agent config, and the bootstrap commands load them through
`scripts/sops-exec-env.py`.

## Goal

A "witwave-self" ecosystem is one named WitwaveWorkspace plus one or more WitwaveAgents, all bound to it, that share a
working copy of this repo and collaborate on maintaining it. Concretely, after this doc is fully implemented the cluster
is running:

- One **WitwaveWorkspace** (`witwave-self`) with one or more shared volumes (`source` for the working repo state,
  `memory` for long-term per-agent memory, more as concerns accrete) that every participating agent mounts at the same
  paths.
- Ten **WitwaveAgent**s (`iris`, `kira`, `nova`, `evan`, `zora`, `finn`, `felix`, `piper`, `mira`, `milo`) with
  `Spec.WorkspaceRefs` pointing at `witwave-self` so they share the workspace.

  - **iris** owns source-tree initialization + release captaincy + git plumbing for the team.
  - **kira** owns documentation hygiene + research.
  - **nova** owns code-internal hygiene (formatting, comment-vs-code verification, comment authoring).
  - **evan** owns code defects — `bug-work` for correctness bugs (logic-defect lens), `risk-work` for security risks
    (CVE / secrets / insecure-pattern lens). The verb "work" is the forward-compatible naming convention for
    product-engineering siblings (future: `gap-work`, `feature-work`).
  - **zora** is the team's manager — she dispatches the other peers from a continuous 60-min decision loop. She reads
    team state (git, peer memories, CI), applies a priority policy (urgency → cadence floor → backlog →
    release-warranted check), and dispatches via `call-peer`. She doesn't write code; she coordinates. The peers stay
    autonomous within their domain.
  - **felix** owns feature work within a conservative autonomous tier ceiling; higher-blast-radius work still requires
    explicit human approval.
  - **mira** observes platform reliability: operator health, agent readiness, pod restarts, runtime storage, release
    posture, upgrade safety, and resource/anomaly signals. When a signal looks problematic, she distills it and sends it
    to zora to route the fix.
  - **milo** is the team's Agent Resources peer: he keeps a live roster directory (who is deployed, who is up vs down,
    what each member does) and stewards safe team-wide version upgrades as the release canary — read-only except for
    one-at-a-time `ww agent upgrade`. He runs on Opus 4.8 at max effort as the team's model canary.

  Agents that author source changes commit work locally and delegate the push to iris via `call-peer`. Iris owns all
  git/GitHub authority for the team; zora owns team-level coordination + release-cadence decisions. Direct user
  invocation of any peer still works — zora is one valid caller, not a gate.

- A **gitSync** sidecar that keeps the shared volume in lockstep with this GitHub repo.

The doc is intentionally incremental — each section is a copy-pasteable command. Sections are added as the bootstrap
surface grows; if a step isn't listed here yet, it isn't part of the bootstrap yet.

## Targets

This doc currently targets **Docker Desktop** (single-node local Kubernetes).

Conventions used by every command in this doc:

- All commands are written with **long-form flags** (e.g. `--namespace`, not `-n`) so the intent is readable without
  consulting the CLI's flag table.
- Multi-flag commands are split across lines with `\` continuations, one flag per line, so each option is reviewable on
  its own.

## Prerequisites

### Cluster + tools

- A Kubernetes cluster reachable via your current kubeconfig context. Enable Kubernetes in Docker Desktop settings.
- The `ww` CLI installed (via the universal installer or Homebrew tap):

  ```bash
  curl -fsSL https://github.com/witwave-ai/witwave/releases/latest/download/install.sh | sh
  ```

  or:

  ```bash
  brew install witwave-ai/homebrew-ww/ww
  ```

### Environment

SOPS dotenv files are the bootstrap source for credentials. The local `mise.local.toml` points SOPS at the Witwave age
key, and `scripts/sops-exec-env.py` decrypts one or more SOPS dotenv files in memory before executing the `ww` command.

Required encrypted files:

- `.agents/self/team.sops.env` carries shared team credentials: `CLAUDE_CODE_OAUTH_TOKEN`, `GITSYNC_USERNAME`,
  `GITSYNC_PASSWORD`, and `OPENAI_API_KEY`.
- `.agents/self/<agent>/agent.sops.env` carries that agent's GitHub identity as `GITHUB_TOKEN` and `GITHUB_USER`.
  Piper's file also carries the X/Twitter publishing credentials as `X_*` keys.

Mira's `.agents/self/mira/agent.sops.env` file now carries the `mira-agent-witwave` GitHub identity. Keep her deployment
step explicit and one-at-a-time so you can observe the first rollout before adding her to the steady-state loop.

Verify the local decrypt path without printing values:

```bash
mise exec -- scripts/sops-exec-env.py \
  .agents/self/team.sops.env \
  .agents/self/iris/agent.sops.env \
  -- sh -lc 'test -n "$CLAUDE_CODE_OAUTH_TOKEN" && test -n "$OPENAI_API_KEY" && test -n "$GITHUB_TOKEN" && test -n "$GITSYNC_USERNAME"'
```

The agent-create commands lift those decrypted shell variables into each agent's containers at the in-container env-var
names each consumer expects: `CLAUDE_CODE_OAUTH_TOKEN` lands on Claude containers as-is; `OPENAI_API_KEY` lands on
OpenAI or Codex containers as-is; `GITHUB_TOKEN` and `GITHUB_USER` land inside that agent's active backend container;
and `GITSYNC_USERNAME` / `GITSYNC_PASSWORD` are minted into a per-agent `<name>-gitsync` Secret and `envFrom`-wired to
the gitSync sidecar.

For this bootstrap the repo (`witwave-ai/witwave`) is public and the sidecar would clone anonymously without any creds —
the `--gitsync-secret-from-env` wiring is shown so the pattern carries over verbatim when iris later points at a private
config repo.

### Storage

Docker Desktop ships a single `hostpath` storage class which is `ReadWriteOnce`. The workspace declares its volumes with
`:rwo` so the operator provisions hostpath PVCs and every agent pod (all on the only node) mounts them directly. The
shared volume lives on Docker Desktop's underlying Linux VM filesystem.

## Step 1 — Install the witwave-operator

The operator reconciles all three CRDs (`WitwaveAgent`, `WitwavePrompt`, `WitwaveWorkspace`) and is the prerequisite for
every subsequent step. The `ww` CLI ships the operator's Helm chart embedded so this single command is all that's needed
— no `helm repo add` and no Helm chart on disk:

```bash
ww operator install \
  --namespace witwave-system \
  --create-namespace
```

`--create-namespace` provisions `witwave-system` if it doesn't already exist. Without the flag, `ww operator install`
refuses on a missing namespace so a typo in `--namespace` can't silently create a junk namespace — same posture as
`ww workspace create` and `ww agent create`.

Verify the operator pod is `Running` and the CRDs are registered:

```bash
ww operator status \
  --namespace witwave-system
```

The status output reports the Helm release, the operator deployment, and the three CRDs. Investigate any non-`Ready`
condition before continuing — the remaining steps depend on the operator being healthy enough to serve admission
webhooks.

## Step 2 — Create the WitwaveWorkspace

The WitwaveWorkspace is the shared envelope every agent that maintains this repo will bind to. It declares two shared
volumes — `source` (the working repo state) and `memory` (long-term per-agent memory) — each projected onto every
binding agent at `/workspaces/witwave-self/<volume-name>`. Secret references and ConfigMap-backed files get added in
later steps as agents need them.

```bash
ww workspace create witwave-self \
  --namespace witwave-self \
  --create-namespace \
  --volume source=20Gi:rwo \
  --volume memory=1Gi:rwo
```

The `--volume` flag's form is `<name>=<size>[@<storageClass>][:<mode>]`. Omitting `@<storageClass>` lets the cluster's
default storage class win — on Docker Desktop that's the bundled `hostpath` class. The `:rwo` suffix asks for
`ReadWriteOnce`, which is what `hostpath` supports.

Verify:

```bash
ww workspace status witwave-self \
  --namespace witwave-self
```

The status output should show the workspace `Ready` with both volumes provisioned. PVC names follow the pattern
`<workspace>-vol-<volume>` — `witwave-self-vol-source` and `witwave-self-vol-memory` here.

## Step 3 — Deploy iris

Iris is a WitwaveAgent with one `claude` backend, bound to the `witwave-self` workspace, with its identity (prompts,
hooks, Claude Code config) sourced from `.agents/self/iris/` in this repo, and per-backend persistent storage for
session, memory, and Witwave conversation/trace logs. One command:

```bash
mise exec -- scripts/sops-exec-env.py .agents/self/team.sops.env .agents/self/iris/agent.sops.env -- \
  ww agent create iris \
  --namespace witwave-self \
  --workspace witwave-self \
  --with-persistence \
  --backend claude \
  --harness-env TASK_TIMEOUT_SECONDS=2700 \
  --harness-env CONVERSATIONS_AUTH_DISABLED=true \
  --backend-env claude:TASK_TIMEOUT_SECONDS=2700 \
  --backend-env claude:CONVERSATIONS_AUTH_DISABLED=true \
  --backend-secret-from-env claude=CLAUDE_CODE_OAUTH_TOKEN \
  --backend-secret-from-env claude=GITHUB_TOKEN \
  --backend-secret-from-env claude=GITHUB_USER \
  --gitsync-bundle https://github.com/witwave-ai/witwave.git@main:.agents/self/iris \
  --gitsync-secret-from-env GITSYNC_USERNAME:GITSYNC_PASSWORD
```

`TASK_TIMEOUT_SECONDS=2700` is set on **both** the harness and the claude backend so the timeout headroom (45 minutes vs
the 5-minute default) applies end-to-end. The harness uses it to size the A2A relay's read timeout
(`_HTTP_TIMEOUT_SECONDS = TASK_TIMEOUT_SECONDS - 10` in `harness/backends/a2a.py`); the backend uses it as the per-task
LLM-call timeout. Mismatch between them causes confusing failures — the harness gives up mid-call and retries, leaving
the backend running and producing duplicate work. The headroom matters when iris watches release workflows (the
container-build job alone can take ~25 minutes) or delegates a long-running task to a sibling.

`CONVERSATIONS_AUTH_DISABLED=true` is the local-dev escape hatch on the harness and backend bearer-token gates for
conversation/status inspection from a local workstation. It lets `ww conversation list / show / --follow` and
`ww team status` read activity without minting per-agent `CONVERSATIONS_AUTH_TOKEN` secrets. This is not required for
agent-to-agent communication or normal team work. Production deployments should set a real token instead.

Verify iris is `Ready` and bound to the workspace:

```bash
ww agent list \
  --namespace witwave-self
```

```bash
ww workspace status witwave-self \
  --namespace witwave-self
```

`ww agent list` should show one row (`iris`) in state `Ready`. `ww workspace status` should report `iris` under the
bound-agents section. The pod has the workspace's `source` and `memory` volumes mounted at
`/workspaces/witwave-self/source` and `/workspaces/witwave-self/memory` on every backend container.

## Step 4 — Deploy kira

Kira is the team's documentation-hygiene agent — periodic scans for typos, dead links, stale paths, markdown formatting
drift, and other mechanical doc issues. Same shape as iris (one `claude` backend, bound to `witwave-self`, identity
sourced from `.agents/self/kira/`); she pushes her own commits via her `git-push` skill rather than handing off to iris.
One command:

```bash
mise exec -- scripts/sops-exec-env.py .agents/self/team.sops.env .agents/self/kira/agent.sops.env -- \
  ww agent create kira \
  --namespace witwave-self \
  --workspace witwave-self \
  --with-persistence \
  --backend claude \
  --harness-env TASK_TIMEOUT_SECONDS=2700 \
  --harness-env CONVERSATIONS_AUTH_DISABLED=true \
  --backend-env claude:TASK_TIMEOUT_SECONDS=2700 \
  --backend-env claude:CONVERSATIONS_AUTH_DISABLED=true \
  --backend-secret-from-env claude=CLAUDE_CODE_OAUTH_TOKEN \
  --backend-secret-from-env claude=GITHUB_TOKEN \
  --backend-secret-from-env claude=GITHUB_USER \
  --gitsync-bundle https://github.com/witwave-ai/witwave.git@main:.agents/self/kira \
  --gitsync-secret-from-env GITSYNC_USERNAME:GITSYNC_PASSWORD
```

Same paired-timeout lift as iris (harness + backend both set to 2700s). Kira's full docs scans walk every markdown file
in the repo and on first run also npx-download prettier and markdownlint-cli — that combination eats through the default
5-minute timeout fast on a cold container. 45 minutes is generous headroom; dial it back later if scans typically finish
well under the ceiling.

Verify both agents are now bound to the workspace:

```bash
ww agent list \
  --namespace witwave-self
```

`ww agent list` should now show two rows (`iris`, `kira`) both in state `Ready`. `ww workspace status witwave-self`
should report both under the bound-agents section.

## Step 5 — Deploy nova

Nova is the team's code-hygiene agent — keeps the code-internal comprehension substrate clean (formatting via ruff /
gofmt / goimports / shfmt / prettier; comment authoring on undocumented exports, helm-docs-style annotations on
`values.yaml`, Dockerfile / shell-script / GitHub Actions comment work). Same shape as iris and kira (one `claude`
backend, bound to `witwave-self`, identity sourced from `.agents/self/nova/`); like kira, she commits locally and
delegates pushes to iris via `call-peer`. One command:

```bash
mise exec -- scripts/sops-exec-env.py .agents/self/team.sops.env .agents/self/nova/agent.sops.env -- \
  ww agent create nova \
  --namespace witwave-self \
  --workspace witwave-self \
  --with-persistence \
  --backend claude \
  --harness-env TASK_TIMEOUT_SECONDS=2700 \
  --harness-env CONVERSATIONS_AUTH_DISABLED=true \
  --backend-env claude:TASK_TIMEOUT_SECONDS=2700 \
  --backend-env claude:CONVERSATIONS_AUTH_DISABLED=true \
  --backend-secret-from-env claude=CLAUDE_CODE_OAUTH_TOKEN \
  --backend-secret-from-env claude=GITHUB_TOKEN \
  --backend-secret-from-env claude=GITHUB_USER \
  --gitsync-bundle https://github.com/witwave-ai/witwave.git@main:.agents/self/nova \
  --gitsync-secret-from-env GITSYNC_USERNAME:GITSYNC_PASSWORD
```

Same paired-timeout lift as iris and kira (harness + backend both 2700s). Nova's `code-format` runs language-specific
formatters across the entire source surface and her `code-document` Tier 3 pass reads function bodies + chart templates
to ground each authored comment — both are within the default 5-minute budget when she's warm but can hit the ceiling on
cold containers. The headroom matters.

## Step 6 — Deploy evan

Evan is the team's defect-finding agent — runs `bug-work` (correctness defects via static analyzers) and `risk-work`
(security defects via CVE / secret / insecure-pattern scanners). Same shape as iris/kira/nova (one `claude` backend,
bound to `witwave-self`, identity sourced from `.agents/self/evan/`); like nova/kira, evan commits locally and delegates
pushes to iris via `call-peer`. Note the higher `TASK_TIMEOUT_SECONDS` — evan's depth-7+ wide passes can run for 30+
minutes, well past the team-default 2700s budget.

```bash
mise exec -- scripts/sops-exec-env.py .agents/self/team.sops.env .agents/self/evan/agent.sops.env -- \
  ww agent create evan \
  --namespace witwave-self \
  --workspace witwave-self \
  --with-persistence \
  --backend claude \
  --harness-env TASK_TIMEOUT_SECONDS=7200 \
  --harness-env CONVERSATIONS_AUTH_DISABLED=true \
  --backend-env claude:TASK_TIMEOUT_SECONDS=7200 \
  --backend-env claude:CONVERSATIONS_AUTH_DISABLED=true \
  --backend-secret-from-env claude=CLAUDE_CODE_OAUTH_TOKEN \
  --backend-secret-from-env claude=GITHUB_TOKEN \
  --backend-secret-from-env claude=GITHUB_USER \
  --gitsync-bundle https://github.com/witwave-ai/witwave.git@main:.agents/self/evan \
  --gitsync-secret-from-env GITSYNC_USERNAME:GITSYNC_PASSWORD
```

## Step 7 — Deploy zora

Zora is the team manager — coordinates work at the team level, decides which peer dispatches when, and gates
release-warranted on the team's quality bar. She doesn't commit code; her only writes are to her own memory namespace.
The `gh` token she carries is read-only — used by her every-tick CI-status check (`gh run list`) so the
never-leave-main-red policy can detect red CI directly rather than infer from indirect signals.

```bash
mise exec -- scripts/sops-exec-env.py .agents/self/team.sops.env .agents/self/zora/agent.sops.env -- \
  ww agent create zora \
  --namespace witwave-self \
  --workspace witwave-self \
  --with-persistence \
  --backend claude \
  --harness-env TASK_TIMEOUT_SECONDS=7200 \
  --harness-env CONVERSATIONS_AUTH_DISABLED=true \
  --backend-env claude:TASK_TIMEOUT_SECONDS=7200 \
  --backend-env claude:CONVERSATIONS_AUTH_DISABLED=true \
  --backend-secret-from-env claude=CLAUDE_CODE_OAUTH_TOKEN \
  --backend-secret-from-env claude=GITHUB_TOKEN \
  --backend-secret-from-env claude=GITHUB_USER \
  --gitsync-bundle https://github.com/witwave-ai/witwave.git@main:.agents/self/zora \
  --gitsync-secret-from-env GITSYNC_USERNAME:GITSYNC_PASSWORD
```

After zora deploys, her 60-minute heartbeat starts firing the `dispatch-team` decision loop. She'll begin reading peer
state from memory, applying the priority policy in her CLAUDE.md, and dispatching the appropriate peer
(iris/kira/nova/evan/finn/felix/piper) via A2A. Until then, all peer dispatches are user-initiated.

## Step 8 — Deploy finn

Finn is the team's gap-fixer — finds and fills functionality gaps via the `gap-work` skill. Risk-tier 1-10 gated (starts
at tier 1 cosmetic; advances per zora's polish-tier control as low-risk territory exhausts clean). Same deployment shape
as evan — one `claude` backend, identity from `.agents/self/finn/`, commits-locally / iris-pushes contract.

```bash
mise exec -- scripts/sops-exec-env.py .agents/self/team.sops.env .agents/self/finn/agent.sops.env -- \
  ww agent create finn \
  --namespace witwave-self \
  --workspace witwave-self \
  --with-persistence \
  --backend claude \
  --harness-env TASK_TIMEOUT_SECONDS=7200 \
  --harness-env CONVERSATIONS_AUTH_DISABLED=true \
  --backend-env claude:TASK_TIMEOUT_SECONDS=7200 \
  --backend-env claude:CONVERSATIONS_AUTH_DISABLED=true \
  --backend-secret-from-env claude=CLAUDE_CODE_OAUTH_TOKEN \
  --backend-secret-from-env claude=GITHUB_TOKEN \
  --backend-secret-from-env claude=GITHUB_USER \
  --gitsync-bundle https://github.com/witwave-ai/witwave.git@main:.agents/self/finn \
  --gitsync-secret-from-env GITSYNC_USERNAME:GITSYNC_PASSWORD
```

## Step 9 — Deploy felix

Felix is the team's feature agent — implements new product capabilities inside the tier ceiling documented in
`.agents/self/README.md`. Same deployment shape as finn: one `claude` backend, identity from `.agents/self/felix/`,
commits locally, and delegates pushes to iris.

```bash
mise exec -- scripts/sops-exec-env.py .agents/self/team.sops.env .agents/self/felix/agent.sops.env -- \
  ww agent create felix \
  --namespace witwave-self \
  --workspace witwave-self \
  --with-persistence \
  --backend claude \
  --harness-env TASK_TIMEOUT_SECONDS=7200 \
  --harness-env CONVERSATIONS_AUTH_DISABLED=true \
  --backend-env claude:TASK_TIMEOUT_SECONDS=7200 \
  --backend-env claude:CONVERSATIONS_AUTH_DISABLED=true \
  --backend-secret-from-env claude=CLAUDE_CODE_OAUTH_TOKEN \
  --backend-secret-from-env claude=GITHUB_TOKEN \
  --backend-secret-from-env claude=GITHUB_USER \
  --gitsync-bundle https://github.com/witwave-ai/witwave.git@main:.agents/self/felix \
  --gitsync-secret-from-env GITSYNC_USERNAME:GITSYNC_PASSWORD
```

## Step 10 — Deploy Piper

Piper is the team's outreach agent — reads team state every 60 minutes and posts substantive events to GitHub
Discussions. She routes Announcements at score ≥9, Progress at 5-8, and stays silent below 5. She is read-only on source
and only writes to her memory namespace and GitHub Discussions. Same deployment shape as the others — one `claude`
backend, identity from `.agents/self/piper/`, no commits-then-iris-pushes flow because she has no commits to push.

```bash
mise exec -- scripts/sops-exec-env.py .agents/self/team.sops.env .agents/self/piper/agent.sops.env -- \
  ww agent create piper \
  --namespace witwave-self \
  --workspace witwave-self \
  --with-persistence \
  --backend claude \
  --harness-env TASK_TIMEOUT_SECONDS=7200 \
  --harness-env CONVERSATIONS_AUTH_DISABLED=true \
  --backend-env claude:TASK_TIMEOUT_SECONDS=7200 \
  --backend-env claude:CONVERSATIONS_AUTH_DISABLED=true \
  --backend-secret-from-env claude=CLAUDE_CODE_OAUTH_TOKEN \
  --backend-secret-from-env claude=GITHUB_TOKEN \
  --backend-secret-from-env claude=GITHUB_USER \
  --gitsync-bundle https://github.com/witwave-ai/witwave.git@main:.agents/self/piper \
  --gitsync-secret-from-env GITSYNC_USERNAME:GITSYNC_PASSWORD
```

Piper's `GITHUB_TOKEN` in `.agents/self/piper/agent.sops.env` must carry `discussion: write` scope on
`witwave-ai/witwave` so her `post-discussion` skill can publish to Announcements + Progress categories. Until the
`piper-agent-witwave` GitHub account exists and the PAT is wired, Piper's `team-pulse` skill runs in draft-only mode
(logs intended posts to her `pulse_log.md` + `drafts/` directory; no `gh api graphql` writes). This lets you calibrate
her voice + scoring before publishing live.

## Step 11 — Deploy Mira

Mira is the team's platform reliability observer — read-only by default, focused on detecting platform bugs/anomalies in
operator health, agent readiness, pod restarts, runtime storage, release posture, upgrade safety, and resource pressure.
Her daily heartbeat runs `platform-health`, records a compact Kubernetes/platform snapshot, and begins historical
analysis once at least three snapshots exist or history spans 24 hours. When something looks problematic, she sends a
distilled finding to zora to route the fix. Systemic, repeated, or fix-needed issues should always become a zora
handoff, not just a private memory entry. Same deployment shape as the other self agents; run it deliberately so her
first rollout can be observed before relying on her heartbeat.

The first snapshot priority is pod/container restart tracking. Mira should capture restart counts each tick, compare
deltas over time, and inspect Kubernetes events plus previous/current container logs when a restart occurs.

```bash
mise exec -- scripts/sops-exec-env.py .agents/self/team.sops.env .agents/self/mira/agent.sops.env -- \
  ww agent create mira \
  --namespace witwave-self \
  --workspace witwave-self \
  --with-persistence \
  --backend codex \
  --harness-env TASK_TIMEOUT_SECONDS=7200 \
  --harness-env CONVERSATIONS_AUTH_DISABLED=true \
  --backend-env codex:TASK_TIMEOUT_SECONDS=7200 \
  --backend-env codex:CONVERSATIONS_AUTH_DISABLED=true \
  --backend-env codex:CODEX_MODEL=gpt-5.5 \
  --backend-env codex:CODEX_REASONING_EFFORT=xhigh \
  --backend-env codex:CODEX_MAX_TOOL_ITERATIONS=10 \
  --backend-env codex:CODEX_DEFAULT_MAX_TOKENS=30000 \
  --backend-env codex:CODEX_STUB_MODE=false \
  --backend-env codex:CODEX_SHELL_ENABLED=true \
  --backend-env codex:CODEX_SHELL_TIMEOUT_SECONDS=45 \
  --backend-env codex:CODEX_SHELL_MAX_OUTPUT_BYTES=16000 \
  --backend-env codex:CODEX_MEMORY_ENABLED=true \
  --backend-env codex:CODEX_MEMORY_ROOT=/workspaces/witwave-self/memory/agents/mira \
  --backend-secret-from-env codex=OPENAI_API_KEY \
  --backend-secret-from-env codex=GITHUB_TOKEN \
  --backend-secret-from-env codex=GITHUB_USER \
  --gitsync-bundle https://github.com/witwave-ai/witwave.git@main:.agents/self/mira \
  --gitsync-secret-from-env GITSYNC_USERNAME:GITSYNC_PASSWORD
```

Mira is intentionally Codex-only in this bootstrap. Her `.witwave/backend.yaml` routes every concern to `codex` with
model `gpt-5.5`, her primary loaded identity document is `.codex/AGENTS.md`, and her Codex skill mirror lives under
`.codex/skills/`. Keep her `.claude/` folder parked in the repo so she can switch back to Claude later without
rebuilding her identity from scratch; when one surface changes, keep the other semantically aligned. Her Codex backend
also sets `CODEX_REASONING_EFFORT=xhigh` for the requested extra-high reasoning posture, caps Codex function-tool loops
with `CODEX_MAX_TOOL_ITERATIONS=10`, gives ad-hoc Codex requests a default `CODEX_DEFAULT_MAX_TOKENS=30000` budget, and
roots the Codex memory tools at `/workspaces/witwave-self/memory/agents/mira`. Her heartbeat also adds
`max-tokens: 30000` so routine platform observation has a hard per-dispatch budget in addition to the daily cadence and
prompt-level tool-call limit.

After Mira is deployed, run her `platform-health` skill once manually before relying on the daily heartbeat. The first
run should report whether the deployed container actually has the read-only tools it needs (`ww`, `kubectl`, `gh`),
create the platform-health snapshot directory under memory, and confirm whether she can send a distilled anomaly report
to zora when needed. Historical anomaly detection will become useful after several heartbeat snapshots accumulate.

## Step 12 — Deploy Milo

Milo is the team's Agent Resources peer. He owns lifecycle hygiene around the agent roster itself: onboarding readiness,
GitHub/profile consistency, credential readiness, avatar/roster drift, safe pause/decommission paths, and role-boundary
clarity. Milo is intentionally separate from Mira: Mira watches whether the platform is healthy enough for agents to
run; Milo watches whether the team membership and lifecycle surfaces are coherent enough for the roster to make sense.

Milo needs Kubernetes write access for two reasons. First, his `team-upgrade` skill drives `ww agent upgrade` to keep
the team on the latest release, which patches the `witwaveagents` CR. Second, future lifecycle work includes pod-level
intervention (evicting a stuck agent pod, cleaning up namespace-local artifacts during an approved pause/decommission).
Use the operator-managed `agentLifecycle` preset: it is `namespaceWrite` (bounded namespace-local remediation — still no
secrets / RBAC / raw pod creation / cluster-scoped resources) **plus** `patch` on `witwaveagents`. Pair it with the
`agentImagePatchPolicy` admission policy (below) so that CR-patch can only change image tags/digests, not repositories
or backend wiring.

His heartbeat runs `roster-audit` hourly (a read-only roster directory), and a separate `team-upgrade` job advances any
in-flight upgrade campaign one agent at a time. Deploying him gives the team a reachable A2A identity, mounted config,
credentials, workspace access, persistence, and the agentLifecycle Kubernetes API identity.

Milo also deploys as the team's **model canary**: his `.witwave/backend.yaml` pins `claude-opus-4-8` and the create
command sets `CLAUDE_EFFORT=max`, so he runs Opus 4.8 at maximum reasoning effort. This is deliberate — he is the first
agent on 4.8/Max, so we prove the model + effort level work on him before rolling them to the rest of the team. (Model
and effort are config, not image tags, so a team-wide rollout is a separate config/git change Milo recommends once he's
proven — not something his `team-upgrade` image rollout does.)

```bash
mise exec -- scripts/sops-exec-env.py .agents/self/team.sops.env .agents/self/milo/agent.sops.env -- \
  ww agent create milo \
  --namespace witwave-self \
  --workspace witwave-self \
  --with-persistence \
  --backend claude \
  --kubernetes-api-access=agentLifecycle \
  --harness-env TASK_TIMEOUT_SECONDS=7200 \
  --harness-env CONVERSATIONS_AUTH_DISABLED=true \
  --backend-env claude:TASK_TIMEOUT_SECONDS=7200 \
  --backend-env claude:CONVERSATIONS_AUTH_DISABLED=true \
  --backend-env claude:CLAUDE_EFFORT=max \
  --backend-secret-from-env claude=CLAUDE_CODE_OAUTH_TOKEN \
  --backend-secret-from-env claude=GITHUB_TOKEN \
  --backend-secret-from-env claude=GITHUB_USER \
  --gitsync-bundle https://github.com/witwave-ai/witwave.git@main:.agents/self/milo \
  --gitsync-secret-from-env GITSYNC_USERNAME:GITSYNC_PASSWORD
```

After Milo is deployed, verify the Kubernetes API identity before giving him any upgrade or lifecycle work:

```bash
# agentLifecycle adds patch on witwaveagents — this is what `ww agent upgrade` needs →
kubectl auth can-i patch witwaveagents \
  --namespace witwave-self \
  --as system:serviceaccount:witwave-self:milo

# the namespaceWrite half, reserved for future pod-lifecycle work →
kubectl auth can-i delete pods \
  --namespace witwave-self \
  --as system:serviceaccount:witwave-self:milo

# still withheld →
kubectl auth can-i get secrets \
  --namespace witwave-self \
  --as system:serviceaccount:witwave-self:milo
```

The expected answers are `yes`, `yes`, and `no`.

**Bound the CR-patch to image versions.** `agentLifecycle` grants `patch` on the whole `witwaveagents` resource; the
`agentImagePatchPolicy` admission policy narrows that to image tags/digests so Milo cannot repoint an image repository
or rewire backends. The enable + service-account scope for the self-team lives in a checked-in operator values overlay,
[`.agents/self/operator-values.yaml`](operator-values.yaml), so it is Helm-managed and survives operator reinstalls (the
chart default stays `enabled: false` — the scope is environment-specific):

```yaml
agentImagePatchPolicy:
  enabled: true
  constrainedServiceAccounts:
    - system:serviceaccount:witwave-self:milo
```

Apply it through the `ww` CLI so the policy is owned by the `witwave-operator` Helm release (not a one-off
`kubectl apply` of `helm template` output, which a later `ww operator upgrade`/reinstall would silently drop or collide
with):

```bash
# Fresh install — fold the policy in from the start:
ww operator install -f .agents/self/operator-values.yaml

# Operator already installed — turn the policy on for the existing release:
ww operator upgrade -y -f .agents/self/operator-values.yaml
```

`ww operator upgrade` reuses previously-applied values (Helm reset-then-reuse), so you only pass `-f` on the run that
first enables the policy. Every subsequent plain `ww operator upgrade` keeps it enforcing while still rolling the
embedded chart's new defaults (the operator image tag tracks the embedded chart version — it is **not** reset).

**Adopting a VAP that was applied out-of-band.** If the policy is already live from an earlier raw `kubectl apply` (so
it exists but is not owned by the release), the values-driven upgrade above would fail with _"exists and cannot be
imported into the current release"_. Hand the two cluster-scoped objects to Helm first — this is an in-place annotate,
so the policy keeps enforcing throughout (no gap where Milo could over-patch):

```bash
for kind in validatingadmissionpolicy validatingadmissionpolicybinding; do
  kubectl annotate "$kind" witwave-operator-agent-image-patch \
    meta.helm.sh/release-name=witwave-operator \
    meta.helm.sh/release-namespace=witwave-system --overwrite
  kubectl label "$kind" witwave-operator-agent-image-patch \
    app.kubernetes.io/managed-by=Helm --overwrite
done

# Now the release can adopt them in place:
ww operator upgrade -y -f .agents/self/operator-values.yaml
```

**Verify** the policy is enforcing and Helm-owned:

```bash
# Owned by the release (managed-by=Helm + the two meta.helm.sh annotations present):
kubectl get validatingadmissionpolicy witwave-operator-agent-image-patch \
  -o jsonpath='{.metadata.labels.app\.kubernetes\.io/managed-by}{"\n"}'

# Enforcing: a repository repoint by Milo's SA is denied, a tag bump is allowed.
# (Run from a context that can impersonate; --as needs cluster RBAC.)
kubectl patch witwaveagent iris -n witwave-self --type=json \
  -p '[{"op":"replace","path":"/spec/image/repository","value":"evil/harness"}]' \
  --as system:serviceaccount:witwave-self:milo --dry-run=server
# → expect: denied by validating admission policy "...-agent-image-patch"
```

## Verify the team

After all ten agents deploy, verify the workspace binding:

```bash
ww agent list \
  --namespace witwave-self
```

`ww agent list` should now show ten rows (`iris`, `kira`, `nova`, `evan`, `zora`, `finn`, `felix`, `piper`, `mira`,
`milo`) all in state `Ready`. `ww workspace status witwave-self` should report all ten under the bound-agents section.
zora's heartbeat fires every 60 minutes; her next tick will discover the team and start dispatching cadence-floor work.

## Tear it down

Tear down in reverse order: agents, workspace, operator, namespaces. Each command is destructive.

Delete each agent (cascades the pod, Service, per-backend PVC `<name>-<backend>-data`, and the ww-managed
`<name>-<backend>` Secret; `--delete-git-secret` also reaps the per-agent `<name>-gitsync` Secret minted by
`--gitsync-secret-from-env`):

```bash
ww agent delete milo \
  --namespace witwave-self \
  --delete-git-secret \
  --yes
```

```bash
ww agent delete mira \
  --namespace witwave-self \
  --delete-git-secret \
  --yes
```

```bash
ww agent delete piper \
  --namespace witwave-self \
  --delete-git-secret \
  --yes
```

```bash
ww agent delete felix \
  --namespace witwave-self \
  --delete-git-secret \
  --yes
```

```bash
ww agent delete finn \
  --namespace witwave-self \
  --delete-git-secret \
  --yes
```

```bash
ww agent delete zora \
  --namespace witwave-self \
  --delete-git-secret \
  --yes
```

```bash
ww agent delete evan \
  --namespace witwave-self \
  --delete-git-secret \
  --yes
```

```bash
ww agent delete nova \
  --namespace witwave-self \
  --delete-git-secret \
  --yes
```

```bash
ww agent delete kira \
  --namespace witwave-self \
  --delete-git-secret \
  --yes
```

```bash
ww agent delete iris \
  --namespace witwave-self \
  --delete-git-secret \
  --yes
```

Delete the workspace (removes both `source` and `memory` PVCs — data is gone unless the volume's reclaim policy is
`Retain`):

```bash
ww workspace delete witwave-self \
  --namespace witwave-self \
  --yes
```

Uninstall the operator and drop the CRDs:

```bash
ww operator uninstall \
  --namespace witwave-system \
  --delete-crds \
  --yes
```

Drop the namespaces:

```bash
kubectl delete namespace witwave-self witwave-system
```
