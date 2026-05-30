"""Tests for the log hygiene behavior of ``harness.utils.run_awatch_loop``.

Asserts the dormant-directory contract:

- Missing directory → DEBUG-level log only. No INFO noise for subsystems
  the agent intentionally doesn't use (file-presence-as-enablement per
  DESIGN.md).
- Transition missing → present → exactly one INFO log announcing the
  watcher becoming active.

These tests stub ``os.path.isdir`` and ``watchfiles.awatch`` so they
never touch the real filesystem or real file-event machinery. Pattern
follows the existing harness tests (``test_mcp_auth.py``): sync test
functions drive async code with ``asyncio.run()`` — no
``pytest-asyncio`` dependency required.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
import types
from pathlib import Path
from unittest.mock import patch

import pytest

# Make harness/ importable as the test lives alongside the source.
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

# Stub out `watchfiles` before anything imports harness.utils. The
# real module is a Rust-backed binary dep installed in the harness
# container image but absent on most dev laptops. Installing a fake
# module into sys.modules makes the deferred `from watchfiles import
# awatch` inside run_awatch_loop resolve to our stub without a real
# install. Each test patches the `awatch` attribute further to a
# scenario-specific stub.
if "watchfiles" not in sys.modules:
    _fake = types.ModuleType("watchfiles")
    _fake.awatch = None  # filled in per-test via patch.object
    sys.modules["watchfiles"] = _fake

import utils  # noqa: E402


class _LoopBreak(Exception):
    """Sentinel raised to short-circuit run_awatch_loop in tests.

    The loop has no return path under normal operation; tests inject
    this from one of the stub callbacks to exit deterministically.
    """


def _make_logger() -> tuple[logging.Logger, list[logging.LogRecord]]:
    """Return a logger wired to a list-capturing handler.

    Each test gets its own logger so captures don't leak across runs.
    """
    records: list[logging.LogRecord] = []

    class _ListHandler(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:  # noqa: D401
            records.append(record)

    name = f"test-run_awatch_loop-{id(records)}"
    lg = logging.getLogger(name)
    lg.setLevel(logging.DEBUG)
    lg.addHandler(_ListHandler())
    lg.propagate = False
    return lg, records


def _never_called(*_args, **_kwargs):
    raise AssertionError("callback should not fire in this test path")


async def _never_called_async(*_args, **_kwargs):
    raise AssertionError("async callback should not fire in this test path")


async def _noop_scan() -> None:
    return None


class _StubAwatch:
    """Minimal async-iterable stand-in for watchfiles.awatch.

    Raises ``_LoopBreak`` on the first iteration so the caller's
    ``async for`` loop terminates deterministically after we've
    exercised the code path under test.
    """

    def __init__(self, _directory):
        pass

    def __aiter__(self):
        return self

    async def __anext__(self):
        raise _LoopBreak


def test_missing_directory_logs_at_debug_only():
    """A directory that never appears logs DEBUG (never INFO).

    Three iterations with the directory absent throughout. Assert:
    every log record is DEBUG level; no INFO record exists.
    """
    lg, records = _make_logger()
    iterations = {"n": 0}

    async def _short_sleep(_delay: float) -> None:
        iterations["n"] += 1
        if iterations["n"] >= 3:
            raise _LoopBreak

    async def _run():
        with (
            patch.object(os.path, "isdir", return_value=False),
            patch.object(asyncio, "sleep", _short_sleep),
        ):
            with pytest.raises(_LoopBreak):
                await utils.run_awatch_loop(
                    directory="/tmp/does-not-exist",
                    watcher_name="jobs",
                    scan=_noop_scan,
                    on_change=_never_called,
                    on_delete=_never_called,
                    cleanup=_never_called,
                    logger_=lg,
                    not_found_message="Jobs directory not found — retrying in 10s.",
                    watcher_exited_message="Jobs watcher exited — retrying in 10s.",
                    retry_delay=0.0,
                )

    asyncio.run(_run())

    assert len(records) == 3, f"unexpected records: {[r.getMessage() for r in records]}"
    for rec in records:
        assert rec.levelno == logging.DEBUG, (
            f"missing-directory log at {rec.levelname} (expected DEBUG): {rec.getMessage()!r}"
        )
        assert "Jobs directory not found" in rec.getMessage()


def test_transition_missing_to_present_logs_info():
    """Transition from "no dir" to "dir exists" emits one INFO.

    Two iterations: first absent (DEBUG), second present (INFO
    transition, then stub-awatch raises _LoopBreak to exit).
    """
    lg, records = _make_logger()
    isdir_calls = {"n": 0}

    def _flipping_isdir(_p):
        isdir_calls["n"] += 1
        return isdir_calls["n"] >= 2

    async def _noop_sleep(_delay: float) -> None:
        return None

    async def _run():
        import watchfiles

        with (
            patch.object(os.path, "isdir", side_effect=_flipping_isdir),
            patch.object(asyncio, "sleep", _noop_sleep),
            patch.object(watchfiles, "awatch", _StubAwatch),
        ):
            with pytest.raises(_LoopBreak):
                await utils.run_awatch_loop(
                    directory="/tmp/appears",
                    watcher_name="jobs",
                    scan=_noop_scan,
                    on_change=_never_called,
                    on_delete=_never_called,
                    cleanup=_never_called,
                    logger_=lg,
                    not_found_message="Jobs directory not found — retrying in 10s.",
                    watcher_exited_message="Jobs watcher exited — retrying in 10s.",
                    retry_delay=0.0,
                )

    asyncio.run(_run())

    debug_records = [r for r in records if r.levelno == logging.DEBUG]
    info_records = [r for r in records if r.levelno == logging.INFO]
    assert len(debug_records) >= 1, records
    assert any("Jobs directory not found" in r.getMessage() for r in debug_records)
    assert len(info_records) >= 1, records
    assert any("now present — starting watcher" in r.getMessage() for r in info_records), (
        f"missing transition-log: {[r.getMessage() for r in info_records]}"
    )


def test_present_on_first_iteration_logs_no_transition():
    """Boot-time directory presence does NOT emit a transition-INFO log.

    When the directory exists on the very first iteration (no prior
    missing state), the "now present" line would be redundant with the
    downstream scan/register signals — don't emit it.
    """
    lg, records = _make_logger()

    async def _noop_sleep(_delay: float) -> None:
        return None

    async def _run():
        import watchfiles

        with (
            patch.object(os.path, "isdir", return_value=True),
            patch.object(asyncio, "sleep", _noop_sleep),
            patch.object(watchfiles, "awatch", _StubAwatch),
        ):
            with pytest.raises(_LoopBreak):
                await utils.run_awatch_loop(
                    directory="/tmp/exists-at-boot",
                    watcher_name="jobs",
                    scan=_noop_scan,
                    on_change=_never_called,
                    on_delete=_never_called,
                    cleanup=_never_called,
                    logger_=lg,
                    not_found_message="Jobs directory not found — retrying in 10s.",
                    watcher_exited_message="Jobs watcher exited — retrying in 10s.",
                    retry_delay=0.0,
                )

    asyncio.run(_run())

    transition_logs = [r for r in records if "now present — starting watcher" in r.getMessage()]
    assert transition_logs == [], (
        f"unexpected transition-log on first-iteration present: {[r.getMessage() for r in transition_logs]}"
    )
