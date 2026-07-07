"""P4: outbound webhook management API.

Integration config → admin/recruiter only. The signing ``secret`` is write-only
(accepted on create/update, never echoed back)."""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from ...deps import require_role
from ...models.user import ROLE_ADMIN, ROLE_RECRUITER, User
from ...platform.database import get_db
from .webhook_service import (
    create_subscription,
    delete_subscription,
    get_subscription,
    list_subscriptions,
    update_subscription,
)

_manage = require_role(ROLE_ADMIN, ROLE_RECRUITER)

router = APIRouter(tags=["Webhooks"])


class SubscriptionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    url: str
    event_types: list | None = None
    is_active: bool = True


class SubscriptionCreate(BaseModel):
    url: str
    secret: str
    event_types: list[str] = []


class SubscriptionUpdate(BaseModel):
    url: str | None = None
    secret: str | None = None
    event_types: list[str] | None = None
    is_active: bool | None = None


class DeliveryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    subscription_id: int
    event_type: str
    status: str
    attempts: int
    response_status: int | None = None
    last_error: str | None = None
    delivered_at: datetime | None = None


@router.get("/webhooks", response_model=list[SubscriptionOut])
def get_webhooks(
    db: Session = Depends(get_db),
    current_user: User = Depends(_manage),
):
    return list_subscriptions(db, current_user.organization_id)


@router.post("/webhooks", response_model=SubscriptionOut, status_code=201)
def create_webhook(
    data: SubscriptionCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(_manage),
):
    sub = create_subscription(
        db, current_user.organization_id,
        url=data.url, secret=data.secret, event_types=data.event_types,
    )
    db.commit()
    db.refresh(sub)
    return sub


@router.patch("/webhooks/{sub_id}", response_model=SubscriptionOut)
def patch_webhook(
    sub_id: int,
    data: SubscriptionUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(_manage),
):
    sub = update_subscription(
        db, current_user.organization_id, sub_id, data.model_dump(exclude_unset=True)
    )
    db.commit()
    db.refresh(sub)
    return sub


@router.delete("/webhooks/{sub_id}", status_code=204)
def remove_webhook(
    sub_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(_manage),
):
    delete_subscription(db, current_user.organization_id, sub_id)
    db.commit()
    return None


@router.get("/webhooks/{sub_id}/deliveries", response_model=list[DeliveryOut])
def get_webhook_deliveries(
    sub_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(_manage),
):
    sub = get_subscription(db, current_user.organization_id, sub_id)
    return sorted(sub.deliveries, key=lambda d: d.id, reverse=True)
