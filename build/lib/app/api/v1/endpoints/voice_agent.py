import os
from dotenv import load_dotenv
from fastapi import APIRouter, HTTPException
import httpx
from sqlalchemy.orm import Session
from fastapi import APIRouter, Depends, Body, HTTPException
from core.database import SessionLocal
from shared.crud import build_vocab_block
from session.crud import get_known_vocab_tags
from core.config.shared import ENV_FILE
# from openai import OpenAI  <- You don't actually need this if you are using httpx to make the web request

router = APIRouter()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

load_dotenv(ENV_FILE)
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")

# FIX 1: I deleted the duplicate `/realtime/session` route that was using the fake command.
# We only need this one route to get the token for your frontend.

@router.post("/api/voice-session")
async def create_voice_session(user_id: int = Body(...), db: Session = Depends(get_db)):
    tags = get_known_vocab_tags(db, user_id)
    vocab_block = build_vocab_block(db, tags)

    instructions = f"""You are a friendly, patient Mandarin conversation partner.
Speak only in Mandarin. Keep responses to 1-2 sentences.

The student currently knows these words. Prefer using them where natural,
but don't force it -- occasional simple words outside this list are fine:

{vocab_block}
"""

    async with httpx.AsyncClient() as client:
        response = await client.post(
            "https://api.openai.com/v1/realtime/client_secrets",
            headers={"Authorization": f"Bearer {OPENAI_API_KEY}", "Content-Type": "application/json"},
            json={
                "session": {
                    "type": "realtime",
                    "model": "gpt-realtime-2.1",
                    "instructions": instructions,
                }
            },
        )
        if response.status_code != 200:
            raise HTTPException(status_code=500, detail=response.text)
        data = response.json()
        return {"client_secret": data["value"], "instructions": instructions}