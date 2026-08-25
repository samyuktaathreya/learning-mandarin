# app/textbook/services.py
"""
Previously: loaded 5 JSON files at import time (unit_questions,
unit_to_vocab_tags_dict, hsk1_dictionary, word_to_pinyin) and built four
in-memory indexes from them (inverted_index, tags_to_unit_dict,
unit_to_tags_dict, unique_vocab_tags).

Now: all of that data lives in the DB and is queried on demand through
crud.py, which handles its own caching (see crud._TTLCache) so this module
doesn't need to hold anything in RAM at import time.

SENSE-AWARE UPDATE: a word can now have several taught meanings (see
textbook.models.VocabSense), each with its own pinyin/english/home unit --
crud.py resolves "which meaning is relevant" per call rather than a word
having one fixed definition forever. The lookup functions here take an
optional `unit_number`/`hsk_level` so a caller who knows WHERE in the
curriculum the lookup is happening (a specific sentence, a specific
learner's current unit) gets back whichever meaning is actually relevant
there, instead of always the word's overall primary sense. lookup_word in
particular now also returns `other_definitions`, so a dictionary-style UI
can show the relevant meaning first and the rest underneath rather than
picking exactly one and hiding everything else.

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
from typing import Optional

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


def get_dictionary_entry(db: Session, hanzi: str, unit_number: Optional[int] = None,
                          hsk_level: int = 1) -> dict | None:
    """Replaces hsk1_dictionary.get(hanzi). Returns the single most
    RELEVANT definition: pass unit_number when you know where in the
    curriculum this lookup is happening (e.g. a word clicked inside a
    specific sentence) so a multi-sense word resolves to whichever meaning
    was actually taught by that point, rather than always its overall
    primary sense."""
    return crud.get_vocab_definition(db, hanzi, unit_number=unit_number, hsk_level=hsk_level)


def get_word_definitions(db: Session, hanzi: str, unit_number: Optional[int] = None,
                          hsk_level: int = 1) -> dict:
    """New capability, not a replacement for anything old: the full
    "relevant definition first, other meanings underneath" view for a
    word -- {"primary": {...} | None, "others": [{...}, ...]}. Use this
    (or lookup_word below, which flattens it into one dict) for any
    dictionary-popup / word-detail UI. See crud.get_word_definitions."""
    return crud.get_word_definitions(db, hanzi, unit_number=unit_number, hsk_level=hsk_level)


def get_pinyin(db: Session, hanzi: str, unit_number: Optional[int] = None, hsk_level: int = 1) -> str | None:
    """Replaces word_to_pinyin.get(hanzi). Pinyin can differ by sense too
    (e.g. 还 hai2 vs. huan2) -- same relevance rule as get_dictionary_entry."""
    return crud.get_pinyin_for_word(db, hanzi, unit_number=unit_number, hsk_level=hsk_level)


def get_all_vocab_tags(db: Session) -> set:
    """Replaces unique_vocab_tags."""
    return crud.get_all_vocab_hanzi(db)


def get_all_unit_numbers(db: Session, hsk_level) -> list:
    """Replaces iterating unit_questions.keys()."""
    return crud.get_all_unit_numbers(db, hsk_level)


def lookup_word(db: Session, hanzi: str, unit_number: Optional[int] = None, hsk_level: int = 1) -> dict:
    """Replaces the inline dictionary-lookup-with-pypinyin-fallback logic
    that used to live directly in router.py's /api/lookup endpoint (moved
    here since it's fundamentally a textbook-data concern, not a routing
    concern -- see the router.py rewrite for the corresponding thin
    endpoint).

    Looks up `hanzi`'s taught senses first (fast, exact, has real English
    definitions). Pass unit_number/hsk_level when you know where in the
    curriculum this lookup is happening -- e.g. the learner's current unit
    -- so a word with multiple taught (or dictionary) meanings resolves to
    whichever one is actually relevant there. Falls back to pypinyin's
    auto-generated reading if the word has no senses at all (not in Vocab
    yet), same fallback the old code did against hsk1_dictionary, just
    against the DB now -- `english` is None on that fallback path since
    pypinyin has no definitions.

    UNLIKE the old version, this ALSO returns `other_definitions`: every
    other taught (or untaught CEDICT reference) meaning the word has,
    beyond the one picked as `pinyin`/`english` above -- so a lookup UI can
    show the relevant meaning first and the rest underneath, rather than
    either hiding them entirely or dumping every definition on the learner
    at once. Empty list for a single-sense word (the common case) or an
    unknown word.
    """
    definitions = crud.get_word_definitions(db, hanzi, unit_number=unit_number, hsk_level=hsk_level)
    primary = definitions["primary"]
    if primary:
        return {
            "hanzi": hanzi,
            "pinyin": primary["pinyin"],
            "english": primary["english"],
            "unit": primary["unit"],
            "hsk_level": primary["hsk_level"],
            "other_definitions": definitions["others"],
        }

    from pypinyin import pinyin, Style
    try:
        result = pinyin(hanzi, style=Style.TONE3, heteronym=False)
        py = "".join([s[0] for s in result]).lower()
    except Exception:
        py = None
    return {
        "hanzi": hanzi,
        "pinyin": py,
        "english": None,
        "unit": None,
        "hsk_level": None,
        "other_definitions": [],
    }


def clear_cache():
    """Forward to crud's cache clear -- call after the data pipeline reruns
    so the app picks up new/changed curriculum data without a restart."""
    crud.clear_cache()