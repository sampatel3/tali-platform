"""Transactional idempotency for later-turn confirmed chat commands."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from sqlalchemy import inspect, text
from sqlalchemy.orm import Session

from ..models.chat_command_receipt import (
    CHAT_COMMAND_COMPLETED,
    CHAT_COMMAND_PENDING,
    ChatCommandReceipt,
)
from .confirmations import ConfirmationCheck


class CommandReceiptConflict(RuntimeError):
    """A stable command key was reused with a different security scope."""


@dataclass(frozen=True)
class CommandReceiptClaim:
    row: ChatCommandReceipt
    dispatch_key: str
    completed_result: dict[str, Any] | None
    is_new: bool
    transaction: Any


def _canonical_hash(value: Any) -> str:
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _command_key(*, token: str, operation: str) -> str:
    digest = hashlib.sha256(
        f"chat-command:v1:{token}:{operation}".encode("utf-8")
    ).hexdigest()
    return f"chat-command/{digest}"


def _receipt_identity(
    *,
    check: ConfirmationCheck,
    conversation_kind: str,
    conversation_id: int,
    organization_id: int,
    role_id: int,
    requested_by_user_id: int,
    operation: str,
    arguments: Any,
) -> tuple[str, dict[str, Any]]:
    if not check.ok or not check.token:
        raise CommandReceiptConflict("confirmed command has no server token")
    kind = str(conversation_kind or "").strip()
    operation = str(operation or "").strip()
    if kind not in {"agent", "taali"}:
        raise CommandReceiptConflict("unsupported chat conversation kind")
    if not operation or len(operation) > 100:
        raise CommandReceiptConflict("invalid confirmed command operation")
    command_key = _command_key(token=str(check.token), operation=operation)
    return command_key, {
        "conversation_kind": kind,
        "conversation_id": int(conversation_id),
        "organization_id": int(organization_id),
        "role_id": int(role_id),
        "requested_by_user_id": int(requested_by_user_id),
        "operation": operation,
        "arguments_hash": _canonical_hash(arguments),
    }


def lookup_command(
    db: Session,
    *,
    check: ConfirmationCheck,
    conversation_kind: str,
    conversation_id: int,
    organization_id: int,
    role_id: int,
    requested_by_user_id: int,
    operation: str,
    arguments: Any,
) -> CommandReceiptClaim | None:
    """Read a prior command claim without creating a pending receipt."""

    command_key, expected = _receipt_identity(
        check=check,
        conversation_kind=conversation_kind,
        conversation_id=conversation_id,
        organization_id=organization_id,
        role_id=role_id,
        requested_by_user_id=requested_by_user_id,
        operation=operation,
        arguments=arguments,
    )
    if db.bind is not None and db.bind.dialect.name == "postgresql":
        db.execute(
            text(
                "SELECT pg_advisory_xact_lock("
                "hashtext('chat_command_receipt'), hashtext(:command_key))"
            ),
            {"command_key": command_key},
        )
    row = (
        db.query(ChatCommandReceipt)
        .filter(ChatCommandReceipt.command_key == command_key)
        .one_or_none()
    )
    if row is None:
        return None
    actual = {key: getattr(row, key) for key in expected}
    if any(str(actual[key]) != str(value) for key, value in expected.items()):
        raise CommandReceiptConflict("confirmed command receipt scope mismatch")
    completed = None
    if row.status == CHAT_COMMAND_COMPLETED and isinstance(row.result, dict):
        completed = dict(row.result)
    return CommandReceiptClaim(
        row=row,
        dispatch_key=command_key,
        completed_result=completed,
        is_new=False,
        transaction=db.get_transaction(),
    )


def begin_command(
    db: Session,
    *,
    check: ConfirmationCheck,
    conversation_kind: str,
    conversation_id: int,
    organization_id: int,
    role_id: int,
    requested_by_user_id: int,
    operation: str,
    arguments: Any,
) -> CommandReceiptClaim:
    """Return the one durable receipt for an approved command.

    The row is deliberately staged without committing or flushing.  Local DB
    mutations and the completed receipt therefore commit with the transcript,
    while an external queue can use ``dispatch_key`` before that commit.  On a
    crash, the same confirmation token deterministically recreates the key.
    """

    command_key, expected = _receipt_identity(
        check=check,
        conversation_kind=conversation_kind,
        conversation_id=conversation_id,
        organization_id=organization_id,
        role_id=role_id,
        requested_by_user_id=requested_by_user_id,
        operation=operation,
        arguments=arguments,
    )
    prior = lookup_command(
        db,
        check=check,
        conversation_kind=conversation_kind,
        conversation_id=conversation_id,
        organization_id=organization_id,
        role_id=role_id,
        requested_by_user_id=requested_by_user_id,
        operation=operation,
        arguments=arguments,
    )
    if prior is not None:
        return prior
    row = ChatCommandReceipt(
        command_key=command_key,
        status=CHAT_COMMAND_PENDING,
        **expected,
    )
    db.add(row)
    return CommandReceiptClaim(
        row=row,
        dispatch_key=command_key,
        completed_result=None,
        is_new=True,
        transaction=db.get_transaction(),
    )


def abandon_uncommitted_command(
    db: Session,
    claim: CommandReceiptClaim,
) -> None:
    """Prevent a caught tool exception from committing a zombie receipt.

    If a canonical domain function committed, the transaction object changes
    and its pending receipt must remain for durable outcome reconstruction. If
    no commit occurred, remove only the newly staged claim before the engine
    catches the exception and commits its safe error tool-result.
    """

    if not claim.is_new or db.get_transaction() is not claim.transaction:
        return
    state = inspect(claim.row)
    if state.pending:
        db.expunge(claim.row)
    elif state.persistent and not state.deleted:
        db.delete(claim.row)


def complete_command(
    db: Session,
    claim: CommandReceiptClaim,
    result: dict[str, Any],
) -> dict[str, Any]:
    """Stage a JSON-safe terminal result in the caller's transaction."""

    safe_result = json.loads(json.dumps(result, default=str))
    claim.row.status = CHAT_COMMAND_COMPLETED
    claim.row.result = safe_result
    db.flush()
    return dict(safe_result)


__all__ = [
    "CommandReceiptClaim",
    "CommandReceiptConflict",
    "abandon_uncommitted_command",
    "begin_command",
    "complete_command",
    "lookup_command",
]
