"""API tests for per-prospect deck share links.

Covers:
- POST   /api/v1/deck-links      → mint (owner only)
- GET    /api/v1/deck-links      → list with open history
- DELETE /api/v1/deck-links/{id} → revoke
- GET    /deck/{token}           → public; redirects to the trailing-slash form
- GET    /deck/{token}/          → public; serves the deck and records the open
- GET    /deck/{token}/{asset}   → public; serves subresources, no view recorded

The point of the feature is that a revoked link stops serving while the others
keep working, so that is asserted explicitly.
"""
from __future__ import annotations

from app.models.user import User
from tests.conftest import auth_headers, TestingSessionLocal


def _make_owner_headers(client):
    headers, email = auth_headers(client)
    # Registration makes the first user of an org its owner; assert rather than
    # assume, so a change in that default fails here loudly.
    with TestingSessionLocal() as db:
        user = db.query(User).filter(User.email == email).first()
        assert user is not None
        assert user.role == "owner", f"expected owner, got {user.role!r}"
    return headers


def _mint(client, headers, label="Venquis", note=None):
    body = {"prospect_label": label}
    if note is not None:
        body["note"] = note
    resp = client.post("/api/v1/deck-links", json=body, headers=headers)
    assert resp.status_code == 201, resp.text
    return resp.json()


def test_mint_returns_unique_url_per_prospect(client):
    headers = _make_owner_headers(client)

    first = _mint(client, headers, label="Venquis")
    second = _mint(client, headers, label="Acme Talent")

    assert first["prospect_label"] == "Venquis"
    assert second["prospect_label"] == "Acme Talent"
    assert first["token"] != second["token"]
    assert first["url"].endswith(f"/deck/{first['token']}")
    assert first["token"].startswith("dck_")
    assert first["view_count"] == 0
    assert first["is_revoked"] is False


def test_mint_requires_a_label(client):
    headers = _make_owner_headers(client)
    resp = client.post("/api/v1/deck-links", json={"prospect_label": "   "}, headers=headers)
    assert resp.status_code == 422, resp.text


def test_non_owner_cannot_mint_or_list(client):
    owner_headers = _make_owner_headers(client)
    _mint(client, owner_headers)

    member_headers, member_email = auth_headers(client)
    with TestingSessionLocal() as db:
        member = db.query(User).filter(User.email == member_email).first()
        member.role = "member"
        db.commit()

    assert client.post(
        "/api/v1/deck-links", json={"prospect_label": "X"}, headers=member_headers
    ).status_code == 403
    assert client.get("/api/v1/deck-links", headers=member_headers).status_code == 403


def test_open_is_recorded_once_per_document_not_per_asset(client):
    headers = _make_owner_headers(client)
    link = _mint(client, headers)
    token = link["token"]

    assert client.get(f"/deck/{token}/").status_code == 200
    # Subresources must not inflate the count — the deck loads nine files.
    assert client.get(f"/deck/{token}/investor-deck/deck.css").status_code == 200

    listed = client.get("/api/v1/deck-links", headers=headers).json()["links"][0]
    assert listed["view_count"] == 1
    assert listed["last_viewed_at"] is not None
    assert len(listed["opens"]) == 1


def test_bare_token_url_redirects_to_trailing_slash(client):
    """The deck's asset paths are relative, so the document must sit at /deck/{token}/."""
    headers = _make_owner_headers(client)
    token = _mint(client, headers)["token"]

    resp = client.get(f"/deck/{token}", follow_redirects=False)
    assert resp.status_code == 307
    assert resp.headers["location"] == f"/deck/{token}/"


def test_deck_document_is_not_cached_or_indexed(client):
    headers = _make_owner_headers(client)
    token = _mint(client, headers)["token"]

    resp = client.get(f"/deck/{token}/")
    assert resp.status_code == 200
    assert "no-store" in resp.headers["cache-control"]
    assert "noindex" in resp.headers["x-robots-tag"]
    # Vercel proxies this; without the opt-out a revoked link could keep
    # serving from CDN cache.
    assert resp.headers["x-vercel-enable-rewrite-caching"] == "0"


def test_served_deck_carries_no_gate_token(client):
    """The whole point: the bundle must not ship a token to the browser."""
    headers = _make_owner_headers(client)
    token = _mint(client, headers)["token"]

    body = client.get(f"/deck/{token}/").text
    assert "__VITE_DEV_TOKEN__" not in body
    assert "tali.dev_token" not in body


def test_revoking_one_link_leaves_the_others_working(client):
    headers = _make_owner_headers(client)
    doomed = _mint(client, headers, label="Lapsed prospect")
    keeper = _mint(client, headers, label="Live prospect")

    resp = client.delete(f"/api/v1/deck-links/{doomed['id']}", headers=headers)
    assert resp.status_code == 200, resp.text
    assert resp.json()["is_revoked"] is True

    assert client.get(f"/deck/{doomed['token']}/").status_code == 410
    assert client.get(f"/deck/{keeper['token']}/").status_code == 200


def test_revoked_link_keeps_its_open_history(client):
    headers = _make_owner_headers(client)
    link = _mint(client, headers)
    client.get(f"/deck/{link['token']}/")
    client.delete(f"/api/v1/deck-links/{link['id']}", headers=headers)

    listed = client.get("/api/v1/deck-links", headers=headers).json()["links"][0]
    assert listed["is_revoked"] is True
    assert listed["view_count"] == 1


def test_unknown_token_is_404_not_410(client):
    """A guesser must not be able to tell 'never existed' from 'revoked'."""
    assert client.get("/deck/dck_not-a-real-token/").status_code == 404


def test_asset_path_traversal_is_refused(client):
    headers = _make_owner_headers(client)
    token = _mint(client, headers)["token"]

    for path in ("../../main.py", "..%2f..%2fmain.py", "../__init__.py"):
        resp = client.get(f"/deck/{token}/{path}")
        assert resp.status_code == 404, f"{path} -> {resp.status_code}"
