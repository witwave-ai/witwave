"""Regression checks for Claude's backend_agent_md_revision parity surface."""

from __future__ import annotations

import hashlib
from pathlib import Path

HERE = Path(__file__).resolve().parent


def test_compute_revision_contract_is_sha256_prefix():
    sample = "You are Claude, running as a Witwave backend."
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
    assert "self._stamp_agent_md_revision(self._agent_md_revision, previous=None)" in src
    assert "self._stamp_agent_md_revision(current, previous=previous)" in src
