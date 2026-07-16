"""One-time Bullhorn connect orchestration.

Owns the connect flow the route drives (build plan §7):

  loginInfo discovery
    -> automated OAuth exchange (auth-code grant with the API-user password)
    -> REST login smoke (``ping`` establishes a session)
    -> ``/entitlements`` pre-flight for Candidate / JobOrder / JobSubmission / Note
       (a clear PER-ENTITY message when a required verb is missing)
    -> fetch the per-org status list + categorization settings
    -> ``seed_stage_map_from_categorization``
    -> persist ENCRYPTED creds + ``bullhorn_connected=True`` + discovered rest_url.

SECURITY — the password:
    The API-user password is passed to :func:`run_connect` ONLY for the
    in-memory OAuth exchange. It is handed to :class:`BullhornAuth` (which never
    logs it) and is NOT stored on the org, NOT returned, and NOT logged here.
    ``client_secret`` / ``refresh_token`` are written as Fernet ciphertext and
    never echoed.

TESTABILITY:
    :func:`build_connect_auth` is the single seam a test overrides to point the
    connect at the fake Bullhorn server (its ``discovery_url``). The route module
    re-exports it so tests monkeypatch ``routes.build_connect_auth`` exactly like
    the Workable tests monkeypatch ``WorkableSyncService.sync_org``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy.orm import Session

from ...components.integrations.bullhorn.auth import BullhornAuth
from ...components.integrations.bullhorn.errors import BullhornError
from ...components.integrations.bullhorn.credential_state import (
    bump_credential_generation,
)
from ...components.integrations.bullhorn.service import BullhornService
from ...components.integrations.bullhorn.stage_map import (
    seed_stage_map_from_categorization,
)
from ...models.organization import Organization, SYNC_MODE_BULLHORN_PRIMARY
from ...platform.secrets import encrypt_integration_secret

logger = logging.getLogger("taali.bullhorn.connect")

# The API user MUST be able to READ the entities we import and WRITE the ones we
# hand decisions back to. Bullhorn ``/entitlements/{entity}`` returns the allowed
# verb list; we require these per entity (the sync reads with GET, moves a
# submission with POST=update, and creates a Note with PUT=create).
_REQUIRED_ENTITLEMENTS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Candidate", ("GET",)),
    ("JobOrder", ("GET",)),
    ("JobSubmission", ("GET", "POST")),
    ("Note", ("GET", "PUT")),
)


class BullhornConnectError(Exception):
    """A connect step failed with a message safe to show the recruiter.

    Never carries a credential — the message is a short, human explanation of
    which step failed (discovery, auth, a missing entitlement, …).
    """

    def __init__(self, public_message: str):
        super().__init__(public_message)
        self.public_message = public_message


@dataclass
class ConnectResult:
    """What a successful connect discovered — for the route's response + persist."""

    rest_url: str | None
    refresh_token: str
    statuses: list[str]
    categorization: dict[str, str | None]
    seeded_rows: int


def build_connect_auth(*, username: str, client_id: str, client_secret: str, password: str) -> BullhornAuth:
    """Construct the :class:`BullhornAuth` used for the one-time connect.

    A persist-less auth (the connect persists the FIRST refresh token itself,
    transactionally, only after every pre-flight passes — so a connect that
    fails a check writes nothing). This is the seam tests override to inject the
    fake server's ``discovery_url``. The real path uses Bullhorn's default
    discovery root baked into :class:`BullhornAuth`.
    """

    def _noop_persist(*, refresh_token: str, rest_url: str | None = None) -> None:
        # Connect owns durable persistence (see run_connect); the auth object
        # must not write to the org here. Never logs the token.
        return None

    return BullhornAuth(
        username=username,
        client_id=client_id,
        client_secret=client_secret,
        refresh_token=None,
        persist_tokens=_noop_persist,
        password=password,
    )


def _preflight_entitlements(service: BullhornService) -> None:
    """Fail with a clear PER-ENTITY message if a required verb is missing.

    Raises :class:`BullhornConnectError` naming the first entity/verb the API
    user lacks, so the recruiter knows exactly which Bullhorn permission to grant.
    """
    for entity, required_verbs in _REQUIRED_ENTITLEMENTS:
        try:
            allowed = {v.upper() for v in service.get_entitlements(entity)}
        except BullhornError:
            raise BullhornConnectError(
                f"Could not read Bullhorn entitlements for {entity}. "
                "Check the API user has permission to view this entity."
            ) from None
        for verb in required_verbs:
            if verb.upper() not in allowed:
                raise BullhornConnectError(
                    f"The Bullhorn API user is missing the '{verb}' entitlement on "
                    f"{entity}. Grant it in Bullhorn, then reconnect."
                )


def run_connect(
    db: Session,
    org: Organization,
    *,
    username: str,
    client_id: str,
    client_secret: str,
    password: str,
) -> ConnectResult:
    """Run the full one-time connect for ``org`` and persist on success.

    Does NOT commit — the caller (route) owns the transaction so the connect is
    atomic with any surrounding work. On ANY failure raises
    :class:`BullhornConnectError` with a recruiter-safe message and writes
    nothing durable (the auth object's persist hook is a no-op; we only stamp the
    org row at the very end). The password is used solely for the in-memory OAuth
    exchange and is never stored or logged.
    """
    auth = build_connect_auth(
        username=username,
        client_id=client_id,
        client_secret=client_secret,
        password=password,
    )

    # 1. discovery + 2. automated OAuth exchange (auth-code grant w/ password).
    try:
        auth.discover()
        auth.authorize_with_password()
    except BullhornError:
        raise BullhornConnectError(
            "Bullhorn sign-in failed. "
            "Check the username, client id, client secret, and API-user password."
        ) from None

    service = BullhornService(auth, client_id=client_id)

    # 3. REST login smoke — ``ping`` forces a session (login) and proves REST
    # reachability with the freshly minted token before we commit to anything.
    try:
        service.ping()
    except BullhornError:
        raise BullhornConnectError(
            "Connected to Bullhorn but the REST session check failed. Please retry."
        ) from None

    # 4. entitlement pre-flight — clear per-entity failure.
    _preflight_entitlements(service)

    # 5. per-org status list + categorization settings.
    try:
        status_payload = service.get_status_list()
    except BullhornError:
        raise BullhornConnectError(
            "Could not read the Bullhorn status list. Please retry."
        ) from None
    statuses = [s for s in status_payload.get("statuses", []) if isinstance(s, str)]
    categorization = status_payload.get("categorization") or {}

    # The rotated refresh token from the connect exchange (the auth object holds
    # it in memory; we persist it below). Guard defensively — a missing token here
    # means the exchange didn't yield one, which is a hard connect failure.
    refresh_token = auth._refresh_token  # noqa: SLF001 — connect owns persistence
    if not refresh_token:
        raise BullhornConnectError(
            "Bullhorn did not return a refresh token during connect. Please retry."
        )
    rest_url = auth._cached_rest_url  # noqa: SLF001 — discovered rest base

    # 6. seed the stage map from categorization defaults (idempotent; also stamps
    # the confirmed/placed status onto org.bullhorn_config for write-back).
    seeded = seed_stage_map_from_categorization(db, org, categorization=categorization)

    # 7. persist ENCRYPTED creds + connection state. Password is NOT persisted.
    org.bullhorn_username = username
    org.bullhorn_client_id = client_id
    org.bullhorn_client_secret = encrypt_integration_secret(client_secret)
    org.bullhorn_refresh_token = encrypt_integration_secret(refresh_token)
    if rest_url:
        org.bullhorn_rest_url = rest_url
    org.bullhorn_connected = True
    bump_credential_generation(org)
    # A Bullhorn-only connection makes Bullhorn the funnel authority. When an
    # incumbent Workable connection is also present, preserve its posture: the
    # shared ATS resolver deliberately routes dual-connected orgs to Workable.
    if not (
        org.workable_connected
        and org.workable_access_token
        and org.workable_subdomain
    ):
        org.sync_mode = SYNC_MODE_BULLHORN_PRIMARY
    db.add(org)

    logger.info(
        "Bullhorn connected org_id=%s statuses=%d seeded_stage_rows=%d",
        org.id,
        len(statuses),
        seeded,
    )
    return ConnectResult(
        rest_url=rest_url,
        refresh_token=refresh_token,
        statuses=statuses,
        categorization=categorization,
        seeded_rows=seeded,
    )
