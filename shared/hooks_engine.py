"""Backend-agnostic PreToolUse/PostToolUse hook policy engine (#631).

Extracted from ``backends/claude/hooks.py`` so that gemini (#631) and eventually
codex (#586) can share the same rule vocabulary, baseline predicate set,
YAML extension loader, and evaluator. The original claude module now
imports from here and keeps only the Claude-SDK-specific integration glue
(``HookMatcher`` callables, ``ClaudeAgentOptions.hooks`` wiring, baseline
on/off env plumbing).

Design notes
------------

* The engine is deliberately free of any SDK import. It operates on two
  primitive inputs — a ``tool_name`` string and a ``tool_input`` dict — and
  returns a decision tuple. Integration with a given SDK (Claude Agent SDK,
  OpenAI Agents SDK, google-genai) is the caller's job.
* Metrics are attached via an optional ``config_error_reporter`` callable so
  this module stays test-friendly and doesn't hard-bind to any one backend's
  ``metrics.py``. Backends pass a small closure that increments their own
  ``backend_hooks_config_errors_total{reason}`` counter with their label set.
* Baseline predicates use parsed tool-input structure (not a JSON substring)
  so trivial encoding bypasses (unicode escapes, whitespace padding,
  absolute paths, home-shorthand, symbolic chmod) do not defeat matching
  (#521).

See ``backends/claude/hooks.py`` for the original motivating documentation and the
``hooks.yaml`` schema.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shlex
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import yaml

logger = logging.getLogger(__name__)


# Optional reporter invoked on each YAML parse/validation failure with a
# ``reason`` string drawn from the closed enum documented in each backend's
# ``metrics.py``. Backends register their own reporter at import time; the
# default is a no-op so unit tests and non-metric callers stay simple.
ConfigErrorReporter = Callable[[str], None]


def _noop_reporter(reason: str) -> None:  # pragma: no cover — trivial
    return None


_config_error_reporter: ConfigErrorReporter = _noop_reporter


def set_config_error_reporter(reporter: ConfigErrorReporter | None) -> None:
    """Install a process-wide reporter for config-error reasons.

    Backends call this once during module import. Passing ``None`` resets
    to the no-op default, which is useful in tests.
    """
    global _config_error_reporter
    _config_error_reporter = reporter or _noop_reporter


def _bump_config_error(reason: str) -> None:
    try:
        _config_error_reporter(reason)
    except Exception:  # pragma: no cover — metrics must never break hook parsing
        pass


# Decision vocabulary — strings instead of enums so YAML-loaded rules can
# round-trip cleanly and hook callables can return the SDK-native literals
# without conversion gymnastics.
DECISION_ALLOW = "allow"
DECISION_DENY = "deny"
DECISION_WARN = "warn"


@dataclass(frozen=True)
class Rule:
    """One evaluation rule for a PreToolUse hook.

    ``tool`` is matched exactly against the caller-supplied ``tool_name`` or is
    the literal ``"*"`` / ``None`` to apply to every tool. ``action`` decides
    what happens on a match. See ``backends/claude/hooks.py`` module docstring for
    the full matching semantics and the ``pattern`` vs ``predicate`` split.
    """

    name: str
    tool: str | None  # None or "*" => any tool
    pattern: re.Pattern[str] | None
    action: str  # DECISION_DENY or DECISION_WARN
    reason: str
    source: str  # "baseline" or "extension"
    predicate: Callable[[dict[str, Any]], bool] | None = None


# ---------------------------------------------------------------------------
# Baseline predicate helpers
# ---------------------------------------------------------------------------


def _bash_command(tool_input: dict[str, Any]) -> str:
    cmd = tool_input.get("command")
    return cmd if isinstance(cmd, str) else ""


def _safe_shlex_split(command: str) -> list[str]:
    try:
        return shlex.split(command, posix=True)
    except ValueError:
        return command.split()


def _argv_basenames(command: str) -> list[str]:
    return [os.path.basename(tok) for tok in _safe_shlex_split(command)]


_RM_RF_DANGEROUS_TARGETS: frozenset[str] = frozenset(
    {
        "/",
        "/*",
        "/.",
        "/..",
        "~",
        "~root",
        "~/",
        "$HOME",
        "${HOME}",
        "/root",
        "/home",
        "/etc",
        "/var",
        "/usr",
        "/bin",
        "/sbin",
        "/boot",
        "/lib",
        "/lib64",
    }
)


def _is_rm_rf_target(token: str) -> bool:
    if not token:
        return False
    if token in _RM_RF_DANGEROUS_TARGETS:
        return True
    stripped = token.rstrip("/*")
    if stripped and stripped != token and stripped in _RM_RF_DANGEROUS_TARGETS:
        return True
    return False


def _predicate_rm_rf_root(tool_input: dict[str, Any]) -> bool:
    argv = _safe_shlex_split(_bash_command(tool_input))
    if not argv:
        return False
    for idx, tok in enumerate(argv):
        base = os.path.basename(tok)
        if base != "rm":
            continue
        rest = argv[idx + 1 :]
        has_recursive = any(
            t in ("-r", "-R", "--recursive") or (t.startswith("-") and not t.startswith("--") and "r" in t)
            for t in rest
        )
        has_force = any(
            t in ("-f", "--force", "--no-preserve-root") or (t.startswith("-") and not t.startswith("--") and "f" in t)
            for t in rest
        )
        if not (has_recursive and has_force):
            if "--no-preserve-root" not in rest:
                continue
        for candidate in rest:
            if _is_rm_rf_target(candidate):
                return True
    for idx, tok in enumerate(argv):
        if os.path.basename(tok) in {"sh", "bash", "zsh", "ksh"} and idx + 2 < len(argv) and argv[idx + 1] == "-c":
            inner = argv[idx + 2]
            if _predicate_rm_rf_root({"command": inner}):
                return True
    return False


def _predicate_git_force_push_main(tool_input: dict[str, Any]) -> bool:
    argv = _safe_shlex_split(_bash_command(tool_input))
    if not argv:
        return False
    i = 0
    while i < len(argv):
        if os.path.basename(argv[i]) == "git":
            rest = argv[i + 1 :]
            j = 0
            while j < len(rest) and rest[j].startswith("-"):
                if rest[j] in ("-C", "-c"):
                    j += 2
                else:
                    j += 1
            if j < len(rest) and rest[j] == "push":
                push_args = rest[j + 1 :]
                has_force = any(
                    a == "-f"
                    or a == "--force"
                    or a == "--force-with-lease"
                    or a.startswith("--force=")
                    or a.startswith("--force-with-lease=")
                    for a in push_args
                )
                targets_main = any(
                    a in ("main", "master")
                    or a.endswith(":main")
                    or a.endswith(":master")
                    or a.endswith("/main")
                    or a.endswith("/master")
                    for a in push_args
                )
                if has_force and targets_main:
                    return True
            i += 1
            continue
        i += 1
    return False


_SHELL_NAMES: frozenset[str] = frozenset({"sh", "bash", "zsh", "ksh", "dash", "ash"})


def _predicate_curl_pipe_shell(tool_input: dict[str, Any]) -> bool:
    command = _bash_command(tool_input)
    if not command:
        return False
    segments = re.split(r"\|+&?", command)
    if len(segments) < 2:
        return False
    for idx in range(len(segments) - 1):
        fetch = _safe_shlex_split(segments[idx])
        consumer = _safe_shlex_split(segments[idx + 1])
        fetch_names = {os.path.basename(t) for t in fetch if not t.startswith("-")}
        if not ({"curl", "wget"} & fetch_names):
            continue
        consumer_head = next(
            (t for t in consumer if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", t)),
            "",
        )
        if os.path.basename(consumer_head) in _SHELL_NAMES:
            return True
    return False


def _predicate_chmod_777(tool_input: dict[str, Any]) -> bool:
    argv = _safe_shlex_split(_bash_command(tool_input))
    if not argv:
        return False
    for idx, tok in enumerate(argv):
        if os.path.basename(tok) != "chmod":
            continue
        for mode_tok in argv[idx + 1 :]:
            if mode_tok.startswith("-"):
                continue
            if re.fullmatch(r"0*[0-7]{3,4}", mode_tok):
                digits = mode_tok.lstrip("0") or "0"
                tail = digits[-3:] if len(digits) >= 3 else digits.rjust(3, "0")
                if tail == "777":
                    return True
                break
            sym = re.fullmatch(r"([ugoa]+)([+=])([rwxXst]+)", mode_tok)
            if sym:
                who, op, perms = sym.group(1), sym.group(2), sym.group(3)
                grants_world_write = "w" in perms and ("a" in who or "o" in who)
                if grants_world_write and op in ("+", "="):
                    return True
                break
            break
    return False


_DD_DEVICE_FAMILIES: tuple[str, ...] = (
    "sd",
    "nvme",
    "disk",
    "hd",
    "vd",
    "xvd",
    "mmcblk",
    "loop",
    "mapper/",
    "md",
    "dm-",
)


def _predicate_dd_device(tool_input: dict[str, Any]) -> bool:
    argv = _safe_shlex_split(_bash_command(tool_input))
    if not argv:
        return False
    for idx, tok in enumerate(argv):
        if os.path.basename(tok) != "dd":
            continue
        for arg in argv[idx + 1 :]:
            if not arg.startswith("of="):
                continue
            target = arg[3:]
            if not target.startswith("/dev/"):
                continue
            suffix = target[len("/dev/") :]
            if any(suffix.startswith(family) for family in _DD_DEVICE_FAMILIES):
                return True
    return False


def _predicate_write_system_path(tool_input: dict[str, Any]) -> bool:
    """``Write``/``Edit``/``MultiEdit``/``NotebookEdit`` to a system path."""
    for key in ("file_path", "path", "notebook_path"):
        value = tool_input.get(key)
        if not isinstance(value, str) or not value:
            continue
        norm = os.path.normpath(value)
        if not os.path.isabs(norm):
            continue
        for root in ("/etc", "/boot", "/bin", "/sbin", "/usr", "/lib", "/lib64", "/sys", "/proc", "/dev"):
            if norm == root or norm.startswith(root + "/"):
                return True
    return False


_BASELINE_RULES_SPEC: list[dict[str, Any]] = [
    {
        "name": "baseline-rm-rf-root",
        "tool": "Bash",
        "predicate": _predicate_rm_rf_root,
        "reason": "rm -rf targeting root or a system directory — refusing by baseline policy.",
    },
    {
        "name": "baseline-git-force-push-main",
        "tool": "Bash",
        "predicate": _predicate_git_force_push_main,
        "reason": "git push --force to main/master — refusing by baseline policy.",
    },
    {
        "name": "baseline-curl-pipe-shell",
        "tool": "Bash",
        "predicate": _predicate_curl_pipe_shell,
        "reason": "curl/wget piped to a shell — refusing by baseline policy.",
    },
    {
        "name": "baseline-chmod-777",
        "tool": "Bash",
        "predicate": _predicate_chmod_777,
        "reason": "chmod world-writable (0777 or a+w/o+w) — refusing by baseline policy.",
    },
    {
        "name": "baseline-dd-device",
        "tool": "Bash",
        "predicate": _predicate_dd_device,
        "reason": "dd to a block device — refusing by baseline policy.",
    },
]

_BASELINE_WRITE_TOOLS: tuple[str, ...] = ("Write", "Edit", "MultiEdit", "NotebookEdit")


def _compile_baseline() -> list[Rule]:
    rules: list[Rule] = []
    for raw in _BASELINE_RULES_SPEC:
        rules.append(
            Rule(
                name=raw["name"],
                tool=raw.get("tool"),
                pattern=None,
                action=DECISION_DENY,
                reason=raw["reason"],
                source="baseline",
                predicate=raw["predicate"],
            )
        )
    for tool in _BASELINE_WRITE_TOOLS:
        rules.append(
            Rule(
                name="baseline-write-system-path",
                tool=tool,
                pattern=None,
                action=DECISION_DENY,
                reason="Write/Edit targeting a system path (/etc, /usr, /bin, …) — refusing by baseline policy.",
                source="baseline",
                predicate=_predicate_write_system_path,
            )
        )
    return rules


BASELINE_RULES: list[Rule] = _compile_baseline()


def _parse_extension_rule(raw: dict[str, Any]) -> Rule | None:
    name = raw.get("name")
    if not isinstance(name, str) or not name.strip():
        logger.warning("hooks.yaml: skipping entry with missing/empty name: %r", raw)
        _bump_config_error("missing_name")
        return None
    tool = raw.get("tool")
    if tool is not None and not isinstance(tool, str):
        logger.warning("hooks.yaml: rule %r has non-string tool %r — skipping.", name, tool)
        _bump_config_error("non_string_tool")
        return None

    deny_pat = raw.get("deny_if_match")
    warn_pat = raw.get("warn_if_match")
    if deny_pat and warn_pat:
        logger.warning("hooks.yaml: rule %r has both deny_if_match and warn_if_match — preferring deny.", name)
        _bump_config_error("both_patterns")
        warn_pat = None
    if not deny_pat and not warn_pat:
        logger.warning("hooks.yaml: rule %r has neither deny_if_match nor warn_if_match — skipping.", name)
        _bump_config_error("no_pattern")
        return None

    action = DECISION_DENY if deny_pat else DECISION_WARN
    pat_source = deny_pat if deny_pat else warn_pat
    if not isinstance(pat_source, str):
        logger.warning("hooks.yaml: rule %r pattern is not a string — skipping.", name)
        _bump_config_error("non_string_pattern")
        return None
    try:
        pattern = re.compile(pat_source)
    except re.error as exc:
        logger.warning("hooks.yaml: rule %r has invalid regex %r: %s — skipping.", name, pat_source, exc)
        _bump_config_error("invalid_regex")
        return None

    reason = raw.get("reason") or f"blocked by extension rule {name!r}"
    if not isinstance(reason, str):
        reason = str(reason)

    return Rule(
        name=name,
        tool=tool if tool and tool != "*" else None,
        pattern=pattern,
        action=action,
        reason=reason,
        source="extension",
    )


def load_extension_rules(path: str) -> list[Rule]:
    """Read hooks.yaml at *path* and return the parsed extension rules."""
    if not os.path.exists(path):
        return []
    try:
        with open(path) as f:
            data = yaml.safe_load(f) or {}
    except Exception as exc:
        logger.warning("Failed to load hooks.yaml from %s: %s", path, exc)
        _bump_config_error("file_load_failed")
        return []

    if not isinstance(data, dict):
        logger.warning("hooks.yaml at %s is not a mapping — ignoring.", path)
        _bump_config_error("not_mapping")
        return []

    raw_list = data.get("extensions") or []
    if not isinstance(raw_list, list):
        logger.warning("hooks.yaml at %s has non-list `extensions` — ignoring.", path)
        _bump_config_error("non_list_extensions")
        return []

    parsed: list[Rule] = []
    for raw in raw_list:
        if not isinstance(raw, dict):
            logger.warning("hooks.yaml: skipping non-mapping entry %r", raw)
            _bump_config_error("non_mapping_entry")
            continue
        rule = _parse_extension_rule(raw)
        if rule is not None:
            parsed.append(rule)
    return parsed


def _tool_matches(rule: Rule, tool_name: str) -> bool:
    return rule.tool is None or rule.tool == tool_name


def _input_haystack(tool_input: dict[str, Any]) -> str:
    try:
        return json.dumps(tool_input, default=str, ensure_ascii=False)
    except Exception:  # pragma: no cover — json with default=str is extremely permissive
        return repr(tool_input)


def evaluate_pre_tool_use(
    tool_name: str,
    tool_input: dict[str, Any],
    rules: list[Rule],
) -> tuple[str, Rule | None]:
    """Evaluate *rules* against a tool call. Returns ``(decision, matched_rule)``."""
    haystack: str | None = None
    first_warn: Rule | None = None
    for rule in rules:
        if not _tool_matches(rule, tool_name):
            continue
        matched = False
        if rule.predicate is not None:
            try:
                matched = bool(rule.predicate(tool_input))
            except Exception:  # pragma: no cover — predicates are total by contract
                logger.exception("baseline predicate %r raised; treating as no-match", rule.name)
                matched = False
        elif rule.pattern is not None:
            if haystack is None:
                haystack = _input_haystack(tool_input)
            matched = rule.pattern.search(haystack) is not None
        if not matched:
            continue
        if rule.action == DECISION_DENY:
            return DECISION_DENY, rule
        if rule.action == DECISION_WARN and first_warn is None:
            first_warn = rule
    if first_warn is not None:
        return DECISION_WARN, first_warn
    return DECISION_ALLOW, None


@dataclass
class HookState:
    """Live, mutable view of the current rule set."""

    baseline_enabled: bool = True
    baseline: list[Rule] = field(default_factory=list)
    extensions: list[Rule] = field(default_factory=list)

    def active_rules(self) -> list[Rule]:
        """Return the rule list the engine should consider for this evaluation.

        When ``baseline_enabled`` is true the baseline rules are
        returned first followed by the extensions; when false the
        baseline is suppressed and only extensions are evaluated. Both
        lists are shallow-copied so the caller can mutate freely
        without disturbing the stored state.
        """
        if self.baseline_enabled:
            return list(self.baseline) + list(self.extensions)
        return list(self.extensions)
