"""Direct unit tests for the new MCP tool handlers.

These bypass the MCP HTTP transport and call the pure-function handlers
in ``app.mcp.handlers`` directly. The MCP HTTP path is already covered
in ``test_mcp_server.py``; these focus on the v2 tools that wrap the
existing search services.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from app.candidate_search.schemas import (
    CandidateDeepVerification,
    GraphPayload,
    ParsedFilter,
    SearchOutput,
    SearchRetrievalSummary,
    SearchRetrievalTrace,
)
from app.mcp import handlers
from app.models.agent_decision import AgentDecision
from app.models.candidate import Candidate
from app.models.candidate_application import CandidateApplication
from app.models.organization import Organization
from app.models.role import ROLE_KIND_SISTER, Role
from app.models.sister_role_evaluation import SisterRoleEvaluation
from app.models.taali_chat_conversation import TaaliChatConversation
from app.models.user import User
from app.taali_chat.tool_registry import dispatch_tool


def _make_user_and_org(db) -> tuple[User, Organization]:
    org = Organization(name="Test Org", slug=f"org-{id(db)}")
    db.add(org)
    db.flush()
    user = User(
        email=f"u-{id(db)}@example.com",
        hashed_password="x",
        full_name="Test",
        organization_id=org.id,
        is_active=True,
        is_verified=True,
        is_superuser=False,
    )
    db.add(user)
    db.commit()
    return user, org


def _make_app(db, *, org_id, role, candidate_name, email, taali=None, pre_screen=None):
    candidate = Candidate(
        organization_id=org_id,
        email=email,
        full_name=candidate_name,
        position="Engineer",
    )
    db.add(candidate)
    db.flush()
    app = CandidateApplication(
        organization_id=org_id,
        candidate_id=candidate.id,
        role_id=role.id,
        status="applied",
        pipeline_stage="review",
        pipeline_stage_source="recruiter",
        application_outcome="open",
        source="manual",
        taali_score_cache_100=taali,
        pre_screen_score_100=pre_screen,
    )
    db.add(app)
    db.commit()
    return app


def _seed_sister_top_candidate_world(db, *, org: Organization) -> dict:
    """Build a role-local truth set whose owner and sister signals disagree.

    The canonical ATS rows deliberately cannot answer the sister-role query:
    the best sister candidate is the owner's below-threshold candidate, while
    the owner's strongest candidate has already advanced in the sister funnel.
    This catches both accidental owner-role filtering and owner-score leakage.
    """

    owner = Role(organization_id=org.id, name="ATS owner", source="manual")
    db.add(owner)
    db.flush()
    sister = Role(
        organization_id=org.id,
        name="Related AI Engineer",
        source="sister",
        role_kind=ROLE_KIND_SISTER,
        ats_owner_role_id=owner.id,
    )
    db.add(sister)
    db.flush()
    specs = (
        # key, display name, owner score, owner verdict/stage, sister score/stage
        ("best", "Sister Best", 12.0, "Below threshold", "review", 96.0, "applied"),
        (
            "second",
            "Sister Second",
            92.0,
            "Advance recommended",
            "applied",
            61.0,
            "review",
        ),
        (
            "sister_advanced",
            "Owner Best But Sister Advanced",
            99.0,
            "Advance recommended",
            "applied",
            30.0,
            "advanced",
        ),
        # Shared ATS progress is a writeback restriction only. It must not
        # remove or move this independent role-local membership.
        (
            "globally_advanced",
            "Globally Advanced",
            8.0,
            None,
            "advanced",
            98.0,
            "applied",
        ),
    )
    applications: dict[str, CandidateApplication] = {}
    for (
        key,
        name,
        owner_score,
        owner_verdict,
        owner_stage,
        sister_score,
        sister_stage,
    ) in specs:
        application = _make_app(
            db,
            org_id=org.id,
            role=owner,
            candidate_name=name,
            email=f"{key.replace('_', '-')}@x.test",
            taali=owner_score,
            pre_screen=owner_score,
        )
        application.pre_screen_recommendation = owner_verdict
        application.pipeline_stage = owner_stage
        applications[key] = application
        db.add(
            SisterRoleEvaluation(
                organization_id=org.id,
                role_id=sister.id,
                source_application_id=application.id,
                status="done",
                pipeline_stage=sister_stage,
                spec_fingerprint="sister-spec",
                role_fit_score=sister_score,
                summary=f"{name} related-role evidence.",
                # A completed score remains valid even when the optional JSON
                # details blob was not produced.
                details=(
                    None
                    if key == "second"
                    else {"summary": f"{name} related-role evidence."}
                ),
            )
        )
    db.commit()
    return {"owner": owner, "sister": sister, **applications}


# ---------------------------------------------------------------------------
# search_applications role projection
# ---------------------------------------------------------------------------


def test_search_applications_uses_sister_score_stage_and_safe_projection(db):
    """The generic application list must use selected-role truth.

    The sister role intentionally owns no ``CandidateApplication`` rows.  Its
    strongest result is below threshold on the ATS owner role, so either a
    direct sister-id filter or an owner-score filter makes this oracle fail.
    """

    user, org = _make_user_and_org(db)
    case = _seed_sister_top_candidate_world(db, org=org)
    assert (
        db.query(CandidateApplication)
        .filter(CandidateApplication.role_id == int(case["sister"].id))
        .count()
        == 0
    )

    rows = handlers.search_applications(
        db,
        user,
        role_id=int(case["sister"].id),
        min_score=70,
        score_type="taali",
        pipeline_stage="applied",
        sort_by="taali_score",
    )

    assert [row["application_id"] for row in rows] == [
        int(case["globally_advanced"].id),
        int(case["best"].id),
    ]
    row = next(item for item in rows if item["application_id"] == int(case["best"].id))
    assert row["role_id"] == int(case["sister"].id)
    assert row["role_name"] == case["sister"].name
    assert row["pipeline_stage"] == "applied"
    assert row["taali_score"] == 96.0
    assert row["pre_screen_score"] == 96.0
    assert row["rank_score"] == 96.0
    assert row["cv_match_score"] == 96.0
    assert row["role_fit_score"] == 96.0
    assert row["assessment_score"] is None
    assert "auto_reject_state" not in row
    assert row["score_mode"] == "sister_role"
    assert row["frontend_url"].endswith(
        f"/candidates/{case['best'].id}?from=jobs/{case['sister'].id}"
    )
    # The owner truth is deliberately the opposite and never surfaces as the
    # selected role's score, stage, or pre-screen verdict.
    assert case["best"].taali_score_cache_100 == 12.0
    assert case["best"].pipeline_stage == "review"
    assert case["best"].pre_screen_recommendation == "Below threshold"
    assert "pre_screen_recommendation" not in row


def test_search_role_candidates_filters_live_pending_decision_state(db):
    user, org = _make_user_and_org(db)
    role = Role(
        organization_id=int(org.id), name="Pending filter role", source="manual"
    )
    db.add(role)
    db.flush()
    pending = _make_app(
        db,
        org_id=int(org.id),
        role=role,
        candidate_name="Pending Candidate",
        email="pending-filter@test.example",
    )
    clear = _make_app(
        db,
        org_id=int(org.id),
        role=role,
        candidate_name="Clear Candidate",
        email="clear-filter@test.example",
    )
    db.add(
        AgentDecision(
            organization_id=int(org.id),
            role_id=int(role.id),
            application_id=int(pending.id),
            decision_type="advance_to_interview",
            recommendation="advance",
            status="reverted_for_feedback",
            reasoning="Needs recruiter review.",
            model_version="test-model",
            prompt_version="test-prompt",
            idempotency_key=f"pending-filter-{pending.id}",
        )
    )
    db.commit()

    with_pending = handlers.search_role_candidates(
        db,
        user,
        role_id=int(role.id),
        has_pending_decision=True,
    )
    without_pending = handlers.search_role_candidates(
        db,
        user,
        role_id=int(role.id),
        has_pending_decision=False,
    )

    assert with_pending["total"] == 1
    assert with_pending["items"][0]["application_id"] == int(pending.id)
    assert with_pending["filters"]["has_pending_decision"] is True
    assert without_pending["total"] == 1
    assert without_pending["items"][0]["application_id"] == int(clear.id)


def test_search_role_candidates_filters_related_role_explicit_ats_transport(db):
    """ATS filters must not confuse local membership with ATS transport.

    A fully independent related role can have its own source application and a
    separate owner-role application used only for shared ATS state.  The first
    candidate proves the linked transport is searched; the second deliberately
    carries a stale matching stage on its local row and proves that row is not
    treated as ATS authority.
    """

    user, org = _make_user_and_org(db)
    owner = Role(organization_id=int(org.id), name="ATS owner", source="manual")
    related = Role(
        organization_id=int(org.id),
        name="Independent related role",
        source="sister",
        role_kind=ROLE_KIND_SISTER,
        ats_owner_role=owner,
    )
    db.add_all([owner, related])
    db.flush()

    def _membership(
        *,
        key: str,
        local_ats_stage: str | None,
        transport_ats_stage: str,
    ) -> tuple[CandidateApplication, CandidateApplication]:
        candidate = Candidate(
            organization_id=int(org.id),
            email=f"{key}@related-ats.test",
            full_name=key.replace("-", " ").title(),
            position="Engineer",
        )
        db.add(candidate)
        db.flush()
        transport = CandidateApplication(
            organization_id=int(org.id),
            candidate_id=int(candidate.id),
            role_id=int(owner.id),
            status="applied",
            pipeline_stage="review",
            pipeline_stage_source="recruiter",
            application_outcome="open",
            source="workable",
            workable_candidate_id=f"wk-{key}",
            workable_stage=transport_ats_stage,
            external_stage_raw=transport_ats_stage,
        )
        local = CandidateApplication(
            organization_id=int(org.id),
            candidate_id=int(candidate.id),
            role_id=int(related.id),
            status="applied",
            pipeline_stage="review",
            pipeline_stage_source="recruiter",
            application_outcome="open",
            source="manual",
            # Deliberately stale/malformed local transport data. It must not
            # become ATS authority for this independent role.
            workable_stage=local_ats_stage,
            external_stage_raw=local_ats_stage,
        )
        db.add_all([transport, local])
        db.flush()
        db.add(
            SisterRoleEvaluation(
                organization_id=int(org.id),
                role_id=int(related.id),
                candidate_id=int(candidate.id),
                source_application_id=int(local.id),
                ats_application_id=int(transport.id),
                status="done",
                pipeline_stage="review",
                application_outcome="open",
                membership_source="direct",
                spec_fingerprint=f"spec-{key}",
                role_fit_score=80,
            )
        )
        return local, transport

    grounded_local, _grounded_transport = _membership(
        key="grounded-match",
        local_ats_stage=None,
        transport_ats_stage="Technical Interview",
    )
    _stale_local, _stale_transport = _membership(
        key="stale-local-match",
        local_ats_stage="Technical Interview",
        transport_ats_stage="Applied",
    )
    db.commit()

    result = handlers.search_role_candidates(
        db,
        user,
        role_id=int(related.id),
        ats_stage="Technical Interview",
    )

    assert result["total"] == 1
    assert result["total_is_exact"] is True
    assert result["items"][0]["application_id"] == int(grounded_local.id)
    assert result["items"][0]["role_id"] == int(related.id)
    assert result["items"][0]["pipeline_stage"] == "review"
    assert result["items"][0]["current_state"]["ats"]["raw_stage"] == (
        "Technical Interview"
    )


def test_role_bound_legacy_detail_and_compare_use_logical_role_projection(db):
    """Legacy tool names must not escape a role-bound Taali conversation."""

    user, org = _make_user_and_org(db)
    case = _seed_sister_top_candidate_world(db, org=org)
    conversation = TaaliChatConversation(
        organization_id=int(org.id),
        user_id=int(user.id),
        role_id=int(case["sister"].id),
        title="Related role truth",
    )
    db.add(conversation)
    db.flush()
    owner_only = _make_app(
        db,
        org_id=int(org.id),
        role=case["owner"],
        candidate_name="Owner Only Candidate",
        email="owner-only-role-bound@test.example",
        taali=77,
    )

    detail = dispatch_tool(
        "get_application",
        {"application_id": int(case["best"].id)},
        db=db,
        user=user,
        conversation=conversation,
    )
    comparison = dispatch_tool(
        "compare_applications",
        {
            "application_ids": [
                int(case["best"].id),
                int(case["second"].id),
            ]
        },
        db=db,
        user=user,
        conversation=conversation,
    )
    candidate = dispatch_tool(
        "get_candidate",
        {"candidate_id": int(case["best"].candidate_id)},
        db=db,
        user=user,
        conversation=conversation,
    )
    cv = dispatch_tool(
        "get_candidate_cv",
        {"candidate_id": int(case["best"].candidate_id)},
        db=db,
        user=user,
        conversation=conversation,
    )

    assert detail["role_id"] == int(case["sister"].id)
    assert detail["taali_score"] == 96.0
    assert detail["current_state"]["pipeline_stage"] == "applied"
    assert [row["role_id"] for row in comparison["applications"]] == [
        int(case["sister"].id),
        int(case["sister"].id),
    ]
    assert [row["scores"]["taali"] for row in comparison["applications"]] == [
        96.0,
        61.0,
    ]
    assert [row["pipeline_stage"] for row in comparison["applications"]] == [
        "applied",
        "review",
    ]
    assert len(candidate["applications"]) == 1
    assert candidate["applications"][0]["role_id"] == int(case["sister"].id)
    assert candidate["applications"][0]["taali_score"] == 96.0
    assert cv["candidate_id"] == int(case["best"].candidate_id)
    with pytest.raises(ValueError, match="not in the acting role"):
        dispatch_tool(
            "get_candidate_cv",
            {"candidate_id": int(owner_only.candidate_id)},
            db=db,
            user=user,
            conversation=conversation,
        )


# ---------------------------------------------------------------------------
# nl_search_candidates
# ---------------------------------------------------------------------------


def test_nl_search_candidates_passes_through_run_search(db):
    """Handler should call ``run_search`` and hydrate result ids into payloads."""
    user, org = _make_user_and_org(db)
    role = Role(organization_id=org.id, name="Backend", source="manual")
    db.add(role)
    db.commit()
    app1 = _make_app(
        db,
        org_id=org.id,
        role=role,
        candidate_name="Alice",
        email="alice@x.test",
        taali=80.0,
    )
    app2 = _make_app(
        db,
        org_id=org.id,
        role=role,
        candidate_name="Bob",
        email="bob@x.test",
        taali=70.0,
    )

    fake_result = SearchOutput(
        application_ids=[app2.id, app1.id],  # rerank changed order
        parsed_filter=ParsedFilter(skills_all=["aws"], free_text="aws engineers"),
        warnings=[],
        rerank_applied=True,
        database_matches=1,
        retrieval_matches=2,
        deep_checked=2,
        evidence_succeeded=1,
        evidence_failed=1,
        qualified=1,
        capped=True,
        exhaustive=False,
        verification_results=[
            CandidateDeepVerification(
                application_id=app2.id,
                status="qualified",
                reason="AWS delivery evidence",
            ),
            CandidateDeepVerification(
                application_id=app1.id,
                status="error",
                error_code="invalid_model_response",
            ),
        ],
        subgraph=None,
    )

    with patch(
        "app.candidate_search.runner.run_search", return_value=fake_result
    ) as runner:
        out = handlers.nl_search_candidates(
            db, user, query="aws engineers with 5 years", role_id=role.id
        )

    assert runner.called
    kwargs = runner.call_args.kwargs
    assert kwargs["organization_id"] == org.id
    assert kwargs["role_id"] == role.id
    assert kwargs["nl_query"] == "aws engineers with 5 years"
    assert kwargs["rerank_enabled"] is False
    assert kwargs["include_subgraph"] is False
    assert out["total_matched"] == 2
    assert out["database_matches"] == 1
    assert out["retrieval_matches"] == 2
    assert out["postgres_matches"] == 1
    assert out["is_exact_empty"] is False
    assert out["returned"] == 2
    assert out["rerank_applied"] is True
    assert out["deep_checked"] == 2
    assert out["evidence_succeeded"] == 1
    assert out["evidence_failed"] == 1
    assert out["qualified"] == 1
    assert out["capped"] is True
    assert out["exhaustive"] is False
    assert [item["status"] for item in out["verification_results"]] == [
        "qualified",
        "error",
    ]
    # Order from run_search must be preserved.
    assert [a["application_id"] for a in out["applications"]] == [app2.id, app1.id]
    assert out["applications"][0]["deep_verification"]["status"] == "qualified"
    assert out["applications"][1]["deep_verification"]["status"] == "error"
    assert out["parsed_filter"]["skills_all"] == ["aws"]


def test_nl_search_candidates_caps_limit(db):
    user, org = _make_user_and_org(db)
    role = Role(organization_id=org.id, name="X", source="manual")
    db.add(role)
    db.commit()
    apps = [
        _make_app(
            db,
            org_id=org.id,
            role=role,
            candidate_name=f"C{i}",
            email=f"c{i}@x.test",
            taali=float(i),
        )
        for i in range(5)
    ]
    fake = SearchOutput(
        application_ids=[a.id for a in apps],
        parsed_filter=ParsedFilter(),
        warnings=[],
        rerank_applied=False,
    )
    with patch("app.candidate_search.runner.run_search", return_value=fake):
        out = handlers.nl_search_candidates(db, user, query="any", limit=2)
    assert len(out["applications"]) == 2
    assert out["total_matched"] == 5  # raw match count is unaffected


def test_autonomous_authority_reaches_all_candidate_search_engines(db):
    user, org = _make_user_and_org(db)
    user.require_role_authority = True
    role = Role(organization_id=org.id, name="Authority", source="manual")
    db.add(role)
    db.commit()
    empty = SearchOutput(
        application_ids=[],
        parsed_filter=ParsedFilter(),
        warnings=[],
        rerank_applied=False,
    )

    with patch("app.candidate_search.runner.run_search", return_value=empty) as runner:
        handlers.nl_search_candidates(
            db,
            user,
            query="payments",
            role_id=role.id,
        )
    assert runner.call_args.kwargs["require_role_authority"] is True

    with (
        patch(
            "app.candidate_search.top_candidates.find_top_candidates",
            return_value={"candidates": []},
        ) as top_engine,
        patch(
            "app.mcp.handlers._attach_shareable_candidate_report",
            side_effect=lambda _db, _user, **kwargs: kwargs["snapshot"],
        ),
    ):
        handlers.find_top_candidates(
            db,
            user,
            query="payments",
            role_id=role.id,
        )
    assert top_engine.call_args.kwargs["require_role_authority"] is True

    with (
        patch(
            "app.candidate_search.top_candidates.screen_pool_against_requirement",
            return_value={"candidates": []},
        ) as screen_engine,
        patch(
            "app.mcp.handlers._attach_shareable_candidate_report",
            side_effect=lambda _db, _user, **kwargs: kwargs["snapshot"],
        ),
    ):
        handlers.screen_pool_against_requirement(
            db,
            user,
            requirement_text="payments",
            role_id=role.id,
        )
    assert screen_engine.call_args.kwargs["require_role_authority"] is True


def test_nl_search_candidates_supports_person_result_pagination(db):
    user, org = _make_user_and_org(db)
    role = Role(organization_id=org.id, name="X", source="manual")
    db.add(role)
    db.commit()
    apps = [
        _make_app(
            db,
            org_id=org.id,
            role=role,
            candidate_name=f"P{i}",
            email=f"p{i}@x.test",
            taali=float(i),
        )
        for i in range(4)
    ]
    fake = SearchOutput(
        application_ids=[a.id for a in apps],
        parsed_filter=ParsedFilter(skills_all=["Python"]),
        database_matches=4,
        retrieval_matches=4,
        verification_results=[
            CandidateDeepVerification(
                application_id=app.id,
                status="qualified",
                reason=f"evidence-{app.id}",
            )
            for app in apps
        ],
        retrieval=SearchRetrievalSummary(
            mode="postgres_only",
            graph_status="not_selected",
            hits=[
                SearchRetrievalTrace(
                    application_id=app.id,
                    candidate_id=int(app.candidate_id),
                    score=float(4 - index),
                    sources=["postgres"],
                    postgres_rank=index + 1,
                )
                for index, app in enumerate(apps)
            ],
        ),
    )
    with patch("app.candidate_search.runner.run_search", return_value=fake) as runner:
        out = handlers.nl_search_candidates(db, user, query="Python", limit=2, offset=2)
    assert [row["application_id"] for row in out["applications"]] == [
        apps[2].id,
        apps[3].id,
    ]
    assert out["offset"] == 2
    assert out["database_matches"] == 4
    assert [row["application_id"] for row in out["verification_results"]] == [
        apps[2].id,
        apps[3].id,
    ]
    assert [row["application_id"] for row in out["retrieval"]["hits"]] == [
        apps[2].id,
        apps[3].id,
    ]
    assert out["retrieval"]["total_hits"] == 4
    assert out["retrieval"]["returned_hits"] == 2
    assert runner.call_args.kwargs["retrieval_limit"] == 1000


def test_nl_search_candidates_rejects_empty_query(db):
    user, _org = _make_user_and_org(db)
    with pytest.raises(ValueError, match="non-empty"):
        handlers.nl_search_candidates(db, user, query="   ")


# ---------------------------------------------------------------------------
# find_top_candidates — complete active logical-pool membership
# ---------------------------------------------------------------------------


def test_find_top_candidates_searches_low_score_and_unscored_active_members(db):
    """Prior scores rank grounded matches; they never define search membership."""
    user, org = _make_user_and_org(db)
    role = Role(organization_id=org.id, name="Backend", source="manual")
    db.add(role)
    db.commit()

    strong = _make_app(
        db,
        org_id=org.id,
        role=role,
        candidate_name="Strong",
        email="strong@x.test",
        taali=80.0,
    )
    review = _make_app(
        db,
        org_id=org.id,
        role=role,
        candidate_name="Review",
        email="review@x.test",
        taali=55.0,
    )
    review.pre_screen_recommendation = "Manual review recommended"
    below = _make_app(
        db,
        org_id=org.id,
        role=role,
        candidate_name="Below",
        email="below@x.test",
        taali=20.0,
    )
    below.pre_screen_recommendation = "Below threshold"
    below_noncanonical = _make_app(
        db,
        org_id=org.id,
        role=role,
        candidate_name="BelowMessy",
        email="belowmessy@x.test",
        taali=30.0,
    )
    below_noncanonical.pre_screen_recommendation = "below threshold "
    unscored = _make_app(
        db,
        org_id=org.id,
        role=role,
        candidate_name="Unscored",
        email="unscored@x.test",
        taali=None,
    )
    db.commit()

    captured: dict = {}

    def _fake_engine(
        *, db, organization_id, role_id, query, base_query, limit, rank_by, **context
    ):
        captured["ids"] = sorted(a.id for a in base_query.all())
        captured["role_id"] = role_id
        captured["limit"] = limit
        captured["rank_by"] = rank_by
        captured["context"] = context
        return {"candidates": [], "shown": 0}

    with patch(
        "app.candidate_search.top_candidates.find_top_candidates",
        side_effect=_fake_engine,
    ):
        handlers.find_top_candidates(
            db,
            user,
            query="top 5 with salary <= 30000 AED",
            role_id=role.id,
            limit=5,
            _search_context={"titles_all": ["project manager"], "titles_any": []},
        )

    assert captured["ids"] == sorted(
        [strong.id, review.id, below.id, below_noncanonical.id, unscored.id]
    )
    assert captured["role_id"] == role.id
    assert below.id in captured["ids"]
    assert below_noncanonical.id in captured["ids"]
    assert unscored.id in captured["ids"]
    assert captured["limit"] == 5
    assert captured["rank_by"] == "taali"
    assert captured["context"]["inherited_titles_all"] == ["project manager"]
    assert captured["context"]["inherited_titles_any"] == []
    assert captured["context"]["score_expression"] is (
        CandidateApplication.taali_score_cache_100
    )
    assert captured["context"]["row_adapter"] is None
    assert captured["context"]["payload_transform"] is None
    assert captured["context"]["authoritative_pool_size"] == 5


def test_find_top_candidates_uses_sister_scope_score_stage_and_projection(db):
    """Related-role search must rank the related projection, not its ATS owner.

    This is deliberately a fully local eval: parsing is fixed, no qualitative
    criterion triggers evidence extraction, and the graph is never consulted.
    """

    user, org = _make_user_and_org(db)
    case = _seed_sister_top_candidate_world(db, org=org)
    runner_calls: list[dict] = []

    def _local_runner(**kwargs):
        runner_calls.append(kwargs)
        return SearchOutput(
            application_ids=[],
            parsed_filter=ParsedFilter(),
            warnings=[],
            rerank_applied=False,
            exhaustive=True,
            is_exact_empty=False,
        )

    with (
        patch("app.candidate_search.runner.run_search", side_effect=_local_runner),
        patch(
            "app.mcp.handlers._attach_shareable_candidate_report",
            side_effect=lambda _db, _user, **kwargs: kwargs["snapshot"],
        ),
    ):
        result = handlers.find_top_candidates(
            db,
            user,
            query="candidates",
            role_id=int(case["sister"].id),
            limit=10,
        )

    assert len(runner_calls) == 1
    assert runner_calls[0]["role_id"] == int(case["sister"].id)
    assert result["pool_size"] == 3
    assert [row["application_id"] for row in result["candidates"]] == [
        int(case["globally_advanced"].id),
        int(case["best"].id),
        int(case["second"].id),
    ]

    source_advanced, best, second = result["candidates"]
    assert [
        source_advanced["taali_score"],
        best["taali_score"],
        second["taali_score"],
    ] == [98.0, 96.0, 61.0]
    assert [
        source_advanced["pipeline_stage"],
        best["pipeline_stage"],
        second["pipeline_stage"],
    ] == [
        "applied",
        "applied",
        "review",
    ]
    assert best["role_id"] == int(case["sister"].id)
    assert best["role_name"] == case["sister"].name
    assert best["score_mode"] == "sister_role"
    assert source_advanced["action_restrictions"]["restricted"] is True
    for row in result["candidates"]:
        assert "operational_role_id" not in row
        assert "source_role_score" not in row

    returned_ids = {int(row["application_id"]) for row in result["candidates"]}
    assert int(case["sister_advanced"].id) not in returned_ids
    assert int(case["globally_advanced"].id) in returned_ids
    # The winning related candidate is explicitly below threshold on the owner
    # role. That owner-only verdict must neither exclude nor relabel it here.
    assert case["best"].pre_screen_recommendation == "Below threshold"


def test_find_top_candidates_rejects_a_foreign_role_before_search_or_report(db):
    user, _org = _make_user_and_org(db)
    other_org = Organization(name="Foreign Org", slug=f"foreign-{id(db)}")
    db.add(other_org)
    db.flush()
    foreign_role = Role(
        organization_id=other_org.id,
        name="Confidential Foreign Role",
        source="manual",
    )
    db.add(foreign_role)
    db.commit()

    with (
        patch("app.candidate_search.top_candidates.find_top_candidates") as engine,
        patch("app.domains.top_reports.service.create_report") as create_report,
        pytest.raises(ValueError, match="not found"),
    ):
        handlers.find_top_candidates(
            db,
            user,
            query="top engineers",
            role_id=foreign_role.id,
        )
    engine.assert_not_called()
    create_report.assert_not_called()


def test_find_top_candidates_mints_pii_scrubbed_shareable_report(db):
    from app.models.top_candidates_report import TopCandidatesReport

    user, org = _make_user_and_org(db)
    role = Role(organization_id=org.id, name="Backend", source="manual")
    db.add(role)
    db.commit()
    grounded = {
        "candidates": [
            {
                "candidate_name": "Ada Lovelace",
                "candidate_email": "ada@example.com",
                "candidate_phone": "+971500000000",
                "criteria": [
                    {
                        "label": "Python",
                        "status": "met",
                        "evidence": [{"source": "cv", "quote": "Built Python systems"}],
                    }
                ],
            }
        ],
        "shown": 1,
        "total_matched": 1,
    }
    with patch(
        "app.candidate_search.top_candidates.find_top_candidates",
        return_value=grounded,
    ):
        result = handlers.find_top_candidates(
            db,
            user,
            query="Python platform experience",
            limit=5,
            role_id=role.id,
        )

    report = db.query(TopCandidatesReport).one()
    assert result["report_token"] == report.token
    assert report.token.startswith("rpt_")
    assert len(report.token) > 24
    assert result["report_url"].endswith(f"/report/{report.token}")
    assert report.organization_id == org.id
    assert report.created_by_user_id == user.id
    assert report.role_id == role.id
    assert report.query == "Python platform experience"
    assert report.snapshot["role_id"] == role.id
    assert report.snapshot["role_name"] == "Backend"
    assert report.snapshot["candidates"][0]["candidate_name"] == "Ada Lovelace"
    assert (
        report.snapshot["candidates"][0]["criteria"][0]["evidence"][0]["quote"]
        == "Built Python systems"
    )
    assert "candidate_email" not in report.snapshot["candidates"][0]
    assert "candidate_phone" not in report.snapshot["candidates"][0]
    # The live in-chat result retains contact fields for authenticated users.
    assert result["candidates"][0]["candidate_email"] == "ada@example.com"
    expires_at = report.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    remaining = expires_at - datetime.now(timezone.utc)
    assert timedelta(days=29) < remaining <= timedelta(days=30)


def test_find_top_candidates_returns_search_result_if_report_persistence_fails(db):
    user, _org = _make_user_and_org(db)
    grounded = {"candidates": [], "shown": 0, "total_matched": 0}

    with (
        patch(
            "app.candidate_search.top_candidates.find_top_candidates",
            return_value=grounded,
        ),
        patch(
            "app.domains.top_reports.service.create_report",
            side_effect=RuntimeError("report store unavailable"),
        ),
        patch.object(db, "rollback", wraps=db.rollback) as rollback,
    ):
        result = handlers.find_top_candidates(db, user, query="top engineers")

    assert result == grounded
    assert "report_token" not in result
    assert "report_url" not in result
    rollback.assert_not_called()
    # The session remains usable by the rest of the chat request.
    assert db.query(Organization).filter(Organization.id == user.organization_id).one()


def test_find_top_candidates_does_not_swallow_caller_flush_failure(db):
    user, _org = _make_user_and_org(db)
    result = {"candidates": [], "shown": 0, "total_matched": 0}

    with (
        patch(
            "app.candidate_search.top_candidates.find_top_candidates",
            return_value=result,
        ),
        patch("app.domains.top_reports.service.create_report") as create_report,
        patch.object(db, "flush", side_effect=RuntimeError("chat flush failed")),
        pytest.raises(RuntimeError, match="chat flush failed"),
    ):
        handlers.find_top_candidates(db, user, query="top engineers")

    create_report.assert_not_called()


# ---------------------------------------------------------------------------
# screen_pool_against_requirement (rediscovery)
# ---------------------------------------------------------------------------


def test_screen_pool_uses_sister_scored_history_and_strips_owner_verdicts(db):
    """Rediscovery admits local evaluations and returns only local role truth."""

    user, org = _make_user_and_org(db)
    case = _seed_sister_top_candidate_world(db, org=org)
    assert case["best"].cv_match_details is None
    assert (
        db.query(CandidateApplication)
        .filter(CandidateApplication.role_id == int(case["sister"].id))
        .count()
        == 0
    )
    captured: dict[str, object] = {}

    def _local_runner(**kwargs):
        captured["ids"] = {int(app.id) for app in kwargs["base_query"].all()}
        captured["role_id"] = kwargs["role_id"]
        return SearchOutput(
            application_ids=[int(case["best"].id), int(case["second"].id)],
            parsed_filter=ParsedFilter(skills_all=["payments"]),
            warnings=[],
            database_matches=2,
            retrieval_matches=2,
            exhaustive=True,
            is_exact_empty=False,
        )

    with (
        patch(
            "app.candidate_search.runner.run_search",
            side_effect=_local_runner,
        ),
        patch(
            "app.mcp.handlers._attach_shareable_candidate_report",
            side_effect=lambda _db, _user, **kwargs: kwargs["snapshot"],
        ),
    ):
        result = handlers.screen_pool_against_requirement(
            db,
            user,
            requirement_text="payments experience",
            role_id=int(case["sister"].id),
        )

    assert captured["role_id"] == int(case["sister"].id)
    assert captured["ids"] == {
        int(case["best"].id),
        int(case["second"].id),
        int(case["sister_advanced"].id),
        int(case["globally_advanced"].id),
    }
    assert result["role_id"] == int(case["sister"].id)
    assert result["role_name"] == case["sister"].name
    assert [row["application_id"] for row in result["candidates"]] == [
        int(case["best"].id),
        int(case["second"].id),
    ]
    assert [row["taali_score"] for row in result["candidates"]] == [96.0, 61.0]
    row = result["candidates"][0]
    assert row["role_id"] == int(case["sister"].id)
    assert row["role_name"] == case["sister"].name
    assert row["pipeline_stage"] == "applied"
    assert row["taali_score"] == 96.0
    assert row["pre_screen_score"] == 96.0
    assert row["rank_score"] == 96.0
    assert row["cv_match_score"] == 96.0
    assert row["role_fit_score"] == 96.0
    assert row["assessment_score"] is None
    assert "auto_reject_state" not in row
    assert row["score_mode"] == "sister_role"
    assert row["candidate_headline"] == "Sister Best related-role evidence."
    assert row["candidate_summary"] is None
    assert row["candidate_years"] is None
    for owner_only_field in (
        "source_role_score",
        "operational_role_id",
        "operational_role_name",
        "pre_screen_recommendation",
        "pre_screen_evidence",
        "auto_reject_state",
        "auto_reject_reason",
    ):
        assert owner_only_field not in row


def test_screen_pool_handler_scopes_scored_nonhired(db):
    """Rediscovery casts over the scored HISTORY: every candidate with a stored
    CV match EXCEPT those already hired — unlike find_top it does NOT restrict
    to the open pipeline (a candidate scored for another role is fair game)."""
    user, org = _make_user_and_org(db)
    role = Role(organization_id=org.id, name="Backend", source="manual")
    db.add(role)
    db.commit()

    scored = _make_app(
        db,
        org_id=org.id,
        role=role,
        candidate_name="Scored",
        email="s@x.test",
        taali=80.0,
    )
    scored.cv_match_details = {"requirements_assessment": []}
    unscored = _make_app(
        db, org_id=org.id, role=role, candidate_name="Unscored", email="u@x.test"
    )  # cv_match_details stays None
    hired = _make_app(
        db,
        org_id=org.id,
        role=role,
        candidate_name="Hired",
        email="h@x.test",
        taali=90.0,
    )
    hired.cv_match_details = {"requirements_assessment": []}
    hired.application_outcome = "hired"
    db.commit()

    captured = {}

    def _fake_engine(*, db, organization_id, role_id, requirement, base_query, limit):
        captured["ids"] = {a.id for a in base_query.all()}
        captured["role_id"] = role_id
        return {"mode": "rediscovery", "candidates": []}

    with patch(
        "app.candidate_search.top_candidates.screen_pool_against_requirement",
        _fake_engine,
    ):
        handlers.screen_pool_against_requirement(
            db, user, requirement_text="banking", role_id=role.id
        )

    assert scored.id in captured["ids"]
    assert captured["role_id"] == role.id
    assert unscored.id not in captured["ids"]  # not scored → excluded
    assert hired.id not in captured["ids"]  # already placed → excluded


def test_screen_pool_mints_report_with_database_only_coverage(db):
    from app.models.top_candidates_report import TopCandidatesReport

    user, org = _make_user_and_org(db)
    role = Role(organization_id=org.id, name="Data", source="manual")
    db.add(role)
    db.commit()
    database_only = {
        "mode": "rediscovery",
        "candidates": [{"candidate_name": "Grace Hopper", "criteria": []}],
        "database_matches": 18,
        "deep_checked": 0,
        "qualified": None,
        "returned": 1,
        "capped": False,
        "evidence_model": None,
        "warnings": [{"code": "deep_verification_not_requested"}],
    }

    with patch(
        "app.candidate_search.top_candidates.screen_pool_against_requirement",
        return_value=database_only,
    ):
        result = handlers.screen_pool_against_requirement(
            db,
            user,
            requirement_text="banking platform experience",
            role_id=role.id,
        )

    report = db.query(TopCandidatesReport).one()
    assert result["report_url"].endswith(f"/report/{report.token}")
    assert report.organization_id == org.id
    assert report.role_id == role.id
    assert report.snapshot["database_matches"] == 18
    assert report.snapshot["deep_checked"] == 0
    assert report.snapshot["qualified"] is None
    assert report.snapshot["evidence_model"] is None
    assert report.snapshot["warnings"] == [{"code": "deep_verification_not_requested"}]


def test_screen_pool_rejects_foreign_role_before_search_or_report(db):
    user, _org = _make_user_and_org(db)
    other_org = Organization(name="Foreign Org", slug=f"foreign-screen-{id(db)}")
    db.add(other_org)
    db.flush()
    foreign_role = Role(
        organization_id=other_org.id,
        name="Confidential Foreign Role",
        source="manual",
    )
    db.add(foreign_role)
    db.commit()

    with (
        patch(
            "app.candidate_search.top_candidates.screen_pool_against_requirement"
        ) as engine,
        patch("app.domains.top_reports.service.create_report") as create_report,
        pytest.raises(ValueError, match="not found"),
    ):
        handlers.screen_pool_against_requirement(
            db,
            user,
            requirement_text="banking",
            role_id=foreign_role.id,
        )
    engine.assert_not_called()
    create_report.assert_not_called()


def test_screen_pool_handler_excludes_candidate_hired_elsewhere(db):
    """A person hired via ONE application must not resurface through a DIFFERENT,
    still-open scored application: rediscovery excludes placed *people*, not just
    the row whose own outcome is 'hired'."""
    user, org = _make_user_and_org(db)
    role_a = Role(organization_id=org.id, name="Backend", source="manual")
    role_b = Role(organization_id=org.id, name="Data", source="manual")
    db.add_all([role_a, role_b])
    db.commit()

    # ONE candidate, TWO applications: hired on role_a, scored + still-open on role_b.
    cand = Candidate(
        organization_id=org.id, email="dup@x.test", full_name="Dup", position="Engineer"
    )
    db.add(cand)
    db.flush()
    hired_app = CandidateApplication(
        organization_id=org.id,
        candidate_id=cand.id,
        role_id=role_a.id,
        status="hired",
        pipeline_stage="hired",
        pipeline_stage_source="recruiter",
        application_outcome="hired",
        source="manual",
        taali_score_cache_100=90.0,
    )
    open_app = CandidateApplication(
        organization_id=org.id,
        candidate_id=cand.id,
        role_id=role_b.id,
        status="applied",
        pipeline_stage="review",
        pipeline_stage_source="recruiter",
        application_outcome="open",
        source="manual",
        taali_score_cache_100=80.0,
    )
    db.add_all([hired_app, open_app])
    db.commit()
    open_app.cv_match_details = {
        "requirements_assessment": []
    }  # scored → eligible but for the hire
    db.commit()

    captured = {}

    def _fake_engine(*, db, organization_id, role_id, requirement, base_query, limit):
        captured["ids"] = {a.id for a in base_query.all()}
        return {"mode": "rediscovery", "candidates": []}

    with patch(
        "app.candidate_search.top_candidates.screen_pool_against_requirement",
        _fake_engine,
    ):
        handlers.screen_pool_against_requirement(db, user, requirement_text="banking")

    assert open_app.id not in captured["ids"]  # candidate already placed elsewhere
    assert hired_app.id not in captured["ids"]


def test_screen_pool_excludes_candidate_hired_in_independent_related_role(db):
    """A related-role placement is person-wide even without a physical hired app."""
    user, org = _make_user_and_org(db)
    owner = Role(organization_id=org.id, name="ATS owner", source="manual")
    related = Role(
        organization_id=org.id,
        name="Independent related hire",
        source="sister",
        role_kind=ROLE_KIND_SISTER,
        ats_owner_role=owner,
    )
    rediscovery_role = Role(
        organization_id=org.id,
        name="Rediscovery role",
        source="manual",
    )
    candidate = Candidate(
        organization_id=org.id,
        email="related-hire@example.test",
        full_name="Already Placed",
        position="Engineer",
    )
    db.add_all([owner, related, rediscovery_role, candidate])
    db.flush()
    transport = CandidateApplication(
        organization_id=org.id,
        candidate_id=candidate.id,
        role_id=owner.id,
        status="applied",
        pipeline_stage="review",
        pipeline_stage_source="recruiter",
        application_outcome="open",
        source="manual",
    )
    scored_open = CandidateApplication(
        organization_id=org.id,
        candidate_id=candidate.id,
        role_id=rediscovery_role.id,
        status="applied",
        pipeline_stage="review",
        pipeline_stage_source="recruiter",
        application_outcome="open",
        source="manual",
        cv_match_details={"requirements_assessment": []},
    )
    db.add_all([transport, scored_open])
    db.flush()
    db.add(
        SisterRoleEvaluation(
            organization_id=org.id,
            role_id=related.id,
            candidate_id=candidate.id,
            source_application_id=transport.id,
            ats_application_id=transport.id,
            status="done",
            pipeline_stage="advanced",
            application_outcome="hired",
            membership_source="initial_snapshot",
            spec_fingerprint="related-hire-spec",
        )
    )
    db.commit()
    captured: dict[str, set[int]] = {}

    def _fake_engine(**kwargs):
        captured["ids"] = {int(app.id) for app in kwargs["base_query"].all()}
        return {"mode": "rediscovery", "candidates": []}

    with patch(
        "app.candidate_search.top_candidates.screen_pool_against_requirement",
        _fake_engine,
    ):
        handlers.screen_pool_against_requirement(
            db,
            user,
            requirement_text="banking",
        )

    assert int(scored_open.id) not in captured["ids"]


def test_screen_pool_handler_rejects_empty_requirement(db):
    user, _org = _make_user_and_org(db)
    with pytest.raises(ValueError, match="non-empty"):
        handlers.screen_pool_against_requirement(db, user, requirement_text="  ")


# ---------------------------------------------------------------------------
# _graph_topology — referential-integrity guard
# ---------------------------------------------------------------------------


def _node(node_id: str, *, label: str = "Person", name: str | None = None) -> dict:
    return {
        "id": node_id,
        "label": label,
        "name": name or node_id,
        "extra": {},
    }


def _edge(
    source: str, target: str, *, label: str = "WORKED_AT", fact: str = ""
) -> dict:
    return {
        "source": source,
        "target": target,
        "label": label,
        "extra": {"fact": fact} if fact else {},
    }


def test_graph_topology_drops_edges_with_unknown_endpoints():
    # Production crash: when payload had >60 nodes, the previous slicing
    # let through edges referencing dropped nodes — cytoscape throws
    # synchronously on dangling endpoints and the React error boundary
    # caught it as "Something went wrong".
    payload = GraphPayload(
        nodes=[_node("a"), _node("b"), _node("c")],
        edges=[
            _edge("a", "b"),  # both endpoints kept → keep
            _edge("a", "ghost"),  # target not in nodes → drop
            _edge("ghost-2", "c"),  # source not in nodes → drop
        ],
    )
    out = handlers._graph_topology(payload)
    edge_pairs = {(e["source"], e["target"]) for e in out["edges"]}
    assert edge_pairs == {("a", "b")}
    # The kept node ids must cover every kept edge endpoint.
    kept_node_ids = {n["id"] for n in out["nodes"]}
    for edge in out["edges"]:
        assert edge["source"] in kept_node_ids
        assert edge["target"] in kept_node_ids


def test_graph_topology_caps_at_60_nodes_but_preserves_edge_endpoints():
    # Build 80 nodes + 100 edges. Edges reference nodes scattered across
    # the full 80, including some past index 60. The kept nodes must
    # cover every kept edge endpoint, AND the cap of 60 nodes /
    # 100 edges must hold.
    nodes = [_node(f"n-{i}") for i in range(80)]
    # Edges 0..49 reference low-index nodes; edges 50..99 reference
    # high-index nodes (which would be dropped by naive slicing).
    edges = [_edge(f"n-{i}", f"n-{(i + 1) % 50}") for i in range(50)] + [
        _edge(f"n-{60 + (i % 20)}", f"n-{60 + ((i + 1) % 20)}") for i in range(50)
    ]
    payload = GraphPayload(nodes=nodes, edges=edges)
    out = handlers._graph_topology(payload)
    assert len(out["nodes"]) <= 60
    assert len(out["edges"]) <= 100
    kept_ids = {n["id"] for n in out["nodes"]}
    for edge in out["edges"]:
        assert edge["source"] in kept_ids and edge["target"] in kept_ids, (
            f"edge {edge} references a node not in the kept set"
        )


# ---------------------------------------------------------------------------
# graph_search_candidates
# ---------------------------------------------------------------------------


def test_graph_search_preserves_hybrid_unavailable_coverage(db):
    user, _org = _make_user_and_org(db)
    shared_result = {
        "applications": [],
        "total_matched": 0,
        "database_matches": 0,
        "retrieval_matches": 0,
        "returned": 0,
        "capped": False,
        "exhaustive": False,
        "is_exact_empty": False,
        "verification_results": [],
        "retrieval": {
            "mode": "hybrid",
            "graph_status": "unavailable",
            "hits": [],
        },
        "warnings": [
            {
                "code": "graph_retrieval_unavailable",
                "message": "Graph recall is unavailable.",
            }
        ],
        "graph": None,
    }
    with patch(
        "app.mcp.handlers.nl_search_candidates", return_value=shared_result
    ) as shared_search:
        out = handlers.graph_search_candidates(db, user, query="worked at stripe")

    shared_search.assert_called_once_with(
        db,
        user,
        query="worked at stripe",
        role_id=None,
        deep_verify=False,
        include_graph=True,
        limit=25,
        offset=0,
    )
    assert out["applications"] == []
    assert out["graph_facts"] == []
    assert out["graph_facts_are_evidence"] is False
    assert out["evidence"] == []
    assert out["warnings"][0]["code"] == "graph_retrieval_unavailable"
    assert out["exhaustive"] is False
    assert out["is_exact_empty"] is False


def test_graph_search_preserves_shared_exact_empty_state(db):
    user, _org = _make_user_and_org(db)
    shared_result = {
        "applications": [],
        "total_matched": 0,
        "database_matches": 0,
        "retrieval_matches": 0,
        "returned": 0,
        "capped": False,
        "exhaustive": True,
        "is_exact_empty": True,
        "verification_results": [],
        "retrieval": {
            "mode": "postgres_only",
            "graph_status": "not_selected",
            "hits": [],
        },
        "warnings": [],
        "graph": None,
    }
    with patch("app.mcp.handlers.nl_search_candidates", return_value=shared_result):
        out = handlers.graph_search_candidates(db, user, query="unknown skill")

    assert out["is_exact_empty"] is True
    assert out["exhaustive"] is True
    assert out["graph_facts"] == []
    assert out["evidence"] == []


def test_graph_search_wraps_shared_role_scoped_result_with_topology_and_evidence(
    db,
):
    user, _org = _make_user_and_org(db)
    shared_result = {
        "applications": [{"application_id": 17, "candidate_id": 9}],
        "total_matched": 1,
        "database_matches": 0,
        "retrieval_matches": 1,
        "returned": 1,
        "capped": True,
        "exhaustive": False,
        "is_exact_empty": False,
        "verification_results": [],
        "retrieval": {
            "mode": "hybrid",
            "graph_status": "ok",
            "hits": [
                {
                    "application_id": 17,
                    "candidate_id": 9,
                    "sources": ["graph"],
                    "evidence": [
                        {
                            "source": "candidate_cv",
                            "reference": "episode:cv-9",
                            "clause_ids": ["criterion-worked-at"],
                        }
                    ],
                }
            ],
        },
        "warnings": [{"code": "graph_coverage_partial", "message": "Partial."}],
        "graph": {
            "nodes": [
                {"id": "person-9", "label": "Person", "name": "Sam", "extra": {}},
                {
                    "id": "company-1",
                    "label": "Company",
                    "name": "Stripe",
                    "extra": {},
                },
            ],
            "edges": [
                {
                    "source": "person-9",
                    "target": "company-1",
                    "label": "WORKED_AT",
                    "fact": "Sam worked at Stripe",
                }
            ],
        },
    }

    with patch(
        "app.mcp.handlers.nl_search_candidates", return_value=shared_result
    ) as shared_search:
        out = handlers.graph_search_candidates(
            db, user, query="worked at stripe", role_id=42, limit=10
        )

    shared_search.assert_called_once_with(
        db,
        user,
        query="worked at stripe",
        role_id=42,
        deep_verify=False,
        include_graph=True,
        limit=10,
        offset=0,
    )
    assert out["applications"] == [{"application_id": 17, "candidate_id": 9}]
    assert out["graph"] == shared_result["graph"]
    assert out["graph_facts"] == [
        {
            "fact": "Sam worked at Stripe",
            "source": "person-9",
            "target": "company-1",
            "label": "WORKED_AT",
            "is_citation": False,
        }
    ]
    assert out["graph_facts_are_evidence"] is False
    assert out["evidence"] == [
        {
            "application_id": 17,
            "candidate_id": 9,
            "source": "candidate_cv",
            "reference": "episode:cv-9",
            "clause_ids": ["criterion-worked-at"],
        }
    ]
    assert out["database_matches"] == 0
    assert out["retrieval_matches"] == 1
    assert out["capped"] is True
    assert out["is_exact_empty"] is False


# ---------------------------------------------------------------------------
# get_candidate_cv
# ---------------------------------------------------------------------------


def test_get_candidate_cv_returns_sections(db):
    user, org = _make_user_and_org(db)
    candidate = Candidate(
        organization_id=org.id,
        email="cara@x.test",
        full_name="Cara",
        position="Eng",
        cv_text="A long CV with many things",
        cv_filename="cara.pdf",
        cv_sections={"summary": "Senior engineer", "skills": ["aws", "python"]},
        skills=["aws", "python"],
    )
    db.add(candidate)
    db.commit()
    out = handlers.get_candidate_cv(db, user, candidate_id=candidate.id)
    assert out["candidate_id"] == candidate.id
    assert out["cv_text"].startswith("A long CV")
    assert out["cv_sections"]["skills"] == ["aws", "python"]
    assert out["cv_filename"] == "cara.pdf"


def test_get_candidate_cv_cross_org_raises(db):
    user, _org = _make_user_and_org(db)
    other_org = Organization(name="Other", slug="other2")
    db.add(other_org)
    db.flush()
    foreign = Candidate(
        organization_id=other_org.id, email="x@y.test", full_name="Hidden", position="X"
    )
    db.add(foreign)
    db.commit()
    with pytest.raises(ValueError, match="not found"):
        handlers.get_candidate_cv(db, user, candidate_id=foreign.id)
