"""Safety contracts for the live Bullhorn fixture-capture operator tool."""

from __future__ import annotations

import json
from pathlib import Path
import sys
from typing import Any

import pytest

from app.components.integrations.bullhorn.errors import BullhornApiError
from app.components.integrations.bullhorn.event_handlers import normalize_event_type
from app.components.integrations.bullhorn.service import BullhornService
from scripts import bullhorn_capture_fixtures as capture_tool


SCRUB_KEY = b"fixed-test-only-scrub-key-32bytes"
CREDENTIALS = {
    "username": "capture-user",
    "client_id": "capture-client",
    "client_secret": "capture-secret",
    "password": "capture-password",
}


def test_dump_removes_nested_pii_credentials_identifiers_dates_and_numbers(
    tmp_path: Path,
) -> None:
    payload = {
        "statuses": ["Client Custom Interview Stage"],
        "categorization": {
            "interviewScheduledJobResponseStatus": "Client Custom Interview Stage"
        },
        "data": [
            {
                "id": 8_675_309,
                "candidateId": "CANDIDATE-REAL-42",
                "firstName": "PrivateFirst",
                "lastName": "PrivateLast",
                "name": "PrivateFirst PrivateLast",
                "email": "private.person@real-client.invalid",
                "phone": "+971501234567",
                "mobile": "",
                "occupation": "Confidential Quantum Researcher",
                "title": "Confidential Chief Architect",
                "description": (
                    "Contact private.person@real-client.invalid on +971501234567"
                ),
                "comments": "Client-only note about a private accommodation",
                "status": "Client Custom Interview Stage",
                "dateAdded": 1_721_234_567_890,
                "createdAt": "2026-07-18T10:11:12Z",
                "salary": 98_765.43,
                "address": {
                    "address1": "42 Confidential Street",
                    "address2": "",
                    "city": "Private City",
                    "zip": 12_345,
                    "latitude": 25.2048,
                    "longitude": 55.2708,
                },
                "clientCorporation": {
                    "id": 999_001,
                    "name": "Secret Client Holdings",
                },
                "clientSecret": "real-client-secret",
                "BhRestToken": "real-rest-token",
                "isOpen": True,
            },
            {
                "id": 8_675_310,
                "status": "Client Custom Interview Stage",
            },
        ]
    }
    tmp_path.mkdir(exist_ok=True)

    capture_tool._dump(tmp_path, "candidate", payload, scrub_key=SCRUB_KEY)

    fixture_path = tmp_path / "candidate.json"
    serialized = fixture_path.read_text(encoding="utf-8")
    for secret_or_pii in (
        "PrivateFirst",
        "PrivateLast",
        "private.person",
        "+971501234567",
        "Confidential Chief Architect",
        "private accommodation",
        "Client Custom Interview Stage",
        "42 Confidential Street",
        "Private City",
        "Confidential Quantum Researcher",
        "Secret Client Holdings",
        "real-client-secret",
        "real-rest-token",
        "CANDIDATE-REAL-42",
        "2026-07-18",
    ):
        assert secret_or_pii not in serialized

    scrubbed = json.loads(serialized)
    first, second = scrubbed["data"]
    assert first["id"] != payload["data"][0]["id"]
    assert first["candidateId"] != payload["data"][0]["candidateId"]
    assert first["dateAdded"] == 946_684_800_000
    assert first["createdAt"] == "2000-01-01T00:00:00Z"
    assert first["salary"] != payload["data"][0]["salary"]
    assert 40_000 <= first["salary"] <= 200_000
    assert first["salary"] % 5_000 == 0
    assert first["address"]["zip"] != payload["data"][0]["address"]["zip"]
    assert first["address"]["latitude"] != payload["data"][0]["address"]["latitude"]
    assert isinstance(first["address"]["latitude"], float)
    assert -90 <= first["address"]["latitude"] <= 90
    assert isinstance(first["address"]["longitude"], float)
    assert -180 <= first["address"]["longitude"] <= 180
    assert first["clientSecret"] == "REDACTED"
    assert first["BhRestToken"] == "REDACTED"
    assert first["isOpen"] is True
    assert first["mobile"] == ""
    assert first["address"]["address2"] == ""
    assert first["name"] == f'{first["firstName"]} {first["lastName"]}'
    assert first["occupation"].startswith("text-")
    assert first["occupation"] not in {
        first["firstName"],
        first["lastName"],
        first["name"],
    }
    assert first["status"] == second["status"]
    assert first["status"] == scrubbed["statuses"][0]
    assert first["status"] == scrubbed["categorization"][
        "interviewScheduledJobResponseStatus"
    ]


def test_scrub_aliases_are_consistent_only_within_one_capture() -> None:
    payload = [
        {"id": 123, "status": "Private Stage"},
        {"id": 123, "status": "Private Stage"},
    ]

    first_capture = capture_tool._scrub(payload, scrub_key=b"a" * 32)
    second_capture = capture_tool._scrub(payload, scrub_key=b"b" * 32)

    assert first_capture[0] == first_capture[1]
    assert first_capture != second_capture


def test_dump_preserves_only_reviewed_entitlement_verbs(tmp_path: Path) -> None:
    capture_tool._dump(
        tmp_path,
        "entitlements",
        {
            "Candidate": ["GET", "POST", "CUSTOM_ADMIN"],
            "JobOrder": ["PUT", "DELETE"],
            "JobSubmission": ["GET"],
            "Note": ["POST"],
        },
        scrub_key=SCRUB_KEY,
    )

    entitlements = json.loads(
        (tmp_path / "entitlements.json").read_text(encoding="utf-8")
    )
    assert entitlements["Candidate"][:2] == ["GET", "POST"]
    assert entitlements["Candidate"][2].startswith("text-")
    assert entitlements["JobOrder"] == ["PUT", "DELETE"]
    assert entitlements["JobSubmission"] == ["GET"]
    assert entitlements["Note"] == ["POST"]


@pytest.mark.parametrize(
    "unsafe_scrubbed",
    [
        {"comments": "unreviewed free text"},
        {"salary": 123_456},
        {"PrivateClientName": "text-0123456789ab"},
    ],
)
def test_dump_fails_closed_before_writing_unproven_values(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    unsafe_scrubbed: dict[str, Any],
) -> None:
    monkeypatch.setattr(
        capture_tool,
        "_scrub",
        lambda *_args, **_kwargs: unsafe_scrubbed,
    )

    with pytest.raises(ValueError, match="unsafe fixture"):
        capture_tool._dump(tmp_path, "unsafe", {}, scrub_key=SCRUB_KEY)

    assert not (tmp_path / "unsafe.json").exists()


@pytest.mark.parametrize(
    ("field", "malicious"),
    [
        ("eventType", "ENTITY-PRIVATE-VARIANT"),
        ("entityEventType", "UPSERTED-PRIVATE"),
        ("entityName", "SecretCustomEntity"),
    ],
)
def test_dump_rejects_unknown_event_protocol_before_file_without_echo(
    tmp_path: Path,
    field: str,
    malicious: str,
) -> None:
    payload = {
        "eventType": "ENTITY",
        "entityEventType": "UPDATED",
        "entityName": "Candidate",
        field: malicious,
    }

    with pytest.raises(ValueError) as caught:
        capture_tool._dump(tmp_path, "event_poll", payload, scrub_key=SCRUB_KEY)

    assert malicious not in str(caught.value)
    assert not (tmp_path / "event_poll.json").exists()


def test_client_status_named_like_event_mutation_is_still_key_scoped_alias() -> None:
    scrubbed = capture_tool._scrub(
        {"status": "UPDATED", "entityEventType": "UPDATED"},
        scrub_key=SCRUB_KEY,
    )

    assert scrubbed["status"].startswith("category-")
    assert scrubbed["status"] != "UPDATED"
    assert scrubbed["entityEventType"] == "UPDATED"


def test_credentials_file_is_private_exact_and_preferred_over_environment(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    path = tmp_path / "bullhorn-connect.json"
    path.write_text(json.dumps(CREDENTIALS), encoding="utf-8")
    path.chmod(0o600)
    for environment_name in capture_tool._CREDENTIAL_KEYS:
        monkeypatch.setenv(environment_name, "environment-value-must-not-win")

    assert capture_tool._load_credentials(path) == CREDENTIALS

    path.chmod(0o640)
    with pytest.raises(SystemExit, match="group or others"):
        capture_tool._load_credentials(path)


def test_credentials_file_rejects_symlinks_and_unexpected_fields(
    tmp_path: Path,
) -> None:
    source = tmp_path / "credentials.json"
    source.write_text(json.dumps(CREDENTIALS), encoding="utf-8")
    source.chmod(0o600)
    symlink = tmp_path / "credentials-link.json"
    symlink.symlink_to(source)

    with pytest.raises(SystemExit, match="valid Bullhorn credentials file"):
        capture_tool._load_credentials(symlink)

    source.write_text(
        json.dumps({**CREDENTIALS, "access_token": "must-not-be-accepted"}),
        encoding="utf-8",
    )
    with pytest.raises(SystemExit, match="exactly the required fields"):
        capture_tool._load_credentials(source)


@pytest.mark.parametrize("line_break", ["\n", "\r", "\r\n"])
def test_credentials_file_rejects_actual_line_breaks_without_echoing_values(
    tmp_path: Path,
    line_break: str,
) -> None:
    secret = f"first-half{line_break}second-half-must-not-be-echoed"
    path = tmp_path / "credentials.json"
    path.write_text(
        json.dumps({**CREDENTIALS, "password": secret}),
        encoding="utf-8",
    )
    path.chmod(0o600)

    with pytest.raises(SystemExit) as raised:
        capture_tool._load_credentials(path)

    assert "first-half" not in str(raised.value)
    assert "second-half-must-not-be-echoed" not in str(raised.value)


def test_credentials_file_preserves_literal_backslash_sequences(tmp_path: Path) -> None:
    literal = r"literal\n-and-\r-characters"
    path = tmp_path / "credentials.json"
    path.write_text(
        json.dumps({**CREDENTIALS, "password": literal}),
        encoding="utf-8",
    )
    path.chmod(0o600)

    loaded = capture_tool._load_credentials(path)

    assert loaded["password"] == literal


def test_credentials_loader_reads_the_opened_inode_when_path_is_swapped(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    if not getattr(capture_tool.os, "O_NOFOLLOW", 0):
        pytest.skip("platform has no atomic no-follow open flag")
    path = tmp_path / "credentials.json"
    replacement = tmp_path / "replacement.json"
    path.write_text(json.dumps(CREDENTIALS), encoding="utf-8")
    replacement.write_text(
        json.dumps({**CREDENTIALS, "password": "swapped-attacker-value"}),
        encoding="utf-8",
    )
    path.chmod(0o600)
    replacement.chmod(0o600)
    real_open = capture_tool.os.open

    def open_then_swap(open_path: object, flags: int) -> int:
        descriptor = real_open(open_path, flags)
        replacement.replace(path)
        return descriptor

    monkeypatch.setattr(capture_tool.os, "open", open_then_swap)

    assert capture_tool._load_credentials(path) == CREDENTIALS


def test_cli_credentials_file_and_documented_output_resolve_to_backend(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    credentials_file = tmp_path / "bullhorn-connect.json"
    credentials_file.write_text(json.dumps(CREDENTIALS), encoding="utf-8")
    credentials_file.chmod(0o600)
    token_state_file = tmp_path / "bullhorn-rotated-token.json"
    observed: dict[str, object] = {}

    def fake_capture(out: Path, **kwargs: object) -> None:
        observed["out"] = out
        observed.update(kwargs)

    monkeypatch.setattr(capture_tool, "capture", fake_capture)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "bullhorn_capture_fixtures.py",
            "--allow-live",
            "--credentials-file",
            str(credentials_file),
            "--token-state-file",
            str(token_state_file),
            "--out",
            "tests/fixtures/bullhorn_recorded",
        ],
    )

    assert capture_tool.main() == 0
    assert observed["out"] == (
        capture_tool._BACKEND / "tests/fixtures/bullhorn_recorded"
    )
    assert observed["credentials"] == CREDENTIALS
    assert observed["token_state_file"] == token_state_file


def test_rotated_token_state_is_atomic_private_and_retains_latest_pair(
    tmp_path: Path,
) -> None:
    token_state = tmp_path / "private" / "bullhorn-token.json"

    capture_tool._persist_token_state(
        token_state,
        refresh_token="first-rotated-refresh",
        rest_url="https://rest.example.test/rest-services/first/",
    )
    capture_tool._persist_token_state(
        token_state,
        refresh_token="latest-rotated-refresh",
        rest_url="https://rest.example.test/rest-services/latest/",
    )

    assert token_state.stat().st_mode & 0o777 == 0o600
    assert json.loads(token_state.read_text(encoding="utf-8")) == {
        "refresh_token": "latest-rotated-refresh",
        "rest_url": "https://rest.example.test/rest-services/latest/",
    }
    assert not list(token_state.parent.glob(".bullhorn-token.json.rotate-*"))


def test_rotated_token_state_refuses_symlink_or_public_existing_file(
    tmp_path: Path,
) -> None:
    public = tmp_path / "public-token.json"
    public.write_text("{}", encoding="utf-8")
    public.chmod(0o644)
    with pytest.raises(RuntimeError, match="private Bullhorn token state"):
        capture_tool._persist_token_state(public, refresh_token="secret")

    private_target = tmp_path / "private-target.json"
    private_target.write_text("{}", encoding="utf-8")
    private_target.chmod(0o600)
    symlink = tmp_path / "token-link.json"
    symlink.symlink_to(private_target)
    with pytest.raises(RuntimeError, match="private Bullhorn token state"):
        capture_tool._persist_token_state(symlink, refresh_token="secret")


def test_service_password_exchange_persists_rotation_to_token_artifact(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    token_state = tmp_path / "rotated.json"
    observed: dict[str, object] = {}

    class _Auth:
        def __init__(self, **kwargs: object) -> None:
            observed.update(kwargs)

        def authorize_with_password(self) -> None:
            observed["persist_tokens"](
                refresh_token="rotation-from-provider",
                rest_url="https://rest.example.test/rest-services/corp/",
            )

    monkeypatch.setattr(capture_tool, "BullhornAuth", _Auth)
    monkeypatch.setattr(
        capture_tool,
        "BullhornService",
        lambda auth, *, client_id: {"auth": auth, "client_id": client_id},
    )

    capture_tool._build_service(CREDENTIALS, token_state_file=token_state)

    assert token_state.stat().st_mode & 0o777 == 0o600
    assert json.loads(token_state.read_text(encoding="utf-8"))["refresh_token"] == (
        "rotation-from-provider"
    )


@pytest.mark.parametrize(
    ("method_name", "kwargs", "expected_path", "selector"),
    [
        (
            "search_job_orders",
            {"fields": "id", "query": "isOpen:true"},
            "search/JobOrder",
            ("query", "isOpen:true"),
        ),
        (
            "search_candidates",
            {"fields": "id", "query": "id:23"},
            "search/Candidate",
            ("query", "id:23"),
        ),
        (
            "query_job_submissions",
            {"fields": "id", "where": "jobOrder.id=11"},
            "query/JobSubmission",
            ("where", "jobOrder.id=11"),
        ),
        (
            "get_job_submission_history",
            {"job_submission_id": 17, "fields": "id"},
            "query/JobSubmissionHistory",
            ("where", "jobSubmission.id=17"),
        ),
        (
            "query_notes",
            {"candidate_id": 23, "fields": "id"},
            "query/Note",
            ("where", "personReference.id=23"),
        ),
    ],
)
def test_capture_reads_bound_the_provider_request_and_rows(
    method_name: str,
    kwargs: dict[str, object],
    expected_path: str,
    selector: tuple[str, str],
) -> None:
    service = object.__new__(BullhornService)
    calls: list[tuple[str, dict[str, object]]] = []

    def fake_request(
        method: str,
        path: str,
        *,
        params: dict[str, object],
    ) -> dict[str, object]:
        assert method == "GET"
        calls.append((path, params))
        return {
            "total": 10_000,
            "data": [{"id": index} for index in range(5)],
        }

    service._request = fake_request  # type: ignore[method-assign]

    rows = getattr(service, method_name)(**kwargs, limit=5)

    assert len(rows) == 5
    assert calls == [
        (
            expected_path,
            {"fields": "id", "start": 0, "count": 5, selector[0]: selector[1]},
        )
    ]


class _RelationalCaptureService:
    def __init__(self, *, returned_candidate_id: int = 51) -> None:
        self.returned_candidate_id = returned_candidate_id
        self.job_query: tuple[str, int] | None = None
        self.submission_where: tuple[str, int] | None = None

    def ping(self) -> dict[str, int]:
        return {"sessionExpires": 1_700_000_000_000}

    def get_status_list(self) -> list[str]:
        return ["Open"]

    def get_entitlements(self, _entity: str) -> list[str]:
        return ["GET"]

    def search_job_orders(self, *, fields: str, query: str, limit: int):
        assert fields == capture_tool.JOB_ORDER_FIELDS
        self.job_query = (query, limit)
        return [{"id": 41, "title": "Dedicated test role", "isOpen": True}]

    def query_job_submissions(self, *, fields: str, where: str, limit: int):
        assert fields == capture_tool.JOB_SUBMISSION_FIELDS
        self.submission_where = (where, limit)
        return [
            {
                "id": 61,
                "candidate": {"id": 51},
                "jobOrder": {"id": 41},
                "status": "Submitted",
            }
        ]

    def search_candidates(self, *, fields: str, query: str, limit: int):
        assert fields == capture_tool.CANDIDATE_FIELDS
        assert (query, limit) == ("id:51", 1)
        return [{"id": self.returned_candidate_id, "name": "Test Person"}]

    def query_notes(self, **_kwargs: object) -> list:
        return []

    def list_file_attachments(self, **_kwargs: object) -> list:
        return []

    def get_job_submission_history(self, **_kwargs: object) -> list:
        return []


def test_explicit_job_capture_preserves_serialized_submission_relationship(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    service = _RelationalCaptureService()
    monkeypatch.setattr(
        capture_tool,
        "_capture_event_subscription",
        lambda *_args, **_kwargs: None,
    )

    capture_tool._capture_payloads(
        service,  # type: ignore[arg-type]
        tmp_path,
        candidate_id=None,
        job_order_id=41,
        max_rows=3,
        scrub_key=SCRUB_KEY,
        require_event=False,
        event_wait_seconds=120,
    )

    assert service.job_query == ("id:41", 1)
    assert service.submission_where == ("jobOrder.id=41", 3)
    jobs = json.loads((tmp_path / "job_orders.json").read_text(encoding="utf-8"))
    submissions = json.loads(
        (tmp_path / "job_submissions.json").read_text(encoding="utf-8")
    )
    captured_job_ids = {job["id"] for job in jobs}
    assert submissions
    assert all(
        submission["jobOrder"]["id"] in captured_job_ids
        for submission in submissions
    )


def test_wrong_candidate_search_result_fails_generically_before_candidate_file(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    wrong_id = 999_123
    service = _RelationalCaptureService(returned_candidate_id=wrong_id)
    monkeypatch.setattr(
        capture_tool,
        "_capture_event_subscription",
        lambda *_args, **_kwargs: None,
    )

    with pytest.raises(RuntimeError, match="did not return the requested candidate") as caught:
        capture_tool._capture_payloads(
            service,  # type: ignore[arg-type]
            tmp_path,
            candidate_id=51,
            job_order_id=41,
            max_rows=3,
            scrub_key=SCRUB_KEY,
            require_event=False,
            event_wait_seconds=120,
        )

    assert str(wrong_id) not in str(caught.value)
    assert not (tmp_path / "candidate.json").exists()


@pytest.mark.parametrize("max_rows", [0, -1, capture_tool.MAX_CAPTURE_ROWS + 1])
def test_invalid_capture_limit_fails_before_service_or_files(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    max_rows: int,
) -> None:
    def unexpected_service_build(_credentials: object) -> object:
        raise AssertionError("invalid limit must fail before provider setup")

    monkeypatch.setattr(capture_tool, "_build_service", unexpected_service_build)
    target = tmp_path / "bullhorn_recorded"

    with pytest.raises(ValueError, match="max rows must be between"):
        capture_tool.capture(
            target,
            candidate_id=None,
            job_order_id=None,
            max_rows=max_rows,
            credentials=CREDENTIALS,
        )

    assert not target.exists()


@pytest.mark.parametrize(
    "malformed",
    [
        "candidate-private-id@example.invalid",
        "123-not-an-id",
        "²",
        "１２３",
        -7,
        True,
        1.5,
        10**21,
    ],
)
def test_malformed_provider_ids_fail_without_echoing_raw_values(
    malformed: object,
) -> None:
    with pytest.raises(RuntimeError, match="malformed candidate ID") as raised:
        capture_tool._provider_id(malformed, field="candidate ID")

    assert str(malformed) not in str(raised.value)


class _EventService:
    def __init__(
        self,
        *,
        create_error: BaseException | None = None,
        poll_error: BaseException | None = None,
        delete_error: Exception | None = None,
        delete_result: object = None,
    ) -> None:
        self.create_error = create_error
        self.poll_error = poll_error
        self.delete_error = delete_error
        self.delete_result = (
            {"result": True} if delete_result is None else delete_result
        )
        self.create_calls: list[str] = []
        self.delete_calls: list[str] = []

    def create_subscription(
        self,
        *,
        subscription_id: str,
        entity_names: list[str],
    ) -> dict[str, object]:
        assert entity_names == list(capture_tool.SUBSCRIBED_ENTITIES)
        self.create_calls.append(subscription_id)
        if self.create_error is not None:
            raise self.create_error
        return {
            "lastRequestId": 0,
            "subscriptionId": subscription_id,
            "createdOn": 1_700_000_000_000,
            "jmsSelector": "JMSType='ENTITY' AND BhCorpId=12345",
        }

    def poll_events(
        self,
        *,
        subscription_id: str,
        max_events: int,
    ) -> dict[str, object]:
        assert subscription_id == self.create_calls[-1]
        assert max_events == 10
        if self.poll_error is not None:
            raise self.poll_error
        return {
            "requestId": 777,
            "events": [
                {
                    "eventId": "ID:JBM-40000517",
                    "eventType": "ENTITY",
                    "eventTimestamp": 1_495_559_294_820,
                    "eventMetadata": {
                        "PERSON_ID": "1314",
                        "TRANSACTION_ID": "private-transaction-id",
                    },
                    "entityName": "Candidate",
                    "entityId": 8592,
                    "entityEventType": "UPDATED",
                    "updatedProperties": ["status", "email", "customPrivateField"],
                }
            ],
        }

    def delete_subscription(self, *, subscription_id: str) -> object:
        self.delete_calls.append(subscription_id)
        if self.delete_error is not None:
            raise self.delete_error
        return self.delete_result


class _FakeClock:
    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


class _SequencedEventService(_EventService):
    def __init__(self, polls: list[dict[str, object]], **kwargs: object) -> None:
        super().__init__(**kwargs)
        self.polls = list(polls)
        self.poll_calls = 0

    def poll_events(
        self,
        *,
        subscription_id: str,
        max_events: int,
    ) -> dict[str, object]:
        assert subscription_id == self.create_calls[-1]
        assert max_events == 10
        self.poll_calls += 1
        return self.polls.pop(0) if self.polls else {"requestId": 0, "events": []}


def _official_event_poll() -> dict[str, object]:
    return {
        "requestId": 2,
        "events": [
            {
                "eventId": "evt-test",
                "eventType": "ENTITY",
                "eventTimestamp": 1_700_000_000_000,
                "entityName": "Candidate",
                "entityId": 42,
                "entityEventType": "UPDATED",
                "updatedProperties": ["status"],
            }
        ],
    }


def test_require_event_waits_once_then_captures_official_event(tmp_path: Path) -> None:
    clock = _FakeClock()
    service = _SequencedEventService(
        [{"requestId": 1, "events": []}, _official_event_poll()]
    )

    capture_tool._capture_event_subscription(
        service,
        tmp_path,
        scrub_key=SCRUB_KEY,
        require_event=True,
        event_wait_seconds=20,
        monotonic=clock.monotonic,
        sleep=clock.sleep,
    )

    assert clock.sleeps == [capture_tool.EVENT_POLL_INTERVAL_SECONDS]
    assert service.poll_calls == 2
    assert service.delete_calls == service.create_calls
    serialized = json.loads((tmp_path / "event_poll.json").read_text(encoding="utf-8"))
    assert serialized["events"][0]["eventType"] == "ENTITY"
    assert serialized["events"][0]["entityEventType"] == "UPDATED"


def test_require_event_timeout_is_exactly_bounded_and_cleans_owned_id(
    tmp_path: Path,
) -> None:
    clock = _FakeClock()
    service = _SequencedEventService([])

    with pytest.raises(TimeoutError, match="bounded deadline"):
        capture_tool._capture_event_subscription(
            service,
            tmp_path,
            scrub_key=SCRUB_KEY,
            require_event=True,
            event_wait_seconds=25,
            monotonic=clock.monotonic,
            sleep=clock.sleep,
        )

    assert clock.now == 25
    assert clock.sleeps == [10, 10, 5]
    assert service.poll_calls == 4
    assert service.delete_calls == service.create_calls
    assert not (tmp_path / "event_poll.json").exists()


def test_require_event_rejects_legacy_envelope_and_cleans_owned_id(
    tmp_path: Path,
) -> None:
    service = _SequencedEventService(
        [
            {
                "requestId": 1,
                "events": [
                    {
                        "eventType": "UPDATED",
                        "entityName": "Candidate",
                        "entityId": 42,
                    }
                ],
            }
        ]
    )

    with pytest.raises(RuntimeError, match="malformed official event envelope"):
        capture_tool._capture_event_subscription(
            service,
            tmp_path,
            scrub_key=SCRUB_KEY,
            require_event=True,
        )

    assert service.delete_calls == service.create_calls
    assert not (tmp_path / "event_poll.json").exists()


@pytest.mark.parametrize(
    "invalid_wait",
    [True, False, 1.0, 0, capture_tool.MAX_EVENT_WAIT_SECONDS + 1],
)
def test_invalid_programmatic_event_wait_fails_before_service_or_files(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    invalid_wait: object,
) -> None:
    target = tmp_path / "bullhorn_recorded"
    monkeypatch.setattr(
        capture_tool,
        "_build_service",
        lambda _credentials: (_ for _ in ()).throw(
            AssertionError("service must not be built")
        ),
    )

    with pytest.raises(ValueError, match="event wait must be an integer"):
        capture_tool.capture(
            target,
            candidate_id=None,
            job_order_id=None,
            max_rows=5,
            credentials=CREDENTIALS,
            require_event=True,
            event_wait_seconds=invalid_wait,  # type: ignore[arg-type]
        )

    assert not target.exists()
    assert not list(tmp_path.glob(".bullhorn_recorded.capture-*"))


def test_timeout_primary_survives_cleanup_failure_without_secret_echo(
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    clock = _FakeClock()
    service = _SequencedEventService(
        [],
        delete_error=RuntimeError("cleanup-provider-secret"),
    )

    with pytest.raises(TimeoutError, match="bounded deadline"):
        capture_tool._capture_event_subscription(
            service,
            tmp_path,
            scrub_key=SCRUB_KEY,
            require_event=True,
            event_wait_seconds=1,
            monotonic=clock.monotonic,
            sleep=clock.sleep,
        )

    captured = capsys.readouterr()
    visible = captured.out + captured.err
    assert "cleanup-provider-secret" not in visible


def test_event_capture_uses_unique_ids_and_deletes_only_its_own(tmp_path: Path) -> None:
    service = _EventService()

    capture_tool._capture_event_subscription(service, tmp_path, scrub_key=SCRUB_KEY)
    capture_tool._capture_event_subscription(service, tmp_path, scrub_key=SCRUB_KEY)

    assert len(set(service.create_calls)) == 2
    assert service.delete_calls == service.create_calls
    assert all(
        value.startswith("TaaliFixtureCapture-") and value.endswith("-DELETE-ME")
        for value in service.create_calls
    )
    created = json.loads(
        (tmp_path / "event_subscription_create.json").read_text(encoding="utf-8")
    )
    event = json.loads(
        (tmp_path / "event_poll.json").read_text(encoding="utf-8")
    )["events"][0]
    assert created["createdOn"] == 946_684_800_000
    assert event["eventTimestamp"] == 946_684_800_000
    assert event["eventType"] == "ENTITY"
    assert event["entityEventType"] == "UPDATED"
    assert normalize_event_type(event) == "UPDATED"
    assert event["entityName"] == "Candidate"
    assert event["updatedProperties"][:2] == ["status", "email"]
    assert event["updatedProperties"][2].startswith("text-")


def test_definitive_create_rejection_does_not_issue_delete(tmp_path: Path) -> None:
    rejection = BullhornApiError("create rejected", status_code=400)
    service = _EventService(create_error=rejection)

    with pytest.raises(BullhornApiError) as raised:
        capture_tool._capture_event_subscription(
            service,
            tmp_path,
            scrub_key=SCRUB_KEY,
        )

    assert raised.value is rejection
    assert service.delete_calls == []


def test_ambiguous_create_failure_attempts_cleanup_without_masking_primary(
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    primary = BullhornApiError("redacted ambiguous create failure")
    service = _EventService(
        create_error=primary,
        delete_error=RuntimeError("delete-secret-must-not-be-printed"),
    )

    with pytest.raises(BullhornApiError) as raised:
        capture_tool._capture_event_subscription(
            service,
            tmp_path,
            scrub_key=SCRUB_KEY,
        )

    captured = capsys.readouterr()
    visible = captured.out + captured.err
    assert raised.value is primary
    assert service.delete_calls == service.create_calls
    assert "delete-secret-must-not-be-printed" not in visible
    assert "RuntimeError" in visible


def test_successful_capture_requires_confirmed_subscription_cleanup(
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    service = _EventService(
        delete_error=RuntimeError("provider-response-secret-must-not-be-printed")
    )

    with pytest.raises(RuntimeError, match="did not confirm subscription cleanup"):
        capture_tool._capture_event_subscription(
            service,
            tmp_path,
            scrub_key=SCRUB_KEY,
        )

    captured = capsys.readouterr()
    visible = captured.out + captured.err
    assert "provider-response-secret-must-not-be-printed" not in visible
    assert "RuntimeError" in visible


def test_delete_false_is_not_treated_as_confirmed_cleanup(
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    service = _EventService(delete_result={"result": False})

    with pytest.raises(RuntimeError, match="did not confirm subscription cleanup"):
        capture_tool._capture_event_subscription(
            service,
            tmp_path,
            scrub_key=SCRUB_KEY,
        )

    captured = capsys.readouterr()
    assert "Bullhorn did not confirm subscription deletion" not in (
        captured.out + captured.err
    )
    assert "RuntimeError" in captured.err


def test_delete_false_does_not_mask_ambiguous_create_failure(
    tmp_path: Path,
) -> None:
    primary = BullhornApiError("redacted ambiguous create failure")
    service = _EventService(
        create_error=primary,
        delete_result={"result": False},
    )

    with pytest.raises(BullhornApiError) as raised:
        capture_tool._capture_event_subscription(
            service,
            tmp_path,
            scrub_key=SCRUB_KEY,
        )

    assert raised.value is primary
    assert service.delete_calls == service.create_calls


@pytest.mark.parametrize("failure_stage", ["dump", "poll"])
def test_post_create_failure_cleans_up_without_masking_primary(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
    failure_stage: str,
) -> None:
    primary = ValueError("primary capture failure")
    service = _EventService(
        poll_error=primary if failure_stage == "poll" else None,
        delete_error=RuntimeError("cleanup-secret-must-not-be-printed"),
    )
    if failure_stage == "dump":
        monkeypatch.setattr(
            capture_tool,
            "_dump",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(primary),
        )

    with pytest.raises(ValueError) as raised:
        capture_tool._capture_event_subscription(
            service,
            tmp_path,
            scrub_key=SCRUB_KEY,
        )

    captured = capsys.readouterr()
    assert raised.value is primary
    assert service.delete_calls == service.create_calls
    assert "cleanup-secret-must-not-be-printed" not in captured.out + captured.err
    assert "RuntimeError" in captured.err


def test_capture_removes_current_run_partial_files_on_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    target = tmp_path / "bullhorn_recorded"
    monkeypatch.setattr(
        capture_tool,
        "_build_service",
        lambda _credentials, **_kwargs: object(),
    )

    def fail_after_partial(_service: object, staging: Path, **_kwargs: object) -> None:
        (staging / "partial.json").write_text("{}", encoding="utf-8")
        raise RuntimeError("provider failed after an earlier fixture")

    monkeypatch.setattr(capture_tool, "_capture_payloads", fail_after_partial)

    with pytest.raises(RuntimeError, match="provider failed"):
        capture_tool.capture(
            target,
            candidate_id=None,
            job_order_id=None,
            max_rows=5,
            credentials=CREDENTIALS,
            token_state_file=tmp_path / "rotated-token.json",
        )

    assert not target.exists()
    assert not list(tmp_path.glob(".bullhorn_recorded.capture-*"))


def test_capture_atomically_publishes_only_the_complete_staging_directory(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    target = tmp_path / "bullhorn_recorded"
    observed: dict[str, int] = {}
    monkeypatch.setattr(
        capture_tool,
        "_build_service",
        lambda _credentials, **_kwargs: object(),
    )

    def complete_capture(_service: object, staging: Path, **kwargs: object) -> None:
        observed["staging_mode"] = staging.stat().st_mode & 0o777
        capture_tool._dump(
            staging,
            "ping",
            {"sessionExpires": 1_234_567},
            scrub_key=kwargs["scrub_key"],
        )

    monkeypatch.setattr(capture_tool, "_capture_payloads", complete_capture)

    capture_tool.capture(
        target,
        candidate_id=None,
        job_order_id=None,
        max_rows=5,
        credentials=CREDENTIALS,
        token_state_file=tmp_path / "rotated-token.json",
    )

    assert observed["staging_mode"] == 0o700
    assert (target / "ping.json").is_file()
    assert not list(tmp_path.glob(".bullhorn_recorded.capture-*"))


def test_capture_never_replaces_preexisting_user_fixtures(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    target = tmp_path / "bullhorn_recorded"
    target.mkdir()
    marker = target / "keep-me.json"
    marker.write_text('{"owned":"by-user"}', encoding="utf-8")

    def unexpected_service_build(_credentials: object) -> object:
        raise AssertionError("live service must not start when output already exists")

    monkeypatch.setattr(capture_tool, "_build_service", unexpected_service_build)

    with pytest.raises(FileExistsError, match="Refusing to replace"):
        capture_tool.capture(
            target,
            candidate_id=None,
            job_order_id=None,
            max_rows=5,
            credentials=CREDENTIALS,
        )

    assert marker.read_text(encoding="utf-8") == '{"owned":"by-user"}'
