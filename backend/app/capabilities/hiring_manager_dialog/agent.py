"""Hiring-manager dialog capability.

Interactive role-spec shaping: hiring manager and agent iterate on
must-haves vs nice-to-haves, see trade-offs against the candidate
pool, and converge on a spec. No dependencies. When inactive, role
specs are authored the existing way (recruiter UI only).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .._stub_helpers import CapabilityContext


CAPABILITY = "hiring_manager_dialog"


@dataclass
class DialogTurn:
    speaker: str  # "hm" | "agent"
    text: str


@dataclass
class RoleSpecDelta:
    added_must_haves: list[str] = field(default_factory=list)
    removed_must_haves: list[str] = field(default_factory=list)
    notes: str = ""


def shape_role_spec(ctx: CapabilityContext, *, transcript: list[DialogTurn]) -> RoleSpecDelta | None:
    if not ctx.is_active(CAPABILITY):
        return None
    return None  # TODO: dialog-driven spec shaping


__all__ = ["CAPABILITY", "DialogTurn", "RoleSpecDelta", "shape_role_spec"]
