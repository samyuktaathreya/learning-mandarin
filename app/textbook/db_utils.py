# textbook/db.py
"""
Engine/session setup for the textbook SQL database, plus the upsert helpers
shared by every pipeline script (vocab_index_parser, sentence_parser,
extract_and_match_grammar, create_questions). Centralizing these here means
each script does its OCR/agent work and then calls these functions instead of
each maintaining its own JSON read/write/merge logic.
"""
from sqlalchemy import create_engine, func
from sqlalchemy.orm import sessionmaker, Session
from contextlib import contextmanager

from models import (
    Base, Unit, Vocab, Sentence, SentenceVocab, WordType,
    GrammarTip, SentenceGrammar, Question, FitbQuestion,
)
import json
from app.core.config.textbook import DATABASE_URL


engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
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

def get_or_create_unit(db: Session, unit_number: int) -> Unit:
    unit = db.query(Unit).filter(Unit.unit_number == unit_number).first()
    if unit is None:
        unit = Unit(unit_number=unit_number)
        db.add(unit)
        db.flush()  # need unit.id before it's referenced by FKs below
    return unit


# --------------------------------- VOCAB ---------------------------------

def upsert_vocab(db: Session, hanzi: str, pinyin: str, english: str,
                  unit_number: int | None, word_type: WordType = WordType.vocab) -> Vocab:
    """
    Insert or update a single vocab row. Mirrors the old process_entries()
    dedup rule: first-seen / LOWEST unit wins if the word already exists with
    a different (non-null) unit. word_type is only ever upgraded from "auto"
    to something more specific, never downgraded -- an unknown-word tag
    later confirmed to be real vocab shouldn't be clobbered by a second
    "auto" hit from another sentence.
    """
    row = db.query(Vocab).filter(Vocab.hanzi == hanzi).first()
    unit = get_or_create_unit(db, unit_number) if unit_number is not None else None

    if row is None:
        row = Vocab(
            hanzi=hanzi,
            pinyin=pinyin,
            english=english,
            word_type=word_type,
            unit_id=unit.id if unit else None,
        )
        db.add(row)
        db.flush()
        return row

    # existing row: apply "lowest unit wins" + never overwrite good data with blanks
    if unit is not None:
        if row.unit is None or unit.unit_number < row.unit.unit_number:
            row.unit_id = unit.id
    if pinyin and not row.pinyin:
        row.pinyin = pinyin
    if english and not row.english:
        row.english = english
    if row.word_type == WordType.auto and word_type != WordType.auto:
        row.word_type = word_type
    db.flush()
    return row


def upsert_vocab_auto(db: Session, hanzi: str, pinyin: str) -> Vocab:
    """Convenience wrapper for sentence_parser's unknown-word fallback tags:
    creates a bare Vocab row (unit=None, word_type=auto) if one doesn't
    already exist, so a tag never dangles without a Vocab row to point at."""
    existing = db.query(Vocab).filter(Vocab.hanzi == hanzi).first()
    if existing:
        return existing
    return upsert_vocab(db, hanzi, pinyin, english="", unit_number=None, word_type=WordType.auto)


def get_word_to_pinyin_map(db: Session) -> dict:
    """Replaces reading word_to_pinyin.json."""
    return {v.hanzi: v.pinyin for v in db.query(Vocab.hanzi, Vocab.pinyin).all()}


def get_word_to_unit_map(db: Session) -> dict:
    """Replaces reading word_to_unit.json. Only includes words that DO have
    a known unit (auto/unknown-unit words are absent, matching how the old
    JSON -- built only from index entries -- never had them either)."""
    rows = (
        db.query(Vocab.hanzi, Unit.unit_number)
        .join(Unit, Vocab.unit_id == Unit.id)
        .all()
    )
    return {hanzi: unit_number for hanzi, unit_number in rows}


# --------------------------------- SENTENCE ---------------------------------

def upsert_sentence(db: Session, unit_number: int, hanzi: str, english: str,
                     pinyin: str, tags: list[str], tag_pinyins: list[str],
                     source: str = None) -> Sentence:
    """
    Insert or update a sentence and its full tag list in one call. Any tag
    not already in `vocab` is created via upsert_vocab_auto so the FK never
    fails -- this is what replaces the old inline `tags: [...]` array.

    Re-running for the same (unit, hanzi) replaces its tag links rather than
    duplicating them, so reprocessing a subset of units (the old
    UNITS_TO_PROCESS override) stays idempotent like the merge-by-unit logic
    in the old run_pipeline().
    """
    unit = get_or_create_unit(db, unit_number)

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
        db.add(SentenceVocab(sentence_id=sentence.id, vocab_id=vocab_row.id, position=position))

    db.flush()
    return sentence


# --------------------------------- GRAMMAR ---------------------------------

def get_or_create_grammar_tip(db: Session, unit_number: int, raw_text: str,
                               content_dict: dict = None) -> GrammarTip:
    """Keyed on (unit, raw_text) -- this is the idempotency point: re-running
    extract_and_match_grammar for a unit doesn't create duplicate tip rows
    or re-spend an API call reformatting text already reformatted. If
    content_dict is given and the row already has content, the existing
    content_json wins (don't clobber a good reformat with a retry's output
    unless the caller explicitly wants to overwrite -- pass overwrite=True)."""
    unit = get_or_create_unit(db, unit_number)
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


def get_sentences_for_unit(db: Session, unit_number: int) -> list[Sentence]:
    return (
        db.query(Sentence)
        .join(Unit, Sentence.unit_id == Unit.id)
        .filter(Unit.unit_number == unit_number)
        .all()
    )


# --------------------------------- QUESTIONS ---------------------------------

def upsert_question(db: Session, unit_number: int, question_type: str, question_text: str,
                     answer_text: str, vocab_id: int = None, sentence_id: int = None,
                     legacy_id: str = None) -> Question:
    """Keyed on (question_type, question, answer) within a unit -- mirrors
    the old create_questions.py signature-based merge (`sig = (question_type,
    question, answer)`), which is what let reruns preserve IDs/tags instead
    of duplicating. legacy_id lets you carry over an old "u3_speaking_vocab_2"
    style ID if something else in the app still references it directly.

    Pass vocab_id for word-level questions, sentence_id for sentence-level
    questions -- see Question model docstring for why sentence questions
    need this (recovering their full multi-word tag list at read time)."""
    unit = get_or_create_unit(db, unit_number)
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
            sentence_id=sentence_id,
            legacy_id=legacy_id,
        )
        db.add(row)
        db.flush()
    else:
        if vocab_id is not None and row.vocab_id is None:
            row.vocab_id = vocab_id
        if sentence_id is not None and row.sentence_id is None:
            row.sentence_id = sentence_id
        db.flush()
    return row


def get_vocab_for_unit(db: Session, unit_number: int, word_types: list[WordType] = None) -> list[Vocab]:
    q = (
        db.query(Vocab)
        .join(Unit, Vocab.unit_id == Unit.id)
        .filter(Unit.unit_number == unit_number)
    )
    if word_types:
        q = q.filter(Vocab.word_type.in_(word_types))
    return q.all()


def get_all_vocab_hanzi(db: Session) -> list[str]:
    """All known hanzi, longest-first -- used for greedy substring matching
    when figuring out which taught words make up a COMPOUND vocab entry
    (e.g. does the vocab word itself contain other known sub-words). This is
    the DB equivalent of the old `all_hanzi` list built from
    index_output.json's vocab+grammar+proper_nouns."""
    rows = db.query(Vocab.hanzi).all()
    hanzi = [r.hanzi for r in rows]
    hanzi.sort(key=len, reverse=True)
    return hanzi


def rehome_sentences(db: Session) -> dict:
    """
    For every sentence, its true earliest-possible unit is the LATEST home
    unit among its own tags (a sentence can't be practiced before every word
    in it has been taught) -- NOT necessarily the unit it was physically
    extracted from (textbook sentences are ungated and can legitimately use
    vocab from later in the same unit, or even, after a correction to an
    earlier vocab entry, end up using only already-known words).

    If a sentence's computed home unit is EARLIER than the unit it's
    currently filed under, move it there (update Sentence.unit_id) so it's
    available for practice as soon as legitimately possible. If an identical
    sentence already exists at the target unit, drop this one as a
    duplicate rather than violate the (unit_id, hanzi) uniqueness constraint
    -- same "moved / deleted" bookkeeping the old JSON-based
    rehome_sentences() did, just against rows instead of dict keys.

    Tags with no known home unit (auto/unknown-unit words) don't constrain
    the target -- same as the old code's `home_unit.get(tag, unit)` fallback
    to the sentence's current unit.
    """
    home_unit = get_word_to_unit_map(db)
    moved, deleted = 0, 0

    all_sentences = db.query(Sentence).join(Unit, Sentence.unit_id == Unit.id).all()
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
        target = max((home_unit.get(t, current_unit) for t in tags), default=current_unit)
        if target >= current_unit:
            continue

        dup_key = (target, sentence.hanzi)
        if dup_key in by_unit_hanzi and by_unit_hanzi[dup_key].id != sentence.id:
            db.delete(sentence)
            deleted += 1
            continue

        target_unit_row = get_or_create_unit(db, target)
        old_key = (current_unit, sentence.hanzi)
        by_unit_hanzi.pop(old_key, None)
        sentence.unit_id = target_unit_row.id
        by_unit_hanzi[dup_key] = sentence
        moved += 1

    db.flush()
    print(f"  [rehome] moved {moved} sentence(s) to an earlier unit, deleted {deleted} duplicate(s)")
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


# --------------------------------- SYNC HELPERS (for sync_index_definitions.py) ---------------------------------

def get_all_vocab_with_status(db: Session) -> tuple[dict[str, Vocab], set[str]]:
    """Returns (vocab_map, needs_retry_set) where:
    - vocab_map is {hanzi: Vocab row} for all vocab in the DB
    - needs_retry_set is {hanzi} for any word with UNKNOWN_PINYIN/UNKNOWN_ENGLISH
      placeholder text, OR a genuinely blank pinyin/english field.

    BUGFIX: originally only checked for the literal placeholder strings
    "UNKNOWN_PINYIN"/"UNKNOWN_ENGLISH". Words auto-created during sentence
    tagging (word_type="auto") get real pinyin but an EMPTY english field
    ("" or None) -- not the placeholder text -- so they silently passed this
    check and were never queued for repair by sync_index_definitions.py.
    Blank/None now counts as needing retry too.

    Used by sync_index_definitions.py to find gaps."""
    all_vocab = db.query(Vocab).all()
    vocab_map = {v.hanzi: v for v in all_vocab}

    def _is_incomplete(v: Vocab) -> bool:
        pinyin = v.pinyin or ""
        english = v.english or ""
        return (
            "UNKNOWN_PINYIN" in pinyin
            or "UNKNOWN_ENGLISH" in english
            or not pinyin.strip()
            or not english.strip()
        )

    needs_retry = {v.hanzi for v in all_vocab if _is_incomplete(v)}
    return vocab_map, needs_retry


def get_all_taught_words(db: Session) -> dict[str, int]:
    """Returns {word: unit_number} for every word that appears in the curriculum,
    combining three sources (in unit order, so first-appearance unit wins):
    - Vocab rows (word_type != auto)
    - SentenceVocab links (words tagged in sentences)
    - FitbQuestion answers (blanked-out words)
    
    Replaces walking unit_vocab_tags.json + units_output.json manually."""
    word_units = {}
    
    # Source 1: Vocab rows (all sections: vocab, grammar, proper_noun)
    all_vocab = (
        db.query(Vocab.hanzi, Unit.unit_number)
        .join(Unit, Vocab.unit_id == Unit.id)
        .filter(Vocab.word_type != WordType.auto)
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
        .all()
    )
    for hanzi, unit_num in sentence_vocab:
        if hanzi not in word_units or unit_num < word_units[hanzi]:
            word_units[hanzi] = unit_num
    
    # Source 3: FITB answers
    fitb_answers = (
        db.query(FitbQuestion.answer, Unit.unit_number)
        .join(Unit, FitbQuestion.unit_id == Unit.id)
        .all()
    )
    for answer, unit_num in fitb_answers:
        if answer:
            if answer not in word_units or unit_num < word_units[answer]:
                word_units[answer] = unit_num
    
    return word_units


def find_example_sentence(db: Session, unit_number: int, word: str) -> str | None:
    """Finds an example sentence demonstrating the given word in the given unit.
    Checks (in order of preference):
    1. A sentence whose tags include the word (most direct match)
    2. A FITB question whose answer is the word
    
    Returns the hanzi sentence text, or None if not found."""
    # Try to find in sentences' tags first
    sentence_with_tag = (
        db.query(Sentence.hanzi)
        .join(Unit, Sentence.unit_id == Unit.id)
        .join(SentenceVocab, SentenceVocab.sentence_id == Sentence.id)
        .join(Vocab, SentenceVocab.vocab_id == Vocab.id)
        .filter(Unit.unit_number == unit_number, Vocab.hanzi == word)
        .order_by(func.length(Sentence.hanzi))  # shortest first (simpler context)
        .first()
    )
    if sentence_with_tag:
        return sentence_with_tag[0]
    
    # Fall back to FITB answer match
    fitb_sentence = (
        db.query(FitbQuestion.full_sentence)
        .join(Unit, FitbQuestion.unit_id == Unit.id)
        .filter(Unit.unit_number == unit_number, FitbQuestion.answer == word)
        .order_by(func.length(FitbQuestion.full_sentence))
        .first()
    )
    if fitb_sentence:
        return fitb_sentence[0]
    
    return None


def update_vocab_entry(db: Session, hanzi: str, pinyin: str, english: str, unit_number: int) -> bool:
    """Updates an existing vocab entry OR creates one if missing.
    Returns True if an update/insert happened."""
    vocab = db.query(Vocab).filter(Vocab.hanzi == hanzi).first()
    if vocab is None:
        unit = get_or_create_unit(db, unit_number)
        vocab = Vocab(hanzi=hanzi, pinyin=pinyin, english=english, unit_id=unit.id, word_type=WordType.vocab)
        db.add(vocab)
        db.flush()
        return True
    
    # Only update if we're improving incomplete data
    if (not vocab.pinyin or "UNKNOWN_PINYIN" in vocab.pinyin) and pinyin and "UNKNOWN_PINYIN" not in pinyin:
        vocab.pinyin = pinyin
    if (not vocab.english or "UNKNOWN_ENGLISH" in vocab.english) and english and "UNKNOWN_ENGLISH" not in english:
        vocab.english = english
    if vocab.unit_id is None:
        vocab.unit_id = get_or_create_unit(db, unit_number).id
    
    db.flush()
    return True