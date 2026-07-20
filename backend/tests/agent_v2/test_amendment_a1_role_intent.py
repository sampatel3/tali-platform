"""Amendment A1 — recruiter intent as first-class.

Covers: version chain (author_new_version), time-anchored fetch
(fetch_active_intent at past/present/future t), drift detection (novel
dimensions in overrides vs stated intent), graph schema constants, and
Pydantic contracts.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from sqlalchemy import event

from app.agent_runtime import role_intent as ri
from app.agent_runtime.contracts import StructuredIntent
from app.candidate_graph import schema as graph_schema
from app.models.agent_decision import AgentDecision
from app.models.candidate import Candidate
from app.models.candidate_application import CandidateApplication
from app.models.decision_feedback import DecisionFeedback
from app.models.organization import Organization
from app.models.role import Role
from app.models.role_intent import RoleIntent


_BIG_PK_COUNTERS = {
    "agent_decisions": 0,
    "decision_feedback": 0,
    "role_intents": 0,
}


def _assign(mapper, connection, target):  # pragma: no cover
    name = target.__table__.name
    if target.id is None and name in _BIG_PK_COUNTERS:
        _BIG_PK_COUNTERS[name] += 1
        target.id = _BIG_PK_COUNTERS[name]


event.listen(AgentDecision, "before_insert", _assign)
event.listen(DecisionFeedback, "before_insert", _assign)
event.listen(RoleIntent, "before_insert", _assign)


def _seed_role(db):
    org = Organization(name="A1 Org", slug=f"a1-{id(db)}")
    db.add(org)
    db.flush()
    role = Role(
        organization_id=org.id,
        name="Backend Engineer",
        source="manual",
        agentic_mode_enabled=True,
        monthly_usd_budget_cents=0,
    )
    db.add(role)
    db.flush()
    return SimpleNamespace(org=org, role=role)


def _seed_decision(db, *, org, role):
    """Drift tests need a real AgentDecision row to satisfy the FK on
    ``decision_feedback.decision_id``."""
    cand = Candidate(
        organization_id=org.id,
        email=f"a1-{id(db)}@x.test",
        full_name="A1 Cand",
    )
    db.add(cand)
    db.flush()
    app = CandidateApplication(
        organization_id=org.id,
        candidate_id=cand.id,
        role_id=role.id,
        status="applied",
        pipeline_stage="review",
        pipeline_stage_source="recruiter",
        application_outcome="open",
        source="manual",
    )
    db.add(app)
    db.flush()
    decision = AgentDecision(
        organization_id=org.id,
        role_id=role.id,
        application_id=app.id,
        decision_type="advance_to_interview",
        recommendation="advance_to_interview",
        status="pending",
        reasoning="drift fixture",
        confidence=0.5,
        model_version="m",
        prompt_version="p",
        idempotency_key=f"a1:{app.id}:advance",
    )
    db.add(decision)
    db.flush()
    return decision


def _user(db, org, idx=1):
    from app.models.user import User
    u = User(
        email=f"a1-{idx}-{id(db)}@x.test",
        hashed_password="x",
        is_active=True,
        is_superuser=False,
        is_verified=True,
        full_name="A1 User",
        organization_id=org.id,
    )
    db.add(u)
    db.flush()
    return u


# ---------------------------------------------------------------------------
# Pydantic contracts
# ---------------------------------------------------------------------------


def test_structured_intent_defaults_are_empty():
    s = StructuredIntent()
    assert s.soft_signals == []
    assert s.deal_breakers == []
    assert s.must_haves_missing_from_spec == []
    assert s.growth_expectations is None


def test_structured_intent_accepts_full_payload():
    s = StructuredIntent(
        soft_signals=["ambiguity tolerance"],
        deal_breakers=["can't push back on senior stakeholders"],
        growth_expectations="grow into team lead within 18 months",
        context_for_opening="backfill for someone who burned out",
        weighting_notes="prioritise communication over depth",
        must_haves_missing_from_spec=["domain knowledge in fintech"],
    )
    assert "ambiguity tolerance" in s.soft_signals
    assert s.context_for_opening.startswith("backfill")


# ---------------------------------------------------------------------------
# Graph vocabulary
# ---------------------------------------------------------------------------


def test_graph_schema_includes_role_intent_node_and_edges():
    assert graph_schema.NODE_ROLE_INTENT == "RoleIntent"
    assert graph_schema.EDGE_HAS_INTENT in graph_schema.ALL_EDGE_TYPES
    assert graph_schema.EDGE_AUTHORED_BY in graph_schema.ALL_EDGE_TYPES
    assert graph_schema.EDGE_SUPERSEDED_BY in graph_schema.ALL_EDGE_TYPES


# ---------------------------------------------------------------------------
# author_new_version + version chain
# ---------------------------------------------------------------------------


def test_author_first_version_starts_at_one(db):
    s = _seed_role(db)
    user = _user(db, s.org)
    row = ri.author_new_version(
        db,
        organization_id=int(s.org.id),
        role_id=int(s.role.id),
        structured=StructuredIntent(soft_signals=["resilience"]),
        free_text="backfill for burnout",
        authored_by_user_id=int(user.id),
    )
    db.commit()
    assert row.version == 1
    assert row.superseded_id is None
    assert row.valid_to is None
    assert row.valid_from is not None


def test_author_supersedes_prior_version(db):
    s = _seed_role(db)
    user = _user(db, s.org)
    v1 = ri.author_new_version(
        db,
        organization_id=int(s.org.id), role_id=int(s.role.id),
        structured=StructuredIntent(soft_signals=["resilience"]),
        authored_by_user_id=int(user.id),
    )
    db.commit()
    v2 = ri.author_new_version(
        db,
        organization_id=int(s.org.id), role_id=int(s.role.id),
        structured=StructuredIntent(soft_signals=["resilience", "ambiguity tolerance"]),
        authored_by_user_id=int(user.id),
    )
    db.commit()
    db.refresh(v1)
    db.refresh(v2)
    assert v2.version == 2
    assert v2.superseded_id == v1.id
    assert v1.valid_to is not None
    assert v2.valid_to is None
    # The two `valid_from / valid_to` align: prior closes at the new one's open.
    assert v1.valid_to == v2.valid_from


# ---------------------------------------------------------------------------
# fetch_active_intent
# ---------------------------------------------------------------------------


def test_fetch_active_intent_returns_none_when_no_versions(db):
    s = _seed_role(db)
    out = ri.fetch_active_intent(db, role_id=int(s.role.id))
    assert out is None


def test_fetch_active_intent_returns_current_version(db):
    s = _seed_role(db)
    user = _user(db, s.org)
    previous = "First answer " + ("prior " * 20)
    ri.author_new_version(
        db, organization_id=int(s.org.id), role_id=int(s.role.id),
        structured=StructuredIntent(soft_signals=["resilience"]),
        free_text=previous,
        authored_by_user_id=int(user.id),
    )
    latest = "LATEST MUST-HAVE\n\nLatest second paragraph"
    ri.author_new_version(
        db, organization_id=int(s.org.id), role_id=int(s.role.id),
        structured=StructuredIntent(soft_signals=["resilience", "depth"]),
        free_text=f"{previous.strip()}\n\n{latest}",
        authored_by_user_id=int(user.id),
    )
    db.commit()
    out = ri.fetch_active_intent(db, role_id=int(s.role.id))
    assert out is not None
    assert out.version == 2
    assert "depth" in out.structured.soft_signals
    assert out.latest_free_text == latest


def test_fetch_active_intent_loads_predecessor_boundary_in_one_query(db):
    s = _seed_role(db)
    role_id = int(s.role.id)
    previous = "First answer"
    ri.author_new_version(
        db,
        organization_id=int(s.org.id),
        role_id=role_id,
        structured=StructuredIntent(),
        free_text=previous,
    )
    latest = "LATEST MUST-HAVE\n\nKeep this paragraph too."
    ri.author_new_version(
        db,
        organization_id=int(s.org.id),
        role_id=role_id,
        structured=StructuredIntent(),
        free_text=f"{previous}\n\n{latest}",
    )
    db.commit()
    statements: list[str] = []

    def capture_select(_conn, _cursor, statement, *_args):
        if statement.lstrip().upper().startswith("SELECT"):
            statements.append(statement)

    engine = db.get_bind()
    event.listen(engine, "before_cursor_execute", capture_select)
    try:
        out = ri.fetch_active_intent(db, role_id=role_id)
    finally:
        event.remove(engine, "before_cursor_execute", capture_select)

    assert out is not None
    assert out.latest_free_text == latest
    assert len(statements) == 1
    assert "LEFT OUTER JOIN role_intents AS" in statements[0]
    assert "role_intents.role_id = role_intents_1.role_id" in statements[0]
    assert (
        "role_intents.organization_id = role_intents_1.organization_id"
        in statements[0]
    )


def test_fetch_active_intent_at_historical_t_returns_old_version(db):
    s = _seed_role(db)
    user = _user(db, s.org)
    t0 = datetime.now(timezone.utc) - timedelta(days=10)
    t1 = datetime.now(timezone.utc) - timedelta(days=2)
    ri.author_new_version(
        db, organization_id=int(s.org.id), role_id=int(s.role.id),
        structured=StructuredIntent(soft_signals=["v1"]),
        authored_by_user_id=int(user.id),
        now=t0,
    )
    ri.author_new_version(
        db, organization_id=int(s.org.id), role_id=int(s.role.id),
        structured=StructuredIntent(soft_signals=["v2"]),
        authored_by_user_id=int(user.id),
        now=t1,
    )
    db.commit()
    # Probe between t0 and t1 — should see v1.
    out_old = ri.fetch_active_intent(
        db, role_id=int(s.role.id), t=t0 + timedelta(days=1)
    )
    assert out_old is not None
    assert out_old.version == 1
    # Probe after t1 — should see v2.
    out_new = ri.fetch_active_intent(
        db, role_id=int(s.role.id), t=t1 + timedelta(days=1)
    )
    assert out_new.version == 2


# ---------------------------------------------------------------------------
# drift detection
# ---------------------------------------------------------------------------


def test_drift_detect_returns_no_novel_when_overrides_match_intent(db):
    s = _seed_role(db)
    user = _user(db, s.org)
    ri.author_new_version(
        db, organization_id=int(s.org.id), role_id=int(s.role.id),
        structured=StructuredIntent(
            soft_signals=["communication clarity", "ownership"],
        ),
        authored_by_user_id=int(user.id),
    )
    decision = _seed_decision(db, org=s.org, role=s.role)
    # Add an override-style feedback row that cites a covered dimension.
    db.add(DecisionFeedback(
        decision_id=int(decision.id),
        reviewer_id=user.id,
        organization_id=s.org.id, role_id=s.role.id,
        failure_mode="other",
        correction_text="lack of communication clarity in interview",
        scope="role",
    ))
    db.commit()
    # Use a higher threshold than the default so a single override
    # carrying a few incidental tokens ("interview", "lack") doesn't
    # trip drift. The substantive dimensions are matched.
    report = ri.drift_detect(db, role_id=int(s.role.id), drift_threshold=10)
    assert "communication clarity" in report.stated_dimensions or \
        "clarity" in report.stated_dimensions
    # The substantive overlap surfaces in observed.
    assert any("clarity" in x for x in report.observed_dimensions)
    assert not report.is_drifting()


def test_drift_detect_flags_when_overrides_cite_novel_dimensions(db):
    s = _seed_role(db)
    user = _user(db, s.org)
    ri.author_new_version(
        db, organization_id=int(s.org.id), role_id=int(s.role.id),
        structured=StructuredIntent(soft_signals=["communication"]),
        authored_by_user_id=int(user.id),
    )
    decision = _seed_decision(db, org=s.org, role=s.role)
    # Three different novel-dimension feedback rows → exceeds default threshold.
    for text in [
        "needs deep fintech regulatory experience",
        "must understand fintech compliance frameworks",
        "lacks fintech regulatory knowledge",
    ]:
        db.add(DecisionFeedback(
            decision_id=int(decision.id),
            reviewer_id=user.id,
            organization_id=s.org.id, role_id=s.role.id,
            failure_mode="missing_signal",
            correction_text=text,
            scope="role",
        ))
    db.commit()
    report = ri.drift_detect(db, role_id=int(s.role.id), drift_threshold=2)
    # "fintech" / "regulatory" should appear in novel.
    novel_lower = {x.lower() for x in report.novel_dimensions}
    assert any("fintech" in x for x in novel_lower)
    assert report.is_drifting() is True


def test_drift_detect_handles_role_with_no_intent(db):
    s = _seed_role(db)
    user = _user(db, s.org)
    decision = _seed_decision(db, org=s.org, role=s.role)
    db.add(DecisionFeedback(
        decision_id=int(decision.id),
        reviewer_id=user.id,
        organization_id=s.org.id, role_id=s.role.id,
        failure_mode="other", correction_text="any text", scope="role",
    ))
    db.commit()
    report = ri.drift_detect(db, role_id=int(s.role.id))
    # No stated dimensions; everything observed is novel — but the
    # report still computes without exploding.
    assert report.stated_dimensions == set()
    assert report.sample_size == 1
