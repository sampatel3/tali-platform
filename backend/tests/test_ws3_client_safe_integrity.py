"""WS3: the integrity/fraud readout is recruiter-only.

score_summary.integrity (trust band + warnings + corroborations) must reach the
recruiter detail payload but MUST be stripped from an external client share.
"""

from __future__ import annotations

from app.domains.assessments_runtime.role_support import application_detail_payload

from tests.sub_agents.conftest import make_full_application


# integrity_signals that produce a warning + a review verdict via the
# server-canonical build_integrity_warnings / aggregate_triangulation.
_SIGNALS = {
    "integrity_signals": {
        "timeline": {"triggered": True, "issues": [
            {"kind": "end_before_start", "detail": "Acme: ends 2018 before it starts 2020"},
        ]},
        "applied": True,
        "penalty_computed": 5.0,
    }
}


def test_integrity_reaches_recruiter_payload(db):
    _, _, _, app = make_full_application(db, cv_match_details=_SIGNALS)
    app.cv_match_score = 72.0
    db.flush()

    payload = application_detail_payload(app, include_cv_text=False, client_safe=False)
    integrity = payload["score_summary"]["integrity"]
    assert integrity is not None
    # The deterministic timeline artifact drives a strong_review verdict.
    assert integrity["verdict"] == "strong_review"
    assert any("Timeline" in w for w in integrity["warnings"])


def test_integrity_stripped_from_client_share(db):
    _, _, _, app = make_full_application(db, cv_match_details=_SIGNALS)
    app.cv_match_score = 72.0
    db.flush()

    payload = application_detail_payload(app, include_cv_text=False, client_safe=True)
    # score_summary survives (scores are shareable) but the integrity readout
    # — who we flagged and why — must be gone.
    assert isinstance(payload["score_summary"], dict)
    assert "integrity" not in payload["score_summary"]
