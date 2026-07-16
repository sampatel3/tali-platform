"""Requisition chat API — create seeds opening message, chat turn (multipart),
serializer extras (custom_fields/messages/completeness/gaps), settings template
GET/PUT. The LLM is monkeypatched at the service module (no Anthropic)."""
import io

import pytest

from app.llm.structured import StructuredResult
from app.domains.assessments_runtime import requisition_routes as requisition_routes_module
from app.models.role import Role
from app.models.role_brief import RoleBrief
from app.models.user import User
from app.platform.config import settings
from app.services import requisition_chat_service as chat
from app.services.requisition_chat_service import ChatCapture, ResponsibilitiesDraft
from tests.conftest import auth_headers


LEGACY_RELATED_SPEC = """# AI Engineer

## Key responsibilities
- Build production RAG services.
- Own model reliability and observability.

## Requirements
- Python
"""


def test_create_requisition_seeds_opening_message_and_serializer_extras(client):
    headers, _ = auth_headers(client)
    resp = client.post("/api/v1/requisitions", json={}, headers=headers)
    assert resp.status_code == 201, resp.text
    body = resp.json()
    # Serializer extras present.
    assert body["custom_fields"] == {}
    assert body["completeness"] == 0
    assert isinstance(body["gaps"], list) and body["gaps"][0]["key"] == "title"
    # Opening assistant message seeded — a free-text brief request, no chips.
    assert len(body["messages"]) == 1
    assert body["messages"][0]["role"] == "assistant"
    assert "in your own words" in body["messages"][0]["content"]
    assert body["messages"][0]["suggested_replies"] == []


def test_chat_endpoint_multipart_applies_and_returns_contract(client, monkeypatch):
    headers, _ = auth_headers(client)
    brief_id = client.post("/api/v1/requisitions", json={}, headers=headers).json()["id"]

    # The route builds a real metered client before generate_structured runs;
    # give it a dummy key so construction succeeds (the call itself is patched).
    monkeypatch.setattr(settings, "ANTHROPIC_API_KEY", "test-key", raising=False)

    def fake_generate_structured(c, **kwargs):
        value = ChatCapture(
            assistant_reply="Onsite or remote? And how many openings?",
            open_questions=["workplace_type?"],
            title="Backend Engineer",
            must_haves=["Python"],
        )
        return StructuredResult(value=value, ok=True)

    monkeypatch.setattr(chat, "generate_structured", fake_generate_structured)

    resp = client.post(
        f"/api/v1/requisitions/{brief_id}/chat",
        data={"message": "We need a backend engineer who knows Python."},
        files=[("files", ("notes.txt", io.BytesIO(b"small team, fast"), "text/plain"))],
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # Top-level contract: brief / reply / messages / gaps / suggested_replies.
    assert set(body.keys()) == {"brief", "reply", "messages", "gaps", "suggested_replies"}
    assert body["reply"].startswith("I've amended the draft.")
    assert "Updated: Must-haves, Title" in body["reply"]
    assert "What domain or industry" in body["reply"]
    # The model gave no suggested_replies → deterministic fallback to the next
    # gap's options. With title captured, the next gap is `domain` (a free-text
    # field), so there are no tappable options — the manager types it.
    assert body["suggested_replies"] == []
    # Brief reflects the capture.
    assert body["brief"]["title"] == "Backend Engineer"
    assert body["brief"]["must_haves"] == ["Python"]
    # Transcript: opening + user + assistant.
    assert [m["role"] for m in body["messages"]] == ["assistant", "user", "assistant"]
    assert body["messages"][1]["attachments"] == [{"name": "notes.txt", "kind": "transcript"}]
    # gaps shrank (title gone) but workplace_type/openings remain.
    gap_keys = [g["key"] for g in body["gaps"]]
    assert "title" not in gap_keys and "workplace_type" in gap_keys


def test_existing_related_draft_is_read_only_hydrated_then_persisted_by_chat(
    client, db, monkeypatch
):
    headers, email = auth_headers(client)
    user = db.query(User).filter(User.email == email).one()
    source = Role(
        organization_id=user.organization_id,
        name="AI Engineer",
        source="workable",
        workable_job_id="legacy-related-source",
        job_spec_text=LEGACY_RELATED_SPEC,
    )
    db.add(source)
    db.flush()
    legacy = RoleBrief(
        organization_id=user.organization_id,
        created_by_user_id=user.id,
        source_role_id=source.id,
        title="AI Engineer · Related",
        agent_state={"jd_override": LEGACY_RELATED_SPEC},
        custom_fields={},
    )
    db.add(legacy)
    db.commit()
    brief_id = legacy.id

    # GET stays read-only but its compatibility view immediately removes the
    # false responsibilities blocker for drafts created before the fix.
    viewed = client.get(
        f"/api/v1/requisitions/{brief_id}", headers=headers
    )
    assert viewed.status_code == 200, viewed.text
    assert viewed.json()["custom_fields"]["responsibilities"] == [
        "Build production RAG services.",
        "Own model reliability and observability.",
    ]
    db.expire_all()
    stored = db.get(RoleBrief, brief_id)
    assert stored.raw_input is None
    assert not (stored.custom_fields or {}).get("responsibilities")

    monkeypatch.setattr(settings, "ANTHROPIC_API_KEY", "test-key", raising=False)
    seen = {}

    def fake_generate_structured(c, **kwargs):
        seen["system"] = kwargs["system"]
        return StructuredResult(
            value=ChatCapture(
                assistant_reply="What else should change?",
                responsibilities=[
                    "Build production RAG services.",
                    "Own model reliability and observability.",
                ],
            ),
            ok=True,
        )

    monkeypatch.setattr(chat, "generate_structured", fake_generate_structured)
    chatted = client.post(
        f"/api/v1/requisitions/{brief_id}/chat",
        data={"message": "Use the attached job spec first"},
        headers=headers,
    )
    assert chatted.status_code == 200, chatted.text
    assert "EXTRACT EXHAUSTIVELY" in seen["system"]
    assert LEGACY_RELATED_SPEC.strip() in seen["system"]
    db.expire_all()
    stored = db.get(RoleBrief, brief_id)
    assert stored.raw_input == LEGACY_RELATED_SPEC.strip()
    assert stored.custom_fields["responsibilities"] == [
        "Build production RAG services.",
        "Own model reliability and observability.",
    ]
    # The deterministic legacy backfill alone must not mark the full saved JD
    # as extracted; a later turn should still ask the model to hydrate its gaps.
    assert "recruiter_source_hydration_digest" not in (stored.agent_state or {})


def test_chat_endpoint_requires_message_or_file(client):
    headers, _ = auth_headers(client)
    brief_id = client.post("/api/v1/requisitions", json={}, headers=headers).json()["id"]
    resp = client.post(
        f"/api/v1/requisitions/{brief_id}/chat", data={"message": "   "}, headers=headers
    )
    assert resp.status_code == 422


@pytest.mark.parametrize(
    ("filename", "content_type", "content"),
    [
        ("renamed.png", "image/heic", b"not really a png"),
        ("forged.png", "image/png", b"not really a png"),
    ],
)
def test_chat_endpoint_rejects_invalid_attachment_before_provider_call(
    client, monkeypatch, filename, content_type, content
):
    headers, _ = auth_headers(client)
    brief_id = client.post("/api/v1/requisitions", json={}, headers=headers).json()["id"]

    def _boom(*args, **kwargs):  # pragma: no cover - rejection must win
        raise AssertionError("provider-backed chat must not run for a rejected upload")

    monkeypatch.setattr(requisition_routes_module, "run_chat_turn", _boom)
    resp = client.post(
        f"/api/v1/requisitions/{brief_id}/chat",
        files=[
            (
                "files",
                (filename, io.BytesIO(content), content_type),
            )
        ],
        headers=headers,
    )

    assert resp.status_code == 415, resp.text
    assert filename in resp.json()["detail"]


def test_chat_replace_or_amend_clarification_keeps_pending_spec_internal(
    client, db, monkeypatch
):
    headers, _ = auth_headers(client)
    brief_id = client.post("/api/v1/requisitions", json={}, headers=headers).json()["id"]
    client.patch(
        f"/api/v1/requisitions/{brief_id}",
        json={"title": "Current role", "jd_override": "CURRENT JD"},
        headers=headers,
    )
    monkeypatch.setattr(settings, "ANTHROPIC_API_KEY", "test-key", raising=False)
    monkeypatch.setattr(
        chat,
        "generate_structured",
        lambda *args, **kwargs: StructuredResult(
            value=ChatCapture(
                assistant_reply="Replace or amend?",
                change_mode="clarify",
                title="Must not apply",
            ),
            ok=True,
        ),
    )

    resp = client.post(
        f"/api/v1/requisitions/{brief_id}/chat",
        data={"message": "Here is a different specification"},
        files=[
            (
                "files",
                ("proposal.txt", io.BytesIO(b"# Different role\n\nMust have Go."), "text/plain"),
            )
        ],
        headers=headers,
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["brief"]["title"] == "Current role"
    assert body["suggested_replies"] == [
        "Replace current draft",
        "Apply differences only",
    ]
    assert "pending_job_spec_source" not in body["brief"]["agent_state"]
    db.expire_all()
    stored = db.get(RoleBrief, brief_id)
    assert "Must have Go" in stored.agent_state["pending_job_spec_source"]


# --------------------------------------------------------------------------- #
# Deterministic single-answer endpoint (NO LLM, NO metering)
# --------------------------------------------------------------------------- #
def test_answer_endpoint_records_select_column_field_no_llm(client):
    """Answering a SELECT *column* field (workplace_type) sets the column, drops
    it from gaps, returns the next gap's question + options, and makes NO LLM
    call. We deliberately do NOT monkeypatch generate_structured nor set an API
    key — a real LLM call would fail, so a 200 proves the path is LLM-free."""
    headers, _ = auth_headers(client)
    created = client.post("/api/v1/requisitions", json={}, headers=headers).json()
    brief_id = created["id"]
    before_completeness = created["completeness"]

    resp = client.post(
        f"/api/v1/requisitions/{brief_id}/answer",
        json={"field_key": "workplace_type", "value": "Remote"},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # Same top-level contract as /chat.
    assert set(body.keys()) == {"brief", "reply", "messages", "gaps", "suggested_replies"}
    # The column was set.
    assert body["brief"]["workplace_type"] == "Remote"
    # workplace_type dropped from gaps.
    gap_keys = [g["key"] for g in body["gaps"]]
    assert "workplace_type" not in gap_keys
    # Reply acknowledges the field + asks the next gap (title is still first).
    assert body["reply"].startswith("Got it — Workplace type: Remote.")
    assert "What role are you hiring for?" in body["reply"]
    # Transcript: opening + user answer + assistant ack.
    assert [m["role"] for m in body["messages"]] == ["assistant", "user", "assistant"]
    assert body["messages"][1]["content"] == "Remote"
    assert body["messages"][1]["attachments"] == []
    # Completeness rose now that a required field is filled.
    assert body["brief"]["completeness"] > before_completeness


def test_answer_endpoint_select_field_offers_next_select_options(client):
    """When the next gap is itself a select (employment_type), the answer reply
    surfaces that gap's options as tappable quick replies."""
    headers, _ = auth_headers(client)
    brief_id = client.post("/api/v1/requisitions", json={}, headers=headers).json()["id"]
    # Fill every required gap that precedes employment_type (title, domain,
    # seniority, summary, workplace_type) so employment_type — a select — becomes
    # the first remaining required gap. domain is a custom field → use /answer.
    for field_key, value in [
        ("title", "Engineer"),
        ("domain", "Banking"),
        ("seniority", "Mid"),
        ("summary", "Builds APIs"),
        ("workplace_type", "Remote"),
    ]:
        client.post(
            f"/api/v1/requisitions/{brief_id}/answer",
            json={"field_key": field_key, "value": value},
            headers=headers,
        )
    resp = client.post(
        f"/api/v1/requisitions/{brief_id}/answer",
        json={"field_key": "openings", "value": 2},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # openings is the answered number field; the next required gap is
    # employment_type (a select) OR urgency — either way options are surfaced.
    gap_keys = [g["key"] for g in body["gaps"]]
    next_key = gap_keys[0]
    assert next_key in ("employment_type", "urgency")
    # Its select options are the tappable quick replies.
    assert body["suggested_replies"] == (
        ["Full-time", "Part-time", "Contract", "Temporary"]
        if next_key == "employment_type"
        else ["Low", "Normal", "High", "Urgent"]
    )


def test_answer_endpoint_custom_field_lands_in_custom_fields(client):
    """Answering a CUSTOM field (urgency has no RoleBrief column) lands the value
    in custom_fields, not a column."""
    headers, _ = auth_headers(client)
    brief_id = client.post("/api/v1/requisitions", json={}, headers=headers).json()["id"]
    resp = client.post(
        f"/api/v1/requisitions/{brief_id}/answer",
        json={"field_key": "urgency", "value": "Urgent"},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["brief"]["custom_fields"]["urgency"] == "Urgent"
    # urgency is required → it dropped out of the gaps.
    assert "urgency" not in [g["key"] for g in body["gaps"]]


def test_answer_endpoint_coerces_list_field_and_joins_readable(client):
    """A list field (must_haves) coerces a list value and renders it joined in
    the transcript + reply."""
    headers, _ = auth_headers(client)
    brief_id = client.post("/api/v1/requisitions", json={}, headers=headers).json()["id"]
    resp = client.post(
        f"/api/v1/requisitions/{brief_id}/answer",
        json={"field_key": "must_haves", "value": ["Python", "AWS"]},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["brief"]["must_haves"] == ["Python", "AWS"]
    assert body["messages"][1]["content"] == "Python, AWS"
    assert body["reply"].startswith("Got it — Must-haves: Python, AWS.")


def test_answer_endpoint_unknown_field_key_422(client):
    headers, _ = auth_headers(client)
    brief_id = client.post("/api/v1/requisitions", json={}, headers=headers).json()["id"]
    resp = client.post(
        f"/api/v1/requisitions/{brief_id}/answer",
        json={"field_key": "not_a_field", "value": "x"},
        headers=headers,
    )
    assert resp.status_code == 422
    # The brief was not mutated (no stray user message persisted).
    after = client.get(f"/api/v1/requisitions/{brief_id}", headers=headers).json()
    assert [m["role"] for m in after["messages"]] == ["assistant"]


def test_answer_endpoint_completeness_reaches_100_then_review_nudge(client):
    """Answering every required field drives completeness to 100 and the reply
    becomes a review-ready nudge with no options."""
    headers, _ = auth_headers(client)
    brief_id = client.post("/api/v1/requisitions", json={}, headers=headers).json()["id"]
    # Required fields in the default template: title, domain, seniority, summary,
    # workplace_type, employment_type, openings, urgency, salary_min, salary_max,
    # salary_currency, must_haves, success_profile, responsibilities.
    # salary_currency is seeded to AED on create.
    answers = [
        ("title", "Backend Engineer"),
        ("domain", "Banking"),
        ("seniority", "Senior"),
        ("summary", "Builds and ships backend services"),
        ("workplace_type", "Remote"),
        ("employment_type", "Full-time"),
        ("openings", 2),
        ("urgency", "Urgent"),
        ("salary_min", 100),
        ("salary_max", 200),
        ("must_haves", ["Python"]),
        ("success_profile", "Ships reliable services in 6 months"),
        ("responsibilities", ["Build APIs", "Own reliability"]),
    ]
    body = None
    for field_key, value in answers:
        body = client.post(
            f"/api/v1/requisitions/{brief_id}/answer",
            json={"field_key": field_key, "value": value},
            headers=headers,
        ).json()
    assert body["gaps"] == []
    assert body["brief"]["completeness"] == 100
    assert body["reply"].endswith(
        "That's everything I need — the brief is ready for review."
    )
    assert body["suggested_replies"] == []


# --------------------------------------------------------------------------- #
# AI-draft responsibilities ("What you'll do")
# --------------------------------------------------------------------------- #
def test_draft_responsibilities_lands_in_custom_fields_and_threads_metering(
    client, monkeypatch
):
    headers, _ = auth_headers(client)
    brief_id = client.post("/api/v1/requisitions", json={}, headers=headers).json()["id"]
    # Capture a bit of spec so the draft has something to ground on.
    client.patch(
        f"/api/v1/requisitions/{brief_id}",
        json={
            "title": "Backend Engineer",
            "seniority": "Senior",
            "must_haves": ["Python"],
            "jd_override": "# Backend Engineer\n\nOld responsibilities.",
        },
        headers=headers,
    )

    # The route builds a real metered client before generate_structured runs;
    # give it a dummy key so construction succeeds (the call itself is patched).
    monkeypatch.setattr(settings, "ANTHROPIC_API_KEY", "test-key", raising=False)

    drafted = [
        "Design and ship backend services",
        "Own API contracts end to end",
        "Mentor mid-level engineers",
    ]
    captured: dict = {}

    def fake_generate_structured(c, **kwargs):
        # Forced tool-use, fast chat model, and the metering feature threaded.
        captured["use_tool_use"] = kwargs.get("use_tool_use")
        captured["model"] = kwargs.get("model")
        captured["feature"] = kwargs["metering"].feature
        captured["entity_id"] = kwargs["metering"].entity_id
        captured["output_model"] = kwargs.get("output_model")
        return StructuredResult(
            value=ResponsibilitiesDraft(responsibilities=drafted), ok=True
        )

    monkeypatch.setattr(chat, "generate_structured", fake_generate_structured)

    resp = client.post(
        f"/api/v1/requisitions/{brief_id}/draft-responsibilities", headers=headers
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # The drafted list landed in custom_fields.responsibilities...
    assert body["custom_fields"]["responsibilities"] == drafted
    assert "responsibilities" not in [gap["key"] for gap in body["gaps"]]
    # ...and the serialized brief is the full requisition shape (gaps/messages).
    assert "gaps" in body and "messages" in body and body["title"] == "Backend Engineer"
    assert body["jd_override"] is None
    assert body["agent_state"]["canonical_spec_mode"] == "structured"
    assert body["agent_state"]["job_spec_last_change_mode"] == "draft_responsibilities"

    # Metering + call shape threaded correctly.
    assert captured["use_tool_use"] is True
    assert captured["feature"] == "requisition_intake_chat"
    assert captured["entity_id"] == f"role_brief:{brief_id}"
    assert captured["output_model"] is ResponsibilitiesDraft
    # The FAST chat model (Haiku) is used, not the resolved/Sonnet model.
    assert captured["model"] == settings.CLAUDE_CHAT_MODEL


def test_draft_responsibilities_merges_with_existing_custom_fields(client, monkeypatch):
    headers, _ = auth_headers(client)
    brief_id = client.post("/api/v1/requisitions", json={}, headers=headers).json()["id"]
    # Seed an unrelated custom field — the draft must not clobber it.
    client.patch(
        f"/api/v1/requisitions/{brief_id}",
        json={"title": "Eng", "custom_fields": {"visa_sponsorship": "Yes"}},
        headers=headers,
    )
    monkeypatch.setattr(settings, "ANTHROPIC_API_KEY", "test-key", raising=False)

    def fake_generate_structured(c, **kwargs):
        return StructuredResult(
            value=ResponsibilitiesDraft(responsibilities=["Build things", "Ship code"]),
            ok=True,
        )

    monkeypatch.setattr(chat, "generate_structured", fake_generate_structured)

    body = client.post(
        f"/api/v1/requisitions/{brief_id}/draft-responsibilities", headers=headers
    ).json()
    assert body["custom_fields"]["visa_sponsorship"] == "Yes"
    assert body["custom_fields"]["responsibilities"] == ["Build things", "Ship code"]


def test_draft_responsibilities_502_on_llm_failure_leaves_brief_untouched(
    client, monkeypatch
):
    headers, _ = auth_headers(client)
    brief_id = client.post("/api/v1/requisitions", json={}, headers=headers).json()["id"]
    client.patch(
        f"/api/v1/requisitions/{brief_id}", json={"title": "Eng"}, headers=headers
    )
    monkeypatch.setattr(settings, "ANTHROPIC_API_KEY", "test-key", raising=False)

    def fake_generate_structured(c, **kwargs):
        return StructuredResult(value=None, ok=False, error_reason="claude_call_failed: boom")

    monkeypatch.setattr(chat, "generate_structured", fake_generate_structured)

    resp = client.post(
        f"/api/v1/requisitions/{brief_id}/draft-responsibilities", headers=headers
    )
    assert resp.status_code == 502, resp.text
    # The brief was not mutated — no responsibilities written.
    after = client.get(f"/api/v1/requisitions/{brief_id}", headers=headers).json()
    assert "responsibilities" not in (after["custom_fields"] or {})


def test_get_requisition_template_returns_default(client):
    headers, _ = auth_headers(client)
    resp = client.get("/api/v1/settings/requisition-template", headers=headers)
    assert resp.status_code == 200, resp.text
    template = resp.json()["template"]
    assert template["sections"][0]["key"] == "role_basics"


def test_put_requisition_template_validates_and_saves(client):
    headers, _ = auth_headers(client)
    good = {
        "version": 1,
        "jd_template": "# {{title}}\n\nCustom JD boilerplate.",
        "sections": [
            {
                "key": "basics",
                "label": "Basics",
                "fields": [
                    {"key": "title", "label": "Title", "type": "text", "required": True},
                    {"key": "region", "label": "Region", "type": "select", "required": False, "options": ["EMEA", "APAC"]},
                ],
            }
        ],
    }
    resp = client.put(
        "/api/v1/settings/requisition-template", json={"template": good}, headers=headers
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["template"] == good
    # Now GET returns the saved override.
    got = client.get("/api/v1/settings/requisition-template", headers=headers).json()["template"]
    assert got == good


def test_put_requisition_template_rejects_bad_shape(client):
    headers, _ = auth_headers(client)
    bad = {"sections": [{"key": "s", "label": "S", "fields": [{"key": "a", "label": "A", "type": "nope"}]}]}
    resp = client.put(
        "/api/v1/settings/requisition-template", json={"template": bad}, headers=headers
    )
    assert resp.status_code == 422


def test_patch_requisition_accepts_custom_fields(client):
    headers, _ = auth_headers(client)
    brief_id = client.post("/api/v1/requisitions", json={}, headers=headers).json()["id"]
    resp = client.patch(
        f"/api/v1/requisitions/{brief_id}",
        json={"title": "Eng", "custom_fields": {"visa_sponsorship": "Yes"}},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["title"] == "Eng"
    assert resp.json()["custom_fields"] == {"visa_sponsorship": "Yes"}


def test_patch_requisition_recomputes_live_completeness(client):
    headers, _ = auth_headers(client)
    brief_id = client.post("/api/v1/requisitions", json={}, headers=headers).json()["id"]

    body = client.patch(
        f"/api/v1/requisitions/{brief_id}",
        json={
            "title": "AI Engineer",
            "custom_fields": {"domain": "Banking", "urgency": "High"},
        },
        headers=headers,
    ).json()

    # Three of eleven default required fields are now filled.
    assert body["completeness"] == round(100 * 3 / 11)
    gap_keys = [gap["key"] for gap in body["gaps"]]
    assert "title" not in gap_keys
    assert "domain" not in gap_keys
    assert "urgency" not in gap_keys
    # GET derives the same percentage from the same live snapshot.
    got = client.get(f"/api/v1/requisitions/{brief_id}", headers=headers).json()
    assert got["completeness"] == body["completeness"]


# --------------------------------------------------------------------------- #
# Warm-start: a 2nd requisition prefills from the org's recent specs
# --------------------------------------------------------------------------- #
def test_create_requisition_warm_starts_from_recent_spec(client):
    headers, _ = auth_headers(client)
    # First requisition: set location + workplace via PATCH.
    first_id = client.post("/api/v1/requisitions", json={}, headers=headers).json()["id"]
    client.patch(
        f"/api/v1/requisitions/{first_id}",
        json={
            "location_city": "Dubai",
            "location_country": "UAE",
            "workplace_type": "Hybrid",
            "department": "Engineering",
            "employment_type": "Full-time",
        },
        headers=headers,
    )
    # Second requisition: should be pre-filled from the first.
    body = client.post("/api/v1/requisitions", json={}, headers=headers).json()
    assert body["location_city"] == "Dubai"
    assert body["location_country"] == "UAE"
    assert body["workplace_type"] == "Hybrid"
    assert body["department"] == "Engineering"
    assert body["employment_type"] == "Full-time"
    # Salary currency still seeded to AED (unaffected by warm-start).
    assert body["salary_currency"] == "AED"
    # The prefilled required fields count toward the live gap engine — they're
    # no longer listed as gaps, and derived completeness reflects those warm
    # values immediately.
    gap_keys = [g["key"] for g in body["gaps"]]
    assert "workplace_type" not in gap_keys and "employment_type" not in gap_keys
    assert body["completeness"] == round(100 * 2 / 11)


def test_create_first_requisition_has_no_warm_start(client):
    headers, _ = auth_headers(client)
    # The very first requisition for a fresh org inherits nothing.
    body = client.post("/api/v1/requisitions", json={}, headers=headers).json()
    assert body["location_city"] is None
    assert body["workplace_type"] is None
    assert body["completeness"] == 0


# --------------------------------------------------------------------------- #
# JD override: PATCH stores it in agent_state; serializer returns it
# --------------------------------------------------------------------------- #
def test_jd_override_round_trips_and_clears_preserving_agent_state(client):
    headers, _ = auth_headers(client)
    brief_id = client.post("/api/v1/requisitions", json={}, headers=headers).json()["id"]

    # New brief has no override.
    assert client.get(f"/api/v1/requisitions/{brief_id}", headers=headers).json()["jd_override"] is None

    # Seed another agent_state key so we can prove jd_override merges, not clobbers.
    client.patch(
        f"/api/v1/requisitions/{brief_id}",
        json={"agent_state": {"open_questions": ["salary range?"]}},
        headers=headers,
    )

    # PATCH sets the override.
    resp = client.patch(
        f"/api/v1/requisitions/{brief_id}",
        json={"jd_override": "# Senior Engineer\n\nHand-edited JD body."},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["jd_override"] == "# Senior Engineer\n\nHand-edited JD body."
    # The other agent_state key is preserved.
    assert resp.json()["agent_state"]["open_questions"] == ["salary range?"]
    # ``jd_override`` is stored inside agent_state, not as a stray column.
    assert resp.json()["agent_state"]["jd_override"] == "# Senior Engineer\n\nHand-edited JD body."
    assert resp.json()["agent_state"]["canonical_spec_mode"] == "verbatim"
    assert resp.json()["agent_state"]["job_spec_revision"] == 1
    assert resp.json()["agent_state"]["job_spec_last_change_mode"] == "manual"

    # Serializer returns it on GET too.
    assert client.get(
        f"/api/v1/requisitions/{brief_id}", headers=headers
    ).json()["jd_override"] == "# Senior Engineer\n\nHand-edited JD body."

    # Clearing with an empty string removes it but leaves open_questions intact.
    cleared = client.patch(
        f"/api/v1/requisitions/{brief_id}", json={"jd_override": ""}, headers=headers
    ).json()
    assert cleared["jd_override"] is None
    assert "jd_override" not in cleared["agent_state"]
    assert cleared["agent_state"]["open_questions"] == ["salary range?"]
    assert cleared["agent_state"]["canonical_spec_mode"] == "structured"
    assert cleared["agent_state"]["job_spec_revision"] == 2


def test_jd_override_alongside_other_field_edits(client):
    headers, _ = auth_headers(client)
    brief_id = client.post("/api/v1/requisitions", json={}, headers=headers).json()["id"]
    # A PATCH that sets jd_override AND a regular column in one go.
    resp = client.patch(
        f"/api/v1/requisitions/{brief_id}",
        json={"title": "Eng", "jd_override": "Custom JD"},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["title"] == "Eng"  # column edit still applied
    assert resp.json()["jd_override"] == "Custom JD"

    # A later Brief-only edit must not leave the old verbatim JD active.
    revised = client.patch(
        f"/api/v1/requisitions/{brief_id}",
        json={"title": "Senior Eng"},
        headers=headers,
    )
    assert revised.status_code == 200, revised.text
    assert revised.json()["title"] == "Senior Eng"
    assert revised.json()["jd_override"] is None
    assert revised.json()["agent_state"]["canonical_spec_mode"] == "structured"
    assert revised.json()["agent_state"]["job_spec_last_change_mode"] == "manual_brief"


def test_company_blurb_settings_get_put_generate_and_serializer_fallback(client):
    """The org's About-the-company blurb is editable in Settings, generates
    gracefully when there's nothing to derive, and falls back onto every
    requisition's serialized brief (so the JD's About-us section fills)."""
    headers, _ = auth_headers(client)
    base = "/api/v1/settings/requisition-template"

    # Default: no blurb yet.
    assert client.get(base, headers=headers).json()["company_blurb"] == ""

    # Generate with no role specs in the org → graceful empty (no crash, no LLM).
    gen = client.post(f"{base}/company-blurb/generate", headers=headers)
    assert gen.status_code == 200, gen.text
    assert gen.json()["company_blurb"] == ""

    # Recruiter sets it by hand.
    put = client.put(
        f"{base}/company-blurb",
        json={"company_blurb": "DeepLight builds agentic hiring software."},
        headers=headers,
    )
    assert put.status_code == 200, put.text
    assert put.json()["company_blurb"] == "DeepLight builds agentic hiring software."
    assert client.get(base, headers=headers).json()["company_blurb"] == "DeepLight builds agentic hiring software."

    # Every requisition's serialized brief now carries it (render-time fallback),
    # so the {{company_description}} JD placeholder fills even on fresh requisitions.
    bid = client.post("/api/v1/requisitions", json={}, headers=headers).json()["id"]
    brief = client.get(f"/api/v1/requisitions/{bid}", headers=headers).json()
    assert brief["custom_fields"]["company_description"] == "DeepLight builds agentic hiring software."
