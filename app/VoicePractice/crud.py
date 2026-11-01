# app/VoicePractice/crud.py
"""
Voice-practice-specific queries: everything the conversation agent needs to
build its session instructions that doesn't already exist in
app.session.crud (user progress) or app.textbook.crud (curriculum).

Reused instead of duplicated:
    app.session.crud.get_user_position   -> user's (hsk_level, unit)
    app.session.crud.get_seen_tags       -> which words the user has met
    app.session.crud.UNITS_PER_HSK_LEVEL -> "unit X of N" for the prompt

What lives here is curriculum lookups shaped for a prompt (topic, the
specific taught meaning of each unit word, example sentences), which no
other module needs.
"""
import random
from typing import Optional

from sqlalchemy.orm import Session

from app.textbook.models import Unit, Vocab, VocabSense, Sentence, WordType
from app.session.crud import get_user_position, get_seen_tags, UNITS_PER_HSK_LEVEL

MAX_TARGET_WORDS = 8       # a 5-minute chat realistically elicits ~5-8 words
MAX_EXAMPLE_SENTENCES = 5


def get_unit(db: Session, hsk_level: int, unit_number: int) -> Optional[Unit]:
    return db.query(Unit).filter(
        Unit.hsk_level == hsk_level,
        Unit.unit_number == unit_number,
    ).first()


def get_unit_words(db: Session, unit: Unit) -> list[dict]:
    """Every word with a sense INTRODUCED in this unit, with the meaning
    taught here. Goes through VocabSense (not Vocab.unit_id) for the same
    reason textbook.crud does: a word retaught with a new meaning in this
    unit should show up with THAT meaning, not its primary one."""
    rows = (
        db.query(Vocab.hanzi, VocabSense.pinyin, VocabSense.english)
        .join(VocabSense, VocabSense.vocab_id == Vocab.id)
        .filter(
            VocabSense.unit_id == unit.id,
            VocabSense.word_type != WordType.auto,
        )
        .all()
    )
    # a word can have 2 senses in one unit; keep the first so each hanzi appears once
    words = {}
    for hanzi, pinyin, english in rows:
        words.setdefault(hanzi, {"hanzi": hanzi, "pinyin": pinyin, "english": english})
    return list(words.values())


def get_unit_example_sentences(db: Session, unit: Unit, limit: int = MAX_EXAMPLE_SENTENCES) -> list[dict]:
    rows = (
        db.query(Sentence.hanzi, Sentence.english)
        .filter(Sentence.unit_id == unit.id)
        .order_by(Sentence.id)
        .limit(limit)
        .all()
    )
    return [{"hanzi": r.hanzi, "english": r.english} for r in rows]


def pick_target_words(user_db: Session, user_id: int, unit_words: list[dict],
                      limit: int = MAX_TARGET_WORDS) -> list[dict]:
    """Choose which unit words the agent should try to get the user to SAY.
    Words the user has already seen in lessons come first (they can
    recognise them, so speaking them is the natural next step); unseen ones
    fill any remaining slots. Shuffled within each group so repeat sessions
    on the same unit practise different words."""
    seen = get_seen_tags(user_db, user_id, facet="character")
    seen_words = [w for w in unit_words if w["hanzi"] in seen]
    unseen_words = [w for w in unit_words if w["hanzi"] not in seen]
    random.shuffle(seen_words)
    random.shuffle(unseen_words)
    return (seen_words + unseen_words)[:limit]


def get_voice_session_context(user_db: Session, textbook_db: Session, user_id: int) -> dict:
    """Everything build_instructions needs, in one call.

    Two sessions because the data lives in two databases: user progress
    (User, StrengthTable) is in the user DB, curriculum (Unit, Vocab,
    VocabSense, Sentence) is in the textbook DB.

    {
      "hsk_level": 2, "unit": 4, "units_in_level": 15,
      "topic": "...", "title": "...",
      "target_words": [{"hanzi","pinyin","english"}, ...],
      "example_sentences": [{"hanzi","english"}, ...],
    }
    topic/title/words/sentences are None/empty if the unit row is missing
    (e.g. a level the pipeline hasn't loaded yet) -- callers should fall
    back gracefully rather than fail the session.
    """
    position = get_user_position(user_db, user_id) or {"hsk_level": 1, "unit": 1}
    hsk_level, unit_number = position["hsk_level"], position["unit"]

    context = {
        "hsk_level": hsk_level,
        "unit": unit_number,
        "units_in_level": UNITS_PER_HSK_LEVEL.get(hsk_level),
        "topic": None,
        "title": None,
        "target_words": [],
        "example_sentences": [],
    }

    unit = get_unit(textbook_db, hsk_level, unit_number)
    if unit is None:
        return context

    context["topic"] = unit.topic
    context["title"] = unit.title
    unit_words = get_unit_words(textbook_db, unit)
    context["target_words"] = pick_target_words(user_db, user_id, unit_words)
    context["example_sentences"] = get_unit_example_sentences(textbook_db, unit)
    return context