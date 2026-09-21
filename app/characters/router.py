from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from characters.services import generate_character_questions
from characters.database import get_characters_db
from textbook.database import get_textbook_db
from app.core.database import SessionLocal
import characters.schemas
import characters.crud
from app.core.deps import get_current_user
from auth.models import User

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# Add your existing imports for get_db, get_characters_db, and generate_character_questions here

router = APIRouter()

@router.get("/api/character_practice")
def character_practice(num_questions: int = 10,
                       user: User = Depends(get_current_user),
                       db: Session = Depends(get_db),
                       characters_db: Session = Depends(get_characters_db),
                       textbook_db: Session = Depends(get_textbook_db)):
    user_id = user.id
    questions = generate_character_questions(db, characters_db, textbook_db, user_id, num_questions)
    return {"user_id": user_id, "question_set": questions}

@router.get("/api/characters/decompose")
def decompose_text(text: str, recursive: bool = False, characters_db: Session = Depends(get_characters_db)):
    """Breaks down each character in `text` into its IDS.
    If recursive=False, only returns the top-level IDS string (no sub-component expansion)."""
    results = []
    for ch in text:
        d = characters.crud.get_decomposition(characters_db, ch, recursive=recursive)
        if d:
            results.append(d)
    return results