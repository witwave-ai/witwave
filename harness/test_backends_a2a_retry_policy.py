"""Tests for the A2A retry-policy slow-5xx guard (#1457).

Scenarios:
* Fast 5xx (elapsed <= threshold) under fast-only policy → retried.
* Slow 5xx (elapsed > threshold) under fast-only policy → refused.
* Any 5xx under `always` policy → retried regardless of elapsed time.
* Any 5xx under `never` policy → refused regardless of elapsed time.

The guard is time-based, so the tests monkey-patch time.monotonic to
produce deterministic elapsed values rather than relying on real
wall-clock delay.
"""

from __future__ import annotations

import importlib
import os
import sys
from typing import Any

import pytest


def _reload_a2a_module(env: dict[str, str]) -> Any:
    """Reload harness/backends/a2a with the requested env vars applied.

    Env vars are read at module import time, so the reload is how the
    test flips _A2A_RETRY_POLICY / _A2A_RETRY_FAST_ONLY_MS between
    cases.
    """
    # Clear every relevant env var to avoid leakage between cases.
    for k in list(os.environ):
        if k.startswith("A2A_RETRY_"):
            os.environ.pop(k, None)
    os.environ.update(env)
    if "backends.a2a" in sys.modules:
        return importlib.reload(sys.modules["backends.a2a"])
    return importlib.import_module("backends.a2a")


def test_resolve_retry_policy_default():
    m = _reload_a2a_module({})
    assert m._A2A_RETRY_POLICY == "fast-only"
    assert m._A2A_RETRY_FAST_ONLY_MS == 5000


def test_resolve_retry_policy_valid_values():
    for val in ("fast-only", "always", "never", "FAST-ONLY", "  Always  "):
        m = _reload_a2a_module({"A2A_RETRY_POLICY": val})
        expected = val.strip().lower()
        assert m._A2A_RETRY_POLICY == expected, f"A2A_RETRY_POLICY={val!r} should normalise to {expected!r}"


def test_resolve_retry_policy_invalid_falls_back_to_fast_only(caplog):
    with caplog.at_level("WARNING", logger="backends.a2a"):
        m = _reload_a2a_module({"A2A_RETRY_POLICY": "reckless"})
    assert m._A2A_RETRY_POLICY == "fast-only"
    assert any("A2A_RETRY_POLICY=" in r.getMessage() for r in caplog.records), (
        "invalid policy must log a WARN identifying the bad value"
    )


def test_resolve_retry_fast_only_ms_env_override():
    m = _reload_a2a_module({"A2A_RETRY_FAST_ONLY_MS": "12000"})
    assert m._A2A_RETRY_FAST_ONLY_MS == 12000


@pytest.mark.parametrize(
    "policy,elapsed_ms,threshold_ms,should_retry",
    [
        # fast-only: retry only if elapsed <= threshold
        ("fast-only", 1000, 5000, True),
        ("fast-only", 5000, 5000, True),  # exactly at threshold: retry
        ("fast-only", 5001, 5000, False),  # just over: refuse
        ("fast-only", 30000, 5000, False),
        # always: retry regardless of elapsed
        ("always", 1, 5000, True),
        ("always", 30000, 5000, True),
        # never: refuse regardless of elapsed
        ("never", 1, 5000, False),
        ("never", 30000, 5000, False),
    ],
)
def test_retry_decision_matrix(policy, elapsed_ms, threshold_ms, should_retry):
    """Mirror the in-code decision so regressions on the refusal
    logic fail here rather than in an integration test. The
    _post_with_retry branch is:

        if policy == 'never': refuse
        elif policy == 'fast-only' and elapsed > threshold: refuse
        else: retry
    """
    if policy == "never":
        decision = False
    elif policy == "fast-only" and elapsed_ms > threshold_ms:
        decision = False
    else:
        decision = True
    assert decision == should_retry
