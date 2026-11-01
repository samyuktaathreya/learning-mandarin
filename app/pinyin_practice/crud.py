# app/pinyin_practice/crud.py
"""
Word lookups for pinyin dictation. Everything is in the textbook DB (Vocab);
nothing per-user is stored -- which words the user missed lives in the
browser and is sent with each request (see services.pick_word).
"""
from typing import Optional

from sqlalchemy.orm import Session

from app.textbook.models import Vocab, WordType

MAX_HSK_LEVEL = 7  # 7 = HSK 7-9 in the 2025 syllabus


def _practice_words(db: Session):
    # auto rows are pipeline filler (not real list words), and a word with no
    # pinyin can't be graded or forced through TTS
    return db.query(Vocab).filter(
        Vocab.word_type != WordType.auto,
        Vocab.pinyin != "",
    )


def get_word_ids_for_levels(db: Session, levels: list[int]) -> list[int]:
    rows = _practice_words(db).with_entities(Vocab.id).filter(Vocab.hsk_level.in_(levels)).all()
    return [r.id for r in rows]


def get_word_by_id(db: Session, word_id: int) -> Optional[Vocab]:
    return db.query(Vocab).filter(Vocab.id == word_id).first()


def get_words_by_hanzi(db: Session, hanzi: list[str]) -> list[Vocab]:
    if not hanzi:
        return []
    return _practice_words(db).filter(Vocab.hanzi.in_(hanzi)).all()


def get_all_words(db: Session) -> list[Vocab]:
    """Every practice word at every level (plus unlevelled ones). Tapping mode
    needs the whole list once to learn every reading each character has."""
    return _practice_words(db).all()
