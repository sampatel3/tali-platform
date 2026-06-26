"""The scored-only / skip-terminal filters on the sync-advance revert backfill.

After "freeze only on terminal hand-off stages", the backfill that un-freezes
candidates #652 mis-advanced must:
- leave TERMINAL rows (offer/hired) alone — they're legitimately `advanced`;
- with --scored-only, skip UNSCORED rows so un-freezing never triggers a
  cv_match LLM re-score (zero-cost backfill).
"""

from app.models.candidate import Candidate
from app.models.candidate_application import CandidateApplication
from app.models.organization import Organization
from app.models.role import Role
from app.scripts.revert_sync_advanced_applications import (
    revert_sync_advanced_applications,
)


def _seed(db, *, workable_stage, cv_match_score, idx):
    org = Organization(name="O", slug=f"o-{id(db)}-{idx}")
    db.add(org); db.flush()
    role = Role(organization_id=org.id, name="R", source="manual")
    db.add(role); db.flush()
    cand = Candidate(organization_id=org.id, email=f"c{id(db)}-{idx}@x.test", full_name="C")
    db.add(cand); db.flush()
    app = CandidateApplication(
        organization_id=org.id, candidate_id=cand.id, role_id=role.id,
        status="advanced", pipeline_stage="advanced", pipeline_stage_source="sync",
        application_outcome="open", source="workable",
        workable_stage=workable_stage, cv_match_score=cv_match_score,
    )
    db.add(app); db.flush()
    return app


def test_scored_only_skips_unscored_and_terminal(db):
    scored_mid = _seed(db, workable_stage="Technical Interview", cv_match_score=42.0, idx=1)
    unscored_mid = _seed(db, workable_stage="Technical Interview", cv_match_score=None, idx=2)
    scored_terminal = _seed(db, workable_stage="Offer", cv_match_score=55.0, idx=3)
    db.commit()

    # Dry run, scored-only: only the scored mid-interview row is selected.
    summary = revert_sync_advanced_applications(db, apply=False, scored_only=True)
    assert summary["matched"] == 1
    assert summary["skipped_terminal"] == 1   # the Offer row
    assert summary["skipped_unscored"] == 1   # the unscored Technical Interview row

    # Apply: only the scored mid-interview row is un-frozen.
    revert_sync_advanced_applications(db, apply=True, scored_only=True)
    db.commit()
    assert scored_mid.pipeline_stage != "advanced"      # reverted (→ applied fallback)
    assert scored_mid.pipeline_stage_source == "system"
    assert unscored_mid.pipeline_stage == "advanced"    # left frozen (would re-score)
    assert scored_terminal.pipeline_stage == "advanced"  # legitimately advanced


def test_default_skips_terminal_but_keeps_unscored(db):
    # Without --scored-only, unscored mid-interview rows ARE reverted; only
    # terminal rows are skipped.
    unscored_mid = _seed(db, workable_stage="Final Interview", cv_match_score=None, idx=4)
    scored_terminal = _seed(db, workable_stage="Hired", cv_match_score=55.0, idx=5)
    db.commit()

    summary = revert_sync_advanced_applications(db, apply=True, scored_only=False)
    db.commit()
    assert summary["skipped_terminal"] == 1
    assert summary["skipped_unscored"] == 0
    assert unscored_mid.pipeline_stage != "advanced"     # reverted
    assert scored_terminal.pipeline_stage == "advanced"  # skipped
