"""Shared input validation helpers used across backends.

Currently exposes ``parse_max_tokens``, factored out of the duplicated
``max_tokens`` parsing blocks that previously lived in
``backends/gemini/main.py`` (MCP ``tools/call`` handler) and
``backends/gemini/executor.py`` (``AgentExecutor.execute``). See risk #537
(companion issues #460, #428).

The helper is intentionally pure: no I/O, no shared state, no
concurrency concerns. It accepts a raw value, validates it as a
positive integer, and emits a warning log when the value is missing a
valid positive integer. Invalid or non-positive inputs are dropped
(returned as ``None``) so callers can fall back to their own defaults.

claude and openai carry analogous parsing blocks; adopting this
helper there is a follow-up and deliberately out of scope for #537.
"""

from __future__ import annotations

import logging
import re
from typing import Any

__all__ = ["parse_max_tokens", "sanitize_model_label"]


# Bounded allow-pattern for the Prometheus ``model`` label. Caller-supplied
# ``metadata.model`` flows into ~12 metric families with a ``model`` label; an
# unbounded string would let a hostile caller blow up metric cardinality by
# sending a fresh UUID per request (TSDB compaction / Prometheus OOM).
# Accept only simple model identifiers (alnum / dot / dash / underscore, length
# <= 64) and collapse anything else to the literal "unknown". Originally landed
# in ``backends/gemini/executor.py`` via #487; hoisted here so claude (#601) and
# openai can share a single well-reviewed definition.
_MODEL_LABEL_RE = re.compile(r"^[a-zA-Z0-9._\-]{1,64}$")


def sanitize_model_label(value: str | None) -> str:
    """Clamp a model label to a bounded, well-formed string for Prometheus.

    Returns the input unchanged when it matches ``_MODEL_LABEL_RE``; otherwise
    returns ``"unknown"``. Empty / ``None`` inputs also collapse to
    ``"unknown"``. Keep ``resolved_model`` / ``effective_model`` intact for
    logging and SDK wiring — only wrap at the Prometheus label site.
    """
    if not value:
        return "unknown"
    if _MODEL_LABEL_RE.match(value):
        return value
    return "unknown"


def parse_max_tokens(
    raw: Any,
    *,
    logger: logging.Logger,
    source: str,
    session_id: str | None = None,
) -> int | None:
    """Parse a raw ``max_tokens`` value into a positive ``int`` or ``None``.

    Parameters
    ----------
    raw:
        The raw value from request arguments or metadata. ``None`` is a
        no-op and returns ``None`` without logging.
    logger:
        The caller's logger, used to emit warnings for non-positive or
        invalid inputs. Kept as a parameter so log records are attributed
        to the calling module rather than ``shared.validation``.
    source:
        Short human-readable label identifying the call site in the log
        message (e.g. ``"MCP tools/call"`` or ``"A2A metadata"``).
    session_id:
        Optional session identifier. When provided, it is included in
        the warning messages to aid debugging; omit for contexts where
        no session has been established yet.

    Returns
    -------
    Optional[int]
        The parsed positive integer, or ``None`` if ``raw`` is ``None``,
        not coercible to ``int``, or not strictly positive.
    """
    if raw is None:
        return None

    prefix = f"{source} (session={session_id!r})" if session_id else source

    try:
        parsed = int(raw)
    except (ValueError, TypeError):
        logger.warning("%s: invalid max_tokens %r; ignoring.", prefix, raw)
        return None

    if parsed <= 0:
        logger.warning("%s: max_tokens=%s is non-positive; ignoring.", prefix, parsed)
        return None

    return parsed
