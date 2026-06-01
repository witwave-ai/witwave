"""Coverage for the claude executor's CLAUDE_MAX_BUFFER_SIZE resolution.

Exercises ``_resolve_max_buffer_size`` WITHOUT importing executor.py (which
pulls in ``claude_agent_sdk``). Mirrors test_effort_resolution.py: parse
executor.py, lift the helper + its default constant, and exec them against a
stub logger so the test runs without the SDK installed.
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


def _load_resolver():
    """Return (_resolve_max_buffer_size, namespace) lifted from executor.py."""
    tree = ast.parse(_EXECUTOR_PATH.read_text())
    nodes = [
        n
        for n in tree.body
        if (isinstance(n, ast.FunctionDef) and n.name == "_resolve_max_buffer_size")
        or (
            isinstance(n, ast.Assign)
            and any(isinstance(t, ast.Name) and t.id == "_DEFAULT_MAX_BUFFER_SIZE" for t in n.targets)
        )
    ]
    if len(nodes) != 2:
        raise AssertionError(
            f"expected _DEFAULT_MAX_BUFFER_SIZE + _resolve_max_buffer_size in executor.py, found {len(nodes)}"
        )
    module = ast.Module(body=nodes, type_ignores=[])
    namespace: dict = {"logger": _StubLogger()}
    exec(compile(module, str(_EXECUTOR_PATH), "exec"), namespace)
    return namespace["_resolve_max_buffer_size"], namespace


class ResolveMaxBufferSizeTest(unittest.TestCase):
    def setUp(self) -> None:
        self.resolve, self.namespace = _load_resolver()
        self.default = self.namespace["_DEFAULT_MAX_BUFFER_SIZE"]

    def test_default_is_16_mib(self):
        # The whole point is headroom over the SDK's 1 MiB default.
        self.assertEqual(self.default, 16 * 1024 * 1024)
        self.assertGreater(self.default, 1024 * 1024)

    def test_unset_returns_default(self):
        for raw in (None, "", "   "):
            self.assertEqual(self.resolve(raw), self.default)

    def test_valid_int_passes_through(self):
        self.assertEqual(self.resolve("8388608"), 8388608)
        self.assertEqual(self.resolve("  33554432  "), 33554432)

    def test_non_int_falls_back_with_warning(self):
        self.assertEqual(self.resolve("lots"), self.default)
        self.assertTrue(self.namespace["logger"].warnings, "non-int must warn")

    def test_nonpositive_falls_back_with_warning(self):
        self.assertEqual(self.resolve("0"), self.default)
        self.assertEqual(self.resolve("-5"), self.default)
        self.assertTrue(self.namespace["logger"].warnings, "non-positive must warn")


if __name__ == "__main__":
    unittest.main()
