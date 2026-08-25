# app/textbook/database.py
"""
FastAPI dependency for the textbook database, mirroring characters/database.py's
get_characters_db pattern. Needed now that router endpoints query Vocab/
Question/Sentence directly instead of reading module-level dicts that used
to be loaded from JSON at import time -- those dicts didn't need a session
because they were just... dicts. Real queries need a real session.

Uses the same engine/SessionLocal already defined in textbook/db.py (the
pipeline's session setup) -- not a second engine. If your pipeline's db.py
and the app's runtime DB should actually point at different files/URLs
(e.g. pipeline writes to a staging DB, app reads from a synced production
copy), split DATABASE_URL into two constants instead of importing SessionLocal
directly. As written, they're the same DB.
"""
from typing import Generator

from sqlalchemy.orm import Session

from textbook.db_utils import SessionLocal

def get_textbook_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()