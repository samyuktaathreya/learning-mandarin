"""
#2 Tell apart look-alike characters (介 vs 开, 进 vs 送).

TODO: rewrite. For now this is the existing spot-the-difference and
pinyin -> character code, moved here unchanged so the endpoint keeps working:

  - character_spot_difference: pick the option that means X
  - character_pinyin_to_char:  pick the option pronounced X

Distractors come from characters.db (confusion_pairs, character_components).
"""

import random
import re
from itertools import product

from pypinyin import pinyin as pypinyin_readings, Style
from sqlalchemy.orm import Session

from app.shared.crud import get_dictionary_entries
from app.characters.models import Character
import characters.crud
from app.textbook.services import get_pinyin, META_TAGS
from app.characters.services.shared import (
    NUM_OPTIONS,
    compute_strength,
    get_character_facet_rows as _get_character_facet_rows,
)


# ---------------------------------------------------------------------------
# Character selection (lowest strength first)
# ---------------------------------------------------------------------------

def get_lowest_strength_character_tags(
    db: Session,
    user_id: int,
    exclude_tags: set[str],
    limit: int,
    allowed_tags: set[str] | None = None,
    allowed_chars: set[str] | None = None,
) -> list[str]:
    """Lowest-strength tags on the 'character' facet, excluding meta tags,
    unit tags, and anything already picked this round (e.g. the radical).

    If allowed_tags is given, only tags the user has been taught qualify: a
    tag passes if it is a taught word, or a single character appearing in a
    taught word (allowed_chars). None means no curriculum gating."""
    rows = _get_character_facet_rows(db, user_id)

    def _in_curriculum(tag: str) -> bool:
        if allowed_tags is None:
            return True
        if tag in allowed_tags:
            return True
        return len(tag) == 1 and allowed_chars is not None and tag in allowed_chars

    candidates = [
        r for r in rows
        if r.tag not in META_TAGS
        and not r.tag.startswith("unit_")
        and r.tag not in exclude_tags
        and _in_curriculum(r.tag)
    ]
    candidates.sort(key=compute_strength)
    return [r.tag for r in candidates[:limit]]


# ---------------------------------------------------------------------------
# Distractor generation
# ---------------------------------------------------------------------------

def _collect_similar(
    characters_db: Session, char: str, limit: int, allowed_chars: set[str] | None
) -> list[str]:
    """Confusibles first, then IDS structural matches, optionally restricted
    to allowed_chars. No random fallback here."""
    similar = characters.crud.get_confusibles(characters_db, char, allowed_chars=allowed_chars)

    if len(similar) < limit:
        ids_matches = characters.crud.get_similar_by_components(
            characters_db, char, depth=0, max_frequency=50, limit=limit,
            allowed_chars=allowed_chars,
        )
        for m in ids_matches:
            if m["char"] not in similar and m["char"] != char:
                similar.append(m["char"])
    return similar


def _get_similar_chars(
    characters_db: Session,
    char: str,
    limit: int = 5,
    allowed_chars: set[str] | None = None,
) -> list[str]:
    """Best-effort similar-looking characters for a single char: confusibles
    first (human-curated, most reliable), then IDS structural similarity as
    fallback, then random other characters if we still don't have enough.

    If allowed_chars is given, characters the user has been taught are
    preferred; unrestricted similar characters only top up what's missing, so
    questions still get built for early units where few matches are allowed."""
    similar = _collect_similar(characters_db, char, limit, allowed_chars)

    if allowed_chars is not None and len(similar) < limit:
        for c in _collect_similar(characters_db, char, limit, None):
            if c not in similar and c != char:
                similar.append(c)

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


def _build_word_distractor(word: str, characters_db: Session,
                           allowed_chars: set[str] | None = None) -> str | None:
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
    similar = _get_similar_chars(characters_db, chars[idx], limit=3, allowed_chars=allowed_chars)
    if not similar:
        return None
    new_chars = chars.copy()
    new_chars[idx] = random.choice(similar)
    candidate = "".join(new_chars)
    return candidate if candidate != word else None


def _normalize_pinyin(p: str) -> str:
    """'Ta1 men5' -> 'ta1men5'; ü/u:/v all -> 'v', the spelling pypinyin uses."""
    return p.lower().replace(" ", "").replace("ü", "v").replace("u:", "v")


def _sounds_like(candidate: str, numbered_pinyin: str) -> bool:
    """True if `candidate` can be read exactly as `numbered_pinyin`, tones
    included -- e.g. 他们 for 'ta1men5' (她们). Every reading of a heteronym
    counts, so a distractor is dropped if ANY of its readings matches."""
    per_char = pypinyin_readings(candidate, style=Style.TONE3, heteronym=True,
                                 neutral_tone_with_five=True)
    target = _normalize_pinyin(numbered_pinyin)
    return any(_normalize_pinyin("".join(r)) == target for r in product(*per_char))


def _core_meaning(definition: str) -> str:
    """'they (females)' -> 'they', so near-synonyms like 他们/她们 compare equal."""
    return re.sub(r"\([^)]*\)", "", definition).strip().lower()


def _means(db: Session, candidate: str, meaning: str) -> bool:
    """True if any dictionary definition of `candidate` is `meaning`, ignoring
    bracketed notes -- e.g. 他们 'they' vs 她们 'they (females)', which would
    make a second right answer."""
    meaning = _core_meaning(meaning)
    return any(
        _core_meaning(d) == meaning
        for e in get_dictionary_entries(db, candidate)
        for d in e.english.split("/")
    )


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


def build_spot_the_difference_question(tag: str, db: Session, characters_db: Session,
                                       allowed_chars: set[str] | None = None) -> dict | None:
    """Show N options, ask the user to pick the one meaning `tag`'s English
    definition -- NOT the tag itself, since writing the hanzi in the question
    text would give away the answer."""
    meaning = _get_english_meaning(db, tag)
    if not meaning:
        return None  # no dictionary entry -- can't build a safe non-giveaway question

    options = {tag}

    if len(tag) == 1:
        # Ask for extra, since synonyms get filtered out
        similar = _get_similar_chars(characters_db, tag, limit=(NUM_OPTIONS - 1) * 2,
                                     allowed_chars=allowed_chars)
        options.update([c for c in similar if not _means(db, c, meaning)][:NUM_OPTIONS - 1])
    else:
        attempts = 0
        while len(options) < NUM_OPTIONS and attempts < NUM_OPTIONS * 3:
            attempts += 1
            distractor = _build_word_distractor(tag, characters_db, allowed_chars=allowed_chars)
            if distractor and not _means(db, distractor, meaning):
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
                                       unit_number: int = None, hsk_level: int = 1,
                                       allowed_chars: set[str] | None = None) -> dict | None:
    """Build a pinyin -> character question. unit_number/hsk_level are optional:
    pass them if this question is being asked in a specific curriculum context
    so that a multi-sense word resolves to its relevant taught meaning."""
    pinyin = get_pinyin(db, tag, unit_number=unit_number, hsk_level=hsk_level)
    if not pinyin:
        return None  # can't ask this type without a known pinyin reading

    options = {tag}
    if len(tag) == 1:
        # Ask for extra, since homophones (e.g. 他/她/它) get filtered out
        similar = _get_similar_chars(characters_db, tag, limit=(NUM_OPTIONS - 1) * 2,
                                     allowed_chars=allowed_chars)
        options.update([c for c in similar if not _sounds_like(c, pinyin)][:NUM_OPTIONS - 1])
    else:
        attempts = 0
        while len(options) < NUM_OPTIONS and attempts < NUM_OPTIONS * 3:
            attempts += 1
            distractor = _build_word_distractor(tag, characters_db, allowed_chars=allowed_chars)
            if distractor and not _sounds_like(distractor, pinyin):
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