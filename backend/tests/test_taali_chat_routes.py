"""Route-level tests for /api/v1/taali-chat/*.

The streaming endpoint is exercised end-to-end with the Anthropic SDK
mocked; the helper endpoints (list / get / rename / delete) are tested
through the FastAPI TestClient with real DB writes.
"""

from __future__ import annotations

import copy
import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.models.taali_chat_conversation import TaaliChatConversation
from app.models.taali_chat_message import TaaliChatMessage
from app.platform.database import SessionLocal
from tests.conftest import auth_headers


# ---------------------------------------------------------------------------
# Anthropic SDK fake — minimal stub mirroring test_taali_chat_service.py
# ---------------------------------------------------------------------------


class _FakeStream:
    def __init__(self, events, final_message):
        self._events = list(events)
        self._final = final_message
        self.current_message_snapshot = SimpleNamespace(content=[])

    def __iter__(self):
        for e in self._events:
            if e.type == "content_block_start":
                self.current_message_snapshot.content.append(e.content_block)
            yield e

    def get_final_message(self):
        return self._final

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return None


class _FakeMessagesResource:
    def __init__(self, plans):
        self._plans = list(plans)
        self.calls = []

    def stream(self, **kwargs):
        self.calls.append(copy.deepcopy(kwargs))
        plan = self._plans.pop(0)
        return _FakeStream(plan["events"], plan["final"])


class _FakeClient:
    def __init__(self, plans):
        self.messages = _FakeMessagesResource(plans)


def _text_plan(text: str):
    events = [
        SimpleNamespace(
            type="content_block_start",
            index=0,
            content_block=SimpleNamespace(type="text", text=""),
        ),
        SimpleNamespace(
            type="content_block_delta",
            index=0,
            delta=SimpleNamespace(type="text_delta", text=text),
        ),
        SimpleNamespace(type="content_block_stop", index=0),
    ]
    final = SimpleNamespace(
        content=[SimpleNamespace(type="text", text=text)],
        stop_reason="end_turn",
        usage=SimpleNamespace(
            input_tokens=10,
            output_tokens=4,
            cache_read_input_tokens=0,
            cache_creation_input_tokens=0,
        ),
    )
    return {"events": events, "final": final}


# ---------------------------------------------------------------------------
# Streaming endpoint
# ---------------------------------------------------------------------------


def test_post_turn_streams_aisdk_frames(client, db):
    headers, _email = auth_headers(client, organization_name="ChatRouteOrg")
    fake = _FakeClient([_text_plan("Hello recruiter.")])

    with patch("app.taali_chat.service.get_client_for_org", return_value=fake), patch(
        "app.taali_chat.service.record_event"
    ):
        resp = client.post(
            "/api/v1/taali-chat/turn",
            headers=headers,
            json={"message": "hi"},
        )
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")
    assert resp.headers["content-encoding"] == "identity"
    body = resp.text

    # Conversation id is published as a `2:` data frame so the frontend
    # can pin the URL after the first turn.
    data_frames = [line for line in body.splitlines() if line.startswith("2:")]
    assert data_frames, f"expected a data frame with conversation_id, got: {body!r}"
    payload = json.loads(data_frames[0][2:])
    assert isinstance(payload, list) and "conversation_id" in payload[0]

    # Streaming must end with a `d:` finish frame.
    assert any(line.startswith("d:") for line in body.splitlines())


def test_post_turn_persists_conversation_visible_via_list_endpoint(client, db):
    headers, _email = auth_headers(client, organization_name="ChatRouteOrg2")
    fake = _FakeClient([_text_plan("First answer.")])

    with patch("app.taali_chat.service.get_client_for_org", return_value=fake), patch(
        "app.taali_chat.service.record_event"
    ):
        resp = client.post(
            "/api/v1/taali-chat/turn",
            headers=headers,
            json={"message": "find me an aws engineer"},
        )
    assert resp.status_code == 200

    listing = client.get("/api/v1/taali-chat/conversations", headers=headers)
    assert listing.status_code == 200, listing.text
    items = listing.json()
    assert len(items) == 1
    assert items[0]["title"].startswith("find me an aws")
    assert items[0]["message_count"] == 2  # user + assistant


def test_get_conversation_detail_returns_messages(client, db):
    headers, _email = auth_headers(client, organization_name="ChatRouteOrg3")
    fake = _FakeClient([_text_plan("Hi.")])

    with patch("app.taali_chat.service.get_client_for_org", return_value=fake), patch(
        "app.taali_chat.service.record_event"
    ):
        client.post("/api/v1/taali-chat/turn", headers=headers, json={"message": "hello"})

    listing = client.get("/api/v1/taali-chat/conversations", headers=headers).json()
    cid = listing[0]["id"]
    detail = client.get(f"/api/v1/taali-chat/conversations/{cid}", headers=headers)
    assert detail.status_code == 200
    body = detail.json()
    assert [m["role"] for m in body["messages"]] == ["user", "assistant"]
    assert body["messages"][0]["content"][0]["text"] == "hello"
    assert body["messages"][1]["content"][0]["text"] == "Hi."


def test_delete_conversation_archives_and_hides(client, db):
    headers, _email = auth_headers(client, organization_name="ChatRouteOrg4")
    fake = _FakeClient([_text_plan("Hi.")])

    with patch("app.taali_chat.service.get_client_for_org", return_value=fake), patch(
        "app.taali_chat.service.record_event"
    ):
        client.post("/api/v1/taali-chat/turn", headers=headers, json={"message": "hi"})

    cid = client.get("/api/v1/taali-chat/conversations", headers=headers).json()[0]["id"]
    delete = client.delete(f"/api/v1/taali-chat/conversations/{cid}", headers=headers)
    assert delete.status_code == 204

    after = client.get("/api/v1/taali-chat/conversations", headers=headers).json()
    assert after == []


def test_rename_conversation(client, db):
    headers, _email = auth_headers(client, organization_name="ChatRouteOrg5")
    fake = _FakeClient([_text_plan("Hi.")])

    with patch("app.taali_chat.service.get_client_for_org", return_value=fake), patch(
        "app.taali_chat.service.record_event"
    ):
        client.post("/api/v1/taali-chat/turn", headers=headers, json={"message": "hi"})

    cid = client.get("/api/v1/taali-chat/conversations", headers=headers).json()[0]["id"]
    renamed = client.patch(
        f"/api/v1/taali-chat/conversations/{cid}",
        headers=headers,
        json={"title": "AWS Glue search"},
    )
    assert renamed.status_code == 200
    assert renamed.json()["title"] == "AWS Glue search"


def test_cross_user_cannot_see_others_conversation(client, db):
    """Two users in different orgs — each sees only their own conversations."""
    headers_a, _ = auth_headers(client, organization_name="OrgA")
    headers_b, _ = auth_headers(client, organization_name="OrgB")
    fake = _FakeClient([_text_plan("Hi A.")])

    with patch("app.taali_chat.service.get_client_for_org", return_value=fake), patch(
        "app.taali_chat.service.record_event"
    ):
        client.post(
            "/api/v1/taali-chat/turn", headers=headers_a, json={"message": "secret to A"}
        )

    list_a = client.get("/api/v1/taali-chat/conversations", headers=headers_a).json()
    list_b = client.get("/api/v1/taali-chat/conversations", headers=headers_b).json()
    assert len(list_a) == 1
    assert list_b == []
    cid = list_a[0]["id"]
    detail_b = client.get(f"/api/v1/taali-chat/conversations/{cid}", headers=headers_b)
    assert detail_b.status_code == 404
