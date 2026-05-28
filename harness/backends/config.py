"""Backend configuration loader.

Reads backend.yaml from BACKEND_CONFIG_PATH (default: /home/agent/.witwave/backend.yaml).

Example backend.yaml:

    backend:
      agents:
        - id: claude
          url: http://claude-code-agent:8000
          model: claude-opus-4-7

        - id: openai
          url: http://openai-agent:8000
          model: gpt-5.5

      routing:
        default:
          agent: claude
          model: claude-opus-4-7
        a2a:
          agent: claude
          model: claude-opus-4-7
        heartbeat:
          agent: claude
          model: claude-opus-4-7
        job:
          agent: claude
          model: claude-opus-4-7
        task:
          agent: claude
          model: claude-opus-4-7
        trigger:
          agent: claude
          model: claude-opus-4-7
        continuation:
          agent: claude
          model: claude-opus-4-7

Each routing value may also be a plain string (agent id), which is equivalent to
specifying only the agent with no model override.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field

import yaml

logger = logging.getLogger(__name__)

BACKEND_CONFIG_PATH = os.environ.get("BACKEND_CONFIG_PATH", "/home/agent/.witwave/backend.yaml")


@dataclass
class BackendConfig:
    """One backend entry parsed from ``backend.yaml``.

    ``id`` is the routing key callers reference (per-message ``backend_id``
    overrides, routing entries, default-backend lookup). ``model`` /
    ``auth_env`` / ``url`` mirror the same-named YAML keys when set.
    ``extra`` captures any keys outside the known set so backend-specific
    config can travel through without schema changes here.
    """

    id: str
    model: str | None = None
    auth_env: str | None = None
    url: str | None = None
    extra: dict = field(default_factory=dict)


def load_backends_config(raw: dict | None = None) -> list[BackendConfig]:
    """Load and validate backends from config file.

    Raises FileNotFoundError if no config file exists.
    Raises ValueError if the config is malformed or contains no valid backends.

    Pass *raw* (a pre-parsed dict) to avoid re-reading the file.
    """
    if raw is None:
        if not os.path.exists(BACKEND_CONFIG_PATH):
            raise FileNotFoundError(f"backend.yaml not found at {BACKEND_CONFIG_PATH}")
        with open(BACKEND_CONFIG_PATH) as f:
            raw = yaml.safe_load(f)

    if (
        not isinstance(raw, dict)
        or "backend" not in raw
        or not isinstance(raw["backend"], dict)
        or "agents" not in raw["backend"]
    ):
        raise ValueError("backend.yaml must contain a top-level 'backend' mapping with an 'agents' list.")

    entries = raw["backend"]["agents"]
    if not isinstance(entries, list) or not entries:
        raise ValueError("backend.yaml 'backend.agents' must be a non-empty list.")

    configs: list[BackendConfig] = []
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError(f"Each backend entry must be a mapping, got: {entry!r}")

        backend_id = entry.get("id")
        if not backend_id:
            raise ValueError(f"Backend entry missing required 'id' field: {entry!r}")

        known = {"id", "model", "auth-env", "url"}
        extra = {k: v for k, v in entry.items() if k not in known}

        configs.append(
            BackendConfig(
                id=backend_id,
                model=entry.get("model") or None,
                auth_env=entry.get("auth-env") or None,
                url=entry.get("url") or None,
                extra=extra,
            )
        )

    ids = [c.id for c in configs]
    if len(ids) != len(set(ids)):
        raise ValueError(f"Duplicate backend ids in backend.yaml: {ids}")

    for c in configs:
        logger.info(f"Backend configured: {c.id} (url={c.url or 'NOT SET'})")

    return configs


@dataclass
class RoutingEntry:
    """A single routing target: an agent id and an optional model override."""

    agent: str
    model: str | None = None


def _parse_routing_entry(value) -> RoutingEntry | None:
    """Parse a routing value into a RoutingEntry.

    Accepts:
      - a plain string → RoutingEntry(agent=value)
      - a dict with 'agent' key → RoutingEntry(agent=..., model=...)
      - None / missing → None
    """
    if not value:
        return None
    if isinstance(value, str):
        return RoutingEntry(agent=value)
    if isinstance(value, dict) and value.get("agent"):
        return RoutingEntry(agent=value["agent"], model=value.get("model") or None)
    return None


@dataclass
class RoutingConfig:
    """Named backend routing overrides read from the 'routing:' block in backend.yaml.

    Each field is a RoutingEntry (agent id + optional model) or None to fall back
    to the default.
    """

    default: RoutingEntry | None = None
    a2a: RoutingEntry | None = None
    heartbeat: RoutingEntry | None = None
    job: RoutingEntry | None = None
    task: RoutingEntry | None = None
    trigger: RoutingEntry | None = None
    continuation: RoutingEntry | None = None


def load_routing_config(raw: dict | None = None) -> RoutingConfig:
    """Load the optional 'routing:' block from backend.yaml.

    Returns a RoutingConfig with all fields set to None if:
    - the config file does not exist,
    - the file has no 'routing:' block, or
    - the block cannot be parsed.

    This preserves the existing default-backend fallback behavior for callers
    that check for None.

    Pass *raw* (a pre-parsed dict) to avoid re-reading the file.
    """
    if raw is None:
        if not os.path.exists(BACKEND_CONFIG_PATH):
            return RoutingConfig()
        try:
            with open(BACKEND_CONFIG_PATH) as f:
                raw = yaml.safe_load(f)
        except Exception as e:
            logger.warning(f"Failed to read {BACKEND_CONFIG_PATH} for routing config: {e}")
            return RoutingConfig()

    if not isinstance(raw, dict) or not isinstance(raw.get("backend"), dict):
        return RoutingConfig()

    routing_raw = raw["backend"].get("routing")
    if not isinstance(routing_raw, dict):
        return RoutingConfig()

    return RoutingConfig(
        default=_parse_routing_entry(routing_raw.get("default")),
        a2a=_parse_routing_entry(routing_raw.get("a2a")),
        heartbeat=_parse_routing_entry(routing_raw.get("heartbeat")),
        job=_parse_routing_entry(routing_raw.get("job")),
        task=_parse_routing_entry(routing_raw.get("task")),
        trigger=_parse_routing_entry(routing_raw.get("trigger")),
        continuation=_parse_routing_entry(routing_raw.get("continuation")),
    )
