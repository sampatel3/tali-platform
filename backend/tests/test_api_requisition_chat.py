"""Requisition chat API — create seeds opening message, chat turn (multipart),
serializer extras (custom_fields/messages/completeness/gaps), settings template
GET/PUT. The LLM is monkeypatched at the service module (no Anthropic)."""
import io

from app.llm.structured import StructuredResult
from app.platform.config import settings
from app.services import requisition_chat_service as chat
from app.services.requisition_chat_service import ChatCapture
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
