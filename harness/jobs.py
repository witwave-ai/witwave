import asyncio
import json
import logging
import os
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from bus import Message, MessageBus
from croniter import croniter
from events import get_event_stream
from metrics import (
    harness_checkpoint_write_errors_total,
    harness_file_watcher_restarts_total,
    harness_job_checkpoint_stale_total,
    harness_job_duration_seconds,
    harness_job_error_duration_seconds,
    harness_job_item_last_error_timestamp_seconds,
    harness_job_item_last_run_timestamp_seconds,
    harness_job_item_last_success_timestamp_seconds,
    harness_job_items_registered,
    harness_job_lag_seconds,
    harness_job_parse_errors_total,
    harness_job_reloads_total,
    harness_job_running_items,
    harness_job_runs_total,
    harness_job_skips_total,
    harness_watcher_events_total,
)
from utils import (
    ConsensusEntry,
    parse_consensus,
    parse_frontmatter,
    parse_frontmatter_raw,
    run_awatch_loop,
)

logger = logging.getLogger(__name__)

JOBS_DIR = os.environ.get("JOBS_DIR", "/home/agent/.witwave/jobs")
CHECKPOINT_DIR = os.path.join(JOBS_DIR, ".checkpoints")
AGENT_NAME = os.environ.get("AGENT_NAME", "witwave")
_JOBS_MAX_CONCURRENT = int(os.environ.get("JOBS_MAX_CONCURRENT", "0"))


@dataclass
class JobItem:
    """Parsed scheduled-job definition.

    Backs an entry in ``JobRunner._items``. ``schedule`` is a
    croniter-compatible cron expression or ``None`` for run-once
    mode (``run_job`` fires once and exits). ``session_id`` pins
    the conversation across reschedules. Runtime bookkeeping
    (``task``, ``running``, ``enabled``, ``next_fire``, ``last_fire``,
    ``last_success``) is populated by the cron loop and
    ``_execute_job`` so the /jobs endpoint can render scheduling
    state without re-parsing cron strings. ``enabled=False`` keeps
    the item listed but stops ``JobRunner._register`` from arming
    a cron task.
    """

    path: str
    name: str
    schedule: str | None
    session_id: str
    content: str
    model: str | None = None
    backend_id: str | None = None
    consensus: list[ConsensusEntry] = field(default_factory=list)
    max_tokens: int | None = None
    task: asyncio.Task | None = field(default=None, compare=False)
    running: bool = False
    # When False, the job is listed in /jobs for dashboard visibility but
    # no cron/run-once task is created. Flipping enabled:true in the md
    # frontmatter triggers a reload and the scheduler registers it.
    enabled: bool = True
    # Timestamps surfaced via /jobs and /.well-known/agent-runs.json (#1087).
    # Populated by the cron loop and _execute_job respectively so dashboards
    # can render 'when next / when last?' without cross-referencing cron
    # strings. All three are Unix epoch seconds (None = never).
    next_fire: float | None = field(default=None, compare=False)
    last_fire: float | None = field(default=None, compare=False)
    last_success: float | None = field(default=None, compare=False)


# Sentinel distinguishing "file parsed cleanly but is disabled" from
# "parse failed entirely". _register uses this to unregister any previously-
# scheduled task when a file flips from enabled to disabled, while parse
# errors fall through to the last-known-good path so transient syntax
# issues don't drop a healthy job off the schedule. Matches the pattern
# already used by triggers.py / continuations.py / webhooks.py.
_DISABLED = object()


def parse_job_file(path: str) -> "JobItem | object | None":
    """Parse a job markdown file into a JobItem.

    Reads frontmatter via ``parse_frontmatter`` and extracts ``name``,
    ``schedule``, ``session``, ``model``, ``agent``, ``consensus``, and
    ``max-tokens`` / ``max_tokens``. ``session`` defaults to a UUIDv5
    derived from ``AGENT_NAME + filename`` so reschedules across
    restarts reuse the same session id without requiring the author to
    pin one in frontmatter. ``enabled`` is parsed as a string-ish bool
    (the literals "false"/"no"/"off"/"n"/"0"/"" all disable) and
    stamped onto the returned JobItem; disabled jobs are still returned
    (with ``enabled=False``) so the registry can list parked files
    without grepping the filesystem.

    Returns ``None`` when an enabled job has a non-empty ``schedule``
    that fails ``croniter.is_valid``, or when any exception is raised
    during parsing (the error is logged and
    ``harness_job_parse_errors_total`` is incremented).
    """
    try:
        with open(path) as f:
            raw = f.read()

        enabled = True

        fields, content = parse_frontmatter(raw)
        raw_fields, _ = parse_frontmatter_raw(raw)
        name = fields.get("name") or None
        schedule = fields.get("schedule") or None
        session_id = fields.get("session") or None
        model = fields.get("model") or None
        backend_id = fields.get("agent") or None
        consensus = parse_consensus(raw_fields.get("consensus"))
        max_tokens: int | None = None
        max_tokens_raw = fields.get("max-tokens") or fields.get("max_tokens")
        if max_tokens_raw is not None:
            try:
                max_tokens = max(1, int(max_tokens_raw))
            except (ValueError, TypeError):
                logger.warning(f"Job file {path}: invalid 'max-tokens' value {max_tokens_raw!r}, ignoring.")
        if "enabled" in fields:
            enabled = str(fields["enabled"]).lower() not in ("false", "no", "off", "n", "0", "")

        # Disabled jobs bypass cron validation — a busted schedule on a
        # job that isn't going to fire shouldn't be a parse error, and
        # keeping them listed lets operators see what's parked without
        # grepping .md files on the filesystem.
        if enabled and schedule and not croniter.is_valid(schedule):
            logger.warning(f"Job file {path}: invalid cron expression '{schedule}', skipping.")
            return None

        filename = Path(path).stem
        name = name or filename
        if not session_id:
            # Generate a deterministic UUID from the agent name and filename
            session_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{AGENT_NAME}.{filename}"))

        return JobItem(
            path=path,
            name=name,
            schedule=schedule,
            session_id=session_id,
            content=content,
            model=model,
            backend_id=backend_id,
            consensus=consensus,
            max_tokens=max_tokens,
            enabled=enabled,
        )

    except Exception as e:
        if harness_job_parse_errors_total is not None:
            harness_job_parse_errors_total.inc()
        logger.error(f"Job file {path}: failed to parse — {e}, skipping.")
        return None


async def _execute_job(item: JobItem, bus: MessageBus, semaphore: asyncio.Semaphore | None) -> None:
    """Fire the job once. Called from both the cron loop and run-once mode."""
    _semaphore_acquired = False
    if semaphore is not None:
        await semaphore.acquire()
        _semaphore_acquired = True

    item.running = True
    if harness_job_running_items is not None:
        harness_job_running_items.inc()
    checkpoint_path = None
    try:
        os.makedirs(CHECKPOINT_DIR, exist_ok=True)
        checkpoint_path = os.path.join(CHECKPOINT_DIR, Path(item.path).stem + ".running.json")
        with open(checkpoint_path, "w") as f:
            json.dump(
                {
                    "started_at": datetime.now(timezone.utc).isoformat(),
                    "name": item.name,
                    "session_id": item.session_id,
                },
                f,
            )
    except Exception as e:
        if harness_checkpoint_write_errors_total is not None:
            harness_checkpoint_write_errors_total.inc()
        logger.error(f"Job '{item.name}' checkpoint write failed: {e}")
    _send_task: asyncio.Task | None = None
    # Initialise before the try so the except branch can always reference it,
    # even when resolve_prompt_env (or any earlier statement) raises before
    # the in-body assignment is reached (#657).
    _job_start = time.monotonic()
    try:
        from prompt_env import resolve_prompt_env  # noqa: E402 — scoped import keeps startup simple

        prompt = resolve_prompt_env(f"Job: {item.name}\n\n{item.content}")
        logger.info(f"Job '{item.name}' firing.")
        # #1322: removed inner `_job_start = time.monotonic()` reassign so
        # duration metric covers resolve_prompt_env + bus.send symmetrically
        # on both success and error paths (was: success underreported).
        message = Message(
            prompt=prompt,
            session_id=item.session_id,
            kind=f"job:{item.name}",
            model=item.model,
            backend_id=item.backend_id,
            consensus=item.consensus,
            max_tokens=item.max_tokens,
        )
        _fire_ts = time.time()
        item.last_fire = _fire_ts  # #1087 — surface last_fire on /jobs snapshot
        if harness_job_item_last_run_timestamp_seconds is not None:
            harness_job_item_last_run_timestamp_seconds.labels(name=item.name).set(_fire_ts)
        _send_task = asyncio.ensure_future(bus.send(message))

        def _log_send_result(t: asyncio.Task, _name: str = item.name) -> None:
            exc = t.exception() if not t.cancelled() else None
            if exc is not None:
                logger.error(f"Job '{_name}' background bus.send failed: {exc}")

        _send_task.add_done_callback(_log_send_result)
        await asyncio.shield(_send_task)
        if harness_job_duration_seconds is not None:
            harness_job_duration_seconds.labels(name=item.name).observe(time.monotonic() - _job_start)
        if harness_job_runs_total is not None:
            harness_job_runs_total.labels(name=item.name, status="success").inc()
        _success_ts = time.time()
        item.last_success = _success_ts  # #1087 — surface last_success on /jobs snapshot
        if harness_job_item_last_success_timestamp_seconds is not None:
            harness_job_item_last_success_timestamp_seconds.labels(name=item.name).set(_success_ts)
    except asyncio.CancelledError:
        if _send_task is not None and not _send_task.done():
            # #1273: bound the drain so SIGTERM cannot hang past kubelet grace
            # on a wedged A2A relay.
            _drain_timeout = float(os.environ.get("JOBS_SHUTDOWN_DRAIN_TIMEOUT", "5"))
            logger.info(
                f"Job '{item.name}' cancelled — awaiting in-flight bus.send "
                f"(up to {_drain_timeout}s) before clearing running flag."
            )
            try:
                await asyncio.wait_for(
                    asyncio.gather(_send_task, return_exceptions=True),
                    timeout=_drain_timeout,
                )
            except asyncio.TimeoutError:
                logger.warning(
                    f"Job '{item.name}' drain timed out after {_drain_timeout}s — "
                    "abandoning in-flight bus.send on shutdown."
                )
                if _send_task is not None and not _send_task.done():
                    _send_task.cancel()
        else:
            logger.info(f"Job '{item.name}' cancelled.")
        raise
    except Exception as e:
        logger.error(f"Job '{item.name}' error: {e}")
        if harness_job_runs_total is not None:
            harness_job_runs_total.labels(name=item.name, status="error").inc()
        if harness_job_error_duration_seconds is not None:
            harness_job_error_duration_seconds.labels(name=item.name).observe(time.monotonic() - _job_start)
        if harness_job_item_last_error_timestamp_seconds is not None:
            harness_job_item_last_error_timestamp_seconds.labels(name=item.name).set(time.time())
        # Publish job.fired (error outcome) on the SSE event stream (#1110).
        try:
            get_event_stream().publish(
                "job.fired",
                {
                    "name": item.name,
                    "schedule": item.schedule or "",
                    "duration_ms": int((time.monotonic() - _job_start) * 1000),
                    "outcome": "error",
                    "error": repr(e)[:512],
                },
                agent_id=AGENT_NAME,
            )
        except Exception:  # pragma: no cover — fan-out is best-effort
            pass
    else:
        # Success-path publish (#1110). Reaches here only when bus.send
        # completed without raising; the duration spans the whole
        # dispatch including the backend response.
        try:
            get_event_stream().publish(
                "job.fired",
                {
                    "name": item.name,
                    "schedule": item.schedule or "",
                    "duration_ms": int((time.monotonic() - _job_start) * 1000),
                    "outcome": "success",
                },
                agent_id=AGENT_NAME,
            )
        except Exception:  # pragma: no cover — fan-out is best-effort
            pass
    finally:
        item.running = False
        if harness_job_running_items is not None:
            harness_job_running_items.dec()
        if semaphore is not None and _semaphore_acquired:
            semaphore.release()
        if checkpoint_path is not None:
            try:
                os.remove(checkpoint_path)
            except FileNotFoundError:
                pass
            except Exception as e:
                if harness_checkpoint_write_errors_total is not None:
                    harness_checkpoint_write_errors_total.inc()
                logger.warning(f"Job '{item.name}' checkpoint delete failed: {e}")


async def run_job(
    item: JobItem,
    bus: MessageBus,
    semaphore: asyncio.Semaphore | None = None,
    backends_ready: asyncio.Event | None = None,
) -> None:
    """Drive a single job's cron loop on the harness event loop.

    Waits for ``backends_ready`` before doing anything so a job whose
    cron tick would fire during early startup doesn't fire against a
    backend pool that isn't yet ready.

    When ``item.schedule is None`` the job is treated as run-once:
    ``_execute_job`` is awaited once and the coroutine exits.
    Otherwise the function loops forever, computing the next tick
    from ``croniter(item.schedule, max(now, last_scheduled))`` so
    cumulative drift (overrunning runs, system suspend/resume,
    backwards NTP steps) cannot push subsequent ticks behind
    wall-clock (#860). Each tick records lag against
    ``harness_job_lag_seconds``; ticks arriving while a previous
    fire is still running are skipped and reported on
    ``harness_job_skips_total``. The optional ``semaphore`` is the
    runner's ``JOBS_MAX_CONCURRENT`` gate, acquired around each
    fire inside ``_execute_job``.
    """
    if backends_ready is not None:
        await backends_ready.wait()

    if item.schedule is None:
        # Run-once mode: fire once and exit.
        logger.info(f"Job '{item.name}' run-once: firing immediately.")
        await _execute_job(item, bus, semaphore)
        return

    # Track the most recent scheduled tick so iterations can anchor the
    # croniter forward rather than relying on a persistent cursor that
    # drifts behind wall-clock under long reloads, suspended laptops, or
    # NTP step adjustments (#860, matches heartbeat #659). Initialised to
    # None; the first iteration anchors at wall-clock.
    last_scheduled: datetime | None = None
    while True:
        now = datetime.now(timezone.utc)
        # Anchor cron from max(now, last_scheduled) every iteration so
        # cumulative drift (overrunning runs, reload-error continues,
        # system suspend/resume) cannot push subsequent ticks behind
        # wall-clock — while last_scheduled prevents the same tick from
        # firing twice if wall-clock skews backwards by a small amount
        # between iterations (#860).
        anchor = now if last_scheduled is None else max(now, last_scheduled)
        next_run = croniter(item.schedule, anchor).get_next(datetime)
        last_scheduled = next_run
        # Expose the next scheduled fire on the snapshot payload (#1087).
        item.next_fire = next_run.timestamp()
        delay = max(0.0, (next_run - now).total_seconds())
        logger.info(f"Job '{item.name}' next run in {delay:.0f}s at {next_run.isoformat()}")
        await asyncio.sleep(delay)

        if harness_job_lag_seconds is not None:
            lag = (datetime.now(timezone.utc) - next_run).total_seconds()
            harness_job_lag_seconds.observe(lag)

        if item.running:
            logger.warning(f"Job '{item.name}' still running from previous turn, skipping.")
            if harness_job_skips_total is not None:
                harness_job_skips_total.labels(name=item.name).inc()
            continue

        await _execute_job(item, bus, semaphore)


class JobRunner:
    """File-watching registry that schedules ``JobItem``s parsed from
    ``JOBS_DIR``.

    Each registered enabled item gets a long-running asyncio task
    executing ``run_job``; disabled items stay in ``_items`` for
    dashboard listing but no cron task is created. When
    ``JOBS_MAX_CONCURRENT`` > 0 a process-wide ``asyncio.Semaphore``
    caps the number of simultaneously executing job fires.
    Registration/unregistration are driven by ``run()`` watching the
    jobs directory and re-parsing on change; parse errors preserve
    the last-known-good registration.
    """

    def __init__(self, bus: MessageBus, backends_ready: asyncio.Event | None = None):
        self._bus = bus
        self._backends_ready = backends_ready
        self._items: dict[str, JobItem] = {}
        self._semaphore: asyncio.Semaphore | None = (
            asyncio.Semaphore(_JOBS_MAX_CONCURRENT) if _JOBS_MAX_CONCURRENT > 0 else None
        )
        if self._semaphore is not None:
            logger.info(f"Job concurrency limit: {_JOBS_MAX_CONCURRENT} concurrent items")

    async def _register(self, path: str) -> None:
        result = parse_job_file(path)
        if result is None:
            # Parse error — preserve the last known good registration so a
            # transient syntax issue doesn't drop a healthy job.
            return
        item = result
        cancelled = self._unregister(path)
        if cancelled is not None:
            await asyncio.gather(cancelled, return_exceptions=True)

        # Disabled: listed for dashboard visibility but no cron. Flipping
        # enabled:true triggers a file-watch reload and this method runs
        # again to create the cron task.
        if not item.enabled:
            self._items[path] = item
            if harness_job_items_registered is not None:
                harness_job_items_registered.set(sum(1 for i in self._items.values() if i.enabled))
            logger.info(f"Job '{item.name}' disabled — listed but not scheduled.")
            return

        task = asyncio.create_task(run_job(item, self._bus, self._semaphore, self._backends_ready))

        def _task_done_callback(t: asyncio.Task, _name: str = item.name) -> None:
            if not t.cancelled() and t.exception() is not None:
                logger.error(f"Job '{_name}' task crashed: {t.exception()!r}")
                if harness_job_runs_total is not None:
                    harness_job_runs_total.labels(name=_name, status="error").inc()

        task.add_done_callback(_task_done_callback)
        item.task = task
        self._items[path] = item
        if harness_job_items_registered is not None:
            harness_job_items_registered.set(sum(1 for i in self._items.values() if i.enabled))
        if item.schedule:
            logger.info(f"Job '{item.name}' registered. Schedule: {item.schedule}")
        else:
            logger.info(f"Job '{item.name}' registered. Mode: run-once")

    def items(self) -> list[dict]:
        """Return a serializable snapshot of all job items (enabled + disabled)."""
        result = []
        for item in self._items.values():
            result.append(
                {
                    "name": item.name,
                    "schedule": item.schedule,
                    "session_id": item.session_id,
                    "backend_id": item.backend_id,
                    "model": item.model,
                    "consensus": [asdict(e) for e in item.consensus],
                    "max_tokens": item.max_tokens,
                    "running": item.running,
                    "enabled": item.enabled,
                    # Fire-schedule bookkeeping (#1087). None when the runner
                    # hasn't yet computed the next cron tick or the job has
                    # never fired, respectively. Epoch seconds.
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
                logger.info(f"Job '{existing.name}' unregistered — cancelling while run is in progress.")
            else:
                logger.info(f"Job '{existing.name}' unregistered.")
            existing.task.cancel()
            if harness_job_items_registered is not None:
                harness_job_items_registered.set(sum(1 for i in self._items.values() if i.enabled))
            return existing.task
        if harness_job_items_registered is not None:
            harness_job_items_registered.set(sum(1 for i in self._items.values() if i.enabled))
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
                    logger.warning(f"Job '{name}': stale checkpoint at {cp_path} — run may have been interrupted")
                    if harness_job_checkpoint_stale_total is not None:
                        harness_job_checkpoint_stale_total.inc()
                    try:
                        os.remove(cp_path)
                    except Exception as rm_err:
                        logger.warning(f"Job '{name}': failed to remove stale checkpoint {cp_path}: {rm_err}")
        if not os.path.isdir(JOBS_DIR):
            return
        try:
            job_files = os.listdir(JOBS_DIR)
        except OSError:
            return
        for filename in job_files:
            if filename.endswith(".md"):
                await self._register(os.path.join(JOBS_DIR, filename))

    async def run(self) -> None:
        """Run the job watcher loop forever.

        Performs an initial ``_scan`` (which also sweeps any stale
        ``.running.json`` checkpoints left under ``CHECKPOINT_DIR``
        and increments ``harness_job_checkpoint_stale_total`` for
        each one found) and then drives ``awatch`` via
        ``run_awatch_loop`` so create/modify events call
        ``_register`` and delete events call ``_unregister``,
        incrementing ``harness_job_reloads_total`` on each. The
        loop self-restarts on watcher exit and survives directory
        deletion by retrying every 10s.
        """
        logger.info(f"Job runner watching {JOBS_DIR}")

        async def _on_change(path: str) -> None:
            logger.info(f"Job file changed: {path}")
            if harness_job_reloads_total is not None:
                harness_job_reloads_total.inc()
            await self._register(path)

        def _on_delete(path: str) -> None:
            logger.info(f"Job file removed: {path}")
            if harness_job_reloads_total is not None:
                harness_job_reloads_total.inc()
            self._unregister(path)

        async def _cleanup() -> None:
            cancelled = [t for path in list(self._items.keys()) if (t := self._unregister(path)) is not None]
            if cancelled:
                await asyncio.gather(*cancelled, return_exceptions=True)

        await run_awatch_loop(
            directory=JOBS_DIR,
            watcher_name="jobs",
            scan=self._scan,
            on_change=_on_change,
            on_delete=_on_delete,
            cleanup=_cleanup,
            logger_=logger,
            not_found_message="Jobs directory not found — retrying in 10s.",
            watcher_exited_message="Jobs directory watcher exited — directory deleted or unavailable. Retrying in 10s.",
            watcher_events_metric=harness_watcher_events_total,
            file_watcher_restarts_metric=harness_file_watcher_restarts_total,
        )
