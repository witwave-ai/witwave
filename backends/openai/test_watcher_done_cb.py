"""Regression coverage for the openai MCP watcher done-callback (#1630).

Bug: when an MCP watcher coroutine returned normally (no exception, not
cancelled) the done-callback in ``backends/openai/main.py`` only emitted a
WARNING log. ``_guarded()`` does not restart on a normal return, so the
pod would continue serving traffic with a missing background task and
nothing in the readiness signal would reflect that loss.

Fix: mirror the cycle-1 claude #1608 fix — drop ``_ready`` to ``False``
on the normal-exit branch so ``/health/ready`` returns 503 and Service
endpoints stop sending traffic to a degraded pod.

This test extracts the ``_make_watcher_done_cb`` factory definition out
of ``main.py`` via source slicing and ``exec()`` it into a constructed
namespace that mimics the module globals it closes over (``logger`` and
``_ready``). We avoid importing ``backends.openai.main`` directly because
that module pulls in the OpenAI Agents SDK, a2a, prometheus_client, and
several other heavy dependencies that are unrelated to this fix — the
bug lives in a few lines of control flow that are trivially unit-testable
in isolation.
"""

from __future__ import annotations

import asyncio
import logging
import re
import unittest
from pathlib import Path

_MAIN_PATH = Path(__file__).resolve().parent / "main.py"


def _extract_factory_source() -> str:
    """Return the textual source of the ``_make_watcher_done_cb`` factory.

    Slices from the ``def _make_watcher_done_cb`` header through the
    ``return _cb`` line, then dedents so the block can stand alone.
    """
    src = _MAIN_PATH.read_text()
    m = re.search(
        r"^( *)def _make_watcher_done_cb\(_wn: str\):\n" r"(?:\1 .*\n|\1\n|\n)*?" r"\1    return _cb\n",
        src,
        re.MULTILINE,
    )
    if m is None:
        raise AssertionError(
            "Could not locate _make_watcher_done_cb in main.py — has the factory been renamed or restructured?"
        )
    block = m.group(0)
    indent = m.group(1)
    if indent:
        # Strip the leading indent off every line so the block is
        # importable at module scope inside our exec() namespace.
        block = (
            "\n".join(line[len(indent) :] if line.startswith(indent) else line for line in block.splitlines()) + "\n"
        )
    return block


def _build_factory(ns: dict):
    """Compile the extracted factory into ``ns`` and return the callable."""
    src = _extract_factory_source()
    exec(compile(src, str(_MAIN_PATH) + ":_make_watcher_done_cb", "exec"), ns)
    return ns["_make_watcher_done_cb"]


class _CompletedTask:
    """asyncio.Task stand-in for the synchronous done-callback path.

    The real callback is invoked by the event loop with a Task that is
    already done; it only ever calls ``cancelled()`` and ``exception()``,
    so a plain stub suffices and we avoid spinning up a real loop.
    """

    def __init__(self, *, cancelled: bool = False, exc: BaseException | None = None):
        self._cancelled = cancelled
        self._exc = exc

    def cancelled(self) -> bool:
        return self._cancelled

    def exception(self) -> BaseException | None:
        return self._exc


class OpenAIWatcherDoneCallbackTests(unittest.TestCase):
    """Direct tests for the #1630 readiness-drop branch."""

    def _fresh_namespace(self) -> dict:
        # Mirror the names the factory closes over at module scope: a
        # ``logger`` for warnings/errors and the ``_ready`` global it
        # flips on the normal-exit branch.
        return {
            "asyncio": asyncio,
            "logger": logging.getLogger("openai.test_watcher_done_cb"),
            "_ready": True,
        }

    def test_normal_exit_drops_readiness(self):
        """Watcher returning normally must set ``_ready = False`` (#1630)."""
        ns = self._fresh_namespace()
        factory = _build_factory(ns)
        cb = factory("agent_md_watcher")
        cb(_CompletedTask(cancelled=False, exc=None))
        self.assertFalse(
            ns["_ready"],
            "Normal-exit branch must drop _ready so /health/ready returns 503 (#1630).",
        )

    def test_exception_exit_does_not_touch_readiness(self):
        """Exception exits go down the _guarded restart path; _ready stays."""
        ns = self._fresh_namespace()
        factory = _build_factory(ns)
        cb = factory("mcp_json_watcher")
        cb(_CompletedTask(cancelled=False, exc=RuntimeError("boom")))
        self.assertTrue(
            ns["_ready"],
            "Exception branch must not flip readiness — _guarded handles it.",
        )

    def test_cancelled_exit_does_not_touch_readiness(self):
        """Cancellation is the normal shutdown path; _ready must not flip."""
        ns = self._fresh_namespace()
        factory = _build_factory(ns)
        cb = factory("config_toml_watcher")
        cb(_CompletedTask(cancelled=True, exc=None))
        self.assertTrue(
            ns["_ready"],
            "Cancelled branch must not flip readiness — that is the shutdown path.",
        )

    def test_normal_exit_logs_warning_with_issue_reference(self):
        """The WARNING log line should mention #1630 so operators can grep."""
        ns = self._fresh_namespace()
        factory = _build_factory(ns)
        cb = factory("api_key_file_watcher")
        with self.assertLogs(ns["logger"], level="WARNING") as captured:
            cb(_CompletedTask(cancelled=False, exc=None))
        joined = "\n".join(captured.output)
        self.assertIn("api_key_file_watcher", joined)
        self.assertIn("#1630", joined)


if __name__ == "__main__":
    unittest.main()
