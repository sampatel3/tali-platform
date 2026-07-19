"""In-process sub-agent registry.

Production sub-agent modules import ``register_sub_agent`` and register their
singleton at module import time.  Registration means the policy evaluator may
dispatch the implementation; retained experimental modules can deliberately
remain unregistered.
"""

from __future__ import annotations

from typing import Any

from .base import SubAgent


_REGISTRY: dict[str, SubAgent] = {}


def register_sub_agent(sub_agent: SubAgent) -> None:
    """Register a sub-agent. Idempotent: re-registering the same name
    overwrites silently — useful when tests stub a sub-agent out and
    the original module re-imports.
    """
    _REGISTRY[sub_agent.name] = sub_agent


def get_sub_agent(name: str) -> SubAgent:
    if name not in _REGISTRY:
        raise KeyError(f"unknown sub-agent: {name}")
    return _REGISTRY[name]


def all_sub_agents() -> list[SubAgent]:
    return list(_REGISTRY.values())


def clear_registry_for_tests() -> None:
    """Reset between tests so a per-test stub doesn't leak."""
    _REGISTRY.clear()


def snapshot() -> dict[str, Any]:
    """Diagnostic snapshot of registered sub-agents (name -> class)."""
    return {name: type(sa).__name__ for name, sa in _REGISTRY.items()}


__all__ = [
    "all_sub_agents",
    "clear_registry_for_tests",
    "get_sub_agent",
    "register_sub_agent",
    "snapshot",
]
