"""
#3 Teach common radicals that appear in the user's own words.

TODO: rewrite. For now this is the existing radical_meaning question, moved
here unchanged so the endpoint keeps working. It is NOT yet limited to
common radicals or to radicals in words the user has learned.
"""

import random

from sqlalchemy.orm import Session

from app.characters.models import RadicalMeta
from app.characters.services.shared import (
    NUM_OPTIONS,
    REVIEW_STRENGTH_THRESHOLD,
    compute_strength,
    get_character_facet_rows as _get_character_facet_rows,
)


# ---------------------------------------------------------------------------
# Radical selection (gating logic)
# ---------------------------------------------------------------------------

def _get_all_radical_chars(characters_db: Session) -> list[str]:
    """Only chars that have real radical_meta rows count as askable radicals
    (variants inserted alongside radicals are is_radical=1 but have no
    meta/meaning, so they're excluded from being quiz targets -- they only
    exist to serve as confusible distractors)."""
    rows = characters_db.query(RadicalMeta.char).all()
    return [r.char for r in rows]


def pick_radical_to_ask(db: Session, characters_db: Session, user_id: int) -> str | None:
    radical_chars = _get_all_radical_chars(characters_db)
    if not radical_chars:
        return None

    strength_rows = _get_character_facet_rows(db, user_id)
    strength_by_tag = {r.tag: r for r in strength_rows}

    seen_radicals = [
        strength_by_tag[c] for c in radical_chars
        if c in strength_by_tag and (strength_by_tag[c].times_seen or 0) >= 1
    ]

    if seen_radicals:
        scored = [(r, compute_strength(r)) for r in seen_radicals]
        weak = [r for r, s in scored if s < REVIEW_STRENGTH_THRESHOLD]
        if weak:
            weak.sort(key=lambda r: compute_strength(r))
            return weak[0].tag

    seen_tags = {r.tag for r in seen_radicals}
    unseen = [c for c in radical_chars if c not in seen_tags]
    if unseen:
        return random.choice(unseen)

    return None  # every radical seen and strong -- no radical question this round


def build_radical_meaning_question(radical_char: str, characters_db: Session) -> dict | None:
    """Show an English meaning, ask the user to pick the matching radical."""
    meta = (
        characters_db.query(RadicalMeta)
        .filter(RadicalMeta.char == radical_char)
        .first()
    )
    if not meta or not meta.english:
        return None

    all_radicals = _get_all_radical_chars(characters_db)
    other_radicals = [r for r in all_radicals if r != radical_char]
    random.shuffle(other_radicals)

    options = {radical_char}
    options.update(other_radicals[: NUM_OPTIONS - 1])

    options = list(options)
    if len(options) < 2:
        return None

    random.shuffle(options)
    return {
        "id": f"charquiz_radical_{radical_char}_{random.randint(0, 999999)}",
        "question_type": "radical_meaning",
        "question": f"Which radical means '{meta.english}'?",
        "options": options,
        "answer": radical_char,
        "tags": [radical_char],
    }