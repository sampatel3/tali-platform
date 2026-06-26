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


# ── GitHub corroboration (FREE official API, no provider, no scraping) ───────
# Unlike LinkedIn, GitHub has a free official API, so we build the real fetch.
# FAIRNESS-CRITICAL: a quiet/empty/private GitHub is NEVER a fraud signal — most
# strong engineers have little public code, and public activity is biased. So
# GitHub only ever CORROBORATES positively (claimed stack shows up in real
# repos) or raises a soft "doesn't resolve" question; it never penalises a
# candidate for the absence or thinness of public activity.
_GITHUB_RE = re.compile(r"https?://(www\.)?github\.com/([A-Za-z0-9-]+)", re.I)
_GITHUB_RESERVED = {
    "orgs", "features", "about", "pricing", "marketplace", "explore",
    "topics", "collections", "sponsors", "settings", "notifications",
}


@dataclass
class GithubProfile:
    username: str = ""
    exists: bool = False
    created_year: int | None = None
    public_repos: int = 0
    languages: list[str] = field(default_factory=list)  # lowercased repo languages


def extract_github_username(
    cv_sections: dict[str, Any] | None, social_profiles: Any = None
) -> str | None:
    """Pull the candidate's GitHub username from the Workable social_profiles or
    the CV links[]. Skips reserved (non-user) paths. None if absent."""
    candidates: list[str] = []
    for sp in social_profiles or []:
        if isinstance(sp, dict):
            candidates.append(str(sp.get("url") or ""))
    sections = cv_sections if isinstance(cv_sections, dict) else {}
    for link in sections.get("links") or []:
        candidates.append(link if isinstance(link, str) else str((link or {}).get("url") or ""))
    for text in candidates:
        m = _GITHUB_RE.search(text or "")
        if m:
            user = m.group(2).strip()
            if user and user.lower() not in _GITHUB_RESERVED:
                return user
    return None


def _real_github_fetch(username: str, *, token: str, timeout: float) -> GithubProfile | None:
    """Fetch a public GitHub profile + repo languages via the official API."""
    import httpx

    headers = {"Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    with httpx.Client(timeout=timeout, headers=headers) as client:
        u = client.get(f"https://api.github.com/users/{username}")
        if u.status_code == 404:
            return GithubProfile(username=username, exists=False)
        u.raise_for_status()
        data = u.json()
        created = str(data.get("created_at") or "")[:4]
        langs: list[str] = []
        try:
            r = client.get(
                f"https://api.github.com/users/{username}/repos",
                params={"sort": "pushed", "per_page": 100, "type": "owner"},
            )
            if r.status_code == 200:
                for repo in r.json() or []:
                    lang = repo.get("language") if isinstance(repo, dict) else None
                    if lang:
                        langs.append(str(lang).strip().lower())
        except Exception:  # pragma: no cover — repos best-effort
            pass
        return GithubProfile(
            username=username,
            exists=True,
            created_year=int(created) if created.isdigit() else None,
            public_repos=int(data.get("public_repos") or 0),
            languages=sorted(set(langs)),
        )


def fetch_github_profile(username: str) -> GithubProfile | None:
    """Real GitHub fetch (auth via GITHUB_TOKEN for the 5000/hr limit). Returns
    None on any error — never raises into scoring."""
    from ..platform.config import settings

    try:
        token = getattr(settings, "GITHUB_TOKEN", "") or ""
        return _real_github_fetch(username, token=token, timeout=8.0)
    except Exception:  # pragma: no cover — fetch failure = no signal
        logger.debug("github fetch failed for %s", username, exc_info=True)
        return None


def corroborate_github(
    *,
    cv_sections: dict[str, Any] | None,
    social_profiles: Any = None,
    fetcher: Any = None,
) -> dict[str, Any] | None:
    """Cross-check the candidate's GitHub against their claimed stack. Returns the
    ``integrity_signals.github`` payload, or None when disabled / no URL.

    Statuses: ``corroborated`` (a claimed skill shows up as a real repo
    language — positive), ``not_found`` (the GitHub URL on the CV doesn't
    resolve — a soft question), ``no_signal`` (account exists but quiet / no
    language overlap — NEUTRAL, never a penalty). ``fetcher`` is injectable for
    tests."""
    from ..platform.config import settings

    if not settings.GITHUB_CORROBORATION_ENABLED:
        return None
    try:
        username = extract_github_username(cv_sections, social_profiles)
        if not username:
            return None
        profile = (fetcher or fetch_github_profile)(username)
        if profile is None:
            return None
        if not profile.exists:
            return {"status": "not_found", "username": username}

        sections = cv_sections if isinstance(cv_sections, dict) else {}
        claimed = {str(s).strip().lower() for s in (sections.get("skills") or []) if s}
        langs = set(profile.languages or [])

        def _hit(skill: str) -> bool:
            return any(skill == lg or skill in lg or lg in skill for lg in langs)

        matched = sorted({s for s in claimed if _hit(s)})
        status = "corroborated" if matched else "no_signal"
        return {
            "status": status,
            "username": username,
            "public_repos": profile.public_repos,
            "created_year": profile.created_year,
            "matched_skills": matched[:12],
            "languages": sorted(langs)[:12],
        }
    except Exception:  # pragma: no cover — never break scoring on a flag
        logger.debug("github corroboration failed", exc_info=True)
        return None
