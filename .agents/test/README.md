# Test Agents

The `.agents/test/` directory is the disposable counterpart to `.agents/self/`. It holds agents used to validate runtime
behavior, operator/CLI wiring, and smoke-test fixtures without touching the self-maintaining team that works on the
repo.

The mission: **prove the platform still boots, routes, schedules, triggers, and records conversation evidence after
config or runtime changes.**

## Secrets

Shared test-team credentials are mirrored into `team.sops.env` as a SOPS-encrypted dotenv file. It currently carries the
Claude OAuth, gitSync, and OpenAI keys used by the test bootstrap and future OpenAI parity runs.

```bash
mise exec -- sops -d .agents/test/team.sops.env
```

The bootstrap loads this file through `scripts/sops-exec-env.py`, so no repo-root plaintext env file is required.

## The team

### Bob - smoke surface

Bob is the primary smoke-test agent. He exercises the broadest active test surface: jobs, tasks, triggers,
continuations, webhooks, routing defaults, model overrides, token-budget handling, fan-in continuations, and parked
multi-backend fixtures.

Bob is Claude-first in the default CLI deployment. His OpenAI and Gemini config directories remain in the repo as parked
fixtures, but those backends are not deployed until API budget/keys are available and the active smoke plan is updated.
Bob is bound to the `witwave-test` workspace in the default deployment.

### Fred - second-agent sanity

Fred is intentionally small. He verifies that the operator can deploy a second named agent with independent scheduler
state, conversation logs, backend storage, heartbeat behavior, and continuation execution.

Fred is Claude-only in the default test deployment and also binds to the `witwave-test` workspace.

### Jack - OpenAI parity scaffold

Jack is a OpenAI-only filesystem scaffold for future backend parity runs. Deploy him with
`ww agent create jack --backend openai` when single-backend OpenAI parity matters.

### Luke - Gemini parity scaffold

Luke is a Gemini-only filesystem scaffold for future backend parity runs. Deploy him with
`ww agent create luke --backend gemini` when single-backend Gemini parity matters.

## Topology

```text
                         ww + witwave-operator
                                  |
                                  v
             +--------------------+--------------------+
             |                                         |
          Bob pod                                  Fred pod
   harness:8000 + claude:8001              harness:8000 + claude:8001
       bound to workspace                    bound to workspace
             |
             | parked repo fixtures, not deployed by default
             v
       Bob .openai / .gemini config

  Jack and Luke are filesystem scaffolds until explicitly created with ww.
```

The test team is intentionally smaller than the self team. The goal is fast feedback and clear conversation-log
evidence, not a permanent autonomous workforce.

## Layout

Each test agent keeps harness runtime configuration in `.witwave/`:

```text
.witwave/
├── agent-card.md
├── backend.yaml
├── HEARTBEAT.md
├── jobs/
├── tasks/
├── triggers/
├── continuations/
└── webhooks/
```

The public agent card source of truth is always `.witwave/agent-card.md`. The Kubernetes Service for a named agent
points at the harness container, so `GET /.well-known/agent.json` returns the harness card, not a backend-sidecar card.

Backend directories only carry backend runtime config:

| Directory  | Purpose                                                         |
| ---------- | --------------------------------------------------------------- |
| `.claude/` | Claude behavior/settings/MCP config for agents that run Claude. |
| `.openai/` | OpenAI behavior/config for OpenAI parity runs.                  |
| `.gemini/` | Gemini behavior config for Gemini fixtures.                     |

Do not add backend-specific `agent-card.md` files here unless a test explicitly needs direct backend-sidecar discovery.
The default smoke deployment treats those as unnecessary drift.

Memory behavior lives in the primary backend identity files: `CLAUDE.md`, `AGENTS.md`, and `GEMINI.md`. The test team
uses the same file-backed contract as the self team: each agent has a private namespace at
`/workspaces/witwave-test/memory/agents/<name>/`, shared team memory lives at the memory root, memory files use typed
frontmatter, and `MEMORY.md` is only an index. Claude and OpenAI parity checks exercise that full namespace/index shape.
Gemini declares the same contract but its parity test remains disabled until the backend exposes filesystem/tool-call
support; same-session recall is not accepted as memory parity.

## Deployment

Deploy through the CLI/operator path documented in the bootstrap:

```bash
sed -n '1,240p' .agents/test/bootstrap.md
```

In practice, Bob and Fred are created with `ww agent create`, `--gitsync-bundle` points at their `.agents/test/<name>`
directories, and `--with-persistence` gives each backend its own operator-owned PVC. Every deployed test agent gets
`--workspace witwave-test`, including Bob, Fred, and promoted Jack/Luke parity agents.

## Future parity surfaces

The parked fixtures exist so the active smoke team can grow deliberately rather than by surprise.

| Surface                  | Current state                                                    | Promotion condition                                             |
| ------------------------ | ---------------------------------------------------------------- | --------------------------------------------------------------- |
| Bob OpenAI backend       | Config present, backend not deployed by default.                 | OpenAI budget/key available and OpenAI smoke rows re-enabled.   |
| Bob Gemini backend       | Config present, backend not deployed by default.                 | Gemini key available and Gemini smoke rows added or re-enabled. |
| Jack OpenAI-only agent   | Filesystem scaffold, deployable through `ww`.                    | Deploy when single-backend OpenAI parity matters.               |
| Luke Gemini-only agent   | Filesystem scaffold, deployable through `ww`.                    | Deploy when single-backend Gemini parity matters.               |
| Consensus smoke fixtures | Prompt files present but disabled because they depend on OpenAI. | Re-enable after OpenAI is active in the test team.              |

## How the smoke loop closes

1. **Create the test workspace** with `ww workspace create witwave-test`; it includes a `memory` volume mounted at
   `/workspaces/witwave-test/memory`.
2. **Deploy Bob and Fred** with `ww agent create` from `.agents/test/bootstrap.md`; bind both to the workspace.
3. **Wait for readiness** with `ww agent status` and `/health/ready` on each harness.
4. **Let run-once fixtures fire** as soon as the backend-ready gate opens.
5. **Inspect conversation evidence** with `ww conversation list --namespace witwave-test --agent <name> --expand`.
6. **Run manual trigger checks** with the same `TRIGGERS_AUTH_TOKEN` used during Bob creation.
7. **Reset with `ww agent delete` and `ww workspace delete`** when a clean run is needed.
8. **Promote parked fixtures deliberately** by changing config, docs, and smoke expectations together.

## Reading further

| File                        | Use                                                        |
| --------------------------- | ---------------------------------------------------------- |
| `.agents/test/bootstrap.md` | Step-by-step test-team bootstrap and reset instructions.   |
| `docs/smoke-tests.md`       | Active smoke-test checklist and conversation-log evidence. |
| `clients/ww/README.md`      | CLI command reference for operator-managed agents.         |
| `.agents/self/README.md`    | Self-maintaining team guide, mirrored by this document.    |
| `AGENTS.md`                 | Full repository architecture and agent layout reference.   |
