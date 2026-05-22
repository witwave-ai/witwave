# Continuations

Continuations fire a prompt when a named upstream source completes. They enable chaining without hardcoding sequences
inside prompts. Each continuation is defined as a `*.md` file in the agent's `.witwave/continuations/` directory. The
continuation runner watches the directory for changes and registers listeners at runtime — no restart needed.

A continuation fires every time the upstream completes. A continuation can continue after another continuation, since
continuations fire with `kind="continuation:{name}"`.

**Minimal continuation** (fires after any job named `code-review` succeeds):

```markdown
---
name: Post-Review Summary
description: Summarizes the findings after a code review job completes.
continues-after: job:code-review
---

Summarize the key findings from the code review that just completed and list the top three action items.
```

**Continuation with trigger-when and delay:**

```markdown
---
name: Deploy Notification
description: Fires after a build job signals success, with a 30s delay.
continues-after: job:build
trigger-when: BUILD_OK
on-error: false
delay: 30s
---

The build completed successfully. Post a deployment summary to the team channel.
```

**Continuation after another continuation:**

```markdown
---
name: Final Report
description: Fires after the deploy notification continuation completes.
continues-after: continuation:deploy-notification
---

Generate a final end-to-end report covering the review, build, and deployment steps.
```

## Frontmatter Fields

| Field                  | Required | Description                                                                                                                                                  |
| ---------------------- | -------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `name`                 | No       | Display name used in logs and metrics. Defaults to filename stem.                                                                                            |
| `description`          | No       | Human-readable summary.                                                                                                                                      |
| `continues-after`      | Yes      | Upstream kind to watch. Supports `fnmatch` glob patterns: `job:<name>`, `task:<name>`, `job:*` (any job), `a2a`, `continuation:<name>`, or `*` for any kind. |
| `on-success`           | No       | Fire when upstream succeeds. Default: `true`.                                                                                                                |
| `on-error`             | No       | Fire when upstream errors. Default: `false`.                                                                                                                 |
| `trigger-when`         | No       | Only fire if the upstream response contains this string.                                                                                                     |
| `delay`                | No       | Pause before firing. Format: `30s`, `5m`, `1h`, `1h30m`. Default: no delay.                                                                                  |
| `session`              | No       | Session ID override. Default: inherit upstream session.                                                                                                      |
| `model`                | No       | Model override passed to the backend.                                                                                                                        |
| `agent`                | No       | Backend ID override (e.g. `openai`); defaults to routing config.                                                                                              |
| `enabled`              | No       | `false` disables without deleting. Default: `true`.                                                                                                          |
| `consensus`            | No       | List of `{backend, model?}` entries to fan out to; empty list (default) disables consensus. Supports glob patterns in `backend`.                             |
| `max-tokens`           | No       | Token budget for this dispatch. Stop and return partial response when reached.                                                                               |
| `max-concurrent-fires` | No       | Maximum number of in-flight fires for this continuation at any time. Fires beyond this cap are dropped and counted. Default: `5`.                            |
