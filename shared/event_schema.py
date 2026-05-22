"""Runtime validation for the harness event stream envelope + payloads (#1110).

The wire contract lives in ``docs/events/events.schema.json``.  This module
re-implements just enough JSON-Schema semantics to validate the envelope
and each per-type payload without pulling the ``jsonschema`` library into
the harness image — the type set is finite and the schema shapes are
simple enough to encode as native Python.

Public surface:

* :data:`KNOWN_TYPES` — tuple of every envelope ``type`` the server will
  emit.  The SSE route rejects unknown types so a typo in a publisher
  call site surfaces immediately rather than being silently forwarded.
* :func:`validate_envelope` — returns ``None`` on success, a short
  human-readable error string on failure.  Callers at emit-time log the
  error, bump a metric, and drop the event rather than propagating a
  malformed one to subscribers.

Additive payload changes (new optional fields) do NOT require code
changes here because every per-type validator defaults to ignoring keys
it does not know about.  Rename / retype / remove bumps the per-type
``version`` integer and registers the new shape side-by-side.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

_OUTCOMES = {"success", "error", "timeout", "skipped"}


def _err(msg: str) -> str:
    return msg


def _is_nonempty_str(v: Any) -> bool:
    return isinstance(v, str) and len(v) > 0


def _is_nonneg_int(v: Any) -> bool:
    # bool is a subclass of int; reject it explicitly so True/False aren't
    # silently accepted as 1/0.
    return isinstance(v, int) and not isinstance(v, bool) and v >= 0


def _is_http_status(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool) and 100 <= v <= 599


def _require_keys(
    payload: dict,
    required: tuple[str, ...],
    optional: tuple[str, ...],
    type_: str,
) -> str | None:
    """Enforce required fields; permit unknown additive fields (#1145).

    Matches the docstring at module-top: *Additive payload changes
    (new optional fields) do NOT require code changes here because
    every per-type validator defaults to ignoring keys it does not
    know about.*  Previously this helper rejected any key outside
    ``required | optional``, which contradicted the documented
    forward-compatibility contract — a publisher adding a new
    optional field would trip validation on every envelope until the
    validator was updated in lockstep.
    """
    for k in required:
        if k not in payload:
            return _err(f"{type_}: missing required payload field {k!r}")
    allowed = set(required) | set(optional)
    # Unknown keys are tolerated for forward compatibility; a debug
    # log gives operators a way to audit unexpected additions
    # without hard-failing valid traffic.
    unknown = [k for k in payload if k not in allowed]
    if unknown:
        import logging as _logging

        _logging.getLogger(__name__).debug(
            "%s: ignoring unknown additive payload fields %r (#1145)",
            type_,
            unknown,
        )
    return None


def _validate_job_fired(p: dict) -> str | None:
    err = _require_keys(p, ("name", "schedule", "duration_ms", "outcome"), ("error",), "job.fired")
    if err:
        return err
    if not _is_nonempty_str(p["name"]):
        return _err("job.fired: name must be a non-empty string")
    if not isinstance(p["schedule"], str):
        return _err("job.fired: schedule must be a string")
    if not _is_nonneg_int(p["duration_ms"]):
        return _err("job.fired: duration_ms must be a non-negative integer")
    if p["outcome"] not in _OUTCOMES:
        return _err(f"job.fired: outcome must be one of {sorted(_OUTCOMES)}")
    if "error" in p and not isinstance(p["error"], str):
        return _err("job.fired: error must be a string")
    return None


def _validate_task_fired(p: dict) -> str | None:
    err = _require_keys(p, ("name", "duration_ms", "outcome"), ("window", "error"), "task.fired")
    if err:
        return err
    if not _is_nonempty_str(p["name"]):
        return _err("task.fired: name must be a non-empty string")
    if "window" in p and not isinstance(p["window"], str):
        return _err("task.fired: window must be a string")
    if not _is_nonneg_int(p["duration_ms"]):
        return _err("task.fired: duration_ms must be a non-negative integer")
    if p["outcome"] not in _OUTCOMES:
        return _err(f"task.fired: outcome must be one of {sorted(_OUTCOMES)}")
    if "error" in p and not isinstance(p["error"], str):
        return _err("task.fired: error must be a string")
    return None


def _validate_heartbeat_fired(p: dict) -> str | None:
    err = _require_keys(p, ("duration_ms", "outcome"), ("schedule", "error"), "heartbeat.fired")
    if err:
        return err
    if "schedule" in p and not isinstance(p["schedule"], str):
        return _err("heartbeat.fired: schedule must be a string")
    if not _is_nonneg_int(p["duration_ms"]):
        return _err("heartbeat.fired: duration_ms must be a non-negative integer")
    if p["outcome"] not in _OUTCOMES:
        return _err(f"heartbeat.fired: outcome must be one of {sorted(_OUTCOMES)}")
    if "error" in p and not isinstance(p["error"], str):
        return _err("heartbeat.fired: error must be a string")
    return None


_CONTINUATION_KINDS = {"job", "task", "trigger", "a2a", "continuation"}


def _validate_continuation_fired(p: dict) -> str | None:
    err = _require_keys(
        p,
        ("name", "upstream_kind", "upstream_name", "duration_ms", "outcome"),
        ("error",),
        "continuation.fired",
    )
    if err:
        return err
    if not _is_nonempty_str(p["name"]):
        return _err("continuation.fired: name must be a non-empty string")
    if p["upstream_kind"] not in _CONTINUATION_KINDS:
        return _err(f"continuation.fired: upstream_kind must be one of {sorted(_CONTINUATION_KINDS)}")
    if not isinstance(p["upstream_name"], str):
        return _err("continuation.fired: upstream_name must be a string")
    if not _is_nonneg_int(p["duration_ms"]):
        return _err("continuation.fired: duration_ms must be a non-negative integer")
    if p["outcome"] not in _OUTCOMES:
        return _err(f"continuation.fired: outcome must be one of {sorted(_OUTCOMES)}")
    if "error" in p and not isinstance(p["error"], str):
        return _err("continuation.fired: error must be a string")
    return None


def _validate_trigger_fired(p: dict) -> str | None:
    err = _require_keys(
        p,
        ("name", "endpoint", "duration_ms", "outcome"),
        ("error",),
        "trigger.fired",
    )
    if err:
        return err
    if not _is_nonempty_str(p["name"]):
        return _err("trigger.fired: name must be a non-empty string")
    if not isinstance(p["endpoint"], str):
        return _err("trigger.fired: endpoint must be a string")
    if not _is_nonneg_int(p["duration_ms"]):
        return _err("trigger.fired: duration_ms must be a non-negative integer")
    if p["outcome"] not in _OUTCOMES:
        return _err(f"trigger.fired: outcome must be one of {sorted(_OUTCOMES)}")
    if "error" in p and not isinstance(p["error"], str):
        return _err("trigger.fired: error must be a string")
    return None


def _validate_webhook_delivered(p: dict) -> str | None:
    err = _require_keys(
        p,
        ("name", "url_host", "status_code", "duration_ms"),
        (),
        "webhook.delivered",
    )
    if err:
        return err
    if not _is_nonempty_str(p["name"]):
        return _err("webhook.delivered: name must be a non-empty string")
    if not isinstance(p["url_host"], str):
        return _err("webhook.delivered: url_host must be a string")
    if not _is_http_status(p["status_code"]):
        return _err("webhook.delivered: status_code must be an int 100..599")
    if not _is_nonneg_int(p["duration_ms"]):
        return _err("webhook.delivered: duration_ms must be a non-negative integer")
    return None


_WEBHOOK_FAILURE_REASONS = {"timeout", "dns_denied", "http_error", "exception"}


def _validate_webhook_failed(p: dict) -> str | None:
    err = _require_keys(
        p,
        ("name", "url_host", "reason", "duration_ms"),
        ("status_code", "error"),
        "webhook.failed",
    )
    if err:
        return err
    if not _is_nonempty_str(p["name"]):
        return _err("webhook.failed: name must be a non-empty string")
    if not isinstance(p["url_host"], str):
        return _err("webhook.failed: url_host must be a string")
    if p["reason"] not in _WEBHOOK_FAILURE_REASONS:
        return _err(f"webhook.failed: reason must be one of {sorted(_WEBHOOK_FAILURE_REASONS)}")
    if "status_code" in p and not _is_http_status(p["status_code"]):
        return _err("webhook.failed: status_code must be an int 100..599")
    if not _is_nonneg_int(p["duration_ms"]):
        return _err("webhook.failed: duration_ms must be a non-negative integer")
    if "error" in p and not isinstance(p["error"], str):
        return _err("webhook.failed: error must be a string")
    return None


_HOOK_BACKENDS = {"claude", "openai", "codex", "gemini"}
_HOOK_DECISIONS = {"allow", "deny", "warn"}


def _validate_hook_decision(p: dict) -> str | None:
    err = _require_keys(
        p,
        ("backend", "session_id_hash", "tool", "decision"),
        ("rule_id", "reason"),
        "hook.decision",
    )
    if err:
        return err
    if p["backend"] not in _HOOK_BACKENDS:
        return _err(f"hook.decision: backend must be one of {sorted(_HOOK_BACKENDS)}")
    if not _is_nonempty_str(p["session_id_hash"]):
        return _err("hook.decision: session_id_hash must be a non-empty string")
    if not isinstance(p["tool"], str):
        return _err("hook.decision: tool must be a string")
    if p["decision"] not in _HOOK_DECISIONS:
        return _err(f"hook.decision: decision must be one of {sorted(_HOOK_DECISIONS)}")
    if "rule_id" in p and not isinstance(p["rule_id"], str):
        return _err("hook.decision: rule_id must be a string")
    if "reason" in p and not isinstance(p["reason"], str):
        return _err("hook.decision: reason must be a string")
    return None


_A2A_CONCERNS = {"a2a", "heartbeat", "job", "task", "trigger", "continuation"}


def _validate_a2a_received(p: dict) -> str | None:
    err = _require_keys(p, ("concern",), ("model",), "a2a.request.received")
    if err:
        return err
    if p["concern"] not in _A2A_CONCERNS:
        return _err(f"a2a.request.received: concern must be one of {sorted(_A2A_CONCERNS)}")
    if "model" in p and not isinstance(p["model"], str):
        return _err("a2a.request.received: model must be a string")
    return None


def _validate_a2a_completed(p: dict) -> str | None:
    err = _require_keys(
        p,
        ("concern", "outcome", "duration_ms"),
        ("error",),
        "a2a.request.completed",
    )
    if err:
        return err
    if p["concern"] not in _A2A_CONCERNS:
        return _err(f"a2a.request.completed: concern must be one of {sorted(_A2A_CONCERNS)}")
    if p["outcome"] not in _OUTCOMES:
        return _err(f"a2a.request.completed: outcome must be one of {sorted(_OUTCOMES)}")
    if not _is_nonneg_int(p["duration_ms"]):
        return _err("a2a.request.completed: duration_ms must be a non-negative integer")
    if "error" in p and not isinstance(p["error"], str):
        return _err("a2a.request.completed: error must be a string")
    return None


_LIFECYCLE_EVENTS = {"started", "stopped", "config_reloaded", "credential_rotated"}


def _validate_agent_lifecycle(p: dict) -> str | None:
    err = _require_keys(p, ("backend", "event"), ("detail",), "agent.lifecycle")
    if err:
        return err
    if p["backend"] not in _HOOK_BACKENDS:
        return _err(f"agent.lifecycle: backend must be one of {sorted(_HOOK_BACKENDS)}")
    if p["event"] not in _LIFECYCLE_EVENTS:
        return _err(f"agent.lifecycle: event must be one of {sorted(_LIFECYCLE_EVENTS)}")
    if "detail" in p and not isinstance(p["detail"], str):
        return _err("agent.lifecycle: detail must be a string")
    return None


_CONVERSATION_ROLES = {"user", "assistant"}


def _is_session_hash(v: Any) -> bool:
    # 12-char sha256 prefix (lowercase hex).
    return isinstance(v, str) and len(v) == 12


def _validate_conversation_turn(p: dict) -> str | None:
    err = _require_keys(
        p,
        ("session_id_hash", "role", "content_bytes"),
        ("model",),
        "conversation.turn",
    )
    if err:
        return err
    if not _is_session_hash(p["session_id_hash"]):
        return _err("conversation.turn: session_id_hash must be a 12-char string")
    if p["role"] not in _CONVERSATION_ROLES:
        return _err(f"conversation.turn: role must be one of {sorted(_CONVERSATION_ROLES)}")
    if not _is_nonneg_int(p["content_bytes"]):
        return _err("conversation.turn: content_bytes must be a non-negative integer")
    if "model" in p and not isinstance(p["model"], str):
        return _err("conversation.turn: model must be a string")
    return None


def _validate_conversation_chunk(p: dict) -> str | None:
    err = _require_keys(
        p,
        ("session_id_hash", "role", "seq", "content", "final"),
        (),
        "conversation.chunk",
    )
    if err:
        return err
    if not _is_session_hash(p["session_id_hash"]):
        return _err("conversation.chunk: session_id_hash must be a 12-char string")
    if p["role"] not in _CONVERSATION_ROLES:
        return _err(f"conversation.chunk: role must be one of {sorted(_CONVERSATION_ROLES)}")
    if not _is_nonneg_int(p["seq"]):
        return _err("conversation.chunk: seq must be a non-negative integer")
    if not isinstance(p["content"], str):
        return _err("conversation.chunk: content must be a string")
    if not isinstance(p["final"], bool):
        return _err("conversation.chunk: final must be a boolean")
    return None


_TOOL_OUTCOMES = {"ok", "error", "denied"}


def _validate_tool_use(p: dict) -> str | None:
    err = _require_keys(
        p,
        ("session_id_hash", "tool", "duration_ms", "outcome"),
        ("result_size_bytes", "error"),
        "tool.use",
    )
    if err:
        return err
    if not _is_session_hash(p["session_id_hash"]):
        return _err("tool.use: session_id_hash must be a 12-char string")
    if not isinstance(p["tool"], str):
        return _err("tool.use: tool must be a string")
    if not _is_nonneg_int(p["duration_ms"]):
        return _err("tool.use: duration_ms must be a non-negative integer")
    if p["outcome"] not in _TOOL_OUTCOMES:
        return _err(f"tool.use: outcome must be one of {sorted(_TOOL_OUTCOMES)}")
    if "result_size_bytes" in p and not _is_nonneg_int(p["result_size_bytes"]):
        return _err("tool.use: result_size_bytes must be a non-negative integer")
    if "error" in p and not isinstance(p["error"], str):
        return _err("tool.use: error must be a string")
    return None


_SPAN_STATUSES = {"ok", "error"}


def _validate_trace_span(p: dict) -> str | None:
    err = _require_keys(
        p,
        ("span_name", "duration_ms", "status", "service"),
        ("session_id_hash",),
        "trace.span",
    )
    if err:
        return err
    if not isinstance(p["span_name"], str) or not p["span_name"]:
        return _err("trace.span: span_name must be a non-empty string")
    if not _is_nonneg_int(p["duration_ms"]):
        return _err("trace.span: duration_ms must be a non-negative integer")
    if p["status"] not in _SPAN_STATUSES:
        return _err(f"trace.span: status must be one of {sorted(_SPAN_STATUSES)}")
    if not isinstance(p["service"], str):
        return _err("trace.span: service must be a string")
    if "session_id_hash" in p and not _is_session_hash(p["session_id_hash"]):
        return _err("trace.span: session_id_hash must be a 12-char string")
    return None


def _validate_stream_gap(p: dict) -> str | None:
    err = _require_keys(p, ("last_seen_id", "resume_id"), ("reason",), "stream.gap")
    if err:
        return err
    # #1327: require non-empty strings for semantically-required ids.
    if not _is_nonempty_str(p["last_seen_id"]):
        return _err("stream.gap: last_seen_id must be a non-empty string")
    if not _is_nonempty_str(p["resume_id"]):
        return _err("stream.gap: resume_id must be a non-empty string")
    if "reason" in p and not isinstance(p["reason"], str):
        return _err("stream.gap: reason must be a string")
    return None


def _validate_stream_overrun(p: dict) -> str | None:
    err = _require_keys(p, ("queue_depth", "queue_max"), ("reason",), "stream.overrun")
    if err:
        return err
    if not _is_nonneg_int(p["queue_depth"]):
        return _err("stream.overrun: queue_depth must be a non-negative integer")
    if not _is_nonneg_int(p["queue_max"]):
        return _err("stream.overrun: queue_max must be a non-negative integer")
    if "reason" in p and not isinstance(p["reason"], str):
        return _err("stream.overrun: reason must be a string")
    return None


# type → payload validator
_VALIDATORS: dict[str, Callable[[dict], str | None]] = {
    "job.fired": _validate_job_fired,
    "task.fired": _validate_task_fired,
    "heartbeat.fired": _validate_heartbeat_fired,
    "continuation.fired": _validate_continuation_fired,
    "trigger.fired": _validate_trigger_fired,
    "webhook.delivered": _validate_webhook_delivered,
    "webhook.failed": _validate_webhook_failed,
    "hook.decision": _validate_hook_decision,
    "a2a.request.received": _validate_a2a_received,
    "a2a.request.completed": _validate_a2a_completed,
    "agent.lifecycle": _validate_agent_lifecycle,
    "conversation.turn": _validate_conversation_turn,
    "conversation.chunk": _validate_conversation_chunk,
    "tool.use": _validate_tool_use,
    "trace.span": _validate_trace_span,
    "stream.gap": _validate_stream_gap,
    "stream.overrun": _validate_stream_overrun,
}

KNOWN_TYPES: tuple[str, ...] = tuple(_VALIDATORS.keys())


def validate_envelope(envelope: dict) -> str | None:
    """Validate a complete event envelope. Return None on success, error string on failure.

    The harness publish path calls this with an already-assembled
    envelope (type/version/id/ts/agent_id/payload).  Failures must not
    propagate — callers log + drop.
    """
    if not isinstance(envelope, dict):
        return _err("envelope must be a JSON object")
    for key in ("type", "version", "id", "ts", "agent_id", "payload"):
        if key not in envelope:
            return _err(f"envelope: missing required field {key!r}")

    type_ = envelope["type"]
    if not isinstance(type_, str) or type_ not in _VALIDATORS:
        return _err(f"envelope: unknown type {type_!r}")

    version = envelope["version"]
    if not isinstance(version, int) or isinstance(version, bool) or version < 1:
        return _err("envelope: version must be a positive integer")

    if not _is_nonempty_str(envelope["id"]):
        return _err("envelope: id must be a non-empty string")
    if not isinstance(envelope["ts"], str) or not envelope["ts"]:
        return _err("envelope: ts must be a non-empty string")

    agent_id = envelope["agent_id"]
    if agent_id is not None and not isinstance(agent_id, str):
        return _err("envelope: agent_id must be a string or null")

    payload = envelope["payload"]
    if not isinstance(payload, dict):
        return _err("envelope: payload must be an object")

    return _VALIDATORS[type_](payload)
