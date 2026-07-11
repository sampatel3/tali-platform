"""Deterministic in-memory state + control surface for the fake Bullhorn server.

This is the state half of the fake; ``bullhorn_app.py`` is the FastAPI wiring
that reads/mutates it. Split in two purely to stay under the 500-LOC gate.

Everything is deterministic: no real clocks, no real randomness. Time is a
mutable integer the tests advance (:meth:`FakeBullhornState.advance_clock`);
ids and tokens are minted from monotonic counters. One ``FakeBullhornState``
instance backs one ``bullhorn_app`` — build a fresh pair per test for isolation.

The contract encoded here is the verified Bullhorn fact sheet (2026-07-02):
loginInfo discovery, single-use rotating refresh tokens, BhRestToken sessions
with TTL, mandatory ``fields=`` reads with a 500-row page cap, PUT-create /
POST-update verb inversion, per-org free-text status lists + categorization
settings, a destructive event-subscription queue with ``requestId`` re-fetch and
30-day expiry, file attachments + resume→text, per-entity entitlements, and
rate-limit (429) injection with assertable counters.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# --- fixed fake constants (deterministic; NOT real Bullhorn URLs) -----------

# The fake serves discovery, oauth and rest from ONE base (the test points the
# client's discovery URL here); loginInfo returns these so the client never
# hardcodes a swimlane. ``{base}`` is filled in by the app at request time.
FAKE_OAUTH_PATH = "/oauth"
FAKE_REST_PATH = "/rest-services/fake"

ACCESS_TOKEN_TTL = 600  # seconds — real access tokens live ~10 min
SESSION_TTL = 600  # BhRestToken session lifetime (test-clock seconds)
SUBSCRIPTION_TTL = 30 * 24 * 3600  # events + subscription expire ~30 days
SEARCH_PAGE_CAP = 500  # /search hard page cap we mimic

# Default per-org status list + categorization settings. Free-text, per-org —
# ``make_org`` overrides these so two orgs can disagree.
DEFAULT_STATUS_LIST = [
    "New Lead",
    "Submitted",
    "Interview Scheduled",
    "Offer Extended",
    "Placed",
    "Client Rejected",
]
DEFAULT_CATEGORIZATION = {
    "interviewScheduledJobResponseStatus": "Interview Scheduled",
    "confirmedJobResponseStatus": "Placed",
    "rejectedJobResponseStatus": "Client Rejected",
}


@dataclass
class OrgState:
    """One tenant. Its api creds, its status list, its entity tables."""

    org_key: str
    username: str
    password: str
    client_id: str
    client_secret: str
    status_list: list[str]
    categorization: dict[str, str]
    # entity tables: {entity_type: {id(int): record(dict)}}
    entities: dict[str, dict[int, dict[str, Any]]] = field(default_factory=dict)
    # per-entity entitlements list, seedable: {entity_type: [str, ...]}
    entitlements: dict[str, list[str]] = field(default_factory=dict)
    # file attachments: {candidate_id: {file_id: {"meta": {...}, "raw": bytes}}}
    files: dict[int, dict[int, dict[str, Any]]] = field(default_factory=dict)
    # event subscriptions: {sub_id: SubscriptionState}
    subscriptions: dict[str, "SubscriptionState"] = field(default_factory=dict)


@dataclass
class SubscriptionState:
    """A Bullhorn event subscription: a durable per-name event queue.

    Reads are DESTRUCTIVE — a normal poll drains up to ``maxEvents`` and stamps
    them with a ``requestId``; re-issuing the SAME ``requestId`` re-fetches ONLY
    that last drained batch (crash-replay), without draining more. ``created_at``
    drives 30-day expiry; ``expired`` forces it for tests.
    """

    sub_id: str
    entity_names: list[str]
    event_types: list[str]
    created_at: int
    queue: list[dict[str, Any]] = field(default_factory=list)
    last_batch: list[dict[str, Any]] = field(default_factory=list)
    last_request_id: int | None = None
    expired: bool = False


@dataclass
class _TokenRecord:
    """An issued OAuth token pair. Refresh tokens are single-use: once exchanged
    the record is marked ``spent`` and re-presenting it is ``invalid_grant``."""

    access_token: str
    refresh_token: str
    org_key: str
    issued_at: int
    spent: bool = False


class FakeBullhornState:
    """All fake state + the seed/control surface the tests drive."""

    def __init__(self) -> None:
        self._clock = 0
        self._seq = 0
        self.orgs: dict[str, OrgState] = {}
        # auth ledgers keyed by the opaque secret the client presents
        self._auth_codes: dict[str, tuple[str, str | None]] = {}  # code -> (org_key, redirect_uri)
        self._refresh_index: dict[str, _TokenRecord] = {}  # refresh_token -> rec
        self._access_index: dict[str, _TokenRecord] = {}  # access_token -> rec
        self._sessions: dict[str, dict[str, Any]] = {}  # BhRestToken -> {org_key, created_at}
        # --- control surface / assertion counters ---
        self.request_count = 0
        self.count_429_served = 0
        self._fail_next_n_429 = 0
        self._fail_every_nth_429 = 0  # 0 = off; N => every Nth request 429s

    # --- test clock ---------------------------------------------------------

    def advance_clock(self, seconds: int) -> None:
        self._clock += int(seconds)

    @property
    def now(self) -> int:
        return self._clock

    def _next(self) -> int:
        self._seq += 1
        return self._seq

    # --- 429 injection ------------------------------------------------------

    def fail_next_requests_with_429(self, n: int) -> None:
        """Serve 429 for the next ``n`` REST requests (drains as they're used)."""
        self._fail_next_n_429 = int(n)

    def fail_every_nth_request_with_429(self, n: int) -> None:
        """Serve 429 on every ``n``-th REST request (0 disables)."""
        self._fail_every_nth_429 = int(n)

    def should_serve_429(self) -> bool:
        """Called once per counted REST request; advances injection state."""
        if self._fail_next_n_429 > 0:
            self._fail_next_n_429 -= 1
            self.count_429_served += 1
            return True
        if self._fail_every_nth_429 and self.request_count % self._fail_every_nth_429 == 0:
            self.count_429_served += 1
            return True
        return False

    # --- seed: orgs + creds -------------------------------------------------

    def make_org(
        self,
        org_key: str = "org1",
        *,
        username: str | None = None,
        password: str = "pw",
        client_id: str | None = None,
        client_secret: str = "secret",
        status_list: list[str] | None = None,
        categorization: dict[str, str] | None = None,
    ) -> OrgState:
        org = OrgState(
            org_key=org_key,
            username=username or f"{org_key}_apiuser",
            password=password,
            client_id=client_id or f"{org_key}_client",
            client_secret=client_secret,
            status_list=list(status_list) if status_list is not None else list(DEFAULT_STATUS_LIST),
            categorization=dict(categorization)
            if categorization is not None
            else dict(DEFAULT_CATEGORIZATION),
        )
        self.orgs[org_key] = org
        return org

    def org_by_username(self, username: str) -> OrgState | None:
        return next((o for o in self.orgs.values() if o.username == username), None)

    def org_by_client_id(self, client_id: str) -> OrgState | None:
        return next((o for o in self.orgs.values() if o.client_id == client_id), None)

    # --- seed: entities -----------------------------------------------------

    def _put_entity(self, org: OrgState, entity: str, record: dict[str, Any]) -> dict[str, Any]:
        table = org.entities.setdefault(entity, {})
        ent_id = record.get("id") or self._next()
        record["id"] = ent_id
        table[ent_id] = record
        return record

    def make_candidate(
        self, org: OrgState, *, name: str = "Ada Lovelace", email: str = "ada@example.com", **extra: Any
    ) -> dict[str, Any]:
        rec = {
            "id": extra.pop("id", None) or self._next(),
            "firstName": name.split(" ")[0],
            "lastName": name.split(" ")[-1],
            "name": name,
            "email": email,
            "status": extra.pop("status", "Active"),
            "dateLastModified": self.now,
        }
        rec.update(extra)
        return self._put_entity(org, "Candidate", rec)

    def make_job_order(
        self, org: OrgState, *, title: str = "Senior Engineer", is_open: bool = True, **extra: Any
    ) -> dict[str, Any]:
        rec = {
            "id": extra.pop("id", None) or self._next(),
            "title": title,
            "isOpen": is_open,
            "status": extra.pop("status", "Accepting Candidates"),
            "dateLastModified": self.now,
        }
        rec.update(extra)
        return self._put_entity(org, "JobOrder", rec)

    def make_job_submission(
        self,
        org: OrgState,
        *,
        candidate_id: int,
        job_order_id: int,
        status: str | None = None,
        **extra: Any,
    ) -> dict[str, Any]:
        rec = {
            "id": extra.pop("id", None) or self._next(),
            "candidate": {"id": candidate_id},
            "jobOrder": {"id": job_order_id},
            "status": status or (org.status_list[0] if org.status_list else "Submitted"),
            "isDeleted": False,
            "dateLastModified": self.now,
        }
        rec.update(extra)
        return self._put_entity(org, "JobSubmission", rec)

    def make_job_submission_history(
        self, org: OrgState, *, job_submission_id: int, status: str, **extra: Any
    ) -> dict[str, Any]:
        rec = {
            "id": extra.pop("id", None) or self._next(),
            "jobSubmission": {"id": job_submission_id},
            "status": status,
            "dateAdded": self.now,
        }
        rec.update(extra)
        return self._put_entity(org, "JobSubmissionHistory", rec)

    # --- seed: files + entitlements ----------------------------------------

    def add_file_attachment(
        self,
        org: OrgState,
        *,
        candidate_id: int,
        raw: bytes,
        name: str = "resume.pdf",
        file_type: str = "Resume",
        content_type: str = "application/pdf",
    ) -> int:
        file_id = self._next()
        meta = {
            "id": file_id,
            "name": name,
            "type": file_type,
            "contentType": content_type,
            "dateAdded": self.now,
        }
        org.files.setdefault(candidate_id, {})[file_id] = {"meta": meta, "raw": raw}
        return file_id

    def set_entitlements(self, org: OrgState, entity: str, values: list[str]) -> None:
        org.entitlements[entity] = list(values)

    # --- events -------------------------------------------------------------

    def emit_event(
        self,
        org: OrgState,
        sub_id: str,
        *,
        entity_name: str,
        entity_id: int,
        event_type: str = "UPDATED",
        updated_properties: list[str] | None = None,
    ) -> None:
        """Enqueue an event onto a subscription. Carries ``updatedProperties``
        field NAMES only (like real), never values."""
        sub = org.subscriptions[sub_id]
        sub.queue.append(
            {
                "eventId": f"evt-{self._next()}",
                "eventType": event_type,
                "entityName": entity_name,
                "entityId": entity_id,
                "updatedProperties": list(updated_properties or []),
            }
        )

    # --- oauth / session token minting (used by the app) --------------------

    def mint_auth_code(self, org: OrgState, *, redirect_uri: str | None = None) -> str:
        code = f"code-{self._next()}"
        # Remember the redirect_uri (if any) sent to /authorize so the token
        # exchange can require the identical value, exactly like real Bullhorn.
        self._auth_codes[code] = (org.org_key, redirect_uri)
        return code

    def _issue_token_pair(self, org_key: str) -> _TokenRecord:
        rec = _TokenRecord(
            access_token=f"access-{self._next()}",
            refresh_token=f"refresh-{self._next()}",
            org_key=org_key,
            issued_at=self.now,
        )
        self._refresh_index[rec.refresh_token] = rec
        self._access_index[rec.access_token] = rec
        return rec

    def exchange_auth_code(
        self, code: str, *, redirect_uri: str | None = None
    ) -> _TokenRecord | str | None:
        """Exchange a one-time auth code. Returns ``None`` for an unknown/spent
        code, ``"redirect_uri_mismatch"`` if the echoed redirect_uri differs from
        the one sent to /authorize (Bullhorn rejects that), else a new pair."""
        entry = self._auth_codes.pop(code, None)
        if entry is None:
            return None
        org_key, authorize_redirect = entry
        if authorize_redirect != redirect_uri:
            return "redirect_uri_mismatch"
        return self._issue_token_pair(org_key)

    def exchange_refresh_token(self, refresh_token: str) -> _TokenRecord | str:
        """Single-use rotation. Returns a NEW pair, or ``"invalid_grant"`` if the
        token is unknown or already spent (strand-detection)."""
        rec = self._refresh_index.get(refresh_token)
        if rec is None or rec.spent:
            return "invalid_grant"
        rec.spent = True
        return self._issue_token_pair(rec.org_key)

    def access_record(self, access_token: str) -> _TokenRecord | None:
        return self._access_index.get(access_token)

    def access_token_valid(self, access_token: str) -> bool:
        rec = self._access_index.get(access_token)
        if rec is None:
            return False
        return (self.now - rec.issued_at) < ACCESS_TOKEN_TTL

    def open_session(self, org_key: str) -> str:
        bh_token = f"BhRest-{self._next()}"
        self._sessions[bh_token] = {
            "org_key": org_key,
            "created_at": self.now,
            "_token": bh_token,
        }
        return bh_token

    def session(self, bh_token: str) -> dict[str, Any] | None:
        sess = self._sessions.get(bh_token)
        if sess is None:
            return None
        if (self.now - sess["created_at"]) >= SESSION_TTL:
            return None
        return sess

    def session_expires_in(self, bh_token: str) -> int:
        sess = self._sessions.get(bh_token)
        if sess is None:
            return 0
        return max(0, SESSION_TTL - (self.now - sess["created_at"]))
