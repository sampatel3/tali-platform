"""Shared candidate-read definitions and in-process dispatch.

The four model-facing surfaces use different authentication and mutation
plumbing.  Their authoritative candidate reads do not: contracts live in the
MCP catalogue and resolve to the same pure handlers here.  A role-bound
surface hides ``role_id`` from the model and this adapter injects it before
strict validation, so a guessed id can never escape the active role.
"""

from __future__ import annotations

from typing import Any, Callable

from sqlalchemy.orm import Session

from . import handlers, operations
from .catalog import (
    CANDIDATE_ACTION_HISTORY,
    CANDIDATE_DECISION_HISTORY,
    CANDIDATE_POOL_STATE,
    ToolSpec,
    get_tool_spec,
    tools_for,
)


def shared_read_specs_for(exposure: str) -> tuple[ToolSpec, ...]:
    """Candidate-grounding read specs exposed on one transport."""

    return tuple(
        spec
        for spec in tools_for(exposure)
        if spec.effect == "read" and bool(spec.capabilities)
    )


def shared_read_definitions(
    exposure: str,
    *,
    bound_role: bool,
) -> list[dict[str, Any]]:
    """Anthropic definitions generated from the canonical typed contracts."""

    return [
        spec.anthropic_definition(bound_role=bound_role)
        for spec in shared_read_specs_for(exposure)
    ]


def _resolve_handler(spec: ToolSpec) -> Callable[..., Any]:
    matches = [
        candidate
        for module in (handlers, operations)
        if callable(candidate := getattr(module, spec.handler_name, None))
    ]
    if len(matches) != 1:
        raise RuntimeError(
            f"shared read {spec.name!r} must resolve exactly one handler "
            f"named {spec.handler_name!r}; found {len(matches)}"
        )
    return matches[0]


def dispatch_shared_read(
    name: str,
    arguments: dict[str, Any] | None,
    *,
    exposure: str,
    db: Session,
    principal: Any,
    bound_role_id: int | None = None,
) -> Any:
    """Validate and dispatch one catalogue-backed candidate read."""

    spec = get_tool_spec(name)
    if spec not in shared_read_specs_for(exposure):
        raise KeyError(f"unknown shared read for {exposure}: {name}")

    raw = dict(arguments or {})
    if bound_role_id is not None and spec.role_scoped:
        supplied = raw.get("role_id")
        if supplied is not None and supplied != int(bound_role_id):
            raise ValueError(
                f"invalid arguments for {name}: role_id is bound to the active role"
            )
        raw["role_id"] = int(bound_role_id)
    safe_args = spec.validate(raw)
    return _resolve_handler(spec)(db, principal, **safe_args)


def capabilities_for_successful_read(name: str, result: Any) -> frozenset[str]:
    """Capabilities grounded by a successful, non-error tool result."""

    if isinstance(result, dict) and (result.get("error") or result.get("available") is False):
        return frozenset()
    # Candidate search/comparison predates the capability catalogue on a few
    # surfaces. Count those results only when they contain positive canonical
    # rows, or an explicitly exhaustive exact empty result. A capped or
    # evidence-unavailable zero must never unlock a hard-zero answer.
    if name in {
        "find_top_candidates",
        "search_candidates",
        "nl_search_candidates",
    } and isinstance(result, dict):
        rows = result.get("candidates") or result.get("applications") or []
        if isinstance(rows, list) and rows:
            return frozenset({CANDIDATE_POOL_STATE})
        if (
            result.get("is_exact_empty") is True
            and result.get("exhaustive") is True
        ):
            return frozenset({CANDIDATE_POOL_STATE})
        return frozenset()
    if name in {"compare_applications", "compare_role_applications"}:
        rows = result.get("applications") if isinstance(result, dict) else None
        return (
            frozenset({CANDIDATE_POOL_STATE})
            if isinstance(rows, list) and bool(rows)
            else frozenset()
        )
    if name == "get_application" and isinstance(result, dict):
        return frozenset({CANDIDATE_POOL_STATE})
    if name == "search_applications" and isinstance(result, list) and result:
        return frozenset({CANDIDATE_POOL_STATE})
    try:
        spec = get_tool_spec(name)
    except KeyError:
        return frozenset()
    # A partial action audit may safely surface verified rows, but it cannot
    # ground an exhaustive historical answer or a hard zero.
    if (
        CANDIDATE_ACTION_HISTORY in spec.capabilities
        and isinstance(result, dict)
        and result.get("total_is_exact") is not True
    ):
        return frozenset()
    if (
        CANDIDATE_DECISION_HISTORY in spec.capabilities
        and isinstance(result, dict)
        and result.get("total_is_exact") is not True
    ):
        return frozenset()
    if (
        CANDIDATE_POOL_STATE in spec.capabilities
        and name == "search_role_candidates"
        and isinstance(result, dict)
        and result.get("total_is_exact") is not True
    ):
        return frozenset()
    return spec.capabilities


__all__ = [
    "capabilities_for_successful_read",
    "dispatch_shared_read",
    "shared_read_definitions",
    "shared_read_specs_for",
]
