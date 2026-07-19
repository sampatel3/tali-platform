"""Tests for ``app.cv_matching.calibrators`` (RALPH 3.1).

Pure-Python: no numpy/sklearn. Covers:
- Platt fit→predict round-trip on synthetic data
- Isotonic fit→predict round-trip
- Auto-selection: small N picks Platt, large N picks Isotonic
- JSON persistence round-trip
- ``apply_calibrator`` returns None when no snapshot exists
"""

from __future__ import annotations

import json
import math

import pytest

from app.cv_matching.calibrators import api as calibrator_api
from app.cv_matching.calibrators import (
    IsotonicCalibrator,
    PlattCalibrator,
    apply_calibrator,
    fit_calibrator,
    load_calibrator,
    save_calibrator,
)
from app.cv_matching.calibrators.api import _SNAPSHOT_DIR


# --------------------------------------------------------------------------- #
# Platt                                                                        #
# --------------------------------------------------------------------------- #


def test_platt_fits_separable_data():
    """High raw scores → high P(advance)."""
    X = [10, 20, 30, 40, 50, 60, 70, 80, 90, 95]
    y = [False] * 5 + [True] * 5
    cal = PlattCalibrator().fit(X, y)
    p_low = cal.predict(15)
    p_high = cal.predict(85)
    assert p_low < 0.4
    assert p_high > 0.6
    assert p_low < p_high  # monotonic


def test_platt_handles_constant_label_data():
    """Edge case: all labels the same. Output should saturate, not crash."""
    cal = PlattCalibrator().fit([10, 20, 30], [True, True, True])
    # All-True training → predict ≈ 1 across the input range.
    assert cal.predict(20) > 0.5


def test_platt_round_trip_through_json():
    cal = PlattCalibrator().fit([10, 50, 90], [False, True, True])
    blob = cal.to_dict()
    serialised = json.loads(json.dumps(blob))
    restored = PlattCalibrator.from_dict(serialised)
    for x in [10, 50, 90]:
        assert abs(restored.predict(x) - cal.predict(x)) < 1e-9


def test_platt_to_dict_kind():
    cal = PlattCalibrator().fit([10, 90], [False, True])
    assert cal.to_dict()["kind"] == "platt"


# --------------------------------------------------------------------------- #
# Isotonic                                                                     #
# --------------------------------------------------------------------------- #


def test_isotonic_is_monotone():
    X = [10, 20, 30, 40, 50, 60, 70, 80, 90]
    y = [False, False, True, False, True, True, False, True, True]
    cal = IsotonicCalibrator().fit(X, y)
    # Sample at many points; predicted curve must be non-decreasing.
    last = -1.0
    for x in range(0, 101, 5):
        p = cal.predict(float(x))
        assert p >= last - 1e-9, f"non-monotone at x={x}: {p} < {last}"
        last = p


def test_isotonic_endpoint_clamping():
    cal = IsotonicCalibrator().fit([10, 50, 90], [False, True, True])
    # Far outside the training range — clamps to the edge breakpoint.
    assert cal.predict(-100) == cal.predict(10)
    assert cal.predict(1000) == cal.predict(90)


def test_isotonic_round_trip_through_json():
    X = [10, 30, 50, 70, 90]
    y = [False, False, True, True, True]
    cal = IsotonicCalibrator().fit(X, y)
    blob = cal.to_dict()
    restored = IsotonicCalibrator.from_dict(json.loads(json.dumps(blob)))
    for x in X:
        assert abs(restored.predict(x) - cal.predict(x)) < 1e-9


# --------------------------------------------------------------------------- #
# fit_calibrator (auto-selection + persistence)                                #
# --------------------------------------------------------------------------- #


def _cleanup_snapshot(role_family: str, dimension: str) -> None:
    paths = {
        calibrator_api._calibrator_path(role_family, dimension),
        calibrator_api._legacy_calibrator_path(role_family, dimension),
    }
    paths.update(
        _SNAPSHOT_DIR.glob(
            f"v2-{calibrator_api._pair_digest(role_family, dimension)}_*.json"
        )
    )
    for path in paths:
        path.unlink(missing_ok=True)


def test_fit_calibrator_selects_platt_for_small_n():
    role_family = "test_role_family_platt"
    dimension = "cv_fit"
    _cleanup_snapshot(role_family, dimension)

    X = list(range(0, 100, 10))  # 10 samples
    y = [False] * 5 + [True] * 5
    cal = fit_calibrator(role_family=role_family, dimension=dimension, X=X, y=y)
    assert isinstance(cal, PlattCalibrator)

    loaded = load_calibrator(role_family, dimension)
    assert isinstance(loaded, PlattCalibrator)
    assert abs(loaded.predict(50) - cal.predict(50)) < 1e-9
    _cleanup_snapshot(role_family, dimension)


def test_fit_calibrator_selects_isotonic_for_large_n():
    role_family = "test_role_family_isotonic"
    dimension = "cv_fit"
    _cleanup_snapshot(role_family, dimension)

    n = 1000
    X = [i * 0.1 for i in range(n)]
    # Sigmoid-shaped truth so isotonic has signal to fit.
    y = [
        (1.0 / (1.0 + math.exp(-(x - 50.0) / 10.0))) > 0.5 for x in X
    ]
    cal = fit_calibrator(role_family=role_family, dimension=dimension, X=X, y=y)
    assert isinstance(cal, IsotonicCalibrator)
    _cleanup_snapshot(role_family, dimension)


def test_apply_calibrator_returns_none_when_missing():
    assert apply_calibrator("nonexistent_role_family_xyz", "cv_fit", 50.0) is None


def test_apply_calibrator_round_trip():
    role_family = "test_apply_round_trip"
    dimension = "cv_fit"
    _cleanup_snapshot(role_family, dimension)

    X = [10, 20, 30, 40, 50, 60, 70, 80, 90]
    y = [False, False, False, False, False, True, True, True, True]
    fit_calibrator(role_family=role_family, dimension=dimension, X=X, y=y)
    p_high = apply_calibrator(role_family, dimension, 90.0)
    p_low = apply_calibrator(role_family, dimension, 10.0)
    assert p_high is not None and p_low is not None
    assert p_high > p_low
    _cleanup_snapshot(role_family, dimension)


def test_save_calibrator_writes_timestamped_and_latest():
    role_family = "test_save"
    dimension = "cv_fit"
    _cleanup_snapshot(role_family, dimension)
    cal = PlattCalibrator().fit([10, 90], [False, True])
    save_calibrator(role_family, dimension, cal)
    digest = calibrator_api._pair_digest(role_family, dimension)
    files = list(_SNAPSHOT_DIR.glob(f"v2-{digest}_*.json"))
    # One timestamped + one latest.
    assert len(files) == 2
    names = {f.name for f in files}
    assert any(n.endswith("_latest.json") for n in names)
    _cleanup_snapshot(role_family, dimension)


def test_colliding_legacy_pair_names_use_distinct_v2_snapshots(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(calibrator_api, "_SNAPSHOT_DIR", tmp_path)
    monkeypatch.setattr(calibrator_api, "_remote_enabled", lambda: False)
    first = PlattCalibrator(a=1.0, b=-1.0)
    second = PlattCalibrator(a=2.0, b=3.0)

    first_path = save_calibrator("a_b", "c", first)
    second_path = save_calibrator("a", "b_c", second)

    assert calibrator_api._legacy_calibrator_path(
        "a_b", "c"
    ) == calibrator_api._legacy_calibrator_path("a", "b_c")
    assert first_path != second_path
    assert load_calibrator("a_b", "c").a == 1.0
    assert load_calibrator("a", "b_c").a == 2.0


def test_supported_legacy_snapshot_is_copied_to_v2_without_deletion(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(calibrator_api, "_SNAPSHOT_DIR", tmp_path)
    monkeypatch.setattr(calibrator_api, "_remote_enabled", lambda: False)
    legacy = calibrator_api._legacy_calibrator_path("aws_glue", "role_fit")
    legacy.write_text(
        json.dumps(PlattCalibrator(a=1.25, b=-0.5).to_dict()),
        encoding="utf-8",
    )

    loaded = load_calibrator("aws_glue", "role_fit")
    canonical = calibrator_api._calibrator_path("aws_glue", "role_fit")

    assert isinstance(loaded, PlattCalibrator)
    assert loaded.a == 1.25
    assert legacy.is_file()
    assert canonical.is_file()
    assert json.loads(canonical.read_text(encoding="utf-8"))["_storage_identity"] == {
        "role_family": "aws_glue",
        "dimension": "role_fit",
    }


def test_unattributed_custom_legacy_collision_is_not_loaded_for_wrong_pair(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(calibrator_api, "_SNAPSHOT_DIR", tmp_path)
    monkeypatch.setattr(calibrator_api, "_remote_enabled", lambda: False)
    # These pairs share the historical filename. The first dimension is part
    # of the fixed production contract; the second is custom and therefore
    # cannot safely claim an identity-free historical file.
    legacy = calibrator_api._legacy_calibrator_path("a", "skills_coverage")
    assert legacy == calibrator_api._legacy_calibrator_path("a_skills", "coverage")
    legacy.write_text(
        json.dumps(PlattCalibrator(a=4.0, b=0.0).to_dict()),
        encoding="utf-8",
    )

    assert load_calibrator("a_skills", "coverage") is None
    assert not calibrator_api._calibrator_path("a_skills", "coverage").exists()

    loaded = load_calibrator("a", "skills_coverage")
    assert isinstance(loaded, PlattCalibrator)
    assert loaded.a == 4.0


def test_calibrator_storage_hashes_unsafe_names_without_collisions(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(calibrator_api, "_SNAPSHOT_DIR", tmp_path)
    cal = PlattCalibrator().fit([10, 90], [False, True])

    first = save_calibrator("../../first", "../role_fit", cal)
    second = save_calibrator("../../second", "../role_fit", cal)

    assert first.parent == tmp_path
    assert second.parent == tmp_path
    assert first.is_file()
    assert second.is_file()
    assert first != second
    assert ".." not in first.name
    remote_parts = calibrator_api._remote_key(
        "../../first", "../role_fit"
    ).split("/")
    assert remote_parts[0] == "calibrators"
    assert remote_parts[1].startswith("~")
    assert remote_parts[2].startswith("~")
    assert ".." not in remote_parts


def test_calibrator_snapshot_replaces_symlink_instead_of_writing_its_target(
    monkeypatch, tmp_path
):
    snapshot_dir = tmp_path / "snapshots"
    outside = tmp_path / "outside.json"
    outside.write_text("do not replace", encoding="utf-8")
    monkeypatch.setattr(calibrator_api, "_SNAPSHOT_DIR", snapshot_dir)
    latest = calibrator_api._calibrator_path("safe_role", "cv_fit")
    latest.parent.mkdir(parents=True)
    latest.symlink_to(outside)
    cal = PlattCalibrator().fit([10, 90], [False, True])

    saved = save_calibrator("safe_role", "cv_fit", cal)

    assert saved == latest
    assert not saved.is_symlink()
    assert outside.read_text(encoding="utf-8") == "do not replace"


def test_calibrator_failure_logs_never_include_storage_exception_text(
    monkeypatch, tmp_path, caplog
):
    from app.services import s3_service

    marker = "calibrator-storage-secret-marker"
    monkeypatch.setattr(calibrator_api, "_SNAPSHOT_DIR", tmp_path)
    monkeypatch.setattr(calibrator_api, "_remote_enabled", lambda: True)
    monkeypatch.setattr(
        s3_service,
        "download_from_s3",
        lambda _key, **_kwargs: (_ for _ in ()).throw(RuntimeError(marker)),
    )
    calibrator_api._remote_checked_at.clear()
    caplog.set_level("WARNING", logger="taali.cv_match.calibrators")

    latest = calibrator_api._calibrator_path("safe_role", "cv_fit")
    calibrator_api._refresh_from_remote("safe_role", "cv_fit", latest)

    monkeypatch.setattr(
        s3_service,
        "upload_bytes_to_s3",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError(marker)),
    )
    cal = PlattCalibrator().fit([10, 90], [False, True])
    save_calibrator("safe_role", "cv_fit", cal)

    monkeypatch.setattr(calibrator_api, "_remote_enabled", lambda: False)
    monkeypatch.setattr(
        calibrator_api.json,
        "loads",
        lambda _body: (_ for _ in ()).throw(RuntimeError(marker)),
    )
    assert load_calibrator("safe_role", "cv_fit") is None
    assert marker not in caplog.text


def test_oversized_remote_calibrator_does_not_replace_local_cache(
    monkeypatch, tmp_path
):
    from app.services import s3_service

    monkeypatch.setattr(calibrator_api, "_SNAPSHOT_DIR", tmp_path)
    monkeypatch.setattr(calibrator_api, "_remote_enabled", lambda: True)
    latest = calibrator_api._calibrator_path("safe_role", "cv_fit")
    latest.write_bytes(b'{"kind":"local"}')
    seen = {}

    def reject_oversized(_key, *, max_bytes):
        seen["max_bytes"] = max_bytes
        return None

    monkeypatch.setattr(s3_service, "download_from_s3", reject_oversized)
    calibrator_api._remote_checked_at.clear()

    calibrator_api._refresh_from_remote("safe_role", "cv_fit", latest)

    assert seen["max_bytes"] == calibrator_api.MAX_CALIBRATOR_SNAPSHOT_BYTES
    assert latest.read_bytes() == b'{"kind":"local"}'


@pytest.mark.parametrize(
    "remote_blob",
    [
        {"kind": "unknown"},
        {
            "kind": "platt",
            "a": float("nan"),
            "b": 0.0,
            "feature_scale": 1.0,
            "feature_shift": 0.0,
        },
        {
            "kind": "platt",
            "a": 1.0,
            "b": 0.0,
            "feature_scale": 0.0,
            "feature_shift": 0.0,
        },
        {"kind": "isotonic", "breakpoints": []},
        {
            "kind": "isotonic",
            "breakpoints": [{"x": 2.0, "y": 0.1}, {"x": 1.0, "y": 0.9}],
        },
        {
            "kind": "isotonic",
            "breakpoints": [{"x": 1.0, "y": 0.9}, {"x": 2.0, "y": 0.1}],
        },
        {
            "kind": "isotonic",
            "breakpoints": [{"x": 1.0, "y": 1.1}],
        },
    ],
)
def test_semantically_invalid_remote_calibrator_does_not_replace_local_cache(
    monkeypatch, tmp_path, remote_blob
):
    from app.services import s3_service

    monkeypatch.setattr(calibrator_api, "_SNAPSHOT_DIR", tmp_path)
    monkeypatch.setattr(calibrator_api, "_remote_enabled", lambda: True)
    latest = calibrator_api._calibrator_path("safe_role", "cv_fit")
    local_body = json.dumps(
        {
            **PlattCalibrator(a=1.0, b=2.0).to_dict(),
            "_storage_identity": {
                "role_family": "safe_role",
                "dimension": "cv_fit",
            },
        }
    ).encode("utf-8")
    latest.write_bytes(local_body)
    monkeypatch.setattr(
        s3_service,
        "download_from_s3",
        lambda _key, **_kwargs: json.dumps(remote_blob).encode("utf-8"),
    )
    calibrator_api._remote_checked_at.clear()

    calibrator_api._refresh_from_remote("safe_role", "cv_fit", latest)

    assert latest.read_bytes() == local_body


def test_oversized_local_calibrator_is_rejected_before_json_parse(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(calibrator_api, "_SNAPSHOT_DIR", tmp_path)
    latest = calibrator_api._calibrator_path("safe_role", "cv_fit")
    with latest.open("wb") as handle:
        handle.truncate(calibrator_api.MAX_CALIBRATOR_SNAPSHOT_BYTES + 1)
    monkeypatch.setattr(
        calibrator_api.json,
        "loads",
        lambda _body: pytest.fail("oversized snapshot reached JSON parsing"),
    )

    assert load_calibrator("safe_role", "cv_fit") is None


# ---------------------------------------------------------------------------
# Realized-outcome label extraction (model-refinement loop)
# ---------------------------------------------------------------------------

from types import SimpleNamespace  # noqa: E402

from app.cv_matching.calibrators.extractor import (  # noqa: E402
    _default_role_family_mapper,
    _outcome_action,
    _extract_raw_scores,
    _role_family_for,
)


def _app(**kw):
    base = {"workable_disqualified": False, "application_outcome": "open", "workable_stage": None}
    base.update(kw)
    return SimpleNamespace(**base)


def test_role_family_prefers_persisted_archetype_and_falls_back_to_role_name():
    role = SimpleNamespace(name="Senior Backend Engineer")
    assert _role_family_for(
        {"archetype_id": "backend_platform"}, role, _default_role_family_mapper
    ) == "backend_platform"
    assert _role_family_for({}, role, _default_role_family_mapper) == "senior_backend_engineer"


class TestOutcomeAction:
    def test_disqualified_is_reject(self):
        assert _outcome_action(_app(workable_disqualified=True, workable_stage="technical interview")) == "reject"

    def test_hired_is_advance(self):
        assert _outcome_action(_app(application_outcome="hired", workable_stage="hired")) == "advance"

    def test_rejected_is_reject(self):
        assert _outcome_action(_app(application_outcome="rejected", workable_stage="rejected")) == "reject"

    def test_offer_open_is_advance(self):
        # offer keeps outcome=open but is a positive label via workable_stage.
        assert _outcome_action(_app(workable_stage="Offer")) == "advance"
        assert _outcome_action(_app(workable_stage="Offer Extended")) == "advance"

    def test_in_funnel_is_none(self):
        assert _outcome_action(_app(workable_stage="technical interview")) is None
        assert _outcome_action(_app(workable_stage=None)) is None

    def test_positive_outcome_wins_over_disqualified(self):
        # A candidate disqualified for a non-reject reason (e.g. role filled)
        # after reaching a positive outcome/stage must NOT be mislabeled reject.
        assert _outcome_action(_app(workable_disqualified=True, application_outcome="hired", workable_stage="hired")) == "advance"
        assert _outcome_action(_app(workable_disqualified=True, workable_stage="Offer")) == "advance"
        # Explicit rejected outcome still rejects even if not disqualified.
        assert _outcome_action(_app(application_outcome="rejected")) == "reject"


def test_extract_raw_scores_handles_no_override():
    details = {
        "role_fit_score": 73.0,
        "cv_fit_score": 70.0,
        "requirements_match_score": 75.0,
        "dimension_scores": {"skills_coverage": 80.0},
    }
    scores = _extract_raw_scores(details, None)
    assert scores["role_fit"] == 73.0
    assert scores["cv_fit"] == 70.0
    assert scores["requirements_match"] == 75.0
    assert scores["skills_coverage"] == 80.0


def test_calibration_beat_tasks_registered_and_scheduled():
    """Regression guard for the calibration tasks' scheduling contract.

    ``recalibrate_cv_match`` is pure math over stored rows — free — so it
    stays on the nightly beat schedule (and must be registered, or beat
    fires a name the worker drops). ``score_terminal_for_calibration``
    dispatches PAID Anthropic scoring, so per the no-auto-paid-jobs
    policy (2026-07-02) it must be registered for explicit runs but must
    NOT be on the beat schedule. Confirmed stale-score recovery has a
    separate, stricter contract tested with its implementation: Beat may
    recover only durable recruiter-authorized rows."""
    import app.tasks  # noqa: F401 — triggers the eager task imports
    from app.tasks.celery_app import celery_app

    scheduled = {entry["task"] for entry in celery_app.conf.beat_schedule.values()}

    free_task = "app.tasks.calibration_tasks.recalibrate_cv_match"
    assert free_task in celery_app.tasks, f"{free_task} not registered on the worker"
    assert free_task in scheduled, f"{free_task} missing from beat_schedule"

    paid_task = "app.tasks.calibration_tasks.score_terminal_for_calibration"
    assert paid_task in celery_app.tasks, f"{paid_task} not registered on the worker"
    assert paid_task not in scheduled, (
        f"{paid_task} dispatches paid scoring and must not run on a schedule"
    )
