"""Unit tests for the compliance-script core functions (no DB).

Imports the pure computation functions from ``scripts/adverse_impact_report.py``
and ``scripts/aedt_audit_pack.py`` and exercises them on fixture data:
labels-CSV parsing, 4/5ths ratio math + small-n suppression, record joining,
and the AEDT section builders.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"


def _load(name):
    spec = importlib.util.spec_from_file_location(name, _SCRIPTS / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    # Register before exec so dataclass field(default_factory=...) resolution
    # can find the module in sys.modules.
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


air = _load("adverse_impact_report")
aedt = _load("aedt_audit_pack")


# ---------------------------------------------------------------------------
# Labels CSV parsing
# ---------------------------------------------------------------------------
def test_parse_labels_application_key_and_unknown_fill():
    rows = [
        {"application_id": "10", "gender": "F", "race": "white"},
        {"application_id": "11", "gender": "", "race": "black"},  # blank -> unknown
        {"application_id": "", "gender": "M", "race": "asian"},   # no key -> skipped
        {"application_id": "xx", "gender": "M", "race": "asian"}, # bad key -> skipped
    ]
    labels = air.parse_labels_csv(rows, fieldnames=["application_id", "gender", "race"])
    assert labels.key_kind == "application_id"
    assert labels.segment_columns == ["gender", "race"]
    assert labels.by_id[10] == {"gender": "F", "race": "white"}
    assert labels.by_id[11] == {"gender": air.UNKNOWN, "race": "black"}
    assert 0 not in labels.by_id and len(labels.by_id) == 2


def test_parse_labels_candidate_key():
    rows = [{"candidate_id": "5", "age_band": "30-39"}]
    labels = air.parse_labels_csv(rows, fieldnames=["candidate_id", "age_band"])
    assert labels.key_kind == "candidate_id"
    assert labels.by_id[5] == {"age_band": "30-39"}


def test_parse_labels_requires_key_column():
    try:
        air.parse_labels_csv([], fieldnames=["gender"])
    except ValueError as exc:
        assert "application_id" in str(exc)
    else:
        raise AssertionError("expected ValueError for missing key column")


# ---------------------------------------------------------------------------
# 4/5ths ratio math + small-n suppression
# ---------------------------------------------------------------------------
def test_impact_ratios_flag_below_threshold():
    # M selects 8/10 = 0.8 (reference); F selects 3/10 = 0.3 -> ratio 0.375 -> flag.
    counts = {"M": (10, 8), "F": (10, 3)}
    cells = air.compute_impact_ratios(counts, threshold=0.80, min_n=5)
    by_group = {c.group: c for c in cells}
    assert by_group["M"].ratio == 1.0
    assert by_group["M"].flagged is False
    assert round(by_group["F"].ratio, 3) == 0.375
    assert by_group["F"].flagged is True


def test_impact_ratios_at_threshold_not_flagged():
    # F rate 0.8, M rate 1.0 -> ratio 0.8 == threshold -> NOT flagged (strict <).
    counts = {"M": (10, 10), "F": (10, 8)}
    cells = air.compute_impact_ratios(counts, threshold=0.80, min_n=5)
    by_group = {c.group: c for c in cells}
    assert by_group["F"].ratio == 0.8
    assert by_group["F"].flagged is False


def test_small_n_suppressed_and_excluded_from_reference():
    # tiny group (n=2) is suppressed and does not become the reference even
    # though its raw rate (1.0) is highest.
    counts = {"M": (10, 5), "F": (10, 4), "NB": (2, 2)}
    cells = air.compute_impact_ratios(counts, threshold=0.80, min_n=5)
    by_group = {c.group: c for c in cells}
    assert by_group["NB"].suppressed is True
    assert by_group["NB"].rate is None
    assert by_group["NB"].flagged is False
    # reference is M's 0.5 (highest non-suppressed), so F 0.4 -> ratio 0.8.
    assert by_group["M"].ratio == 1.0
    assert by_group["F"].ratio == 0.8


def test_zero_reference_rate_is_parity():
    counts = {"M": (10, 0), "F": (10, 0)}
    cells = air.compute_impact_ratios(counts, threshold=0.80, min_n=5)
    for c in cells:
        assert c.ratio == 1.0
        assert c.flagged is False


# ---------------------------------------------------------------------------
# Record joining + tally
# ---------------------------------------------------------------------------
def _labels(by_id, cols, kind="application_id"):
    lab = air.Labels(key_kind=kind, segment_columns=cols)
    lab.by_id = by_id
    return lab


def test_build_records_joins_and_derives_intersections():
    labels = _labels(
        {100: {"gender": "F", "race": "white"}, 101: {"gender": "M", "race": "black"}},
        ["gender", "race"],
    )
    decision_map = {
        100: {"candidate_id": 1, "advance_reco": True, "advance_approved": True, "reject": False},
        101: {"candidate_id": 2, "advance_reco": False, "advance_approved": False, "reject": True},
        # 102 has a decision but no label -> excluded.
        102: {"candidate_id": 3, "advance_reco": True, "advance_approved": False, "reject": False},
    }
    records = air.build_records(labels, decision_map, hired_ids={100})
    assert len(records) == 2
    # Rejection is recorded as its favorable complement — a raw rejection rate
    # would invert the 4/5ths lens (the most-rejected group would never flag).
    r101 = next(r for r in records if r["application_id"] == 101)
    assert r101["non_reject"] is False
    r100 = next(r for r in records if r["application_id"] == 100)
    assert r100["non_reject"] is True
    assert r100["segments"]["gender×race"] == "F × white"
    assert r100["hire"] is True
    assert r100["advance_reco"] is True
    keys = air.segment_keys_for(labels)
    assert "gender×race" in keys and "gender×age_band" not in keys


def test_tally_counts_by_segment():
    records = [
        {"segments": {"gender": "F"}, "advance_reco": True},
        {"segments": {"gender": "F"}, "advance_reco": False},
        {"segments": {"gender": "M"}, "advance_reco": True},
    ]
    counts = air.tally_counts(records, segment_key="gender", metric="advance_reco")
    assert counts["F"] == (2, 1)
    assert counts["M"] == (1, 1)


def test_load_impact_ratio_threshold_reads_yaml():
    # Reads the real config file (0.80). Falls back to 0.80 if PyYAML missing.
    assert air.load_impact_ratio_threshold() == 0.80


def test_render_report_counts_flags():
    labels = _labels(
        {1: {"gender": "F"}, 2: {"gender": "M"}}, ["gender"]
    )
    # Build 20 records: 10 M all advanced, 10 F none advanced -> F flagged.
    records = []
    for i in range(10):
        records.append({"segments": {"gender": "M"}, "advance_reco": True,
                        "advance_approved": False, "non_reject": True, "hire": False})
        records.append({"segments": {"gender": "F"}, "advance_reco": False,
                        "advance_approved": False, "non_reject": True, "hire": False})
    text, flags = air.render_report(records, labels, threshold=0.80, min_n=5)
    assert flags >= 1
    assert "SEGMENT: gender" in text


# ---------------------------------------------------------------------------
# AEDT section builders
# ---------------------------------------------------------------------------
def test_section_system_description_lists_versions():
    versions = {
        "decision_versions": [{"model_version": "claude-x", "prompt_version": "p1"}],
        "engine_versions": [{"scoring_version": "cv_fit_v3", "score_rubric_version": "r2"}],
    }
    out = aedt.section_system_description(versions)
    assert "claude-x" in out and "p1" in out
    assert "cv_fit_v3" in out and "r2" in out
    assert out.startswith("## 1. System description")


def test_section_system_description_handles_empty():
    out = aedt.section_system_description({"decision_versions": [], "engine_versions": []})
    assert "No decisions recorded" in out
    assert "No CV-scoring engine versions" in out


def test_section_volume_table_and_total():
    volume = [
        {"decision_type": "advance_to_interview", "status": "approved", "n": 3},
        {"decision_type": "reject", "status": "pending", "n": 2},
    ]
    out = aedt.section_volume(volume)
    assert "| advance_to_interview | approved | 3 |" in out
    assert "**5**" in out


def test_section_oversight_percentages_and_median():
    oversight = {
        "dispositions": [
            {"human_disposition": "approved", "status": "approved", "n": 6},
            {"human_disposition": "overridden", "status": "overridden", "n": 3},
            {"human_disposition": "taught", "status": "reverted_for_feedback", "n": 1},
        ],
        "latencies_seconds": [30.0, 60.0, 120.0],
        "top_override_reasons": [{"resolution_note": "wrong fit", "n": 2}],
    }
    out = aedt.section_oversight(oversight)
    assert "approved: 6 (60.0%)" in out
    assert "overridden: 3 (30.0%)" in out
    assert "taught (send-back & correct): 1 (10.0%)" in out
    assert "wrong fit" in out
    assert "60s" in out  # median of [30,60,120] = 60


def test_section_integrity_rates():
    out = aedt.section_integrity(
        {"assessments_total": 10, "prompt_fraud_flagged": 2, "integrity_flagged": 1}
    )
    assert "assessments in window: **10**" in out
    assert "prompt-fraud flagged: 2 (20.0%)" in out


def test_section_bias_audit_not_configured():
    out = aedt.section_bias_audit(None)
    assert "Not configured" in out
    assert "bias_audit_thresholds.yaml" in out


def test_section_bias_audit_with_result():
    from datetime import datetime, timezone

    latest = {
        "policy_version_id": 7,
        "audited_at": datetime(2026, 6, 1, tzinfo=timezone.utc),
        "passed": True,
        "metrics_json": {"gender": {"F": 0.4}},
        "violations_json": None,
    }
    out = aedt.section_bias_audit(latest)
    assert "PASS" in out
    assert "policy_version_id: 7" in out
    assert "violations: none recorded" in out


def test_section_data_minimisation_references_blocklist():
    out = aedt.section_data_minimisation()
    assert "blocked_edge_attributes.yaml" in out
    assert "never stored" in out


def test_build_pack_assembles_all_sections():
    pack = aedt.build_pack(
        org_id=2,
        from_dt=None,
        to_dt=None,
        versions={"decision_versions": [], "engine_versions": []},
        volume=[],
        oversight={"dispositions": [], "latencies_seconds": [], "top_override_reasons": []},
        integrity={"assessments_total": 0, "prompt_fraud_flagged": 0, "integrity_flagged": 0},
        latest_bias_audit=None,
    )
    assert "# AEDT audit pack" in pack
    for heading in ("## 1.", "## 2.", "## 3.", "## 4.", "## 5.", "## 6.", "## 7."):
        assert heading in pack
    assert "organisation_id: 2" in pack


def test_rejection_lens_flags_the_most_rejected_group():
    """The Codex P1 scenario: 8/10 of one group rejected vs 2/10 of another.

    Under a raw rejection-rate lens the heavily-rejected group would sit at
    ratio 1.0 and never flag; under the favorable non-rejection lens it is the
    group below the 4/5ths threshold."""
    counts = {"A": (10, 2), "B": (10, 8)}  # non-rejected counts: A=2/10, B=8/10
    cells = {c.group: c for c in air.compute_impact_ratios(counts, threshold=0.80, min_n=5)}
    assert cells["A"].flagged is True      # 0.2/0.8 = 0.25 < 0.80
    assert cells["B"].flagged is False


def test_iso_to_bound_snaps_date_only_to_end_of_day():
    dt = air._iso("2026-06-30", end_of_day=True)
    assert (dt.hour, dt.minute, dt.second) == (23, 59, 59)
    # Explicit timestamps and lower bounds are untouched.
    assert air._iso("2026-06-30T10:00:00", end_of_day=True).hour == 10
    assert air._iso("2026-06-30").hour == 0
    import scripts.aedt_audit_pack as ap
    assert ap._iso("2026-06-30", end_of_day=True).hour == 23


def test_oversight_includes_auto_approved_bucket():
    """auto_promote roles resolve with human_disposition='auto_approved'; the
    oversight denominator must include them or 100 auto-approvals + 1 override
    reads as 100% overridden."""
    import scripts.aedt_audit_pack as ap

    text_out = ap.section_oversight({
        "dispositions": [
            {"human_disposition": "auto_approved", "status": "approved", "n": 100},
            {"human_disposition": "overridden", "status": "overridden", "n": 1},
        ],
        "latencies_seconds": [],
        "top_override_reasons": [],
    })
    assert "auto-approved (role auto_promote, no human review): 100 (99.0%)" in text_out
    assert "overridden: 1 (1.0%)" in text_out
