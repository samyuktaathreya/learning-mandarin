# app/textbook/crud.py
"""
DB-backed replacement for the in-memory indexes services.py used to build
from JSON at import time (inverted_index, unit_to_vocab_tags_dict,
tags_to_unit_dict, unit_to_tags_dict, hsk1_dictionary, word_to_pinyin).

Every function here takes `db: Session` explicitly rather than reading a
module-level global -- callers (tier_questions.py etc.) now need to thread a
session through, same as every other crud module in this app already does.

SENSE-AWARE UPDATE: Vocab is now just the hanzi IDENTITY row -- a word's
actual taught meaning(s) live on VocabSense rows (see textbook.models.
VocabSense), each with its own pinyin/english/home unit, since a word can
be taught with a genuinely different meaning at a later unit (or hsk_level)
than the one it was first introduced in. Vocab.pinyin/english/unit_id stay
as a cached snapshot of the word's PRIMARY sense for anything that doesn't
need per-meaning precision, but every function here that used to filter on
Vocab.unit_id now queries VocabSense directly instead -- the old
Vocab.unit_id-only queries would silently miss a word introduced with a
NEW sense in a unit that isn't its primary sense's home.

CACHING: curriculum data (vocab, questions, sentences) changes only when the
data pipeline reruns, not per-request, so the hot-path functions here
(get_vocab_tags_for_unit, get_questions_for_tag) are cached in-process with a
TTL rather than re-querying on every call within a session-generation loop
that may call them 100+ times. Cache keys are the query PARAMETERS, not the
db session, since curriculum data is identical across sessions/users -- this
means the cache is shared app-wide, and clear_cache() (call after running the
data pipeline, or from an admin endpoint) forces a refresh without an app
restart.

Question rows are returned as plain dicts shaped like the old JSON question
objects (id, question_type, question, answer, unit, tags) specifically so
callers that used to do q["tags"], q["id"], q.get("unit") on inverted_index
entries need minimal changes -- "tags" is computed on the fly (see
_tags_for_question) since sentence questions test EVERY word in their
sentence, not just one (this is exactly why Question.sentence_id was added
to the schema). A new "definition" key carries the specific taught meaning
a word-level question tests (via Question.vocab_sense_id), for callers that
want to show it.
"""
import time
from threading import Lock
from typing import Optional

from sqlalchemy.orm import Session
from sqlalchemy import func, or_, and_

from textbook.models import Unit, Vocab, VocabSense, Sentence, SentenceVocab, Question, WordType
from session.constants import QUESTION_TYPE_FACETS  # single source of truth -- see note below

DEFAULT_CACHE_TTL_SECONDS = 3600  # 1 hour; curriculum data changes rarely


# --------------------------------- CACHE ---------------------------------
# A minimal manual TTL cache instead of functools.lru_cache: lru_cache can't
# have entries invalidated selectively or on a schedule, and its maxsize
# eviction would be a poor fit for "cache everything, expire after an hour."
# Thread-safe via a single lock -- fine for read-mostly curriculum data;
# this isn't a per-request hot path at the level a fine-grained lock would
# matter for.

class _TTLCache:
    def __init__(self, ttl_seconds: float = DEFAULT_CACHE_TTL_SECONDS):
        self._ttl = ttl_seconds
        self._store: dict = {}
        self._lock = Lock()

    def get(self, key):
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                return None, False
            value, expires_at = entry
            if time.time() >= expires_at:
                del self._store[key]
                return None, False
            return value, True

    def set(self, key, value, ttl_seconds: float = None):
        with self._lock:
            self._store[key] = (value, time.time() + (ttl_seconds or self._ttl))

    def clear(self):
        with self._lock:
            self._store.clear()


_cache = _TTLCache()


def clear_cache():
    """Call this after the data pipeline reruns (or from an admin endpoint)
    so the app picks up new/changed curriculum data without a restart."""
    _cache.clear()


# --------------------------------- SENSE RESOLUTION ---------------------------------
# Shared helpers every definition-lookup function below goes through, so
# "which meaning is relevant right now" is decided in exactly one place.

def _senses_taught_by(vocab: Vocab, unit_number: int, hsk_level: int) -> list:
    return [
        s for s in vocab.senses
        if s.unit is not None and (s.unit.hsk_level, s.unit.unit_number) <= (hsk_level, unit_number)
    ]


def _resolve_relevant_sense(vocab: Optional[Vocab], unit_number: Optional[int],
                             hsk_level: int) -> Optional[VocabSense]:
    """Which sense of `vocab` is most relevant right now: if unit_number is
    given, the LATEST sense already taught by that point in the curriculum
    (mirrors the data pipeline's resolve_sense_for_sentence -- the most
    recently introduced meaning a student at that point would know);
    otherwise the word's overall primary sense. Falls back to the primary
    sense (then to any sense at all) if nothing is homed early enough.
    None if the word doesn't exist or has no senses yet."""
    if vocab is None or not vocab.senses:
        return None
    if unit_number is not None:
        candidates = _senses_taught_by(vocab, unit_number, hsk_level)
        if candidates:
            return max(candidates, key=lambda s: (s.unit.hsk_level, s.unit.unit_number))
    primary = next((s for s in vocab.senses if s.is_primary), None)
    return primary or vocab.senses[0]


def _sense_to_dict(sense: VocabSense) -> dict:
    word_type = sense.word_type
    return {
        "hanzi": sense.vocab.hanzi,
        "pinyin": sense.pinyin,
        "english": sense.english,
        "unit": sense.unit.unit_number if sense.unit else None,
        "hsk_level": sense.unit.hsk_level if sense.unit else None,
        "word_type": word_type.value if hasattr(word_type, "value") else word_type,
    }


# --------------------------------- VOCAB TAGS PER UNIT ---------------------------------
# Replaces unit_to_vocab_tags_dict[unit] (built once from unit_vocab_tags.json
# at import). One row per unit's set of taught vocab hanzi.

def get_vocab_tags_for_unit(db: Session, unit_number: int, hsk_level: int = 1) -> set:
    """Which vocab hanzi have a sense INTRODUCED in this unit -- i.e. this
    unit taught (at least one meaning of) this word. Sourced from
    VocabSense, not Vocab.unit_id (which only reflects the word's PRIMARY
    sense's home) -- a word can gain a brand-new sense in a unit that
    isn't where it was originally taught, and that unit should still show
    the word as "introduced here." unit_number alone no longer uniquely
    identifies a Unit row -- (unit_number, hsk_level) together do, since
    numbering restarts per level."""
    cache_key = ("vocab_tags_for_unit", unit_number, hsk_level)
    cached, hit = _cache.get(cache_key)
    if hit:
        return cached

    rows = (
        db.query(Vocab.hanzi)
        .join(VocabSense, VocabSense.vocab_id == Vocab.id)
        .join(Unit, VocabSense.unit_id == Unit.id)
        .filter(
            Unit.unit_number == unit_number,
            Unit.hsk_level == hsk_level,
            VocabSense.word_type != WordType.auto,
        )
        .distinct()
        .all()
    )
    result = {r.hanzi for r in rows}
    _cache.set(cache_key, result)
    return result


def get_tags_to_unit_map(db: Session) -> dict:
    """Replaces tags_to_unit_dict (tag -> EARLIEST unit it's taught in). A
    word can now have senses homed at several different units -- this
    keeps the old single-unit-per-word contract by taking the earliest one
    (a word counts as "known" as soon as any of its meanings has been
    introduced), matching the data pipeline's own get_word_to_unit_map.

    NOTE: like the original version of this function, this ignores
    hsk_level entirely when comparing unit_numbers -- fine while only one
    level is loaded, but once a word can have senses across different
    hsk_levels, "unit_number" alone stops being unambiguous. Flagging this
    rather than silently fixing it, since it's a pre-existing limitation
    of this specific map, not something the sense refactor introduced."""
    cache_key = ("tags_to_unit_map",)
    cached, hit = _cache.get(cache_key)
    if hit:
        return cached

    rows = (
        db.query(Vocab.hanzi, Unit.unit_number)
        .join(VocabSense, VocabSense.vocab_id == Vocab.id)
        .join(Unit, VocabSense.unit_id == Unit.id)
        .filter(VocabSense.word_type != WordType.auto)
        .all()
    )
    result = {}
    for hanzi, unit_number in rows:
        if hanzi not in result or unit_number < result[hanzi]:
            result[hanzi] = unit_number
    _cache.set(cache_key, result)
    return result


def get_unit_to_tags_map(db: Session) -> dict:
    """Replaces unit_to_tags_dict (unit -> {tags}). Unlike
    get_tags_to_unit_map (which collapses each word down to its single
    earliest unit), this lists a word under EVERY unit that has a sense
    homed there -- a word retaught with a genuinely new sense shows up in
    that later unit's set too, not just its first appearance."""
    cache_key = ("unit_to_tags_map",)
    cached, hit = _cache.get(cache_key)
    if hit:
        return cached

    rows = (
        db.query(Vocab.hanzi, Unit.unit_number)
        .join(VocabSense, VocabSense.vocab_id == Vocab.id)
        .join(Unit, VocabSense.unit_id == Unit.id)
        .filter(VocabSense.word_type != WordType.auto)
        .all()
    )
    result = {}
    for hanzi, unit_number in rows:
        result.setdefault(unit_number, set()).add(hanzi)
    _cache.set(cache_key, result)
    return result


# --------------------------------- QUESTIONS PER TAG (inverted index) ---------------------------------
# Replaces inverted_index[tag] -> [question, ...]. This is the hot path:
# generate_tier_questions calls this ~100+ times building one session.

def _tags_for_question(db: Session, q: Question) -> list:
    """A question's full list of vocab tags -- one tag for a word question
    (q.vocab_id), or every tag in the sentence for a sentence question
    (q.sentence_id -> SentenceVocab). This is what the old JSON's per-
    question `tags: [...]` array held; see the Question model docstring for
    why it can't just be a single vocab_id for sentence-type questions."""
    if q.vocab_id is not None:
        vocab = db.query(Vocab.hanzi).filter(Vocab.id == q.vocab_id).first()
        return [vocab.hanzi] if vocab else []
    if q.sentence_id is not None:
        rows = (
            db.query(Vocab.hanzi)
            .join(SentenceVocab, SentenceVocab.vocab_id == Vocab.id)
            .filter(SentenceVocab.sentence_id == q.sentence_id)
            .order_by(SentenceVocab.position)
            .all()
        )
        return [r.hanzi for r in rows]
    return []


def _question_to_dict(db: Session, q: Question, unit_number: int) -> dict:
    definition = None
    if q.vocab_sense_id is not None:
        sense = db.query(VocabSense).filter(VocabSense.id == q.vocab_sense_id).first()
        if sense:
            definition = sense.english
    return {
        "id": q.legacy_id or f"q{q.id}",
        "db_id": q.id,  # real PK, in case a caller needs it distinctly from legacy_id
        "question_type": q.question_type,
        "question": q.question,
        "answer": q.answer,
        "unit": unit_number,
        "tags": _tags_for_question(db, q),
        "definition": definition,  # the specific taught meaning this question tests, if any (word-level only)
    }


def get_questions_for_tag(db: Session, tag: str, unit_number: int, hsk_level: int = 1,
                          question_type: Optional[str] = None) -> list:
    """Replaces inverted_index.get(tag, []), filtered to a specific unit
    (the old code did `q.get("unit") == unit` as a separate filter step
    after pulling from inverted_index -- baked directly into the query here).

    A question reaches this list if `tag` is ANY of its tags -- i.e. for a
    word question, tag must equal the tested word; for a sentence question,
    tag must be any word IN that sentence. This matches the old inverted
    index's construction (indexed under every tag in q["tags"]). Question
    rows are already scoped to a single unit at creation time (see
    create_questions.py), so this doesn't need to separately check WHICH
    sense of `tag` a given question tests -- it just needs to find
    questions unit_id points at."""
    cache_key = ("questions_for_tag", tag, unit_number, hsk_level, question_type)
    cached, hit = _cache.get(cache_key)
    if hit:
        return cached

    vocab = db.query(Vocab).filter(Vocab.hanzi == tag).first()
    if vocab is None:
        _cache.set(cache_key, [])
        return []

    unit = db.query(Unit).filter(
        Unit.unit_number == unit_number,
        Unit.hsk_level == hsk_level,
    ).first()
    if unit is None:
        _cache.set(cache_key, [])
        return []

    # word-level questions testing this exact vocab
    word_q = db.query(Question).filter(
        Question.unit_id == unit.id,
        Question.vocab_id == vocab.id,
    )
    if question_type:
        word_q = word_q.filter(Question.question_type == question_type)

    # sentence-level questions where this vocab is one of the sentence's tags
    sentence_q = (
        db.query(Question)
        .join(Sentence, Question.sentence_id == Sentence.id)
        .join(SentenceVocab, SentenceVocab.sentence_id == Sentence.id)
        .filter(
            Question.unit_id == unit.id,
            SentenceVocab.vocab_id == vocab.id,
        )
    )
    if question_type:
        sentence_q = sentence_q.filter(Question.question_type == question_type)

    all_questions = list(word_q.all()) + list(sentence_q.all())
    seen_ids = set()
    deduped = []
    for q in all_questions:
        if q.id not in seen_ids:
            seen_ids.add(q.id)
            deduped.append(q)

    result = [_question_to_dict(db, q, unit_number) for q in deduped]
    _cache.set(cache_key, result)
    return result


def get_questions_for_tag_up_to_unit(db: Session, tag: str, max_unit: int, max_hsk_level: int = 1,
                                     question_type: Optional[str] = None) -> list:
    """Like get_questions_for_tag, but across every unit the user has
    already passed -- ALL units in every hsk_level strictly below
    max_hsk_level, plus units <= max_unit within max_hsk_level itself.
    e.g. a user on hsk_level=3, unit=2 can review from all of hsk_level 1,
    all of hsk_level 2, and hsk_level 3's unit 1 (not unit 2, their current
    unit -- that's still being learned, not yet review-eligible).

    Replaces the old `q.get("unit", 0) <= max_unit` filter that used to run
    over inverted_index.get(tag, []) in review_engine.py."""
    cache_key = ("questions_for_tag_up_to_unit", tag, max_unit, max_hsk_level, question_type)
    cached, hit = _cache.get(cache_key)
    if hit:
        return cached

    vocab = db.query(Vocab).filter(Vocab.hanzi == tag).first()
    if vocab is None:
        _cache.set(cache_key, [])
        return []

    unit_ids_subq = (
        db.query(Unit.id, Unit.unit_number)
        .filter(
            or_(
                Unit.hsk_level < max_hsk_level,
                and_(Unit.hsk_level == max_hsk_level, Unit.unit_number <= max_unit),
            )
        )
        .subquery()
    )

    word_q = (
        db.query(Question)
        .join(unit_ids_subq, Question.unit_id == unit_ids_subq.c.id)
        .filter(Question.vocab_id == vocab.id)
    )
    if question_type:
        word_q = word_q.filter(Question.question_type == question_type)

    sentence_q = (
        db.query(Question)
        .join(unit_ids_subq, Question.unit_id == unit_ids_subq.c.id)
        .join(Sentence, Question.sentence_id == Sentence.id)
        .join(SentenceVocab, SentenceVocab.sentence_id == Sentence.id)
        .filter(SentenceVocab.vocab_id == vocab.id)
    )
    if question_type:
        sentence_q = sentence_q.filter(Question.question_type == question_type)

    all_questions = list(word_q.all()) + list(sentence_q.all())
    seen_ids = set()
    deduped = []
    for q in all_questions:
        if q.id not in seen_ids:
            seen_ids.add(q.id)
            deduped.append(q)

    unit_number_by_id = {
        u.id: u.unit_number
        for u in db.query(Unit).filter(
            or_(
                Unit.hsk_level < max_hsk_level,
                and_(Unit.hsk_level == max_hsk_level, Unit.unit_number <= max_unit),
            )
        ).all()
    }
    result = [_question_to_dict(db, q, unit_number_by_id.get(q.unit_id)) for q in deduped]
    _cache.set(cache_key, result)
    return result


def get_all_questions_for_unit(db: Session, unit_number: int, hsk_level: int = 1) -> list:
    """Replaces `unit_questions.get(str(unit), [])` -- every Question row in
    a unit, regardless of tag/type. Used by generate_unit_test, which then
    filters down to ALL_TIER_QUESTION_TYPES itself; unlike
    get_questions_for_tag, this isn't scoped to one word at all."""
    cache_key = ("all_questions_for_unit", unit_number, hsk_level)
    cached, hit = _cache.get(cache_key)
    if hit:
        return cached

    unit = db.query(Unit).filter(
        Unit.unit_number == unit_number,
        Unit.hsk_level == hsk_level,
    ).first()
    if unit is None:
        _cache.set(cache_key, [])
        return []

    questions = db.query(Question).filter(Question.unit_id == unit.id).all()
    result = [_question_to_dict(db, q, unit_number) for q in questions]
    _cache.set(cache_key, result)
    return result

def get_question_by_id(db: Session, question_db_id: int, unit_number: Optional[int] = None) -> Optional[dict]:
    """Fetch a single question by its real DB id (not legacy_id -- use a
    separate lookup if you need legacy_id resolution)."""
    q = db.query(Question).filter(Question.id == question_db_id).first()
    if q is None:
        return None
    if unit_number is None:
        unit = db.query(Unit).filter(Unit.id == q.unit_id).first()
        unit_number = unit.unit_number if unit else None
    return _question_to_dict(db, q, unit_number)


# --------------------------------- DICTIONARY / PINYIN ---------------------------------
# Replaces hsk1_dictionary (hanzi -> {hanzi, pinyin, english}) and
# word_to_pinyin (hanzi -> pinyin), both previously loaded whole into RAM.

def get_vocab_definition(db: Session, hanzi: str, unit_number: Optional[int] = None,
                          hsk_level: int = 1) -> Optional[dict]:
    """Replaces hsk1_dictionary.get(hanzi). Returns the single MOST
    RELEVANT definition for `hanzi` given where the user is in the
    curriculum: if unit_number is given, whichever sense was most recently
    taught by that point (e.g. click a word inside a specific sentence);
    otherwise the word's overall primary sense (e.g. a plain flashcard
    lookup with no context). None if the word isn't in the vocab table, or
    has no senses yet.

    For a word with MULTIPLE taught (or dictionary) meanings, this
    intentionally returns only ONE -- see get_word_definitions for the
    "relevant one first, everything else underneath" view."""
    cache_key = ("vocab_definition", hanzi, unit_number, hsk_level)
    cached, hit = _cache.get(cache_key)
    if hit:
        return cached

    vocab = db.query(Vocab).filter(Vocab.hanzi == hanzi).first()
    sense = _resolve_relevant_sense(vocab, unit_number, hsk_level)
    result = _sense_to_dict(sense) if sense else None
    _cache.set(cache_key, result)
    return result


def get_word_definitions(db: Session, hanzi: str, unit_number: Optional[int] = None,
                          hsk_level: int = 1) -> dict:
    """The full "relevant definition first, everything else underneath"
    view for a word -- what a word-lookup / dictionary-popup UI should use
    instead of get_vocab_definition. `primary` is exactly what
    get_vocab_definition returns; `others` is every OTHER sense the word
    has, ordered taught-in-this-hsk_level first (earliest unit first),
    then taught-in-other-hsk_levels, then untaught CEDICT reference senses
    (words CEDICT knows a meaning for that the curriculum hasn't taught
    yet -- see append_orphan_tags.register_cedict_word) last. Never
    returns more than one definition as "the" definition -- callers
    control whether/how `others` gets shown."""
    cache_key = ("word_definitions", hanzi, unit_number, hsk_level)
    cached, hit = _cache.get(cache_key)
    if hit:
        return cached

    vocab = db.query(Vocab).filter(Vocab.hanzi == hanzi).first()
    if vocab is None or not vocab.senses:
        result = {"primary": None, "others": []}
        _cache.set(cache_key, result)
        return result

    primary = _resolve_relevant_sense(vocab, unit_number, hsk_level)

    def sort_key(s: VocabSense):
        if s.unit is None:
            return (2, 0, 0)  # untaught reference sense -- sorts last
        same_level = 0 if s.unit.hsk_level == hsk_level else 1
        return (same_level, s.unit.hsk_level, s.unit.unit_number)

    others = sorted(
        (s for s in vocab.senses if primary is None or s.id != primary.id),
        key=sort_key,
    )
    result = {
        "primary": _sense_to_dict(primary) if primary else None,
        "others": [_sense_to_dict(s) for s in others],
    }
    _cache.set(cache_key, result)
    return result


def get_pinyin_for_word(db: Session, hanzi: str, unit_number: Optional[int] = None,
                         hsk_level: int = 1) -> Optional[str]:
    """Replaces word_to_pinyin.get(hanzi). Same relevance rule as
    get_vocab_definition -- pinyin can differ by sense too (e.g. 还 hai2
    vs. huan2), so this isn't a fixed per-word fact once a word has
    multiple readings."""
    definition = get_vocab_definition(db, hanzi, unit_number=unit_number, hsk_level=hsk_level)
    return definition["pinyin"] if definition else None


def get_all_vocab_hanzi(db: Session) -> set:
    """Replaces `unique_vocab_tags` (the full set of every taught vocab
    word across all units). Sourced from VocabSense rather than Vocab
    directly -- a word only counts as "taught" if it has at least one
    non-auto sense, regardless of what Vocab's (possibly still-blank,
    cache-lag) primary snapshot currently says."""
    cache_key = ("all_vocab_hanzi",)
    cached, hit = _cache.get(cache_key)
    if hit:
        return cached

    rows = (
        db.query(Vocab.hanzi)
        .join(VocabSense, VocabSense.vocab_id == Vocab.id)
        .filter(VocabSense.word_type != WordType.auto)
        .distinct()
        .all()
    )
    result = {r.hanzi for r in rows}
    _cache.set(cache_key, result)
    return result


def get_all_unit_numbers(db: Session, hsk_level: Optional[int] = None) -> list:
    """Replaces `unit_questions.keys()` -- every unit that has at least one
    Question row, ascending. Optionally filtered to a single hsk_level so a
    user only sees units belonging to their current HSK track (all units are
    hsk_level=1 today, but this keeps the door open as more levels are
    loaded)."""
    cache_key = ("all_unit_numbers", hsk_level)
    cached, hit = _cache.get(cache_key)
    if hit:
        return cached

    query = (
        db.query(Unit.unit_number)
        .join(Question, Question.unit_id == Unit.id)
        .distinct()
    )
    if hsk_level is not None:
        query = query.filter(Unit.hsk_level == hsk_level)
    query = query.order_by(Unit.unit_number)

    result = [r.unit_number for r in query.all()]
    _cache.set(cache_key, result)
    return result