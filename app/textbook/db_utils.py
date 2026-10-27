# textbook/db_utils.py
"""
Engine/session setup for the textbook SQL database, plus the helpers shared
by every pipeline stage and the app.

PIPELINE (see models.py for the full order and why unit_id is nullable):
  load_hsk_vocab   -> upsert_hsk_vocab
  import_hsa       -> upsert_import_sentence
  tag_sentences    -> get_untagged_sentences, get_or_create_vocab,
                      upsert_vocab_sense, sense cache helpers,
                      set_sentence_tags, set_sentence_level
  grammar          -> get_or_create_grammar_point, link_sentence_grammar
  assign_units     -> clear_units, create_unit
  finalize         -> set_primary_senses_by_earliest_unit
  create_questions -> upsert_question, get_senses_for_unit, ...

Units are no longer known when sentences, words and senses are created:
assign_units.py computes them afterwards. So nothing here creates a unit as
a side effect of writing content any more, and nothing re-homes a sense one
sentence at a time -- assign_units.py sets every home in one pass.

`hsk_level` parameters still default to 1, so existing app callers that
follow that pattern keep working.

PINYIN: sense matching uses CITATION tones (bu4 shi4), compared through
_normalize_pinyin so "ai4 hao4", "ai4hao4" and "AI4HAO4" all match, and
CC-CEDICT's "u:" / "ü" match pypinyin's "v". Tone-sandhi pinyin is display
only (VocabSense.pinyin_display, SentenceVocab.pinyin, Sentence.pinyin).
"""
import re
import json
from collections import defaultdict
from contextlib import contextmanager

from sqlalchemy import create_engine, event, func
from sqlalchemy.orm import sessionmaker, Session

from .models import (
    Base, Unit, Vocab, VocabSense, Sentence, SentenceVocab, WordType,
    VocabOrigin, SentenceSource, OUT_OF_LIST_LEVEL,
    GrammarTip, SentenceGrammar, Question, FitbQuestion, SenseCache, PinyinWord,
)
from app.core.config.data import TEXTBOOK_DB
from app.core.logger import logger

engine = create_engine(f"sqlite:///{TEXTBOOK_DB}", connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


# SQLite's Python driver (pysqlite) starts transactions lazily, so a
# SAVEPOINT can open its own transaction and RELEASE then COMMITS it:
# Session.begin_nested() silently writes to disk and an outer rollback
# can't undo it. SQLAlchemy's documented fix: let SQLAlchemy emit BEGIN
# itself. tag_sentences.py relies on this (one savepoint per sentence, and
# --estimate rolls everything back).
@event.listens_for(engine, "connect")
def _sqlite_no_implicit_transactions(dbapi_connection, connection_record):
    dbapi_connection.isolation_level = None


@event.listens_for(engine, "begin")
def _sqlite_explicit_begin(conn):
    conn.exec_driver_sql("BEGIN")


def init_db():
    Base.metadata.create_all(bind=engine)
    _add_missing_columns()


# create_all() makes missing TABLES but never adds a column to an existing
# one, so columns added to a model after the DB was created go here.
_ADDED_COLUMNS = [("vocab", "radical", "TEXT"), ("vocab", "pattern", "TEXT")]

_PATTERN_SPLIT_RE = re.compile(r"[…⋯.。～~\s]+")


def pattern_pieces(pattern: str) -> list[str]:
    """"虽然……但是……" -> ["虽然", "但是"]."""
    return [p for p in _PATTERN_SPLIT_RE.split(pattern or "") if p]


def _add_missing_columns():
    with engine.begin() as conn:
        for table, column, sql_type in _ADDED_COLUMNS:
            existing = {row[1] for row in conn.exec_driver_sql(f"PRAGMA table_info({table})")}
            if column not in existing:
                conn.exec_driver_sql(f"ALTER TABLE {table} ADD COLUMN {column} {sql_type}")
                logger.info(f"  [migrate] added {table}.{column}")


def reset_textbook_data(db: Session) -> dict:
    """Deletes every row the textbook pipeline writes, so a run starts from
    scratch: questions, units, sentences, vocab, senses, grammar points and
    their links. Also pinyin_words, which points at vocab ids -- rebuild it
    afterwards (build_pinyin_words.py). pinyin_syllables and
    pinyin_sound_guides don't depend on vocab and are kept. Returns
    {table: rows deleted}."""
    counts = {}
    for model in (Question, FitbQuestion, SentenceGrammar, SentenceVocab, SenseCache,
                  PinyinWord, VocabSense, Sentence, Vocab, GrammarTip, Unit):
        counts[model.__tablename__] = db.query(model).delete(synchronize_session=False)
    db.flush()
    db.expire_all()
    return counts


@contextmanager
def get_session():
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


# --------------------------------- NORMALIZATION ---------------------------------

_GLOSS_NORM_RE = re.compile(r"[^\w]+")


def normalize_gloss(english: str) -> str:
    """Loose normalization for comparing two english glosses -- case,
    punctuation and whitespace differences don't count as a different
    meaning."""
    return _GLOSS_NORM_RE.sub(" ", (english or "").strip().lower()).strip()


def _normalize_pinyin(pinyin: str) -> str:
    """Comparison key for CITATION pinyin: lowercase, no spaces, and every
    spelling of ü ("ü", "u:", "v") folded to "v" -- the vocab JSON and
    pypinyin spell it differently."""
    p = (pinyin or "").strip().lower().replace("u:", "v").replace("ü", "v")
    return re.sub(r"\s+", "", p)


# --------------------------------- UNIT ---------------------------------

def get_or_create_unit(db: Session, unit_number: int, hsk_level: int = 1) -> Unit:
    """Looks up a unit by (unit_number, hsk_level), creating a bare one if
    it's missing. Content-writing helpers no longer call this (units come
    from assign_units.py); it stays for upsert_question and app code."""
    unit = (
        db.query(Unit)
        .filter(Unit.unit_number == unit_number, Unit.hsk_level == hsk_level)
        .first()
    )
    if unit is None:
        unit = Unit(unit_number=unit_number, hsk_level=hsk_level)
        db.add(unit)
        db.flush()
    return unit


def create_unit(db: Session, unit_number: int, hsk_level: int, title: str,
                topic: str | None = None, part: int | None = None) -> Unit:
    """assign_units.py: writes one unit. Call clear_units first on a re-run;
    this raises on a duplicate (unit_number, hsk_level) rather than silently
    reusing a stale row."""
    unit = Unit(unit_number=unit_number, hsk_level=hsk_level, title=title, topic=topic, part=part)
    db.add(unit)
    db.flush()
    return unit


def clear_units(db: Session, hsk_level: int) -> int:
    """assign_units.py, before rebuilding a level: un-assigns everything that
    points at this level's units (sentences, words, senses, grammar points),
    then deletes the units. Content rows are kept -- only their unit_id is
    cleared. Questions and FITB questions for the level are deleted, since
    they belong to a unit and are regenerated by create_questions.py.
    Returns the number of units deleted."""
    unit_ids = [u.id for u in db.query(Unit.id).filter(Unit.hsk_level == hsk_level).all()]
    if not unit_ids:
        return 0
    for model in (Sentence, Vocab, VocabSense, GrammarTip):
        db.query(model).filter(model.unit_id.in_(unit_ids)).update(
            {model.unit_id: None}, synchronize_session=False)
    db.query(Question).filter(Question.unit_id.in_(unit_ids)).delete(synchronize_session=False)
    db.query(FitbQuestion).filter(FitbQuestion.unit_id.in_(unit_ids)).delete(synchronize_session=False)
    db.query(Unit).filter(Unit.id.in_(unit_ids)).delete(synchronize_session=False)
    db.flush()
    db.expire_all()
    logger.debug(f"  [clear-units] hsk_level {hsk_level}: removed {len(unit_ids)} unit(s)")
    return len(unit_ids)


def get_highest_unit_number(db: Session, hsk_level: int = 1) -> int | None:
    """Highest unit_number that exists for `hsk_level`, or None if the level
    has no units yet."""
    return (
        db.query(func.max(Unit.unit_number))
        .filter(Unit.hsk_level == hsk_level)
        .scalar()
    )


# --------------------------------- VOCAB ---------------------------------
#
# TERMINOLOGY: a "sense" (VocabSense row) is one distinct taught MEANING of a
# hanzi. A Vocab row can have several (还 "still" vs 还 "to return"). Vocab's
# pinyin/english/unit_id/word_type/origin are a CACHED SNAPSHOT of its
# PRIMARY sense, kept in sync here for callers that aren't sense-aware.

def upsert_hsk_vocab(db: Session, hanzi: str, hsk_level: int, pinyin: str, english: str,
                     frequency: int | None = None, pos: list[str] | None = None,
                     word_type: WordType = WordType.vocab, radical: str | None = None,
                     pattern: str | None = None) -> Vocab:
    """load_hsk_lists.py: one row per word in the HSK list. Creates NO
    sense -- senses are created only when a sentence uses a meaning.
    pinyin/english are the main reading and its definition, as a display
    default until a primary sense exists. Idempotent: a re-run updates the
    list fields in place and never touches unit_id or a synced cache."""
    vocab = db.query(Vocab).filter(Vocab.hanzi == hanzi).first()
    if vocab is None:
        vocab = Vocab(hanzi=hanzi, pinyin=pinyin, english=english, word_type=word_type,
                      origin=VocabOrigin.hsk_list)
        db.add(vocab)
    has_primary = vocab.id is not None and db.query(VocabSense).filter(
        VocabSense.vocab_id == vocab.id, VocabSense.is_primary == 1).count() > 0
    if not has_primary:
        vocab.pinyin, vocab.english, vocab.word_type = pinyin, english, word_type
    vocab.hsk_level = hsk_level
    vocab.frequency = frequency
    vocab.pos = json.dumps(pos, ensure_ascii=False) if pos is not None else None
    vocab.radical = radical
    vocab.pattern = pattern
    vocab.origin = vocab.origin or VocabOrigin.hsk_list
    db.flush()
    return vocab


def get_or_create_vocab(db: Session, hanzi: str, word_type: WordType = WordType.auto,
                        origin: VocabOrigin | None = None, pinyin: str = "",
                        english: str = "") -> Vocab:
    """The hanzi IDENTITY row. HSK-list words already exist (upsert_hsk_vocab),
    so this only creates rows for words tag_sentences.py finds outside the
    list: proper nouns (word_type=proper_noun, origin=proper_noun) and
    unlisted words (word_type=auto, origin=unlisted). An existing row is
    returned unchanged."""
    row = db.query(Vocab).filter(Vocab.hanzi == hanzi).first()
    if row is None:
        row = Vocab(hanzi=hanzi, pinyin=pinyin, english=english, word_type=word_type,
                    origin=origin, unit_id=None, hsk_level=None)
        db.add(row)
        db.flush()
    return row


def get_vocab_hanzi_through_level(db: Session, hsk_level: int) -> set:
    """HSK-list words at or below `hsk_level` -- tag_sentences.py's
    vocab-list gate. Proper nouns and unlisted words have no level and are
    never included."""
    rows = (
        db.query(Vocab.hanzi)
        .filter(Vocab.hsk_level.isnot(None), Vocab.hsk_level <= hsk_level)
        .all()
    )
    return {r.hanzi for r in rows}


def get_all_vocab_hanzi(db: Session) -> list[str]:
    """All known hanzi, longest-first -- for greedy substring matching of
    compound words. Global across every hsk_level."""
    rows = db.query(Vocab.hanzi).all()
    hanzi = [r.hanzi for r in rows]
    hanzi.sort(key=len, reverse=True)
    return hanzi


def get_vocab_for_unit(db: Session, unit_number: int, word_types: list[WordType] = None,
                       hsk_level: int = 1) -> list[Vocab]:
    """LEGACY: Vocab rows filtered by their cached PRIMARY sense's home unit.
    A word retaught with a new sense at this unit won't show up here; prefer
    get_senses_for_unit for per-meaning content."""
    q = (
        db.query(Vocab)
        .join(Unit, Vocab.unit_id == Unit.id)
        .filter(Unit.unit_number == unit_number, Unit.hsk_level == hsk_level)
    )
    if word_types:
        q = q.filter(Vocab.word_type.in_(word_types))
    return q.all()


# --------------------------------- SENSES ---------------------------------

def _sense_home_key(sense: VocabSense):
    """(hsk_level, unit_number) of a sense's home, or None if it has none."""
    return (sense.unit.hsk_level, sense.unit.unit_number) if sense.unit else None


def upsert_vocab_sense(db: Session, vocab: Vocab | str, pinyin: str, english: str,
                       origin: VocabOrigin, word_type: WordType = WordType.vocab,
                       pos_tag: str | None = None, form_index: int | None = None,
                       meaning_index: int | None = None) -> VocabSense:
    """tag_sentences.py: insert-or-reuse ONE meaning of a word, with no unit
    (assign_units.py sets it later).

    `pinyin` is the CITATION reading. An existing sense of the same word with
    the same pinyin and (normalized) english is reused -- blank pos_tag /
    form_index / meaning_index on it are filled in, nothing else changes, and
    its origin is never overwritten.

    A new sense is made primary only if it's the word's first, so the Vocab
    cache has a definition straight away. The real rule (earliest-taught
    sense) is applied afterwards by set_primary_senses_by_earliest_unit."""
    if isinstance(vocab, str):
        vocab = get_or_create_vocab(db, vocab)
    want_py, want_en = _normalize_pinyin(pinyin), normalize_gloss(english)

    existing = db.query(VocabSense).filter(VocabSense.vocab_id == vocab.id).order_by(VocabSense.id).all()
    for s in existing:
        if _normalize_pinyin(s.pinyin) == want_py and normalize_gloss(s.english) == want_en:
            if pos_tag and not s.pos_tag:
                s.pos_tag = pos_tag
            if form_index is not None and s.form_index is None:
                s.form_index, s.meaning_index = form_index, meaning_index
            db.flush()
            return s

    sense = VocabSense(
        vocab_id=vocab.id, unit_id=None, pinyin=pinyin, english=english,
        word_type=word_type, origin=origin, pos_tag=pos_tag,
        form_index=form_index, meaning_index=meaning_index,
        is_primary=0,
    )
    db.add(sense)
    db.flush()
    if not existing:
        set_primary_sense(db, vocab, sense)
    return sense


def get_senses_for_vocab(db: Session, hanzi: str) -> list[VocabSense]:
    """Every sense of `hanzi`, oldest first. Queries directly rather than
    vocab.senses, which can be stale right after a sense is created in the
    same transaction."""
    vocab = db.query(Vocab).filter(Vocab.hanzi == hanzi).first()
    if vocab is None:
        return []
    return db.query(VocabSense).filter(VocabSense.vocab_id == vocab.id).order_by(VocabSense.id).all()


def set_primary_sense(db: Session, vocab: Vocab, sense: VocabSense):
    """Marks `sense` as `vocab`'s primary sense, clears the flag on its
    others, then syncs Vocab's cache columns. Exactly one primary per word
    is enforced here, not by a DB constraint."""
    for s in db.query(VocabSense).filter(VocabSense.vocab_id == vocab.id).all():
        if s.id != sense.id and s.is_primary:
            s.is_primary = 0
    sense.is_primary = 1
    db.flush()
    refresh_primary_cache(db, vocab)


def refresh_primary_cache(db: Session, vocab: Vocab):
    """Copies the primary sense's fields onto Vocab's cache columns.
    Vocab.pinyin gets the DISPLAY (sandhi) pinyin when the finalize stage has
    written one, otherwise the citation pinyin. word_type / origin are only
    copied when the sense has them, so an HSK-list word keeps origin=hsk_list
    even when its primary meaning was AI-added."""
    primary = (
        db.query(VocabSense)
        .filter(VocabSense.vocab_id == vocab.id, VocabSense.is_primary == 1)
        .first()
    )
    if primary is None:
        return
    vocab.pinyin = primary.pinyin_display or primary.pinyin
    vocab.english = primary.english
    vocab.word_type = primary.word_type
    vocab.unit_id = primary.unit_id
    if vocab.origin is None:
        vocab.origin = primary.origin
    db.flush()


def set_primary_senses_by_earliest_unit(db: Session, hsk_level: int | None = None) -> int:
    """Finalize stage: each word's primary sense = its earliest-taught sense,
    by (hsk_level, unit_number) of the sense's unit. Ties keep JSON order
    (form_index, meaning_index), then creation order. Senses with no unit
    sort last. Pass hsk_level to only touch words whose senses are homed in
    that level. Returns the number of words whose primary changed."""
    q = db.query(Vocab).join(VocabSense, VocabSense.vocab_id == Vocab.id)
    if hsk_level is not None:
        q = q.join(Unit, VocabSense.unit_id == Unit.id).filter(Unit.hsk_level == hsk_level)
    changed = 0
    for vocab in q.distinct().all():
        senses = db.query(VocabSense).filter(VocabSense.vocab_id == vocab.id).all()

        def key(s: VocabSense):
            home = _sense_home_key(s)
            return (home is None, home or (0, 0),
                    s.form_index if s.form_index is not None else 999,
                    s.meaning_index if s.meaning_index is not None else 999, s.id)

        best = min(senses, key=key)
        if not best.is_primary:
            changed += 1
        set_primary_sense(db, vocab, best)
    return changed


def merge_sense_into(db: Session, keep: VocabSense, drop: VocabSense) -> VocabSense:
    """Folds `drop` into `keep` (two rows for the same meaning of the same
    word) and deletes `drop`: every SentenceVocab / Question / SenseCache row
    pointing at `drop` is repointed to `keep`, blank pinyin / pos_tag / JSON
    indexes on `keep` are filled from `drop`, and if `drop` was primary,
    `keep` becomes primary. A clean-up tool, e.g. after merging an ai_added
    sense into the JSON meaning it duplicates."""
    if keep.id == drop.id:
        return keep
    if keep.vocab_id != drop.vocab_id:
        raise ValueError(f"can't merge senses of different words "
                         f"(sense {drop.id} vocab {drop.vocab_id} -> sense {keep.id} vocab {keep.vocab_id})")

    for row in db.query(SentenceVocab).filter(SentenceVocab.vocab_sense_id == drop.id).all():
        row.sense = keep
    for row in db.query(Question).filter(Question.vocab_sense_id == drop.id).all():
        row.sense = keep
    for row in db.query(SenseCache).filter(SenseCache.vocab_sense_id == drop.id).all():
        row.sense = keep

    if not (keep.pinyin or "").strip() and (drop.pinyin or "").strip():
        keep.pinyin = drop.pinyin
    if not keep.pos_tag and drop.pos_tag:
        keep.pos_tag = drop.pos_tag
    if keep.form_index is None and drop.form_index is not None:
        keep.form_index, keep.meaning_index = drop.form_index, drop.meaning_index
    drop_key, keep_key = _sense_home_key(drop), _sense_home_key(keep)
    if drop_key is not None and (keep_key is None or drop_key < keep_key):
        keep.unit_id = drop.unit_id

    was_primary = bool(drop.is_primary)
    vocab = keep.vocab
    db.flush()
    db.delete(drop)
    db.flush()
    db.expire(vocab, ["senses"])

    if was_primary:
        set_primary_sense(db, vocab, keep)
    elif keep.is_primary:
        refresh_primary_cache(db, vocab)
    logger.debug(f"  [merge-sense] {vocab.hanzi}: merged sense {drop.id} into {keep.id}")
    return keep


def fill_sense_pinyin(db: Session, sense: VocabSense, pinyin: str):
    """Fills in a sense's pinyin only if it's currently blank."""
    if pinyin and not (sense.pinyin or "").strip():
        sense.pinyin = pinyin
        db.flush()
        if sense.is_primary:
            refresh_primary_cache(db, sense.vocab)


def update_incomplete_sense(db: Session, sense: VocabSense, pinyin: str, english: str) -> bool:
    """Fills in pinyin/english on a sense ONLY where it's blank or an
    UNKNOWN_* placeholder -- never overwrites good data. Returns True if
    anything changed."""
    changed = False
    if (not sense.pinyin or "UNKNOWN_PINYIN" in sense.pinyin) and pinyin and "UNKNOWN_PINYIN" not in pinyin:
        sense.pinyin = pinyin
        changed = True
    if (not sense.english or "UNKNOWN_ENGLISH" in sense.english) and english and "UNKNOWN_ENGLISH" not in english:
        sense.english = english
        changed = True
    if changed:
        db.flush()
        if sense.is_primary:
            refresh_primary_cache(db, sense.vocab)
    return changed


# ---- unit-based sense lookups (valid once assign_units.py has run) ----

def get_senses_for_unit(db: Session, unit_number: int, word_types: list[WordType] = None,
                        hsk_level: int = 1) -> list[VocabSense]:
    """The SENSES homed at this unit (not Vocab rows): a unit can introduce
    a new meaning of a word whose primary sense lives elsewhere."""
    q = (
        db.query(VocabSense)
        .join(Unit, VocabSense.unit_id == Unit.id)
        .filter(Unit.unit_number == unit_number, Unit.hsk_level == hsk_level)
    )
    if word_types:
        q = q.filter(VocabSense.word_type.in_(word_types))
    return q.all()


def get_senses_taught_by(db: Session, vocab_id: int, unit_number: int,
                         hsk_level: int = 1) -> list[VocabSense]:
    """Every sense of `vocab_id` already taught by (hsk_level, unit_number)."""
    senses = db.query(VocabSense).filter(VocabSense.vocab_id == vocab_id).all()
    return [s for s in senses
            if (hk := _sense_home_key(s)) is not None and hk <= (hsk_level, unit_number)]


def resolve_sense_for_sentence(db: Session, vocab_id: int, unit_number: int,
                               hsk_level: int = 1) -> VocabSense | None:
    """FALLBACK guess at which sense a sentence in (unit_number, hsk_level)
    uses: the latest sense already taught by then, else the primary, else
    any. Tagged sentences don't need this -- SentenceVocab.vocab_sense_id
    records the resolved sense directly."""
    candidates = get_senses_taught_by(db, vocab_id, unit_number, hsk_level)
    if candidates:
        return max(candidates, key=lambda s: (s.unit.hsk_level, s.unit.unit_number))
    all_senses = db.query(VocabSense).filter(VocabSense.vocab_id == vocab_id).all()
    if not all_senses:
        return None
    return next((s for s in all_senses if s.is_primary), all_senses[0])


def get_word_definitions(db: Session, hanzi: str, unit_number: int = None, hsk_level: int = 1):
    """Returns (primary, others) for `hanzi`, for a "definition + other
    meanings underneath" display. primary = the sense relevant at
    unit_number if given, else the word's primary sense. others = the rest,
    taught-in-this-level first (earliest unit first), then other levels,
    then senses with no unit."""
    vocab = db.query(Vocab).filter(Vocab.hanzi == hanzi).first()
    if vocab is None or not vocab.senses:
        return None, []

    if unit_number is not None:
        primary = resolve_sense_for_sentence(db, vocab.id, unit_number, hsk_level)
    else:
        primary = next((s for s in vocab.senses if s.is_primary), vocab.senses[0])

    def sort_key(s: VocabSense):
        if s.unit is None:
            return (2, 0, 0)
        same_level = 0 if s.unit.hsk_level == hsk_level else 1
        return (same_level, s.unit.hsk_level, s.unit.unit_number)

    others = sorted((s for s in vocab.senses if primary is None or s.id != primary.id), key=sort_key)
    return primary, others


# --------------------------------- SENSE CACHE ---------------------------------

def get_cached_sense(db: Session, hanzi: str, pos_tag: str, pinyin: str) -> VocabSense | None:
    """(hanzi, pos_tag, citation pinyin) already resolved before? Zero AI
    calls on a hit. pinyin is normalized, so spacing / ü spelling don't
    cause misses."""
    if not (hanzi and pos_tag and pinyin):
        return None
    entry = (
        db.query(SenseCache)
        .filter(SenseCache.hanzi == hanzi, SenseCache.pos_tag == pos_tag,
                SenseCache.pinyin == _normalize_pinyin(pinyin))
        .first()
    )
    return entry.sense if entry else None


def write_sense_cache(db: Session, hanzi: str, pos_tag: str, pinyin: str, sense: VocabSense):
    """Records a (hanzi, pos_tag, pinyin) -> sense resolution. Idempotent:
    an existing entry is repointed rather than duplicated."""
    if not (hanzi and pos_tag and pinyin):
        return
    key = _normalize_pinyin(pinyin)
    entry = (
        db.query(SenseCache)
        .filter(SenseCache.hanzi == hanzi, SenseCache.pos_tag == pos_tag, SenseCache.pinyin == key)
        .first()
    )
    if entry is None:
        db.add(SenseCache(hanzi=hanzi, pos_tag=pos_tag, pinyin=key, vocab_sense_id=sense.id))
    else:
        entry.vocab_sense_id = sense.id
    db.flush()


def get_senses_matching_pos_pinyin(db: Session, hanzi: str, pos_tag: str, pinyin: str) -> list[VocabSense]:
    """Existing senses of `hanzi` with this exact pos_tag and (normalized)
    citation pinyin -- the no-AI backfill path when the cache has no entry."""
    want = _normalize_pinyin(pinyin)
    return [s for s in get_senses_for_vocab(db, hanzi)
            if s.pos_tag == pos_tag and _normalize_pinyin(s.pinyin) == want]


# --------------------------------- SENTENCES ---------------------------------

def _coerce_source(source) -> "SentenceSource | None":
    """Accepts a SentenceSource member or a plain string ("hsa") and
    normalizes to the enum; unknown strings raise."""
    if source is None or isinstance(source, SentenceSource):
        return source
    try:
        return SentenceSource(source)
    except ValueError:
        raise ValueError(
            f"Unknown sentence source {source!r} -- expected one of "
            f"{[s.value for s in SentenceSource]}"
        )


def upsert_import_sentence(db: Session, hanzi: str, english: str, external_id: str,
                           source: str | SentenceSource = SentenceSource.hsa,
                           topic: str | None = None, audio_normal: str | None = None,
                           audio_slow: str | None = None, pinyin: str = "") -> tuple[Sentence, bool]:
    """import_hsa.py: writes one bare sentence (no unit, no tags).

    Keyed on external_id: re-importing the same dataset row updates it in
    place. Sentence text is unique, so a DIFFERENT external_id with text
    already in the table is a duplicate in the dataset: the existing row is
    kept untouched and returned. Returns (sentence, written) -- written is
    False for a skipped duplicate."""
    source = _coerce_source(source)
    sentence = db.query(Sentence).filter(Sentence.external_id == external_id).first()
    if sentence is None:
        dup = db.query(Sentence).filter(Sentence.hanzi == hanzi).first()
        if dup is not None:
            logger.debug(f"  [import] duplicate text {hanzi!r}: keeping {dup.external_id}, skipping {external_id}")
            return dup, False
        sentence = Sentence(hanzi=hanzi, external_id=external_id, unit_id=None)
        db.add(sentence)
    sentence.hanzi = hanzi
    sentence.english = english or sentence.english or ""
    sentence.pinyin = pinyin or sentence.pinyin or ""
    sentence.source = source
    sentence.topic = topic
    sentence.audio_normal = audio_normal
    sentence.audio_slow = audio_slow
    db.flush()
    return sentence, True


def get_untagged_sentences(db: Session, source: str | SentenceSource | None = None,
                           limit: int | None = None) -> list[Sentence]:
    """tag_sentences.py: sentences with no SentenceVocab rows yet. No join to
    units (sentences have none yet) and no level filter (a sentence's level
    is only known after tagging)."""
    q = (
        db.query(Sentence)
        .outerjoin(SentenceVocab, SentenceVocab.sentence_id == Sentence.id)
        .filter(SentenceVocab.id.is_(None))
        .order_by(Sentence.id)
    )
    if source is not None:
        q = q.filter(Sentence.source == _coerce_source(source))
    if limit:
        q = q.limit(limit)
    return q.all()


def set_sentence_tags(db: Session, sentence: Sentence, resolved_tags: list):
    """Replaces a sentence's SentenceVocab rows wholesale, in sentence order
    (position = index). Each tag is either a dict:
        {"vocab_id", "sense_id", "pos_tag", "reading", "context_definition", "pinyin"}
    (only vocab_id required; sense_id None = resolve_senses.py picks it
    later), or the older tuple form (vocab_id, vocab_sense_id[,
    context_definition[, pinyin]])."""
    db.query(SentenceVocab).filter(SentenceVocab.sentence_id == sentence.id).delete()
    db.flush()
    for position, tag in enumerate(resolved_tags):
        if isinstance(tag, dict):
            t = tag
        else:
            vocab_id, sense_id, *rest = tag
            t = {"vocab_id": vocab_id, "sense_id": sense_id,
                 "context_definition": rest[0] if len(rest) > 0 else None,
                 "pinyin": rest[1] if len(rest) > 1 else None}
        db.add(SentenceVocab(sentence_id=sentence.id, vocab_id=t["vocab_id"],
                             vocab_sense_id=t.get("sense_id"), position=position,
                             pos_tag=t.get("pos_tag"), reading=t.get("reading"),
                             context_definition=t.get("context_definition"), pinyin=t.get("pinyin")))
    db.flush()


def delete_unused_senses(db: Session) -> int:
    """Deletes senses that no sentence uses any more (left over after
    meanings are re-picked), with their SenseCache rows and the questions
    built on them (create_questions.py rebuilds questions anyway). Keeps the
    Vocab rows. Returns how many senses were deleted."""
    used = {sid for (sid,) in db.query(SentenceVocab.vocab_sense_id).filter(
        SentenceVocab.vocab_sense_id.isnot(None)).distinct().all()}
    unused = [sid for (sid,) in db.query(VocabSense.id).all() if sid not in used]
    for i in range(0, len(unused), 500):
        chunk = unused[i:i + 500]
        db.query(SenseCache).filter(SenseCache.vocab_sense_id.in_(chunk)).delete(synchronize_session=False)
        db.query(Question).filter(Question.vocab_sense_id.in_(chunk)).delete(synchronize_session=False)
        db.query(VocabSense).filter(VocabSense.id.in_(chunk)).delete(synchronize_session=False)
    db.flush()
    db.expire_all()
    return len(unused)


def delete_unused_non_list_vocab(db: Session) -> tuple[int, int]:
    """After re-tagging: deletes every sense no sentence uses, then words
    OUTSIDE the HSK list (names, unlisted, AI-defined) that no sentence uses
    any more (e.g. 今 after 今晚 stops being split), with their questions and
    pinyin-drill rows. HSK-list words are never deleted. Returns
    (senses deleted, words deleted)."""
    senses_gone = delete_unused_senses(db)
    used = {vid for (vid,) in db.query(SentenceVocab.vocab_id).distinct().all()}
    orphans = [vid for (vid,) in db.query(Vocab.id).filter(Vocab.hsk_level.is_(None)).all() if vid not in used]
    for i in range(0, len(orphans), 500):
        chunk = orphans[i:i + 500]
        sense_ids = [sid for (sid,) in db.query(VocabSense.id).filter(VocabSense.vocab_id.in_(chunk)).all()]
        if sense_ids:
            db.query(SenseCache).filter(SenseCache.vocab_sense_id.in_(sense_ids)).delete(synchronize_session=False)
            db.query(Question).filter(Question.vocab_sense_id.in_(sense_ids)).delete(synchronize_session=False)
            db.query(VocabSense).filter(VocabSense.id.in_(sense_ids)).delete(synchronize_session=False)
        db.query(Question).filter(Question.vocab_id.in_(chunk)).delete(synchronize_session=False)
        db.query(PinyinWord).filter(PinyinWord.vocab_id.in_(chunk)).delete(synchronize_session=False)
        db.query(Vocab).filter(Vocab.id.in_(chunk)).delete(synchronize_session=False)
    db.flush()
    db.expire_all()
    return senses_gone, len(orphans)


def set_sentence_level(db: Session, sentence: Sentence) -> int | None:
    """Sets and returns the sentence's level from its words alone:
      - any word outside the HSK list that isn't a name -> OUT_OF_LIST_LEVEL
        (never built, so the sentence is in no level's units)
      - a piece of a listed pattern (虽然 of 虽然……但是……) without every
        other piece in the sentence counts as outside the list too
      - otherwise the highest hsk_level among its list words; names don't
        count (they're taught in the unit, but don't decide the level)
      - None if it has no list words at all.
    detect_grammar.py may raise an HSK 1 sentence afterwards."""
    rows = (
        db.query(Vocab.hsk_level, Vocab.word_type, Vocab.hanzi, Vocab.pattern)
        .join(SentenceVocab, SentenceVocab.vocab_id == Vocab.id)
        .filter(SentenceVocab.sentence_id == sentence.id)
        .all()
    )
    words = {hanzi for _, _, hanzi, _ in rows}
    incomplete_pattern = any(pattern and not all(p in words for p in pattern_pieces(pattern))
                             for _, _, _, pattern in rows)
    if incomplete_pattern or any(lvl is None and wt != WordType.proper_noun for lvl, wt, _, _ in rows):
        level = OUT_OF_LIST_LEVEL
    else:
        level = max((lvl for lvl, _, _, _ in rows if lvl is not None), default=None)
    sentence.hsk_level = level
    db.flush()
    return level


def get_sentences_for_unit(db: Session, unit_number: int, hsk_level: int = 1) -> list[Sentence]:
    return (
        db.query(Sentence)
        .join(Unit, Sentence.unit_id == Unit.id)
        .filter(Unit.unit_number == unit_number, Unit.hsk_level == hsk_level)
        .all()
    )


def get_tags_for_sentence(db: Session, sentence_id: int) -> list[str]:
    """Hanzi of every tagged word in a sentence, in order. Used by grading
    code to know which StrengthTable tags to update."""
    rows = (
        db.query(Vocab.hanzi)
        .join(SentenceVocab, SentenceVocab.vocab_id == Vocab.id)
        .filter(SentenceVocab.sentence_id == sentence_id)
        .order_by(SentenceVocab.position)
        .all()
    )
    return [r.hanzi for r in rows]


def find_example_sentence(db: Session, unit_number: int, word: str, hsk_level: int = 1) -> str | None:
    """An example sentence for `word` in (unit_number, hsk_level): the
    shortest tagged sentence using it, else a FITB question's sentence whose
    answer is the word. None if neither exists."""
    sentence_with_tag = (
        db.query(Sentence.hanzi)
        .join(Unit, Sentence.unit_id == Unit.id)
        .join(SentenceVocab, SentenceVocab.sentence_id == Sentence.id)
        .join(Vocab, SentenceVocab.vocab_id == Vocab.id)
        .filter(Unit.unit_number == unit_number, Unit.hsk_level == hsk_level, Vocab.hanzi == word)
        .order_by(func.length(Sentence.hanzi))
        .first()
    )
    if sentence_with_tag:
        return sentence_with_tag[0]

    fitb_sentence = (
        db.query(FitbQuestion.full_sentence)
        .join(Unit, FitbQuestion.unit_id == Unit.id)
        .filter(Unit.unit_number == unit_number, Unit.hsk_level == hsk_level, FitbQuestion.answer == word)
        .order_by(func.length(FitbQuestion.full_sentence))
        .first()
    )
    if fitb_sentence:
        return fitb_sentence[0]
    return None


# --------------------------------- GRAMMAR ---------------------------------

def get_or_create_grammar_point(db: Session, grammar_code: str, hsk_level: int, raw_text: str,
                                content_dict: dict | None = None) -> GrammarTip:
    """Grammar stage: one row per point in the official grammar list, keyed
    on grammar_code (e.g. "1-16"). Created with no unit -- assign_units.py
    sets the unit where it first appears. A re-run updates the text and
    content in place."""
    row = db.query(GrammarTip).filter(GrammarTip.grammar_code == grammar_code).first()
    if row is None:
        row = GrammarTip(grammar_code=grammar_code, unit_id=None)
        db.add(row)
    row.hsk_level = hsk_level
    row.raw_text = raw_text
    if content_dict is not None:
        row.content_json = json.dumps(content_dict, ensure_ascii=False)
    elif not row.content_json:
        row.content_json = "{}"
    db.flush()
    return row


def link_sentence_grammar(db: Session, sentence_id: int, grammar_tip_id: int):
    """Idempotent: linking the same (sentence, grammar point) twice is a no-op."""
    exists = (
        db.query(SentenceGrammar)
        .filter(SentenceGrammar.sentence_id == sentence_id,
                SentenceGrammar.grammar_tip_id == grammar_tip_id)
        .first()
    )
    if exists is None:
        db.add(SentenceGrammar(sentence_id=sentence_id, grammar_tip_id=grammar_tip_id))
        db.flush()


def get_grammar_tips_for_sentence(db: Session, sentence_id: int) -> list[dict]:
    """The parsed content_json of every grammar point attached to a sentence."""
    rows = (
        db.query(GrammarTip)
        .join(SentenceGrammar, SentenceGrammar.grammar_tip_id == GrammarTip.id)
        .filter(SentenceGrammar.sentence_id == sentence_id)
        .all()
    )
    out = []
    for r in rows:
        try:
            out.append(json.loads(r.content_json))
        except (json.JSONDecodeError, TypeError):
            out.append({})
    return out


# --------------------------------- QUESTIONS ---------------------------------

def upsert_question(db: Session, unit_number: int, question_type: str, question_text: str,
                    answer_text: str, vocab_id: int = None, vocab_sense_id: int = None,
                    sentence_id: int = None, legacy_id: str = None, hsk_level: int = 1) -> Question:
    """Keyed on (question_type, question, answer) within a unit, so re-runs
    don't duplicate. Pass vocab_id (+ vocab_sense_id) for word-level
    questions, sentence_id for sentence-level ones."""
    unit = get_or_create_unit(db, unit_number, hsk_level)
    row = (
        db.query(Question)
        .filter(Question.unit_id == unit.id,
                Question.question_type == question_type,
                Question.question == question_text,
                Question.answer == answer_text)
        .first()
    )
    if row is None:
        row = Question(
            unit_id=unit.id, question_type=question_type, question=question_text,
            answer=answer_text, vocab_id=vocab_id, vocab_sense_id=vocab_sense_id,
            sentence_id=sentence_id, legacy_id=legacy_id,
        )
        db.add(row)
        db.flush()
    else:
        if vocab_id is not None and row.vocab_id is None:
            row.vocab_id = vocab_id
        if vocab_sense_id is not None and row.vocab_sense_id is None:
            row.vocab_sense_id = vocab_sense_id
        if sentence_id is not None and row.sentence_id is None:
            row.sentence_id = sentence_id
        db.flush()
    return row


# --------------------------------- LEGACY LOOKUPS ---------------------------------
# Still correct under the new schema (once units are assigned) but written
# for old pipeline scripts. Kept in case app code imports them; delete any
# that `grep -rn <name> app/` shows nothing uses.

def get_word_to_pinyin_map(db: Session) -> dict:
    """{hanzi: pinyin} from each word's cached primary sense."""
    return {v.hanzi: v.pinyin for v in db.query(Vocab.hanzi, Vocab.pinyin).all()}


def get_word_to_unit_map(db: Session) -> dict:
    """{hanzi: (unit_number, hsk_level)} -- each word's EARLIEST home across
    all its senses."""
    rows = (
        db.query(Vocab.hanzi, Unit.unit_number, Unit.hsk_level)
        .join(VocabSense, VocabSense.vocab_id == Vocab.id)
        .join(Unit, VocabSense.unit_id == Unit.id)
        .all()
    )
    earliest = {}
    for hanzi, unit_number, hsk_level in rows:
        key = (hsk_level, unit_number)
        if hanzi not in earliest or key < earliest[hanzi]:
            earliest[hanzi] = key
    return {hanzi: (unit_number, hsk_level) for hanzi, (hsk_level, unit_number) in earliest.items()}


def get_word_usage_units(db: Session, hsk_level: int = 1) -> dict:
    """{hanzi: {unit_number, ...}} -- every unit in this level where the word
    is used: sense homes, sentence tags and FITB answers."""
    usage = defaultdict(set)
    for hanzi, unit_num in (
        db.query(Vocab.hanzi, Unit.unit_number)
        .join(VocabSense, VocabSense.vocab_id == Vocab.id)
        .join(Unit, VocabSense.unit_id == Unit.id)
        .filter(Unit.hsk_level == hsk_level)
        .all()
    ):
        usage[hanzi].add(unit_num)
    for hanzi, unit_num in (
        db.query(Vocab.hanzi, Unit.unit_number)
        .join(SentenceVocab, SentenceVocab.vocab_id == Vocab.id)
        .join(Sentence, SentenceVocab.sentence_id == Sentence.id)
        .join(Unit, Sentence.unit_id == Unit.id)
        .filter(Unit.hsk_level == hsk_level)
        .all()
    ):
        usage[hanzi].add(unit_num)
    for answer, unit_num in (
        db.query(FitbQuestion.answer, Unit.unit_number)
        .join(Unit, FitbQuestion.unit_id == Unit.id)
        .filter(Unit.hsk_level == hsk_level)
        .all()
    ):
        if answer:
            usage[answer].add(unit_num)
    return dict(usage)


def get_all_vocab_senses_with_status(db: Session) -> tuple:
    """(senses_by_hanzi, incomplete_sense_ids): every sense grouped by
    hanzi, plus the ids of senses with blank or UNKNOWN_* pinyin/english."""
    all_senses = db.query(VocabSense).all()
    by_hanzi = defaultdict(list)
    for s in all_senses:
        by_hanzi[s.vocab.hanzi].append(s)

    def _is_incomplete(s: VocabSense) -> bool:
        pinyin, english = s.pinyin or "", s.english or ""
        return ("UNKNOWN_PINYIN" in pinyin or "UNKNOWN_ENGLISH" in english
                or not pinyin.strip() or not english.strip())

    return dict(by_hanzi), {s.id for s in all_senses if _is_incomplete(s)}


def get_uncovered_word_units(db: Session, hsk_level: int = 1) -> list:
    """[(hanzi, unit_number), ...] -- uses of a word in this level with no
    complete sense homed at or before that unit. Should be empty once
    assign_units.py has run; useful as a sanity check."""
    usage = get_word_usage_units(db, hsk_level=hsk_level)
    _, incomplete_ids = get_all_vocab_senses_with_status(db)
    gaps = []
    for hanzi, units in usage.items():
        vocab = db.query(Vocab).filter(Vocab.hanzi == hanzi).first()
        senses = vocab.senses if vocab else []
        complete_homed = sorted(
            s.unit.unit_number for s in senses
            if s.unit is not None and s.unit.hsk_level == hsk_level and s.id not in incomplete_ids
        )
        for u in sorted(units):
            if not any(h <= u for h in complete_homed):
                gaps.append((hanzi, u))
    return gaps


def get_all_taught_words(db: Session, hsk_level: int = 1) -> dict[str, int]:
    """{word: earliest_unit_number} in this level, from vocab homes, sentence
    tags and FITB answers."""
    word_units = {}
    for hanzi, unit_num in (
        db.query(Vocab.hanzi, Unit.unit_number)
        .join(Unit, Vocab.unit_id == Unit.id)
        .filter(Vocab.word_type != WordType.auto, Unit.hsk_level == hsk_level)
        .all()
    ):
        word_units.setdefault(hanzi, unit_num)
    for hanzi, unit_num in (
        db.query(Vocab.hanzi, Unit.unit_number)
        .join(SentenceVocab, SentenceVocab.vocab_id == Vocab.id)
        .join(Sentence, SentenceVocab.sentence_id == Sentence.id)
        .join(Unit, Sentence.unit_id == Unit.id)
        .filter(Unit.hsk_level == hsk_level)
        .all()
    ):
        if hanzi not in word_units or unit_num < word_units[hanzi]:
            word_units[hanzi] = unit_num
    for answer, unit_num in (
        db.query(FitbQuestion.answer, Unit.unit_number)
        .join(Unit, FitbQuestion.unit_id == Unit.id)
        .filter(Unit.hsk_level == hsk_level)
        .all()
    ):
        if answer and (answer not in word_units or unit_num < word_units[answer]):
            word_units[answer] = unit_num
    return word_units