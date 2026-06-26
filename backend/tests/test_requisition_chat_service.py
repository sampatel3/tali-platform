"""Conversational requisition intake — gap engine, completeness, opening
message, capture/apply, multimodal assembly, and a monkeypatched chat turn."""
from app.llm.structured import StructuredResult
from app.models import Organization
from app.services import requisition_chat_service as chat
from app.services.requisition_chat_service import (
    ChatAttachment,
    ChatCapture,
    apply_capture,
    build_persisted_user_message,
    build_user_turn_content,
    compute_completeness,
    compute_gaps,
    opening_message,
    run_chat_turn,
    seed_opening_message,
)
from app.services.requisition_intake_agent import (
    WeightedPriority,
)
from app.services.requisition_template_service import (
    DEFAULT_REQUISITION_TEMPLATE,
    resolve_template,
)
from app.services.role_brief_service import create_brief, update_brief_fields


def _brief(db, **org_kw):
    org = Organization(name="Acme", slug="acme", **org_kw)
    db.add(org)
    db.flush()
    return create_brief(db, organization_id=org.id), org


# --------------------------------------------------------------------------- #
# Gap engine + completeness
# --------------------------------------------------------------------------- #
def test_compute_gaps_lists_required_empty_in_template_order(db):
    b, _ = _brief(db)
    template = DEFAULT_REQUISITION_TEMPLATE
    gaps = compute_gaps(b, template)
    keys = [g["key"] for g in gaps]
    # All required fields, in template (section→field) order.
    assert keys == [
        "title",
        "workplace_type",
        "employment_type",
        "openings",
        "urgency",
        "salary_min",
        "salary_max",
        "salary_currency",
        "must_haves",
    ]
    # Each gap carries its section.
    title_gap = gaps[0]
    assert title_gap["section"] == "role_basics" and title_gap["label"] == "Title"


def test_gaps_shrink_as_required_fields_fill(db):
    b, _ = _brief(db)
    template = DEFAULT_REQUISITION_TEMPLATE
    update_brief_fields(db, b, title="Backend Engineer", openings=2)
    keys = [g["key"] for g in compute_gaps(b, template)]
    assert "title" not in keys and "openings" not in keys
    assert "workplace_type" in keys  # still empty


def test_empty_list_and_dict_count_as_unfilled(db):
    b, _ = _brief(db)
    update_brief_fields(db, b, must_haves=[], sourcing_signals={})
    keys = [g["key"] for g in compute_gaps(b, DEFAULT_REQUISITION_TEMPLATE)]
    assert "must_haves" in keys  # [] is empty → still a gap


def test_suggested_replies_prefers_model_supplied(db):
    b, _ = _brief(db)
    cap = ChatCapture(assistant_reply="?", suggested_replies=["A", "B", "", "C"])
    replies = chat._resolve_suggested_replies(cap, b, DEFAULT_REQUISITION_TEMPLATE)
    assert replies == ["A", "B", "C"]  # blanks stripped, model wins


def test_suggested_replies_fallback_to_select_options_of_next_gap(db):
    b, _ = _brief(db)
    template = DEFAULT_REQUISITION_TEMPLATE
    # With only title filled, the next required gap is workplace_type (a select).
    update_brief_fields(db, b, title="Eng")
    cap = ChatCapture(assistant_reply="Onsite, hybrid or remote?")  # no replies
    replies = chat._resolve_suggested_replies(cap, b, template)
    assert replies == ["Onsite", "Hybrid", "Remote"]


def test_opening_message_has_no_options_for_text_first_field(db):
    b, _ = _brief(db)
    seed_opening_message(b, DEFAULT_REQUISITION_TEMPLATE)
    # First required field (title) is text → opening offers no tappable chips.
    assert b.messages[0]["role"] == "assistant"
    assert b.messages[0]["suggested_replies"] == []


def test_completeness_math(db):
    b, _ = _brief(db)
    template = DEFAULT_REQUISITION_TEMPLATE
    assert compute_completeness(b, template) == 0
    # 9 required fields total. Fill 2 → round(100*2/9) = 22.
    update_brief_fields(db, b, title="Eng", openings=1)
    assert compute_completeness(b, template) == 22
    # Fill the remaining 7 (urgency is a custom select → custom_fields).
    update_brief_fields(
        db, b,
        workplace_type="Remote", employment_type="Full-time",
        salary_min=100, salary_max=150, salary_currency="USD",
        must_haves=["Python"], custom_fields={"urgency": "High"},
    )
    assert compute_completeness(b, template) == 100


def test_completeness_100_when_no_required_fields(db):
    b, _ = _brief(db)
    template = {
        "version": 1,
        "sections": [
            {"key": "s", "label": "S", "fields": [{"key": "title", "label": "T", "type": "text", "required": False}]}
        ],
    }
    assert compute_completeness(b, template) == 100


# --------------------------------------------------------------------------- #
# Opening message
# --------------------------------------------------------------------------- #
def test_opening_message_includes_greeting_and_first_required_question():
    msg = opening_message(DEFAULT_REQUISITION_TEMPLATE)
    assert msg.startswith("Hi —")
    assert "what role are you hiring for?" in msg


def test_seed_opening_message_sets_single_assistant_turn(db):
    b, _ = _brief(db)
    seed_opening_message(b, DEFAULT_REQUISITION_TEMPLATE)
    assert len(b.messages) == 1
    assert b.messages[0]["role"] == "assistant"
    assert "what role are you hiring for?" in b.messages[0]["content"]
    assert b.messages[0]["attachments"] == []


# --------------------------------------------------------------------------- #
# Capture → apply (coercion, columns + custom_fields, no-blanking)
# --------------------------------------------------------------------------- #
def test_apply_capture_routes_columns_lists_structs_and_custom(db):
    custom_template = {
        "version": 1,
        "sections": [
            {
                "key": "role_basics",
                "label": "Role basics",
                "fields": [
                    {"key": "title", "label": "Title", "type": "text", "required": True},
                ],
            },
            {
                "key": "requirements",
                "label": "Requirements",
                "fields": [
                    {"key": "must_haves", "label": "Must-haves", "type": "list", "required": True},
                ],
            },
            {
                "key": "context",
                "label": "Context",
                "fields": [
                    {"key": "priorities", "label": "Priorities", "type": "struct_list", "required": False},
                    # An org-added custom field with NO RoleBrief column.
                    {"key": "visa_sponsorship", "label": "Visa", "type": "text", "required": False},
                ],
            },
        ],
    }
    b, _ = _brief(db)
    capture = ChatCapture(
        assistant_reply="Got it — what's the salary range?",
        open_questions=["salary range?"],
        title="Senior Backend Engineer",
        must_haves=["Python", "Postgres"],
        priorities=[WeightedPriority(factor="domain", weight="high")],
        custom={"visa_sponsorship": "Yes, we sponsor"},
    )
    apply_capture(db, b, capture, custom_template)

    # Column (text), list, struct_list applied to the right columns.
    assert b.title == "Senior Backend Engineer"
    assert b.must_haves == ["Python", "Postgres"]
    assert b.priorities == [{"factor": "domain", "weight": "high"}]
    # Custom (no-column) key landed in custom_fields.
    assert b.custom_fields["visa_sponsorship"] == "Yes, we sponsor"
    # open_questions persisted to agent_state.
    assert b.agent_state["open_questions"] == ["salary range?"]
    # completeness: 2/2 required filled → 100.
    assert b.completeness == 100


def test_apply_capture_does_not_blank_previously_captured(db):
    b, _ = _brief(db)
    update_brief_fields(db, b, title="Original Title", must_haves=["Python"])
    # A turn that captures nothing for title/must_haves must not wipe them.
    capture = ChatCapture(assistant_reply="Thanks!", department="Platform")
    apply_capture(db, b, capture, DEFAULT_REQUISITION_TEMPLATE)
    assert b.title == "Original Title"
    assert b.must_haves == ["Python"]
    assert b.department == "Platform"


def test_apply_capture_number_coercion(db):
    b, _ = _brief(db)
    # Model may emit a string for a number-typed field; coerce it.
    capture = ChatCapture(assistant_reply="ok", openings="3", salary_min=120000)
    apply_capture(db, b, capture, DEFAULT_REQUISITION_TEMPLATE)
    assert b.openings == 3
    assert b.salary_min == 120000


def test_apply_capture_target_start_date_maps_to_target_start_column(db):
    b, _ = _brief(db)
    capture = ChatCapture(assistant_reply="ok", target_start_date="2026-09-01")
    apply_capture(db, b, capture, DEFAULT_REQUISITION_TEMPLATE)
    assert b.target_start == "2026-09-01"


def test_apply_capture_sourcing_signals_list_and_process_text(db):
    # sourcing_signals (list) and process (longtext) match the template's
    # declared types — stored as a cleaned list of strings and a plain string.
    b, _ = _brief(db)
    capture = ChatCapture(
        assistant_reply="ok",
        sourcing_signals=["Ex-Stripe", "Strong OSS presence"],
        process="3 rounds: screen, take-home, onsite. Urgent.",
    )
    apply_capture(db, b, capture, DEFAULT_REQUISITION_TEMPLATE)
    assert b.sourcing_signals == ["Ex-Stripe", "Strong OSS presence"]
    assert b.process == "3 rounds: screen, take-home, onsite. Urgent."


# --------------------------------------------------------------------------- #
# Multimodal assembly
# --------------------------------------------------------------------------- #
def test_persisted_user_message_records_attachment_metadata():
    msg = build_persisted_user_message(
        "see attached",
        [
            ChatAttachment(name="call.vtt", content_type="text/vtt", content=b"x"),
            ChatAttachment(name="board.png", content_type="image/png", content=b"y"),
            ChatAttachment(name="jd.pdf", content_type="application/pdf", content=b"z"),
        ],
    )
    assert msg["role"] == "user" and msg["content"] == "see attached"
    kinds = {a["name"]: a["kind"] for a in msg["attachments"]}
    assert kinds == {"call.vtt": "transcript", "board.png": "image", "jd.pdf": "file"}


def test_transcript_text_reaches_user_turn_content():
    content = build_user_turn_content(
        "here are my notes",
        [ChatAttachment(name="kickoff.txt", content_type="text/plain", content=b"We need a staff PM")],
    )
    # No image → plain string content.
    assert isinstance(content, str)
    assert "here are my notes" in content
    assert "[Attached transcript: kickoff.txt]" in content
    assert "We need a staff PM" in content


def test_image_attachment_produces_base64_image_block():
    content = build_user_turn_content(
        "what does this say",
        [ChatAttachment(name="shot.png", content_type="image/png", content=b"\x89PNG-bytes")],
    )
    # Image present → list of content blocks, with one image block (base64).
    assert isinstance(content, list)
    image_blocks = [b for b in content if b.get("type") == "image"]
    assert len(image_blocks) == 1
    src = image_blocks[0]["source"]
    assert src["type"] == "base64" and src["media_type"] == "image/png" and src["data"]
    # The text part is preserved alongside the image.
    assert any(b.get("type") == "text" and "what does this say" in b.get("text", "") for b in content)


# --------------------------------------------------------------------------- #
# Orchestrated chat turn (LLM monkeypatched — no Anthropic)
# --------------------------------------------------------------------------- #
def test_run_chat_turn_applies_capture_appends_messages_shrinks_gaps(db, monkeypatch):
    b, _org = _brief(db)
    seed_opening_message(b, resolve_template(_org))
    db.flush()

    captured_calls = {}

    def fake_generate_structured(client, **kwargs):
        # Record the constructed LLM input so we can assert on it.
        captured_calls["messages"] = kwargs["messages"]
        captured_calls["system"] = kwargs["system"]
        captured_calls["feature"] = kwargs["metering"].feature
        value = ChatCapture(
            assistant_reply="Great — onsite or remote, and how many openings?",
            open_questions=["workplace_type?", "openings?"],
            title="Backend Engineer",
            must_haves=["Python", "Postgres"],
            salary_min=100000,
            salary_max=140000,
            salary_currency="USD",
        )
        return StructuredResult(value=value, ok=True)

    monkeypatch.setattr(chat, "generate_structured", fake_generate_structured)

    result = run_chat_turn(
        db, b,
        message="We need a backend engineer, Python + Postgres, $100-140k.",
        attachments=[ChatAttachment(name="notes.txt", content_type="text/plain", content=b"team is small")],
        client=object(),
        model="test-model",
    )
    assert result.ok

    # Captured values applied to the right columns.
    assert b.title == "Backend Engineer"
    assert b.must_haves == ["Python", "Postgres"]
    assert b.salary_currency == "USD"
    # source_kind defaulted to conversational.
    assert b.source_kind == "conversational"
    # open_questions persisted.
    assert b.agent_state["open_questions"] == ["workplace_type?", "openings?"]

    # Transcript: opening + user + assistant reply.
    roles = [m["role"] for m in b.messages]
    assert roles == ["assistant", "user", "assistant"]
    assert b.messages[1]["content"].startswith("We need a backend engineer")
    assert b.messages[1]["attachments"] == [{"name": "notes.txt", "kind": "transcript"}]
    assert b.messages[2]["content"].startswith("Great")

    # The transcript file content reached the LLM input (multimodal assertion).
    last_user = captured_calls["messages"][-1]
    assert last_user["role"] == "user"
    assert "[Attached transcript: notes.txt]" in last_user["content"]
    assert "team is small" in last_user["content"]
    # Metered under the right feature.
    assert captured_calls["feature"] == "requisition_intake_chat"

    # Gaps shrank: title/salary/must_haves now filled; workplace_type/openings remain.
    keys = [g["key"] for g in compute_gaps(b, resolve_template(_org))]
    assert "title" not in keys and "must_haves" not in keys
    assert "workplace_type" in keys and "openings" in keys
    # completeness recomputed: 5/9 required filled (urgency still empty).
    assert b.completeness == round(100 * 5 / 9)


def test_run_chat_turn_image_block_reaches_llm(db, monkeypatch):
    b, _org = _brief(db)

    seen = {}

    def fake_generate_structured(client, **kwargs):
        seen["messages"] = kwargs["messages"]
        return StructuredResult(
            value=ChatCapture(assistant_reply="I can see the whiteboard."), ok=True
        )

    monkeypatch.setattr(chat, "generate_structured", fake_generate_structured)
    run_chat_turn(
        db, b,
        message="here's the whiteboard from our kickoff",
        attachments=[ChatAttachment(name="wb.png", content_type="image/png", content=b"\x89PNGdata")],
        client=object(),
        model="test-model",
    )
    last_user = seen["messages"][-1]
    assert isinstance(last_user["content"], list)
    assert any(blk.get("type") == "image" for blk in last_user["content"])


def test_run_chat_turn_failure_rolls_back_capture_but_keeps_user_message(db, monkeypatch):
    b, _org = _brief(db)
    monkeypatch.setattr(
        chat, "generate_structured",
        lambda *a, **k: StructuredResult(value=None, ok=False, error_reason="boom"),
    )
    result = run_chat_turn(db, b, message="a backend engineer", client=object(), model="m")
    assert not result.ok
    # Nothing captured.
    assert b.title is None
    # The user message was still appended (the turn happened).
    assert b.messages[-1]["role"] == "user"
