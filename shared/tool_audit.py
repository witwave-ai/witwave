"""Shared helper for writing tool-audit rows across backends (#858).

Both claude and codex append ``event_type='tool_audit'`` rows to the
per-backend ``tool-activity.jsonl`` stream so a single ``/trace`` endpoint
can surface both SDK-level tool events and hook-level audit rows.

Before this module existed the two backends had drifted:

- ``claude`` wrote via an async ``log_tool_audit`` that offloaded the
  blocking ``_append_log`` onto ``asyncio.to_thread`` and bumped
  ``backend_log_write_errors_total`` +
  ``backend_log_write_errors_by_logger_total`` on failure.
- ``codex`` wrote synchronously from an async-call site and lacked a
  per-logger write-error counter until #804 / #858.

This helper consolidates the write path: one async ``log_tool_audit``
that does the ``event_type`` stamp, ``asyncio.to_thread(_append_log,
...)`` offload, metric bookkeeping, and swallows exceptions so audit
failure never breaks the agent's primary response path.

Callers supply a ``ToolAuditContext`` containing:

- ``trace_log_path`` — where to append (TRACE_LOG on each backend).
- ``labels`` — the ``{agent, agent_id, backend}`` dict the caller already
  uses for every other metric.
- ``metrics`` — a ``ToolAuditMetrics`` dataclass holding direct references
  to the per-backend counter objects; each field may be ``None`` when
  metrics are disabled (same pattern as the rest of the metrics surface).

``ToolAuditMetrics`` is passed by reference rather than imported from
``metrics`` here because the shared module must stay import-time clean of
backend-specific metric registries.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from log_utils import _append_log  # type: ignore

logger = logging.getLogger(__name__)


# Rotation-pressure threshold (#1102). When the tool-activity.jsonl file
# exceeds this size we bump ``tool_audit_rotation_pressure_total{reason}``
# so operators can alert before /trace reads become unusable. Default
# 256 MiB; tune via TOOL_ACTIVITY_ROTATION_PRESSURE_BYTES. 0 disables.
# The check is opportunistic (every N writes) to avoid a stat() per
# append.
try:
    _ROTATION_PRESSURE_BYTES = int(os.environ.get("TOOL_ACTIVITY_ROTATION_PRESSURE_BYTES", "268435456"))
except ValueError:
    _ROTATION_PRESSURE_BYTES = 268435456
try:
    _ROTATION_CHECK_EVERY = max(1, int(os.environ.get("TOOL_ACTIVITY_ROTATION_CHECK_EVERY", "100")))
except ValueError:
    _ROTATION_CHECK_EVERY = 100
_rotation_counters: dict[str, int] = {}


@dataclass
class ToolAuditMetrics:
    """Backend-supplied Prometheus counters. Any field may be None."""

    tool_audit_entries_total: Any = None
    log_entries_total: Any = None
    log_bytes_total: Any = None
    log_write_errors_total: Any = None
    log_write_errors_by_logger_total: Any = None
    # Per-entry byte-size histogram and rotation-pressure counter (#1102).
    tool_audit_bytes_per_entry: Any = None
    tool_audit_rotation_pressure_total: Any = None


@dataclass
class ToolAuditContext:
    """Bundle of the per-backend state ``log_tool_audit`` needs.

    ``trace_log_path`` is the JSONL file appended to. ``labels`` is the
    fixed label set applied to every metric bumped by audit writes (so
    they share the dimensionality of the rest of the backend's log
    counters). ``metrics`` is the Prometheus counter set; any individual
    field may be ``None`` when the backend hasn't wired that metric.
    """

    trace_log_path: str
    labels: Mapping[str, str]
    metrics: ToolAuditMetrics


def _utf8_byte_length(text: str) -> int:
    try:
        return len(text.encode("utf-8"))
    except Exception:
        return len(text)


async def log_tool_audit(ctx: ToolAuditContext, entry: dict) -> None:
    """Append one audit row to ``tool-activity.jsonl`` with event_type='tool_audit'.

    Never raises. Failure bumps the standard log-write-error counters with
    ``logger='tool_audit'`` so operators can distinguish audit vs
    conversation vs trace write failures on a single dashboard.
    """
    try:
        stamped = {**entry, "event_type": "tool_audit"}
        line = json.dumps(stamped, default=str)
        await asyncio.to_thread(_append_log, ctx.trace_log_path, line)
        m = ctx.metrics
        tool = str(entry.get("tool_name") or entry.get("tool") or "unknown")
        if m.tool_audit_entries_total is not None:
            try:
                m.tool_audit_entries_total.labels(**ctx.labels, tool=tool).inc()
            except Exception:
                pass
        if m.log_entries_total is not None:
            try:
                m.log_entries_total.labels(**ctx.labels, logger="trace").inc()
            except Exception:
                pass
        _line_bytes = _utf8_byte_length(line)
        if m.log_bytes_total is not None:
            try:
                m.log_bytes_total.labels(**ctx.labels, logger="trace").inc(_line_bytes)
            except Exception:
                pass
        # Per-entry byte histogram (#1102).
        if m.tool_audit_bytes_per_entry is not None:
            try:
                m.tool_audit_bytes_per_entry.labels(**ctx.labels, tool=tool).observe(_line_bytes)
            except Exception:
                pass
        # Opportunistic rotation-pressure check (#1102). Only os.stat()
        # every _ROTATION_CHECK_EVERY writes per path to keep the hot
        # path cheap. Any stat failure is swallowed.
        if _ROTATION_PRESSURE_BYTES > 0 and m.tool_audit_rotation_pressure_total is not None:
            try:
                _path = ctx.trace_log_path
                _n = _rotation_counters.get(_path, 0) + 1
                _rotation_counters[_path] = _n
                if _n % _ROTATION_CHECK_EVERY == 0:
                    try:
                        _size = os.path.getsize(_path)
                    except OSError:
                        _size = 0
                    if _size >= _ROTATION_PRESSURE_BYTES:
                        try:
                            m.tool_audit_rotation_pressure_total.labels(
                                **ctx.labels, reason="size_threshold_exceeded"
                            ).inc()
                        except Exception:
                            pass
            except Exception:
                pass
    except Exception as exc:  # pragma: no cover — audit must never raise
        m = ctx.metrics
        if m.log_write_errors_total is not None:
            try:
                m.log_write_errors_total.labels(**ctx.labels).inc()
            except Exception:
                pass
        if m.log_write_errors_by_logger_total is not None:
            try:
                m.log_write_errors_by_logger_total.labels(**ctx.labels, logger="tool_audit").inc()
            except Exception:
                pass
        logger.error("log_tool_audit error: %r", exc)
