# app/auth/webhooks.py
import os
import json
from fastapi import APIRouter, Request, HTTPException, Header
from svix.webhooks import Webhook, WebhookVerificationError

from app.core.database import SessionLocal
from app.auth.crud import get_or_create_user, delete_user_by_clerk_id

from app.core.config.shared import settings

router = APIRouter(prefix="/api/webhooks", tags=["webhooks"])


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@router.post("/clerk")
async def clerk_webhook(
    request: Request,
    svix_id: str = Header(None, alias="svix-id"),
    svix_timestamp: str = Header(None, alias="svix-timestamp"),
    svix_signature: str = Header(None, alias="svix-signature"),
):
    webhook_secret = settings.CLERK_WEBHOOK_SECRET
    if not webhook_secret:
        print("no webhook secret")
        raise HTTPException(status_code=503, detail="Webhook not configured yet")

    payload = await request.body()

    if not (svix_id and svix_timestamp and svix_signature):
        raise HTTPException(status_code=400, detail="Missing svix headers")

    wh = Webhook(webhook_secret)
    try:
        wh.verify(
            payload,
            {
                "svix-id": svix_id,
                "svix-timestamp": svix_timestamp,
                "svix-signature": svix_signature,
            },
        )
    except WebhookVerificationError:
        raise HTTPException(status_code=400, detail="Invalid webhook signature")

    # verify() raises on failure; if we're here, the payload is authentic.
    # Parse it ourselves rather than trusting verify()'s return value,
    # since that's apparently inconsistent across svix versions.
    event = json.loads(payload)

    event_type = event.get("type")
    data = event.get("data", {})

    db = next(get_db())
    try:
        if event_type == "user.created":
            clerk_id = data["id"]
            email = data.get("email_addresses", [{}])[0].get("email_address")
            get_or_create_user(db, clerk_id=clerk_id, email=email)

        elif event_type == "user.deleted":
            clerk_id = data.get("id")
            if clerk_id:
                delete_user_by_clerk_id(db, clerk_id)

    finally:
        db.close()

    return {"status": "ok"}