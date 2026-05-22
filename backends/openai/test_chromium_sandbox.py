"""Regression coverage for Chromium sandbox posture (#1619).

Risk: ``backends/openai/computer.py`` historically launched Chromium with
``--no-sandbox`` and ``--disable-setuid-sandbox`` unconditionally at both
launch sites (stand-alone ``PlaywrightComputer._ensure_browser`` and the
pool-scoped ``BrowserPool._ensure_browser``). On hosts whose kernels are
fully capable of hosting the sandbox (unprivileged user namespaces +
seccomp), this turned every Chromium renderer escape into a single-step
pod compromise.

Fix: opt-out env var ``CHROMIUM_SANDBOX_DISABLED`` gates the two sandbox
flags; ``--disable-dev-shm-usage`` stays on always (memory workaround,
not security).

These tests don't try to exercise Playwright. The ``agents`` SDK pulled
in by ``computer.py`` is heavy and unrelated to the env-gating logic, so
we stub it out and import the module fresh under each env setting to
verify ``_chromium_launch_args()`` honours the toggle.
"""

from __future__ import annotations

import importlib
import os
import sys
import types
import unittest
from pathlib import Path

_COMPUTER_DIR = Path(__file__).resolve().parent


def _install_agents_stub() -> None:
    """Provide a minimal ``agents.computer`` stub for import-time symbols."""
    if "agents.computer" in sys.modules and "agents" in sys.modules:
        return
    agents_pkg = types.ModuleType("agents")
    agents_pkg.__path__ = []  # mark as a package
    computer_mod = types.ModuleType("agents.computer")

    class _AsyncComputer:  # noqa: D401 - stub
        pass

    computer_mod.AsyncComputer = _AsyncComputer
    computer_mod.Button = str  # the source uses it only as a type alias
    sys.modules["agents"] = agents_pkg
    sys.modules["agents.computer"] = computer_mod


def _load_computer(env_overrides: dict[str, str | None]) -> types.ModuleType:
    """Import ``backends.openai.computer`` fresh under the supplied env."""
    _install_agents_stub()

    # Ensure the directory containing computer.py is importable as a
    # top-level module name to dodge backends/__init__ side effects.
    if str(_COMPUTER_DIR) not in sys.path:
        sys.path.insert(0, str(_COMPUTER_DIR))

    saved: dict[str, str | None] = {}
    for key, value in env_overrides.items():
        saved[key] = os.environ.get(key)
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value

    try:
        # Force a fresh import so module-level env reads pick up the override.
        sys.modules.pop("computer", None)
        return importlib.import_module("computer")
    finally:
        for key, original in saved.items():
            if original is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = original


class ChromiumSandboxArgsTests(unittest.TestCase):
    def test_default_unset_keeps_sandbox_enabled(self) -> None:
        mod = _load_computer({"CHROMIUM_SANDBOX_DISABLED": None})
        args = mod._chromium_launch_args()
        self.assertNotIn("--no-sandbox", args)
        self.assertNotIn("--disable-setuid-sandbox", args)
        # Memory workaround is independent of security posture.
        self.assertIn("--disable-dev-shm-usage", args)
        self.assertFalse(mod._CHROMIUM_SANDBOX_DISABLED)

    def test_explicit_true_disables_sandbox(self) -> None:
        mod = _load_computer({"CHROMIUM_SANDBOX_DISABLED": "true"})
        args = mod._chromium_launch_args()
        self.assertIn("--no-sandbox", args)
        self.assertIn("--disable-setuid-sandbox", args)
        self.assertIn("--disable-dev-shm-usage", args)
        self.assertTrue(mod._CHROMIUM_SANDBOX_DISABLED)

    def test_truthy_aliases_accepted(self) -> None:
        for value in ("1", "yes", "TRUE", "Yes"):
            with self.subTest(value=value):
                mod = _load_computer({"CHROMIUM_SANDBOX_DISABLED": value})
                self.assertIn("--no-sandbox", mod._chromium_launch_args())

    def test_falsy_values_keep_sandbox_enabled(self) -> None:
        for value in ("false", "0", "no", ""):
            with self.subTest(value=value):
                mod = _load_computer({"CHROMIUM_SANDBOX_DISABLED": value})
                self.assertNotIn("--no-sandbox", mod._chromium_launch_args())


class ChromiumSandboxLaunchSiteSourceTests(unittest.TestCase):
    """Belt-and-braces: pin both launch sites to ``_chromium_launch_args()``.

    If a future refactor reintroduces a literal ``["--no-sandbox", ...]``
    list at either ``chromium.launch(...)`` call, the env-var opt-out
    silently stops protecting that site. Reading the source string keeps
    the regression detectable without spinning up Playwright.
    """

    def test_no_literal_no_sandbox_at_launch_calls(self) -> None:
        source = (_COMPUTER_DIR / "computer.py").read_text()
        # The sandbox flags must appear only inside the helper + its docstring,
        # never inline at a chromium.launch(...) call site.
        launch_blocks = source.split("chromium.launch(")
        # First chunk is preamble; subsequent chunks are launch-call bodies.
        for idx, chunk in enumerate(launch_blocks[1:], start=1):
            with self.subTest(launch_site=idx):
                # Inspect only up to the closing paren of this launch call.
                head = chunk.split(")", 1)[0]
                self.assertNotIn("--no-sandbox", head)
                self.assertNotIn("--disable-setuid-sandbox", head)
                self.assertIn("_chromium_launch_args", head)


if __name__ == "__main__":
    unittest.main()
