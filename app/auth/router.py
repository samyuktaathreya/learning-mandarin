# app/routers/auth.py
from fastapi import APIRouter, Response, Header, HTTPException
from typing import Optional
from app.core.session_auth import issue_session
from app.core.turnstile import verify_turnstile

router = APIRouter(prefix="/api/auth", tags=["auth"])

@router.post("/verify")
async def verify(response: Response, x_turnstile_token: Optional[str] = Header(None)):
    if not x_turnstile_token:
        raise HTTPException(403, "Missing Turnstile token")
    if not await verify_turnstile(x_turnstile_token):
        raise HTTPException(403, "Bot detected")

    response.set_cookie(
        "session",
        issue_session(),
        httponly=True,
        secure=True,
        samesite="none",
        domain=".wenku.app",
        max_age=3600,
        path="/",
    )
    return {"ok": True}