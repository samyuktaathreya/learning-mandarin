# app/routers/auth.py
import uuid
from fastapi import APIRouter, Request, Response, Header, HTTPException
from typing import Optional
from app.core.session_auth import issue_session
from app.core.turnstile import verify_turnstile
from app.core.config.shared import settings

router = APIRouter(prefix="/api/auth", tags=["auth"])

@router.post("/verify")
async def verify(request: Request, response: Response, x_turnstile_token: Optional[str] = Header(None)):
    GUEST_ID_MAX_AGE = 60 * 60 * 24 * 365 * 10  # ~10 years — no real expiry, guest_id persists until cache is cleared
    if not x_turnstile_token:
        raise HTTPException(status_code=403, detail="Missing Turnstile token")

    if not await verify_turnstile(x_turnstile_token):
        raise HTTPException(status_code=403, detail="Bot detected")

    is_prod = settings.environment == "prod"
    cookie_kwargs = dict(
        httponly=True,
        secure=is_prod,
        samesite="lax" if not is_prod else "none",
        domain=".wenku.app" if is_prod else None,
        path="/",
    )

    response.set_cookie(key="session", value=issue_session(), max_age=3600, **cookie_kwargs)

    # Mint a guest_id only if this browser doesn't already have one —
    # otherwise a guest gets a new identity (and loses progress) every
    # time their session cookie expires and /verify re-runs.
    if not request.cookies.get("guest_id"):
        response.set_cookie(
            key="guest_id",
            value=str(uuid.uuid4()),
            max_age=GUEST_ID_MAX_AGE,
            **cookie_kwargs,
        )

    return {"ok": True}