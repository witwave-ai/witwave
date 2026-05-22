"""Source-shape coverage for GPT-5.5 tool support in the OpenAI backend.

The backend previously used the legacy LocalShellTool path from
openai-agents==0.9.3. OpenAI now documents that local_shell is outdated
and model-limited, while the newer ShellTool path supports GPT-5.5. These
tests pin the migration so a future dependency bump cannot quietly slide
the backend back onto the older tool surface.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_EXECUTOR = _HERE / "executor.py"
_REQUIREMENTS = _HERE / "requirements.txt"


class OpenAIGpt55ToolSupportSourceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.executor = _EXECUTOR.read_text(encoding="utf-8")
        cls.requirements = _REQUIREMENTS.read_text(encoding="utf-8")

    def test_agents_sdk_is_current_tool_generation(self) -> None:
        self.assertIn("openai-agents==0.17.3", self.requirements)

    def test_default_model_is_gpt_55(self) -> None:
        self.assertRegex(
            self.executor,
            re.compile(
                r'OPENAI_MODEL\s*=\s*os\.environ\.get\("OPENAI_MODEL"\)\s*'
                r'or\s*os\.environ\.get\("CODEX_MODEL"\)\s*or\s*"gpt-5\.5"'
            ),
        )

    def test_uses_modern_shell_tool_not_legacy_local_shell(self) -> None:
        self.assertIn("ShellTool,", self.executor)
        self.assertIn("ShellTool(executor=_shell_executor)", self.executor)
        self.assertNotIn("LocalShellTool", self.executor)
        self.assertNotIn("LocalShellCommandRequest", self.executor)

    def test_forces_responses_api_for_shell_tooling(self) -> None:
        self.assertIn("openai_use_responses=True", self.executor)

    def test_reasoning_effort_can_be_set_to_xhigh(self) -> None:
        self.assertIn("OPENAI_REASONING_EFFORT", self.executor)
        self.assertIn('"extra high": "xhigh"', self.executor)
        self.assertIn("ModelSettings(reasoning=Reasoning(effort=effort))", self.executor)

    def test_computer_tool_allows_gpt_55(self) -> None:
        self.assertRegex(
            self.executor,
            re.compile(r'_COMPUTER_SUPPORTED_MODELS\s*=\s*\{[^}]*"gpt-5\.5"[^}]*\}'),
        )
        self.assertIn('_COMPUTER_SUPPORTED_MODEL_PREFIXES = ("gpt-5.5-",)', self.executor)


if __name__ == "__main__":
    unittest.main()
