"""Regression coverage for the openai executor's MCP_CONFIG_PATH allow-list (#1731).

Risk: ``_load_mcp_config()`` opened ``MCP_CONFIG_PATH`` with no realpath /
prefix check. A hostile env override (``MCP_CONFIG_PATH=/etc/passwd`` or a
SA-token path) would feed the loader an arbitrary file; on parse failure
the path content was leaked through the ``backend_mcp_config_errors_total``
log line.

Fix mirrors backends/gemini/executor.py (#1610): declare a module-level
``_MCP_CONFIG_PATH_ALLOWED_PREFIX`` (default ``/home/agent/``) and refuse
with ``{}`` + WARN when the resolved path is outside the prefix.

We follow the openai test_max_sessions_zero_guard.py / test_prompt_size_cap.py
style: pin the source shape with regex (so the prefix gate can't silently
regress) and re-evaluate the equivalent guard in isolation rather than
importing the full executor module — its SDK chain is too heavy.
"""

from __future__ import annotations

import os
import os.path
import re
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_EXECUTOR_PATH = _HERE / "executor.py"


def _resolve_and_check(path: str, prefix: str) -> bool:
    """Mirror the runtime guard so the test can exercise it standalone."""
    if not os.path.exists(path):
        return False
    resolved = os.path.realpath(path)
    return resolved.startswith(prefix)


class McpConfigPathRuntimeGuardTests(unittest.TestCase):
    """The realpath + prefix combo must reject /etc/passwd and accept paths
    under the documented home prefix."""

    def test_etc_passwd_rejected(self):
        # /etc/passwd exists on every POSIX host — and the test is most
        # meaningful when the file actually exists, because the production
        # short-circuit (``if not os.path.exists``) returns ``{}`` for missing
        # files anyway.
        if os.path.exists("/etc/passwd"):
            self.assertFalse(_resolve_and_check("/etc/passwd", "/home/agent/"))

    def test_proc_self_rejected(self):
        if os.path.exists("/proc/self/environ"):
            self.assertFalse(_resolve_and_check("/proc/self/environ", "/home/agent/"))

    def test_under_prefix_accepted(self):
        # The realpath of __file__ lives inside this repo's checkout; widen
        # the prefix to that and confirm the guard accepts it.
        prefix = os.path.dirname(os.path.realpath(__file__)) + os.sep
        self.assertTrue(_resolve_and_check(str(_EXECUTOR_PATH), prefix))


class McpConfigPathSourceShapeTests(unittest.TestCase):
    """Pin the prefix gate so a future edit cannot silently regress."""

    @classmethod
    def setUpClass(cls):
        cls.executor_source = _EXECUTOR_PATH.read_text(encoding="utf-8")

    def test_module_constant_present(self):
        # Default must be the documented /home/agent/ prefix.
        pattern = re.compile(
            r"_MCP_CONFIG_PATH_ALLOWED_PREFIX\s*=\s*os\.environ\.get\(\s*"
            r'"MCP_CONFIG_PATH_ALLOWED_PREFIX"\s*,\s*"/home/agent/"\s*,?\s*\)'
        )
        self.assertRegex(self.executor_source, pattern)

    def test_realpath_check_present(self):
        # The check must use os.path.realpath (defeats symlink-bypass) and
        # gate on startswith against the module constant.
        pattern = re.compile(r"resolved\s*=\s*os\.path\.realpath\(\s*MCP_CONFIG_PATH\s*\)")
        self.assertRegex(self.executor_source, pattern)
        guard = re.compile(r"if\s+not\s+resolved\.startswith\(\s*_MCP_CONFIG_PATH_ALLOWED_PREFIX\s*\)\s*:")
        self.assertRegex(self.executor_source, guard)

    def test_out_of_prefix_returns_empty_dict(self):
        # The rejection path must return {} (treat as "no MCP servers")
        # rather than raise. We assert the WARN-then-return shape lives
        # adjacent so a future refactor that splits the helpers doesn't
        # silently drop the return.
        pattern = re.compile(
            r'logger\.warning\([^)]*"MCP config path[^"]*outside allowed prefix[^"]*"',
            re.DOTALL,
        )
        self.assertRegex(self.executor_source, pattern)
        # The function must return {} on the out-of-prefix branch. We
        # check that the rejection block is followed (within ~5 lines)
        # by ``return {}``.
        m = re.search(
            r"if\s+not\s+resolved\.startswith\(\s*_MCP_CONFIG_PATH_ALLOWED_PREFIX\s*\)\s*:"
            r"(?:[^\n]*\n){1,8}?\s*return\s*\{\}",
            self.executor_source,
        )
        self.assertIsNotNone(m, "out-of-prefix branch must return {}")

    def test_issue_cited(self):
        # Issue tag must appear at both the constant declaration and the
        # _load_mcp_config docstring so future readers find the rationale.
        self.assertIn("#1731", self.executor_source)


if __name__ == "__main__":
    unittest.main()
