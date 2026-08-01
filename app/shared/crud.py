"""
Top-level crud.py -- everything left after session-domain functions moved to
session/crud.py. These two only touch DictionaryEntry, which isn't a session
model, so they don't belong in the session package.
"""
from sqlalchemy.orm import Session
from shared.models import DictionaryEntry

def get_dictionary_entries(db: Session, word: str):
    """
    Look up a word in the CC-CEDICT dictionary.
    Matches against both Simplified and Traditional characters.
    """
    return db.query(DictionaryEntry).filter(
        (DictionaryEntry.simplified == word) | (DictionaryEntry.traditional == word)
    ).all()


def build_vocab_block(db: Session, tags: list[str]) -> str:
    entries = (
        db.query(DictionaryEntry)
        .filter(DictionaryEntry.simplified.in_(tags))
        .all()
    )
    return "\n".join(f"{e.simplified} ({e.pinyin}) - {e.english}" for e in entries)