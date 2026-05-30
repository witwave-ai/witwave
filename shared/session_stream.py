"""Per-session SSE broadcaster for backend drill-down streams (#1110 phase 4).

Each backend (`claude`, `openai`, `gemini`) exposes
``GET /api/sessions/<session_id>/stream``; this module provides the
fan-out plumbing that feeds that endpoint.  The wire contract is the
same SSE envelope used by ``GET /events/stream`` on the harness — the
only differences are:

* **Scope.**  Events are scoped to one ``session_id`` and one backend
  process.  No cross-pod sharing; no tee to harness.  Chunk-level
  traffic would flood the multiplexed harness timeline; keeping it
  per-backend matches the drill-down use case.
* **Lifecycle.**  Broadcasters are created lazily when the first chunk
  is published for a session (or when the first subscriber arrives)
  and linger ``CONVERSATION_STREAM_GRACE_SEC`` seconds after the last
  subscriber disconnects so brief reconnects can resume from the ring.

Public surface:

* :class:`SessionStream` — per-session broadcaster.
* :func:`get_session_stream` — registry lookup + lazy creation.
* :func:`drop_session_stream` — test/cleanup hook.
* :func:`session_id_hash` — 12-char SHA-256 prefix helper; callers use
  this in every event payload so the raw session_id never leaves the
  process.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import time
from collections import deque
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

try:  # pragma: no cover — validator import guard
    from event_schema import validate_envelope  # type: ignore[import-not-found]
except Exception:  # pragma: no cover
    from shared.event_schema import validate_envelope  # type: ignore[no-redef]

logger = logging.getLogger(__name__)


CONVERSATION_STREAM_QUEUE_MAX = int(os.environ.get("CONVERSATION_STREAM_QUEUE_MAX", "500"))
CONVERSATION_STREAM_RING_MAX = int(os.environ.get("CONVERSATION_STREAM_RING_MAX", "200"))
CONVERSATION_STREAM_KEEPALIVE_SEC = float(os.environ.get("CONVERSATION_STREAM_KEEPALIVE_SEC", "15"))
CONVERSATION_STREAM_GRACE_SEC = float(os.environ.get("CONVERSATION_STREAM_GRACE_SEC", "60"))
# Periodic sweeper cadence (#1735). Defaults to half the grace window
# so an idle broadcaster gets evicted within a single grace period.
CONVERSATION_STREAM_SWEEP_INTERVAL_SEC = float(
    os.environ.get(
        "CONVERSATION_STREAM_SWEEP_INTERVAL_SEC",
        str(max(15.0, CONVERSATION_STREAM_GRACE_SEC / 2)),
    )
)
# Hard ceiling on the registry size (#1735). When an insertion would
# push the registry past this cap, the oldest broadcaster (insertion
# order) is dropped first as a belt-and-braces guard against the slow
# leak the periodic sweeper closes from the other side. Defaults to
# 50 000 — comfortably above any single-pod legitimate load while
# still bounding worst-case memory.
CONVERSATION_STREAM_REGISTRY_MAX = max(
    1,
    int(os.environ.get("CONVERSATION_STREAM_REGISTRY_MAX", "50000")),
)


@dataclass
class SessionStreamEnvelope:
    """One published envelope — serialised into a single SSE frame."""

    type: str
    version: int
    id: str
    ts: str
    agent_id: str | None
    payload: dict

    def to_dict(self) -> dict:
        """Return a plain-dict view of the envelope for JSON serialisation.

        ``payload`` is shallow-copied so a downstream consumer can mutate
        the returned dict without aliasing this envelope's stored payload.
        """
        return {
            "type": self.type,
            "version": self.version,
            "id": self.id,
            "ts": self.ts,
            "agent_id": self.agent_id,
            "payload": dict(self.payload),
        }


_OVERRUN_SENTINEL: Any = object()


class _Subscriber:
    __slots__ = ("queue", "closed")

    def __init__(self, queue: asyncio.Queue) -> None:
        self.queue = queue
        self.closed: bool = False

    def __hash__(self) -> int:
        return id(self)

    def __eq__(self, other: object) -> bool:
        return self is other


def session_id_hash(session_id: str) -> str:
    """Return the 12-char sha256 prefix used in event payloads."""
    if not isinstance(session_id, str):
        session_id = str(session_id or "")
    digest = hashlib.sha256(session_id.encode("utf-8")).hexdigest()
    return digest[:12]


def _now_iso_ms() -> str:
    now = datetime.now(timezone.utc)
    return now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{now.microsecond // 1000:03d}Z"


class SessionStream:
    """Per-session broadcaster.

    Not thread-safe; expected to be driven from a single asyncio loop.
    Methods are safe to call concurrently from that loop.
    """

    def __init__(
        self,
        session_id: str,
        *,
        queue_max: int = CONVERSATION_STREAM_QUEUE_MAX,
        ring_max: int = CONVERSATION_STREAM_RING_MAX,
        agent_id: str | None = None,
    ) -> None:
        self.session_id = session_id
        self.session_hash = session_id_hash(session_id)
        self._queue_max = max(1, queue_max)
        self._ring_max = max(1, ring_max)
        self._ring: deque[SessionStreamEnvelope] = deque(maxlen=self._ring_max)
        self._subscribers: set[_Subscriber] = set()
        self._next_id: int = 0
        self._agent_id = agent_id
        # Per-turn chunk sequence number (#1139).  Spans both user and
        # assistant roles within a single turn so seq is monotonic
        # across roles — previously only assistant chunks consumed the
        # counter and the user chunk always claimed seq=0, colliding
        # with the first assistant chunk.  Callers invoke
        # :meth:`reset_turn_seq` at the start of each turn and then
        # :meth:`next_turn_seq` for every role-chunk they emit.
        self._turn_seq: int = 0
        # Last-unsubscribed wall-time; used by the registry to decide
        # when the grace period has elapsed.  Set at broadcaster
        # construction (#1147) so a newly-created broadcaster with no
        # subscribers is already on the idle clock — previously
        # ``is_idle_past`` started counting on first inspection, which
        # delayed sweeper eviction by one tick.
        self._idle_since: float | None = time.monotonic()
        # Last publish-activity monotonic timestamp (#1147). Used in
        # conjunction with ``_idle_since`` so a broadcaster that is
        # still receiving publishes doesn't get swept even if it has
        # no live subscribers.
        self._last_activity: float = time.monotonic()

    # ---------- subscription ----------

    def subscribe(self) -> AsyncIterator[SessionStreamEnvelope]:
        """Register a new subscriber and return its async envelope iterator.

        Each call yields a fresh subscriber bound to a private
        bounded queue (``_queue_max``); attaching a subscriber clears
        the idle clock so the registry sweeper (#1147) won't reap a
        broadcaster that just acquired a consumer. The returned
        async-generator removes the subscriber from the set on exit.
        """
        sub = _Subscriber(asyncio.Queue(maxsize=self._queue_max))
        self._subscribers.add(sub)
        # First subscriber attached — clear the idle clock (#1147).
        self._idle_since = None
        return self._iterate(sub)

    async def _iterate(self, sub: _Subscriber) -> AsyncIterator[SessionStreamEnvelope]:
        try:
            while True:
                item = await sub.queue.get()
                if item is _OVERRUN_SENTINEL:
                    return
                yield item
                if sub.closed and sub.queue.empty():
                    return
        finally:
            self._remove_subscriber(sub)

    def _remove_subscriber(self, sub: _Subscriber) -> None:
        if sub in self._subscribers:
            self._subscribers.discard(sub)
        if not self._subscribers:
            self._idle_since = time.monotonic()

    # ---------- publish path ----------

    def publish_chunk(
        self,
        *,
        role: str,
        seq: int,
        content: str,
        final: bool,
    ) -> SessionStreamEnvelope | None:
        """Emit a ``conversation.chunk`` envelope for this session."""
        return self.publish(
            "conversation.chunk",
            {
                "session_id_hash": self.session_hash,
                "role": role,
                "seq": int(seq),
                "content": str(content),
                "final": bool(final),
            },
        )

    def next_turn_seq(self) -> int:
        """Allocate + return the next per-turn seq (#1139).

        The counter covers both user and assistant chunks within a
        single turn so observers see a strictly monotonic seq across
        roles.  Callers invoke this for every chunk they publish,
        including the initial user chunk.
        """
        n = self._turn_seq
        self._turn_seq += 1
        return n

    def reset_turn_seq(self) -> None:
        """Reset the per-turn chunk counter.  Call at the start of each turn."""
        self._turn_seq = 0

    # Deprecated aliases kept for backward compatibility with callers
    # that predate #1139.  The semantics are identical now — both roles
    # share the same counter.  Scheduled for removal once all backend
    # executors are migrated.
    def next_assistant_seq(self) -> int:
        """Deprecated alias for :meth:`next_turn_seq` (#1139 backcompat)."""
        return self.next_turn_seq()

    def reset_assistant_seq(self) -> None:
        """Deprecated alias for :meth:`reset_turn_seq` (#1139 backcompat)."""
        self.reset_turn_seq()

    def publish(
        self,
        type_: str,
        payload: dict,
        *,
        version: int = 1,
    ) -> SessionStreamEnvelope | None:
        """Validate + fan out a single envelope.  Never raises."""
        self._next_id += 1
        envelope = SessionStreamEnvelope(
            type=type_,
            version=version,
            id=str(self._next_id),
            ts=_now_iso_ms(),
            agent_id=self._agent_id,
            payload=dict(payload),
        )
        err = validate_envelope(envelope.to_dict())
        if err is not None:
            logger.warning(
                "session_stream: dropping invalid %r envelope for session %s: %s",
                type_,
                self.session_hash,
                err,
            )
            self._next_id -= 1
            return None

        self._ring.append(envelope)
        # Refresh activity timestamp (#1147) — a broadcaster that is
        # still receiving publishes isn't idle even if all subscribers
        # have detached.
        self._last_activity = time.monotonic()
        self._fanout(envelope)
        return envelope

    def _fanout(self, envelope: SessionStreamEnvelope) -> None:
        for sub in list(self._subscribers):
            if sub.closed:
                continue
            try:
                sub.queue.put_nowait(envelope)
            except asyncio.QueueFull:
                self._evict_slow(sub)

    def _evict_slow(self, sub: _Subscriber) -> None:
        sub.closed = True
        self._next_id += 1
        overrun = SessionStreamEnvelope(
            type="stream.overrun",
            version=1,
            id=str(self._next_id),
            ts=_now_iso_ms(),
            agent_id=self._agent_id,
            payload={
                "queue_depth": sub.queue.qsize(),
                "queue_max": self._queue_max,
                "reason": "subscriber queue full; evicted",
            },
        )
        try:
            _ = sub.queue.get_nowait()
        except asyncio.QueueEmpty:
            pass
        try:
            sub.queue.put_nowait(overrun)
        except asyncio.QueueFull:
            pass
        try:
            sub.queue.put_nowait(_OVERRUN_SENTINEL)
        except asyncio.QueueFull:
            pass
        logger.warning(
            "session_stream: subscriber for session %s evicted — queue full (cap=%d)",
            self.session_hash,
            self._queue_max,
        )
        if sub in self._subscribers:
            self._subscribers.discard(sub)
            if not self._subscribers:
                self._idle_since = time.monotonic()

    # ---------- replay ----------

    def replay_from(self, last_id: str | None) -> list[SessionStreamEnvelope]:
        """Return ring-buffered envelopes with ``id`` strictly greater than ``last_id``.

        When ``last_id`` is falsy or cannot be parsed as an int the full
        ring is returned (used for first-attach replay). Envelopes whose
        own ``id`` isn't an int are skipped silently — defensive against
        any future non-numeric id schemes co-existing in the ring.
        """
        if not last_id:
            return list(self._ring)
        try:
            last_n = int(last_id)
        except (TypeError, ValueError):
            return list(self._ring)
        out: list[SessionStreamEnvelope] = []
        for ev in self._ring:
            try:
                if int(ev.id) > last_n:
                    out.append(ev)
            except ValueError:
                continue
        return out

    # ---------- introspection ----------

    @property
    def subscriber_count(self) -> int:
        """Number of currently attached subscribers."""
        return len(self._subscribers)

    @property
    def ring_size(self) -> int:
        """Number of envelopes currently retained in the replay ring."""
        return len(self._ring)

    def is_idle_past(self, grace_sec: float) -> bool:
        """True iff no subscribers AND no publishes for ``grace_sec`` (#1147).

        A broadcaster qualifies as idle only when both the subscriber
        set is empty and the last publish is older than the grace
        window.  ``_idle_since`` is set at construction and reset each
        time a subscriber attaches/detaches; ``_last_activity`` is
        bumped by every :meth:`publish` so a still-active session
        isn't reaped out from under live publishers.
        """
        if self._subscribers:
            return False
        if self._idle_since is None:
            # Defensive — a detached subscriber should have set this,
            # but guard against races where the idle clock was missed.
            self._idle_since = time.monotonic()
            return False
        now = time.monotonic()
        since_idle = now - self._idle_since
        since_publish = now - self._last_activity
        # Require BOTH clocks past the grace window: a broadcaster that
        # has no subscribers but is still receiving publishes is not
        # idle yet.
        return since_idle >= grace_sec and since_publish >= grace_sec


# ---------------------------------------------------------------------------
# Process-wide registry
# ---------------------------------------------------------------------------
_registry: dict[str, SessionStream] = {}


def get_session_stream(session_id: str, *, agent_id: str | None = None, create: bool = True) -> SessionStream | None:
    """Return the broadcaster for *session_id*, creating one if absent.

    When ``create=False`` returns ``None`` for unknown sessions — useful
    for the SSE handler to distinguish "unknown session" from "known
    but idle".

    #1735: when an insertion would push ``len(_registry)`` past
    :data:`CONVERSATION_STREAM_REGISTRY_MAX`, the oldest entry (insertion
    order) is evicted first as a belt-and-braces guard against the
    slow-leak risk the periodic sweeper closes from the other side. Any
    in-flight subscribers on the evicted broadcaster receive a
    ``stream.overrun`` envelope + sentinel via the same teardown path
    used by :func:`sweep_idle_streams` so their HTTP connections don't
    leak.
    """
    stream = _registry.get(session_id)
    if stream is not None:
        return stream
    if not create:
        return None
    if len(_registry) >= CONVERSATION_STREAM_REGISTRY_MAX:
        _evict_oldest_broadcaster()
    stream = SessionStream(session_id, agent_id=agent_id)
    _registry[session_id] = stream
    return stream


def _evict_oldest_broadcaster() -> None:
    """Drop the oldest registry entry, terminating any subscribers on it.

    Mirrors the subscriber-teardown path in :func:`sweep_idle_streams`
    so the LRU eviction route doesn't strand long-running SSE callers
    on a queue that no broadcaster can ever publish to again.
    """
    if not _registry:
        return
    oldest_sid = next(iter(_registry))
    stream = _registry.get(oldest_sid)
    if stream is None:
        _registry.pop(oldest_sid, None)
        return
    for sub in list(stream._subscribers):  # noqa: SLF001
        sub.closed = True
        stream._next_id += 1  # noqa: SLF001
        overrun = SessionStreamEnvelope(
            type="stream.overrun",
            version=1,
            id=str(stream._next_id),  # noqa: SLF001
            ts=_now_iso_ms(),
            agent_id=stream._agent_id,  # noqa: SLF001
            payload={
                "queue_depth": sub.queue.qsize(),
                "queue_max": stream._queue_max,  # noqa: SLF001
                "reason": "broadcaster evicted by registry-cap LRU",
            },
        )
        try:
            sub.queue.put_nowait(overrun)
        except asyncio.QueueFull:
            pass
        try:
            sub.queue.put_nowait(_OVERRUN_SENTINEL)
        except asyncio.QueueFull:
            pass
        stream._subscribers.discard(sub)  # noqa: SLF001
    _registry.pop(oldest_sid, None)
    logger.warning(
        "session_stream: registry hit cap %d — evicted oldest broadcaster",
        CONVERSATION_STREAM_REGISTRY_MAX,
    )


def drop_session_stream(session_id: str) -> None:
    """Forcibly evict a broadcaster from the registry."""
    _registry.pop(session_id, None)


def sweep_idle_streams(
    grace_sec: float = CONVERSATION_STREAM_GRACE_SEC,
) -> int:
    """Drop any broadcasters that have been idle past ``grace_sec``.

    Intended to be called from a periodic sweeper task.  Returns the
    number of broadcasters evicted.

    Before a broadcaster is popped from the registry, any lingering
    subscriber queues are terminated by enqueuing a ``stream.overrun``
    envelope plus the overrun sentinel (#1148).  Without this step,
    evictor-race subscribers that attached between ``is_idle_past``
    and ``pop`` would be stranded waiting on ``queue.get()`` forever
    — their queue would go unreferenced, their ``_iterate`` coroutine
    would leak, and their HTTP connection would sit open until the
    client-side timeout.

    #1418 INVARIANT: the `_registry` dict mutations here + in
    `get_session_stream` assume single-asyncio-loop discipline. If a
    future refactor dispatches this sweeper from a worker thread (or
    adds a concurrent asyncio.to_thread(sweep_idle_streams) caller),
    add a lock around _registry mutations to prevent half-popped
    entries being observed by concurrent publish/subscribe.
    """
    # #1418: assert event-loop affinity so a future threaded call
    # path fails loudly instead of silently racing the registry.
    try:
        import asyncio as _asyncio

        _asyncio.get_running_loop()
    except RuntimeError:
        # Called outside a running asyncio loop — OK for tests that
        # drive the sweeper synchronously, but emit a once-per-process
        # WARN so operators notice if this fires in production.
        if not getattr(sweep_idle_streams, "_no_loop_warned", False):
            logger.warning(
                "sweep_idle_streams: called outside running asyncio loop. "
                "_registry mutations assume single-loop discipline; "
                "threaded callers must add a lock. (#1418)"
            )
            try:
                sweep_idle_streams._no_loop_warned = True  # type: ignore[attr-defined]
            except Exception:
                pass
    dropped: list[str] = []
    for sid, stream in list(_registry.items()):
        if stream.is_idle_past(grace_sec):
            dropped.append(sid)
    for sid in dropped:
        stream = _registry.get(sid)
        if stream is not None:
            # Terminate any stragglers before dropping the broadcaster.
            for sub in list(stream._subscribers):  # noqa: SLF001 — intentional
                sub.closed = True
                # #1294: increment per subscriber so each terminal envelope
                # carries a unique id; otherwise N subscribers all see the
                # same Last-Event-ID on close.
                stream._next_id += 1  # noqa: SLF001
                overrun = SessionStreamEnvelope(
                    type="stream.overrun",
                    version=1,
                    id=str(stream._next_id),  # noqa: SLF001
                    ts=_now_iso_ms(),
                    agent_id=stream._agent_id,  # noqa: SLF001
                    payload={
                        "queue_depth": sub.queue.qsize(),
                        "queue_max": stream._queue_max,  # noqa: SLF001
                        "reason": "broadcaster swept while subscriber attached",
                    },
                )
                try:
                    sub.queue.put_nowait(overrun)
                except asyncio.QueueFull:
                    pass
                try:
                    sub.queue.put_nowait(_OVERRUN_SENTINEL)
                except asyncio.QueueFull:
                    pass
                stream._subscribers.discard(sub)  # noqa: SLF001
        _registry.pop(sid, None)
    if dropped:
        logger.debug("session_stream: swept %d idle broadcasters", len(dropped))
    return len(dropped)


def reset_session_streams_for_tests() -> None:
    """Clear the registry.  Tests only."""
    _registry.clear()


def registry_size() -> int:
    """Return the number of live SessionStream entries in the process-wide registry."""
    return len(_registry)


async def run_idle_sweeper(
    interval_sec: float = CONVERSATION_STREAM_SWEEP_INTERVAL_SEC,
    grace_sec: float = CONVERSATION_STREAM_GRACE_SEC,
) -> None:
    """Periodic sweeper coroutine for backend lifespans (#1735).

    Calls :func:`sweep_idle_streams` every ``interval_sec`` seconds.
    Designed to be wrapped in each backend's ``_guarded`` restart-loop
    so a transient failure in the sweep path doesn't take readiness
    down. Cancellation propagates so graceful shutdown drains cleanly.

    Without this sweeper, ``_registry`` grows unbounded — every fresh
    ``session_id`` (including HMAC-bound IDs that scale with
    caller×session, not just session) inserts and never evicts. The
    LRU cap on insertion provides a belt-and-braces backstop, but the
    periodic sweeper is what keeps memory steady-state.
    """
    while True:
        try:
            await asyncio.sleep(interval_sec)
            sweep_idle_streams(grace_sec=grace_sec)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # pragma: no cover — defensive
            logger.warning("session_stream sweeper iteration failed: %r", exc)


# ---------------------------------------------------------------------------
# SSE route factory
# ---------------------------------------------------------------------------


def _sse_serialise(envelope: SessionStreamEnvelope) -> bytes:
    import json as _json

    body = _json.dumps(envelope.to_dict(), separators=(",", ":"))
    return (f"event: {envelope.type}\nid: {envelope.id}\ndata: {body}\n\n").encode()


def make_session_stream_handler(
    auth_token: str,
    *,
    agent_id: str | None = None,
    keepalive_sec: float = CONVERSATION_STREAM_KEEPALIVE_SEC,
):
    """Return a Starlette ASGI handler for ``GET /api/sessions/{id}/stream``.

    The factory keeps the three backend ``main.py`` wirings identical
    (same auth posture, same keepalive cadence, same Last-Event-ID
    resume).  Each backend passes its own ``CONVERSATIONS_AUTH_TOKEN``
    and agent identity for the envelope metadata.
    """
    import hashlib as _hashlib
    import hmac as _hmac
    import os as _os

    from starlette.requests import Request
    from starlette.responses import JSONResponse, StreamingResponse

    try:
        from conversations import auth_disabled_escape_hatch  # type: ignore
    except Exception:  # pragma: no cover
        from shared.conversations import auth_disabled_escape_hatch  # type: ignore

    # #1399: per-caller concurrent-stream cap so one authed caller
    # opening many SSE streams can't exhaust FDs. Key on the
    # fingerprint of the presented bearer (SHA-256 prefix) so the cap
    # is per-credential. Defaults to 8 streams per caller.
    # #1645: clamp at zero so a negative value disables the cap deliberately
    # rather than skipping the whole gate at line 562 (`if _per_caller_max > 0:`),
    # which would let one caller open unlimited concurrent SSE streams.
    _per_caller_max = max(0, int(_os.environ.get("SESSION_STREAM_MAX_PER_CALLER", "8")))
    _per_caller_counts: dict[str, int] = {}

    async def handler(request: Request):
        # Auth — parity with /conversations, /trace, /mcp, /api/traces.
        if not auth_token:
            if not auth_disabled_escape_hatch():
                return JSONResponse({"error": "auth not configured"}, status_code=503)
        else:
            header = request.headers.get("Authorization", "")
            if not _hmac.compare_digest(f"Bearer {auth_token}", header):
                return JSONResponse({"error": "unauthorized"}, status_code=401)

        session_id = request.path_params.get("session_id") or ""
        if not session_id:
            return JSONResponse({"error": "missing session_id"}, status_code=400)

        # #1399: cap concurrent streams per caller.
        _bearer = request.headers.get("Authorization", "")[:128]
        _caller_fp = _hashlib.sha256(_bearer.encode("utf-8", errors="replace")).hexdigest()[:16]
        if _per_caller_max > 0:
            cur = _per_caller_counts.get(_caller_fp, 0)
            if cur >= _per_caller_max:
                return JSONResponse(
                    {"error": "too many concurrent streams for this caller"},
                    status_code=429,
                )
            _per_caller_counts[_caller_fp] = cur + 1

        # #1403: if any setup step between cap-reservation and the
        # body generator raises (get_session_stream, replay_from,
        # client disconnect before body starts), release the slot.
        # Without this, ~8 errored connects from the same bearer
        # permanently lock the caller out at 429.
        # #1415: make idempotent via a closure flag so the body-generator
        # finally + BackgroundTask fallback can't double-decrement.
        _released = [False]

        def _release_slot() -> None:
            if _per_caller_max <= 0:
                return
            if _released[0]:
                return
            _released[0] = True
            try:
                _cur = _per_caller_counts.get(_caller_fp, 0)
                if _cur <= 1:
                    _per_caller_counts.pop(_caller_fp, None)
                else:
                    _per_caller_counts[_caller_fp] = _cur - 1
            except Exception:
                pass

        try:
            stream = get_session_stream(session_id, agent_id=agent_id, create=True)
            last_event_id = request.headers.get("Last-Event-ID") or request.query_params.get("last_event_id")
            replay = stream.replay_from(last_event_id) if last_event_id else []
        except Exception:
            _release_slot()
            raise

        async def _body():
            # Initial comment so proxies flush headers early.
            yield b": stream-start\n\n"
            # Replay ring tail (if requested).
            for ev in replay:
                yield _sse_serialise(ev)
            sub_iter = stream.subscribe()
            try:
                while True:
                    try:
                        envelope = await asyncio.wait_for(sub_iter.__anext__(), timeout=keepalive_sec)
                    except asyncio.TimeoutError:
                        yield b": keepalive\n\n"
                        continue
                    except StopAsyncIteration:
                        break
                    yield _sse_serialise(envelope)
                    if envelope.type == "stream.overrun":
                        break
            except asyncio.CancelledError:
                raise
            finally:
                # Best-effort close; iterator __aexit__ triggers
                # _remove_subscriber() which starts the idle clock.
                _aclose = getattr(sub_iter, "aclose", None)
                if _aclose is not None:
                    try:
                        await _aclose()
                    except Exception:
                        pass
                # #1399/#1415: release the per-caller cap slot via
                # the shared idempotent helper so the BackgroundTask
                # fallback can't double-decrement.
                _release_slot()

        # #1415: attach _release_slot as a Starlette BackgroundTask so
        # the slot is released unconditionally — covering the case where
        # the client disconnects between handler return and the body
        # generator's first yield (and the generator is never invoked).
        # The finally inside _body() ALSO releases, but the per-fp
        # counter's decrement is idempotent (bounded at 0 via `cur <= 1`).
        from starlette.background import BackgroundTask

        async def _release_slot_async() -> None:
            _release_slot()

        return StreamingResponse(
            _body(),
            media_type="text/event-stream",
            background=BackgroundTask(_release_slot_async),
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
                "Connection": "keep-alive",
            },
        )

    return handler
