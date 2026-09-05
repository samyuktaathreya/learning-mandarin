import base64
from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from shared.services.audio import (
    generate_and_cache_audio,
    clear_session_audio,
    process_spoken_audio
)
from textbook.database import get_textbook_db

from fastapi import APIRouter, Depends
from app.core.turnstile import require_turnstile

router = APIRouter(dependencies=[Depends(require_turnstile)])

@router.post("/api/audio")
async def get_audio(payload: dict):
    text = payload["text"]
    slow = payload.get("slow", False)
    
    filepath = await generate_and_cache_audio(text, slow=slow)
    
    with open(filepath, "rb") as f:
        audio_data = base64.b64encode(f.read()).decode("utf-8")
        
    return JSONResponse({"audio": audio_data})


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
        import traceback
        traceback.print_exc()
        # Fallback for internal service errors (like failed FFmpeg conversions)
        if str(e) == "Assessment failed":
            return JSONResponse({"error": "Assessment failed", "mode": "assessment"}, status_code=500)
        return JSONResponse({"error": str(e)}, status_code=500)