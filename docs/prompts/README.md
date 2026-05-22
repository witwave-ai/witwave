# Prompts

Prompts are markdown files with YAML frontmatter that define what gets dispatched to a backend LLM. Each prompt type
differs only in how and when it is triggered — the body is always the prompt text sent to the agent.

## Prompt Types

| Type                              | File location                 | Trigger                                  |
| --------------------------------- | ----------------------------- | ---------------------------------------- |
| [Heartbeat](heartbeat.md)         | `.witwave/HEARTBEAT.md`       | Cron schedule (single file)              |
| [Jobs](jobs.md)                   | `.witwave/jobs/*.md`          | Cron schedule (one file per job)         |
| [Tasks](tasks.md)                 | `.witwave/tasks/*.md`         | Calendar window (days, time, date range) |
| [Triggers](triggers.md)           | `.witwave/triggers/*.md`      | Inbound HTTP POST                        |
| [Continuations](continuations.md) | `.witwave/continuations/*.md` | Upstream prompt completion               |
| [Webhooks](webhooks.md)           | `.witwave/webhooks/*.md`      | Outbound HTTP after any prompt completes |

All prompt files support `model:` and `agent:` frontmatter fields to override the default backend routing on a
per-prompt basis. All prompt files also support `consensus:` (a list of backend entries to fan out to; see below) and
`max-tokens:` (per-dispatch token budget; returns partial response when reached).

### Consensus

`consensus` is a YAML list of backend entries. Each entry specifies a `backend` glob pattern and an optional `model`
override. An empty list (the default) disables consensus — the prompt is dispatched to the single routing target.

```yaml
consensus:
  - backend: "claude" # exact backend ID
    model: "claude-opus-4-7" # optional model override
  - backend: "openai*" # glob pattern — matches all openai backends
  - backend: "claude"
    model: "claude-haiku-4-5" # same backend, different model = two parallel calls
```

When consensus is active, the prompt is dispatched to every matched `(backend, model)` pair concurrently, then the
responses are aggregated:

- **Binary responses** (yes/no/agree/disagree variants): majority vote. The default backend breaks ties.
- **Freeform responses**: a synthesis prompt is dispatched to the default backend, which merges the collected responses
  into a single coherent answer.

The same backend can appear twice with different models — each combination is treated as a distinct call.
