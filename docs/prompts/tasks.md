# Tasks

Tasks are calendar-driven scheduled prompts. Unlike jobs (which repeat on a simple cron interval indefinitely), tasks
can be bounded by a daily time window, optional days-of-week selection, and optional start/end date bounds — modeled
after recurring calendar events. When `window-start` is omitted, the task runs once immediately after registration and
exits. Tasks can optionally loop within their window, pausing between iterations and stopping early when the agent
signals completion.

**Minimal test task** (fires once daily at midnight UTC, no window close, no loop):

```markdown
---
name: Task Ping
description:
  Test task — verifies the task scheduler fires and the backend responds correctly. Fires once daily at midnight UTC.
window-start: "00:00"
enabled: true
---

Respond with TASK_OK.
```

**Realistic looping task** (weekdays, Chicago time, 2-hour window, loops every 30 minutes, stops on signal):

```markdown
---
name: Morning Standup Review
description: Reviews open GitHub Issues each weekday morning and summarizes progress.
days: "1-5"
timezone: America/Chicago
window-start: "08:00"
window-duration: 2h
loop: true
loop-gap: 30m
done-when: STANDUP_DONE
---

Review all open GitHub Issues updated in the last 24 hours. Summarize progress, flag blockers, and identify the
highest-priority item for today. When done, respond with STANDUP_DONE.
```

## Frontmatter Fields

| Field             | Required | Description                                                                                                                      |
| ----------------- | -------- | -------------------------------------------------------------------------------------------------------------------------------- |
| `name`            | No       | Display name used in logs and metrics. Defaults to filename stem.                                                                |
| `description`     | No       | Human-readable summary.                                                                                                          |
| `start`           | No       | Earliest date the task is eligible to run (inclusive, `YYYY-MM-DD`). Omit for no lower bound.                                    |
| `end`             | No       | Last date the task is eligible to run (inclusive, `YYYY-MM-DD`). Omit for no expiry.                                             |
| `days`            | No       | Cron weekday expression — numeric (`1-5`, `1,3,5`) or abbreviation (`Mon-Fri`, `Mon,Wed,Fri`). Default: `*` (every day).         |
| `timezone`        | No       | IANA time zone (e.g. `America/New_York`). Applied to `window-start`. Default: `UTC`.                                             |
| `window-start`    | No       | Start of the daily run window (`HH:MM` in the task's time zone). Omit for run-once mode.                                         |
| `window-duration` | No       | Duration of the run window from `window-start`. Format: `30m`, `4h`, `1h30m`. Required to enable looping.                        |
| `loop`            | No       | If `true`, re-run within the window after each completion. Requires `window-duration`. Default: `false`.                         |
| `loop-gap`        | No       | Pause after a run completes before the next iteration. Format: `30s`, `15m`, `1h`, `1h30m`. Default: no pause.                   |
| `done-when`       | No       | Stop looping for the day if the backend response contains this string.                                                           |
| `model`           | No       | Model override passed to the backend.                                                                                            |
| `agent`           | No       | Backend ID override (e.g. `openai`); defaults to routing config.                                                                  |
| `enabled`         | No       | `false` disables without deleting. Default: `true`.                                                                              |
| `consensus`       | No       | List of `{backend, model?}` entries to fan out to; empty list (default) disables consensus. Supports glob patterns in `backend`. |
| `max-tokens`      | No       | Token budget for this dispatch. Stop and return partial response when reached.                                                   |
