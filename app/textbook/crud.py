# app/textbook/crud.py
"""
DB-backed replacement for the in-memory indexes services.py used to build
from JSON at import time (inverted_index, unit_to_vocab_tags_dict,
tags_to_unit_dict, unit_to_tags_dict, hsk1_dictionary, word_to_pinyin).

Every function here takes `db: Session` explicitly rather than reading a
module-level global -- callers (tier_questions.py etc.) now need to thread a
session through, same as every other crud module in this app already does.

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
to the schema).
"""
import time
from threading import Lock
from typing import Optional

from sqlalchemy.orm import Session
from sqlalchemy import func

from textbook.models import Unit, Vocab, Sentence, SentenceVocab, Question, WordType
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


# --------------------------------- VOCAB TAGS PER UNIT ---------------------------------
# Replaces unit_to_vocab_tags_dict[unit] (built once from unit_vocab_tags.json
# at import). One row per unit's set of taught vocab hanzi.

def get_vocab_tags_for_unit(db: Session, unit_number: int) -> set:
    """Which vocab hanzi are taught in this unit. Mirrors the old
    unit_to_vocab_tags_dict.get(unit, set()) -- includes vocab/grammar/
    proper_noun word_types (everything EXCEPT "auto" fallback words, which
    were never in unit_vocab_tags.json either since they have no real home
    unit)."""
    cache_key = ("vocab_tags_for_unit", unit_number)
    cached, hit = _cache.get(cache_key)
    if hit:
        return cached

    rows = (
        db.query(Vocab.hanzi)
        .join(Unit, Vocab.unit_id == Unit.id)
        .filter(Unit.unit_number == unit_number, Vocab.word_type != WordType.auto)
        .all()
    )
    result = {r.hanzi for r in rows}
    _cache.set(cache_key, result)
    return result


def get_tags_to_unit_map(db: Session) -> dict:
    """Replaces tags_to_unit_dict (tag -> earliest unit). Global map, cached
    as a whole since it's small and queried as a single lookup table."""
    cache_key = ("tags_to_unit_map",)
    cached, hit = _cache.get(cache_key)
    if hit:
        return cached

    rows = (
        db.query(Vocab.hanzi, Unit.unit_number)
        .join(Unit, Vocab.unit_id == Unit.id)
        .filter(Vocab.word_type != WordType.auto)
        .all()
    )
    result = {hanzi: unit_number for hanzi, unit_number in rows}
    _cache.set(cache_key, result)
    return result


def get_unit_to_tags_map(db: Session) -> dict:
    """Replaces unit_to_tags_dict (unit -> {tags}). Built from the same
    query as get_tags_to_unit_map, just grouped the other way -- cached
    separately since callers may want one or the other."""
    cache_key = ("unit_to_tags_map",)
    cached, hit = _cache.get(cache_key)
    if hit:
        return cached

    rows = (
        db.query(Vocab.hanzi, Unit.unit_number)
        .join(Unit, Vocab.unit_id == Unit.id)
        .filter(Vocab.word_type != WordType.auto)
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
    return {
        "id": q.legacy_id or f"q{q.id}",
        "db_id": q.id,  # real PK, in case a caller needs it distinctly from legacy_id
        "question_type": q.question_type,
        "question": q.question,
        "answer": q.answer,
        "unit": unit_number,
        "tags": _tags_for_question(db, q),
    }


def get_questions_for_tag(db: Session, tag: str, unit_number: int,
                          question_type: Optional[str] = None) -> list:
    """Replaces inverted_index.get(tag, []), filtered to a specific unit
    (the old code did `q.get("unit") == unit` as a separate filter step
    after pulling from inverted_index -- baked directly into the query here).

    A question reaches this list if `tag` is ANY of its tags -- i.e. for a
    word question, tag must equal the tested word; for a sentence question,
    tag must be any word IN that sentence. This matches the old inverted
    index's construction (indexed under every tag in q["tags"])."""
    cache_key = ("questions_for_tag", tag, unit_number, question_type)
    cached, hit = _cache.get(cache_key)
    if hit:
        return cached

    vocab = db.query(Vocab).filter(Vocab.hanzi == tag).first()
    if vocab is None:
        _cache.set(cache_key, [])
        return []

    unit = db.query(Unit).filter(Unit.unit_number == unit_number).first()
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
    # de-dupe (a sentence question could theoretically double-match if a
    # word appears twice in one sentence -- SentenceVocab now allows that)
    seen_ids = set()
    deduped = []
    for q in all_questions:
        if q.id not in seen_ids:
            seen_ids.add(q.id)
            deduped.append(q)

    result = [_question_to_dict(db, q, unit_number) for q in deduped]
    _cache.set(cache_key, result)
    return result


def get_questions_for_tag_up_to_unit(db: Session, tag: str, max_unit: int,
                                     question_type: Optional[str] = None) -> list:
    """Like get_questions_for_tag, but across every unit <= max_unit rather
    than one exact unit. This is what review needs: a review question for a
    word taught in unit 2 can legitimately come from unit 2, 3, or 4's
    question bank (any unit the learner has already reached), not just the
    word's home unit. Replaces the old `q.get("unit", 0) <= max_unit` filter
    that used to run over inverted_index.get(tag, []) in review_engine.py."""
    cache_key = ("questions_for_tag_up_to_unit", tag, max_unit, question_type)
    cached, hit = _cache.get(cache_key)
    if hit:
        return cached

    vocab = db.query(Vocab).filter(Vocab.hanzi == tag).first()
    if vocab is None:
        _cache.set(cache_key, [])
        return []

    unit_ids_subq = (
        db.query(Unit.id, Unit.unit_number)
        .filter(Unit.unit_number <= max_unit)
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

    # unit number per question, needed for _question_to_dict's "unit" field
    unit_number_by_id = {u.id: u.unit_number for u in db.query(Unit).filter(Unit.unit_number <= max_unit).all()}
    result = [_question_to_dict(db, q, unit_number_by_id.get(q.unit_id)) for q in deduped]
    _cache.set(cache_key, result)
    return result


def get_all_questions_for_unit(db: Session, unit_number: int) -> list:
    """Replaces `unit_questions.get(str(unit), [])` -- every Question row in
    a unit, regardless of tag/type. Used by generate_unit_test, which then
    filters down to ALL_TIER_QUESTION_TYPES itself; unlike
    get_questions_for_tag, this isn't scoped to one word at all."""
    cache_key = ("all_questions_for_unit", unit_number)
    cached, hit = _cache.get(cache_key)
    if hit:
        return cached

    unit = db.query(Unit).filter(Unit.unit_number == unit_number).first()
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

def get_vocab_definition(db: Session, hanzi: str) -> Optional[dict]:
    """Replaces hsk1_dictionary.get(hanzi). Returns None if not found
    (old code would KeyError or return None depending on call site --
    returning None here and letting callers handle it explicitly)."""
    cache_key = ("vocab_definition", hanzi)
    cached, hit = _cache.get(cache_key)
    if hit:
        return cached

    vocab = db.query(Vocab).filter(Vocab.hanzi == hanzi).first()
    if vocab is None:
        _cache.set(cache_key, None)
        return None

    result = {"hanzi": vocab.hanzi, "pinyin": vocab.pinyin, "english": vocab.english}
    _cache.set(cache_key, result)
    return result


def get_pinyin_for_word(db: Session, hanzi: str) -> Optional[str]:
    """Replaces word_to_pinyin.get(hanzi)."""
    cache_key = ("pinyin_for_word", hanzi)
    cached, hit = _cache.get(cache_key)
    if hit:
        return cached

    vocab = db.query(Vocab.pinyin).filter(Vocab.hanzi == hanzi).first()
    result = vocab.pinyin if vocab else None
    _cache.set(cache_key, result)
    return result


def get_all_vocab_hanzi(db: Session) -> set:
    """Replaces `unique_vocab_tags` (the full set of every taught vocab
    word across all units)."""
    cache_key = ("all_vocab_hanzi",)
    cached, hit = _cache.get(cache_key)
    if hit:
        return cached

    rows = db.query(Vocab.hanzi).filter(Vocab.word_type != WordType.auto).all()
    result = {r.hanzi for r in rows}
    _cache.set(cache_key, result)
    return result


def get_all_unit_numbers(db: Session) -> list:
    """Replaces `unit_questions.keys()` -- the old code iterated
    unit_questions.json's top-level keys to get "every unit that has
    questions." Same intent here: every unit that has at least one Question
    row, ascending. (Not just every Unit row -- a unit could exist as a row
    with, say, only vocab extracted so far and no questions generated yet;
    /api/progress's loop specifically wants units that are ready to show
    progress for.)"""
    cache_key = ("all_unit_numbers",)
    cached, hit = _cache.get(cache_key)
    if hit:
        return cached

    rows = (
        db.query(Unit.unit_number)
        .join(Question, Question.unit_id == Unit.id)
        .distinct()
        .order_by(Unit.unit_number)
        .all()
    )
    result = [r.unit_number for r in rows]
    _cache.set(cache_key, result)
    return result