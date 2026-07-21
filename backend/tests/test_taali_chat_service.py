"""Service-level tests for the Anthropic loop / SSE protocol adapter.

These mock ``anthropic.Anthropic.messages.stream`` so we never make a real
API call. Focus is on:

  - frame ordering (text deltas, tool_call_start, tool_call_result, finish)
  - tool dispatch correctness (handler called with right args, errors
    converted to ``isError`` frames)
  - persistence (one TaaliChatConversation, alternating user/assistant
    TaaliChatMessage rows in the right order)
  - ``MAX_TOOL_ROUNDS`` guard

The real Anthropic SDK is replaced with a tiny double that yields the
same event shapes as the live SDK.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import patch

from app.candidate_search.tool_failure_contract import (
    CANDIDATE_SEARCH_UNAVAILABLE_CODE,
    CANDIDATE_SEARCH_UNAVAILABLE_MESSAGE,
)
from app.models.candidate import Candidate
from app.models.candidate_application import CandidateApplication
from app.models.organization import Organization
from app.models.role import Role
from app.models.taali_chat_conversation import TaaliChatConversation
from app.models.taali_chat_message import TaaliChatMessage
from app.models.user import User
from app.taali_chat.service import ChatTurnInput, run_chat_turn
from app.taali_chat.search_context import population_context_for_search
from app.taali_chat.stream_round import CHAT_ROUND_IDLE_TIMEOUT_SECONDS
from app.services.usage_metering_service import InsufficientCreditsError


# ---------------------------------------------------------------------------
# Anthropic SDK fake
# ---------------------------------------------------------------------------


class _FakeStream:
    """Stand-in for ``client.messages.stream(...)`` returning canned events."""

    def __init__(self, events, final_message):
        self._events = list(events)
        self._final = final_message
        self.current_message_snapshot = SimpleNamespace(content=[])

    def __iter__(self):
        for e in self._events:
            # Mimic the SDK growing a snapshot as blocks come in.
            etype = e.type
            snap = self.current_message_snapshot.content
            if etype == "content_block_start":
                snap.append(e.content_block)
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
        self.on_stream = None

    def stream(self, **kwargs):
        if self.on_stream is not None:
            self.on_stream()
        # Snapshot kwargs — the service mutates ``messages`` later in the
        # turn loop, so we'd otherwise see post-mutation state on inspect.
        import copy

        self.calls.append(copy.deepcopy(kwargs))
        plan = self._plans.pop(0)
        return _FakeStream(plan["events"], plan["final"])


class _FakeClient:
    def __init__(self, plans):
        self.messages = _FakeMessagesResource(plans)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def test_followup_search_carries_forward_the_last_explicit_occupation():
    messages = [
        {
            "role": "user",
            "content": [{
                "type": "text",
                "text": "can you find a project manager with Treasury experience (Banking domain)",
            }],
        },
        {
            "role": "assistant",
            "content": [{
                "type": "tool_use",
                "name": "find_top_candidates",
                "input": {
                    "query": "project manager with Treasury experience (Banking domain)"
                },
            }],
        },
        {
            "role": "user",
            "content": [{
                "type": "text",
                "text": "can you find me the top candidates with Treasury banking experience",
            }],
        },
    ]

    context = population_context_for_search(
        messages,
        current_query="Treasury banking experience",
    )

    assert context == {"titles_all": ["project manager"], "titles_any": []}


def test_followup_search_can_explicitly_clear_the_occupation_scope():
    messages = [{
        "role": "user",
        "content": [{
            "type": "text",
            "text": "search across all roles for Treasury banking experience",
        }],
    }]

    assert population_context_for_search(
        messages,
        current_query="Treasury banking experience across all roles",
    ) is None


def test_followup_search_does_not_carry_forward_an_excluded_title():
    prior = {
        "role": "user",
        "content": [{"type": "text", "text": "find project managers with Treasury experience"}],
    }
    for latest in (
        "not project managers, find people with banking experience",
        "exclude project managers and find people with banking experience",
        "I don't want project managers; find people with banking experience",
        "not project managers or scrum masters, show Treasury experience",
    ):
        messages = [
            prior,
            {"role": "user", "content": [{"type": "text", "text": latest}]},
        ]
        assert population_context_for_search(
            messages,
            current_query="banking experience",
        ) is None


def test_followup_title_exclusion_can_replace_the_population():
    messages = [
        {
            "role": "user",
            "content": [{"type": "text", "text": "find project managers"}],
        },
        {
            "role": "user",
            "content": [{
                "type": "text",
                "text": "not project managers; find data engineers instead",
            }],
        },
    ]

    assert population_context_for_search(
        messages,
        current_query="find relevant people",
    ) == {"titles_all": ["data engineer"], "titles_any": []}


def _seed_user(db) -> tuple[User, Organization]:
    org = Organization(name="ChatTestOrg", slug=f"chat-{id(db)}")
    db.add(org)
    db.flush()
    user = User(
        email=f"chat-{id(db)}@example.com",
        hashed_password="x",
        full_name="Recruiter",
        organization_id=org.id,
        is_active=True,
        is_verified=True,
        is_superuser=False,
    )
    db.add(user)
    db.commit()
    return user, org


def _text_only_plan(text: str):
    """Generates a plan that just yields one text response and stops."""
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
        SimpleNamespace(
            type="message_delta",
            delta=SimpleNamespace(stop_reason="end_turn"),
        ),
    ]
    final = SimpleNamespace(
        content=[SimpleNamespace(type="text", text=text)],
        stop_reason="end_turn",
        usage=SimpleNamespace(
            input_tokens=10,
            output_tokens=5,
            cache_read_input_tokens=0,
            cache_creation_input_tokens=0,
        ),
    )
    return {"events": events, "final": final}


def test_insufficient_credits_does_not_persist_an_unanswered_turn(db):
    user, org = _seed_user(db)

    with patch(
        "app.taali_chat.service.reserve",
        side_effect=InsufficientCreditsError(
            organization_id=org.id,
            required=10_000,
            available=0,
        ),
    ), patch("app.taali_chat.service.get_client_for_org") as get_client:
        frames = _drain(
            run_chat_turn(
                db=db,
                user=user,
                organization=org,
                turn=ChatTurnInput(user_message="find candidates", conversation_id=None),
            )
        )

    assert any("out of AI credits" in frame for frame in frames)
    assert db.query(TaaliChatConversation).count() == 0
    assert db.query(TaaliChatMessage).count() == 0
    get_client.assert_not_called()


def _tool_use_plan(*, tool_id: str, tool_name: str, args: dict):
    """First-round plan: emit a tool_use block with streamed args, stop on tool_use."""
    args_json = json.dumps(args)
    events = [
        SimpleNamespace(
            type="content_block_start",
            index=0,
            content_block=SimpleNamespace(type="tool_use", id=tool_id, name=tool_name),
        ),
        SimpleNamespace(
            type="content_block_delta",
            index=0,
            delta=SimpleNamespace(type="input_json_delta", partial_json=args_json),
        ),
        SimpleNamespace(type="content_block_stop", index=0),
        SimpleNamespace(
            type="message_delta", delta=SimpleNamespace(stop_reason="tool_use")
        ),
    ]
    final = SimpleNamespace(
        content=[
            SimpleNamespace(type="tool_use", id=tool_id, name=tool_name, input=args),
        ],
        stop_reason="tool_use",
        usage=SimpleNamespace(
            input_tokens=12,
            output_tokens=8,
            cache_read_input_tokens=0,
            cache_creation_input_tokens=0,
        ),
    )
    return {"events": events, "final": final}


def _multi_tool_use_plan(calls: list[tuple[str, str, dict]]):
    events = []
    blocks = []
    for index, (tool_id, tool_name, args) in enumerate(calls):
        events.extend(
            [
                SimpleNamespace(
                    type="content_block_start",
                    index=index,
                    content_block=SimpleNamespace(
                        type="tool_use", id=tool_id, name=tool_name
                    ),
                ),
                SimpleNamespace(
                    type="content_block_delta",
                    index=index,
                    delta=SimpleNamespace(
                        type="input_json_delta",
                        partial_json=json.dumps(args),
                    ),
                ),
                SimpleNamespace(type="content_block_stop", index=index),
            ]
        )
        blocks.append(
            SimpleNamespace(type="tool_use", id=tool_id, name=tool_name, input=args)
        )
    return {
        "events": events,
        "final": SimpleNamespace(
            content=blocks,
            stop_reason="tool_use",
            usage=SimpleNamespace(
                input_tokens=12,
                output_tokens=8,
                cache_read_input_tokens=0,
                cache_creation_input_tokens=0,
            ),
        ),
    }


def _drain(generator) -> list[str]:
    return [frame.body for frame in generator]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_text_only_turn_persists_and_streams(db):
    user, org = _seed_user(db)
    plans = [_text_only_plan("Hi there.")]
    fake_client = _FakeClient(plans)
    transaction_states = []
    fake_client.messages.on_stream = lambda: transaction_states.append(
        db.in_transaction()
    )

    with patch(
        "app.taali_chat.service.get_client_for_org", return_value=fake_client
    ) as get_client, patch(
        "app.taali_chat.service.record_event"
    ):
        frames = _drain(
            run_chat_turn(
                db=db,
                user=user,
                organization=org,
                turn=ChatTurnInput(user_message="hello", conversation_id=None),
            )
        )

    text_deltas = [f for f in frames if f.startswith("0:")]
    assert text_deltas, "expected at least one text delta frame"
    assert "Hi there." in "".join(json.loads(f[2:]) for f in text_deltas)
    assert any(f.startswith("d:") for f in frames), "expected finish-message frame"
    progress_frames = [json.loads(f[2:]) for f in frames if f.startswith("2:")]
    assert any(
        item.get("progress", {}).get("stage") == "planning"
        for payload in progress_frames
        for item in payload
    )
    assert fake_client.messages.calls[0]["timeout"] == CHAT_ROUND_IDLE_TIMEOUT_SECONDS
    assert get_client.call_args.kwargs == {
        "timeout": CHAT_ROUND_IDLE_TIMEOUT_SECONDS,
        "max_retries": 0,
    }
    assert transaction_states == [False]

    # One conversation, exactly two messages (user + assistant).
    convos = db.query(TaaliChatConversation).all()
    assert len(convos) == 1
    convo = convos[0]
    assert convo.organization_id == org.id
    assert convo.user_id == user.id

    msgs = (
        db.query(TaaliChatMessage)
        .filter(TaaliChatMessage.conversation_id == convo.id)
        .order_by(TaaliChatMessage.id)
        .all()
    )
    assert [m.role for m in msgs] == ["user", "assistant"]
    assert msgs[0].content[0]["text"] == "hello"
    assert msgs[1].stop_reason == "end_turn"


def test_tool_call_dispatches_and_emits_result(db):
    user, org = _seed_user(db)
    role = Role(organization_id=org.id, name="Backend", source="manual")
    db.add(role)
    db.commit()
    candidate = Candidate(
        organization_id=org.id, email="x@x.test", full_name="X", position="Eng"
    )
    db.add(candidate)
    db.flush()
    db.add(
        CandidateApplication(
            organization_id=org.id,
            candidate_id=candidate.id,
            role_id=role.id,
            status="applied",
            pipeline_stage="review",
            pipeline_stage_source="recruiter",
            application_outcome="open",
            source="manual",
            taali_score_cache_100=80.0,
        )
    )
    db.commit()

    plans = [
        _tool_use_plan(
            tool_id="toolu_001",
            tool_name="search_applications",
            args={"role_id": role.id, "min_score": 70},
        ),
        _text_only_plan("Found one strong candidate above 70."),
    ]
    fake_client = _FakeClient(plans)

    with patch("app.taali_chat.service.get_client_for_org", return_value=fake_client), patch(
        "app.taali_chat.service.record_event"
    ):
        frames = _drain(
            run_chat_turn(
                db=db,
                user=user,
                organization=org,
                turn=ChatTurnInput(user_message="any candidates above 70?", conversation_id=None),
            )
        )

    # AI SDK v3 tool-call lifecycle frames: b (streaming-start),
    # c (args delta), 9 (complete tool_call), a (tool_result).
    streaming_starts = [f for f in frames if f.startswith("b:")]
    deltas = [f for f in frames if f.startswith("c:")]
    completes = [f for f in frames if f.startswith("9:")]
    results = [f for f in frames if f.startswith("a:")]
    assert len(streaming_starts) == 1 and len(completes) == 1 and len(results) == 1
    assert deltas, "expected at least one args delta"
    result_payload = json.loads(results[0][2:])
    assert result_payload["toolCallId"] == "toolu_001"
    # The dispatched search_applications must return a list of candidate rows.
    assert isinstance(result_payload["result"], list)
    assert len(result_payload["result"]) == 1

    # Anthropic was called twice: once with no prior assistant turn, then
    # with the tool_use + tool_result appended.
    assert len(fake_client.messages.calls) == 2
    second_messages = fake_client.messages.calls[1]["messages"]
    assert any(
        m["role"] == "user"
        and isinstance(m["content"], list)
        and m["content"]
        and m["content"][0].get("type") == "tool_result"
        for m in second_messages
    )


def test_sensitive_tool_result_is_available_live_but_not_persisted(db):
    user, org = _seed_user(db)
    raw_cv = "PRIVATE CV SOURCE TEXT THAT MUST NOT BE STORED IN CHAT"
    candidate = Candidate(
        organization_id=org.id,
        email="private@example.test",
        full_name="Private Candidate",
        position="Engineer",
        cv_text=raw_cv,
    )
    db.add(candidate)
    db.commit()

    plans = [
        _tool_use_plan(
            tool_id="toolu_sensitive",
            tool_name="get_candidate_cv",
            args={"candidate_id": candidate.id},
        ),
        _text_only_plan("I checked the source CV."),
    ]
    fake_client = _FakeClient(plans)

    with patch("app.taali_chat.service.get_client_for_org", return_value=fake_client), patch(
        "app.taali_chat.service.record_event"
    ):
        frames = _drain(
            run_chat_turn(
                db=db,
                user=user,
                organization=org,
                turn=ChatTurnInput(user_message="check the CV", conversation_id=None),
            )
        )

    live_result = next(json.loads(frame[2:]) for frame in frames if frame.startswith("a:"))
    assert live_result["result"]["cv_text"] == raw_cv
    assert raw_cv in fake_client.messages.calls[1]["messages"][-1]["content"][0]["content"]

    stored_messages = db.query(TaaliChatMessage).all()
    serialized = json.dumps([message.content for message in stored_messages])
    assert raw_cv not in serialized
    assert "omitted_from_transcript" in serialized


def test_tool_error_emits_is_error_frame(db):
    user, org = _seed_user(db)
    plans = [
        _tool_use_plan(
            tool_id="toolu_bad",
            tool_name="get_role",
            args={"role_id": 999_999},  # nonexistent
        ),
        _text_only_plan("Sorry, that role doesn't exist."),
    ]
    fake_client = _FakeClient(plans)

    with patch("app.taali_chat.service.get_client_for_org", return_value=fake_client), patch(
        "app.taali_chat.service.record_event"
    ):
        frames = _drain(
            run_chat_turn(
                db=db,
                user=user,
                organization=org,
                turn=ChatTurnInput(user_message="show role 999999", conversation_id=None),
            )
        )

    # In AI SDK v3 protocol, tool errors flow through the tool_result
    # payload itself — there's no isError flag at this level. We surface
    # errors as ``{"error": "...", "tool": "..."}`` inside ``result``.
    error_results = [
        f
        for f in frames
        if f.startswith("a:")
        and isinstance(json.loads(f[2:]).get("result"), dict)
        and "error" in json.loads(f[2:])["result"]
    ]
    assert len(error_results) == 1


def test_candidate_search_failure_recovers_and_ends_without_model_synthesis(db):
    user, org = _seed_user(db)
    fake_client = _FakeClient(
        [
            _tool_use_plan(
                tool_id="toolu_search_failure",
                tool_name="find_top_candidates",
                args={"query": "PySpark experience"},
            )
        ]
    )
    raw_marker = str(org.slug)

    def fail_search(*_args, **_kwargs):
        tool_db = _kwargs["db"]
        tool_db.add(Organization(name="Duplicate", slug=raw_marker))
        tool_db.flush()  # real IntegrityError leaves the Session rollback-only
        raise AssertionError("duplicate organization flush should fail")

    with (
        patch("app.taali_chat.service.get_client_for_org", return_value=fake_client),
        patch("app.taali_chat.service.record_event"),
        patch("app.taali_chat.tool_execution.dispatch_tool", side_effect=fail_search),
        patch.object(db, "rollback", wraps=db.rollback) as rollback,
    ):
        frames = _drain(
            run_chat_turn(
                db=db,
                user=user,
                organization=org,
                turn=ChatTurnInput(
                    user_message="find PySpark candidates",
                    conversation_id=None,
                ),
            )
        )
    db.commit()  # proves the safe transcript survives failed-transaction recovery

    assert len(fake_client.messages.calls) == 1
    rollback.assert_called_once()
    serialized_frames = "".join(frames)
    assert CANDIDATE_SEARCH_UNAVAILABLE_MESSAGE in serialized_frames
    assert CANDIDATE_SEARCH_UNAVAILABLE_CODE in serialized_frames
    assert raw_marker not in serialized_frames
    assert not any(frame.startswith("3:") for frame in frames)

    messages = db.query(TaaliChatMessage).order_by(TaaliChatMessage.id).all()
    assert [message.role for message in messages] == [
        "user",
        "assistant",
        "user",
        "assistant",
    ]
    serialized_messages = json.dumps([message.content for message in messages])
    assert CANDIDATE_SEARCH_UNAVAILABLE_CODE in serialized_messages
    assert CANDIDATE_SEARCH_UNAVAILABLE_MESSAGE in serialized_messages
    assert raw_marker not in serialized_messages


def test_verified_search_result_is_durable_before_later_tool_failure(db):
    user, org = _seed_user(db)
    raw_marker = str(org.slug)
    report_slug = f"durable-search-report-{id(db)}"
    report_url = "https://reports.example.test/durable-search"
    fake_client = _FakeClient(
        [
            _multi_tool_use_plan(
                [
                    ("toolu_read_first", "get_role", {"role_id": 1}),
                    (
                        "toolu_search_second",
                        "find_top_candidates",
                        {"query": "PySpark experience"},
                    ),
                ]
            ),
            _text_only_plan("The verified search completed; the other read failed."),
        ]
    )
    dispatched: list[str] = []

    def dispatch(name, *_args, **kwargs):
        dispatched.append(name)
        tool_db = kwargs["db"]
        if name == "find_top_candidates":
            tool_db.add(Organization(name="Durable search report", slug=report_slug))
            tool_db.flush()
            return {
                "is_exact_empty": True,
                "candidates": [],
                "report_url": report_url,
            }
        tool_db.add(Organization(name="Duplicate", slug=raw_marker))
        tool_db.flush()  # real IntegrityError rolls back only this tool phase
        raise AssertionError("duplicate organization flush should fail")

    with (
        patch("app.taali_chat.service.get_client_for_org", return_value=fake_client),
        patch("app.taali_chat.service.record_event"),
        patch("app.taali_chat.tool_execution.dispatch_tool", side_effect=dispatch),
    ):
        frames = _drain(
            run_chat_turn(
                db=db,
                user=user,
                organization=org,
                turn=ChatTurnInput(
                    user_message="Search, then read the role",
                    conversation_id=None,
                ),
            )
        )
    db.commit()

    assert dispatched == ["find_top_candidates", "get_role"]
    assert len(fake_client.messages.calls) == 2
    assert raw_marker not in "".join(frames)
    second_round_messages = json.dumps(fake_client.messages.calls[1]["messages"])
    assert report_url in second_round_messages
    assert db.query(Organization).filter(Organization.slug == report_slug).count() == 1


def test_max_tool_rounds_guard(db):
    """A repeated identical tool plan should trip the no-progress breaker early."""
    user, org = _seed_user(db)
    plans = [
        _tool_use_plan(
            tool_id=f"toolu_{i}", tool_name="list_roles", args={}
        )
        for i in range(20)  # generous; the no-progress breaker should stop it
    ]
    fake_client = _FakeClient(plans)

    with patch("app.taali_chat.service.get_client_for_org", return_value=fake_client), patch(
        "app.taali_chat.service.record_event"
    ):
        frames = _drain(
            run_chat_turn(
                db=db,
                user=user,
                organization=org,
                turn=ChatTurnInput(user_message="loop forever", conversation_id=None),
            )
        )

    from app.taali_chat.service import MAX_IDENTICAL_TOOL_ROUNDS

    # First call plus the configured number of identical retries. The final
    # repeated response is paid/metered but its duplicate tool is not run.
    assert len(fake_client.messages.calls) == MAX_IDENTICAL_TOOL_ROUNDS + 1
    assert any(f.startswith("3:") for f in frames), "expected error frame on guard trip"
    assert any(f.startswith("d:") for f in frames)


def test_continuing_conversation_loads_history(db):
    user, org = _seed_user(db)
    convo = TaaliChatConversation(organization_id=org.id, user_id=user.id, title="Prior")
    db.add(convo)
    db.flush()
    db.add(
        TaaliChatMessage(
            conversation_id=convo.id,
            organization_id=org.id,
            role="user",
            content=[{"type": "text", "text": "older question"}],
        )
    )
    db.add(
        TaaliChatMessage(
            conversation_id=convo.id,
            organization_id=org.id,
            role="assistant",
            content=[{"type": "text", "text": "older answer"}],
        )
    )
    db.commit()

    plans = [_text_only_plan("Continuing.")]
    fake_client = _FakeClient(plans)
    with patch("app.taali_chat.service.get_client_for_org", return_value=fake_client), patch(
        "app.taali_chat.service.record_event"
    ):
        _drain(
            run_chat_turn(
                db=db,
                user=user,
                organization=org,
                turn=ChatTurnInput(user_message="follow-up", conversation_id=convo.id),
            )
        )

    # Anthropic call should have received >= 3 messages: 2 historical + 1 new.
    sent = fake_client.messages.calls[0]["messages"]
    assert len(sent) >= 3
    assert sent[-1]["role"] == "user"
    assert sent[-1]["content"][0]["text"] == "follow-up"


def test_unknown_conversation_id_emits_error(db):
    user, org = _seed_user(db)
    plans: list[dict] = []  # no Anthropic call expected
    fake_client = _FakeClient(plans)
    with patch("app.taali_chat.service.get_client_for_org", return_value=fake_client), patch(
        "app.taali_chat.service.record_event"
    ):
        frames = _drain(
            run_chat_turn(
                db=db,
                user=user,
                organization=org,
                turn=ChatTurnInput(user_message="hi", conversation_id=999_999),
            )
        )
    assert any(f.startswith("3:") and "999999" in f for f in frames)
    # Anthropic must not have been called.
    assert len(fake_client.messages.calls) == 0
