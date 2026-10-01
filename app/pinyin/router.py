# app/pinyin/router.py
from fastapi import APIRouter, Depends, Body, File, Form, HTTPException, UploadFile
from sqlalchemy.orm import Session

from core.database import SessionLocal
from textbook.database import get_textbook_db
from core.deps import get_current_user
from auth.models import User
from app.pinyin.services import services as pinyin_services
from pinyin.services import phoneme

from typing import Literal


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


router = APIRouter()


@router.get("/api/pinyin/question")
def pinyin_question(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    textbook_db: Session = Depends(get_textbook_db),
):
    return pinyin_services.generate_pinyin_question(db, textbook_db, user.id)


@router.get("/api/pinyin/session")
def pinyin_session(
    num_questions: int = 10,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    textbook_db: Session = Depends(get_textbook_db),
):
    questions = pinyin_services.generate_pinyin_session(db, textbook_db, user.id, num_questions)
    return {"user_id": user.id, "question_set": questions}


@router.get("/api/pinyin/progress")
def pinyin_progress(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return pinyin_services.get_pinyin_progress(db, user.id)

@router.patch("/api/submit/pinyin")
def submit_pinyin(
    list_of_question_data: list[dict] = Body(...),
    is_correct: list[bool] = Body(...),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return pinyin_services.process_pinyin_submission(db, user.id, list_of_question_data, is_correct)

@router.get("/api/pinyin/get_row_by_tag")
def get_row_by_tag(
    tag: str,
    category: Literal["initial", "final"],
    textbook_db: Session = Depends(get_textbook_db),
):
    return pinyin_services.get_representative_row_by_tag(textbook_db, tag, category)


MAX_AUDIO_BYTES = 2 * 1024 * 1024  # ~2 MB is far more than a short spoken clip


@router.post("/api/pinyin/check_phonemes")
def check_phonemes(
    audio: UploadFile = File(...),
    expected_pinyin: str = Form(...),  # e.g. "zhe2" or "ni3hao3"; tones are ignored
    user: User = Depends(get_current_user),
):
    """Grades initial + final only. zhe2 spoken as zhe4 is correct here --
    tones are graded by the tone checker.

    Plain `def` (not async) on purpose: decoding is CPU-bound, so FastAPI
    runs it in its threadpool instead of blocking the event loop."""
    data = audio.file.read(MAX_AUDIO_BYTES + 1)
    if not data:
        raise HTTPException(400, "Empty audio upload")
    if len(data) > MAX_AUDIO_BYTES:
        raise HTTPException(413, "Audio clip too large")
    try:
        return phoneme.check_phonemes(data, expected_pinyin)
    except phoneme.AudioDecodeError:
        raise HTTPException(400, "Could not decode audio")
    except ValueError as e:
        raise HTTPException(422, str(e))