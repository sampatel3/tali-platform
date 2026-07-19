"""In-process lease observation for serialized ATS operation handlers."""

from __future__ import annotations

from ..services.workable_actions_service import WorkableWritebackError


def ownership_is_lost(locks, ownership_lost) -> bool:
    return any(ownership_lost(lock) for lock in locks)


def execute_with_lease_observer(
    *,
    runner,
    db,
    organization_id: int,
    op_type: str,
    payload: dict,
    locks,
    ownership_lost,
) -> dict:
    """Expose the live lease to each handler's exact provider boundary."""

    should_yield = lambda: ownership_is_lost(locks, ownership_lost)
    result = runner.execute_op(
        db,
        organization_id=int(organization_id),
        op_type=op_type,
        payload=payload,
        should_yield=should_yield,
    )
    if result.get("mutex_lease_lost"):
        error = WorkableWritebackError(
            action=op_type,
            code="mutex_lease_lost",
            message="ATS mutex ownership became uncertain",
            retriable=True,
        )
        error.provider_called = False
        raise error
    if result.get("status") == "failed" and result.get("provider_called") is False:
        error = WorkableWritebackError(
            action=op_type,
            code=str(result.get("code") or "provider_rejected"),
            message=(
                "ATS rejected the operation before applying it"
                if result.get("retriable") is not True
                else "ATS temporarily rejected the operation before applying it"
            ),
            retriable=result.get("retriable") is True,
        )
        error.provider_called = False
        raise error
    return result


def retry_is_proven_safe(error, locks, ownership_lost) -> bool:
    """A lost lease may retry only with proof the provider was not called."""

    return (
        not ownership_is_lost(locks, ownership_lost)
        or getattr(error, "provider_called", None) is False
    )


__all__ = [
    "execute_with_lease_observer",
    "ownership_is_lost",
    "retry_is_proven_safe",
]
