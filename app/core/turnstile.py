import os
import httpx
from typing import Optional
from fastapi import HTTPException, Header
from app.core.config.shared import settings

# Cloudflare's official always-pass test secret key
TEST_SECRET_KEY = "1x0000000000000000000000000000000AA"

async def verify_turnstile(token: str) -> bool:
    # Use environment secret in production; fallback to dummy test secret in dev
    if settings.environment == "prod":
        secret_key = os.getenv("TURNSTILE_SECRET")
    else:
        secret_key = os.getenv("TURNSTILE_SECRET") or TEST_SECRET_KEY

    async with httpx.AsyncClient() as client:
        try:
            resp = await client.post(
                "https://challenges.cloudflare.com/turnstile/v0/siteverify",
                data={
                    "secret": secret_key,
                    "response": token
                }
            )
            result = resp.json()
            print(f"[Turnstile] siteverify response ({settings.environment}): {result}")
            return result.get("success", False)
        except Exception as e:
            print(f"[Turnstile] verification error: {e}")
            return False

async def require_turnstile(x_turnstile_token: Optional[str] = Header(None)):
    print(f"[Turnstile] Header received: {x_turnstile_token[:20] if x_turnstile_token else None}")
    
    if not x_turnstile_token:
        raise HTTPException(status_code=403, detail="Missing Turnstile token")

    if not await verify_turnstile(x_turnstile_token):
        raise HTTPException(status_code=403, detail="Bot detected")