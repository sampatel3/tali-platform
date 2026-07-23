"""Batched runtime projection for mixed logical-role application pages."""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from ...candidate_search.role_assessment_scores import (
    hydrate_ordinary_assessment_runtime,
)
from ...models.candidate_application import CandidateApplication
from ...services.related_role_application_runtime import project_related_role_pages
from .role_support import application_list_payload


def project_global_application_runtime(
    db: Session,
    *,
    logical_selection: Any,
    hydrated_keys: list[tuple[int, int]],
    rows: list[CandidateApplication],
    include_cv_text: bool,
) -> list[dict[str, Any]]:
    """Overlay ordinary and related assessment truth in constant query count."""

    memberships = (
        logical_selection.resolve_memberships(db, hydrated_keys)
        if logical_selection.active and hydrated_keys
        else {}
    )
    related_indices = {
        index
        for index, key in enumerate(hydrated_keys)
        if (
            (membership := memberships.get(key)) is not None
            and membership.is_related
        )
    }
    ordinary_applications = [
        application
        for index, application in enumerate(rows)
        if index not in related_indices
    ]
    if ordinary_applications:
        hydrate_ordinary_assessment_runtime(
            db,
            organization_id=int(logical_selection.organization_id),
            applications=ordinary_applications,
        )
    items = [
        application_list_payload(
            application,
            include_cv_text=include_cv_text,
            include_assessment_runtime=index not in related_indices,
        )
        for index, application in enumerate(rows)
    ]
    if not logical_selection.active or not hydrated_keys:
        return items

    related_groups: dict[int, list[int]] = {}
    for index, (key, item) in enumerate(zip(hydrated_keys, items, strict=True)):
        membership = memberships.get(key)
        if membership is None:
            continue
        item["logical_membership_id"] = membership.public_id
        item["logical_role_id"] = int(membership.logical_role.id)
        item["role_id"] = int(membership.logical_role.id)
        item["role_name"] = membership.logical_role.name
        if membership.is_related and membership.evaluation is not None:
            related_groups.setdefault(int(membership.logical_role.id), []).append(
                index
            )
    grouped_inputs = [
        (
            logical_selection.roles_by_id[related_role_id],
            [rows[index] for index in indices],
            [items[index] for index in indices],
        )
        for related_role_id, indices in related_groups.items()
    ]
    projected_groups = project_related_role_pages(db, groups=grouped_inputs)
    for (related_role_id, indices), projected in zip(
        related_groups.items(),
        projected_groups,
        strict=True,
    ):
        for index, payload in zip(indices, projected, strict=True):
            payload["logical_membership_id"] = (
                f"{related_role_id}:{int(rows[index].id)}"
            )
            payload["logical_role_id"] = related_role_id
            items[index] = payload
    return items


__all__ = ["project_global_application_runtime"]
