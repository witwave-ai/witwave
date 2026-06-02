"""Guard the graceful SDK-buffer-overflow handling in execute().

CLAUDE_MAX_BUFFER_SIZE (16 MiB default) is the first line of defense against the
SDK's "JSON message exceeded maximum buffer size" error. This is the belt-and-
suspenders: when a single SDK message still exceeds it, execute() must return a
clear error reply instead of re-raising — re-raising surfaces JSON-RPC -32603,
which crashes the dispatch and wedges the agent (the harness treats it as a
stuck peer and team recovery stalls — the 2026-06-02 evan incident). Source-
level (the executor pulls in the full SDK + MCP deps) so a refactor that drops
the guard fails loudly here.
"""

from __future__ import annotations

from pathlib import Path

EXECUTOR = Path(__file__).parent / "executor.py"


def test_max_buffer_size_configurable() -> None:
    src = EXECUTOR.read_text()
    assert "CLAUDE_MAX_BUFFER_SIZE" in src, "the SDK read buffer must be configurable"
    assert "max_buffer_size" in src, "CLAUDE_MAX_BUFFER_SIZE must be passed to ClaudeAgentOptions"


def test_buffer_overflow_returns_graceful_reply() -> None:
    src = EXECUTOR.read_text()
    marker = '"maximum buffer size" in str(_exc)'
    assert marker in src, "execute() must special-case the SDK buffer-overflow so it never crashes with -32603"
    # The graceful branch enqueues a clear reply and returns rather than
    # re-raising (which would surface -32603 and wedge the agent).
    window = src[src.index(marker) : src.index(marker) + 1000]
    assert "new_agent_text_message(" in window, "the graceful branch must enqueue a clear error reply"
    assert "return" in window, "the graceful branch must return instead of re-raising -32603"
