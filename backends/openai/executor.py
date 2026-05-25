import asyncio
import contextvars
import hashlib
import json
import logging
import os
import re
import subprocess
import time
import uuid
from collections import OrderedDict, deque
from collections.abc import Awaitable, Callable
from contextlib import AsyncExitStack
from datetime import datetime, timezone

from a2a.server.agent_execution import AgentExecutor as A2AAgentExecutor
from a2a.server.agent_execution import RequestContext
from a2a.server.events import EventQueue
from a2a.utils import new_agent_text_message
from agents import (
    Agent,
    ComputerTool,
    ModelSettings,
    RunConfig,
    Runner,
    ShellCallOutcome,
    ShellCommandOutput,
    ShellCommandRequest,
    ShellResult,
    ShellTool,
    SQLiteSession,
    WebSearchTool,
)
from agents.items import ToolCallItem, ToolCallOutputItem
from agents.models.multi_provider import MultiProvider
from computer import BrowserPool
from exceptions import BudgetExceededError, PromptTooLargeError
from log_utils import _append_log
from mcp_command_allowlist import (
    mcp_command_allowed as _openai_mcp_command_allowed,
)
from mcp_command_allowlist import (
    mcp_command_args_safe as _openai_mcp_command_args_safe,
)
from metrics import (
    backend_a2a_last_request_timestamp_seconds,
    backend_a2a_request_duration_seconds,
    backend_a2a_requests_total,
    backend_active_sessions,
    backend_agent_md_revision,
    backend_budget_exceeded_total,
    backend_codex_hooks_denials_total,
    backend_concurrent_queries,
    backend_context_exhaustion_total,
    backend_context_tokens,
    backend_context_tokens_remaining,
    backend_context_usage_percent,
    backend_context_warnings_total,
    backend_empty_prompts_total,
    backend_empty_responses_total,
    backend_file_watcher_restarts_total,
    backend_hook_session_missing_total,
    backend_hooks_active_rules,
    backend_hooks_config_errors_total,
    backend_hooks_denials_total,
    backend_hooks_enforcement_mode,
    backend_hooks_shed_total,
    backend_log_bytes_total,
    backend_log_entries_total,
    backend_log_write_errors_by_logger_total,
    backend_log_write_errors_total,
    backend_lru_cache_utilization_percent,
    backend_mcp_command_rejected_total,
    backend_mcp_config_errors_total,
    backend_mcp_config_reloads_total,
    backend_mcp_outbound_duration_seconds,
    backend_mcp_outbound_requests_total,
    backend_mcp_servers_active,
    backend_model_requests_total,
    backend_openai_hooks_denials_total,
    backend_prompt_length_bytes,
    backend_prompt_too_large_total,
    backend_response_length_bytes,
    backend_running_tasks,
    backend_sdk_client_errors_total,
    backend_sdk_context_fetch_errors_total,
    backend_sdk_errors_total,
    backend_sdk_messages_per_query,
    backend_sdk_query_duration_seconds,
    backend_sdk_query_error_duration_seconds,
    backend_sdk_result_errors_total,
    backend_sdk_session_duration_seconds,
    backend_sdk_time_to_first_message_seconds,
    backend_sdk_tokens_per_query,
    backend_sdk_tool_call_input_size_bytes,
    backend_sdk_tool_calls_per_query,
    backend_sdk_tool_calls_total,
    backend_sdk_tool_duration_seconds,
    backend_sdk_tool_errors_total,
    backend_sdk_tool_result_size_bytes,
    backend_sdk_turns_per_query,
    backend_session_age_seconds,
    backend_session_evictions_total,
    backend_session_history_save_errors_total,
    backend_session_idle_seconds,
    backend_session_path_mismatch_total,
    backend_session_starts_total,
    backend_stderr_lines_per_task,
    backend_streaming_chunks_dropped_total,
    backend_streaming_events_emitted_total,
    backend_task_cancellations_total,
    backend_task_duration_seconds,
    backend_task_error_duration_seconds,
    backend_task_last_error_timestamp_seconds,
    backend_task_last_success_timestamp_seconds,
    backend_task_timeout_headroom_seconds,
    backend_tasks_total,
    backend_tasks_with_stderr_total,
    backend_text_blocks_per_query,
    backend_tool_audit_bytes_per_entry,
    backend_tool_audit_entries_total,
    backend_tool_audit_rotation_pressure_total,
    backend_watcher_events_total,
)
from openai.types.shared import Reasoning
from otel import set_span_error, start_span
from redact import redact_text, should_redact
from tool_audit import (  # type: ignore
    ToolAuditContext as _ToolAuditContext,
)
from tool_audit import (
    ToolAuditMetrics as _ToolAuditMetrics,
)
from tool_audit import (
    log_tool_audit as _shared_log_tool_audit,
)
from validation import parse_max_tokens, sanitize_model_label

logger = logging.getLogger(__name__)

# Per-task session context (#937). The ShellTool baseline deny path fires
# before the Agents SDK exposes session_id to the tool, so hook.decision events
# previously shipped with session_id="" and broke cross-backend correlation
# (webhooks, dashboard forensics). Stashing the current session_id in a
# ContextVar at _run_inner entry lets the shell executor thread it through
# without plumbing new parameters into the SDK's tool-invocation surface.
_current_session_id: contextvars.ContextVar[str] = contextvars.ContextVar("openai_current_session_id", default="")


AGENT_NAME = os.environ.get("AGENT_NAME", "openai")
AGENT_OWNER = os.environ.get("AGENT_OWNER") or AGENT_NAME
AGENT_ID = os.environ.get("AGENT_ID", "openai")

# Backend→harness generic event channel (#1110 phase 3). Import lazily
# to tolerate environments where PYTHONPATH is not set up for the
# shared/ mount yet (unit tests invoked in-tree). Emit sites wrap in
# try/except and never let emission failure propagate.
try:
    from hook_events import schedule_event_post as _schedule_event_post  # type: ignore
except Exception:  # pragma: no cover
    try:
        from shared.hook_events import schedule_event_post as _schedule_event_post  # type: ignore
    except Exception:

        def _schedule_event_post(*_a, **_kw):  # type: ignore
            return False


def _session_id_hash(session_id: str) -> str:
    """Return the 12-char sha256 prefix used in events (#1110 phase 3)."""
    if not session_id:
        return "000000000000"
    return hashlib.sha256(session_id.encode("utf-8")).hexdigest()[:12]


def _emit_event_safe(event_type: str, payload: dict) -> None:
    """Fire-and-forget event emit — never raises into the caller."""
    try:
        _schedule_event_post(event_type, payload, agent_id=AGENT_OWNER or AGENT_NAME)
    except Exception as exc:  # pragma: no cover
        logger.debug("event emit (%s) raised: %r", event_type, exc)


def _default_existing_path(primary: str, legacy: str) -> str:
    """Prefer the new OpenAI path while tolerating legacy .codex mounts."""
    if os.path.exists(primary) or not os.path.exists(legacy):
        return primary
    return legacy


CONVERSATION_LOG = os.environ.get("CONVERSATION_LOG", "/home/agent/logs/conversation.jsonl")
TRACE_LOG = os.environ.get("TRACE_LOG", "/home/agent/logs/tool-activity.jsonl")
AGENT_MD = os.environ.get(
    "OPENAI_AGENT_MD",
    _default_existing_path("/home/agent/.openai/AGENTS.md", "/home/agent/.codex/AGENTS.md"),
)
OPENAI_SESSION_DB = (
    os.environ.get("OPENAI_SESSION_DB")
    or os.environ.get("CODEX_SESSION_DB")
    or _default_existing_path("/home/agent/logs/openai_sessions.db", "/home/agent/logs/codex_sessions.db")
)

OPENAI_CONFIG_TOML = (
    os.environ.get("OPENAI_CONFIG_TOML")
    or os.environ.get("CODEX_CONFIG_TOML")
    or _default_existing_path("/home/agent/.openai/config.toml", "/home/agent/.codex/config.toml")
)
# MCP server config — same wire format as claude's mcp.json so users can
# share the file shape between backends. OpenAI mounts the .openai/ tree by
# default, so the path differs (#432).
MCP_CONFIG_PATH = os.environ.get(
    "MCP_CONFIG_PATH",
    _default_existing_path("/home/agent/.openai/mcp.json", "/home/agent/.codex/mcp.json"),
)

# #1731: Validate the resolved MCP_CONFIG_PATH lives under a documented
# prefix so a hostile env override (e.g. ``MCP_CONFIG_PATH=/etc/passwd``)
# cannot be fed to ``json.load`` -- the parse error path would then leak
# file shape / perms via metric label cardinality and log noise. The
# default mirrors the in-container home for backend pods; tests /
# non-default deployments can override via ``MCP_CONFIG_PATH_ALLOWED_PREFIX``.
# Mirrors backends/gemini/executor.py:303-304 (#1610).
_MCP_CONFIG_PATH_ALLOWED_PREFIX = os.environ.get(
    "MCP_CONFIG_PATH_ALLOWED_PREFIX",
    "/home/agent/",
)

MAX_SESSIONS = max(1, int(os.environ.get("MAX_SESSIONS", "10000")))
TASK_TIMEOUT_SECONDS = int(os.environ.get("TASK_TIMEOUT_SECONDS", "300"))
# Per-chunk timeout for the streaming on_chunk callback. Bounds the SDK event
# loop's wait on a slow A2A consumer so token-budget enforcement and SDK
# iteration are never stalled by backpressure on a single delivery. On timeout
# the chunk is logged and dropped; iteration continues (#539).
STREAM_CHUNK_TIMEOUT_SECONDS = float(os.environ.get("STREAM_CHUNK_TIMEOUT_SECONDS", "5"))
# Percent of max_tokens at which a context-window warning metric is incremented.
# Tunable via env so operators can dial sensitivity without patching the
# binary. Matches the claude knob (#459).
CONTEXT_USAGE_WARN_THRESHOLD = float(os.environ.get("CONTEXT_USAGE_WARN_THRESHOLD", "0.8"))
# Maximum number of bytes of prompt text included in INFO-level log messages.
# Set to 0 to suppress prompt text from logs entirely; set higher for more context.
LOG_PROMPT_MAX_BYTES = int(os.environ.get("LOG_PROMPT_MAX_BYTES", "200"))
# Hard cap on inbound prompt UTF-8 byte length (#1620). A pathological
# caller could otherwise ship a multi-GB prompt and OOM the pod before
# the Agents SDK ever sees it. Default 10 MiB is comfortably above any
# legitimate conversational use; bump via MAX_PROMPT_BYTES env when an
# operator deliberately wants larger inputs (e.g. document ingest jobs).
_MAX_PROMPT_BYTES = int(os.environ.get("MAX_PROMPT_BYTES", str(10 * 1024 * 1024)))
# Cap tool_result "content" payload written to TRACE_LOG (#939). Large
# shell stdout, kubectl --all-namespaces, or MCP payloads would otherwise
# blow the rotation budget and memory on /trace / the dashboard Tool
# Activity tab. 16 KiB default; set to 0 to disable (legacy behaviour).
LOG_TRACE_CONTENT_MAX_BYTES = int(os.environ.get("LOG_TRACE_CONTENT_MAX_BYTES", "16384"))

OPENAI_MODEL = os.environ.get("OPENAI_MODEL") or os.environ.get("CODEX_MODEL") or "gpt-5.5"
OPENAI_REASONING_EFFORT = os.environ.get("OPENAI_REASONING_EFFORT") or os.environ.get("CODEX_REASONING_EFFORT", "")
OPENAI_API_KEY: str | None = os.environ.get("OPENAI_API_KEY") or None


def _current_openai_api_key() -> str | None:
    """Return the live OpenAI API key, honouring hot-reload semantics (#728).

    Reads the env var on every call so a ``OPENAI_API_KEY_FILE`` watcher (or
    an external process that rewrites the env via ``os.environ``) can rotate
    the credential without a pod restart, matching gemini's #1057 pattern.

    Falls back to the module-load value only if the env has been unset since
    import (prevents regression when the watcher is disabled).
    """
    return os.environ.get("OPENAI_API_KEY") or OPENAI_API_KEY or None


def _resolve_model_label(model: str | None) -> str:
    """Resolve a non-empty, cardinality-safe model label for observability (#719).

    Falls back to the module-load ``OPENAI_MODEL`` default, then runs the
    result through ``sanitize_model_label`` so caller-supplied
    ``metadata.model`` values can't blow up Prometheus cardinality by
    injecting a fresh UUID per request. Using ``"unknown"`` (not ``""``)
    keeps Prometheus series and OTel span attributes filterable in
    dashboards and avoids phantom empty-string label values (#570).
    """
    return sanitize_model_label(model or OPENAI_MODEL or None)


_BACKEND_ID = "openai"
_LABELS = {"agent": AGENT_OWNER, "agent_id": AGENT_ID, "backend": _BACKEND_ID}


# Env var keys that must not be overridden by caller-supplied values because
# they influence binary resolution or dynamic-linker / interpreter behavior
# and could be used for privilege escalation or code injection.
_SHELL_ENV_DENYLIST: frozenset[str] = frozenset(
    {
        "PATH",
        "LD_PRELOAD",
        "LD_LIBRARY_PATH",
        "LD_AUDIT",
        "LD_DEBUG",
        "PYTHONPATH",
        "PYTHONSTARTUP",
        "PYTHONINSPECT",
        "RUBYLIB",
        "RUBYOPT",
        "PERL5LIB",
        "PERL5OPT",
        "NODE_PATH",
        "DYLD_INSERT_LIBRARIES",
        "DYLD_LIBRARY_PATH",
        "DYLD_FRAMEWORK_PATH",
        # #1054: proxy + TLS trust + key-log env vars. A caller-supplied
        # override here can silently route all downstream HTTP(S) traffic
        # through an attacker proxy, swap the CA bundle to one they control
        # (MITM) or dump TLS pre-master secrets for offline decryption.
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
        "NO_PROXY",
        "http_proxy",
        "https_proxy",
        "all_proxy",
        "no_proxy",
        "SSL_CERT_FILE",
        "SSL_CERT_DIR",
        "REQUESTS_CA_BUNDLE",
        "CURL_CA_BUNDLE",
        "NODE_EXTRA_CA_CERTS",
        "SSLKEYLOGFILE",
    }
)


# Audit rows share TRACE_LOG with SDK tool events, discriminated by
# ``event_type`` ("tool_audit" vs "tool_use"/"tool_result"). The two
# feeds were consolidated so downstream UIs can show a single
# "Tool Activity" tab.


# PreToolUse deny EXAMPLES for ShellTool (#586, #722 — shell-only scope).
#
# IMPORTANT SCOPE NOTE (#722): these rules are textual regex matches
# against the space-joined argv.  They provide ergonomic protection
# against obvious fat-finger / prompt-injection patterns, NOT a
# security boundary.  Any determined caller can bypass them with
# trivial lexical rewrites (``sh -c 'rm -rf /'``, ``tee /dev/sda``,
# ``bash <(curl ...)``, heredocs, paths like ``/etc/shadow``, chmod
# g+w on a setuid binary, etc.).
#
# For real containment rely on the container's read-only filesystem
# mounts, non-root UID, and CAP_DROP=ALL — not on this list.  The
# rules below have been expanded beyond the initial five to cover a
# handful of commonly-observed LLM fat-fingers (rm -rf /etc, ``sh -c``
# wrapper, ``tee`` to block devices, chmod +s for setuid) but operators
# should treat them as an opinionated deny-starter, not a baseline in
# the audit sense.
#
# Mirrors claude's ``baseline-*`` rule-name prefix so the
# ``backend_hooks_denials_total{rule=...}`` label keeps cross-backend
# dashboard parity, while the comment block above documents the real
# scope.
#
# #1350: These rules are ADVISORY-not-enforcement. The ``baseline-*``
# prefix on the metric label is load-bearing for dashboard parity, so
# renaming is a breaking change. Operators reading dashboards must
# understand that shell access is trivially bypassable (sh -c, heredoc,
# env substitution) — the rules filter fat-finger mistakes, not a
# determined attacker. The alerts-runbook and AGENTS.md call this out
# explicitly; the rule name alone is not a contract.
_SHELL_DENY_RULES: tuple[tuple[str, "re.Pattern[str]", str], ...] = (
    (
        "baseline-rm-rf-root",
        # Widen the target set to include common system paths a fat-finger
        # rm can still nuke — /etc, /var, /usr, /boot. The flag-parsing
        # regex still handles -rf / -fr / -r -f variants.
        re.compile(
            r"\brm\s+(-[a-zA-Z]*r[a-zA-Z]*f|-[a-zA-Z]*f[a-zA-Z]*r|-r\s+-f|-f\s+-r)\s+"
            r"(/|/\*|~|\$HOME|/\$|/etc\b|/var\b|/usr\b|/boot\b|/lib\b|/bin\b|/sbin\b)",
            re.IGNORECASE,
        ),
        "rm -rf of /, ~, $HOME, or a critical system dir",
    ),
    (
        "baseline-git-force-push-main",
        re.compile(
            r"\bgit\s+push\b.*\b--force\b.*\b(main|master)\b|\bgit\s+push\b.*\b(main|master)\b.*--force\b|\bgit\s+push\s+-f\b.*\b(main|master)\b",
            re.IGNORECASE,
        ),
        "git force-push to main/master",
    ),
    (
        "baseline-curl-pipe-shell",
        # Add wget plus the bash <() process-substitution variant since
        # both are common LLM-generated install commands.
        re.compile(r"\b(curl|wget)\b[^|]*\|\s*(sh|bash|zsh|python3?)\b|\bbash\s+<\(\s*(curl|wget)\b", re.IGNORECASE),
        "curl | sh / bash | zsh / python pipeline",
    ),
    (
        "baseline-chmod-777",
        re.compile(r"\bchmod\b\s+(-R\s+)?[0-7]*777\b"),
        "chmod 777",
    ),
    (
        "baseline-chmod-setuid",
        re.compile(r"\bchmod\b\s+(-R\s+)?(u\+s|[24]7[0-7][0-7])\b"),
        "chmod setuid bit",
    ),
    (
        "baseline-dd-device",
        re.compile(r"\bdd\b.*\bof=/dev/(sd|nvme|hd|xvd)", re.IGNORECASE),
        "dd of=/dev/<block-device>",
    ),
    (
        "baseline-tee-device",
        # Catches ``... | tee /dev/sda`` and friends — tee to a raw block
        # device has the same blast radius as dd.
        re.compile(r"\btee\b[^|&;]*\s/dev/(sd|nvme|hd|xvd)", re.IGNORECASE),
        "tee to /dev/<block-device>",
    ),
    (
        "baseline-sh-c-destructive-wrapper",
        # ``sh -c 'rm -rf /'`` trivially bypassed the original argv
        # match. Flag the wrapper when the inner command itself contains
        # an obvious destructive token so we don't false-positive on
        # benign sh -c helpers.
        re.compile(
            r"\b(sh|bash|zsh)\s+-c\b.*\b(rm\s+-rf?|mkfs|dd\s+of=/dev/|:\(\)\s*\{)",
            re.IGNORECASE,
        ),
        "shell -c wrapping a destructive command",
    ),
)


# Import the shared predicate-based baseline at module load so an import
# failure is visible at backend startup rather than silently yielding an
# empty list per-call (#938). A rename or bug in shared/hooks_engine.py
# previously left the legacy regex table as the only line of defence —
# while shell-baseline log lines still appeared normal — a false sense
# of parity with claude.
try:
    from hooks_engine import BASELINE_RULES as _SHARED_BASELINE_RULES  # type: ignore
except Exception as _baseline_import_exc:  # noqa: BLE001 — documented single-fail path
    logger.warning(
        "openai: failed to import shared BASELINE_RULES from hooks_engine: %r — "
        "predicate baseline DISABLED; legacy regex table remains active (#938)",
        _baseline_import_exc,
    )
    _SHARED_BASELINE_RULES: list = []
    if backend_hooks_config_errors_total is not None:
        try:
            backend_hooks_config_errors_total.labels(
                **_LABELS,
                reason="baseline_import",
            ).inc()
        except Exception:
            # Metric emission must never mask the import failure.
            pass


def _evaluate_shell_baseline(cmd_parts: list[str]) -> tuple[str, str] | None:
    """Return (rule, reason) for the first matching baseline rule, else None.

    ``cmd_parts`` is the argv list as supplied by the SDK. Joined on single
    spaces before matching so the regex authors don't need to guess quoting.

    Consults the shared ``BASELINE_RULES`` first (#807) so any rule added
    to the canonical engine (used by claude's hook path) is picked up on
    openai automatically rather than needing a manual back-port. Falls
    back to the legacy in-file regex rules for coverage parity during
    the transition.
    """
    joined = " ".join(cmd_parts)
    # Shared baseline (predicate-based). Pass the joined command through
    # as ``tool_input['command']`` so predicates matching claude's Bash
    # shape accept openai's ShellTool command strings transparently. Import
    # happened at module load (#938); use the cached symbol.
    BASELINE_RULES = _SHARED_BASELINE_RULES
    shared_input = {"command": joined}
    for rule in BASELINE_RULES:
        # Only Bash-scoped rules apply to the shell executor; Write/Edit
        # rules are irrelevant to ShellTool commands.
        if getattr(rule, "tool", None) != "Bash":
            continue
        predicate = getattr(rule, "predicate", None)
        if predicate is None:
            continue
        try:
            if predicate(shared_input):
                return rule.name, rule.reason
        except Exception as _pred_exc:
            # A predicate bug must not block a dispatch — fall through to
            # the legacy table so operators still see the regex denials.
            # #1055: surface the predicate fault via
            # backend_hooks_config_errors_total{reason='predicate_runtime'}
            # so silent swallowing doesn't hide a broken baseline rule.
            logger.warning(
                "_evaluate_shell_baseline predicate raised for rule=%s: %r",
                getattr(rule, "name", "?"),
                _pred_exc,
            )
            if backend_hooks_config_errors_total is not None:
                try:
                    backend_hooks_config_errors_total.labels(
                        **_LABELS,
                        reason="predicate_runtime",
                    ).inc()
                except Exception:
                    pass
            continue
    for rule, pattern, reason in _SHELL_DENY_RULES:
        if pattern.search(joined):
            return rule, reason
    return None


def _pre_tool_use_gate(
    tool_name: str,
    tool_input: dict,
    rules: list | None = None,
) -> tuple[str, str] | None:
    """Centralised PreToolUse gate for non-shell tools (#1862, SCAFFOLD).

    Invokes ``shared/hooks_engine.evaluate_pre_tool_use`` with the tool name
    and a dict-shaped input payload. Returns ``None`` on allow, or a
    ``(rule_name, reason)`` tuple on deny so callers can emit the
    ``backend_hooks_denials_total`` counter and raise a deny exception with
    consistent wording.

    STATUS — this function is ready to fire but has **no callers yet for
    non-shell tools**. The Agents SDK does not expose
    a per-tool-call pre-invoke callback for ``WebSearchTool``, ``ComputerTool``,
    or MCP stdio/HTTP tools; only ``ShellTool`` accepts an ``executor=``
    which is already gated by :func:`_evaluate_shell_baseline`. Invoking
    this gate for the remaining tools requires either:

      1. Upstream: a pre-invoke callback on the SDK's tool dispatcher, or
      2. In-repo: wrapping each tool with a thin subclass whose ``invoke``
         method calls this gate before delegating — feasible but non-trivial
         because ``Computer`` and the MCP ``MCPServer*`` classes have internal
         contracts we'd be replacing.

    Until one of those lands, this helper centralises the evaluate call so
    new interposition points can wire in with a single line:

        denied = _pre_tool_use_gate(name, input)
        if denied is not None:
            raise HookDenyError(*denied)

    TODO(#1862): subclass ``WebSearchTool`` / ``ComputerTool`` / add an MCP
    proxy wrapper and route every invocation through this gate. When done,
    remove the "scaffold" wording from this docstring and add coverage in
    backends/openai/tests/.
    """
    try:
        from hooks_engine import evaluate_pre_tool_use  # type: ignore
    except Exception as _imp_exc:
        logger.warning(
            "_pre_tool_use_gate: hooks_engine import failed (%r); fail-open for %s.",
            _imp_exc,
            tool_name,
        )
        return None
    _rules = rules if rules is not None else list(_SHARED_BASELINE_RULES or [])
    try:
        # Shared-engine contract (#1194): returns (decision, matched_rule).
        decision, matched = evaluate_pre_tool_use(tool_name, tool_input, _rules)
    except Exception as _eval_exc:
        logger.warning(
            "_pre_tool_use_gate: evaluate raised for %s: %r — fail-open.",
            tool_name,
            _eval_exc,
        )
        return None
    if str(decision).lower() == "deny" and matched is not None:
        return (
            str(getattr(matched, "name", "?")),
            str(getattr(matched, "reason", "denied")),
        )
    return None


async def _append_tool_audit(entry: dict) -> None:
    """Append an ``event_type='tool_audit'`` row via the shared helper (#858).

    Delegates to ``shared/tool_audit.py::log_tool_audit`` so the openai and
    claude write paths converge on one implementation: async via
    ``asyncio.to_thread`` over ``_append_log`` (fcntl lock + rotation),
    with the same metric bookkeeping, same error swallowing, and identical
    row shape (``event_type`` stamp + JSON serialisation).
    """
    await _shared_log_tool_audit(
        _ToolAuditContext(
            trace_log_path=TRACE_LOG,
            labels=_LABELS,
            metrics=_ToolAuditMetrics(
                tool_audit_entries_total=backend_tool_audit_entries_total,
                log_entries_total=backend_log_entries_total,
                log_bytes_total=backend_log_bytes_total,
                log_write_errors_total=backend_log_write_errors_total,
                log_write_errors_by_logger_total=backend_log_write_errors_by_logger_total,
                # #1102: size / rotation observability on tool-activity.jsonl.
                tool_audit_bytes_per_entry=backend_tool_audit_bytes_per_entry,
                tool_audit_rotation_pressure_total=backend_tool_audit_rotation_pressure_total,
            ),
        ),
        entry,
    )


class ShellBlockedError(RuntimeError):
    """Raised by _shell_executor when a shell command fails the baseline deny
    policy (#670). Surfacing the error as an exception lets the Agents SDK
    flag the tool result with ``is_error=True`` so
    ``backend_sdk_tool_errors_total`` and trace JSONL reflect the failure.
    """


class ShellExecutionError(RuntimeError):
    """Raised by _shell_executor when subprocess.run itself faults."""


async def _shell_executor(req: ShellCommandRequest) -> ShellResult:
    # tool.call child span (#630) — the ShellTool invocation path. Kept
    # around the full executor body so baseline-deny short-circuits and
    # subprocess.run are both inside the span, not just the allowed branch.
    with start_span(
        "tool.call",
        kind="internal",
        attributes={"tool.name": "Shell"},
    ) as _tool_span:
        try:
            return await _shell_executor_inner(req)
        except BaseException as _exc:
            set_span_error(_tool_span, _exc)
            raise


async def _shell_executor_inner(req: ShellCommandRequest) -> ShellResult:
    action = req.data.action
    timeout_ms = action.timeout_ms
    # Clamp non-positive timeouts to the 30s default (#879). Previously
    # `if timeout_ms else 30.0` accepted any truthy value — including
    # negatives — so timeout_ms=-1 produced timeout_s=-0.001 which
    # subprocess.run treats as already-expired and raises
    # TimeoutExpired instantly. Prompt-injectable DoS via
    # ShellCommandRequest. Additionally enforce a 1s floor so a
    # legitimately tiny-positive value (e.g. timeout_ms=1) still gives
    # the subprocess enough time to execute — otherwise every
    # invocation times out instantly regardless of the command
    # (#987).
    _SHELL_TIMEOUT_MIN_S = 1.0
    if timeout_ms and timeout_ms > 0:
        timeout_s = max(_SHELL_TIMEOUT_MIN_S, timeout_ms / 1000.0)
    else:
        timeout_s = 30.0
    outputs: list[ShellCommandOutput] = []
    for cmd in action.commands:
        await _check_shell_command_allowed(cmd)
        await _append_tool_audit(
            {
                "ts": time.time(),
                "tool": "Shell",
                "decision": "allow",
                "command": cmd,
            }
        )
        try:
            result = await asyncio.to_thread(
                subprocess.run,
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout_s,
                shell=True,
                executable="/bin/bash",
                env=_safe_shell_env(),
            )
            outputs.append(
                ShellCommandOutput(
                    command=cmd,
                    stdout=result.stdout or "",
                    stderr=result.stderr or "",
                    outcome=ShellCallOutcome(type="exit", exit_code=result.returncode),
                )
            )
        except subprocess.TimeoutExpired as exc:
            outputs.append(
                ShellCommandOutput(
                    command=cmd,
                    stdout=_timeout_stream_to_text(exc.stdout),
                    stderr=_timeout_stream_to_text(exc.stderr),
                    outcome=ShellCallOutcome(type="timeout"),
                )
            )
            break
        except Exception as exc:
            raise ShellExecutionError(f"Shell error: {exc}") from exc
    return ShellResult(output=outputs, max_output_length=action.max_output_length)


def _timeout_stream_to_text(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return value.decode(errors="replace")


def _safe_shell_env() -> dict[str, str]:
    return {k: os.environ[k] for k in ("PATH", "HOME", "USER", "TMPDIR", "LANG", "LC_ALL") if k in os.environ}


async def _check_shell_command_allowed(cmd: str) -> None:
    # PreToolUse deny baseline (#586 shell-only).
    _denied = _evaluate_shell_baseline([cmd])
    if _denied is None:
        return
    rule, reason = _denied
    if backend_openai_hooks_denials_total is not None:
        try:
            backend_openai_hooks_denials_total.labels(**_LABELS, rule=rule).inc()
        except Exception:
            pass
    if backend_codex_hooks_denials_total is not None:
        try:
            backend_codex_hooks_denials_total.labels(**_LABELS, rule=rule).inc()
        except Exception:
            pass
    # Canonical cross-backend name (#789). openai's baseline is
    # shell-only so tool='shell' and source='baseline' are fixed.
    if backend_hooks_denials_total is not None:
        try:
            backend_hooks_denials_total.labels(
                **_LABELS,
                tool="shell",
                source="baseline",
                rule=rule,
            ).inc()
        except Exception:
            pass
    await _append_tool_audit(
        {
            "ts": time.time(),
            "tool": "Shell",
            "decision": "deny",
            "rule": rule,
            "reason": reason,
            "command": cmd,
        }
    )
    # Backend→harness hook.decision side-channel (#779). Claude's
    # executor carries its own equivalent for the full PreToolUse
    # surface; openai emits only on shell-baseline denials today
    # since those are the only hooks it evaluates. gemini is
    # blocked on #1863 (AFC/tool-loop interposition) before it can
    # emit similarly.
    try:
        import hook_events as _hook_events

        # Pass the pre-labelled shed counter so sustained shedding is
        # visible on dashboards (#957). Prior to this commit
        # schedule_post's one-shot WARN fired once and went silent,
        # leaving a cap-reached storm undetectable.
        _shed_counter = backend_hooks_shed_total.labels(**_LABELS) if backend_hooks_shed_total is not None else None
        _sid = _current_session_id.get()
        if not _sid:
            # #1052: empty session_id means a baseline check fired
            # outside the normal _run_inner dispatch path (warmup,
            # lifespan, /mcp tools/call). Treat as a defect surface —
            # WARN and bump a dedicated counter so dashboards catch
            # the regression class that #937 closed for the primary
            # path only.
            logger.warning(
                "_shell_executor: emitting hook.decision with empty session_id (edge-dispatch path?) rule=%s",
                rule,
            )
            if backend_hook_session_missing_total is not None:
                try:
                    backend_hook_session_missing_total.labels(
                        **_LABELS,
                        tool="shell",
                        source="baseline",
                    ).inc()
                except Exception:
                    pass
        _hook_events.schedule_post(
            {
                # #1149: send BACKEND id (openai/claude/gemini), not
                # the named witwave agent — see claude/executor.py
                # _event_dict for the full rationale.
                "agent": _BACKEND_ID,
                # #937: carry the per-task session_id from the ContextVar
                # seeded in _run_inner. Falls back to "" only when the
                # executor is driven outside a normal task run (tests,
                # warmup, etc.) — same semantics as the old hard-coded "".
                # #1052: empty-value case is now instrumented above.
                "session_id": _sid,
                "tool": "shell",
                "decision": "deny",
                "rule_name": rule,
                "reason": reason,
                "source": "baseline",
                "traceparent": None,
            },
            shed_counter=_shed_counter,
        )
    except Exception as _hev_exc:
        logger.debug("hook.decision transport scheduling failed: %r", _hev_exc)
    logger.warning("_shell_executor: baseline deny rule=%s cmd=%r", rule, cmd)
    # Raise so the SDK flags the tool result as is_error=True; the
    # denial counter above still increments before the exception
    # (#670).
    raise ShellBlockedError(f"Command blocked by shell baseline rule '{rule}': {reason}")


def _load_tool_config() -> dict:
    """Read [tools] table from config.toml. Returns empty dict if file absent or unparseable."""
    try:
        import tomllib
    except ImportError:
        try:
            import tomli as tomllib  # type: ignore
        except ImportError:
            return {}
    try:
        with open(OPENAI_CONFIG_TOML, "rb") as f:
            data = tomllib.load(f)
        return data.get("tools", {})
    except Exception as exc:
        logger.warning("Could not read tool config from %s: %s", OPENAI_CONFIG_TOML, exc)
        return {}


def _load_mcp_config() -> dict:
    """Load and normalise the MCP server config from MCP_CONFIG_PATH (#432).

    Accepts both the Claude-native shape (`{"mcpServers": {...}}`) and a flat
    `{server_name: {...}}` dict, returning the inner dict in both cases.
    Missing file is treated as "no MCP servers" (returns {}). Parse / I/O
    errors return {} AND increment backend_mcp_config_errors_total.

    Path posture (#1731 — gemini parity port of #1610): the resolved
    (``os.path.realpath``) MCP_CONFIG_PATH must live under
    ``MCP_CONFIG_PATH_ALLOWED_PREFIX`` (default ``/home/agent/``). This
    blocks a hostile env override that would point the loader at
    arbitrary files such as ``/etc/passwd`` or
    ``/var/run/secrets/kubernetes.io/serviceaccount/token``. The parse
    error path would otherwise leak file shape / perms via metric label
    cardinality and log noise. Out-of-prefix paths are skipped with a
    WARN log; missing-file semantics are unchanged.
    """
    if not os.path.exists(MCP_CONFIG_PATH):
        return {}
    resolved = os.path.realpath(MCP_CONFIG_PATH)
    if not resolved.startswith(_MCP_CONFIG_PATH_ALLOWED_PREFIX):
        logger.warning(
            "MCP config path %s (resolved %s) is outside allowed prefix %s; skipping load.",
            MCP_CONFIG_PATH,
            resolved,
            _MCP_CONFIG_PATH_ALLOWED_PREFIX,
        )
        return {}
    try:
        with open(MCP_CONFIG_PATH) as f:
            data = json.load(f)
        if isinstance(data, dict) and "mcpServers" in data and isinstance(data["mcpServers"], dict):
            return data["mcpServers"]
        if isinstance(data, dict):
            return data
        logger.warning("MCP config at %s is not a dict; ignoring.", MCP_CONFIG_PATH)
        return {}
    except Exception as e:
        if backend_mcp_config_errors_total is not None:
            backend_mcp_config_errors_total.labels(**_LABELS).inc()
        logger.warning("Failed to load MCP config from %s: %s", MCP_CONFIG_PATH, e)
        return {}


# MCP stdio command allow-list (#720 — parity with claude #711). Without
# this guard, a malicious mcp.json landed via gitSync or the WitwavePrompt
# path could spawn an arbitrary binary inside the openai backend pod.
#
# #964: The rule now lives in shared/mcp_command_allowlist.py — the
# three backends used to carry forked copies that drifted on defaults,
# metric reasons, and the absolute-path fallback behaviour (#862). The
# shared helper is the source of truth; openai keeps only its cwd allow-list
# (cwd wasn't covered by the shared module at the time of consolidation).

_DEFAULT_OPENAI_MCP_ALLOWED_CWD_PREFIXES = "/home/agent/,/tmp/"
_OPENAI_MCP_ALLOWED_CWD_PREFIXES: tuple[str, ...] = tuple(
    t.strip()
    for t in os.environ.get(
        "MCP_ALLOWED_CWD_PREFIXES",
        _DEFAULT_OPENAI_MCP_ALLOWED_CWD_PREFIXES,
    ).split(",")
    if t.strip()
)


def _openai_mcp_cwd_allowed(cwd: str) -> tuple[bool, str]:
    """Return (ok, reason) for an MCP stdio ``cwd`` value (#720).

    Only absolute paths whose prefix matches MCP_ALLOWED_CWD_PREFIXES are
    accepted.  Relative cwd is rejected — an attacker-controlled relative
    path combined with a permitted command basename could still break
    out of the intended working directory.
    """
    if not isinstance(cwd, str):
        return False, "cwd_non_string"
    c = cwd.strip()
    if not c:
        return False, "cwd_empty"
    if not c.startswith("/"):
        return False, "cwd_not_absolute"
    for prefix in _OPENAI_MCP_ALLOWED_CWD_PREFIXES:
        if c.startswith(prefix):
            return True, "cwd_allowed"
    return False, "cwd_not_on_prefix"


def _build_mcp_servers(mcp_config: dict) -> list:
    """Convert an MCP config dict into OpenAI Agents SDK MCPServer instances (#432).

    Each entry's transport is detected from its shape:
    - has 'command' key  → MCPServerStdio (subprocess transport)
    - has 'url' key      → MCPServerStreamableHttp (preferred HTTP transport)

    Returned servers are NOT yet entered as context managers — the caller is
    responsible for entering them via AsyncExitStack before passing to
    Agent(mcp_servers=[...]). Each entry that fails to instantiate is logged
    and skipped so a single bad entry does not break unrelated MCP servers.

    Every stdio entry runs through the command + cwd allow-list
    (#720). Rejected entries are dropped so a mis-merged mcp.json
    cannot trigger subprocess execution of an unauthorised binary.
    """
    if not mcp_config:
        return []
    try:
        from agents.mcp import MCPServerStdio, MCPServerStreamableHttp
    except Exception as _imp_exc:
        logger.warning(
            "openai-agents SDK does not expose agents.mcp servers (%s); "
            "MCP support disabled — install a newer openai-agents.",
            _imp_exc,
        )
        return []

    servers = []
    for name, cfg in mcp_config.items():
        if not isinstance(cfg, dict):
            logger.warning("MCP server %r: config must be a dict; got %r — skipping.", name, type(cfg).__name__)
            continue
        try:
            if "command" in cfg:
                # Validate command against the allow-list BEFORE any
                # other field processing so a rejection is logged and
                # counted cheaply (#720).
                cmd_ok, cmd_reason = _openai_mcp_command_allowed(cfg["command"])
                if not cmd_ok:
                    logger.warning(
                        "MCP server %r: command %r rejected by allow-list "
                        "(%s) — dropping entry. Set MCP_ALLOWED_COMMANDS "
                        "/ MCP_ALLOWED_COMMAND_PREFIXES to widen. (#720)",
                        name,
                        cfg["command"],
                        cmd_reason,
                    )
                    if backend_mcp_command_rejected_total is not None:
                        try:
                            backend_mcp_command_rejected_total.labels(
                                **_LABELS,
                                reason=cmd_reason,
                            ).inc()
                        except Exception:
                            pass
                    continue
                if "cwd" in cfg:
                    cwd_ok, cwd_reason = _openai_mcp_cwd_allowed(cfg["cwd"])
                    if not cwd_ok:
                        logger.warning(
                            "MCP server %r: cwd %r rejected by allow-list "
                            "(%s) — dropping entry. Set "
                            "MCP_ALLOWED_CWD_PREFIXES to widen. (#720)",
                            name,
                            cfg["cwd"],
                            cwd_reason,
                        )
                        if backend_mcp_command_rejected_total is not None:
                            try:
                                backend_mcp_command_rejected_total.labels(
                                    **_LABELS,
                                    reason=cwd_reason,
                                ).inc()
                            except Exception:
                                pass
                        continue
                # Args sanitiser (#1734 / #930). When ``command`` is an
                # interpreter (uv, uvx, python, node, …), its ``args``
                # array can still deliver arbitrary code via -c / -e /
                # positional script paths. Drop the entry so a widened
                # MCP_ALLOWED_COMMANDS doesn't silently re-open the
                # arbitrary-code-execution path the README promises is
                # closed.
                args_ok, args_reason = _openai_mcp_command_args_safe(
                    cfg["command"],
                    cfg.get("args"),
                )
                if not args_ok:
                    logger.warning(
                        "MCP server %r: args for command %r rejected "
                        "by args sanitiser (%s) — dropping entry. "
                        "Adjust the config or set "
                        "MCP_ALLOWED_CWD_PREFIXES if a positional "
                        "script lives in an operator-vetted tree. "
                        "(#1734)",
                        name,
                        cfg["command"],
                        args_reason,
                    )
                    if backend_mcp_command_rejected_total is not None:
                        try:
                            backend_mcp_command_rejected_total.labels(
                                **_LABELS,
                                reason=args_reason,
                            ).inc()
                        except Exception:
                            pass
                    continue
                params = {"command": cfg["command"]}
                if "args" in cfg:
                    params["args"] = list(cfg["args"])
                if "env" in cfg:
                    # Apply the same loader/interpreter env denylist used by
                    # _shell_executor (#248) to MCP stdio env — MCPServerStdio
                    # spawns a subprocess with identical code-injection risk
                    # profile. Strip keys that could be used to hijack binary
                    # resolution or dynamic-linker / interpreter behavior
                    # before passing env to the SDK (#519).
                    raw_env = dict(cfg["env"])
                    sanitized_env = {k: v for k, v in raw_env.items() if k not in _SHELL_ENV_DENYLIST}
                    rejected = set(raw_env) - set(sanitized_env)
                    if rejected:
                        logger.warning(
                            "MCP server %r: stripped dangerous env vars from config env: %s",
                            name,
                            sorted(rejected),
                        )
                    params["env"] = sanitized_env
                if "cwd" in cfg:
                    params["cwd"] = cfg["cwd"]
                servers.append(MCPServerStdio(name=name, params=params))
            elif "url" in cfg:
                params = {"url": cfg["url"]}
                # #1332: if the entry has no Authorization header and an
                # MCP_TOOL_AUTH_TOKEN env var is set, auto-stamp a Bearer
                # header so operators don't have to embed the token in
                # mcp.json. Explicit headers still win.
                raw_headers = dict(cfg.get("headers") or {})
                _mcp_env_token = os.environ.get("MCP_TOOL_AUTH_TOKEN", "").strip()
                _has_auth = any(isinstance(k, str) and k.strip().lower() == "authorization" for k in raw_headers)
                if _mcp_env_token and not _has_auth:
                    raw_headers["Authorization"] = f"Bearer {_mcp_env_token}"
                if raw_headers:
                    # #1056: restrict allowed header names to a safe set so
                    # mcp.json can't inject arbitrary request headers
                    # (Host override, Forwarded-For spoof, caller impersonation
                    # via X-*). Anything outside the allow-list is dropped.
                    allowed_hdr_prefixes = ("x-",)
                    allowed_hdr_names = {
                        "authorization",
                        "accept",
                        "accept-encoding",
                        "content-type",
                        "user-agent",
                    }
                    safe_headers: dict[str, str] = {}
                    dropped: list[str] = []
                    for hk, hv in raw_headers.items():
                        if not isinstance(hk, str) or not isinstance(hv, str):
                            dropped.append(str(hk))
                            continue
                        lowered = hk.strip().lower()
                        ok = lowered in allowed_hdr_names or any(lowered.startswith(p) for p in allowed_hdr_prefixes)
                        if ok:
                            safe_headers[hk] = hv
                        else:
                            dropped.append(hk)
                    if dropped:
                        # Log names only — never values. Values may contain
                        # tokens; the operator-visible signal is the header
                        # key list.
                        logger.warning(
                            "MCP server %r: dropped %d disallowed header(s) %s — "
                            "only Authorization/Accept/Content-Type/User-Agent and "
                            "X-* are allowed. (#1056)",
                            name,
                            len(dropped),
                            sorted(dropped),
                        )
                    params["headers"] = safe_headers
                servers.append(MCPServerStreamableHttp(name=name, params=params))
            else:
                logger.warning(
                    "MCP server %r: missing both 'command' and 'url'; cannot determine transport — skipping.",
                    name,
                )
        except Exception as _e:
            logger.warning("MCP server %r: failed to instantiate (%s); skipping.", name, _e)
    return servers


_browser_pool: BrowserPool | None = None
# _computer_lock is initialized in main() inside asyncio.run() so that it is
# always created within the running event loop.  A module-level asyncio.Lock()
# causes a DeprecationWarning in Python 3.10+ and wrong-loop attachment in
# Python 3.12+ (#378).  Do not set this at import time.
_computer_lock: asyncio.Lock | None = None

# Serialises the LRU evict/insert block in _track_session (#506). Without
# this, a concurrent caller can observe the shared OrderedDict in a
# transient under-capacity state between popitem(last=False) and the
# post-await sessions[session_id] = ... insertion — leading to
# over-eviction, mis-counted metrics, and redundant SQLite deletes.
#
# Historically this was double-checked-lazy-initialised at two call sites
# (#506 / #668); that pattern risked two ``asyncio.Lock()`` instances under
# concurrent initialisation and made it easy for a future caller to omit
# the double-check (#725).  ``_get_sessions_lock`` is now the sole
# constructor — ``main.py`` also eagerly seeds it inside ``asyncio.run``
# for parity with ``_computer_lock``.  The helper remains safe to call
# lazily from tests that skip the main() bootstrap.
_sessions_lock: asyncio.Lock | None = None


def _get_sessions_lock() -> asyncio.Lock:
    """Return the process-wide ``_sessions_lock``, creating it if needed (#725).

    Centralises the previously duplicated lazy-init so every future call
    site goes through one path and we cannot end up with two Lock
    instances racing each other under concurrent first-touch.  Relies on
    CPython's GIL to make the ``is None`` → assignment pair effectively
    atomic for the asyncio.Lock constructor (no I/O, no awaits).
    """
    global _sessions_lock
    if _sessions_lock is None:
        _sessions_lock = asyncio.Lock()
    return _sessions_lock


_COMPUTER_SUPPORTED_MODELS = {"computer-use-preview", "gpt-5.5"}
_COMPUTER_SUPPORTED_MODEL_PREFIXES = ("gpt-5.5-",)


def _supports_computer_tool(model: str) -> bool:
    return model in _COMPUTER_SUPPORTED_MODELS or model.startswith(_COMPUTER_SUPPORTED_MODEL_PREFIXES)


_REASONING_EFFORT_ALIASES = {
    "extra high": "xhigh",
    "extra-high": "xhigh",
    "extra_high": "xhigh",
    "x-high": "xhigh",
}
_REASONING_EFFORTS = {"minimal", "low", "medium", "high", "xhigh"}


def _build_model_settings() -> ModelSettings:
    effort = OPENAI_REASONING_EFFORT.strip().lower()
    effort = _REASONING_EFFORT_ALIASES.get(effort, effort)
    if not effort:
        return ModelSettings()
    if effort not in _REASONING_EFFORTS:
        logger.warning(
            "Ignoring unsupported OPENAI_REASONING_EFFORT=%r; expected one of %s.",
            OPENAI_REASONING_EFFORT,
            sorted(_REASONING_EFFORTS),
        )
        return ModelSettings()
    return ModelSettings(reasoning=Reasoning(effort=effort))


async def _build_tools(model: str, session_id: str, tool_config: dict | None = None) -> list:
    """Build the SDK tool list for one run.

    The ComputerTool is bound to a per-``session_id`` PlaywrightComputer
    acquired from the shared BrowserPool so cookies, localStorage,
    service workers, cache, and page state stay isolated between A2A
    sessions (#522).

    ``tool_config`` is the cached [tools] table from OPENAI_CONFIG_TOML,
    maintained by ``AgentExecutor.tool_config_watcher`` (#561). If None
    (e.g. invoked outside a running AgentExecutor, such as in tests),
    falls back to a direct disk read for backwards compatibility.
    """
    global _browser_pool
    cfg = tool_config if tool_config is not None else _load_tool_config()
    tools = []
    if cfg.get("shell", False):
        tools.append(ShellTool(executor=_shell_executor))
    if cfg.get("web_search", False):
        tools.append(WebSearchTool())
    if cfg.get("computer", False) and _supports_computer_tool(model):
        async with _computer_lock:
            if _browser_pool is None:
                _browser_pool = BrowserPool()
            pool = _browser_pool
        computer = await pool.acquire(session_id)
        tools.append(ComputerTool(computer=computer))
    return tools


async def _release_computer(session_id: str) -> None:
    """Release the per-session PlaywrightComputer, if any.

    Called when a session is evicted from the LRU cache, times out, or
    the executor shuts down. Closes the session's browser context
    (isolating its cookies/storage/service workers from any future
    session) while leaving the shared browser process running.
    """
    pool = _browser_pool
    if pool is None:
        return
    try:
        await pool.release(session_id)
    except Exception as _e:
        logger.warning(
            "Failed to release PlaywrightComputer for session %r: %s",
            session_id,
            _e,
        )


# SQLite busy_timeout in milliseconds. Matches the harness SqliteTaskStore
# defaults (#704) — 5s tolerates brief rsync-style locks and concurrent
# writers from the OpenAI Agents SDK without flipping every resume into an
# OperationalError. Overridable for extremely contended deployments via
# OPENAI_SQLITE_BUSY_TIMEOUT_MS (#727).
_OPENAI_SQLITE_BUSY_TIMEOUT_MS = int(os.environ.get("OPENAI_SQLITE_BUSY_TIMEOUT_MS", "5000"))


def _openai_sqlite_connect(db_path: str):
    """Open a sqlite3 connection with WAL + busy_timeout applied (#727).

    Mirrors the pattern used by ``harness/sqlite_task_store.py`` (#704) and
    ``backends/claude``'s SqliteTaskStore (#713).  WAL lets readers proceed
    in parallel with the single OpenAI Agents SDK writer, and busy_timeout absorbs
    transient contention without raising.  Both PRAGMAs are best-effort —
    some network filesystems cannot host a WAL journal; in that case we log
    and continue on the default delete-mode journal rather than failing the
    caller (a missing WAL degrades is_new accuracy but does not lose
    correctness).
    """
    import sqlite3 as _sqlite3

    conn = _sqlite3.connect(db_path, check_same_thread=False)
    try:
        conn.execute("PRAGMA journal_mode=WAL")
    except _sqlite3.OperationalError as exc:
        logger.warning(
            "openai sqlite: journal_mode=WAL failed (%s) — continuing on default journal",
            exc,
        )
    try:
        conn.execute(f"PRAGMA busy_timeout={_OPENAI_SQLITE_BUSY_TIMEOUT_MS}")
    except _sqlite3.OperationalError as exc:
        logger.warning("openai sqlite: busy_timeout pragma failed (%s)", exc)
    return conn


def _sqlite_session_exists(session_id: str) -> bool:
    """Check whether a session already has history in OPENAI_SESSION_DB.

    Uses a direct sqlite3 query against the agent_sessions table so that
    after a process restart we correctly identify sessions that exist on disk
    even though the in-memory LRU cache is empty.  Returns False if the
    database file does not exist yet or if any error occurs.
    """
    db_path = OPENAI_SESSION_DB
    if db_path == ":memory:" or not db_path:
        return False
    import os as _os

    if not _os.path.exists(db_path):
        return False
    try:
        conn = _openai_sqlite_connect(db_path)
        try:
            cursor = conn.execute(
                "SELECT 1 FROM agent_sessions WHERE session_id = ? LIMIT 1",
                (session_id,),
            )
            return cursor.fetchone() is not None
        finally:
            conn.close()
    except Exception as exc:
        logger.warning("_sqlite_session_exists(%r) failed: %s", session_id, exc)
        if backend_session_history_save_errors_total is not None:
            backend_session_history_save_errors_total.labels(**_LABELS).inc()
        return False


def _delete_sqlite_session(session_id: str, db_path: str) -> None:
    """Delete a session row from the SQLite session database (blocking I/O).

    Intended to be called via asyncio.to_thread() so the event loop is not
    stalled by SQLite I/O during timeout cleanup (#361).
    """
    conn = _openai_sqlite_connect(db_path)
    try:
        conn.execute("DELETE FROM agent_sessions WHERE session_id = ?", (session_id,))
        conn.commit()
    finally:
        conn.close()


def _session_layout_self_test() -> None:
    """Probe the openai SQLiteSession on-disk layout at startup (#806).

    Mirrors claude's #530 probe. Writes + reads + deletes a sentinel row
    through a direct ``sqlite3`` connection against ``OPENAI_SESSION_DB`` so
    operators get an immediate signal when:

    - the DB directory is not writable (permissions / read-only mount),
    - WAL cannot be enabled (filesystem that forbids ``-wal`` / ``-shm``
      sidecar files — e.g. some network mounts),
    - the ``agent_sessions`` table exists but under a different schema than
      ``_sqlite_session_exists`` / ``_delete_sqlite_session`` expect (would
      cause silent eviction storage leaks under #361 flow),
    - the DELETE round-trip does not actually remove the row on disk.

    Every failure bumps ``backend_session_path_mismatch_total{reason=...}``
    and logs loud. A broken probe must never prevent startup — every branch
    swallows exceptions.

    The probe runs against the configured DB path (not an ephemeral tempfile)
    so permission / mount-level misconfiguration is surfaced against the real
    target. The sentinel row is guaranteed-deleted at the end so it cannot
    leak into the normal session set.
    """

    def _bump(reason: str) -> None:
        if backend_session_path_mismatch_total is not None:
            try:
                backend_session_path_mismatch_total.labels(
                    **_LABELS,
                    reason=reason,
                ).inc()
            except Exception:
                pass

    db_path = OPENAI_SESSION_DB
    if not db_path or db_path == ":memory:":
        logger.info(
            "session-layout self-test: OPENAI_SESSION_DB is %r — skipping probe (in-memory / unset). (#806)",
            db_path,
        )
        return

    probe_id = "__witwave_openai_session_probe__"
    try:
        # (1) Ensure parent dir exists + is writable.
        parent = os.path.dirname(db_path) or "."
        try:
            os.makedirs(parent, exist_ok=True)
        except Exception as exc:
            logger.error(
                "session-layout self-test: cannot create parent dir %r (%r) — session history will not persist. (#806)",
                parent,
                exc,
            )
            _bump("parent_dir_create_failed")
            return
        if not os.access(parent, os.W_OK):
            logger.error(
                "session-layout self-test: parent dir %r is not writable — session history will not persist. (#806)",
                parent,
            )
            _bump("parent_dir_not_writable")
            return

        # (2) Open the configured DB and confirm WAL + busy_timeout apply.
        try:
            import sqlite3 as _sqlite3

            conn = _openai_sqlite_connect(db_path)
        except Exception as exc:
            logger.error(
                "session-layout self-test: sqlite3.connect(%r) failed: %r — session history will not persist. (#806)",
                db_path,
                exc,
            )
            _bump("connect_failed")
            return

        try:
            try:
                mode_row = conn.execute("PRAGMA journal_mode").fetchone()
                if mode_row and mode_row[0].lower() != "wal":
                    logger.warning(
                        "session-layout self-test: journal_mode is %r (expected wal) — "
                        "SQLite may not have applied the WAL PRAGMA. (#806)",
                        mode_row[0],
                    )
                    _bump("journal_mode_not_wal")
            except Exception:
                _bump("journal_mode_query_failed")

            # (3) Ensure the agent_sessions table exists with the columns we
            # depend on. If the SDK ever switches schema or splits rows
            # across tables, _delete_sqlite_session would silently no-op.
            try:
                cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='agent_sessions'")
                have_table = cursor.fetchone() is not None
            except Exception as exc:
                logger.warning(
                    "session-layout self-test: sqlite_master query failed: %r (#806)",
                    exc,
                )
                _bump("sqlite_master_query_failed")
                have_table = False

            if not have_table:
                # Fresh install — create a minimal compatible shape so the
                # write/read round-trip below can run. The SDK will redefine
                # / migrate on first real session use.
                try:
                    conn.execute("CREATE TABLE IF NOT EXISTS agent_sessions (session_id TEXT PRIMARY KEY, data TEXT)")
                    conn.commit()
                except Exception as exc:
                    logger.error(
                        "session-layout self-test: CREATE TABLE agent_sessions "
                        "failed: %r — session history will not persist. (#806)",
                        exc,
                    )
                    _bump("create_table_failed")
                    return

            # (4) Write+read+delete a sentinel row. Confirms the disk path
            # can round-trip and DELETE actually removes rows (i.e. no
            # stale VIEW / TRIGGER hiding the deletion).
            try:
                conn.execute(
                    "INSERT OR REPLACE INTO agent_sessions (session_id) VALUES (?)",
                    (probe_id,),
                )
                conn.commit()
            except _sqlite3.OperationalError as exc:
                # Schema has extra NOT NULL columns we don't know about —
                # non-fatal (SDK writes those itself); record and continue.
                logger.info(
                    "session-layout self-test: sentinel INSERT on agent_sessions "
                    "failed (%r); schema has columns beyond session_id. Skipping "
                    "round-trip probe. (#806)",
                    exc,
                )
                _bump("insert_schema_mismatch")
                return
            except Exception as exc:
                logger.error(
                    "session-layout self-test: sentinel INSERT failed: %r — session writes likely broken. (#806)",
                    exc,
                )
                _bump("insert_failed")
                return

            try:
                row = conn.execute(
                    "SELECT 1 FROM agent_sessions WHERE session_id = ?",
                    (probe_id,),
                ).fetchone()
                if row is None:
                    logger.error(
                        "session-layout self-test: sentinel row not readable "
                        "after INSERT — storage path is broken. (#806)",
                    )
                    _bump("read_after_insert_missing")
            except Exception as exc:
                logger.warning(
                    "session-layout self-test: SELECT after INSERT failed: %r (#806)",
                    exc,
                )
                _bump("select_failed")

            try:
                conn.execute(
                    "DELETE FROM agent_sessions WHERE session_id = ?",
                    (probe_id,),
                )
                conn.commit()
                row2 = conn.execute(
                    "SELECT 1 FROM agent_sessions WHERE session_id = ?",
                    (probe_id,),
                ).fetchone()
                if row2 is not None:
                    logger.error(
                        "session-layout self-test: sentinel row still present "
                        "after DELETE — eviction cleanup will leak. (#806)",
                    )
                    _bump("delete_did_not_remove")
            except Exception as exc:
                logger.warning(
                    "session-layout self-test: DELETE round-trip failed: %r (#806)",
                    exc,
                )
                _bump("delete_failed")

            logger.info(
                "session-layout self-test: OPENAI_SESSION_DB=%r verified (write/read/delete round-trip OK). (#806)",
                db_path,
            )
        finally:
            try:
                conn.close()
            except Exception:
                pass
    except Exception as exc:
        # Outer safety net — no probe failure should kill startup.
        logger.warning(
            "session-layout self-test: probe raised unexpectedly: %r (#806)",
            exc,
        )
        _bump("probe_exception")


def _load_agent_md() -> str:
    try:
        with open(AGENT_MD) as f:
            return f.read()
    except OSError:
        return ""


def _compute_agent_md_revision(content: str) -> str:
    """Return the SHA-256 hex prefix (first 12 chars) of AGENTS.md content (#1097).

    Used as the ``revision`` label on ``backend_agent_md_revision`` and as
    the ``openai.agent_md_revision`` per-query span attribute so operators
    can verify a hot-reload has propagated to running queries.
    """
    return hashlib.sha256(content.encode("utf-8", errors="replace")).hexdigest()[:12]


def _current_trace_id_hex() -> str | None:
    """Return the active OTel span's trace_id as hex, or None when no active span.

    Used to stamp ``trace_id`` on conversation.jsonl rows so external
    log-correlation tools can join the backend log with harness / downstream
    spans (#636). Returns None when OTel is disabled (invalid span context
    or zero trace_id) so old rows stay backward-compatible.
    """
    try:
        from opentelemetry import trace as _otel_trace

        span = _otel_trace.get_current_span()
        ctx = span.get_span_context()
        if not ctx or not ctx.is_valid or ctx.trace_id == 0:
            return None
        return _otel_trace.format_trace_id(ctx.trace_id)
    except Exception:
        return None


async def log_entry(role: str, text: str, session_id: str, model: str | None = None, tokens: int | None = None) -> None:
    try:
        # Opt-in redaction pass (#1193, parity with claude). Guarded on LOG_REDACT
        # so existing deployments retain identical output without the regex cost;
        # operators flip the env var to take the safer posture when
        # conversation.jsonl is read by humans or forwarded to an external log
        # store.
        _text_for_log = redact_text(text) if should_redact() else text
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "agent": AGENT_NAME,
            "session_id": session_id,
            "role": role,
            "model": model,
            "tokens": tokens,
            "text": _text_for_log,
        }
        # Stamp trace_id from the active OTel span so conversation rows can be
        # joined with backend/harness traces (#636). Absent when OTel is off.
        _tid = _current_trace_id_hex()
        if _tid is not None:
            entry["trace_id"] = _tid
        _line = json.dumps(entry)
        await asyncio.to_thread(_append_log, CONVERSATION_LOG, _line)
        if backend_log_entries_total is not None:
            backend_log_entries_total.labels(**_LABELS, logger="conversation").inc()
        if backend_log_bytes_total is not None:
            backend_log_bytes_total.labels(**_LABELS, logger="conversation").inc(len(_line.encode()))
    except Exception as e:
        if backend_log_write_errors_total is not None:
            backend_log_write_errors_total.labels(**_LABELS).inc()
        if backend_log_write_errors_by_logger_total is not None:
            try:
                backend_log_write_errors_by_logger_total.labels(**_LABELS, logger="conversation").inc()
            except Exception:
                pass
        logger.error(f"log_entry error: {e}")


async def log_trace(text: str) -> None:
    try:
        await asyncio.to_thread(_append_log, TRACE_LOG, text)
        if backend_log_entries_total is not None:
            backend_log_entries_total.labels(**_LABELS, logger="trace").inc()
        if backend_log_bytes_total is not None:
            backend_log_bytes_total.labels(**_LABELS, logger="trace").inc(len(text.encode()))
    except Exception as e:
        if backend_log_write_errors_total is not None:
            backend_log_write_errors_total.labels(**_LABELS).inc()
        if backend_log_write_errors_by_logger_total is not None:
            try:
                backend_log_write_errors_by_logger_total.labels(**_LABELS, logger="trace").inc()
            except Exception:
                pass
        logger.error(f"log_trace error: {e}")


async def _track_session(sessions: OrderedDict[str, float], session_id: str) -> None:
    # Serialise evict/insert on the shared OrderedDict so concurrent callers
    # (A2A execute() and the /mcp tools/call path both share
    # AgentExecutor._sessions) cannot interleave popitem(last=False) with
    # the post-await reinsertion (#506). The SQLite delete and browser
    # release run inside the lock so the invariant
    # `len(sessions) <= MAX_SESSIONS` and eviction-metric accuracy are
    # restored before any other coroutine observes the dict — consistent
    # with the #522 per-session computer-tool pool and the #526
    # lifespan-scoped MCP server lock (both of which also serialise
    # await-crossing critical sections over shared state).
    async with _get_sessions_lock():
        if session_id in sessions:
            sessions.move_to_end(session_id)
            sessions[session_id] = time.monotonic()
        else:
            if len(sessions) >= MAX_SESSIONS:
                _evicted_id, last_used_at = sessions.popitem(last=False)
                if backend_session_evictions_total is not None:
                    backend_session_evictions_total.labels(**_LABELS).inc()
                if backend_session_age_seconds is not None:
                    backend_session_age_seconds.labels(**_LABELS).observe(time.monotonic() - last_used_at)
                # Clean up the evicted session's SQLite record so the database does not
                # grow unboundedly as sessions cycle through the LRU cache (#415).
                # Run the delete in a thread pool so the event loop is not blocked
                # on slow/remote filesystems — same pattern the timeout-cleanup
                # path at line 766 uses, and consistent with claude #426 (#450).
                _db = OPENAI_SESSION_DB
                if _db and _db != ":memory:":
                    try:
                        await asyncio.to_thread(_delete_sqlite_session, _evicted_id, _db)
                    except Exception as _del_exc:
                        logger.warning("Could not delete evicted session %r from DB: %s", _evicted_id, _del_exc)
                # Release the per-session Playwright browser context, if any, so
                # cookies/localStorage/service workers from the evicted session do
                # not linger in memory (#522). Safe no-op when the session never
                # used the computer tool.
                await _release_computer(_evicted_id)
            sessions[session_id] = time.monotonic()
        if backend_active_sessions is not None:
            backend_active_sessions.labels(**_LABELS).set(len(sessions))
        if backend_lru_cache_utilization_percent is not None and MAX_SESSIONS > 0:
            backend_lru_cache_utilization_percent.labels(**_LABELS).set(len(sessions) / MAX_SESSIONS * 100)


async def run_query(
    prompt: str,
    session_id: str,
    agent_md_content: str,
    model: str | None = None,
    max_tokens: int | None = None,
    on_chunk: Callable[[str], Awaitable[None]] | None = None,
    live_mcp_servers: list | None = None,
    tool_config: dict | None = None,
) -> list[str]:
    resolved_model = model or OPENAI_MODEL
    log_dir = os.path.dirname(OPENAI_SESSION_DB)
    if log_dir:
        os.makedirs(log_dir, exist_ok=True)

    instructions = f"Your name is {AGENT_NAME}. Your session ID is {session_id}."
    if agent_md_content:
        instructions = f"{agent_md_content}\n\nYour session ID is {session_id}."

    # MCP servers are entered once at backend lifespan start by
    # AgentExecutor._apply_mcp_config() and passed in live here (#526). Previous
    # behaviour spawned a fresh stdio subprocess on every request; servers now
    # persist across requests for performance and to allow stateful MCP servers
    # (e.g. kubeconfig / HTTP pool) to retain state between calls.
    _live_mcp_servers: list = list(live_mcp_servers or [])

    try:
        session = SQLiteSession(session_id, OPENAI_SESSION_DB)
    except Exception as _sess_exc:
        logger.error("Failed to initialise SQLiteSession for %r: %s", session_id, _sess_exc)
        if backend_session_history_save_errors_total is not None:
            backend_session_history_save_errors_total.labels(**_LABELS).inc()
        session = None

    # Read the key per-request so OPENAI_API_KEY_FILE rotation takes effect on
    # the next call without a pod restart (#728).
    _live_openai_key = _current_openai_api_key()
    if not _live_openai_key:
        # #1501: loud operator-facing error so a missing OPENAI_API_KEY
        # doesn't silently fall through to the Agents SDK's implicit
        # default (opaque deep-SDK failure). Logged at ERROR with
        # actionable text; the run still proceeds with run_config=None
        # so behaviour on recovered keys is unchanged.
        logger.error(
            "OPENAI_API_KEY is unset for session %r — Agents SDK will fall back "
            "to its own env/default lookup, which typically produces an opaque "
            "401/auth error. Set OPENAI_API_KEY (or OPENAI_API_KEY_FILE for "
            "rotation) in the backend environment. See #728 / #1501.",
            session_id,
        )
    run_config = (
        RunConfig(model_provider=MultiProvider(openai_api_key=_live_openai_key, openai_use_responses=True))
        if _live_openai_key
        else None
    )

    collected: list[str] = []
    _query_start = time.monotonic()
    _session_start = time.monotonic()
    _first_chunk_at: float | None = None
    _turn_count = 0
    _message_count = 0
    _tool_call_names: dict[str, str] = {}  # call_id -> tool name
    _tool_start_times: dict[str, float] = {}  # call_id -> monotonic start time
    # #1495: FIFO of synthesized ids for tool_calls that arrived without a
    # raw call_id/id. When the matching tool_output also lacks a call_id we
    # can't string-match, so fall back to FIFO order. Parallel calls without
    # ids are otherwise indistinguishable; FIFO keeps elapsed > 0 and keeps
    # tool_name correlation in dispatch order rather than collapsing to the
    # first synth entry.
    _pending_synth_call_ids: deque[str] = deque()
    _tool_call_count = 0
    # Per-task SDK error/noise tally (#802). Mirrors claude's subprocess-stderr
    # line count; observed at end-of-task into backend_stderr_lines_per_task.
    _stderr_count = 0
    _total_tokens = 0
    # Initialized before the try so the llm.request span's finally-close
    # handler always has a sentinel to test against even if Agent construction
    # fails (#630).
    _llm_ctx = None

    try:
        # MCP servers are owned by AgentExecutor's lifespan-scoped AsyncExitStack
        # (#526). We receive them already-entered; we do not enter or exit them
        # per request. The snapshot above is the caller's live list.
        openai_agent = Agent(
            name=AGENT_NAME,
            instructions=instructions,
            model=resolved_model,
            model_settings=_build_model_settings(),
            tools=await _build_tools(resolved_model, session_id, tool_config=tool_config),
            mcp_servers=_live_mcp_servers,
        )

        # llm.request child span (#630) — one per Runner event-loop entry.
        # Managed manually with a finally so we do not need to re-indent the
        # long stream loop below. The span stays open for the full streaming
        # iteration so nested tool.call / mcp.call spans parent under it.
        #
        # ``openai.agent_md_revision`` (#1097) — SHA-256 hex prefix of the
        # AGENTS.md content that seeded ``instructions`` for this run. Paired
        # with the ``backend_agent_md_revision`` gauge so operators can verify
        # a hot-reload has propagated to in-flight queries.
        _llm_attrs = {"model": _resolve_model_label(resolved_model)}
        try:
            _llm_attrs["openai.agent_md_revision"] = _compute_agent_md_revision(agent_md_content)
        except Exception:
            pass
        _llm_ctx = start_span(
            "llm.request",
            kind="client",
            attributes=_llm_attrs,
        )
        _llm_ctx.__enter__()
        result = Runner.run_streamed(openai_agent, prompt, session=session, run_config=run_config)
        async for event in result.stream_events():
            _message_count += 1
            if event.type == "raw_response_event":
                data = getattr(event, "data", None)
                delta = getattr(data, "delta", None)
                if delta and hasattr(delta, "text") and delta.text:
                    if _first_chunk_at is None:
                        _first_chunk_at = time.monotonic()
                        if backend_sdk_time_to_first_message_seconds is not None:
                            backend_sdk_time_to_first_message_seconds.labels(
                                **_LABELS, model=sanitize_model_label(resolved_model)
                            ).observe(_first_chunk_at - _query_start)
                    collected.append(delta.text)
                    # Stream the chunk to the A2A event_queue (#430). Set by
                    # execute(); None when MCP /mcp endpoint or non-streaming
                    # caller. Awaited directly so chunk events stay ordered
                    # on the wire and exceptions surface here. Errors are
                    # logged and swallowed so SDK iteration is never aborted.
                    # Wrapped in asyncio.wait_for so a slow/stuck A2A consumer
                    # cannot stall the SDK event loop, token-budget
                    # enforcement, or tool-call processing (#539). On timeout
                    # the chunk is dropped with a warning and iteration
                    # continues; the overall TASK_TIMEOUT_SECONDS still bounds
                    # total request time.
                    if on_chunk is not None:
                        try:
                            await asyncio.wait_for(
                                on_chunk(delta.text),
                                timeout=STREAM_CHUNK_TIMEOUT_SECONDS,
                            )
                        except asyncio.TimeoutError:
                            logger.warning(
                                "Session %r: on_chunk callback timed out after %.3fs; dropping chunk and continuing stream",  # noqa: E501
                                session_id,
                                STREAM_CHUNK_TIMEOUT_SECONDS,
                            )
                            # Signal the drop to the outer executor so it can
                            # emit a final-flush aggregated event (#724). The
                            # stream_state dict is attached to the on_chunk
                            # callable by AgentExecutor.execute so the inner
                            # loop doesn't need a separate kwarg on every
                            # call site.
                            _state = getattr(on_chunk, "stream_state", None)
                            if isinstance(_state, dict):
                                _state["dropped"] = _state.get("dropped", 0) + 1
                            if backend_streaming_chunks_dropped_total is not None:
                                try:
                                    _lbl = getattr(on_chunk, "label_model", "") or _resolve_model_label(model)
                                    backend_streaming_chunks_dropped_total.labels(
                                        **_LABELS,
                                        model=_lbl,
                                    ).inc()
                                except Exception:
                                    pass
                        except Exception as _e:
                            logger.warning("Session %r: on_chunk callback raised: %s", session_id, _e)
                # Check usage on response events — response.completed carries usage
                # in event.data.response (ResponseCompletedEvent.response = Response)
                try:
                    _usage = getattr(data, "usage", None) or getattr(getattr(data, "response", None), "usage", None)
                    if _usage is not None:
                        # Only enforce the budget when total_tokens is reported.
                        # output_tokens alone undercounts (excludes prompt/cached
                        # tokens) and previously caused premature budget trips
                        # for callers whose SDK surfaces only the output side.
                        _candidate = getattr(_usage, "total_tokens", None)
                        if _candidate is not None:
                            _total_tokens = max(_total_tokens, int(_candidate))
                            if max_tokens is not None and _total_tokens >= max_tokens:
                                if backend_budget_exceeded_total is not None:
                                    backend_budget_exceeded_total.labels(**_LABELS).inc()
                                raise BudgetExceededError(_total_tokens, max_tokens, list(collected))
                except BudgetExceededError:
                    raise
                except Exception as _usage_exc:
                    # Context-usage fetch/parse failure (#803). Best-effort
                    # counter bump; never let usage extraction break the
                    # outer query.
                    logger.debug("context usage extraction failed: %s", _usage_exc)
                    if backend_sdk_context_fetch_errors_total is not None:
                        try:
                            backend_sdk_context_fetch_errors_total.labels(
                                **_LABELS, model=sanitize_model_label(resolved_model)
                            ).inc()
                        except Exception:
                            pass
            elif event.type == "agent_updated_stream_event":
                _turn_count += 1
            elif event.type == "run_item_stream_event":
                item = event.item
                if isinstance(item, ToolCallItem):
                    raw = item.raw_item
                    # Synthesize a UUID when the SDK provides neither call_id
                    # nor id so parallel calls don't collapse into a shared
                    # "" key (which would reset start-time bookkeeping and
                    # yield elapsed ≈ 0 for all but the first call, #671).
                    _raw_call_id = getattr(raw, "call_id", None) or getattr(raw, "id", None)
                    if _raw_call_id:
                        call_id = _raw_call_id
                    else:
                        call_id = f"synth-{uuid.uuid4().hex}"
                        # #1495: remember synth ids in FIFO order so the
                        # matching tool_output (which also won't have a
                        # call_id on the raw item) can recover the right
                        # name + start-time key.
                        _pending_synth_call_ids.append(call_id)
                    name = getattr(raw, "name", None) or getattr(raw, "type", "unknown")
                    # For shell, extract command(s) as input.
                    if hasattr(raw, "action") and hasattr(raw.action, "commands"):
                        tool_input = {"commands": list(raw.action.commands)}
                    elif hasattr(raw, "action") and hasattr(raw.action, "command"):
                        tool_input = {"command": raw.action.command}
                    else:
                        args_str = getattr(raw, "arguments", None)
                        if args_str:
                            try:
                                tool_input = json.loads(args_str)
                            except Exception:
                                tool_input = {"arguments": args_str}
                        else:
                            tool_input = {}
                    _tool_call_names[call_id] = name
                    _tool_start_times[call_id] = time.monotonic()
                    _tool_call_count += 1
                    if backend_sdk_tool_calls_total is not None:
                        backend_sdk_tool_calls_total.labels(**_LABELS, tool=name).inc()
                    if backend_sdk_tool_call_input_size_bytes is not None:
                        try:
                            _input_bytes = len(json.dumps(tool_input).encode())
                            backend_sdk_tool_call_input_size_bytes.labels(**_LABELS, tool=name).observe(_input_bytes)
                        except Exception:
                            pass
                    # tool.call / mcp.call span (#630). Emitted on the dispatch
                    # bookkeeping side so the call is visible in trace UIs as a
                    # child of llm.request. MCP tools surface via Agents SDK
                    # with a separate raw_item.type (e.g. "mcp_call"); fall
                    # back to name-prefix detection for the Claude-compatible
                    # "mcp__server__tool" convention.
                    _raw_type = getattr(raw, "type", "") or ""
                    _is_mcp = "mcp" in _raw_type.lower() or (isinstance(name, str) and name.startswith("mcp__"))
                    if _is_mcp:
                        _mcp_server = ""
                        _mcp_tool = name
                        if isinstance(name, str) and name.startswith("mcp__"):
                            _parts = name.split("__", 2)
                            _mcp_server = _parts[1] if len(_parts) > 1 else ""
                            _mcp_tool = _parts[2] if len(_parts) > 2 else name
                        _tool_ctx = start_span(
                            "mcp.call",
                            kind="client",
                            attributes={
                                "mcp.server": _mcp_server,
                                "mcp.tool": _mcp_tool,
                                "tool.name": name,
                            },
                        )
                    else:
                        _tool_ctx = start_span(
                            "tool.call",
                            kind="internal",
                            attributes={"tool.name": name},
                        )
                    _tool_ctx.__enter__()
                    try:
                        ts = datetime.now(timezone.utc).isoformat()
                        # Apply the same LOG_TRACE_CONTENT_MAX_BYTES cap
                        # to tool_use.input as tool_result.content
                        # (#989). Previously the cap bounded only the
                        # result side, so a multi-MB tool_input from an
                        # MCP call could blow the tool-activity.jsonl
                        # rotation budget and /trace memory even though
                        # the paired result was neatly truncated.
                        _input_for_log: object = tool_input
                        if LOG_TRACE_CONTENT_MAX_BYTES > 0:
                            try:
                                _input_json = json.dumps(tool_input, default=str)
                            except Exception:
                                _input_json = str(tool_input)
                            _input_bytes_full = len(_input_json.encode("utf-8"))
                            if _input_bytes_full > LOG_TRACE_CONTENT_MAX_BYTES:
                                _truncated = _input_bytes_full - LOG_TRACE_CONTENT_MAX_BYTES
                                _clipped = _input_json.encode("utf-8")[:LOG_TRACE_CONTENT_MAX_BYTES].decode(
                                    "utf-8", errors="replace"
                                )
                                # Store a stringified, marked-truncated form
                                # under the "input" field so downstream
                                # consumers (dashboard /trace) still parse
                                # cleanly and see the truncation marker.
                                _input_for_log = f"{_clipped}\n[truncated {_truncated} bytes]"
                        entry = {
                            "ts": ts,
                            "agent": AGENT_NAME,
                            "agent_id": AGENT_ID,
                            "session_id": session_id,
                            "event_type": "tool_use",
                            "model": resolved_model,
                            "id": call_id,
                            "name": name,
                            "input": _input_for_log,
                        }
                        await log_trace(json.dumps(entry))
                    except Exception as e:
                        logger.error(f"log_trace tool_use error: {e}")
                    finally:
                        try:
                            _tool_ctx.__exit__(None, None, None)
                        except Exception:
                            pass
                elif isinstance(item, ToolCallOutputItem):
                    raw = item.raw_item
                    call_id = raw.get("call_id", "") if isinstance(raw, dict) else getattr(raw, "call_id", "")
                    # #1495: output side gets no call_id when the tool-call
                    # side had to synthesize one. Recover the synth key in
                    # FIFO order so parallel tool calls don't collapse onto
                    # a single bookkeeping entry (elapsed ≈ 0 for all but
                    # the first) and tool_name stays correlated.
                    if not call_id and _pending_synth_call_ids:
                        call_id = _pending_synth_call_ids.popleft()
                    else:
                        try:
                            _pending_synth_call_ids.remove(call_id)
                        except ValueError:
                            pass
                    tool_name = _tool_call_names.get(call_id, "unknown")
                    output = item.output
                    content_full = str(output)
                    # Observe metrics on the FULL size so operator dashboards
                    # still see multi-MB outputs — but cap the JSONL payload
                    # so a single tool_result row cannot blow the rotation
                    # budget or the /trace endpoint's memory (#939).
                    full_bytes = len(content_full.encode("utf-8"))
                    if LOG_TRACE_CONTENT_MAX_BYTES > 0 and full_bytes > LOG_TRACE_CONTENT_MAX_BYTES:
                        truncated_bytes = full_bytes - LOG_TRACE_CONTENT_MAX_BYTES
                        content = (
                            content_full.encode("utf-8")[:LOG_TRACE_CONTENT_MAX_BYTES].decode("utf-8", errors="replace")
                            + f"\n[truncated {truncated_bytes} bytes]"
                        )
                    else:
                        content = content_full
                    is_error = bool(getattr(item, "is_error", None) or (isinstance(raw, dict) and raw.get("is_error")))
                    try:
                        ts = datetime.now(timezone.utc).isoformat()
                        entry = {
                            "ts": ts,
                            "agent": AGENT_NAME,
                            "agent_id": AGENT_ID,
                            "session_id": session_id,
                            "event_type": "tool_result",
                            "model": resolved_model,
                            "tool_use_id": call_id,
                            "name": tool_name,
                            "content": content,
                            "is_error": is_error,
                        }
                        await log_trace(json.dumps(entry))
                    except Exception as e:
                        logger.error(f"log_trace tool_result error: {e}")
                    _tool_elapsed = time.monotonic() - _tool_start_times.pop(call_id, time.monotonic())
                    if backend_sdk_tool_duration_seconds is not None:
                        backend_sdk_tool_duration_seconds.labels(**_LABELS, tool=tool_name).observe(_tool_elapsed)
                    if is_error and backend_sdk_tool_errors_total is not None:
                        backend_sdk_tool_errors_total.labels(**_LABELS, tool=tool_name).inc()
                    if backend_sdk_tool_result_size_bytes is not None:
                        backend_sdk_tool_result_size_bytes.labels(**_LABELS, tool=tool_name).observe(full_bytes)
                    # tool.use event (#1110 phase 3). Fire-and-forget.
                    try:
                        _emit_event_safe(
                            "tool.use",
                            {
                                "session_id_hash": _session_id_hash(session_id),
                                "tool": tool_name or "unknown",
                                "duration_ms": int(_tool_elapsed * 1000),
                                "outcome": "error" if is_error else "ok",
                                "result_size_bytes": int(full_bytes),
                            },
                        )
                    except Exception:
                        pass
                    # Outbound MCP tool metric family (#1104) — no-op for
                    # non-mcp__ tool names.
                    try:
                        from mcp_metrics import observe_outbound_mcp_call as _obs_outbound_mcp

                        _obs_outbound_mcp(
                            backend_mcp_outbound_requests_total,
                            backend_mcp_outbound_duration_seconds,
                            dict(_LABELS),
                            tool_name,
                            _tool_elapsed,
                            bool(is_error),
                        )
                    except Exception:
                        pass
    except BudgetExceededError as exc:
        _stderr_count += 1
        if backend_sdk_session_duration_seconds is not None:
            backend_sdk_session_duration_seconds.labels(**_LABELS, model=sanitize_model_label(resolved_model)).observe(
                time.monotonic() - _session_start
            )
        partial_response = "".join(exc.collected)
        if partial_response:
            await log_entry("agent", partial_response, session_id, model=resolved_model, tokens=_total_tokens or None)
        # Per-query aggregate metrics (#669): the normal block below the
        # try/except is skipped when we re-raise, so mirror the relevant
        # observations here so budget-exceeded runs do not under-report
        # tokens, tool-call counts, and context-usage metrics.
        try:
            if backend_sdk_query_duration_seconds is not None:
                backend_sdk_query_duration_seconds.labels(
                    **_LABELS, model=sanitize_model_label(resolved_model)
                ).observe(time.monotonic() - _query_start)
            if backend_sdk_messages_per_query is not None:
                backend_sdk_messages_per_query.labels(**_LABELS, model=sanitize_model_label(resolved_model)).observe(
                    _message_count
                )
            if backend_sdk_turns_per_query is not None:
                backend_sdk_turns_per_query.labels(**_LABELS, model=sanitize_model_label(resolved_model)).observe(
                    _turn_count
                )
            if backend_text_blocks_per_query is not None:
                backend_text_blocks_per_query.labels(**_LABELS, model=sanitize_model_label(resolved_model)).observe(
                    len(collected)
                )
            if backend_sdk_tokens_per_query is not None:
                backend_sdk_tokens_per_query.labels(**_LABELS, model=sanitize_model_label(resolved_model)).observe(
                    _total_tokens
                )
            if backend_sdk_tool_calls_per_query is not None:
                backend_sdk_tool_calls_per_query.labels(**_LABELS, model=sanitize_model_label(resolved_model)).observe(
                    _tool_call_count
                )
            if _total_tokens:
                if backend_context_tokens is not None:
                    backend_context_tokens.labels(**_LABELS).observe(_total_tokens)
                if max_tokens:
                    if backend_context_tokens_remaining is not None:
                        backend_context_tokens_remaining.labels(**_LABELS).observe(max(0, max_tokens - _total_tokens))
                    _pct = _total_tokens / max_tokens * 100
                    if backend_context_usage_percent is not None:
                        backend_context_usage_percent.labels(**_LABELS).observe(_pct)
                    if _pct >= 100 and backend_context_exhaustion_total is not None:
                        backend_context_exhaustion_total.labels(**_LABELS).inc()
                    elif _pct >= CONTEXT_USAGE_WARN_THRESHOLD * 100 and backend_context_warnings_total is not None:
                        backend_context_warnings_total.labels(**_LABELS).inc()
        except Exception as _mex:
            logger.debug("per-query metrics emit on BudgetExceededError failed: %s", _mex)
        raise
    except Exception as _run_exc:
        _stderr_count += 1
        if backend_sdk_query_error_duration_seconds is not None:
            backend_sdk_query_error_duration_seconds.labels(
                **_LABELS, model=sanitize_model_label(resolved_model)
            ).observe(time.monotonic() - _query_start)
        if backend_sdk_session_duration_seconds is not None:
            backend_sdk_session_duration_seconds.labels(**_LABELS, model=sanitize_model_label(resolved_model)).observe(
                time.monotonic() - _session_start
            )
        # Mark the llm.request span as errored so traces reflect the failure
        # even though we re-raise immediately (#630).
        try:
            _otel_cur = getattr(_llm_ctx, "_active_span", None)
            set_span_error(_otel_cur, _run_exc)
        except Exception:
            pass
        # Classify by exception type to match claude's error metric surface
        # (#431). Best-effort — unknown exception types fall through to the
        # generic backend_sdk_errors_total counter.
        try:
            import openai as _openai

            if isinstance(_run_exc, _openai.APIConnectionError):
                if backend_sdk_client_errors_total is not None:
                    backend_sdk_client_errors_total.labels(**_LABELS, model=sanitize_model_label(resolved_model)).inc()
            elif isinstance(_run_exc, _openai.APIError):
                if backend_sdk_result_errors_total is not None:
                    backend_sdk_result_errors_total.labels(**_LABELS, model=sanitize_model_label(resolved_model)).inc()
            else:
                if backend_sdk_errors_total is not None:
                    backend_sdk_errors_total.labels(**_LABELS, model=sanitize_model_label(resolved_model)).inc()
        except Exception:
            if backend_sdk_errors_total is not None:
                backend_sdk_errors_total.labels(**_LABELS, model=sanitize_model_label(resolved_model)).inc()
        raise
    finally:
        # Close the llm.request span opened above (#630). Best-effort — if the
        # context manager was never entered (e.g. exception before its __enter__
        # call), swallow any error.
        if _llm_ctx is not None:
            try:
                _llm_ctx.__exit__(None, None, None)
            except Exception:
                pass
        # Per-task SDK error/noise metrics (#802). Always fire — even for
        # successful runs (histogram observation of 0) so the rate is
        # interpretable.
        try:
            if backend_stderr_lines_per_task is not None:
                backend_stderr_lines_per_task.labels(**_LABELS).observe(_stderr_count)
            if _stderr_count and backend_tasks_with_stderr_total is not None:
                backend_tasks_with_stderr_total.labels(**_LABELS).inc()
        except Exception:
            pass

    if backend_sdk_session_duration_seconds is not None:
        backend_sdk_session_duration_seconds.labels(**_LABELS, model=sanitize_model_label(resolved_model)).observe(
            time.monotonic() - _session_start
        )

    # Prefer final_output as the SDK's authoritative answer when it is available.
    # Streaming deltas may represent intermediate or partial content during tool-call
    # turns; final_output is always the completed response the SDK intends to return.
    # Fall back to streamed collected content only when final_output is absent (#381).
    final = getattr(result, "final_output", None)
    if final and isinstance(final, str):
        if not collected:
            collected.append(final)
        else:
            streamed = "".join(collected)
            if final != streamed:
                # #1497: previously the log said "using streamed content" —
                # which dropped the authoritative final_output in favour
                # of possibly-partial streamed deltas. Prefer final_output
                # and bump a divergence counter so operators can alert on
                # frequent mismatches.
                logger.debug(
                    "final_output differs from streamed deltas — using final_output (len streamed=%d, len final=%d)",
                    len(streamed),
                    len(final),
                )
                try:
                    from metrics import backend_final_output_divergence_total as _bfod

                    if _bfod is not None:
                        _bfod.labels(**_LABELS).inc()
                except Exception:
                    pass
                collected.clear()
                collected.append(final)

    full_response = "".join(collected)
    if full_response:
        await log_entry("agent", full_response, session_id, model=resolved_model, tokens=_total_tokens or None)
        # conversation.turn event (#1110 phase 3). One event per
        # completed assistant turn; partial-response log_entry sites
        # intentionally do NOT emit so the stream summarises the
        # turn rather than every chunk.
        try:
            # Omit ``model`` entirely when falsy (#1150).
            _a_turn_payload: dict = {
                "session_id_hash": _session_id_hash(session_id),
                "role": "assistant",
                "content_bytes": len(full_response.encode("utf-8")),
            }
            if resolved_model:
                _a_turn_payload["model"] = resolved_model
            _emit_event_safe("conversation.turn", _a_turn_payload)
        except Exception:
            pass

    if backend_sdk_query_duration_seconds is not None:
        backend_sdk_query_duration_seconds.labels(**_LABELS, model=sanitize_model_label(resolved_model)).observe(
            time.monotonic() - _query_start
        )
    if backend_sdk_messages_per_query is not None:
        backend_sdk_messages_per_query.labels(**_LABELS, model=sanitize_model_label(resolved_model)).observe(
            _message_count
        )
    if backend_sdk_turns_per_query is not None:
        backend_sdk_turns_per_query.labels(**_LABELS, model=sanitize_model_label(resolved_model)).observe(_turn_count)
    if backend_text_blocks_per_query is not None:
        backend_text_blocks_per_query.labels(**_LABELS, model=sanitize_model_label(resolved_model)).observe(
            len(collected)
        )
    if backend_sdk_tokens_per_query is not None:
        backend_sdk_tokens_per_query.labels(**_LABELS, model=sanitize_model_label(resolved_model)).observe(
            _total_tokens
        )
    if backend_sdk_tool_calls_per_query is not None:
        backend_sdk_tool_calls_per_query.labels(**_LABELS, model=sanitize_model_label(resolved_model)).observe(
            _tool_call_count
        )
    if _total_tokens:
        if backend_context_tokens is not None:
            backend_context_tokens.labels(**_LABELS).observe(_total_tokens)
        if max_tokens:
            if backend_context_tokens_remaining is not None:
                backend_context_tokens_remaining.labels(**_LABELS).observe(max(0, max_tokens - _total_tokens))
            _pct = _total_tokens / max_tokens * 100
            if backend_context_usage_percent is not None:
                backend_context_usage_percent.labels(**_LABELS).observe(_pct)
            if _pct >= 100 and backend_context_exhaustion_total is not None:
                backend_context_exhaustion_total.labels(**_LABELS).inc()
            elif _pct >= CONTEXT_USAGE_WARN_THRESHOLD * 100 and backend_context_warnings_total is not None:
                backend_context_warnings_total.labels(**_LABELS).inc()

    # Log a trace entry for the completed turn
    try:
        ts = datetime.now(timezone.utc).isoformat()
        entry = {
            "ts": ts,
            "agent": AGENT_NAME,
            "agent_id": AGENT_ID,
            "session_id": session_id,
            "event_type": "response",
            "model": resolved_model,
            "chunks": len(collected),
        }
        await log_trace(json.dumps(entry))
    except Exception as e:
        logger.error(f"log_trace error: {e}")

    return collected


async def run(
    prompt: str,
    session_id: str,
    sessions: OrderedDict[str, float],
    agent_md_content: str,
    model: str | None = None,
    max_tokens: int | None = None,
    on_chunk: Callable[[str], Awaitable[None]] | None = None,
    live_mcp_servers: list | None = None,
    tool_config: dict | None = None,
) -> str:
    if backend_concurrent_queries is not None:
        backend_concurrent_queries.labels(**_LABELS).inc()
    try:
        return await _run_inner(
            prompt,
            session_id,
            sessions,
            agent_md_content,
            model,
            max_tokens,
            on_chunk=on_chunk,
            live_mcp_servers=live_mcp_servers,
            tool_config=tool_config,
        )
    finally:
        if backend_concurrent_queries is not None:
            backend_concurrent_queries.labels(**_LABELS).dec()


async def _run_inner(
    prompt: str,
    session_id: str,
    sessions: OrderedDict[str, float],
    agent_md_content: str,
    model: str | None = None,
    max_tokens: int | None = None,
    on_chunk: Callable[[str], Awaitable[None]] | None = None,
    live_mcp_servers: list | None = None,
    tool_config: dict | None = None,
) -> str:
    # #937: seed the per-task session ContextVar so shell-baseline denials
    # (and any other contextvar-aware callers) can correlate their hook
    # events back to this A2A session. No reset is required — the set
    # scopes to the current asyncio Task's context, which terminates when
    # this coroutine returns.
    _current_session_id.set(session_id)
    resolved_model = model or OPENAI_MODEL
    if backend_model_requests_total is not None:
        backend_model_requests_total.labels(**_LABELS, model=sanitize_model_label(resolved_model)).inc()

    # #1499: snapshot membership + last-used under the shared sessions
    # lock so concurrent _track_session mutations (popitem/move_to_end)
    # cannot flip the is_new result between the membership check and
    # the SQLite probe, which would mis-label backend_session_starts_total.
    async with _get_sessions_lock():
        _in_memory = session_id in sessions
        _last_used = sessions.get(session_id) if _in_memory else None
    is_new = not _in_memory and not await asyncio.to_thread(_sqlite_session_exists, session_id)
    if not is_new and backend_session_idle_seconds is not None:
        if _last_used is not None:
            backend_session_idle_seconds.labels(**_LABELS).observe(time.monotonic() - _last_used)
    if backend_session_starts_total is not None:
        backend_session_starts_total.labels(**_LABELS, type="new" if is_new else "resumed").inc()

    _prompt_preview = (
        prompt[:LOG_PROMPT_MAX_BYTES] + ("[truncated]" if len(prompt) > LOG_PROMPT_MAX_BYTES else "")
        if LOG_PROMPT_MAX_BYTES > 0
        else "[redacted]"
    )
    logger.info(f"Session {session_id} ({'new' if is_new else 'existing'}) — prompt: {_prompt_preview!r}")
    await log_entry("user", prompt, session_id, model=resolved_model)
    # conversation.turn event (#1110 phase 3). Wrap — never raise.
    # Omit the ``model`` key entirely when falsy (#1150).
    try:
        _u_turn_payload: dict = {
            "session_id_hash": _session_id_hash(session_id),
            "role": "user",
            "content_bytes": len((prompt or "").encode("utf-8")),
        }
        if resolved_model:
            _u_turn_payload["model"] = resolved_model
        _emit_event_safe("conversation.turn", _u_turn_payload)
    except Exception:
        pass

    if backend_prompt_length_bytes is not None:
        backend_prompt_length_bytes.labels(**_LABELS).observe(len(prompt.encode()))

    _start = time.monotonic()
    _budget_exceeded = False
    try:
        collected = await asyncio.wait_for(
            run_query(
                prompt,
                session_id,
                agent_md_content,
                model=model,
                max_tokens=max_tokens,
                on_chunk=on_chunk,
                live_mcp_servers=live_mcp_servers,
                tool_config=tool_config,
            ),
            timeout=TASK_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError:
        logger.error(f"Session {session_id!r}: timed out after {TASK_TIMEOUT_SECONDS}s.")
        # Serialise the timeout eviction under _sessions_lock (#668) so
        # the pop + _release_computer + metric updates can't interleave
        # with _track_session's popitem/move_to_end over the shared
        # OrderedDict. Shares the single ``_get_sessions_lock`` helper
        # with ``_track_session`` (#725).
        async with _get_sessions_lock():
            # Evict the session from the LRU cache on timeout. The underlying
            # SQLiteSession may be in an inconsistent state after a mid-stream
            # cancellation; removing it ensures the next call for this session_id
            # starts fresh rather than attempting to resume a broken session.
            sessions.pop(session_id, None)
            # Also remove the SQLite history row so the next request for this
            # session_id starts with empty history rather than reloading the
            # potentially stale snapshot stored before the timeout.
            # Run in a thread to avoid blocking the event loop with SQLite I/O (#361).
            _db_path = OPENAI_SESSION_DB
            if _db_path and _db_path != ":memory:":
                try:
                    await asyncio.to_thread(_delete_sqlite_session, session_id, _db_path)
                    logger.info("Removed stale SQLite session for timed-out session %r", session_id)
                except Exception as _e:
                    logger.warning("Could not remove SQLite session for timed-out session %r: %s", session_id, _e)
            # Drop the per-session Playwright context so a later request reusing
            # this session_id does not inherit mid-stream browser state (#522).
            await _release_computer(session_id)
            if backend_tasks_total is not None:
                backend_tasks_total.labels(**_LABELS, status="timeout").inc()
            if backend_task_error_duration_seconds is not None:
                backend_task_error_duration_seconds.labels(**_LABELS).observe(time.monotonic() - _start)
            if backend_task_last_error_timestamp_seconds is not None:
                backend_task_last_error_timestamp_seconds.labels(**_LABELS).set(time.time())
        raise
    except BudgetExceededError as _bexc:
        _budget_exceeded = True
        logger.warning(f"Session {session_id!r}: {_bexc} — returning partial response.")
        await log_entry(
            "system",
            f"Budget exceeded: {_bexc.total} tokens used of {_bexc.limit} limit.",
            session_id,
            model=resolved_model,
        )
        collected = _bexc.collected
        await _track_session(sessions, session_id)
    except Exception:
        if backend_tasks_total is not None:
            backend_tasks_total.labels(**_LABELS, status="error").inc()
        if backend_task_error_duration_seconds is not None:
            backend_task_error_duration_seconds.labels(**_LABELS).observe(time.monotonic() - _start)
        if backend_task_last_error_timestamp_seconds is not None:
            backend_task_last_error_timestamp_seconds.labels(**_LABELS).set(time.time())
        raise

    if not _budget_exceeded:
        await _track_session(sessions, session_id)
    if backend_tasks_total is not None:
        backend_tasks_total.labels(**_LABELS, status="budget_exceeded" if _budget_exceeded else "success").inc()
    if not _budget_exceeded and backend_task_last_success_timestamp_seconds is not None:
        backend_task_last_success_timestamp_seconds.labels(**_LABELS).set(time.time())
    if backend_task_duration_seconds is not None:
        backend_task_duration_seconds.labels(**_LABELS).observe(time.monotonic() - _start)
    if backend_task_timeout_headroom_seconds is not None:
        backend_task_timeout_headroom_seconds.labels(**_LABELS).observe(
            TASK_TIMEOUT_SECONDS - (time.monotonic() - _start)
        )

    response = "".join(collected) if collected else ""
    if not response:
        if backend_empty_responses_total is not None:
            backend_empty_responses_total.labels(**_LABELS).inc()
    elif backend_response_length_bytes is not None:
        backend_response_length_bytes.labels(**_LABELS).observe(len(response.encode()))
    return response


class AgentExecutor(A2AAgentExecutor):
    def __init__(self):
        self._sessions: OrderedDict[str, float] = OrderedDict()
        self._running_tasks: dict[str, asyncio.Task] = {}
        self._agent_md_content: str = _load_agent_md()
        # Cached SHA-256 hex prefix of the currently-active AGENTS.md (#1097).
        # Recomputed in perform_initial_loads() and agent_md_watcher() on each
        # reload; stamped on backend_agent_md_revision gauge (value 1) and
        # mirrored into the per-query span attribute openai.agent_md_revision.
        self._agent_md_revision: str = _compute_agent_md_revision(self._agent_md_content)
        self._stamp_agent_md_revision(self._agent_md_revision, previous=None)
        # MCP config dict loaded from MCP_CONFIG_PATH; populated and refreshed
        # by mcp_config_watcher() (#432).
        self._mcp_config: dict = {}
        # [tools] table from OPENAI_CONFIG_TOML; populated and refreshed by
        # tool_config_watcher() (#561). Eliminates per-request TOML re-parse
        # in _build_tools; consistent with the mcp.json / AGENTS.md cache
        # pattern elsewhere in this module.
        self._tool_config: dict = {}
        self._mcp_watcher_tasks: list[asyncio.Task] = []
        # Lifespan-scoped MCP server stack (#526). MCP servers are entered
        # once at startup (or on hot-reload) and reused across requests,
        # eliminating per-request stdio subprocess spawn overhead. The lock
        # serialises reload-vs-request access to _live_mcp_servers so an
        # in-flight Agent(...) call never sees a half-torn-down server.
        self._mcp_stack: AsyncExitStack | None = None
        self._live_mcp_servers: list = []
        self._mcp_servers_lock: asyncio.Lock | None = None
        # Refcount of in-flight requests holding the current stack. When a
        # reload swaps in a new stack while this is > 0, the old stack is
        # parked in _mcp_old_stacks and only aclose()d when the last
        # user releases (#667).
        self._mcp_stack_refcount: int = 0
        self._mcp_old_stacks: list[tuple[AsyncExitStack, int]] = []
        # Public idempotency flag — set to True after close() completes so
        # callers (e.g. main.py's lifespan) can safely avoid double-close of
        # shared resources like the module-level _browser_pool (#555).
        self.closed: bool = False
        # #1095: perform_initial_loads() flips these to True so the watcher
        # bodies skip their redundant first parse once readiness has been
        # gated on a synchronous initial load. Mirrors claude #869.
        self._initial_mcp_loaded = False
        self._initial_agent_md_loaded = False
        self._initial_tool_config_loaded = False
        # OpenAI can actively block the ShellTool executor, but broader
        # Agents SDK tools do not yet expose a universal PreToolUse
        # interposition point. Report the conservative partial/skeleton
        # mode so dashboards do not mistake shell-only coverage for full
        # Claude-style enforcement.
        if backend_hooks_enforcement_mode is not None:
            backend_hooks_enforcement_mode.labels(**_LABELS).set(0)
        if backend_hooks_active_rules is not None:
            shared_shell_rules = sum(1 for rule in _SHARED_BASELINE_RULES if getattr(rule, "tool", None) == "Bash")
            backend_hooks_active_rules.labels(**_LABELS, source="baseline").set(
                shared_shell_rules + len(_SHELL_DENY_RULES)
            )
            backend_hooks_active_rules.labels(**_LABELS, source="extension").set(0)

    def hooks_enforcement_mode_for_health(self) -> str:
        """Return a compact health-payload label for OpenAI's hook posture."""
        return "partial"

    def _stamp_agent_md_revision(self, current: str, previous: str | None) -> None:
        """Update backend_agent_md_revision for a (possibly) new revision (#1097).

        Zero out the previous revision's gauge via .remove() so only the
        live revision reports value 1. All calls are best-effort: prometheus
        registry churn must never break hot-reload.
        """
        if backend_agent_md_revision is None:
            return
        if previous is not None and previous != current:
            try:
                backend_agent_md_revision.remove(
                    _LABELS["agent"],
                    _LABELS["agent_id"],
                    _LABELS["backend"],
                    previous,
                )
            except (KeyError, ValueError):
                # Label set never registered / already removed — benign.
                pass
            except Exception as exc:  # pragma: no cover — defensive
                logger.debug("agent_md_revision remove failed for %r: %r", previous, exc)
        try:
            backend_agent_md_revision.labels(**_LABELS, revision=current).set(1)
        except Exception as exc:  # pragma: no cover — defensive
            logger.debug("agent_md_revision set failed for %r: %r", current, exc)

    def _mcp_watchers(self):
        """Return callables for AGENTS.md, mcp.json, config.toml, and API-key
        secret-file watching (#371, #432, #561, #728)."""
        return [
            self.agent_md_watcher,
            self.mcp_config_watcher,
            self.tool_config_watcher,
            self.api_key_file_watcher,
        ]

    async def perform_initial_loads(self) -> None:
        """Pre-load AGENTS.md, mcp.json, and config.toml before readiness (#1095).

        Mirrors the claude peer added in #869: run the first parse of every
        hot-reloadable config file synchronously on the lifespan/startup path
        so a request arriving in the first ~100ms after readiness flips sees
        a populated executor rather than an empty ``_agent_md_content`` or
        empty ``_mcp_config``. Watchers detect the pre-loaded flags below
        and skip their own first parse before entering ``awatch()``.
        """
        # AGENTS.md — __init__ already loaded it synchronously, but refresh
        # here so any content written between import time and lifespan start
        # is picked up before readiness flips.
        try:
            self._agent_md_content = _load_agent_md()
            logger.info("AGENTS.md loaded (initial) from %s", AGENT_MD)
        except Exception as exc:
            logger.warning("AGENTS.md initial load failed: %r (keeping __init__ value)", exc)
        # Refresh cached revision + gauge (#1097). If the content changed
        # between __init__ time and lifespan start, swap the gauge label set
        # so only the active revision reports 1.
        _prev_rev = self._agent_md_revision
        _new_rev = _compute_agent_md_revision(self._agent_md_content)
        if _new_rev != _prev_rev:
            self._agent_md_revision = _new_rev
            self._stamp_agent_md_revision(_new_rev, previous=_prev_rev)
        self._initial_agent_md_loaded = True

        # MCP config — load + enter the initial stack so requests land on
        # live servers.
        try:
            self._mcp_config = await asyncio.to_thread(_load_mcp_config)
        except Exception as exc:
            logger.warning("MCP config initial load failed: %r", exc)
            self._mcp_config = {}
        if self._mcp_config:
            logger.info("MCP config loaded (initial): %s", list(self._mcp_config.keys()))
        try:
            # #1496: shield the MCP stack setup so a perform_initial_loads()
            # timeout in main.py cannot cancel _apply_mcp_config mid-enter,
            # which would leave stdio MCP subprocesses orphaned (the
            # AsyncExitStack wouldn't have been aenter'd fully yet so no
            # __aexit__ runs to kill the child). The caller's wait_for
            # still raises TimeoutError; the shielded task completes on
            # its own and the watchers remain authoritative.
            await asyncio.shield(self._apply_mcp_config(self._mcp_config))
        except asyncio.CancelledError:
            # Propagate cancellation cleanly; the shielded inner task has
            # its own lifetime and will tear itself down via the
            # AsyncExitStack when it finishes.
            raise
        except Exception as exc:
            logger.warning("Initial MCP stack start failed: %r", exc)
        self._initial_mcp_loaded = True

        # [tools] table from config.toml.
        try:
            self._tool_config = await asyncio.to_thread(_load_tool_config)
            logger.info(
                "tool config loaded (initial) from %s: %s",
                OPENAI_CONFIG_TOML,
                dict(self._tool_config),
            )
        except Exception as exc:
            logger.warning("tool config initial load failed: %r", exc)
        self._initial_tool_config_loaded = True

        # Session-layout self-test (#806). Runs after the config loads so log
        # ordering matches the other backends' readiness handshake. Offloaded
        # to a thread because sqlite I/O is blocking.
        try:
            await asyncio.to_thread(_session_layout_self_test)
        except Exception as exc:
            logger.warning(
                "session-layout self-test scheduling failed: %r (#806)",
                exc,
            )

    async def _apply_mcp_config(self, mcp_config: dict) -> None:
        """Enter the given MCP config into a fresh lifespan-scoped stack (#526).

        Tears down any previously-entered stack first, then enters each server
        as an async context manager. Failures on individual servers are logged
        and skipped so one broken entry does not prevent others from starting.
        The backend_mcp_servers_active gauge reflects the actually-running count,
        not the config-loaded count.
        """
        if self._mcp_servers_lock is None:
            self._mcp_servers_lock = asyncio.Lock()
        async with self._mcp_servers_lock:
            # Park the previous stack rather than closing it immediately
            # (#667). In-flight requests may still be using its servers;
            # we can only aclose once every caller has released. Stacks
            # with refcount==0 (no current in-flight users) are closed
            # now for prompt subprocess teardown.
            if self._mcp_stack is not None:
                _prev_stack = self._mcp_stack
                _prev_refcount = self._mcp_stack_refcount
                self._mcp_stack = None
                self._live_mcp_servers = []
                self._mcp_stack_refcount = 0
                if _prev_refcount <= 0:
                    try:
                        await _prev_stack.aclose()
                    except Exception as _close_exc:
                        logger.warning("Previous MCP stack aclose error: %s", _close_exc)
                else:
                    logger.info(
                        "MCP hot-reload: deferring aclose of previous stack until %d in-flight request(s) release it.",
                        _prev_refcount,
                    )
                    self._mcp_old_stacks.append((_prev_stack, _prev_refcount))

            new_stack = AsyncExitStack()
            await new_stack.__aenter__()
            new_live: list = []
            try:
                for _srv in _build_mcp_servers(mcp_config or {}):
                    # mcp.call child span (#630) — wraps the stdio transport
                    # bring-up so the trace shows which MCP server the stack
                    # is spinning up (or failing to). kind=client reflects
                    # that the backend is dialling an external server.
                    with start_span(
                        "mcp.call",
                        kind="client",
                        attributes={
                            "mcp.server": getattr(_srv, "name", "?") or "?",
                            "mcp.tool": "__start__",
                        },
                    ) as _mcp_span:
                        try:
                            _live = await new_stack.enter_async_context(_srv)
                            new_live.append(_live)
                        except Exception as _mcp_exc:
                            set_span_error(_mcp_span, _mcp_exc)
                            logger.warning(
                                "MCP server %r failed to start (%s); proceeding without it.",
                                getattr(_srv, "name", "?"),
                                _mcp_exc,
                            )
            except Exception:
                # If the per-server loop itself raises something unexpected,
                # unwind the partial stack so we do not leak subprocesses.
                try:
                    await new_stack.aclose()
                except Exception:
                    pass
                raise

            self._mcp_stack = new_stack
            self._live_mcp_servers = new_live
            if backend_mcp_servers_active is not None:
                backend_mcp_servers_active.labels(**_LABELS).set(len(new_live))

    async def _snapshot_live_mcp_servers(self) -> list:
        """Return a defensive copy of the currently-live MCP server list (#526).

        Taken under the lock so a concurrent hot-reload cannot swap the list
        out from under the caller mid-read.
        """
        if self._mcp_servers_lock is None:
            self._mcp_servers_lock = asyncio.Lock()
        async with self._mcp_servers_lock:
            return list(self._live_mcp_servers)

    async def _acquire_mcp_stack(self) -> tuple[list, "AsyncExitStack | None"]:
        """Acquire the current MCP stack for one in-flight request (#667).

        Returns a snapshot of the live server list and the stack the caller
        is now holding a refcount on. The caller MUST pair this with
        _release_mcp_stack(stack) in a finally block.
        """
        if self._mcp_servers_lock is None:
            self._mcp_servers_lock = asyncio.Lock()
        async with self._mcp_servers_lock:
            stack = self._mcp_stack
            if stack is not None:
                self._mcp_stack_refcount += 1
            return list(self._live_mcp_servers), stack

    async def _release_mcp_stack(self, stack: "AsyncExitStack | None") -> None:
        """Release a refcount previously acquired via _acquire_mcp_stack.

        Closes a parked old stack when its refcount hits zero so subprocesses
        stop promptly without breaking in-flight traffic (#667).
        """
        if stack is None:
            return
        if self._mcp_servers_lock is None:
            self._mcp_servers_lock = asyncio.Lock()
        async with self._mcp_servers_lock:
            # Is this the current stack?
            if self._mcp_stack is stack:
                if self._mcp_stack_refcount > 0:
                    self._mcp_stack_refcount -= 1
                else:
                    # Refcount underflow: more releases than acquires.
                    # Log loudly so the caller's logic error doesn't hide
                    # behind a silent no-op (which previously masked
                    # double-release patterns and left old stacks
                    # potentially never closed).
                    logger.warning(
                        "MCP stack release underflow on current stack — "
                        "refcount already 0; release ignored. This indicates "
                        "an unmatched _release_mcp_stack call."
                    )
                return
            # Otherwise it's a parked old stack.
            for i, (old_stack, old_ref) in enumerate(self._mcp_old_stacks):
                if old_stack is stack:
                    new_ref = old_ref - 1
                    if new_ref <= 0:
                        self._mcp_old_stacks.pop(i)
                        try:
                            await old_stack.aclose()
                        except Exception as _close_exc:
                            logger.warning(
                                "Deferred MCP stack aclose error: %s",
                                _close_exc,
                            )
                    else:
                        self._mcp_old_stacks[i] = (old_stack, new_ref)
                    return
            # Stack matched neither current nor any parked old stack.
            # Log so an operator can see the leak — the unmatched stack's
            # subprocesses + HTTP connections will not be reclaimed.
            logger.warning(
                "MCP stack release matched no tracked stack — "
                "subprocess/connection leak suspected. This indicates "
                "a stack reference outliving its tracking entry."
            )

    async def mcp_config_watcher(self) -> None:
        """Watch MCP_CONFIG_PATH for changes and hot-reload the MCP server config (#432, #526).

        Mirrors the claude pattern: load on startup, then watch the parent
        directory for any changes to the config file. Each reload restarts the
        lifespan-scoped MCP server stack so stdio subprocesses are respawned
        cleanly under the new config and existing request traffic sees a
        consistent snapshot.
        """
        from watchfiles import awatch as _awatch

        # Initial load + first stack entry. Skipped when
        # perform_initial_loads already ran on the lifespan startup path
        # (#1095) — the state is populated and readiness has been gated
        # on it, so re-parsing here would be redundant work.
        if not self._initial_mcp_loaded:
            self._mcp_config = await asyncio.to_thread(_load_mcp_config)
            if self._mcp_config:
                logger.info("MCP config loaded: %s", list(self._mcp_config.keys()))
            try:
                await self._apply_mcp_config(self._mcp_config)
            except Exception as _apply_exc:
                logger.warning("Initial MCP stack start failed: %s", _apply_exc)

        watch_dir = os.path.dirname(os.path.abspath(MCP_CONFIG_PATH))
        while True:
            if not os.path.isdir(watch_dir):
                logger.info("MCP config directory not found — retrying in 10s.")
                await asyncio.sleep(10)
                continue
            async for changes in _awatch(watch_dir, recursive=False):
                if backend_watcher_events_total is not None:
                    backend_watcher_events_total.labels(**_LABELS, watcher="mcp").inc()
                for _, path in changes:
                    if os.path.abspath(path) == os.path.abspath(MCP_CONFIG_PATH):
                        self._mcp_config = await asyncio.to_thread(_load_mcp_config)
                        logger.info("MCP config reloaded: %s", list(self._mcp_config.keys()))
                        try:
                            await self._apply_mcp_config(self._mcp_config)
                        except Exception as _apply_exc:
                            logger.warning("MCP stack reload failed: %s", _apply_exc)
                        if backend_mcp_config_reloads_total is not None:
                            backend_mcp_config_reloads_total.labels(**_LABELS).inc()
                        break
            logger.warning("MCP config directory watcher exited — retrying in 10s.")
            if backend_file_watcher_restarts_total is not None:
                backend_file_watcher_restarts_total.labels(**_LABELS, watcher="mcp").inc()
            await asyncio.sleep(10)

    async def agent_md_watcher(self) -> None:
        """Watch AGENT_MD for changes and hot-reload agent identity / behavioral instructions (#371).

        This ensures that updating AGENTS.md does not require a container restart,
        consistent with all other file-based configuration in the platform.
        """
        from watchfiles import awatch as _awatch

        # Perform an initial load so the watcher starts with current content.
        # Skipped when perform_initial_loads already ran (#1095).
        if not self._initial_agent_md_loaded:
            self._agent_md_content = _load_agent_md()
            logger.info("AGENTS.md loaded from %s", AGENT_MD)
            # Refresh revision gauge (#1097) in case __init__'s snapshot is stale.
            _prev_rev = self._agent_md_revision
            _new_rev = _compute_agent_md_revision(self._agent_md_content)
            if _new_rev != _prev_rev:
                self._agent_md_revision = _new_rev
                self._stamp_agent_md_revision(_new_rev, previous=_prev_rev)

        watch_dir = os.path.dirname(os.path.abspath(AGENT_MD))
        while True:
            if not os.path.isdir(watch_dir):
                logger.info("AGENTS.md directory not found — retrying in 10s.")
                await asyncio.sleep(10)
                continue
            async for changes in _awatch(watch_dir):
                if backend_watcher_events_total is not None:
                    backend_watcher_events_total.labels(**_LABELS, watcher="agent_md").inc()
                for _, path in changes:
                    if os.path.abspath(path) == os.path.abspath(AGENT_MD):
                        self._agent_md_content = _load_agent_md()
                        logger.info("AGENTS.md reloaded from %s", AGENT_MD)
                        # Update revision gauge on hot-reload (#1097). Clear
                        # the old label set so only the live revision reads 1.
                        _prev_rev = self._agent_md_revision
                        _new_rev = _compute_agent_md_revision(self._agent_md_content)
                        if _new_rev != _prev_rev:
                            self._agent_md_revision = _new_rev
                            self._stamp_agent_md_revision(_new_rev, previous=_prev_rev)
                        break
            logger.warning("AGENTS.md directory watcher exited — retrying in 10s.")
            if backend_file_watcher_restarts_total is not None:
                backend_file_watcher_restarts_total.labels(**_LABELS, watcher="agent_md").inc()
            await asyncio.sleep(10)

    async def tool_config_watcher(self) -> None:
        """Watch OPENAI_CONFIG_TOML for changes and hot-reload the [tools] table (#561).

        Mirrors the mcp_config_watcher / agent_md_watcher pattern: load once on
        startup into ``self._tool_config`` (used by ``_build_tools`` via
        ``run_query(tool_config=...)``), then watch the parent directory for any
        changes to the config file. Eliminates the per-request
        ``open + tomllib.load`` that previously ran on the hot path of every
        Agent construction.
        """
        from watchfiles import awatch as _awatch

        # Initial load so the cache is populated before the first request
        # arrives. Run in a thread so slow/remote filesystems do not stall the
        # event loop at startup (same rationale as mcp_config_watcher).
        # Skipped when perform_initial_loads already ran (#1095).
        if not self._initial_tool_config_loaded:
            self._tool_config = await asyncio.to_thread(_load_tool_config)
            logger.info("tool config loaded from %s: %s", OPENAI_CONFIG_TOML, dict(self._tool_config))

        watch_dir = os.path.dirname(os.path.abspath(OPENAI_CONFIG_TOML))
        while True:
            if not os.path.isdir(watch_dir):
                logger.info("tool config directory not found — retrying in 10s.")
                await asyncio.sleep(10)
                continue
            async for changes in _awatch(watch_dir, recursive=False):
                if backend_watcher_events_total is not None:
                    backend_watcher_events_total.labels(**_LABELS, watcher="tool_config").inc()
                for _, path in changes:
                    if os.path.abspath(path) == os.path.abspath(OPENAI_CONFIG_TOML):
                        self._tool_config = await asyncio.to_thread(_load_tool_config)
                        logger.info("tool config reloaded from %s: %s", OPENAI_CONFIG_TOML, dict(self._tool_config))
                        break
            logger.warning("tool config directory watcher exited — retrying in 10s.")
            if backend_file_watcher_restarts_total is not None:
                backend_file_watcher_restarts_total.labels(**_LABELS, watcher="tool_config").inc()
            await asyncio.sleep(10)

    async def api_key_file_watcher(self) -> None:
        """Watch the OPENAI API key file and refresh the cached key on change (#728).

        Mirrors gemini's #1057 pattern. Operators rotating ``OPENAI_API_KEY``
        previously had to restart every pod because the key was captured at
        module import. When ``OPENAI_API_KEY_FILE`` is set, this watcher
        re-reads the file and updates ``os.environ['OPENAI_API_KEY']`` on
        every change so the next ``run_query`` call picks up the new value
        via ``_current_openai_api_key``.

        Disabled when ``OPENAI_API_KEY_FILE`` is unset (key sourced from the
        literal env var only). Uses the same ``watchfiles.awatch`` pattern as
        every other watcher in this module so restart / watcher_events
        metrics remain comparable.
        """
        key_file = os.environ.get("OPENAI_API_KEY_FILE", "").strip()
        if not key_file:
            logger.info(
                "api_key_file_watcher: OPENAI_API_KEY_FILE unset; key rotation "
                "via secret-file is disabled. Set OPENAI_API_KEY_FILE to a path "
                "for hot rotation without pod restart. (#728)"
            )
            return
        from watchfiles import awatch as _awatch

        watch_dir = os.path.dirname(os.path.abspath(key_file)) or "/"
        while True:
            if not os.path.isdir(watch_dir):
                logger.info(
                    "api_key_file_watcher: directory %r not found — retrying in 10s.",
                    watch_dir,
                )
                await asyncio.sleep(10)
                continue
            try:
                async for changes in _awatch(watch_dir, recursive=False):
                    if backend_watcher_events_total is not None:
                        backend_watcher_events_total.labels(
                            **_LABELS,
                            watcher="api_key_file",
                        ).inc()
                    for _, path in changes:
                        if os.path.abspath(path) == os.path.abspath(key_file):
                            logger.info(
                                "api_key_file_watcher: %r changed — refreshing OPENAI_API_KEY for the next request.",
                                key_file,
                            )
                            try:
                                with open(key_file) as _fh:
                                    _new_key = _fh.read().strip()
                                if _new_key:
                                    os.environ["OPENAI_API_KEY"] = _new_key
                            except Exception as _read_exc:
                                logger.warning(
                                    "api_key_file_watcher: failed to read %r: %r",
                                    key_file,
                                    _read_exc,
                                )
                            break
            except Exception as _w_exc:
                logger.warning(
                    "api_key_file_watcher: awatch loop exited (%r) — retrying in 10s.",
                    _w_exc,
                )
                if backend_file_watcher_restarts_total is not None:
                    backend_file_watcher_restarts_total.labels(
                        **_LABELS,
                        watcher="api_key_file",
                    ).inc()
                await asyncio.sleep(10)

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        _exec_start = time.monotonic()
        prompt = context.get_user_input()
        metadata = context.message.metadata or {}
        # Empty-prompt guard (#544 / #801). An empty or whitespace-only prompt
        # will otherwise reach the SDK and burn a query/tokens on a no-op
        # "How can I help?" reply. Reject early, bump counters, and log the
        # rejection so the occurrence is visible in both metrics and the
        # transcript. Mirrors the claude implementation.
        if not prompt or not prompt.strip():
            _empty_sid_raw = str(
                context.context_id or (context.message.metadata or {}).get("session_id") or ""
            ).strip()[:256]
            # Do NOT default the sanitised raw to "unknown" here: the
            # accepted-prompt path below passes "" for the same input,
            # which derive_session_id maps to a fresh uuid4, while
            # "unknown" would deterministically collapse every such
            # caller onto the same derived id (#990). Route the raw ""
            # through derive_session_id directly so both paths fracture
            # identically under SESSION_ID_SECRET.
            _empty_sid_sanitized = "".join(c for c in _empty_sid_raw if c >= " ")
            # Route through derive_session_id (#880) so log-entries and
            # metric labels use the HMAC-bound id under SESSION_ID_SECRET,
            # matching the accepted-prompt path below. Otherwise the
            # rejected-prompt path writes under the raw caller id while
            # the valid path uses the derived id, splitting rows for the
            # same logical session across two ids.
            from session_binding import derive_session_id as _derive_session_id

            _empty_caller_id = metadata.get("caller_id") if isinstance(metadata.get("caller_id"), str) else None
            _empty_sid = _derive_session_id(
                _empty_sid_sanitized,
                caller_identity=_empty_caller_id,
            )
            logger.warning(f"Session {_empty_sid!r}: rejected execute() — prompt was empty or whitespace-only (#544).")
            if backend_empty_prompts_total is not None:
                backend_empty_prompts_total.labels(**_LABELS).inc()
            if backend_a2a_requests_total is not None:
                backend_a2a_requests_total.labels(**_LABELS, status="error").inc()
            if backend_a2a_request_duration_seconds is not None:
                backend_a2a_request_duration_seconds.labels(**_LABELS).observe(time.monotonic() - _exec_start)
            if backend_a2a_last_request_timestamp_seconds is not None:
                backend_a2a_last_request_timestamp_seconds.labels(**_LABELS).set(time.time())
            await log_entry(
                "system",
                "execute() rejected: empty or whitespace-only prompt (#544).",
                _empty_sid,
            )
            await event_queue.enqueue_event(
                new_agent_text_message(
                    "Error: prompt is empty or whitespace-only; request rejected without dispatching to the model."
                )
            )
            return
        # Hard prompt-size cap (#1620). Reject before the prompt reaches the
        # SDK so a pathological caller cannot OOM the pod by shipping a
        # multi-GB payload. Mirrors the empty-prompt rejection idiom: bump a
        # Prometheus counter, log the rejection at WARN, and return a clean
        # A2A error message rather than crashing the worker. The
        # PromptTooLargeError type is exported from shared/exceptions for
        # programmatic detection by callers/tests; the executor itself
        # converts it into a user-visible A2A text message so the harness
        # surface stays consistent with the empty-prompt path.
        _prompt_bytes = len(prompt.encode("utf-8"))
        if _prompt_bytes > _MAX_PROMPT_BYTES:
            _too_large_sid_raw = str(
                context.context_id or (context.message.metadata or {}).get("session_id") or ""
            ).strip()[:256]
            _too_large_sid_sanitized = "".join(c for c in _too_large_sid_raw if c >= " ")
            from session_binding import derive_session_id as _derive_session_id_tl

            _too_large_caller_id = metadata.get("caller_id") if isinstance(metadata.get("caller_id"), str) else None
            _too_large_sid = _derive_session_id_tl(
                _too_large_sid_sanitized,
                caller_identity=_too_large_caller_id,
            )
            _too_large_err = PromptTooLargeError(_prompt_bytes, _MAX_PROMPT_BYTES)
            logger.warning(
                f"Session {_too_large_sid!r}: rejected execute() — prompt size "
                f"{_prompt_bytes} bytes exceeds MAX_PROMPT_BYTES={_MAX_PROMPT_BYTES} (#1620)."
            )
            if backend_prompt_too_large_total is not None:
                backend_prompt_too_large_total.labels(**_LABELS).inc()
            if backend_a2a_requests_total is not None:
                backend_a2a_requests_total.labels(**_LABELS, status="error").inc()
            if backend_a2a_request_duration_seconds is not None:
                backend_a2a_request_duration_seconds.labels(**_LABELS).observe(time.monotonic() - _exec_start)
            if backend_a2a_last_request_timestamp_seconds is not None:
                backend_a2a_last_request_timestamp_seconds.labels(**_LABELS).set(time.time())
            await log_entry(
                "system",
                f"execute() rejected: prompt {_prompt_bytes} bytes exceeds "
                f"MAX_PROMPT_BYTES={_MAX_PROMPT_BYTES} (#1620).",
                _too_large_sid,
            )
            await event_queue.enqueue_event(new_agent_text_message(f"Error: {_too_large_err}"))
            return
        # OTel server span continuation (#469).
        from otel import extract_otel_context as _extract_ctx

        _tp = metadata.get("traceparent") if isinstance(metadata.get("traceparent"), str) else None
        _otel_parent = _extract_ctx({"traceparent": _tp}) if _tp else None
        _raw_sid = "".join(
            c for c in str(context.context_id or metadata.get("session_id") or "").strip()[:256] if c >= " "
        )
        # Per-caller session_id binding parity with claude (#710) and
        # gemini (#733). The same risk applies to openai: raw session_id
        # is caller-supplied and acts as a bearer secret when left
        # unbound. The shared helper is backward compatible (no-op when
        # SESSION_ID_SECRET is unset), so this merely closes the
        # consistency gap across backends without changing default
        # behaviour.
        from session_binding import (
            derive_session_id as _derive_session_id,
        )
        from session_binding import (
            derive_session_id_candidates as _derive_session_id_candidates,
        )
        from session_binding import (
            note_prev_secret_hit as _note_prev_secret_hit,
        )

        _caller_id = metadata.get("caller_id") if isinstance(metadata.get("caller_id"), str) else None
        # Probe-list rotation (#1042). Same pattern as claude executor:
        # candidates[0] is the current-secret derivation; any subsequent
        # candidate corresponds to SESSION_ID_SECRET_PREV. If existing
        # rows in OPENAI_SESSION_DB are keyed under the previous derivation
        # we route this request to the old id so resume works mid-window.
        _sid_candidates = _derive_session_id_candidates(_raw_sid, caller_identity=_caller_id)
        session_id = _sid_candidates[0]
        if len(_sid_candidates) > 1:
            for _prev_sid in _sid_candidates[1:]:
                if await asyncio.to_thread(_sqlite_session_exists, _prev_sid):
                    session_id = _prev_sid
                    _note_prev_secret_hit(_raw_sid)
                    break
        _ = _derive_session_id  # noqa: F841 — retained for callers not yet on the probe list
        model = metadata.get("model") or None
        # Shared parser lives in shared/validation.py (#537, #428, #555).
        max_tokens = parse_max_tokens(
            metadata.get("max_tokens"),
            logger=logger,
            source="A2A metadata",
            session_id=session_id,
        )
        task_id = context.task_id

        if task_id:
            current = asyncio.current_task()
            if current:
                self._running_tasks[task_id] = current
                if backend_running_tasks is not None:
                    backend_running_tasks.labels(**_LABELS).inc()
        _response = ""
        _success = False
        _error: str | None = None
        # Streaming bridge (#430): forward each text delta to the A2A
        # event_queue as it arrives. Tracks emission count so the
        # post-completion aggregated enqueue can be skipped when chunks were
        # already delivered.
        #
        # Dropped-chunk tracking (#724): when on_chunk raises TimeoutError
        # in _run_query_inner, the chunk text is dropped silently and
        # iteration continues. We now count the drops on the outer
        # executor via a shared state dict so the final-flush decision
        # knows whether the partial-stream was actually complete or
        # had gaps. When any chunk was dropped we emit the full
        # aggregated _response at completion so the client sees
        # complete text regardless of which mid-stream events were
        # truncated.
        _chunks_emitted = 0
        _streaming_label_model = _resolve_model_label(model)

        # Per-session SSE drill-down stream (#1110 phase 4). Best-effort
        # — a broadcaster fault must not break the A2A response path.
        #
        # #1498: use a per-turn LOCAL seq counter instead of the shared
        # broadcaster's reset_turn_seq/next_turn_seq. The broadcaster is
        # per-session and shared across concurrent requests; calling
        # reset_turn_seq() mid-way through another turn would break the
        # strictly-monotonic seq invariant that SSE consumers rely on.
        # The publish_chunk API already accepts an external seq so the
        # per-turn counter can live entirely in this function scope.
        _turn_seq_counter: list[int] = [0]

        def _next_seq() -> int:
            n = _turn_seq_counter[0]
            _turn_seq_counter[0] = n + 1
            return n

        try:
            from session_stream import get_session_stream as _get_session_stream

            _sess_stream = _get_session_stream(session_id, agent_id=AGENT_OWNER)
            _sess_stream.publish_chunk(
                role="user",
                seq=_next_seq(),
                content=prompt,
                final=True,
            )
        except Exception as _sess_exc:  # pragma: no cover
            logger.warning("session_stream: user prompt publish failed: %r", _sess_exc)
            _sess_stream = None

        async def _emit_chunk(text: str) -> None:
            nonlocal _chunks_emitted
            # Per-session SSE drill-down (#1110 phase 4). Publish before
            # enqueue so the session stream sees the chunk even if the
            # A2A enqueue fails (client sees overrun separately via
            # _attempted_texts + drop-fallback).
            if _sess_stream is not None:
                try:
                    _sess_stream.publish_chunk(
                        role="assistant",
                        seq=_next_seq(),  # #1498: local per-turn counter
                        content=text,
                        final=False,
                    )
                except Exception as _s_exc:  # pragma: no cover
                    logger.warning("session_stream: chunk publish failed: %r", _s_exc)
            # Per-chunk A2A event_queue emission removed — see
            # backends/claude/executor.py _emit_chunk for the rationale
            # (A2A SDK's blocking handler returns on first Message event,
            # so per-chunk Message emission caused blocking callers to
            # see only the first chunk). _chunks_emitted is retained for
            # the streaming-events metric only.
            _chunks_emitted += 1
            if backend_streaming_events_emitted_total is not None:
                backend_streaming_events_emitted_total.labels(**_LABELS, model=_streaming_label_model).inc()

        from otel import set_span_error as _set_span_error
        from otel import start_span as _start_span

        _otel_span = None
        try:
            with _start_span(
                "openai.execute",
                kind="server",
                parent_context=_otel_parent,
                attributes={
                    "a2.session_id": session_id,
                    "a2.model": _resolve_model_label(model),
                    "a2.agent": AGENT_NAME,
                    "a2.agent_id": AGENT_ID,
                },
            ) as _otel_span:
                _mcp_servers_snapshot, _mcp_stack_held = await self._acquire_mcp_stack()
                try:
                    _response = await run(
                        prompt,
                        session_id,
                        self._sessions,
                        self._agent_md_content,
                        model=model,
                        max_tokens=max_tokens,
                        on_chunk=_emit_chunk,
                        live_mcp_servers=_mcp_servers_snapshot,
                        tool_config=self._tool_config,
                    )
                finally:
                    # Release the hot-reload refcount (#667). Outside of
                    # try so exceptions still propagate.
                    await self._release_mcp_stack(_mcp_stack_held)
                _success = True
                # Always emit the final aggregated Message event (was
                # previously gated on _chunks_emitted == 0; with the
                # per-chunk path removed there's no duplicate-text risk
                # and this is the ONLY Message event blocking callers
                # see, so it must always fire when text was produced).
                if _response:
                    await event_queue.enqueue_event(new_agent_text_message(_response))
                # Per-session stream: final marker chunk (#1110 phase 4).
                if _sess_stream is not None:
                    try:
                        _sess_stream.publish_chunk(
                            role="assistant",
                            seq=_next_seq(),  # #1498: local per-turn counter
                            content="",
                            final=True,
                        )
                    except Exception as _f_exc:  # pragma: no cover
                        logger.warning("session_stream: final chunk publish failed: %r", _f_exc)
                if backend_a2a_requests_total is not None:
                    backend_a2a_requests_total.labels(**_LABELS, status="success").inc()
        except Exception as _exc:
            _error = repr(_exc)
            _set_span_error(_otel_span, _exc)
            if backend_a2a_requests_total is not None:
                backend_a2a_requests_total.labels(**_LABELS, status="error").inc()
            # Per-session stream: emit a terminal final=True assistant
            # chunk on the failure path too (#1141) so observers see a
            # deterministic turn boundary even when execution blew up
            # mid-stream.  Best-effort — cannot mask the original
            # exception being re-raised below.
            if _sess_stream is not None:
                try:
                    _sess_stream.publish_chunk(
                        role="assistant",
                        seq=_next_seq(),  # #1498: local per-turn counter
                        content="",
                        final=True,
                    )
                except Exception as _ef_exc:  # pragma: no cover
                    logger.warning(
                        "session_stream: final chunk publish on error path failed: %r",
                        _ef_exc,
                    )
            raise
        finally:
            if backend_a2a_request_duration_seconds is not None:
                backend_a2a_request_duration_seconds.labels(**_LABELS).observe(time.monotonic() - _exec_start)
            if backend_a2a_last_request_timestamp_seconds is not None:
                backend_a2a_last_request_timestamp_seconds.labels(**_LABELS).set(time.time())
            if task_id and task_id in self._running_tasks:
                self._running_tasks.pop(task_id)
                if backend_running_tasks is not None:
                    backend_running_tasks.labels(**_LABELS).dec()

    async def close(self) -> None:
        """Cancel and drain all MCP watcher tasks, tear down the MCP server
        stack (#526), and close the Playwright computer.

        Idempotent: the public ``self.closed`` flag guards a second invocation
        so the shutdown path cannot double-close shared resources such as
        ``_browser_pool`` (#555).
        """
        if self.closed:
            return
        for task in self._mcp_watcher_tasks:
            task.cancel()
        if self._mcp_watcher_tasks:
            await asyncio.gather(*self._mcp_watcher_tasks, return_exceptions=True)
        self._mcp_watcher_tasks.clear()
        # Tear down the lifespan-scoped MCP stack so all stdio subprocesses and
        # HTTP sessions are released cleanly on shutdown. Done after the
        # watcher task is drained to avoid racing a concurrent reload.
        if self._mcp_stack is not None:
            try:
                await self._mcp_stack.aclose()
            except Exception as _close_exc:
                logger.warning("MCP stack aclose on shutdown: %s", _close_exc)
            self._mcp_stack = None
            self._live_mcp_servers = []
            self._mcp_stack_refcount = 0
            if backend_mcp_servers_active is not None:
                backend_mcp_servers_active.labels(**_LABELS).set(0)
        # Also drain any parked old stacks (#667) — those are from previous
        # hot-reloads that still had in-flight holders when swapped. On
        # shutdown we force-close them regardless of refcount.
        while self._mcp_old_stacks:
            _old_stack, _ = self._mcp_old_stacks.pop()
            try:
                await _old_stack.aclose()
            except Exception as _close_exc:
                logger.warning("Parked MCP stack aclose on shutdown: %s", _close_exc)
        global _browser_pool
        if _browser_pool is not None:
            try:
                await _browser_pool.close()
            except Exception as _e:
                logger.warning("Failed to close BrowserPool on shutdown: %s", _e)
            _browser_pool = None
        self.closed = True

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        if backend_task_cancellations_total is not None:
            backend_task_cancellations_total.labels(**_LABELS).inc()
        task_id = context.task_id
        task = self._running_tasks.get(task_id) if task_id else None
        if task:
            task.cancel()
            logger.info(f"Task {task_id!r} cancellation requested.")
        else:
            logger.info(f"Task {task_id!r} cancellation requested but no running task found.")
