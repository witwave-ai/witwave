"""Unit tests for harness/heartbeat.py load_heartbeat (#1690).

Companion to harness/test_heartbeat_drift.py which covers the
tick-anchoring scheduler logic — this file covers the frontmatter
parse path (`schedule:`, `enabled:`, `model:`, `agent:`,
`max-tokens:`, body content).

heartbeat.py reads the file path from a module-level constant
HEARTBEAT_PATH bound at import time from `os.environ`, so tests
override the env var BEFORE importing the module so the constant
resolves to the test fixture path.

Run with:
    PYTHONPATH=harness:shared pytest harness/test_heartbeat_parse.py
"""

from __future__ import annotations

import importlib
import os
import sys
import tempfile
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent
_REPO_ROOT = _HERE.parent
sys.path.insert(0, str(_HERE))
sys.path.insert(0, str(_REPO_ROOT / "shared"))

# Use a session-scoped temp dir for HEARTBEAT_PATH; each test writes a
# fresh file at that exact path before importing/reloading the module.
_TMP_DIR = tempfile.mkdtemp(prefix="hb-test-")
_HB_PATH = os.path.join(_TMP_DIR, "HEARTBEAT.md")
os.environ["HEARTBEAT_PATH"] = _HB_PATH
os.environ.setdefault("AGENT_NAME", "test-agent")

import heartbeat  # noqa: E402


def _write_heartbeat(body: str) -> None:
    """Write the heartbeat fixture and reload the module so its cached
    HEARTBEAT_PATH points at our temp file."""
    Path(_HB_PATH).write_text(body)
    importlib.reload(heartbeat)


def _clear_heartbeat() -> None:
    """Remove the heartbeat file to simulate the no-heartbeat state."""
    try:
        os.unlink(_HB_PATH)
    except FileNotFoundError:
        pass
    importlib.reload(heartbeat)


# ----- happy path -----


def test_parse_minimal_heartbeat():
    _write_heartbeat("---\nschedule: '*/5 * * * *'\n---\nCheck team status.")
    result = heartbeat.load_heartbeat()
    assert result is not None
    schedule, content, model, backend_id, consensus, max_tokens = result
    assert schedule == "*/5 * * * *"
    assert content.strip() == "Check team status."


def test_default_schedule_when_omitted():
    """schedule: omitted falls back to DEFAULT_SCHEDULE rather than failing."""
    _write_heartbeat("---\n---\nCheck team status.")
    result = heartbeat.load_heartbeat()
    assert result is not None
    schedule, *_ = result
    assert schedule == heartbeat.DEFAULT_SCHEDULE


# ----- enabled flag -----


@pytest.mark.parametrize("disabled_value", ["false", "no", "off", "n", "0"])
def test_enabled_false_returns_none(disabled_value):
    """A disabled heartbeat returns None — the runner sees this as
    'no heartbeat configured' and skips dispatching."""
    _write_heartbeat(f"---\nenabled: {disabled_value}\nschedule: '*/5 * * * *'\n---\nbody")
    assert heartbeat.load_heartbeat() is None


# ----- empty body -----


def test_empty_body_returns_none():
    """A heartbeat with no body has nothing to dispatch — return None
    rather than a default."""
    _write_heartbeat("---\nschedule: '*/5 * * * *'\n---\n")
    assert heartbeat.load_heartbeat() is None


# ----- invalid cron falls back to default -----


def test_invalid_cron_falls_back_to_default():
    """Per the warning at heartbeat.py:104-106, an invalid cron
    expression is logged and the default is used — not a hard error
    so the heartbeat still runs."""
    _write_heartbeat("---\nschedule: not-a-cron\n---\nbody")
    result = heartbeat.load_heartbeat()
    assert result is not None
    schedule, *_ = result
    assert schedule == heartbeat.DEFAULT_SCHEDULE


# ----- model + agent -----


def test_model_and_agent_passthrough():
    _write_heartbeat("---\nschedule: '*/5 * * * *'\nmodel: gpt-4\nagent: openai\n---\nbody")
    result = heartbeat.load_heartbeat()
    assert result is not None
    schedule, content, model, backend_id, consensus, max_tokens = result
    assert model == "gpt-4"
    assert backend_id == "openai"


# ----- max-tokens -----


def test_max_tokens_valid():
    _write_heartbeat("---\nschedule: '*/5 * * * *'\nmax-tokens: 4000\n---\nbody")
    result = heartbeat.load_heartbeat()
    assert result is not None
    *_, max_tokens = result
    assert max_tokens == 4000


def test_max_tokens_clamped_to_min_one():
    _write_heartbeat("---\nschedule: '*/5 * * * *'\nmax-tokens: 0\n---\nbody")
    result = heartbeat.load_heartbeat()
    assert result is not None
    *_, max_tokens = result
    assert max_tokens == 1


def test_max_tokens_invalid_falls_back_to_none():
    _write_heartbeat("---\nschedule: '*/5 * * * *'\nmax-tokens: bogus\n---\nbody")
    result = heartbeat.load_heartbeat()
    assert result is not None
    *_, max_tokens = result
    assert max_tokens is None


# ----- file absent -----


def test_no_heartbeat_file_returns_none():
    _clear_heartbeat()
    assert heartbeat.load_heartbeat() is None


if __name__ == "__main__":  # pragma: no cover
    sys.exit(pytest.main([__file__, "-v"]))
