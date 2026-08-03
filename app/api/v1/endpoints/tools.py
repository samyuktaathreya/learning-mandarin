from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session
from core.database import SessionLocal
from pinyin_utils import to_numbered_pinyin
from shared.crud import get_dictionary_entries

# ----------------------------- DB DEPENDENCY -----------------------------

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

router = APIRouter()

@router.post("/api/pinyin")
async def get_pinyin(payload: dict):
    text = payload.get("text", "")
    return JSONResponse({"pinyin": to_numbered_pinyin(text)})

@router.get("/dictionary/{word}")
async def get_translation(word: str, db: Session = Depends(get_db)):
    """
    RESTful GET endpoint to query a Chinese word translation from SQLite.
    Checks both Simplified and Traditional indexes via crud.py.
    """
    results = get_dictionary_entries(db, word=word)

    if not results:
        raise HTTPException(status_code=404, detail="Word not found in dictionary")

    return {
        "word": word,
        "results": [
            {
                "simplified": entry.simplified,
                "traditional": entry.traditional,
                "pinyin": entry.pinyin,
                "english": entry.english.split(" / ") # Split back into list format
            }
            for entry in results
        ]
    }