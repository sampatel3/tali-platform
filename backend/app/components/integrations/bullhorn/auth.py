"""Bullhorn discovery + OAuth + REST-session lifecycle.

Split out from ``service.py`` (transport + typed methods) so each stays under the
500-LOC gate and the auth invariant is isolated and testable.

THE CRITICAL INVARIANT — single-use rotating refresh tokens
-----------------------------------------------------------
Bullhorn refresh tokens are single-use and ROTATE on every ``/oauth/token``
exchange. If we obtain a new access token but crash before persisting the new
refresh token, the org is stranded forever (the old refresh token is already
spent server-side; the new one is lost). So on every refresh exchange we:

  1. POST /oauth/token (grant=refresh_token) -> {new_access, new_refresh}
  2. call ``persist_tokens(refresh_token=new_refresh, rest_url=...)`` — the hook
     durably writes + flushes the new refresh token to the org row IN ITS OWN
     TRANSACTION, BEFORE step 3.
  3. only now adopt the new access token in memory and proceed to REST login.

``persist_tokens`` is injected by the constructor so the contract tests can
observe the ordering (assert it fired before login) and kill it between steps
(assert a lost rotation is detected on the next connect, never silently
half-applied). Nothing here logs a token, code, or secret.

URL injection
-------------
``discovery_url`` / ``oauth_url`` / ``rest_url`` are all constructor args so a
fake server can be pointed at without monkeypatching. Discovery
(``loginInfo``) yields the per-customer oauth + rest swimlanes; we never
hardcode a cluster URL or corpToken.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
import contextlib
from typing import Iterator, Protocol
from urllib.parse import parse_qs, urljoin, urlsplit

import httpx

from .errors import BullhornAuthError, redact_exc

logger = logging.getLogger(__name__)

# Bullhorn's documented discovery root. Overridable via constructor for tests /
# region overrides; NEVER hardcode a swimlane past this — loginInfo returns the
# real oauth + rest URLs for the customer.
DEFAULT_DISCOVERY_URL = "https://rest.bullhornstaffing.com/rest-services/loginInfo"


@contextlib.contextmanager
def quiet_httpx() -> Iterator[None]:
    """Suppress httpx's INFO request-log line for the duration of a call.

    Bullhorn requires the access token (on ``/login``) and the ``BhRestToken``
    (on every REST call) in the URL QUERY STRING, and httpx's default INFO
    handler logs the full request URL — which would spill those live tokens into
    application logs. Workable's client is immune (it uses Bearer *headers*, which
    httpx does not log), but Bullhorn's URL-token contract forces this. We raise
    only the ``httpx`` logger's threshold to WARNING around each request, then
    restore it — narrower than muting httpx globally, and it leaves genuine httpx
    warnings/errors intact.
    """
    lg = logging.getLogger("httpx")
    previous = lg.level
    lg.setLevel(max(previous, logging.WARNING))
    try:
        yield
    finally:
        lg.setLevel(previous)


def _code_from_authorize(resp: httpx.Response) -> str | None:
    """Extract the OAuth authorization code from an /oauth/authorize response.

    Real Bullhorn returns a 302 whose ``Location`` header is
    ``<redirect_uri>?code=<authcode>&...`` (the code may be URL-encoded;
    ``parse_qs`` decodes it). We read it straight off the header rather than
    following the redirect (the target would consume/lose the code). The code is a
    secret, so nothing here logs the header.
    """
    if resp.is_redirect:
        location = resp.headers.get("Location", "")
        params = parse_qs(urlsplit(location).query)
        values = params.get("code")
        return values[0] if values else None
    return None


class PersistTokens(Protocol):
    """Durably persist rotated credentials to the org row before first use.

    MUST write ``refresh_token`` (encrypted) — and ``rest_url`` when provided —
    and flush/commit so a crash immediately after cannot lose the rotation.
    Called with keyword args only.
    """

    def __call__(self, *, refresh_token: str, rest_url: str | None = None) -> None: ...


@dataclass
class _RestSession:
    """A live REST session: the BhRestToken + the corpToken-bearing rest URL."""

    bh_rest_token: str
    rest_url: str  # absolute, ends with "/"; carries the corpToken from login


class BullhornAuth:
    """Owns discovery, OAuth exchange (with the rotation invariant), and the
    in-memory REST session. One instance per org, per client instance.
    """

    def __init__(
        self,
        *,
        username: str,
        client_id: str,
        client_secret: str,
        refresh_token: str | None,
        persist_tokens: PersistTokens,
        rest_url: str | None = None,
        discovery_url: str = DEFAULT_DISCOVERY_URL,
        oauth_url: str | None = None,
        transport: httpx.BaseTransport | None = None,
        timeout: float = 30.0,
        password: str | None = None,
        redirect_uri: str | None = None,
    ):
        self._username = username
        self._client_id = client_id
        self._client_secret = client_secret
        self._refresh_token = refresh_token
        self._persist = persist_tokens
        # Cached rest base from a prior connect (org.bullhorn_rest_url). Refreshed
        # from loginInfo on auth failure.
        self._cached_rest_url = rest_url
        self._discovery_url = discovery_url
        self._oauth_url = oauth_url
        self._password = password
        # Optional per Bullhorn docs: if the OAuth key has a single registered
        # redirect_uri, omit it and Bullhorn uses that one; if set, it must match a
        # registered URI AND be echoed identically on the token exchange (below).
        self._redirect_uri = redirect_uri
        self._transport = transport
        self._timeout = timeout

        self._access_token: str | None = None
        self._session: _RestSession | None = None

    # --- http ---------------------------------------------------------------

    def _client(self) -> httpx.Client:
        return httpx.Client(timeout=self._timeout, transport=self._transport)

    # --- discovery ----------------------------------------------------------

    def discover(self) -> tuple[str, str]:
        """GET loginInfo -> (oauthUrl, restUrl). Never hardcodes a swimlane."""
        try:
            with quiet_httpx(), self._client() as client:
                resp = client.get(self._discovery_url, params={"username": self._username})
                resp.raise_for_status()
                data = resp.json()
        except Exception as exc:  # noqa: BLE001 — normalize to a typed auth error
            raise BullhornAuthError(f"Bullhorn discovery failed: {redact_exc(exc)}") from exc
        oauth_url = data.get("oauthUrl")
        rest_url = data.get("restUrl")
        if not oauth_url or not rest_url:
            raise BullhornAuthError("Bullhorn discovery response missing oauthUrl/restUrl")
        self._oauth_url = oauth_url
        self._cached_rest_url = rest_url
        return oauth_url, rest_url

    def _oauth_base(self) -> str:
        if not self._oauth_url:
            self.discover()
        assert self._oauth_url is not None
        return self._oauth_url.rstrip("/")

    def _rest_base(self) -> str:
        if not self._cached_rest_url:
            self.discover()
        assert self._cached_rest_url is not None
        return self._cached_rest_url

    # --- oauth exchange -----------------------------------------------------

    def _exchange_token(self, data: dict) -> dict:
        """POST /oauth/token. ``data`` selects the grant. Never logs the body."""
        oauth = self._oauth_base()
        url = f"{oauth}/token"
        try:
            with quiet_httpx(), self._client() as client:
                resp = client.post(url, data=data)
                resp.raise_for_status()
                return resp.json()
        except httpx.HTTPStatusError as exc:
            # 400 invalid_grant on a refresh is the strand signal; surface typed.
            grant = data.get("grant_type")
            raise BullhornAuthError(
                f"Bullhorn OAuth {grant} exchange failed (status "
                f"{exc.response.status_code if exc.response else '?'})"
            ) from exc
        except Exception as exc:  # noqa: BLE001
            raise BullhornAuthError(f"Bullhorn OAuth exchange failed: {redact_exc(exc)}") from exc

    def _adopt_pair_and_persist(self, token_payload: dict, *, rest_url: str | None) -> None:
        """Persist the rotated refresh token BEFORE adopting the new access token.

        This is the crash-safety ordering: the durable write of the new refresh
        token happens first (its own transaction, via the hook), and only if it
        returns cleanly do we hold the new access token in memory. A failure in
        ``persist_tokens`` raises before the access token is usable, so we never
        end up having spent the old refresh token while the new one is unsaved.
        """
        new_refresh = token_payload.get("refresh_token")
        new_access = token_payload.get("access_token")
        if not new_access:
            raise BullhornAuthError("Bullhorn OAuth response missing access_token")
        if new_refresh:
            # Persist FIRST, and only adopt the new access token if it succeeds.
            # A persist failure is surfaced as a typed auth error (never the raw
            # hook exception) so op_runner treats it like any auth failure and
            # does not retry blindly; crucially ``self._access_token`` is left
            # UNCHANGED, so we never end up having used the new access token while
            # the rotated refresh token is unsaved (the strand condition).
            try:
                self._persist(refresh_token=new_refresh, rest_url=rest_url)
            except BullhornAuthError:
                raise
            except Exception as exc:  # noqa: BLE001 — normalize hook failures
                raise BullhornAuthError(
                    "Failed to persist rotated Bullhorn refresh token; "
                    "aborting before using the new access token"
                ) from exc
            self._refresh_token = new_refresh
        self._access_token = new_access

    def authorize_with_password(self) -> None:
        """One-time connect: automated auth-code grant then token exchange.

        POST /oauth/authorize (action=Login, username, password) -> code, then
        exchange the code for the first token pair. Requires ``password`` (only
        held for the one-time connect). Persists the first refresh token via the
        hook exactly like a rotation.

        The automated auth-code grant does NOT return the code in a JSON body: it
        responds with a 302 redirect whose ``Location`` header carries
        ``?code=<authcode>`` (per Bullhorn's docs). So we must NOT follow the
        redirect (the code lives in the redirect, not its target) and must NOT
        ``raise_for_status()`` on the 3xx — a 302 is the SUCCESS case here. We read
        the code off ``Location`` and treat a 4xx/5xx (bad creds) as the failure.
        """
        if not self._password:
            raise BullhornAuthError("password is required for the one-time Bullhorn connect")
        oauth = self._oauth_base()
        data = {
            "client_id": self._client_id,
            "username": self._username,
            "password": self._password,
            "action": "Login",
            "response_type": "code",
        }
        # Optional: only sent when configured; must then match on the token leg.
        if self._redirect_uri:
            data["redirect_uri"] = self._redirect_uri
        try:
            with quiet_httpx(), self._client() as client:
                # follow_redirects stays False (httpx default): the code is IN the
                # 302's Location header, not at the redirect target.
                resp = client.post(f"{oauth}/authorize", data=data)
                # A 302/301 IS success (code in Location). Only a real error status
                # (bad credentials -> 4xx, server -> 5xx) is a failure.
                if resp.status_code >= 400:
                    resp.raise_for_status()
                code = _code_from_authorize(resp)
        except Exception as exc:  # noqa: BLE001
            raise BullhornAuthError(f"Bullhorn authorize failed: {redact_exc(exc)}") from exc
        if not code:
            raise BullhornAuthError("Bullhorn authorize response missing code")
        exchange = {
            "grant_type": "authorization_code",
            "code": code,
            "client_id": self._client_id,
            "client_secret": self._client_secret,
        }
        # Bullhorn requires the SAME redirect_uri on the token exchange iff it was
        # sent to /authorize.
        if self._redirect_uri:
            exchange["redirect_uri"] = self._redirect_uri
        payload = self._exchange_token(exchange)
        self._adopt_pair_and_persist(payload, rest_url=self._cached_rest_url)

    def refresh_access_token(self) -> None:
        """Exchange the stored refresh token for a new pair (rotation invariant)."""
        if not self._refresh_token:
            raise BullhornAuthError("no Bullhorn refresh token available; reconnect required")
        payload = self._exchange_token(
            {
                "grant_type": "refresh_token",
                "refresh_token": self._refresh_token,
                "client_id": self._client_id,
                "client_secret": self._client_secret,
            }
        )
        self._adopt_pair_and_persist(payload, rest_url=self._cached_rest_url)

    # --- REST session -------------------------------------------------------

    def _login(self) -> _RestSession:
        """POST {restUrl}/login?version=*&access_token= -> BhRestToken + rest url.

        The login-returned ``restUrl`` carries the corpToken and is authoritative
        for all subsequent REST calls. If it's relative (fake server), resolve it
        against the discovery rest base.
        """
        if not self._access_token:
            raise BullhornAuthError("cannot REST-login without an access token")
        base = self._rest_base().rstrip("/")
        url = f"{base}/login"
        try:
            with quiet_httpx(), self._client() as client:
                resp = client.post(url, params={"version": "*", "access_token": self._access_token})
                resp.raise_for_status()
                data = resp.json()
        except Exception as exc:  # noqa: BLE001
            raise BullhornAuthError(f"Bullhorn REST login failed: {redact_exc(exc)}") from exc
        bh_token = data.get("BhRestToken")
        rest_url = data.get("restUrl")
        if not bh_token or not rest_url:
            raise BullhornAuthError("Bullhorn REST login response missing BhRestToken/restUrl")
        # The real Bullhorn returns an ABSOLUTE restUrl (with corpToken) — keep it.
        # The fake returns a root-relative path (e.g. "/rest-services/fake/");
        # resolve it against the discovery rest base so ``urljoin`` anchors it to
        # the right origin. Leave the leading slash intact — ``urljoin`` treats a
        # root-relative path against the origin, which is what we want (stripping
        # it would double the base path segment).
        if not rest_url.startswith("http"):
            rest_url = urljoin(self._rest_base(), rest_url)
        if not rest_url.endswith("/"):
            rest_url += "/"
        return _RestSession(bh_rest_token=bh_token, rest_url=rest_url)

    def ensure_session(self) -> _RestSession:
        """Lazy login: reuse the in-memory session until a 401 invalidates it.

        On first use (no access token yet) refresh to obtain one, then login.
        """
        if self._session is not None:
            return self._session
        if self._access_token is None:
            self.refresh_access_token()
        self._session = self._login()
        return self._session

    def reauthenticate(self) -> _RestSession:
        """Handle a 401: refresh (rotation invariant) -> re-login EXACTLY once.

        Drops any cached session, refreshes the access token (persisting the new
        refresh token first), and re-logs-in. Raises :class:`BullhornAuthError`
        if this still fails; the caller does NOT retry again (op_runner owns any
        further retry).
        """
        self._session = None
        self.refresh_access_token()
        self._session = self._login()
        return self._session

    @property
    def rest_url(self) -> str | None:
        """The live corpToken-bearing rest url, if a session is open."""
        return self._session.rest_url if self._session else None
