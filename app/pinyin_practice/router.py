# app/pinyin_practice/router.py
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.textbook.database import get_textbook_db
from app.pinyin_practice import services as practice_services
from app.pinyin_practice.crud import MAX_HSK_LEVEL

router = APIRouter()


class NextWordRequest(BaseModel):
    hsk_level: int = Field(ge=1, le=MAX_HSK_LEVEL)
    missed: list[str] = Field(default_factory=list, max_length=200)  # hanzi answered wrong, not yet right
    recent: list[str] = Field(default_factory=list, max_length=50)   # hanzi shown most recently, oldest first


class CheckRequest(BaseModel):
    word_id: int
    answer: str = Field(max_length=100)


@router.post("/api/pinyin-practice")
async def next_word(body: NextWordRequest, textbook_db: Session = Depends(get_textbook_db)):
    try:
        word = practice_services.pick_word(textbook_db, body.hsk_level, body.missed, body.recent)
    except practice_services.PinyinPracticeError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return {"word": practice_services.word_payload(word)}


@router.post("/api/pinyin-practice/tap")
async def next_tap_question(body: NextWordRequest, textbook_db: Session = Depends(get_textbook_db)):
    """Tapping mode. missed/recent hold single characters here, not words.
    The answer comes back with the question (it's needed to play the audio
    anyway), so the page grades the tap itself."""
    try:
        question = practice_services.pick_tap_question(textbook_db, body.hsk_level, body.missed, body.recent)
    except practice_services.PinyinPracticeError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return {"question": question}


@router.post("/api/pinyin-practice/check")
async def check(body: CheckRequest, textbook_db: Session = Depends(get_textbook_db)):
    result = practice_services.check_answer(textbook_db, body.word_id, body.answer)
    if result is None:
        raise HTTPException(status_code=404, detail="Word not found")
    return result
