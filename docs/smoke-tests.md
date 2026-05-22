# Smoke Tests

Two distinct smoke surfaces live in this repo:

1. **Deployed-agent conversation smoke** (this document, below). Tests that the active test team (`bob`, `fred`)
   produces the expected canonical responses. The current CLI/operator test deployment runs Claude-only by default: Bob
   and Fred are deployed, while Bob's OpenAI/Gemini fixtures and the Jack/Luke parity scaffolds stay dormant until
   credentials and budget are available.
2. **CLI hello-world smoke** (`scripts/smoke-ww-agent.sh`). Tests that the `ww agent` subtree works end-to-end against a
   real cluster: `create`, `list`, `status`, `send`, `logs`, `events`, `delete`, plus validation paths. It creates one
   throwaway echo-backed agent, asserts expected output, and deletes it at the end.

Run the CLI smoke after cutting a new `ww` release against a cluster with the operator installed:

```bash
ww operator install
./scripts/smoke-ww-agent.sh
```

Knobs via env: `WW_BIN`, `WW_SMOKE_NS`, `WW_SMOKE_AGENT`, `WW_SMOKE_KEEP`. See the script header for details.

This document describes the deployed-agent conversation smoke: what to look for in the conversation log after deploying
the test team. All active tests run automatically on deploy through run-once jobs or recurring schedules. Check
conversation evidence with:

```bash
ww conversation list --namespace witwave-test --agent bob --expand
ww conversation list --namespace witwave-test --agent fred --expand
```

The authoritative model for each call is in the conversation log, not the response text. Models often misreport their
own version.

---

## Deploying

Deploy the test team through the CLI/operator bootstrap:

```bash
# Read the walkthrough, then run the `ww agent create` commands there.
cat .agents/test/bootstrap.md
```

The bootstrap loads shared credentials from `.agents/test/team.sops.env` through `scripts/sops-exec-env.py`. Required
SOPS-backed variables are `CLAUDE_CODE_OAUTH_TOKEN`, `GITSYNC_USERNAME`, and `GITSYNC_PASSWORD`. Set
`TRIGGERS_AUTH_TOKEN` when you plan to run the manual trigger curl checks below; otherwise the bootstrap command
generates one for the current shell.

The bootstrap also creates a small `witwave-test` workspace and binds every deployed test agent to it, including Bob,
Fred, and any promoted Jack/Luke parity agents.

---

## Bob

Bob is the larger smoke surface. In the default test deployment, every active Bob route targets Claude. OpenAI and Gemini
configs remain in the tree as disabled/parked fixtures for future multi-backend runs.

### Jobs - run once on deploy

These fire immediately when the stack comes up. Look for them near the top of the conversation log.

| Job                          | Agent      | What to check                                                                                                                                        |
| ---------------------------- | ---------- | ---------------------------------------------------------------------------------------------------------------------------------------------------- |
| `backend-check-claude`       | bob-claude | Response identifies itself as Claude; logged under `bob-claude`                                                                                      |
| `model-check-claude-default` | bob-claude | `model` field in log matches the default Claude model in `backend.yaml`                                                                              |
| `model-check-claude-opus`    | bob-claude | `model` field = `claude-opus-4-7`                                                                                                                    |
| `model-check-claude-sonnet`  | bob-claude | `model` field = `claude-sonnet-4-6`                                                                                                                  |
| `model-check-claude-haiku`   | bob-claude | `model` field = `claude-haiku-4-5`                                                                                                                   |
| `animal-memory-claude`       | bob-claude | Response acknowledges hamsters; seeds session memory                                                                                                 |
| `budget-exceeded-claude`     | bob-claude | `system` entry in log: `Budget exceeded: N tokens used of 10 limit.` - verifies `max-tokens` enforcement surfaces as a system conversation-log entry |
| `fanin-a`                    | default    | `FANIN_A_OK` response; first leg of fan-in continuation test                                                                                         |
| `fanin-b`                    | default    | `FANIN_B_OK` response; second leg of fan-in continuation test                                                                                        |

Disabled OpenAI job fixtures remain under `.agents/test/bob/.witwave/jobs/` with `enabled: false`, including GPT-5.5 and
GPT-5.3-OpenAI model checks. They should not appear in the active smoke log unless the OpenAI backend is deliberately
re-enabled.

### Jobs - continuation chains

These fire once and trigger continuation chains. Check that all steps appear in order in the log.

| Job                    | Continuation chain                                              | What to check                                                                                               |
| ---------------------- | --------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------- |
| `animal-memory-claude` | `animal-memory-claude-turtles` -> `animal-memory-claude-recall` | Final recall response names both hamsters and turtles; all three logged under `bob-claude` in session order |

### Manual memory checks

| Fixture                        | Backend     | What to check                                                                                                          |
| ------------------------------ | ----------- | ---------------------------------------------------------------------------------------------------------------------- |
| `CLAUDE.md` memory instruction | bob-claude  | Spec `025.a` writes a typed project memory under `/workspaces/witwave-test/memory/agents/bob/` and updates `MEMORY.md` |
| `AGENTS.md` memory instruction | jack-openai  | Spec `025.b`, disabled by default, performs the same namespace/index check after Jack is promoted                      |
| `GEMINI.md` memory instruction | luke-gemini | Spec `025.c`, disabled until Gemini can write/read workspace files; same-session recall is not memory parity           |

### Jobs - recurring

| Job            | Schedule     | What to check                                                                  |
| -------------- | ------------ | ------------------------------------------------------------------------------ |
| `ping`         | every 15 min | `JOB_OK` response; `continuation-ping` fires immediately after in same session |
| `ping-claude`  | every 30 min | `PING_OK` under `bob-claude`                                                   |
| `ping-default` | every 10 min | `PING_OK` under default backend (`bob-claude`)                                 |

### Heartbeat

The heartbeat is configured in `HEARTBEAT.md` with its own cron schedule and dispatches a prompt through the heartbeat
scheduler. Verifying it catches whole-heartbeat-subsystem outages that a job smoke test cannot.

| Dispatcher | Schedule                     | What to check                                                                              |
| ---------- | ---------------------------- | ------------------------------------------------------------------------------------------ |
| heartbeat  | every hour (top of the hour) | `HEARTBEAT_OK` appears in the conversation log within about 1 minute of each hour boundary |

### Tasks

Tasks fire based on time windows. Check the log after the window opens.

| Task               | Window                                                    | What to check                                                                            |
| ------------------ | --------------------------------------------------------- | ---------------------------------------------------------------------------------------- |
| `task-ping`        | daily at 00:00 UTC                                        | `TASK_OK`; `continuation-ping-delayed` fires about 10s later in same session             |
| `task-smoke`       | any time (24h window)                                     | `TASK_SMOKE_OK`                                                                          |
| `task-ping-loop`   | Mon-Fri 00:00 UTC, loops every 10m for 1h                 | `LOOP_OK` first two fires, `LOOP_DONE` on third; task stops looping after `LOOP_DONE`    |
| `task-ping-window` | daily 08:00 America/Chicago, 2h window                    | `WINDOW_OK`; check `ts` in log is within the Chicago window                              |
| `task-ping-full`   | Mon-Fri 08:00 America/Chicago, 4h window, loops every 30m | `FULL_DONE` on first fire; task stops; logged under `bob-claude` with `claude-haiku-4-5` |

### Triggers

Triggers require a manual POST. Forward Bob's service to the historical local smoke port:

```bash
kubectl port-forward svc/bob 8099:8000 -n witwave-test
```

Then, in another terminal:

```bash
curl -X POST http://localhost:8099/triggers/ping \
  -H "Authorization: Bearer $TRIGGERS_AUTH_TOKEN"
```

| Trigger   | Endpoint                                                  | What to check                                                                         |
| --------- | --------------------------------------------------------- | ------------------------------------------------------------------------------------- |
| `ping`    | `POST /triggers/ping`                                     | `TRIGGER_OK` in log; `continuation-trigger-ping` fires about 5s later in same session |
| `echo`    | `POST /triggers/echo` body `{"token":"abc123"}`           | Response is `ECHO:abc123`                                                             |
| `webhook` | `POST /triggers/webhook` with valid `X-Hub-Signature-256` | Response summarizes the payload                                                       |

### Continuations

Continuations are triggered by the jobs and tasks above. They should not need to be fired manually; verify they appear
in the correct session, in order, after their upstream.

| Continuation                   | Upstream                                    | What to check                                                                                   |
| ------------------------------ | ------------------------------------------- | ----------------------------------------------------------------------------------------------- |
| `continuation-ping`            | `job:ping`                                  | Fires immediately after `ping` job; same session; `CONTINUATION_OK`                             |
| `continuation-ping-delayed`    | `task:task-ping`                            | Fires about 10s after `task-ping`; same session; `CONTINUATION_DELAYED_OK`                      |
| `continuation-trigger-ping`    | `trigger:ping`                              | Fires about 5s after trigger ping; same session; `CONTINUATION_TRIGGER_OK`                      |
| `animal-memory-claude-turtles` | `job:animal-memory-claude`                  | Fires after deploy; adds turtles to Claude session                                              |
| `animal-memory-claude-recall`  | `continuation:animal-memory-claude-turtles` | Fires after turtles; response names both hamsters and turtles                                   |
| `continuation-fanin-test`      | `job:fanin-a` + `job:fanin-b`               | Fires only after both fan-in jobs complete; `FANIN_OK` response; verifies fan-in state tracking |

Disabled OpenAI continuation fixtures remain in place with `enabled: false` and should stay quiet in the default smoke
run.

### Webhooks

Webhooks fire outbound HTTP POSTs after matching upstream completions. The bootstrap sets
`WEBHOOK_TEST_URL_FEATURE_SINK`, `WEBHOOK_TEST_URL_WEBHOOK_SINK`, `WEBHOOK_TEST_TOKEN`, and `WEBHOOK_TEST_BEARER` on
Bob's harness. Env-derived webhook URLs resolve only through `url-env-var`; inline `{{env.VAR}}` in the `url:` field is
not supported.

| Webhook        | Fires when                                   | What to check                                                                          |
| -------------- | -------------------------------------------- | -------------------------------------------------------------------------------------- |
| `chain-test`   | A2A response contains `WEBHOOK_FIRE`         | POST delivered to `webhook-sink` trigger; `WEBHOOK_CHAIN_OK` appears in log            |
| `test-env-url` | A2A response contains `WEBHOOK_ENV_URL_FIRE` | POST delivered to the URL in `WEBHOOK_TEST_URL_FEATURE_SINK`; `FEATURE_SINK_OK` in log |
| `test-extract` | A2A response contains `WEBHOOK_EXTRACT_FIRE` | LLM extraction runs; extracted word appears in POST body; `FEATURE_SINK_OK` in log     |
| `test-headers` | A2A response contains `WEBHOOK_HEADERS_FIRE` | POST includes `X-Test-Token` and `X-Static-Header` headers; `FEATURE_SINK_OK` in log   |

---

## Fred

Fred runs a single Claude backend. This is the simpler multi-agent deployment check.

| Test                      | What to check                                               |
| ------------------------- | ----------------------------------------------------------- |
| `ping` job (every 15 min) | `JOB_OK` in Fred's conversation log                         |
| `continuation-ping`       | Fires after `ping`; same session; `CONTINUATION_OK`         |
| heartbeat                 | `HEARTBEAT_OK` appears on the configured heartbeat schedule |

---

## Fresh Deploy

To get a clean conversation log, delete and recreate the agents. The operator removes owned Deployments, Services, PVCs,
and backend credential Secrets; `--delete-git-secret` also removes the per-agent gitSync credential Secret minted by the
CLI.

```bash
ww agent delete bob --namespace witwave-test --delete-git-secret --yes || true
ww agent delete fred --namespace witwave-test --delete-git-secret --yes || true
```

Redeploy with `.agents/test/bootstrap.md`. Run-once jobs fire fresh after the new pods become ready.

---

## Viewing the Conversation

Use the CLI conversation surface:

```bash
ww conversation list --namespace witwave-test --agent bob --expand
ww conversation list --namespace witwave-test --agent fred --expand
```

For live harness logs:

```bash
ww agent logs bob --namespace witwave-test --follow
ww agent logs fred --namespace witwave-test --follow
```

---

## Quick Checklist

After deploying, confirm in order:

1. `ww agent status` reports Bob and Fred ready.
2. `GET /health/ready` returns `{"status":"ready"}` on both agents.
3. Run-once jobs appear near the top of Bob's conversation log.
4. Model check logs show correct `model` fields; ignore self-reported model text.
5. Claude agent messages show a non-null `tokens` field in the conversation log.
6. Animal memory chain: hamsters -> turtles -> recall with both animals, on Claude.
7. Memory: Bob follows `CLAUDE.md`, writes a typed project memory under `/workspaces/witwave-test/memory/agents/bob/`,
   and updates `MEMORY.md`.
8. `ping` job fires every 15 min with `continuation-ping` immediately after.
9. Fred's `ping` job and `continuation-ping` appear in Fred's conversation log.
10. Fan-in: `fanin-a` and `fanin-b` both appear; `continuation-fanin-test` fires exactly once after both, with
    `FANIN_OK`.
11. Budget: `budget-exceeded-claude` produces a `system` log entry matching
    `Budget exceeded: N tokens used of 10 limit.`
12. Heartbeat: `HEARTBEAT_OK` appears in the conversation log within about 1 minute of each hour boundary.
13. Disabled OpenAI/Gemini fixtures do not appear in the default Bob smoke log.

---

## Future Smoke Tests

Planned additions are kept here so the intent stays visible without cluttering the active tables above. Promote a row
into the active sections only when the required plumbing exists and the test can stay inside the prompt-in /
conversation-log-out verification pattern.

### Medium Complexity

| Test                                              | What it would verify                                                                                                                   | Why deferred                                                                                          |
| ------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------- |
| **OpenAI backend parity**                          | `backend-check-openai`, `model-check-openai-*`, `animal-memory-openai`, `ping-openai`                                                      | Bob's OpenAI config is present but not deployed by default; Jack is the single-backend OpenAI scaffold. |
| **OpenAI memory parity**                           | Jack's `AGENTS.md` memory check writes a typed project memory and namespace index under `/workspaces/witwave-test/memory/agents/jack/` | Jack requires `OPENAI_API_KEY` and is not deployed in the default Claude-first smoke run.             |
| **Gemini memory parity**                          | Luke's `GEMINI.md` memory check writes the same file-backed namespace/index shape after Gemini filesystem/tool-call support lands      | Luke requires `GEMINI_API_KEY` and the current Gemini fixture cannot write workspace memory files.    |
| **Gemini backend parity**                         | `backend-check-gemini`, `model-check-gemini-*`, `animal-memory-gemini`, `ping-gemini`                                                  | Gemini backend is supported; Luke is the single-backend Gemini scaffold.                              |
| **Consensus fan-out**                             | Multi-model fan-out and synthesis across OpenAI + Claude models                                                                         | Bob's consensus fixtures are present but disabled because they depend on the dormant OpenAI backend.   |
| **Prompt-kind filter on webhooks**                | `notify-on-kind` glob filter; a webhook subscribed to `job:*` fires on job responses but not trigger responses                         | Needs two webhook configs plus matching chain-sink triggers.                                          |
| **Concurrent load ordering**                      | Multiple jobs firing at the same cron tick all complete without scheduler interleaving bugs                                            | Needs three or more jobs with identical `schedule:` plus clear log-ordering assertions.               |
| **Session persistence across pod restart**        | Session ID and memory survive a pod restart                                                                                            | Requires manual deploy-time choreography: seed session, restart pod, verify memory.                   |
| **Per-message `model` override via A2A metadata** | `metadata.model` on an inbound A2A request overrides the routing default and lands in the log                                          | Triggers do not yet propagate `metadata.model` from the HTTP payload to dispatch.                     |

### Not a Fit for This Document

These are real things worth testing, but they do not fit the prompt-in / conversation-log-out pattern. They should live
in a separate integration/e2e suite.

| Test                                                                      | Why it does not fit                                                                          |
| ------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------- |
| Metrics endpoint smoke (`/metrics` scrape asserts specific counters move) | Verification is regex over Prometheus text output, not conversation log.                     |
| Trigger trace/header propagation                                          | Verification is on HTTP headers, not conversation log.                                       |
| Read-only endpoints return well-formed responses                          | curl-and-assert-on-JSON, not conversation log.                                               |
| Agent-card hot-reload                                                     | Verification is on `/.well-known/agent.json`, and requires mutating mounted config mid-test. |
| Strict token-count assertion                                              | This is an invariant check on existing log entries, not a separate dispatched test.          |
