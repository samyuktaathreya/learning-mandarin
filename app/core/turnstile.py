import httpx
import os
from typing import Optional
from fastapi import HTTPException, Header

async def verify_turnstile(token: str) -> bool:
    async with httpx.AsyncClient() as client:
        try:
            resp = await client.post(
                "https://challenges.cloudflare.com/turnstile/v0/siteverify",
                data={
                    "secret": os.getenv("TURNSTILE_SECRET"),
                    "response": token
                }
            )
            result = resp.json()
            print(f"[Turnstile] siteverify response: {result}")  # ADD THIS
            return result.get("success", False)
        except Exception as e:
            print(f"[Turnstile] verification error: {e}")
            return False

async def require_turnstile(x_turnstile_token: Optional[str] = Header(None)):
    print(f"[Turnstile] Header received: {x_turnstile_token[:20] if x_turnstile_token else None}")  # ADD THIS
    
    if not x_turnstile_token:
        raise HTTPException(status_code=403, detail="Missing Turnstile token")

    if not await verify_turnstile(x_turnstile_token):
        raise HTTPException(status_code=403, detail="Bot detected")