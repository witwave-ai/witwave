"""Regression coverage for the claude executor's success-timestamp gating
(#1729).

Bug: the ``backend_task_last_success_timestamp_seconds`` gauge was set
unconditionally on the post-task path, even when ``_budget_exceeded`` was
true. That advanced the "last success" timestamp on a path the executor
already labels ``status="budget_exceeded"`` for ``backend_tasks_total``,
causing dashboards/alerts that watch the success-timestamp gauge to
under-report freshness gaps when budget exhaustion is the actual outcome.

Fix: gate the ``set(time.time())`` call with ``if not _budget_exceeded`` —
parity with the codex fix from #1662.

Mirrors backends/openai/test_success_timestamp_gating.py.
"""

from __future__ import annotations

import re
import time
import unittest
from pathlib import Path

_EXECUTOR_PATH = Path(__file__).resolve().parent / "executor.py"


class _Gauge:
    """Minimal Prometheus-gauge stand-in: records the last set value."""

    def __init__(self):
        self.value = None
        self._labelled = self

    def labels(self, **_kw):
        return self

    def set(self, v):
        self.value = v


def _stamp_success_timestamp(gauge, *, budget_exceeded):
    """Replicate the post-fix predicate verbatim."""
    if not budget_exceeded and gauge is not None:
        gauge.labels().set(time.time())


class ClaudeSuccessTimestampGatingTests(unittest.TestCase):
    """Behavioral tests for the #1729 fix."""

    def test_normal_success_advances_gauge(self):
        gauge = _Gauge()
        before = time.time()
        _stamp_success_timestamp(gauge, budget_exceeded=False)
        after = time.time()
        self.assertIsNotNone(gauge.value)
        self.assertGreaterEqual(gauge.value, before)
        self.assertLessEqual(gauge.value, after)

    def test_budget_exceeded_does_not_advance_gauge(self):
        gauge = _Gauge()
        _stamp_success_timestamp(gauge, budget_exceeded=True)
        self.assertIsNone(gauge.value)

    def test_budget_exceeded_preserves_prior_value(self):
        gauge = _Gauge()
        gauge.value = 1234567890.0
        _stamp_success_timestamp(gauge, budget_exceeded=True)
        self.assertEqual(gauge.value, 1234567890.0)


class ClaudeSuccessTimestampSourceShapeTests(unittest.TestCase):
    """Pin the source shape so the gate can't be removed without tripping CI."""

    @classmethod
    def setUpClass(cls):
        cls.source = _EXECUTOR_PATH.read_text(encoding="utf-8")

    def test_success_timestamp_is_gated_on_budget_exceeded(self):
        pattern = re.compile(
            r"if\s+not\s+_budget_exceeded\s+and\s+backend_task_last_success_timestamp_seconds\s+is\s+not\s+None\s*:\s*\n\s*backend_task_last_success_timestamp_seconds\.labels"
        )
        self.assertRegex(self.source, pattern)

    def test_success_timestamp_not_set_unconditionally(self):
        bad = re.compile(
            r"^\s*if\s+backend_task_last_success_timestamp_seconds\s+is\s+not\s+None\s*:\s*\n\s*backend_task_last_success_timestamp_seconds\.labels\(\*\*_LABELS\)\.set\(time\.time\(\)\)",
            re.MULTILINE,
        )
        self.assertNotRegex(self.source, bad)


if __name__ == "__main__":
    unittest.main()
