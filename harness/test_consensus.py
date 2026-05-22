"""Unit tests for consensus-mode primitives (#1694).

Covers two pure functions that the consensus dispatcher depends on:

  - `_classify_binary` (harness/executor.py:363) — maps a raw response
    string to "yes" / "no" / None for binary-mode majority vote.
  - `parse_consensus` (harness/utils.py:44) — parses the
    `consensus:` frontmatter field into a list of ConsensusEntry
    objects, supporting glob-pattern backends + optional model
    overrides.

These two are the load-bearing pieces of consensus mode. Full
end-to-end aggregation (run_consensus) needs backend stubs and
remains untested at the unit level — out of scope for this file.

Run with:
    PYTHONPATH=harness:shared pytest harness/test_consensus.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent
_REPO_ROOT = _HERE.parent
sys.path.insert(0, str(_HERE))
sys.path.insert(0, str(_REPO_ROOT / "shared"))

os.environ.setdefault("AGENT_NAME", "test-agent")

from utils import ConsensusEntry, parse_consensus  # noqa: E402


# Import _classify_binary + the keyword sets directly. executor.py
# pulls in a2a-sdk which has version-coupled imports; install a tiny
# stub so the tests import cleanly in a plain unit-test env.
def _stub_a2a_if_needed():
    if "a2a" in sys.modules:
        return
    import types as _t

    _a2a = _t.ModuleType("a2a")
    _server = _t.ModuleType("a2a.server")
    _ae = _t.ModuleType("a2a.server.agent_execution")
    _ev = _t.ModuleType("a2a.server.events")
    _utils = _t.ModuleType("a2a.utils")
    _types = _t.ModuleType("a2a.types")

    class _Stub:  # any attribute access returns another _Stub
        def __init__(self, *a, **kw):
            pass

        def __getattr__(self, _):
            return _Stub

        def __call__(self, *a, **kw):
            return _Stub()

    _ae.AgentExecutor = _Stub
    _ae.RequestContext = _Stub
    _ev.EventQueue = _Stub
    _utils.new_agent_text_message = lambda *a, **kw: None
    _types.UnsupportedOperationError = type("UnsupportedOperationError", (Exception,), {})
    _types.TaskState = _Stub
    _types.Task = _Stub
    _types.TaskArtifactUpdateEvent = _Stub
    _types.TaskStatus = _Stub
    _types.TaskStatusUpdateEvent = _Stub
    _types.Part = _Stub
    _types.TextPart = _Stub
    _types.Message = _Stub
    _types.Role = _Stub
    sys.modules["a2a"] = _a2a
    sys.modules["a2a.server"] = _server
    sys.modules["a2a.server.agent_execution"] = _ae
    sys.modules["a2a.server.events"] = _ev
    sys.modules["a2a.utils"] = _utils
    sys.modules["a2a.types"] = _types


def _get_classify_binary():
    _stub_a2a_if_needed()
    from executor import _classify_binary

    return _classify_binary


def _get_binary_keyword_sets():
    _stub_a2a_if_needed()
    from executor import _BINARY_NO, _BINARY_YES

    return _BINARY_YES, _BINARY_NO


# ----- _classify_binary: matrix -----


@pytest.mark.parametrize(
    "raw",
    ["yes", "true", "agree", "correct", "approved", "confirmed", "positive", "1"],
)
def test_classify_binary_yes_keywords(raw):
    classify = _get_classify_binary()
    assert classify(raw) == "yes"


@pytest.mark.parametrize(
    "raw",
    ["no", "false", "disagree", "incorrect", "rejected", "denied", "negative", "0"],
)
def test_classify_binary_no_keywords(raw):
    classify = _get_classify_binary()
    assert classify(raw) == "no"


@pytest.mark.parametrize(
    "raw",
    [
        "Yes",
        "YES",
        "  yes  ",
        "yes.",
        "YES.",
        "yes.\n",
    ],
)
def test_classify_binary_case_and_trailing_period_handled(raw):
    """Match should be case-insensitive and tolerate a trailing period
    + leading/trailing whitespace, since LLMs commonly suffix answers."""
    classify = _get_classify_binary()
    assert classify(raw) == "yes"


@pytest.mark.parametrize(
    "raw",
    [
        "yes, but with caveats",  # not a single keyword
        "I agree because ...",  # not a bare keyword
        "maybe",
        "perhaps yes",
        "yesterday",  # substring of "yes" but different word
        "",
        "   ",
    ],
)
def test_classify_binary_unclassifiable_returns_none(raw):
    """Anything that isn't a bare yes/no keyword returns None — the
    consensus path then falls into the freeform-synthesis branch
    rather than miscounting a hedged answer as a binary vote."""
    classify = _get_classify_binary()
    assert classify(raw) is None


def test_classify_binary_period_only_doesnt_classify():
    classify = _get_classify_binary()
    assert classify(".") is None


# ----- parse_consensus -----


def test_parse_consensus_empty_returns_empty_list():
    assert parse_consensus(None) == []
    assert parse_consensus("") == []
    assert parse_consensus({}) == []
    assert parse_consensus([]) == []


def test_parse_consensus_rejects_non_list():
    """Per the docstring, only YAML lists are accepted."""
    assert parse_consensus("just-a-string") == []
    assert parse_consensus(42) == []
    assert parse_consensus({"backend": "claude"}) == []  # bare dict, not list


def test_parse_consensus_simple_backend_glob():
    entries = parse_consensus([{"backend": "*"}])
    assert entries == [ConsensusEntry(backend="*", model=None)]


def test_parse_consensus_backend_with_explicit_model():
    entries = parse_consensus(
        [
            {"backend": "claude", "model": "claude-opus-4-7"},
        ]
    )
    assert len(entries) == 1
    assert entries[0].backend == "claude"
    assert entries[0].model == "claude-opus-4-7"


def test_parse_consensus_multiple_entries_preserves_order():
    """Order matters — fan-out happens in list order and the default-
    backend tie-break uses the first matching entry."""
    entries = parse_consensus(
        [
            {"backend": "claude"},
            {"backend": "openai*"},
            {"backend": "claude", "model": "claude-haiku-4-5"},
        ]
    )
    assert [e.backend for e in entries] == ["claude", "openai*", "claude"]
    assert [e.model for e in entries] == [None, None, "claude-haiku-4-5"]


def test_parse_consensus_drops_entries_without_backend():
    """An entry that lacks `backend:` is silently dropped — bare
    `model:`-only entries make no sense in a consensus list."""
    entries = parse_consensus(
        [
            {"backend": "claude"},
            {"model": "no-backend-key"},  # dropped
            {"backend": ""},  # dropped (falsy backend)
            {"backend": "openai"},
        ]
    )
    assert [e.backend for e in entries] == ["claude", "openai"]


def test_parse_consensus_coerces_non_string_backend_to_string():
    """If YAML produces an int/etc for backend, the parser stringifies
    rather than raising. Glob-matching against backend IDs is
    case-sensitive string match."""
    entries = parse_consensus([{"backend": 42, "model": 7}])
    # Should accept and coerce.
    assert len(entries) == 1
    assert entries[0].backend == "42"
    assert entries[0].model == "7"


def test_parse_consensus_drops_non_dict_entries():
    """Bare strings or numbers in the list are not legal consensus
    entries (the docstring shows objects only); they should be ignored."""
    entries = parse_consensus(
        [
            "claude",  # bare string — not legal
            {"backend": "claude"},
            42,  # bare int — not legal
            {"backend": "openai"},
        ]
    )
    assert [e.backend for e in entries] == ["claude", "openai"]


if __name__ == "__main__":  # pragma: no cover
    sys.exit(pytest.main([__file__, "-v"]))
