# app/pinyin/crud.py
"""
Query layer for pinyin data. Two databases are touched here even though
this file lives under pinyin/, not textbook/ or session/ -- same pattern
as characters/crud.py using characters_db: crud is organized by feature
domain, not by which engine backs it.

  - textbook_db: PinyinSyllable (static curriculum data, seeded once)
  - db (session DB): SoundProgress (per-user mastery of tags)
"""
from sqlalchemy.orm import Session

from app.textbook.models import PinyinSyllable
from app.session.models import SoundProgress


def get_syllables_for_tag(textbook_db: Session, tag: str) -> list[PinyinSyllable]:
    """All syllables where this tag is the initial, final, OR tone.
    Tone is stored as an int column, so a 'toneN' tag needs translating
    by the caller before hitting this -- see services.py's tag handling.
    """
    return (
        textbook_db.query(PinyinSyllable)
        .filter(
            (PinyinSyllable.initial_tag == tag) | (PinyinSyllable.final_tag == tag)
        )
        .all()
    )


def get_syllables_matching_tags(
    textbook_db: Session, target_tag: str, tone: int, unlocked_tags: set[str]
) -> list[PinyinSyllable]:
    """The actual question-pool query: syllables that exercise target_tag,
    at the given tone, where every OTHER tag on the syllable is already
    unlocked. This is the online, per-user-state query discussed earlier --
    not a precomputed level->syllables mapping.
    """
    candidates = (
        textbook_db.query(PinyinSyllable)
        .filter(PinyinSyllable.tone == tone)
        .filter(
            (PinyinSyllable.initial_tag == target_tag)
            | (PinyinSyllable.final_tag == target_tag)
        )
        .all()
    )
    return [
        s for s in candidates
        if (s.initial_tag is None or s.initial_tag in unlocked_tags)
        and s.final_tag in unlocked_tags
    ]


def get_sound_progress(db: Session, user_id: int) -> list[SoundProgress]:
    return db.query(SoundProgress).filter(SoundProgress.user_id == user_id).all()


def get_sound_progress_map(db: Session, user_id: int) -> dict[str, SoundProgress]:
    return {row.sound: row for row in get_sound_progress(db, user_id)}


def upsert_sound_progress(db: Session, user_id: int, tag: str, correct: bool) -> None:
    row = (
        db.query(SoundProgress)
        .filter(SoundProgress.user_id == user_id, SoundProgress.sound == tag)
        .first()
    )
    if row is None:
        row = SoundProgress(user_id=user_id, sound=tag, attempts=0, successes=0)
        db.add(row)
    row.attempts = (row.attempts or 0) + 1
    if correct:
        row.successes = (row.successes or 0) + 1
    db.commit()