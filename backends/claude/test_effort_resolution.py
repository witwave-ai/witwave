"""Behavioral coverage for the claude executor's CLAUDE_EFFORT resolution.

Exercises ``_resolve_effort`` WITHOUT importing executor.py (which pulls in
``claude_agent_sdk``). Mirrors the source-extraction pattern used by
test_success_timestamp_gating.py: parse executor.py, lift just the helper +
its allow-list, and exec them against a stub logger. That keeps the test
runnable without the SDK installed while still exercising real logic.
"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

_EXECUTOR_PATH = Path(__file__).resolve().parent / "executor.py"


class _StubLogger:
    def __init__(self) -> None:
        self.warnings: list[tuple] = []

    def warning(self, *args, **kwargs) -> None:
        self.warnings.append((args, kwargs))


def _load_resolve_effort():
    """Return (_resolve_effort callable, exec namespace) lifted from executor.py."""
    tree = ast.parse(_EXECUTOR_PATH.read_text())
    nodes = [
        n
        for n in tree.body
        if (isinstance(n, ast.FunctionDef) and n.name == "_resolve_effort")
        or (
            isinstance(n, ast.Assign)
            and any(isinstance(t, ast.Name) and t.id == "_VALID_EFFORT" for t in n.targets)
        )
    ]
    if len(nodes) != 2:
        raise AssertionError(
            f"expected _VALID_EFFORT + _resolve_effort in executor.py, found {len(nodes)}"
        )
    module = ast.Module(body=nodes, type_ignores=[])
    namespace: dict = {"logger": _StubLogger()}
    exec(compile(module, str(_EXECUTOR_PATH), "exec"), namespace)
    return namespace["_resolve_effort"], namespace


class ResolveEffortTest(unittest.TestCase):
    def setUp(self) -> None:
        self.resolve, self.namespace = _load_resolve_effort()

    def test_valid_levels_pass_through(self):
        for level in ("low", "medium", "high", "max"):
            self.assertEqual(self.resolve(level), level)

    def test_case_and_whitespace_normalized(self):
        self.assertEqual(self.resolve("MAX"), "max")
        self.assertEqual(self.resolve("  High  "), "high")

    def test_unset_returns_none(self):
        self.assertIsNone(self.resolve(""))
        self.assertIsNone(self.resolve(None))

    def test_unknown_level_ignored_with_warning(self):
        # 'xhigh' exists in newer SDKs but NOT the pinned 0.1.55 — it must be
        # rejected so we never pass `--effort xhigh` to a CLI that rejects it.
        self.assertIsNone(self.resolve("xhigh"))
        self.assertIsNone(self.resolve("garbage"))
        self.assertTrue(self.namespace["logger"].warnings, "expected a warning for an unknown effort")

    def test_allowlist_matches_pinned_sdk(self):
        self.assertEqual(self.namespace["_VALID_EFFORT"], ("low", "medium", "high", "max"))


if __name__ == "__main__":
    unittest.main()
