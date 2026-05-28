import asyncio
import hashlib
import json
import logging
import os
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from contextlib import AsyncExitStack
from datetime import datetime, timezone
from typing import Any

from a2a.server.agent_execution import AgentExecutor as A2AAgentExecutor
from a2a.server.agent_execution import RequestContext
from a2a.server.events import EventQueue
from a2a.utils import new_agent_text_message
from google import genai
from google.genai import types

# Pin guard (#737). google-genai only started attaching stable ``id``
# attributes to function_call / function_response Parts in 1.73.x; on
# older releases the AFC pairing in ``_emit_afc_history`` falls back to
# FIFO-by-content-index, which can mispair parallel tool calls that
# share a tool name and pollute ``backend_sdk_tool_duration_seconds``.
#
# ``requirements.txt`` pins ``google-genai==1.73.1`` — this module-load
# assertion makes a drift by an operator (``pip install --upgrade`` in
# a dev shell, a forgotten Dockerfile bump) surface as a loud warning
# rather than silently regressing pairing accuracy.  We do not raise —
# the FIFO fallback still produces correct totals, only individual
# duration samples risk mislabelling.
_MIN_GENAI_VERSION = "1.73.0"
try:
    from importlib.metadata import version as _pkg_version

    _genai_version = _pkg_version("google-genai")
except Exception as _exc:  # pragma: no cover - metadata access failure
    _genai_version = None
    logger_early = logging.getLogger(__name__)
    logger_early.warning(
        "google-genai package metadata unavailable (%s) — AFC id-based pairing check skipped.",
        _exc,
    )


def _parse_version(v: str) -> tuple[int, ...]:
    out: list[int] = []
    for piece in v.split("."):
        try:
            out.append(int(piece.split("+", 1)[0].split("-", 1)[0]))
        except ValueError:
            break
    return tuple(out)


if _genai_version is not None:
    _cur = _parse_version(_genai_version)
    _min = _parse_version(_MIN_GENAI_VERSION)
    if _cur < _min:
        logging.getLogger(__name__).warning(
            "google-genai %s < %s: function_call/response Parts may lack stable ids; "
            "AFC metric pairing will fall back to FIFO and can mispair parallel "
            "calls that share a tool name (#737). Pin google-genai>=%s in requirements.txt.",
            _genai_version,
            _MIN_GENAI_VERSION,
            _MIN_GENAI_VERSION,
        )
from exceptions import BudgetExceededError, PromptTooLargeError

# Hooks engine facade (#631). Imported even though the gemini tool-call path
# is not wired yet (tracked by #1863) — this lands the infrastructure so the
# executor can register the hooks_config_watcher alongside existing watchers
# and the eventual tool-loop interposition can drop `evaluate_pre_tool_use`
# in without further plumbing.
from hooks import (
    BASELINE_RULES,
    HOOKS_BASELINE_ENABLED,
    HOOKS_CONFIG_PATH,
    HookState,
    load_hooks_config_sync,
)
from log_utils import _append_log
from metrics import (
    backend_a2a_last_request_timestamp_seconds,
    backend_a2a_request_duration_seconds,
    backend_a2a_requests_total,
    backend_active_sessions,
    backend_agent_md_revision,
    backend_allowed_tools_reload_total,
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
    backend_history_force_split_total,
    backend_hooks_config_errors_total,
    backend_hooks_config_reloads_total,
    backend_hooks_enforcement_mode,
    backend_hooks_evaluations_total,
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
    backend_mcp_server_exits_total,
    backend_mcp_server_up,
    backend_mcp_servers_active,
    backend_model_requests_total,
    backend_prompt_length_bytes,
    backend_prompt_too_large_total,
    backend_response_length_bytes,
    backend_running_tasks,
    backend_sdk_client_errors_total,
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
    backend_sdk_tool_calls_total,
    backend_sdk_tool_duration_seconds,
    backend_sdk_tool_errors_total,
    backend_sdk_tool_result_size_bytes,
    backend_sdk_turns_per_query,
    backend_session_age_seconds,
    backend_session_evictions_total,
    backend_session_history_reset_total,
    backend_session_history_save_errors_total,
    backend_session_idle_seconds,
    backend_session_starts_total,
    backend_streaming_events_emitted_total,
    backend_task_cancellations_total,
    backend_task_duration_seconds,
    backend_task_error_duration_seconds,
    backend_task_last_error_timestamp_seconds,
    backend_task_last_success_timestamp_seconds,
    backend_task_timeout_headroom_seconds,
    backend_tasks_total,
    backend_text_blocks_per_query,
    backend_tool_audit_bytes_per_entry,
    backend_tool_audit_entries_total,
    backend_tool_audit_rotation_pressure_total,
    backend_watcher_events_total,
)
from otel import set_span_error, start_span
from redact import redact_text, should_redact

# Shared PostToolUse audit helper (#809). Parity with codex's
# _append_tool_audit path — emits event_type='tool_audit' rows into
# tool-activity.jsonl so the dashboard Tool Trace tab sees gemini tool
# invocations alongside claude/codex.
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


def _pre_tool_use_gate(
    tool_name: str,
    tool_input: dict,
    *,
    state: "HookState | None" = None,
) -> tuple[str, str] | None:
    """PreToolUse gate for gemini tool calls (#1863, SCAFFOLD).

    Centralises the ``hooks_engine.evaluate_pre_tool_use`` call so a future
    AFC-off path can invoke deny/warn evaluation between the SDK's
    ``function_call`` emission and ``ClientSession.call_tool``. Returns
    ``None`` on allow, ``(rule_name, reason)`` on deny.

    STATUS — scaffold only. The google-genai SDK's Automatic Function
    Calling (AFC) runs the tool ping-pong inside ``generate_content`` /
    ``send_message_stream``; there is no pre-invoke callback exposed, so
    this helper cannot be wired into the live tool path yet. Fully closing
    #1863 requires either:

      1. Disabling AFC and hand-rolling the ``function_call`` /
         ``function_response`` loop so there is an
         interposition point, or
      2. A wrapper around ``ClientSession.call_tool`` that every gemini
         MCP invocation must route through — non-trivial because AFC owns
         the dispatch.

    Until one of those lands, this helper exists so the evaluate site is
    consistent across backends (codex has the same-shaped scaffold from
    #1862). Baseline rules and hooks.yaml extensions continue to load via
    ``hooks_config_watcher`` so ``self._hook_state`` is always current;
    wiring is the only missing piece.

    TODO(#1863): switch to AFC-off + hand-rolled tool loop, call this gate
    between SDK emission and ``ClientSession.call_tool``, and add coverage
    under ``backends/gemini/tests/``.
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
    # #1724: align with the shared-engine contract documented in
    # shared/hooks_engine.py — evaluate_pre_tool_use takes the rule list
    # positionally and returns ``(decision: str, matched_rule: Rule | None)``.
    # Mirrors the codex #1194 fix. Flatten HookState into its active rules
    # via the canonical helper so the baseline_enabled flag is honoured.
    if state is not None:
        try:
            _rules = state.active_rules()
        except Exception:
            _rules = []
    else:
        _rules = []
    try:
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


logger = logging.getLogger(__name__)


AGENT_NAME = os.environ.get("AGENT_NAME", "gemini")
AGENT_OWNER = os.environ.get("AGENT_OWNER") or AGENT_NAME
AGENT_ID = os.environ.get("AGENT_ID", "gemini")

# Backend→harness generic event channel (#1110 phase 3). Import lazily
# to tolerate environments where PYTHONPATH is not set up for the
# shared/ mount yet. Emit sites wrap in try/except and never let
# emission failure propagate.
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


CONVERSATION_LOG = os.environ.get("CONVERSATION_LOG", "/home/agent/logs/conversation.jsonl")
TRACE_LOG = os.environ.get("TRACE_LOG", "/home/agent/logs/tool-activity.jsonl")
AGENT_MD = "/home/agent/.gemini/GEMINI.md"
SESSION_STORE_DIR = os.environ.get("SESSION_STORE_DIR", "/home/agent/.gemini/memory/sessions")

# Ensure the sessions directory exists once at module load time rather than
# on every _session_path() call.  This eliminates a redundant os.makedirs
# syscall on the hot path for every prompt (see #320).
try:
    os.makedirs(SESSION_STORE_DIR, exist_ok=True)
except OSError:
    pass  # read-only or not yet mounted — will fail naturally on first write

MAX_SESSIONS = max(1, int(os.environ.get("MAX_SESSIONS", "10000")))  # #1718, mirrors codex #1629
TASK_TIMEOUT_SECONDS = int(os.environ.get("TASK_TIMEOUT_SECONDS", "300"))
# Maximum number of bytes of prompt text included in INFO-level log messages.
# Set to 0 to suppress prompt text from logs entirely; set higher for more context.
LOG_PROMPT_MAX_BYTES = int(os.environ.get("LOG_PROMPT_MAX_BYTES", "200"))
# Hard cap on inbound prompt UTF-8 byte length (#1620 / #1730). A
# pathological caller could otherwise ship a multi-GB prompt and OOM
# the pod before the google-genai SDK ever sees it. Default 10 MiB is
# comfortably above any legitimate conversational use; bump via
# MAX_PROMPT_BYTES env when an operator deliberately wants larger
# inputs (e.g. document ingest jobs). Mirrors codex's _MAX_PROMPT_BYTES
# so the cross-backend backend_prompt_too_large_total dashboard
# series is meaningful for every backend.
_MAX_PROMPT_BYTES = int(os.environ.get("MAX_PROMPT_BYTES", str(10 * 1024 * 1024)))

GEMINI_MODEL = os.environ.get("GEMINI_MODEL") or "gemini-2.5-pro"
GEMINI_API_KEY: str | None = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY") or None

MCP_CONFIG_PATH = os.environ.get("MCP_CONFIG_PATH", "/home/agent/.gemini/mcp.json")

# #1610: Validate the resolved MCP_CONFIG_PATH lives under a documented prefix
# so a hostile env override (e.g. ``MCP_CONFIG_PATH=/etc/passwd``) cannot be
# fed to ``json.load`` -- the parse error path would then leak file shape /
# perms via metric label cardinality and log noise. The default mirrors the
# in-container home for backend pods; tests / non-default deployments can
# override via ``MCP_CONFIG_PATH_ALLOWED_PREFIX``.
_MCP_CONFIG_PATH_ALLOWED_PREFIX = os.environ.get(
    "MCP_CONFIG_PATH_ALLOWED_PREFIX",
    "/home/agent/",
)

# #1100: scaffold for the eventual gemini allow-list enforcement. The
# AFC tool loop inside send_message_stream today runs every bound tool
# without consulting a deny/allow surface; once #1863 interposes on the
# loop, this parsed list will gate each tool call. Landing the env var
# + metric now means dashboards already have series registered when the
# enforcement flips on, avoiding a churn wave.
#
# Semantics:
#   * Unset / empty => no restriction (current behaviour; AFC unchanged).
#   * Comma-separated list => only named tools are permitted.
#
# Exposed as a module-level list so a future ``settings_watcher`` can
# mutate it in place and bump ``backend_allowed_tools_reload_total``.
_ALLOWED_TOOLS_ENV = os.environ.get("ALLOWED_TOOLS")
ALLOWED_TOOLS: list[str] = [t.strip() for t in _ALLOWED_TOOLS_ENV.split(",") if t.strip()] if _ALLOWED_TOOLS_ENV else []


def _bump_allowed_tools_reload(direction: str) -> None:
    """Bump backend_allowed_tools_reload_total (#1100).

    Safe to call before the metric is registered (labels() returns ``None``
    when the Counter is still ``None``). ``direction`` is one of
    ``initial|tighten|widen|rotate`` to match claude's label schema.
    """
    if backend_allowed_tools_reload_total is None:
        return
    try:
        backend_allowed_tools_reload_total.labels(**_LABELS, direction=direction).inc()
    except Exception:
        pass


# Env var keys that must not be overridden by caller-supplied MCP server env
# entries. Mirrors codex (#519): MCP stdio entries spawn a subprocess with
# identical code-injection risk so keep the denylist symmetric. gemini has
# no LocalShell path; this list is only used by ``_build_mcp_stdio_params``.
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

_BACKEND_ID = "gemini"
_LABELS = {"agent": AGENT_OWNER, "agent_id": AGENT_ID, "backend": _BACKEND_ID}

# Bounded allow-pattern for the Prometheus `model` label (#487, hoisted to
# ``shared/validation.py`` for reuse across backends in #601). User-supplied
# metadata.model flows through resolved_model into 12+ metric call sites; an
# unbounded string would let a caller blow up metric cardinality by sending a
# fresh UUID per request. The shared ``sanitize_model_label`` helper enforces
# the allow-pattern (alnum / dot / dash / underscore, length <= 64) and falls
# back to the literal "unknown". Keep the private ``_sanitize_model_label``
# alias so existing call sites stay on-pattern with other local helpers.
_sanitize_model_label = sanitize_model_label


def _load_agent_md() -> str:
    try:
        with open(AGENT_MD) as f:
            return f.read()
    except OSError:
        return ""


def _compute_agent_md_revision(content: str) -> str:
    """Return the SHA-256 hex prefix (first 12 chars) of GEMINI.md content (#1751).

    Used as the ``revision`` label on ``backend_agent_md_revision`` so
    operators can verify a hot-reload has propagated to running queries.
    Mirrors backends/openai/executor.py::_compute_agent_md_revision (#1097).
    """
    return hashlib.sha256(content.encode("utf-8", errors="replace")).hexdigest()[:12]


def _load_mcp_config() -> dict:
    """Load and normalise the MCP server config from MCP_CONFIG_PATH (#640).

    Accepts both the Claude-native shape (``{"mcpServers": {...}}``) and a
    flat ``{server_name: {...}}`` dict, returning the inner dict in both
    cases. Missing file is treated as "no MCP servers" (returns ``{}``).
    Parse / I/O errors return ``{}`` AND increment
    ``backend_mcp_config_errors_total``. Mirrors codex._load_mcp_config for
    parity across backends.

    Path posture (#1610): the resolved (``os.path.realpath``) MCP_CONFIG_PATH
    must live under ``MCP_CONFIG_PATH_ALLOWED_PREFIX`` (default
    ``/home/agent/``). This blocks a hostile env override that would point
    the loader at arbitrary files such as ``/etc/passwd``. Test / non-
    default deployments override the prefix via the
    ``MCP_CONFIG_PATH_ALLOWED_PREFIX`` env var. Out-of-prefix paths are
    skipped with a WARN log; missing-file semantics are unchanged.
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


# MCP stdio command+cwd allow-list (#730 — parity with claude #711 /
# codex #720). Every stdio command pushed into the gemini
# lifespan-scoped MCP stack is validated here first. Rejections are
# dropped and counted via backend_mcp_command_rejected_total{reason} so
# a mis-merged mcp.json can't reach google-genai AFC.
#
# #964: Command checking now lives in shared/mcp_command_allowlist.py
# so the three backends don't carry drifting forks (defaults, metric
# reasons, and the absolute-path fallback differed before — see #862).
# The cwd allow-list stays local until the shared module grows a
# counterpart.
from mcp_command_allowlist import (  # noqa: E402
    mcp_command_allowed as _gemini_mcp_command_allowed,
)
from mcp_command_allowlist import (
    mcp_command_args_safe as _gemini_mcp_command_args_safe,
)

_DEFAULT_GEMINI_MCP_ALLOWED_CWD_PREFIXES = "/home/agent/,/tmp/"
_GEMINI_MCP_ALLOWED_CWD_PREFIXES: tuple[str, ...] = tuple(
    t.strip()
    for t in os.environ.get(
        "MCP_ALLOWED_CWD_PREFIXES",
        _DEFAULT_GEMINI_MCP_ALLOWED_CWD_PREFIXES,
    ).split(",")
    if t.strip()
)


def _gemini_mcp_cwd_allowed(cwd: Any) -> tuple[bool, str]:
    """Return (ok, reason) for an MCP stdio ``cwd`` value (#730)."""
    if not isinstance(cwd, str):
        return False, "cwd_non_string"
    c = cwd.strip()
    if not c:
        return False, "cwd_empty"
    if not c.startswith("/"):
        return False, "cwd_not_absolute"
    for prefix in _GEMINI_MCP_ALLOWED_CWD_PREFIXES:
        if c.startswith(prefix):
            return True, "cwd_allowed"
    return False, "cwd_not_on_prefix"


def _build_mcp_stdio_params(name: str, cfg: dict) -> Any | None:
    """Construct an ``mcp.StdioServerParameters`` from a single config entry (#640).

    Applies the shared env denylist (#519) before passing ``env`` through to
    the subprocess so a malicious config cannot hijack dynamic-linker /
    interpreter resolution of the spawned MCP server. Returns ``None`` on
    malformed entries (logged and skipped by the caller).

    The command + cwd allow-list (#730) drops any entry whose ``command``
    falls outside ``MCP_ALLOWED_COMMANDS`` / ``MCP_ALLOWED_COMMAND_PREFIXES``
    or whose ``cwd`` isn't under ``MCP_ALLOWED_CWD_PREFIXES``. Rejected
    entries are counted in ``backend_mcp_command_rejected_total``.
    """
    try:
        from mcp import StdioServerParameters  # type: ignore
    except Exception as _imp_exc:
        logger.warning(
            "mcp package not available (%s); MCP support disabled.",
            _imp_exc,
        )
        return None
    if "command" not in cfg:
        logger.warning(
            "MCP server %r: missing 'command' (gemini only supports stdio transport via google-genai AFC); skipping.",
            name,
        )
        return None
    cmd_ok, cmd_reason = _gemini_mcp_command_allowed(cfg["command"])
    if not cmd_ok:
        logger.warning(
            "MCP server %r: command %r rejected by allow-list (%s) — "
            "dropping entry. Set MCP_ALLOWED_COMMANDS / "
            "MCP_ALLOWED_COMMAND_PREFIXES to widen. (#730)",
            name,
            cfg.get("command"),
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
        return None
    if "cwd" in cfg:
        cwd_ok, cwd_reason = _gemini_mcp_cwd_allowed(cfg["cwd"])
        if not cwd_ok:
            logger.warning(
                "MCP server %r: cwd %r rejected by allow-list (%s) — "
                "dropping entry. Set MCP_ALLOWED_CWD_PREFIXES to widen. (#730)",
                name,
                cfg.get("cwd"),
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
            return None
    # Args sanitiser (#1734 / #930). When ``command`` is an
    # interpreter (uv, uvx, python, node, …), its ``args`` array can
    # still deliver arbitrary code via -c / -e / positional script
    # paths. Drop the entry so a widened MCP_ALLOWED_COMMANDS doesn't
    # silently re-open the arbitrary-code-execution path the README
    # promises is closed.
    args_ok, args_reason = _gemini_mcp_command_args_safe(
        cfg["command"],
        cfg.get("args"),
    )
    if not args_ok:
        logger.warning(
            "MCP server %r: args for command %r rejected by args "
            "sanitiser (%s) — dropping entry. Adjust the config or "
            "set MCP_ALLOWED_CWD_PREFIXES if a positional script "
            "lives in an operator-vetted tree. (#1734)",
            name,
            cfg.get("command"),
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
        return None
    params_kwargs: dict = {"command": cfg["command"]}
    if "args" in cfg:
        params_kwargs["args"] = list(cfg["args"])
    if "env" in cfg:
        raw_env = dict(cfg["env"])
        sanitized_env = {k: v for k, v in raw_env.items() if k not in _SHELL_ENV_DENYLIST}
        rejected = set(raw_env) - set(sanitized_env)
        if rejected:
            logger.warning(
                "MCP server %r: stripped dangerous env vars from config env: %s",
                name,
                sorted(rejected),
            )
        params_kwargs["env"] = sanitized_env
    if "cwd" in cfg:
        params_kwargs["cwd"] = cfg["cwd"]
    try:
        return StdioServerParameters(**params_kwargs)
    except Exception as _e:
        logger.warning("MCP server %r: failed to build stdio params (%s); skipping.", name, _e)
        return None


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
    ``redact_text`` first when ``should_redact()`` returns true (#1193, parity
    with claude). When OTel is active the row is stamped with the current
    span's ``trace_id`` so conversation rows can be joined with backend /
    harness traces (#636). On success bumps ``backend_log_entries_total`` and
    ``backend_log_bytes_total`` under ``logger="conversation"``. Swallows all
    exceptions after incrementing ``backend_log_write_errors_total`` so a log
    failure never propagates back into the caller.
    """
    try:
        # Opt-in redaction pass (#1193, parity with claude). Guarded on
        # LOG_REDACT so existing deployments retain identical output
        # without the regex cost; operators flip the env var to take the
        # safer posture when conversation.jsonl is read by humans or
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
            backend_log_bytes_total.labels(**_LABELS, logger="conversation").inc(len(_line.encode()))
    except Exception as e:
        if backend_log_write_errors_total is not None:
            backend_log_write_errors_total.labels(**_LABELS).inc()
        logger.error(f"log_entry error: {e}")


def _to_jsonable(value: Any) -> Any:
    """Best-effort coercion of SDK objects into json-serialisable structures.

    Used when we extract ``function_call.args`` and ``function_response.response``
    off the google-genai AFC history — the SDK returns pydantic models or
    their raw proto mirrors. Falls back to ``repr`` so logging never crashes
    on an unexpected shape (#640).
    """
    try:
        if hasattr(value, "model_dump"):
            return value.model_dump(exclude_none=True)
    except Exception:
        pass
    if isinstance(value, dict):
        return {k: _to_jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_to_jsonable(v) for v in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    try:
        return repr(value)
    except Exception:
        return "<unrepr-able>"


async def _emit_afc_history(
    history: list,
    *,
    session_id: str,
    model: str | None,
    prefix_history: list | None = None,
) -> None:
    """Extract ``function_call`` / ``function_response`` parts from an AFC history
    and emit ``tool_use`` / ``tool_result`` trace rows + metrics (#640).

    google-genai's AFC appends both the user/assistant turns and the
    synthesised function_call / function_response turns to
    ``chat.history`` (and to ``response.automatic_function_calling_history``
    on non-streaming calls). This helper walks that flat list, pairs each
    ``function_call`` with the matching ``function_response`` by tool name,
    and writes one row per side into ``tool-activity.jsonl`` using the same shape
    claude uses so the dashboard TraceView (#592) and OTel trace viewer
    (#632) can render them uniformly.

    ``prefix_history`` (#996) seeds the pending-fc tables with
    function_call parts from turns *prior* to this slice — used on
    resumption paths where chats.create re-appends a persisted
    function_call that only now gets its function_response. No rows or
    metrics are emitted for the prefix; it exists purely so a fr in
    the current slice can pair with its fc instead of being mis-labelled
    via the cross-tool FIFO fallback (#887).

    Errors here are logged and swallowed — observability must never break
    the response path.
    """
    if not history and not prefix_history:
        return
    # Pairing strategy (#676):
    #   1. If both sides carry a matching id (newer google-genai), pair by id.
    #   2. Else fall back to content index — parallel calls from the same
    #      content Part block emit their function_response rows in the same
    #      index order, which is stable even when multiple in-flight calls
    #      share a tool name.
    pending_by_id: dict[str, dict] = {}
    # Tool-name-segregated FIFO (#887). Previously a single flat FIFO was
    # used across all tool names, so parallel calls with ids missing on
    # the response side would pop the oldest pending entry regardless of
    # name, mis-labelling tool_use_id + the duration metric's tool label.
    # Same-tool FIFO first; cross-tool fallback only when same-tool empty
    # (with a WARN so operators notice SDK-version drift).
    pending_by_name: dict[str, list[dict]] = {}
    # Secondary flat FIFO preserved only for cross-tool fallback ordering.
    pending_by_index: list[dict] = []
    call_counter = 0
    # #1510: separate counter for unpaired function_response rows. The
    # synthesized fr id previously reused call_counter (the fc counter),
    # so two unpaired fr entries within a turn collided on the same id
    # and broke dashboard joins / OTel dedup. Increment fr_counter only
    # for unpaired fr entries.
    fr_counter = 0
    # Coalesce cross-tool FIFO fallback warnings to one line per
    # _emit_afc_history invocation (#998). A multi-tool AFC turn with
    # SDK-version-dropped fr ids previously emitted one WARN per
    # response part, flooding logs while communicating nothing
    # actionable beyond "SDK drift is active this turn".
    _cross_tool_fallback_count = 0
    _cross_tool_fallback_example: tuple[str, str] | None = None

    # Seed pending tables from prefix_history (#996) so any unmatched
    # function_call from a prior turn can still pair with a
    # function_response that lands in this slice. Remove from the
    # pending tables any fc/fr that were already paired in the prefix;
    # we only want the unmatched tail.
    if prefix_history:
        # #1514: use a counter local to the prefix seed loop so it
        # cannot leak into the current-turn ``call_counter``. Previously
        # prefix fcs bumped ``call_counter`` without decrementing when a
        # paired fr removed them from pending, leaving the current-turn
        # synthesized ids starting at a non-contiguous offset and
        # breaking trace correlation across resume.
        _pre_call_counter = 0
        for _pre_content in prefix_history:
            _pre_parts = getattr(_pre_content, "parts", None) or []
            for _pre_part in _pre_parts:
                _pre_fc = getattr(_pre_part, "function_call", None)
                _pre_fr = getattr(_pre_part, "function_response", None)
                if _pre_fc is not None:
                    _pre_call_counter += 1
                    _pre_name = getattr(_pre_fc, "name", None) or "<unknown>"
                    _pre_fc_id = getattr(_pre_fc, "id", None)
                    _pre_call_id = _pre_fc_id or f"fc-{session_id[:8]}-pre-{_pre_call_counter}"
                    _pre_ts = datetime.now(timezone.utc).isoformat()
                    # #1727: mark prefix-sourced entries so the response side
                    # can skip the duration histogram observe — the original
                    # fc start time was lost across the persistence boundary,
                    # and computing a delta against the seed-loop "now"
                    # produces a near-zero sample that misrepresents real
                    # tool runtime.
                    _pre_entry = {
                        "id": _pre_call_id,
                        "ts": _pre_ts,
                        "name": _pre_name,
                        "fc_id": _pre_fc_id,
                        "from_prefix": True,
                    }
                    if _pre_fc_id:
                        pending_by_id[_pre_fc_id] = _pre_entry
                    pending_by_name.setdefault(_pre_name, []).append(_pre_entry)
                    pending_by_index.append(_pre_entry)
                elif _pre_fr is not None:
                    # Pair off a prefix fc with this prefix fr so it does
                    # not linger into the current slice's pending tables.
                    _pre_fr_id = getattr(_pre_fr, "id", None)
                    _pre_name = getattr(_pre_fr, "name", None) or "<unknown>"
                    _pre_matched = None
                    if _pre_fr_id and _pre_fr_id in pending_by_id:
                        _pre_matched = pending_by_id.pop(_pre_fr_id)
                    elif pending_by_name.get(_pre_name):
                        _pre_matched = pending_by_name[_pre_name].pop(0)
                        if _pre_matched.get("fc_id"):
                            pending_by_id.pop(_pre_matched["fc_id"], None)
                    if _pre_matched is not None:
                        try:
                            pending_by_index.remove(_pre_matched)
                        except ValueError:
                            pass
                        _q = pending_by_name.get(_pre_matched.get("name", ""))
                        if _q and _pre_matched in _q:
                            try:
                                _q.remove(_pre_matched)
                            except ValueError:
                                pass

    for content in history:
        parts = getattr(content, "parts", None) or []
        for part in parts:
            fc = getattr(part, "function_call", None)
            fr = getattr(part, "function_response", None)
            if fc is not None:
                call_counter += 1
                name = getattr(fc, "name", None) or "<unknown>"
                fc_id = getattr(fc, "id", None)
                # Gemini function_call objects don't carry a stable id on
                # older SDK releases; synthesise one so the matching
                # tool_result row can reference it.
                call_id = fc_id or f"fc-{session_id[:8]}-{call_counter}"
                args = getattr(fc, "args", None)
                ts = datetime.now(timezone.utc).isoformat()
                entry = {
                    "ts": ts,
                    "event_type": "tool_use",
                    "id": call_id,
                    "name": name,
                    "input": _to_jsonable(args) if args is not None else {},
                    "session_id": session_id,
                    "agent": AGENT_NAME,
                    "model": model,
                }
                _tid = _current_trace_id_hex()
                if _tid is not None:
                    entry["trace_id"] = _tid
                try:
                    await log_trace(json.dumps(entry))
                except Exception as _e:
                    logger.debug("AFC tool_use log failed: %s", _e)
                # Per-call input payload size (#811). Observed on the best
                # available proxy for payload bytes — JSON-encoded args
                # matches the byte count claude reports for its own
                # backend_sdk_tool_call_input_size_bytes.
                if backend_sdk_tool_call_input_size_bytes is not None:
                    try:
                        _input_bytes = len(
                            json.dumps(
                                entry["input"],
                                default=str,
                                ensure_ascii=False,
                            ).encode("utf-8")
                        )
                        backend_sdk_tool_call_input_size_bytes.labels(
                            **_LABELS,
                            tool=name,
                        ).observe(_input_bytes)
                    except Exception:
                        pass
                pending_entry = {"id": call_id, "ts": ts, "name": name, "fc_id": fc_id}
                if fc_id:
                    pending_by_id[fc_id] = pending_entry
                pending_by_name.setdefault(name, []).append(pending_entry)
                pending_by_index.append(pending_entry)
            elif fr is not None:
                name = getattr(fr, "name", None) or "<unknown>"
                response = getattr(fr, "response", None)
                is_error = False
                # Best-effort error detection: google-genai surfaces tool
                # errors as a dict with an ``error`` key on the response.
                response_j = _to_jsonable(response) if response is not None else None
                if isinstance(response_j, dict) and "error" in response_j:
                    is_error = True
                # Prefer the SDK-supplied id when both sides provide one;
                # fall back to content-order (FIFO index across all tools)
                # for older SDK releases where ids are absent.
                fr_id = getattr(fr, "id", None)
                matched = None
                if fr_id and fr_id in pending_by_id:
                    matched = pending_by_id.pop(fr_id)
                    try:
                        pending_by_index.remove(matched)
                    except ValueError:
                        pass
                    _same_name_q = pending_by_name.get(matched.get("name", ""))
                    if _same_name_q:
                        try:
                            _same_name_q.remove(matched)
                        except ValueError:
                            pass
                else:
                    # Same-tool FIFO first (#887). Only fall back to the
                    # cross-tool flat FIFO when this tool has no pending,
                    # and warn so a SDK-version drift that drops fr.id
                    # doesn't silently mis-label duration metrics.
                    _same_name_q = pending_by_name.get(name) or []
                    if _same_name_q:
                        matched = _same_name_q.pop(0)
                        try:
                            pending_by_index.remove(matched)
                        except ValueError:
                            pass
                        if matched.get("fc_id"):
                            pending_by_id.pop(matched["fc_id"], None)
                    elif pending_by_index:
                        matched = pending_by_index.pop(0)
                        if matched.get("fc_id"):
                            pending_by_id.pop(matched["fc_id"], None)
                        _xn = matched.get("name", "")
                        _xq = pending_by_name.get(_xn)
                        if _xq:
                            try:
                                _xq.remove(matched)
                            except ValueError:
                                pass
                        # #998: coalesce the cross-tool FIFO fallback
                        # warning to a single summary line per
                        # _emit_afc_history invocation. Record a
                        # representative (fr_name, matched_fc_name) pair
                        # the first time it fires so operators have
                        # enough detail to diagnose SDK drift without
                        # log spam.
                        _cross_tool_fallback_count += 1
                        if _cross_tool_fallback_example is None:
                            _cross_tool_fallback_example = (name, matched.get("name") or "")
                tool_use_id = matched["id"] if matched else None
                ts = datetime.now(timezone.utc).isoformat()
                # #1510: increment fr_counter only when the fr is
                # unpaired (no matched tool_use_id). Using the shared
                # call_counter here would collide on duplicate fr ids
                # when two unpaired fr entries land without an
                # intervening fc bump.
                if not tool_use_id:
                    fr_counter += 1
                entry = {
                    "ts": ts,
                    "event_type": "tool_result",
                    "id": f"{tool_use_id}-resp" if tool_use_id else f"fr-{session_id[:8]}-{fr_counter}",
                    "tool_use_id": tool_use_id,
                    "content": response_j,
                    "is_error": is_error,
                    "session_id": session_id,
                    "agent": AGENT_NAME,
                    "model": model,
                }
                _tid = _current_trace_id_hex()
                if _tid is not None:
                    entry["trace_id"] = _tid
                try:
                    await log_trace(json.dumps(entry))
                except Exception as _e:
                    logger.debug("AFC tool_result log failed: %s", _e)
                # PostToolUse audit row (#809). Mirrors codex / claude
                # so the dashboard Tool Trace tab sees gemini tool
                # invocations alongside the other backends. Fires on
                # every paired function_call/function_response; under
                # AFC the pair emits once per call (not per turn).
                try:
                    _audit_entry = {
                        "ts": ts,
                        "tool_name": name,
                        "tool": name,
                        "tool_use_id": tool_use_id,
                        "decision": "error" if is_error else "allow",
                        "is_error": is_error,
                        "session_id": session_id,
                        "agent": AGENT_NAME,
                        "model": model,
                        "result": response_j,
                    }
                    _tid2 = _current_trace_id_hex()
                    if _tid2 is not None:
                        _audit_entry["trace_id"] = _tid2
                    await _append_tool_audit(_audit_entry)
                except Exception as _audit_exc:
                    logger.debug("AFC tool_audit emit failed: %s", _audit_exc)
                # Per-call result payload size (#811). Peer parity with
                # claude's backend_sdk_tool_result_size_bytes.
                if backend_sdk_tool_result_size_bytes is not None:
                    try:
                        _result_bytes = (
                            len(
                                json.dumps(
                                    response_j,
                                    default=str,
                                    ensure_ascii=False,
                                ).encode("utf-8")
                            )
                            if response_j is not None
                            else 0
                        )
                        backend_sdk_tool_result_size_bytes.labels(
                            **_LABELS,
                            tool=name,
                        ).observe(_result_bytes)
                    except Exception:
                        pass
                # Metrics: one sample per observed function_call (#793).
                # Aligned with claude/codex — plain counter for calls,
                # separate counter for errors.
                if backend_sdk_tool_calls_total is not None:
                    backend_sdk_tool_calls_total.labels(**_LABELS, tool=name).inc()
                if is_error and backend_sdk_tool_errors_total is not None:
                    backend_sdk_tool_errors_total.labels(**_LABELS, tool=name).inc()
                # Duration: the SDK does not expose per-call timings through
                # AFC history. Fall back to wall-clock delta between the
                # matched function_call row and this function_response row
                # — an approximation that still catches runaway tool calls.
                _dur_seconds: float = 0.0
                if matched is not None:
                    try:
                        _start = datetime.fromisoformat(matched["ts"])
                        _end = datetime.fromisoformat(ts)
                        _dur_seconds = max(0.0, (_end - _start).total_seconds())
                        # #1727: skip duration histogram observe when matched
                        # was seeded from prefix_history. The real fc start
                        # time was lost across the persistence boundary, so
                        # the computed delta is a near-zero false sample.
                        # Tool-call counters and result-size metric still
                        # fire so audit / counts stay correct.
                        if backend_sdk_tool_duration_seconds is not None and not matched.get("from_prefix"):
                            backend_sdk_tool_duration_seconds.labels(**_LABELS, tool=name).observe(_dur_seconds)
                    except Exception:
                        pass
                # tool.use event (#1110 phase 3). Fire-and-forget.
                try:
                    _emit_result_bytes = 0
                    try:
                        _emit_result_bytes = (
                            len(
                                json.dumps(
                                    response_j,
                                    default=str,
                                    ensure_ascii=False,
                                ).encode("utf-8")
                            )
                            if response_j is not None
                            else 0
                        )
                    except Exception:
                        _emit_result_bytes = 0
                    _emit_event_safe(
                        "tool.use",
                        {
                            "session_id_hash": _session_id_hash(session_id),
                            "tool": name or "unknown",
                            "duration_ms": int(_dur_seconds * 1000),
                            "outcome": "error" if is_error else "ok",
                            "result_size_bytes": int(_emit_result_bytes),
                        },
                    )
                except Exception:
                    pass
                # Outbound MCP tool metric family (#1104) — no-op for non-mcp__ names.
                try:
                    from mcp_metrics import observe_outbound_mcp_call as _obs_outbound_mcp

                    _obs_outbound_mcp(
                        backend_mcp_outbound_requests_total,
                        backend_mcp_outbound_duration_seconds,
                        dict(_LABELS),
                        name,
                        _dur_seconds,
                        bool(is_error),
                    )
                except Exception:
                    pass

    # #998: emit a single cross-tool FIFO fallback warning per
    # invocation, carrying an occurrence count and a representative
    # (fr_name, matched_fc_name) pair so operators retain enough detail
    # to diagnose SDK-version drift without per-part log spam.
    if _cross_tool_fallback_count > 0:
        _fr_name, _fc_name = _cross_tool_fallback_example or ("?", "?")
        logger.warning(
            "AFC pairing: %d cross-tool FIFO fallback pair(s) this turn; "
            "example: fr name=%r matched pending fc name=%r — "
            "tool_use_id/duration label may be imprecise (#887, #998).",
            _cross_tool_fallback_count,
            _fr_name,
            _fc_name,
        )


async def _append_tool_audit(entry: dict) -> None:
    """Append an ``event_type='tool_audit'`` row via the shared helper (#809).

    Mirrors codex's _append_tool_audit so the dashboard Tool Trace tab
    sees gemini tool invocations with consistent fields. Never raises —
    the shared helper degrades via the log-write error counters if the
    underlying tool-activity.jsonl write fails.
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
                # #1755: wire per-logger error counter so write failures on
                # tool-activity.jsonl bump the {logger="tool_audit"} bucket
                # instead of getting collapsed into the global total.
                log_write_errors_by_logger_total=backend_log_write_errors_by_logger_total,
                # #1756: wire bytes-per-entry histogram + rotation pressure
                # counter so the shared tool_audit helper observes both. The
                # constructor previously omitted them, leaving the series
                # registered-but-empty on gemini while claude / codex emit
                # normally.
                tool_audit_bytes_per_entry=backend_tool_audit_bytes_per_entry,
                tool_audit_rotation_pressure_total=backend_tool_audit_rotation_pressure_total,
            ),
        ),
        entry,
    )


async def log_trace(text: str) -> None:
    """Append a raw line to ``TRACE_LOG`` for this turn.

    Thin wrapper around ``_append_log`` (run on a thread) plus metric
    bookkeeping under ``logger="trace"`` — ``backend_log_entries_total`` and
    ``backend_log_bytes_total`` on success, ``backend_log_write_errors_total``
    on failure. Like ``log_entry`` it swallows write exceptions so the
    trace-emitting code paths can call it unconditionally. ``text`` is
    written verbatim — callers are responsible for whatever JSON encoding /
    framing the trace consumers expect.
    """
    try:
        await asyncio.to_thread(_append_log, TRACE_LOG, text)
        if backend_log_entries_total is not None:
            backend_log_entries_total.labels(**_LABELS, logger="trace").inc()
        if backend_log_bytes_total is not None:
            backend_log_bytes_total.labels(**_LABELS, logger="trace").inc(len(text.encode()))
    except Exception as e:
        if backend_log_write_errors_total is not None:
            backend_log_write_errors_total.labels(**_LABELS).inc()
        logger.error(f"log_trace error: {e}")


def _session_path(session_id: str) -> str:
    return os.path.join(SESSION_STORE_DIR, f"{session_id}.json")


def _session_file_exists(session_id: str) -> bool:
    """Return True if a persisted session history file exists on disk for session_id.

    Used to detect resumed sessions after a process restart, when the in-memory
    LRU cache is empty but history exists on disk.  Always returns False if any
    error occurs so it never prevents a prompt from being processed.
    """
    try:
        return os.path.exists(_session_path(session_id))
    except Exception:
        return False


def _load_history(session_id: str) -> list[types.Content]:
    """Load persisted conversation history for a session, or return empty list."""
    path = _session_path(session_id)
    if not os.path.exists(path):
        return []
    try:
        with open(path) as f:
            raw = json.load(f)
    except Exception as e:
        # File-level corruption (bad JSON / unreadable): we can't recover
        # partial rows without a parseable object, so return []. Keep
        # WARNING so operators see the event.
        logger.warning(f"Failed to load session history for {session_id!r}: {e}")
        return []
    # #1508: per-entry + per-part exception handling so a single corrupt
    # Part (e.g. after an SDK schema bump that introduced a new field
    # validator) doesn't nuke the entire conversation. Skip the bad
    # rows, log at WARNING, and return everything else. Previously a
    # ValidationError on one Part raised out to the broad except above
    # and discarded the whole history.
    history: list[types.Content] = []
    _dropped_parts = 0
    _dropped_entries = 0
    for entry in raw if isinstance(raw, list) else []:
        parts: list = []
        for p in entry.get("parts", []) if isinstance(entry, dict) else []:
            if not p:
                continue
            try:
                parts.append(types.Part(**p))
            except Exception as _pe:
                _dropped_parts += 1
                logger.debug(
                    "Session %r: dropping corrupt Part during load: %r",
                    session_id,
                    _pe,
                )
        if not parts:
            _dropped_entries += 1
            continue
        try:
            history.append(types.Content(role=entry["role"], parts=parts))
        except Exception as _ce:
            _dropped_entries += 1
            logger.debug(
                "Session %r: dropping corrupt Content during load: %r",
                session_id,
                _ce,
            )
    if _dropped_parts or _dropped_entries:
        logger.warning(
            "Session %r: partial history load — dropped %d bad part(s) and %d "
            "bad entry/entries; retained %d entries. (#1508)",
            session_id,
            _dropped_parts,
            _dropped_entries,
            len(history),
        )
    return history


_SAVE_HISTORY_MAX_RETRIES = int(os.environ.get("GEMINI_SAVE_HISTORY_MAX_RETRIES", "3"))
_SAVE_HISTORY_BACKOFF_BASE = float(os.environ.get("GEMINI_SAVE_HISTORY_BACKOFF", "0.5"))
# Maximum number of turns to persist per session. Older turns are dropped so that
# per-turn save cost and file size stay bounded even for very long sessions (#349).
# Set to 0 to disable truncation (keep full history).
_SAVE_HISTORY_MAX_TURNS = int(os.environ.get("GEMINI_MAX_HISTORY_TURNS", "100"))
# Byte cap on the serialised history file (#817). Prevents a single
# large function_response from ballooning the saved session even when
# the turn count is under the limit — reloading such a file on every
# A2A request would inflate RAM usage and wire cost. Default 256 KiB;
# set to 0 to disable the cap. Enforced AFTER the turn cap so the slice
# still lands on a safe user-turn boundary.
_SAVE_HISTORY_MAX_BYTES = int(os.environ.get("GEMINI_MAX_HISTORY_BYTES", str(256 * 1024)))

# #1058: AFC per-turn soft cap on chat.history growth. AFC ping-pong is
# internal to send_message_stream; a runaway tool loop could accumulate
# megabytes of tool-call/response rows before _emit_afc_history has a
# chance to slice and persist. Check chat.history byte size on each
# streamed chunk and raise BudgetExceededError early once we cross the
# cap. Default 2 MiB (~8x _SAVE_HISTORY_MAX_BYTES) so normal AFC loops
# complete; set to 0 to disable.
_AFC_HISTORY_SOFT_CAP_BYTES = int(os.environ.get("GEMINI_AFC_HISTORY_SOFT_CAP_BYTES", str(2 * 1024 * 1024)))


def _write_history_to_disk(tmp_path: str, path: str, raw: list) -> None:
    """Write serialized history to disk atomically (blocking I/O, run in a thread)."""
    with open(tmp_path, "w") as f:
        json.dump(raw, f)
    os.replace(tmp_path, path)


# Per-session "save is done" Events (#674). The timeout cleanup path
# awaits these before os.remove so a writer thread cannot resurrect the
# file via os.replace after the remove.
_history_write_done: dict[str, asyncio.Event] = {}

# Per-session cleanup-epoch counter (#732). The timeout cleanup path
# increments this before (and again after) ``os.remove``; every save
# captures the pre-save epoch and refuses to ``os.replace`` when the
# epoch advanced while its ``to_thread`` write was in flight.  That
# covers the "5s wait elapsed, os.remove fired, writer completes its
# write-to-tmp on disk pressure, replace resurrects file" race the
# #674 done-Event alone couldn't fully close.  All access happens on
# the event loop thread (the inner _write_history_to_disk blocking
# call runs in a worker thread but reads a snapshot of the epoch
# captured before it was dispatched, so no cross-thread visibility
# hazard applies).
_session_cleanup_epoch: dict[str, int] = {}

# Strong-reference set for _track_session's fire-and-forget
# _deferred_evict_remove tasks (#999). asyncio.create_task only keeps a
# weak reference from the running loop, so without a strong ref a task
# scheduled just before loop shutdown can be garbage-collected before
# the deferred os.remove executes — leaving a stale session file on
# disk that resumption on the next pod boot happily re-hydrates. The
# executor's close() path drains this set after cancelling watchers so
# a graceful shutdown flushes every pending eviction. done_callback
# discards completed tasks so the set does not grow unbounded.
_EVICT_REMOVE_TASKS: set[asyncio.Task] = set()

# #1513: bound concurrent deferred-eviction tasks under churn. Each task
# awaits the pending history-save with a 5s timeout, so a bursty eviction
# pattern (many sessions touched then LRU-evicted in quick succession)
# could balloon the set. Cap via env; when the cap is reached the
# backpressure branch in _track_session keeps awaiting any in-flight
# history save (synchronous os.remove is only used when no save is
# pending) so we never wipe the on-disk file from underneath a writer.
#
# Tuning (#1611): the default of 256 was chosen for typical eviction
# bursts of ~10s of sessions/sec with ~100ms saves. Raise it
# (e.g. ``GEMINI_EVICT_REMOVE_TASKS_MAX=1024``) on hosts where:
#   * sustained eviction churn exceeds ~256 concurrent saves, or
#   * disk fsync latency frequently approaches the 30s wait budget
#     used by the backpressure branch (so few tasks complete in time
#     to make headroom for new evictions).
# Lower it (e.g. ``=64``) on memory-constrained pods where the per-task
# overhead is more important than wait fidelity. Setting it ``=0``
# disables deferred eviction entirely and degrades to the
# backpressure branch on every eviction (still safe — never removes
# while a save is in flight).
_EVICT_REMOVE_TASKS_MAX = int(os.environ.get("GEMINI_EVICT_REMOVE_TASKS_MAX") or "256")

# #1611: how long the backpressure branch waits for an in-flight history
# save before re-queueing the eviction. Longer than the per-task 5s
# wait used by the deferred-eviction task because the backpressure path
# is the last line of defence — we'd rather block briefly than risk
# unlinking the file from under a writer that's about to publish.
_EVICT_BACKPRESSURE_SAVE_WAIT_SEC = float(os.environ.get("GEMINI_EVICT_BACKPRESSURE_SAVE_WAIT_SEC") or "30.0")


def _bump_cleanup_epoch(session_id: str) -> int:
    """Increment and return the cleanup epoch for ``session_id`` (#732)."""
    nxt = _session_cleanup_epoch.get(session_id, 0) + 1
    _session_cleanup_epoch[session_id] = nxt
    return nxt


def _write_history_tmp_blocking(tmp_path: str, raw: list) -> None:
    """Blocking helper: write ``raw`` to ``tmp_path`` as JSON (#732).

    Kept as a separate function so the to_thread dispatch only does
    filesystem I/O — the cleanup-epoch check and os.replace now run
    back on the event-loop thread (see #1504) so we don't read the
    ``_session_cleanup_epoch`` dict cross-thread where memory
    visibility + stale-read races could resurrect a cleaned-up file.
    """
    with open(tmp_path, "w") as f:
        json.dump(raw, f)


def _write_history_respecting_epoch(tmp_path: str, path: str, raw: list, session_id: str, expected_epoch: int) -> bool:
    """Backwards-compatible thin wrapper retained for callers/tests.

    Newer call sites (see ``_save_history``) split the tmp write onto
    a worker thread and do the epoch check + ``os.replace`` on the
    event-loop thread (#1504). This helper performs the full sequence
    synchronously under the assumption that the caller is single-
    threaded, and is kept as a stable API surface for any out-of-tree
    callers.
    """
    _write_history_tmp_blocking(tmp_path, raw)
    current_epoch = _session_cleanup_epoch.get(session_id, 0)
    if current_epoch != expected_epoch:
        try:
            os.remove(tmp_path)
        except FileNotFoundError:
            pass
        return False
    os.replace(tmp_path, path)
    return True


async def _save_history(session_id: str, history: list[types.Content]) -> None:
    """Persist conversation history for a session.

    Retries up to _SAVE_HISTORY_MAX_RETRIES times with exponential backoff on
    failure.  After all retries are exhausted, raises the exception so the
    caller can log it at ERROR level rather than silently discarding it.
    """
    path = _session_path(session_id)
    raw = []
    for content in history:
        parts = [p.model_dump(exclude_none=True) for p in (content.parts or []) if p]
        if parts:
            raw.append({"role": content.role, "parts": parts})
    if _SAVE_HISTORY_MAX_TURNS > 0 and len(raw) > _SAVE_HISTORY_MAX_TURNS:
        # Advance the cut point forward until it lands on a user-role
        # Content whose first part is not a function_response (#672).
        # Gemini's send_message_stream requires the history start with a
        # user turn, and a function_call/function_response pair must stay
        # intact — a naive slice can split AFC pairs or start on a
        # "model"/function_response Content, which permanently bricks the
        # session on reload.
        cut = len(raw) - _SAVE_HISTORY_MAX_TURNS
        n = len(raw)
        while cut < n:
            entry = raw[cut]
            if entry.get("role") != "user":
                cut += 1
                continue
            # Scan EVERY part (#945). User-role Content can carry
            # multiple parts where a later part is a function_response
            # following an earlier text part — inspecting only parts[0]
            # missed that case and produced a structurally invalid
            # history that Gemini rejected on reload, bricking the
            # session. Any part containing a function_response makes
            # this boundary unsafe.
            _parts = entry.get("parts") or []
            if any("function_response" in (p or {}) for p in _parts):
                # Splitting before a function_response would orphan the
                # pair — keep walking until we clear it.
                cut += 1
                continue
            break
        if cut >= n:
            # Tail contained no safe cut point anywhere within the
            # target window (#731).  Previously we either preserved the
            # last entry alone when it happened to be a user turn or
            # dropped history entirely — the second branch silently
            # wiped the session when the trailing segment was still
            # mid-AFC (MCP-heavy workloads hit this regularly).  The
            # fix preserves the FULL history when no safe boundary
            # exists, logs a warning so operators notice the elongated
            # payload, and relies on the next truncation pass to find
            # a boundary once the AFC chain finishes.  Correctness
            # trumps size for persisted sessions — a one-off oversized
            # file is recoverable; a silently wiped conversation is
            # not.
            logger.warning(
                "Session %r: history truncation found no safe boundary within "
                "the target window; preserving full history (%d entries) to "
                "avoid silent session loss. Next save will retry truncation (#731).",
                session_id,
                n,
            )
        else:
            raw = raw[cut:]
    # Byte-cap enforcement (#817). Drop oldest pairs one safe boundary
    # at a time until the serialised payload fits under the cap, using
    # the same user-role / non-function_response boundary rule the
    # turn-cap branch relies on. Stops short of wiping history: if no
    # smaller safe cut is available we preserve the current slice and
    # log a warning — matches the correctness-over-size stance above.
    if _SAVE_HISTORY_MAX_BYTES > 0 and raw:
        try:
            current_bytes = len(json.dumps(raw, default=str).encode("utf-8"))
        except Exception:
            current_bytes = 0
        while current_bytes > _SAVE_HISTORY_MAX_BYTES and len(raw) > 1:
            # Find the next safe cut boundary after index 0.
            n = len(raw)
            nxt = 1
            while nxt < n:
                entry = raw[nxt]
                if entry.get("role") != "user":
                    nxt += 1
                    continue
                # Scan EVERY part, not just parts[0] (#945). Mirrors the
                # turn-cap branch above.
                _parts = entry.get("parts") or []
                if any("function_response" in (p or {}) for p in _parts):
                    nxt += 1
                    continue
                break
            if nxt >= n:
                # #1622: corruption recovery path — NOT the happy path.
                #
                # The safe-boundary search above failed: every user-role
                # entry in the trim window carries a function_response,
                # so cutting on any of them would orphan an AFC pair.
                # The original #817 fix preserved the current oversized
                # slice and relied on "the next save will find a
                # boundary". When the AFC chain is the *entire* history
                # (e.g. a runaway tool loop or an MCP server that keeps
                # round-tripping function_response/function_call), that
                # next boundary never arrives — every save after this
                # one re-enters the same branch, file stays oversized,
                # and the session oscillates between "too big to save
                # cleanly" and "too big to load efficiently".
                #
                # Force-split fallback: cut at the EARLIEST user-role
                # boundary in the window even though it splits a
                # mid-AFC function_call/function_response pair. Yes,
                # this leaves the persisted history with a dangling
                # function_call at the head — Gemini will reject the
                # reload and the session will fall back to a fresh
                # start. That's a deliberate trade: a recoverable
                # session reset beats an unbounded oversized file that
                # the byte cap was put in place to prevent. The next
                # successful save resumes normal #817 behaviour.
                fallback_cut = -1
                for idx in range(1, n):
                    if raw[idx].get("role") == "user":
                        fallback_cut = idx
                        break
                if fallback_cut < 0:
                    # No user-role entry at all in the trim window
                    # (pathological: model-only tail). Nothing safe or
                    # unsafe we can do — preserve the slice and let the
                    # next save retry, matching the original #817
                    # stance.
                    logger.warning(
                        "Session %r: byte-cap trim found no safe boundary "
                        "and no user-role fallback boundary "
                        "(payload=%dB cap=%dB); preserving current slice (#817).",
                        session_id,
                        current_bytes,
                        _SAVE_HISTORY_MAX_BYTES,
                    )
                    break
                logger.warning(
                    "Session %r: no safe AFC boundary; force-split at user "
                    "boundary idx=%d (payload=%dB cap=%dB) — corruption "
                    "recovery path, NOT the happy path. Reload may reset "
                    "the session if the head function_call is orphaned (#1622).",
                    session_id,
                    fallback_cut,
                    current_bytes,
                    _SAVE_HISTORY_MAX_BYTES,
                )
                try:
                    if backend_history_force_split_total is not None:
                        backend_history_force_split_total.labels(**_LABELS).inc()
                except Exception:
                    # Metrics surface must never fail the save path.
                    pass
                raw = raw[fallback_cut:]
                try:
                    current_bytes = len(json.dumps(raw, default=str).encode("utf-8"))
                except Exception:
                    break
                # Continue the outer while-loop: the force-split may
                # still leave us above the cap if the remaining slice
                # is itself a giant entry. Subsequent iterations will
                # either find a safe boundary or fall back again.
                continue
            raw = raw[nxt:]
            try:
                current_bytes = len(json.dumps(raw, default=str).encode("utf-8"))
            except Exception:
                break
    tmp_path = path + ".tmp"
    last_exc: Exception | None = None
    # Enforce the single-loop invariant this function relies on (#890).
    # _session_cleanup_epoch, _history_write_done, and done_event.set are
    # mutated without locks — any off-loop call corrupts them.
    _assert_event_loop_thread()
    # Snapshot the cleanup epoch FIRST (#890). If a concurrent cleanup
    # bump lands between the done_event install and the epoch read, the
    # task that sees a stale epoch would publish on top of a cleaned-up
    # session. Reading the epoch before any other bookkeeping lets the
    # blocking helper's recheck-then-replace logic catch the cleanup.
    expected_epoch = _session_cleanup_epoch.get(session_id, 0)
    # Announce that a save is in progress so a concurrent timeout cleanup
    # can await the done-Event before os.remove (#674). Replace any prior
    # Event for this session so we signal completion of *this* save only.
    done_event = asyncio.Event()
    _history_write_done[session_id] = done_event
    try:
        for attempt in range(_SAVE_HISTORY_MAX_RETRIES):
            try:
                # #1504: split the write so only filesystem I/O runs on
                # the worker thread. The cleanup-epoch recheck +
                # os.replace run back on the event-loop thread so we
                # never read _session_cleanup_epoch from another thread
                # (cross-thread dict memory-visibility + stale-read race
                # could resurrect a file an eviction just removed).
                await asyncio.to_thread(_write_history_tmp_blocking, tmp_path, raw)
                current_epoch = _session_cleanup_epoch.get(session_id, 0)
                if current_epoch != expected_epoch:
                    try:
                        os.remove(tmp_path)
                    except FileNotFoundError:
                        pass
                    logger.info(
                        "Session %r: cleanup epoch advanced during save — "
                        "dropping this write to avoid resurrecting a removed "
                        "history file (#732).",
                        session_id,
                    )
                    # #1506: raise instead of silent return. The caller
                    # treated a silent return as success, so its
                    # history_save_failed bookkeeping was not updated and
                    # a subsequent request could reload a stale on-disk
                    # snapshot (or, if the eviction had already removed
                    # the file, fail to spot that in-memory history
                    # never reached disk). Use a RuntimeError with a
                    # sentinel message and break out of the retry loop
                    # — retrying does not undo an epoch advance.
                    last_exc = RuntimeError(
                        f"cleanup epoch advanced during save for {session_id!r} "
                        f"(expected={expected_epoch}, current={current_epoch})"
                    )
                    break
                # os.replace is atomic on POSIX; run on the loop thread
                # so it is sequenced with the epoch read above.
                os.replace(tmp_path, path)
                return
            except Exception as e:
                last_exc = e
                logger.warning(
                    f"Failed to save session history for {session_id!r} "
                    f"(attempt {attempt + 1}/{_SAVE_HISTORY_MAX_RETRIES}): {e}"
                )
                if attempt < _SAVE_HISTORY_MAX_RETRIES - 1:
                    await asyncio.sleep(_SAVE_HISTORY_BACKOFF_BASE * (2**attempt))
    finally:
        # Always signal completion (success, permanent failure, or
        # cancellation) so the timeout cleanup never blocks forever.
        done_event.set()
        # Only clear the mapping if it still points at our Event — a
        # later save may have replaced it.
        if _history_write_done.get(session_id) is done_event:
            _history_write_done.pop(session_id, None)
    # All retries exhausted — raise so the caller can log at ERROR level.
    raise RuntimeError(
        f"Permanently failed to save session history for {session_id!r} after {_SAVE_HISTORY_MAX_RETRIES} attempts"
    ) from last_exc


class _RefCountedLock:
    """An asyncio.Lock bundled with a waiter refcount (#483).

    The refcount is incremented by `_acquire_session_lock` BEFORE `async with
    lock` and decremented by `_release_session_lock` AFTER the lock is released.
    Eviction from ``session_locks`` is only permitted when the refcount reaches
    zero, which guarantees:

    - A task that has already looked up (or is about to acquire) a lock entry
      cannot have that entry silently replaced by a fresh ``asyncio.Lock``
      while it still holds — or is queued on — the original lock instance.
    - The #401 hygiene goal is preserved: once the last waiter is done, the
      entry is removed from the dict so idle session locks do not accumulate.
    """

    __slots__ = ("lock", "refcount")

    def __init__(self) -> None:
        self.lock: asyncio.Lock = asyncio.Lock()
        self.refcount: int = 0


def _assert_event_loop_thread() -> None:
    """Assert we're running on the executor's asyncio event loop (#729).

    ``_acquire_session_lock`` / ``_release_session_lock`` mutate the shared
    ``session_locks`` dict without any async-level guard — correctness
    depends entirely on the single-event-loop invariant.  The invariant has
    held for every call site in the repo so far, but a future refactor that
    schedules a session helper from ``asyncio.to_thread`` (or any worker
    thread) would silently corrupt the refcount.  This assertion surfaces
    such a regression as an obvious ``RuntimeError`` at the offending call
    site instead of leaking stale entries for days.

    ``asyncio.get_running_loop`` raises when called off-loop — we catch
    ``RuntimeError`` specifically to turn it into a message that references
    this issue so operators can grep the codebase.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError as exc:
        raise RuntimeError(
            "session_locks mutated off the asyncio event loop thread — "
            "gemini refcounted-lock invariant requires single-loop access (#729)"
        ) from exc


def _acquire_session_lock(session_id: str, session_locks: dict[str, "_RefCountedLock"]) -> "_RefCountedLock":
    """Return (and register a waiter on) the refcounted lock for ``session_id``.

    Must be paired with a ``_release_session_lock`` call in a ``finally`` so
    that eviction invariants are not violated on cancellation or error. This is
    safe to call without holding any async-level lock because ``session_locks``
    is mutated only from the single asyncio event loop thread; the refcount
    bump and dict insertion happen synchronously in one step.

    The ``_assert_event_loop_thread`` call makes the documented
    single-loop invariant enforceable (#729): a future refactor that
    accidentally schedules this from a worker thread raises immediately
    instead of corrupting the refcount.
    """
    _assert_event_loop_thread()
    entry = session_locks.get(session_id)
    if entry is None:
        entry = _RefCountedLock()
        session_locks[session_id] = entry
    entry.refcount += 1
    return entry


def _release_session_lock(session_id: str, session_locks: dict[str, "_RefCountedLock"]) -> None:
    """Drop this task's reference; evict the dict entry when no waiters remain.

    The ``session_locks.get(session_id) is entry`` identity check below is
    load-bearing (#729): if the entry pointer was replaced since our
    ``acquire`` — which cannot happen on the current single-loop code
    path but is exactly the regression the assertion above guards
    against — we must not pop the newer entry out from under another
    waiter.  The ``is`` comparison preserves the invariant even if a
    future code path ever violates the single-loop contract.
    """
    _assert_event_loop_thread()
    entry = session_locks.get(session_id)
    if entry is None:
        return
    entry.refcount -= 1
    if entry.refcount <= 0 and session_locks.get(session_id) is entry:
        session_locks.pop(session_id, None)


def _track_session(
    sessions: OrderedDict[str, float],
    session_id: str,
    session_locks: dict[str, "_RefCountedLock"],
    history_save_failed: set[str] | None = None,
) -> None:
    if session_id in sessions:
        sessions.move_to_end(session_id)
        sessions[session_id] = time.monotonic()
    else:
        if len(sessions) >= MAX_SESSIONS:
            _evicted_id, last_used_at = sessions.popitem(last=False)
            # Only evict the lock entry when no one holds or waits on it.
            # Otherwise the current holder's release path (_release_session_lock)
            # will remove it once refcount reaches zero. This preserves the
            # mutual-exclusion invariant under MAX_SESSIONS pressure (#483).
            _evicted_entry = session_locks.get(_evicted_id)
            if _evicted_entry is not None and _evicted_entry.refcount <= 0:
                session_locks.pop(_evicted_id, None)
            # Prune the evicted session from history_save_failed so the set
            # does not grow unbounded under sustained save failure (#485).
            # Mirrors the pop symmetry maintained for sessions/session_locks.
            if history_save_failed is not None:
                history_save_failed.discard(_evicted_id)
            if backend_session_evictions_total is not None:
                backend_session_evictions_total.labels(**_LABELS).inc()
            if backend_session_age_seconds is not None:
                backend_session_age_seconds.labels(**_LABELS).observe(time.monotonic() - last_used_at)
            _evicted_path = os.path.join(SESSION_STORE_DIR, f"{_evicted_id}.json")
            # Bump the cleanup epoch so any in-flight writer for the
            # evicted session aborts before publishing (#732).
            _bump_cleanup_epoch(_evicted_id)
            # Defer the actual file removal to an asyncio task so it can
            # (a) await the in-flight writer's done-event briefly and
            # (b) call os.remove via asyncio.to_thread instead of blocking
            # the event loop (#889). Mirrors the pattern already used on
            # the timeout path at ~1670. _track_session itself is sync —
            # the task is scheduled via create_task off the currently
            # running loop; if no loop is running (unlikely but possible
            # from a sync test), fall back to the old blocking remove.
            try:
                _loop = asyncio.get_running_loop()
            except RuntimeError:
                _loop = None
            if _loop is not None:

                async def _deferred_evict_remove(
                    _ev_id: str = _evicted_id,
                    _ev_path: str = _evicted_path,
                ) -> None:
                    _pending_save = _history_write_done.get(_ev_id)
                    if _pending_save is not None:
                        try:
                            await asyncio.wait_for(_pending_save.wait(), timeout=5.0)
                        except asyncio.TimeoutError:
                            logger.warning(
                                "History save for LRU-evicted session %r did not "
                                "complete within 5s — removing file anyway; epoch "
                                "guard (#732) blocks any late os.replace.",
                                _ev_id,
                            )
                    try:
                        await asyncio.to_thread(os.remove, _ev_path)
                    except FileNotFoundError:
                        pass
                    except OSError as _e:
                        logger.warning(
                            "Could not remove evicted session file %s: %s",
                            _ev_path,
                            _e,
                        )
                    _bump_cleanup_epoch(_ev_id)
                    _session_cleanup_epoch.pop(_ev_id, None)

                # #1513: bound the deferred-eviction task set. Under
                # sustained churn the 5s wait-for on each task could let
                # the set grow proportionally to eviction backlog.
                if len(_EVICT_REMOVE_TASKS) >= _EVICT_REMOVE_TASKS_MAX:
                    # Backpressure (#1611): the deferred-eviction set is
                    # full, but we MUST NOT synchronously os.remove() while
                    # a history save is in flight — doing so races the
                    # writer's os.replace (the epoch guard at #732 helps,
                    # but the file has still been observed to flap on
                    # disk in CI). Instead:
                    #   * If a save is pending, schedule an async waiter
                    #     that blocks up to _EVICT_BACKPRESSURE_SAVE_WAIT_SEC
                    #     and then performs the remove. The waiter joins
                    #     the same _EVICT_REMOVE_TASKS set so close()
                    #     drains it on shutdown.
                    #   * If no save is pending, the synchronous remove
                    #     is safe — take the fast path so backpressure
                    #     still relieves quickly.
                    _pending_save_bp = _history_write_done.get(_evicted_id)
                    if _pending_save_bp is not None:

                        async def _backpressure_evict_remove(
                            _ev_id: str = _evicted_id,
                            _ev_path: str = _evicted_path,
                            _save_event: asyncio.Event = _pending_save_bp,
                        ) -> None:
                            try:
                                await asyncio.wait_for(
                                    _save_event.wait(),
                                    timeout=_EVICT_BACKPRESSURE_SAVE_WAIT_SEC,
                                )
                            except asyncio.TimeoutError:
                                logger.warning(
                                    "History save for LRU-evicted session %r "
                                    "did not complete within %.1fs under "
                                    "backpressure — removing file anyway; "
                                    "epoch guard (#732) blocks any late "
                                    "os.replace. (#1611)",
                                    _ev_id,
                                    _EVICT_BACKPRESSURE_SAVE_WAIT_SEC,
                                )
                            try:
                                await asyncio.to_thread(os.remove, _ev_path)
                            except FileNotFoundError:
                                pass
                            except OSError as _bp_err:
                                logger.warning(
                                    "Could not remove evicted session file %s (backpressure path): %s",
                                    _ev_path,
                                    _bp_err,
                                )
                            _bump_cleanup_epoch(_ev_id)
                            _session_cleanup_epoch.pop(_ev_id, None)

                        _bp_task = _loop.create_task(_backpressure_evict_remove())
                        _EVICT_REMOVE_TASKS.add(_bp_task)
                        _bp_task.add_done_callback(_EVICT_REMOVE_TASKS.discard)
                    else:
                        # No writer in flight — synchronous remove is safe
                        # and avoids growing the deferred set further.
                        try:
                            os.remove(_evicted_path)
                        except FileNotFoundError:
                            pass
                        except OSError as e:
                            logger.warning(
                                "Could not remove evicted session file %s (deferred cap reached, no pending save): %s",
                                _evicted_path,
                                e,
                            )
                        _bump_cleanup_epoch(_evicted_id)
                        _session_cleanup_epoch.pop(_evicted_id, None)
                else:
                    _evict_task = _loop.create_task(_deferred_evict_remove())
                    # Hold a strong reference so loop shutdown doesn't
                    # garbage-collect the task mid-flight (#999). The
                    # done-callback discards the reference on completion
                    # so the set stays bounded under sustained eviction.
                    _EVICT_REMOVE_TASKS.add(_evict_task)
                    _evict_task.add_done_callback(_EVICT_REMOVE_TASKS.discard)
            else:
                try:
                    os.remove(_evicted_path)
                except FileNotFoundError:
                    pass
                except OSError as e:
                    logger.warning(
                        "Could not remove evicted session file %s: %s",
                        _evicted_path,
                        e,
                    )
                _bump_cleanup_epoch(_evicted_id)
                _session_cleanup_epoch.pop(_evicted_id, None)
        sessions[session_id] = time.monotonic()
    if backend_active_sessions is not None:
        backend_active_sessions.labels(**_LABELS).set(len(sessions))
    if backend_lru_cache_utilization_percent is not None and MAX_SESSIONS > 0:
        backend_lru_cache_utilization_percent.labels(**_LABELS).set(len(sessions) / MAX_SESSIONS * 100)


_genai_client: genai.Client | None = None
# threading.Lock (not asyncio.Lock) so both the sync ``_get_client`` and
# the async ``_close_client`` can share the same serialisation primitive
# (#734). Construction + teardown both finish in microseconds — no
# event-loop blocking concern. Guards the read-env + construct + assign
# sequence against a concurrent ``_close_client`` that nulls the
# singleton between the ``if None`` check and the assignment during
# key rotation.
import threading as _threading

_genai_client_lock = _threading.Lock()

# #1621: rotation flag + pending-teardown queue. The api-key-file watcher
# now requests rotation by setting ``_rotation_pending`` instead of nulling
# the singleton; ``_get_client`` performs build-then-swap atomically so a
# construction failure (e.g., env var transiently empty during rotation)
# leaves the previously-cached client in place rather than blanking the
# singleton and breaking every subsequent request. Old clients are queued
# in ``_clients_pending_close`` and torn down opportunistically by
# ``_close_client``.
_rotation_pending: bool = False
_clients_pending_close: "list[genai.Client]" = []

# #1505: serialise the api-key-file watcher's env mutation + _close_client
# sequence. Without this, two rapid rotations could interleave env writes
# and client closes so an in-flight rotation's _close_client races with
# the next rotation's os.environ update, producing a window where the
# cached client is closed but the env still holds the old key, or vice
# versa. Lazily constructed on first use so module import stays
# event-loop-free. Created under an import-safe sentinel.
_api_key_rotation_lock: "asyncio.Lock | None" = None


def _get_api_key_rotation_lock() -> asyncio.Lock:
    """Lazy accessor for the rotation serialisation lock (#1505)."""
    global _api_key_rotation_lock
    if _api_key_rotation_lock is None:
        _api_key_rotation_lock = asyncio.Lock()
    return _api_key_rotation_lock


def _get_client(model_label: str | None = None) -> genai.Client:
    """Return the module-level genai.Client singleton, creating it on first call.

    The API key is read from the environment on each construction so that
    setting _genai_client = None and calling _get_client() again (e.g., in
    a future refresh path) will pick up the current key value rather than
    the value captured at module import time.

    Note: in standard deployments, API key changes require a process restart
    since the container environment is not updated in-place. Setting
    _genai_client = None alone is not sufficient unless the process environment
    is also updated (e.g., via a secrets-manager sidecar that mutates os.environ).

    Serialisation (#734): the read-env + construct + assign sequence runs
    under ``_genai_client_lock`` so a concurrent ``_close_client`` during
    an API-key rotation cannot produce a window where request A captures
    the new client while request B's in-flight ``_get_client`` is still
    using a stale reference.  The double-checked first read before the
    lock keeps the steady-state fast path allocation-free.

    Rotation safety (#1621): when ``_rotation_pending`` is set the function
    constructs the candidate client first (under the lock) and only swaps
    on success.  If construction raises (e.g., the rotated key file is
    momentarily empty or the env var has been cleared) the previous
    ``_genai_client`` is preserved verbatim and the error is logged so
    in-flight traffic keeps serving under the old key instead of seeing
    a None singleton on every subsequent call.
    """
    global _genai_client, _rotation_pending
    client = _genai_client
    if client is not None and not _rotation_pending:
        return client
    with _genai_client_lock:
        # Re-check inside the lock — another caller may have completed
        # cold-start or rotation while we were waiting.
        if _genai_client is not None and not _rotation_pending:
            return _genai_client
        # Cold-start vs. rotation share the same build-then-swap sequence
        # below; the only difference is that on rotation we have an
        # existing client to fall back to if the build fails (#1621).
        existing = _genai_client
        key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY") or None
        if not key:
            if existing is not None:
                # #1621: rotation requested but the new key is missing.
                # Keep serving under the previous client; clear the
                # rotation flag so we don't hammer this path on every
                # request.  The watcher will re-arm it on the next file
                # change.
                logger.error(
                    "_get_client: rotation requested but no API key "
                    "available in env (GEMINI_API_KEY/GOOGLE_API_KEY); "
                    "keeping previously-cached client to preserve "
                    "request-serving capacity. (#1621)"
                )
                _rotation_pending = False
                return existing
            raise RuntimeError("No Gemini API key configured. Set GEMINI_API_KEY or GOOGLE_API_KEY.")
        # Build candidate FIRST (#1621). Only swap on success.
        _spawn_start = time.monotonic()
        try:
            new_client = genai.Client(api_key=key)
        except Exception as _build_exc:
            if existing is not None:
                # #1621: keep the previously-cached client so callers
                # continue to receive a usable client. Clear the
                # rotation flag — the watcher will re-arm on the next
                # file change.
                logger.error(
                    "_get_client: failed to construct new genai.Client "
                    "during rotation (%r); keeping previously-cached "
                    "client to preserve request-serving capacity. (#1621)",
                    _build_exc,
                )
                _rotation_pending = False
                return existing
            # Cold-start failure with no prior client — propagate so the
            # caller sees the underlying error rather than a confusing
            # None singleton on the next call.
            raise
        # Success: swap atomically and queue the prior client (if any)
        # for opportunistic teardown by the next ``_close_client`` call.
        if existing is not None:
            _clients_pending_close.append(existing)
        _genai_client = new_client
        _rotation_pending = False
        try:
            # Cold-start / rotation timing (#813). Observes subprocess/
            # client init latency so operators can alert on slow first-
            # request cold-starts (and slow rotations).
            if backend_sdk_subprocess_spawn_duration_seconds is not None:
                backend_sdk_subprocess_spawn_duration_seconds.labels(
                    **_LABELS,
                    model=_sanitize_model_label(model_label or ""),
                ).observe(time.monotonic() - _spawn_start)
        except Exception:
            pass
        return _genai_client


def _request_client_rotation() -> None:
    """Mark the cached genai.Client for rotation on the next ``_get_client`` call.

    #1621: the previous flow nulled ``_genai_client`` eagerly via
    ``_close_client`` and let ``_get_client`` rebuild on demand. If the new
    key was unavailable at rebuild time the caller saw an exception AND the
    singleton was left as None, so every subsequent request also raised
    until the env was repaired. By flipping a flag instead, ``_get_client``
    can build the new client first and fall back to the previously-cached
    one when construction fails — keeping the backend serving under the
    old key until rotation succeeds.
    """
    global _rotation_pending
    with _genai_client_lock:
        _rotation_pending = True


async def _close_client() -> None:
    """Dispose the module-level genai.Client singleton, if any.

    google-genai (>=1.20) does not expose a public close API on ``genai.Client``;
    the SDK's underlying ``BaseApiClient`` owns ``_httpx_client`` (sync) and
    ``_async_httpx_client`` (async) connection pools that otherwise linger
    until the process exits. Best-effort teardown: close whichever pools were
    actually instantiated and swallow any errors so shutdown is never blocked
    by a transport quirk. Resets the singleton so a later ``_get_client()``
    call will construct a fresh instance.
    """
    # Null the singleton reference *inside* _genai_client_lock so a
    # concurrent _get_client cannot start using the old client after we
    # begin tearing it down (#734). We do the actual aclose work outside
    # the lock to avoid blocking the event loop; any subsequent
    # _get_client call past the lock boundary constructs a fresh
    # instance against the current env.
    #
    # #1621: also drain ``_clients_pending_close`` here. Successful
    # rotations queue the prior client there (instead of tearing it down
    # eagerly under ``_get_client``'s sync lock) so connection pool
    # teardown happens on this async path.
    global _genai_client, _rotation_pending
    with _genai_client_lock:
        client = _genai_client
        _genai_client = None
        _rotation_pending = False
        pending = list(_clients_pending_close)
        _clients_pending_close.clear()
    to_close: list[genai.Client] = []
    if client is not None:
        to_close.append(client)
    to_close.extend(pending)
    for _c in to_close:
        try:
            api_client = getattr(_c, "_api_client", None)
            if api_client is not None:
                async_httpx = getattr(api_client, "_async_httpx_client", None)
                if async_httpx is not None:
                    aclose = getattr(async_httpx, "aclose", None)
                    if aclose is not None:
                        try:
                            await aclose()
                        except Exception as e:  # pragma: no cover - defensive
                            logger.debug("genai async httpx client aclose failed: %s", e)
                sync_httpx = getattr(api_client, "_httpx_client", None)
                if sync_httpx is not None:
                    close = getattr(sync_httpx, "close", None)
                    if close is not None:
                        try:
                            close()
                        except Exception as e:  # pragma: no cover - defensive
                            logger.debug("genai sync httpx client close failed: %s", e)
        except Exception:  # pragma: no cover — defensive wrapper
            pass


async def run_query(
    prompt: str,
    session_id: str,
    agent_md_content: str,
    session_locks: dict[str, "_RefCountedLock"],
    history_save_failed: set[str] | None = None,
    model: str | None = None,
    max_tokens: int | None = None,
    on_chunk: Callable[[str], Awaitable[None]] | None = None,
    live_mcp_servers: list | None = None,
    sessions: OrderedDict[str, float] | None = None,
) -> list[str]:
    """Run one Gemini ``send_message_stream`` turn under a refcounted session lock.

    Resolves the model (caller string preserved verbatim, sanitized only at
    metric label sites #487), builds ``instructions`` from
    ``agent_md_content`` plus ``session_id``, and acquires the per-session
    refcounted lock via ``_acquire_session_lock`` so the dict entry cannot
    be evicted while we are queued — eviction is gated on refcount == 0
    (#483).

    History handling: when ``session_id`` is in ``history_save_failed`` the
    call starts with an empty history, best-effort removes the stale
    on-disk file, increments ``backend_session_history_reset_total`` and
    WARNs so operators can distinguish a one-off save hiccup from a
    session that was silently wiped (#886, #1000). Otherwise loads via
    ``_load_history`` inside ``asyncio.to_thread``.

    Chat is created via ``client.aio.chats.create`` with the persisted
    history and a ``GenerateContentConfig`` carrying ``system_instruction``
    plus ``tools=list(live_mcp_servers)`` when present — google-genai's
    experimental MCP-as-tool support runs the function-call ping-pong
    inside Automatic Function Calling (AFC) (#640). For AFC observability,
    ``len(chat.history)`` is snapshotted before the stream so
    ``_emit_afc_history`` later walks only the new-slice (#883) and forwards
    the pre-snapshot prefix for cross-turn ``function_call`` ↔
    ``function_response`` id pairing (#996). Hook enforcement is skeletal
    while AFC runs the tool loop — ``self._hook_state`` is kept in sync by
    ``hooks_config_watcher`` so a future AFC-off path can wire it in
    without further plumbing (#1863).

    Streams ``chat.send_message_stream(prompt)``: appends ``chunk.text`` to
    ``collected``, observes ``backend_sdk_time_to_first_message_seconds`` on
    the first text chunk, fires ``on_chunk`` with errors warned-and-
    swallowed so SDK iteration is never aborted (#430), updates
    ``_total_tokens`` from ``usage_metadata.total_token_count``, and
    budget-checks on every chunk against the running total rather than only
    on chunks carrying usage (#1503 — otherwise a sequence of no-usage
    chunks could push past ``max_tokens`` silently). An AFC history soft
    cap is sampled every 4th chunk using the same ``model_dump`` +
    ``json.dumps`` byte sizing the save path uses (#1058, #1507) — both
    budget paths raise ``BudgetExceededError``.

    The ``llm.request`` child span (#630) is opened pre-stream with
    ``model`` plus ``mcp.sessions.count`` / ``tools.count`` attributes when
    ``live_mcp_servers`` is non-empty, and closed exactly once in the
    inner ``finally``.

    Error paths skip persisting ``chat.history`` to avoid leaving the
    session in a state that violates Gemini's alternating user/model
    contract or resumes on incomplete content — the session is added to
    ``history_save_failed`` so the next request restarts fresh (#493 budget
    path, #499 generic path, #409 / #437 invariant). Non-budget errors also
    observe ``backend_sdk_query_error_duration_seconds`` and classify via
    ``google.api_core`` into ``client_errors`` / ``result_errors`` / generic
    ``backend_sdk_errors_total`` (#445). Both error paths observe
    ``backend_sdk_session_duration_seconds`` before re-raising.

    Success path joins ``collected`` into ``full_response``, calls
    ``log_entry`` with the model and token count, emits the
    ``conversation.turn`` event (#1110 phase 3 — assistant role,
    ``content_bytes``, model omitted when falsy #1150), runs
    ``_emit_afc_history`` on the new slice with the pre-snapshot prefix
    forwarded, and persists via ``_save_history`` on a snapshot copy so the
    SDK cannot mutate the list mid-iteration (#1511). Permanent save
    failures log at ERROR, increment
    ``backend_session_history_save_errors_total``, and add the session to
    ``history_save_failed`` so the next request starts fresh (#409); the
    completed response is still returned to the caller.

    After the inner async-with block, observes the per-query histograms
    (``backend_sdk_query_duration_seconds``, ``messages_per_query``,
    ``turns_per_query``, ``text_blocks_per_query``, ``tokens_per_query``
    with parity to claude #813) and context-window observations
    (``backend_context_tokens``, ``backend_context_tokens_remaining``,
    ``backend_context_usage_percent`` plus ``backend_context_exhaustion_total``
    at >=100% or ``backend_context_warnings_total`` at >=80%), writes the
    ``response`` row via ``log_trace``, and promotes the session in the
    ``sessions`` LRU while still holding the session lock so a concurrent
    request cannot trigger ``MAX_SESSIONS`` eviction between save and
    promotion (#675).

    The outer ``finally`` always decrements the refcount via
    ``_release_session_lock`` — the lock entry is evicted from
    ``session_locks`` only when no other task holds or is waiting on it
    (#483).
    """
    resolved_model = model or GEMINI_MODEL
    # Note: resolved_model carries the raw caller-supplied string (so we pass
    # it faithfully to the SDK and log it verbatim). Wherever it lands in a
    # Prometheus label, pass it through _sanitize_model_label() so a hostile
    # caller cannot blow up metric cardinality (#487).

    instructions = f"Your name is {AGENT_NAME}. Your session ID is {session_id}."
    if agent_md_content:
        instructions = f"{agent_md_content}\n\nYour session ID is {session_id}."

    # Refcounted lock lookup (#483). The waiter is registered before we
    # block on the lock so the dict entry cannot be evicted out from under
    # us while we are queued — eviction is gated on refcount == 0.
    entry = _acquire_session_lock(session_id, session_locks)
    try:
        async with entry.lock:
            # Skip _load_history when this session is in the
            # history_save_failed set (#886). A previous run's save
            # permanently failed, so the on-disk file is either stale
            # or partially written — replaying it would silently
            # resume from inconsistent turn state. Start fresh and
            # best-effort remove the stale file so the next successful
            # save is not merged with a corrupt remainder.
            _save_failed = history_save_failed is not None and session_id in history_save_failed
            if _save_failed:
                history = []
                # #1000: surface the silent-reset path. Operators need
                # to distinguish "one save hiccup, user continued
                # normally" from "user's session was wiped and they're
                # talking to a brand-new assistant". Bump a counter and
                # log at WARNING so both /metrics and the log stream
                # carry the signal.
                if backend_session_history_reset_total is not None:
                    try:
                        backend_session_history_reset_total.labels(**_LABELS).inc()
                    except Exception:
                        pass
                logger.warning(
                    "Gemini run_query: session %r starting fresh — prior "
                    "history save permanently failed; stale on-disk file "
                    "will be removed (#886, #1000).",
                    session_id,
                )
                try:
                    _stale_path = _session_path(session_id)
                    if os.path.exists(_stale_path):
                        os.remove(_stale_path)
                except Exception as _rm_exc:
                    logger.debug(
                        "Gemini run_query: stale history removal for %r failed: %s",
                        session_id,
                        _rm_exc,
                    )
            else:
                history = await asyncio.to_thread(_load_history, session_id)

            client = _get_client()

            # NOTE(#1863): AFC-internal — hook enforcement requires tool-loop
            # interposition. The google-genai SDK's Automatic Function
            # Calling (AFC) runs the tool ping-pong inside ``generate_content``,
            # so a ``PreToolUse``-style ``evaluate_pre_tool_use`` call site here
            # cannot intercept MCP tool invocations without disabling AFC and
            # hand-rolling the loop. ``self._hook_state`` is still kept in sync
            # by ``hooks_config_watcher`` (#631) so that a future AFC-off path
            # can wire the engine in without further plumbing.

            # Build the GenerateContentConfig. When live MCP sessions are
            # attached (#640), pass them into ``tools=[...]`` — google-genai's
            # experimental MCP-as-tool support accepts raw ``ClientSession``
            # objects and handles the full function_call / function_response
            # ping-pong via AFC. See ``googleapis.github.io/python-genai``
            # for the current surface.
            _config_kwargs: dict = {"system_instruction": instructions}
            _live = list(live_mcp_servers or [])
            if _live:
                _config_kwargs["tools"] = list(_live)
            # Create chat with persisted history and system instruction
            chat = client.aio.chats.create(
                model=resolved_model,
                config=types.GenerateContentConfig(**_config_kwargs),
                history=history,
            )

            # Snapshot pre-call history length so AFC observability only emits
            # function_call / function_response pairs appended during *this*
            # turn (#883). Previously _emit_afc_history walked the entire
            # chat.history every turn, duplicating every prior turn's rows
            # and counter increments. The snapshot is taken after chat
            # creation so it covers the persisted turns — anything beyond
            # this index is new in the current send_message_stream roundtrip.
            try:
                _afc_history_start = len(getattr(chat, "history", []) or [])
            except Exception:
                _afc_history_start = 0

            collected: list[str] = []
            _query_start = time.monotonic()
            _session_start = time.monotonic()
            _first_chunk_at: float | None = None
            _turn_count = 0
            _message_count = 0
            _total_tokens = 0

            # llm.request child span (#630) — one per generate_content /
            # send_message_stream round-trip. Managed via manual enter/exit so
            # the streaming loop body below does not need to be re-indented.
            # When ``live_mcp_servers`` is non-empty (#640), AFC may dispatch
            # an arbitrary number of MCP tool calls inside this single SDK
            # invocation without surfacing per-call hooks to the caller.
            # Emitting a child span per MCP tool call would require disabling
            # AFC; instead we stamp the ``mcp.sessions.count`` / ``tools.count``
            # attributes on this aggregate span so traces still record that an
            # AFC roundtrip happened and how many sessions were in scope. A
            # future AFC-off path can split this into per-call child spans.
            _llm_attrs: dict = {"model": _sanitize_model_label(resolved_model)}
            if _live:
                _llm_attrs["mcp.sessions.count"] = len(_live)
                _llm_attrs["tools.count"] = len(_live)
            _llm_ctx = start_span(
                "llm.request",
                kind="client",
                attributes=_llm_attrs,
            )
            _llm_ctx.__enter__()
            _llm_closed = False
            try:
                async for chunk in await chat.send_message_stream(prompt):
                    _message_count += 1
                    text = getattr(chunk, "text", None)
                    if text:
                        if _first_chunk_at is None:
                            _first_chunk_at = time.monotonic()
                            if backend_sdk_time_to_first_message_seconds is not None:
                                backend_sdk_time_to_first_message_seconds.labels(
                                    **_LABELS, model=_sanitize_model_label(resolved_model)
                                ).observe(_first_chunk_at - _query_start)
                        collected.append(text)
                        # Stream the chunk to the A2A event_queue (#430). Set by
                        # execute(); None for non-streaming callers (e.g. /mcp).
                        # Awaited directly so events stay ordered and exceptions
                        # surface here. Errors swallowed so SDK iteration is never
                        # aborted.
                        if on_chunk is not None:
                            try:
                                await on_chunk(text)
                            except Exception as _e:
                                logger.warning("Session %r: on_chunk callback raised: %s", session_id, _e)
                    # Track token count and check budget on each chunk
                    _usage_meta = getattr(chunk, "usage_metadata", None)
                    _token_count = getattr(_usage_meta, "total_token_count", None)
                    if _token_count is not None:
                        _total_tokens = int(_token_count)
                    # #1503: budget check must run on every chunk against the
                    # accumulated _total_tokens, not only on chunks that
                    # carried usage_metadata. Otherwise a sequence of chunks
                    # with no usage field could push the running total past
                    # max_tokens silently and defeat the budget control.
                    if max_tokens is not None and _total_tokens >= max_tokens:
                        if backend_budget_exceeded_total is not None:
                            backend_budget_exceeded_total.labels(**_LABELS).inc()
                        raise BudgetExceededError(_total_tokens, max_tokens, list(collected))
                    # #1058: AFC history soft cap. Estimate current
                    # chat.history footprint and raise BudgetExceededError
                    # early if an AFC ping-pong loop is accumulating
                    # unbounded tool-call/result rows inside the SDK. Use
                    # a cheap per-chunk sampling rate (every 4th chunk)
                    # to avoid running a sizeof over multi-MB history on
                    # every token; still catches runaway growth within a
                    # few chunks of crossing the cap.
                    if _AFC_HISTORY_SOFT_CAP_BYTES > 0 and (_message_count & 0x3) == 0:
                        # #1507: switch from len(repr(_h)) to the same
                        # model_dump + json.dumps byte count the save
                        # path uses (_SAVE_HISTORY_MAX_BYTES). repr() on
                        # pydantic Content/Part does not correlate with
                        # serialised byte size (e.g. it includes type-
                        # name prefixes + quoting that scale
                        # non-linearly with payload) so the soft cap
                        # was effectively arbitrary. Errors fall back
                        # to 0 so the soft cap never false-trips.
                        try:
                            _raw_for_sizing = []
                            for _h in chat.history or []:
                                _parts = [
                                    p.model_dump(exclude_none=True) for p in (getattr(_h, "parts", None) or []) if p
                                ]
                                if _parts:
                                    _raw_for_sizing.append({"role": getattr(_h, "role", ""), "parts": _parts})
                            _hist_bytes = len(json.dumps(_raw_for_sizing, default=str).encode("utf-8"))
                        except Exception:
                            _hist_bytes = 0
                        if _hist_bytes >= _AFC_HISTORY_SOFT_CAP_BYTES:
                            logger.warning(
                                "Session %r: AFC history soft cap tripped "
                                "(~%d bytes >= %d) — aborting turn before "
                                "truncation runs post-hoc. (#1058)",
                                session_id,
                                _hist_bytes,
                                _AFC_HISTORY_SOFT_CAP_BYTES,
                            )
                            if backend_budget_exceeded_total is not None:
                                try:
                                    backend_budget_exceeded_total.labels(**_LABELS).inc()
                                except Exception:
                                    pass
                            raise BudgetExceededError(_total_tokens, max_tokens or 0, list(collected))
                _turn_count = 1
            except BudgetExceededError as exc:
                if backend_sdk_session_duration_seconds is not None:
                    backend_sdk_session_duration_seconds.labels(
                        **_LABELS, model=_sanitize_model_label(resolved_model)
                    ).observe(time.monotonic() - _session_start)
                partial_response = "".join(exc.collected)
                if partial_response:
                    await log_entry(
                        "agent", partial_response, session_id, model=resolved_model, tokens=_total_tokens or None
                    )
                # Do not persist chat.history here (#493). At this point the
                # history contains the user turn that triggered the aborted
                # call and, at best, a partial/implementation-defined model
                # turn appended by the google-genai SDK. Saving that would
                # leave the session in a state that either violates Gemini's
                # alternating user/model contract or resumes on incomplete
                # content on the next request. Instead, mark the session in
                # history_save_failed so the next request starts fresh —
                # same invariant the success-path handler maintains
                # (#437, #409). The prior on-disk history remains authoritative.
                if history_save_failed is not None:
                    history_save_failed.add(session_id)
                raise
            except Exception as _run_exc:
                if backend_sdk_query_error_duration_seconds is not None:
                    backend_sdk_query_error_duration_seconds.labels(
                        **_LABELS, model=_sanitize_model_label(resolved_model)
                    ).observe(time.monotonic() - _query_start)
                if backend_sdk_session_duration_seconds is not None:
                    backend_sdk_session_duration_seconds.labels(
                        **_LABELS, model=_sanitize_model_label(resolved_model)
                    ).observe(time.monotonic() - _session_start)
                # Classify by exception type so the new SDK error counters track
                # connection vs result vs catch-all failures (#445). Best-effort —
                # if the google.api_core import is unavailable, fall through to the
                # generic catch-all counter.
                try:
                    from google.api_core import exceptions as _g_exc

                    if isinstance(_run_exc, _g_exc.ClientError):
                        if backend_sdk_client_errors_total is not None:
                            backend_sdk_client_errors_total.labels(
                                **_LABELS, model=_sanitize_model_label(resolved_model)
                            ).inc()
                    elif isinstance(_run_exc, _g_exc.GoogleAPIError):
                        if backend_sdk_result_errors_total is not None:
                            backend_sdk_result_errors_total.labels(
                                **_LABELS, model=_sanitize_model_label(resolved_model)
                            ).inc()
                    else:
                        if backend_sdk_errors_total is not None:
                            backend_sdk_errors_total.labels(
                                **_LABELS, model=_sanitize_model_label(resolved_model)
                            ).inc()
                except Exception:
                    if backend_sdk_errors_total is not None:
                        backend_sdk_errors_total.labels(**_LABELS, model=_sanitize_model_label(resolved_model)).inc()
                # Do not persist chat.history here (#499). The SDK may have
                # partially advanced chat.history to include the failed user
                # turn with no (or an incomplete) assistant response. Saving
                # that would leave the session violating Gemini's alternating
                # user/model contract or resuming on incomplete content on the
                # next request. Mirror the BudgetExceededError policy (#493):
                # skip the save and mark the session in history_save_failed so
                # the next request starts fresh. The prior on-disk history (if
                # any) remains authoritative; _run_inner treats save-failed
                # sessions as new (#409, #437).
                if history_save_failed is not None:
                    history_save_failed.add(session_id)
                raise
            finally:
                # Close the llm.request span (#630). Safe to call once in the
                # finally — the context manager swallows double-close and on
                # error paths the propagating exception is already recorded in
                # _sdk_errors metrics above.
                if not _llm_closed:
                    _llm_closed = True
                    try:
                        _llm_ctx.__exit__(None, None, None)
                    except Exception:
                        pass

            if backend_sdk_session_duration_seconds is not None:
                backend_sdk_session_duration_seconds.labels(
                    **_LABELS, model=_sanitize_model_label(resolved_model)
                ).observe(time.monotonic() - _session_start)

            full_response = "".join(collected)
            if full_response:
                await log_entry("agent", full_response, session_id, model=resolved_model, tokens=_total_tokens or None)
                # conversation.turn event (#1110 phase 3). One event per
                # completed assistant turn; partial sites do not emit.
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

            # AFC observability (#640): walk chat.history to surface any
            # function_call / function_response parts appended by the SDK
            # during this roundtrip. Emits one tool_use + tool_result row
            # per pair into tool-activity.jsonl (matching claude's shape for
            # dashboard TraceView #592 and OTel trace viewer #632) and
            # increments backend_sdk_tool_calls_total / observes
            # backend_sdk_tool_duration_seconds. No-op when AFC did not run.
            if _live:
                try:
                    # Pass only the slice appended during this turn (#883)
                    # so we don't duplicate every prior turn's AFC rows and
                    # metric increments on every request. Also forward the
                    # pre-snapshot prefix as seed context (#996) so a
                    # function_response in the new slice that pairs with a
                    # function_call persisted from a prior turn still
                    # matches by id instead of falling through to the
                    # cross-tool FIFO with a mis-labelled tool_use_id.
                    _full_history = list(chat.history or [])
                    _new_history = _full_history[_afc_history_start:]
                    _prefix_history = _full_history[:_afc_history_start]
                    await _emit_afc_history(
                        _new_history,
                        session_id=session_id,
                        model=resolved_model,
                        prefix_history=_prefix_history,
                    )
                except Exception as _afc_exc:
                    logger.debug("AFC history emit failed: %s", _afc_exc)

            # Persist updated history — log at ERROR on permanent failure so it is
            # visible in monitoring, but do not propagate so the completed response
            # is still returned to the caller.  Mark the session in history_save_failed
            # so the next request starts fresh rather than resuming inconsistent state (#409).
            try:
                # #1511: snapshot chat.history before handing it to
                # _save_history. The SDK can mutate the list concurrently
                # (AFC continuation appends, background turn finalisers)
                # and iteration over the live list risks "list changed
                # size during iteration" at the part-dump loop.
                await _save_history(session_id, list(chat.history or []))
                if history_save_failed is not None:
                    history_save_failed.discard(session_id)
            except Exception as _save_exc:
                logger.error(
                    "Permanently failed to save session history for %r: %s",
                    session_id,
                    _save_exc,
                    exc_info=True,
                )
                if backend_session_history_save_errors_total is not None:
                    backend_session_history_save_errors_total.labels(**_LABELS).inc()
                if history_save_failed is not None:
                    history_save_failed.add(session_id)

        if backend_sdk_query_duration_seconds is not None:
            backend_sdk_query_duration_seconds.labels(**_LABELS, model=_sanitize_model_label(resolved_model)).observe(
                time.monotonic() - _query_start
            )
        if backend_sdk_messages_per_query is not None:
            backend_sdk_messages_per_query.labels(**_LABELS, model=_sanitize_model_label(resolved_model)).observe(
                _message_count
            )
        if backend_sdk_turns_per_query is not None:
            backend_sdk_turns_per_query.labels(**_LABELS, model=_sanitize_model_label(resolved_model)).observe(
                _turn_count
            )
        if backend_text_blocks_per_query is not None:
            backend_text_blocks_per_query.labels(**_LABELS, model=_sanitize_model_label(resolved_model)).observe(
                len(collected)
            )
        # Tokens-per-query (#813). Parity with claude's
        # backend_sdk_tokens_per_query. Falls back to 0 when the
        # response usage_metadata is missing.
        if backend_sdk_tokens_per_query is not None:
            try:
                backend_sdk_tokens_per_query.labels(
                    **_LABELS,
                    model=_sanitize_model_label(resolved_model),
                ).observe(int(_total_tokens or 0))
            except Exception:
                pass
        if _total_tokens is not None and max_tokens is not None and max_tokens > 0:
            if backend_context_tokens is not None:
                backend_context_tokens.labels(**_LABELS).observe(_total_tokens)
            if backend_context_tokens_remaining is not None:
                backend_context_tokens_remaining.labels(**_LABELS).observe(max(0, max_tokens - _total_tokens))
            _pct = _total_tokens / max_tokens * 100
            if backend_context_usage_percent is not None:
                backend_context_usage_percent.labels(**_LABELS).observe(_pct)
            if _pct >= 100 and backend_context_exhaustion_total is not None:
                backend_context_exhaustion_total.labels(**_LABELS).inc()
            elif _pct >= 80 and backend_context_warnings_total is not None:
                backend_context_warnings_total.labels(**_LABELS).inc()

        try:
            ts = datetime.now(timezone.utc).isoformat()
            _trace_entry = {
                "ts": ts,
                "agent": AGENT_NAME,
                "agent_id": AGENT_ID,
                "session_id": session_id,
                "event_type": "response",
                "model": resolved_model,
                "chunks": len(collected),
            }
            await log_trace(json.dumps(_trace_entry))
        except Exception as e:
            logger.error(f"log_trace error: {e}")

        # Promote the session in the LRU while still holding the session
        # lock so a concurrent request cannot trigger MAX_SESSIONS eviction
        # (which calls os.remove on the on-disk history) between our save
        # and promotion (#675). _run_inner also calls _track_session on
        # the success path; that call is a no-op move_to_end when we've
        # already registered the session here.
        if sessions is not None:
            _track_session(sessions, session_id, session_locks, history_save_failed)

        return collected
    finally:
        # Drop our refcount; the lock entry is evicted from session_locks only
        # when no other task holds or is waiting on it (#483).
        _release_session_lock(session_id, session_locks)


async def run(
    prompt: str,
    session_id: str,
    sessions: OrderedDict[str, float],
    agent_md_content: str,
    session_locks: dict[str, "_RefCountedLock"],
    history_save_failed: set[str] | None = None,
    model: str | None = None,
    max_tokens: int | None = None,
    on_chunk: Callable[[str], Awaitable[None]] | None = None,
    live_mcp_servers: list | None = None,
) -> str:
    if backend_concurrent_queries is not None:
        backend_concurrent_queries.labels(**_LABELS).inc()
    try:
        return await _run_inner(
            prompt,
            session_id,
            sessions,
            agent_md_content,
            session_locks,
            history_save_failed,
            model,
            max_tokens,
            on_chunk=on_chunk,
            live_mcp_servers=live_mcp_servers,
        )
    finally:
        if backend_concurrent_queries is not None:
            backend_concurrent_queries.labels(**_LABELS).dec()


async def _run_inner(
    prompt: str,
    session_id: str,
    sessions: OrderedDict[str, float],
    agent_md_content: str,
    session_locks: dict[str, "_RefCountedLock"],
    history_save_failed: set[str] | None = None,
    model: str | None = None,
    max_tokens: int | None = None,
    on_chunk: Callable[[str], Awaitable[None]] | None = None,
    live_mcp_servers: list | None = None,
) -> str:
    resolved_model = model or GEMINI_MODEL
    if backend_model_requests_total is not None:
        backend_model_requests_total.labels(**_LABELS, model=_sanitize_model_label(resolved_model)).inc()

    # Treat sessions whose history failed to persist as new — resuming from a
    # partially-written or missing history file could produce incorrect context (#409).
    _save_failed = history_save_failed is not None and session_id in history_save_failed
    is_new = _save_failed or (
        session_id not in sessions and not await asyncio.to_thread(_session_file_exists, session_id)
    )
    if not is_new and backend_session_idle_seconds is not None:
        _last_used = sessions.get(session_id)
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
                session_locks,
                history_save_failed,
                model=model,
                max_tokens=max_tokens,
                on_chunk=on_chunk,
                live_mcp_servers=live_mcp_servers,
                sessions=sessions,
            ),
            timeout=TASK_TIMEOUT_SECONDS,
        )
        _track_session(sessions, session_id, session_locks, history_save_failed)
        # Lock-entry hygiene (#401) is now handled by the refcount in
        # run_query's finally clause so the pop cannot race with another
        # waiter (#483).
    except asyncio.TimeoutError:
        logger.error(f"Session {session_id!r}: timed out after {TASK_TIMEOUT_SECONDS}s.")
        # Evict the session from the LRU cache on timeout. The underlying
        # ChatSession may be in an inconsistent state after a mid-stream
        # cancellation; removing it ensures the next call for this session_id
        # starts fresh rather than attempting to resume a broken session.
        sessions.pop(session_id, None)
        # Prune the timed-out session from history_save_failed so the set
        # does not grow unbounded across cycling session IDs (#485).
        if history_save_failed is not None:
            history_save_failed.discard(session_id)
        # Lock-entry hygiene on timeout is handled by run_query's finally
        # (refcount release). Popping here while another waiter holds the
        # lock would reintroduce the #483 race.
        # Also remove the on-disk history file so the next request for this
        # session_id starts with empty history rather than reloading the
        # potentially stale or mid-stream snapshot written before the timeout.
        # Bump the cleanup epoch BEFORE waiting + removing (#732) so any
        # in-flight writer observes the advance and its
        # _write_history_respecting_epoch helper refuses to os.replace
        # after its to_thread returns — this closes the remaining race
        # the done-Event 5s wait could not fully cover under I/O
        # pressure (writer stuck between write-to-tmp and replace).
        _bump_cleanup_epoch(session_id)
        # Wait (briefly) for any in-flight writer to signal completion so
        # the subsequent os.remove is not raced by a late os.replace that
        # resurrects the file (#674).
        _pending_save = _history_write_done.get(session_id)
        if _pending_save is not None:
            try:
                await asyncio.wait_for(_pending_save.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                logger.warning(
                    "History save for timed-out session %r did not complete "
                    "within 5s — removing file anyway; the epoch guard "
                    "(#732) will block any late os.replace.",
                    session_id,
                )
        _timeout_path = _session_path(session_id)
        try:
            os.remove(_timeout_path)
            logger.info("Removed stale session file for timed-out session %r", session_id)
        except FileNotFoundError:
            pass
        except OSError as _e:
            logger.warning("Could not remove session file for timed-out session %r: %s", session_id, _e)
        # Bump again AFTER os.remove so a writer that captured the
        # pre-first-bump epoch but hasn't yet dispatched its to_thread
        # still sees a mismatch on recheck.  Two bumps are cheap and
        # make the "captured mid-cleanup" window impossible to hit.
        _bump_cleanup_epoch(session_id)
        # Symmetric with the LRU eviction path (line ~1160/1173): pop
        # the bookkeeping dicts so a stream of unique session IDs
        # timing out repeatedly does not leak entries into
        # _session_cleanup_epoch / _history_write_done (#942).  The
        # second-bump above is the final read of the epoch for this
        # session on this cleanup path — any later writer for this
        # session_id starts a fresh epoch at 1 via _bump_cleanup_epoch
        # above in run_query.
        _session_cleanup_epoch.pop(session_id, None)
        if _history_write_done is not None:
            _history_write_done.pop(session_id, None)
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
        _track_session(sessions, session_id, session_locks, history_save_failed)
        # Lock-entry hygiene handled by run_query's finally (#483).
    except Exception:
        # Lock-entry hygiene handled by run_query's finally (#483); popping
        # here races with other waiters.
        # Do NOT discard session_id from history_save_failed here (#520).
        # run_query's own except block intentionally ADDS session_id to
        # history_save_failed before re-raising (#493, #499), so the next
        # request for this session starts fresh rather than resuming a
        # partially-advanced chat.history. Discarding it here would silently
        # undo that protection. Unbounded-growth concerns (#485) are
        # addressed on the success and budget-exceeded paths via
        # _track_session's LRU-aligned pruning, and on the timeout path
        # above (which also removes the on-disk session file).
        #
        # #1059: for sessions that errored BEFORE _track_session ran, the
        # LRU-aligned pruning never touches _session_cleanup_epoch /
        # _history_write_done, so a burst of unique session_ids that all
        # error will leak bookkeeping entries. Only the timeout branch
        # above (#942) popped them. Do the same here as a safety net when
        # the session never made it into the LRU.
        if session_id not in sessions:
            _session_cleanup_epoch.pop(session_id, None)
            if _history_write_done is not None:
                _history_write_done.pop(session_id, None)
        if backend_tasks_total is not None:
            backend_tasks_total.labels(**_LABELS, status="error").inc()
        if backend_task_error_duration_seconds is not None:
            backend_task_error_duration_seconds.labels(**_LABELS).observe(time.monotonic() - _start)
        if backend_task_last_error_timestamp_seconds is not None:
            backend_task_last_error_timestamp_seconds.labels(**_LABELS).set(time.time())
        raise

    if backend_tasks_total is not None:
        backend_tasks_total.labels(**_LABELS, status="budget_exceeded" if _budget_exceeded else "success").inc()
    if backend_task_last_success_timestamp_seconds is not None:
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
    """A2A agent executor backed by the google-genai SDK.

    Subclass of ``a2a.server.agent_execution.AgentExecutor`` that implements
    the A2A ``execute`` / ``cancel`` contract by driving google-genai chat
    sessions. The constructor validates ``GEMINI_API_KEY`` /
    ``GOOGLE_API_KEY`` at startup (#417) so missing credentials surface
    immediately rather than on the first request, and owns the executor's
    mutable runtime state:

    - ``_sessions`` — per-session LRU of last-active timestamps; pairs with
      ``_session_locks`` (a dict of ``_RefCountedLock``) so concurrent
      requests for the same session id serialise without leaking lock
      objects for inactive sessions.
    - ``_running_tasks`` — A2A ``task_id`` → ``asyncio.Task`` map populated
      on entry to ``execute`` and cleared in its ``finally``; ``cancel()``
      and ``close()`` use it to drain in-flight requests.
    - ``_agent_md_content`` / ``_agent_md_revision`` — cached GEMINI.md
      contents and the SHA-256 hex prefix stamped on
      ``backend_agent_md_revision`` (#1751, mirrors codex #1097); refreshed
      by ``agent_md_watcher`` on every successful reload.
    - ``_history_save_failed`` — session ids whose history could not be
      persisted; on the next request those sessions are treated as new
      rather than resuming potentially inconsistent state (#409).
    - ``_hook_state`` — ``HookState`` holding the baseline + extensions rule
      lists (#631). Gemini's AFC executes tool calls inside
      ``generate_content`` so PreToolUse hooks never fire — the
      ``backend_hooks_enforcement_mode`` gauge is stamped to ``0``
      ("skeleton") until #1863 hand-rolls the tool loop. The
      ``hook.decision`` side-channel wiring is pre-installed (#963) so the
      eventual enforcement path can decide-then-post in one call.
    - ``_mcp_config`` / ``_mcp_stack`` / ``_live_mcp_servers`` — lifespan-
      scoped MCP session stack (#640, mirrors codex #526). MCP stdio
      subprocesses are entered once at startup or on hot-reload and reused
      across requests.
    - ``_mcp_servers_lock`` — lazy ``asyncio.Lock`` so off-loop construction
      (unit tests) still works; production callers must invoke
      ``bind_to_event_loop()`` from ``main()`` on the serving loop to
      eliminate "Lock bound to different event loop" hazards (#1509).
    - ``_mcp_known_servers`` — every server name that has had
      ``backend_mcp_server_up`` set non-zero, so hot-reload / shutdown can
      zero-out gauges for servers removed from the new config and
      false-OK alerting cannot hide a renamed/removed server (#884).
    - ``_mcp_stack_refcount`` + ``_mcp_old_stacks`` — refcount of in-flight
      requests holding the current stack and a list of parked old stacks
      with park timestamps. A hot reload swaps in a new stack and parks
      the old; old stacks are ``aclose()``d when the last holder releases
      (#673, mirror of codex #667). The watchdog (#735) force-closes
      stacks lingering past ``_mcp_parked_stack_max_age_s``, and the hard
      grace factor (#995, env-tunable via ``MCP_PARKED_STACK_HARD_GRACE_FACTOR``)
      eventually reclaims even refcount>0 entries so a leaked reference
      cannot retain a stdio subprocess + pipes indefinitely.
    """

    def __init__(self):
        # Validate API key at startup so missing credentials surface immediately
        # rather than on the first request (#417).
        _key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY") or None
        if not _key:
            raise RuntimeError("No Gemini API key configured. Set GEMINI_API_KEY or GOOGLE_API_KEY before starting.")
        self._sessions: OrderedDict[str, float] = OrderedDict()
        self._session_locks: dict[str, _RefCountedLock] = {}
        self._running_tasks: dict[str, asyncio.Task] = {}
        self._agent_md_content: str = _load_agent_md()
        # SHA-256 hex prefix of the currently-active GEMINI.md (#1751).
        # Mirrors codex #1097: stamped on backend_agent_md_revision and
        # refreshed by agent_md_watcher on each successful reload so
        # operators can verify the rollout from /metrics.
        self._agent_md_revision: str = _compute_agent_md_revision(self._agent_md_content)
        self._stamp_agent_md_revision(self._agent_md_revision, previous=None)
        self._mcp_watcher_tasks: list[asyncio.Task] = []
        # Session IDs whose history could not be persisted. On next request,
        # these sessions are treated as new rather than resuming potentially
        # inconsistent state (#409).
        self._history_save_failed: set[str] = set()
        # Hooks policy state (#631). Baseline rules ship with the image; the
        # extensions list starts empty and is populated by
        # ``hooks_config_watcher`` on startup and on every subsequent
        # hooks.yaml change. Held by reference so any future tool-call path
        # (#1863) sees the latest rule set without re-reading the file.
        self._hook_state: HookState = HookState(
            baseline_enabled=HOOKS_BASELINE_ENABLED,
            baseline=list(BASELINE_RULES) if HOOKS_BASELINE_ENABLED else [],
            extensions=[],
        )
        # Report hook enforcement mode to Prometheus (#736). Gemini's AFC
        # runs the tool-call loop inside google-genai's generate_content,
        # so PreToolUse hooks never fire even when hooks.yaml is loaded.
        # We publish the "skeleton" sentinel (0) so dashboards and alert
        # rules can distinguish this from the claude backend's
        # "enforcing" (1). When #1863 adds tool-loop interposition we can flip this
        # to 1 in one place.
        if backend_hooks_enforcement_mode is not None:
            backend_hooks_enforcement_mode.labels(**_LABELS).set(0)
        # hook.decision harness side-channel (#963). Skeleton only: the
        # AFC-off path that will actually evaluate PreToolUse denies
        # lands in #1863. Wiring the import + helper now means the
        # eventual enforcement path calls a function that already exists
        # and uses the same shared/hook_events.schedule_post transport as
        # claude + codex, avoiding a drift window where gemini evaluates
        # locally but the harness side-channel is silent.
        try:
            import hook_events as _hev  # noqa: F401

            self._hook_events_ready = True
        except Exception:  # pragma: no cover — shared mount failure
            self._hook_events_ready = False
        # Lifespan-scoped MCP session stack (#640 — mirrors codex #526).
        # MCP stdio subprocesses are entered once at startup (or on
        # hot-reload) and reused across requests. The lock serialises
        # reload-vs-request access to ``_live_mcp_servers`` so an in-flight
        # ``generate_content`` call with AFC cannot see a half-torn-down
        # ClientSession mid-stream.
        self._mcp_config: dict = {}
        self._mcp_stack: AsyncExitStack | None = None
        self._live_mcp_servers: list = []
        # #1509: Kept as Optional + lazy so unit-test paths that
        # instantiate AgentExecutor off-loop can still construct the
        # instance. Production callers should invoke
        # ``bind_to_event_loop()`` from main() on the serving loop to
        # eliminate "Lock bound to different event loop" hazards.
        self._mcp_servers_lock: asyncio.Lock | None = None
        # Track every MCP server name that has had backend_mcp_server_up
        # set to a non-zero value so hot-reload / shutdown can zero-out
        # the gauge for servers that were removed from the new config
        # (#884). Without this set the gauge remains at 1 for long-gone
        # servers, producing false-OK alerting after a server is
        # removed or renamed.
        self._mcp_known_servers: set[str] = set()
        # Refcount of in-flight requests holding the current stack. When a
        # hot-reload swaps in a new stack while this is > 0, the old stack is
        # parked in _mcp_old_stacks and only aclose()d when the last
        # user releases (#673, mirror of codex #667).
        #
        # Watchdog (#735): parked stacks also carry the monotonic time at
        # which they were parked so the watchdog task can force-close any
        # entry whose pairing failed (caller lost reference, exception
        # skipped the release finally, etc.).  Without the watchdog a
        # single refcount/release mismatch leaked a stdio subprocess +
        # pipe pair indefinitely across reloads.
        self._mcp_stack_refcount: int = 0
        self._mcp_old_stacks: list[tuple[AsyncExitStack, int, float]] = []
        # Max age a parked stack may linger before the watchdog force-
        # closes it.  Longer than TASK_TIMEOUT_SECONDS so any genuinely
        # in-flight request gets the natural release path; shorter than
        # a workday so a leaked reference doesn't accumulate overnight.
        self._mcp_parked_stack_max_age_s: float = max(
            float(TASK_TIMEOUT_SECONDS) * 2.0,
            600.0,
        )
        # Hard grace factor (#995): after _PARKED_STACK_HARD_GRACE_FACTOR
        # × max_age_s have elapsed, the watchdog force-closes even when
        # refcount > 0. #885 introduced the refcount>0 skip to protect
        # in-flight callers, but a genuinely leaked refcount (exception
        # skipped finally, reference lost in a refactor) now stayed
        # parked forever with a one-shot WARN and then silent leak.
        # 4× gives an already-generous in-flight request another ~40
        # minutes beyond TASK_TIMEOUT_SECONDS × 2 before the subprocess
        # + pipes are finally reclaimed.
        self._mcp_parked_stack_hard_grace_factor: float = float(
            os.environ.get("MCP_PARKED_STACK_HARD_GRACE_FACTOR", "4.0")
        )

    def _post_hook_decision_event(
        self,
        *,
        session_id: str,
        tool: str,
        decision: str,
        rule_name: str,
        reason: str,
        source: str = "baseline",
        traceparent: str | None = None,
    ) -> None:
        """Post a hook.decision event to the harness side-channel (#963).

        gemini's PreToolUse path is blocked on the tool-loop interposition
        tracked by #1863, so this helper has no inline caller yet. Adding the
        full wiring now means the eventual enforcement path can
        decide-then-post in one call without a second plumbing change,
        keeping gemini consistent with claude (#779) and codex (#937).
        Fire-and-forget via schedule_post so a transport stall cannot
        back-pressure the evaluator.

        #1001 hardening:
          * Lazy re-probe the ``hook_events`` import on every call so a
            late-mount (rolling update that landed shared/ after pod
            start) flips _hook_events_ready to True the first time the
            helper is invoked rather than staying latched False.
          * Flip backend_hooks_enforcement_mode from skeleton (0) to
            enforcing (1) on the first successful schedule_post so
            dashboards and alerts notice the transition.
        """
        if not getattr(self, "_hook_events_ready", False):
            # Late-mount re-probe: shared/hook_events.py may have been
            # mounted after __init__ captured the import state. A one-
            # line re-import here is cheap under Python's module cache
            # and promotes the flag as soon as the mount lands.
            try:
                import hook_events as _hev_probe  # noqa: F401

                self._hook_events_ready = True
            except Exception:
                return
        try:
            import hook_events as _hev

            _hev.schedule_post(
                {
                    # #1149: send BACKEND id (gemini/claude/codex), not
                    # the named witwave agent — see claude/executor.py
                    # _event_dict for the full rationale.
                    "agent": _BACKEND_ID,
                    "session_id": session_id,
                    "tool": tool,
                    "decision": decision,
                    "rule_name": rule_name,
                    "reason": reason,
                    "source": source,
                    "traceparent": traceparent,
                }
            )
            # First-fire transition: promote the skeleton gauge (0) to
            # enforcing (1) so cross-backend dashboards light up the
            # moment gemini's evaluation path actually posts.
            if not getattr(self, "_hook_enforcement_mode_promoted", False):
                self._hook_enforcement_mode_promoted = True
                if backend_hooks_enforcement_mode is not None:
                    try:
                        backend_hooks_enforcement_mode.labels(**_LABELS).set(1)
                    except Exception:
                        pass
            if backend_hooks_evaluations_total is not None:
                try:
                    backend_hooks_evaluations_total.labels(
                        **_LABELS,
                        tool=tool or "unknown",
                        decision=decision,
                    ).inc()
                except Exception:
                    pass
        except Exception as _hev_exc:
            logger.debug("hook.decision transport scheduling failed: %r", _hev_exc)

    async def bind_to_event_loop(self) -> None:
        """Construct loop-bound primitives on the serving event loop (#1509).

        Call this from main() once the uvicorn loop is running. Without
        this, lazy construction of _mcp_servers_lock in the first /mcp
        or hot-reload path could bind it to whichever loop happened to
        be current — typically fine in production but brittle under
        tests that mix asyncio.run() with pre-built executors, and
        cross-loop use surfaces as "Lock bound to different event loop".

        Idempotent: safe to call twice; a second call is a no-op.
        """
        if self._mcp_servers_lock is None:
            self._mcp_servers_lock = asyncio.Lock()

    def _mcp_watchers(self):
        """Return callables for GEMINI.md, hooks.yaml, mcp.json, parked-stack watchdog,
        and the API key secret-file rotator (#371, #631, #640, #735, #1057)."""
        return [
            self.agent_md_watcher,
            self.hooks_config_watcher,
            self.mcp_config_watcher,
            self.mcp_parked_stacks_watchdog,
            self.api_key_file_watcher,
        ]

    def _stamp_agent_md_revision(self, current: str, previous: str | None) -> None:
        """Update backend_agent_md_revision for a (possibly) new revision (#1751).

        Mirrors backends/openai/executor.py::AgentExecutor._stamp_agent_md_revision
        added in #1097: clear the prior revision's label set via .remove(...)
        so only the live revision reports value 1, then .set(1) on the new
        label set. All calls are best-effort — prometheus registry churn must
        never break a hot-reload.
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

    async def api_key_file_watcher(self) -> None:
        """Watch the API key file and refresh the genai.Client on change (#1057).

        Operators rotating the Gemini key previously had to restart every
        pod: ``_get_client`` cached the client for the process lifetime,
        and ``_close_client`` was never invoked outside shutdown. This
        watcher closes the cached client and re-reads the env on every
        change to ``GEMINI_API_KEY_FILE`` so the next request constructs
        a fresh client against the rotated key.

        Disabled when ``GEMINI_API_KEY_FILE`` is unset (key is sourced from
        the literal env var only — no file to watch). Uses the same
        watchfiles.awatch pattern as every other watcher in this module
        so the restart / watcher_events metrics remain comparable.
        """
        key_file = os.environ.get("GEMINI_API_KEY_FILE", "").strip()
        if not key_file:
            # No file-backed key configured. Log once and return — the
            # watcher is deliberately structural so it's always listed
            # alongside its peers but becomes a no-op when unused.
            logger.info(
                "api_key_file_watcher: GEMINI_API_KEY_FILE unset; key rotation "
                "via secret-file is disabled. Set GEMINI_API_KEY_FILE to a path "
                "for hot rotation without pod restart. (#1057)"
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
                                "api_key_file_watcher: %r changed — "
                                "closing cached genai.Client so the next "
                                "request picks up the rotated key.",
                                key_file,
                            )
                            # #1505: serialise env mutation + _close_client
                            # so two rapid rotations can't interleave (env
                            # for rotation A with close for rotation B).
                            # Read-then-write under the rotation lock so
                            # a concurrent _get_client never observes a
                            # half-rotated (new env, closed client or
                            # stale env, live client) state window.
                            async with _get_api_key_rotation_lock():
                                try:
                                    with open(key_file) as _fh:
                                        _new_key = _fh.read().strip()
                                    if _new_key:
                                        os.environ["GEMINI_API_KEY"] = _new_key
                                except Exception as _read_exc:
                                    logger.warning(
                                        "api_key_file_watcher: failed to read %r: %r",
                                        key_file,
                                        _read_exc,
                                    )
                                # #1621: flag rotation instead of nulling
                                # the cached client. The next _get_client
                                # call builds the new instance and only
                                # swaps on success — a failed build keeps
                                # the previous client live so traffic
                                # doesn't stall on a None singleton.
                                try:
                                    _request_client_rotation()
                                except Exception as _rot_exc:
                                    logger.warning(
                                        "api_key_file_watcher: _request_client_rotation raised %r",
                                        _rot_exc,
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

    async def mcp_parked_stacks_watchdog(self) -> None:
        """Periodic watchdog that force-closes parked MCP stacks older than
        ``self._mcp_parked_stack_max_age_s`` (#735).

        Guards against a caller that acquired the stack but never paired
        ``_release_mcp_stack`` — an exception skipped the finally, a
        future refactor lost the reference, etc.  Without this, every
        such mismatch leaks a stdio subprocess plus its ClientSession
        pipes for the lifetime of the process.

        The watchdog respects the ``_mcp_servers_lock`` so it never
        races a concurrent reload or release.  Entries it closes are
        logged at WARNING so operators see leaks surface in the logs
        instead of silently accumulating.
        """
        # Tick at a fraction of the max-age so a stuck stack is closed
        # within ~1/4 of the budget regardless of when it was parked.
        interval = max(30.0, self._mcp_parked_stack_max_age_s / 4.0)
        while True:
            try:
                await asyncio.sleep(interval)
            except asyncio.CancelledError:
                return
            if self._mcp_servers_lock is None:
                continue
            async with self._mcp_servers_lock:
                now = time.monotonic()
                still_parked: list[tuple[AsyncExitStack, int, float]] = []
                to_close: list[tuple[AsyncExitStack, int, float]] = []
                for entry in self._mcp_old_stacks:
                    _old_stack, _old_ref, parked_at = entry
                    # Only force-close when stale AND no callers are still
                    # using the parked stack (#885). Closing while in-use
                    # aborts an in-flight AFC round-trip on the victim stack.
                    # Stale-but-in-use entries stay parked; we log a one-shot
                    # WARN so operators notice a caller that never released
                    # rather than silently aborting their request.
                    age = now - parked_at
                    hard_grace_s = self._mcp_parked_stack_max_age_s * max(1.0, self._mcp_parked_stack_hard_grace_factor)
                    if age > self._mcp_parked_stack_max_age_s:
                        if _old_ref <= 0:
                            to_close.append(entry)
                        elif age > hard_grace_s:
                            # #995: refcount never dropped to 0 despite
                            # the generous grace window — treat as a
                            # leaked reference and force-close to
                            # reclaim the subprocess + pipes. The
                            # in-flight caller (if any) is already
                            # beyond the protection budget; the
                            # alternative is indefinite leak.
                            logger.warning(
                                "MCP watchdog: parked stack age=%.1fs exceeded "
                                "hard-grace (%.1fs) with refcount=%d > 0 — "
                                "force-closing to reclaim subprocess and pipes "
                                "(#995). Original refcount protection: #885.",
                                age,
                                hard_grace_s,
                                _old_ref,
                            )
                            to_close.append(entry)
                        else:
                            still_parked.append(entry)
                            if not getattr(_old_stack, "_watchdog_stale_warned", False):
                                try:
                                    _old_stack._watchdog_stale_warned = True
                                except Exception:
                                    pass
                                logger.warning(
                                    "MCP watchdog: parked stack stale (age=%.1fs) "
                                    "but refcount=%d > 0 — leaving intact to "
                                    "avoid aborting an in-flight call (#885); "
                                    "hard-grace force-close at age %.1fs (#995).",
                                    age,
                                    _old_ref,
                                    hard_grace_s,
                                )
                    else:
                        still_parked.append(entry)
                self._mcp_old_stacks = still_parked
            for old_stack, old_ref, parked_at in to_close:
                logger.warning(
                    "MCP watchdog: force-closing parked stack (refcount=%d, "
                    "age=%.1fs) — acquire/release pairing failed (#735).",
                    old_ref,
                    now - parked_at,
                )
                try:
                    await old_stack.aclose()
                except Exception as _close_exc:
                    logger.warning(
                        "Watchdog aclose of parked MCP stack failed: %s",
                        _close_exc,
                    )

    async def _apply_mcp_config(self, mcp_config: dict) -> None:
        """Enter the given MCP config into a fresh lifespan-scoped stack (#640).

        Mirrors codex.AgentExecutor._apply_mcp_config (#526). Tears down
        any previously-entered stack first, then opens a fresh
        ``stdio_client`` + ``ClientSession`` per server. Failures on
        individual servers are logged and skipped so one broken entry does
        not prevent others from starting. The ``backend_mcp_servers_active`` gauge
        reflects the actually-running count, not the config-loaded count.

        The ``ClientSession`` objects are what google-genai's AFC accepts in
        ``GenerateContentConfig(tools=[...])`` — they are the authoritative
        handle used everywhere downstream.
        """
        if self._mcp_servers_lock is None:
            self._mcp_servers_lock = asyncio.Lock()
        async with self._mcp_servers_lock:
            # Park the previous stack rather than closing it immediately
            # (#673). In-flight generate_content / AFC calls may still be
            # using its ClientSessions; only aclose once every caller has
            # released via _release_mcp_stack.
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
                    self._mcp_old_stacks.append(
                        (_prev_stack, _prev_refcount, time.monotonic()),
                    )

            if not mcp_config:
                if backend_mcp_servers_active is not None:
                    backend_mcp_servers_active.labels(**_LABELS).set(0)
                return

            try:
                from mcp import ClientSession  # type: ignore
                from mcp.client.stdio import stdio_client  # type: ignore
            except Exception as _imp_exc:
                logger.warning(
                    "mcp package not available (%s); MCP support disabled.",
                    _imp_exc,
                )
                if backend_mcp_servers_active is not None:
                    backend_mcp_servers_active.labels(**_LABELS).set(0)
                return

            new_stack = AsyncExitStack()
            await new_stack.__aenter__()
            new_live: list = []
            try:
                for name, cfg in mcp_config.items():
                    if not isinstance(cfg, dict):
                        logger.warning(
                            "MCP server %r: config must be a dict; got %r — skipping.",
                            name,
                            type(cfg).__name__,
                        )
                        continue
                    # #814: URL-shaped MCP entries (production in-cluster
                    # Services such as http://witwave-mcp-kubernetes:8000) now
                    # map to FastMCP's streamable-http transport, with SSE
                    # as a fallback for servers that predate streamable-http.
                    # Claude and codex already branch on this shape; gemini
                    # previously rejected every URL entry and silently ran
                    # with zero MCP servers in operator-only installs.
                    url_entry = cfg.get("url") if isinstance(cfg, dict) else None
                    if isinstance(url_entry, str) and url_entry:
                        with start_span(
                            "mcp.call",
                            kind="client",
                            attributes={
                                "mcp.server": name,
                                "mcp.tool": "__start__",
                                "mcp.transport": "streamable_http",
                            },
                        ) as _mcp_span:
                            try:
                                try:
                                    from mcp.client.streamable_http import streamablehttp_client
                                except ImportError:
                                    streamablehttp_client = None  # type: ignore
                                try:
                                    from mcp.client.sse import sse_client
                                except ImportError:
                                    sse_client = None  # type: ignore
                                # #1332: auto-stamp Authorization: Bearer when
                                # MCP_TOOL_AUTH_TOKEN is set and no explicit
                                # header was supplied by the mcp.json entry.
                                _headers = dict((cfg.get("headers") if isinstance(cfg, dict) else None) or {})
                                _mcp_env_token = os.environ.get("MCP_TOOL_AUTH_TOKEN", "").strip()
                                _has_auth = any(
                                    isinstance(k, str) and k.strip().lower() == "authorization" for k in _headers
                                )
                                if _mcp_env_token and not _has_auth:
                                    _headers["Authorization"] = f"Bearer {_mcp_env_token}"
                                if streamablehttp_client is not None:
                                    read, write, _ = await new_stack.enter_async_context(
                                        streamablehttp_client(url_entry, headers=_headers or None)
                                    )
                                elif sse_client is not None:
                                    # #1344: forward auth headers on the SSE fallback
                                    # path too (matches the streamable branch). Older
                                    # mcp.client.sse.sse_client signatures may not
                                    # accept `headers=`; fall back to the 1-arg form
                                    # in that case.
                                    try:
                                        read, write = await new_stack.enter_async_context(
                                            sse_client(url_entry, headers=_headers or None)
                                        )
                                    except TypeError:
                                        read, write = await new_stack.enter_async_context(sse_client(url_entry))
                                else:
                                    raise RuntimeError("neither streamablehttp_client nor sse_client available")
                                session = await new_stack.enter_async_context(ClientSession(read, write))
                                await session.initialize()
                                new_live.append(session)
                                if backend_mcp_server_up is not None:
                                    try:
                                        backend_mcp_server_up.labels(
                                            **_LABELS,
                                            server=name,
                                        ).set(1)
                                    except Exception:
                                        pass
                                self._mcp_known_servers.add(name)
                            except Exception as _mcp_exc:
                                set_span_error(_mcp_span, _mcp_exc)
                                logger.warning(
                                    "MCP server %r (url=%s) failed to start (%s); proceeding without it.",
                                    name,
                                    url_entry,
                                    _mcp_exc,
                                )
                                if backend_mcp_server_up is not None:
                                    try:
                                        backend_mcp_server_up.labels(
                                            **_LABELS,
                                            server=name,
                                        ).set(0)
                                    except Exception:
                                        pass
                                self._mcp_known_servers.add(name)
                                if backend_mcp_server_exits_total is not None:
                                    try:
                                        backend_mcp_server_exits_total.labels(
                                            **_LABELS,
                                            server=name,
                                            reason="init_failed",
                                        ).inc()
                                    except Exception:
                                        pass
                        continue
                    params = _build_mcp_stdio_params(name, cfg)
                    if params is None:
                        continue
                    # mcp.call child span (#630) — wraps the stdio transport
                    # bring-up so the trace shows which MCP server the stack
                    # is spinning up (or failing to). kind=client reflects
                    # that the backend is dialling an external server.
                    with start_span(
                        "mcp.call",
                        kind="client",
                        attributes={"mcp.server": name, "mcp.tool": "__start__"},
                    ) as _mcp_span:
                        try:
                            read, write = await new_stack.enter_async_context(stdio_client(params))
                            session = await new_stack.enter_async_context(ClientSession(read, write))
                            await session.initialize()
                            new_live.append(session)
                            # Per-server liveness gauge (#816). Set to 1 on
                            # successful init; flipped to 0 when the stack
                            # is torn down or replaced on hot-reload.
                            if backend_mcp_server_up is not None:
                                try:
                                    backend_mcp_server_up.labels(
                                        **_LABELS,
                                        server=name,
                                    ).set(1)
                                except Exception:
                                    pass
                            self._mcp_known_servers.add(name)
                        except Exception as _mcp_exc:
                            set_span_error(_mcp_span, _mcp_exc)
                            logger.warning(
                                "MCP server %r failed to start (%s); proceeding without it.",
                                name,
                                _mcp_exc,
                            )
                            if backend_mcp_server_up is not None:
                                try:
                                    backend_mcp_server_up.labels(
                                        **_LABELS,
                                        server=name,
                                    ).set(0)
                                except Exception:
                                    pass
                            self._mcp_known_servers.add(name)
                            if backend_mcp_server_exits_total is not None:
                                try:
                                    backend_mcp_server_exits_total.labels(
                                        **_LABELS,
                                        server=name,
                                        reason="init_failed",
                                    ).inc()
                                except Exception:
                                    pass
            except Exception:
                try:
                    await new_stack.aclose()
                except Exception:
                    pass
                raise

            self._mcp_stack = new_stack
            self._live_mcp_servers = new_live
            if backend_mcp_servers_active is not None:
                backend_mcp_servers_active.labels(**_LABELS).set(len(new_live))

            # Zero-out backend_mcp_server_up for any server that was
            # present in a previous config but is absent from mcp_config
            # (#884). Without this the gauge remains at 1 forever for
            # removed/renamed servers and feeds stale OK signals into
            # alerting. We record every server we've ever seen in
            # self._mcp_known_servers and subtract the current config
            # keys; the delta gets set to 0 here.
            new_names = set(mcp_config.keys())
            removed = self._mcp_known_servers - new_names
            if removed and backend_mcp_server_up is not None:
                for _gone in removed:
                    try:
                        backend_mcp_server_up.labels(
                            **_LABELS,
                            server=_gone,
                        ).set(0)
                    except Exception:
                        pass
            # _mcp_known_servers now reflects the currently-configured
            # set plus anything still-bound to a zero gauge; keep only
            # the current config names so future reloads don't keep
            # growing the set unboundedly. The previous assignment
            # (new_names | self._mcp_known_servers) was the inverse of
            # this intent — it was a union with the full historical
            # set, so every reload monotonically grew the set (#994).
            # The correct right-hand side is new_names | removed: the
            # currently-configured servers plus the servers we just
            # zeroed out on this reload, so operators can still see
            # the zeroed gauge but names from prior reloads that are
            # neither in the current config nor in `removed` drop out.
            self._mcp_known_servers = new_names | removed

            # Startup warning re: AFC vs hooks asymmetry (#1863). Logged once
            # per stack bring-up so operators see it on every reload when
            # both sides are active. hooks skeleton in #631 cannot intercept
            # tool calls that AFC runs inside the SDK; the hand-rolled-loop
            # enforcement work is tracked by #1863.
            if (
                new_live
                and os.environ.get("HOOKS_CONFIG_PATH")
                and (self._hook_state.extensions or self._hook_state.baseline)
            ):
                logger.warning(
                    "gemini hooks skeleton (#631) cannot intercept MCP tool calls "
                    "because google-genai's AFC runs the loop internally. "
                    "See #1863 for the AFC/tool-loop interposition work required "
                    "before policy enforcement can cover Gemini MCP calls."
                )

    async def _acquire_mcp_stack(self) -> tuple[list, "AsyncExitStack | None"]:
        """Acquire the current MCP stack for one in-flight request (#673).

        Returns a snapshot of the live ClientSession list and the stack the
        caller is now holding a refcount on. MUST be paired with
        _release_mcp_stack in a finally block so hot-reload can free parked
        stacks promptly.
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

        Closes a parked old stack when its refcount hits zero so
        ClientSession stdio subprocesses stop promptly without breaking
        in-flight AFC traffic (#673).
        """
        if stack is None:
            return
        if self._mcp_servers_lock is None:
            self._mcp_servers_lock = asyncio.Lock()
        async with self._mcp_servers_lock:
            if self._mcp_stack is stack:
                if self._mcp_stack_refcount > 0:
                    self._mcp_stack_refcount -= 1
                return
            for i, (old_stack, old_ref, parked_at) in enumerate(self._mcp_old_stacks):
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
                        self._mcp_old_stacks[i] = (old_stack, new_ref, parked_at)
                    return

    async def _snapshot_live_mcp_servers(self) -> list:
        """Return a defensive copy of the currently-live MCP server list (#640).

        Taken under the lock so a concurrent hot-reload cannot swap the list
        out from under the caller mid-read.
        """
        if self._mcp_servers_lock is None:
            self._mcp_servers_lock = asyncio.Lock()
        async with self._mcp_servers_lock:
            return list(self._live_mcp_servers)

    async def mcp_config_watcher(self) -> None:
        """Watch MCP_CONFIG_PATH and hot-reload the MCP server stack (#640).

        Mirrors codex.AgentExecutor.mcp_config_watcher (#432, #526): load
        on startup, then watch the parent directory for any changes to the
        config file. Each reload restarts the lifespan-scoped MCP server
        stack so stdio subprocesses are respawned cleanly under the new
        config and existing request traffic sees a consistent snapshot.
        """
        from watchfiles import awatch as _awatch

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

        This ensures that updating GEMINI.md does not require a container restart,
        consistent with all other file-based configuration in the platform.
        """
        from watchfiles import awatch as _awatch

        # Perform an initial load so the watcher starts with current content.
        self._agent_md_content = _load_agent_md()
        # #1751: refresh revision gauge on initial load so a watcher
        # restart re-stamps value 1 even if the in-memory revision was
        # already in place from __init__.
        new_rev = _compute_agent_md_revision(self._agent_md_content)
        if new_rev != self._agent_md_revision:
            self._stamp_agent_md_revision(new_rev, previous=self._agent_md_revision)
            self._agent_md_revision = new_rev
        logger.info("GEMINI.md loaded from %s", AGENT_MD)

        watch_dir = os.path.dirname(os.path.abspath(AGENT_MD))
        while True:
            if not os.path.isdir(watch_dir):
                logger.info("GEMINI.md directory not found — retrying in 10s.")
                await asyncio.sleep(10)
                continue
            async for changes in _awatch(watch_dir):
                if backend_watcher_events_total is not None:
                    backend_watcher_events_total.labels(**_LABELS, watcher="agent_md").inc()
                for _, path in changes:
                    if os.path.abspath(path) == os.path.abspath(AGENT_MD):
                        self._agent_md_content = _load_agent_md()
                        # #1751: stamp the new revision and clear the old.
                        new_rev = _compute_agent_md_revision(self._agent_md_content)
                        if new_rev != self._agent_md_revision:
                            self._stamp_agent_md_revision(new_rev, previous=self._agent_md_revision)
                            self._agent_md_revision = new_rev
                        logger.info("GEMINI.md reloaded from %s", AGENT_MD)
                        break
            logger.warning("GEMINI.md directory watcher exited — retrying in 10s.")
            if backend_file_watcher_restarts_total is not None:
                backend_file_watcher_restarts_total.labels(**_LABELS, watcher="agent_md").inc()
            await asyncio.sleep(10)

    async def hooks_config_watcher(self) -> None:
        """Watch hooks.yaml and hot-reload extension rules (#631).

        Mirrors ``backends/claude/executor.AgentExecutor.hooks_config_watcher`` so
        operators see identical semantics across backends: an initial load,
        then an ``awatch`` over the containing directory, re-parsing on every
        change to the target file. Failures during reload keep the previous
        rule set in place so a malformed edit cannot accidentally disable the
        policy.

        Note: this runs even though the gemini tool-call path is not yet
        exercising the engine (tracked by #1863). Keeping the watcher active
        means rules are ready the moment #1863 plumbs ``evaluate_pre_tool_use``
        into the dispatch path.
        """
        from watchfiles import awatch as _awatch

        self._hook_state.extensions = await asyncio.to_thread(load_hooks_config_sync)
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
            async for changes in _awatch(watch_dir):
                if backend_watcher_events_total is not None:
                    backend_watcher_events_total.labels(**_LABELS, watcher="hooks").inc()
                for _, path in changes:
                    if os.path.abspath(path) == os.path.abspath(HOOKS_CONFIG_PATH):
                        try:
                            new_rules = await asyncio.to_thread(load_hooks_config_sync)
                            self._hook_state.extensions = new_rules
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

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        """Run one A2A request through the google-genai SDK and reply on ``event_queue``.

        Validation gates run before any API call is made or token is burned:
        empty / whitespace-only prompts are rejected with a
        ``backend_empty_prompts_total`` bump, an A2A error message, and a
        ``"system"`` row in the conversation log (#544 / #812); prompts
        larger than ``_MAX_PROMPT_BYTES`` are rejected with
        ``backend_prompt_too_large_total`` and a ``PromptTooLargeError``
        (#1620 / #1730). The session id is derived through
        ``shared/session_binding.py`` with optional per-caller binding when
        ``SESSION_ID_SECRET`` is set (#733 / #888) and probe-list rotation
        when ``SESSION_ID_SECRET_PREV`` is also set so an in-progress secret
        rotation can still resume an existing on-disk history (#1042);
        rejection paths route their session id through the same derivation
        so log / metric rows do not split across raw vs derived ids. When
        the upstream harness forwards a ``traceparent`` in
        ``message.metadata`` the OTel server span ``gemini.execute``
        continues that trace (#469).

        Streaming: each text chunk is forwarded to the per-session SSE
        broadcaster (``session_stream``) via ``_emit_chunk`` so the
        dashboard's session drill-down sees text as it arrives (#1110 phase
        4). Per-chunk Message events on the A2A wire are intentionally NOT
        emitted because the A2A SDK's ``ResultAggregator`` treats every
        ``Message`` as terminal — only one final aggregated
        ``new_agent_text_message`` is enqueued at completion. A terminal
        ``final=True`` session-stream chunk is published on both success
        and error paths (#1141) so observers see a deterministic turn
        boundary.

        Per-task bookkeeping: when ``context.task_id`` is set the current
        asyncio task is registered in ``self._running_tasks`` (and
        ``backend_running_tasks`` bumped) on entry, then removed in the
        ``finally`` block alongside the request-duration / last-request
        timestamp / requests-total metric updates. This lets ``cancel()``
        and ``close()`` find and drain in-flight requests. The lifespan-
        scoped MCP stack refcount is acquired before the ``run`` call and
        released in a nested ``finally`` so a hot-reload can park the old
        stack without yanking servers out from under an in-flight request
        (#673).
        """
        _exec_start = time.monotonic()
        prompt = context.get_user_input()
        metadata = context.message.metadata or {}
        # Empty-prompt guard (#544 / #812). Mirrors claude/codex: reject
        # whitespace-only prompts before they reach the Gemini API and
        # mis-classify as a model error. Bump counters, write a system log
        # entry, emit an error event, and return.
        if not prompt or not prompt.strip():
            _empty_sid_raw = str(
                context.context_id or (context.message.metadata or {}).get("session_id") or ""
            ).strip()[:256]
            _empty_sid_sanitized = "".join(c for c in _empty_sid_raw if c >= " ") or "unknown"
            # Route through derive_session_id (#888) so the log entry and
            # every metric label uses the HMAC-bound session id under
            # SESSION_ID_SECRET — otherwise the rejected-prompt path
            # lands rows under the raw caller-supplied id while the
            # accepted path used the derived id, which leaks the
            # resumption key and mis-bins the two request categories.
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
        # Hard prompt-size cap (#1620 / #1730 — gemini parity port of
        # codex). Reject before the prompt reaches the google-genai SDK
        # so a pathological caller cannot OOM the pod by shipping a
        # multi-GB payload. Mirrors the empty-prompt rejection idiom:
        # bump a Prometheus counter, log the rejection at WARN, and
        # return a clean A2A error message rather than crashing the
        # worker. The PromptTooLargeError type is exported from
        # shared/exceptions for programmatic detection by callers/tests;
        # the executor itself converts it into a user-visible A2A text
        # message so the harness surface stays consistent with the
        # empty-prompt path.
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
        # Per-caller session_id binding (#733). See the matching block in
        # backends/claude/executor.py for the rationale and the shared
        # helper in shared/session_binding.py for the derivation rules.
        # Backward compatible: legacy derivation when SESSION_ID_SECRET
        # is unset.
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
        # Probe-list rotation (#1042). If SESSION_ID_SECRET_PREV is set we
        # compute both derivations and route resumption to whichever id
        # has an existing history JSON on disk. Parity with the
        # corresponding blocks in backends/{claude,openai}/executor.py.
        _sid_candidates = _derive_session_id_candidates(_raw_sid, caller_identity=_caller_id)
        session_id = _sid_candidates[0]
        if len(_sid_candidates) > 1:
            for _prev_sid in _sid_candidates[1:]:
                if await asyncio.to_thread(_session_file_exists, _prev_sid):
                    session_id = _prev_sid
                    _note_prev_secret_hit(_raw_sid)
                    break
        _ = _derive_session_id  # noqa: F841 — retained import for future call sites
        model = metadata.get("model") or None
        # Shared parser lives in shared/validation.py (#537, #428).
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
        # Streaming bridge (#430): forward each chunk text to the A2A
        # event_queue as it arrives. Tracks emission count so the
        # post-completion aggregated enqueue can be skipped when chunks were
        # already delivered.
        _chunks_emitted = 0
        # Pre-sanitize once for the streaming counter (#487): the inner closure
        # runs per chunk so resolving the bounded label here keeps it O(1) per
        # emit and guarantees a single canonical value for the whole request.
        _streaming_label_model = _sanitize_model_label(model or GEMINI_MODEL or "")

        # Per-session SSE drill-down stream (#1110 phase 4). Best-effort
        # — a broadcaster fault must not break the A2A response path.
        try:
            from session_stream import get_session_stream as _get_session_stream

            _sess_stream = _get_session_stream(session_id, agent_id=AGENT_OWNER)
            # Per-turn seq covers user+assistant chunks (#1139).
            _sess_stream.reset_turn_seq()
            _sess_stream.publish_chunk(
                role="user",
                seq=_sess_stream.next_turn_seq(),
                content=prompt,
                final=True,
            )
        except Exception as _sess_exc:  # pragma: no cover
            logger.warning("session_stream: user prompt publish failed: %r", _sess_exc)
            _sess_stream = None

        async def _emit_chunk(text: str) -> None:
            nonlocal _chunks_emitted
            # Per-session SSE drill-down (#1110 phase 4). Mirrors the codex
            # shape: session_stream publish stays before A2A enqueue (it's
            # allowed to fire ahead of delivery), but the chunks_emitted
            # counter and streaming-events metric only advance after the
            # enqueue succeeds (#1721, mirrors codex #1199). Counting
            # before enqueue overstated traffic on enqueue failure and
            # caused _chunks_emitted to skip the aggregated final-flush.
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
            # Per-chunk A2A event_queue emission removed — see
            # backends/claude/executor.py _emit_chunk for the rationale
            # (A2A SDK's blocking handler returns on first Message event,
            # so per-chunk Message emission caused blocking callers to
            # see only the first chunk). The streaming-events metric and
            # _chunks_emitted counter still advance: chunks DID arrive
            # at the backend from the SDK, just not onto the A2A wire.
            _chunks_emitted += 1
            if backend_streaming_events_emitted_total is not None:
                backend_streaming_events_emitted_total.labels(**_LABELS, model=_streaming_label_model).inc()

        from otel import set_span_error as _set_span_error
        from otel import start_span as _start_span

        _otel_span = None
        try:
            with _start_span(
                "gemini.execute",
                kind="server",
                parent_context=_otel_parent,
                attributes={
                    "a2.session_id": session_id,
                    "a2.model": model or GEMINI_MODEL or "",
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
                        self._session_locks,
                        history_save_failed=self._history_save_failed,
                        model=model,
                        max_tokens=max_tokens,
                        on_chunk=_emit_chunk,
                        live_mcp_servers=_mcp_servers_snapshot,
                    )
                finally:
                    # Release the hot-reload refcount (#673).
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
            _set_span_error(_otel_span, _exc)
            if backend_a2a_requests_total is not None:
                backend_a2a_requests_total.labels(**_LABELS, status="error").inc()
            # Per-session stream: emit a terminal final=True assistant
            # chunk on the failure path too (#1141) so observers see a
            # deterministic turn boundary even when execution blew up
            # mid-stream.  Best-effort.
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
        ``self._running_tasks`` (the same map populated on ``execute()``
        entry); when present, calls ``task.cancel()`` and logs the request.
        When absent, logs that no running task was found and returns without
        raising — common when the task already completed or was cancelled by
        another path (e.g. ``close()``). ``event_queue`` is accepted for the
        A2A protocol signature but is not used; the running ``execute()``'s
        own ``finally`` block emits any terminal A2A events as
        ``CancelledError`` propagates out.
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

    async def close(self) -> None:
        """Cancel and drain all watcher tasks, tear down the MCP stack (#640),
        then dispose the genai client.

        The genai client close runs *after* watchers are drained so in-flight
        A2A requests are not orphaned mid-call (#545). The MCP stack is
        torn down between the watchers and the genai client so stdio
        subprocesses and ClientSession pipes are released cleanly before
        the HTTP client pools go away.
        """
        for task in self._mcp_watcher_tasks:
            task.cancel()
        if self._mcp_watcher_tasks:
            await asyncio.gather(*self._mcp_watcher_tasks, return_exceptions=True)
        self._mcp_watcher_tasks.clear()
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
            # Zero-out backend_mcp_server_up on shutdown for every
            # known server (#884). Without this the gauge stays at 1
            # for any server we ever booted, producing false-OK alerts
            # from a pod that's already exited.
            if backend_mcp_server_up is not None:
                for _name in self._mcp_known_servers:
                    try:
                        backend_mcp_server_up.labels(
                            **_LABELS,
                            server=_name,
                        ).set(0)
                    except Exception:
                        pass
        # Drain any parked old stacks (#673) from previous hot-reloads that
        # still had in-flight holders when swapped. On shutdown we force-
        # close them regardless of refcount.
        while self._mcp_old_stacks:
            _old_stack, _old_ref, _old_parked_at = self._mcp_old_stacks.pop()
            try:
                await _old_stack.aclose()
            except Exception as _close_exc:
                logger.warning("Parked MCP stack aclose on shutdown: %s", _close_exc)
        # Drain any pending _track_session deferred-evict os.remove
        # tasks (#999) with a bounded wait so a graceful shutdown
        # actually removes the evicted session file instead of letting
        # loop-close drop the pending task. 10s is comfortably greater
        # than the per-task 5s wait on _history_write_done.
        if _EVICT_REMOVE_TASKS:
            _pending = list(_EVICT_REMOVE_TASKS)
            try:
                await asyncio.wait_for(
                    asyncio.gather(*_pending, return_exceptions=True),
                    timeout=10.0,
                )
            except asyncio.TimeoutError:
                logger.warning(
                    "Deferred session-evict removal did not drain within 10s — %d task(s) still pending; cancelling.",
                    len(_pending),
                )
                for _t in _pending:
                    if not _t.done():
                        _t.cancel()
                await asyncio.gather(*_pending, return_exceptions=True)
        await _close_client()
