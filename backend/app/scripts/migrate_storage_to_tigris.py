"""One-shot migration: copy stored documents from a source S3 bucket
(typically the old AWS S3) into the active Tigris/R2/MinIO bucket and
rewrite the corresponding ``*_file_url`` columns in the database.

Run this AFTER pointing ``AWS_*`` env vars at the new Tigris bucket
(see PR description for the playbook). The script reads from a
SEPARATE source bucket using ``MIGRATION_SRC_*`` env vars, so the
running app keeps using the new bucket throughout.

What it touches
---------------
- ``candidate.cv_file_url``
- ``candidate.job_spec_file_url``
- ``candidate_application.cv_file_url``
- ``role.job_spec_file_url``
- ``assessment.cv_file_url``

Skipped: ``cached/reports/*.pdf`` — derived artefacts, regenerated on
next download by the report endpoint. No DB rows reference them.

Behaviour
---------
- **Idempotent**: rows whose URL already matches the new bucket are
  skipped. Re-runnable after partial failures.
- **Dry-run**: ``--dry-run`` prints what would change without copying
  bytes or writing the DB.
- **Resumable**: per-row commit; ``Ctrl-C`` mid-run loses at most one
  in-flight copy.
- **Read-only on source**: never deletes from the source bucket. After
  verifying the new bucket has every file, you delete the source
  bucket manually in AWS.

Env vars (source — old AWS)
---------------------------
``MIGRATION_SRC_AWS_ACCESS_KEY_ID``      AWS read-only key (one-time use)
``MIGRATION_SRC_AWS_SECRET_ACCESS_KEY``  AWS read-only secret
``MIGRATION_SRC_AWS_S3_BUCKET``          old bucket name (e.g. taali-assessments)
``MIGRATION_SRC_AWS_REGION``             old bucket region (default: us-east-1)
``MIGRATION_SRC_AWS_S3_ENDPOINT_URL``    optional; only set when source is non-AWS

The destination uses the normal ``AWS_*`` settings already loaded by the
app (which by this point should be pointing at Tigris).

Usage
-----
::

    # From the backend dir
    python -m app.scripts.migrate_storage_to_tigris --dry-run
    python -m app.scripts.migrate_storage_to_tigris

Or from a Railway one-off shell pointed at the same env.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from dataclasses import dataclass, field
from typing import Iterable, Optional

import boto3
from botocore.exceptions import ClientError
from sqlalchemy.orm import Session

from app.platform.config import settings
from app.platform.database import SessionLocal
from app.models.assessment import Assessment
from app.models.candidate import Candidate
from app.models.candidate_application import CandidateApplication
from app.models.role import Role
from app.services.s3_service import (
    _build_object_url,
    extract_key_from_url,
    s3_object_exists,
)


logger = logging.getLogger("taali.migrate_storage")


@dataclass
class Stats:
    examined: int = 0
    skipped_non_object: int = 0
    skipped_already_migrated: int = 0
    copied: int = 0
    rewritten: int = 0
    errors: list[str] = field(default_factory=list)

    def render(self) -> str:
        return (
            f"examined={self.examined} "
            f"skipped_non_object={self.skipped_non_object} "
            f"skipped_already_migrated={self.skipped_already_migrated} "
            f"copied={self.copied} "
            f"rewritten={self.rewritten} "
            f"errors={len(self.errors)}"
        )


def _build_source_client():
    access_key = os.environ.get("MIGRATION_SRC_AWS_ACCESS_KEY_ID")
    secret_key = os.environ.get("MIGRATION_SRC_AWS_SECRET_ACCESS_KEY")
    region = os.environ.get("MIGRATION_SRC_AWS_REGION", "us-east-1")
    endpoint = os.environ.get("MIGRATION_SRC_AWS_S3_ENDPOINT_URL") or None
    if not access_key or not secret_key:
        raise SystemExit(
            "MIGRATION_SRC_AWS_ACCESS_KEY_ID + MIGRATION_SRC_AWS_SECRET_ACCESS_KEY "
            "must be set so the script can read from the old bucket."
        )
    return boto3.client(
        "s3",
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        region_name=region,
        endpoint_url=endpoint,
    )


def _build_dest_client():
    if not settings.AWS_ACCESS_KEY_ID or not settings.AWS_SECRET_ACCESS_KEY:
        raise SystemExit(
            "Destination credentials (AWS_ACCESS_KEY_ID/AWS_SECRET_ACCESS_KEY) "
            "are not set. Configure Tigris env vars before running."
        )
    if not settings.AWS_S3_BUCKET:
        raise SystemExit("AWS_S3_BUCKET is not set on the destination.")
    endpoint = (settings.AWS_S3_ENDPOINT_URL or "").strip() or None
    return boto3.client(
        "s3",
        aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
        aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
        region_name=settings.AWS_REGION,
        endpoint_url=endpoint,
    )


def _iter_url_rows(db: Session) -> Iterable[tuple[str, object, str]]:
    """Yield ``(table_label, row, attr_name)`` for every column that
    can hold a stored-document URL."""
    for row in db.query(Candidate).filter(Candidate.cv_file_url.isnot(None)).all():
        yield "candidate.cv_file_url", row, "cv_file_url"
    for row in db.query(Candidate).filter(Candidate.job_spec_file_url.isnot(None)).all():
        yield "candidate.job_spec_file_url", row, "job_spec_file_url"
    for row in db.query(CandidateApplication).filter(CandidateApplication.cv_file_url.isnot(None)).all():
        yield "candidate_application.cv_file_url", row, "cv_file_url"
    for row in db.query(Role).filter(Role.job_spec_file_url.isnot(None)).all():
        yield "role.job_spec_file_url", row, "job_spec_file_url"
    for row in db.query(Assessment).filter(Assessment.cv_file_url.isnot(None)).all():
        yield "assessment.cv_file_url", row, "cv_file_url"


def _copy_object(src_client, src_bucket: str, dst_client, dst_bucket: str, key: str, *, dry_run: bool) -> Optional[str]:
    """Stream object bytes from source bucket to destination bucket at
    the same key. Returns the new public URL on success, or None on
    skip/failure.
    """
    if dry_run:
        return _build_object_url(dst_bucket, key)

    try:
        head = dst_client.head_object(Bucket=dst_bucket, Key=key)
        # Already migrated — nothing to do.
        return _build_object_url(dst_bucket, key)
    except ClientError as exc:
        code = (exc.response or {}).get("Error", {}).get("Code", "")
        if code not in {"404", "NoSuchKey", "NotFound"}:
            logger.warning("dest head_object(%s) failed unexpectedly: %s", key, exc)
            return None

    try:
        obj = src_client.get_object(Bucket=src_bucket, Key=key)
    except ClientError as exc:
        logger.error("source get_object(%s) failed: %s", key, exc)
        return None

    body = obj["Body"].read()
    content_type = obj.get("ContentType") or "application/octet-stream"
    try:
        dst_client.put_object(Bucket=dst_bucket, Key=key, Body=body, ContentType=content_type)
    except ClientError as exc:
        logger.error("dest put_object(%s) failed: %s", key, exc)
        return None
    return _build_object_url(dst_bucket, key)


def migrate(*, dry_run: bool) -> Stats:
    src_client = _build_source_client()
    src_bucket = os.environ.get("MIGRATION_SRC_AWS_S3_BUCKET")
    if not src_bucket:
        raise SystemExit("MIGRATION_SRC_AWS_S3_BUCKET must be set.")
    dst_client = _build_dest_client()
    dst_bucket = settings.AWS_S3_BUCKET

    logger.info(
        "Migration plan: source=%s -> destination=%s%s",
        src_bucket,
        dst_bucket,
        " (DRY RUN)" if dry_run else "",
    )

    stats = Stats()
    db: Session = SessionLocal()
    try:
        for label, row, attr in _iter_url_rows(db):
            stats.examined += 1
            url = getattr(row, attr) or ""
            parsed = extract_key_from_url(url)
            if parsed is None:
                # Local filesystem path or some other URL we don't own.
                stats.skipped_non_object += 1
                continue
            row_bucket, key = parsed
            if row_bucket == dst_bucket:
                # Already pointing at the destination — nothing to copy.
                stats.skipped_already_migrated += 1
                continue

            if row_bucket != src_bucket:
                logger.warning(
                    "%s id=%s points at unexpected bucket %s — skipping (expected %s)",
                    label, getattr(row, "id", "?"), row_bucket, src_bucket,
                )
                stats.errors.append(f"{label} id={getattr(row, 'id', '?')}: unexpected bucket {row_bucket}")
                continue

            new_url = _copy_object(src_client, src_bucket, dst_client, dst_bucket, key, dry_run=dry_run)
            if new_url is None:
                stats.errors.append(f"{label} id={getattr(row, 'id', '?')}: copy failed for key {key}")
                continue
            stats.copied += 1

            if dry_run:
                logger.info(
                    "DRY %s id=%s key=%s -> %s",
                    label, getattr(row, "id", "?"), key, new_url,
                )
                continue

            setattr(row, attr, new_url)
            try:
                db.commit()
                stats.rewritten += 1
                logger.info(
                    "%s id=%s key=%s migrated",
                    label, getattr(row, "id", "?"), key,
                )
            except Exception as exc:
                db.rollback()
                stats.errors.append(f"{label} id={getattr(row, 'id', '?')}: db commit failed: {exc}")
                logger.exception("DB commit failed for %s id=%s", label, getattr(row, "id", "?"))
    finally:
        db.close()
    return stats


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Report what would change; don't copy or write DB.")
    parser.add_argument(
        "--log-level", default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    stats = migrate(dry_run=args.dry_run)
    print("\n=== migration summary ===")
    print(stats.render())
    if stats.errors:
        print("\nerrors:")
        for err in stats.errors[:50]:
            print(f"  - {err}")
        if len(stats.errors) > 50:
            print(f"  ... and {len(stats.errors) - 50} more")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
