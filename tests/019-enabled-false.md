---
description: Verifies that precommitted fixtures with enabled:false stay suppressed in the default smoke deployment.
enabled: true
---

Bob carries several OpenAI fixtures under `.agents/test/bob/.witwave/jobs/` and
`.agents/test/bob/.witwave/continuations/` with `enabled: false`. They are deliberately parked while the default test
deployment is Claude-only.

## Verification

Inspect Bob's conversation evidence:

```bash
ww conversation list --namespace witwave-test --agent bob --expand
```

Check that none of these disabled-fixture markers appear:

- `animal-memory-openai`
- `backend-check-openai`
- `model-check-openai-default`
- `model-check-openai-gpt-5-3-openai`
- `model-check-openai-gpt-5-5`
- `ping-openai`
- `bob-openai`

## Pass/Fail Criteria

The test passes if none of the disabled OpenAI fixtures appear in Bob's conversation log. It fails if any OpenAI fixture
fires or any entry is attributed to `bob-openai` in the default deployment.

**If the failure is caused by a code bug in the system under test, do not fix it; mark the test as failed and report the
issue. Only fix tooling or execution problems that prevent the test itself from running.**
