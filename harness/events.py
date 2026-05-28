"""In-process event emitter + subscriber registry for ``GET /events/stream`` (#1110).

The wire contract lives in ``docs/events/events.schema.json``; the
runtime validator is ``shared/event_schema.py``.  This module wraps the
fan-out plumbing:

* :class:`EventEnvelope` — the dataclass serialised into each SSE frame.
* :class:`EventStream` — the per-process publish/subscribe registry with
  a bounded per-subscriber queue, a bounded in-memory ring for
  ``Last-Event-ID`` resume, and slow-subscriber eviction that surfaces
  a terminal ``stream.overrun`` event on the evicted queue before it
  closes.
* :func:`get_event_stream` — process-wide singleton accessor.

The stream is strictly in-process: every published event is validated,
assigned a monotonic id, timestamped at the emitter, pushed onto the
ring, and fanned out to each live subscriber's queue non-blockingly.
A subscriber that falls behind its queue cap is dropped so one laggard
client cannot stall publish() for every other subscriber.
"""

from __future__ import annotations

import asyncio
import logging
import os
import threading
from collections import deque
from collections.abc import AsyncIterator, Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

try:  # pragma: no cover — validator import guard
    from event_schema import KNOWN_TYPES, validate_envelope  # type: ignore[import-not-found]
except Exception:  # pragma: no cover
    # Fall through when harness is importable without /home/agent/shared on
    # PYTHONPATH; the harness Dockerfile sets PYTHONPATH=shared, tests add
    # the shared dir explicitly.
    from shared.event_schema import KNOWN_TYPES, validate_envelope  # type: ignore[no-redef]

logger = logging.getLogger(__name__)


EVENT_STREAM_QUEUE_MAX = int(os.environ.get("EVENT_STREAM_QUEUE_MAX", "1000"))
EVENT_STREAM_RING_MAX = int(os.environ.get("EVENT_STREAM_RING_MAX", "1000"))


@dataclass
class EventEnvelope:
    """Single SSE envelope — mirrors ``docs/events/events.schema.json``."""

    type: str
    version: int
    id: str
    ts: str
    agent_id: str | None
    payload: dict

    def to_dict(self) -> dict:
        return {
            "type": self.type,
            "version": self.version,
            "id": self.id,
            "ts": self.ts,
            "agent_id": self.agent_id,
            "payload": dict(self.payload),
        }


class _Subscriber:
    """Identity-hashable subscriber record.

    Avoids ``@dataclass`` here because default dataclass instances are
    unhashable with mutable fields and we need set membership for the
    subscriber registry.
    """

    __slots__ = ("queue", "closed")

    def __init__(self, queue: asyncio.Queue) -> None:
        self.queue = queue
        # When closed, subscribe()'s generator drains any remaining
        # items then exits.
        self.closed: bool = False

    def __hash__(self) -> int:  # identity semantics
        return id(self)

    def __eq__(self, other: object) -> bool:  # identity semantics
        return self is other


_OVERRUN_SENTINEL: Any = object()


@dataclass
class _PublisherMetrics:
    """Thin wrapper so tests can inspect counters without a Prometheus server.

    Wired by :func:`EventStream.attach_metrics` at app startup; when no
    metrics are attached every bump is a no-op.
    """

    subscribers: Any = None  # Gauge
    published_total: Any = None  # Counter(type=)
    dropped_total: Any = None  # Counter(reason=)
    overruns_total: Any = None  # Counter
    validation_errors_total: Any = None  # Counter(type=)
    ring_size: Any = None  # Gauge


class EventStream:
    """Per-process event broker.

    Not thread-safe; expected to be driven from a single asyncio loop.
    Methods are safe to call concurrently from the same loop.
    """

    def __init__(
        self,
        queue_max: int = EVENT_STREAM_QUEUE_MAX,
        ring_max: int = EVENT_STREAM_RING_MAX,
    ) -> None:
        self._queue_max = max(1, queue_max)
        self._ring_max = max(1, ring_max)
        self._ring: deque[EventEnvelope] = deque(maxlen=self._ring_max)
        self._subscribers: set[_Subscriber] = set()
        self._next_id: int = 0
        # #1582: cross-thread publish() calls (OTel span processor
        # threads, webhook callbacks) could corrupt the inc/dec sequence
        # and hand duplicate ids to SSE clients. Guard both mutations.
        self._id_lock = threading.Lock()
        self._metrics = _PublisherMetrics()

    # ---------- metrics wiring ----------

    def attach_metrics(
        self,
        *,
        subscribers: Any = None,
        published_total: Any = None,
        dropped_total: Any = None,
        overruns_total: Any = None,
        validation_errors_total: Any = None,
        ring_size: Any = None,
    ) -> None:
        """Wire Prometheus counters / gauges for publish-path observability."""
        self._metrics = _PublisherMetrics(
            subscribers=subscribers,
            published_total=published_total,
            dropped_total=dropped_total,
            overruns_total=overruns_total,
            validation_errors_total=validation_errors_total,
            ring_size=ring_size,
        )

    # ---------- subscription ----------

    def subscribe(self) -> AsyncIterator[EventEnvelope]:
        """Return a fresh async iterator that yields envelopes live.

        Each call creates its own bounded queue and its own generator.
        When the subscriber falls behind (queue full on publish) it is
        handed a terminal :class:`EventEnvelope` with ``type=stream.overrun``
        and its iterator closes.
        """
        sub = _Subscriber(asyncio.Queue(maxsize=self._queue_max))
        self._subscribers.add(sub)
        self._sync_subscribers_gauge()
        return self._iterate(sub)

    async def _iterate(self, sub: _Subscriber) -> AsyncIterator[EventEnvelope]:
        try:
            while True:
                item = await sub.queue.get()
                if item is _OVERRUN_SENTINEL:
                    # The publisher will have already enqueued a
                    # stream.overrun envelope before the sentinel; this
                    # sentinel only exists to unblock a queue.get() that
                    # was waiting when we filled the last slot.
                    return
                yield item
                if sub.closed and sub.queue.empty():
                    return
        finally:
            self._remove_subscriber(sub)

    def _remove_subscriber(self, sub: _Subscriber) -> None:
        if sub in self._subscribers:
            self._subscribers.discard(sub)
            self._sync_subscribers_gauge()

    def _sync_subscribers_gauge(self) -> None:
        if self._metrics.subscribers is not None:
            try:
                self._metrics.subscribers.set(len(self._subscribers))
            except Exception:  # pragma: no cover — metric plumbing best-effort
                pass

    # ---------- publish path ----------

    def publish(
        self,
        type_: str,
        payload: dict,
        agent_id: str | None = None,
        version: int = 1,
    ) -> EventEnvelope | None:
        """Validate + fan out a single event.

        Returns the envelope that was broadcast, or ``None`` when the
        event failed validation and was dropped.  Never raises.
        """
        with self._id_lock:
            self._next_id += 1
            _assigned_id = self._next_id
        # #1231: single now() sample avoids seconds/ms sampled across a
        # sub-millisecond rollover producing a non-monotonic ts.
        _now = datetime.now(timezone.utc)
        envelope = EventEnvelope(
            type=type_,
            version=version,
            id=str(_assigned_id),
            ts=_now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{_now.microsecond // 1000:03d}Z",
            agent_id=agent_id,
            payload=dict(payload),
        )
        err = validate_envelope(envelope.to_dict())
        if err is not None:
            logger.warning("event_stream: dropping invalid %r envelope: %s", type_, err)
            self._bump(self._metrics.validation_errors_total, labels={"type": type_})
            self._bump(self._metrics.dropped_total, labels={"reason": "validation"})
            # Roll the id back so the next successful publish keeps the
            # id sequence contiguous — clients passing Last-Event-ID must
            # not see synthetic gaps from validation drops. Only roll
            # back if no other publisher has moved the counter past
            # _assigned_id; otherwise we'd hand the same id out twice
            # (#1582).
            with self._id_lock:
                if self._next_id == _assigned_id:
                    self._next_id -= 1
            return None

        self._ring.append(envelope)
        if self._metrics.ring_size is not None:
            try:
                self._metrics.ring_size.set(len(self._ring))
            except Exception:
                pass

        self._fanout(envelope)
        self._bump(self._metrics.published_total, labels={"type": type_})
        return envelope

    def _fanout(self, envelope: EventEnvelope) -> None:
        """Push envelope onto every live subscriber's queue (non-blocking)."""
        # Copy so eviction during iteration is safe.
        for sub in list(self._subscribers):
            if sub.closed:
                continue
            try:
                sub.queue.put_nowait(envelope)
            except asyncio.QueueFull:
                self._evict_slow(sub)

    def _evict_slow(self, sub: _Subscriber) -> None:
        """Hand a slow subscriber an overrun envelope and close its iterator."""
        sub.closed = True
        self._bump(self._metrics.overruns_total)
        self._bump(self._metrics.dropped_total, labels={"reason": "overrun"})
        # Generate an overrun envelope scoped to this subscriber. The
        # envelope is NOT pushed to the ring or to other subscribers —
        # other subscribers saw no overrun. Do NOT increment the shared
        # ``_next_id`` counter for this synthetic envelope (#1140): it's
        # visible only to the evicted subscriber, so the non-evicted
        # subscribers must not see a gap in their Last-Event-ID
        # progression. Use a subscriber-scoped synthetic id based on
        # the current publish position suffixed with ".overrun" so the
        # evicted subscriber still sees a unique, monotonic id for the
        # terminal frame.
        # #1231: single now() sample.
        _now_ov = datetime.now(timezone.utc)
        overrun = EventEnvelope(
            type="stream.overrun",
            version=1,
            id=f"{self._next_id}.overrun",
            ts=_now_ov.strftime("%Y-%m-%dT%H:%M:%S.") + f"{_now_ov.microsecond // 1000:03d}Z",
            agent_id=None,
            payload={
                "queue_depth": sub.queue.qsize(),
                "queue_max": self._queue_max,
                "reason": "subscriber queue full; evicted",
            },
        )
        # Best effort: drain one slot so the overrun envelope lands.
        try:
            _ = sub.queue.get_nowait()
        except asyncio.QueueEmpty:
            pass
        try:
            sub.queue.put_nowait(overrun)
        except asyncio.QueueFull:
            pass
        # Sentinel so any waiting .get() wakes up and the iterator exits
        # deterministically.
        try:
            sub.queue.put_nowait(_OVERRUN_SENTINEL)
        except asyncio.QueueFull:
            # Queue is still jammed — leave it; the iterator will notice
            # `closed` after the next yield.
            pass
        logger.warning("event_stream: subscriber evicted — queue full (cap=%d)", self._queue_max)
        # Remove from the set eagerly so the subscribers gauge reflects
        # reality even if the iterator hasn't yet drained.
        self._remove_subscriber(sub)

    # ---------- replay ----------

    def replay_from(self, last_id: str | None) -> list[EventEnvelope]:
        """Return ring events with ``id > last_id`` in publish order.

        ``last_id`` that does not parse as an integer or is empty returns
        the entire ring window (bounded by ring_max).
        """
        if not last_id:
            return list(self._ring)
        try:
            last_n = int(last_id)
        except (TypeError, ValueError):
            return list(self._ring)
        out: list[EventEnvelope] = []
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
        return len(self._subscribers)

    @property
    def ring_size(self) -> int:
        return len(self._ring)

    def known_types(self) -> Iterable[str]:
        """Return the allow-list of envelope ``type`` strings the validator accepts.

        Sourced from ``shared.event_schema.KNOWN_TYPES`` — exposed as a
        method so callers (UIs, debug endpoints) can render the supported
        set without importing the schema module directly.
        """
        return KNOWN_TYPES

    # ---------- metric helper ----------

    @staticmethod
    def _bump(counter: Any, labels: dict | None = None) -> None:
        if counter is None:
            return
        try:
            if labels:
                counter.labels(**labels).inc()
            else:
                counter.inc()
        except Exception:  # pragma: no cover — metric plumbing best-effort
            pass


# ---------------------------------------------------------------------------
# Process-wide singleton
# ---------------------------------------------------------------------------
_singleton: EventStream | None = None


def get_event_stream() -> EventStream:
    """Return the process-wide EventStream singleton, creating it on first call.

    Production code reaches for the stream through this accessor so every
    publisher and SSE subscriber share the same fan-out queue. Tests that
    need isolation should call ``reset_event_stream_for_tests()`` to swap
    in a fresh instance rather than touching ``_singleton`` directly.
    """
    global _singleton
    if _singleton is None:
        _singleton = EventStream()
    return _singleton


def reset_event_stream_for_tests() -> EventStream:
    """Replace the process singleton with a fresh instance. Tests only."""
    global _singleton
    _singleton = EventStream()
    return _singleton


# ---------------------------------------------------------------------------
# Publish-envelope helper (shared with harness/main.py /internal/events/publish)
# ---------------------------------------------------------------------------

MAX_EVENT_PUBLISH_FIELD_BYTES = 4096


def parse_and_publish_envelope(
    body_bytes: bytes,
    *,
    stream: EventStream | None = None,
    rejected_counter: Any = None,
) -> tuple[int, str | None]:
    """Parse a raw POST body into an envelope and fan it out on *stream*.

    Returns ``(status_code, error_or_none)``. 204 on success, 400 on any
    structural or validation failure, 413 is NOT returned here — callers
    enforce body-size caps before they reach this function.

    This is the testable kernel of ``event_publish_handler`` in
    ``harness/main.py`` (#1110 phase 3). Keeping the logic here lets
    tests exercise the full validation path without pulling in the A2A
    SDK / uvicorn / prometheus_client at import time.

    ``rejected_counter`` is optional; when provided its
    ``labels(reason=...)`` counter is bumped on any rejection path. The
    caller is responsible for bumping an ``auth``-reason counter before
    this function is reached (auth happens above the body parse).
    """
    import json as _json

    if stream is None:
        stream = get_event_stream()

    def _bump(reason: str) -> None:
        if rejected_counter is None:
            return
        try:
            rejected_counter.labels(reason=reason).inc()
        except Exception:
            pass

    try:
        body = _json.loads(body_bytes.decode("utf-8") or "{}")
    except (UnicodeDecodeError, _json.JSONDecodeError) as exc:
        logger.warning("parse_and_publish_envelope: malformed JSON body: %r", exc)
        _bump("malformed_json")
        return 400, "malformed json"
    if not isinstance(body, dict):
        _bump("validation")
        return 400, "payload must be a JSON object"

    type_raw = body.get("type")
    payload_raw = body.get("payload")
    agent_id_raw = body.get("agent_id")
    version_raw = body.get("version", 1)
    if not isinstance(type_raw, str) or not type_raw:
        _bump("validation")
        return 400, "type is required"
    if not isinstance(payload_raw, dict):
        _bump("validation")
        return 400, "payload must be an object"
    if agent_id_raw is not None and not isinstance(agent_id_raw, str):
        _bump("validation")
        return 400, "agent_id must be a string or null"
    # Cap agent_id to the same limit as payload fields (#1146). An
    # uncapped agent_id let an unbounded string flow straight into
    # ``stream.publish`` and into every subscriber's queue, blowing
    # the per-field budget the payload path already enforces.
    if isinstance(agent_id_raw, str) and len(agent_id_raw) > MAX_EVENT_PUBLISH_FIELD_BYTES:
        agent_id_raw = agent_id_raw[:MAX_EVENT_PUBLISH_FIELD_BYTES] + "...[truncated]"
    if not isinstance(version_raw, int) or isinstance(version_raw, bool) or version_raw < 1:
        _bump("validation")
        return 400, "version must be a positive integer"

    # Per-field length caps on any string payload field (#924 mirror).
    def _cap_value(v: Any) -> Any:
        if isinstance(v, str) and len(v) > MAX_EVENT_PUBLISH_FIELD_BYTES:
            return v[:MAX_EVENT_PUBLISH_FIELD_BYTES] + "...[truncated]"
        return v

    capped_payload: dict = {}
    for k, v in payload_raw.items():
        if not isinstance(k, str):
            _bump("validation")
            return 400, "payload keys must be strings"
        capped_payload[k] = _cap_value(v)

    envelope = stream.publish(
        type_raw,
        capped_payload,
        agent_id=agent_id_raw,
        version=version_raw,
    )
    if envelope is None:
        _bump("validation")
        return 400, "validation failed"
    return 204, None
