"""Per-prospect deck share links — mint, audit, revoke, and serve.

The sales deck used to be a static asset at ``/_deck/index.html``. Vercel's
``handle: filesystem`` phase served that path directly, which meant the entire
deck — and the build-inlined gate token — was readable by anyone who knew the
URL. The bundle now lives in ``backend/app/static/deck/`` (outside the frontend
build, so nothing reaches the CDN) and is served only from here, after the
token in the path has been checked against a live, unrevoked row.

Endpoints:
- ``POST   /api/v1/deck-links``      — mint a link for one prospect (owner only)
- ``GET    /api/v1/deck-links``      — list with open history (owner only)
- ``DELETE /api/v1/deck-links/{id}`` — revoke one link (owner only)
- ``GET    /deck/{token}``           — public; the deck itself
- ``GET    /deck/{token}/{path}``    — public; its css/js/img subresources

Revocation is a soft delete so the open history survives it. Only the HTML
entry point records a view; recording subresources too would multiply every
open by the nine files the deck loads.
"""
from __future__ import annotations

import mimetypes
import secrets
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ...deps import require_org_owner
from ...models.deck_share_link import DeckShareLink, DeckShareView
from ...models.user import User
from ...platform.database import get_db

# The /api/v1 prefix is applied by main.py at include time, matching the
# other domain routers.
router = APIRouter(tags=["Deck share links"])
public_router = APIRouter(tags=["Deck share links (public)"])

# backend/app/domains/deck_share_links/routes.py -> backend/app/static/deck
DECK_ROOT = (Path(__file__).resolve().parents[2] / "static" / "deck").resolve()

MAX_LABEL_LEN = 120
MAX_NOTE_LEN = 500


def _generate_token() -> str:
    """Mirror the ``shr_``/``sub_`` recipe: prefix + 192 bits of entropy."""
    return f"dck_{secrets.token_urlsafe(24)}"


def _as_aware(value: datetime | None) -> datetime | None:
    """Normalise naive timestamps (SQLite in tests) to UTC-aware."""
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _link_payload(link: DeckShareLink, base_url: str = "") -> dict[str, Any]:
    opens = sorted(
        (_as_aware(v.viewed_at) for v in (link.views or []) if v.viewed_at),
        reverse=True,
    )
    return {
        "id": link.id,
        "prospect_label": link.prospect_label,
        "note": link.note,
        "url": f"{base_url}/deck/{link.token}",
        "token": link.token,
        "created_at": _as_aware(link.created_at),
        "expires_at": _as_aware(link.expires_at),
        "revoked_at": _as_aware(link.revoked_at),
        "is_revoked": link.is_revoked,
        "view_count": link.view_count or 0,
        "last_viewed_at": _as_aware(link.last_viewed_at),
        "opens": [o.isoformat() for o in opens[:20]],
    }


class DeckLinkCreate(BaseModel):
    prospect_label: str = Field(..., min_length=1, max_length=MAX_LABEL_LEN)
    note: str | None = Field(default=None, max_length=MAX_NOTE_LEN)


@router.post("/deck-links", status_code=201)
def create_deck_link(
    body: DeckLinkCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_org_owner),
) -> dict[str, Any]:
    label = body.prospect_label.strip()
    if not label:
        raise HTTPException(status_code=422, detail="Who is this link for?")

    link = DeckShareLink(
        prospect_label=label,
        note=(body.note or "").strip() or None,
        token=_generate_token(),
        created_by_user_id=current_user.id,
        view_count=0,
    )
    db.add(link)
    db.commit()
    db.refresh(link)
    return _link_payload(link)


@router.get("/deck-links")
def list_deck_links(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_org_owner),
) -> dict[str, Any]:
    links = (
        db.query(DeckShareLink)
        .order_by(DeckShareLink.created_at.desc())
        .all()
    )
    return {"links": [_link_payload(link) for link in links]}


@router.delete("/deck-links/{link_id}", status_code=200)
def revoke_deck_link(
    link_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_org_owner),
) -> dict[str, Any]:
    link = db.query(DeckShareLink).filter(DeckShareLink.id == link_id).first()
    if link is None:
        raise HTTPException(status_code=404, detail="Link not found")
    if link.revoked_at is None:
        link.revoked_at = datetime.now(timezone.utc)
        db.commit()
        db.refresh(link)
    return _link_payload(link)


def _resolve_live_link(token: str, db: Session) -> DeckShareLink:
    """404/410 ladder, matching the share-link convention.

    404 for an unknown token so a guesser cannot distinguish "never existed"
    from "revoked"; 410 once the recipient has a link we deliberately retired,
    so they see a clear message rather than a dead end.
    """
    link = (
        db.query(DeckShareLink).filter(DeckShareLink.token == token).first()
    )
    if link is None:
        raise HTTPException(status_code=404, detail="Not found")
    if link.revoked_at is not None:
        raise HTTPException(status_code=410, detail="This deck link has been revoked")
    expires_at = _as_aware(link.expires_at)
    if expires_at is not None and expires_at <= datetime.now(timezone.utc):
        raise HTTPException(status_code=410, detail="This deck link has expired")
    return link


def _safe_deck_file(relative_path: str) -> Path:
    """Resolve inside DECK_ROOT or 404. Blocks ../ traversal."""
    candidate = (DECK_ROOT / relative_path).resolve()
    if candidate != DECK_ROOT and DECK_ROOT not in candidate.parents:
        raise HTTPException(status_code=404, detail="Not found")
    if not candidate.is_file():
        raise HTTPException(status_code=404, detail="Not found")
    return candidate


def _no_index_headers(cache: str) -> dict[str, str]:
    return {
        "Cache-Control": cache,
        # Vercel proxies this response; without this it may cache a document
        # whose link we later revoke.
        "x-vercel-enable-rewrite-caching": "0",
        "X-Robots-Tag": "noindex, nofollow, noarchive",
        "Referrer-Policy": "no-referrer",
    }


@public_router.get("/deck/{token}")
def serve_deck_index(token: str, request: Request, db: Session = Depends(get_db)):
    """Serve the deck itself and record the open.

    Redirects to the trailing-slash form so the deck's relative asset paths
    (``investor-deck/deck.css``, ``img/surf-hub.png``) resolve under
    ``/deck/{token}/`` instead of ``/deck/``.
    """
    _resolve_live_link(token, db)
    return RedirectResponse(url=f"/deck/{token}/", status_code=307)


@public_router.get("/deck/{token}/")
def serve_deck_root(token: str, request: Request, db: Session = Depends(get_db)):
    link = _resolve_live_link(token, db)

    link.view_count = (link.view_count or 0) + 1
    link.last_viewed_at = datetime.now(timezone.utc)
    db.add(
        DeckShareView(
            deck_share_link_id=link.id,
            user_agent=(request.headers.get("user-agent") or "")[:300] or None,
        )
    )
    db.commit()

    return FileResponse(
        _safe_deck_file("index.html"),
        media_type="text/html",
        headers=_no_index_headers("private, no-store, max-age=0"),
    )


@public_router.get("/deck/{token}/{path:path}")
def serve_deck_asset(
    token: str, path: str, db: Session = Depends(get_db)
):
    """Serve a subresource. Gated by the same token; no view recorded."""
    link = _resolve_live_link(token, db)
    if not path or path.endswith("/"):
        raise HTTPException(status_code=404, detail="Not found")

    file_path = _safe_deck_file(path)
    media_type, _ = mimetypes.guess_type(str(file_path))
    # Subresources are scoped under the token path and useless without the
    # HTML, so a short private cache is safe and makes re-opens quick.
    return FileResponse(
        file_path,
        media_type=media_type or "application/octet-stream",
        headers=_no_index_headers("private, max-age=600"),
    )
