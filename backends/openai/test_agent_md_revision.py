"""Regression checks for OpenAI's backend_agent_md_revision parity surface.

Mirrors claude/gemini's test_agent_md_revision.py: the gauge is stamped on
instantiation and refreshed when agent_md_watcher detects a content change
(#1097). PARITY.md row 35 lists openai as "Full" on identity revision
metric; these source-shape regression checks anchor that claim.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

HERE = Path(__file__).resolve().parent


def test_compute_revision_contract_is_sha256_prefix():
    sample = "You are an OpenAI-backed Witwave agent."
    expected = hashlib.sha256(sample.encode("utf-8", errors="replace")).hexdigest()[:12]

    src = (HERE / "executor.py").read_text()
    assert "def _compute_agent_md_revision(content: str) -> str:" in src
    assert 'hashlib.sha256(content.encode("utf-8", errors="replace")).hexdigest()[:12]' in src
    assert len(expected) == 12
    assert all(ch in "0123456789abcdef" for ch in expected)


def test_metrics_module_declares_agent_md_revision_gauge():
    src = (HERE / "metrics.py").read_text()

    assert "backend_agent_md_revision" in src
    assert "backend_agent_md_revision = prometheus_client.Gauge" in src
    assert '"backend_agent_md_revision"' in src
    assert '"revision"' in src


def test_executor_stamps_revision_on_initial_load_and_reload():
    src = (HERE / "executor.py").read_text()

    assert "backend_agent_md_revision" in src
    assert "_compute_agent_md_revision" in src
    assert "_stamp_agent_md_revision" in src
    # Initial stamp at AgentExecutor.__init__ time (previous=None).
    assert "self._stamp_agent_md_revision(self._agent_md_revision, previous=None)" in src
    # Reload stamp from agent_md_watcher / perform_initial_loads using the
    # openai-shaped local-variable names (_new_rev / _prev_rev).
    assert "self._stamp_agent_md_revision(_new_rev, previous=_prev_rev)" in src
