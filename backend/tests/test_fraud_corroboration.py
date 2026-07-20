"""Prong-2 cross-source corroboration (Waves 2-4): graph collective
corroboration, GitHub cross-check, CV-internal coherence (years-vs-span +
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
                                    "github": {"status": "corroborated"}})["verdict"] == "ok"


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


def test_trust_band_maps_from_verdict():
    assert aggregate_triangulation({})["trust_band"] == "high"
    assert aggregate_triangulation({"jd_shingle": {"triggered": True}})["trust_band"] == "medium"
    two = aggregate_triangulation({"jd_shingle": {"triggered": True}, "unverified_employers": {"count": 1}})
    assert two["trust_band"] == "low"
    assert two["to_verify"] == 2
    det = aggregate_triangulation({"document_hygiene": {"injection_detected": True}})
    assert det["trust_band"] == "low"


def test_build_integrity_warnings_canonical_strings():
    from app.services.fraud_detection import build_integrity_warnings

    assert build_integrity_warnings({}) == []
    w = build_integrity_warnings({
        "github": {"status": "not_found", "username": "ghost"},
        "graph_corroboration": {"status": "anomaly", "companies": [{"status": "anomaly", "company": "BigBank"}]},
        "experience_inflation": {"triggered": True, "years_claimed": 18, "years_evidenced": 6},
    })
    joined = " ".join(w)
    assert "github.com/ghost" in joined
    assert "BigBank" in joined
    assert "18 years" in joined


def test_triangulation_records_corroborations():
    out = aggregate_triangulation({
        "graph_corroboration": {"status": "anomaly"},
        "github": {"status": "corroborated"},
    })
    assert "graph_anomaly" in out["soft_disagreements"]
    assert "github" in out["corroborations"]


# ── De-noise: the experience-span fix + dropping the over-eager Workable diff ──
def test_experience_inflation_uses_full_cv_history_not_capped_timeline():
    # Real cv_sections.experience shape (date STRINGS, oldest roles grouped into
    # one block back to 2010). A 15-year claim is corroborated by the ~16-year
    # span, so it must NOT flag — the bug was computing the span from the 5-capped
    # snapshot timeline, which dropped the old roles and faked a gap.
    cv_exp = [
        {"company": "Emirates", "start": "May 2022", "end": "Present"},
        {"company": "GameIN", "start": "Jul 2021", "end": "May 2022"},
        {"company": "RAKBANK", "start": "Nov 2020", "end": "Jun 2021"},
        {"company": "IBM", "start": "Nov 2019", "end": "Oct 2020"},
        {"company": "Algorythma", "start": "Jun 2017", "end": "Oct 2019"},
        {"company": "Lamsa, Gametion, Big Leap Studios", "start": "2010", "end": "2017"},
    ]
    res = detect_experience_inflation(15.0, cv_exp, now_year=2026)
    assert res.triggered is False
    assert res.years_evidenced == 16.0


def test_experience_inflation_still_flags_real_gap_with_string_dates():
    # Same string shape, but the claim really is impossible vs the evidence.
    cv_exp = [{"company": "A", "start": "2018", "end": "2022"}]
    res = detect_experience_inflation(20.0, cv_exp, now_year=2026)
    assert res.triggered is True


def test_workable_history_diff_is_not_surfaced_or_scored():
    from app.services.fraud_detection import build_integrity_warnings

    sig = {
        "workable_history_diff": {
            "triggered": True,
            "issues": [{"kind": "date_shift", "detail": "Acme: CV start 2018 vs Workable 2021"}],
        },
    }
    # No recruiter warning, and it does not move the trust verdict.
    assert build_integrity_warnings(sig) == []
    tri = aggregate_triangulation(sig)
    assert "workable_history_diff" not in tri["soft_disagreements"]
    assert tri["verdict"] == "ok"


def test_build_corroboration_notes_surfaces_positives_only():
    from app.services.fraud_detection import build_corroboration_notes

    assert build_corroboration_notes({}) == []
    gh = build_corroboration_notes(
        {"github": {"status": "corroborated", "username": "octocat", "matched_skills": ["python", "go"]}}
    )
    assert len(gh) == 1 and "github.com/octocat" in gh[0] and "python" in gh[0]
    graph = build_corroboration_notes({"graph_corroboration": {"status": "corroborated"}})
    assert len(graph) == 1
    # A failed / anomalous check is a warning, never a corroboration.
    assert build_corroboration_notes({"github": {"status": "not_found"}}) == []


# ── Wave 4: pypdf render-state scan (graceful) ──────────────────────────────
def test_render_state_scan_graceful_on_junk():
    from app.services.document_hygiene import scan_pdf_render_state

    assert scan_pdf_render_state(b"not a pdf")["checked"] is False


# ── Async shortlist enrichment gating (graph + GitHub off the hot path) ──────
def _fake_app(score, verdict):
    from types import SimpleNamespace

    details = {"integrity_signals": {"triangulation": {"verdict": verdict}}} if verdict else {}
    return SimpleNamespace(
        id=1, cv_match_score=score, cv_match_details=details,
        organization_id=1, candidate=None, cv_sections={},
    )


def test_should_enrich_false_when_both_axes_disabled():
    from app.services.corroboration_enrichment import should_enrich

    assert settings.GRAPH_CORROBORATION_ENABLED is False
    assert settings.GITHUB_CORROBORATION_ENABLED is False
    assert should_enrich(_fake_app(90, "strong_review")) is False


def test_should_enrich_requires_high_match_and_a_flag(monkeypatch):
    from app.services.corroboration_enrichment import should_enrich

    monkeypatch.setattr(settings, "GRAPH_CORROBORATION_ENABLED", True)
    assert should_enrich(_fake_app(80, "review")) is True  # match + flag
    assert should_enrich(_fake_app(80, "ok")) is False  # match, no flag
    assert should_enrich(_fake_app(80, None)) is False  # no triangulation
    assert should_enrich(_fake_app(20, "strong_review")) is False  # flag, not a candidate


# ── GitHub corroboration (free official API; corroborate-first, FP-safe) ─────
def test_extract_github_username_from_links_and_social():
    assert ec.extract_github_username({"links": ["see https://github.com/octocat"]}) == "octocat"
    assert ec.extract_github_username(None, [{"url": "https://www.github.com/jane-doe"}]) == "jane-doe"
    assert ec.extract_github_username({"links": ["https://github.com/orgs/acme"]}) is None  # reserved path
    assert ec.extract_github_username({"links": ["https://gitlab.com/x"]}) is None


def test_github_disabled_returns_none():
    assert settings.GITHUB_CORROBORATION_ENABLED is False
    assert ec.corroborate_github(cv_sections={"links": ["https://github.com/octocat"]}) is None


def test_github_corroborated_when_language_matches_claimed_stack(monkeypatch):
    monkeypatch.setattr(settings, "GITHUB_CORROBORATION_ENABLED", True)
    prof = ec.GithubProfile(username="octocat", exists=True, created_year=2011,
                            public_repos=20, languages=["python", "go"])
    out = ec.corroborate_github(
        cv_sections={"links": ["https://github.com/octocat"], "skills": ["Python", "SQL"]},
        fetcher=lambda u: prof,
    )
    assert out["status"] == "corroborated"
    assert "python" in out["matched_skills"]


def test_github_not_found_url_is_soft_flag(monkeypatch):
    monkeypatch.setattr(settings, "GITHUB_CORROBORATION_ENABLED", True)
    out = ec.corroborate_github(
        cv_sections={"links": ["https://github.com/ghost"], "skills": ["Python"]},
        fetcher=lambda u: ec.GithubProfile(username="ghost", exists=False),
    )
    assert out["status"] == "not_found"


def test_github_quiet_account_is_neutral_never_penalised(monkeypatch):
    # Account exists but its public languages don't overlap the claimed stack —
    # this is NOT fraud (private work is invisible). Must be no_signal, not a flag.
    monkeypatch.setattr(settings, "GITHUB_CORROBORATION_ENABLED", True)
    out = ec.corroborate_github(
        cv_sections={"links": ["https://github.com/q"], "skills": ["Python"]},
        fetcher=lambda u: ec.GithubProfile(username="q", exists=True, languages=["haskell"]),
    )
    assert out["status"] == "no_signal"


def test_triangulation_github_axes():
    nf = aggregate_triangulation({"github": {"status": "not_found"}})
    assert "github_not_found" in nf["soft_disagreements"]
    corr = aggregate_triangulation({"github": {"status": "corroborated"}})
    assert "github" in corr["corroborations"]
    assert corr["verdict"] == "ok"  # positive corroboration is not a disagreement


# ── P4: graph outcome prior nudge (SHADOW — never applies) ───────────────────
def test_outcome_prior_nudge_bounds_and_confidence_scaling():
    from app.services.graph_outcome_prior import outcome_prior_nudge

    assert outcome_prior_nudge(1.0, 1.0, max_nudge=5.0) == 5.0  # max advance, full conf
    assert outcome_prior_nudge(0.0, 1.0, max_nudge=5.0) == -5.0  # min advance
    assert outcome_prior_nudge(0.5, 1.0, max_nudge=5.0) == 0.0  # neutral
    assert outcome_prior_nudge(1.0, 0.5, max_nudge=5.0) == 2.5  # half confidence halves it
    assert abs(outcome_prior_nudge(1.0, 1.0, max_nudge=3.0)) <= 3.0  # respects the cap
    assert outcome_prior_nudge("bad", None, max_nudge=5.0) == 0.0  # fail-safe


def test_outcome_prior_shadow_is_never_applied():
    from app.services.graph_outcome_prior import build_outcome_prior_shadow

    assert build_outcome_prior_shadow(None, max_nudge=5.0) is None
    out = build_outcome_prior_shadow({"p_advance": 0.8, "confidence": 0.6}, max_nudge=5.0)
    assert out["applied"] is False  # SHADOW — the whole point
    assert out["would_be_nudge"] == 1.8  # (0.8-0.5)*2*0.6*5


def test_outcome_prior_fetch_fails_open():
    from app.services.graph_outcome_prior import fetch_outcome_prior

    # No graph configured / shadow not activated → None, never raises.
    assert fetch_outcome_prior(object(), None) is None
