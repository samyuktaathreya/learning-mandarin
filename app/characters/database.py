"""
Separate database connection for characters.db — the offline-populated SQLite
database of Chinese character decompositions and confusion pairs.

This is intentionally isolated from mandarin_app.db (see database.py):
- characters.db is populated once offline by ids-app-data/scripts/
- It is read-only at runtime; no init_db, no seeding, no migrations here
- Use get_characters_db() as a FastAPI dependency, same pattern as get_db()
  in your route files

Path: the characters.db file sits in ids-app-data/data/clean/ relative to
the repo root. Adjust CHARACTERS_DB_PATH if your layout differs.
"""

from pathlib import Path
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base
from core.config.data import CHARACTERS_DB

# Create the SQLite URL
DATABASE_URL = f"sqlite:///{CHARACTERS_DB}"

engine = create_engine(
    DATABASE_URL, connect_args={"check_same_thread": False}
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
CharactersBase = declarative_base()

def get_characters_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()