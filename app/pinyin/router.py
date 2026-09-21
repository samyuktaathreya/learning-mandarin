# app/pinyin/router.py
from fastapi import APIRouter, Depends, Body
from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from textbook.database import get_textbook_db
from app.core.deps import get_current_user
from auth.models import User
from app.pinyin import services as pinyin_services


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