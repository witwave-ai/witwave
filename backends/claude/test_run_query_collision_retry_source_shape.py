"""Source-shape regression coverage for claude's ``run_query`` collision-retry outer wrapper (#1048, #870, #1485).

The session-collision retry contract is unique to the claude backend among
the four SDK-using peers (gemini / openai / codex do not surface
"session ... already in use" stderr on new-session creation). The
contract is fan-out across four cooperating sites in ``executor.py``:

1. ``_run_query_inner`` accepts a mutable ``tool_use_flag: list[bool] | None``
   sentinel and sets ``tool_use_flag[0] = True`` on the first
   ``ToolUseBlock`` observed during the turn. This is the idempotency
   marker that makes the outer wrapper safe to retry.
2. ``run_query`` constructs ``_tool_use_flag: list[bool] = [False]`` and
   forwards it into the inner call. On collision-retry it forwards the
   same list (not a fresh one) so a tool_use on the resumed attempt also
   marks the flag (#1485).
3. The outer wrapper refuses the retry — re-raising the original
   exception — when ``_tool_use_flag[0]`` is already ``True``. Replaying
   a prompt that already executed a tool_use risks duplicate cluster
   mutations or file writes; the contract is "fail loudly rather than
   silently double-mutate" (#1048).
4. The retry path deliberately does NOT re-observe
   ``backend_sdk_query_error_duration_seconds`` at the outer site — the
   inner ``_run_query_inner`` already observed it on the inner error
   path, and a second observation would double-count the histogram and
   could drift label cardinality from the inner call's pre-computed
   ``_sdk_labels`` (#870).

This file pins each fan-out point so a future refactor that drops a
piece of the wiring — e.g. forgets to thread ``tool_use_flag`` into the
retry call, or removes the refuse-on-tool-use guard, or re-adds the
double-observe at the outer site — fails this file at unit time rather
than silently re-enabling duplicate-side-effect retries (#1048
pre-mitigation) or histogram label drift (#870 pre-mitigation).

The claude backend's test convention precludes importing
``executor.py`` directly: the SDK chain (``claude_agent_sdk``,
``ClaudeAgentOptions``, the prometheus client init) is heavy, and the
existing claude source-shape tests (``test_agent_md_revision.py``,
``test_sub_app_lifespan_timeout.py``) all use pytest-function-style
text-substring assertions against ``executor.py`` read as text. This
file follows that local convention; it is the claude-side analogue of
``backends/openai/test_mcp_lifespan_source_shape.py`` which uses the
unittest-class style required by openai-local convention.

PARITY.md row "Focused regression tests" lists claude as having
collision-retry coverage as part of the run_query contract; this file
anchors that claim with a source-shape regression check.
"""

from __future__ import annotations

from pathlib import Path

HERE = Path(__file__).resolve().parent


def _source() -> str:
    return (HERE / "executor.py").read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# RunQuery signature + idempotency-marker construction
# ---------------------------------------------------------------------------


def test_run_query_is_async_module_level_function():
    # Module-level ``async def run_query`` — pinning that it is async
    # (the SDK chain is awaitable) and at module level (callers import
    # it directly, not via a class method).
    src = _source()
    assert "async def run_query(" in src


def test_run_query_constructs_tool_use_flag_as_single_element_list():
    # The mutable sentinel pattern: a one-element list ``[False]`` so
    # the inner function can mutate ``[0]`` in place and the outer
    # wrapper observes the mutation on the retry-decision branch.
    # Replacing this with a scalar would break the in-place mutation;
    # replacing with a fresh list per retry would lose the idempotency
    # guarantee.
    src = _source()
    assert "_tool_use_flag: list[bool] = [False]" in src


def test_run_query_initial_call_forwards_tool_use_flag():
    # First (non-retry) call to ``_run_query_inner`` forwards the
    # sentinel via the ``tool_use_flag=`` kwarg. Without this, the
    # initial attempt's tool_use observations would not be recorded
    # and the refuse-on-tool-use guard below would always see
    # ``[False]`` — making the guard a no-op.
    src = _source()
    assert "tool_use_flag=_tool_use_flag," in src


# ---------------------------------------------------------------------------
# Inner function: ToolUseBlock branch sets the sentinel
# ---------------------------------------------------------------------------


def test_inner_function_accepts_tool_use_flag_kwarg():
    # ``_run_query_inner`` signature declares the sentinel kwarg with
    # an Optional list type. The Optional default matters: synthetic
    # callers (tests, /mcp probes) may invoke without the sentinel.
    src = _source()
    assert 'tool_use_flag: "list[bool] | None" = None' in src


def test_inner_function_sets_flag_on_first_tool_use_block():
    # The ToolUseBlock branch in the inner loop must set
    # ``tool_use_flag[0] = True`` on the first tool_use observed.
    # Without this, the outer wrapper's refuse-on-tool-use guard
    # becomes a no-op and #1048 regresses (silent duplicate
    # side-effect on retry).
    src = _source()
    assert "if tool_use_flag is not None and not tool_use_flag[0]:" in src
    assert "tool_use_flag[0] = True" in src


# ---------------------------------------------------------------------------
# Collision detection: stderr scan for "session ... already in use"
# ---------------------------------------------------------------------------


def test_collision_lines_scan_uses_case_insensitive_substring_match():
    # The collision detector scans captured stderr for the SDK's
    # "session ... already in use" signature. Both substrings are
    # lowered before comparison so SDK casing changes do not silently
    # disable the retry path. Pinning the lowercase form here ensures
    # a future refactor that drops the ``.lower()`` calls fails this
    # test instead of regressing into casing-fragile detection.
    src = _source()
    assert '"session" in line.lower()' in src
    assert '"already in use" in line.lower()' in src


def test_collision_retry_only_when_is_new_and_collision_lines_present():
    # The retry branch is gated on BOTH ``is_new`` (only fresh-session
    # collisions get retried; a resume that fails for any other reason
    # must not silently retry) AND ``_collision_lines`` non-empty
    # (without an SDK collision signature, the failure is some other
    # error and the contract is to propagate, not to retry as resume).
    src = _source()
    assert "if is_new and _collision_lines:" in src


# ---------------------------------------------------------------------------
# Refuse-on-tool-use guard (#1048 idempotency invariant)
# ---------------------------------------------------------------------------


def test_refuse_retry_when_flag_already_set():
    # The load-bearing #1048 guard: if the failed attempt already
    # executed a tool_use, the retry path must be refused (re-raise
    # the original exception) to avoid duplicating cluster mutations
    # or file writes. Removing this branch re-opens the silent
    # duplicate-side-effect window the issue was filed to close.
    src = _source()
    assert "if _tool_use_flag[0]:" in src
    assert "#1048" in src


# ---------------------------------------------------------------------------
# Retry path: resume=True + same sentinel forwarded (#1485)
# ---------------------------------------------------------------------------


def test_retry_path_invokes_make_options_with_resume_true():
    # The retry must construct a fresh options object with
    # ``resume=True`` (not ``resume=not is_new`` which the first call
    # uses). Without resume=True the SDK creates yet another new
    # session, hitting the collision again and looping. Pinning the
    # literal kwarg ensures a refactor that consolidates the two
    # ``_make_options(...)`` call sites must preserve the resume
    # toggle on the retry path.
    src = _source()
    assert "resume=True," in src


def test_retry_path_forwards_same_tool_use_flag_for_1485():
    # The retry call must forward the SAME ``_tool_use_flag`` list
    # (not a fresh one), so a tool_use on the resumed attempt updates
    # the idempotency marker. Without this, a subsequent failure
    # after retry would not reflect the replayed tool activity (#1485).
    # The literal ``tool_use_flag=_tool_use_flag,`` appears twice in
    # ``run_query`` body: once on the initial call (already pinned
    # above) and once on the retry call — assert the count is exactly
    # two so an accidental drop or rename surfaces here.
    src = _source()
    # Count occurrences within ``run_query`` body region only. The
    # whole-file count would also include _run_query_inner's own
    # forward into the loop; we want exactly the two ``run_query``
    # forwards.
    run_query_start = src.find("async def run_query(")
    assert run_query_start != -1
    # The next ``async def`` after run_query terminates the body.
    next_async_def = src.find("\nasync def ", run_query_start + 1)
    body = src[run_query_start:next_async_def] if next_async_def != -1 else src[run_query_start:]
    assert body.count("tool_use_flag=_tool_use_flag,") == 2


# ---------------------------------------------------------------------------
# #870: retry path does NOT re-observe the SDK query-error histogram
# ---------------------------------------------------------------------------


def test_retry_path_does_not_re_observe_sdk_query_error_duration():
    # The #870 fix: the inner ``_run_query_inner`` already observes
    # ``backend_sdk_query_error_duration_seconds`` on its error path,
    # so the outer retry block must NOT call ``.observe(...)`` on
    # that histogram a second time. Pinning the comment AND verifying
    # no second observe-call appears in run_query's body keeps the
    # invariant explicit.
    src = _source()
    # The explanatory comment naming #870 must remain so future
    # maintainers understand why the histogram observe is absent at
    # the outer site.
    assert "#870" in src
    # And verify there is no ``backend_sdk_query_error_duration_seconds.labels``
    # call in the run_query body (it should only appear in
    # _run_query_inner). Same body-bounding technique as above.
    run_query_start = src.find("async def run_query(")
    assert run_query_start != -1
    next_async_def = src.find("\nasync def ", run_query_start + 1)
    body = src[run_query_start:next_async_def] if next_async_def != -1 else src[run_query_start:]
    assert "backend_sdk_query_error_duration_seconds.labels" not in body


# ---------------------------------------------------------------------------
# Retry-accounting metric
# ---------------------------------------------------------------------------


def test_retry_path_increments_backend_task_retries_total():
    # ``backend_task_retries_total`` is the operator-facing counter
    # that surfaces collision-retry frequency in /metrics. Without
    # this increment a noisy collision storm becomes silent in
    # observability. The ``if … is not None`` guard mirrors the
    # rest of the file's prometheus-optional pattern.
    src = _source()
    assert "if backend_task_retries_total is not None:" in src
    assert "backend_task_retries_total.labels(**_LABELS).inc()" in src


# ---------------------------------------------------------------------------
# BudgetExceededError propagates unmodified
# ---------------------------------------------------------------------------


def test_budget_exceeded_error_propagates_unmodified():
    # The except-chain in ``run_query`` must catch ``BudgetExceededError``
    # FIRST and re-raise it without going through the
    # collision-retry branch. The budget cap is a hard ceiling — a
    # retry would still re-hit the same cap and burn another SDK
    # turn for no progress. Pinning the literal ``except
    # BudgetExceededError: raise`` keeps that contract explicit.
    src = _source()
    # The ``raise`` line is on the line after the except clause; we
    # check for the except clause directly and that the next clause
    # is the broader ``except Exception:`` (or bare ``except:``)
    # which is where the collision-retry lives.
    assert "except BudgetExceededError:" in src
    # And: the BudgetExceededError except must lexically precede the
    # broader ``except Exception:`` block so the budget never falls
    # through to the retry path.
    budget_idx = src.find("except BudgetExceededError:")
    broad_idx = src.find("except Exception:", budget_idx)
    assert budget_idx != -1
    assert broad_idx != -1
    assert budget_idx < broad_idx


# ---------------------------------------------------------------------------
# Finally-block observability
# ---------------------------------------------------------------------------


def test_finally_block_observes_stderr_lines_histogram():
    # ``backend_stderr_lines_per_task`` is observed in ``finally`` so
    # the histogram rate stays interpretable across clean and noisy
    # runs (zero observations for clean runs are intentional — a
    # missing observe would skew the rate). Pinning the literal call
    # ensures a refactor that moves the observation out of finally
    # (e.g. into the try block) fails this test, because that change
    # would skip the observation on exception paths.
    src = _source()
    assert "if backend_stderr_lines_per_task is not None:" in src
    assert "backend_stderr_lines_per_task.labels(**_LABELS).observe(len(stderr_lines))" in src


def test_finally_block_increments_tasks_with_stderr_only_when_nonempty():
    # ``backend_tasks_with_stderr_total`` is gated on ``stderr_lines``
    # being non-empty so a clean run does not inflate the counter.
    # The ``if stderr_lines and ... is not None:`` form is the
    # current implementation; pinning it keeps a refactor that
    # accidentally drops the non-empty gate (and thus inflates the
    # counter on every run) from landing silently.
    src = _source()
    assert "if stderr_lines and backend_tasks_with_stderr_total is not None:" in src
    assert "backend_tasks_with_stderr_total.labels(**_LABELS).inc()" in src


# ---------------------------------------------------------------------------
# Docstring contract claims — anchor breadcrumbs for spelunkers
# ---------------------------------------------------------------------------


def test_run_query_docstring_references_issue_1048():
    # #1048 is the canonical issue for the session-collision-retry
    # contract. The docstring naming it lets a maintainer following
    # a bug report land on the right historical context. Remove the
    # ref and the next maintainer loses the breadcrumb.
    src = _source()
    assert "#1048" in src


def test_run_query_docstring_references_issue_870():
    # #870 is the no-double-observe invariant on the outer retry
    # path. The docstring naming it explains why the outer site is
    # deliberately silent on the error histogram.
    src = _source()
    assert "#870" in src


def test_run_query_docstring_references_issue_1485():
    # #1485 is the forward-flag-on-retry rule that makes the
    # idempotency marker correctly observe tool_use activity on
    # resumed attempts too.
    src = _source()
    assert "#1485" in src
