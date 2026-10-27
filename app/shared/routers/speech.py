"""
shared/routers/speech.py

GET /api/speech/token -> { token, region, expires_in }

The browser calls this once when a practice session starts, then again
shortly before `expires_in` runs out. Recordings go straight from the
browser to Azure; this server is never in the audio path.
"""
from fastapi import APIRouter, Depends, HTTPException, Response

from app.auth.models import User
from app.core.config.shared import settings
from app.core.deps import get_current_user
from app.core.logger import logger
from app.shared.services.speech_token import SpeechTokenError, get_speech_token

router = APIRouter()


@router.get("/api/speech/token")
async def speech_token(response: Response, user: User = Depends(get_current_user)):
    # `user` isn't used below. Asking for it is what makes this route
    # require a signed-in user or guest: get_current_user raises a 401 first.
    try:
        token, expires_in = await get_speech_token()
    except SpeechTokenError as e:
        logger.error(f"[speech token] {e}")
        raise HTTPException(status_code=503, detail="Speech service is unavailable, try again shortly.")

    # Tokens are credentials: keep them out of browser and proxy caches.
    response.headers["Cache-Control"] = "no-store"
    return {"token": token, "region": settings.AZURE_SPEECH_REGION, "expires_in": expires_in}