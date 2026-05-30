import asyncio
import hashlib
import json
import logging
import os
import pathlib
import threading
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timezone

from a2a.server.agent_execution import AgentExecutor as A2AAgentExecutor
from a2a.server.agent_execution import RequestContext
from a2a.server.events import EventQueue
from a2a.utils import new_agent_text_message
from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient, HookMatcher
from claude_agent_sdk.types import AssistantMessage, ResultMessage, TextBlock, ToolResultBlock, ToolUseBlock
from exceptions import BudgetExceededError, PromptTooLargeError
from hooks import (
    BASELINE_RULES,
    DECISION_DENY,
    DECISION_WARN,
    HOOKS_BASELINE_ENABLED,
    HOOKS_CONFIG_PATH,
    HookState,
    evaluate_pre_tool_use,
    load_hooks_config_sync,
)
from log_utils import _append_log
from metrics import (
    backend_a2a_last_request_timestamp_seconds,
    backend_a2a_request_duration_seconds,
    backend_a2a_requests_total,
    backend_active_sessions,
    backend_agent_md_revision,
    backend_budget_exceeded_total,
    backend_concurrent_queries,
    backend_context_exhaustion_total,
    backend_context_tokens,
    backend_context_tokens_remaining,
    backend_context_usage_percent,
    backend_context_warnings_total,
    backend_empty_prompts_total,
    backend_empty_responses_total,
    backend_file_watcher_restarts_total,
    backend_hooks_active_rules,
    backend_hooks_blocked_total,
    backend_hooks_config_errors_total,
    backend_hooks_config_reloads_total,
    backend_hooks_denials_total,
    backend_hooks_enforcement_mode,
    backend_hooks_evaluations_total,
    backend_hooks_shed_total,
    backend_hooks_warnings_total,
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
    backend_sdk_subprocess_spawn_duration_seconds,
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
    backend_task_retries_total,
    backend_task_timeout_headroom_seconds,
    backend_tasks_total,
    backend_tasks_with_stderr_total,
    backend_text_blocks_per_query,
    backend_tool_audit_bytes_per_entry,
    backend_tool_audit_entries_total,
    backend_tool_audit_rotation_pressure_total,
    backend_watcher_events_total,
)
from otel import start_span
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
from validation import sanitize_model_label
from watchfiles import awatch

logger = logging.getLogger(__name__)


try:
    import unicodedata as _unicodedata_startup

    _STARTUP_CWD = _unicodedata_startup.normalize("NFC", os.getcwd())
except Exception:
    _STARTUP_CWD = None


def _session_file_path(session_id: str) -> "pathlib.Path | None":
    """Return the on-disk path for a Claude session file, or None on error.

    Derives the session file path from documented conventions only — no
    private SDK internals.  The Claude Agent SDK stores session files at:

        <config_home>/projects/<sanitized_cwd>/<session_id>.jsonl

    where config_home is $CLAUDE_CONFIG_DIR or ~/.claude, and sanitized_cwd
    is the working directory with all non-alphanumeric characters replaced by
    hyphens (truncated to 200 characters with a hash suffix if longer).
    """
    import re
    import unicodedata

    _SANITIZE_RE = re.compile(r"[^a-zA-Z0-9]")
    _MAX_LEN = 200

    def _simple_hash(s: str) -> str:
        """32-bit JS-compatible hash to base36 — matches Claude CLI directory naming."""
        h = 0
        for ch in s:
            h = (h << 5) - h + ord(ch)
            h = h & 0xFFFFFFFF
            if h >= 0x80000000:
                h -= 0x100000000
        h = abs(h)
        if h == 0:
            return "0"
        digits = "0123456789abcdefghijklmnopqrstuvwxyz"
        out: list[str] = []
        n = h
        while n > 0:
            out.append(digits[n % 36])
            n //= 36
        return "".join(reversed(out))

    def _sanitize(name: str) -> str:
        sanitized = _SANITIZE_RE.sub("-", name)
        if len(sanitized) <= _MAX_LEN:
            return sanitized
        return f"{sanitized[:_MAX_LEN]}-{_simple_hash(name)}"

    def _config_home() -> pathlib.Path:
        config_dir = os.environ.get("CLAUDE_CONFIG_DIR")
        if config_dir:
            return pathlib.Path(unicodedata.normalize("NFC", config_dir))
        return pathlib.Path(unicodedata.normalize("NFC", str(pathlib.Path.home() / ".claude")))

    try:
        # Refuse to resolve if cwd has drifted since startup (#1047).  Any
        # in-process ``os.chdir`` would otherwise produce a wrong-but-
        # plausible path that misses history and targets unlinks at the
        # wrong files.  Log once at warning level, bump the drift counter,
        # and return None so callers treat the session as unknown rather
        # than corrupting state.
        if _STARTUP_CWD is None:
            return None
        try:
            live_cwd = os.getcwd()
        except Exception:
            live_cwd = None
        if live_cwd != _STARTUP_CWD:
            if backend_session_path_mismatch_total is not None:
                try:
                    backend_session_path_mismatch_total.labels(**_LABELS, reason="cwd_drift").inc()
                except Exception:
                    pass
            if not getattr(_session_file_path, "_logged_cwd_drift", False):
                logger.warning(
                    "session-path: cwd drifted since startup (startup=%r live=%r) — "
                    "refusing to resolve session file path. See #1047.",
                    _STARTUP_CWD,
                    live_cwd,
                )
                try:
                    _session_file_path._logged_cwd_drift = True  # type: ignore[attr-defined]
                except Exception:
                    pass
            return None
        sessions_dir = _config_home() / "projects" / _sanitize(_STARTUP_CWD)
        return sessions_dir / f"{session_id}.jsonl"
    except Exception:
        return None


def _session_file_exists(session_id: str) -> bool:
    """Check whether a Claude session file exists on disk for this session_id.

    Used to detect resumed sessions after a process restart, when the in-memory
    LRU cache is empty but history exists on disk.  Always returns False if any
    error occurs so it never prevents a prompt from being processed.
    """
    try:
        path = _session_file_path(session_id)
        return path is not None and path.exists()
    except Exception:
        if backend_session_history_save_errors_total is not None:
            backend_session_history_save_errors_total.labels(**_LABELS).inc()
        return False


def _session_path_self_test() -> None:
    """Startup probe for Claude Agent SDK on-disk layout drift (#530).

    The backend derives session file paths via a hand-rolled port of what the
    Claude CLI *appears* to do: ``<config_home>/projects/<sanitized_cwd>/<session_id>.jsonl``
    with a JS-style 32-bit hash for long paths. If a future SDK/CLI release
    changes that shape (moves to SQLite, adopts an index file, swaps the
    sanitizer, switches to a 64-bit hash, etc.), ``_session_file_path`` will
    resolve to a wrong-but-plausible path. The consequences are silent:

    - ``_session_file_exists`` returns False for every post-restart resume,
      causing one wasted subprocess spawn + retry per cold request.
    - ``_track_session`` and the timeout path unlink the wrong file with
      ``missing_ok=True``, so disk usage grows unbounded.

    This probe runs once at startup. It **never** spawns a ClaudeSDKClient,
    issues a query, or touches the network — it is read-only filesystem +
    internal SDK attribute inspection, so cold-start cost is ~milliseconds and
    no tokens are billed. Any failure is logged loud (WARN/ERROR) and
    ``backend_session_path_mismatch_total`` is incremented with a ``reason`` label
    so operators can alert on drift. A broken probe must never prevent the
    agent from starting — this function swallows every exception.

    Upstream ask: a first-class SDK API (``get_session_file_path(session_id)``
    or ``session_file_exists(session_id)``) would make this probe obsolete
    and remove the hand-rolled hash entirely.
    """
    import pathlib

    def _bump(reason: str) -> None:
        if backend_session_path_mismatch_total is not None:
            try:
                backend_session_path_mismatch_total.labels(**_LABELS, reason=reason).inc()
            except Exception:
                # Metric emission must never crash startup.
                pass

    try:
        # (1) Shape check — the function we rely on must return a pathlib.Path
        # whose layout matches our documented assumption. A probe session id
        # is fine here; we never write the file, we only inspect the name.
        probe_id = "0000probe-self-test-0000"
        resolved = _session_file_path(probe_id)
        if resolved is None:
            logger.error(
                "session-path self-test: _session_file_path returned None for probe id %r — "
                "layout helper is broken; eviction/timeout unlinks will no-op, disk may grow. "
                "See #530.",
                probe_id,
            )
            _bump("resolver_returned_none")
            return

        if not isinstance(resolved, pathlib.Path):
            logger.error(
                "session-path self-test: _session_file_path returned %r (type=%s) — expected pathlib.Path. See #530.",
                resolved,
                type(resolved).__name__,
            )
            _bump("resolver_wrong_type")
            return

        # Expected shape: .../projects/<sanitized_cwd>/<session_id>.jsonl
        parts = resolved.parts
        if resolved.suffix != ".jsonl":
            logger.warning(
                "session-path self-test: resolved path %r does not end in .jsonl — "
                "Claude Agent SDK on-disk layout may have changed (e.g. moved to SQLite). "
                "See #530.",
                str(resolved),
            )
            _bump("suffix_not_jsonl")
        if "projects" not in parts:
            logger.warning(
                "session-path self-test: resolved path %r has no 'projects' segment — "
                "SDK layout may have changed. See #530.",
                str(resolved),
            )
            _bump("missing_projects_segment")
        if resolved.stem != probe_id:
            logger.warning(
                "session-path self-test: resolved stem %r does not match probe id %r — "
                "session-id handling has changed. See #530.",
                resolved.stem,
                probe_id,
            )
            _bump("stem_mismatch")

        # (2) Cross-check against any files the SDK has actually written on
        # disk before this process started. If the sessions directory we
        # derived is missing but *some* projects directory exists with the
        # same config_home, the sanitizer has almost certainly drifted.
        sessions_dir = resolved.parent
        config_projects_dir = sessions_dir.parent
        if config_projects_dir.exists() and config_projects_dir.is_dir():
            if not sessions_dir.exists():
                # Other project subdirs exist but ours does not — strong
                # signal that _sanitize() disagrees with the CLI. Only warn
                # when there are peer entries; an empty projects/ dir on a
                # fresh install is not a mismatch.
                try:
                    peers = [p.name for p in config_projects_dir.iterdir() if p.is_dir()]
                except OSError:
                    peers = []
                if peers:
                    logger.warning(
                        "session-path self-test: expected sessions dir %r does not exist "
                        "but sibling project dirs %r do — sanitizer may have drifted "
                        "from the CLI. Eviction/timeout unlinks will target the wrong "
                        "location. See #530.",
                        str(sessions_dir),
                        peers[:5],
                    )
                    _bump("sanitized_cwd_not_found")

        # (3) Best-effort static check that claude_agent_sdk still ships the
        # symbols we consume. If a future release renames or removes these,
        # _make_options / run_query will fail at call time; this tells us at
        # startup instead so operators have a clean signal.
        try:
            import claude_agent_sdk as _sdk

            for _symbol in ("ClaudeAgentOptions", "ClaudeSDKClient", "HookMatcher"):
                if not hasattr(_sdk, _symbol):
                    logger.error(
                        "session-path self-test: claude_agent_sdk is missing expected "
                        "symbol %r — SDK version incompatible. See #530.",
                        _symbol,
                    )
                    _bump(f"sdk_symbol_missing_{_symbol}")
        except ImportError:
            # The SDK is a hard dependency at the top of this module; if it
            # is somehow missing here, import would have failed earlier. Swallow.
            pass

        logger.info(
            "session-path self-test: ok — resolver layout matches documented conventions (%r).",
            str(resolved),
        )
    except Exception as exc:
        # Broken probe must not take the agent offline.
        logger.warning(
            "session-path self-test raised %r — continuing startup. See #530.",
            exc,
        )
        _bump("probe_exception")


AGENT_NAME = os.environ.get("AGENT_NAME", "claude")
AGENT_OWNER = os.environ.get("AGENT_OWNER") or AGENT_NAME
AGENT_ID = os.environ.get("AGENT_ID") or os.environ.get("HOSTNAME") or "claude"

# Backend→harness generic event channel (#1110 phase 3). Imported lazily
# so the backend still works without PYTHONPATH=shared/ (e.g. in unit
# tests run directly in this directory). All call sites wrap in
# try/except and never let emission failure propagate.
try:
    from hook_events import schedule_event_post as _schedule_event_post  # type: ignore
except Exception:  # pragma: no cover - defensive path
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
    except Exception as exc:  # pragma: no cover - defensive path
        logger.debug("event emit (%s) raised: %r", event_type, exc)


CONVERSATION_LOG = os.environ.get("CONVERSATION_LOG", "/home/agent/logs/conversation.jsonl")
TRACE_LOG = os.environ.get("TRACE_LOG", "/home/agent/logs/tool-activity.jsonl")
MCP_CONFIG_PATH = os.environ.get("MCP_CONFIG_PATH", "/home/agent/.claude/mcp.json")
AGENT_MD = os.environ.get("AGENT_MD_PATH", "/home/agent/.claude/CLAUDE.md")

# Per-chunk timeout on the on_chunk SSE consumer (#1091 — parity with
# codex #724). When the A2A event_queue stalls past this deadline, drop
# the chunk and bump backend_streaming_chunks_dropped_total rather than
# blocking the executor on a back-pressured consumer. The final
# aggregated response still fires at completion so clients see the
# complete output.
STREAM_CHUNK_TIMEOUT_SECONDS = float(os.environ.get("STREAM_CHUNK_TIMEOUT_SECONDS", "5"))

# Backend→harness hook.decision transport (#641).  Empty HARNESS_EVENTS_URL
# disables the POST path cleanly — the in-process OTel span-event emission
# from #633 still fires regardless.  Defaults the bearer token to
# TRIGGERS_AUTH_TOKEN so operators don't need an additional secret.
HARNESS_EVENTS_URL = os.environ.get("HARNESS_EVENTS_URL", "") or ""

# Token source resolution (#933, rotation-aware via #981): prefer the
# dedicated HARNESS_EVENTS_AUTH_TOKEN; fall back to TRIGGERS_AUTH_TOKEN
# only for backward compatibility with deployments that predate #700.
# _resolve_harness_events_auth_token() is called on every POST so
# Kubernetes projected-Secret rotation takes effect in-process without
# a pod restart. The import-time logger.info that previously advertised
# the token source moved into the first successful resolution so the
# log lands *after* main.py configures structured JSON handlers (#980)
# and the emission is rate-limited to one-per-source-change so a
# rotation back to the legacy fallback is still visible.
_HET_SOURCE_LAST: str | None = None
_HET_SOURCE_LOCK = threading.Lock()


def _resolve_harness_events_auth_token() -> tuple[str, str]:
    """Return (token, source_label) for the harness-events bearer.

    Read fresh on every call so rotated Kubernetes Secrets (projected
    into the container as env vars via the kubelet sync loop) take
    effect without a backend restart. Emits a log line the first time
    a given source is observed and on subsequent changes so operators
    can detect silent reversion to the legacy fallback (#980, #981).
    """
    primary = os.environ.get("HARNESS_EVENTS_AUTH_TOKEN", "")
    fallback = os.environ.get("TRIGGERS_AUTH_TOKEN", "")
    if primary:
        token = primary
        source = "HARNESS_EVENTS_AUTH_TOKEN"
    elif fallback:
        token = fallback
        source = "TRIGGERS_AUTH_TOKEN (legacy fallback — set HARNESS_EVENTS_AUTH_TOKEN to silence)"
    else:
        token = ""
        source = "(unset)"

    global _HET_SOURCE_LAST
    with _HET_SOURCE_LOCK:
        if source != _HET_SOURCE_LAST:
            previous = _HET_SOURCE_LAST
            _HET_SOURCE_LAST = source
            emit = True
        else:
            emit = False
            previous = None
    if emit:
        if source.startswith("TRIGGERS_AUTH_TOKEN"):
            logger.warning(
                "hook.decision transport: HARNESS_EVENTS_AUTH_TOKEN is unset; "
                "using the legacy TRIGGERS_AUTH_TOKEN fallback (#933). Rotating "
                "HARNESS_EVENTS_AUTH_TOKEN without clearing TRIGGERS_AUTH_TOKEN "
                "would silently keep the stale bearer — set a dedicated "
                "HARNESS_EVENTS_AUTH_TOKEN in production."
            )
        logger.info(
            "hook.decision transport: bearer source = %s%s",
            source,
            f" (was {previous!r})" if previous is not None else "",
        )
    return token, source


# Tight timeout — the hook is synchronous with tool execution; we never block
# on the POST (fire-and-forget via asyncio.create_task) but still want any
# connection the client opens to fail fast when the harness is unreachable.
HARNESS_EVENTS_TIMEOUT = float(os.environ.get("HARNESS_EVENTS_TIMEOUT", "5.0"))

# Conservative secure-by-default tool set: read-only filesystem + search, no Bash/Write/Edit/WebFetch.
# Resolution order:
#   1. ALLOWED_TOOLS env var (deploy-time override, always wins)
#   2. .claude/settings.json `permissions.allow` (file-based config,
#      matches Claude Code's native schema so shared tooling works)
#   3. Conservative built-in default (Read,Grep,Glob)
_DEFAULT_TOOLS = "Read,Grep,Glob"
_SETTINGS_PATH = os.environ.get("CLAUDE_SETTINGS_PATH", "/home/agent/.claude/settings.json")


def _load_allowed_from_settings(path: str) -> list[str] | None:
    """Return permissions.allow from .claude/settings.json, or None when absent.

    Returns ``None`` when the file is missing or malformed so the caller falls
    back to its next resolution step.  An empty permissions.allow list is
    treated as "no tools" and returned as ``[]`` — absence is distinct from
    explicit emptiness.
    """
    try:
        if not os.path.exists(path):
            return None
        with open(path) as fh:
            data = json.load(fh)
    except Exception as exc:
        logger.warning("Could not parse %s — ignoring permissions.allow: %s", path, exc)
        return None
    perms = (data or {}).get("permissions") or {}
    allow = perms.get("allow")
    if allow is None:
        return None
    if not isinstance(allow, list):
        logger.warning("%s permissions.allow must be a list, got %r — ignoring.", path, type(allow))
        return None
    return [str(t).strip() for t in allow if str(t).strip()]


_ALLOWED_TOOLS_ENV = os.environ.get("ALLOWED_TOOLS")


def _resolve_allowed_tools() -> tuple[list[str], str]:
    """Resolve ALLOWED_TOOLS following env → settings.json → default order.

    Returns a (tools, source) tuple. ``source`` is one of ``"env"``,
    ``"settings"``, or ``"default"`` — used by the hot-reload watcher to
    build an informative log line without re-computing the resolution logic.
    The env var is frozen at import time and always wins, so any
    settings.json edit is silently shadowed until the env var is unset.
    """
    if _ALLOWED_TOOLS_ENV is not None:
        return [t.strip() for t in _ALLOWED_TOOLS_ENV.split(",") if t.strip()], "env"
    allow = _load_allowed_from_settings(_SETTINGS_PATH)
    if allow is not None:
        return allow, "settings"
    return [t.strip() for t in _DEFAULT_TOOLS.split(",") if t.strip()], "default"


_tools_initial, _tools_source = _resolve_allowed_tools()
# ALLOWED_TOOLS is *mutated in place* by ``settings_watcher`` so
# ``_make_options`` continues to read the module-level reference as it
# always has.  The env-var branch yields a frozen result — the watcher
# skips writes in that case to preserve the documented "env always wins"
# contract (#717).
ALLOWED_TOOLS: list[str] = list(_tools_initial)
if _tools_source == "env":
    logger.info("ALLOWED_TOOLS resolved to: %s (from ALLOWED_TOOLS env).", ",".join(ALLOWED_TOOLS))
elif _tools_source == "settings":
    logger.info(
        "ALLOWED_TOOLS resolved to: %s (from %s permissions.allow).",
        ",".join(ALLOWED_TOOLS),
        _SETTINGS_PATH,
    )
else:
    logger.warning(
        "ALLOWED_TOOLS resolved to default (read-only): %s. "
        "Set ALLOWED_TOOLS env var or .claude/settings.json permissions.allow "
        "to override (e.g. to enable Bash/Write/Edit/WebFetch).",
        ",".join(ALLOWED_TOOLS),
    )

CONTEXT_USAGE_WARN_THRESHOLD = float(os.environ.get("CONTEXT_USAGE_WARN_THRESHOLD", "0.8"))
MAX_SESSIONS = int(os.environ.get("MAX_SESSIONS", "10000"))
TASK_TIMEOUT_SECONDS = int(os.environ.get("TASK_TIMEOUT_SECONDS", "300"))
_MAX_PROMPT_BYTES = int(os.environ.get("MAX_PROMPT_BYTES", str(10 * 1024 * 1024)))
# Maximum number of bytes of prompt text included in INFO-level log messages.
# Set to 0 to suppress prompt text from logs entirely; set higher for more context.
LOG_PROMPT_MAX_BYTES = int(os.environ.get("LOG_PROMPT_MAX_BYTES", "200"))

_BACKEND_ID = "claude"
_LABELS = {"agent": AGENT_OWNER, "agent_id": AGENT_ID, "backend": _BACKEND_ID}


# Env var keys that must not be forwarded to MCP stdio subprocesses because
# they influence binary resolution or dynamic-linker / interpreter behavior
# and could be used for privilege escalation or code injection via a malicious
# or poorly-audited ``mcp.json``. Mirrors the denylist codex applies to
# both ``_shell_executor`` (#248) and ``_build_mcp_servers`` (#519); lifted
# here verbatim so the two backends stay in lockstep (#606).
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
    }
)

CLAUDE_MODEL = os.environ.get("CLAUDE_MODEL") or None

# Reasoning-effort level forwarded to the Claude Agent SDK (`effort=` → the
# CLI's `--effort`). When set to one of low/medium/high/max, every request from
# this backend runs at that effort; unset preserves the SDK default. Wired as a
# per-backend env so a single agent (e.g. the Agent-Resources canary) can run at
# `max` without touching the rest of the team. An unrecognized value is ignored
# with a warning rather than failing every request — the pinned SDK
# (claude_agent_sdk 0.1.55) only accepts the four levels below.
_VALID_EFFORT = ("low", "medium", "high", "max")


def _resolve_effort(raw: str | None) -> str | None:
    value = (raw or "").strip().lower()
    if not value:
        return None
    if value not in _VALID_EFFORT:
        logger.warning(
            "CLAUDE_EFFORT=%r is not one of %s; ignoring and using the SDK default effort",
            raw,
            ", ".join(_VALID_EFFORT),
        )
        return None
    return value


CLAUDE_EFFORT = _resolve_effort(os.environ.get("CLAUDE_EFFORT"))


def _current_claude_credential() -> tuple[str | None, str | None]:
    """Read credential + env-var-name live each call (#1351).

    Secret rotation via cert-manager / external-secrets swaps the env
    values in place; capturing them at import time produced a silent
    fallback to the OLD credential indefinitely. Mirror codex's
    ``_current_openai_api_key()`` pattern (#728).
    """
    tok = os.environ.get("CLAUDE_CODE_OAUTH_TOKEN")
    if tok:
        return tok, "CLAUDE_CODE_OAUTH_TOKEN"
    tok = os.environ.get("ANTHROPIC_API_KEY")
    if tok:
        return tok, "ANTHROPIC_API_KEY"
    return None, None


# Legacy module-level snapshot kept for backward compatibility — tests
# and logs may still reference these. They track the state at IMPORT
# time; use ``_current_claude_credential()`` for fresh reads.
CLAUDE_CREDENTIAL, CLAUDE_AUTH_ENV = _current_claude_credential()


def _load_agent_md() -> str:
    try:
        with open(AGENT_MD) as f:
            return f.read()
    except OSError:
        return ""


def _compute_agent_md_revision(content: str) -> str:
    """Return the SHA-256 hex prefix (first 12 chars) of CLAUDE.md content."""
    return hashlib.sha256(content.encode("utf-8", errors="replace")).hexdigest()[:12]


def _utf8_byte_length(s: str) -> int:
    """Return the UTF-8 byte length of *s* without allocating a throwaway bytes object
    for ASCII-only inputs (#558).

    ``str.encode()`` allocates a fresh ``bytes`` object equal in size to the encoded
    form. On multi-megabyte payloads that allocation is pure waste when the caller
    only needs the length. Python's ``str.isascii()`` is an O(n) C-level check; when
    it returns True (the common case for JSON logs and latin text), the UTF-8 byte
    length equals the character length and no allocation is required. For non-ASCII
    strings we fall back to a single ``encode()`` — correctness preserved.
    """
    if s.isascii():
        return len(s)
    return len(s.encode("utf-8"))


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
    """Append one conversation row to ``CONVERSATION_LOG`` for this turn.

    Writes a JSONL row carrying ``ts`` / ``agent`` / ``session_id`` / ``role``
    / ``model`` / ``tokens`` / ``text`` via ``_append_log`` on a thread so the
    request path is never blocked by disk I/O. ``text`` is run through
    ``redact_text`` first when ``should_redact()`` returns true (#714, opt-in
    so existing deployments retain identical output). When OTel is active the
    row is stamped with the current span's ``trace_id`` so conversation rows
    can be joined with backend / harness traces (#636). On success bumps
    ``backend_log_entries_total`` and ``backend_log_bytes_total`` under
    ``logger="conversation"``. Swallows all exceptions after incrementing
    ``backend_log_write_errors_total`` and the by-logger counter so a log
    failure never propagates back into the caller.
    """
    try:
        # Opt-in redaction pass (#714). Guarded on LOG_REDACT so
        # existing deployments retain identical output without the
        # regex cost; operators flip the env var to take the safer
        # posture when conversation.jsonl is read by humans or
        # forwarded to an external log store.
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
            backend_log_bytes_total.labels(**_LABELS, logger="conversation").inc(_utf8_byte_length(_line))
    except Exception as e:
        if backend_log_write_errors_total is not None:
            backend_log_write_errors_total.labels(**_LABELS).inc()
        if backend_log_write_errors_by_logger_total is not None:
            backend_log_write_errors_by_logger_total.labels(**_LABELS, logger="conversation").inc()
        logger.error(f"log_entry error: {e}")


async def log_trace(text: str) -> None:
    """Append a raw line to ``TRACE_LOG`` for this turn.

    Thin wrapper around ``_append_log`` (run on a thread) plus metric
    bookkeeping under ``logger="trace"`` — ``backend_log_entries_total`` and
    ``backend_log_bytes_total`` on success, ``backend_log_write_errors_total``
    and the by-logger counter on failure. Like ``log_entry`` it swallows
    write exceptions so the trace-emitting code paths can call it
    unconditionally. ``text`` is written verbatim — callers are responsible
    for whatever JSON encoding / framing the trace consumers expect.
    """
    try:
        await asyncio.to_thread(_append_log, TRACE_LOG, text)
        if backend_log_entries_total is not None:
            backend_log_entries_total.labels(**_LABELS, logger="trace").inc()
        if backend_log_bytes_total is not None:
            backend_log_bytes_total.labels(**_LABELS, logger="trace").inc(_utf8_byte_length(text))
    except Exception as e:
        if backend_log_write_errors_total is not None:
            backend_log_write_errors_total.labels(**_LABELS).inc()
        if backend_log_write_errors_by_logger_total is not None:
            backend_log_write_errors_by_logger_total.labels(**_LABELS, logger="trace").inc()
        logger.error(f"log_trace error: {e}")


async def log_tool_audit(entry: dict) -> None:
    """Append one audit row via the shared helper (#858).

    Delegates to ``shared/tool_audit.py::log_tool_audit`` so claude and
    codex converge on one async implementation with identical row shape,
    metric bookkeeping, and error handling. Previously this wrote to a
    separate ``tool-audit.jsonl`` file; the two feeds were consolidated
    so one endpoint (``/trace``) carries both SDK-level tool events and
    hook-level audit rows. Readers filter by ``event_type``.
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


# Per-loop httpx.AsyncClient registry (#716). Keyed by id(loop) so the
# thread-mode metrics server (running a second loop) or any future
# caller on a non-main loop does not hang on a module-level Lock that
# was bound to the first loop. Each loop gets its own lock + client
# pair; clients from dead loops are pruned on next access.
_HOOK_HTTP_CLIENTS: dict[int, tuple["httpx.AsyncClient", asyncio.Lock]] = {}  # noqa: F821
# Guard first-touch of _HOOK_HTTP_CLIENTS with a threading.Lock (#868).
# Two concurrent first-touches each created their own asyncio.Lock
# and entered separate critical sections, so two AsyncClient instances
# could be built — one got overwritten in the dict and was never
# closed, leaking httpx sockets. A threading.Lock works across all
# loops (the only data we serialise is the dict insert) and keeps the
# hot path lock-free once the loop entry exists.
_HOOK_HTTP_CLIENTS_LOCK = threading.Lock()


async def _get_hook_http_client() -> "httpx.AsyncClient":  # noqa: F821
    loop = asyncio.get_running_loop()
    key = id(loop)
    # #1361: on hot paths, also prune entries whose loop is closed
    # (a secondary loop that exited without calling
    # _close_hook_http_client leaks the AsyncClient + pool).
    # This check is O(n_loops); ~1-2 loops typical in this process.
    _dead_keys: list[int] = []
    for _k in list(_HOOK_HTTP_CLIENTS.keys()):
        if _k == key:
            continue
        _entry = _HOOK_HTTP_CLIENTS.get(_k)
        # We can't recover the loop object from id() alone; detect dead
        # entries by checking the AsyncClient's transport internal
        # `is_closed` flag as a best-effort signal. httpx AsyncClient
        # exposes `is_closed` in recent versions.
        try:
            if _entry is not None and getattr(_entry[0], "is_closed", False):
                _dead_keys.append(_k)
        except Exception:
            pass
    for _dk in _dead_keys:
        _HOOK_HTTP_CLIENTS.pop(_dk, None)
    entry = _HOOK_HTTP_CLIENTS.get(key)
    if entry is None:
        # New loop — construct inside the running loop so the Lock is
        # created against the right asyncio context. The
        # threading.Lock guarantees that two concurrent first-touches
        # cannot each create a separate client (#868).
        with _HOOK_HTTP_CLIENTS_LOCK:
            entry = _HOOK_HTTP_CLIENTS.get(key)
            if entry is None:
                import httpx

                client = httpx.AsyncClient(timeout=HARNESS_EVENTS_TIMEOUT)
                entry = (client, asyncio.Lock())
                _HOOK_HTTP_CLIENTS[key] = entry
    return entry[0]


async def _close_hook_http_client() -> None:
    """Close the hook httpx client for the current loop (#716).

    Per-loop registry now, so close-on-shutdown only drops this loop's
    entry. Other loops (e.g. a thread-mode metrics server) manage
    their own lifecycles.
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    entry = _HOOK_HTTP_CLIENTS.pop(id(loop), None)
    if entry is not None:
        client, _lock = entry
        try:
            await client.aclose()
        except Exception as exc:  # pragma: no cover — best-effort shutdown
            logger.debug("hook httpx client close failed: %r", exc)


# One-shot guard for the "HARNESS_EVENTS_AUTH_TOKEN missing" warning
# emitted from _post_hook_event_to_harness (#666). Keeps the misconfig
# visible at default log levels without spamming once per tool call.
_HARNESS_EVENTS_AUTH_TOKEN_WARNED = False
# #1489: guard the check-then-set so the "token missing" warning
# emits at most once under concurrent _post_hook_event_to_harness
# coroutines. Without this, two concurrent calls can both read False
# and both emit the warning.
_HARNESS_EVENTS_AUTH_TOKEN_WARNED_LOCK = threading.Lock()


# Module-level strong-ref set for fire-and-forget hook.decision POST tasks.
# asyncio.create_task only keeps a weak reference to the task from the event
# loop, so without a strong reference the task may be garbage-collected before
# it completes (Python asyncio docs). Entries are discarded via
# add_done_callback so the set does not grow unboundedly (#660).
_INFLIGHT_HOOK_POSTS: set[asyncio.Task] = set()
# Serialises the cap check-and-add across event loops (#931). Under the
# standard single-loop topology this is redundant with the asyncio
# scheduler's implicit ordering, but the set is module-global — a
# secondary loop touching it would double-count without a lock. Also
# pairs cleanly with the shed-warning flag's _HOOK_POST_SHED_WARNED_LOCK.
_INFLIGHT_HOOK_POSTS_LOCK = threading.Lock()

# Bounded cap on concurrent fire-and-forget hook.decision POSTs (#712).
# When the harness is unreachable and tool calls fire rapidly, without
# a cap the set would grow without bound along with httpx connections,
# causing memory growth, pool exhaustion, and eventually OOM.
_HOOK_POST_MAX_INFLIGHT_DEFAULT = 64
try:
    _HOOK_POST_MAX_INFLIGHT = int(os.environ.get("HOOK_POST_MAX_INFLIGHT", "32"))
except (TypeError, ValueError):
    logger.warning(
        "HOOK_POST_MAX_INFLIGHT could not be parsed as int; forcing safe default %d (#1196).",
        _HOOK_POST_MAX_INFLIGHT_DEFAULT,
    )
    _HOOK_POST_MAX_INFLIGHT = _HOOK_POST_MAX_INFLIGHT_DEFAULT
if _HOOK_POST_MAX_INFLIGHT <= 0:
    # A cap <= 0 means every post is shed, so tool-use hook decisions are
    # never sent to the harness — an infinite shed loop with no forward
    # progress (#1196). Force a safe default and log loud so operators
    # see the misconfiguration on startup.
    logger.warning(
        "HOOK_POST_MAX_INFLIGHT=%d is non-positive; forcing safe default %d "
        "to avoid infinite hook-post shedding (#1196).",
        _HOOK_POST_MAX_INFLIGHT,
        _HOOK_POST_MAX_INFLIGHT_DEFAULT,
    )
    _HOOK_POST_MAX_INFLIGHT = _HOOK_POST_MAX_INFLIGHT_DEFAULT
# One-shot warning guard for the "shed because at cap" log so bursts
# don't spam the log per tool call. Both the set/re-arm paths write
# this global from different callbacks — the done callback fires on
# every task completion. Guard both writes with a threading.Lock so
# concurrent completions can't race the re-arm (#873) and duplicate
# the warning on a subsequent sustained-shed event.
_HOOK_POST_SHED_WARNED = False
_HOOK_POST_SHED_WARNED_LOCK = threading.Lock()


async def _post_hook_event_to_harness(event_dict: dict) -> None:
    """Fire-and-forget POST of a hook.decision event to the harness (#641).

    Transport for the backend→harness side-channel defined in #633: the
    harness exposes ``POST /internal/events/hook-decision`` and fans matching
    events out to subscribed webhooks via
    :func:`WebhookRunner.fire_hook_decision`.  Scheduled by the hook callbacks
    via ``asyncio.create_task`` so a slow or unreachable harness never stalls
    tool execution.  All exceptions are swallowed (warn-level log) because
    observability must never break the hook pipeline.
    """
    if not HARNESS_EVENTS_URL:
        return
    # Re-resolve the token per call so rotated secrets take effect
    # in-process without a pod restart (#981). Resolution also logs
    # source changes so a silent reversion to the legacy fallback is
    # surfaced at default log levels.
    token, _ = _resolve_harness_events_auth_token()
    if not token:
        # Surface the misconfiguration exactly once at WARNING: the harness
        # endpoint will reject every POST, so hook events are silently
        # dropped. Operators who set HARNESS_EVENTS_URL without the token
        # need this visible at default log levels (#666). Subsequent calls
        # still early-return but stay quiet to avoid log spam per tool use.
        global _HARNESS_EVENTS_AUTH_TOKEN_WARNED
        # #1489: check-then-set under lock so concurrent callers can't
        # both pass the guard and emit the warning twice.
        _should_emit = False
        with _HARNESS_EVENTS_AUTH_TOKEN_WARNED_LOCK:
            if not _HARNESS_EVENTS_AUTH_TOKEN_WARNED:
                _HARNESS_EVENTS_AUTH_TOKEN_WARNED = True
                _should_emit = True
        if _should_emit:
            logger.warning(
                "hook.decision transport DISABLED: HARNESS_EVENTS_URL is set "
                "but HARNESS_EVENTS_AUTH_TOKEN is empty. Set the token so the "
                "harness endpoint accepts the POST."
            )
        return
    url = HARNESS_EVENTS_URL.rstrip("/") + "/internal/events/hook-decision"
    try:
        client = await _get_hook_http_client()
        await client.post(
            url,
            json=event_dict,
            headers={"Authorization": f"Bearer {token}"},
        )
    except Exception as exc:
        # Transport failures are expected during harness restarts or network
        # blips; the span event from #633 still captured the decision.
        logger.warning("hook.decision POST to %s failed: %r", url, exc)


def _make_pre_tool_use_hook(
    state: HookState, session_id_ref: dict | None = None, state_lock: "threading.Lock | None" = None
):
    """Build the PreToolUse hook callable bound to *state*.

    The callable is closed over *state* so it always sees the latest rule
    set, even after ``hooks.yaml`` hot-reload mutates ``state.extensions``.
    ``session_id_ref`` mirrors the PostToolUse pattern so the hook event
    payload carries the backend-derived (HMAC-bound) session_id rather than
    the SDK-internal id (#871). ``state_lock`` serialises the
    ``active_rules()`` snapshot against the watcher's extension swap (#1488).
    """

    async def _hook(input_data: dict, tool_use_id: str | None, context) -> dict:
        tool_name = input_data.get("tool_name", "")
        tool_input = input_data.get("tool_input") or {}
        # hook.invoke child span (#630). Wraps the rule-evaluation + logging
        # path so PreToolUse invocations show up as a dedicated span instead of
        # being implicit under the ambient llm.request / tool.call span.
        with start_span(
            "hook.invoke",
            kind="internal",
            attributes={
                "hook.name": "PreToolUse",
                "hook.phase": "pre",
                "tool.name": tool_name or "",
            },
        ):
            # #1488: snapshot rules under the hook-state lock (if provided)
            # so a concurrent hot-reload can't interleave the baseline +
            # extensions read with the writer's extension swap.
            if state_lock is not None:
                with state_lock:
                    rules = state.active_rules()
            else:
                rules = state.active_rules()
            decision, matched = evaluate_pre_tool_use(tool_name, tool_input, rules)

        # Bump the evaluations-total denominator once per call so operators
        # can compute deny / warn / allow rates (#620). Done before the
        # branch returns so all three paths contribute.
        if backend_hooks_evaluations_total is not None:
            backend_hooks_evaluations_total.labels(
                **_LABELS,
                tool=tool_name or "unknown",
                decision=decision,
            ).inc()

        # Emit a span event on the current OTel span for allow/warn/deny so
        # hook decisions surface in trace UIs alongside the tool invocation
        # that triggered them (#633). No-op when OTel is disabled or no
        # current span is active. Wrapped in a bare try/except because
        # observability must never break hook evaluation.
        _traceparent: str | None = None
        try:
            from opentelemetry import trace as _otel_trace

            _span = _otel_trace.get_current_span()
            if _span is not None and _span.is_recording():
                _attrs = {
                    "decision": decision,
                    "rule": (matched.name if matched is not None else ""),
                    "source": (matched.source if matched is not None else ""),
                    "tool": tool_name or "",
                    "reason": (matched.reason if matched is not None else ""),
                }
                _span.add_event("hook.decision", _attrs)
                if decision == DECISION_DENY:
                    _span.set_attribute("hook.denied", True)
                # Capture the current trace-context as a W3C traceparent header
                # so the harness can forward it on to webhook receivers.
                try:
                    _ctx = _span.get_span_context()
                    if getattr(_ctx, "is_valid", False) or (
                        getattr(_ctx, "trace_id", 0) and getattr(_ctx, "span_id", 0)
                    ):
                        _traceparent = f"00-{_ctx.trace_id:032x}-{_ctx.span_id:016x}-{int(_ctx.trace_flags) & 0xFF:02x}"
                except Exception:
                    _traceparent = None
        except Exception:
            pass

        # Schedule the backend→harness transport (#641) fire-and-forget so
        # that a slow or unreachable harness never stalls tool execution.
        # Wrapped in try/except because the hook must never fail on the
        # transport; the span event above is still the authoritative record.
        try:
            # Prefer the backend-derived session_id (HMAC-bound via
            # session_binding) so downstream subscribers correlate to
            # persisted state; fall back to the SDK-supplied value only
            # if no closure ref was passed (#871). An empty-string
            # ref['value'] is treated as "ref present but not yet
            # populated" — still respected rather than transparently
            # falling back to input_data.session_id — so a future
            # refactor that zeroes the ref does not silently revert
            # hook payloads to the SDK-internal id (#984).
            if session_id_ref is not None and "value" in session_id_ref:
                _resolved_sid = session_id_ref.get("value") or ""
            else:
                _resolved_sid = input_data.get("session_id") or ""
            _event_dict = {
                # Per the harness's HookDecisionEvent contract
                # (harness/bus.py:_emit_hook_decision_event_stream
                # docstring), `agent` here is the BACKEND id
                # (claude/codex/gemini), not the named witwave agent
                # (iris/kira/nova). The named-agent goes on the SSE
                # envelope's `agent_id` instead, derived from
                # AGENT_OWNER inside the harness. Sending
                # AGENT_OWNER here trips the "unknown agent" drop
                # branch (#1149) and silences a real policy signal.
                "agent": _BACKEND_ID,
                "session_id": _resolved_sid,
                "tool": tool_name or "",
                "decision": decision,
                "rule_name": (matched.name if matched is not None else ""),
                "reason": (matched.reason if matched is not None else ""),
                "source": (matched.source if matched is not None else ""),
                "traceparent": _traceparent,
            }
            # Bounded-inflight shed (#712). Without this, a burst of
            # tool calls while the harness is unreachable would spawn
            # unbounded POST tasks, exhausting the httpx pool and
            # driving the backend to OOM. When at cap we drop the
            # event and bump a counter — the span event recorded
            # above remains the authoritative record.
            # Check-only under _INFLIGHT_HOOK_POSTS_LOCK (#931 retained
            # under secondary-loop topologies). asyncio.create_task is
            # invoked *outside* the lock so a loop-shutdown RuntimeError
            # (or any other scheduler exception) doesn't surface as an
            # AssertionError downstream — and so the blocking
            # threading.Lock never wraps an asyncio-scheduler call
            # (#983). If create_task raises or returns, we defensively
            # reconcile the in-flight set afterwards.
            # #1487: hold _HOOK_POST_SHED_WARNED_LOCK across the cap-check
            # + warning emit + flag-set so a concurrent _done reset (which
            # also grabs _HOOK_POST_SHED_WARNED_LOCK) cannot interleave
            # between the cap-check and the warning emit and cause the
            # per-drain-cycle warning to re-fire more than once. We take
            # SHED_WARNED first, then read INFLIGHT nested under its own
            # lock — safe because every other call site takes the two
            # locks sequentially (not nested), so no deadlock arises.
            global _HOOK_POST_SHED_WARNED
            with _HOOK_POST_SHED_WARNED_LOCK:
                with _INFLIGHT_HOOK_POSTS_LOCK:
                    _size_before = len(_INFLIGHT_HOOK_POSTS)
                    _over_cap = _size_before >= _HOOK_POST_MAX_INFLIGHT
                if _over_cap and not _HOOK_POST_SHED_WARNED:
                    logger.warning(
                        "hook.decision POST shed: %d in-flight at cap=%d (further shed suppressed until drain)",
                        _size_before,
                        _HOOK_POST_MAX_INFLIGHT,
                    )
                    _HOOK_POST_SHED_WARNED = True
            if _over_cap:
                if backend_hooks_shed_total is not None:
                    try:
                        backend_hooks_shed_total.labels(**_LABELS).inc()
                    except Exception:
                        pass
            else:
                _t_created: asyncio.Task | None = None
                try:
                    _t_created = asyncio.create_task(_post_hook_event_to_harness(_event_dict))
                except RuntimeError as _rt_exc:
                    # Event loop closing / no running loop. Not fatal —
                    # the span event above already captured the decision.
                    # #1492: bump the shed counter here too so a burst of
                    # tool calls during shutdown (when create_task raises
                    # with "loop is closed") isn't silently dropped from
                    # the shed-rate dashboard. Reuse the same metric as
                    # the cap-based shed path — operators can't
                    # distinguish the two reasons but the counter stays
                    # complete.
                    if backend_hooks_shed_total is not None:
                        try:
                            backend_hooks_shed_total.labels(**_LABELS).inc()
                        except Exception:
                            pass
                    logger.debug(
                        "hook.decision transport scheduling skipped (loop not running): %r",
                        _rt_exc,
                    )
                    _t_created = None
                if _t_created is not None:
                    with _INFLIGHT_HOOK_POSTS_LOCK:
                        _INFLIGHT_HOOK_POSTS.add(_t_created)

                    def _done(tt, _reset=_INFLIGHT_HOOK_POSTS):
                        with _INFLIGHT_HOOK_POSTS_LOCK:
                            _reset.discard(tt)
                            _size = len(_reset)
                        # Re-arm the warning once we drop back below cap.
                        # Serialise with the same Lock so two concurrent
                        # completions can't both flip the flag after one
                        # task already did (#873).
                        if _size < _HOOK_POST_MAX_INFLIGHT // 2:
                            global _HOOK_POST_SHED_WARNED
                            with _HOOK_POST_SHED_WARNED_LOCK:
                                _HOOK_POST_SHED_WARNED = False

                    _t_created.add_done_callback(_done)
        except Exception as _transport_exc:
            logger.debug("hook.decision transport scheduling failed: %r", _transport_exc)

        if decision == DECISION_DENY and matched is not None:
            if backend_hooks_blocked_total is not None:
                backend_hooks_blocked_total.labels(
                    **_LABELS,
                    tool=tool_name or "unknown",
                    source=matched.source,
                    rule=matched.name,
                ).inc()
            # Canonical cross-backend name (#789). Increment alongside
            # the legacy series so dashboards can migrate incrementally.
            if backend_hooks_denials_total is not None:
                backend_hooks_denials_total.labels(
                    **_LABELS,
                    tool=tool_name or "unknown",
                    source=matched.source,
                    rule=matched.name,
                ).inc()
            logger.warning(
                "PreToolUse DENY: tool=%r rule=%r source=%r reason=%r",
                tool_name,
                matched.name,
                matched.source,
                matched.reason,
            )
            return {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": matched.reason,
                }
            }

        if decision == DECISION_WARN and matched is not None:
            if backend_hooks_warnings_total is not None:
                backend_hooks_warnings_total.labels(
                    **_LABELS,
                    tool=tool_name or "unknown",
                    source=matched.source,
                    rule=matched.name,
                ).inc()
            logger.info(
                "PreToolUse WARN: tool=%r rule=%r source=%r reason=%r",
                tool_name,
                matched.name,
                matched.source,
                matched.reason,
            )

        return {}

    return _hook


def _make_post_tool_use_hook(session_id_ref: dict, model_ref: dict):
    """Build the PostToolUse hook callable.

    Always emits one audit entry per tool call. The ``*_ref`` dicts are
    single-slot mutable containers (``{"value": ...}``) so the executor's
    outer call site can stamp the current session/model into each audit row
    without rebuilding ClaudeAgentOptions per call.
    """

    async def _hook(input_data: dict, tool_use_id: str | None, context) -> dict:
        # hook.invoke child span (#630) — PostToolUse audit write.
        with start_span(
            "hook.invoke",
            kind="internal",
            attributes={
                "hook.name": "PostToolUse",
                "hook.phase": "post",
                "tool.name": input_data.get("tool_name") or "",
            },
        ):
            try:
                # ``tool_response`` can be large — capture only a preview so the
                # audit log stays compact. Operators who need the full payload
                # can read tool-activity.jsonl.
                _resp = input_data.get("tool_response")
                if isinstance(_resp, (dict, list)):
                    _resp_preview = json.dumps(_resp, default=str)[:2048]
                else:
                    _resp_preview = str(_resp)[:2048] if _resp is not None else ""
                entry = {
                    "ts": datetime.now(timezone.utc).isoformat(),
                    "agent": AGENT_NAME,
                    "agent_id": AGENT_ID,
                    "session_id": session_id_ref.get("value") or input_data.get("session_id") or "",
                    "model": model_ref.get("value") or "",
                    "tool_use_id": tool_use_id or "",
                    "tool_name": input_data.get("tool_name") or "",
                    "tool_input": input_data.get("tool_input") or {},
                    "tool_response_preview": _resp_preview,
                }
                await log_tool_audit(entry)
            except Exception as exc:
                logger.error("PostToolUse audit error: %r", exc)
            return {}

    return _hook


async def _log_tool_event(event_type: str, block, session_id: str, model: str | None = None) -> None:
    try:
        ts = datetime.now(timezone.utc).isoformat()
        if event_type == "tool_use":
            entry = {
                "ts": ts,
                "agent": AGENT_NAME,
                "agent_id": AGENT_ID,
                "session_id": session_id,
                "event_type": event_type,
                "model": model,
                "id": block.id,
                "name": block.name,
                "input": block.input,
            }
        else:
            entry = {
                "ts": ts,
                "agent": AGENT_NAME,
                "agent_id": AGENT_ID,
                "session_id": session_id,
                "event_type": event_type,
                "model": model,
                "tool_use_id": block.tool_use_id,
                "content": block.content,
                "is_error": block.is_error,
            }
        await log_trace(json.dumps(entry))
    except Exception as e:
        logger.error(f"_log_tool_event error: {e}")


# MCP stdio command allow-list (#711). ``mcp.json`` is hot-reloaded and the
# Claude Agent SDK spawns whatever ``command`` an entry lists, so an attacker
# with commit access — or a mis-merge — that lands ``command: "/bin/sh"``
# achieves arbitrary code execution inside the backend container. The
# allow-list caps that blast radius to a small, operator-curated set.
#
# Configuration:
#   MCP_ALLOWED_COMMANDS  Comma-separated list. Each entry is either an
#                         absolute path (``/usr/local/bin/mcp-kubernetes``)
#                         or a bare basename (``mcp-kubernetes``). Empty
#                         defaults to a conservative read-only set that
#                         covers the MCP tools shipped in this repo.
#   MCP_ALLOWED_COMMAND_PREFIXES  Comma-separated absolute-path prefixes.
#                         Commands resolving to a real path that begins
#                         with one of these prefixes are accepted even if
#                         the basename isn't explicitly in the allow-list.
#                         Default "/home/agent/mcp-bin/,/usr/local/bin/".
# MCP command allow-list logic now lives in shared/mcp_command_allowlist.py
# so codex + gemini can import the same rule (#711 / #797). Keep the
# private alias so intra-file callers (``_sanitize_mcp_servers`` below)
# don't need to touch their import paths.
from mcp_command_allowlist import (  # noqa: E402
    mcp_command_allowed as _mcp_command_allowed,
)
from mcp_command_allowlist import (  # noqa: E402
    mcp_command_args_safe as _mcp_command_args_safe,
)


def _sanitize_mcp_servers(servers: dict) -> dict:
    """Strip loader/interpreter-hijack env vars and enforce the command
    allow-list for every stdio MCP server entry (#606, #711).

    The Claude Agent SDK forwards each server entry's ``env`` dict verbatim to
    the subprocess it spawns for stdio transport, so a malicious or poorly-
    audited ``mcp.json`` could drop e.g. ``LD_PRELOAD`` / ``PYTHONPATH`` into
    the MCP server process and achieve code execution — identical threat
    model to codex #519. Filter each entry's ``env`` through
    ``_SHELL_ENV_DENYLIST`` and log any rejected keys at WARNING so
    operators notice misconfigurations. Non-dict entries, entries without
    an ``env`` key, and non-stdio transports are passed through untouched.

    The command allow-list (#711) rejects entries whose ``command`` falls
    outside ``MCP_ALLOWED_COMMANDS`` + ``MCP_ALLOWED_COMMAND_PREFIXES``
    — the rejected entry is dropped from the returned dict entirely
    (rather than silently mutating ``command`` to something safe) so a
    mis-merged mcp.json can't achieve partial code execution.
    """
    if not isinstance(servers, dict):
        return servers
    out: dict = {}
    for name, cfg in servers.items():
        if not isinstance(cfg, dict):
            out[name] = cfg
            continue
        # Apply env scrub first so the logged "rejected" set is meaningful
        # regardless of whether the command is later rejected.
        # Build a shallow copy of cfg rather than mutating the caller's
        # dict (#872). Previously the sanitised env was written back
        # into the input cfg, so any caller reusing its input
        # observed a silently scrubbed env. Returning a new dict is
        # the expected pure-function shape.
        new_cfg = dict(cfg)
        raw_env = cfg.get("env")
        if isinstance(raw_env, dict):
            sanitized_env = {k: v for k, v in raw_env.items() if k not in _SHELL_ENV_DENYLIST}
            rejected = set(raw_env) - set(sanitized_env)
            if rejected:
                logger.warning(
                    "MCP server %r: stripped dangerous env vars from config env: %s",
                    name,
                    sorted(rejected),
                )
            new_cfg["env"] = sanitized_env
        # Only validate the command field when the entry is an stdio
        # transport (presence of ``command``). HTTP/SSE transports carry
        # ``url`` instead and are out of scope for the allow-list.
        cmd = new_cfg.get("command")
        if cmd is not None:
            ok, reason = _mcp_command_allowed(cmd)
            if not ok:
                logger.warning(
                    "MCP server %r: command %r rejected by allow-list (%s) — "
                    "dropping entry. Set MCP_ALLOWED_COMMANDS / "
                    "MCP_ALLOWED_COMMAND_PREFIXES to widen. (#711)",
                    name,
                    cmd,
                    reason,
                )
                if backend_mcp_command_rejected_total is not None:
                    try:
                        backend_mcp_command_rejected_total.labels(
                            **_LABELS,
                            reason=reason,
                        ).inc()
                    except Exception:
                        pass
                continue
            # Args sanitiser (#1734 / #930). When ``command`` is an
            # interpreter (uv, uvx, python, node, …), its ``args`` array
            # can still deliver arbitrary code via -c / -e / positional
            # script paths. Drop the entry if args fail the check so a
            # widened MCP_ALLOWED_COMMANDS doesn't silently re-open the
            # arbitrary-code-execution path the README promises is
            # closed.
            args_val = new_cfg.get("args")
            args_ok, args_reason = _mcp_command_args_safe(cmd, args_val)
            if not args_ok:
                logger.warning(
                    "MCP server %r: args for command %r rejected by "
                    "args sanitiser (%s) — dropping entry. Adjust the "
                    "config or set MCP_ALLOWED_CWD_PREFIXES if a "
                    "positional script lives in an operator-vetted "
                    "tree. (#1734)",
                    name,
                    cmd,
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
        out[name] = new_cfg
    return out


# Structural validation extracted to mcp_shape.py so it can be
# unit-tested without importing the full executor surface (#1051).
from mcp_shape import validate_mcp_servers_shape as _validate_mcp_servers_shape  # noqa: E402


def _load_mcp_config() -> dict:
    """Load, sanitize, and validate mcp.json.

    Returns ``{}`` when the file is absent (legitimate "no MCP servers"
    state). Raises on parse/validation errors so hot-reload callers can
    distinguish "successfully empty" from "unparseable" and keep the
    previous server set in the latter case (#591).

    Structural validation (#1051) happens after the command allow-list
    and env denylist pass in :func:`_sanitize_mcp_servers`. Entries that
    don't match the SDK's minimum shape (``command`` OR ``url``, typed
    correctly) are dropped and logged at WARNING.
    """
    if not os.path.exists(MCP_CONFIG_PATH):
        return {}
    try:
        with open(MCP_CONFIG_PATH) as f:
            data = json.load(f)
        # mcp.json may be in Claude's native format {"mcpServers": {...}}.
        # The SDK expects the inner servers dict directly, not the wrapper object.
        if not isinstance(data, dict):
            raise ValueError(f"mcp.json must be a JSON object (got {type(data).__name__})")
        if "mcpServers" in data:
            # Wrapper shape: the inner value must be a dict. Previously a
            # malformed value (e.g. a list) fell through to the else-branch
            # and silently sanitised the wrapper object itself, masking the
            # config error (#1200). Raise explicitly so the caller's
            # error-counter path fires and the operator sees the malformed
            # config at WARNING.
            if not isinstance(data["mcpServers"], dict):
                raise ValueError("mcp.json: mcpServers must be a dict")
            sanitized = _sanitize_mcp_servers(data["mcpServers"])
        else:
            sanitized = _sanitize_mcp_servers(data)
        valid, rejected = _validate_mcp_servers_shape(sanitized)
        for name, reason in rejected:
            logger.warning(
                "MCP server %r: rejected by shape validation (%s) — "
                "dropping entry so executor view matches SDK runtime. (#1051)",
                name,
                reason,
            )
        return valid
    except Exception as e:
        if backend_mcp_config_errors_total is not None:
            backend_mcp_config_errors_total.labels(**_LABELS).inc()
        logger.warning(f"Failed to load MCP config from {MCP_CONFIG_PATH}: {e}")
        raise


_sessions_lock: asyncio.Lock | None = None


def _get_sessions_lock() -> asyncio.Lock:
    """Return the process-wide ``_sessions_lock``, creating it lazily (#1195).

    Parity with the openai backend (``backends/openai/executor.py``): any
    evict/unlink/insert mutation of the shared sessions OrderedDict must
    serialise through this lock so concurrent A2A and /mcp paths cannot
    interleave ``popitem(last=False)`` with post-await reinserts.
    """
    global _sessions_lock
    if _sessions_lock is None:
        _sessions_lock = asyncio.Lock()
    return _sessions_lock


async def _track_session(sessions: OrderedDict[str, float], session_id: str) -> None:
    # Serialise evict/unlink/insert on the shared OrderedDict (#1195) so
    # concurrent callers (A2A execute() and the /mcp tools/call path both
    # share AgentExecutor._sessions) cannot interleave popitem(last=False)
    # with the post-await reinsertion. Mirrors codex's #506/#725 pattern.
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
                # Remove the on-disk session file for the evicted session so that
                # disk space is reclaimed and a future request for the same session
                # ID starts with a clean slate rather than stale history (#368).
                # Run the unlink in a thread pool so the event loop is not blocked
                # on slow/remote filesystems (#426).
                _evicted_path = _session_file_path(_evicted_id)
                if _evicted_path is not None:
                    try:
                        await asyncio.to_thread(_evicted_path.unlink, missing_ok=True)
                    except OSError as _e:
                        logger.warning("Failed to remove evicted session file %r: %s", _evicted_path, _e)
            sessions[session_id] = time.monotonic()
        if backend_active_sessions is not None:
            backend_active_sessions.labels(**_LABELS).set(len(sessions))
        if backend_lru_cache_utilization_percent is not None:
            backend_lru_cache_utilization_percent.labels(**_LABELS).set(len(sessions) / MAX_SESSIONS * 100)


@dataclass(frozen=True)
class RunContext:
    """Per-request pass-through parameters for ``run`` / ``run_query`` / ``_run_inner`` (#551).

    Groups the fields that flow through all three layers unchanged. Per-layer
    parameters that interact with streaming/budget/hooks (``max_tokens``,
    ``on_chunk``, ``hook_state``) and layer-specific state (``sessions``,
    ``is_new``, ``prompt``) are intentionally *not* captured here — they stay
    as explicit arguments because they are either modified, created, or
    consumed differently at each layer.

    Frozen so a caller cannot accidentally mutate the context mid-run and
    cause different layers to see different values.
    """

    session_id: str
    model: str | None
    mcp_servers: dict
    agent_md_content: str


def _make_options(
    session_id: str,
    resume: bool,
    stderr_fn,
    mcp_servers: dict,
    model: str | None = None,
    agent_md_content: str = "",
    hook_state: HookState | None = None,
) -> ClaudeAgentOptions:
    env: dict | None = None
    # #1351: re-read per call so Secret rotation reaches the SDK.
    _cred, _cred_env = _current_claude_credential()
    if _cred and _cred_env:
        env = {_cred_env: _cred}

    system_prompt = f"Your name is {AGENT_NAME}. Your session ID is {session_id}."
    if agent_md_content:
        system_prompt = f"{agent_md_content}\n\nYour session ID is {session_id}."

    # Build PreToolUse/PostToolUse matchers if a hook state was supplied.
    # PostToolUse is always wired — it only writes an audit row, never
    # blocks — so operators cannot accidentally turn off the forensic
    # trail. PreToolUse is only wired when at least one rule is active,
    # to avoid the per-call SDK round-trip when the baseline has been
    # disabled and no extensions have loaded (#467).
    hooks_cfg: dict | None = None
    if hook_state is not None:
        _active = hook_state.active_rules()
        _session_ref = {"value": session_id}
        _model_ref = {"value": model or CLAUDE_MODEL or ""}
        hooks_cfg = {
            "PostToolUse": [
                HookMatcher(matcher="*", hooks=[_make_post_tool_use_hook(_session_ref, _model_ref)]),
            ],
        }
        if _active:
            # #1488: retrieve the sidecar lock attached by the executor's
            # __init__ so the hook can snapshot active_rules() under it.
            _state_lock = getattr(hook_state, "_state_lock", None)
            hooks_cfg["PreToolUse"] = [
                HookMatcher(
                    matcher="*",
                    hooks=[_make_pre_tool_use_hook(hook_state, _session_ref, state_lock=_state_lock)],
                ),
            ]

    return ClaudeAgentOptions(
        allowed_tools=ALLOWED_TOOLS,
        system_prompt=system_prompt,
        resume=session_id if resume else None,
        session_id=session_id if not resume else None,
        stderr=stderr_fn,
        mcp_servers=mcp_servers,
        model=model or CLAUDE_MODEL,
        **({"effort": CLAUDE_EFFORT} if CLAUDE_EFFORT else {}),
        **({"hooks": hooks_cfg} if hooks_cfg else {}),
        **({"env": env} if env else {}),
    )


async def _run_query_inner(
    prompt: str,
    options: ClaudeAgentOptions,
    session_id: str,
    effective_model: str | None = None,
    max_tokens: int | None = None,
    on_chunk: Callable[[str], Awaitable[None]] | None = None,
    tool_use_flag: "list[bool] | None" = None,
) -> list[str]:
    # Sanitize the `model` label once at the construction site so every
    # downstream `.labels(**_sdk_labels)` call is cardinality-bounded (#601).
    # Raw `effective_model` is still used for logging and SDK wiring elsewhere.
    _sdk_labels = {**_LABELS, "model": sanitize_model_label(effective_model)}
    collected: list[str] = []
    _query_start = time.monotonic()
    _message_count = 0
    _tool_names: dict[str, str] = {}
    _tool_start_times: dict[str, float] = {}
    _last_total_tokens = 0
    _session_start = time.monotonic()
    _assistant_turn_count = 0
    # Tracks whether a non-empty TextBlock has already been streamed via on_chunk
    # during this run. Used to insert "\n\n" between streamed blocks so streaming
    # clients see the same on-wire shape as the non-streaming aggregation path
    # ("\n\n".join(collected) below) — see #500.
    _streamed_text_emitted = False
    # Counts successfully-dispatched non-empty streaming chunks for this run so
    # separator insertion doesn't desync on on_chunk timeouts (#1192). The
    # boolean ``_streamed_text_emitted`` is retained for external compatibility
    # but the authoritative separator gate is this counter.
    _stream_chunks_emitted = 0

    # llm.request child span (#630) — one per Claude SDK round-trip. Scoped
    # around the client lifecycle + receive_response loop so tool / hook spans
    # emitted inside nest correctly under this model-call span.
    try:
        _spawn_start = time.monotonic()
        with start_span(
            "llm.request",
            kind="client",
            attributes={"model": sanitize_model_label(effective_model)},
        ):
            async with ClaudeSDKClient(options=options) as client:
                if backend_sdk_subprocess_spawn_duration_seconds is not None:
                    backend_sdk_subprocess_spawn_duration_seconds.labels(**_sdk_labels).observe(
                        time.monotonic() - _spawn_start
                    )
                await client.query(prompt)
                _query_sent_at = time.monotonic()
                async for message in client.receive_response():
                    _message_count += 1
                    if isinstance(message, AssistantMessage):
                        if _assistant_turn_count == 0:
                            if backend_sdk_time_to_first_message_seconds is not None:
                                backend_sdk_time_to_first_message_seconds.labels(**_sdk_labels).observe(
                                    time.monotonic() - _query_sent_at
                                )
                        _assistant_turn_count += 1
                        _text_blocks: list[str] = []
                        for block in message.content:
                            if isinstance(block, TextBlock):
                                collected.append(block.text)
                                _text_blocks.append(block.text)
                                # Stream the chunk to the A2A event_queue (#430).
                                # The on_chunk coroutine is set by execute(); when
                                # None (e.g. MCP /mcp endpoint or non-streaming
                                # caller), we just buffer and the caller gets the
                                # full text at the end as before. Awaited directly
                                # so events stay ordered and exceptions surface
                                # rather than being swallowed by a fire-and-forget
                                # task object.
                                if on_chunk is not None and block.text:
                                    # Wrap on_chunk dispatch with an asyncio
                                    # timeout (#1091) — parity with codex
                                    # #724. A stalled A2A consumer must not
                                    # block the executor indefinitely; drop
                                    # the chunk and bump the drop counter so
                                    # operators can alert on back-pressure.
                                    _chunk_label_model = sanitize_model_label(effective_model)
                                    # #1484: Split separator emission into its
                                    # own try/except/increment. Previously the
                                    # separator and the text block shared one
                                    # try/except — if "\n\n" succeeded but
                                    # block.text then timed out, the emitted
                                    # counter stayed at its old value and the
                                    # next block re-emitted "\n\n" yielding
                                    # "\n\n\n\ntext" on the stream.
                                    if _stream_chunks_emitted > 0:
                                        try:
                                            await asyncio.wait_for(
                                                on_chunk("\n\n"),
                                                timeout=STREAM_CHUNK_TIMEOUT_SECONDS,
                                            )
                                            # Count the separator as an emitted
                                            # chunk so a subsequent text-block
                                            # timeout cannot cause a double-
                                            # prefix on the following block.
                                            _stream_chunks_emitted += 1
                                        except asyncio.TimeoutError:
                                            logger.warning(
                                                "Session %r: on_chunk separator timed out after %.3fs; "
                                                "dropping separator and continuing stream (#1091/#1484)",
                                                session_id,
                                                STREAM_CHUNK_TIMEOUT_SECONDS,
                                            )
                                            if backend_streaming_chunks_dropped_total is not None:
                                                try:
                                                    backend_streaming_chunks_dropped_total.labels(
                                                        **_LABELS,
                                                        model=_chunk_label_model,
                                                    ).inc()
                                                except Exception:
                                                    pass
                                        except Exception as _e:
                                            logger.warning(
                                                "Session %r: on_chunk separator raised: %s",
                                                session_id,
                                                _e,
                                                exc_info=True,
                                            )
                                            if backend_streaming_chunks_dropped_total is not None:
                                                try:
                                                    backend_streaming_chunks_dropped_total.labels(
                                                        **_LABELS,
                                                        model=_chunk_label_model,
                                                    ).inc()
                                                except Exception:
                                                    pass
                                    try:
                                        await asyncio.wait_for(
                                            on_chunk(block.text),
                                            timeout=STREAM_CHUNK_TIMEOUT_SECONDS,
                                        )
                                        # Only mark as emitted AFTER on_chunk
                                        # succeeded (#1192). If the await
                                        # above raised, the separator gate
                                        # must stay false so the *next*
                                        # chunk doesn't double-prefix.
                                        _stream_chunks_emitted += 1
                                        _streamed_text_emitted = True
                                    except asyncio.TimeoutError:
                                        logger.warning(
                                            "Session %r: on_chunk callback timed out after %.3fs; "
                                            "dropping chunk and continuing stream (#1091)",
                                            session_id,
                                            STREAM_CHUNK_TIMEOUT_SECONDS,
                                        )
                                        if backend_streaming_chunks_dropped_total is not None:
                                            try:
                                                backend_streaming_chunks_dropped_total.labels(
                                                    **_LABELS,
                                                    model=_chunk_label_model,
                                                ).inc()
                                            except Exception:
                                                pass
                                    except Exception as _e:
                                        # Never let a streaming-side error abort the
                                        # SDK iteration; log with traceback so
                                        # programming bugs surface (#1198), and
                                        # reuse the drop counter to expose
                                        # non-timeout losses — the existing metric
                                        # has no ``reason`` label so we share the
                                        # timeout series; operators can correlate
                                        # with the WARNING log line.
                                        logger.warning(
                                            "Session %r: on_chunk callback raised: %s",
                                            session_id,
                                            _e,
                                            exc_info=True,
                                        )
                                        if backend_streaming_chunks_dropped_total is not None:
                                            try:
                                                backend_streaming_chunks_dropped_total.labels(
                                                    **_LABELS,
                                                    model=_chunk_label_model,
                                                ).inc()
                                            except Exception:
                                                pass
                            elif isinstance(block, ToolUseBlock):
                                _tool_names[block.id] = block.name
                                _tool_start_times[block.id] = time.monotonic()
                                # Record that at least one tool_use executed on
                                # this attempt — run_query() consults this flag
                                # to refuse a collision-retry replay that would
                                # duplicate cluster mutations / writes (#1048).
                                if tool_use_flag is not None and not tool_use_flag[0]:
                                    tool_use_flag[0] = True
                                if backend_sdk_tool_calls_total is not None:
                                    backend_sdk_tool_calls_total.labels(**_LABELS, tool=block.name).inc()
                                if backend_sdk_tool_call_input_size_bytes is not None:
                                    backend_sdk_tool_call_input_size_bytes.labels(**_LABELS, tool=block.name).observe(
                                        _utf8_byte_length(json.dumps(block.input, default=str))
                                    )
                                # tool.call / mcp.call child span (#630).
                                # Claude MCP tools surface as ToolUseBlocks with
                                # names of the form "mcp__<server>__<tool>";
                                # split to emit the more specific mcp.call span
                                # with server/tool attributes. Non-MCP tool
                                # calls get a generic tool.call span.
                                _bname = block.name or ""
                                if _bname.startswith("mcp__"):
                                    _parts = _bname.split("__", 2)
                                    _mcp_server = _parts[1] if len(_parts) > 1 else ""
                                    _mcp_tool = _parts[2] if len(_parts) > 2 else ""
                                    with start_span(
                                        "mcp.call",
                                        kind="client",
                                        attributes={
                                            "mcp.server": _mcp_server,
                                            "mcp.tool": _mcp_tool,
                                            "tool.name": _bname,
                                        },
                                    ):
                                        await _log_tool_event("tool_use", block, session_id, model=effective_model)
                                else:
                                    with start_span(
                                        "tool.call",
                                        kind="internal",
                                        attributes={"tool.name": _bname},
                                    ):
                                        await _log_tool_event("tool_use", block, session_id, model=effective_model)
                            elif isinstance(block, ToolResultBlock):
                                tool_name = _tool_names.get(block.tool_use_id, "unknown")
                                if block.is_error and backend_sdk_tool_errors_total is not None:
                                    backend_sdk_tool_errors_total.labels(**_LABELS, tool=tool_name).inc()
                                _t_start = _tool_start_times.pop(block.tool_use_id, None)
                                _tool_dur = (time.monotonic() - _t_start) if _t_start is not None else 0.0
                                if _t_start is not None and backend_sdk_tool_duration_seconds is not None:
                                    backend_sdk_tool_duration_seconds.labels(**_LABELS, tool=tool_name).observe(
                                        _tool_dur
                                    )
                                # Outbound MCP tool metric family (#1104) —
                                # no-op for non-mcp__ tools so we don't
                                # inflate series with SDK-internal tools.
                                try:
                                    from mcp_metrics import observe_outbound_mcp_call as _obs_outbound_mcp

                                    _obs_outbound_mcp(
                                        backend_mcp_outbound_requests_total,
                                        backend_mcp_outbound_duration_seconds,
                                        dict(_LABELS),
                                        tool_name,
                                        _tool_dur,
                                        bool(block.is_error),
                                    )
                                except Exception:
                                    pass
                                if backend_sdk_tool_result_size_bytes is not None:
                                    backend_sdk_tool_result_size_bytes.labels(**_LABELS, tool=tool_name).observe(
                                        _utf8_byte_length(str(block.content))
                                    )
                                await _log_tool_event("tool_result", block, session_id, model=effective_model)
                                # tool.use event (#1110 phase 3).
                                # Fire-and-forget; never let emit failure
                                # interrupt the tool dispatch path.
                                try:
                                    _result_size = (
                                        _utf8_byte_length(str(block.content)) if block.content is not None else 0
                                    )
                                    _emit_event_safe(
                                        "tool.use",
                                        {
                                            "session_id_hash": _session_id_hash(session_id),
                                            "tool": tool_name or "unknown",
                                            "duration_ms": int(_tool_dur * 1000),
                                            "outcome": "error" if block.is_error else "ok",
                                            "result_size_bytes": _result_size,
                                        },
                                    )
                                except Exception:
                                    pass
                        try:
                            usage = await client.get_context_usage()
                            pct = usage.get("percentage", 0.0)
                            _last_total_tokens = usage.get("totalTokens", 0)
                            if backend_context_tokens is not None:
                                backend_context_tokens.labels(**_LABELS).observe(_last_total_tokens)
                            if backend_context_tokens_remaining is not None:
                                backend_context_tokens_remaining.labels(**_LABELS).observe(
                                    usage.get("maxTokens", 0) - _last_total_tokens
                                )
                            if backend_context_usage_percent is not None:
                                backend_context_usage_percent.labels(**_LABELS).observe(pct)
                            if pct >= 100 and backend_context_exhaustion_total is not None:
                                backend_context_exhaustion_total.labels(**_LABELS).inc()
                            if pct >= CONTEXT_USAGE_WARN_THRESHOLD * 100:
                                if backend_context_warnings_total is not None:
                                    backend_context_warnings_total.labels(**_LABELS).inc()
                                logger.warning(
                                    f"Session {session_id!r}: context usage {pct:.1f}% "
                                    f"exceeds threshold {CONTEXT_USAGE_WARN_THRESHOLD * 100:.0f}%"
                                )
                            if max_tokens is not None and _last_total_tokens >= max_tokens:
                                if backend_budget_exceeded_total is not None:
                                    backend_budget_exceeded_total.labels(**_LABELS).inc()
                                raise BudgetExceededError(_last_total_tokens, max_tokens, list(collected))
                        except BudgetExceededError:
                            raise
                        except Exception as e:
                            if backend_sdk_context_fetch_errors_total is not None:
                                backend_sdk_context_fetch_errors_total.labels(**_sdk_labels).inc()
                            logger.warning(f"Session {session_id!r}: get_context_usage failed: {e}")
                        for _text in _text_blocks:
                            await log_entry(
                                "agent", _text, session_id, model=effective_model, tokens=_last_total_tokens or None
                            )
                            # conversation.turn event (#1110 phase 3).
                            # Fire-and-forget; never let emit failure
                            # interrupt the streaming response path.
                            try:
                                # Omit ``model`` entirely when falsy (#1150).
                                _a_turn_payload: dict = {
                                    "session_id_hash": _session_id_hash(session_id),
                                    "role": "assistant",
                                    "content_bytes": len((_text or "").encode("utf-8")),
                                }
                                if effective_model:
                                    _a_turn_payload["model"] = effective_model
                                _emit_event_safe(
                                    "conversation.turn",
                                    _a_turn_payload,
                                )
                            except Exception:
                                pass
                    elif isinstance(message, ResultMessage) and message.is_error:
                        if backend_sdk_result_errors_total is not None:
                            backend_sdk_result_errors_total.labels(**_sdk_labels).inc()
                        if backend_sdk_query_error_duration_seconds is not None:
                            backend_sdk_query_error_duration_seconds.labels(**_sdk_labels).observe(
                                time.monotonic() - _query_start
                            )
                        error_parts = list(message.errors or [])
                        if not error_parts and message.result:
                            error_parts = [message.result]
                        raise RuntimeError(
                            "\n".join(str(e) for e in error_parts)
                            if error_parts
                            else "Claude SDK returned an error result with no details"
                        )
    except (OSError, ConnectionError):
        if backend_sdk_client_errors_total is not None:
            backend_sdk_client_errors_total.labels(**_sdk_labels).inc()
        if backend_sdk_query_error_duration_seconds is not None:
            backend_sdk_query_error_duration_seconds.labels(**_sdk_labels).observe(time.monotonic() - _query_start)
        raise
    finally:
        if backend_sdk_session_duration_seconds is not None:
            backend_sdk_session_duration_seconds.labels(**_sdk_labels).observe(time.monotonic() - _session_start)

    if backend_sdk_query_duration_seconds is not None:
        backend_sdk_query_duration_seconds.labels(**_sdk_labels).observe(time.monotonic() - _query_start)
    if backend_sdk_messages_per_query is not None:
        backend_sdk_messages_per_query.labels(**_sdk_labels).observe(_message_count)
    if backend_sdk_tokens_per_query is not None:
        backend_sdk_tokens_per_query.labels(**_sdk_labels).observe(_last_total_tokens)
    if backend_sdk_tool_calls_per_query is not None:
        backend_sdk_tool_calls_per_query.labels(**_sdk_labels).observe(len(_tool_names))
    if backend_sdk_turns_per_query is not None:
        backend_sdk_turns_per_query.labels(**_sdk_labels).observe(_assistant_turn_count)
    if backend_text_blocks_per_query is not None:
        backend_text_blocks_per_query.labels(**_sdk_labels).observe(len(collected))
    return collected


async def run_query(
    prompt: str,
    ctx: RunContext,
    is_new: bool,
    max_tokens: int | None = None,
    on_chunk: Callable[[str], Awaitable[None]] | None = None,
    hook_state: HookState | None = None,
) -> list[str]:
    """Outer wrapper around ``_run_query_inner`` with session-collision retry (#1048).

    Builds ``ClaudeAgentOptions`` via ``_make_options`` (passing ``ctx.session_id``,
    ``resume=not is_new``, ``stderr_fn=capture_stderr``, plus ``mcp_servers`` /
    ``model`` / ``agent_md_content`` / ``hook_state`` from the caller) and
    delegates to ``_run_query_inner``. ``capture_stderr`` accumulates SDK
    subprocess stderr into ``stderr_lines``, increments
    ``backend_sdk_errors_total`` per line, and logs each at ERROR so a noisy SDK
    is visible both in ``/metrics`` and in the log stream.

    ``BudgetExceededError`` always propagates unmodified. On any other
    exception, scans ``stderr_lines`` for a "session ... already in use"
    collision: when ``is_new`` and at least one collision line is present,
    retries the prompt once with ``resume=True`` and increments
    ``backend_task_retries_total``. The retry is refused (original exception
    re-raises, logged at ERROR) when the failed attempt already recorded a
    ``ToolUseBlock`` — ``_run_query_inner`` sets ``_tool_use_flag[0]=True`` on
    the first tool use, and replaying the prompt would duplicate cluster
    mutations or file writes (#1048). The retry path deliberately does NOT
    re-observe ``backend_sdk_query_error_duration_seconds`` because
    ``_run_query_inner`` already observed it on the inner error path; a
    second observe at this outer site would double-count the histogram and
    risk a label drift from the inner call's pre-computed ``_sdk_labels``
    (#870). ``_tool_use_flag`` is forwarded into the retry call so a tool_use
    on the resumed attempt also updates the idempotency marker (#1485).

    The ``finally`` block always observes ``backend_stderr_lines_per_task``
    (length of ``stderr_lines``, zero for clean runs so the histogram rate
    stays interpretable) and increments ``backend_tasks_with_stderr_total``
    when ``stderr_lines`` is non-empty. Returns the list returned by
    ``_run_query_inner`` — collected text chunks for the turn; ``on_chunk``,
    ``max_tokens``, and ``hook_state`` are forwarded unchanged.
    """
    stderr_lines: list[str] = []
    _query_start = time.monotonic()

    def capture_stderr(line: str) -> None:
        stderr_lines.append(line)
        if backend_sdk_errors_total is not None:
            backend_sdk_errors_total.labels(**_LABELS).inc()
        logger.error(f"[claude stderr] {line}")

    effective_model = ctx.model or CLAUDE_MODEL
    # Mutable idempotency marker — set to [True] by _run_query_inner when it
    # records the first ToolUseBlock. The collision-retry path below consults
    # this to refuse a replay that would duplicate cluster mutations / file
    # writes (#1048).
    _tool_use_flag: list[bool] = [False]
    try:
        return await _run_query_inner(
            prompt,
            _make_options(
                ctx.session_id,
                resume=not is_new,
                stderr_fn=capture_stderr,
                mcp_servers=ctx.mcp_servers,
                model=ctx.model,
                agent_md_content=ctx.agent_md_content,
                hook_state=hook_state,
            ),
            ctx.session_id,
            effective_model=effective_model,
            max_tokens=max_tokens,
            on_chunk=on_chunk,
            tool_use_flag=_tool_use_flag,
        )
    except BudgetExceededError:
        raise
    except Exception:
        _collision_lines = [
            line for line in stderr_lines if "session" in line.lower() and "already in use" in line.lower()
        ]
        if is_new and _collision_lines:
            # If the failed attempt already executed at least one tool_use,
            # replaying the prompt as a resume would re-run those tools and
            # potentially duplicate cluster mutations or file writes (#1048).
            # Refuse the retry and re-raise.
            if _tool_use_flag[0]:
                logger.error(
                    f"Session {ctx.session_id!r}: session-ID collision on new session "
                    f"but attempt already executed a tool_use — refusing retry to avoid "
                    f"duplicate side effects. See #1048."
                )
                raise
            logger.warning(
                f"Session {ctx.session_id!r}: session-ID collision detected on new session "
                f"(stderr: {_collision_lines[0]!r}) — retrying as resume."
            )
            if backend_task_retries_total is not None:
                backend_task_retries_total.labels(**_LABELS).inc()
            # Don't re-observe backend_sdk_query_error_duration_seconds
            # here (#870) — the inner _run_query_inner already observed
            # it on the error path (OSError/ConnectionError at 1339 or
            # ResultMessage.is_error at 1330). A second observe at this
            # outer site double-counted the histogram for every retried
            # error, and also rebuilt the label shape from _LABELS+
            # sanitize_model_label(effective_model) which could drift
            # from the inner call's pre-computed _sdk_labels.
            return await _run_query_inner(
                prompt,
                _make_options(
                    ctx.session_id,
                    resume=True,
                    stderr_fn=capture_stderr,
                    mcp_servers=ctx.mcp_servers,
                    model=ctx.model,
                    agent_md_content=ctx.agent_md_content,
                    hook_state=hook_state,
                ),
                ctx.session_id,
                effective_model=effective_model,
                max_tokens=max_tokens,
                on_chunk=on_chunk,
                # #1485: pass tool_use_flag on the retry path too so the
                # idempotency marker gets updated if the resumed attempt
                # executes any tool_use. Without this, a subsequent failure
                # after retry would not reflect the replayed tool activity.
                tool_use_flag=_tool_use_flag,
            )
        raise
    finally:
        if backend_stderr_lines_per_task is not None:
            backend_stderr_lines_per_task.labels(**_LABELS).observe(len(stderr_lines))
        if stderr_lines and backend_tasks_with_stderr_total is not None:
            backend_tasks_with_stderr_total.labels(**_LABELS).inc()


async def run(
    prompt: str,
    session_id: str,
    sessions: OrderedDict[str, float],
    mcp_servers: dict,
    agent_md_content: str,
    model: str | None = None,
    max_tokens: int | None = None,
    on_chunk: Callable[[str], Awaitable[None]] | None = None,
    hook_state: "HookState | None" = None,
) -> str:
    """External entry point — preserves the legacy signature (#551).

    Thin adapter that packs the pass-through params (``session_id``, ``model``,
    ``mcp_servers``, ``agent_md_content``) into a ``RunContext`` and forwards
    the per-layer params (``sessions``, ``max_tokens``, ``on_chunk``,
    ``hook_state``) explicitly to ``_run_inner``. Keeps all existing callers
    (executor.execute, backends/claude/main.py MCP tools/call path) working without
    signature churn.
    """
    ctx = RunContext(
        session_id=session_id,
        model=model,
        mcp_servers=mcp_servers,
        agent_md_content=agent_md_content,
    )
    if backend_concurrent_queries is not None:
        backend_concurrent_queries.labels(**_LABELS).inc()
    try:
        return await _run_inner(
            prompt,
            ctx,
            sessions,
            max_tokens=max_tokens,
            on_chunk=on_chunk,
            hook_state=hook_state,
        )
    finally:
        if backend_concurrent_queries is not None:
            backend_concurrent_queries.labels(**_LABELS).dec()


async def _run_inner(
    prompt: str,
    ctx: RunContext,
    sessions: OrderedDict[str, float],
    max_tokens: int | None = None,
    on_chunk: Callable[[str], Awaitable[None]] | None = None,
    hook_state: "HookState | None" = None,
) -> str:
    resolved_model = ctx.model or CLAUDE_MODEL or "default"
    if backend_model_requests_total is not None:
        backend_model_requests_total.labels(**_LABELS, model=sanitize_model_label(resolved_model)).inc()

    # The Claude CLI's `--resume <id>` mode requires a session file on
    # disk; when the in-memory LRU cache has the session tracked but the
    # file is missing (evicted, deleted, cwd drift — see
    # backend_session_path_mismatch_total) `resume=` fails with an empty-
    # stderr ProcessError. Ground truth is the disk: if there's no
    # session file, treat as new regardless of what the cache says. The
    # cache lookup below still drives the session-idle metric.
    file_exists = await asyncio.to_thread(_session_file_exists, ctx.session_id)
    is_new = not file_exists
    if not is_new and backend_session_idle_seconds is not None:
        _last_used = sessions.get(ctx.session_id)
        if _last_used is not None:
            backend_session_idle_seconds.labels(**_LABELS).observe(time.monotonic() - _last_used)
    if backend_session_starts_total is not None:
        backend_session_starts_total.labels(**_LABELS, type="new" if is_new else "resumed").inc()

    _prompt_preview = (
        prompt[:LOG_PROMPT_MAX_BYTES] + ("[truncated]" if len(prompt) > LOG_PROMPT_MAX_BYTES else "")
        if LOG_PROMPT_MAX_BYTES > 0
        else "[redacted]"
    )
    logger.info(f"Session {ctx.session_id} ({'new' if is_new else 'existing'}) — prompt: {_prompt_preview!r}")
    await log_entry("user", prompt, ctx.session_id, model=ctx.model)
    # conversation.turn event (#1110 phase 3). Wrap — never raise.
    # Omit the ``model`` key entirely when it's falsy (#1150) — an
    # empty string was both noisier and a validator foot-gun (the
    # schema accepts any string including ""; downstream consumers
    # would rather see the field missing than an empty stub).
    try:
        _turn_payload: dict = {
            "session_id_hash": _session_id_hash(ctx.session_id),
            "role": "user",
            "content_bytes": len((prompt or "").encode("utf-8")),
        }
        if ctx.model:
            _turn_payload["model"] = ctx.model
        _emit_event_safe("conversation.turn", _turn_payload)
    except Exception:
        pass

    if backend_prompt_length_bytes is not None:
        backend_prompt_length_bytes.labels(**_LABELS).observe(_utf8_byte_length(prompt))

    _start = time.monotonic()
    _budget_exceeded = False
    try:
        collected = await asyncio.wait_for(
            run_query(prompt, ctx, is_new, max_tokens=max_tokens, on_chunk=on_chunk, hook_state=hook_state),
            timeout=TASK_TIMEOUT_SECONDS,
        )
        await _track_session(sessions, ctx.session_id)
    except asyncio.TimeoutError as _exc:
        logger.error(f"Session {ctx.session_id!r}: timed out after {TASK_TIMEOUT_SECONDS}s.")
        # Terminal marker for partial assistant text already committed by
        # per-block log_entry calls inside _run_query_inner (#566). Without
        # this, conversation.jsonl ends on a plausible-looking agent turn
        # that never completed. Mirrors the BudgetExceededError branch above.
        await log_entry(
            "system",
            f"{type(_exc).__name__}: {_exc}",
            ctx.session_id,
            model=ctx.model,
        )
        # Remove the session from the LRU cache on timeout. The SDK context
        # manager is cancelled mid-stream, so the session state may be
        # inconsistent. Evicting it ensures the next call starts a fresh
        # session rather than trying to resume a potentially broken one.
        # #1483: serialise under _get_sessions_lock() to match _track_session's
        # popitem/move_to_end path on the shared OrderedDict.
        async with _get_sessions_lock():
            sessions.pop(ctx.session_id, None)
        # Also remove the on-disk session file so the next request for this
        # session_id starts with empty history rather than reloading the
        # potentially stale or mid-stream snapshot written before the timeout.
        # Run the unlink in a thread pool to avoid blocking the event loop on
        # slow/remote filesystems (#426).
        _timeout_path = _session_file_path(ctx.session_id)
        if _timeout_path is not None:
            try:
                await asyncio.to_thread(_timeout_path.unlink, missing_ok=True)
                logger.info("Removed stale session file for timed-out session %r", ctx.session_id)
            except OSError as _e:
                logger.warning("Could not remove session file for timed-out session %r: %s", ctx.session_id, _e)
        if backend_tasks_total is not None:
            backend_tasks_total.labels(**_LABELS, status="timeout").inc()
        if backend_task_error_duration_seconds is not None:
            backend_task_error_duration_seconds.labels(**_LABELS).observe(time.monotonic() - _start)
        if backend_task_last_error_timestamp_seconds is not None:
            backend_task_last_error_timestamp_seconds.labels(**_LABELS).set(time.time())
        raise
    except BudgetExceededError as _bexc:
        _budget_exceeded = True
        logger.warning(f"Session {ctx.session_id!r}: {_bexc} — returning partial response.")
        await log_entry(
            "system",
            f"Budget exceeded: {_bexc.total} tokens used of {_bexc.limit} limit.",
            ctx.session_id,
            model=ctx.model,
        )
        collected = _bexc.collected
        await _track_session(sessions, ctx.session_id)
    except Exception as _exc:
        if backend_tasks_total is not None:
            backend_tasks_total.labels(**_LABELS, status="error").inc()
        if backend_task_error_duration_seconds is not None:
            backend_task_error_duration_seconds.labels(**_LABELS).observe(time.monotonic() - _start)
        if backend_task_last_error_timestamp_seconds is not None:
            backend_task_last_error_timestamp_seconds.labels(**_LABELS).set(time.time())
        # Terminal marker so partial assistant text already in
        # conversation.jsonl is demarcated on mid-stream failure (#566).
        await log_entry(
            "system",
            f"{type(_exc).__name__}: {_exc}",
            ctx.session_id,
            model=ctx.model,
        )
        raise

    if backend_tasks_total is not None:
        backend_tasks_total.labels(**_LABELS, status="budget_exceeded" if _budget_exceeded else "success").inc()
    # #1729 — only advance the success-timestamp gauge on full successes,
    # not budget-exceeded partials. Mirrors the codex fix from #1662.
    if not _budget_exceeded and backend_task_last_success_timestamp_seconds is not None:
        backend_task_last_success_timestamp_seconds.labels(**_LABELS).set(time.time())
    if backend_task_duration_seconds is not None:
        backend_task_duration_seconds.labels(**_LABELS).observe(time.monotonic() - _start)
    if backend_task_timeout_headroom_seconds is not None:
        backend_task_timeout_headroom_seconds.labels(**_LABELS).observe(
            TASK_TIMEOUT_SECONDS - (time.monotonic() - _start)
        )

    response = "\n\n".join(collected) if collected else ""
    if not response:
        if backend_empty_responses_total is not None:
            backend_empty_responses_total.labels(**_LABELS).inc()
    elif backend_response_length_bytes is not None:
        backend_response_length_bytes.labels(**_LABELS).observe(_utf8_byte_length(response))
    return response


class AgentExecutor(A2AAgentExecutor):
    """A2A agent executor backed by the Claude Agent SDK.

    Subclass of ``a2a.server.agent_execution.AgentExecutor`` that implements
    the A2A ``execute`` / ``cancel`` contract by driving ``ClaudeSDKClient``
    queries. The instance owns the executor's mutable runtime state:

    - ``_sessions`` — per-session LRU of last-active timestamps used by the
      SDK glue to decide when to drop a session client.
    - ``_running_tasks`` — A2A ``task_id`` → ``asyncio.Task`` map populated
      on entry to ``execute`` and cleared in its ``finally``; ``cancel()``
      and ``close()`` use it to drain in-flight requests.
    - ``_mcp_servers`` — currently-live MCP server set, swapped atomically
      under ``_mcp_reload_lock`` via ``_swap_mcp_servers`` (#1051) so a
      burst of file events can't interleave reloads. ``_mcp_generation``
      is bumped on every swap and ``backend_mcp_servers_active`` is
      restamped.
    - ``_agent_md_content`` / ``_agent_md_revision`` — cached CLAUDE.md
      contents and a hash-derived revision label that
      ``backend_agent_md_revision`` is stamped with so operators can see
      which revision is live.
    - ``_hook_state`` — ``HookState`` containing the baseline + extension
      rule lists for the PreToolUse gate (#467). Mutations are serialised
      against pre-tool-use readers by the threading lock at
      ``_hook_state_lock`` (#1488).
    - ``_mcp_watcher_tasks`` — background watcher tasks (MCP config,
      CLAUDE.md, hooks.yaml, settings.json) returned by ``_mcp_watchers``.

    Lifecycle:

    1. ``perform_initial_loads`` runs synchronously on the lifespan
       startup path so the first MCP / CLAUDE.md / hooks.yaml parse is
       complete before readiness flips (#869).
    2. The watcher tasks then keep config in sync on disk-change events.
    3. ``execute`` dispatches one A2A request per call, tracking the task
       in ``_running_tasks`` so it can be cancelled.
    4. ``close`` cancels and awaits in-flight ``execute`` tasks first,
       then the MCP / config watchers (#587), and closes the shared hook
       httpx client (#661).

    A one-shot probe at ``__init__`` time runs ``_session_path_self_test``
    to detect Claude Agent SDK on-disk layout drift (#530) — observability
    only; failures never block startup.
    """

    def __init__(self):
        self._sessions: OrderedDict[str, float] = OrderedDict()
        self._running_tasks: dict[str, asyncio.Task] = {}
        self._mcp_servers: dict = {}
        # Serialise MCP reloads so a rapid burst of file events can't
        # interleave parses and swap out-of-order (#1051). All mutations
        # of ``self._mcp_servers`` go through ``_swap_mcp_servers`` under
        # this lock. Created lazily when the event loop is available —
        # __init__ runs before the lifespan context on some shapes.
        self._mcp_reload_lock: asyncio.Lock | None = None
        self._mcp_generation: int = 0
        self._agent_md_content: str = _load_agent_md()
        self._agent_md_revision: str = _compute_agent_md_revision(self._agent_md_content)
        self._stamp_agent_md_revision(self._agent_md_revision, previous=None)
        self._mcp_watcher_tasks: list[asyncio.Task] = []
        # Hook policy state (#467). The baseline is loaded eagerly; the
        # hooks_config_watcher populates `extensions` and keeps it in sync
        # with hooks.yaml on disk.
        self._hook_state: HookState = HookState(
            baseline_enabled=HOOKS_BASELINE_ENABLED,
            baseline=list(BASELINE_RULES) if HOOKS_BASELINE_ENABLED else [],
            extensions=[],
        )
        # #1488: serialise writes to hook_state.extensions against readers
        # taking an active_rules() snapshot. The writer path runs on the
        # watcher's event loop; readers run on the pre-tool-use hook
        # callback thread. A threading.Lock works for both — the hot path
        # is a single attribute swap inside the lock so event-loop
        # blocking is negligible. Attach to the HookState instance too
        # so _make_options can forward it to _make_pre_tool_use_hook
        # without a new parameter.
        self._hook_state_lock: threading.Lock = threading.Lock()
        try:
            self._hook_state._state_lock = self._hook_state_lock
        except Exception:
            # Attribute set on a dataclass instance; should never fail,
            # but be defensive so executor import never breaks on
            # HookState stub fallback (#1050).
            pass
        if backend_hooks_active_rules is not None:
            backend_hooks_active_rules.labels(**_LABELS, source="baseline").set(len(self._hook_state.baseline))
            backend_hooks_active_rules.labels(**_LABELS, source="extension").set(0)
        self._refresh_hook_enforcement_mode_metric()
        # One-shot probe for Claude Agent SDK on-disk layout drift (#530).
        # Read-only filesystem + attribute inspection; no SDK subprocess is
        # spawned and no LLM query is fired. Emits backend_session_path_mismatch_total
        # labelled by reason when the layout helper's assumptions no longer
        # match reality. Must never prevent startup — wrapped internally.
        _session_path_self_test()

    def _mcp_watchers(self):
        """Return callables for MCP config, CLAUDE.md, hooks.yaml, and settings.json watching."""
        return [
            self.mcp_config_watcher,
            self.agent_md_watcher,
            self.hooks_config_watcher,
            self.settings_watcher,
        ]

    def _refresh_hook_enforcement_mode_metric(self) -> None:
        """Report whether the Claude PreToolUse gate has any active rules."""
        if backend_hooks_enforcement_mode is None:
            return
        try:
            rule_count = len(self._hook_state.baseline) + len(self._hook_state.extensions)
            backend_hooks_enforcement_mode.labels(**_LABELS).set(1 if rule_count > 0 else -1)
        except Exception:
            # Metrics must never affect request execution or startup.
            pass

    def hooks_enforcement_mode_for_health(self) -> str:
        """Return a compact health-payload label for the active hook posture."""
        try:
            rule_count = len(self._hook_state.baseline) + len(self._hook_state.extensions)
            return "enforcing" if rule_count > 0 else "disabled"
        except Exception:
            return "unknown"

    def _stamp_agent_md_revision(self, current: str, previous: str | None) -> None:
        """Update backend_agent_md_revision for a possibly-new CLAUDE.md revision."""
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
                pass
            except Exception as exc:  # pragma: no cover - defensive
                logger.debug("agent_md_revision remove failed for %r: %r", previous, exc)
        try:
            backend_agent_md_revision.labels(**_LABELS, revision=current).set(1)
        except Exception as exc:  # pragma: no cover - defensive
            logger.debug("agent_md_revision set failed for %r: %r", current, exc)

    def _get_mcp_reload_lock(self) -> asyncio.Lock:
        """Lazily create the MCP reload lock (#1051).

        Deferring creation until first use avoids pinning the lock to
        whatever loop happens to be active at ``__init__`` time (which
        in some pytest harnesses is a throw-away loop).
        """
        if self._mcp_reload_lock is None:
            self._mcp_reload_lock = asyncio.Lock()
        return self._mcp_reload_lock

    async def _swap_mcp_servers(self, new_servers: dict, *, source: str) -> None:
        """Atomically replace ``self._mcp_servers`` with ``new_servers`` (#1051).

        Holds the reload lock across the pointer swap and the companion
        metric / generation bookkeeping so a concurrent reload can't
        interleave and leave the executor observing a stale
        ``_mcp_generation`` for the latest server set. ``source`` is a
        short label (``"initial"`` / ``"watcher"``) used in the
        transition log so operators see which path applied the change.
        """
        async with self._get_mcp_reload_lock():
            old_keys = sorted(self._mcp_servers.keys())
            new_keys = sorted(new_servers.keys())
            self._mcp_servers = new_servers
            self._mcp_generation += 1
            if backend_mcp_servers_active is not None:
                backend_mcp_servers_active.labels(**_LABELS).set(len(new_servers))
            if old_keys != new_keys:
                logger.info(
                    "MCP config swapped (%s, gen=%d): %s -> %s",
                    source,
                    self._mcp_generation,
                    old_keys,
                    new_keys,
                )
            else:
                logger.info(
                    "MCP config reloaded (%s, gen=%d, keys unchanged): %s",
                    source,
                    self._mcp_generation,
                    new_keys,
                )

    async def perform_initial_loads(self) -> None:
        """Pre-load MCP config, CLAUDE.md, and hooks.yaml before readiness (#869).

        Previously these initial parses happened inside each watcher body, which
        started as a background task alongside ``server.serve()``. A request
        arriving immediately after pod start could therefore observe an empty
        MCP server set, empty agent_md content, or baseline-only hooks. Running
        the first parse synchronously on the lifespan/startup path guarantees
        that when ``_set_ready_when_started`` flips readiness, the executor
        state reflects on-disk config. The watchers then detect the pre-loaded
        state and skip the redundant first parse before entering awatch().
        """
        # MCP config. Only mark initial-load as 'done' on success so a parse
        # failure at boot doesn't lock watchers out of their first-parse path
        # (#978). On failure, leave _initial_mcp_loaded falsy; the watcher's
        # `if not _initial_mcp_loaded` branch will retry the parse on first
        # awatch event instead of skipping it as a redundant re-parse.
        # Route the swap through _swap_mcp_servers so the watcher's later
        # reloads share the same serialisation point (#1051).
        try:
            _initial = await asyncio.to_thread(_load_mcp_config)
        except Exception:
            await self._swap_mcp_servers({}, source="initial-failed")
        else:
            await self._swap_mcp_servers(_initial, source="initial")
            self._initial_mcp_loaded = True

        # CLAUDE.md — __init__ already loaded this synchronously, but refresh
        # here so any content written between import time and lifespan start is
        # picked up before readiness flips. Only mark 'done' on success (#978).
        try:
            self._agent_md_content = _load_agent_md()
            logger.info("CLAUDE.md loaded (initial) from %s", AGENT_MD)
        except Exception as exc:
            logger.warning("CLAUDE.md initial load failed: %r (keeping __init__ value)", exc)
        else:
            previous = self._agent_md_revision
            current = _compute_agent_md_revision(self._agent_md_content)
            if current != previous:
                self._agent_md_revision = current
                self._stamp_agent_md_revision(current, previous=previous)
            self._initial_agent_md_loaded = True

        # hooks.yaml extensions. Only mark 'done' on success (#978).
        try:
            _new_ext = await asyncio.to_thread(load_hooks_config_sync)
            # #1488: swap under the hook-state lock so a concurrent
            # PreToolUse reader cannot snapshot a partial state.
            with self._hook_state_lock:
                self._hook_state.extensions = _new_ext
        except Exception as exc:
            logger.warning("hooks.yaml initial load failed: %r (baseline-only)", exc)
        else:
            self._initial_hooks_loaded = True
        if backend_hooks_active_rules is not None:
            backend_hooks_active_rules.labels(**_LABELS, source="extension").set(len(self._hook_state.extensions))
        self._refresh_hook_enforcement_mode_metric()
        logger.info(
            "Hooks config loaded (initial): baseline=%s (rules=%d) extensions=%d",
            self._hook_state.baseline_enabled,
            len(self._hook_state.baseline),
            len(self._hook_state.extensions),
        )

    async def close(self) -> None:
        """Cancel and drain all in-flight execute() tasks and MCP watcher tasks (#587).

        On shutdown, uvicorn invokes this before its force-shutdown deadline.
        Previously only the MCP/agent_md/hooks watchers were cancelled, leaving
        any in-flight A2A request tasks tracked in ``self._running_tasks`` to
        run to natural completion — or be SIGKILLed by uvicorn — which left
        partial records in ``conversation.jsonl``, ``tool-activity.jsonl``, and
        ``tool-audit.jsonl`` plus orphaned Claude CLI subprocesses.

        Cancellation order matters:
          1. Cancel in-flight execute() tasks first. ``_run_query`` /
             ``_run_inner`` propagate ``CancelledError`` through the
             ``async with ClaudeSDKClient(...)`` context manager, which
             closes the CLI subprocess, and the ``finally`` blocks in
             ``execute()`` flush their last JSONL appends via
             ``asyncio.to_thread(_append_log, ...)`` before the task exits.
          2. Then cancel the MCP/config watchers (existing behavior).
        """
        in_flight = list(self._running_tasks.values())
        for task in in_flight:
            task.cancel()
        if in_flight:
            await asyncio.gather(*in_flight, return_exceptions=True)

        for task in self._mcp_watcher_tasks:
            task.cancel()
        if self._mcp_watcher_tasks:
            await asyncio.gather(*self._mcp_watcher_tasks, return_exceptions=True)
        self._mcp_watcher_tasks.clear()

        # Close the module-level httpx client used for hook POSTs (#661).
        await _close_hook_http_client()

    async def mcp_config_watcher(self) -> None:
        """Watch ``MCP_CONFIG_PATH`` and hot-reload the active MCP server set (#591).

        Mirrors ``hooks_config_watcher`` and ``agent_md_watcher`` in shape:
        an initial parse, then a never-exiting ``awatch`` loop over the
        containing directory that re-parses on every change to the target
        file. Every server-set mutation routes through
        ``_swap_mcp_servers`` so the ``_mcp_reload_lock`` (#1051)
        serialises this watcher against the lifespan-path initial load and
        against any other concurrent reload — a rapid burst of file events
        cannot publish an older parse after a newer one.

        Initial-load posture: ``_load_mcp_config`` is invoked in a worker
        thread; on parse failure the swap is performed with an empty
        server set (source ``"watcher-initial-failed"``) because there is
        no previous value to retain. The warning log and
        ``backend_mcp_config_errors_total`` increment for that failure are
        emitted by ``_load_mcp_config`` itself, not here. The whole
        initial-load block is skipped when ``_initial_mcp_loaded`` is set
        — that flag is flipped by ``perform_initial_loads`` on the
        lifespan startup path (#869), so the watcher does not double-load
        when the backend boots normally.

        Reload posture: on a change event whose absolute path matches
        ``MCP_CONFIG_PATH`` the new server set is parsed into a temporary
        and only swapped on success. A malformed edit logs a warning and
        breaks out to the outer loop with the previous server set
        intact, matching ``hooks_config_watcher``'s fail-safe (#591).
        Successful reloads bump ``backend_mcp_config_reloads_total``;
        every event batch bumps ``backend_watcher_events_total`` with
        ``watcher="mcp"``.

        Resilience: if the watched directory is absent the watcher logs
        and sleeps 10s before re-checking, and if ``awatch`` itself
        returns (directory disappeared mid-watch) the outer ``while True``
        re-enters after a 10s sleep, bumping
        ``backend_file_watcher_restarts_total`` with ``watcher="mcp"`` so
        a flapping config volume is observable in metrics. The coroutine
        is intended to run for the lifetime of the executor; ``close()``
        cancels it via ``_mcp_watcher_tasks``.
        """
        # Initial load: fall back to an empty server set if the first parse
        # fails — there is no previous value to keep. The warning log and
        # ``backend_mcp_config_errors_total`` increment are already emitted by
        # ``_load_mcp_config`` itself (#591). Skipped when perform_initial_loads
        # already ran on the lifespan startup path (#869). Route through
        # ``_swap_mcp_servers`` so the reload lock serialises this with the
        # post-awatch reloads below (#1051).
        if not getattr(self, "_initial_mcp_loaded", False):
            try:
                _initial = await asyncio.to_thread(_load_mcp_config)
            except Exception:
                await self._swap_mcp_servers({}, source="watcher-initial-failed")
            else:
                await self._swap_mcp_servers(_initial, source="watcher-initial")

        watch_dir = os.path.dirname(MCP_CONFIG_PATH)
        while True:
            if not os.path.isdir(watch_dir):
                logger.info("MCP config directory not found — retrying in 10s.")
                await asyncio.sleep(10)
                continue
            async for changes in awatch(watch_dir, recursive=False):
                if backend_watcher_events_total is not None:
                    backend_watcher_events_total.labels(**_LABELS, watcher="mcp").inc()
                for _, path in changes:
                    if os.path.abspath(path) == os.path.abspath(MCP_CONFIG_PATH):
                        # Load into a temp and only swap on success. A
                        # malformed edit must not wipe the live server set —
                        # mirrors ``hooks_config_watcher`` (#591). The swap
                        # itself is serialised by ``_swap_mcp_servers``
                        # under the reload lock (#1051), so a rapid burst
                        # of file events can't interleave parses and
                        # publish an older parse after a newer one.
                        try:
                            new_servers = await asyncio.to_thread(_load_mcp_config)
                        except Exception as exc:
                            logger.warning(
                                "MCP config reload failed — keeping previous servers: %s",
                                exc,
                            )
                            break
                        await self._swap_mcp_servers(new_servers, source="watcher")
                        if backend_mcp_config_reloads_total is not None:
                            backend_mcp_config_reloads_total.labels(**_LABELS).inc()
                        break
            logger.warning("MCP config directory watcher exited — retrying in 10s.")
            if backend_file_watcher_restarts_total is not None:
                backend_file_watcher_restarts_total.labels(**_LABELS, watcher="mcp").inc()
            await asyncio.sleep(10)

    async def agent_md_watcher(self) -> None:
        """Watch AGENT_MD for changes and hot-reload agent identity / behavioral instructions (#371).

        This ensures that updating CLAUDE.md does not require a container restart,
        consistent with all other file-based configuration in the platform.
        """
        # Perform an initial load so the watcher starts with current content.
        # Skipped when perform_initial_loads already ran on startup (#869).
        if not getattr(self, "_initial_agent_md_loaded", False):
            self._agent_md_content = _load_agent_md()
            previous = self._agent_md_revision
            current = _compute_agent_md_revision(self._agent_md_content)
            if current != previous:
                self._agent_md_revision = current
                self._stamp_agent_md_revision(current, previous=previous)
            logger.info("CLAUDE.md loaded from %s", AGENT_MD)

        watch_dir = os.path.dirname(os.path.abspath(AGENT_MD))
        while True:
            if not os.path.isdir(watch_dir):
                logger.info("CLAUDE.md directory not found — retrying in 10s.")
                await asyncio.sleep(10)
                continue
            async for changes in awatch(watch_dir, recursive=False):
                if backend_watcher_events_total is not None:
                    backend_watcher_events_total.labels(**_LABELS, watcher="agent_md").inc()
                for _, path in changes:
                    if os.path.abspath(path) == os.path.abspath(AGENT_MD):
                        self._agent_md_content = _load_agent_md()
                        previous = self._agent_md_revision
                        current = _compute_agent_md_revision(self._agent_md_content)
                        if current != previous:
                            self._agent_md_revision = current
                            self._stamp_agent_md_revision(current, previous=previous)
                        logger.info("CLAUDE.md reloaded from %s", AGENT_MD)
                        break
            logger.warning("CLAUDE.md directory watcher exited — retrying in 10s.")
            if backend_file_watcher_restarts_total is not None:
                backend_file_watcher_restarts_total.labels(**_LABELS, watcher="agent_md").inc()
            await asyncio.sleep(10)

    async def hooks_config_watcher(self) -> None:
        """Watch hooks.yaml and hot-reload extension rules (#467).

        Mirrors ``mcp_config_watcher`` — initial load, then awatch over the
        containing directory, re-parsing on every change to the target file.
        Failures during reload keep the previous rule set in place so a
        malformed edit cannot accidentally disable the policy.
        """
        # Skipped when perform_initial_loads already ran on startup (#869).
        if not getattr(self, "_initial_hooks_loaded", False):
            _initial_ext = await asyncio.to_thread(load_hooks_config_sync)
            # #1488: swap under the hook-state lock (see __init__).
            with self._hook_state_lock:
                self._hook_state.extensions = _initial_ext
            if backend_hooks_active_rules is not None:
                backend_hooks_active_rules.labels(**_LABELS, source="extension").set(len(self._hook_state.extensions))
            self._refresh_hook_enforcement_mode_metric()
            logger.info(
                "Hooks config loaded: baseline=%s (rules=%d) extensions=%d",
                self._hook_state.baseline_enabled,
                len(self._hook_state.baseline),
                len(self._hook_state.extensions),
            )

        watch_dir = os.path.dirname(os.path.abspath(HOOKS_CONFIG_PATH))
        while True:
            if not os.path.isdir(watch_dir):
                logger.info("hooks.yaml directory not found — retrying in 10s.")
                await asyncio.sleep(10)
                continue
            async for changes in awatch(watch_dir, recursive=False):
                if backend_watcher_events_total is not None:
                    backend_watcher_events_total.labels(**_LABELS, watcher="hooks").inc()
                for _, path in changes:
                    if os.path.abspath(path) == os.path.abspath(HOOKS_CONFIG_PATH):
                        try:
                            new_rules = await asyncio.to_thread(load_hooks_config_sync)
                            # #1488: serialise swap against readers.
                            with self._hook_state_lock:
                                self._hook_state.extensions = new_rules
                            if backend_hooks_active_rules is not None:
                                backend_hooks_active_rules.labels(**_LABELS, source="extension").set(len(new_rules))
                            self._refresh_hook_enforcement_mode_metric()
                            if backend_hooks_config_reloads_total is not None:
                                backend_hooks_config_reloads_total.labels(**_LABELS).inc()
                            logger.info("hooks.yaml reloaded: extensions=%d", len(new_rules))
                        except Exception as exc:
                            logger.warning("hooks.yaml reload failed — keeping previous rules: %s", exc)
                            if backend_hooks_config_errors_total is not None:
                                backend_hooks_config_errors_total.labels(
                                    **_LABELS,
                                    reason="yaml_reload_failed",
                                ).inc()
                        break
            logger.warning("hooks.yaml directory watcher exited — retrying in 10s.")
            if backend_file_watcher_restarts_total is not None:
                backend_file_watcher_restarts_total.labels(**_LABELS, watcher="hooks").inc()
            await asyncio.sleep(10)

    async def settings_watcher(self) -> None:
        """Watch .claude/settings.json and hot-reload ``ALLOWED_TOOLS`` (#717).

        Mirrors ``hooks_config_watcher`` — initial pass is a no-op (the
        module-level resolution already ran at import) and subsequent edits
        re-run ``_load_allowed_from_settings`` and mutate ``ALLOWED_TOOLS``
        in place so subsequent calls to ``_make_options`` pick up the new
        value without restarting the container.

        When ``ALLOWED_TOOLS`` was resolved from the env var at import time,
        edits to settings.json are ignored (the documented "env always wins"
        contract) — the watcher logs one line explaining why and otherwise
        stays quiet.  Malformed JSON keeps the prior list in place;
        ``_load_allowed_from_settings`` already logs and returns ``None`` in
        that case.
        """
        # MUST be declared at the top of the function scope, before any
        # read of ``ALLOWED_TOOLS`` in the diff/apply blocks below
        # (Python: "name used prior to global declaration" SyntaxError
        # otherwise). Required because we rebind the name on reload at
        # line ~2546 after #1491; without the hoist, the earlier reads
        # bind ALLOWED_TOOLS as a local and the module fails to parse,
        # crash-looping the pod. See #1491 regression.
        global ALLOWED_TOOLS
        if _tools_source == "env":
            logger.info(
                "settings_watcher: ALLOWED_TOOLS env var is set — settings.json edits will be ignored "
                "until the env var is unset (#717)."
            )
            return

        watch_dir = os.path.dirname(os.path.abspath(_SETTINGS_PATH))
        while True:
            if not os.path.isdir(watch_dir):
                logger.info("settings.json directory not found — retrying in 10s.")
                await asyncio.sleep(10)
                continue
            async for changes in awatch(watch_dir, recursive=False):
                if backend_watcher_events_total is not None:
                    backend_watcher_events_total.labels(**_LABELS, watcher="settings").inc()
                for _, path in changes:
                    if os.path.abspath(path) == os.path.abspath(_SETTINGS_PATH):
                        try:
                            new_allow = await asyncio.to_thread(
                                _load_allowed_from_settings,
                                _SETTINGS_PATH,
                            )
                        except Exception as exc:
                            logger.warning(
                                "settings.json reload raised — keeping previous ALLOWED_TOOLS: %s",
                                exc,
                            )
                            break
                        if new_allow is None:
                            # File disappeared or permissions.allow removed —
                            # fall back to the built-in default so a bad edit
                            # never silently widens the permission set.
                            new_list = [t.strip() for t in _DEFAULT_TOOLS.split(",") if t.strip()]
                        else:
                            new_list = new_allow
                        if new_list != ALLOWED_TOOLS:
                            old_set = set(ALLOWED_TOOLS)
                            new_set = set(new_list)
                            if new_set < old_set:
                                direction = "tighten"
                            elif new_set > old_set:
                                direction = "widen"
                            elif new_set == old_set:
                                # Same membership, only list order changed.
                                # Report this distinctly so 'rotate' retains
                                # its meaning of "set differs but is neither a
                                # strict subset nor superset" (#979).
                                direction = "reorder"
                            else:
                                direction = "rotate"
                            # Snapshot active session count BEFORE mutation so
                            # the metric value reflects the population still
                            # holding the old permission set (#934).
                            _active_at_reload = len(self._sessions) if hasattr(self, "_sessions") else 0
                            # #1491: rebind the module-level name to a new
                            # list instead of mutating in place. Slice
                            # assignment (ALLOWED_TOOLS[:] = new_list)
                            # briefly produces a list of the new length
                            # with old-position values remaining for the
                            # trailing / leading slots during the copy; a
                            # concurrent _make_options read could observe
                            # that transient state. Reference reassignment
                            # is atomic in CPython so readers always see
                            # the old or new list in full. The `global`
                            # declaration that authorises this rebind
                            # is at the top of settings_watcher above.
                            ALLOWED_TOOLS = list(new_list)
                            try:
                                from metrics import backend_allowed_tools_reload_total as _bar

                                if _bar is not None:
                                    _bar.labels(**_LABELS, direction=direction).inc()
                            except Exception:
                                pass
                            logger.info(
                                "settings.json reloaded: ALLOWED_TOOLS -> %s (direction=%s, %d active sessions still on old set; takes effect on next session).",  # noqa: E501
                                ",".join(ALLOWED_TOOLS),
                                direction,
                                _active_at_reload,
                            )
                        break
            logger.warning("settings.json directory watcher exited — retrying in 10s.")
            if backend_file_watcher_restarts_total is not None:
                backend_file_watcher_restarts_total.labels(**_LABELS, watcher="settings").inc()
            await asyncio.sleep(10)

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        """Run one A2A request through the Claude SDK and reply on ``event_queue``.

        Validation gates run before any subprocess is spawned or token is
        burned: empty / whitespace-only prompts are rejected with a
        ``backend_empty_prompts_total`` bump, an A2A error message, and a
        ``"system"`` row in the conversation log (#544); prompts larger
        than ``_MAX_PROMPT_BYTES`` are rejected with
        ``backend_prompt_too_large_total`` and a ``PromptTooLargeError``
        (#1620). The session id is derived through ``session_binding``
        with optional per-caller binding when ``SESSION_ID_SECRET`` is set
        (#710) and probe-list rotation when ``SESSION_ID_SECRET_PREV`` is
        also set so an in-progress secret rotation can still resume an
        existing on-disk session (#1042). When the upstream harness
        forwards a ``traceparent`` in ``message.metadata`` the OTel server
        span ``claude.execute`` continues that trace (#469).

        Streaming: each ``TextBlock`` is forwarded to the per-session SSE
        broadcaster (``session_stream``) via ``_emit_chunk`` so the
        dashboard's session drill-down sees text as it arrives (#1110
        phase 4 / #1139 monotonic turn-seq). Per-chunk Message events on
        the A2A wire are intentionally NOT emitted because the A2A SDK's
        ``ResultAggregator`` treats every ``Message`` as terminal — only
        one final aggregated ``new_agent_text_message`` is enqueued at
        completion. A terminal ``final=True`` session-stream chunk is
        published on both success and error paths (#1141) so observers
        see a deterministic turn boundary.

        Per-task bookkeeping: when ``context.task_id`` is set the current
        asyncio task is registered in ``self._running_tasks`` (and
        ``backend_running_tasks`` bumped) on entry, then removed in the
        ``finally`` block alongside the request-duration / last-request
        timestamp / requests-total metric updates. This lets ``cancel()``
        and ``close()`` find and drain in-flight requests.
        """
        _exec_start = time.monotonic()
        prompt = context.get_user_input()
        metadata = context.message.metadata or {}
        # Empty-prompt guard (#544). A metadata-only or whitespace-only A2A
        # message would otherwise flow into run() -> ClaudeSDKClient.query("")
        # and spawn a subprocess / burn tokens on a "How can I help?" reply.
        # Reject it here with an explicit A2A error event, a counter bump, and
        # a conversation log entry so the occurrence is visible in both
        # metrics and the session transcript. Mirrors the early-validation
        # pattern used for max_tokens further down.
        if not prompt or not prompt.strip():
            _empty_sid_raw = str(
                context.context_id or (context.message.metadata or {}).get("session_id") or ""
            ).strip()[:256]
            _empty_sid = "".join(c for c in _empty_sid_raw if c >= " ") or "unknown"
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
        # Claude SDK so pathological callers cannot OOM the pod with an
        # oversized A2A payload. Mirrors OpenAI/Gemini/Codex behavior.
        _prompt_bytes = len(prompt.encode("utf-8"))
        if _prompt_bytes > _MAX_PROMPT_BYTES:
            _too_large_sid_raw = str(
                context.context_id or (context.message.metadata or {}).get("session_id") or ""
            ).strip()[:256]
            _too_large_sid = "".join(c for c in _too_large_sid_raw if c >= " ") or "unknown"
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
        # OTel server span continuation (#469). Upstream harness echoes the
        # traceparent into metadata; when OTel is on we use it as the parent
        # context so the backend span joins the end-to-end trace.
        from otel import extract_otel_context as _extract_ctx

        _tp = metadata.get("traceparent") if isinstance(metadata.get("traceparent"), str) else None
        _otel_parent = _extract_ctx({"traceparent": _tp}) if _tp else None
        _raw_sid = "".join(
            c for c in str(context.context_id or metadata.get("session_id") or "").strip()[:256] if c >= " "
        )
        # Per-caller session_id binding (#710). When SESSION_ID_SECRET is
        # set on this backend and the upstream harness stamps
        # metadata.caller_id (a principal fingerprint), the shared helper
        # namespaces the derived session_id per caller so a second
        # principal observing another's raw id cannot address the same
        # persisted session. Backward compatible: when the env var is
        # unset the derivation is identical to the legacy uuid5 path.
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
        # Probe-list rotation (#1042). When SESSION_ID_SECRET_PREV is set
        # we compute candidate ids under both the current and previous
        # secret. If an on-disk session file exists under the previous
        # candidate, route this request to the old id so mid-rotation
        # resume still works. New sessions always land on candidates[0].
        _sid_candidates = _derive_session_id_candidates(_raw_sid, caller_identity=_caller_id)
        session_id = _sid_candidates[0]
        if len(_sid_candidates) > 1:
            for _prev_sid in _sid_candidates[1:]:
                if await asyncio.to_thread(_session_file_exists, _prev_sid):
                    session_id = _prev_sid
                    _note_prev_secret_hit(_raw_sid)
                    break
        # Retained for call sites that don't need rotation awareness.
        _ = _derive_session_id  # noqa: F841
        model = metadata.get("model") or None
        _max_tokens_raw = metadata.get("max_tokens")
        max_tokens: int | None = None
        if _max_tokens_raw is not None:
            try:
                _parsed = int(_max_tokens_raw)
                if _parsed <= 0:
                    logger.warning(f"Session {session_id!r}: max_tokens={_parsed} is non-positive; ignoring (#428).")
                else:
                    max_tokens = _parsed
            except (ValueError, TypeError):
                logger.warning(f"Session {session_id!r}: invalid max_tokens in metadata {_max_tokens_raw!r}, ignoring.")
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
        # Streaming bridge (#430): forward each TextBlock to the A2A event_queue
        # as it arrives. Tracks emission count so the post-completion aggregated
        # enqueue can be skipped when chunks were already delivered (avoids
        # duplicate text on the wire).
        _chunks_emitted = 0
        # Bound the `model` label for streaming metrics to the same allow-pattern
        # applied to _sdk_labels (#601) so `metadata.model` cannot inflate
        # Prometheus cardinality via the streaming code path.
        _streaming_label_model = sanitize_model_label(model or CLAUDE_MODEL)

        # Per-session SSE drill-down stream (#1110 phase 4). Resolve the
        # broadcaster and reset the assistant seq at the start of this
        # turn; emit a user chunk first so observers see the prompt
        # alongside the assistant chunks.
        try:
            from session_stream import get_session_stream as _get_session_stream

            _sess_stream = _get_session_stream(session_id, agent_id=AGENT_OWNER)
            # Per-turn seq covers user+assistant chunks (#1139) so
            # observers see monotonic seq across roles; the user chunk
            # consumes seq=0 and the first assistant chunk gets seq=1+.
            _sess_stream.reset_turn_seq()
            _sess_stream.publish_chunk(
                role="user",
                seq=_sess_stream.next_turn_seq(),
                content=prompt,
                final=True,
            )
        except Exception as _sess_exc:  # pragma: no cover — best-effort
            logger.warning("session_stream: user prompt publish failed: %r", _sess_exc)
            _sess_stream = None

        async def _emit_chunk(text: str) -> None:
            nonlocal _chunks_emitted
            _chunks_emitted += 1
            if backend_streaming_events_emitted_total is not None:
                backend_streaming_events_emitted_total.labels(**_LABELS, model=_streaming_label_model).inc()
            # Per-session SSE drill-down (#1110 phase 4). Best-effort —
            # a broadcaster fault must not break the A2A response path.
            if _sess_stream is not None:
                try:
                    _sess_stream.publish_chunk(
                        role="assistant",
                        seq=_sess_stream.next_turn_seq(),
                        content=text,
                        final=False,
                    )
                except Exception as _s_exc:  # pragma: no cover
                    logger.warning("session_stream: chunk publish failed: %r", _s_exc)
            # Per-chunk A2A event_queue emission removed (was: #430).
            # The A2A SDK's ResultAggregator.consume_and_break_on_interrupt
            # treats every Message event as terminal and returns on the
            # first one (a2a/server/tasks/result_aggregator.py around the
            # `if isinstance(event, Message): return event` branch).
            # Per-chunk Message emission therefore caused blocking
            # callers (every consumer in this repo today — dashboard's
            # useChat.ts and `ww send` both call message/send blocking)
            # to receive only the first chunk while the rest of the
            # agent's turns silently completed in the background.
            # Per-chunk visibility for the dashboard's session
            # drill-down is preserved via _sess_stream above — that's a
            # separate SSE channel (#1110 phase 4) and a different
            # transport from A2A's message/stream. If a real
            # message/stream consumer ever appears, revisit by emitting
            # chunk events as TaskStatusUpdateEvent (which the SDK
            # consumer doesn't treat as terminal) plus one final Message
            # at the end.

        from otel import set_span_error as _set_span_error
        from otel import start_span as _start_span

        try:
            with _start_span(
                "claude.execute",
                kind="server",
                parent_context=_otel_parent,
                attributes={
                    "a2.session_id": session_id,
                    "a2.model": model or CLAUDE_MODEL or "",
                    "a2.agent": AGENT_NAME,
                    "a2.agent_id": AGENT_ID,
                },
            ) as _otel_span:
                try:
                    _response = await run(
                        prompt,
                        session_id,
                        self._sessions,
                        self._mcp_servers,
                        self._agent_md_content,
                        model=model,
                        max_tokens=max_tokens,
                        on_chunk=_emit_chunk,
                        hook_state=self._hook_state,
                    )
                    _success = True
                    # Always emit the final aggregated Message event (was
                    # gated on _chunks_emitted == 0 prior to the per-chunk
                    # removal above). With the per-chunk path gone there's
                    # no duplicate-text risk; this is the ONLY Message
                    # event blocking callers see, so it must always fire
                    # when text was produced.
                    if _response:
                        await event_queue.enqueue_event(new_agent_text_message(_response))
                    # Per-session stream: publish a final assistant chunk
                    # marker so observers know the turn completed (#1110
                    # phase 4).  Best-effort.
                    if _sess_stream is not None:
                        try:
                            _sess_stream.publish_chunk(
                                role="assistant",
                                seq=_sess_stream.next_turn_seq(),
                                content="",
                                final=True,
                            )
                        except Exception as _f_exc:  # pragma: no cover
                            logger.warning("session_stream: final chunk publish failed: %r", _f_exc)
                    if backend_a2a_requests_total is not None:
                        backend_a2a_requests_total.labels(**_LABELS, status="success").inc()
                except Exception as _exc:
                    _error = repr(_exc)
                    if backend_a2a_requests_total is not None:
                        backend_a2a_requests_total.labels(**_LABELS, status="error").inc()
                    _set_span_error(_otel_span, _exc)
                    # Terminal marker so partial assistant text already in
                    # conversation.jsonl is demarcated on mid-stream failure
                    # (#566). log_entry swallows its own exceptions, so this
                    # cannot mask the original error being re-raised below.
                    await log_entry(
                        "system",
                        f"{type(_exc).__name__}: {_exc}",
                        session_id,
                        model=model,
                    )
                    # Per-session stream: emit a final=True assistant chunk
                    # on the failure path too (#1141) so observers see a
                    # deterministic turn boundary even when execution blew
                    # up mid-stream.  Best-effort.
                    if _sess_stream is not None:
                        try:
                            _sess_stream.publish_chunk(
                                role="assistant",
                                seq=_sess_stream.next_turn_seq(),
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

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        """Cancel the in-flight ``execute()`` asyncio task for an A2A ``task_id``.

        Increments ``backend_task_cancellations_total`` unconditionally so
        cancellation attempts are visible in metrics even when no matching
        task is tracked. Looks up the task by ``context.task_id`` in
        ``self._running_tasks`` (the same map populated on
        ``execute()`` entry); when present, calls ``task.cancel()`` and
        logs the request. When absent, logs that no running task was
        found and returns without raising — common when the task already
        completed or was cancelled by another path (e.g. ``close()``).
        ``event_queue`` is accepted for the A2A protocol signature but is
        not used; the running execute()'s own ``finally`` block emits any
        terminal A2A events as ``CancelledError`` propagates out.
        """
        if backend_task_cancellations_total is not None:
            backend_task_cancellations_total.labels(**_LABELS).inc()
        task_id = context.task_id
        task = self._running_tasks.get(task_id) if task_id else None
        if task:
            task.cancel()
            logger.info(f"Task {task_id!r} cancellation requested.")
        else:
            logger.info(f"Task {task_id!r} cancellation requested but no running task found.")
