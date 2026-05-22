"""Internal async message bus for the harness scheduler.

Dedup semantics (#615)
----------------------
Two enqueue paths exist, and the choice between them is deliberate:

* :meth:`MessageBus.send` — always enqueues, always awaits the result.
  Used by trigger dispatch, ad-hoc run endpoints, continuations, and any
  caller that must not silently drop a request. The ``_pending_kinds``
  membership is set on enqueue but does **not** dedup — a second call
  with the same ``kind`` still enqueues and the caller still gets its
  own future.

* :meth:`MessageBus.try_send` — dedups against ``_pending_kinds``;
  returns ``False`` when a message of the same ``kind`` is already
  in-flight. Used today only by the heartbeat scheduler, whose
  semantics are "fire at most one heartbeat per tick, silently skip
  ticks that overlap a still-running one". Job / task / trigger /
  continuation runners intentionally use ``send`` so overlapping
  schedules do not silently coalesce.

Dedup usage is already observable via ``harness_bus_dedup_total{kind}``
so operators can see which kinds actually experience dedup without
auditing call sites.
"""

import asyncio
import hashlib
import logging
import os
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from metrics import (
    harness_bus_dedup_total,
    harness_bus_pending_kinds,
    harness_bus_queue_depth,
    harness_hook_decision_dropped_total,
    harness_hook_decision_listener_dup_rejects_total,
    harness_hook_decision_listener_errors_total,
)
from utils import ConsensusEntry

BUS_MAX_QUEUE_DEPTH = int(os.environ.get("BUS_MAX_QUEUE_DEPTH", "100"))
BUS_SEND_TIMEOUT = float(os.environ.get("BUS_SEND_TIMEOUT", "30.0"))

logger = logging.getLogger(__name__)


@dataclass
class Message:
    """Single unit of work flowing through :class:`MessageBus`.

    ``kind`` is the dedup key used by :meth:`MessageBus.try_send` and the
    label on bus-level Prometheus metrics; reusing the same ``kind`` for
    two semantically distinct schedules will cause one to silently
    suppress the other when ``try_send`` is in play. ``result`` is a
    future the bus worker resolves with the dispatch outcome — callers
    that go through :meth:`MessageBus.send` await it; ``try_send``
    callers can also await it but typically don't. ``trace_context``
    carries the W3C trace headers attached on inbound A2A requests so
    downstream backend calls and webhook deliveries propagate the same
    span tree (#468).
    """

    prompt: str
    session_id: str | None = None
    kind: str = "a2a"  # "a2a", "heartbeat", "job:<name>", "task:<name>", "trigger:<endpoint>", "continuation:<name>"
    model: str | None = None
    backend_id: str | None = None
    consensus: list[ConsensusEntry] = field(default_factory=list)  # non-empty = fan-out to matching backends
    max_tokens: int | None = None  # per-dispatch token budget; backends stop when exceeded
    enqueued_at: float = 0.0
    result: asyncio.Future | None = field(default=None)
    metadata: dict[str, Any] = field(default_factory=dict)
    # W3C trace context attached to this message (#468). Carried through every
    # downstream A2A relay and webhook delivery. None for messages that
    # originate inside the harness without an inbound request — callers can
    # mint a fresh context via trace.new_context() when needed.
    trace_context: Any = None  # avoid forward-importing harness.trace


class MessageBus:
    """Async message queue with kind-level dedup.

    Wraps an ``asyncio.Queue`` (capped at ``BUS_MAX_QUEUE_DEPTH``) plus a
    ``_pending_kinds`` set so the bus worker can dedup re-fires of the
    same scheduled job/task/trigger while a prior one is still
    in-flight (#514).

    Lifecycle for one message:

    - ``send`` always enqueues, awaiting on the Message's result future;
      a full queue surfaces as ``asyncio.QueueFull`` after
      ``BUS_SEND_TIMEOUT``.
    - ``try_send`` enqueues only if no message of the same ``kind`` is
      currently pending; returns False otherwise (counted in
      ``harness_bus_dedup_total``).
    - ``receive`` blocks for the next queued message.
    - The bus worker MUST call ``release_pending(kind)`` in a
      ``finally`` after processing — otherwise a failed message
      starves all future ``try_send`` calls for that kind.

    Prometheus gauges (``harness_bus_queue_depth``,
    ``harness_bus_pending_kinds``) are updated inline on every state
    change so a stale value never persists across an error path; the
    cleanup ``except BaseException`` in ``send`` covers timeout,
    cancellation, and worker failure under a single discard branch.
    """

    def __init__(self):
        self._queue: asyncio.Queue[Message] = asyncio.Queue(maxsize=BUS_MAX_QUEUE_DEPTH)
        self._pending_kinds: set[str] = set()

    async def send(self, message: Message) -> str:
        """Enqueue *message* unconditionally and await its result.

        Always enqueues — no dedup against ``_pending_kinds`` — so
        overlapping schedules each get their own dispatch and their own
        future. Raises :class:`asyncio.QueueFull` (with the implicit
        ``TimeoutError`` chain suppressed) when the queue stays full for
        longer than ``BUS_SEND_TIMEOUT``. On any exception path
        (timeout, cancellation, worker failure) the ``_pending_kinds``
        slot for this ``kind`` is cleared so future sends are not
        starved.
        """
        loop = asyncio.get_running_loop()
        # #1182: re-mint the future if the Message is being reused and its
        # previous future is already resolved. A stale done future would
        # short-circuit the ``await message.result`` below.
        if message.result is None or message.result.done():
            message.result = loop.create_future()
        self._pending_kinds.add(message.kind)
        if harness_bus_pending_kinds is not None:
            harness_bus_pending_kinds.set(len(self._pending_kinds))
        message.enqueued_at = time.monotonic()
        try:
            try:
                await asyncio.wait_for(self._queue.put(message), timeout=BUS_SEND_TIMEOUT)
            except asyncio.TimeoutError:
                logger.error(f"Bus send timed out after {BUS_SEND_TIMEOUT}s — queue full (depth={self._queue.qsize()})")
                # `from None` suppresses the implicit TimeoutError chain so
                # tracebacks surface QueueFull cleanly. The intent (timeout
                # → queue-full is one cleanup path) is documented at L101.
                raise asyncio.QueueFull() from None
            # Update queue depth unconditionally after put() succeeds. Placing
            # this update here (before awaiting result) ensures the gauge is
            # correct regardless of whether the awaiting coroutine is cancelled
            # or raises — the message is physically in the queue from this point.
            if harness_bus_queue_depth is not None:
                harness_bus_queue_depth.set(self._queue.qsize())
            return await message.result
        except BaseException:
            # Single cleanup path covers TimeoutError → QueueFull, CancelledError
            # during put or result, and any error raised by the bus worker.
            self._pending_kinds.discard(message.kind)
            if harness_bus_pending_kinds is not None:
                harness_bus_pending_kinds.set(len(self._pending_kinds))
            raise

    def try_send(self, message: Message) -> bool:
        """Enqueue message only if no message of the same kind is already pending. Returns True if enqueued."""
        if message.kind in self._pending_kinds:
            if harness_bus_dedup_total is not None:
                harness_bus_dedup_total.labels(kind=message.kind).inc()
            if harness_bus_queue_depth is not None:
                harness_bus_queue_depth.set(self._queue.qsize())
            return False
        # #1182: re-mint the future if a reused Message's previous future
        # is already resolved.
        if message.result is None or message.result.done():
            message.result = asyncio.get_running_loop().create_future()
        self._pending_kinds.add(message.kind)
        if harness_bus_pending_kinds is not None:
            harness_bus_pending_kinds.set(len(self._pending_kinds))
        message.enqueued_at = time.monotonic()
        try:
            self._queue.put_nowait(message)
        except asyncio.QueueFull:
            self._pending_kinds.discard(message.kind)
            if harness_bus_pending_kinds is not None:
                harness_bus_pending_kinds.set(len(self._pending_kinds))
            return False
        if harness_bus_queue_depth is not None:
            harness_bus_queue_depth.set(self._queue.qsize())
        return True

    async def receive(self) -> Message:
        """Block for the next queued message and refresh the depth gauge.

        Updates ``harness_bus_queue_depth`` after the ``get()`` returns
        so the gauge reflects the post-dequeue size. The
        ``_pending_kinds`` slot is *not* released here — that's the bus
        worker's responsibility via :meth:`release_pending` once
        processing finishes, so re-fires of the same ``kind`` continue
        to dedup until the prior one has finished executing (#514).
        """
        message = await self._queue.get()
        if harness_bus_queue_depth is not None:
            harness_bus_queue_depth.set(self._queue.qsize())
        return message

    def release_pending(self, kind: str) -> None:
        """Release the dedup slot for ``kind``.

        The ``_pending_kinds`` lifetime spans enqueue through the end of
        ``process_bus`` so ``try_send`` correctly dedups a second scheduled
        fire while the first is still executing (#514). Callers (the bus
        worker) must invoke this in a ``finally`` after processing so that
        both success and error paths clear the slot — otherwise a failed
        message would starve all future ``try_send`` for that kind.
        """
        self._pending_kinds.discard(kind)
        if harness_bus_pending_kinds is not None:
            harness_bus_pending_kinds.set(len(self._pending_kinds))


# ---------------------------------------------------------------------------
# Side-channel events (#633)
# ---------------------------------------------------------------------------
# The queue-based prompt path above deliberately deduplicates by ``kind`` and
# carries a ``result`` future the caller awaits.  Observability fan-out
# (webhooks, metrics sinks) has neither requirement: the producer should not
# block, listeners should not be deduped against one another, and a missing
# listener must not back-pressure the caller.  We keep those semantics in a
# separate lightweight callback registry rather than reusing ``MessageBus``.
#
# As of #633 the only event shape defined on this channel is
# :class:`HookDecisionEvent`.  Backends (which run in a separate process from
# the harness today) cannot reach this registry directly — a follow-up gap
# will file the cross-process transport.  The in-process scaffold exists so
# harness-side call sites and unit tests have a stable API to target before
# that transport lands.


@dataclass
class HookDecisionEvent:
    """Structured record of a single PreToolUse hook decision (#633).

    Emitted whenever an claude hook evaluator finalises a decision
    (allow / warn / deny).  Mirrors the attribute set stamped onto the OTel
    span event so downstream consumers see the same shape regardless of the
    transport.  ``traceparent`` is a serialised W3C trace-context header so
    webhook receivers can correlate the event with the originating trace.
    """

    agent: str
    session_id: str
    tool: str
    decision: str  # "allow" | "warn" | "deny"
    rule_name: str
    reason: str
    source: str  # "baseline" | "extension" | ...
    traceparent: str | None = None


# Registered listener callbacks.  Each is invoked synchronously from
# :func:`publish_hook_decision`; a listener that must do async work should
# schedule it onto its own loop (e.g. ``asyncio.create_task``).  Failures in
# any one listener must not prevent the others from running and must never
# propagate out of ``publish_hook_decision``.
_hook_decision_listeners: list[Callable[[HookDecisionEvent], None]] = []

# Identity set used to reject duplicate registrations across module
# reloads (#1036). Without this guard an `importlib.reload(bus)` or a
# misbehaving test fixture that re-imports the harness can silently
# double every downstream fan-out. We key on the callable object so a
# re-registration of the exact same function is a no-op; a different
# function with the same qualname is still allowed.
_hook_decision_listener_ids: set[int] = set()

# Error / dup-reject / dropped counter surfaces. Wired at import time
# from ``harness/metrics.py`` — when METRICS_ENABLED is unset the
# imports resolve to ``None`` and the bump helpers below no-op.
listener_errors_total = harness_hook_decision_listener_errors_total
listener_dup_rejects_total = harness_hook_decision_listener_dup_rejects_total
_hook_decision_dropped_counter = harness_hook_decision_dropped_total


def subscribe_hook_decision(listener: Callable[[HookDecisionEvent], None]) -> None:
    """Register *listener* for future :class:`HookDecisionEvent` publications.

    Duplicate registrations of the same callable are rejected (#1036) to
    prevent double-dispatch after module reloads. The rejection bumps
    :data:`listener_dup_rejects_total` when wired.
    """
    key = id(listener)
    if key in _hook_decision_listener_ids:
        logger.warning(
            "subscribe_hook_decision: listener %r is already registered; "
            "ignoring duplicate (likely an unintended module reload).",
            listener,
        )
        if listener_dup_rejects_total is not None:
            try:
                listener_dup_rejects_total.inc()
            except Exception:
                pass
        return
    _hook_decision_listener_ids.add(key)
    _hook_decision_listeners.append(listener)


def unsubscribe_hook_decision(listener: Callable[[HookDecisionEvent], None]) -> None:
    """Remove a previously registered listener. No-op if not registered."""
    try:
        _hook_decision_listeners.remove(listener)
    except ValueError:
        pass
    _hook_decision_listener_ids.discard(id(listener))


# Bounded queue drained by ``_hook_decision_dispatch_loop`` (#928). The
# HTTP handler (hook_decision_event_handler) previously invoked listeners
# synchronously; a listener that grew a sync-blocking step (URL
# validation, metrics HTTP, later-added policy code) would stall the
# /internal/events/hook-decision request and push backpressure up into
# the backend's hook-posting thread. Dispatching via the queue severs
# that chain: handler enqueues + returns 202 in O(1); the dispatcher
# task invokes listeners one at a time on its own tick.
#
# maxsize is bounded so a stuck listener cannot consume unbounded memory
# across a sustained flood — at the limit publish_hook_decision drops
# the newest event and bumps a counter for operators. 1024 is 1 queued
# event per millisecond of sustained 1k/s burst, well above normal
# hook cadence.
_HOOK_DECISION_QUEUE_MAX = 1024
_hook_decision_queue: "asyncio.Queue[HookDecisionEvent] | None" = None
_hook_decision_queue_loop: "asyncio.AbstractEventLoop | None" = None
_hook_decision_dropped = 0


def _ensure_hook_decision_queue() -> "tuple[asyncio.Queue[HookDecisionEvent] | None, asyncio.AbstractEventLoop | None]":
    """Return (queue, loop) if the dispatcher has been wired, else (None, None).

    The harness wires the queue + background task in main.py lifespan
    via :func:`start_hook_decision_dispatcher`. Unit tests and callers
    that never started a dispatcher get the legacy synchronous fan-out
    so test coverage that predates #928 keeps working.
    """
    return _hook_decision_queue, _hook_decision_queue_loop


def start_hook_decision_dispatcher(loop: "asyncio.AbstractEventLoop") -> "asyncio.Task":
    """Create the bounded queue and return an asyncio task that drains it.

    Callers should schedule the returned task into the harness lifespan
    task list so it is cancelled on shutdown.
    """
    global _hook_decision_queue, _hook_decision_queue_loop
    _hook_decision_queue = asyncio.Queue(maxsize=_HOOK_DECISION_QUEUE_MAX)
    _hook_decision_queue_loop = loop

    async def _dispatch_loop() -> None:
        q = _hook_decision_queue
        assert q is not None
        # #1183: wrap the inner drain in a self-restarting supervisor. An
        # unexpected exception at the ``await q.get()`` boundary (or
        # anywhere below) would previously kill the dispatcher task for the
        # lifetime of the process — hook.decision events would then silently
        # accumulate up to _HOOK_DECISION_QUEUE_MAX and get dropped. By
        # catching non-cancellation Exception at the outer while, the loop
        # is self-healing without changing how it's scheduled in main.py.
        while True:
            try:
                while True:
                    event = await q.get()
                    for listener in list(_hook_decision_listeners):
                        try:
                            listener(event)
                        except Exception as exc:  # pragma: no cover — best-effort side channel
                            logger.warning("hook.decision listener %r raised: %r", listener, exc)
                            _bump_listener_error(listener, exc)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error(
                    "hook.decision dispatcher crashed — restarting loop: %r",
                    exc,
                    exc_info=True,
                )
                # Yield to the loop so a tight crash doesn't spin.
                await asyncio.sleep(0)

    return loop.create_task(_dispatch_loop(), name="hook_decision_dispatcher")


def publish_hook_decision(event: HookDecisionEvent) -> None:
    """Fan ``event`` out to every registered listener. Never raises.

    When :func:`start_hook_decision_dispatcher` has been called (i.e. the
    harness is running its lifespan), enqueues into the bounded queue so
    the HTTP handler returns 202 without waiting for listener fan-out.
    Otherwise falls back to synchronous invocation (legacy path, unit
    tests).
    """
    global _hook_decision_dropped
    q, q_loop = _ensure_hook_decision_queue()
    if q is not None and q_loop is not None:
        try:
            # call_soon_threadsafe-free enqueue: we're typically called
            # from the handler's own loop, so put_nowait is safe. If the
            # queue is full, drop the newest event and count it so
            # operators can alarm on sustained backpressure.
            q.put_nowait(event)
        except asyncio.QueueFull:
            _hook_decision_dropped += 1
            logger.warning(
                "hook.decision queue full (cap=%d, dropped=%d total) — "
                "dropping event agent=%r session=%r tool=%r (#928).",
                _HOOK_DECISION_QUEUE_MAX,
                _hook_decision_dropped,
                event.agent,
                event.session_id,
                event.tool,
            )
            # Export to Prometheus (#1085). Previously the drop was
            # tracked only in _hook_decision_dropped without a scrape
            # path, so the #928 safety valve had no operator alert
            # surface. Now dashboards can:
            #   rate(harness_hook_decision_dropped_total[5m]) > 0.
            if _hook_decision_dropped_counter is not None:
                try:
                    _hook_decision_dropped_counter.inc()
                except Exception:
                    pass
        return
    # Legacy synchronous fan-out when no dispatcher is running.
    for listener in list(_hook_decision_listeners):
        try:
            listener(event)
        except Exception as exc:  # pragma: no cover — best-effort side channel
            logger.warning("hook.decision listener %r raised: %r", listener, exc)
            _bump_listener_error(listener, exc)


def _emit_hook_decision_event_stream(event: HookDecisionEvent) -> None:
    """Re-emit a HookDecisionEvent onto the SSE event stream (#1110).

    session_id is hashed to a SHA-256 prefix so dashboards can group by
    session without ever seeing the HMAC-bound raw id. agent field on the
    HookDecisionEvent is the backend name (claude/openai/gemini); the
    envelope's agent_id is the named Witwave agent (iris/nova/…) taken from
    AGENT_NAME.
    """
    try:
        from events import get_event_stream  # scoped import — avoid import cycle
    except Exception:  # pragma: no cover — harness context required
        return
    try:
        # Drop events with out-of-contract agent/decision values
        # (#1149) rather than silently substituting "claude"/"allow".
        # Coercion misreported dashboards: a `warn`-mode test rule
        # landing on `openai` was rewritten as a claude `allow`
        # envelope, completely erasing the operator's real policy
        # signal.  Dropping the event is strictly safer — the
        # backend's own OTel span event still captures the decision.
        if event.agent not in ("claude", "openai", "codex", "gemini"):
            logger.warning(
                "hook.decision SSE drop: unknown agent %r (expected one of claude/openai/codex/gemini) (#1149)",
                event.agent,
            )
            return
        if event.decision not in ("allow", "deny", "warn"):
            logger.warning(
                "hook.decision SSE drop: unknown decision %r (expected one of allow/deny/warn) (#1149)",
                event.decision,
            )
            return
        sid_hash = hashlib.sha256((event.session_id or "").encode("utf-8")).hexdigest()[:12]
        payload: dict = {
            "backend": event.agent,
            "session_id_hash": sid_hash or "0" * 12,
            "tool": event.tool or "",
            "decision": event.decision,
        }
        if event.rule_name:
            payload["rule_id"] = event.rule_name
        if event.reason:
            payload["reason"] = event.reason
        agent_name = os.environ.get("AGENT_NAME", "witwave")
        get_event_stream().publish("hook.decision", payload, agent_id=agent_name)
    except Exception:  # pragma: no cover — fan-out is best-effort
        logger.debug("hook.decision SSE publish failed", exc_info=True)


def _bump_listener_error(listener: Callable[..., object], exc: BaseException) -> None:
    """Increment the optional Prometheus error counter (#1036).

    Surfaced as ``harness_hook_decision_listener_errors_total`` by the
    harness metrics module when wired. Best-effort: a mis-wired counter
    must never break fan-out.
    """
    if listener_errors_total is None:
        return
    try:
        name = getattr(listener, "__qualname__", None) or getattr(listener, "__name__", "<unknown>")
        listener_errors_total.labels(
            listener=name,
            error=type(exc).__name__,
        ).inc()
    except Exception:
        pass


def get_hook_decision_dropped_count() -> int:
    """Return the cumulative count of dropped hook.decision events (#928)."""
    return _hook_decision_dropped
