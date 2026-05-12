"""Per-agent model tier + max_tokens loader — discipline §8.1 + §8.4.

Single source of truth for "which Anthropic model + max_tokens does
agent X use?". Loads ``config/agent_models.yaml`` with a 30s in-process
cache. Defaults fall back to ``settings.CLAUDE_MODEL`` when the YAML is
missing or PyYAML isn't installed (some environments don't have it).

The agent ID space follows the sub-agent registry names:
  pre_screen · cv_scoring · graph_priors · task_selection ·
  assessment_scoring · intent_parser

Adding a new agent: drop a row in the YAML; no code change needed.
"""

from __future__ import annotations

import logging
import os
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Final


logger = logging.getLogger("taali.agent_runtime.model_config")


CONFIG_PATH: Final[Path] = (
    Path(__file__).parent.parent.parent / "config" / "agent_models.yaml"
)


@dataclass(frozen=True)
class AgentModelConfig:
    """The two knobs every Anthropic call needs."""

    model: str
    max_tokens: int


_DEFAULT = AgentModelConfig(model="claude-haiku-4-5", max_tokens=512)


@dataclass
class _Cache:
    by_agent: dict[str, AgentModelConfig]
    default: AgentModelConfig
    loaded_at: datetime


_REFRESH_SECONDS = 30
_lock = threading.Lock()
_cache: _Cache | None = None


def _load_from_disk(path: Path = CONFIG_PATH) -> _Cache:
    """Parse the YAML. Returns a cache with safe defaults when the file
    is absent or unparseable — never raises so the agent path can't
    fail closed.
    """
    default = _DEFAULT
    by_agent: dict[str, AgentModelConfig] = {}
    if not path.exists():
        logger.info("agent_models.yaml missing at %s; using global default", path)
        return _Cache(by_agent={}, default=default, loaded_at=datetime.now(timezone.utc))
    try:
        import yaml  # type: ignore[import-not-found]

        with path.open("r") as fh:
            raw = yaml.safe_load(fh) or {}
    except Exception as exc:
        logger.warning("agent_models.yaml parse failed: %s; using defaults", exc)
        return _Cache(by_agent={}, default=default, loaded_at=datetime.now(timezone.utc))

    # Default section overrides the hard-coded default.
    raw_default = raw.get("default") or {}
    default = AgentModelConfig(
        model=str(raw_default.get("model") or default.model),
        max_tokens=int(raw_default.get("max_tokens") or default.max_tokens),
    )

    # Per-agent overrides.
    for name, body in (raw.get("agents") or {}).items():
        if not isinstance(body, dict):
            continue
        by_agent[str(name)] = AgentModelConfig(
            model=str(body.get("model") or default.model),
            max_tokens=int(body.get("max_tokens") or default.max_tokens),
        )

    return _Cache(by_agent=by_agent, default=default, loaded_at=datetime.now(timezone.utc))


def _maybe_refresh() -> _Cache:
    global _cache
    now = datetime.now(timezone.utc)
    if _cache is None or (now - _cache.loaded_at).total_seconds() > _REFRESH_SECONDS:
        with _lock:
            if _cache is None or (now - _cache.loaded_at).total_seconds() > _REFRESH_SECONDS:
                _cache = _load_from_disk()
    return _cache


def get_model_for_agent(agent_name: str) -> AgentModelConfig:
    """Return the config for ``agent_name``.

    Falls back to the global default when the agent isn't listed; falls
    back to a hard-coded default when the YAML is missing entirely.
    Never raises.
    """
    cache = _maybe_refresh()
    return cache.by_agent.get(agent_name, cache.default)


def invalidate() -> None:
    """Force the next ``get_model_for_agent`` call to reload from disk.

    Useful in tests and after an admin updates the file.
    """
    global _cache
    with _lock:
        _cache = None


def all_configs() -> dict[str, AgentModelConfig]:
    """Snapshot of every per-agent config (for admin dashboards / debug)."""
    cache = _maybe_refresh()
    return dict(cache.by_agent)


def global_default() -> AgentModelConfig:
    return _maybe_refresh().default


__all__ = [
    "AgentModelConfig",
    "CONFIG_PATH",
    "all_configs",
    "get_model_for_agent",
    "global_default",
    "invalidate",
]
