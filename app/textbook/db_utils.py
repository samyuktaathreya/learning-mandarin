# textbook/db.py
"""
Engine/session setup for the textbook SQL database, plus the upsert helpers
shared by every pipeline script (vocab_index_parser, sentence_parser,
extract_and_match_grammar, create_questions). Centralizing these here means
each script does its OCR/agent work and then calls these functions instead of
each maintaining its own JSON read/write/merge logic.

HSK-LEVEL MIGRATION: `units.unit_number` is no longer globally unique --
uniqueness moved to (unit_number, hsk_level), since unit numbering restarts
per HSK level (see the migration doc / models.py's `_unit_number_hsk_level_uc`
constraint). Every function here that resolves or creates a Unit row now
takes an `hsk_level: int = 1` parameter, defaulting to 1 so every EXISTING
caller (this app's own crud.py already follows this same default-1 pattern)
keeps working unchanged against today's HSK1-only data. Only the pipeline
scripts pass hsk_level explicitly, via the HSK_LEVEL env var main.py sets.
"""
from sqlalchemy import create_engine, func, or_, and_
from sqlalchemy.orm import sessionmaker, Session
from contextlib import contextmanager

from .models import (
    Base, Unit, Vocab, VocabSense, Sentence, SentenceVocab, WordType,
    VocabOrigin, SentenceSource,
    GrammarTip, SentenceGrammar, Question, FitbQuestion, SenseCache,
)
import json
from collections import defaultdict
from app.core.config.data import TEXTBOOK_DB


engine = create_engine(f"sqlite:///{TEXTBOOK_DB}", connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def init_db():
    Base.metadata.create_all(bind=engine)


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


# --------------------------------- UNIT ---------------------------------

def get_or_create_unit(db: Session, unit_number: int, hsk_level: int = 1) -> Unit:
    """unit_number alone no longer identifies a unique row -- (unit_number,
    hsk_level) together do, since numbering restarts per level. Every
    caller of this function (directly or via the upsert helpers below) must
    now supply the hsk_level it means, or it'll silently default to 1."""
    unit = (
        db.query(Unit)
        .filter(Unit.unit_number == unit_number, Unit.hsk_level == hsk_level)
        .first()
    )
    if unit is None:
        unit = Unit(unit_number=unit_number, hsk_level=hsk_level)
        db.add(unit)
        db.flush()  # need unit.id before it's referenced by FKs below
    return unit


# --------------------------------- VOCAB ---------------------------------
#
# TERMINOLOGY: a "sense" (VocabSense row) is one distinct taught MEANING of
# a hanzi -- its own pinyin/english/home-unit. A hanzi (Vocab row) can have
# several senses (e.g. 还 "still" homed at HSK1 unit 5, vs. 还 "to return
# (something)" homed at HSK3 unit 20). Vocab.pinyin/english/unit_id/word_type
# remain as a CACHED SNAPSHOT of the word's PRIMARY sense (see
# VocabSense.is_primary) -- kept in sync here, purely for callers that
# haven't been migrated to be sense-aware. New code should go through the
# sense functions below rather than reading/writing Vocab's cache fields
# directly.
#
# SENSE-MATCHING POLICY (decides whether a new definition is a NEW sense or
# just a restatement of one already on file):
#   - vocab_index_parser.py has real printed-index text on both sides for
#     every comparison it makes, so it trusts that text directly (handled
#     entirely in vocab_index_parser.py, via upsert_vocab_sense below).
#   - Every OTHER source of a definition (CEDICT gap-fills, Claude-authored
#     definitions, parent-word recovery -- i.e. anything append_orphan_tags.py
#     does) is, by definition, a case where the printed index didn't cover
#     this appearance. Those callers should use register_word_sense()
#     instead, which asks Claude to compare against the nearest existing
#     sense before deciding to fragment into a new one.

def _is_placeholder_sense(sense: VocabSense) -> bool:
    """A bare unknown-word placeholder (sentence_parser.upsert_vocab_auto's
    output) -- auto word_type AND blank english. Distinct from a genuinely
    complete "auto" classification, which can't happen (auto is only ever
    used for placeholders), but checking both keeps this from ever
    misfiring on legitimate data."""
    return sense.word_type == WordType.auto and not (sense.english or "").strip()

def get_vocab_hanzi_through_level(db: Session, hsk_level: int) -> set:
    """Hanzi with at least one sense taught at or before `hsk_level` --
    the correct input for tag_sentences.py's HSK vocab-list gate (unlike
    get_all_vocab_hanzi, which is intentionally global/unscoped for
    compound-word matching, not level cutoffs)."""
    rows = (
        db.query(Vocab.hanzi)
        .join(VocabSense, VocabSense.vocab_id == Vocab.id)
        .join(Unit, VocabSense.unit_id == Unit.id)
        .filter(Unit.hsk_level <= hsk_level, VocabSense.word_type != WordType.auto)
        .distinct()
        .all()
    )
    return {r.hanzi for r in rows}


def upsert_vocab_sense(db: Session, hanzi: str, pinyin: str, english: str,
                        unit_number: int | None, word_type: WordType = WordType.vocab,
                        hsk_level: int = 1, make_primary: bool | None = None,
                        origin: VocabOrigin = VocabOrigin.vocab_index) -> VocabSense:
    """
    Insert-or-reuse ONE taught meaning of `hanzi`. Creates the Vocab
    identity row if this is a brand-new hanzi.

    Idempotency: if a sense already exists for (vocab, unit, hsk_level,
    english) -- literally the same meaning already recorded at this exact
    home -- that row is reused rather than duplicated, so reruns of
    vocab_index_parser.py stay idempotent. A DIFFERENT `english` for a
    hanzi that already has senses ALWAYS creates a new VocabSense row here
    -- this function trusts whatever sense identity the caller has already
    resolved; it does NOT do same-vs-new-sense disambiguation itself (see
    register_word_sense for that).

    origin: which pipeline stage is registering this meaning --
    VocabOrigin.vocab_index (default, for vocab_index_parser.py) or
    VocabOrigin.textbook_sentence (for tag_sentences.py, when a sentence
    surfaces a word/meaning the printed index never listed). Set once at
    creation; reused (existing) senses keep whatever origin they were
    first created with -- this function never overwrites an existing
    sense's origin.

    make_primary: None (default) = primary iff this is the very first
    sense ever recorded for this word, OR the word's current primary is
    still just sentence_parser's blank unknown-word placeholder (created
    before any real definition existed) and this new sense is a real one
    -- promoting a placeholder as soon as real content arrives, rather
    than leaving Vocab's cache permanently blank because something else
    tagged this word in a sentence before vocab_index_parser.py or
    append_orphan_tags.py ever got to it. True/False forces it explicitly.
    """
    vocab = get_or_create_vocab(db, hanzi)
    unit = get_or_create_unit(db, unit_number, hsk_level) if unit_number is not None else None
    unit_id = unit.id if unit else None

    existing = (
        db.query(VocabSense)
        .filter(VocabSense.vocab_id == vocab.id, VocabSense.unit_id == unit_id,
                VocabSense.english == english)
        .first()
    )
    if existing is not None:
        if pinyin and not existing.pinyin:
            existing.pinyin = pinyin
            db.flush()
        if make_primary:
            set_primary_sense(db, vocab, existing)
        return existing

    is_first_sense = db.query(VocabSense).filter(VocabSense.vocab_id == vocab.id).count() == 0
    current_primary = (
        db.query(VocabSense)
        .filter(VocabSense.vocab_id == vocab.id, VocabSense.is_primary == 1)
        .first()
    )
    promotes_placeholder = (
        make_primary is None and not is_first_sense
        and current_primary is not None and _is_placeholder_sense(current_primary)
        and word_type != WordType.auto and (english or "").strip()
    )
    sense = VocabSense(
        vocab_id=vocab.id, unit_id=unit_id, pinyin=pinyin, english=english,
        word_type=word_type, origin=origin,
        is_primary=1 if (make_primary or (make_primary is None and is_first_sense) or promotes_placeholder) else 0,
    )
    db.add(sense)
    db.flush()
    if sense.is_primary:
        set_primary_sense(db, vocab, sense)  # clears any other primary, syncs Vocab's cache
    return sense


def get_word_definitions(db: Session, hanzi: str, unit_number: int = None, hsk_level: int = 1):
    """Returns (primary, others) for `hanzi`, ready for a "definition +
    other meanings underneath" display:
    - primary: the single most RELEVANT sense -- if unit_number is given,
      whichever sense resolve_sense_for_sentence would pick for that point
      in the curriculum (the latest one already taught by then); otherwise
      the word's overall primary sense. None if the word has no senses.
    - others: every other sense, ordered taught-in-this-hsk_level first
      (earliest unit first), then taught-in-other-hsk_levels, then
      untaught CEDICT reference senses (unit=None) last -- so a curious
      user sees "meanings you've been taught" before "other dictionary
      meanings you haven't gotten to yet."
    """
    vocab = db.query(Vocab).filter(Vocab.hanzi == hanzi).first()
    if vocab is None or not vocab.senses:
        return None, []

    if unit_number is not None:
        primary = resolve_sense_for_sentence(db, vocab.id, unit_number, hsk_level)
    else:
        primary = next((s for s in vocab.senses if s.is_primary), vocab.senses[0])

    def sort_key(s: VocabSense):
        if s.unit is None:
            return (2, 0, 0)  # untaught reference sense -- sorts last
        same_level = 0 if s.unit.hsk_level == hsk_level else 1
        return (same_level, s.unit.hsk_level, s.unit.unit_number)

    others = sorted((s for s in vocab.senses if primary is None or s.id != primary.id), key=sort_key)
    return primary, others


def get_or_create_vocab(db: Session, hanzi: str) -> Vocab:
    """Just the hanzi IDENTITY row -- no definition here, that lives on
    VocabSense. Direct callers should be rare; most code wants
    upsert_vocab_sense or register_word_sense instead."""
    row = db.query(Vocab).filter(Vocab.hanzi == hanzi).first()
    if row is None:
        row = Vocab(hanzi=hanzi, pinyin="", english="", word_type=WordType.auto, unit_id=None)
        db.add(row)
        db.flush()
    return row


def set_primary_sense(db: Session, vocab: Vocab, sense: VocabSense):
    """Marks `sense` as the primary sense for `vocab`, clearing any other
    sense's primary flag, then syncs Vocab's legacy cache columns to match.
    Exactly one primary per vocab_id is an app-level invariant (enforced
    here), not a DB constraint.

    Queries VocabSense directly rather than iterating vocab.senses --
    that in-memory relationship collection can be stale (SQLAlchemy
    doesn't auto-sync a parent's collection when a child row is created via
    its FK column, e.g. `VocabSense(vocab_id=vocab.id, ...)`, only when
    assigned through the `.vocab`/`.senses` relationship itself), so
    trusting it here could clear a primary flag on a sense that's no
    longer actually in the collection, or miss the sense being un-primaried."""
    for s in db.query(VocabSense).filter(VocabSense.vocab_id == vocab.id).all():
        if s.id != sense.id and s.is_primary:
            s.is_primary = 0
    sense.is_primary = 1
    db.flush()
    refresh_primary_cache(db, vocab)


def refresh_primary_cache(db: Session, vocab: Vocab):
    """Re-copies the current primary sense's fields onto Vocab's cache
    columns. Call this after anything that might change what the primary
    sense's data looks like (e.g. re-homing a primary sense to an earlier
    unit) without going through set_primary_sense itself.

    Queries VocabSense directly (see set_primary_sense's docstring for why
    -- vocab.senses can be stale mid-transaction for a just-created sense)."""
    primary = (
        db.query(VocabSense)
        .filter(VocabSense.vocab_id == vocab.id, VocabSense.is_primary == 1)
        .first()
    )
    if primary is None:
        return
    vocab.pinyin = primary.pinyin
    vocab.english = primary.english
    vocab.word_type = primary.word_type
    vocab.unit_id = primary.unit_id
    vocab.origin = primary.origin
    db.flush()


def upsert_vocab_auto(db: Session, hanzi: str, pinyin: str) -> Vocab:
    """Convenience wrapper for sentence_parser's unknown-word fallback tags:
    creates a bare Vocab row + a bare placeholder sense (unit=None,
    word_type=auto) if one doesn't already exist, so a tag always has both
    a Vocab row AND a sense to attach to (SentenceVocab.vocab_sense_id needs
    something to resolve to). No hsk_level needed -- unit_number is None
    either way.

    origin is stamped textbook_sentence here since this placeholder only
    ever gets created off a real sentence occurrence, never off the index."""
    existing = db.query(Vocab).filter(Vocab.hanzi == hanzi).first()
    if existing:
        return existing
    upsert_vocab_sense(db, hanzi, pinyin, english="", unit_number=None,
                        word_type=WordType.auto, make_primary=True,
                        origin=VocabOrigin.textbook_sentence)
    return db.query(Vocab).filter(Vocab.hanzi == hanzi).first()


def get_senses_for_vocab(db: Session, hanzi: str) -> list[VocabSense]:
    vocab = db.query(Vocab).filter(Vocab.hanzi == hanzi).first()
    if vocab is None:
        return []
    # Direct query rather than vocab.senses -- this is called repeatedly
    # within tag_sentences.py's per-word resolution loop, often right after
    # a sense was just created for this same vocab_id earlier in the same
    # transaction; vocab.senses can be stale in that situation (see
    # set_primary_sense's docstring).
    return db.query(VocabSense).filter(VocabSense.vocab_id == vocab.id).order_by(VocabSense.id).all()


def get_senses_for_unit(db: Session, unit_number: int, word_types: list[WordType] = None,
                         hsk_level: int = 1) -> list[VocabSense]:
    """The sense-aware replacement for get_vocab_for_unit: returns the
    SENSES homed at this unit, not Vocab rows -- a unit can introduce a new
    sense of a word whose Vocab.unit_id cache (the PRIMARY sense's home)
    points somewhere else entirely."""
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
    """Every sense of `vocab_id` already taught by (hsk_level, unit_number)
    -- i.e. every meaning a student would plausibly know by this point.
    Length 0 = a genuine coverage gap (append_orphan_tags.py's job).
    Length 1 = unambiguous. Length > 1 = real disambiguation is needed
    (which of several known meanings does THIS sentence use) -- that's a
    text-comparison problem for the caller (create_questions.py), not
    something resolvable from unit numbers alone."""
    senses = db.query(VocabSense).filter(VocabSense.vocab_id == vocab_id).all()

    def home_key(s: VocabSense):
        return None if s.unit is None else (s.unit.hsk_level, s.unit.unit_number)

    return [s for s in senses if (hk := home_key(s)) is not None and hk <= (hsk_level, unit_number)]


def resolve_sense_for_sentence(db: Session, vocab_id: int, unit_number: int,
                                hsk_level: int = 1) -> VocabSense | None:
    """Picks which sense a sentence in (unit_number, hsk_level) is most
    likely demonstrating: the LATEST-homed sense already taught by that
    point (the most recently introduced meaning a student would know by
    now) -- a reasonable default when a word has exactly one candidate, and
    still the best guess when it has several (real per-sentence
    disambiguation among multiple candidates needs sentence text + Claude;
    see create_questions.resolve_sentence_sense_ambiguity). Falls back to the
    word's primary sense if nothing is homed early enough, then to
    whatever sense exists at all. Returns None only if the word has no
    senses whatsoever yet."""
    candidates = get_senses_taught_by(db, vocab_id, unit_number, hsk_level)
    if candidates:
        return max(candidates, key=lambda s: (s.unit.hsk_level, s.unit.unit_number))

    all_senses = db.query(VocabSense).filter(VocabSense.vocab_id == vocab_id).all()
    if not all_senses:
        return None
    primary = next((s for s in all_senses if s.is_primary), None)
    return primary or all_senses[0]


def get_highest_unit_number(db: Session, hsk_level: int = 1) -> int | None:
    """Highest unit_number that exists for `hsk_level` -- used by
    import_sentences.py to place external sentences/vocab that introduce a
    genuinely new word with no textbook anchor: rather than guessing a
    placement, they go at the END of the level (the learner is assumed to
    already know everything else the level covers by that point). Returns
    None if this hsk_level has no units yet (i.e. the textbook pipeline
    hasn't been run for it)."""
    return (
        db.query(func.max(Unit.unit_number))
        .filter(Unit.hsk_level == hsk_level)
        .scalar()
    )


def get_word_to_pinyin_map(db: Session) -> dict:
    """Replaces reading word_to_pinyin.json. Now sourced from each word's
    PRIMARY sense via Vocab's cache columns -- fine for sentence_parser's
    use (picking a reasonable pinyin to display before any sense has been
    resolved for a brand-new sentence); sense-specific pinyin is looked up
    later via the sense itself once tags are written."""
    return {v.hanzi: v.pinyin for v in db.query(Vocab.hanzi, Vocab.pinyin).all()}


def get_word_to_unit_map(db: Session) -> dict:
    """Replaces reading word_to_unit.json. Returns each word's EARLIEST
    known home across all its senses (not just the primary sense) --
    sentence_parser's vocab-gate logic ("has this word been taught by unit
    N yet") should treat a word as known as soon as ANY of its meanings has
    been introduced, not only its primary one. Returns {hanzi:
    (unit_number, hsk_level)} tuples since "unit 3" alone is ambiguous once
    more than one hsk_level exists."""
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
    """{hanzi: {unit_number, ...}} -- EVERY unit (within this hsk_level)
    where the word is actually used: sense homes + sentence tags + FITB
    answers. Multi-valued (unlike get_word_to_unit_map's single earliest
    home), because sense-coverage gap detection needs to know about every
    appearance, not just the first."""
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


def get_uncovered_word_units(db: Session, hsk_level: int = 1) -> list:
    """[(hanzi, unit_number), ...] -- every place a word is actually used in
    this hsk_level's curriculum where no COMPLETE sense homed at or before
    that unit exists to explain it yet. This is the real per-appearance gap
    list append_orphan_tags.py fills, replacing the old single
    {word: earliest_unit} "missing" map -- a word can need coverage at
    several different units now (once per sense it's actually taught
    with), not just its first appearance."""
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


# --------------------------------- SENTENCE ---------------------------------

def _coerce_source(source) -> "SentenceSource | None":
    """Accepts either a SentenceSource member or a plain string (every
    existing caller passes plain strings like "textbook"/"workbook") and
    normalizes to the enum. Unknown strings raise loudly rather than
    silently writing garbage into a column that's now typed -- a typo'd
    source string used to just sit there as free text; now it should fail
    the pipeline run so it gets fixed at the source, not discovered later
    when someone tries to filter sentences by origin."""
    if source is None or isinstance(source, SentenceSource):
        return source
    try:
        return SentenceSource(source)
    except ValueError:
        raise ValueError(
            f"Unknown sentence source {source!r} -- expected one of "
            f"{[s.value for s in SentenceSource]}"
        )


def upsert_sentence_bare(db: Session, unit_number: int, hanzi: str, english: str,
                          pinyin: str, source: str = None, hsk_level: int = 1) -> Sentence:
    """Writes/updates just the Sentence row -- no tags. Used by
    sentence_parser.py now that tagging is tag_sentences.py's job (a
    separate pipeline stage run afterward, once HanLP + sense-resolution
    are available). A sentence written here has no SentenceVocab rows yet
    -- set_sentence_tags() adds those in the next stage.

    `source` distinguishes where the sentence came from: "textbook" /
    "workbook" (sentence_parser.py) vs. "external" (import_sentences.py,
    the hsk-sentence-audio pypi library) -- see models.SentenceSource.
    Accepts a plain string for backward compatibility with existing
    callers; normalized via _coerce_source.

    Re-running for the same (unit, hanzi) updates english/pinyin/source
    in place rather than duplicating, matching upsert_sentence's old
    idempotency contract (just without the tag side of it)."""
    unit = get_or_create_unit(db, unit_number, hsk_level)
    source = _coerce_source(source)

    sentence = (
        db.query(Sentence)
        .filter(Sentence.unit_id == unit.id, Sentence.hanzi == hanzi)
        .first()
    )
    if sentence is None:
        sentence = Sentence(unit_id=unit.id, hanzi=hanzi, english=english,
                            pinyin=pinyin, source=source)
        db.add(sentence)
        db.flush()
    else:
        sentence.english = english or sentence.english
        sentence.pinyin = pinyin or sentence.pinyin
        sentence.source = source or sentence.source
        db.flush()
    return sentence


def set_sentence_tags(db: Session, sentence: Sentence, resolved_tags: list[tuple[int, int | None]]):
    """Replaces a sentence's SentenceVocab rows wholesale, given tags
    tag_sentences.py has ALREADY resolved to (vocab_id, vocab_sense_id)
    pairs -- this function does no sense resolution itself, it just writes
    what it's told. `resolved_tags` is ordered (position = index in list).

    Re-running for a sentence that already has tags clears the old links
    first, so reprocessing a unit's tagging doesn't leave stale/duplicate
    SentenceVocab rows behind."""
    db.query(SentenceVocab).filter(SentenceVocab.sentence_id == sentence.id).delete()
    db.flush()
    for position, (vocab_id, sense_id) in enumerate(resolved_tags):
        db.add(SentenceVocab(sentence_id=sentence.id, vocab_id=vocab_id,
                              vocab_sense_id=sense_id, position=position))
    db.flush()


def upsert_sentence(db: Session, unit_number: int, hanzi: str, english: str,
                     pinyin: str, tags: list[str], tag_pinyins: list[str],
                     source: str = None, hsk_level: int = 1) -> Sentence:
    """LEGACY all-in-one path (sentence + auto-tags with no real sense
    resolution beyond resolve_sense_for_sentence's existing-senses-only
    lookup) -- kept only for import_sentences.py's external-sentence flow,
    which resolves/creates senses itself via the sense-cache helpers BEFORE
    calling this, so by the time this runs every tag already has a real
    Vocab row. New textbook-pipeline code should use upsert_sentence_bare +
    set_sentence_tags instead (see tag_sentences.py), since tagging is now
    a distinct pipeline stage with its own HanLP + AI-assisted sense
    resolution that this function doesn't do.

    import_sentences.py should pass source="external" (or
    SentenceSource.external) here -- see models.SentenceSource.
    """
    unit = get_or_create_unit(db, unit_number, hsk_level)
    source = _coerce_source(source)

    sentence = (
        db.query(Sentence)
        .filter(Sentence.unit_id == unit.id, Sentence.hanzi == hanzi)
        .first()
    )
    if sentence is None:
        sentence = Sentence(unit_id=unit.id, hanzi=hanzi, english=english,
                            pinyin=pinyin, source=source)
        db.add(sentence)
        db.flush()
    else:
        sentence.english = english or sentence.english
        sentence.pinyin = pinyin or sentence.pinyin
        sentence.source = source or sentence.source
        # clear old links before re-writing tags
        db.query(SentenceVocab).filter(SentenceVocab.sentence_id == sentence.id).delete()
        db.flush()

    for position, (tag, tag_pinyin) in enumerate(zip(tags, tag_pinyins)):
        vocab_row = db.query(Vocab).filter(Vocab.hanzi == tag).first()
        if vocab_row is None:
            vocab_row = upsert_vocab_auto(db, tag, tag_pinyin)
        # Resolve which taught MEANING this occurrence uses -- the latest
        # sense already introduced by (unit_number, hsk_level). See
        # resolve_sense_for_sentence's docstring for the tie-breaking rule
        # when a word has several candidate senses already taught.
        sense = resolve_sense_for_sentence(db, vocab_row.id, unit_number, hsk_level)
        db.add(SentenceVocab(sentence_id=sentence.id, vocab_id=vocab_row.id,
                              vocab_sense_id=sense.id if sense else None,
                              position=position))

    db.flush()
    return sentence


# --------------------------------- GRAMMAR ---------------------------------

def get_or_create_grammar_tip(db: Session, unit_number: int, raw_text: str,
                               content_dict: dict = None, hsk_level: int = 1) -> GrammarTip:
    """Keyed on (unit, raw_text) -- this is the idempotency point: re-running
    extract_and_match_grammar for a unit doesn't create duplicate tip rows
    or re-spend an API call reformatting text already reformatted. If
    content_dict is given and the row already has content, the existing
    content_json wins (don't clobber a good reformat with a retry's output
    unless the caller explicitly wants to overwrite -- pass overwrite=True)."""
    unit = get_or_create_unit(db, unit_number, hsk_level)
    row = (
        db.query(GrammarTip)
        .filter(GrammarTip.unit_id == unit.id, GrammarTip.raw_text == raw_text)
        .first()
    )
    if row is None:
        row = GrammarTip(
            unit_id=unit.id,
            raw_text=raw_text,
            content_json=json.dumps(content_dict or {}, ensure_ascii=False),
        )
        db.add(row)
        db.flush()
    elif content_dict is not None and row.content_json in ("{}", "", None):
        row.content_json = json.dumps(content_dict, ensure_ascii=False)
        db.flush()
    return row


def link_sentence_grammar(db: Session, sentence_id: int, grammar_tip_id: int):
    """Idempotent: adding the same (sentence, tip) link twice is a no-op,
    since a sentence being re-matched to a tip it's already matched to isn't
    an error, just redundant work."""
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
    """Returns the list of structured tip dicts (parsed content_json)
    attached to a sentence -- direct replacement for the old
    sentence["grammar_tip"] list."""
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


def get_sentences_for_unit(db: Session, unit_number: int, hsk_level: int = 1) -> list[Sentence]:
    return (
        db.query(Sentence)
        .join(Unit, Sentence.unit_id == Unit.id)
        .filter(Unit.unit_number == unit_number, Unit.hsk_level == hsk_level)
        .all()
    )


# --------------------------------- QUESTIONS ---------------------------------

def upsert_question(db: Session, unit_number: int, question_type: str, question_text: str,
                     answer_text: str, vocab_id: int = None, vocab_sense_id: int = None,
                     sentence_id: int = None, legacy_id: str = None, hsk_level: int = 1) -> Question:
    """Keyed on (question_type, question, answer) within a unit -- mirrors
    the old create_questions.py signature-based merge (`sig = (question_type,
    question, answer)`), which is what let reruns preserve IDs/tags instead
    of duplicating. legacy_id lets you carry over an old "u3_speaking_vocab_2"
    style ID if something else in the app still references it directly.

    Pass vocab_id (+ vocab_sense_id, when the question tests one specific
    taught meaning) for word-level questions, sentence_id for sentence-level
    questions -- see Question model docstring for why sentence questions
    need this (recovering their full multi-word tag list at read time)."""
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
            unit_id=unit.id,
            question_type=question_type,
            question=question_text,
            answer=answer_text,
            vocab_id=vocab_id,
            vocab_sense_id=vocab_sense_id,
            sentence_id=sentence_id,
            legacy_id=legacy_id,
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


def get_vocab_for_unit(db: Session, unit_number: int, word_types: list[WordType] = None,
                        hsk_level: int = 1) -> list[Vocab]:
    """LEGACY: returns Vocab rows filtered by their cached PRIMARY sense's
    home unit -- a word retaught with a NEW sense at this unit won't show
    up here if its primary sense lives elsewhere. Prefer
    get_senses_for_unit for anything building per-meaning content (question
    generation, etc.); kept for callers that only need "which words have
    their earliest/primary meaning here" (e.g. simple word lists)."""
    q = (
        db.query(Vocab)
        .join(Unit, Vocab.unit_id == Unit.id)
        .filter(Unit.unit_number == unit_number, Unit.hsk_level == hsk_level)
    )
    if word_types:
        q = q.filter(Vocab.word_type.in_(word_types))
    return q.all()


def get_all_vocab_hanzi(db: Session) -> list[str]:
    """All known hanzi, longest-first -- used for greedy substring matching
    when figuring out which taught words make up a COMPOUND vocab entry
    (e.g. does the vocab word itself contain other known sub-words). This is
    the DB equivalent of the old `all_hanzi` list built from
    index_output.json's vocab+grammar+proper_nouns. Global across every
    hsk_level -- a compound word can legitimately be built from sub-words
    taught in an earlier level, and per-unit "not yet taught" gating is
    handled separately (has_unlearned_vocab), not here."""
    rows = db.query(Vocab.hanzi).all()
    hanzi = [r.hanzi for r in rows]
    hanzi.sort(key=len, reverse=True)
    return hanzi


def rehome_sentences(db: Session, hsk_level: int = 1) -> dict:
    """
    For every sentence IN THIS hsk_level, its true earliest-possible unit is
    the LATEST home unit among its own SAME-LEVEL tags (a sentence can't be
    practiced before every word in it has been taught) -- NOT necessarily
    the unit it was physically extracted from.

    Deliberately stays within one hsk_level (per the migration doc): a
    sentence using only HSK1 words shouldn't get rehomed to an HSK2 unit
    just because the unit_number happens to be lower there, and a tag whose
    home is an EARLIER hsk_level than this sentence's own level doesn't
    pull the target down further -- we don't know that earlier level's
    first unit_number here, so such tags simply don't constrain the target
    (they're already "known" by definition, from a prior level). Only tags
    homed in THIS SAME hsk_level set the floor for how early the sentence
    can move. If a sentence has no same-level tags at all, it's left where
    it is (no confident earlier target to move it to).

    If a sentence's computed home unit is EARLIER than the unit it's
    currently filed under, move it there (update Sentence.unit_id) so it's
    available for practice as soon as legitimately possible. If an identical
    sentence already exists at the target unit (same hsk_level), drop this
    one as a duplicate rather than violate the (unit_id, hanzi) uniqueness
    constraint -- same "moved / deleted" bookkeeping the old JSON-based
    rehome_sentences() did, just against rows instead of dict keys.
    """
    home_unit = get_word_to_unit_map(db)  # {hanzi: (unit_number, hsk_level)}
    moved, deleted = 0, 0

    all_sentences = (
        db.query(Sentence)
        .join(Unit, Sentence.unit_id == Unit.id)
        .filter(Unit.hsk_level == hsk_level)
        .all()
    )
    # cache current (unit_number, hanzi) -> Sentence for duplicate detection,
    # refreshed as we go so a sentence moved earlier in this pass is itself
    # detectable as a duplicate target for a later sentence in the same pass
    by_unit_hanzi = {}
    for s in all_sentences:
        by_unit_hanzi[(s.unit.unit_number, s.hanzi)] = s

    for sentence in all_sentences:
        current_unit = sentence.unit.unit_number
        tags = get_tags_for_sentence(db, sentence.id)
        if not tags:
            continue

        # only same-hsk_level tags set the floor; earlier-level tags are
        # already known and don't constrain how early this sentence can go
        same_level_unit_numbers = [
            home_unit[t][0] for t in tags
            if t in home_unit and home_unit[t][1] == hsk_level
        ]
        if not same_level_unit_numbers:
            continue
        target = max(same_level_unit_numbers)
        if target >= current_unit:
            continue

        dup_key = (target, sentence.hanzi)
        if dup_key in by_unit_hanzi and by_unit_hanzi[dup_key].id != sentence.id:
            # Delete join table rows FIRST (can't cascade due to composite primary key)
            db.query(SentenceGrammar).filter(SentenceGrammar.sentence_id == sentence.id).delete()
            db.query(SentenceVocab).filter(SentenceVocab.sentence_id == sentence.id).delete()
            db.delete(sentence)
            deleted += 1
            continue

        target_unit_row = get_or_create_unit(db, target, hsk_level)
        old_key = (current_unit, sentence.hanzi)
        by_unit_hanzi.pop(old_key, None)
        sentence.unit_id = target_unit_row.id
        by_unit_hanzi[dup_key] = sentence
        moved += 1

    db.flush()
    print(f"  [rehome] hsk_level {hsk_level}: moved {moved} sentence(s) to an earlier unit, "
          f"deleted {deleted} duplicate(s)")
    return {"moved": moved, "deleted": deleted}


def get_tags_for_sentence(db: Session, sentence_id: int) -> list[str]:
    """The direct replacement for reading `sentence["tags"]` off the old JSON.
    Used by submit_session / grading code to know which StrengthTable tags
    to update after a sentence-based question."""
    rows = (
        db.query(Vocab.hanzi)
        .join(SentenceVocab, SentenceVocab.vocab_id == Vocab.id)
        .filter(SentenceVocab.sentence_id == sentence_id)
        .order_by(SentenceVocab.position)
        .all()
    )
    return [r.hanzi for r in rows]


# --------------------------------- SYNC HELPERS (for append_orphan_tags.py) ---------------------------------

def get_all_vocab_senses_with_status(db: Session) -> tuple:
    """Returns (senses_by_hanzi, incomplete_sense_ids):
    - senses_by_hanzi: {hanzi: [VocabSense, ...]} for every hanzi that has
      at least one sense.
    - incomplete_sense_ids: {VocabSense.id, ...} for any sense with a blank
      or UNKNOWN_* placeholder pinyin/english.

    This is the sense-level replacement for the old (vocab_map,
    needs_retry) pair -- "needs retry" is now evaluated per SENSE, since a
    word can have one perfectly complete sense and another still-blank one
    at the same time."""
    all_senses = db.query(VocabSense).all()
    by_hanzi = defaultdict(list)
    for s in all_senses:
        by_hanzi[s.vocab.hanzi].append(s)

    def _is_incomplete(s: VocabSense) -> bool:
        pinyin = s.pinyin or ""
        english = s.english or ""
        return (
            "UNKNOWN_PINYIN" in pinyin
            or "UNKNOWN_ENGLISH" in english
            or not pinyin.strip()
            or not english.strip()
        )

    incomplete = {s.id for s in all_senses if _is_incomplete(s)}
    return dict(by_hanzi), incomplete


def get_cached_sense(db: Session, hanzi: str, pos_tag: str, pinyin: str) -> VocabSense | None:
    """Deterministic SenseCache lookup -- (hanzi, pos_tag, pinyin) already
    resolved to a specific sense before. Zero AI calls when this hits.
    Returns None on a miss (new word, or a POS+reading combo not seen
    before for this word)."""
    if not (hanzi and pos_tag and pinyin):
        return None
    entry = (
        db.query(SenseCache)
        .filter(SenseCache.hanzi == hanzi, SenseCache.pos_tag == pos_tag, SenseCache.pinyin == pinyin)
        .first()
    )
    return entry.sense if entry else None


def write_sense_cache(db: Session, hanzi: str, pos_tag: str, pinyin: str, sense: VocabSense):
    """Records a (hanzi, pos_tag, pinyin) -> sense resolution so the next
    sentence using this exact word/POS/reading combo skips straight to this
    sense with no AI call. Idempotent: re-pointing an existing cache row to
    a different sense_id (e.g. if a sense got merged/rehomed under a new
    id) overwrites rather than erroring."""
    if not (hanzi and pos_tag and pinyin):
        return
    entry = (
        db.query(SenseCache)
        .filter(SenseCache.hanzi == hanzi, SenseCache.pos_tag == pos_tag, SenseCache.pinyin == pinyin)
        .first()
    )
    if entry is None:
        entry = SenseCache(hanzi=hanzi, pos_tag=pos_tag, pinyin=pinyin, vocab_sense_id=sense.id)
        db.add(entry)
    else:
        entry.vocab_sense_id = sense.id
    db.flush()


def get_senses_matching_pos_pinyin(db: Session, hanzi: str, pos_tag: str, pinyin: str) -> list[VocabSense]:
    """Existing senses of `hanzi` that already share this exact (pos_tag,
    pinyin) combo -- checked as a fallback when SenseCache itself has no
    entry yet (e.g. a sense was created by vocab_index_parser.py, which
    doesn't populate pos_tag), before tag_sentences.py ever saw this word).
    If this finds a match, it's used directly (and the cache backfilled)
    with no AI call; an empty result means a genuine AI comparison is
    needed."""
    senses = get_senses_for_vocab(db, hanzi)
    return [s for s in senses if s.pos_tag == pos_tag and s.pinyin == pinyin]



def get_nearest_sense(db: Session, hanzi: str, unit_number: int, hsk_level: int = 1) -> VocabSense | None:
    """The existing sense of `hanzi` whose home is CLOSEST to (unit_number,
    hsk_level) -- the natural comparison point when a new definition comes
    from OUTSIDE the printed index (CEDICT, Claude) and we need to ask
    "is this the same meaning as something already on file, or different?"
    Senses in an earlier hsk_level sort before ones in a later hsk_level
    regardless of raw distance (they're "further away" in curriculum
    order); a sense with no home unit at all sorts last. Returns None if
    the word has no senses yet."""
    senses = get_senses_for_vocab(db, hanzi)
    if not senses:
        return None

    def sort_key(s: VocabSense):
        if s.unit is None:
            return (2, 0)
        if s.unit.hsk_level < hsk_level:
            return (0, hsk_level - s.unit.hsk_level)
        if s.unit.hsk_level > hsk_level:
            return (1, s.unit.hsk_level - hsk_level)
        return (0, abs(s.unit.unit_number - unit_number))

    return min(senses, key=sort_key)


def rehome_sense(db: Session, sense: VocabSense, unit_number: int, hsk_level: int = 1):
    """Moves `sense`'s home EARLIER, to (unit_number, hsk_level), if that's
    genuinely earlier than its current home -- used when the same meaning
    turns out to have been used earlier than previously recorded. Never
    moves a sense later. Re-syncs Vocab's cache if this is the primary
    sense."""
    new_key = (hsk_level, unit_number)
    current_key = (sense.unit.hsk_level, sense.unit.unit_number) if sense.unit else None
    if current_key is not None and new_key >= current_key:
        return
    sense.unit_id = get_or_create_unit(db, unit_number, hsk_level).id
    db.flush()
    if sense.is_primary:
        refresh_primary_cache(db, sense.vocab)


def fill_sense_pinyin(db: Session, sense: VocabSense, pinyin: str):
    """Fills in a sense's pinyin only if it's currently blank -- never
    overwrites a real reading with a guess."""
    if pinyin and not (sense.pinyin or "").strip():
        sense.pinyin = pinyin
        db.flush()
        if sense.is_primary:
            refresh_primary_cache(db, sense.vocab)


def get_all_taught_words(db: Session, hsk_level: int = 1) -> dict[str, int]:
    """LEGACY: {word: earliest_unit_number} -- collapses a word to its
    single earliest appearance, which is exactly the granularity the sense
    refactor moved away from for gap-detection purposes. Use
    get_uncovered_word_units / get_word_usage_units instead for anything
    that needs to know about EVERY appearance of a word, not just the
    first. Kept for any remaining caller that only needs "earliest unit
    per word" semantics.

    Returns {word: unit_number} for every word that appears in the
    curriculum FOR THIS HSK LEVEL, combining three sources (in unit order,
    so first-appearance unit wins):
    - Vocab rows (word_type != auto)
    - SentenceVocab links (words tagged in sentences)
    - FitbQuestion answers (blanked-out words)

    Scoped to a single hsk_level so repairing HSK1's gaps doesn't also pull
    in HSK2 units that haven't been loaded yet (or vice versa). Returns
    plain unit_number ints -- safe here since every row is already filtered
    to one level, so there's no ambiguity."""
    word_units = {}

    # Source 1: Vocab rows (all sections: vocab, grammar, proper_noun)
    all_vocab = (
        db.query(Vocab.hanzi, Unit.unit_number)
        .join(Unit, Vocab.unit_id == Unit.id)
        .filter(Vocab.word_type != WordType.auto, Unit.hsk_level == hsk_level)
        .all()
    )
    for hanzi, unit_num in all_vocab:
        word_units.setdefault(hanzi, unit_num)

    # Source 2: SentenceVocab links (words in sentences)
    sentence_vocab = (
        db.query(Vocab.hanzi, Unit.unit_number)
        .join(SentenceVocab, SentenceVocab.vocab_id == Vocab.id)
        .join(Sentence, SentenceVocab.sentence_id == Sentence.id)
        .join(Unit, Sentence.unit_id == Unit.id)
        .filter(Unit.hsk_level == hsk_level)
        .all()
    )
    for hanzi, unit_num in sentence_vocab:
        if hanzi not in word_units or unit_num < word_units[hanzi]:
            word_units[hanzi] = unit_num

    # Source 3: FITB answers
    fitb_answers = (
        db.query(FitbQuestion.answer, Unit.unit_number)
        .join(Unit, FitbQuestion.unit_id == Unit.id)
        .filter(Unit.hsk_level == hsk_level)
        .all()
    )
    for answer, unit_num in fitb_answers:
        if answer:
            if answer not in word_units or unit_num < word_units[answer]:
                word_units[answer] = unit_num

    return word_units


def find_example_sentence(db: Session, unit_number: int, word: str, hsk_level: int = 1) -> str | None:
    """Finds an example sentence demonstrating the given word in the given
    (unit_number, hsk_level). Checks (in order of preference):
    1. A sentence whose tags include the word (most direct match)
    2. A FITB question whose answer is the word

    Returns the hanzi sentence text, or None if not found."""
    # Try to find in sentences' tags first
    sentence_with_tag = (
        db.query(Sentence.hanzi)
        .join(Unit, Sentence.unit_id == Unit.id)
        .join(SentenceVocab, SentenceVocab.sentence_id == Sentence.id)
        .join(Vocab, SentenceVocab.vocab_id == Vocab.id)
        .filter(Unit.unit_number == unit_number, Unit.hsk_level == hsk_level, Vocab.hanzi == word)
        .order_by(func.length(Sentence.hanzi))  # shortest first (simpler context)
        .first()
    )
    if sentence_with_tag:
        return sentence_with_tag[0]

    # Fall back to FITB answer match
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


def update_incomplete_sense(db: Session, sense: VocabSense, pinyin: str, english: str) -> bool:
    """Fills in pinyin/english on an existing sense ONLY where it's
    currently blank/placeholder -- never overwrites good data. This is for
    RETRYING a sense already known to be incomplete (see
    get_all_vocab_senses_with_status), not for registering a new meaning --
    use register_word_sense (append_orphan_tags.py) for that, since
    deciding whether a definition is "the same sense, just filled in" vs.
    "actually a different sense" needs the Claude comparison there.
    Returns True if anything changed."""
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