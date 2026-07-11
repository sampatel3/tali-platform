#!/usr/bin/env python
"""Record sanitized Bullhorn fixtures from a real client instance.

Implements BULLHORN_BUILD_PLAN §0.2 (recorded-fixture validation): once client
credentials arrive, exercise the READ endpoints our integration uses against the
real instance, scrub all PII + tokens, and write JSON fixtures shaped the way the
fake server (tests/fakes/bullhorn_*) and contract tests expect. Those fixtures
keep the fake honest against at least one real instance.

SAFETY
------
* Read-only EXCEPT the event subscription, which we create + immediately delete
  (it is the only way to record the destructive event-queue shape). Nothing else
  writes to the client's data.
* Refuses to run without an explicit ``--allow-live`` flag.
* Every recorded record is scrubbed: names/emails/phones become deterministic
  fakes; any token/secret/BhRestToken query param or field is redacted.

USAGE
-----
    python scripts/bullhorn_capture_fixtures.py --allow-live \
        [--out backend/tests/fixtures/bullhorn_recorded] \
        [--candidate-id N] [--job-order-id N] [--max 5]

Credentials come from the environment (never CLI args, so they don't land in
shell history):
    BULLHORN_USERNAME, BULLHORN_CLIENT_ID, BULLHORN_CLIENT_SECRET, BULLHORN_PASSWORD

This script is an operator tool, not part of the app or its test suite.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any

# --- allow running from a repo checkout without installing the package --------
_BACKEND = Path(__file__).resolve().parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from app.components.integrations.bullhorn.auth import BullhornAuth  # noqa: E402
from app.components.integrations.bullhorn.service import BullhornService  # noqa: E402
from app.components.integrations.bullhorn.sync_service import (  # noqa: E402
    JOB_ORDER_FIELDS,
    JOB_SUBMISSION_FIELDS,
)

# Field lists the integration reads — kept in sync with the sync modules so the
# recorded shapes match what production actually requests.
CANDIDATE_FIELDS = "id,firstName,lastName,name,email,phone,mobile,occupation,address,dateLastModified"
FILE_ATTACHMENT_FIELDS = "id,name,type,contentType,dateAdded"
SUBMISSION_HISTORY_FIELDS = "id,status,dateAdded,modifyingUser"
NOTE_FIELDS = "id,comments,action,dateAdded,commentingPerson"

# Keys whose VALUES are sensitive wherever they appear in a recorded record.
_TOKEN_KEYS = {
    "bhresttoken",
    "resttoken",
    "access_token",
    "refresh_token",
    "client_secret",
    "password",
    "corptoken",
    "sessionkey",
}
# Free-text / identifying fields replaced with a deterministic fake.
_NAME_KEYS = {"firstname", "lastname", "name", "occupation"}
_EMAIL_KEYS = {"email"}
_PHONE_KEYS = {"phone", "mobile"}


def _fake_name(seed: str) -> str:
    h = int(hashlib.sha256(seed.encode()).hexdigest(), 16)
    first = ["Ada", "Grace", "Alan", "Katherine", "Linus", "Radia", "Barbara", "Dennis"]
    last = ["Lovelace", "Hopper", "Turing", "Johnson", "Torvalds", "Perlman", "Liskov", "Ritchie"]
    return f"{first[h % len(first)]} {last[(h // 7) % len(last)]}"


def _scrub(value: Any, *, key: str | None = None, seed: str = "x") -> Any:
    """Recursively scrub a recorded record: redact tokens, fake PII, keep shape."""
    lkey = (key or "").lower()
    if isinstance(value, dict):
        return {k: _scrub(v, key=k, seed=str(value.get("id", seed))) for k, v in value.items()}
    if isinstance(value, list):
        return [_scrub(v, key=key, seed=seed) for v in value]
    if lkey in _TOKEN_KEYS:
        return "REDACTED"
    if lkey in _EMAIL_KEYS and isinstance(value, str) and value:
        return f"candidate-{hashlib.sha256((seed + value).encode()).hexdigest()[:10]}@example.com"
    if lkey in _PHONE_KEYS and isinstance(value, str) and value:
        return "+10000000000"
    if lkey in _NAME_KEYS and isinstance(value, str) and value:
        full = _fake_name(seed + value)
        if lkey == "firstname":
            return full.split(" ")[0]
        if lkey == "lastname":
            return full.split(" ")[-1]
        return full
    if lkey == "address" and isinstance(value, dict):
        return {"city": "Anytown", "state": "NA", "zip": "00000"}
    return value


def _build_service() -> BullhornService:
    def _noop_persist(*, refresh_token: str, rest_url: str | None = None) -> None:
        return None

    missing = [
        name
        for name in ("BULLHORN_USERNAME", "BULLHORN_CLIENT_ID", "BULLHORN_CLIENT_SECRET", "BULLHORN_PASSWORD")
        if not os.environ.get(name)
    ]
    if missing:
        raise SystemExit(f"Missing required env vars: {', '.join(missing)}")

    auth = BullhornAuth(
        username=os.environ["BULLHORN_USERNAME"],
        client_id=os.environ["BULLHORN_CLIENT_ID"],
        client_secret=os.environ["BULLHORN_CLIENT_SECRET"],
        refresh_token=None,
        persist_tokens=_noop_persist,
        password=os.environ["BULLHORN_PASSWORD"],
    )
    auth.authorize_with_password()
    return BullhornService(auth, client_id=os.environ["BULLHORN_CLIENT_ID"])


def _dump(out: Path, name: str, payload: Any) -> None:
    scrubbed = _scrub(payload)
    path = out / f"{name}.json"
    path.write_text(json.dumps(scrubbed, indent=2, sort_keys=True, default=str))
    print(f"  wrote {path.relative_to(_BACKEND.parent)}")


def capture(out: Path, *, candidate_id: int | None, job_order_id: int | None, max_rows: int) -> None:
    service = _build_service()
    out.mkdir(parents=True, exist_ok=True)

    print("ping / meta")
    _dump(out, "ping", service.ping())
    _dump(out, "status_list", service.get_status_list())
    _dump(
        out,
        "entitlements",
        {
            "Candidate": service.get_entitlements("Candidate"),
            "JobOrder": service.get_entitlements("JobOrder"),
            "JobSubmission": service.get_entitlements("JobSubmission"),
            "Note": service.get_entitlements("Note"),
        },
    )

    print("jobOrders")
    job_orders = service.search_job_orders(fields=JOB_ORDER_FIELDS, query="isOpen:true")[:max_rows]
    _dump(out, "job_orders", job_orders)

    print("jobSubmissions")
    where = f"jobOrder.id={int(job_order_id)}" if job_order_id else ""
    submissions = service.query_job_submissions(fields=JOB_SUBMISSION_FIELDS, where=where)[:max_rows]
    _dump(out, "job_submissions", submissions)

    # Resolve a candidate id to sample the candidate + files + notes reads.
    cand_id = candidate_id
    if cand_id is None and submissions:
        cand_ref = submissions[0].get("candidate") or {}
        cand_id = cand_ref.get("id")
    if cand_id is not None:
        print(f"candidate {cand_id} (notes, files)")
        _dump(out, "notes", service.query_notes(candidate_id=cand_id, fields=NOTE_FIELDS)[:max_rows])
        _dump(
            out,
            "file_attachments",
            service.list_file_attachments(candidate_id=cand_id, fields=FILE_ATTACHMENT_FIELDS)[:max_rows],
        )
    else:
        print("  (no candidate id available — skipping notes/files)")

    if submissions:
        sub_id = submissions[0].get("id")
        print(f"jobSubmissionHistory {sub_id}")
        _dump(
            out,
            "job_submission_history",
            service.get_job_submission_history(job_submission_id=sub_id, fields=SUBMISSION_HISTORY_FIELDS)[:max_rows],
        )

    # Event subscription: the ONE write. Create a throwaway subscription, poll it
    # once to record the queue shape, then delete it. Named so it is obvious it is
    # ours if cleanup ever fails.
    print("event subscription (create → poll → delete)")
    sub_name = "TaaliFixtureCapture-DELETE-ME"
    created = None
    try:
        created = service.create_subscription(
            subscription_id=sub_name, entity_names=["JobSubmission", "Candidate"]
        )
        _dump(out, "event_subscription_create", created)
        _dump(out, "event_poll", service.poll_events(subscription_id=sub_name, max_events=10))
    finally:
        try:
            service.delete_subscription(subscription_id=sub_name)
            print("  deleted throwaway subscription")
        except Exception as exc:  # noqa: BLE001
            print(f"  WARNING: could not delete subscription {sub_name}: {exc}", file=sys.stderr)

    print(f"\nDone. Fixtures in {out}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Record sanitized Bullhorn fixtures (read-only + one throwaway subscription).")
    parser.add_argument("--allow-live", action="store_true", help="Required. Confirms you intend to hit a real Bullhorn instance.")
    parser.add_argument("--out", default="tests/fixtures/bullhorn_recorded", help="Output directory (relative to backend/).")
    parser.add_argument("--candidate-id", type=int, default=None)
    parser.add_argument("--job-order-id", type=int, default=None)
    parser.add_argument("--max", type=int, default=5, dest="max_rows", help="Max rows per entity to record.")
    args = parser.parse_args()

    if not args.allow_live:
        print(
            "Refusing to run without --allow-live. This script connects to a REAL "
            "Bullhorn instance (read-only + one throwaway event subscription).",
            file=sys.stderr,
        )
        return 2

    out = Path(args.out)
    if not out.is_absolute():
        out = _BACKEND / out
    capture(out, candidate_id=args.candidate_id, job_order_id=args.job_order_id, max_rows=args.max_rows)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
