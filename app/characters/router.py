from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from characters.services import generate_character_questions
from characters.database import get_characters_db
from database import SessionLocal

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# Add your existing imports for get_db, get_characters_db, and generate_character_questions here

router = APIRouter()

@router.get("/api/character_practice/{user_id}")
def character_practice(user_id: int, num_questions: int = 10,
                       db: Session = Depends(get_db),
                       characters_db: Session = Depends(get_characters_db)):
    questions = generate_character_questions(db, characters_db, user_id, num_questions)
    return {"user_id": user_id, "question_set": questions}