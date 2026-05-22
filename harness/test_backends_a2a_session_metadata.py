"""Regression coverage for stable first-turn A2A session metadata."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "harness"))
sys.path.insert(0, str(_ROOT / "shared"))


def test_run_query_sends_session_id_metadata_before_context_exists():
    try:
        from backends.a2a import A2ABackend
        from backends.config import BackendConfig
    except ImportError:
        from harness.backends.a2a import A2ABackend  # type: ignore
        from harness.backends.config import BackendConfig  # type: ignore

    backend = A2ABackend(BackendConfig(id="codex", url="http://codex:8000", model=None, auth_env=None))
    captured: dict = {}

    async def fake_post(url: str, body: bytes, traceparent: str | None = None) -> str:
        captured["url"] = url
        captured["payload"] = json.loads(body.decode("utf-8"))
        return json.dumps(
            {
                "jsonrpc": "2.0",
                "id": captured["payload"]["id"],
                "result": {
                    "kind": "message",
                    "role": "agent",
                    "messageId": "reply-1",
                    "contextId": "backend-context",
                    "parts": [{"kind": "text", "text": "ok"}],
                },
            }
        )

    backend._post_with_retry = fake_post  # type: ignore[method-assign]

    try:
        result = asyncio.run(backend.run_query("hello", session_id="stable-session", is_new=True))
    finally:
        asyncio.run(backend.close())

    assert result == ["ok"]
    message = captured["payload"]["params"]["message"]
    assert "contextId" not in message
    assert message["metadata"]["session_id"] == "stable-session"


def test_run_query_keeps_session_id_metadata_after_context_exists():
    try:
        from backends.a2a import A2ABackend
        from backends.config import BackendConfig
    except ImportError:
        from harness.backends.a2a import A2ABackend  # type: ignore
        from harness.backends.config import BackendConfig  # type: ignore

    backend = A2ABackend(BackendConfig(id="codex", url="http://codex:8000", model=None, auth_env=None))
    captured: list[dict] = []

    async def fake_post(url: str, body: bytes, traceparent: str | None = None) -> str:
        payload = json.loads(body.decode("utf-8"))
        captured.append(payload)
        return json.dumps(
            {
                "jsonrpc": "2.0",
                "id": payload["id"],
                "result": {
                    "kind": "message",
                    "role": "agent",
                    "messageId": f"reply-{len(captured)}",
                    "contextId": "backend-context",
                    "parts": [{"kind": "text", "text": "ok"}],
                },
            }
        )

    backend._post_with_retry = fake_post  # type: ignore[method-assign]

    try:
        asyncio.run(backend.run_query("hello", session_id="stable-session", is_new=True))
        asyncio.run(backend.run_query("again", session_id="stable-session", is_new=False))
    finally:
        asyncio.run(backend.close())

    second = captured[1]["params"]["message"]
    assert second["contextId"] == "stable-session"
    assert second["metadata"]["session_id"] == "stable-session"
