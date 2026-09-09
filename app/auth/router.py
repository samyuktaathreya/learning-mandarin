# app/routers/auth.py
from fastapi import APIRouter, Response, Header, HTTPException
from typing import Optional
from app.core.session_auth import issue_session
from app.core.turnstile import verify_turnstile
from app.core.config.shared import settings

router = APIRouter(prefix="/api/auth", tags=["auth"])

@router.post("/verify")
async def verify(response: Response, x_turnstile_token: Optional[str] = Header(None)):
    if not x_turnstile_token:
        raise HTTPException(status_code=403, detail="Missing Turnstile token")
    
    if not await verify_turnstile(x_turnstile_token):
        raise HTTPException(status_code=403, detail="Bot detected")

    is_prod = settings.environment == "prod"

    # Set cookie attributes conditionally based on environment
    response.set_cookie(
        key="session",
        value=issue_session(),
        httponly=True,
        secure=is_prod,                      # HTTPS only in production
        samesite="lax" if not is_prod else "none",  # 'lax' works on localhost HTTP
        domain=".wenku.app" if is_prod else None,   # Omit domain on localhost
        max_age=3600,
        path="/",
    )
    return {"ok": True}