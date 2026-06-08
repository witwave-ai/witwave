"""Unit tests for shared/hook_events.py (#779)."""

import asyncio
import importlib
import sys
from pathlib import Path

import pytest

_SHARED = Path(__file__).resolve().parents[1] / "shared"
sys.path.insert(0, str(_SHARED))


class _CountCounter:
    """Minimal stand-in for a Prometheus Counter — only needs .inc()."""

    def __init__(self) -> None:
        self.count = 0

    def inc(self, amount: int = 1) -> None:
        self.count += amount


def _reload(monkeypatch, url: str = "", token: str = "", cap: str = "32"):
    """Reload shared.hook_events with specific env so module-level
    constants are re-evaluated.

    #1693: clear the canonical ``HOOK_EVENTS_AUTH_TOKEN`` name too. The
    module's precedence chain (shared/hook_events.py:69-74) reads
    ``HOOK_EVENTS_AUTH_TOKEN`` FIRST, so a value leaked from the
    developer's shell or a CI env (e.g. an operator's
    ``HOOK_EVENTS_AUTH_TOKEN=...`` export) used to silently bypass
    ``token=""`` and broke test_missing_token_logs_once with stray
    connect-error WARNINGS instead of the expected single missing-token
    warning. Set all three names explicitly so the test arg is
    authoritative regardless of shell state.
    """
    monkeypatch.setenv("HARNESS_EVENTS_URL", url)
    monkeypatch.setenv("HOOK_EVENTS_AUTH_TOKEN", token)
    monkeypatch.setenv("HARNESS_EVENTS_AUTH_TOKEN", token)
    monkeypatch.setenv("TRIGGERS_AUTH_TOKEN", "")
    monkeypatch.setenv("HOOK_POST_MAX_INFLIGHT", cap)
    import hook_events  # type: ignore

    importlib.reload(hook_events)
    return hook_events


def test_no_url_returns_false_silently(monkeypatch):
    he = _reload(monkeypatch, url="")
    ok = he.schedule_post({"agent": "a", "tool": "shell", "decision": "deny"})
    assert ok is False
    assert len(he._INFLIGHT) == 0


def test_post_is_scheduled_when_url_and_token_set(monkeypatch):
    async def run():
        he = _reload(monkeypatch, url="http://harness", token="s")
        ok = he.schedule_post({"agent": "a", "tool": "shell", "decision": "deny"})
        assert ok is True
        # Wait for the task to finish (it will fail to POST since the
        # host doesn't resolve, but the failure path is what we want
        # covered — no raise, task cleans up).
        pending = list(he._INFLIGHT)
        await asyncio.gather(*pending, return_exceptions=True)
        # Done-callback discards from the set.
        assert len(he._INFLIGHT) == 0

    asyncio.run(run())


def test_shed_bumps_counter(monkeypatch):
    async def run():
        he = _reload(monkeypatch, url="http://harness", token="s", cap="2")
        # Pre-fill the inflight set so the cap is tripped immediately.
        # Use never-completing futures so the set stays full.
        loop = asyncio.get_running_loop()
        fake1 = loop.create_future()
        fake2 = loop.create_future()
        he._INFLIGHT.add(fake1)
        he._INFLIGHT.add(fake2)

        counter = _CountCounter()
        ok = he.schedule_post(
            {"agent": "a", "tool": "shell", "decision": "deny"},
            shed_counter=counter,
        )
        assert ok is False, "cap=2 with 2 in flight should shed"
        assert counter.count == 1, "shed_counter should be incremented once"

        # Clean up the fakes so pytest doesn't complain.
        fake1.set_result(None)
        fake2.set_result(None)
        he._INFLIGHT.discard(fake1)
        he._INFLIGHT.discard(fake2)

    asyncio.run(run())


def test_missing_token_logs_once(monkeypatch, caplog):
    import logging

    async def run():
        he = _reload(monkeypatch, url="http://harness", token="")
        with caplog.at_level(logging.WARNING, logger="hook_events"):
            he.schedule_post({"agent": "a"})
            he.schedule_post({"agent": "b"})
            he.schedule_post({"agent": "c"})
            # Let the scheduled tasks run _post_once which triggers the
            # missing-token warning path.
            pending = list(he._INFLIGHT)
            await asyncio.gather(*pending, return_exceptions=True)
        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert (
            len(warnings) == 1
        ), "missing-token warning must fire exactly once per process to avoid log flood on sustained misconfig"

    asyncio.run(run())


def test_schedule_event_post_builds_envelope(monkeypatch):
    """schedule_event_post (#1110 phase 3) builds a valid envelope and
    calls into the shared POST path with the publish URL + bearer."""

    async def run():
        he = _reload(monkeypatch, url="http://harness", token="s")
        # Replace _post_once_to with a capture stub so we see the
        # url + body + the fact that it was actually invoked.
        calls: list[tuple[str, dict]] = []

        async def _capture(url, body):
            calls.append((url, body))

        monkeypatch.setattr(he, "_post_once_to", _capture)
        ok = he.schedule_event_post(
            "conversation.turn",
            {
                "session_id_hash": "abcdef012345",
                "role": "user",
                "content_bytes": 10,
                "model": "m1",
            },
            agent_id="iris",
        )
        assert ok is True
        pending = list(he._INFLIGHT)
        await asyncio.gather(*pending, return_exceptions=True)
        assert len(calls) == 1, f"expected one POST, got {calls}"
        url, body = calls[0]
        assert url.endswith("/internal/events/publish"), url
        assert body["type"] == "conversation.turn"
        assert body["agent_id"] == "iris"
        assert body["version"] == 1
        assert body["payload"]["session_id_hash"] == "abcdef012345"
        # envelope carries a `ts` and a placeholder `id` — the harness
        # rewrites the id at receive time.
        assert isinstance(body["ts"], str) and body["ts"].endswith("Z")
        assert "id" in body

    asyncio.run(run())


def test_schedule_event_post_drops_invalid_envelope(monkeypatch):
    """Schema validation at emit time drops malformed events without
    invoking the POST path. Caller never observes an exception."""

    async def run():
        he = _reload(monkeypatch, url="http://harness", token="s")
        calls: list[tuple[str, dict]] = []

        async def _capture(url, body):
            calls.append((url, body))

        monkeypatch.setattr(he, "_post_once_to", _capture)
        # Missing required `outcome`.
        ok = he.schedule_event_post(
            "tool.use",
            {
                "session_id_hash": "abcdef012345",
                "tool": "Bash",
                "duration_ms": 5,
            },
            agent_id="iris",
        )
        assert ok is False
        assert calls == []

    asyncio.run(run())


def test_schedule_event_post_no_url_silent_drop(monkeypatch):
    """With HARNESS_EVENTS_URL unset, schedule_event_post silently drops."""
    he = _reload(monkeypatch, url="")
    ok = he.schedule_event_post(
        "conversation.turn",
        {
            "session_id_hash": "abcdef012345",
            "role": "user",
            "content_bytes": 10,
        },
        agent_id="iris",
    )
    assert ok is False
    assert len(he._INFLIGHT) == 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(pytest.main([__file__, "-q"]))
