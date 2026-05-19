import asyncio
import logging
import os
import time
from dataclasses import asdict, dataclass, field
from fnmatch import fnmatch
from pathlib import Path
from typing import Any

from bus import Message, MessageBus
from events import get_event_stream
from metrics import (
    harness_continuation_fanin_evictions_total,
    harness_continuation_fires_shed_total,
    harness_continuation_fires_total,
    harness_continuation_items_registered,
    harness_continuation_parse_errors_total,
    harness_continuation_reloads_total,
    harness_continuation_runs_total,
    harness_continuation_throttled_total,
    harness_continuations_shed_on_shutdown_total,
    harness_file_watcher_restarts_total,
    harness_watcher_events_total,
)
from utils import (
    ConsensusEntry,
    parse_consensus,
    parse_duration,
    parse_frontmatter,
    parse_frontmatter_raw,
    run_awatch_loop,
)

logger = logging.getLogger(__name__)

CONTINUATIONS_DIR = os.environ.get("CONTINUATIONS_DIR", "/home/agent/.witwave/continuations")

# Sentinel returned by parse_continuation_file() when the file is explicitly
# disabled (enabled: false).  Distinct from None (parse error) so that
# _register() can unregister a disabled continuation rather than preserving it.
_DISABLED = object()

# Global default cap on concurrent in-flight fires per continuation.
# Overridable per-continuation via the max-concurrent-fires frontmatter field.
CONTINUATION_MAX_CONCURRENT_FIRES = int(os.environ.get("CONTINUATION_MAX_CONCURRENT_FIRES", "5"))
# Global concurrency cap across *all* continuations (#781). Mirrors
# WEBHOOK_MAX_CONCURRENT_DELIVERIES so N continuations sharing an
# upstream can't fan out 5×N in-flight fires and starve the harness
# event loop. Set to 0 to disable (not recommended). Default is 5×the
# per-continuation cap, matching the webhook pattern.
CONTINUATION_MAX_CONCURRENT_FIRES_GLOBAL = int(
    os.environ.get("CONTINUATION_MAX_CONCURRENT_FIRES_GLOBAL", str(CONTINUATION_MAX_CONCURRENT_FIRES * 5))
)

# TTL (seconds) for partial fan-in state entries in `_fanin_state`.  Prevents
# unbounded growth when one of several required upstreams never fires for a
# given session (#557).  Default 1 hour; override via env.
CONTINUATION_FANIN_TTL = float(os.environ.get("CONTINUATION_FANIN_TTL", "3600"))


@dataclass
class ContinuationItem:
    """Parsed reactive-continuation definition.

    Backs an entry in ``ContinuationRunner._items``. The continuation
    fires a downstream prompt whenever upstream prompts matching
    every entry in ``continues_after`` (fnmatch patterns; all must
    be satisfied for fan-in) complete and the outcome and content
    predicates pass. ``on_success`` / ``on_error`` gate fires by
    upstream outcome; ``trigger_when`` requires the upstream
    response to contain a literal substring; ``delay`` (seconds)
    defers the downstream fire. ``session_id`` pins the downstream
    conversation, falling back to the upstream session at fire time
    when ``None``. ``max_concurrent_fires`` is the per-continuation
    in-flight cap; ``enabled=False`` keeps the item listed but
    excludes it from event matching in ``notify()``. Continuations
    have no ``next_fire`` (they are event-driven), only the
    observed ``last_fire`` / ``last_success`` timestamps.
    """

    path: str
    name: str
    continues_after: list[str]  # one or more upstream kind patterns (fnmatch); all must complete (fan-in)
    content: str  # prompt body
    on_success: bool = True  # fire on successful upstream completion
    on_error: bool = False  # fire on upstream error
    trigger_when: str | None = None  # only fire if upstream response contains this string
    delay: float | None = None  # seconds to wait before firing
    session_id: str | None = None  # if None, inherit upstream session_id at fire time
    model: str | None = None
    backend_id: str | None = None
    description: str | None = None
    consensus: list[ConsensusEntry] = field(default_factory=list)
    max_tokens: int | None = None
    max_concurrent_fires: int = CONTINUATION_MAX_CONCURRENT_FIRES
    # When False, the continuation is listed in /continuations for
    # dashboard visibility but does not subscribe to upstream events —
    # no fires. Flipping enabled:true re-arms on reload.
    enabled: bool = True
    # Fire bookkeeping surfaced on /continuations (#1087). Epoch
    # seconds; None = never fired. next_fire stays None for
    # continuations because they're reactive — no cron schedule.
    last_fire: float | None = field(default=None, compare=False)
    last_success: float | None = field(default=None, compare=False)


def parse_continuation_file(path: str) -> "ContinuationItem | object | None":
    """Parse a continuation file. Returns:
    - ContinuationItem on success
    - _DISABLED sentinel when enabled: false or continues-after is missing/empty
    - None on parse error (caller should preserve last known good registration)
    """
    try:
        with open(path) as f:
            raw = f.read()

        fields, content = parse_frontmatter(raw)
        raw_fields, _ = parse_frontmatter_raw(raw)

        enabled = True
        if "enabled" in fields:
            enabled = str(fields["enabled"]).lower() not in ("false", "no", "off", "n", "0", "")

        # #1689: read from raw_fields so YAML lists pass through as
        # actual Python lists. The legacy `fields` dict at line 100
        # stringifies every value via `str(v)` so a YAML-list input
        # would arrive as `"['job:a', 'job:b']"` and the comma-split
        # branch below would produce bracket-tainted nonsense
        # (`["[\\'job:a\\'", "\\'job:b\\']"]`) that never matches a
        # real upstream kind. Reading the raw value preserves the list
        # shape; comma-separated strings still work via the else branch.
        continues_after_raw = raw_fields.get("continues-after")
        if continues_after_raw is None:
            continues_after_raw = ""
        # Accepts a single string or a YAML list.  A comma-separated string is
        # also accepted as a convenience shorthand (e.g. "job:a, job:b").
        if isinstance(continues_after_raw, list):
            continues_after = [str(p).strip() for p in continues_after_raw if str(p).strip()]
        else:
            text = str(continues_after_raw).strip()
            if not text:
                continues_after = []
            else:
                continues_after = [p.strip() for p in text.split(",") if p.strip()]
        # Missing continues-after is a hard parse failure for ENABLED
        # continuations (nothing to subscribe to). For disabled ones it
        # just means the display shows "—" — the user is parking the
        # file while figuring out what it should chain off of.
        if not continues_after and enabled:
            # #1184: a missing continues-after on an *enabled* item is a parse
            # failure, not a "disabled" signal. Returning _DISABLED here caused
            # _register to unregister a healthy last-known-good entry on every
            # subsequent reload that happened to trip this branch (e.g. a
            # transient frontmatter edit mid-save). Return None so _register
            # preserves the previous registration until a fully-valid file
            # reappears.
            logger.warning(
                f"Continuation file {path}: missing required 'continues-after' field, "
                "treating as parse error (preserving last-known-good registration)."
            )
            return None

        filename = Path(path).stem
        name = fields.get("name") or filename

        on_success = True
        if "on-success" in fields:
            on_success = str(fields["on-success"]).lower() not in ("false", "")

        on_error = False
        if "on-error" in fields:
            on_error = str(fields["on-error"]).lower() not in ("false", "")

        trigger_when = fields.get("trigger-when") or None

        delay: float | None = None
        delay_raw = fields.get("delay")
        if delay_raw:
            try:
                delay = parse_duration(str(delay_raw))
            except ValueError as e:
                logger.warning(f"Continuation file {path}: invalid 'delay': {e}, ignoring.")

        session_id = fields.get("session") or None
        model = fields.get("model") or None
        backend_id = fields.get("agent") or None
        description = fields.get("description") or None
        consensus = parse_consensus(raw_fields.get("consensus"))
        max_tokens: int | None = None
        max_tokens_raw = fields.get("max-tokens") or fields.get("max_tokens")
        if max_tokens_raw is not None:
            try:
                max_tokens = max(1, int(max_tokens_raw))
            except (ValueError, TypeError):
                logger.warning(f"Continuation file {path}: invalid 'max-tokens' value {max_tokens_raw!r}, ignoring.")

        max_concurrent_fires = CONTINUATION_MAX_CONCURRENT_FIRES
        max_fires_raw = fields.get("max-concurrent-fires") or fields.get("max_concurrent_fires")
        if max_fires_raw is not None:
            try:
                max_concurrent_fires = max(1, int(max_fires_raw))
            except (ValueError, TypeError):
                logger.warning(
                    f"Continuation file {path}: invalid 'max-concurrent-fires' value {max_fires_raw!r}, "
                    f"using default {CONTINUATION_MAX_CONCURRENT_FIRES}."
                )

        return ContinuationItem(
            path=path,
            name=name,
            continues_after=continues_after,
            content=content,
            on_success=on_success,
            on_error=on_error,
            trigger_when=trigger_when,
            delay=delay,
            session_id=session_id,
            model=model,
            backend_id=backend_id,
            description=description,
            consensus=consensus,
            max_tokens=max_tokens,
            max_concurrent_fires=max_concurrent_fires,
            enabled=enabled,
        )

    except Exception as e:
        if harness_continuation_parse_errors_total is not None:
            harness_continuation_parse_errors_total.inc()
        logger.error(f"Continuation file {path}: failed to parse — {e}, skipping.")
        return None


async def _fire(
    item: ContinuationItem,
    session_id: str,
    bus: MessageBus,
    trace_context: Any = None,
    upstream_kind_label: str = "",
) -> None:
    if item.delay is not None:
        try:
            await asyncio.sleep(item.delay)
        except asyncio.CancelledError:
            # Shutdown cancelled the delay before the fire landed (#1280).
            # Log loudly so operators know the continuation was dropped, bump
            # the shed-on-shutdown counter, then re-raise so the runner still
            # unwinds its task tree cleanly.
            logger.warning(
                "continuation %r cancelled during delay (session_id=%s, upstream=%s)",
                item.name,
                session_id,
                upstream_kind_label or "",
            )
            if harness_continuations_shed_on_shutdown_total is not None:
                try:
                    harness_continuations_shed_on_shutdown_total.labels(name=item.name).inc()
                except Exception:
                    pass
            raise
    from prompt_env import resolve_prompt_env  # noqa: E402 — scoped import keeps startup simple

    prompt = resolve_prompt_env(f"Continuation: {item.name}\n\n{item.content}")
    resolved_session = item.session_id or session_id
    item.last_fire = time.time()  # #1087
    _start = time.monotonic()
    # Parse kind like "job:daily-report" → ("job", "daily-report") for the
    # event payload's (upstream_kind, upstream_name) split. Legacy callers
    # that didn't pass a label default to upstream_kind="continuation",
    # upstream_name=session_id slice so the envelope still validates.
    _parsed_kind = "continuation"
    _parsed_name = resolved_session[:32]
    if upstream_kind_label and ":" in upstream_kind_label:
        _base, _, _rest = upstream_kind_label.partition(":")
        if _base in {"job", "task", "trigger", "a2a", "continuation"}:
            _parsed_kind = _base
            _parsed_name = _rest or _parsed_name
    elif upstream_kind_label in {"job", "task", "trigger", "a2a", "continuation"}:
        _parsed_kind = upstream_kind_label
    _agent_name = os.environ.get("AGENT_NAME", "witwave")
    try:
        # Propagate the upstream trace_context (#784) so the continuation
        # span joins the same trace as its originating
        # job/task/trigger/a2a/continuation rather than surfacing as a new
        # root. Without this, every continuation in a chain shows up as
        # an orphan trace in Tempo/Jaeger.
        response = await bus.send(
            Message(
                prompt=prompt,
                session_id=resolved_session,
                kind=f"continuation:{item.name}",
                model=item.model,
                backend_id=item.backend_id,
                consensus=item.consensus,
                max_tokens=item.max_tokens,
                trace_context=trace_context,
            )
        )
        if harness_continuation_runs_total is not None:
            harness_continuation_runs_total.labels(name=item.name, status="success").inc()
        item.last_success = time.time()  # #1087
        logger.info(f"Continuation '{item.name}' completed successfully. Response: {response!r}")
        try:
            get_event_stream().publish(
                "continuation.fired",
                {
                    "name": item.name,
                    "upstream_kind": _parsed_kind,
                    "upstream_name": _parsed_name,
                    "duration_ms": int((time.monotonic() - _start) * 1000),
                    "outcome": "success",
                },
                agent_id=_agent_name,
            )
        except Exception:  # pragma: no cover
            pass
    except Exception as e:
        if harness_continuation_runs_total is not None:
            harness_continuation_runs_total.labels(name=item.name, status="error").inc()
        logger.error(f"Continuation '{item.name}' error: {e}")
        try:
            get_event_stream().publish(
                "continuation.fired",
                {
                    "name": item.name,
                    "upstream_kind": _parsed_kind,
                    "upstream_name": _parsed_name,
                    "duration_ms": int((time.monotonic() - _start) * 1000),
                    "outcome": "error",
                    "error": repr(e)[:512],
                },
                agent_id=_agent_name,
            )
        except Exception:  # pragma: no cover
            pass


class ContinuationRunner:
    """Reactive registry mapping ``ContinuationItem``s to upstream events.

    Continuations don't run on a schedule; instead the harness calls
    ``notify()`` whenever a prompt completes, and each enabled item
    whose ``continues_after`` pattern(s) match (and whose outcome /
    ``trigger_when`` predicates pass) fires a downstream prompt.
    Multiple-pattern items use fan-in: per-``(item.path,
    session_id)`` state in ``_fanin_state`` accumulates the upstream
    kinds seen so far and the item only fires once every configured
    pattern is satisfied. Stale partial fan-in state is evicted
    after ``CONTINUATION_FANIN_TTL`` seconds to bound memory growth
    when a required upstream never arrives (#557). Two throttles
    bound in-flight fan-out: per-item ``max_concurrent_fires`` and
    the process-wide ``CONTINUATION_MAX_CONCURRENT_FIRES_GLOBAL``.
    ``close()`` drains in-flight fires under a timeout so harness
    shutdown does not abort live bus POSTs.
    """

    async def close(self, timeout: float = 5.0) -> None:
        """Drain in-flight continuation fires under a timeout (#1275).

        Called during harness shutdown so CancelledError does not kill
        in-flight bus.send POSTs mid-flight (parity with webhook_runner.close()).
        """
        if not self._active_fires:
            return
        _fires = list(self._active_fires)
        try:
            await asyncio.wait_for(
                asyncio.gather(*_fires, return_exceptions=True),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            logger.warning(
                "continuations: drain timed out after %.1fs; %d fire(s) abandoned.",
                timeout,
                len(self._active_fires),
            )
            for _t in list(self._active_fires):
                if not _t.done():
                    _t.cancel()

    def __init__(self):
        self._items: dict[str, ContinuationItem] = {}
        self._active_fires: set[asyncio.Task] = set()
        # Per-continuation in-flight tasks, keyed by continuation name.
        self._fires_by_name: dict[str, set[asyncio.Task]] = {}
        # Fan-in state: maps (continuation_path, session_id) -> (monotonic_ts, set
        # of upstream kind patterns satisfied so far).  Cleared on each fire;
        # stale partial entries are evicted after CONTINUATION_FANIN_TTL to
        # prevent unbounded growth when a required upstream never completes
        # (#557).
        #
        # #1187: keyed by the continuation *file path* rather than the
        # mutable ``item.name`` so a rename (``name:`` edit) mid-fan-in
        # doesn't orphan previously-accumulated state under the old name.
        # Path is stable for the lifetime of a registration.
        self._fanin_state: dict[tuple[str, str], tuple[float, set[str]]] = {}

    def _register(self, path: str) -> None:
        result = parse_continuation_file(path)
        if result is _DISABLED:
            # parse returned _DISABLED for "hard unregisterable" reasons
            # (missing continues-after on an enabled continuation). Pull
            # any previous registration.
            self._unregister(path)
            return
        if result is None:
            # Parse error — preserve the last known good registration.
            return
        item = result
        self._unregister(path)
        self._items[path] = item
        # registered-count metric tracks only enabled continuations; the
        # dispatcher filters on enabled so disabled entries never fire.
        if harness_continuation_items_registered is not None:
            harness_continuation_items_registered.set(sum(1 for i in self._items.values() if i.enabled))
        if item.enabled:
            logger.info(f"Continuation '{item.name}' registered (continues-after: {item.continues_after}).")
        else:
            logger.info(f"Continuation '{item.name}' disabled — listed but not subscribing.")

    def _unregister(self, path: str) -> None:
        existing = self._items.pop(path, None)
        if existing is not None:
            logger.info(f"Continuation '{existing.name}' unregistered.")
            # #1187: match fan-in state by path (the stable key), not by
            # item.name (mutable across a rename).
            stale = [k for k in self._fanin_state if k[0] == path]
            for k in stale:
                del self._fanin_state[k]
        if harness_continuation_items_registered is not None:
            harness_continuation_items_registered.set(sum(1 for i in self._items.values() if i.enabled))

    def _evict_stale_fanin(self, now: float | None = None) -> int:
        """Evict fan-in state entries older than CONTINUATION_FANIN_TTL.

        Returns the number of evicted entries.  Records each eviction on the
        harness_continuation_fanin_evictions_total counter, labelled by
        continuation name.  Called opportunistically from notify() so stale
        partial fan-ins can never accumulate unboundedly (#557).
        """
        if CONTINUATION_FANIN_TTL <= 0:
            return 0
        if now is None:
            now = time.monotonic()
        cutoff = now - CONTINUATION_FANIN_TTL
        stale_keys = [k for k, (ts, _seen) in self._fanin_state.items() if ts < cutoff]
        for key in stale_keys:
            # #1187: key[0] is now the continuation file path; resolve back to
            # the current item.name for log + metric labels so operator-facing
            # signals stay stable. Falls back to path basename if the item has
            # been unregistered before the evict pass observed it.
            _path, _session = key
            _item = self._items.get(_path)
            name = _item.name if _item is not None else Path(_path).stem
            del self._fanin_state[key]
            logger.info(
                f"Continuation '{name}': evicted stale fan-in state "
                f"(session={_session}) after {CONTINUATION_FANIN_TTL}s TTL."
            )
            if harness_continuation_fanin_evictions_total is not None:
                harness_continuation_fanin_evictions_total.labels(name=name).inc()
        return len(stale_keys)

    async def _scan(self) -> None:
        if not os.path.isdir(CONTINUATIONS_DIR):
            return
        try:
            filenames = os.listdir(CONTINUATIONS_DIR)
        except OSError:
            return
        for filename in filenames:
            if filename.endswith(".md"):
                self._register(os.path.join(CONTINUATIONS_DIR, filename))

    def items(self) -> list[dict]:
        """Return a serializable snapshot of all continuation items (enabled + disabled)."""
        result = []
        for item in self._items.values():
            # Serialize continues_after: list when >1 entry, single
            # string when 1, None for disabled entries that have no
            # upstream specified (legal only when enabled=False).
            if not item.continues_after:
                ca: str | list[str] | None = None
            elif len(item.continues_after) > 1:
                ca = item.continues_after
            else:
                ca = item.continues_after[0]
            result.append(
                {
                    "name": item.name,
                    "continues_after": ca,
                    "on_success": item.on_success,
                    "on_error": item.on_error,
                    "trigger_when": item.trigger_when,
                    "delay": item.delay,
                    "description": item.description,
                    "backend_id": item.backend_id,
                    "model": item.model,
                    "consensus": [asdict(e) for e in item.consensus],
                    "max_tokens": item.max_tokens,
                    "max_concurrent_fires": item.max_concurrent_fires,
                    "active_fires": len(self._fires_by_name.get(item.name, set())),
                    "enabled": item.enabled,
                    # #1087 — reactive runner has no next_fire, but the
                    # observed fire timestamps still answer "did it fire?".
                    "next_fire": None,
                    "last_fire": item.last_fire,
                    "last_success": item.last_success,
                }
            )
        return result

    def notify(
        self,
        kind: str,
        session_id: str,
        success: bool,
        response: str,
        bus: MessageBus,
        trace_context: Any = None,
    ) -> None:
        """Called by on_prompt_completed() when an upstream completes. Non-blocking.

        ``trace_context`` is the W3C trace-context captured on the
        upstream Message so the fired continuation inherits the same
        trace_id (#784). ``None`` preserves the pre-#784 behaviour of
        starting a new root.
        """
        # Evict any stale partial fan-in state before processing this event so
        # sessions whose required upstream never fires cannot leak memory (#557).
        self._evict_stale_fanin()
        for item in list(self._items.values()):
            # Disabled continuations stay in _items for listing purposes
            # only — never subscribe to upstream events.
            if not item.enabled:
                continue
            outcome_matches = (success and item.on_success) or (not success and item.on_error)
            content_matches = item.trigger_when is None or item.trigger_when in response

            # Find which pattern(s) in continues_after this upstream kind satisfies.
            matched_patterns = [p for p in item.continues_after if p == "*" or fnmatch(kind, p)]
            if not matched_patterns or not outcome_matches or not content_matches:
                continue

            fanin_key: tuple[str, str] | None = None
            if len(item.continues_after) == 1:
                # Single-upstream fast path — fire immediately (original behaviour).
                ready = True
            else:
                # Fan-in: record which concrete upstream kinds have arrived
                # for this session, then ready-check by matching each
                # configured pattern against the stored kinds (#1039). The
                # previous implementation stored patterns and called
                # ``fnmatch(p2, p)`` — treating a stored pattern as the
                # 'name' against the configured pattern. Same-base patterns
                # trivially matched (``job:* == job:*``) so fan-ins fired
                # after seeing any one upstream whose pattern-shape overlapped
                # with another required pattern, regardless of whether the
                # other upstream had actually completed.
                # #1187: key by path (stable) rather than item.name (mutable).
                key = (item.path, session_id)
                entry = self._fanin_state.get(key)
                if entry is None:
                    seen: set[str] = set()
                    _prior_ts: float | None = None
                else:
                    _prior_ts, seen = entry
                _pre_size = len(seen)
                seen.add(kind)
                # #1578: only refresh the TTL when the event advances fan-in
                # readiness (new kind added). Repeat events from a noisy
                # upstream would otherwise keep partial fan-in state alive
                # indefinitely past the configured window.
                if len(seen) > _pre_size or _prior_ts is None:
                    self._fanin_state[key] = (time.monotonic(), seen)
                else:
                    self._fanin_state[key] = (_prior_ts, seen)
                # Every configured pattern must be satisfied by at least one
                # observed upstream kind.
                ready = all(any(p == "*" or fnmatch(kind_seen, p) for kind_seen in seen) for p in item.continues_after)
                if ready:
                    # Defer state clear until after the throttle check passes
                    # (#656). Clearing before the throttle dropped accumulated
                    # pattern state for shed fires, leaving the fan-in with no
                    # replay path.
                    fanin_key = key

            if not ready:
                continue

            # Global throttle (#781): shed the fire if the process-wide
            # in-flight continuation count is at or above
            # CONTINUATION_MAX_CONCURRENT_FIRES_GLOBAL. Mirrors the
            # webhook runner's WEBHOOK_MAX_CONCURRENT_DELIVERIES cap so
            # N continuations sharing an upstream can't fan out 5×N
            # in-flight fires and starve the harness event loop.
            if (
                CONTINUATION_MAX_CONCURRENT_FIRES_GLOBAL > 0
                and len(self._active_fires) >= CONTINUATION_MAX_CONCURRENT_FIRES_GLOBAL
            ):
                logger.warning(
                    f"Continuation '{item.name}': global in-flight cap "
                    f"({CONTINUATION_MAX_CONCURRENT_FIRES_GLOBAL}) reached — "
                    f"shedding fire for upstream '{kind}'."
                )
                if harness_continuation_fires_shed_total is not None:
                    try:
                        harness_continuation_fires_shed_total.labels(name=item.name).inc()
                    except Exception:
                        pass
                # Fan-in state intentionally preserved — same as the
                # per-continuation throttle path — so a future upstream
                # event can re-enter once the cap drops.
                continue

            # Throttle: skip this fire if the per-continuation in-flight
            # count already equals max_concurrent_fires.
            fires = self._fires_by_name.setdefault(item.name, set())
            if len(fires) >= item.max_concurrent_fires:
                logger.warning(
                    f"Continuation '{item.name}': max_concurrent_fires ({item.max_concurrent_fires}) "
                    f"reached — skipping fire for upstream '{kind}'."
                )
                if harness_continuation_throttled_total is not None:
                    harness_continuation_throttled_total.labels(name=item.name).inc()
                # Fan-in state intentionally preserved — a subsequent
                # upstream event can re-enter the ready branch once the
                # in-flight count drops below max_concurrent_fires.
                continue

            # Throttle passed — schedule first, then clear fan-in state so
            # a future exception between the two lines cannot produce a
            # cleared-state-with-no-fire window (#1325).
            if harness_continuation_fires_total is not None:
                harness_continuation_fires_total.labels(upstream_kind=kind).inc()
            _t = asyncio.ensure_future(_fire(item, session_id, bus, trace_context, upstream_kind_label=kind))
            if fanin_key is not None:
                self._fanin_state.pop(fanin_key, None)
            self._active_fires.add(_t)
            fires.add(_t)

            def _cleanup(t: asyncio.Task, _name: str = item.name) -> None:
                self._active_fires.discard(t)
                # Pop-when-empty: drop the per-name set once it's drained so
                # entries for unregistered/renamed continuations don't linger
                # across hot reloads (#507).
                _fires = self._fires_by_name.get(_name)
                if _fires is not None:
                    _fires.discard(t)
                    if not _fires:
                        self._fires_by_name.pop(_name, None)

            _t.add_done_callback(_cleanup)

    async def run(self) -> None:
        """Run the continuation watcher loop forever.

        Performs an initial ``_scan`` of ``CONTINUATIONS_DIR`` and
        then drives ``awatch`` via ``run_awatch_loop`` so
        create/modify events call ``_register`` and delete events
        call ``_unregister``, incrementing
        ``harness_continuation_reloads_total`` on each. Continuations
        do not run autonomously inside ``run()`` — they are reactive
        and fired by ``notify()`` when matching upstream events
        arrive. The watcher self-restarts on exit and survives
        directory deletion by retrying every 10s.
        """
        logger.info(f"Continuation runner watching {CONTINUATIONS_DIR}")

        def _on_change(path: str) -> None:
            logger.info(f"Continuation file changed: {path}")
            if harness_continuation_reloads_total is not None:
                harness_continuation_reloads_total.inc()
            self._register(path)

        def _on_delete(path: str) -> None:
            logger.info(f"Continuation file removed: {path}")
            if harness_continuation_reloads_total is not None:
                harness_continuation_reloads_total.inc()
            self._unregister(path)

        def _cleanup() -> None:
            for path in list(self._items.keys()):
                self._unregister(path)

        await run_awatch_loop(
            directory=CONTINUATIONS_DIR,
            watcher_name="continuations",
            scan=self._scan,
            on_change=_on_change,
            on_delete=_on_delete,
            cleanup=_cleanup,
            logger_=logger,
            not_found_message="Continuations directory not found — retrying in 10s.",
            watcher_exited_message="Continuations directory watcher exited — directory deleted or unavailable. Retrying in 10s.",  # noqa: E501
            watcher_events_metric=harness_watcher_events_total,
            file_watcher_restarts_metric=harness_file_watcher_restarts_total,
        )
