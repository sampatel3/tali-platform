"""Frontend URL builders so MCP results include clickable deep-links.

Mirrors the ``pathForPage`` cases in ``frontend/src/app/routing.js``.
"""

from __future__ import annotations

from urllib.parse import quote

from ..platform.config import settings


def _frontend_base() -> str:
    base = (settings.FRONTEND_URL or "").rstrip("/")
    return base or "http://localhost:5173"


def role_url(role_id: int) -> str:
    return f"{_frontend_base()}/jobs/{quote(str(role_id))}"


def home_url() -> str:
    return f"{_frontend_base()}/home"


def roles_url() -> str:
    return f"{_frontend_base()}/jobs"


def candidates_url() -> str:
    return f"{_frontend_base()}/candidates"


def assessments_url() -> str:
    return f"{_frontend_base()}/assessments"


def application_url(application_id: int, role_id: int | None = None) -> str:
    base = f"{_frontend_base()}/candidates/{quote(str(application_id))}"
    if role_id is not None:
        return f"{base}?from=jobs/{quote(str(role_id))}"
    return base


def assessment_url(
    assessment_id: int,
    *,
    application_id: int | None = None,
    role_id: int | None = None,
) -> str:
    """Recruiter-safe link to an assessment result.

    Assessment results now live on the consolidated application page. Legacy
    rows without an application still use the assessment redirect route; the
    URL never contains the candidate's bearer token.
    """
    if application_id is not None:
        base = application_url(application_id, role_id=role_id)
        separator = "&" if "?" in base else "?"
        return f"{base}{separator}tab=assessment"
    return f"{_frontend_base()}/assessments/{quote(str(assessment_id))}"


def candidate_url(candidate_id: int) -> str:
    # No dedicated candidate-detail page; recruiter views land on the
    # application list filtered to that candidate's row.
    return f"{_frontend_base()}/candidates?candidate_id={quote(str(candidate_id))}"
