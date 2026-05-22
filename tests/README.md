# Smoke Tests

End-to-end smoke tests for the witwave autonomous agent platform, exercised against the operator-managed test team under
`.agents/test/` (`bob` and `fred` in the `witwave-test` namespace by default).

## Running

The tests are markdown specs designed to be executed by an agent (Claude Code, OpenAI, or a human) that reads each file
in order and follows the instructions. There is no central test runner; each spec is self-contained.

```text
000-init.md      - install/check operator, create workspace, create Bob/Fred with ww, poll readiness, start port-forwards
001...025        - individual smoke checks
900-cleanup.md   - delete the test agents/workspace and stop port-forwards
```

Stop the run early if `000-init.md` fails. The leaf specs assume `000-init.md` has forwarded Bob's app port on
`localhost:8099`, Bob's metrics port on `localhost:9099`, and Fred's app port on `localhost:8098`. Specs that used to
create temporary prompt files locally are disabled until they are converted into precommitted gitSync fixtures. Local
file edits do not affect an operator-managed agent that is syncing from GitHub. `000-init.md` binds Bob and Fred to the
`witwave-test` workspace.

### Check the spec's `enabled` flag before running

**Before executing any spec, read its frontmatter and verify `enabled: true`.** A spec with `enabled: false` must be
skipped entirely. Do not dispatch the test prompt, do not curl the endpoint, and do not look in the conversation log.
Report it as `skipped` in the output table with the reason pulled from the spec's description or a nearby comment.

Why this matters: `enabled: false` usually signals that the underlying fixture (a disabled job, a removed backend, an
absent env var) is not present in the running deployment. Running the spec anyway produces a false-negative failure that
masks real issues. The check is cheap:

```bash
awk '/^---$/{c++; if (c==2) exit} c==1 && /^enabled:/{print}' tests/014-continuation-fires.md
# enabled: true   # run it
# enabled: false  # skip it, record as skipped
```

Agents exercising the suite should default to **skip** when `enabled:` is missing or unparseable, and should log the
decision so the reviewer can tell skip-from-spec apart from skip-from-absent-fixture.

## Framework conventions

- **Numbering**: `000` is init; `9xx` is teardown; everything else is a leaf test. Sub-tests share a base number with
  letter suffixes (`003.a`, `003.b`).
- **Each spec must declare `description` and `enabled` in YAML frontmatter.**
- **Tests should be idempotent**: re-running after a partial failure must not require manual cleanup.
- **Conversation evidence comes from `ww conversation`** for deployed agents. Local repo `logs/` directories are runtime
  placeholders and are not the source of truth for operator-managed smoke runs.
- **Code bugs are findings, not fixes.** Each spec ends with a "do not fix code bugs" note. Tooling/infra fixes are
  expected and welcome.

## Trigger auth contract

The harness rejects every trigger POST that lacks either a per-trigger HMAC secret or a Bearer token matching
`TRIGGERS_AUTH_TOKEN` (security-by-default since 2026-04-12). `000-init.md` expects the run to start from a shell loaded
with `.agents/test/team.sops.env`; if `TRIGGERS_AUTH_TOKEN` is missing, it generates one and keeps it in the current
shell for the run. Specs use:

```bash
-H "Authorization: Bearer ${TRIGGERS_AUTH_TOKEN:?set TRIGGERS_AUTH_TOKEN before running smoke specs}"
```

Bob's bootstrap also sets the webhook env vars used by the webhook specs: `WEBHOOK_TEST_HOST`,
`WEBHOOK_TEST_URL_FEATURE_SINK`, `WEBHOOK_TEST_URL_WEBHOOK_SINK`, `WEBHOOK_TEST_TOKEN`, and `WEBHOOK_TEST_BEARER`.

## Required Tabular Output

After running the suite, **produce a markdown table summarising every test**. This is the canonical artefact the run
delivers; without it, results are scattered across individual spec outputs and hard to triage. Use exactly these
columns, in this order:

| Column     | Required | Notes                                                                                    |
| ---------- | -------- | ---------------------------------------------------------------------------------------- |
| `Test`     | yes      | Numeric ID + suffix, e.g. `003.a`.                                                       |
| `Name`     | yes      | Short name from frontmatter or filename, e.g. `session-init`.                            |
| `Status`   | yes      | One of `pass`, `fail`, `skipped`, `deferred`.                                            |
| `Evidence` | yes      | Concrete proof: log line excerpt, HTTP code, returned token. One sentence, <= 120 chars. |
| `Notes`    | optional | Why deferred, what blocked, follow-up issue numbers.                                     |

Example:

```markdown
| Test  | Name                          | Status   | Evidence                                           | Notes    |
| ----- | ----------------------------- | -------- | -------------------------------------------------- | -------- |
| 001   | heartbeat-fires               | pass     | A2A round-trip returned `HEARTBEAT_OK`             |          |
| 002   | job-executes                  | pass     | conversation: `Job: ping` -> `JOB_OK`              |          |
| 003.a | session-init                  | pass     | A2A response: `SESSION_INIT_OK`                    |          |
| 008   | heartbeat-session-persistence | deferred | not run - needs heartbeat schedule swap + git push | deferred |
| 020   | metrics-aggregation           | pass     | `/metrics` returned backend + harness series       |          |
```

After the table, include:

- **Bugs surfaced**: anything that looked like a code bug, with reproduction steps. Do not fix; file an issue.
- **Tooling/infra fixes applied**: what was patched to keep the suite running, with commit SHA(s) if committed.
- **Deferred**: a one-line reason per skip/defer, ideally with the missing fixture path.

## Test Inventory

| Test  | Surface                                               |
| ----- | ----------------------------------------------------- |
| 000   | init: deploy with ww, poll ready, start port-forwards |
| 001   | heartbeat fires                                       |
| 002   | job executes                                          |
| 003.a | session init                                          |
| 003.b | session continuity                                    |
| 004   | trigger fires                                         |
| 005   | health endpoints                                      |
| 006   | trigger discovery                                     |
| 007.a | backend routing                                       |
| 007.b | backend model                                         |
| 008   | heartbeat session persistence                         |
| 009   | trigger dedup                                         |
| 010   | trigger payload                                       |
| 011   | job session persistence                               |
| 012   | task fires                                            |
| 013   | task continuation                                     |
| 014   | continuation fires                                    |
| 015   | continuation delayed                                  |
| 016   | continuation chaining                                 |
| 017   | continuation on-error                                 |
| 018   | continuation trigger-when                             |
| 019   | enabled-false suppression                             |
| 020   | metrics aggregation                                   |
| 021   | webhook delivery                                      |
| 022   | webhook env-url interpolation                         |
| 023   | webhook headers                                       |
| 024   | webhook extract                                       |
| 025.a | Claude memory                                         |
| 025.b | OpenAI memory                                         |
| 025.c | Gemini memory                                         |
| 900   | cleanup                                               |
