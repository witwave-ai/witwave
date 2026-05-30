import asyncio
import json
import logging
import os
import time
import uuid
from collections import OrderedDict
from datetime import datetime, timezone
from typing import TYPE_CHECKING

import yaml

if TYPE_CHECKING:
    from continuations import ContinuationRunner
    from webhooks import WebhookRunner
from a2a.server.agent_execution import AgentExecutor as A2AAgentExecutor
from a2a.server.agent_execution import RequestContext
from a2a.server.events import EventQueue
from a2a.utils import new_agent_text_message
from backends.a2a import A2ABackend
from backends.config import (
    BACKEND_CONFIG_PATH,
    BackendConfig,
    RoutingEntry,
    load_backends_config,
    load_routing_config,
)
from bus import Message, MessageBus
from events import get_event_stream
from log_utils import _append_log
from metrics import (
    harness_a2a_last_request_timestamp_seconds,
    harness_a2a_prompt_oversize_total,
    harness_a2a_request_duration_seconds,
    harness_a2a_requests_total,
    harness_a2a_traces_received_total,
    harness_active_sessions,
    harness_backends_config_stale,
    harness_backends_reload_errors_total,
    harness_background_tasks,
    harness_background_tasks_shed_total,
    harness_background_tasks_timeout_total,
    harness_concurrent_queries,
    harness_consensus_backend_errors_total,
    harness_consensus_runs_total,
    harness_empty_responses_total,
    harness_log_bytes_total,
    harness_log_entries_total,
    harness_log_write_errors_total,
    harness_lru_cache_utilization_percent,
    harness_model_requests_total,
    harness_prompt_length_bytes,
    harness_response_length_bytes,
    harness_running_tasks,
    harness_session_age_seconds,
    harness_session_evictions_total,
    harness_session_idle_seconds,
    harness_session_starts_total,
    harness_task_cancellations_total,
    harness_task_duration_seconds,
    harness_task_error_duration_seconds,
    harness_task_last_error_timestamp_seconds,
    harness_task_last_success_timestamp_seconds,
    harness_task_outer_timeout_cancel_total,
    harness_task_restarts_total,
    harness_task_timeout_headroom_seconds,
    harness_tasks_total,
)
from tracing import (
    TraceContext,
    context_from_inbound,
    extract_otel_context,
    new_context,
    set_span_error,
    start_span,
)
from utils import ConsensusEntry

logger = logging.getLogger(__name__)

AGENT_NAME = os.environ.get("AGENT_NAME", "witwave")
CONVERSATION_LOG = os.environ.get("CONVERSATION_LOG", "/home/agent/logs/conversation.jsonl")

MAX_SESSIONS = int(os.environ.get("MAX_SESSIONS", "10000"))
TASK_TIMEOUT_SECONDS = int(os.environ.get("TASK_TIMEOUT_SECONDS", "300"))
# A2A inbound prompt size cap (#783). Mirrors the trigger-side
# MAX_TRIGGER_BODY_BYTES (#529) so the harness can't buffer an
# arbitrarily large prompt, log it, and forward it to a backend.
# Default 1 MiB; set A2A_MAX_PROMPT_BYTES=0 to disable.
A2A_MAX_PROMPT_BYTES = int(os.environ.get("A2A_MAX_PROMPT_BYTES", str(1_048_576)))
# Maximum number of bytes of prompt text included in INFO-level log messages.
# Set to 0 to suppress prompt text from logs entirely; set higher for more context.
LOG_PROMPT_MAX_BYTES = int(os.environ.get("LOG_PROMPT_MAX_BYTES", "200"))
# Hard ceiling on how long an on_prompt_completed fan-out (continuation + webhook
# extraction) is allowed to run before the tracking task is cancelled. Chosen to
# be generous by default — 2× TASK_TIMEOUT_SECONDS + a 60s margin — so legitimate
# LLM extraction completes while still bounding stuck downstreams. Override via
# ON_PROMPT_COMPLETED_TIMEOUT (#549).
ON_PROMPT_COMPLETED_TIMEOUT = float(os.environ.get("ON_PROMPT_COMPLETED_TIMEOUT", str(TASK_TIMEOUT_SECONDS * 2 + 60)))
# Maximum number of background tasks tracked by AgentExecutor at any one time.
# When the cap is hit new tasks are shed and counted in
# harness_background_tasks_shed_total rather than growing the set without bound (#549).
BACKGROUND_TASKS_MAX = int(os.environ.get("BACKGROUND_TASKS_MAX", "1000"))

# Rate-limit window (seconds) for shed-event WARN logs (#1644). The first shed
# in each window is logged immediately; subsequent sheds within the window are
# counted and emitted as a summary WARN when the window flushes. Per-source so
# a noisy critical-class caller can't suppress visibility into a different
# starving source.
_BACKGROUND_SHED_LOG_WINDOW_SEC = float(os.environ.get("BACKGROUND_SHED_LOG_WINDOW_SEC", "10"))
# Module-level state: source -> {"window_start": float, "suppressed": int}.
# Plain dict (no lock) — track_background runs on the asyncio thread.
_background_shed_log_state: dict[str, dict[str, float]] = {}


async def log_entry(
    role: str,
    text: str,
    session_id: str,
    model: str | None = None,
    backend: str | None = None,
    trace_context: TraceContext | None = None,
) -> None:
    """Append one JSONL entry to the conversation log.

    Builds a record with timestamp, agent, ``session_id``, ``role``,
    optional ``model`` and ``backend``, and the raw ``text``; when
    ``trace_context`` is supplied the ``trace_id`` and ``parent_id``
    are attached for downstream log-correlation (#468). The write is
    dispatched via ``asyncio.to_thread`` so the event loop stays
    responsive. Errors are caught and counted in
    ``harness_log_write_errors_total`` so a transient disk failure
    cannot take down the calling request path.
    """
    try:
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "agent": AGENT_NAME,
            "session_id": session_id,
            "role": role,
            "model": model,
            "backend": backend,
            "text": text,
        }
        # Attach trace context to every conversation log line when present so
        # external log-correlation tools can join the JSONL with downstream
        # backend traces and webhooks (#468). Absent by default to keep old
        # logs backward-compatible.
        if trace_context is not None:
            entry["trace_id"] = trace_context.trace_id
            entry["span_id"] = trace_context.parent_id
        _line = json.dumps(entry)
        await asyncio.to_thread(_append_log, CONVERSATION_LOG, _line)
        if harness_log_entries_total is not None:
            harness_log_entries_total.labels(logger="conversation").inc()
        if harness_log_bytes_total is not None:
            harness_log_bytes_total.labels(logger="conversation").inc(len(_line.encode()))
    except Exception as e:
        if harness_log_write_errors_total is not None:
            harness_log_write_errors_total.inc()
        logger.error(f"log_entry error: {e}")


def _build_backend(config: BackendConfig):
    return A2ABackend(config=config)


def load_backends():
    """Read backend.yaml once and return (backends dict, default_id, routing config)."""
    if not os.path.exists(BACKEND_CONFIG_PATH):
        raise FileNotFoundError(f"backend.yaml not found at {BACKEND_CONFIG_PATH}")
    with open(BACKEND_CONFIG_PATH) as f:
        raw = yaml.safe_load(f)
    configs = load_backends_config(raw)
    backends = {c.id: _build_backend(c) for c in configs}
    routing = load_routing_config(raw)
    # Validate all routing entries reference known backend IDs.
    _routing_fields = {
        "default": routing.default,
        "a2a": routing.a2a,
        "heartbeat": routing.heartbeat,
        "job": routing.job,
        "task": routing.task,
        "trigger": routing.trigger,
        "continuation": routing.continuation,
    }
    for _field, _entry in _routing_fields.items():
        if _entry is not None and _entry.agent not in backends:
            raise ValueError(
                f"routing.{_field} agent '{_entry.agent}' does not match any configured backend id. "
                f"Known ids: {list(backends)}"
            )
    if routing.default:
        default_id = routing.default.agent
    else:
        default_id = configs[0].id
        logger.info(f"No routing.default specified — using first backend: '{default_id}'")
    logger.info(f"Default backend: '{default_id}'")
    return backends, default_id, routing


def _track_session(sessions: OrderedDict[str, float], session_id: str) -> None:
    if session_id in sessions:
        sessions.move_to_end(session_id)
        sessions[session_id] = time.monotonic()
    else:
        if len(sessions) >= MAX_SESSIONS:
            _evicted_id, last_used_at = sessions.popitem(last=False)
            if harness_session_evictions_total is not None:
                harness_session_evictions_total.inc()
            if harness_session_age_seconds is not None:
                harness_session_age_seconds.observe(time.monotonic() - last_used_at)
            # #1308: log the eviction with session_id so operators can
            # correlate backend-side inventory drift. Full event type
            # creation requires schema updates (docs/events/) and is
            # tracked separately; this audit log is the minimum signal.
            logger.info(
                "session.evicted: session_id=%s last_used_age_s=%.1f lru_cap=%d",
                _evicted_id,
                time.monotonic() - last_used_at,
                MAX_SESSIONS,
            )
        sessions[session_id] = time.monotonic()
    if harness_active_sessions is not None:
        harness_active_sessions.set(len(sessions))
    if harness_lru_cache_utilization_percent is not None:
        harness_lru_cache_utilization_percent.set(len(sessions) / MAX_SESSIONS * 100)


async def run(
    prompt: str,
    session_id: str,
    sessions: OrderedDict[str, float],
    backends: dict,
    default_backend_id: str,
    backend_id: str | None = None,
    model: str | None = None,
    max_tokens: int | None = None,
    trace_context: TraceContext | None = None,
    caller_id: str | None = None,
) -> str:
    """Dispatch one prompt to the routed backend and return its text response.

    Thin wrapper around :func:`_run_inner` that maintains the
    ``harness_concurrent_queries`` gauge across the call (incremented
    on entry, decremented in ``finally`` regardless of outcome). All
    routing and session-LRU bookkeeping happens inside ``_run_inner``
    — callers do not need to update ``sessions`` themselves.
    """
    if harness_concurrent_queries is not None:
        harness_concurrent_queries.inc()
    try:
        return await _run_inner(
            prompt,
            session_id,
            sessions,
            backends,
            default_backend_id,
            backend_id,
            model,
            max_tokens,
            trace_context=trace_context,
            caller_id=caller_id,
        )
    finally:
        if harness_concurrent_queries is not None:
            harness_concurrent_queries.dec()


async def _run_inner(
    prompt: str,
    session_id: str,
    sessions: OrderedDict[str, float],
    backends: dict,
    default_backend_id: str,
    backend_id: str | None = None,
    model: str | None = None,
    max_tokens: int | None = None,
    trace_context: TraceContext | None = None,
    caller_id: str | None = None,
    consensus_mode: bool = False,
) -> str:
    resolved_id = backend_id or default_backend_id
    # #1575: surface a clearer diagnostic when routing yields no backend id
    # at all (empty routing + unset default). The generic "No backend
    # configured with id 'None'" message misled operators during graceful
    # startup into thinking a named backend was misconfigured.
    if resolved_id is None:
        raise ValueError(
            "No backend id resolved — routing yielded no entry and no "
            "default backend is configured (backend.yaml empty or still "
            "loading)."
        )
    backend = backends.get(resolved_id)
    if backend is None:
        raise ValueError(f"No backend configured with id '{resolved_id}'")

    if harness_model_requests_total is not None:
        harness_model_requests_total.labels(model=model or "default").inc()

    is_new = session_id not in sessions
    if not is_new and harness_session_idle_seconds is not None:
        harness_session_idle_seconds.observe(time.monotonic() - sessions[session_id])
    # #1188: under consensus, a single logical prompt fans out to N backends
    # and each leg calls _run_inner with the same session_id — incrementing
    # session_starts_total once per leg inflates the metric by the consensus
    # fan-out factor. The caller (run_consensus) accounts for the logical
    # session start separately, so skip the per-leg increment here.
    if not consensus_mode and harness_session_starts_total is not None:
        harness_session_starts_total.labels(type="new" if is_new else "resumed").inc()
    _track_session(sessions, session_id)

    _prompt_preview = (
        prompt[:LOG_PROMPT_MAX_BYTES] + ("[truncated]" if len(prompt) > LOG_PROMPT_MAX_BYTES else "")
        if LOG_PROMPT_MAX_BYTES > 0
        else "[redacted]"
    )
    _trace_tag = f" trace_id={trace_context.trace_id}" if trace_context is not None else ""
    logger.info(
        f"Session {session_id} ({'new' if is_new else 'existing'}) backend={resolved_id}{_trace_tag} — prompt: {_prompt_preview!r}"  # noqa: E501
    )
    await log_entry("user", prompt, session_id, model=model, backend=resolved_id, trace_context=trace_context)

    if harness_prompt_length_bytes is not None:
        harness_prompt_length_bytes.observe(len(prompt.encode()))

    _start = time.monotonic()
    try:
        # caller_id propagates here only when the backend's run_query
        # signature accepts it (A2ABackend on the relay path). Non-relay
        # backends (in-process claude/openai/gemini) ignore caller_id;
        # they derive session binding from their own /mcp bearer.
        _run_kwargs: dict[str, object] = dict(
            model=model,
            max_tokens=max_tokens,
            trace_context=trace_context,
        )
        if caller_id is not None:
            _run_kwargs["caller_id"] = caller_id
        collected = await asyncio.wait_for(
            backend.run_query(prompt, session_id, is_new, **_run_kwargs),
            timeout=TASK_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError:
        # #1457: elevate the existing timeout log to structured WARN
        # with the identifying fields operators need to audit for
        # potential double-billing. When TASK_TIMEOUT_SECONDS fires
        # mid-LLM-call, the backend may continue running to
        # completion without a client to return to — tokens still
        # billed, no response delivered. The bill-reconciliation
        # audit needs session_id + backend + prompt_length +
        # trace_id; this log line is where that audit starts.
        _elapsed = time.monotonic() - _start
        _trace_id = trace_context.trace_id if trace_context is not None else ""
        logger.warning(
            "session_timeout_cancel session_id=%s backend=%s "
            "elapsed_seconds=%.2f prompt_bytes=%d trace_id=%s "
            "likely_double_bill=server-side LLM call may have continued "
            "without client to return to; reconcile against billing",
            session_id,
            resolved_id,
            _elapsed,
            len(prompt.encode()),
            _trace_id,
        )
        if harness_tasks_total is not None:
            harness_tasks_total.labels(status="timeout").inc()
        if harness_task_outer_timeout_cancel_total is not None:
            try:
                harness_task_outer_timeout_cancel_total.labels(backend=resolved_id).inc()
            except Exception:
                pass
        if harness_task_error_duration_seconds is not None:
            harness_task_error_duration_seconds.observe(_elapsed)
        if harness_task_last_error_timestamp_seconds is not None:
            harness_task_last_error_timestamp_seconds.set(time.time())
        raise
    except Exception:
        if harness_tasks_total is not None:
            harness_tasks_total.labels(status="error").inc()
        if harness_task_error_duration_seconds is not None:
            harness_task_error_duration_seconds.observe(time.monotonic() - _start)
        if harness_task_last_error_timestamp_seconds is not None:
            harness_task_last_error_timestamp_seconds.set(time.time())
        raise

    if harness_tasks_total is not None:
        harness_tasks_total.labels(status="success").inc()
    if harness_task_last_success_timestamp_seconds is not None:
        harness_task_last_success_timestamp_seconds.set(time.time())
    if harness_task_duration_seconds is not None:
        harness_task_duration_seconds.observe(time.monotonic() - _start)
    if harness_task_timeout_headroom_seconds is not None:
        harness_task_timeout_headroom_seconds.observe(TASK_TIMEOUT_SECONDS - (time.monotonic() - _start))

    response = "\n\n".join(collected) if collected else ""
    if not response:
        if harness_empty_responses_total is not None:
            harness_empty_responses_total.inc()
    elif harness_response_length_bytes is not None:
        harness_response_length_bytes.observe(len(response.encode()))
    return response


_BINARY_YES = frozenset({"yes", "true", "agree", "correct", "approved", "confirmed", "positive", "1"})
_BINARY_NO = frozenset({"no", "false", "disagree", "incorrect", "rejected", "denied", "negative", "0"})


def _classify_binary(text: str) -> str | None:
    """Return 'yes', 'no', or None if the response cannot be classified as binary."""
    normalised = text.strip().lower().rstrip(".")
    if normalised in _BINARY_YES:
        return "yes"
    if normalised in _BINARY_NO:
        return "no"
    return None


async def run_consensus(
    prompt: str,
    session_id: str,
    sessions: OrderedDict[str, float],
    backends: dict,
    default_backend_id: str,
    consensus_entries: list[ConsensusEntry],
    synthesis_backend_id: str | None = None,
    synthesis_model: str | None = None,
    max_tokens: int | None = None,
    trace_context: TraceContext | None = None,
) -> str:
    """Fan out *prompt* to matching backends concurrently and aggregate.

    *consensus_entries* is a list of ConsensusEntry (backend glob pattern + optional model).
    Each entry's backend pattern is matched against configured backend IDs via fnmatch.
    Model resolution per backend: entry model → BackendConfig.model → None.
    Binary responses (yes/no variants): majority vote; default backend wins ties.
    Freeform responses: a synthesis pass is sent to the default backend.
    """
    import fnmatch

    # Resolve entries to (call_key, backend_id, model) tuples, expanding glob patterns.
    # The same backend may appear multiple times with different models.
    # call_key = "bid:model" when model is set, else "bid" — used as the response label.
    resolved: list[tuple[str, str, str | None]] = []
    seen: set[tuple[str, str | None]] = set()
    for entry in consensus_entries:
        matched = [bid for bid in backends if fnmatch.fnmatch(bid, entry.backend)]
        if not matched:
            logger.warning("Consensus: pattern %r matched no backends (known: %s)", entry.backend, list(backends))
        for bid in matched:
            backend_model = entry.model or (backends[bid]._config.model if hasattr(backends[bid], "_config") else None)
            pair = (bid, backend_model)
            if pair not in seen:
                seen.add(pair)
                call_key = f"{bid}:{backend_model}" if backend_model else bid
                resolved.append((call_key, bid, backend_model))
    if not resolved:
        logger.warning("Consensus: no backends matched — falling back to default.")
        resolved = [(default_backend_id, default_backend_id, None)]

    async def _call(call_key: str, bid: str, model: str | None) -> tuple[str, str | Exception]:
        try:
            result = await _run_inner(
                prompt,
                session_id,
                sessions,
                backends,
                default_backend_id,
                backend_id=bid,
                model=model,
                max_tokens=max_tokens,
                trace_context=trace_context,
                consensus_mode=True,  # #1188
            )
            return call_key, result
        except Exception as exc:
            return call_key, exc

    raw_results = await asyncio.gather(*[_call(k, bid, m) for k, bid, m in resolved])

    def _classify_consensus_error(exc: BaseException) -> str:
        """Bounded classification for the consensus error counter's reason label.

        Keep the label cardinality small so the counter is safe to scrape —
        raw exception class names would let a misbehaving backend blow the
        cardinality budget. Only four buckets: timeout (deadline hit),
        connection (network / 5xx bubbled up as ConnectionError), backend_error
        (backend returned a structured error via RuntimeError from the A2A
        relay), other (anything else — unexpected; investigate if it shows up).
        """
        if isinstance(exc, asyncio.TimeoutError):
            return "timeout"
        if isinstance(exc, ConnectionError):
            return "connection"
        if isinstance(exc, RuntimeError):
            return "backend_error"
        return "other"

    responses: dict[str, str] = {}
    for call_key, outcome in raw_results:
        if isinstance(outcome, Exception):
            logger.error(f"Consensus backend {call_key!r} failed: {outcome!r}")
            if harness_consensus_backend_errors_total is not None:
                harness_consensus_backend_errors_total.labels(reason=_classify_consensus_error(outcome)).inc()
        else:
            responses[call_key] = outcome

    if not responses:
        raise RuntimeError("Consensus: all backends failed — no responses to aggregate.")

    # Attempt binary classification.
    classifications = {bid: _classify_binary(text) for bid, text in responses.items()}
    all_binary = all(v is not None for v in classifications.values())

    if all_binary:
        # Majority vote.
        yes_count = sum(1 for v in classifications.values() if v == "yes")
        no_count = sum(1 for v in classifications.values() if v == "no")
        if yes_count != no_count:
            winner = "yes" if yes_count > no_count else "no"
        else:
            # Tie — default backend wins (use first matching call_key).
            # If the default backend isn't among the consensus participants (or its
            # call_key isn't present in classifications), fall back deterministically
            # to the lexicographically first call_key rather than silently biasing
            # toward "yes" (#496).
            default_key = next((k for k, bid, _ in resolved if bid == default_backend_id), None)
            if default_key is not None and default_key in classifications:
                winner = classifications[default_key]
            else:
                fallback_key = min(classifications.keys())
                logger.warning(
                    "Consensus tie-break: default backend %r not present in "
                    "classifications (keys=%s); falling back deterministically to %r.",
                    default_backend_id,
                    sorted(classifications.keys()),
                    fallback_key,
                )
                winner = classifications[fallback_key]
        logger.info(f"Consensus (binary): yes={yes_count} no={no_count} → {winner}")
        if harness_consensus_runs_total is not None:
            harness_consensus_runs_total.labels(mode="binary", status="success").inc()
        return winner

    # Freeform: synthesis pass.
    parts = "\n\n".join(f"[{bid}]: {text}" for bid, text in responses.items())
    synthesis_prompt = (
        "The following responses were collected from multiple AI agents for the same prompt. "
        "Synthesise them into a single coherent, balanced answer, preserving the most important "
        "insights and noting any significant disagreements.\n\n"
        f"Original prompt: {prompt}\n\n"
        f"Agent responses:\n{parts}"
    )
    try:
        synthesised = await _run_inner(
            synthesis_prompt,
            session_id,
            sessions,
            backends,
            default_backend_id,
            backend_id=synthesis_backend_id or default_backend_id,
            model=synthesis_model,
            max_tokens=max_tokens,
            trace_context=trace_context,
            consensus_mode=True,  # #1188 — synthesis is still part of the consensus flow
        )
    except Exception as exc:
        logger.error(f"Consensus synthesis pass failed: {exc!r} — returning concatenated responses.")
        if harness_consensus_runs_total is not None:
            harness_consensus_runs_total.labels(mode="freeform", status="error").inc()
        return parts
    logger.info(f"Consensus (freeform): synthesised from {len(responses)} backend(s).")
    if harness_consensus_runs_total is not None:
        harness_consensus_runs_total.labels(mode="freeform", status="success").inc()
    return synthesised


async def _guarded(
    coro_fn,
    *args,
    restart_delay: float = 5.0,
    max_restarts: int = 5,
    on_not_ready=None,
    on_recovered=None,
) -> None:
    """Restart a coroutine function in a restart loop, catching unexpected exceptions.

    Replaces the former _guarded_watcher (which was a diverged subset of this
    implementation).  When on_not_ready / on_recovered callbacks are provided the
    caller can react to consecutive-crash and recovery events — e.g. to update a
    readiness flag (#363).

    The consecutive restart counter resets whenever a run lasts at least
    restart_delay seconds, so transient failures spread over time do not
    accumulate toward the threshold.
    """
    consecutive_restarts = 0
    while True:
        _attempt_start = time.monotonic()
        # #1311: if we were in a restart streak, schedule an on_recovered
        # timer that fires after restart_delay of continuous running —
        # otherwise on_recovered only fires on the NEXT crash, which may
        # never come for a task that heals.
        _recovery_watchdog: asyncio.Task | None = None
        if consecutive_restarts > 0 and on_recovered is not None:

            async def _recovery_watch(delay: float, cb) -> None:
                try:
                    await asyncio.sleep(delay)
                    cb()
                except asyncio.CancelledError:
                    pass

            # #1402: align the watchdog threshold with the streak reset
            # threshold (#1367). Previously fired at 1× restart_delay
            # while reset required 3×, so dashboards reported "recovered"
            # while consecutive_restarts kept climbing — contradictory
            # signals to alert rules.
            _recovery_watchdog = asyncio.ensure_future(_recovery_watch(restart_delay * 3, on_recovered))
        try:
            try:
                await coro_fn(*args)
                return  # clean exit — do not restart
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                # #1367: require 3× restart_delay of stable running before
                # resetting the streak. Previous rule (reset on any 5s-stable
                # run) let a task crashing every 6s never accumulate toward
                # WORKER_MAX_RESTARTS — infinite-restart CPU burn without
                # readiness flipping.
                if time.monotonic() - _attempt_start >= restart_delay * 3:
                    consecutive_restarts = 0
                consecutive_restarts += 1
                logger.error(
                    f"Task {coro_fn.__name__!r} crashed: {exc!r} — "
                    f"restarting in {restart_delay}s (consecutive restart #{consecutive_restarts})"
                )
                if harness_task_restarts_total is not None:
                    harness_task_restarts_total.labels(task=coro_fn.__name__).inc()
                if on_not_ready is not None and consecutive_restarts >= max_restarts:
                    on_not_ready()
                await asyncio.sleep(restart_delay)
        finally:
            # #1345: cancel the watchdog on EVERY exit path — clean return,
            # CancelledError, or exception. The previous shape only
            # cancelled in the except branches, leaking the watchdog when
            # coro_fn returns cleanly (time-bomb: on_recovered would fire
            # later for a task that won't restart).
            if _recovery_watchdog is not None and not _recovery_watchdog.done():
                _recovery_watchdog.cancel()


class AgentExecutor(A2AAgentExecutor):
    """Harness implementation of the A2A SDK's executor interface.

    Owns the per-process session LRU, the map of running A2A tasks (keyed
    by ``task_id`` for cancellation lookup), the loaded backend registry,
    and the optional continuation / webhook / bus runners. Loads
    ``backend.yaml`` eagerly in ``__init__`` and degrades to empty routing
    if the file is missing or malformed at startup so the harness can come
    up; the ``backends_watcher`` task then picks up the next valid config.
    """

    def __init__(self):
        self._sessions: OrderedDict[str, float] = OrderedDict()
        self._running_tasks: dict[str, asyncio.Task] = {}
        # #1328: degrade gracefully when backend.yaml is temporarily
        # invalid at startup. The watcher task (`backends_watcher`) already
        # handles hot-reload failures, so starting with empty routing lets
        # the harness come up and pick up the next valid config.
        try:
            self._backends, self._default_backend_id, self._routing = load_backends()
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "executor: backend.yaml invalid at startup (%r); "
                "starting with empty routing. Watcher will pick up next "
                "valid config.",
                exc,
            )
            # #1401: `_routing` must be a RoutingConfig dataclass (not {})
            # so `_routing_entry_for_kind` attribute access (.a2a /
            # .heartbeat / …) doesn't AttributeError on the first request
            # before the watcher reloads a valid config. The #1328 fallback
            # used {} and defeated its own graceful-start guarantee.
            from backends.config import RoutingConfig

            self._backends = {}
            self._default_backend_id = None
            self._routing = RoutingConfig()
        self._mcp_watcher_tasks: list[asyncio.Task] = []
        self._background_tasks: set[asyncio.Task] = set()
        self._continuation_runner = None
        self._webhook_runner = None
        self._bus = None
        # Tracks how many consecutive backend.yaml reload attempts have
        # failed. /health/ready flips to degraded once this crosses
        # BACKEND_RELOAD_FAILURE_THRESHOLD so operators see the issue
        # beyond a log line and a Prometheus counter (#702).
        self._backends_reload_consecutive_failures: int = 0

    @property
    def backends_reload_consecutive_failures(self) -> int:
        """Number of consecutive failed ``backend.yaml`` hot-reload attempts.

        Read by the readiness probe (#702): once this crosses
        ``BACKEND_RELOAD_FAILURE_THRESHOLD`` the harness flips to degraded
        so operators see the issue beyond a log line and a counter.
        """
        return self._backends_reload_consecutive_failures

    # Public read-only accessors for the two most-accessed private attributes
    # across executor-boundary call sites (narrow slice of #572). These are
    # additive: call sites still read ``_backends`` / ``_default_backend_id``
    # directly today; new code should prefer the public names so the underlying
    # storage can evolve without a big-bang rename. The broader API-extraction
    # refactor (all accessors, call-site migration, TriggerRunner wrapping,
    # A2ABackend._config exposure) remains deferred.
    @property
    def backends(self) -> dict:
        """Mapping of backend_id → A2ABackend. Reflects live reloads."""
        return self._backends

    @property
    def default_backend_id(self) -> str:
        """Currently configured default backend id. Reflects live reloads."""
        return self._default_backend_id

    def track_background(
        self,
        coro,
        *,
        source: str = "unknown",
        timeout: float | None = None,
        name: str | None = None,
        session_id: str | None = None,
        caller_identity: str | None = None,
    ) -> asyncio.Task | None:
        """Schedule ``coro`` as a tracked background task with a bounded lifetime.

        The task is added to ``_background_tasks`` and discarded via a done-callback
        when it finishes, so the set stays bounded even if the coroutine raises.
        A hard ceiling on in-flight tasks (``BACKGROUND_TASKS_MAX``) prevents the
        set from growing without bound when downstreams hang — excess tasks are
        shed and the ``harness_background_tasks_shed_total`` counter is incremented.
        A per-task timeout (``ON_PROMPT_COMPLETED_TIMEOUT`` by default) prevents a
        single stuck coroutine from pinning memory forever (#549).

        Returns the scheduled task, or ``None`` if the task was shed.

        Shed policy is source-priority aware (#706): sources classed as
        "critical" (webhook retries, continuation fan-outs) may exceed
        the standard cap up to BACKGROUND_TASKS_CRITICAL_MAX so a
        burst of A2A traffic cannot silently drop webhook deliveries.
        Non-critical sources are shed at the lower cap. Every shed
        decision is logged with both the shed source and the currently
        occupying sources so operators can see which callers are
        starving the bucket.
        """
        # Sources whose work represents durable at-most-once delivery
        # semantics — losing them drops data that can't be recovered
        # from a retry/reconcile loop. The default covers webhooks
        # (incl. hook.decision) and continuations; other sources can be
        # promoted via BACKGROUND_TASK_CRITICAL_SOURCES (comma-separated).
        _critical_env = os.environ.get(
            "BACKGROUND_TASK_CRITICAL_SOURCES",
            "webhook,webhook_retry,hook_decision,continuation",
        )
        _critical_sources = {s.strip() for s in _critical_env.split(",") if s.strip()}
        _critical_cap = int(
            os.environ.get(
                "BACKGROUND_TASKS_CRITICAL_MAX",
                str(BACKGROUND_TASKS_MAX * 2),
            )
        )
        is_critical = source in _critical_sources
        effective_cap = _critical_cap if is_critical else BACKGROUND_TASKS_MAX
        if len(self._background_tasks) >= effective_cap:
            # Emit a distribution of currently-occupying sources so the
            # operator can see which caller is starving the bucket.
            _occupants: dict[str, int] = {}
            for t in self._background_tasks:
                tname = (t.get_name() or "").removeprefix("bg-")
                _occupants[tname] = _occupants.get(tname, 0) + 1
            logger.warning(
                "track_background: shedding %r task (in-flight=%d, cap=%d, critical=%s, occupants=%s)",
                source,
                len(self._background_tasks),
                effective_cap,
                is_critical,
                _occupants,
            )
            # Per-shed WARN with session_id + caller identity so operators can
            # see real-time drops without scraping metrics (#1644). Rate-limited
            # per-source on a configurable window: first shed in the window is
            # logged immediately, subsequent sheds are counted and emitted as a
            # summary WARN when the window flushes. Avoids log spam under
            # sustained drops while preserving "first sign of trouble" signal.
            _now = time.monotonic()
            _state = _background_shed_log_state.get(source)
            if _state is None or (_now - _state["window_start"]) >= _BACKGROUND_SHED_LOG_WINDOW_SEC:
                # Flush prior window's suppressed count (if any) before opening
                # a new window. This emits a summary on the *next* shed; for a
                # cleaner exit a periodic flusher could be wired in, but per
                # #1644 the requirement is real-time visibility on drops.
                if _state is not None and _state["suppressed"] > 0:
                    logger.warning(
                        "background task shed: source=%s suppressed=%d in last %.1fs window",
                        source,
                        int(_state["suppressed"]),
                        _BACKGROUND_SHED_LOG_WINDOW_SEC,
                        extra={"shed": True},
                    )
                logger.warning(
                    "background task shed: source=%s session=%s caller=%s",
                    source,
                    session_id,
                    caller_identity,
                    extra={"shed": True},
                )
                _background_shed_log_state[source] = {
                    "window_start": _now,
                    "suppressed": 0,
                }
            else:
                _state["suppressed"] = _state.get("suppressed", 0) + 1
            if harness_background_tasks_shed_total is not None:
                harness_background_tasks_shed_total.labels(source=source).inc()
            # Close the coroutine so we don't leak a 'coroutine was never awaited'
            # warning and any resources it holds are released immediately.
            # #1670: surface close failures via WARN so operators can see them
            # instead of silently swallowing the exception.
            try:
                coro.close()
            except Exception as _close_err:
                logger.warning(
                    "background-task close after shed failed: source=%s err=%r",
                    source,
                    _close_err,
                )
            return None

        _effective_timeout = timeout if timeout is not None else ON_PROMPT_COMPLETED_TIMEOUT

        async def _bounded() -> None:
            try:
                await asyncio.wait_for(coro, timeout=_effective_timeout)
            except asyncio.TimeoutError:
                logger.error(
                    "track_background: %r task exceeded timeout=%.1fs and was cancelled",
                    source,
                    _effective_timeout,
                )
                if harness_background_tasks_timeout_total is not None:
                    harness_background_tasks_timeout_total.labels(source=source).inc()
                # Re-raise so _on_done sees t.exception() and any awaiter
                # of the returned task observes the timeout. Without this,
                # a timed-out task looks indistinguishable from a clean
                # completion to any consumer of the task object.
                raise

        task = asyncio.create_task(_bounded(), name=name or f"bg-{source}")
        self._background_tasks.add(task)
        if harness_background_tasks is not None:
            harness_background_tasks.set(len(self._background_tasks))

        def _on_done(t: asyncio.Task, _tasks=self._background_tasks) -> None:
            _tasks.discard(t)
            if harness_background_tasks is not None:
                harness_background_tasks.set(len(_tasks))
            if not t.cancelled():
                exc = t.exception()
                if exc is not None:
                    logger.error(f"track_background({source!r}) error: {exc!r}")

        task.add_done_callback(_on_done)
        return task

    def set_continuation_runner(self, runner: "ContinuationRunner", bus: MessageBus) -> None:
        """Attach the continuation runner and its MessageBus to this executor.

        Called once during harness startup. After this returns
        ``on_prompt_completed`` forwards completion notifications into the
        runner so chained prompts can be scheduled onto the bus.
        """
        self._continuation_runner = runner
        self._bus = bus

    def set_webhook_runner(self, runner: "WebhookRunner") -> None:
        """Attach the webhook runner and seed it with the current backend map.

        The runner is told the live ``backends`` dict and the resolved
        ``default_backend_id`` so it can dispatch outbound webhook prompts
        through the same routing surface the A2A path uses.
        """
        self._webhook_runner = runner
        runner.set_backends(self._backends, self._default_backend_id)

    def _routing_entry_for_kind(self, kind: str) -> RoutingEntry | None:
        """Return the RoutingEntry for the given message kind, or None to use the default."""
        if kind == "a2a":
            entry = self._routing.a2a
        elif kind == "heartbeat":
            entry = self._routing.heartbeat
        elif kind.startswith("job"):
            entry = self._routing.job
        elif kind.startswith("task"):
            entry = self._routing.task
        elif kind.startswith("trigger"):
            entry = self._routing.trigger
        elif kind.startswith("continuation"):
            entry = self._routing.continuation
        else:
            entry = None
        logger.debug(
            "routing kind=%r → entry agent=%r model=%r",
            kind,
            entry.agent if entry else None,
            entry.model if entry else None,
        )
        return entry

    def _resolve_model(
        self, message_model: str | None, routing_entry: RoutingEntry | None, backend_id: str
    ) -> str | None:
        """Resolve the model to use: per-message → routing entry → per-backend config.

        The routing entry model is only applied when the routing entry's agent matches
        the resolved backend. If a per-item agent override redirects to a different
        backend, the routing entry model is irrelevant and we fall through to the
        per-backend config model instead.
        """
        if message_model:
            logger.debug("model resolution: using per-message override %r", message_model)
            return message_model
        if routing_entry and routing_entry.model and (routing_entry.agent is None or routing_entry.agent == backend_id):
            logger.debug("model resolution: using routing entry model %r", routing_entry.model)
            return routing_entry.model
        backend = self._backends.get(backend_id)
        if backend is not None and backend._config.model:
            logger.debug("model resolution: using backend config model %r for %r", backend._config.model, backend_id)
            return backend._config.model
        logger.debug("model resolution: no model configured for backend %r — sending without override", backend_id)
        return None

    def _mcp_watchers(self):
        """Return callables for any backends that have an MCP config watcher."""
        watchers = []
        for backend in self._backends.values():
            watcher = getattr(backend, "mcp_config_watcher", None)
            if callable(watcher):
                watchers.append(watcher)
        return watchers

    async def on_prompt_completed(
        self,
        source: str,
        kind: str,
        session_id: str,
        success: bool,
        response: str,
        duration_seconds: float,
        error: str | None = None,
        model: str | None = None,
        trace_context: TraceContext | None = None,
    ) -> None:
        """Fan out a prompt-completion event to continuation and webhook runners.

        Both side effects are gated on the corresponding runner being
        attached, and both are non-blocking — neither runner blocks the
        caller. The ``trace_context`` is propagated to the continuation
        notification so any child prompt joins the originating trace
        (#784), and to the webhook fire so delivery records carry the
        same ``trace_id``.
        """
        if self._continuation_runner is not None:
            # Propagate upstream trace_context so the continuation's prompt
            # joins the originating trace rather than surfacing as a new
            # root (#784). Callers of on_prompt_completed already plumb
            # trace_context through from the originating job/task/trigger.
            self._continuation_runner.notify(
                kind,
                session_id,
                success,
                response or "",
                self._bus,
                trace_context=trace_context,
            )
        if self._webhook_runner is not None:
            self._webhook_runner.fire(
                source=source,
                kind=kind,
                session_id=session_id,
                success=success,
                response=response or "",
                duration_seconds=duration_seconds,
                error=error,
                model=model,
                trace_context=trace_context,
            )

    async def backends_watcher(self) -> None:
        """Watch BACKEND_CONFIG_PATH and reload backends on file change."""
        from backends.config import BACKEND_CONFIG_PATH
        from watchfiles import awatch

        watch_dir = os.path.dirname(os.path.abspath(BACKEND_CONFIG_PATH))
        logger.info(f"Backends watcher watching {BACKEND_CONFIG_PATH}")
        while True:
            if not os.path.isdir(watch_dir):
                logger.info("Backends config directory not found — retrying in 10s.")
                await asyncio.sleep(10)
                continue
            async for changes in awatch(watch_dir):
                for _, path in changes:
                    if os.path.abspath(path) == os.path.abspath(BACKEND_CONFIG_PATH):
                        logger.info("backend.yaml changed — reloading.")
                        try:
                            new_backends, new_default_id, new_routing = load_backends()
                            old_backends = list(self._backends.values())
                            self._backends = new_backends
                            self._default_backend_id = new_default_id
                            self._routing = new_routing
                            if self._webhook_runner is not None:
                                self._webhook_runner.set_backends(new_backends, new_default_id)
                            logger.info(f"Backends reloaded: {list(new_backends.keys())} (default: {new_default_id})")
                            # Close old backend clients to release connection pool resources.
                            await asyncio.gather(
                                *[b.close() for b in old_backends if hasattr(b, "close")],
                                return_exceptions=True,
                            )
                            # Cancel old MCP watcher tasks and await their completion
                            # before starting new ones, so old and new watchers do not
                            # briefly overlap and cause duplicate reloads (#369).
                            _old_watcher_tasks = list(self._mcp_watcher_tasks)
                            for t in _old_watcher_tasks:
                                t.cancel()
                            if _old_watcher_tasks:
                                await asyncio.gather(*_old_watcher_tasks, return_exceptions=True)
                            self._mcp_watcher_tasks = []
                            for watcher in self._mcp_watchers():
                                task = asyncio.create_task(_guarded(watcher))
                                task.add_done_callback(
                                    lambda t, _w=watcher: (
                                        logger.error(
                                            f"MCP watcher {_w.__name__!r} exited unexpectedly: {t.exception()!r}"
                                        )
                                        if not t.cancelled() and t.exception() is not None
                                        else None
                                    )
                                )
                                self._mcp_watcher_tasks.append(task)
                            # Reload succeeded — clear the stale gauge + counter (#702).
                            self._backends_reload_consecutive_failures = 0
                            if harness_backends_config_stale is not None:
                                harness_backends_config_stale.set(0)
                        except Exception as e:
                            logger.error("Failed to reload backends — keeping previous config: %s", e, exc_info=True)
                            # Surface reload failure in metrics so a malformed
                            # backend.yaml is visible beyond a single log line
                            # (#702). Operators can alert on the counter rate
                            # and on the stale gauge going hot; the harness
                            # also exposes the consecutive failure count so
                            # /health/ready can degrade after a threshold.
                            self._backends_reload_consecutive_failures += 1
                            if harness_backends_reload_errors_total is not None:
                                harness_backends_reload_errors_total.inc()
                            if harness_backends_config_stale is not None:
                                harness_backends_config_stale.set(1)
                        break
            logger.warning("Backends watcher exited — retrying in 10s.")
            await asyncio.sleep(10)

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        """A2A SDK entrypoint — run one inbound prompt and enqueue the reply.

        Enforces an A2A-side UTF-8 byte cap before any work
        (``A2A_MAX_PROMPT_BYTES``, #783); oversize prompts get an error
        message back and never reach the backend. Resolves the session
        id from ``context.context_id`` / metadata, sanitises it (strips
        control chars, caps at 256 bytes, falls back to a fresh uuid),
        and resolves ``backend_id`` / ``model`` / ``max_tokens`` from
        metadata with routing-config fallback. The W3C ``traceparent``
        and ``caller_id`` in metadata are preserved end-to-end (#468,
        #1084) so the call joins the originating trace and presents a
        stable principal to session binding.

        Always enqueues at least one final agent message — even when
        the backend returned an empty string — because the A2A SDK's
        DefaultRequestHandler waits for a Message event to declare the
        task complete (#harness-32603). The outbound message carries
        ``trace_id`` / ``span_id`` in metadata so callers can correlate
        the reply (#636). On exception the traceback is logged at the
        catch site so JSON-RPC -32603 surfaces remain diagnosable.

        The ``finally`` block always emits the prompt-completion
        fan-out (continuation + webhook), the SSE
        ``a2a.request.completed`` event, and the request-duration
        histogram. When :meth:`track_background` sheds the completion
        coroutine under load (#1181) the continuation and webhook
        runners are invoked synchronously in-line so completion
        signals are never lost.
        """
        _exec_start = time.monotonic()
        prompt = context.get_user_input()
        metadata = context.message.metadata or {}
        # A2A inbound prompt size cap (#783). Check before logging,
        # routing, or backend dispatch so an oversize payload never
        # exercises the rest of the pipeline. Size is measured on the
        # UTF-8 byte encoding so operators can reason about a fixed
        # byte budget rather than a Python str length.
        if A2A_MAX_PROMPT_BYTES > 0 and prompt:
            try:
                _prompt_bytes = len(prompt.encode("utf-8", errors="replace"))
            except Exception:
                _prompt_bytes = len(prompt)
            if _prompt_bytes > A2A_MAX_PROMPT_BYTES:
                logger.warning(
                    "A2A execute() rejected: prompt %d bytes > A2A_MAX_PROMPT_BYTES=%d (#783).",
                    _prompt_bytes,
                    A2A_MAX_PROMPT_BYTES,
                )
                if harness_a2a_prompt_oversize_total is not None:
                    try:
                        harness_a2a_prompt_oversize_total.inc()
                    except Exception:
                        pass
                if harness_a2a_requests_total is not None:
                    try:
                        harness_a2a_requests_total.labels(status="error").inc()
                    except Exception:
                        pass
                if harness_a2a_request_duration_seconds is not None:
                    try:
                        harness_a2a_request_duration_seconds.observe(time.monotonic() - _exec_start)
                    except Exception:
                        pass
                if harness_a2a_last_request_timestamp_seconds is not None:
                    try:
                        harness_a2a_last_request_timestamp_seconds.set(time.time())
                    except Exception:
                        pass
                await event_queue.enqueue_event(
                    new_agent_text_message(
                        f"Error: prompt {_prompt_bytes} bytes exceeds A2A_MAX_PROMPT_BYTES={A2A_MAX_PROMPT_BYTES}."
                    )
                )
                return
        _raw_sid = "".join(
            c for c in str(context.context_id or metadata.get("session_id") or "").strip()[:256] if c >= " "
        )
        session_id = _raw_sid or str(uuid.uuid4())
        # Explicit backend_id in metadata takes priority; otherwise use routing config.
        _a2a_entry = self._routing_entry_for_kind("a2a")
        backend_id = metadata.get("backend_id") or (_a2a_entry.agent if _a2a_entry else None)
        model = metadata.get("model") or None
        if not model:
            _resolved_backend_id = backend_id or self._default_backend_id
            model = self._resolve_model(None, _a2a_entry, _resolved_backend_id)
        _max_tokens_raw = metadata.get("max_tokens")
        max_tokens: int | None = None
        if _max_tokens_raw is not None:
            try:
                max_tokens = int(_max_tokens_raw)
            except (ValueError, TypeError):
                logger.warning(f"Session {session_id!r}: invalid max_tokens in metadata {_max_tokens_raw!r}, ignoring.")
        # Resolve W3C trace context from inbound metadata (#468). The A2A SDK
        # doesn't surface raw HTTP headers here, but upstream callers echo the
        # traceparent into message.metadata so we can still continue the trace.
        _tp_header = metadata.get("traceparent")
        trace_context, _had_inbound = context_from_inbound(_tp_header if isinstance(_tp_header, str) else None)
        # Inbound caller identity (#1084). Forwarded opaquely from upstream
        # relays so the chain {ingress-relay -> harness -> A2A backend}
        # presents a consistent principal to derive_session_id at each hop.
        # When absent (e.g. first-hop harness with no upstream stamp) the
        # value stays None and the session binding falls back to its
        # documented legacy derivation.
        _inbound_caller_id = metadata.get("caller_id")
        caller_id: str | None = (
            _inbound_caller_id if isinstance(_inbound_caller_id, str) and _inbound_caller_id else None
        )
        if harness_a2a_traces_received_total is not None:
            harness_a2a_traces_received_total.labels(has_inbound=str(_had_inbound).lower()).inc()
        # Emit a2a.request.received onto the SSE event stream (#1110).
        try:
            _rcv_payload: dict = {"concern": "a2a"}
            if model:
                _rcv_payload["model"] = model
            _agent_name = os.environ.get("AGENT_NAME", "witwave")
            get_event_stream().publish("a2a.request.received", _rcv_payload, agent_id=_agent_name)
        except Exception:  # pragma: no cover
            pass
        # Bridge to OTel (#469). When OTel is enabled the extracted context
        # becomes the parent of the server span below; when disabled this
        # returns None and start_span silently emits a no-op span.
        _otel_parent = extract_otel_context({"traceparent": _tp_header}) if _tp_header else None
        task_id = context.task_id

        if task_id:
            current = asyncio.current_task()
            if current:
                self._running_tasks[task_id] = current
                if harness_running_tasks is not None:
                    harness_running_tasks.inc()
        _response = ""
        _success = False
        _error: str | None = None
        _span_attrs = {
            "witwave.session_id": session_id,
            "witwave.backend_id": backend_id or self._default_backend_id,
            "witwave.model": model or "",
            "witwave.trace_id": trace_context.trace_id,
            "witwave.has_inbound_trace": _had_inbound,
        }
        try:
            with start_span(
                "a2a.execute",
                kind="server",
                parent_context=_otel_parent,
                attributes=_span_attrs,
            ) as _span:
                try:
                    _response = await run(
                        prompt,
                        session_id,
                        self._sessions,
                        self._backends,
                        self._default_backend_id,
                        backend_id=backend_id,
                        model=model,
                        max_tokens=max_tokens,
                        trace_context=trace_context,
                        caller_id=caller_id,
                    )
                    _success = True
                    # Always enqueue a final agent message — even when the response
                    # text is empty (#harness-32603 fix). The A2A SDK's
                    # DefaultRequestHandler waits for at least one Message event from
                    # execute() to declare the task complete and serialise a JSON-RPC
                    # success reply. When execute() returned with no events queued
                    # (the prior `if _response:` gate skipped the enqueue), the SDK
                    # had nothing to return and surfaced JSON-RPC -32603 to the
                    # caller — even though run() had succeeded. Empty-pass evan
                    # sweeps reproduced this in seconds despite zero per-call latency.
                    # Enqueue a placeholder text on empty response so the SDK can
                    # always complete the task cleanly.
                    _outbound_text = _response if _response else ""
                    _msg = new_agent_text_message(_outbound_text)
                    if trace_context is not None and trace_context.trace_id:
                        # Stamp trace_id on the outbound A2A response message
                        # metadata so callers can correlate the reply with the
                        # end-to-end trace (#636). Additive / optional — absent
                        # when no trace context exists.
                        _existing = _msg.metadata or {}
                        _existing["trace_id"] = trace_context.trace_id
                        if trace_context.parent_id:
                            _existing["span_id"] = trace_context.parent_id
                        _msg.metadata = _existing
                    await event_queue.enqueue_event(_msg)
                    if harness_a2a_requests_total is not None:
                        harness_a2a_requests_total.labels(status="success").inc()
                except Exception as _exc:
                    _error = repr(_exc)
                    # Log the full traceback at the catch site (#harness-32603 diagnostic).
                    # Without this, exceptions raised after run() succeeds (during message
                    # build, event-queue enqueue, or A2A SDK serialisation) are silently
                    # re-raised and surface to the caller as JSON-RPC -32603 with empty
                    # body. The repr in `_error` reaches webhooks but isn't logged here,
                    # so operators can't diagnose without enabling debug + reproducing.
                    logger.exception(
                        "executor.execute raised after run(); session_id=%s backend=%s "
                        "trace_id=%s response_bytes=%d — caller will see JSON-RPC -32603",
                        session_id,
                        backend_id or self._default_backend_id,
                        trace_context.trace_id if trace_context is not None else "",
                        len(_response or ""),
                    )
                    if harness_a2a_requests_total is not None:
                        harness_a2a_requests_total.labels(status="error").inc()
                    set_span_error(_span, _exc)
                    raise
        finally:
            _task = self.track_background(
                self.on_prompt_completed(
                    source="a2a",
                    kind="a2a",
                    session_id=session_id,
                    success=_success,
                    response=_response,
                    duration_seconds=time.monotonic() - _exec_start,
                    error=_error,
                    model=model,
                    trace_context=trace_context,
                ),
                source="a2a",
            )
            if _task is None:
                # #1181: track_background shed the on_prompt_completed
                # fan-out due to background-task pressure. Invoke the
                # non-blocking fire-and-forget paths synchronously so
                # continuations and webhooks still receive the completion
                # signal. Both runners use asyncio.ensure_future / their
                # own queues internally — neither blocks the caller.
                try:
                    if self._continuation_runner is not None and self._bus is not None:
                        self._continuation_runner.notify(
                            "a2a",
                            session_id,
                            _success,
                            _response or "",
                            self._bus,
                            trace_context=trace_context,
                        )
                except Exception as _ec:  # noqa: BLE001 — shed-path must never raise
                    logger.error(f"shed-path continuation notify failed: {_ec!r}")
                try:
                    if self._webhook_runner is not None:
                        self._webhook_runner.fire(
                            source="a2a",
                            kind="a2a",
                            session_id=session_id,
                            success=_success,
                            response=_response or "",
                            duration_seconds=time.monotonic() - _exec_start,
                            error=_error,
                            model=model,
                            trace_context=trace_context,
                        )
                except Exception as _ew:  # noqa: BLE001
                    logger.error(f"shed-path webhook fire failed: {_ew!r}")
            if harness_a2a_request_duration_seconds is not None:
                harness_a2a_request_duration_seconds.observe(time.monotonic() - _exec_start)
            if harness_a2a_last_request_timestamp_seconds is not None:
                harness_a2a_last_request_timestamp_seconds.set(time.time())
            # Emit a2a.request.completed onto the SSE event stream (#1110).
            try:
                _comp_payload: dict = {
                    "concern": "a2a",
                    "outcome": "success" if _success else "error",
                    "duration_ms": int((time.monotonic() - _exec_start) * 1000),
                }
                if not _success and _error:
                    _comp_payload["error"] = _error[:512]
                _agent_name = os.environ.get("AGENT_NAME", "witwave")
                get_event_stream().publish("a2a.request.completed", _comp_payload, agent_id=_agent_name)
            except Exception:  # pragma: no cover
                pass
            if task_id and task_id in self._running_tasks:
                self._running_tasks.pop(task_id)
                if harness_running_tasks is not None:
                    harness_running_tasks.dec()

    async def drain_background(self) -> None:
        """Phase 1+2 of shutdown: MCP watchers and background tasks (#861).

        Split out of ``close()`` so the lifespan layer can drain executor-owned
        background work *before* the bus worker is cancelled, while keeping
        backend httpx clients alive for in-flight process_bus calls. The
        bus_task cancellation and subsequent backend close now live in
        ``main.py`` and call ``close_backends()`` after the bus has drained.
        """
        # Phase 1: MCP watchers.
        for task in self._mcp_watcher_tasks:
            task.cancel()
        if self._mcp_watcher_tasks:
            await asyncio.gather(*self._mcp_watcher_tasks, return_exceptions=True)
        self._mcp_watcher_tasks.clear()

        # Phase 2: Background tasks (webhook / trigger / continuation fires).
        if self._background_tasks:
            _pending = [t for t in self._background_tasks if not t.done()]
            for t in _pending:
                t.cancel()
            if _pending:
                await asyncio.gather(*_pending, return_exceptions=True)
            # Done callbacks will have discarded most entries by now; clear
            # anything that slipped through (e.g. callback scheduled after
            # loop close) so the set doesn't retain references.
            self._background_tasks.clear()
            if harness_background_tasks is not None:
                try:
                    harness_background_tasks.set(0)
                except Exception:
                    pass

    async def close_backends(self) -> None:
        """Phase 3 of shutdown: close pooled backend httpx clients (#861).

        Must run *after* the bus worker has drained in-flight process_bus
        calls — otherwise in-flight calls observe a closed httpx client and
        surface "client has been closed" / connection-reset errors.

        Also closes any backend constructed by ``backends_watcher`` that may
        not yet have been swapped into ``self._backends`` — or that was swapped
        out and is awaiting gc — via the module-level ``_pending_backends``
        WeakSet (#1279). A reload that finishes building new backends after
        this lifespan `finally` has already snapshotted ``self._backends``
        would otherwise leak their pooled ``httpx.AsyncClient``s.
        """
        # De-duplicate across the live dict and the pending WeakSet while
        # preserving identity (don't rely on ==, use id() keys).
        _seen_ids: set[int] = set()
        _targets: list[object] = []
        for _backend in list(self._backends.values()):
            if id(_backend) not in _seen_ids:
                _seen_ids.add(id(_backend))
                _targets.append(_backend)
        try:
            from backends.a2a import _pending_backends as _pending
        except Exception:
            _pending = None  # type: ignore[assignment]
        if _pending is not None:
            for _backend in list(_pending):
                if id(_backend) not in _seen_ids:
                    _seen_ids.add(id(_backend))
                    _targets.append(_backend)
        for _backend in _targets:
            _close = getattr(_backend, "close", None)
            if _close is None:
                continue
            try:
                await _close()
            except Exception as _exc:  # noqa: BLE001 — shutdown must continue
                logger.warning("backend %r close() failed: %r", getattr(_backend, "id", "?"), _exc)

    async def close(self) -> None:
        """Coordinated shutdown: watchers, background tasks, backend clients (#604).

        Retained for back-compat (tests and any direct callers). In the
        harness's phased-shutdown path (#861), ``main.py`` calls
        ``drain_background()`` inside the lifespan finally (before bus_task
        cancel) and ``close_backends()`` after the bus has drained, so that
        in-flight process_bus calls don't see closed httpx clients.
        """
        await self.drain_background()
        await self.close_backends()

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        """A2A SDK cancellation hook — request cancel of the running task, if any.

        Looks up the asyncio task previously registered under
        ``context.task_id`` in ``_running_tasks`` and calls ``.cancel()``
        on it; logs at INFO either way. Bumps
        ``harness_task_cancellations_total`` unconditionally so the metric
        reflects requests received, not requests that found a live task.
        """
        if harness_task_cancellations_total is not None:
            harness_task_cancellations_total.inc()
        task_id = context.task_id
        task = self._running_tasks.get(task_id) if task_id else None
        if task:
            task.cancel()
            logger.info(f"Task {task_id!r} cancellation requested.")
        else:
            logger.info(f"Task {task_id!r} cancellation requested but no running task found.")

    async def process_bus(self, message: Message) -> None:
        """Run one bus-delivered :class:`Message` and complete its Future.

        Used for non-A2A work (heartbeats, jobs, tasks, triggers,
        continuations). Resolves the backend via per-message override
        → routing config → default, mints a fresh trace context when
        the message has none so internally scheduled work still
        carries a ``trace_id`` (#468), and dispatches to
        :func:`run_consensus` when ``message.consensus`` is populated
        or :func:`run` otherwise. The response or exception is set on
        ``message.result`` (when non-None and not already done) so the
        producer side can await completion.

        Mirrors :meth:`execute` for completion fan-out: the
        ``on_prompt_completed`` coroutine is scheduled via
        :meth:`track_background` with ``source="bus"``; on shed
        (#1181) the continuation and webhook runners fire
        synchronously in-line so bus-kind completions never go silently
        missing.
        """
        _bus_start = time.monotonic()
        _session_id = message.session_id or str(uuid.uuid4())
        _response = ""
        _success = False
        _error: str | None = None
        # Use per-item backend_id override, then routing config, then default.
        _entry = self._routing_entry_for_kind(message.kind)
        _routed_backend_id = message.backend_id or (_entry.agent if _entry else None)
        _resolved_id = _routed_backend_id or self._default_backend_id
        _model = self._resolve_model(message.model, _entry, _resolved_id)
        # Bus-originated work (heartbeats, jobs, tasks, triggers, continuations)
        # mints a fresh context when the message has no inbound trace. This
        # ensures every backend call carries a trace_id even for internally
        # scheduled work (#468).
        _trace_context = message.trace_context or new_context()
        try:
            if message.consensus:
                _response = await run_consensus(
                    message.prompt,
                    _session_id,
                    self._sessions,
                    self._backends,
                    self._default_backend_id,
                    consensus_entries=message.consensus,
                    synthesis_backend_id=_resolved_id,
                    synthesis_model=_model,
                    max_tokens=message.max_tokens,
                    trace_context=_trace_context,
                )
            else:
                _response = await run(
                    message.prompt,
                    _session_id,
                    self._sessions,
                    self._backends,
                    self._default_backend_id,
                    backend_id=_routed_backend_id,
                    model=_model,
                    max_tokens=message.max_tokens,
                    trace_context=_trace_context,
                )
            _success = True
            if message.result is not None and not message.result.done():
                message.result.set_result(_response)
        except Exception as e:
            _error = repr(e)
            logger.exception(f"process_bus error for session {_session_id!r}: {e}")
            if message.result is not None and not message.result.done():
                message.result.set_exception(e)
        finally:
            _task = self.track_background(
                self.on_prompt_completed(
                    source="bus",
                    kind=message.kind,
                    session_id=_session_id,
                    success=_success,
                    response=_response,
                    duration_seconds=time.monotonic() - _bus_start,
                    error=_error,
                    model=_model,
                    trace_context=_trace_context,
                ),
                source="bus",
            )
            if _task is None:
                # #1181: shed-path fallback — fire continuation/webhook
                # runners synchronously so bus-kind completions (heartbeat,
                # job, task, trigger, continuation) still propagate when
                # track_background drops the wrapper coroutine.
                try:
                    if self._continuation_runner is not None and self._bus is not None:
                        self._continuation_runner.notify(
                            message.kind,
                            _session_id,
                            _success,
                            _response or "",
                            self._bus,
                            trace_context=_trace_context,
                        )
                except Exception as _ec:  # noqa: BLE001
                    logger.error(f"shed-path continuation notify failed: {_ec!r}")
                try:
                    if self._webhook_runner is not None:
                        self._webhook_runner.fire(
                            source="bus",
                            kind=message.kind,
                            session_id=_session_id,
                            success=_success,
                            response=_response or "",
                            duration_seconds=time.monotonic() - _bus_start,
                            error=_error,
                            model=_model,
                            trace_context=_trace_context,
                        )
                except Exception as _ew:  # noqa: BLE001
                    logger.error(f"shed-path webhook fire failed: {_ew!r}")
