# Jobs

Jobs are defined as `*.md` files in the agent's `.witwave/jobs/` directory. Each file is an independent scheduled
prompt. The job scheduler watches the directory for changes and registers or unregisters jobs at runtime — no restart
needed.

**Minimal test job** (fires every 5 minutes, verifies job scheduler + backend are alive):

```markdown
---
name: Ping
description: Test job — verifies the job scheduler fires and the backend responds correctly.
schedule: "*/5 * * * *"
enabled: true
---

Respond with JOB_OK.
```

**Code review job** (fires hourly, creates GitHub Issues for findings):

```markdown
---
name: Code Review
description: Reviews source code and files GitHub Issues for bugs, reliability issues, and code quality findings.
schedule: "0 * * * *"
enabled: true
---

Review the source code in the repo root and create GitHub Issues for findings.

Steps:

1. Read README.md and CLAUDE.md to understand the purpose and architecture.
2. Read all source files under the repo root.
3. Evaluate for bugs, reliability issues, code quality, and missing observability.
4. For each finding, check whether an open issue already covers it before creating a new one.
5. Create issues using the appropriate type label (type/bug, type/reliability, type/code-quality, type/enhancement).
```

**Question answering job** (fires every 5 minutes, answers open GitHub questions):

```markdown
---
name: Answer Questions
description: Polls for open GitHub Issues with type/question, claims one, researches and answers it.
schedule: "*/5 * * * *"
enabled: true
---

Poll for open unanswered questions in GitHub Issues and answer them.

Steps:

1. List open issues labeled type/question using `gh issue list --state open --label "type/question"`.
2. Take the first unclaimed issue (where the body contains "Claimed by: none").
3. Claim it by updating the body to set "Claimed by: <agent-name>".
4. Research the question thoroughly using repo files, git history, and external sources as needed.
5. Post a complete answer as a comment using `gh issue comment <number> --body "<answer>"`.
6. Close the issue with `gh issue close <number>`.
```

**Development job** (fires every 30 minutes, works through approved issues):

```markdown
---
name: Development
description: Works through approved GitHub Issues in priority order — one item per run.
schedule: "*/30 * * * *"
enabled: true
---

Resolve the highest-priority approved GitHub Issue.

Priority order: type/bug → type/reliability → type/code-quality → type/enhancement. Within each type, work priority/p0
before priority/p1, and so on.

Steps:

1. List all open approved issues using `gh issue list --label "status/approved"`.
2. Select the highest-priority issue with the smallest scope.
3. Read the issue body to understand the problem, file reference, and acceptance criteria.
4. Read the relevant source files.
5. Implement the minimal fix necessary.
6. Close the issue when done.
```

## Frontmatter Fields

| Field         | Required | Description                                                                                                                      |
| ------------- | -------- | -------------------------------------------------------------------------------------------------------------------------------- |
| `name`        | No       | Display name used in logs and metrics; defaults to filename                                                                      |
| `description` | No       | Human-readable summary                                                                                                           |
| `schedule`    | Yes      | Cron expression (UTC)                                                                                                            |
| `enabled`     | No       | `true` (default) to activate, `false` to disable                                                                                 |
| `session`     | No       | Session ID override; defaults to a deterministic UUID                                                                            |
| `model`       | No       | Model override passed to the backend; defaults to backend default                                                                |
| `agent`       | No       | Backend ID override (e.g. `openai`); defaults to routing config                                                                   |
| `consensus`   | No       | List of `{backend, model?}` entries to fan out to; empty list (default) disables consensus. Supports glob patterns in `backend`. |
| `max-tokens`  | No       | Token budget for this dispatch. Stop and return partial response when reached.                                                   |
