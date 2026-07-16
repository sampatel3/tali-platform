"""Route-level tests for /api/v1/taali-chat/*.

The streaming endpoint is exercised end-to-end with the Anthropic SDK
mocked; the helper endpoints (list / get / rename / delete) are tested
through the FastAPI TestClient with real DB writes.
"""

from __future__ import annotations

import copy
import json
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch


from app.models.taali_chat_conversation import TaaliChatConversation
from app.models.taali_chat_message import TaaliChatMessage
from app.models.user import User
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

    with patch("app.taali_chat.service.get_client_for_org", return_value=fake):
        resp = client.post(
            "/api/v1/taali-chat/turn",
            headers=headers,
            json={"message": "hi"},
        )
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")
    body = resp.text

    # Conversation id is published as a `2:` data frame so the frontend
    # can pin the URL after the first turn.
    data_frames = [line for line in body.splitlines() if line.startswith("2:")]
    assert data_frames, f"expected a data frame with conversation_id, got: {body!r}"
    payload = json.loads(data_frames[0][2:])
    assert isinstance(payload, list) and "conversation_id" in payload[0]

    # Streaming must end with a `d:` finish frame.
    assert any(line.startswith("d:") for line in body.splitlines())


def test_post_turn_does_not_stream_internal_exception(client, db):
    headers, _email = auth_headers(client, organization_name="ChatSafeErrorOrg")
    secret = "sdk-token=private-value"

    with patch(
        "app.domains.taali_chat.routes.run_chat_turn",
        side_effect=RuntimeError(secret),
    ):
        response = client.post(
            "/api/v1/taali-chat/turn",
            headers=headers,
            json={"message": "trigger failure"},
        )

    assert response.status_code == 200
    assert '3:"chat_turn_failed"' in response.text
    assert secret not in response.text


def test_post_turn_persists_conversation_visible_via_list_endpoint(client, db):
    headers, _email = auth_headers(client, organization_name="ChatRouteOrg2")
    fake = _FakeClient([_text_plan("First answer.")])

    with patch("app.taali_chat.service.get_client_for_org", return_value=fake):
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

    with patch("app.taali_chat.service.get_client_for_org", return_value=fake):
        client.post("/api/v1/taali-chat/turn", headers=headers, json={"message": "hello"})

    listing = client.get("/api/v1/taali-chat/conversations", headers=headers).json()
    cid = listing[0]["id"]
    detail = client.get(f"/api/v1/taali-chat/conversations/{cid}", headers=headers)
    assert detail.status_code == 200
    body = detail.json()
    assert [m["role"] for m in body["messages"]] == ["user", "assistant"]
    assert body["messages"][0]["content"][0]["text"] == "hello"
    assert body["messages"][1]["content"][0]["text"] == "Hi."
    assert body["has_more"] is False
    assert body["next_before"] is None


def test_get_conversation_detail_pages_back_without_gaps_or_duplicates(client, db):
    headers, email = auth_headers(client, organization_name="ChatPaginationOrg")
    user = db.query(User).filter(User.email == email).one()
    conversation = TaaliChatConversation(
        organization_id=user.organization_id,
        user_id=user.id,
        title="Long-running search",
    )
    db.add(conversation)
    db.flush()

    # Give every row the same timestamp so the test exercises the id
    # tie-breaker rather than accidentally relying on timestamp precision.
    created_at = datetime(2026, 1, 2, 3, 4, tzinfo=timezone.utc)
    rows = [
        TaaliChatMessage(
            conversation_id=conversation.id,
            organization_id=user.organization_id,
            role="user" if index % 2 == 0 else "assistant",
            content=[{"type": "text", "text": f"message {index}"}],
            created_at=created_at,
        )
        for index in range(65)
    ]
    db.add_all(rows)
    db.commit()
    expected_ids = [row.id for row in rows]

    latest = client.get(
        f"/api/v1/taali-chat/conversations/{conversation.id}",
        headers=headers,
    )
    assert latest.status_code == 200
    latest_body = latest.json()
    latest_ids = [message["id"] for message in latest_body["messages"]]
    assert latest_ids == expected_ids[-60:]
    assert latest_body["has_more"] is True
    assert latest_body["next_before"] == expected_ids[-60]

    older = client.get(
        f"/api/v1/taali-chat/conversations/{conversation.id}",
        headers=headers,
        params={"before": latest_body["next_before"]},
    )
    assert older.status_code == 200
    older_body = older.json()
    older_ids = [message["id"] for message in older_body["messages"]]
    assert older_ids == expected_ids[:5]
    assert older_body["has_more"] is False
    assert older_body["next_before"] is None
    assert older_ids + latest_ids == expected_ids
    assert len(set(older_ids + latest_ids)) == 65

    oversized = client.get(
        f"/api/v1/taali-chat/conversations/{conversation.id}",
        headers=headers,
        params={"limit": 201},
    )
    assert oversized.status_code == 422


def test_get_conversation_detail_rejects_cursor_from_another_conversation(client, db):
    headers, email = auth_headers(client, organization_name="ChatCursorScopeOrg")
    user = db.query(User).filter(User.email == email).one()
    conversations = [
        TaaliChatConversation(
            organization_id=user.organization_id,
            user_id=user.id,
            title=f"Conversation {index}",
        )
        for index in range(2)
    ]
    db.add_all(conversations)
    db.flush()
    foreign_cursor = TaaliChatMessage(
        conversation_id=conversations[1].id,
        organization_id=user.organization_id,
        role="user",
        content=[{"type": "text", "text": "not this conversation"}],
    )
    db.add(foreign_cursor)
    db.commit()

    response = client.get(
        f"/api/v1/taali-chat/conversations/{conversations[0].id}",
        headers=headers,
        params={"before": foreign_cursor.id},
    )
    assert response.status_code == 400
    assert response.json()["detail"] == "Invalid conversation cursor"


def test_delete_conversation_archives_and_hides(client, db):
    headers, _email = auth_headers(client, organization_name="ChatRouteOrg4")
    fake = _FakeClient([_text_plan("Hi.")])

    with patch("app.taali_chat.service.get_client_for_org", return_value=fake):
        client.post("/api/v1/taali-chat/turn", headers=headers, json={"message": "hi"})

    cid = client.get("/api/v1/taali-chat/conversations", headers=headers).json()[0]["id"]
    delete = client.delete(f"/api/v1/taali-chat/conversations/{cid}", headers=headers)
    assert delete.status_code == 204

    after = client.get("/api/v1/taali-chat/conversations", headers=headers).json()
    assert after == []


def test_rename_conversation(client, db):
    headers, _email = auth_headers(client, organization_name="ChatRouteOrg5")
    fake = _FakeClient([_text_plan("Hi.")])

    with patch("app.taali_chat.service.get_client_for_org", return_value=fake):
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

    with patch("app.taali_chat.service.get_client_for_org", return_value=fake):
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
