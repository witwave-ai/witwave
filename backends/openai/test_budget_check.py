"""Regression coverage for the openai executor's per-response token-budget
check (#1600).

Bug: the budget candidate was

    _candidate = getattr(_usage, "total_tokens", None) or getattr(_usage, "output_tokens", None)

which engaged the budget enforcement using ``output_tokens`` whenever
``total_tokens`` was missing or zero. ``output_tokens`` alone excludes the
prompt + cached input contribution, so the comparison ``_total_tokens >=
max_tokens`` mixed apples and oranges and tripped the budget early for
callers whose SDK surfaces only the output side of usage.

Fix: only enforce when ``total_tokens`` is present.

This test pins both halves of the predicate by reading the source of the
relevant ``executor.py`` block and asserting on its shape directly. We
avoid importing ``executor`` itself because the module pulls in a heavy
SDK chain (agents, computer, etc.) that isn't useful here — the bug lives
in three lines of attribute access that are trivially unit-testable in
isolation.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

_EXECUTOR_PATH = Path(__file__).resolve().parent / "executor.py"


class _Usage:
    """Minimal stand-in for the SDK's usage object."""

    def __init__(self, *, total_tokens=None, output_tokens=None):
        if total_tokens is not None:
            self.total_tokens = total_tokens
        if output_tokens is not None:
            self.output_tokens = output_tokens


def _candidate_from_usage(usage):
    """Replicate the post-fix predicate verbatim (executor.py line 1652)."""
    return getattr(usage, "total_tokens", None)


class OpenAIBudgetCandidateTests(unittest.TestCase):
    """Direct-predicate tests for the #1600 fix."""

    def test_output_tokens_only_does_not_engage_budget(self):
        """A usage object reporting only output_tokens must NOT engage the
        budget check — that was the regression in #1600.
        """
        usage = _Usage(output_tokens=9_999)
        self.assertIsNone(_candidate_from_usage(usage))

    def test_total_tokens_engages_budget(self):
        """Baseline: total_tokens still drives the budget candidate."""
        usage = _Usage(total_tokens=1234)
        self.assertEqual(_candidate_from_usage(usage), 1234)

    def test_total_tokens_zero_is_still_a_value(self):
        """Zero is a legitimate report (no consumption yet) and must not
        be coerced to None — the pre-fix ``or`` operator did exactly that
        and silently fell through to output_tokens.
        """
        usage = _Usage(total_tokens=0, output_tokens=500)
        self.assertEqual(_candidate_from_usage(usage), 0)

    def test_neither_attribute_present(self):
        usage = _Usage()
        self.assertIsNone(_candidate_from_usage(usage))


class OpenAIBudgetSourceShapeTests(unittest.TestCase):
    """Pin the source shape so a future edit can't reintroduce the
    ``or output_tokens`` fallback without tripping CI.
    """

    @classmethod
    def setUpClass(cls):
        cls.source = _EXECUTOR_PATH.read_text(encoding="utf-8")

    def test_candidate_uses_only_total_tokens(self):
        # Match the post-fix line tolerant of whitespace.
        pattern = re.compile(r'_candidate\s*=\s*getattr\(\s*_usage\s*,\s*"total_tokens"\s*,\s*None\s*\)')
        self.assertRegex(self.source, pattern)

    def test_candidate_does_not_fall_back_to_output_tokens(self):
        # The bug was the ``or getattr(..., "output_tokens", None)`` tail.
        # If anyone reintroduces it, this guard fires.
        bad = re.compile(
            r"_candidate\s*=\s*getattr\(.*?total_tokens.*?\)\s*or\s*getattr\(.*?output_tokens",
            re.DOTALL,
        )
        self.assertNotRegex(self.source, bad)


if __name__ == "__main__":
    unittest.main()
