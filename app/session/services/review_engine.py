import random
from datetime import datetime

from sqlalchemy.orm import Session

from session import crud
from session.constants import (
    MAX_TIER,
    GRADUATION_THRESHOLD,
    REVIEW_THRESHOLD,
    REVIEW_TYPES_BY_FACET,
)
from textbook import services as textbook_services

def is_facet_review_eligible(tier: int, facet_count: int) -> bool:
    return tier >= MAX_TIER and facet_count >= GRADUATION_THRESHOLD


def _all_review_eligible_facets(db: Session, textbook_db: Session, user_id: int, hsk_level: int = 1) -> list:
    """Every (word, facet) pair that is SERVING-eligible for review: tier 4 +
    facet_count >= GRADUATION_THRESHOLD, AND the word's teaching unit is
    strictly before the current unit. A word still being learned in the current
    unit is never review-eligible -- review is for consolidated, past-unit
    material only, and its sentences are guaranteed to contain only known
    words.

    Now takes `textbook_db` -- was: `tags_to_unit_dict.get(r.tag)` against a
    module-level dict loaded from unit_vocab_tags.json at import time; now:
    `textbook_services.get_tag_home_unit(textbook_db, r.tag)`, a cached DB
    lookup (see textbook/crud.py's get_tags_to_unit_map)."""
    progress = crud.get_progress_by_user(db, user_id)
    if not progress:
        return []

    tags = {r.tag for r in progress}
    tiers = crud.get_tiers_for_tags(db, user_id, tags)
    current_unit = crud.get_user(db, user_id).current_unit

    eligible = []
    for r in progress:
        if r.facet not in ("character", "pinyin"):
            continue
        if not is_facet_review_eligible(tiers.get(r.tag, 1), r.correct_count):
            continue
        teaching_unit = textbook_services.get_tag_home_unit(textbook_db, r.tag, hsk_level)
        if teaching_unit is None or teaching_unit >= current_unit:
            continue  # word's own unit isn't finished -- not review-eligible yet
        eligible.append((r.tag, r.facet, r))
    return eligible


def _due_review_facets(db: Session, textbook_db: Session, user_id: int, hsk_level: int = 1) -> list:
    now = datetime.utcnow()
    scored = []
    for tag, facet, r in _all_review_eligible_facets(db, textbook_db, user_id, hsk_level):
        strength = 0.5 ** ((now - r.last_practice).total_seconds() / 86400 / r.stability)
        if strength < REVIEW_THRESHOLD:
            scored.append((tag, facet, strength))
    scored.sort(key=lambda x: x[2])
    return [(tag, facet) for tag, facet, _ in scored]


def _pick_review_question(textbook_db: Session, tag: str, facet: str, used_ids: set,
                          max_unit: int, seen_counts: dict, hsk_level: int = 1):
    """Review picker, routed by which facet is actually due.

    pinyin facet due: 80% "transcribe hanzi to pinyin" (isolated recall --
        character shown, no meaning/audio cue, learner must produce tones cold).
        20% other types that still exercise pinyin production/recognition
        (listening sentence, speaking sentence), as a fallback/variety pool.

    character facet due: 80% "translate english word to chinese" (meaning ->
        character via IME -- the closest thing to isolated character recall
        this format allows). 20% other types that still require producing/
        reading the character in context (translate english sentence to
        chinese, fill in the blank).

    Note: "transcribe hanzi to pinyin" only advances the pinyin facet
    (see QUESTION_TYPE_FACETS in session/constants.py) -- it shows the
    character already, so it doesn't test character recall.

    Was: `inverted_index.get(tag, [])` filtered inline by question_type,
    used_ids, and `q.get("unit", 0) <= max_unit`. Now:
    `textbook_services.get_questions_for_tag_up_to_unit(textbook_db, tag,
    max_unit, question_type=qt)`, which does the unit-range filtering as
    part of the query itself -- see textbook/crud.py's
    get_questions_for_tag_up_to_unit. Only the used_ids exclusion still
    needs to happen here.
    """
    if facet == "pinyin":
        primary = "transcribe hanzi to pinyin"
        fallback_pool = list(REVIEW_TYPES_BY_FACET["pinyin"])
    else:  # facet == "character"
        primary = "translate english word to chinese"
        fallback_pool = list(REVIEW_TYPES_BY_FACET["character"])

    random.shuffle(fallback_pool)
    if random.random() < 0.80:
        ordered_types = [primary] + fallback_pool
    else:
        ordered_types = fallback_pool + [primary]

    for qt in ordered_types:
        pool = [
            q for q in textbook_services.get_questions_for_tag_up_to_unit(
                textbook_db, tag, max_unit, question_type=qt, hsk_level=hsk_level
            )
            if q["id"] not in used_ids
        ]
        if not pool:
            continue

        unseen = [q for q in pool if q["id"] not in seen_counts]
        if unseen:
            return random.choice(unseen)

        min_shown = min(seen_counts.get(q["id"], 0) for q in pool)
        least_shown = [q for q in pool if seen_counts.get(q["id"], 0) == min_shown]
        return random.choice(least_shown)

    return None


def generate_review_questions(db: Session, textbook_db: Session, user_id, used_ids, limit=None):
    user = crud.get_user(db, user_id)
    hsk_level = getattr(user, "hsk_level", 1)
    max_unit = user.current_unit - 1
    seen_counts = crud.get_seen_question_counts(db, user_id)
    picks = []
    for tag, facet in _due_review_facets(db, textbook_db, user_id, hsk_level):
        if limit is not None and len(picks) >= limit:
            break
        q = _pick_review_question(textbook_db, tag, facet, used_ids, max_unit, seen_counts, hsk_level)
        if q:
            picks.append((q, tag))
            used_ids.add(q["id"])
    return picks


def _project_strength(r, days_ahead: float) -> float:
    now = datetime.utcnow()
    elapsed_days = (now - r.last_practice).total_seconds() / 86400 + days_ahead
    return 0.5 ** (elapsed_days / r.stability)


def review_due_word_count(db: Session, textbook_db: Session, user_id) -> int:
    hsk_level = getattr(crud.get_user(db, user_id), "hsk_level", 1)
    return len({tag for tag, _facet in _due_review_facets(db, textbook_db, user_id, hsk_level)})


def review_due_tomorrow_word_count(db: Session, textbook_db: Session, user_id) -> int:
    hsk_level = getattr(crud.get_user(db, user_id), "hsk_level", 1)
    tags = set()
    for tag, facet, r in _all_review_eligible_facets(db, textbook_db, user_id, hsk_level):
        if _project_strength(r, 1.0) < REVIEW_THRESHOLD:
            tags.add(tag)
    return len(tags)