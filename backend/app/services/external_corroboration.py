"""GitHub corroboration (Prong 2) — cross-check the candidate's OWN GitHub URL
against their claimed stack via the FREE official GitHub API.

We already parse the GitHub URL into ``cv_sections.links``, and GitHub has a free
official API, so there's no provider, no scraping, no legal fight — and $0 run.

FAIRNESS-CRITICAL: a quiet / empty / private / language-mismatched GitHub is
**never** a fraud signal — most strong engineers have little public code, and
public activity is biased. So GitHub only ever CORROBORATES positively (a claimed
skill shows up as a real repo language) or raises a soft "this URL doesn't
resolve" question; it never penalises a candidate for the absence or thinness of
public activity.

Async enrichment, shortlist-gated. Gated ``GITHUB_CORROBORATION_ENABLED``.
Persisted into ``cv_match_details.integrity_signals.github``.

(A LinkedIn URL cross-check was scoped here too but DEFERRED — it needs a paid
data provider, the only paid axis. See the Linear feature ticket and
``docs/CV_FRAUD_FUNNEL_DESIGN.md`` §3.)
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("taali.external_corroboration")

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
    """Pull the candidate's GitHub username from the Workable ``social_profiles``
    or the CV ``links[]``. Skips reserved (non-user) paths. ``None`` if absent."""
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
    ``None`` on any error — never raises into scoring."""
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
    ``integrity_signals.github`` payload, or ``None`` when disabled / no URL.

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
