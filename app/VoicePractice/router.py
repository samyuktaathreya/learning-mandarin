# app/VoicePractice/router.py
import uuid
from typing import Any, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

# TODO: use whatever dependency your textbook routes use for the curriculum DB
# (the one that has the `units`/`vocab` tables). get_db is the user DB.
from app.core.deps import get_current_user, get_db
from app.textbook.database import get_textbook_db
from app.core.logger import logger
from app.auth.models import User
from app.session.crud import get_user_position
from app.VoicePractice import services as voice_services
from app.VoicePractice import feedback as voice_feedback
from app.VoicePractice.voice_log import log_event

router = APIRouter()


@router.post("/api/voice-session")
async def create_voice_session(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    textbook_db: Session = Depends(get_textbook_db),
):
    session_id = uuid.uuid4().hex[:12]
    try:
        voice_session = voice_services.build_session(db, textbook_db, user.id)
    except Exception as e:
        log_event(session_id, "server", "session.build_failed", {"user_id": user.id, "error": repr(e)})
        raise

    ctx = voice_session["context"]
    log_event(session_id, "server", "session.created", {
        "user_id": user.id,
        "hsk_level": ctx["hsk_level"],
        "unit": ctx["unit"],
        "topic": ctx["topic"],
        "title": ctx["title"],
        "target_words": [w["hanzi"] for w in ctx["target_words"]],
        "example_sentences": len(ctx["example_sentences"]),
    })
    log_event(session_id, "server", "session.instructions", {"instructions": voice_session["instructions"]})

    try:
        client_secret = await voice_services.create_client_secret(voice_session["instructions"])
    except voice_services.VoiceSessionError as e:
        logger.error(f"[voice session] {e}")
        log_event(session_id, "server", "session.client_secret_failed", {"error": str(e)})
        raise HTTPException(status_code=503, detail="Voice service is unavailable, try again shortly.")

    return {
        "session_id": session_id,
        "client_secret": client_secret,
        "instructions": voice_session["instructions"],
        "target_words": voice_session["target_words"],
    }


# ---------------------------- client event log ----------------------------

class ClientLogEvent(BaseModel):
    t: str = Field(max_length=40)          # browser ISO timestamp
    type: str = Field(max_length=100)
    data: Optional[Any] = None


class ClientLogBatch(BaseModel):
    session_id: str = Field(max_length=64)
    events: list[ClientLogEvent] = Field(max_length=500)


@router.post("/api/voice-session/log", status_code=204)
async def log_client_events(
    body: ClientLogBatch,
    user: User = Depends(get_current_user),
):
    for e in body.events:
        log_event(body.session_id, "client", e.type, e.data, client_time=e.t)
    return Response(status_code=204)


# ---------------------------- feedback ----------------------------

class TranscriptLine(BaseModel):
    role: Literal["user", "assistant"]
    text: str


class FeedbackRequest(BaseModel):
    transcript: list[TranscriptLine]
    session_id: Optional[str] = Field(default=None, max_length=64)


@router.post("/api/voice-session/feedback")
async def get_voice_feedback(
    body: FeedbackRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    sid = body.session_id
    transcript = [line.model_dump() for line in body.transcript if line.text.strip()]
    if not any(line["role"] == "user" for line in transcript):
        log_event(sid, "server", "feedback.skipped", {"reason": "no student lines"})
        return {"mistakes": [], "possible_mishearings": [], "praise": ""}

    position = get_user_position(db, user.id) or {"hsk_level": 1}
    log_event(sid, "server", "feedback.requested", {"lines": len(transcript), "hsk_level": position["hsk_level"]})
    try:
        result = await voice_feedback.analyze_transcript(transcript, position["hsk_level"])
    except voice_feedback.FeedbackError as e:
        logger.error(f"[voice feedback] {e}")
        log_event(sid, "server", "feedback.failed", {"error": str(e)})
        raise HTTPException(status_code=503, detail="Feedback is unavailable right now.")

    log_event(sid, "server", "feedback.done", result)
    return result