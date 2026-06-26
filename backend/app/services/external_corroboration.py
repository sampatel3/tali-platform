"""LinkedIn URL cross-check (Prong 2, Wave 3).

Diff the CV against the candidate's OWN public LinkedIn profile — the URL THEY
gave us (parsed from the CV ``links[]`` or the Workable ``social_profiles``),
never name-based discovery. No identity-matching guesswork, no scraping of
strangers — we only ever fetch a profile the candidate themselves linked.

Flag-only and fail-open:
  * match → corroborates the CV history;
  * mismatch → a QUESTION for the recruiter (the candidate controls both
    documents), never an auto-reject;
  * no URL / no fetchable profile (common in MENA) → no signal, no penalty for
    absence.

The fetch route is a **pluggable adapter**. LinkedIn blocks unauthenticated
fetches, so a real deployment wires a provider (or accepts partial coverage) via
``set_linkedin_fetcher``; until then the default fetcher returns ``None`` and the
lever is inert — the diff logic is fully built and tested regardless. Gated on
``LINKEDIN_CORROBORATION_ENABLED``. Async enrichment → persisted into
``cv_match_details.integrity_signals.linkedin``.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("taali.external_corroboration")

_LINKEDIN_RE = re.compile(r"https?://([a-z0-9-]+\.)?linkedin\.com/[^\s\"'<>]+", re.I)


@dataclass
class LinkedInProfile:
    """Minimal parsed profile. ``experience`` entries mirror the CV-history
    shape (``company`` / ``title`` / ``start`` / ``end``) so they diff directly
    against ``cv_sections.experience`` via ``diff_cv_vs_workable_history``."""

    url: str = ""
    experience: list[dict[str, Any]] = field(default_factory=list)


# Pluggable fetch route. Default = no provider wired → None (fail open).
_FETCHER: Callable[[str], "LinkedInProfile | None"] | None = None


def set_linkedin_fetcher(fn: Callable[[str], "LinkedInProfile | None"] | None) -> None:
    """Wire a real provider/scraper: ``fn(url) -> LinkedInProfile | None``."""
    global _FETCHER
    _FETCHER = fn


def fetch_linkedin_profile(url: str) -> LinkedInProfile | None:
    """Fetch + parse a public LinkedIn profile via the wired adapter. Returns
    ``None`` (no signal) when no provider is wired or the fetch fails."""
    if not url or _FETCHER is None:
        return None
    try:
        return _FETCHER(url)
    except Exception:  # pragma: no cover — provider failures are no-signal
        logger.debug("linkedin fetch failed for %s", url, exc_info=True)
        return None


def extract_linkedin_url(
    cv_sections: dict[str, Any] | None, social_profiles: Any = None
) -> str | None:
    """Pull the candidate's own LinkedIn URL from the Workable ``social_profiles``
    or the CV ``links[]`` — whichever we already hold. ``None`` if absent."""
    for sp in social_profiles or []:
        if not isinstance(sp, dict):
            continue
        if "linkedin" in str(sp.get("type") or "").lower():
            u = str(sp.get("url") or "").strip()
            if u:
                return u
        m = _LINKEDIN_RE.search(str(sp.get("url") or ""))
        if m:
            return m.group(0)
    sections = cv_sections if isinstance(cv_sections, dict) else {}
    for link in sections.get("links") or []:
        text = link if isinstance(link, str) else str((link or {}).get("url") or "")
        m = _LINKEDIN_RE.search(text)
        if m:
            return m.group(0)
    return None


def corroborate_linkedin(
    *,
    cv_sections: dict[str, Any] | None,
    social_profiles: Any = None,
) -> dict[str, Any] | None:
    """Run the LinkedIn cross-check. Returns the ``integrity_signals.linkedin``
    payload, or ``None`` when disabled / no URL / no fetchable profile.
    Fail-open throughout."""
    from ..platform.config import settings

    if not settings.LINKEDIN_CORROBORATION_ENABLED:
        return None
    try:
        from .fraud_detection import diff_cv_vs_workable_history

        url = extract_linkedin_url(cv_sections, social_profiles)
        if not url:
            return None
        profile = fetch_linkedin_profile(url)
        if profile is None or not profile.experience:
            return None
        sections = cv_sections if isinstance(cv_sections, dict) else {}
        cv_exp = sections.get("experience") or []
        diff = diff_cv_vs_workable_history(cv_exp, profile.experience)
        return {
            "status": "mismatch" if diff.triggered else "match",
            "url": url,
            "diff": diff.to_dict(),
        }
    except Exception:  # pragma: no cover — never break scoring on a flag
        logger.debug("linkedin corroboration failed", exc_info=True)
        return None
