import asyncio
import logging
import os
import time
import uuid
from datetime import datetime, timezone

from bus import Message, MessageBus
from croniter import croniter
from events import get_event_stream
from metrics import (
    harness_file_watcher_restarts_total,
    harness_heartbeat_duration_seconds,
    harness_heartbeat_error_duration_seconds,
    harness_heartbeat_lag_seconds,
    harness_heartbeat_last_error_timestamp_seconds,
    harness_heartbeat_last_run_timestamp_seconds,
    harness_heartbeat_last_success_timestamp_seconds,
    harness_heartbeat_load_errors_total,
    harness_heartbeat_reloads_total,
    harness_heartbeat_runs_total,
    harness_heartbeat_skips_total,
    harness_watcher_events_total,
)
from utils import ConsensusEntry, parse_consensus, parse_frontmatter, parse_frontmatter_raw
from watchfiles import awatch

logger = logging.getLogger(__name__)

HEARTBEAT_PATH = os.environ.get("HEARTBEAT_PATH", "/home/agent/.witwave/HEARTBEAT.md")
DEFAULT_SCHEDULE = "*/30 * * * *"
HEARTBEAT_DIR = os.path.dirname(HEARTBEAT_PATH)
AGENT_NAME = os.environ.get("AGENT_NAME", "witwave")
# #1390: deterministic uuid5 derived from AGENT_NAME. In multi-tenant
# deployments where agent names are public (visible via /agents or
# k8s manifest), SESSION_ID_SECRET must be set so downstream
# session-id binding (shared/session_binding.py) HMAC-derives the
# effective id per caller. Without a secret the legacy uuid5 path is
# used and knowing the agent name is sufficient to address the
# heartbeat session. Document: AGENTS.md §session-binding.
HEARTBEAT_SESSION = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{AGENT_NAME}.heartbeat"))
# Bounded wait for an in-flight heartbeat to wind down after stop_event fires
# (#492). The loop also races message.result against stop_event internally, so
# this outer bound only acts as a safety net against an unresponsive backend
# that ignores future cancellation.
HEARTBEAT_STOP_JOIN_TIMEOUT = float(os.environ.get("HEARTBEAT_STOP_JOIN_TIMEOUT", "5.0"))
# Sentinel token: heartbeat prompts should include this string to suppress response logging.
HEARTBEAT_OK = "HEARTBEAT_OK"

# Fire bookkeeping for /heartbeat snapshot responses (#1087). Epoch
# seconds; None until populated by the first iteration / fire / success
# respectively. These live at module level because the runner itself
# is module-level (no runner instance to hang state off).
next_fire: float | None = None
last_fire: float | None = None
last_success: float | None = None


def snapshot() -> dict[str, float | None]:
    """Return the current fire-schedule bookkeeping dict (#1087).

    Intended for the /heartbeat HTTP handler to fold into its JSON
    response.  Read-only snapshot — callers must not mutate.
    """
    return {
        "next_fire": next_fire,
        "last_fire": last_fire,
        "last_success": last_success,
    }


def load_heartbeat() -> tuple[str, str, str | None, str | None, list[ConsensusEntry], int | None] | None:
    """Load and parse the HEARTBEAT.md singleton.

    Returns ``None`` when HEARTBEAT_PATH is missing, the file disables
    itself via ``enabled: false`` in frontmatter, or has empty body
    content. Otherwise returns a tuple of ``(schedule, content, model,
    backend_id, consensus, max_tokens)``:

    - ``schedule`` falls back to DEFAULT_SCHEDULE when the frontmatter
      value is absent or fails ``croniter.is_valid``; the failure is
      logged at WARNING.
    - ``model`` / ``backend_id`` are taken verbatim from frontmatter
      and may be None.
    - ``consensus`` is parsed via ``parse_consensus`` from the raw
      frontmatter so list-shaped values survive YAML scalar coercion.
    - ``max_tokens`` accepts ``max-tokens`` or ``max_tokens``, is
      clamped to ``>= 1``, and stays None when the value is missing or
      fails int conversion (the parse failure is logged at WARNING).
    """
    if not os.path.exists(HEARTBEAT_PATH):
        return None
    with open(HEARTBEAT_PATH) as f:
        raw = f.read()

    schedule = DEFAULT_SCHEDULE
    enabled = True

    fields, content = parse_frontmatter(raw)
    raw_fields, _ = parse_frontmatter_raw(raw)
    if "schedule" in fields:
        schedule = fields["schedule"]
    if "enabled" in fields:
        enabled = str(fields["enabled"]).lower() not in ("false", "no", "off", "n", "0", "")
    model = fields.get("model") or None
    backend_id = fields.get("agent") or None
    consensus = parse_consensus(raw_fields.get("consensus"))
    max_tokens: int | None = None
    max_tokens_raw = fields.get("max-tokens") or fields.get("max_tokens")
    if max_tokens_raw is not None:
        try:
            max_tokens = max(1, int(max_tokens_raw))
        except (ValueError, TypeError):
            logger.warning(f"HEARTBEAT.md: invalid 'max-tokens' value {max_tokens_raw!r}, ignoring.")

    if not enabled:
        return None

    if not content:
        return None

    if not croniter.is_valid(schedule):
        logger.warning(f"HEARTBEAT.md has invalid cron expression '{schedule}', using default.")
        schedule = DEFAULT_SCHEDULE

    return schedule, content, model, backend_id, consensus, max_tokens


async def _run_loop(
    bus: MessageBus,
    schedule: str,
    content: str,
    stop_event: asyncio.Event,
    model: str | None = None,
    backend_id: str | None = None,
    consensus: list[ConsensusEntry] | None = None,
    max_tokens: int | None = None,
    backends_ready: asyncio.Event | None = None,
) -> None:
    # Track the most recent scheduled tick so iterations can anchor the
    # croniter forward rather than relying on a persistent cursor that
    # drifts behind wall-clock under long reloads, suspended laptops, or
    # NTP step adjustments (#659). Initialised to None; the first
    # iteration anchors at wall-clock.
    last_scheduled: datetime | None = None

    # #1584: recreate stop_waiter each iteration so a transient
    # set/clear on stop_event (e.g. reload paths that clear mid-tick)
    # can't leave a stale `done` task that short-circuits every
    # subsequent asyncio.wait back to FIRST_COMPLETED immediately.
    def _make_stop_waiter() -> asyncio.Task:
        if stop_event.is_set():
            # Already set — create a task that completes immediately so
            # callers still get a valid Task to pass to asyncio.wait.
            async def _noop() -> None:
                return None

            return asyncio.create_task(_noop())
        return asyncio.create_task(stop_event.wait())

    stop_waiter = _make_stop_waiter()
    # Wait for backends to pass /health before firing the first heartbeat
    # (#785). Without this, a */1 schedule would dispatch at *:00 while
    # the backend container was still warming up and the prompt would
    # 503 before any work could start.
    if backends_ready is not None and not backends_ready.is_set():
        logger.info("Heartbeat waiting for backends_ready before first run.")
        _ready_waiter = asyncio.create_task(backends_ready.wait())
        done, _pending = await asyncio.wait(
            {_ready_waiter, stop_waiter},
            return_when=asyncio.FIRST_COMPLETED,
        )
        if stop_waiter in done:
            _ready_waiter.cancel()
            return
    try:
        while not stop_event.is_set():
            # #1584: ensure stop_waiter reflects the *current* stop_event
            # state at the top of each iteration. If a prior external
            # clear() left the task done, recreate it.
            if stop_waiter.done():
                stop_waiter = _make_stop_waiter()
            now = datetime.now(timezone.utc)
            # Anchor cron from max(now, last_scheduled) every iteration so
            # cumulative drift (overrunning backend calls, reload-error
            # `continue`s, system suspend/resume) cannot push subsequent
            # ticks behind wall-clock — while `last_scheduled` prevents the
            # same tick from firing twice if wall-clock skews backwards by
            # a small amount between iterations (#659).
            anchor = now if last_scheduled is None else max(now, last_scheduled)
            next_run = croniter(schedule, anchor).get_next(datetime)
            last_scheduled = next_run
            global next_fire
            next_fire = next_run.timestamp()  # #1087
            delay = max(0.0, (next_run - now).total_seconds())
            logger.info(f"Heartbeat next run in {delay:.0f}s at {next_run.isoformat()}")
            try:
                await asyncio.wait_for(asyncio.shield(stop_waiter), timeout=delay)
                return  # stop_event fired — exit loop so caller can restart with new config
            except asyncio.TimeoutError:
                pass

            if harness_heartbeat_lag_seconds is not None:
                lag = (datetime.now(timezone.utc) - next_run).total_seconds()
                harness_heartbeat_lag_seconds.observe(lag)

            if stop_event.is_set():
                return

            # Reload content (schedule changes are handled by the watcher, but content may change)
            try:
                loaded = load_heartbeat()
            except Exception as e:
                if harness_heartbeat_load_errors_total is not None:
                    harness_heartbeat_load_errors_total.inc()
                logger.warning(f"Heartbeat reload error — skipping this run: {e}")
                continue
            if not loaded:
                logger.info("Heartbeat skipped — HEARTBEAT.md disabled or empty.")
                continue
            _, content, model, backend_id, consensus, max_tokens = loaded

            from prompt_env import resolve_prompt_env  # noqa: E402 — scoped import keeps startup simple

            prompt = resolve_prompt_env(f"Heartbeat check. Follow these instructions:\n\n{content}")
            _hb_start = time.monotonic()
            message = Message(
                prompt=prompt,
                session_id=HEARTBEAT_SESSION,
                kind="heartbeat",
                model=model,
                backend_id=backend_id,
                consensus=consensus,
                max_tokens=max_tokens,
            )
            if not bus.try_send(message):
                logger.info("Heartbeat skipped — previous heartbeat still pending.")
                if harness_heartbeat_skips_total is not None:
                    harness_heartbeat_skips_total.inc()
                continue
            # #1323: record last_fire unconditionally once try_send has
            # returned True — a future bus refactor where result is None
            # would otherwise produce a silent no-op (no metrics / no
            # event stream emit), leaving dashboards claiming healthy
            # while no actual fire happened.
            logger.info("Heartbeat firing.")
            _fire_ts = time.time()
            global last_fire
            last_fire = _fire_ts  # #1087
            if harness_heartbeat_last_run_timestamp_seconds is not None:
                harness_heartbeat_last_run_timestamp_seconds.set(_fire_ts)
            if message.result is not None:
                try:
                    # Race the in-flight result against stop_event (#492). Shield
                    # the result future so asyncio.wait doesn't mark our local
                    # reference as cancelled — the bus worker still owns the
                    # message and will release the dedup slot (#514) once
                    # process_bus completes, regardless of whether we await the
                    # outcome here.
                    shielded_result = asyncio.shield(message.result)
                    done, _ = await asyncio.wait(
                        {shielded_result, stop_waiter},
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    if shielded_result not in done:
                        # stop_event fired while the backend call is still
                        # in-flight; abandon the result and exit the loop so the
                        # watcher can reload promptly.
                        logger.info("Heartbeat stop requested mid-run — abandoning pending result.")
                        return
                    response = shielded_result.result()
                    if harness_heartbeat_duration_seconds is not None:
                        harness_heartbeat_duration_seconds.observe(time.monotonic() - _hb_start)
                    if response and HEARTBEAT_OK not in response:
                        logger.info(f"Heartbeat response: {response}")
                    if harness_heartbeat_runs_total is not None:
                        harness_heartbeat_runs_total.labels(status="success").inc()
                    _success_ts = time.time()
                    global last_success
                    last_success = _success_ts  # #1087
                    if harness_heartbeat_last_success_timestamp_seconds is not None:
                        harness_heartbeat_last_success_timestamp_seconds.set(_success_ts)
                    try:
                        _hb_payload: dict = {
                            "duration_ms": int((time.monotonic() - _hb_start) * 1000),
                            "outcome": "success",
                        }
                        if schedule:
                            _hb_payload["schedule"] = schedule
                        get_event_stream().publish("heartbeat.fired", _hb_payload, agent_id=AGENT_NAME)
                    except Exception:  # pragma: no cover
                        pass
                except Exception as e:
                    logger.error(f"Heartbeat executor error: {e}")
                    if harness_heartbeat_runs_total is not None:
                        harness_heartbeat_runs_total.labels(status="error").inc()
                    if harness_heartbeat_error_duration_seconds is not None:
                        harness_heartbeat_error_duration_seconds.observe(time.monotonic() - _hb_start)
                    if harness_heartbeat_last_error_timestamp_seconds is not None:
                        harness_heartbeat_last_error_timestamp_seconds.set(time.time())
                    try:
                        _hb_err: dict = {
                            "duration_ms": int((time.monotonic() - _hb_start) * 1000),
                            "outcome": "error",
                            "error": repr(e)[:512],
                        }
                        if schedule:
                            _hb_err["schedule"] = schedule
                        get_event_stream().publish("heartbeat.fired", _hb_err, agent_id=AGENT_NAME)
                    except Exception:  # pragma: no cover
                        pass
    finally:
        stop_waiter.cancel()


def _loop_task_done_callback(t: asyncio.Task) -> None:
    """Log and count unexpected exceptions from a _run_loop task."""
    if not t.cancelled() and t.exception() is not None:
        logger.error(f"Heartbeat loop_task crashed: {t.exception()!r}")
        if harness_heartbeat_runs_total is not None:
            harness_heartbeat_runs_total.labels(status="error").inc()


async def _stop_and_join(loop_task: asyncio.Task, stop_event: asyncio.Event) -> None:
    """Signal the run loop to stop and wait for it to unwind, with a bound (#492).

    The loop already races ``message.result`` against ``stop_event`` internally,
    so a clean exit should be prompt. This bounded join is a safety net: if an
    unresponsive backend keeps the loop parked past
    :data:`HEARTBEAT_STOP_JOIN_TIMEOUT`, cancel the task so the watcher can
    proceed with the reload instead of hanging indefinitely.
    """
    stop_event.set()
    try:
        await asyncio.wait_for(asyncio.shield(loop_task), timeout=HEARTBEAT_STOP_JOIN_TIMEOUT)
    except asyncio.TimeoutError:
        logger.warning(f"Heartbeat loop did not stop within {HEARTBEAT_STOP_JOIN_TIMEOUT:.1f}s — cancelling.")
        loop_task.cancel()
        try:
            await loop_task
        except (asyncio.CancelledError, Exception):
            pass


async def heartbeat_runner(
    bus: MessageBus,
    backends_ready: asyncio.Event | None = None,
) -> None:
    """Watch ``HEARTBEAT.md`` and (re)launch the cron-driven fire loop.

    On entry, calls :func:`load_heartbeat` to read schedule + prompt
    content from ``HEARTBEAT_PATH``. A successful load spawns the
    inner ``_run_loop`` task that fires heartbeat :class:`Message`
    instances onto *bus* on the configured cron cadence; load failures
    or an empty / disabled file leave the runner in the idle state
    where it only watches for file changes.

    The outer loop polls for the heartbeat directory to exist (with
    10s backoff) and then uses ``watchfiles.awatch`` to react to edits
    on ``HEARTBEAT.md``. Each edit cancels and joins the current loop
    via :func:`_stop_and_join`, reloads the file, and re-spawns the
    loop with the new schedule — so operators can re-pace the
    heartbeat without restarting the harness. ``backends_ready`` is
    forwarded into ``_run_loop`` so the very first fire waits for
    backend readiness before dispatching.
    """
    try:
        loaded = load_heartbeat()
    except Exception as e:
        if harness_heartbeat_load_errors_total is not None:
            harness_heartbeat_load_errors_total.inc()
        logger.warning(f"Heartbeat load error at startup — treating as disabled: {e}")
        loaded = None
    if not loaded:
        logger.info("Heartbeat idle — HEARTBEAT.md not found, disabled, or empty.")
    else:
        schedule, content, model, backend_id, consensus, max_tokens = loaded
        logger.info(f"Heartbeat runner started. Schedule: {schedule}")

    stop_event = asyncio.Event()
    loop_task: asyncio.Task | None = None

    if loaded:
        loop_task = asyncio.create_task(
            _run_loop(
                bus,
                schedule,
                content,
                stop_event,
                model=model,
                backend_id=backend_id,
                consensus=consensus,
                max_tokens=max_tokens,
                backends_ready=backends_ready,
            )
        )
        loop_task.add_done_callback(_loop_task_done_callback)

    while True:
        if not os.path.isdir(HEARTBEAT_DIR):
            logger.info("Heartbeat directory not found — retrying in 10s.")
            await asyncio.sleep(10)
            continue

        async for changes in awatch(HEARTBEAT_DIR):
            if harness_watcher_events_total is not None:
                harness_watcher_events_total.labels(watcher="heartbeat").inc()
            for _, path in changes:
                if not path.endswith("HEARTBEAT.md"):
                    continue

                logger.info("HEARTBEAT.md changed — reloading.")
                if harness_heartbeat_reloads_total is not None:
                    harness_heartbeat_reloads_total.inc()
                if loop_task and not loop_task.done():
                    await _stop_and_join(loop_task, stop_event)
                    stop_event.clear()

                try:
                    loaded = load_heartbeat()
                except Exception as e:
                    if harness_heartbeat_load_errors_total is not None:
                        harness_heartbeat_load_errors_total.inc()
                    logger.warning(f"Heartbeat reload error after file change — skipping: {e}")
                    loop_task = None
                    continue
                if not loaded:
                    logger.info("Heartbeat disabled or empty after reload.")
                    loop_task = None
                else:
                    schedule, content, model, backend_id, consensus, max_tokens = loaded
                    logger.info(f"Heartbeat reloaded. Schedule: {schedule}")
                    loop_task = asyncio.create_task(
                        _run_loop(
                            bus,
                            schedule,
                            content,
                            stop_event,
                            model=model,
                            backend_id=backend_id,
                            consensus=consensus,
                            max_tokens=max_tokens,
                            backends_ready=backends_ready,
                        )
                    )
                    loop_task.add_done_callback(_loop_task_done_callback)

        logger.warning("Heartbeat directory watcher exited — directory deleted or unavailable. Retrying in 10s.")
        if harness_file_watcher_restarts_total is not None:
            harness_file_watcher_restarts_total.labels(watcher="heartbeat").inc()
        if loop_task and not loop_task.done():
            await _stop_and_join(loop_task, stop_event)
        stop_event.clear()
        try:
            loaded = load_heartbeat()
        except Exception as e:
            if harness_heartbeat_load_errors_total is not None:
                harness_heartbeat_load_errors_total.inc()
            logger.warning(f"Heartbeat reload error after watcher restart — skipping: {e}")
            loaded = None
        if loaded:
            schedule, content, model, backend_id, consensus, max_tokens = loaded
            loop_task = asyncio.create_task(
                _run_loop(
                    bus,
                    schedule,
                    content,
                    stop_event,
                    model=model,
                    backend_id=backend_id,
                    consensus=consensus,
                    max_tokens=max_tokens,
                    backends_ready=backends_ready,
                )
            )
            loop_task.add_done_callback(_loop_task_done_callback)
        else:
            loop_task = None
            logger.info("Heartbeat disabled or empty after watcher restart.")
        await asyncio.sleep(10)
