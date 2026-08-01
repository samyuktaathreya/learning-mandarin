from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from core.database import SessionLocal
from .services import (
    evaluate_chinese_to_english,
    evaluate_english_to_chinese
)

router = APIRouter()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@router.post("/api/grade_chinese_to_english")
async def grade_chinese_to_english(payload: dict, db: Session = Depends(get_db)):
    """
    Grade: is this a valid English translation of the Chinese?
    Ignore the expected_answer entirely — compare against the question itself.
    """
    user_answer = payload.get("user_answer", "").strip()
    question = payload.get("question", "").strip()

    if not user_answer or not question:
        return JSONResponse({"is_correct": False})

    result = evaluate_chinese_to_english(db, user_answer, question)
    return JSONResponse(result)

@router.post("/api/grade_english_to_chinese")
async def grade_english_to_chinese(payload: dict, db: Session = Depends(get_db)):
    """
    Branches on question_type:
      - LISTENING (transcription): strict pinyin comparison, word order enforced.
      - TRANSLATION (everything else): meaning-based AI grade against the question.
      
    Expected payload: { user_answer, expected_answer, question_type, question? }
    """
    user_answer = payload.get("user_answer", "").strip()
    expected = payload.get("expected_answer", "").strip()
    question = payload.get("question", "").strip()
    question_type = payload.get("question_type", "")

    if not user_answer or not expected:
        return JSONResponse({"is_correct": False})

    result = evaluate_english_to_chinese(db, user_answer, expected, question, question_type)
    return JSONResponse(result)