"""Prong-2 cross-source corroboration (Waves 2-4): graph collective
corroboration, LinkedIn URL diff, CV-internal coherence (years-vs-span +
anachronism), and the triangulation aggregator.
"""

from __future__ import annotations

from app.platform.config import settings
from app.services import external_corroboration as ec
from app.services.fraud_detection import (
    aggregate_triangulation,
    detect_experience_inflation,
    detect_tech_anachronism,
)
from app.services.graph_corroboration import corroborate_claimed_stack


# ── Wave 2: graph collective corroboration (pure analyser) ──────────────────
def _dist(total, skills):
    return {"company": "Acme", "total_candidates": total, "skills": skills}


def test_graph_corroborated_when_sharing_signature_stack():
    dist = _dist(10, {"python": 8, "spark": 7, "airflow": 6, "sas": 1})
    res = corroborate_claimed_stack(["Python", "Spark"], dist, min_observations=5)
    assert res.status == "corroborated"
    assert "python" in res.matched_skills


def test_graph_anomaly_when_sharing_nothing_with_concentrated_company():
    # Company is overwhelmingly SAS/SQL; candidate claims a bleeding-edge stack
    # nobody there shows → anomaly (the inflation-to-spec tell).
    dist = _dist(12, {"sas": 11, "sql": 10, "excel": 8})
    res = corroborate_claimed_stack(["PyTorch", "Kubernetes", "Rust"], dist, min_observations=5)
    assert res.status == "anomaly"


def test_graph_cold_start_fails_open():
    dist = _dist(3, {"python": 3})  # below min_observations
    res = corroborate_claimed_stack(["Python"], dist, min_observations=5)
    assert res.status == "no_signal"


def test_graph_diffuse_company_is_no_signal():
    # No skill reaches the concentration threshold → can't judge an outlier.
    dist = _dist(10, {"a": 2, "b": 2, "c": 2, "d": 1})
    res = corroborate_claimed_stack(["z"], dist, min_observations=5)
    assert res.status == "no_signal"


def test_graph_disabled_entry_returns_none():
    from app.services.graph_corroboration import corroborate_candidate_stack

    assert settings.GRAPH_CORROBORATION_ENABLED is False
    assert corroborate_candidate_stack(
        organization_id=1, cv_sections={"experience": [{"company": "Acme"}], "skills": ["python"]},
        min_observations=5,
    ) is None


# ── Wave 3: LinkedIn URL cross-check ────────────────────────────────────────
def test_extract_linkedin_url_from_social_and_links():
    assert ec.extract_linkedin_url(
        None, [{"type": "linkedin", "url": "https://www.linkedin.com/in/jane"}]
    ) == "https://www.linkedin.com/in/jane"
    got = ec.extract_linkedin_url({"links": ["see https://linkedin.com/in/bob here"]}, None)
    assert got == "https://linkedin.com/in/bob"
    assert ec.extract_linkedin_url({"links": ["https://github.com/x"]}, []) is None


def test_linkedin_disabled_returns_none():
    assert settings.LINKEDIN_CORROBORATION_ENABLED is False
    assert ec.corroborate_linkedin(cv_sections={"links": ["https://linkedin.com/in/x"]}) is None


def test_linkedin_match_and_mismatch_with_wired_fetcher(monkeypatch):
    monkeypatch.setattr(settings, "LINKEDIN_CORROBORATION_ENABLED", True)
    profile = ec.LinkedInProfile(
        url="x", experience=[{"company": "Acme", "start": "2019", "end": "2022"}]
    )
    ec.set_linkedin_fetcher(lambda url: profile)
    try:
        match = ec.corroborate_linkedin(
            cv_sections={
                "experience": [{"company": "Acme Corp", "start": "2019", "end": "2022"}],
                "links": ["https://linkedin.com/in/jane"],
            }
        )
        assert match["status"] == "match"
        mismatch = ec.corroborate_linkedin(
            cv_sections={
                "experience": [{"company": "Ghostco", "start": "2019", "end": "2022"}],
                "links": ["https://linkedin.com/in/jane"],
            }
        )
        assert mismatch["status"] == "mismatch"
    finally:
        ec.set_linkedin_fetcher(None)


def test_linkedin_no_url_fails_open(monkeypatch):
    monkeypatch.setattr(settings, "LINKEDIN_CORROBORATION_ENABLED", True)
    ec.set_linkedin_fetcher(lambda url: ec.LinkedInProfile(url=url, experience=[{"company": "X"}]))
    try:
        assert ec.corroborate_linkedin(cv_sections={"experience": [{"company": "X"}]}) is None
    finally:
        ec.set_linkedin_fetcher(None)


# ── Wave 4: CV-internal coherence ───────────────────────────────────────────
def test_experience_inflation_flags_impossible_total():
    # Claims 18 years but the whole career spans 2016-2022 (6 years).
    tl = [
        {"company": "A", "start_year": 2016, "end_year": 2019},
        {"company": "B", "start_year": 2019, "end_year": 2022},
    ]
    res = detect_experience_inflation(18.0, tl)
    assert res.triggered is True
    assert res.years_evidenced == 6.0


def test_experience_inflation_within_tolerance_clean():
    tl = [{"company": "A", "start_year": 2010, "end_year": 2024}]
    res = detect_experience_inflation(15.0, tl)  # span 14, claim 15 → gap 1 <= 2
    assert res.triggered is False


def test_experience_inflation_fails_open_without_timeline():
    assert detect_experience_inflation(20.0, []).triggered is False
    assert detect_experience_inflation(None, [{"start_year": 2010}]).triggered is False


def test_tech_anachronism_flags_tool_before_existence():
    exp = [{"company": "A", "title": "Eng", "end": "2010",
            "bullets": ["Ran Kubernetes clusters in production"]}]
    res = detect_tech_anachronism(exp)
    assert res.triggered is True
    assert res.issues[0]["tool"] == "kubernetes"


def test_tech_anachronism_no_false_positive_in_era_or_substring():
    # Tool used after it existed → clean; and "go" must not match "good".
    exp = [
        {"end": "2020", "bullets": ["Built services in Go and Kubernetes"]},
        {"end": "2008", "bullets": ["A very good engineer doing great work"]},
    ]
    assert detect_tech_anachronism(exp).triggered is False


# ── Wave 4: triangulation ───────────────────────────────────────────────────
def test_triangulation_ok_with_no_disagreements():
    assert aggregate_triangulation({})["verdict"] == "ok"
    assert aggregate_triangulation({"graph_corroboration": {"status": "corroborated"},
                                    "linkedin": {"status": "match"}})["verdict"] == "ok"


def test_triangulation_single_soft_is_review():
    out = aggregate_triangulation({"jd_shingle": {"triggered": True}})
    assert out["verdict"] == "review"
    assert out["disagreement_count"] == 1


def test_triangulation_two_soft_is_strong():
    out = aggregate_triangulation({
        "jd_shingle": {"triggered": True},
        "unverified_employers": {"count": 2},
    })
    assert out["verdict"] == "strong_review"
    assert set(out["soft_disagreements"]) == {"jd_mirroring", "unverified_employers"}


def test_triangulation_deterministic_artifact_is_strong():
    out = aggregate_triangulation({"document_hygiene": {"injection_detected": True}})
    assert out["verdict"] == "strong_review"
    assert "hidden_text" in out["deterministic_artifacts"]


def test_triangulation_records_corroborations():
    out = aggregate_triangulation({
        "graph_corroboration": {"status": "anomaly"},
        "linkedin": {"status": "match"},
    })
    assert "graph_anomaly" in out["soft_disagreements"]
    assert "linkedin" in out["corroborations"]


# ── Wave 4: PyPDF2 render-state scan (graceful) ─────────────────────────────
def test_render_state_scan_graceful_on_junk():
    from app.services.document_hygiene import scan_pdf_render_state

    assert scan_pdf_render_state(b"not a pdf")["checked"] is False
