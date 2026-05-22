# Test Team Bootstrap

This document bootstraps the disposable test/smoke agents under `.agents/test/`. It is intentionally separate from the
self-team bootstrap: the self team is long-lived and self-maintaining, while the test team is a throwaway CLI/operator
deployment used to validate runtime behavior.

## Goal

After this bootstrap, the cluster is running the active test team:

- One **WitwaveWorkspace** (`witwave-test`) with a tiny `memory` volume, bound to every deployed test agent.
- **bob**: the main smoke-test agent, active on Claude by default.
- **fred**: a smaller Claude-only second-agent sanity check.
- **jack**: OpenAI-only filesystem scaffold, promoted only when OpenAI parity needs a live agent.
- **luke**: Gemini-only filesystem scaffold, promoted only when Gemini parity needs a live agent.

Bob's OpenAI and Gemini config directories stay parked in the repo as future fixtures. They are not part of the default
deployment because the active smoke loop is Claude-first and should stay cheap, deterministic, and fast.

Workspace binding is intentionally uniform: every deployed test agent joins `witwave-test` so smoke runs exercise the
same workspace projection path across the team.

## Prerequisites

- A Kubernetes cluster reachable via your current kubeconfig context.
- `ww`, `kubectl`, and `python3` on your PATH.
- The witwave operator installed or installable through `ww operator install`.
- SOPS/mise configured for this repo. `mise.local.toml` should set `SOPS_AGE_KEY_FILE`, and `scripts/sops-exec-env.py`
  loads the encrypted dotenv file for each `ww` command.

Required encrypted file:

```text
.agents/test/team.sops.env
```

It carries `CLAUDE_CODE_OAUTH_TOKEN`, `GITSYNC_USERNAME`, `GITSYNC_PASSWORD`, and `OPENAI_API_KEY`.

Generate a trigger bearer for the current shell before creating Bob:

```bash
export TRIGGERS_AUTH_TOKEN="${TRIGGERS_AUTH_TOKEN:-$(python3 -c 'import secrets; print(secrets.token_hex(32))')}"
printf 'Using TRIGGERS_AUTH_TOKEN=%s\n' "$TRIGGERS_AUTH_TOKEN"
```

Verify the SOPS decrypt path without printing values:

```bash
mise exec -- scripts/sops-exec-env.py .agents/test/team.sops.env -- \
  sh -lc 'test -n "$CLAUDE_CODE_OAUTH_TOKEN" && test -n "$GITSYNC_USERNAME" && test -n "$OPENAI_API_KEY"'
```

If you promote Luke, export `GEMINI_API_KEY` in the shell or add it to an encrypted SOPS file before running the Luke
command.

## Step 1 - Install or Check the Operator

```bash
ww operator install --yes
ww operator status
```

## Step 2 - Create the Test Workspace

The test workspace is deliberately small. It exists to prove workspace projection, `spec.workspaceRefs[]` wiring, and
the shared memory-volume path in the smoke environment while keeping shared workspace state intentionally disposable.

```bash
ww workspace create witwave-test \
  --namespace witwave-test \
  --create-namespace \
  --volume memory=1Gi:rwo \
  --yes

ww workspace status witwave-test \
  --namespace witwave-test
```

The `memory` volume mounts into bound backend containers at `/workspaces/witwave-test/memory`. Every deployed test agent
is bound in this bootstrap.

## Step 3 - Deploy Bob

Bob carries the broad smoke surface: jobs, tasks, triggers, continuations, webhooks, model checks, fan-in continuations,
and disabled backend fixtures. The service name is `bob`; the service port is `8000`; the Claude sidecar listens on the
`ww` default backend port `8001`.

```bash
mise exec -- scripts/sops-exec-env.py .agents/test/team.sops.env -- \
  ww agent create bob \
  --namespace witwave-test \
  --create-namespace \
  --team test \
  --workspace witwave-test \
  --with-persistence \
  --backend claude \
  --harness-env CONVERSATIONS_AUTH_DISABLED=true \
  --harness-env TRIGGERS_AUTH_TOKEN="$TRIGGERS_AUTH_TOKEN" \
  --harness-env WEBHOOK_TEST_HOST=bob:8000 \
  --harness-env WEBHOOK_TEST_URL_FEATURE_SINK=http://bob:8000/triggers/feature-sink \
  --harness-env WEBHOOK_TEST_URL_WEBHOOK_SINK=http://bob:8000/triggers/webhook-sink \
  --harness-env WEBHOOK_TEST_TOKEN=test-token-abc123 \
  --harness-env WEBHOOK_TEST_BEARER="$TRIGGERS_AUTH_TOKEN" \
  --backend-env claude:CONVERSATIONS_AUTH_DISABLED=true \
  --backend-secret-from-env claude=CLAUDE_CODE_OAUTH_TOKEN \
  --gitsync-bundle https://github.com/witwave-ai/witwave.git@main:.agents/test/bob \
  --gitsync-secret-from-env GITSYNC_USERNAME:GITSYNC_PASSWORD \
  --yes
```

`CONVERSATIONS_AUTH_DISABLED=true` is the local-dev escape hatch that lets `ww conversation list / show / --follow` work
without minting per-agent `CONVERSATIONS_AUTH_TOKEN` secrets. Keep that to disposable smoke clusters; production
deployments should use real bearer tokens.

## Step 4 - Deploy Fred

Fred is intentionally small. He validates the second-agent path with independent scheduler state, conversation logs,
backend storage, heartbeat behavior, and continuation execution.

```bash
mise exec -- scripts/sops-exec-env.py .agents/test/team.sops.env -- \
  ww agent create fred \
  --namespace witwave-test \
  --create-namespace \
  --team test \
  --workspace witwave-test \
  --with-persistence \
  --backend claude \
  --harness-env CONVERSATIONS_AUTH_DISABLED=true \
  --backend-env claude:CONVERSATIONS_AUTH_DISABLED=true \
  --backend-secret-from-env claude=CLAUDE_CODE_OAUTH_TOKEN \
  --gitsync-bundle https://github.com/witwave-ai/witwave.git@main:.agents/test/fred \
  --gitsync-secret-from-env GITSYNC_USERNAME:GITSYNC_PASSWORD \
  --yes
```

## Step 5 - Verify Readiness

```bash
ww workspace status witwave-test --namespace witwave-test
ww agent list --namespace witwave-test
ww agent status bob --namespace witwave-test
ww agent status fred --namespace witwave-test
ww agent send bob --namespace witwave-test "Respond with SMOKE_OK."
ww agent send fred --namespace witwave-test "Respond with FRED_OK."
```

For raw HTTP checks, keep the historical local ports while forwarding to the operator-managed service port:

```bash
kubectl port-forward svc/bob 8099:8000 --namespace witwave-test
```

In another terminal:

```bash
curl http://localhost:8099/.well-known/agent.json
curl http://localhost:8099/health/ready
```

Repeat for Fred when needed:

```bash
kubectl port-forward svc/fred 8098:8000 --namespace witwave-test
curl http://localhost:8098/health/ready
```

`ww workspace status` should list Bob and Fred under bound agents.

## Step 6 - Inspect Conversation Evidence

```bash
ww conversation list --namespace witwave-test --agent bob --expand
ww conversation list --namespace witwave-test --agent fred --expand
```

The active smoke evidence should come from the conversation endpoint or `ww conversation`, not from local repo log
files. The backend logs live inside the agent PVCs when deployed through the operator.

## Step 7 - Run Manual Trigger Checks

Manual trigger checks need the same bearer token that was deployed into Bob. If the token was generated in the current
shell, keep using that shell or export the same value again before redeploying.

```bash
curl -X POST http://localhost:8099/triggers/ping \
  -H "Authorization: Bearer $TRIGGERS_AUTH_TOKEN"
```

Expected result: Bob logs `TRIGGER_OK`, then `continuation-trigger-ping` logs `CONTINUATION_TRIGGER_OK` after the
configured delay.

Echo trigger:

```bash
curl -X POST http://localhost:8099/triggers/echo \
  -H "Authorization: Bearer $TRIGGERS_AUTH_TOKEN" \
  -H "Content-Type: application/json" \
  --data '{"token":"abc123"}'
```

Expected result: response/log content includes `ECHO:abc123`.

## Step 8 - Wipe State for a Fresh Smoke Run

```bash
ww agent delete bob --namespace witwave-test --delete-git-secret --yes || true
ww agent delete fred --namespace witwave-test --delete-git-secret --yes || true
ww workspace delete witwave-test --namespace witwave-test --wait --yes || true
```

`ww agent delete` removes the `WitwaveAgent` CR, the operator-owned Deployment/Service/PVCs, and ww-managed backend
credential Secrets by default. `--delete-git-secret` also removes the per-agent gitSync credential Secret minted by
`--gitsync-secret-from-env`.

Delete bound agents before deleting the workspace; the operator intentionally refuses workspace deletion while agents
still reference it.

Redeploy by rerunning Steps 2 through 4.

## Step 9 - Promote Parked Backends Deliberately

Jack and Luke are the cleaner promotion path for single-backend parity checks:

```bash
mise exec -- scripts/sops-exec-env.py .agents/test/team.sops.env -- \
  ww agent create jack \
  --namespace witwave-test \
  --create-namespace \
  --team test \
  --workspace witwave-test \
  --with-persistence \
  --backend openai \
  --harness-env CONVERSATIONS_AUTH_DISABLED=true \
  --backend-env openai:CONVERSATIONS_AUTH_DISABLED=true \
  --backend-secret-from-env openai=OPENAI_API_KEY \
  --gitsync-bundle https://github.com/witwave-ai/witwave.git@main:.agents/test/jack \
  --gitsync-secret-from-env GITSYNC_USERNAME:GITSYNC_PASSWORD \
  --yes

mise exec -- scripts/sops-exec-env.py .agents/test/team.sops.env -- \
  ww agent create luke \
  --namespace witwave-test \
  --create-namespace \
  --team test \
  --workspace witwave-test \
  --with-persistence \
  --backend gemini \
  --harness-env CONVERSATIONS_AUTH_DISABLED=true \
  --backend-env gemini:CONVERSATIONS_AUTH_DISABLED=true \
  --backend-secret-from-env gemini=GEMINI_API_KEY \
  --gitsync-bundle https://github.com/witwave-ai/witwave.git@main:.agents/test/luke \
  --gitsync-secret-from-env GITSYNC_USERNAME:GITSYNC_PASSWORD \
  --yes
```

Bob, Fred, Jack, and Luke include memory instructions in their primary identity documents (`CLAUDE.md`, `AGENTS.md`, or
`GEMINI.md`). The test team mirrors the self-team file-backed memory contract: each agent writes its own namespace under
`/workspaces/witwave-test/memory/agents/<name>/`, shared team memory stays at the memory root, memory entries use typed
frontmatter, and `MEMORY.md` is an index. Claude and OpenAI parity checks exercise that full shape. Gemini declares the
same contract, but its parity check remains disabled until the backend can write/read workspace files; same-session
recall is not treated as memory parity.

To promote Bob back to multi-backend smoke, update Bob's `backend.yaml`, enable the relevant parked prompt fixtures, and
update `docs/smoke-tests.md` in the same change so the checklist matches the runtime. If memory parity is part of that
promotion, keep Bob's `.openai/AGENTS.md` and `.gemini/GEMINI.md` memory instructions aligned with the active backend
behavior being tested.

When promoting Jack or Luke, keep them bound to `witwave-test` as shown above so parity agents exercise the same
workspace projection path as Bob and Fred.

## Reading Further

- Test-agent inventory: `.agents/test/README.md`
- Active smoke expectations: `docs/smoke-tests.md`
- CLI command reference: `clients/ww/README.md`
- Self-team bootstrap for the long-lived pattern: `.agents/self/bootstrap.md`
