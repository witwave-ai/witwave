"""Fetch and merge conversation and trace logs from backend agents."""

import asyncio
import json
import logging
import os
import time
from datetime import datetime, timezone

import httpx
from metrics import harness_backend_proxy_fetch_errors_total

from backends.config import BackendConfig

logger = logging.getLogger(__name__)

# Cap on per-backend fan-out response size. The 5s timeout bounds wall-clock
# but a misbehaving peer on the pod-local network can still push hundreds of
# MiB within that window. Multiple concurrent clients requesting trace/
# conversations for the same session would each buffer a full copy; a cap
# here keeps harness memory pressure bounded on any single proxy call.
# Set via HARNESS_PROXY_MAX_RESPONSE_BYTES; values <= 0 disable the cap.
_PROXY_MAX_RESPONSE_BYTES = int(os.environ.get("HARNESS_PROXY_MAX_RESPONSE_BYTES", str(64 * 1024 * 1024)))


async def _capped_get_json(
    client: httpx.AsyncClient,
    url: str,
    params: dict,
    headers: dict,
) -> tuple[int, list | dict | None]:
    """GET *url* and parse JSON, capping the buffered body size.

    Returns ``(status_code, parsed_or_none)``. ``parsed_or_none`` is the
    decoded body on 2xx when the size cap is not exceeded, else ``None``.
    Non-2xx responses still return their status so the caller can log it;
    a cap-exceeded or malformed-JSON body returns status 200 with
    ``parsed=None`` so the caller treats it as an empty page.
    """
    async with client.stream("GET", url, params=params, headers=headers) as resp:
        if resp.status_code != 200:
            # Drain to avoid leaking the connection.
            await resp.aread()
            return resp.status_code, None
        if _PROXY_MAX_RESPONSE_BYTES <= 0:
            await resp.aread()
            try:
                return 200, json.loads(resp.text)
            except (ValueError, json.JSONDecodeError):
                return 200, None
        chunks: list[bytes] = []
        total = 0
        async for chunk in resp.aiter_bytes():
            total += len(chunk)
            if total > _PROXY_MAX_RESPONSE_BYTES:
                logger.warning(
                    "harness proxy response from %s exceeds HARNESS_PROXY_MAX_RESPONSE_BYTES=%d; truncating",
                    url,
                    _PROXY_MAX_RESPONSE_BYTES,
                )
                return 200, None
            chunks.append(chunk)
        body = b"".join(chunks).decode(resp.encoding or "utf-8", errors="replace")
        try:
            return 200, json.loads(body)
        except (ValueError, json.JSONDecodeError):
            return 200, None


# Log-flood guard: throttle warning-level emissions per (backend_id, endpoint)
# to at most once per _LOG_THROTTLE_SECONDS. Further failures within the window
# fall back to debug-level so a down backend does not flood logs for hours (#579).
_LOG_THROTTLE_SECONDS = 60.0
_last_warn_ts: dict[tuple[str, str], float] = {}


def _log_fetch_error(backend_id: str, endpoint: str, message: str) -> None:
    """Emit a warning at most once per backend+endpoint per throttle window; else debug."""
    key = (backend_id, endpoint)
    now = time.monotonic()
    last = _last_warn_ts.get(key, 0.0)
    if now - last >= _LOG_THROTTLE_SECONDS:
        _last_warn_ts[key] = now
        logger.warning(message)
    else:
        logger.debug(message)


def _count_fetch_error(backend_id: str, endpoint: str) -> None:
    """Increment the proxy fetch-error counter when metrics are enabled."""
    if harness_backend_proxy_fetch_errors_total is not None:
        harness_backend_proxy_fetch_errors_total.labels(backend=backend_id, endpoint=endpoint).inc()


def _ts_sort_key(entry: dict) -> str:
    """Normalize a backend entry ``ts`` to a comparable ISO-8601 string.

    Different backends emit ``ts`` in different shapes (claude=ISO 8601 string,
    codex=numeric epoch seconds — see ``shared/conversations.py``). Sorting a
    merged list of mixed types raises ``TypeError`` in Python 3, so coerce
    numeric timestamps to ISO before comparison. Falls back to ``str()`` for
    anything else so the sort never raises.
    """
    ts = entry.get("ts", "")
    if isinstance(ts, (int, float)):
        try:
            return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
        except (OverflowError, OSError, ValueError):
            return str(ts)
    return str(ts) if ts is not None else ""


async def fetch_backend_conversations(
    backends: list[BackendConfig],
    since: str | None = None,
    limit: int | None = None,
    auth_token: str | None = None,
) -> list[dict]:
    """Fetch /conversations from each backend concurrently and return merged entries sorted by ts.

    Backends that are unreachable or return non-200 are skipped; failures are counted in
    harness_backend_proxy_fetch_errors_total{backend, endpoint="conversations"} and logged at
    warning (throttled per-backend per-endpoint) so silent degradation is visible (#579).
    When auth_token is provided, it is forwarded as a Bearer Authorization header.
    """
    # Pass limit to each backend so the per-backend response is bounded.
    # This caps the merged deduplication set to O(n_backends × limit) entries
    # rather than allowing unlimited accumulation (#365).
    params: dict = {}
    if since:
        params["since"] = since
    if limit is not None:
        params["limit"] = limit
    headers: dict = {}
    if auth_token:
        headers["Authorization"] = f"Bearer {auth_token}"

    async def _fetch_one_conversations(client: httpx.AsyncClient, backend: BackendConfig) -> list[dict]:
        if not backend.url:
            return []
        url = backend.url.rstrip("/") + "/conversations"
        try:
            status, entries = await _capped_get_json(client, url, params, headers)
            if status == 200:
                if isinstance(entries, list):
                    return entries
            else:
                _count_fetch_error(backend.id, "conversations")
                _log_fetch_error(
                    backend.id,
                    "conversations",
                    f"Backend {backend.id!r} /conversations returned {status} — skipping",
                )
        except Exception as exc:
            _count_fetch_error(backend.id, "conversations")
            _log_fetch_error(
                backend.id,
                "conversations",
                f"Backend {backend.id!r} /conversations unreachable: {exc!r} — skipping",
            )
        return []

    seen: set[tuple] = set()
    all_entries: list[dict] = []
    async with httpx.AsyncClient(timeout=5.0) as client:
        results = await asyncio.gather(
            *[_fetch_one_conversations(client, b) for b in backends],
            return_exceptions=True,
        )
    for backend, result in zip(backends, results):
        if isinstance(result, BaseException):
            _count_fetch_error(backend.id, "conversations")
            _log_fetch_error(
                backend.id,
                "conversations",
                f"Backend {backend.id!r} /conversations gather error: {result!r} — skipping",
            )
            continue
        for entry in result:
            key = (
                entry.get("ts"),
                entry.get("session_id"),
                entry.get("role"),
                entry.get("agent"),
                (entry.get("text") or "")[:64],
            )
            if key not in seen:
                seen.add(key)
                all_entries.append(entry)

    all_entries.sort(key=_ts_sort_key)
    if limit is not None:
        all_entries = all_entries[-limit:]
    return all_entries


async def fetch_backend_tool_audit(
    backends: list[BackendConfig],
    since: str | None = None,
    limit: int | None = None,
    decision: str | None = None,
    tool: str | None = None,
    session: str | None = None,
    auth_token: str | None = None,
) -> list[dict]:
    """Fetch /tool-audit from each backend concurrently and return merged rows (#635).

    Mirrors :func:`fetch_backend_conversations` / :func:`fetch_backend_trace`:
    unreachable or non-200 backends are skipped, failures counted in
    ``harness_backend_proxy_fetch_errors_total{endpoint="tool_audit"}`` and logged
    at warning (throttled). ``since`` / ``decision`` / ``tool`` / ``session`` are
    forwarded verbatim so the per-backend read does the filtering cheaply.
    """
    params: dict = {}
    if since:
        params["since"] = since
    if limit is not None:
        params["limit"] = limit
    if decision:
        params["decision"] = decision
    if tool:
        params["tool"] = tool
    if session:
        params["session"] = session
    headers: dict = {}
    if auth_token:
        headers["Authorization"] = f"Bearer {auth_token}"

    async def _fetch_one_tool_audit(client: httpx.AsyncClient, backend: BackendConfig) -> list[dict]:
        if not backend.url:
            return []
        url = backend.url.rstrip("/") + "/tool-audit"
        try:
            status, entries = await _capped_get_json(client, url, params, headers)
            if status == 200:
                if isinstance(entries, list):
                    return entries
            else:
                _count_fetch_error(backend.id, "tool_audit")
                _log_fetch_error(
                    backend.id,
                    "tool_audit",
                    f"Backend {backend.id!r} /tool-audit returned {status} — skipping",
                )
        except Exception as exc:
            _count_fetch_error(backend.id, "tool_audit")
            _log_fetch_error(
                backend.id,
                "tool_audit",
                f"Backend {backend.id!r} /tool-audit unreachable: {exc!r} — skipping",
            )
        return []

    seen: set[tuple] = set()
    all_entries: list[dict] = []
    async with httpx.AsyncClient(timeout=5.0) as client:
        results = await asyncio.gather(
            *[_fetch_one_tool_audit(client, b) for b in backends],
            return_exceptions=True,
        )
    for backend, result in zip(backends, results):
        if isinstance(result, BaseException):
            _count_fetch_error(backend.id, "tool_audit")
            _log_fetch_error(
                backend.id,
                "tool_audit",
                f"Backend {backend.id!r} /tool-audit gather error: {result!r} — skipping",
            )
            continue
        for entry in result:
            key = (
                entry.get("ts"),
                entry.get("session_id"),
                entry.get("tool_use_id"),
                entry.get("tool_name") or entry.get("tool"),
                entry.get("decision"),
            )
            if key not in seen:
                seen.add(key)
                all_entries.append(entry)

    all_entries.sort(key=lambda e: str(e.get("ts", "")))
    if limit is not None:
        all_entries = all_entries[-limit:]
    return all_entries


async def fetch_backend_trace(
    backends: list[BackendConfig],
    since: str | None = None,
    limit: int | None = None,
    auth_token: str | None = None,
) -> list[dict]:
    """Fetch /trace from each backend concurrently and return merged entries sorted by ts.

    Backends that are unreachable or return non-200 are skipped; failures are counted in
    harness_backend_proxy_fetch_errors_total{backend, endpoint="trace"} and logged at
    warning (throttled per-backend per-endpoint) so silent degradation is visible (#579).
    When auth_token is provided, it is forwarded as a Bearer Authorization header.
    """
    # Pass limit to each backend so the per-backend response is bounded.
    # This caps the merged deduplication set to O(n_backends × limit) entries
    # rather than allowing unlimited accumulation (#365).
    params: dict = {}
    if since:
        params["since"] = since
    if limit is not None:
        params["limit"] = limit
    headers: dict = {}
    if auth_token:
        headers["Authorization"] = f"Bearer {auth_token}"

    async def _fetch_one_trace(client: httpx.AsyncClient, backend: BackendConfig) -> list[dict]:
        if not backend.url:
            return []
        url = backend.url.rstrip("/") + "/trace"
        try:
            status, entries = await _capped_get_json(client, url, params, headers)
            if status == 200:
                if isinstance(entries, list):
                    return entries
            else:
                _count_fetch_error(backend.id, "trace")
                _log_fetch_error(
                    backend.id,
                    "trace",
                    f"Backend {backend.id!r} /trace returned {status} — skipping",
                )
        except Exception as exc:
            _count_fetch_error(backend.id, "trace")
            _log_fetch_error(
                backend.id,
                "trace",
                f"Backend {backend.id!r} /trace unreachable: {exc!r} — skipping",
            )
        return []

    seen: set[tuple] = set()
    all_entries: list[dict] = []
    async with httpx.AsyncClient(timeout=5.0) as client:
        results = await asyncio.gather(
            *[_fetch_one_trace(client, b) for b in backends],
            return_exceptions=True,
        )
    for backend, result in zip(backends, results):
        if isinstance(result, BaseException):
            _count_fetch_error(backend.id, "trace")
            _log_fetch_error(
                backend.id,
                "trace",
                f"Backend {backend.id!r} /trace gather error: {result!r} — skipping",
            )
            continue
        for entry in result:
            key = (
                entry.get("ts"),
                entry.get("session_id"),
                entry.get("event_type"),
                entry.get("id") or entry.get("tool_use_id"),
            )
            if key not in seen:
                seen.add(key)
                all_entries.append(entry)

    all_entries.sort(key=_ts_sort_key)
    if limit is not None:
        all_entries = all_entries[-limit:]
    return all_entries
