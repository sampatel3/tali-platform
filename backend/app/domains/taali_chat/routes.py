"""HTTP routes for Taali Chat.

  POST   /api/v1/taali-chat/turn                    streaming chat turn (SSE)
  GET    /api/v1/taali-chat/conversations           sidebar list
  GET    /api/v1/taali-chat/conversations/{id}      transcript
  DELETE /api/v1/taali-chat/conversations/{id}      soft-delete
  PATCH  /api/v1/taali-chat/conversations/{id}      rename

All endpoints reuse fastapi-users JWT auth via ``get_current_user`` and
are scoped to the caller's organization.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import desc
from sqlalchemy.orm import Session

from ...deps import get_current_user
from ...models.organization import Organization
from ...models.taali_chat_conversation import TaaliChatConversation
from ...models.taali_chat_message import TaaliChatMessage
from ...models.user import User
from ...platform.database import SessionLocal, get_db
from ...taali_chat.service import ChatTurnInput, run_chat_turn

router = APIRouter(prefix="/taali-chat", tags=["taali-chat"])


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class TurnRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=8000)
    conversation_id: Optional[int] = None
    # Optional role scope. Used only on the FIRST turn of a new
    # conversation (when conversation_id is None). The resulting
    # TaaliChatConversation row records role_id; subsequent turns use
    # the persisted scope and ignore this field.
    role_id: Optional[int] = None


class ConversationSummary(BaseModel):
    id: int
    title: Optional[str]
    created_at: datetime
    updated_at: Optional[datetime]
    message_count: int
    role_id: Optional[int] = None


class ConversationDetail(BaseModel):
    id: int
    title: Optional[str]
    created_at: datetime
    updated_at: Optional[datetime]
    messages: list[dict]
    role_id: Optional[int] = None


class ConversationRename(BaseModel):
    title: str = Field(..., min_length=1, max_length=200)


# ---------------------------------------------------------------------------
# POST /turn — streaming chat turn
# ---------------------------------------------------------------------------


@router.post("/turn")
def chat_turn(
    body: TurnRequest,
    current_user: User = Depends(get_current_user),
):
    """Stream one chat turn back to the client.

    Wire format: AI SDK Data Stream Protocol (newline-delimited tagged
    frames over ``Content-Type: text/event-stream``). The frontend
    consumes this with ``@assistant-ui/react`` or Vercel ``useChat``
    without further adapters.
    """
    if current_user.organization_id is None:
        raise HTTPException(status_code=400, detail="User has no organization")

    def _stream():
        # Each turn opens its own short-lived session — we don't share
        # the request-scoped session because it would close before the
        # generator finishes streaming.
        db = SessionLocal()
        try:
            organization = (
                db.query(Organization)
                .filter(Organization.id == current_user.organization_id)
                .first()
            )
            user = db.query(User).filter(User.id == current_user.id).first()
            if user is None or organization is None:
                yield 'e:{"finishReason":"stop","usage":{"promptTokens":0,"completionTokens":0},"isContinued":false}\n'
                return
            try:
                for frame in run_chat_turn(
                    db=db,
                    user=user,
                    organization=organization,
                    turn=ChatTurnInput(
                        user_message=body.message,
                        conversation_id=body.conversation_id,
                        role_id=body.role_id,
                    ),
                ):
                    yield frame.body
                db.commit()
            except Exception as exc:
                db.rollback()
                err = str(exc).replace('"', '\\"')
                yield f'3:"{err}"\n'
                yield 'd:{"finishReason":"stop","usage":{"promptTokens":0,"completionTokens":0}}\n'
        finally:
            db.close()

    return StreamingResponse(
        _stream(),
        media_type="text/event-stream",
        headers={
            # Disable proxy buffering so chunks reach the browser immediately.
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


# ---------------------------------------------------------------------------
# GET /conversations — sidebar
# ---------------------------------------------------------------------------


@router.get("/conversations", response_model=list[ConversationSummary])
def list_conversations(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    rows = (
        db.query(TaaliChatConversation)
        .filter(
            TaaliChatConversation.organization_id == current_user.organization_id,
            TaaliChatConversation.user_id == current_user.id,
            TaaliChatConversation.archived_at.is_(None),
        )
        .order_by(
            desc(
                TaaliChatConversation.updated_at.is_(None),
            ),
            desc(TaaliChatConversation.updated_at),
            desc(TaaliChatConversation.created_at),
        )
        .limit(200)
        .all()
    )
    if not rows:
        return []
    counts = {
        row_id: int(count)
        for row_id, count in (
            db.query(TaaliChatMessage.conversation_id, _count(TaaliChatMessage.id))
            .filter(TaaliChatMessage.conversation_id.in_([r.id for r in rows]))
            .group_by(TaaliChatMessage.conversation_id)
            .all()
        )
    }
    return [
        ConversationSummary(
            id=r.id,
            title=r.title,
            created_at=r.created_at,
            updated_at=r.updated_at,
            message_count=counts.get(r.id, 0),
            role_id=r.role_id,
        )
        for r in rows
    ]


@router.get("/conversations/{conversation_id}", response_model=ConversationDetail)
def get_conversation(
    conversation_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    convo = _get_owned_conversation(db, conversation_id, current_user)
    rows = (
        db.query(TaaliChatMessage)
        .filter(TaaliChatMessage.conversation_id == convo.id)
        .order_by(TaaliChatMessage.created_at.asc(), TaaliChatMessage.id.asc())
        .all()
    )
    return ConversationDetail(
        id=convo.id,
        title=convo.title,
        created_at=convo.created_at,
        updated_at=convo.updated_at,
        role_id=convo.role_id,
        messages=[
            {
                "id": m.id,
                "role": m.role,
                "content": m.content,
                "created_at": m.created_at.isoformat() if m.created_at else None,
                "stop_reason": m.stop_reason,
            }
            for m in rows
        ],
    )


@router.delete("/conversations/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_conversation(
    conversation_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    convo = _get_owned_conversation(db, conversation_id, current_user)
    convo.archived_at = datetime.now(timezone.utc)
    db.commit()
    return None


@router.patch("/conversations/{conversation_id}", response_model=ConversationSummary)
def rename_conversation(
    conversation_id: int,
    body: ConversationRename,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    convo = _get_owned_conversation(db, conversation_id, current_user)
    convo.title = body.title.strip()[:200]
    db.commit()
    db.refresh(convo)
    msg_count = (
        db.query(_count(TaaliChatMessage.id))
        .filter(TaaliChatMessage.conversation_id == convo.id)
        .scalar()
        or 0
    )
    return ConversationSummary(
        id=convo.id,
        title=convo.title,
        created_at=convo.created_at,
        updated_at=convo.updated_at,
        message_count=int(msg_count),
        role_id=convo.role_id,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_owned_conversation(
    db: Session, conversation_id: int, current_user: User
) -> TaaliChatConversation:
    convo = (
        db.query(TaaliChatConversation)
        .filter(
            TaaliChatConversation.id == conversation_id,
            TaaliChatConversation.organization_id == current_user.organization_id,
            TaaliChatConversation.user_id == current_user.id,
            TaaliChatConversation.archived_at.is_(None),
        )
        .first()
    )
    if convo is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return convo


def _count(col):  # tiny shim so we don't import sqlalchemy.func in the route body twice
    from sqlalchemy import func as _func

    return _func.count(col)
