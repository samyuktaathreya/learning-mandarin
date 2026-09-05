import httpx
import os
from typing import Optional
from fastapi import HTTPException, Header
from app.core.config.shared import settings

async def verify_turnstile(token: str) -> bool:
    print("")
    async with httpx.AsyncClient() as client:
        try:
            resp = await client.post(
                "https://challenges.cloudflare.com/turnstile/v0/siteverify",
                data={
                    "secret": settings.TURNSTILE_SECRET,
                    "response": token
                }
            )
            return resp.json().get("success", False)
        except Exception as e:
            print(f"Turnstile verification error: {e}")
            return False

async def require_turnstile(x_turnstile_token: Optional[str] = Header(None)):
    if settings.environment == "DEV":
        print("DEV mode: skipping Turnstile validation")
        return

    if not x_turnstile_token:
        raise HTTPException(status_code=403, detail="Missing Turnstile token")

    if not await verify_turnstile(x_turnstile_token):
        raise HTTPException(status_code=403, detail="Bot detected")