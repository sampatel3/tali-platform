"""Shared types for assessment repository provisioning."""

from __future__ import annotations

from dataclasses import dataclass


class AssessmentRepositoryError(RuntimeError):
    """Raised when repository provisioning fails."""


@dataclass
class BranchContext:
    repo_url: str
    branch_name: str
    clone_command: str


__all__ = ["AssessmentRepositoryError", "BranchContext"]
