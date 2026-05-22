# Heartbeat

A heartbeat fires on a cron schedule and dispatches a prompt to the agent's default backend. It is defined in a single
`HEARTBEAT.md` file in the agent's `.witwave/` directory.

**Minimal test heartbeat** (fires every hour, verifies scheduler + backend are alive):

```markdown
---
description: Test heartbeat — verifies the scheduler fires correctly.
schedule: "0 * * * *"
enabled: true
---

Respond with HEARTBEAT_OK.
```

**Real heartbeat** (fires every 30 minutes, drives autonomous work):

```markdown
---
description: Proactive work heartbeat — reviews open issues and advances the highest-priority item.
schedule: "*/30 * * * *"
enabled: true
---

You have woken up. Check for the highest-priority approved GitHub Issue and advance it by one meaningful step. Do not
start new work while a prior run is still in progress. Report what you did or why you skipped.
```

## Frontmatter Fields

| Field         | Required | Description                                                                                                                      |
| ------------- | -------- | -------------------------------------------------------------------------------------------------------------------------------- |
| `description` | No       | Human-readable summary                                                                                                           |
| `schedule`    | Yes      | Cron expression (UTC)                                                                                                            |
| `enabled`     | Yes      | `true` to activate, `false` to disable                                                                                           |
| `model`       | No       | Model override passed to the backend; defaults to backend default                                                                |
| `agent`       | No       | Backend ID override (e.g. `openai`); defaults to routing config                                                                   |
| `consensus`   | No       | List of `{backend, model?}` entries to fan out to; empty list (default) disables consensus. Supports glob patterns in `backend`. |
| `max-tokens`  | No       | Token budget for this dispatch. Stop and return partial response when reached.                                                   |
