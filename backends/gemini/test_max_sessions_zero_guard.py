"""Regression coverage for the gemini executor's MAX_SESSIONS=0 guard (#1718).

Mirrors backends/openai/test_max_sessions_zero_guard.py: clamp the
MAX_SESSIONS env parse to >= 1 and gate the LRU-utilization metric
division on MAX_SESSIONS > 0.
"""

from __future__ import annotations

import os
import re
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_EXECUTOR_PATH = _HERE / "executor.py"


def _eval_clamp(env_value: str | None) -> int:
    prev = os.environ.get("MAX_SESSIONS")
    if env_value is None:
        os.environ.pop("MAX_SESSIONS", None)
    else:
        os.environ["MAX_SESSIONS"] = env_value
    try:
        return max(1, int(os.environ.get("MAX_SESSIONS", "10000")))
    finally:
        if prev is None:
            os.environ.pop("MAX_SESSIONS", None)
        else:
            os.environ["MAX_SESSIONS"] = prev


class MaxSessionsClampTests(unittest.TestCase):
    def test_zero_clamps_to_one(self):
        value = _eval_clamp("0")
        self.assertEqual(value, 1)
        try:
            _ = 0 / value
        except ZeroDivisionError:  # pragma: no cover
            self.fail("MAX_SESSIONS clamp failed; eviction would still divide by zero")

    def test_default_unchanged(self):
        self.assertEqual(_eval_clamp(None), 10000)

    def test_negative_also_clamps(self):
        self.assertGreaterEqual(_eval_clamp("-5"), 1)


class MaxSessionsSourceShapeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.executor_source = _EXECUTOR_PATH.read_text(encoding="utf-8")

    def test_clamp_present(self):
        pattern = re.compile(
            r"MAX_SESSIONS\s*=\s*max\(\s*1\s*,\s*int\(\s*os\.environ\.get\("
            r'\s*"MAX_SESSIONS"\s*,\s*"10000"\s*\)\s*\)\s*\)'
        )
        self.assertRegex(self.executor_source, pattern)

    def test_metric_division_guarded(self):
        pattern = re.compile(
            r"if\s+backend_lru_cache_utilization_percent\s+is\s+not\s+None\s+" r"and\s+MAX_SESSIONS\s*>\s*0\s*:"
        )
        self.assertRegex(self.executor_source, pattern)


if __name__ == "__main__":
    unittest.main()
