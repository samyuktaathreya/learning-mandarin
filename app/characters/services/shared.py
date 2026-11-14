"""
Shared by the character-practice sub-features:

  char_to_word.py  -- #1 which word does this character belong to?
  confusables.py   -- #2 tell apart look-alike characters (介 vs 开)
  radicals.py      -- #3 common radicals from the user's own words

Holds what they all need (what the user has been taught, strength helpers)
and generate_character_questions(), which decides the mix. The endpoint
imports it from here.

Reads mandarin_app.db (users, StrengthTable), the textbook db (vocab, units,
vocab_senses) and, for #2/#3, characters.db.
"""

import random
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.session.models import StrengthTable

REVIEW_STRENGTH_THRESHOLD = 0.80  # matches REVIEW_THRESHOLD in session.py
NUM_OPTIONS = 4                   # multiple-choice option count for all quiz types
CHAR_TO_WORD_SHARE = 0.30         # per-slot chance a non-radical slot is a character -> word question


# ---------------------------------------------------------------------------
# Strength
# ---------------------------------------------------------------------------

def compute_strength(row: StrengthTable) -> float:
    if not row or not row.stability:
        return 0.0
    elapsed_days = (datetime.utcnow() - row.last_practice).total_seconds() / 86400
    return 0.5 ** (elapsed_days / row.stability)


def get_character_facet_rows(db: Session, user_id: int) -> list[StrengthTable]:
    return (
        db.query(StrengthTable)
        .filter(StrengthTable.user_id == user_id, StrengthTable.facet == "character")
        .all()
    )


def get_character_strengths(db: Session, user_id: int) -> dict[str, float]:
    """{tag: current strength} for every tag on the 'character' facet."""
    return {r.tag: compute_strength(r) for r in get_character_facet_rows(db, user_id)}


# ---------------------------------------------------------------------------
# Curriculum gating
# ---------------------------------------------------------------------------

def _resolve_progress(
    db: Session, user_id: int, unit_number: int | None, hsk_level: int
) -> tuple[int | None, int]:
    """Return (unit, hsk_level) to gate on. An explicit unit_number wins;
    otherwise fall back to users.current_unit / users.hsk_level. Returns
    (None, hsk_level) if progress can't be determined (-> no gating)."""
    if unit_number is not None:
        return unit_number, hsk_level
    row = db.execute(
        text("SELECT current_unit, hsk_level FROM users WHERE id = :uid"),
        {"uid": user_id},
    ).first()
    if not row or row.current_unit is None:
        return None, hsk_level
    return row.current_unit, (row.hsk_level or hsk_level)


_ALLOWED_VOCAB_SQL = text("""
    SELECT v.hanzi
    FROM vocab v
    JOIN units u ON u.id = v.unit_id
    WHERE u.hsk_level < :hsk OR (u.hsk_level = :hsk AND u.unit_number <= :unit)
    UNION
    SELECT v.hanzi
    FROM vocab_senses s
    JOIN vocab v ON v.id = s.vocab_id
    JOIN units u ON u.id = s.unit_id
    WHERE u.hsk_level < :hsk OR (u.hsk_level = :hsk AND u.unit_number <= :unit)
""")


def get_allowed_curriculum(
    db: Session,
    textbook_db: Session,
    user_id: int,
    unit_number: int | None,
    hsk_level: int,
) -> tuple[set[str] | None, set[str] | None]:
    """
    Returns (allowed_tags, allowed_chars), or (None, None) when progress is
    unknown (callers treat None as "don't gate").

      allowed_tags  -- vocab.hanzi taught up to the user's position. A word
                       counts if either the word itself or any of its senses
                       was introduced at or before that point.
      allowed_chars -- every individual character appearing in those words.
    """
    unit, hsk = _resolve_progress(db, user_id, unit_number, hsk_level)
    if unit is None:
        return None, None

    rows = textbook_db.execute(_ALLOWED_VOCAB_SQL, {"hsk": hsk, "unit": unit}).all()
    allowed_tags = {r[0] for r in rows if r[0]}
    allowed_chars = {ch for tag in allowed_tags for ch in tag}
    return allowed_tags, allowed_chars


# ---------------------------------------------------------------------------
# Question mix (the endpoint calls this)
# ---------------------------------------------------------------------------

def generate_character_questions(
    db: Session,
    characters_db: Session,
    textbook_db: Session,
    user_id: int,
    num_questions: int = 2,
    unit_number: int = None,
    hsk_level: int = 1,
) -> list[dict]:
    """
    Generates up to `num_questions` character/radical quiz questions.

    Called from generate_session (appends a small fixed portion to every
    session) and can also be called directly with a larger num_questions for
    a standalone character-practice page/endpoint.

    unit_number/hsk_level are optional: pass them if this character quiz is
    being generated within a specific curriculum context (e.g. after
    completing a unit), so that multi-sense words resolve to their relevant
    taught meaning. Omit for a general character-practice call.

    Regardless of whether they are passed, character (word) targets are gated
    to what the user has been taught: if unit_number is omitted, the user's
    current_unit / hsk_level are read from the users table. Radicals are not
    gated.
    """
    # Imported here, not at the top: the sub-feature modules import the
    # shared helpers from this file, so a top-level import would loop.
    from app.characters.services import char_to_word, confusables, radicals

    questions: list[dict] = []
    used_tags: set[str] = set()

    allowed_tags, allowed_chars = get_allowed_curriculum(
        db, textbook_db, user_id, unit_number, hsk_level
    )

    # Step 1: radical gating -- at most one radical question per call
    radical_tag = radicals.pick_radical_to_ask(db, characters_db, user_id)
    if radical_tag:
        q = radicals.build_radical_meaning_question(radical_tag, characters_db)
        if q:
            questions.append(q)
            used_tags.add(radical_tag)

    # Step 2: character -> word ("which word is 介 from?"). Each remaining slot
    # independently has a CHAR_TO_WORD_SHARE chance of being this type, so it
    # still shows up when a session only has 1-2 slots left (a fixed
    # round(remaining * share) would be 0 there).
    remaining = num_questions - len(questions)
    word_quota = sum(random.random() < CHAR_TO_WORD_SHARE for _ in range(remaining))
    for q in char_to_word.generate_character_to_word_questions(
        db, user_id, allowed_tags, word_quota, exclude_tags=used_tags
    ):
        questions.append(q)
        used_tags.update(q["tags"])

    # Step 3: fill the rest with character questions (lowest strength first)
    remaining = num_questions - len(questions)
    if remaining > 0:
        candidate_tags = confusables.get_lowest_strength_character_tags(
            db, user_id, exclude_tags=used_tags, limit=remaining * 3,  # extra buffer in case a builder fails
            allowed_tags=allowed_tags, allowed_chars=allowed_chars,
        )

        for tag in candidate_tags:
            if len(questions) >= num_questions:
                break
            if tag in used_tags:
                continue

            # ~70% spot-the-difference, ~30% pinyin -> character
            if random.random() < 0.70:
                q = confusables.build_spot_the_difference_question(tag, db, characters_db,
                                                       allowed_chars=allowed_chars)
                if not q:
                    q = confusables.build_pinyin_to_character_question(textbook_db, tag, characters_db,
                                                            unit_number=unit_number, hsk_level=hsk_level,
                                                            allowed_chars=allowed_chars)
            else:
                q = confusables.build_pinyin_to_character_question(textbook_db, tag, characters_db,
                                                        unit_number=unit_number, hsk_level=hsk_level,
                                                        allowed_chars=allowed_chars)
                if not q:
                    q = confusables.build_spot_the_difference_question(tag, db, characters_db,
                                                           allowed_chars=allowed_chars)
            if q:
                questions.append(q)
                used_tags.add(tag)

    return questions