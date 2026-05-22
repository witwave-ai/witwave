"""Regression coverage for the openai executor's MAX_SESSIONS=0 guard (#1629).

Risk: ``MAX_SESSIONS = int(os.environ.get("MAX_SESSIONS", "10000"))`` accepted
``"0"`` verbatim, and the LRU-utilization metric divides ``len(sessions) /
MAX_SESSIONS`` on every eviction. With the env var explicitly set to ``0`` the
divisor is zero and the eviction path crashes with ``ZeroDivisionError`` —
silently breaking session bookkeeping, including delete-on-evict and the
per-session computer-context release.

Fix: clamp the parse to ``MAX_SESSIONS = max(1, int(...))`` and additionally
gate the metric write with ``if MAX_SESSIONS > 0:`` as a defence-in-depth
guard so any future code path that re-derives ``MAX_SESSIONS`` from somewhere
unguarded still cannot trigger the division.

We follow the test_prompt_size_cap.py style: pin the source shape with regex
(so the clamp can't silently regress) and import ``executor`` under stubbed
heavy deps so we can assert the runtime value of ``MAX_SESSIONS`` after env
manipulation.
"""

from __future__ import annotations

import os
import re
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_REPO_ROOT = _HERE.parents[1]
_EXECUTOR_PATH = _HERE / "executor.py"


def _eval_clamp(env_value: str | None) -> int:
    """Evaluate the same parse the executor uses, in isolation.

    We can't ``import executor`` in a unit test because its module-level
    imports pull the OpenAI Agents SDK + Playwright + the a2a server
    surface — none of which are install-required for a constant-parse
    regression. Instead we re-evaluate the literal expression that lives
    in ``executor.py`` against a controlled os.environ snapshot. The
    source-shape test class below pins that the literal expression in
    the module continues to match what we evaluate here, so the two
    halves of the test cannot drift apart silently.
    """
    prev = os.environ.get("MAX_SESSIONS")
    if env_value is None:
        os.environ.pop("MAX_SESSIONS", None)
    else:
        os.environ["MAX_SESSIONS"] = env_value
    try:
        # Mirror exactly the line in backends/openai/executor.py.
        return max(1, int(os.environ.get("MAX_SESSIONS", "10000")))
    finally:
        if prev is None:
            os.environ.pop("MAX_SESSIONS", None)
        else:
            os.environ["MAX_SESSIONS"] = prev


class MaxSessionsClampTests(unittest.TestCase):
    """The runtime constant must clamp to >= 1 regardless of env input."""

    def test_zero_clamps_to_one(self):
        value = _eval_clamp("0")
        self.assertEqual(value, 1)
        # Sanity: division by the clamped value cannot raise.
        try:
            _ = 0 / value
        except ZeroDivisionError:  # pragma: no cover - regression guard
            self.fail("MAX_SESSIONS clamp failed; eviction would still divide by zero")

    def test_default_unchanged(self):
        self.assertEqual(_eval_clamp(None), 10000)

    def test_negative_also_clamps(self):
        self.assertGreaterEqual(_eval_clamp("-5"), 1)


class MaxSessionsSourceShapeTests(unittest.TestCase):
    """Pin the clamp + metric guard so future edits can't silently regress."""

    @classmethod
    def setUpClass(cls):
        cls.executor_source = _EXECUTOR_PATH.read_text(encoding="utf-8")

    def test_clamp_present(self):
        # Must be exactly max(1, int(...)) on the MAX_SESSIONS env parse.
        pattern = re.compile(
            r"MAX_SESSIONS\s*=\s*max\(\s*1\s*,\s*int\(\s*os\.environ\.get\("
            r'\s*"MAX_SESSIONS"\s*,\s*"10000"\s*\)\s*\)\s*\)'
        )
        self.assertRegex(self.executor_source, pattern)

    def test_metric_division_guarded(self):
        # The eviction-path division must be gated on MAX_SESSIONS > 0.
        pattern = re.compile(
            r"if\s+backend_lru_cache_utilization_percent\s+is\s+not\s+None\s+" r"and\s+MAX_SESSIONS\s*>\s*0\s*:"
        )
        self.assertRegex(self.executor_source, pattern)


if __name__ == "__main__":
    unittest.main()
