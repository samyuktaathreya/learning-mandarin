# app/textbook/services.py
"""
Previously: loaded 5 JSON files at import time (unit_questions,
unit_to_vocab_tags_dict, hsk1_dictionary, word_to_pinyin) and built four
in-memory indexes from them (inverted_index, tags_to_unit_dict,
unit_to_tags_dict, unique_vocab_tags).

Now: all of that data lives in the DB and is queried on demand through
crud.py, which handles its own caching (see crud._TTLCache) so this module
doesn't need to hold anything in RAM at import time.

What's KEPT here: the constants that had nothing to do with JSON loading
(META_TAGS, QUESTION_TYPES, FACETS) -- these describe the domain, not the
data source, so they don't move.

What's GONE: every `with open(...) as f: json.load(f)` block, and the loop
that built inverted_index/tags_to_unit_dict/unit_to_tags_dict/
unique_vocab_tags by walking unit_questions.

CALLER IMPACT: any code that did

    from textbook.services import inverted_index, unit_to_vocab_tags_dict
    unit_tags = unit_to_vocab_tags_dict.get(unit, set())
    questions = inverted_index.get(tag, [])

now needs a `db: Session` and calls the crud functions instead:

    from textbook import crud
    unit_tags = crud.get_vocab_tags_for_unit(db, unit)
    questions = crud.get_questions_for_tag(db, tag, unit)

This module re-exports the crud functions under their old-ish names (see
below) purely so the diff in calling code is smaller -- direct crud imports
are equally fine and arguably clearer going forward.
"""
from sqlalchemy.orm import Session

from textbook import crud

# --------------------------------- CONSTANTS (unchanged) ---------------------------------

META_TAGS = {
    "speaking_vocab", "speaking_sentence", "listening_vocab",
    "listening_sentence", "fill_in_the_blank", "transcribe_word_to_pinyin",
    "translate_chinese_word_to_english", "translate_chinese_sentence_to_english",
    "translate_english_word_to_chinese", "translate_english_sentence_to_chinese",
    "transcribe_hanzi_to_pinyin",
}

QUESTION_TYPES = [
    "listening sentence",
    "speaking sentence",
    "speaking vocab",
    "listening vocab",
    "transcribe word to pinyin",
    "translate english sentence to chinese",
    "translate english word to chinese",
    "fill in the blank",
    "translate chinese sentence to english",
    "translate chinese word to english",
    "transcribe hanzi to pinyin",
]

FACETS = ("character", "pinyin")

QUESTION_TYPE_FACETS = crud.QUESTION_TYPE_FACETS  # re-exported; crud.py itself imports this from session.constants (single source of truth), doesn't redefine it


# --------------------------------- DB-BACKED LOOKUPS (thin re-exports) ---------------------------------
# These just forward to crud.py. Kept here under names close to the old
# globals so callers can update `unit_to_vocab_tags_dict.get(unit, set())`
# to `get_unit_vocab_tags(db, unit)` rather than rewiring imports entirely.
# New code should probably just import crud directly instead of going
# through this indirection layer.

def get_unit_vocab_tags(db: Session, unit: int, hsk_level: int = 1) -> set:
    """Replaces unit_to_vocab_tags_dict.get(unit, set())."""
    return crud.get_vocab_tags_for_unit(db, unit, hsk_level)


def get_tag_home_unit(db: Session, tag: str) -> int | None:
    """Replaces tags_to_unit_dict.get(tag)."""
    return crud.get_tags_to_unit_map(db).get(tag)


def get_unit_tags(db: Session, unit: int) -> set:
    """Replaces unit_to_tags_dict.get(unit, set())."""
    return crud.get_unit_to_tags_map(db).get(unit, set())


def get_questions_for_tag(db: Session, tag: str, unit: int, hsk_level: int, question_type: str = None) -> list:
    """Replaces inverted_index.get(tag, []) (already unit-filtered, unlike
    the old inverted_index which needed a separate `q.get("unit") == unit`
    filter step at the call site -- see crud.get_questions_for_tag)."""
    return crud.get_questions_for_tag(db, tag, unit, hsk_level, question_type)


def get_questions_for_tag_up_to_unit(db: Session, tag: str, max_unit: int, question_type: str = None, hsk_level: int = 1) -> list:
    """Replaces the old `inverted_index.get(tag, [])` filtered inline by
    `q.get("unit", 0) <= max_unit` -- used by review_engine.py, where a due
    review word can be quizzed using ANY unit's question bank up to the
    learner's current unit (and any HSK level strictly below it), not just
    its home unit."""
    return crud.get_questions_for_tag_up_to_unit(
        db, tag, max_unit, max_hsk_level=hsk_level, question_type=question_type
    )


def get_all_questions_for_unit(db: Session, unit_number: int) -> list:
    """Replaces `unit_questions.get(str(unit), [])` -- used by
    generate_unit_test, which needs every question in a unit regardless of
    tag, then filters to ALL_TIER_QUESTION_TYPES itself."""
    return crud.get_all_questions_for_unit(db, unit_number)


def get_dictionary_entry(db: Session, hanzi: str) -> dict | None:
    """Replaces hsk1_dictionary.get(hanzi)."""
    return crud.get_vocab_definition(db, hanzi)


def get_pinyin(db: Session, hanzi: str) -> str | None:
    """Replaces word_to_pinyin.get(hanzi)."""
    return crud.get_pinyin_for_word(db, hanzi)


def get_all_vocab_tags(db: Session) -> set:
    """Replaces unique_vocab_tags."""
    return crud.get_all_vocab_hanzi(db)


def get_all_unit_numbers(db: Session, hsk_level) -> list:
    """Replaces iterating unit_questions.keys()."""
    return crud.get_all_unit_numbers(db, hsk_level)


def lookup_word(db: Session, hanzi: str) -> dict:
    """Replaces the inline dictionary-lookup-with-pypinyin-fallback logic
    that used to live directly in router.py's /api/lookup endpoint (moved
    here since it's fundamentally a textbook-data concern, not a routing
    concern -- see the router.py rewrite for the corresponding thin
    endpoint).

    Looks up `hanzi` in the Vocab table first (fast, exact, has an English
    definition). Falls back to pypinyin's auto-generated reading if not
    found in Vocab -- same fallback the old code did against
    hsk1_dictionary, just against the DB now. english is None on the
    fallback path since pypinyin has no definitions, same as before.
    """
    entry = crud.get_vocab_definition(db, hanzi)
    if entry:
        return {"hanzi": hanzi, "pinyin": entry["pinyin"], "english": entry["english"]}

    from pypinyin import pinyin, Style
    try:
        result = pinyin(hanzi, style=Style.TONE3, heteronym=False)
        py = "".join([s[0] for s in result]).lower()
    except Exception:
        py = None
    return {"hanzi": hanzi, "pinyin": py, "english": None}


def clear_cache():
    """Forward to crud's cache clear -- call after the data pipeline reruns
    so the app picks up new/changed curriculum data without a restart."""
    crud.clear_cache()