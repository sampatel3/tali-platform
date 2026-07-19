#!/usr/bin/env python
"""Record sanitized Bullhorn fixtures from a real client instance.

Implements BULLHORN_BUILD_PLAN §0.2 (recorded-fixture validation): once client
credentials arrive, exercise the READ endpoints our integration uses against the
real instance, scrub all PII + tokens, and write JSON fixtures shaped the way the
fake server (tests/fakes/bullhorn_*) and contract tests expect. Those fixtures
keep the fake honest against at least one real instance.

SAFETY
------
* Read-only EXCEPT the event subscription, which we create, poll boundedly, and
  delete (it is the only way to record the destructive event-queue shape).
  Nothing else writes to the client's data.
* Refuses to run without an explicit ``--allow-live`` flag.
* Every recorded record is scrubbed: names/emails/phones become capture-local
  fakes; any token/secret/BhRestToken query param or field is redacted.
* Every rotated single-use refresh token is atomically retained in the required
  mode-0600 ``--token-state-file`` before its paired access token can be used.
* Identifiers and categorical values remain internally consistent within one
  capture, but are keyed with a random value that is never written to disk.

USAGE
-----
    python scripts/bullhorn_capture_fixtures.py --allow-live \
        [--credentials-file /private/path/bullhorn-connect.json] \
        --token-state-file /private/path/bullhorn-rotated-token.json \
        [--out tests/fixtures/bullhorn_recorded] \
        [--candidate-id N] [--job-order-id N] [--max 5]

Prefer a mode-0600 JSON credentials file with the keys ``username``,
``client_id``, ``client_secret``, and ``password``. The legacy environment
fallback remains available (never put credentials in CLI args):
    BULLHORN_USERNAME, BULLHORN_CLIENT_ID, BULLHORN_CLIENT_SECRET, BULLHORN_PASSWORD

This script is an operator tool, not part of the app or its test suite.
"""
from __future__ import annotations

import argparse
from collections.abc import Callable, Mapping
import json
import os
import re
import secrets
import shutil
import stat
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

# --- allow running from a repo checkout without installing the package --------
_BACKEND = Path(__file__).resolve().parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from app.components.integrations.bullhorn.auth import BullhornAuth  # noqa: E402
from app.components.integrations.bullhorn.errors import (  # noqa: E402
    BullhornApiError,
    BullhornAuthError,
    redact_exc,
)
from app.components.integrations.bullhorn.event_handlers import (  # noqa: E402
    SUBSCRIBED_ENTITIES,
    normalize_event_type,
)
from app.components.integrations.bullhorn.service import BullhornService  # noqa: E402
from app.components.integrations.bullhorn.sync_service import (  # noqa: E402
    JOB_ORDER_FIELDS,
    JOB_SUBMISSION_FIELDS,
)
from scripts.bullhorn_capture_scrub import (  # noqa: E402
    assert_scrubbed_safe as _assert_scrubbed_safe,
    scrub as _scrub,
)

# Field lists the integration reads — kept in sync with the sync modules so the
# recorded shapes match what production actually requests.
CANDIDATE_FIELDS = "id,firstName,lastName,name,email,phone,mobile,occupation,address,dateLastModified"
FILE_ATTACHMENT_FIELDS = "id,name,type,contentType,dateAdded"
SUBMISSION_HISTORY_FIELDS = "id,status,dateAdded,modifyingUser"
NOTE_FIELDS = "id,comments,action,dateAdded,commentingPerson"
MAX_CAPTURE_ROWS = 100
MAX_EVENT_WAIT_SECONDS = 300
EVENT_POLL_INTERVAL_SECONDS = 10

_CREDENTIAL_KEYS = (
    "BULLHORN_USERNAME",
    "BULLHORN_CLIENT_ID",
    "BULLHORN_CLIENT_SECRET",
    "BULLHORN_PASSWORD",
)
_CREDENTIAL_FIELDS = {
    "BULLHORN_USERNAME": "username",
    "BULLHORN_CLIENT_ID": "client_id",
    "BULLHORN_CLIENT_SECRET": "client_secret",
    "BULLHORN_PASSWORD": "password",
}


def _validate_credentials(credentials: Mapping[str, object]) -> dict[str, str]:
    if set(credentials) != set(_CREDENTIAL_FIELDS.values()):
        raise SystemExit("Credentials file must contain exactly the required fields")
    validated: dict[str, str] = {}
    for field in _CREDENTIAL_FIELDS.values():
        value = credentials[field]
        if (
            not isinstance(value, str)
            or not value
            or len(value) > 4_096
            or "\r" in value
            or "\n" in value
        ):
            raise SystemExit(f"Invalid Bullhorn credential field: {field}")
        validated[field] = value
    return validated


def _load_credentials(credentials_file: Path | None) -> dict[str, str]:
    if credentials_file is None:
        missing = [name for name in _CREDENTIAL_KEYS if not os.environ.get(name)]
        if missing:
            raise SystemExit(f"Missing required env vars: {', '.join(missing)}")
        return _validate_credentials(
            {
                field: os.environ[environment_name]
                for environment_name, field in _CREDENTIAL_FIELDS.items()
            }
        )

    descriptor = -1
    try:
        flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
        nofollow = getattr(os, "O_NOFOLLOW", 0)
        flags |= nofollow
        descriptor = os.open(credentials_file, flags)
        file_stat = os.fstat(descriptor)
        if not nofollow:
            path_stat = os.lstat(credentials_file)
            if (
                stat.S_ISLNK(path_stat.st_mode)
                or (path_stat.st_dev, path_stat.st_ino)
                != (file_stat.st_dev, file_stat.st_ino)
            ):
                raise ValueError
        if not stat.S_ISREG(file_stat.st_mode) or file_stat.st_size > 32_768:
            raise ValueError
        if stat.S_IMODE(file_stat.st_mode) & 0o077:
            raise PermissionError
        with os.fdopen(descriptor, encoding="utf-8") as credential_stream:
            descriptor = -1
            raw = json.load(credential_stream)
    except PermissionError:
        raise SystemExit("Credentials file must not be accessible by group or others") from None
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
        raise SystemExit("Could not read a valid Bullhorn credentials file") from None
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    if not isinstance(raw, dict):
        raise SystemExit("Credentials file must contain a JSON object")
    return _validate_credentials(raw)


def _persist_token_state(
    path: Path,
    *,
    refresh_token: str,
    rest_url: str | None = None,
) -> None:
    """Atomically retain Bullhorn's rotated single-use refresh-token chain."""
    if (
        not isinstance(refresh_token, str)
        or not refresh_token
        or len(refresh_token) > 16_384
        or "\r" in refresh_token
        or "\n" in refresh_token
        or (
            rest_url is not None
            and (
                not isinstance(rest_url, str)
                or not rest_url
                or len(rest_url) > 16_384
                or "\r" in rest_url
                or "\n" in rest_url
            )
        )
    ):
        raise RuntimeError("Bullhorn returned invalid rotated token state")
    path = Path(path)
    path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    if path.exists() or path.is_symlink():
        existing = path.lstat()
        if (
            not stat.S_ISREG(existing.st_mode)
            or stat.S_ISLNK(existing.st_mode)
            or stat.S_IMODE(existing.st_mode) & 0o077
        ):
            raise RuntimeError("Could not persist private Bullhorn token state")
    descriptor = -1
    temporary_path: Path | None = None
    try:
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{path.name}.rotate-",
            dir=path.parent,
        )
        temporary_path = Path(temporary_name)
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as token_stream:
            descriptor = -1
            json.dump(
                {"refresh_token": refresh_token, "rest_url": rest_url},
                token_stream,
                separators=(",", ":"),
            )
            token_stream.flush()
            os.fsync(token_stream.fileno())
        os.replace(temporary_path, path)
        temporary_path = None
        path.chmod(0o600)
    except Exception:
        raise RuntimeError("Could not persist private Bullhorn token state") from None
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def _build_service(
    credentials: Mapping[str, str] | None = None,
    *,
    token_state_file: Path,
) -> BullhornService:
    def _persist(*, refresh_token: str, rest_url: str | None = None) -> None:
        _persist_token_state(
            token_state_file,
            refresh_token=refresh_token,
            rest_url=rest_url,
        )

    resolved = (
        _validate_credentials(credentials)
        if credentials is not None
        else _load_credentials(None)
    )

    auth = BullhornAuth(
        username=resolved["username"],
        client_id=resolved["client_id"],
        client_secret=resolved["client_secret"],
        refresh_token=None,
        persist_tokens=_persist,
        password=resolved["password"],
    )
    auth.authorize_with_password()
    return BullhornService(auth, client_id=resolved["client_id"])


def _dump(out: Path, name: str, payload: Any, *, scrub_key: bytes) -> None:
    scrubbed = _scrub(payload, scrub_key=scrub_key)
    _assert_scrubbed_safe(scrubbed)
    serialized = json.dumps(scrubbed, indent=2, sort_keys=True)
    path = out / f"{name}.json"
    path.write_text(serialized, encoding="utf-8")
    print(f"  prepared {path.name}")


def _is_definitive_create_rejection(exc: BaseException) -> bool:
    if isinstance(exc, BullhornAuthError):
        return True
    if not isinstance(exc, BullhornApiError) or exc.status_code is None:
        return False
    return 400 <= exc.status_code < 500 and exc.status_code not in {408, 425, 429}


def _delete_owned_subscription(
    service: BullhornService,
    subscription_id: str,
    *,
    required: bool,
) -> None:
    try:
        result = service.delete_subscription(subscription_id=subscription_id)
        if not isinstance(result, dict) or result.get("result") is not True:
            raise RuntimeError("Bullhorn did not confirm subscription deletion")
        print("  deleted throwaway subscription")
    except Exception as exc:  # noqa: BLE001
        print(
            f"  WARNING: could not delete owned subscription "
            f"{subscription_id}: {redact_exc(exc)}",
            file=sys.stderr,
        )
        if required:
            raise RuntimeError(
                "Bullhorn fixture capture did not confirm subscription cleanup"
            ) from None


def _capture_event_subscription(
    service: BullhornService,
    out: Path,
    *,
    scrub_key: bytes,
    require_event: bool = False,
    event_wait_seconds: int = 120,
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    event_wait_seconds = _validate_event_wait_seconds(event_wait_seconds)
    subscription_id = f"TaaliFixtureCapture-{secrets.token_hex(8)}-DELETE-ME"
    try:
        created = service.create_subscription(
            subscription_id=subscription_id,
            entity_names=list(SUBSCRIBED_ENTITIES),
        )
    except BaseException as exc:
        # A timeout or connection loss can occur after Bullhorn applied the PUT.
        # This random ID cannot overlap an app subscription, so best-effort
        # deletion is safe on ambiguous outcomes. A definite 4xx/auth rejection
        # cannot have created it and needs no follow-up write.
        if not _is_definitive_create_rejection(exc):
            _delete_owned_subscription(
                service,
                subscription_id,
                required=False,
            )
        raise

    try:
        _dump(
            out,
            "event_subscription_create",
            created,
            scrub_key=scrub_key,
        )
        deadline = monotonic() + event_wait_seconds
        while True:
            poll = service.poll_events(subscription_id=subscription_id, max_events=10)
            poll_events = poll.get("events") if isinstance(poll, dict) else None
            if not require_event or (isinstance(poll_events, list) and poll_events):
                if require_event:
                    for event in poll_events:
                        if (
                            not isinstance(event, dict)
                            or event.get("eventType") != "ENTITY"
                            or normalize_event_type(event) is None
                        ):
                            raise RuntimeError(
                                "Bullhorn returned a malformed official event envelope"
                            )
                _dump(out, "event_poll", poll, scrub_key=scrub_key)
                break
            remaining = deadline - monotonic()
            if remaining <= 0:
                raise TimeoutError(
                    "No Bullhorn test event arrived before the bounded deadline"
                )
            sleep(min(EVENT_POLL_INTERVAL_SECONDS, remaining))
    except BaseException:
        # Preserve the provider/scrub failure if cleanup also fails.
        _delete_owned_subscription(service, subscription_id, required=False)
        raise
    _delete_owned_subscription(service, subscription_id, required=True)


def _provider_id(value: object, *, field: str) -> int:
    """Normalize a provider ID without ever echoing its raw value in errors."""
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise RuntimeError(f"Bullhorn returned a malformed {field}")
    rendered = str(value)
    if re.fullmatch(r"[0-9]{1,20}", rendered) is None:
        raise RuntimeError(f"Bullhorn returned a malformed {field}")
    try:
        normalized = int(rendered)
    except (TypeError, ValueError, OverflowError):
        raise RuntimeError(f"Bullhorn returned a malformed {field}") from None
    if normalized <= 0:
        raise RuntimeError(f"Bullhorn returned a malformed {field}")
    return normalized


def _validate_max_rows(max_rows: object) -> int:
    if type(max_rows) is not int or not 1 <= max_rows <= MAX_CAPTURE_ROWS:
        raise ValueError(f"max rows must be between 1 and {MAX_CAPTURE_ROWS}")
    return max_rows


def _capture_payloads(
    service: BullhornService,
    out: Path,
    *,
    candidate_id: int | None,
    job_order_id: int | None,
    max_rows: int,
    scrub_key: bytes,
    require_event: bool,
    event_wait_seconds: int,
) -> None:
    max_rows = _validate_max_rows(max_rows)
    requested_job_order_id = (
        _provider_id(job_order_id, field="job order ID")
        if job_order_id is not None
        else None
    )
    print("ping / meta")
    _dump(out, "ping", service.ping(), scrub_key=scrub_key)
    _dump(out, "status_list", service.get_status_list(), scrub_key=scrub_key)
    _dump(
        out,
        "entitlements",
        {
            "Candidate": service.get_entitlements("Candidate"),
            "JobOrder": service.get_entitlements("JobOrder"),
            "JobSubmission": service.get_entitlements("JobSubmission"),
            "Note": service.get_entitlements("Note"),
        },
        scrub_key=scrub_key,
    )

    print("jobOrders")
    job_query = (
        f"id:{requested_job_order_id}"
        if requested_job_order_id is not None
        else "isOpen:true"
    )
    job_limit = 1 if requested_job_order_id is not None else max_rows
    job_orders = service.search_job_orders(
        fields=JOB_ORDER_FIELDS,
        query=job_query,
        limit=job_limit,
    )
    captured_job_ids = {
        _provider_id(row.get("id"), field="job order ID")
        for row in job_orders
    }
    if requested_job_order_id is not None and captured_job_ids != {
        requested_job_order_id
    }:
        raise RuntimeError("Bullhorn did not return the requested job order")
    _dump(out, "job_orders", job_orders, scrub_key=scrub_key)

    print("jobSubmissions")
    anchor_job_order_id = (
        requested_job_order_id
        if requested_job_order_id is not None
        else (
            _provider_id(job_orders[0].get("id"), field="job order ID")
            if job_orders
            else None
        )
    )
    submissions = (
        service.query_job_submissions(
            fields=JOB_SUBMISSION_FIELDS,
            where=f"jobOrder.id={anchor_job_order_id}",
            limit=max_rows,
        )
        if anchor_job_order_id is not None
        else []
    )
    for submission in submissions:
        job_reference = submission.get("jobOrder")
        if not isinstance(job_reference, dict):
            raise RuntimeError("Bullhorn returned a malformed job order reference")
        linked_job_id = _provider_id(
            job_reference.get("id"),
            field="job order reference ID",
        )
        if linked_job_id not in captured_job_ids:
            raise RuntimeError("Bullhorn returned an unanchored job submission")
    _dump(out, "job_submissions", submissions, scrub_key=scrub_key)

    # Resolve a candidate id to sample the candidate + files + notes reads.
    cand_id: object = candidate_id
    if cand_id is None and submissions:
        cand_ref = submissions[0].get("candidate")
        if not isinstance(cand_ref, dict):
            raise RuntimeError("Bullhorn returned a malformed candidate reference")
        cand_id = cand_ref.get("id")
    if cand_id is not None:
        normalized_candidate_id = _provider_id(cand_id, field="candidate ID")
        print("candidate sample (record, notes, files)")
        candidate_rows = service.search_candidates(
            fields=CANDIDATE_FIELDS,
            query=f"id:{normalized_candidate_id}",
            limit=1,
        )
        if len(candidate_rows) != 1 or _provider_id(
            candidate_rows[0].get("id"),
            field="candidate ID",
        ) != normalized_candidate_id:
            raise RuntimeError("Bullhorn did not return the requested candidate")
        _dump(
            out,
            "candidate",
            candidate_rows,
            scrub_key=scrub_key,
        )
        _dump(
            out,
            "notes",
            service.query_notes(
                candidate_id=normalized_candidate_id,
                fields=NOTE_FIELDS,
                limit=max_rows,
            ),
            scrub_key=scrub_key,
        )
        _dump(
            out,
            "file_attachments",
            service.list_file_attachments(
                candidate_id=normalized_candidate_id,
                fields=FILE_ATTACHMENT_FIELDS,
            )[:max_rows],
            scrub_key=scrub_key,
        )
    else:
        print("  (no candidate id available — skipping notes/files)")

    if submissions:
        sub_id = _provider_id(
            submissions[0].get("id"),
            field="job submission ID",
        )
        print("jobSubmissionHistory sample")
        _dump(
            out,
            "job_submission_history",
            service.get_job_submission_history(
                job_submission_id=sub_id,
                fields=SUBMISSION_HISTORY_FIELDS,
                limit=max_rows,
            ),
            scrub_key=scrub_key,
        )

    # Event subscription: the ONE write. Create a throwaway subscription, poll
    # once by default or repeatedly until the bounded --require-event deadline,
    # then delete it. Its name makes ownership obvious if cleanup ever fails.
    print("event subscription (create → poll → delete)")
    _capture_event_subscription(
        service,
        out,
        scrub_key=scrub_key,
        require_event=require_event,
        event_wait_seconds=event_wait_seconds,
    )


def capture(
    out: Path,
    *,
    candidate_id: int | None,
    job_order_id: int | None,
    max_rows: int,
    credentials: Mapping[str, str] | None = None,
    token_state_file: Path | None = None,
    require_event: bool = False,
    event_wait_seconds: int = 120,
) -> None:
    """Capture privately, then atomically publish a complete fixture set."""
    max_rows = _validate_max_rows(max_rows)
    event_wait_seconds = _validate_event_wait_seconds(event_wait_seconds)
    if candidate_id is not None:
        candidate_id = _provider_id(candidate_id, field="candidate ID")
    if job_order_id is not None:
        job_order_id = _provider_id(job_order_id, field="job order ID")
    if out.exists():
        raise FileExistsError(
            "Refusing to replace an existing Bullhorn fixture output directory"
        )
    if token_state_file is None:
        raise ValueError("A private token-state file is required for live capture")
    token_state_file = Path(token_state_file)
    resolved_out = out.resolve(strict=False)
    resolved_token_state = token_state_file.resolve(strict=False)
    if resolved_token_state == resolved_out or resolved_out in resolved_token_state.parents:
        raise ValueError("Token-state file must be outside the fixture output directory")
    out.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(
        tempfile.mkdtemp(
            prefix=f".{out.name}.capture-",
            dir=out.parent,
        )
    )
    try:
        service = _build_service(
            credentials,
            token_state_file=token_state_file,
        )
        _capture_payloads(
            service,
            staging,
            candidate_id=candidate_id,
            job_order_id=job_order_id,
            max_rows=max_rows,
            scrub_key=secrets.token_bytes(32),
            require_event=require_event,
            event_wait_seconds=event_wait_seconds,
        )
        if out.exists():
            raise FileExistsError(
                "Bullhorn fixture output appeared during capture; refusing to replace it"
            )
        staging.rename(out)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    print(f"\nDone. Complete sanitized fixtures published in {out}")


def _resolve_output_path(raw_path: str) -> Path:
    output = Path(raw_path)
    return output if output.is_absolute() else _BACKEND / output


def _max_rows_argument(raw_value: str) -> int:
    try:
        return _validate_max_rows(int(raw_value))
    except (TypeError, ValueError):
        raise argparse.ArgumentTypeError(
            f"must be an integer between 1 and {MAX_CAPTURE_ROWS}"
        ) from None


def _provider_id_argument(raw_value: str) -> int:
    try:
        return _provider_id(raw_value, field="provider ID")
    except RuntimeError:
        raise argparse.ArgumentTypeError("must be a positive integer") from None


def _event_wait_argument(raw_value: str) -> int:
    try:
        seconds = int(raw_value)
    except ValueError:
        seconds = 0
    if not 1 <= seconds <= MAX_EVENT_WAIT_SECONDS:
        raise argparse.ArgumentTypeError(
            f"must be an integer between 1 and {MAX_EVENT_WAIT_SECONDS}"
        )
    return seconds


def _validate_event_wait_seconds(value: object) -> int:
    if type(value) is not int or not 1 <= value <= MAX_EVENT_WAIT_SECONDS:
        raise ValueError(
            f"event wait must be an integer between 1 and {MAX_EVENT_WAIT_SECONDS} seconds"
        )
    return value


def main() -> int:
    parser = argparse.ArgumentParser(description="Record sanitized Bullhorn fixtures (read-only + one throwaway subscription).")
    parser.add_argument("--allow-live", action="store_true", help="Required. Confirms you intend to hit a real Bullhorn instance.")
    parser.add_argument("--out", default="tests/fixtures/bullhorn_recorded", help="Output directory (relative to backend/).")
    parser.add_argument(
        "--credentials-file",
        type=Path,
        default=None,
        help="Mode-0600 JSON credential file (preferred to exported environment variables).",
    )
    parser.add_argument(
        "--token-state-file",
        type=Path,
        required=True,
        help="Private mode-0600 artifact that retains every rotated refresh token.",
    )
    parser.add_argument("--candidate-id", type=_provider_id_argument, default=None)
    parser.add_argument("--job-order-id", type=_provider_id_argument, default=None)
    parser.add_argument(
        "--max",
        type=_max_rows_argument,
        default=5,
        dest="max_rows",
        help=f"Max rows per entity to record (1-{MAX_CAPTURE_ROWS}).",
    )
    parser.add_argument(
        "--require-event",
        action="store_true",
        help="Wait boundedly for a manually generated event on a dedicated test record.",
    )
    parser.add_argument(
        "--event-wait-seconds",
        type=_event_wait_argument,
        default=120,
        help=f"Bound for --require-event (1-{MAX_EVENT_WAIT_SECONDS} seconds).",
    )
    args = parser.parse_args()

    if not args.allow_live:
        print(
            "Refusing to run without --allow-live. This script connects to a REAL "
            "Bullhorn instance (read-only + one throwaway event subscription).",
            file=sys.stderr,
        )
        return 2

    credentials = _load_credentials(args.credentials_file)
    out = _resolve_output_path(args.out)
    capture(
        out,
        candidate_id=args.candidate_id,
        job_order_id=args.job_order_id,
        max_rows=args.max_rows,
        credentials=credentials,
        token_state_file=args.token_state_file,
        require_event=args.require_event,
        event_wait_seconds=args.event_wait_seconds,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
