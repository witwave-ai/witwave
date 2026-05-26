"""Unit tests for tools/prometheus/server.py logging redaction (#1639).

Exercises the non-200 upstream path of ``_prom_get`` with a fake httpx
client to assert that sensitive upstream body content does NOT land in
WARNING-level log records. Operators can still see the diagnostic-safe
fields (endpoint, status code, byte count); the body itself is not
copied into the operator log surface.

The server module imports `mcp` and `httpx`, both standard PyPI
packages installed in the test venv. The test patches the module-
level httpx client so no real network call happens.
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from typing import Any

import pytest

_PROM_DIR = Path(__file__).resolve().parent
_SHARED = _PROM_DIR.parents[1] / "shared"
sys.path.insert(0, str(_PROM_DIR))
sys.path.insert(0, str(_SHARED))

# Module-level guard: server.py validates PROMETHEUS_URL at import time.
os.environ.setdefault("PROMETHEUS_URL", "http://prom.example.test:9090")

import server  # type: ignore  # noqa: E402

# Sensitive payload the upstream might return — internal stack trace,
# tenancy ids, anything an operator wouldn't want copied into agent log
# infrastructure. The redaction guarantee under test is: no substring of
# this string appears in any captured log record.
_SENSITIVE_BODY = (
    "PrometheusInternalError: tenant=acme-corp shard=42 trace=abc123def456 stack: at promql.eval (line 9001)"
)


class _FakeStreamResponse:
    """Minimal stand-in for httpx.Client.stream context manager response."""

    def __init__(self, status_code: int, body: bytes) -> None:
        self.status_code = status_code
        self._body = body

    def __enter__(self) -> _FakeStreamResponse:
        return self

    def __exit__(self, *exc: Any) -> None:
        return None

    def iter_bytes(self):  # noqa: D401
        # Yield in two chunks to exercise the streaming buffer path.
        if self._body:
            mid = max(1, len(self._body) // 2)
            yield self._body[:mid]
            yield self._body[mid:]


class _FakeHttpxClient:
    def __init__(self, status_code: int, body: bytes) -> None:
        self._status = status_code
        self._body = body

    def stream(self, _method: str, _url: str, **_kwargs: Any) -> _FakeStreamResponse:
        return _FakeStreamResponse(self._status, self._body)


def test_non_200_log_excludes_body_content(monkeypatch, caplog):
    """#1639: a 500 with sensitive content must not leak the body into logs."""
    fake_client = _FakeHttpxClient(500, _SENSITIVE_BODY.encode("utf-8"))
    monkeypatch.setattr(server, "_get_shared_httpx_client", lambda: fake_client)

    caplog.set_level(logging.WARNING, logger="tools.prometheus")

    with pytest.raises(server.PrometheusError, match="HTTP 500"):
        server._prom_get("/api/v1/query", {"query": "up"})

    # At least one WARNING was emitted for the upstream non-200.
    warning_records = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert warning_records, "expected a WARNING log on non-200 upstream"

    # The redaction guarantee: no piece of the sensitive body, nor any
    # of its distinctive substrings, can appear in any captured record
    # (formatted message OR raw args).
    sensitive_substrings = [
        _SENSITIVE_BODY,
        "tenant=acme-corp",
        "shard=42",
        "trace=abc123def456",
        "PrometheusInternalError",
        "promql.eval",
    ]
    for record in caplog.records:
        rendered = record.getMessage()
        # Also stringify the raw args tuple in case the formatter never ran.
        args_repr = repr(record.args) if record.args else ""
        for needle in sensitive_substrings:
            assert needle not in rendered, f"sensitive substring {needle!r} leaked into log message: {rendered!r}"
            assert needle not in args_repr, f"sensitive substring {needle!r} leaked into log args: {args_repr!r}"


def test_non_200_log_keeps_diagnostic_fields(monkeypatch, caplog):
    """#1639: status code, byte count, and endpoint stay in the log line."""
    body = _SENSITIVE_BODY.encode("utf-8")
    fake_client = _FakeHttpxClient(503, body)
    monkeypatch.setattr(server, "_get_shared_httpx_client", lambda: fake_client)

    caplog.set_level(logging.WARNING, logger="tools.prometheus")

    with pytest.raises(server.PrometheusError):
        server._prom_get("/api/v1/query", {"query": "up"})

    rendered = "\n".join(r.getMessage() for r in caplog.records)
    assert "503" in rendered, "status code must be retained for ops debugging"
    assert "/api/v1/query" in rendered, "endpoint path must be retained"
    assert str(len(body)) in rendered, "byte count must be retained"


def test_bearer_to_cloud_metadata_endpoint_refuses_to_start_even_with_plaintext_optin(
    monkeypatch,
):
    """#1652: bearer + cloud-metadata host must fail closed.

    PROMETHEUS_ALLOW_PLAINTEXT_BEARER=true is the documented escape hatch
    for trusted in-cluster mTLS / loopback transports — it must NOT
    authorise sending a bearer token to a cloud-provider instance-
    metadata IP. That endpoint is privileged regardless of transport.
    """
    import importlib

    monkeypatch.setenv("PROMETHEUS_URL", "http://169.254.169.254:9090")
    monkeypatch.setenv("PROMETHEUS_BEARER_TOKEN", "s3cret-token")
    monkeypatch.setenv("PROMETHEUS_ALLOW_PLAINTEXT_BEARER", "true")
    # Ensure no allow-list short-circuits the test before the metadata
    # check (the allow-list raises a different error first if set).
    monkeypatch.delenv("MCP_PROM_URL_ALLOWLIST", raising=False)

    # Drop cached server module so import-time guards re-run with the
    # patched environment.
    sys.modules.pop("server", None)

    with pytest.raises(RuntimeError) as excinfo:
        importlib.import_module("server")

    msg = str(excinfo.value)
    assert "1652" in msg, f"error message must cite #1652: {msg!r}"
    assert (
        "metadata" in msg.lower() or "169.254.169.254" in msg
    ), f"error message must identify the metadata endpoint: {msg!r}"

    # Restore a healthy server module for any subsequent tests in the
    # session, so test isolation is preserved.
    monkeypatch.setenv("PROMETHEUS_URL", "http://prom.example.test:9090")
    monkeypatch.delenv("PROMETHEUS_BEARER_TOKEN", raising=False)
    monkeypatch.delenv("PROMETHEUS_ALLOW_PLAINTEXT_BEARER", raising=False)
    sys.modules.pop("server", None)
    importlib.import_module("server")


if __name__ == "__main__":  # pragma: no cover
    sys.exit(pytest.main([__file__, "-q"]))
