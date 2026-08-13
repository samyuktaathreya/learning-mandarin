"""
Generates character-recognition and radical-recognition quiz questions.

Reads strength data from mandarin_app.db (via crud.py / StrengthTable, same
facet="character" used everywhere else) and similarity/confusible data from
characters.db (via characters.crud.py) to build distractors.

Two question shapes for characters (see pedagogical discussion):
  - "character_spot_difference": show N similar-looking options, pick the
    one that matches the given word/character. Pure visual recognition.
  - "character_pinyin_to_char": show pinyin, pick the correct character/word
    from similar-looking options. Sound -> visual mapping.

One question shape for radicals:
  - "radical_meaning": show an English meaning, pick the correct radical from
    other radicals as distractors.

Radical gating (see design discussion): only ask a radical question if
  (a) the user has seen a radical whose strength is below 0.80 -> ask lowest
      strength one, OR
  (b) all seen radicals are >= 0.80 AND there are unseen radicals -> ask one
      unseen radical at random, OR
  (c) otherwise -> no radical question this round; fill with characters.

Both radical and character questions update the SAME "character" facet in
StrengthTable -- there is no separate radical facet. The tag stored is the
radical/word actually being tested, not the distractor characters.

SENSE-AWARE UPDATE: optional unit_number/hsk_level parameters threaded through
so that if a character quiz is asked within a curriculum context (e.g. after a
specific unit), it can resolve to the relevant taught meaning rather than always
the primary sense. Most callers won't have this context, so defaults are fine.
"""

import random
from datetime import datetime

from sqlalchemy.orm import Session

from shared.crud import get_dictionary_entries
from session.models import StrengthTable
from characters.models import Character, RadicalMeta
import characters.crud
from textbook.services import get_pinyin, META_TAGS

REVIEW_STRENGTH_THRESHOLD = 0.80  # matches REVIEW_THRESHOLD in session.py
NUM_OPTIONS = 4                   # multiple-choice option count for all quiz types


# ---------------------------------------------------------------------------
# Strength helpers (mirrors the formula used in session.py's facet_detail)
# ---------------------------------------------------------------------------

def compute_strength(row: StrengthTable) -> float:
    if not row or not row.stability:
        return 0.0
    now = datetime.utcnow()
    elapsed_days = (now - row.last_practice).total_seconds() / 86400
    return 0.5 ** (elapsed_days / row.stability)


def _get_character_facet_rows(db: Session, user_id: int) -> list[StrengthTable]:
    return (
        db.query(StrengthTable)
        .filter(StrengthTable.user_id == user_id, StrengthTable.facet == "character")
        .all()
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


# ---------------------------------------------------------------------------
# Character selection (lowest strength first)
# ---------------------------------------------------------------------------

def get_lowest_strength_character_tags(
    db: Session,
    user_id: int,
    exclude_tags: set[str],
    limit: int,
) -> list[str]:
    """Lowest-strength tags on the 'character' facet, excluding meta tags,
    unit tags, and anything already picked this round (e.g. the radical)."""
    rows = _get_character_facet_rows(db, user_id)
    candidates = [
        r for r in rows
        if r.tag not in META_TAGS
        and not r.tag.startswith("unit_")
        and r.tag not in exclude_tags
    ]
    candidates.sort(key=compute_strength)
    return [r.tag for r in candidates[:limit]]


# ---------------------------------------------------------------------------
# Distractor generation
# ---------------------------------------------------------------------------

def _get_similar_chars(characters_db: Session, char: str, limit: int = 5) -> list[str]:
    """Best-effort similar-looking characters for a single char: confusibles
    first (human-curated, most reliable), then IDS structural similarity as
    fallback, then random other characters if we still don't have enough."""
    similar = characters.crud.get_confusibles(characters_db, char)

    if len(similar) < limit:
        ids_matches = characters.crud.get_similar_by_components(
            characters_db, char, depth=0, max_frequency=50, limit=limit
        )
        for m in ids_matches:
            if m["char"] not in similar and m["char"] != char:
                similar.append(m["char"])

    if len(similar) < limit:
        # last-resort fallback: random other characters from the corpus
        fallback = (
            characters_db.query(Character.char)
            .filter(Character.char != char)
            .order_by(Character.codepoint)  # deterministic; shuffled below
            .limit(limit * 3)
            .all()
        )
        pool = [f.char for f in fallback if f.char not in similar]
        random.shuffle(pool)
        similar.extend(pool[: limit - len(similar)])

    return similar[:limit]


def _build_word_distractor(word: str, characters_db: Session) -> str | None:
    """For a multi-character word, build one plausible-wrong variant by either
    (a) swapping one character for a similar-looking one, or (b) reversing
    character order -- e.g. 中国 -> 中因 or 国中."""
    chars = list(word)
    if len(chars) < 2:
        return None

    strategy = random.choice(["swap_char", "swap_order"]) if len(set(chars)) > 1 else "swap_char"

    if strategy == "swap_order":
        reversed_word = "".join(reversed(chars))
        if reversed_word != word:
            return reversed_word
        # fall through to swap_char if reversing produced the same string
        # (e.g. a doubled character like 平平)

    # swap_char: replace one character with a similar-looking one
    idx = random.randrange(len(chars))
    similar = _get_similar_chars(characters_db, chars[idx], limit=3)
    if not similar:
        return None
    new_chars = chars.copy()
    new_chars[idx] = random.choice(similar)
    candidate = "".join(new_chars)
    return candidate if candidate != word else None


# ---------------------------------------------------------------------------
# Question builders
# ---------------------------------------------------------------------------

def _get_english_meaning(db: Session, tag: str) -> str | None:
    """Look up an English gloss for `tag` from the CC-CEDICT dictionary table
    (mandarin_app.db), so quiz questions can anchor on meaning instead of
    showing the hanzi answer itself. Takes the first entry's first definition
    if multiple entries/definitions exist."""
    entries = get_dictionary_entries(db, tag)
    if not entries:
        return None
    first_def = entries[0].english.split("/")[0].strip()
    return first_def or None


def build_spot_the_difference_question(tag: str, db: Session, characters_db: Session) -> dict | None:
    """Show N options, ask the user to pick the one meaning `tag`'s English
    definition -- NOT the tag itself, since writing the hanzi in the question
    text would give away the answer."""
    meaning = _get_english_meaning(db, tag)
    if not meaning:
        return None  # no dictionary entry -- can't build a safe non-giveaway question

    options = {tag}

    if len(tag) == 1:
        similar = _get_similar_chars(characters_db, tag, limit=NUM_OPTIONS - 1)
        options.update(similar)
    else:
        attempts = 0
        while len(options) < NUM_OPTIONS and attempts < NUM_OPTIONS * 3:
            attempts += 1
            distractor = _build_word_distractor(tag, characters_db)
            if distractor:
                options.add(distractor)

    options = list(options)
    if len(options) < 2:
        return None  # not enough distractors to make a meaningful question

    random.shuffle(options)
    return {
        "id": f"charquiz_spot_{tag}_{random.randint(0, 999999)}",
        "question_type": "character_spot_difference",
        "question": f"Which one means '{meaning}'?",
        "options": options,
        "answer": tag,
        "tags": [tag],
    }


def build_pinyin_to_character_question(db: Session, tag: str, characters_db: Session,
                                       unit_number: int = None, hsk_level: int = 1) -> dict | None:
    """Build a pinyin -> character question. unit_number/hsk_level are optional:
    pass them if this question is being asked in a specific curriculum context
    so that a multi-sense word resolves to its relevant taught meaning."""
    pinyin = get_pinyin(db, tag, unit_number=unit_number, hsk_level=hsk_level)
    if not pinyin:
        return None  # can't ask this type without a known pinyin reading

    options = {tag}
    if len(tag) == 1:
        options.update(_get_similar_chars(characters_db, tag, limit=NUM_OPTIONS - 1))
    else:
        attempts = 0
        while len(options) < NUM_OPTIONS and attempts < NUM_OPTIONS * 3:
            attempts += 1
            distractor = _build_word_distractor(tag, characters_db)
            if distractor:
                options.add(distractor)

    options = list(options)
    if len(options) < 2:
        return None

    random.shuffle(options)
    return {
        "id": f"charquiz_pinyin_{tag}_{random.randint(0, 999999)}",
        "question_type": "character_pinyin_to_char",
        "question": f"Which one is pronounced '{pinyin}'?",
        "options": options,
        "answer": tag,
        "tags": [tag],
    }


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


# ---------------------------------------------------------------------------
# Main entry point
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
    """
    questions: list[dict] = []
    used_tags: set[str] = set()

    # Step 1: radical gating -- at most one radical question per call
    radical_tag = pick_radical_to_ask(db, characters_db, user_id)
    if radical_tag:
        q = build_radical_meaning_question(radical_tag, characters_db)
        if q:
            questions.append(q)
            used_tags.add(radical_tag)

    # Step 2: fill the rest with character questions (lowest strength first)
    remaining = num_questions - len(questions)
    if remaining > 0:
        candidate_tags = get_lowest_strength_character_tags(
            db, user_id, exclude_tags=used_tags, limit=remaining * 3  # extra buffer in case a builder fails
        )

        for tag in candidate_tags:
            if len(questions) >= num_questions:
                break
            if tag in used_tags:
                continue

            # ~70% spot-the-difference, ~30% pinyin -> character
            if random.random() < 0.70:
                q = build_spot_the_difference_question(tag, db, characters_db)
                if not q:
                    q = build_pinyin_to_character_question(textbook_db, tag, characters_db,
                                                            unit_number=unit_number, hsk_level=hsk_level)
            else:
                q = build_pinyin_to_character_question(textbook_db, tag, characters_db,
                                                        unit_number=unit_number, hsk_level=hsk_level)
                if not q:
                    q = build_spot_the_difference_question(tag, db, characters_db)
            if q:
                questions.append(q)
                used_tags.add(tag)

    return questions