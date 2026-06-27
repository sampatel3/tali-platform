"""Requisition chat API — create seeds opening message, chat turn (multipart),
serializer extras (custom_fields/messages/completeness/gaps), settings template
GET/PUT. The LLM is monkeypatched at the service module (no Anthropic)."""
import io

from app.llm.structured import StructuredResult
from app.platform.config import settings
from app.services import requisition_chat_service as chat
from app.services.requisition_chat_service import ChatCapture, ResponsibilitiesDraft
from tests.conftest import auth_headers


def test_create_requisition_seeds_opening_message_and_serializer_extras(client):
    headers, _ = auth_headers(client)
    resp = client.post("/api/v1/requisitions", json={}, headers=headers)
    assert resp.status_code == 201, resp.text
    body = resp.json()
    # Serializer extras present.
    assert body["custom_fields"] == {}
    assert body["completeness"] == 0
    assert isinstance(body["gaps"], list) and body["gaps"][0]["key"] == "title"
    # Opening assistant message seeded.
    assert len(body["messages"]) == 1
    assert body["messages"][0]["role"] == "assistant"
    assert "what role are you hiring for?" in body["messages"][0]["content"]


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
    assert body["reply"].startswith("Onsite or remote")
    # The model gave no suggested_replies → deterministic fallback to the next
    # select gap's options (workplace_type), surfaced as tappable quick replies.
    assert body["suggested_replies"] == ["Onsite", "Hybrid", "Remote"]
    # Brief reflects the capture.
    assert body["brief"]["title"] == "Backend Engineer"
    assert body["brief"]["must_haves"] == ["Python"]
    # Transcript: opening + user + assistant.
    assert [m["role"] for m in body["messages"]] == ["assistant", "user", "assistant"]
    assert body["messages"][1]["attachments"] == [{"name": "notes.txt", "kind": "transcript"}]
    # gaps shrank (title gone) but workplace_type/openings remain.
    gap_keys = [g["key"] for g in body["gaps"]]
    assert "title" not in gap_keys and "workplace_type" in gap_keys


def test_chat_endpoint_requires_message_or_file(client):
    headers, _ = auth_headers(client)
    brief_id = client.post("/api/v1/requisitions", json={}, headers=headers).json()["id"]
    resp = client.post(
        f"/api/v1/requisitions/{brief_id}/chat", data={"message": "   "}, headers=headers
    )
    assert resp.status_code == 422


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
    # Fill the earlier required gaps (title, workplace_type) so employment_type
    # becomes the first remaining required gap.
    client.patch(
        f"/api/v1/requisitions/{brief_id}",
        json={"title": "Engineer", "workplace_type": "Remote"},
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


def test_answer_endpoint_completeness_reaches_100_then_publish_nudge(client):
    """Answering every required field drives completeness to 100 and the reply
    becomes the publish nudge with no options."""
    headers, _ = auth_headers(client)
    brief_id = client.post("/api/v1/requisitions", json={}, headers=headers).json()["id"]
    # Required fields in the default template: title, workplace_type,
    # employment_type, openings, urgency, salary_min, salary_max, salary_currency,
    # must_haves. salary_currency is seeded to AED on create.
    answers = [
        ("title", "Backend Engineer"),
        ("workplace_type", "Remote"),
        ("employment_type", "Full-time"),
        ("openings", 2),
        ("urgency", "Urgent"),
        ("salary_min", 100),
        ("salary_max", 200),
        ("must_haves", ["Python"]),
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
    assert body["reply"].endswith("That's everything I need — want to publish this?")
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
        json={"title": "Backend Engineer", "seniority": "Senior", "must_haves": ["Python"]},
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
    # ...and the serialized brief is the full requisition shape (gaps/messages).
    assert "gaps" in body and "messages" in body and body["title"] == "Backend Engineer"

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
    # no longer listed as gaps (completeness itself stays 0 until the first chat
    # turn, matching the create contract).
    gap_keys = [g["key"] for g in body["gaps"]]
    assert "workplace_type" not in gap_keys and "employment_type" not in gap_keys


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
