"""Requisition intake agent — extraction + apply (no real LLM)."""
from app.llm.structured import StructuredResult
from app.models import Organization
from app.services import requisition_intake_agent as agent
from app.services.requisition_intake_agent import (
    BriefExtraction,
    CalibrationExemplar,
    HiringProcess,
    SourcingSignals,
    WeightedPriority,
    apply_extraction,
    build_intake_messages,
    run_intake_extraction,
)
from app.services.role_brief_service import create_brief, update_brief_fields


def _brief(db):
    org = Organization(name="Acme", slug="acme")
    db.add(org)
    db.flush()
    return create_brief(db, organization_id=org.id)


def test_build_intake_messages_embeds_current_and_new(db):
    b = _brief(db)
    update_brief_fields(db, b, title="Eng")
    system, messages = build_intake_messages(b, "We need a backend engineer in Dubai.")
    assert "intake" in system.lower()
    assert messages[0]["role"] == "user"
    assert "backend engineer in Dubai" in messages[0]["content"]
    assert "Eng" in messages[0]["content"]  # current brief folded in


def test_apply_extraction_maps_all_layers(db):
    b = _brief(db)
    ext = BriefExtraction(
        title="Backend Engineer",
        must_haves=["Python", "Postgres"],
        dealbreakers=["Must be onsite"],
        success_profile="Ships features fast",
        priorities=[WeightedPriority(factor="domain", weight="high")],
        calibration_exemplars=[CalibrationExemplar(kind="good", description="Jane")],
        sourcing_signals=SourcingSignals(companies=["Acme"], titles=["SWE"]),
        process=HiringProcess(rounds=3, urgency="high"),
        open_questions=["What's the salary band?"],
        completeness=65,
    )
    apply_extraction(db, b, ext)
    assert b.title == "Backend Engineer"
    assert b.must_haves == ["Python", "Postgres"]
    assert b.priorities[0]["factor"] == "domain"
    assert b.calibration_exemplars[0]["kind"] == "good"
    assert b.sourcing_signals["companies"] == ["Acme"]
    assert b.process["rounds"] == 3
    assert b.agent_state["open_questions"] == ["What's the salary band?"]
    assert b.completeness == 65


def test_run_intake_extraction_applies_result(db, monkeypatch):
    b = _brief(db)
    canned = BriefExtraction(
        title="Data Scientist", must_haves=["ML"], completeness=40,
        open_questions=["seniority?"],
    )
    monkeypatch.setattr(
        agent, "generate_structured", lambda *a, **k: StructuredResult(value=canned, ok=True)
    )
    result = run_intake_extraction(
        db, b, "We want a data scientist.", source_kind="transcript",
        client=object(), model="test-model",
    )
    assert result.ok
    assert b.title == "Data Scientist" and b.must_haves == ["ML"]
    assert b.source_kind == "transcript"
    assert b.raw_input == "We want a data scientist."
    assert b.agent_state["open_questions"] == ["seniority?"]
    assert b.completeness == 40


def test_run_intake_extraction_failure_applies_nothing(db, monkeypatch):
    b = _brief(db)
    monkeypatch.setattr(
        agent, "generate_structured",
        lambda *a, **k: StructuredResult(value=None, ok=False, error_reason="boom"),
    )
    result = run_intake_extraction(db, b, "x", client=object(), model="test-model")
    assert not result.ok
    assert b.title is None
