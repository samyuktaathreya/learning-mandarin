import base64
import traceback

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.shared.services.audio import (
    get_audio,
    clear_session_audio,
    process_spoken_audio,
    ForcedPronunciationError,
)
from app.textbook.database import get_textbook_db

router = APIRouter()


@router.post("/api/audio")
async def audio(payload: dict):
    """
    Other features: unchanged, returns {"audio": <base64 mp3>}.

    Pinyin section (sends "source": "pinyin"):
      recording found -> {"url": "/pinyin-audio/...", "source": "recording"}
      no recording    -> {"audio": <base64 mp3>,     "source": "tts"}
    """
    try:
        result = await get_audio(
            payload["text"],
            slow=payload.get("slow", False),
            numbered_pinyin=payload.get("pinyin"),
            prefer_recording=payload.get("source") == "pinyin",
        )
    except ForcedPronunciationError as e:
        # 502: Azure (upstream) rejected the request. The body shows exactly
        # what was sent and what Azure said, readable in the Network tab.
        return JSONResponse(status_code=502, content=e.to_dict())

    return JSONResponse(result)


@router.post("/api/audio/clear")
async def clear_audio():
    count = clear_session_audio()
    return {"deleted": count}


@router.post("/api/transcribe")
async def transcribe(payload: dict, textbook_db: Session = Depends(get_textbook_db)):
    audio_b64 = payload.get("audio")
    expected = payload.get("expected", "").strip()
    hanzi = payload.get("hanzi", "").strip()
    question_type = payload.get("question_type", "").strip()

    if not audio_b64:
        return JSONResponse({"error": "No audio provided"}, status_code=400)

    audio_bytes = base64.b64decode(audio_b64)

    try:
        result = await process_spoken_audio(audio_bytes, expected, hanzi, question_type, textbook_db)
        return JSONResponse(result)
    except Exception as e:
        traceback.print_exc()
        # Fallback for internal service errors (like failed FFmpeg conversions)
        if str(e) == "Assessment failed":
            return JSONResponse({"error": "Assessment failed", "mode": "assessment"}, status_code=500)
        return JSONResponse({"error": str(e)}, status_code=500)