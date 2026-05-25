"""Tests for A2A_SESSION_CONTEXT_CACHE_MAX parse-time validation (#1648).

Mirrors the strict-fail wrapping introduced in
``_resolve_session_context_cache_max``: bad input raises at module load
so the harness fails CrashLoopBackoff visibly rather than silently
falling back to the default and masking an operator typo.
"""

from __future__ import annotations

import importlib
import os
import sys
from typing import Any

import pytest


def _reload_a2a_module(env: dict[str, str]) -> Any:
    """Reload harness/backends/a2a with the requested env vars applied.

    The cache-max value is parsed at module import via
    ``_resolve_session_context_cache_max``; reloading the module
    re-runs that parse against the freshly mutated environment.
    """
    os.environ.pop("A2A_SESSION_CONTEXT_CACHE_MAX", None)
    os.environ.update(env)
    if "backends.a2a" in sys.modules:
        return importlib.reload(sys.modules["backends.a2a"])
    return importlib.import_module("backends.a2a")


def test_session_context_cache_max_zero_raises():
    with pytest.raises(ValueError, match="A2A_SESSION_CONTEXT_CACHE_MAX"):
        _reload_a2a_module({"A2A_SESSION_CONTEXT_CACHE_MAX": "0"})
    # Leave the module in a usable state for subsequent tests.
    _reload_a2a_module({})


def test_session_context_cache_max_non_int_raises():
    with pytest.raises(ValueError, match="A2A_SESSION_CONTEXT_CACHE_MAX"):
        _reload_a2a_module({"A2A_SESSION_CONTEXT_CACHE_MAX": "abc"})
    _reload_a2a_module({})


def test_session_context_cache_max_negative_raises():
    with pytest.raises(ValueError, match="A2A_SESSION_CONTEXT_CACHE_MAX"):
        _reload_a2a_module({"A2A_SESSION_CONTEXT_CACHE_MAX": "-5"})
    _reload_a2a_module({})


def test_session_context_cache_max_valid_value():
    m = _reload_a2a_module({"A2A_SESSION_CONTEXT_CACHE_MAX": "1000"})
    assert m._SESSION_CONTEXT_CACHE_MAX == 1000
    _reload_a2a_module({})


def test_session_context_cache_max_default():
    m = _reload_a2a_module({})
    assert m._SESSION_CONTEXT_CACHE_MAX == 10000


def test_session_context_cache_max_logs_info_at_startup(caplog):
    with caplog.at_level("INFO", logger="backends.a2a"):
        _reload_a2a_module({"A2A_SESSION_CONTEXT_CACHE_MAX": "2500"})
    assert any(
        "A2A_SESSION_CONTEXT_CACHE_MAX=2500" in r.getMessage() for r in caplog.records
    ), "valid value should be echoed at INFO so operators can spot the resolved cap"
    _reload_a2a_module({})
