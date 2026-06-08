import asyncio
import json
import logging
import os
import re
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta, timezone
from datetime import time as dtime
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from bus import Message, MessageBus
from croniter import croniter
from events import get_event_stream
from metrics import (
    harness_checkpoint_write_errors_total,
    harness_file_watcher_restarts_total,
    harness_sched_task_checkpoint_stale_total,
    harness_sched_task_duration_seconds,
    harness_sched_task_error_duration_seconds,
    harness_sched_task_item_last_error_timestamp_seconds,
    harness_sched_task_item_last_run_timestamp_seconds,
    harness_sched_task_item_last_success_timestamp_seconds,
    harness_sched_task_items_registered,
    harness_sched_task_lag_seconds,
    harness_sched_task_parse_errors_total,
    harness_sched_task_reloads_total,
    harness_sched_task_running_items,
    harness_sched_task_runs_total,
    harness_sched_task_skips_total,
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

TASKS_DIR = os.environ.get("TASKS_DIR", "/home/agent/.witwave/tasks")
CHECKPOINT_DIR = os.path.join(TASKS_DIR, ".checkpoints")
AGENT_NAME = os.environ.get("AGENT_NAME", "witwave")
_TASKS_MAX_CONCURRENT = int(os.environ.get("TASKS_MAX_CONCURRENT", "0"))

_DAY_ABBREVS = {
    "sun": "0",
    "mon": "1",
    "tue": "2",
    "wed": "3",
    "thu": "4",
    "fri": "5",
    "sat": "6",
}


def _translate_day_abbrevs(expr: str) -> str:
    """Replace three-letter day abbreviations with cron numeric equivalents."""

    def _replace(m):
        return _DAY_ABBREVS.get(m.group(0).lower(), m.group(0))

    return re.sub(r"\b(Sun|Mon|Tue|Wed|Thu|Fri|Sat)\b", _replace, expr, flags=re.IGNORECASE)


@dataclass
class TaskItem:
    """Parsed scheduled-task definition.

    Backs an entry in ``TaskRunner._items``. Combines the schedule
    fields (``days_expr``, ``tz``, ``window_start``, ``window_end``,
    ``loop``, ``loop_gap``, ``start``, ``end``) with execution
    parameters (``content``, ``model``, ``backend_id``, ``consensus``,
    ``max_tokens``, ``done_when``) and runtime bookkeeping
    (``task``, ``running``, ``enabled``, ``next_fire``, ``last_fire``,
    ``last_success``). ``window_start is None`` selects run-once
    mode (the task fires immediately and exits, with no schedule).
    ``enabled=False`` keeps the item visible on /tasks but stops
    ``TaskRunner._register`` from arming a schedule for it.
    """

    path: str
    name: str
    days_expr: str
    tz: ZoneInfo
    window_start: dtime | None  # None = run-once mode (fire immediately, no schedule)
    window_end: dtime | None  # close time derived from window_start + window_duration
    loop: bool
    loop_gap: float | None  # seconds
    done_when: str | None
    content: str
    start: date | None = None
    end: date | None = None
    model: str | None = None
    backend_id: str | None = None
    consensus: list[ConsensusEntry] = field(default_factory=list)
    max_tokens: int | None = None
    task: asyncio.Task | None = field(default=None, compare=False)
    running: bool = False
    # When False, the task is listed in /tasks for dashboard visibility
    # but no schedule is armed. Flipping enabled:true triggers a reload.
    enabled: bool = True
    # Fire bookkeeping exposed via /tasks and the #1086 discovery doc
    # (#1087). Epoch seconds — None when never computed / fired.
    next_fire: float | None = field(default=None, compare=False)
    last_fire: float | None = field(default=None, compare=False)
    last_success: float | None = field(default=None, compare=False)


# Sentinel distinguishing "file parsed cleanly but is disabled" from "parse
# failed entirely". _register uses this to stop a running schedule when a
# file flips from enabled to disabled; parse errors fall through to the
# last-known-good path so transient syntax issues don't drop a healthy
# task off the schedule. Matches the pattern in triggers/continuations/
# webhooks and the fix that went into jobs.py alongside.
_DISABLED = object()


def parse_task_file(path: str) -> "TaskItem | object | None":
    """Parse a task markdown file into a TaskItem. Returns:

    - ``TaskItem`` on success (including ``enabled=False`` items,
      which are returned with sentinel defaults for fields that
      would otherwise need validation — they are listed in /tasks
      for dashboard visibility but not scheduled).
    - ``None`` on parse error or invalid frontmatter (unknown
      timezone, invalid ``days`` cron expression, malformed
      ``window-start``, ``window-duration`` without ``window-start``,
      or any uncaught exception); each such case increments
      ``harness_sched_task_parse_errors_total`` and the caller is
      expected to preserve any previous last-known-good
      registration.

    Supported frontmatter keys: ``name``, ``timezone``, ``days``
    (cron weekday expression, three-letter abbreviations like
    ``Mon`` are translated to numerics), ``window-start`` /
    ``window_start`` (``HH:MM``; omitting it enables run-once mode),
    ``window-duration`` / ``window_duration`` (derives
    ``window_end`` tz-aware so DST transitions are respected, #1305),
    ``loop`` (requires ``window-duration``), ``loop-gap`` /
    ``loop_gap``, ``done-when`` / ``done_when``, ``start`` / ``end``
    (ISO dates), ``model``, ``agent``, ``consensus``, ``max-tokens``
    / ``max_tokens``, and ``enabled``.
    """
    try:
        with open(path) as f:
            raw = f.read()

        fields, content = parse_frontmatter(raw)
        raw_fields, _ = parse_frontmatter_raw(raw)

        enabled = True
        if "enabled" in fields:
            enabled = str(fields["enabled"]).lower() not in ("false", "no", "off", "n", "0", "")
        if not enabled:
            # Return a minimal TaskItem with enabled=False so the task is
            # listed in /tasks for dashboard visibility but isn't armed.
            # Fields that would fail validation (bad days_expr, malformed
            # window times, etc.) are set to sentinel defaults — the
            # validations only matter when the task is actually going to
            # run. Flipping enabled:true triggers a reload that runs the
            # full parse path.
            filename = Path(path).stem
            name = fields.get("name") or filename
            days_raw = str(fields.get("days") or "—")
            ws_raw = fields.get("window-start") or fields.get("window_start") or "—"
            logger.info(f"Task '{name}' disabled — listed but not scheduled.")
            return TaskItem(
                path=path,
                name=name,
                days_expr=days_raw,
                tz=ZoneInfo("UTC"),
                window_start=None,
                window_end=None,
                loop=False,
                loop_gap=None,
                done_when=None,
                content=content,
                model=fields.get("model") or None,
                backend_id=fields.get("agent") or None,
                enabled=False,
            )

        # name
        filename = Path(path).stem
        name = fields.get("name") or filename

        # timezone
        tz_str = fields.get("timezone") or "UTC"
        try:
            tz = ZoneInfo(tz_str)
        except ZoneInfoNotFoundError:
            logger.warning(f"Task file {path}: unknown timezone {tz_str!r}, skipping.")
            if harness_sched_task_parse_errors_total is not None:
                harness_sched_task_parse_errors_total.inc()
            return None

        # days
        days_raw = str(fields.get("days") or "*").strip()
        days_expr = _translate_day_abbrevs(days_raw)
        if not croniter.is_valid(f"0 0 * * {days_expr}"):
            logger.warning(f"Task file {path}: invalid days expression {days_raw!r}, skipping.")
            if harness_sched_task_parse_errors_total is not None:
                harness_sched_task_parse_errors_total.inc()
            return None

        # window-start (optional — omitting enables run-once mode)
        ws_raw = fields.get("window-start") or fields.get("window_start")
        window_start: dtime | None = None
        if ws_raw:
            try:
                h, m = str(ws_raw).split(":")
                window_start = dtime(int(h), int(m), tzinfo=None)
            except Exception:
                logger.warning(f"Task file {path}: invalid 'window-start' {ws_raw!r}, skipping.")
                if harness_sched_task_parse_errors_total is not None:
                    harness_sched_task_parse_errors_total.inc()
                return None

        # window-duration
        wd_raw = fields.get("window-duration") or fields.get("window_duration")
        window_end: dtime | None = None

        if wd_raw:
            if window_start is None:
                logger.warning(f"Task file {path}: 'window-duration' requires 'window-start' — skipping.")
                if harness_sched_task_parse_errors_total is not None:
                    harness_sched_task_parse_errors_total.inc()
                return None
            try:
                duration_secs = parse_duration(str(wd_raw))
            except ValueError as e:
                logger.warning(f"Task file {path}: invalid 'window-duration': {e}, skipping.")
                if harness_sched_task_parse_errors_total is not None:
                    harness_sched_task_parse_errors_total.inc()
                return None
            # #1305: combine tz-aware so DST transitions don't silently
            # skew the derived window_end. If the operator zone transitions
            # forward between window_start and window_start+duration, the
            # resulting wall-clock end shifts by the DST offset; that is
            # the correct observable wall-clock, not the naive one.
            _ref_date = datetime.now(tz).date()
            ws_dt = datetime.combine(_ref_date, window_start).replace(tzinfo=tz)
            we_dt = ws_dt + timedelta(seconds=duration_secs)
            window_end = we_dt.timetz().replace(tzinfo=None)

        # loop
        loop = str(fields.get("loop", "false")).lower() not in ("false", "")
        if loop and window_end is None:
            logger.warning(f"Task file {path}: 'loop: true' requires 'window-duration' — disabling loop.")
            loop = False

        # loop-gap
        loop_gap: float | None = None
        lg_raw = fields.get("loop-gap") or fields.get("loop_gap")
        if lg_raw:
            try:
                loop_gap = parse_duration(str(lg_raw))
            except ValueError as e:
                logger.warning(f"Task file {path}: invalid 'loop-gap': {e}, ignoring.")

        # done-when
        done_when = fields.get("done-when") or fields.get("done_when") or None

        # start / end dates
        start: date | None = None
        end: date | None = None
        start_raw = fields.get("start")
        end_raw = fields.get("end")
        if start_raw:
            try:
                start = date.fromisoformat(str(start_raw))
            except ValueError:
                logger.warning(f"Task file {path}: invalid 'start' date {start_raw!r}, ignoring.")
        if end_raw:
            try:
                end = date.fromisoformat(str(end_raw))
            except ValueError:
                logger.warning(f"Task file {path}: invalid 'end' date {end_raw!r}, ignoring.")

        # model
        model = fields.get("model") or None

        # backend
        backend_id = fields.get("agent") or None

        # consensus
        consensus = parse_consensus(raw_fields.get("consensus"))

        # max_tokens
        max_tokens: int | None = None
        max_tokens_raw = fields.get("max-tokens") or fields.get("max_tokens")
        if max_tokens_raw is not None:
            try:
                max_tokens = max(1, int(max_tokens_raw))
            except (ValueError, TypeError):
                logger.warning(f"Task file {path}: invalid 'max-tokens' value {max_tokens_raw!r}, ignoring.")

        return TaskItem(
            path=path,
            name=name,
            days_expr=days_expr,
            tz=tz,
            window_start=window_start,
            window_end=window_end,
            loop=loop,
            loop_gap=loop_gap,
            done_when=done_when,
            content=content,
            start=start,
            end=end,
            model=model,
            backend_id=backend_id,
            consensus=consensus,
            max_tokens=max_tokens,
        )

    except Exception as e:
        if harness_sched_task_parse_errors_total is not None:
            harness_sched_task_parse_errors_total.inc()
        logger.error(f"Task file {path}: failed to parse — {e}, skipping.")
        return None


def _day_session_id(item: TaskItem, today: date) -> str:
    """Deterministic session ID scoped to agent + task filename + calendar date."""
    filename = Path(item.path).stem
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"{AGENT_NAME}.{filename}.{today.isoformat()}"))


def _day_matches(item: TaskItem, d: date) -> bool:
    """Return True if date d matches the task's days expression."""
    # croniter interprets weekday 0 as Sunday; Python's weekday() is 0=Monday.
    # Use a dummy cron expression pinned to 00:00 on day d and check if it fires.
    cron_expr = f"0 0 * * {item.days_expr}"
    # Check whether croniter considers midnight on d as a match by finding the
    # previous fire before d+1 and seeing if it equals d.
    dt_start = datetime.combine(d, dtime(0, 0))
    c = croniter(cron_expr, dt_start - timedelta(seconds=1))
    next_fire = c.get_next(datetime)
    return next_fire.date() == d


def _now_in_tz(tz: ZoneInfo) -> datetime:
    return datetime.now(tz)


def _next_window_open(item: TaskItem, after: datetime) -> datetime | None:
    """Return the next datetime (in item.tz) when the task window opens, starting after `after`.

    Returns None if no future eligible day exists, or if the item has no window_start (run-once mode).
    """
    if item.window_start is None:
        return None
    tz = item.tz
    # Normalise `after` to item's timezone
    after_local = after.astimezone(tz)
    # Start checking from the next minute to avoid re-triggering the same moment
    check_date = after_local.date()
    # Determine whether to advance past today's window_start.
    # For midnight-spanning windows (window_end < window_start), times in the
    # early-morning portion (t < window_end) belong to the window that opened
    # the previous evening — window_start for *today* has not yet arrived, but
    # the window is still active.  In this case we should advance check_date by
    # 1 so that we schedule the next opening (tonight) rather than returning
    # today's window_start (which is in the future but after the active window
    # has already closed at window_end).
    _t = after_local.time()
    _midnight_spanning = item.window_end is not None and item.window_end < item.window_start
    if _midnight_spanning and _t < item.window_end:
        # We are inside the early-morning tail of yesterday's window.
        # Advance past today so we find tonight's (or a future) window_start.
        check_date += timedelta(days=1)
    elif _t >= item.window_start:
        # We are past window_start for today (normal case).
        check_date += timedelta(days=1)

    # #1306: derive the iteration cap from item.end when bounded so
    # sparse-day tasks with a far-future end don't silently expire at
    # 2 years. Cap at 10 years by default (generous upper bound that
    # still protects against runaway loops on unbounded sparse-day
    # expressions).
    _max_iters = (item.end - check_date).days + 2 if item.end else 366 * 10
    for _ in range(_max_iters):
        if item.start and check_date < item.start:
            check_date += timedelta(days=1)
            continue
        if item.end and check_date > item.end:
            return None
        if _day_matches(item, check_date):
            return datetime.combine(check_date, item.window_start, tzinfo=tz)
        check_date += timedelta(days=1)

    return None


def _inside_window(item: TaskItem) -> bool:
    """Return True if current time is inside the task's daily window on an eligible day."""
    now = _now_in_tz(item.tz)
    today = now.date()
    if item.start and today < item.start:
        return False
    if item.end and today > item.end:
        return False
    if not _day_matches(item, today):
        return False
    t = now.time()
    if item.window_end and item.window_end < item.window_start:
        # Midnight-spanning window: open if time >= start OR time < end
        if not (t >= item.window_start or t < item.window_end):
            return False
    else:
        if t < item.window_start:
            return False
        if item.window_end and t >= item.window_end:
            return False
    return True


async def run_task(
    item: TaskItem,
    bus: MessageBus,
    semaphore: asyncio.Semaphore | None = None,
    backends_ready: asyncio.Event | None = None,
) -> None:
    """Drive a single task's lifecycle on the harness event loop.

    Waits for ``backends_ready`` before doing anything so a task whose
    window opens during early startup doesn't fire against a backend
    pool that isn't yet ready.

    When ``item.window_start is None`` the task runs once
    immediately and returns; otherwise it loops indefinitely,
    sleeping until the next window opens (computed by
    ``_next_window_open`` honouring ``item.start``/``item.end`` and
    ``item.days_expr``), firing inside the window, and — when
    ``item.loop`` is set — re-firing with ``loop_gap`` spacing until
    ``window_end`` is reached or ``done_when`` matches the response.

    Each fire writes a ``.running.json`` checkpoint under
    ``CHECKPOINT_DIR`` so the next ``_scan`` after an unclean restart
    can detect the interrupted run and emit
    ``harness_sched_task_checkpoint_stale_total``. The optional
    ``semaphore`` (the runner's process-wide
    ``TASKS_MAX_CONCURRENT`` gate) is acquired around each fire so
    concurrent task items cannot exceed the cap.
    """
    if backends_ready is not None:
        await backends_ready.wait()

    filename = Path(item.path).stem
    checkpoint_path = os.path.join(CHECKPOINT_DIR, filename + ".running.json")

    # --- Run-once mode: no window-start, fire immediately and exit ---
    if item.window_start is None:
        logger.info(f"Task '{item.name}' run-once: firing immediately.")
        _semaphore_acquired = False
        if semaphore is not None:
            await semaphore.acquire()
            _semaphore_acquired = True
        item.running = True
        if harness_sched_task_running_items is not None:
            harness_sched_task_running_items.inc()
        session_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{AGENT_NAME}.{filename}"))

        # Write checkpoint so an interrupted run can be detected on restart
        try:
            os.makedirs(CHECKPOINT_DIR, exist_ok=True)
            with open(checkpoint_path, "w") as f:
                json.dump(
                    {
                        "started_at": datetime.now(timezone.utc).isoformat(),
                        "name": item.name,
                        "session_id": session_id,
                    },
                    f,
                )
        except Exception as e:
            if harness_checkpoint_write_errors_total is not None:
                harness_checkpoint_write_errors_total.inc()
            logger.error(f"Task '{item.name}' checkpoint write failed: {e}")

        # Initialise before the try so the except branch can always reference
        # it, even when resolve_prompt_env (or any earlier statement) raises
        # before the in-body assignment is reached (#658, mirrors #657).
        _task_start = time.monotonic()
        # #1571: initialise before the try so the CancelledError branch can
        # reference _send_task even when resolve_prompt_env (or any earlier
        # statement) raises before the asyncio.ensure_future assignment.
        _send_task: asyncio.Task | None = None
        try:
            from prompt_env import resolve_prompt_env  # noqa: E402 — scoped import keeps startup simple

            # #1675: do NOT reassign _task_start here. The pre-try
            # initialisation at the top of this branch is the correct
            # baseline — the duration metric must cover resolve_prompt_env
            # + bus.send symmetrically on both success and error paths
            # (matches the post-#1322 jobs.py pattern; tasks.py was
            # missed at that time).
            prompt = resolve_prompt_env(f"Task: {item.name}\n\n{item.content}")
            _fire_ts = time.time()
            item.last_fire = _fire_ts  # #1087
            if harness_sched_task_item_last_run_timestamp_seconds is not None:
                harness_sched_task_item_last_run_timestamp_seconds.labels(name=item.name).set(_fire_ts)
            message = Message(
                prompt=prompt,
                session_id=session_id,
                kind=f"task:{item.name}",
                model=item.model,
                backend_id=item.backend_id,
                consensus=item.consensus,
                max_tokens=item.max_tokens,
            )
            _send_task = asyncio.ensure_future(bus.send(message))

            def _log_send_result(t: asyncio.Task, _name: str = item.name) -> None:
                exc = t.exception() if not t.cancelled() else None
                if exc is not None:
                    logger.error(f"Task '{_name}' background bus.send failed: {exc}")

            _send_task.add_done_callback(_log_send_result)
            await asyncio.shield(_send_task)
            if harness_sched_task_duration_seconds is not None:
                harness_sched_task_duration_seconds.labels(name=item.name).observe(time.monotonic() - _task_start)
            if harness_sched_task_runs_total is not None:
                harness_sched_task_runs_total.labels(name=item.name, status="success").inc()
            _success_ts = time.time()
            item.last_success = _success_ts  # #1087
            if harness_sched_task_item_last_success_timestamp_seconds is not None:
                harness_sched_task_item_last_success_timestamp_seconds.labels(name=item.name).set(_success_ts)
        except asyncio.CancelledError:
            # #1307: mirror the loop-path drain (#1274) on run-once so the
            # in-flight bus.send doesn't leak past cleanup.
            if _send_task is not None and not _send_task.done():
                _drain_timeout = float(os.environ.get("TASKS_SHUTDOWN_DRAIN_TIMEOUT", "5"))
                try:
                    await asyncio.wait_for(
                        asyncio.gather(_send_task, return_exceptions=True),
                        timeout=_drain_timeout,
                    )
                except asyncio.TimeoutError:
                    logger.warning(
                        f"Task '{item.name}' run-once drain timed out after "
                        f"{_drain_timeout}s — abandoning in-flight send."
                    )
                    if not _send_task.done():
                        _send_task.cancel()
            raise
        except Exception as e:
            logger.error(f"Task '{item.name}' error: {e}")
            if harness_sched_task_runs_total is not None:
                harness_sched_task_runs_total.labels(name=item.name, status="error").inc()
            if harness_sched_task_error_duration_seconds is not None:
                harness_sched_task_error_duration_seconds.labels(name=item.name).observe(time.monotonic() - _task_start)
            if harness_sched_task_item_last_error_timestamp_seconds is not None:
                harness_sched_task_item_last_error_timestamp_seconds.labels(name=item.name).set(time.time())
            try:
                get_event_stream().publish(
                    "task.fired",
                    {
                        "name": item.name,
                        "duration_ms": int((time.monotonic() - _task_start) * 1000),
                        "outcome": "error",
                        "error": repr(e)[:512],
                    },
                    agent_id=AGENT_NAME,
                )
            except Exception:  # pragma: no cover
                pass
        else:
            try:
                get_event_stream().publish(
                    "task.fired",
                    {
                        "name": item.name,
                        "duration_ms": int((time.monotonic() - _task_start) * 1000),
                        "outcome": "success",
                    },
                    agent_id=AGENT_NAME,
                )
            except Exception:  # pragma: no cover
                pass
        finally:
            item.running = False
            if harness_sched_task_running_items is not None:
                harness_sched_task_running_items.dec()
            if semaphore is not None and _semaphore_acquired:
                semaphore.release()
            try:
                os.remove(checkpoint_path)
            except FileNotFoundError:
                pass
            except Exception as e:
                logger.warning(f"Task '{item.name}' checkpoint cleanup failed: {e}")
        return

    # --- Startup: handle restart-within-window logic ---
    stale_checkpoint = os.path.exists(checkpoint_path)
    if stale_checkpoint:
        if _inside_window(item):
            logger.info(f"Task '{item.name}': stale checkpoint found — firing immediately (interrupted run).")
            if harness_sched_task_checkpoint_stale_total is not None:
                harness_sched_task_checkpoint_stale_total.inc()
            # Remove checkpoint before re-firing so we don't double-count on next restart
            try:
                os.remove(checkpoint_path)
            except Exception:
                pass
            # Fall through to fire immediately below by setting a flag
            _fire_now = True
        else:
            logger.warning(
                f"Task '{item.name}': stale checkpoint found but outside window — removing and skipping to next window."
            )
            if harness_sched_task_checkpoint_stale_total is not None:
                harness_sched_task_checkpoint_stale_total.inc()
            try:
                os.remove(checkpoint_path)
            except Exception:
                pass
            _fire_now = False
    else:
        # No checkpoint and currently inside window: fire immediately. We cannot
        # assume the task already ran cleanly — no checkpoint means it either
        # hasn't run yet today, or ran on a prior deployment that didn't write a
        # checkpoint. Firing is the safe default; idempotent tasks handle this
        # correctly, and skipping silently causes missed runs on restart.
        if _inside_window(item):
            logger.info(f"Task '{item.name}': no checkpoint found inside window — firing immediately.")
            _fire_now = True
        else:
            _fire_now = False

    while True:
        if not _fire_now:
            now = _now_in_tz(item.tz)
            next_open = _next_window_open(item, now)
            if next_open is None:
                logger.info(f"Task '{item.name}': no future eligible days — task has expired.")
                return
            delay = (next_open.astimezone(timezone.utc) - datetime.now(timezone.utc)).total_seconds()
            logger.info(f"Task '{item.name}' next window opens in {delay:.0f}s at {next_open.isoformat()}")
            await asyncio.sleep(max(delay, 0))

        _fire_now = False  # only fires once from restart path

        # Skip if already running
        if item.running:
            logger.warning(f"Task '{item.name}' still running from previous window, skipping.")
            if harness_sched_task_skips_total is not None:
                harness_sched_task_skips_total.labels(name=item.name).inc()
            continue

        _semaphore_acquired = False
        if semaphore is not None:
            await semaphore.acquire()
            _semaphore_acquired = True

        item.running = True
        if harness_sched_task_running_items is not None:
            harness_sched_task_running_items.inc()

        # Generate per-day session ID
        today = _now_in_tz(item.tz).date()
        session_id = _day_session_id(item, today)

        # Write checkpoint
        try:
            os.makedirs(CHECKPOINT_DIR, exist_ok=True)
            with open(checkpoint_path, "w") as f:
                json.dump(
                    {
                        "started_at": datetime.now(timezone.utc).isoformat(),
                        "name": item.name,
                        "session_id": session_id,
                    },
                    f,
                )
        except Exception as e:
            if harness_checkpoint_write_errors_total is not None:
                harness_checkpoint_write_errors_total.inc()
            logger.error(f"Task '{item.name}' checkpoint write failed: {e}")

        try:
            while True:
                # Record lag
                scheduled_open = datetime.combine(_now_in_tz(item.tz).date(), item.window_start, tzinfo=item.tz)
                lag = max(0.0, (_now_in_tz(item.tz) - scheduled_open).total_seconds())
                if harness_sched_task_lag_seconds is not None:
                    harness_sched_task_lag_seconds.observe(lag)
                _fire_ts = time.time()
                item.last_fire = _fire_ts  # #1087
                if harness_sched_task_item_last_run_timestamp_seconds is not None:
                    harness_sched_task_item_last_run_timestamp_seconds.labels(name=item.name).set(_fire_ts)

                from prompt_env import resolve_prompt_env  # noqa: E402 — scoped import keeps startup simple

                prompt = resolve_prompt_env(f"Task: {item.name}\n\n{item.content}")
                logger.info(f"Task '{item.name}' firing (session={session_id}).")
                _task_start = time.monotonic()

                _send_task: asyncio.Task | None = None
                response = ""
                try:
                    message = Message(
                        prompt=prompt,
                        session_id=session_id,
                        kind=f"task:{item.name}",
                        model=item.model,
                        backend_id=item.backend_id,
                        consensus=item.consensus,
                        max_tokens=item.max_tokens,
                    )
                    _send_task = asyncio.ensure_future(bus.send(message))

                    def _log_send_result(t: asyncio.Task, _name: str = item.name) -> None:
                        exc = t.exception() if not t.cancelled() else None
                        if exc is not None:
                            logger.error(f"Task '{_name}' background bus.send failed: {exc}")

                    _send_task.add_done_callback(_log_send_result)
                    response = await asyncio.shield(_send_task)

                    if harness_sched_task_duration_seconds is not None:
                        harness_sched_task_duration_seconds.labels(name=item.name).observe(
                            time.monotonic() - _task_start
                        )
                    if harness_sched_task_runs_total is not None:
                        harness_sched_task_runs_total.labels(name=item.name, status="success").inc()
                    _success_ts = time.time()
                    item.last_success = _success_ts  # #1087
                    if harness_sched_task_item_last_success_timestamp_seconds is not None:
                        harness_sched_task_item_last_success_timestamp_seconds.labels(name=item.name).set(_success_ts)
                    try:
                        _window_str = (
                            f"{item.window_start.strftime('%H:%M')}-{item.window_end.strftime('%H:%M')}"
                            if item.window_start and item.window_end
                            else ""
                        )
                        _p: dict = {
                            "name": item.name,
                            "duration_ms": int((time.monotonic() - _task_start) * 1000),
                            "outcome": "success",
                        }
                        if _window_str:
                            _p["window"] = _window_str
                        get_event_stream().publish("task.fired", _p, agent_id=AGENT_NAME)
                    except Exception:  # pragma: no cover
                        pass

                except asyncio.CancelledError:
                    if _send_task is not None and not _send_task.done():
                        # #1274: bounded drain, prevents SIGTERM hang.
                        _drain_timeout = float(os.environ.get("TASKS_SHUTDOWN_DRAIN_TIMEOUT", "5"))
                        logger.info(
                            f"Task '{item.name}' cancelled — awaiting in-flight bus.send (up to {_drain_timeout}s)."
                        )
                        try:
                            await asyncio.wait_for(
                                asyncio.gather(_send_task, return_exceptions=True),
                                timeout=_drain_timeout,
                            )
                        except asyncio.TimeoutError:
                            logger.warning(
                                f"Task '{item.name}' drain timed out after "
                                f"{_drain_timeout}s — abandoning in-flight send."
                            )
                            if _send_task is not None and not _send_task.done():
                                _send_task.cancel()
                    raise
                except Exception as e:
                    logger.error(f"Task '{item.name}' error: {e}")
                    if harness_sched_task_runs_total is not None:
                        harness_sched_task_runs_total.labels(name=item.name, status="error").inc()
                    if harness_sched_task_error_duration_seconds is not None:
                        harness_sched_task_error_duration_seconds.labels(name=item.name).observe(
                            time.monotonic() - _task_start
                        )
                    if harness_sched_task_item_last_error_timestamp_seconds is not None:
                        harness_sched_task_item_last_error_timestamp_seconds.labels(name=item.name).set(time.time())
                    try:
                        _window_str = (
                            f"{item.window_start.strftime('%H:%M')}-{item.window_end.strftime('%H:%M')}"
                            if item.window_start and item.window_end
                            else ""
                        )
                        _p2: dict = {
                            "name": item.name,
                            "duration_ms": int((time.monotonic() - _task_start) * 1000),
                            "outcome": "error",
                            "error": repr(e)[:512],
                        }
                        if _window_str:
                            _p2["window"] = _window_str
                        get_event_stream().publish("task.fired", _p2, agent_id=AGENT_NAME)
                    except Exception:  # pragma: no cover
                        pass
                    break  # stop looping on error; go back to waiting for next window

                # Loop logic
                if not item.loop or item.window_end is None:
                    break  # fire once, then wait for next window

                # done-when check
                if item.done_when and response and item.done_when in response:
                    logger.info(f"Task '{item.name}': done-when signal received — stopping for the day.")
                    break

                # window boundary check
                if not _inside_window(item):
                    logger.info(f"Task '{item.name}': window closed — stopping for the day.")
                    break

                # loop-gap
                if item.loop_gap:
                    logger.info(f"Task '{item.name}': waiting {item.loop_gap:.0f}s before next iteration.")
                    await asyncio.sleep(item.loop_gap)

                # Re-check window after gap (gap may have pushed past window_end)
                if item.window_end and not _inside_window(item):
                    logger.info(f"Task '{item.name}': window closed after loop-gap — stopping for the day.")
                    break

                # Refresh session ID in case day rolled over during a long gap
                new_today = _now_in_tz(item.tz).date()
                if new_today != today:
                    today = new_today
                    session_id = _day_session_id(item, today)

        except asyncio.CancelledError:
            raise
        finally:
            item.running = False
            if harness_sched_task_running_items is not None:
                harness_sched_task_running_items.dec()
            if semaphore is not None and _semaphore_acquired:
                semaphore.release()
            try:
                os.remove(checkpoint_path)
            except FileNotFoundError:
                pass
            except Exception as e:
                if harness_checkpoint_write_errors_total is not None:
                    harness_checkpoint_write_errors_total.inc()
                logger.warning(f"Task '{item.name}' checkpoint delete failed: {e}")


class TaskRunner:
    """File-watching registry that schedules ``TaskItem``s parsed from
    ``TASKS_DIR``.

    Each registered enabled item gets a long-running asyncio task
    executing ``run_task``; disabled items stay in ``_items`` for
    dashboard listing but are not scheduled. When
    ``TASKS_MAX_CONCURRENT`` > 0 a process-wide ``asyncio.Semaphore``
    caps the number of simultaneously executing task fires.
    Registration/unregistration are driven by ``run()`` watching the
    tasks directory and re-parsing on change; parse errors preserve
    the last-known-good registration so transient syntax issues do not
    drop a healthy schedule.
    """

    def __init__(self, bus: MessageBus, backends_ready: asyncio.Event | None = None):
        self._bus = bus
        self._backends_ready = backends_ready
        self._items: dict[str, TaskItem] = {}
        self._semaphore: asyncio.Semaphore | None = (
            asyncio.Semaphore(_TASKS_MAX_CONCURRENT) if _TASKS_MAX_CONCURRENT > 0 else None
        )
        if self._semaphore is not None:
            logger.info(f"Task concurrency limit: {_TASKS_MAX_CONCURRENT} concurrent items")

    async def _register(self, path: str) -> None:
        result = parse_task_file(path)
        if result is None:
            # Parse error — preserve last-known-good.
            return
        item = result
        cancelled = self._unregister(path)
        if cancelled is not None:
            await asyncio.gather(cancelled, return_exceptions=True)

        # Disabled: listed for dashboard visibility, no schedule armed.
        if not item.enabled:
            self._items[path] = item
            if harness_sched_task_items_registered is not None:
                harness_sched_task_items_registered.set(sum(1 for i in self._items.values() if i.enabled))
            return

        task = asyncio.create_task(run_task(item, self._bus, self._semaphore, self._backends_ready))

        def _task_done_callback(t: asyncio.Task, _name: str = item.name) -> None:
            if not t.cancelled() and t.exception() is not None:
                logger.error(f"Task '{_name}' coroutine crashed: {t.exception()!r}")
                if harness_sched_task_runs_total is not None:
                    harness_sched_task_runs_total.labels(name=_name, status="error").inc()

        task.add_done_callback(_task_done_callback)
        item.task = task
        self._items[path] = item
        if harness_sched_task_items_registered is not None:
            harness_sched_task_items_registered.set(sum(1 for i in self._items.values() if i.enabled))
        if item.window_start is not None:
            logger.info(f"Task '{item.name}' registered. Window: {item.window_start.strftime('%H:%M')}")
        else:
            logger.info(f"Task '{item.name}' registered. Mode: run-once")

    def items(self) -> list[dict]:
        """Return a serializable snapshot of currently registered task items."""
        result = []
        for item in self._items.values():
            result.append(
                {
                    "name": item.name,
                    "days_expr": item.days_expr,
                    "timezone": str(item.tz),
                    "window_start": item.window_start.strftime("%H:%M") if item.window_start else None,
                    "window_end": item.window_end.strftime("%H:%M") if item.window_end else None,
                    "loop": item.loop,
                    "session_id": None,  # per-day session IDs are generated at runtime
                    "backend_id": item.backend_id,
                    "model": item.model,
                    "consensus": [asdict(e) for e in item.consensus],
                    "max_tokens": item.max_tokens,
                    "start": item.start.isoformat() if item.start else None,
                    "end": item.end.isoformat() if item.end else None,
                    "running": item.running,
                    "enabled": item.enabled,
                    # #1087 — fire bookkeeping (epoch seconds, None when
                    # never computed / fired).
                    "next_fire": item.next_fire,
                    "last_fire": item.last_fire,
                    "last_success": item.last_success,
                }
            )
        return result

    def _unregister(self, path: str) -> asyncio.Task | None:
        existing = self._items.pop(path, None)
        if existing and existing.task:
            if existing.running:
                logger.info(f"Task '{existing.name}' unregistered — cancelling while run is in progress.")
            else:
                logger.info(f"Task '{existing.name}' unregistered.")
            existing.task.cancel()
            if harness_sched_task_items_registered is not None:
                harness_sched_task_items_registered.set(sum(1 for i in self._items.values() if i.enabled))
            return existing.task
        if harness_sched_task_items_registered is not None:
            harness_sched_task_items_registered.set(sum(1 for i in self._items.values() if i.enabled))
        return None

    async def _scan(self) -> None:
        if os.path.isdir(CHECKPOINT_DIR):
            try:
                cp_filenames = os.listdir(CHECKPOINT_DIR)
            except OSError:
                cp_filenames = []
            for cp_filename in cp_filenames:
                if cp_filename.endswith(".running.json"):
                    cp_path = os.path.join(CHECKPOINT_DIR, cp_filename)
                    try:
                        with open(cp_path) as f:
                            data = json.load(f)
                        name = data.get("name") or Path(cp_filename).stem
                    except Exception:
                        name = Path(cp_filename).stem
                    logger.warning(f"Task '{name}': stale checkpoint at {cp_path} — run may have been interrupted")
                    if harness_sched_task_checkpoint_stale_total is not None:
                        harness_sched_task_checkpoint_stale_total.inc()
                    try:
                        os.remove(cp_path)
                    except Exception as rm_err:
                        logger.warning(f"Task '{name}': failed to remove stale checkpoint {cp_path}: {rm_err}")
        if not os.path.isdir(TASKS_DIR):
            return
        try:
            task_files = os.listdir(TASKS_DIR)
        except OSError:
            return
        for filename in task_files:
            if filename.endswith(".md"):
                await self._register(os.path.join(TASKS_DIR, filename))

    async def run(self) -> None:
        """Run the task watcher loop forever.

        Performs an initial ``_scan`` of ``TASKS_DIR`` (which also
        sweeps any stale ``.running.json`` checkpoints left behind by
        an interrupted previous process) and then drives ``awatch``
        via ``run_awatch_loop`` so create/modify events call
        ``_register`` and delete events call ``_unregister``,
        incrementing ``harness_sched_task_reloads_total`` on each.
        The loop self-restarts on watcher exit and survives directory
        deletion by retrying every 10s.
        """
        logger.info(f"Task runner watching {TASKS_DIR}")

        async def _on_change(path: str) -> None:
            logger.info(f"Task file changed: {path}")
            if harness_sched_task_reloads_total is not None:
                harness_sched_task_reloads_total.inc()
            await self._register(path)

        def _on_delete(path: str) -> None:
            logger.info(f"Task file removed: {path}")
            if harness_sched_task_reloads_total is not None:
                harness_sched_task_reloads_total.inc()
            self._unregister(path)

        async def _cleanup() -> None:
            cancelled = [t for path in list(self._items.keys()) if (t := self._unregister(path)) is not None]
            if cancelled:
                await asyncio.gather(*cancelled, return_exceptions=True)

        await run_awatch_loop(
            directory=TASKS_DIR,
            watcher_name="tasks",
            scan=self._scan,
            on_change=_on_change,
            on_delete=_on_delete,
            cleanup=_cleanup,
            logger_=logger,
            not_found_message="Tasks directory not found — retrying in 10s.",
            watcher_exited_message="Tasks directory watcher exited — directory deleted or unavailable. Retrying in 10s.",  # noqa: E501
            watcher_events_metric=harness_watcher_events_total,
            file_watcher_restarts_metric=harness_file_watcher_restarts_total,
        )
