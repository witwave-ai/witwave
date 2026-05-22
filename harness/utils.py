"""Shared utilities for the autonomous agent."""

import asyncio
import inspect
import logging
import os
import re
from collections.abc import Awaitable, Callable
from typing import Any

import yaml

# #1326: re.ASCII so \d matches only [0-9]. Without the flag, Unicode
# decimal digits (Arabic-Indic, Thai, etc.) parse successfully and
# surprise operators.
_DURATION_RE = re.compile(r"^(?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s)?$", re.ASCII)


def parse_duration(value: str) -> float:
    """Parse a human-readable duration string into total seconds.

    Supports: 30s, 15m, 1h, 1h30m, 1h30m45s (and combinations thereof).
    Raises ValueError if the format is unrecognized.
    """
    value = value.strip()
    m = _DURATION_RE.match(value)
    if not m or not any(m.groups()):
        raise ValueError(f"Unrecognized duration format: {value!r}. Expected e.g. '30s', '15m', '1h', '1h30m'.")
    hours = int(m.group(1) or 0)
    minutes = int(m.group(2) or 0)
    seconds = int(m.group(3) or 0)
    return hours * 3600 + minutes * 60 + seconds


from dataclasses import dataclass  # noqa: E402


@dataclass
class ConsensusEntry:
    """One participant in a consensus fan-out."""

    backend: str
    model: str | None = None


def parse_consensus(value) -> list[ConsensusEntry]:
    """Parse the ``consensus`` frontmatter field into a list of ConsensusEntry objects.

    Only a YAML list of objects is accepted. Absent or empty list means disabled.

      consensus: []                                  # disabled (default)
      consensus:
        - backend: "*"                               # all backends, default model
        - backend: "claude"
          model: "claude-opus-4-7"
        - backend: "openai*"                          # glob — matches openai, openai-fast, etc.
    """
    if not isinstance(value, list):
        return []
    entries = []
    for item in value:
        if isinstance(item, dict) and item.get("backend"):
            entries.append(
                ConsensusEntry(
                    backend=str(item["backend"]),
                    model=str(item["model"]) if item.get("model") else None,
                )
            )
    return entries


_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n?(.*)", re.DOTALL)

# Hard cap on the YAML-frontmatter portion fed into safe_load (#1038).
# Frontmatter is typed config — cron, endpoints, schedule windows — so
# 64 KiB is already ample for every real use. Anything above that is
# either a bug in the authoring tool or a YAML-bomb attempt; we reject
# before calling safe_load so a malicious pin cannot pin RAM on
# repeated watcher ticks.
PARSE_FRONTMATTER_MAX_YAML_BYTES = int(os.environ.get("PARSE_FRONTMATTER_MAX_YAML_BYTES", str(64 * 1024)))
# Cap on the whole .md file passed through read_md_bounded() (#1038).
# Runners scan dirs on every watcher tick so a 50 MB .md tab-completed
# by accident would pin significant RAM across all 6 runners. 128 KiB
# matches the largest real HEARTBEAT/jobs corpus by 3 orders of
# magnitude and still leaves headroom.
PARSE_FRONTMATTER_MAX_FILE_BYTES = int(os.environ.get("PARSE_FRONTMATTER_MAX_FILE_BYTES", str(128 * 1024)))


class FrontmatterTooLarge(ValueError):
    """Raised when the frontmatter YAML block exceeds the byte cap (#1038)."""


def _safe_load_bounded(yaml_text: str) -> Any:
    """safe_load *yaml_text* under a hard byte cap (#1038).

    PyYAML's safe_load has no depth / alias-expansion / byte-budget
    knob, so the only practical guard against YAML-bombs is to refuse
    obviously-too-large inputs before the parser sees them. Callers
    handle the ``FrontmatterTooLarge`` exception by logging and
    skipping the file — same behaviour as a yaml.YAMLError.
    """
    if len(yaml_text.encode("utf-8", errors="replace")) > PARSE_FRONTMATTER_MAX_YAML_BYTES:
        raise FrontmatterTooLarge(
            f"frontmatter YAML exceeds {PARSE_FRONTMATTER_MAX_YAML_BYTES} bytes (see PARSE_FRONTMATTER_MAX_YAML_BYTES)"
        )
    return yaml.safe_load(yaml_text)


def parse_frontmatter(raw: str) -> tuple[dict[str, str], str]:
    """Parse YAML-like frontmatter from a markdown string.

    Returns a tuple of ``(fields, body)`` where *fields* is a dict mapping each
    frontmatter key to its string value (leading/trailing whitespace and
    surrounding quotes stripped), and *body* is the content that follows the
    closing ``---`` delimiter, stripped of leading and trailing whitespace.  If
    no frontmatter block is detected the returned dict is empty and *body* is
    the original *raw* string unchanged.

    Raises :class:`FrontmatterTooLarge` when the YAML block exceeds the
    size cap (#1038) so callers can log-and-skip rather than feeding a
    YAML-bomb to ``safe_load``.
    """
    match = _FRONTMATTER_RE.match(raw)
    if not match:
        return {}, raw

    parsed = _safe_load_bounded(match.group(1))
    if not isinstance(parsed, dict):
        parsed = {}
    fields: dict[str, str] = {k: str(v) if v is not None else "" for k, v in parsed.items()}

    return fields, match.group(2).strip()


def parse_frontmatter_raw(raw: str) -> tuple[dict, str]:
    """Like parse_frontmatter but returns field values uncoerced (preserving lists, bools, ints, etc.)."""
    match = _FRONTMATTER_RE.match(raw)
    if not match:
        return {}, raw
    parsed = _safe_load_bounded(match.group(1))
    if not isinstance(parsed, dict):
        parsed = {}
    return parsed, match.group(2).strip()


# (path, mtime_ns, size) -> cached raw text. Short-circuits the repeated
# full re-read each runner did on every watcher tick (#1038). The cache
# keys include both mtime and size so a writer that patches a file
# without bumping mtime (rare) still invalidates correctly.
# #1388: switched to OrderedDict for LRU (move-on-hit + popitem-oldest)
# instead of FIFO which would evict the oldest inserted regardless of
# recent access. Also tracks an eviction counter so operators can alert
# on cache thrash (>1024 md files in a watched tree).
from collections import OrderedDict  # noqa: E402

_MD_CACHE: "OrderedDict[str, tuple[int, int, str]]" = OrderedDict()
_MD_CACHE_MAX = int(os.environ.get("PARSE_FRONTMATTER_CACHE_MAX", "1024"))
_MD_CACHE_EVICTIONS = 0  # read by metrics.py via `read_md_cache_evictions_total`


def read_md_bounded(path: str) -> str | None:
    """Read a markdown file with size cap + mtime/size short-circuit (#1038).

    Returns the file's raw text, or ``None`` when:

    * the file is gone (runners already log-and-skip ``None``),
    * the file exceeds :data:`PARSE_FRONTMATTER_MAX_FILE_BYTES` — a
      WARNING is emitted so operators see the skip.

    Subsequent calls with an unchanged ``(mtime_ns, size)`` return the
    cached text directly instead of re-opening and re-reading the file.
    This is the main CPU/RAM win on watcher-tick storms.
    """
    try:
        st = os.stat(path)
    except FileNotFoundError:
        _MD_CACHE.pop(path, None)
        return None
    except OSError:
        return None

    if st.st_size > PARSE_FRONTMATTER_MAX_FILE_BYTES:
        import logging as _logging

        _logging.getLogger(__name__).warning(
            "read_md_bounded: %r is %d bytes (>%d cap); skipping. "
            "Raise PARSE_FRONTMATTER_MAX_FILE_BYTES if the file is "
            "legitimate or trim it upstream.",
            path,
            st.st_size,
            PARSE_FRONTMATTER_MAX_FILE_BYTES,
        )
        _MD_CACHE.pop(path, None)
        return None

    cached = _MD_CACHE.get(path)
    if cached is not None and cached[0] == st.st_mtime_ns and cached[1] == st.st_size:
        # #1388: LRU move-on-hit so an actively-read path isn't evicted
        # just because an unrelated hot path inserted recently.
        _MD_CACHE.move_to_end(path)
        return cached[2]

    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            raw = fh.read(PARSE_FRONTMATTER_MAX_FILE_BYTES + 1)
    except OSError:
        return None

    # A write-race between stat and read could return a longer string;
    # refuse to trust that and force a fresh stat on the next call.
    if len(raw.encode("utf-8", errors="replace")) > PARSE_FRONTMATTER_MAX_FILE_BYTES:
        _MD_CACHE.pop(path, None)
        return None

    # #1388: LRU eviction of the least-recently-used entry; also bump
    # the module-level counter so metrics can alert on thrash.
    # #1747: also bump the Prometheus counter when present so operators
    # see thrash from /metrics rather than having to attach to the
    # process. The local int counter is preserved for back-compat.
    if len(_MD_CACHE) >= _MD_CACHE_MAX:
        try:
            _MD_CACHE.popitem(last=False)
            global _MD_CACHE_EVICTIONS
            _MD_CACHE_EVICTIONS += 1
            try:
                from metrics import harness_md_cache_evictions_total as _hmce  # type: ignore

                if _hmce is not None:
                    _hmce.inc()
            except Exception:
                # Metrics import failure must never break cache eviction.
                pass
        except KeyError:
            pass
    _MD_CACHE[path] = (st.st_mtime_ns, st.st_size, raw)
    return raw


async def _maybe_await(value: Any) -> None:
    """Await *value* if it is awaitable; otherwise do nothing.

    Lets callers pass either sync or async callbacks into ``run_awatch_loop``.
    """
    if inspect.isawaitable(value):
        await value


async def run_awatch_loop(
    *,
    directory: str,
    watcher_name: str,
    scan: Callable[[], Awaitable[None]],
    on_change: Callable[[str], Any],
    on_delete: Callable[[str], Any],
    cleanup: Callable[[], Any],
    logger_: logging.Logger,
    not_found_message: str,
    watcher_exited_message: str,
    retry_delay: float = 10.0,
    file_suffix: str = ".md",
    watcher_events_metric: Any = None,
    file_watcher_restarts_metric: Any = None,
) -> None:
    """Run the standard md-directory awatch loop shared by the harness runners.

    Encapsulates the pattern introduced by #513: on each iteration, spawn the
    initial ``scan()`` as a concurrent task so it runs *after* ``awatch()`` has
    entered its RustNotify context manager (closing the TOCTOU race), then
    iterate change events, and on watcher exit cancel+await the still-running
    ``_scan_task`` before restarting. Without the cancel+await, a new
    ``_scan_task`` from the next iteration could race with a stale prior one
    and produce duplicate ``_register`` calls on a flapping directory mount.

    Per-runner semantics (logging on register/unregister, reload metric
    bookkeeping, sync vs async ``_register``, `count_reload` flags,
    post-watcher cleanup) stay in the caller-supplied callbacks. This helper
    owns only the loop machinery.

    Args:
        directory: The directory to watch. Absence is tolerated — the loop
            sleeps and retries ``retry_delay`` seconds later.
        watcher_name: Label used for metrics and log messages.
        scan: Async callable invoked once per iteration to reconcile the
            current directory contents with the registry.
        on_change: Callback invoked for each observed ``.md`` file whose
            path still exists on disk. May be sync or async.
        on_delete: Callback invoked for each observed ``.md`` file whose
            path no longer exists. May be sync or async.
        cleanup: Callback invoked after the watcher exits, before sleeping
            and restarting. May be sync or async.
        logger_: Logger for lifecycle messages (not-found, watcher-exited).
        not_found_message: Log line emitted when ``directory`` is missing.
        watcher_exited_message: Log line emitted when ``awatch`` returns.
        retry_delay: Seconds to sleep before restarting the watcher.
        file_suffix: Only paths with this suffix trigger change/delete
            callbacks. Defaults to ``.md``.
        watcher_events_metric: Optional Prometheus counter; incremented with
            ``labels(watcher=watcher_name).inc()`` on every awatch batch.
        file_watcher_restarts_metric: Optional Prometheus counter;
            incremented with ``labels(watcher=watcher_name).inc()`` each
            time the watcher restarts.
    """
    # Imported inline so callers that never invoke run_awatch_loop don't pay
    # the watchfiles import cost (and so utils.py stays importable even if
    # watchfiles is absent at module load time).
    from watchfiles import awatch

    # Dormant-directory tracking. Per the file-presence-as-enablement
    # model (see DESIGN.md: harness subsystems are opt-in by content),
    # a missing directory is a normal, expected state — it means "this
    # agent is not using this subsystem." So the default-level log on
    # a missing directory is DEBUG, not INFO: no noise for agents that
    # intentionally run dormant on a subsystem.
    #
    # The one INFO-level signal we DO emit is the transition
    # missing → present. That's interesting: content just appeared,
    # the watcher is now active. Operators want to see that moment.
    was_missing: bool | None = None  # None = pre-flight; first iteration decides

    while True:
        if not os.path.isdir(directory):
            # DEBUG on every miss — visible under `-v` for diagnostics,
            # silent by default. No distinction between first-miss and
            # subsequent-miss: both are the same expected state.
            logger_.debug(not_found_message)
            was_missing = True
            await asyncio.sleep(retry_delay)
            continue

        # Directory present. When we just transitioned from missing →
        # present, announce it — operators want to see when content
        # actually materialises (e.g. via gitSync or a later ConfigMap
        # mount). `is True` excludes the pre-flight (None) case; on
        # initial boot with content already present we don't need a
        # "transition" log because the subsequent scan/register cycle
        # will log its own lifecycle signal for each entry.
        if was_missing is True:
            logger_.info(
                "%s directory %r now present — starting watcher",
                watcher_name,
                directory,
            )
        was_missing = False

        # Schedule scan() as a concurrent task so it runs *after* awatch()
        # has entered its RustNotify context manager (i.e. after the OS-level
        # watch is registered). Files added between watch registration and
        # scan completion are already tracked by the watcher; scan() and
        # on_change() are expected to be idempotent so duplicate events from
        # both the scan and the watcher are safe.
        _scan_task = asyncio.ensure_future(scan())

        def _scan_done(t: asyncio.Task, _name: str = watcher_name) -> None:
            if not t.cancelled() and t.exception() is not None:
                logger_.error(f"{_name} _scan crashed: %r", t.exception())

        _scan_task.add_done_callback(_scan_done)
        # #1185: await the concurrent scan before draining awatch so any
        # on_change / on_delete calls emitted by the watcher during the scan
        # are serialised *after* the scan's initial registration pass. Without
        # this, a file changed between scan-start and scan-finish could be
        # processed in parallel — the watcher event racing with the scan's
        # call to the same on_change handler. Errors from _scan are still
        # observable via the _scan_done callback logged above.
        try:
            await _scan_task
        except Exception:
            # Already logged by _scan_done; swallow here so awatch still runs.
            pass
        async for changes in awatch(directory):
            if watcher_events_metric is not None:
                watcher_events_metric.labels(watcher=watcher_name).inc()
            for _, path in changes:
                if not path.endswith(file_suffix):
                    continue
                if os.path.exists(path):
                    await _maybe_await(on_change(path))
                else:
                    await _maybe_await(on_delete(path))

        logger_.warning(watcher_exited_message)
        if file_watcher_restarts_metric is not None:
            file_watcher_restarts_metric.labels(watcher=watcher_name).inc()
        # Cancel + await the in-flight _scan_task before the next iteration
        # so a new _scan_task from the next loop cannot race with a prior
        # one (e.g. on a flapping directory mount). Without this, overlapping
        # scan() runs would produce duplicate on_change calls and double-
        # cancellation of the same per-item tasks (#513).
        if not _scan_task.done():
            _scan_task.cancel()
        await asyncio.gather(_scan_task, return_exceptions=True)
        await _maybe_await(cleanup())
        await asyncio.sleep(retry_delay)
