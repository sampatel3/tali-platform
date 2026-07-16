"""Bullhorn credential-lineage compare-and-swap helpers.

Refresh tokens are single-use. The shared Bullhorn mutex is the primary
serialization boundary, while this generation check is the durable backstop:
an old client created before reconnect can never overwrite the newly-connected
credential lineage with a late token rotation.
"""

from __future__ import annotations

from ....models.organization import Organization
from ....platform.database import SessionLocal
from ....platform.secrets import encrypt_integration_secret


class BullhornCredentialSuperseded(RuntimeError):
    """A stale client attempted to persist into a newer credential lineage."""


def credential_generation(org: Organization) -> int:
    try:
        return max(0, int(org.bullhorn_credential_generation or 0))
    except (TypeError, ValueError):
        return 0


def bump_credential_generation(org: Organization) -> int:
    generation = credential_generation(org) + 1
    org.bullhorn_credential_generation = generation
    # Keep a non-authoritative mirror for operator diagnostics and backwards
    # compatibility. The first-class integer column above is the CAS fence.
    config = dict(org.bullhorn_config) if isinstance(org.bullhorn_config, dict) else {}
    config["credential_generation"] = generation
    org.bullhorn_config = config
    return generation


def persist_rotated_credentials(
    *,
    org_id: int,
    expected_generation: int,
    refresh_token: str,
    rest_url: str | None = None,
) -> tuple[str, str | None]:
    """CAS-persist a rotated token and return its encrypted local mirror."""
    hook_db = SessionLocal()
    try:
        encrypted = encrypt_integration_secret(refresh_token)
        values: dict = {"bullhorn_refresh_token": encrypted}
        if rest_url:
            values["bullhorn_rest_url"] = rest_url
        # One conditional statement is the durable compare-and-swap. If a
        # reconnect commits a newer generation before or while this UPDATE is
        # waiting on the row, PostgreSQL rechecks the predicate after the wait
        # and rowcount is zero. If this UPDATE wins first, reconnect overwrites
        # it with the new lineage in its subsequent commit. Either ordering
        # prevents an old client from being final state.
        updated = (
            hook_db.query(Organization)
            .filter(
                Organization.id == int(org_id),
                Organization.bullhorn_credential_generation
                == int(expected_generation),
            )
            .update(values, synchronize_session=False)
        )
        if updated != 1:
            hook_db.rollback()
            raise BullhornCredentialSuperseded(
                f"Bullhorn credential generation changed for org {org_id}"
            )
        hook_db.commit()
        return encrypted, rest_url
    finally:
        hook_db.close()
