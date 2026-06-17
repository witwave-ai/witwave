"""Source-shape regression coverage for claude's slow-turn watchdog.

A dispatched turn that hangs (a tool call that never returns, an upstream retry
loop) produces no log output until ``TASK_TIMEOUT_SECONDS`` cancels it — minutes
of silence that made the 2026-06-17 kira docs-cleanup wedge invisible in real
time (the harness saw only a dropped connection / timeout; the backend logged
only heartbeats between dispatch and the eventual timeout). ``_slow_turn_watchdog``
runs alongside every turn in ``_run_inner`` and logs a WARNING at escalating
intervals so a hang is diagnosable *while it happens*.

This file pins the wiring so a future refactor that drops the watchdog creation
or its ``finally`` cancellation fails at unit time rather than silently restoring
the can't-see-a-hang gap. Follows the claude-local convention (see
``test_sessions_lock_source_shape.py``): text-substring assertions against
``executor.py`` read as text, because importing the SDK chain is heavy.
"""

from __future__ import annotations

from pathlib import Path

HERE = Path(__file__).resolve().parent


def _source() -> str:
    return (HERE / "executor.py").read_text(encoding="utf-8")


def test_slow_turn_warn_seconds_env_knob_declared():
    # Operator-tunable threshold; 0 disables. Pin the env read so the knob
    # can't be silently replaced by a hardcoded constant.
    src = _source()
    assert 'SLOW_TURN_WARN_SECONDS = float(os.environ.get("SLOW_TURN_WARN_SECONDS"' in src


def test_watchdog_coroutine_defined_with_disable_gate():
    src = _source()
    assert "async def _slow_turn_watchdog(" in src
    # 0/negative is a true off switch — the early return must stay.
    assert "if SLOW_TURN_WARN_SECONDS <= 0:" in src


def test_watchdog_never_destabilises_the_turn():
    # A diagnostic must never break the turn it observes: re-raise
    # CancelledError (so finally-cancel works cleanly) and swallow everything
    # else.
    src = _source()
    assert "except asyncio.CancelledError:" in src
    assert 'logger.debug("slow-turn watchdog error (ignored)", exc_info=True)' in src


def test_run_inner_creates_and_cancels_watchdog():
    # The wiring: _run_inner spawns the watchdog before the wait_for and
    # cancels it in a finally. Dropping either silently restores the
    # can't-see-a-hang gap.
    src = _source()
    assert "_slow_watchdog = asyncio.create_task(_slow_turn_watchdog(ctx.session_id, ctx.model, _start))" in src
    # The cancel MUST be in a finally so it runs on the success, timeout, AND
    # error paths — not only the happy path.
    assert "    finally:\n        _slow_watchdog.cancel()" in src
