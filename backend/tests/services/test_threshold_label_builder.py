"""Terminal-outcome label derivation (incl. the post-handover stage-bug fix)."""

import itertools

from app.models.candidate import Candidate
from app.models.candidate_application import CandidateApplication
from app.models.organization import Organization
from app.models.role import ROLE_KIND_SISTER, Role
from app.models.sister_role_evaluation import SisterRoleEvaluation
from app.services.threshold_calibration.label_builder import (
    build_labelled_pairs,
    label_for_application,
)

_ctr = itertools.count(1)


def _org_role(db):
    org = Organization(name="O", slug=f"o-{next(_ctr)}")
    db.add(org)
    db.flush()
    role = Role(organization_id=org.id, name="R", source="manual")
    db.add(role)
    db.flush()
    db.commit()
    return org, role


def _app(db, org, role, *, score=70.0, outcome="open", stage="applied",
         wstage=None, disq=False, cv=True):
    n = next(_ctr)
    cand = Candidate(organization_id=org.id, email=f"c{n}@x.test", full_name=f"C{n}")
    db.add(cand)
    db.flush()
    a = CandidateApplication(
        organization_id=org.id, candidate_id=cand.id, role_id=role.id,
        status="applied", pipeline_stage=stage, pipeline_stage_source="recruiter",
        application_outcome=outcome, source="manual", cv_text="x",
        cv_match_score=(score if cv else None), workable_stage=wstage,
        workable_disqualified=disq,
    )
    db.add(a)
    db.commit()
    return a


def test_rejected_wins_over_advanced(db):
    org, role = _org_role(db)
    a = _app(db, org, role, outcome="rejected", stage="advanced", wstage="final_interview")
    assert label_for_application(a) == 0


def test_advanced_and_hired_positive(db):
    org, role = _org_role(db)
    assert label_for_application(_app(db, org, role, stage="advanced")) == 1
    assert label_for_application(_app(db, org, role, outcome="hired")) == 1


def test_post_handover_stage_positive_incl_bugfix(db):
    org, role = _org_role(db)
    # final_interview was already covered; first_stage/technical/presentation
    # are the previously-dropped stages this change adds.
    for st in ("final_interview", "first_stage", "technical", "presentation"):
        assert label_for_application(_app(db, org, role, wstage=st)) == 1, st


def test_disqualified_is_negative(db):
    org, role = _org_role(db)
    assert label_for_application(_app(db, org, role, disq=True, stage="advanced")) == 0


def test_open_excluded(db):
    org, role = _org_role(db)
    assert label_for_application(_app(db, org, role, outcome="open", stage="applied")) is None


def test_build_pairs_excludes_unscored_and_open(db):
    org, role = _org_role(db)
    _app(db, org, role, score=80, stage="advanced")    # positive
    _app(db, org, role, score=20, outcome="rejected")  # negative
    _app(db, org, role, score=50, outcome="open")      # excluded (open)
    _app(db, org, role, outcome="rejected", cv=False)  # excluded (no score)
    ls = build_labelled_pairs(db, organization_id=org.id)
    assert ls.n_positive == 1
    assert ls.n_negative == 1
    assert sorted(ls.pairs) == [(20.0, 0), (80.0, 1)]


def test_labels_preserve_opposing_owner_and_related_membership_truth(db):
    """One physical row contributes two independent role-local labels/scores."""

    org, owner = _org_role(db)
    related = Role(
        organization_id=org.id,
        name="Independent related role",
        source="sister",
        role_kind=ROLE_KIND_SISTER,
        ats_owner_role_id=owner.id,
    )
    db.add(related)
    db.flush()
    app = _app(db, org, owner, score=90.0, outcome="hired", stage="advanced")
    app.cv_match_details = {"prompt_version": "owner-prompt"}
    db.add(
        SisterRoleEvaluation(
            organization_id=org.id,
            role_id=related.id,
            candidate_id=app.candidate_id,
            source_application_id=app.id,
            ats_application_id=app.id,
            status="done",
            pipeline_stage="applied",
            application_outcome="rejected",
            membership_source="ground_truth_eval",
            spec_fingerprint="threshold-related-truth",
            role_fit_score=10.0,
            prompt_version="related-prompt",
        )
    )
    db.commit()

    owner_labels = build_labelled_pairs(
        db, organization_id=org.id, role_id=owner.id
    )
    related_labels = build_labelled_pairs(
        db, organization_id=org.id, role_id=related.id
    )
    organization_labels = build_labelled_pairs(db, organization_id=org.id)

    assert owner_labels.pairs == [(90.0, 1)]
    assert owner_labels.prompt_version == "owner-prompt"
    assert related_labels.pairs == [(10.0, 0)]
    assert related_labels.prompt_version == "related-prompt"
    assert sorted(organization_labels.pairs) == [(10.0, 0), (90.0, 1)]
