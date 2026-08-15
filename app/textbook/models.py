# textbook/models.py
"""
SQL schema for the textbook database (replaces units_output.json,
unit_vocabs_tag.json, index_output.json, and the "tags" arrays that used
to live inline in units_output.json's sentence records).

Design notes:
- `Vocab` is the single source of truth for every hanzi word: vocab proper,
  grammar-classified index entries (particles/aux markers), and proper nouns
  all live here with a `word_type` discriminator, because vocab_index_parser's
  downstream consumers (word_to_pinyin / word_to_unit / the tagger's "known
  words" list) never cared about that distinction -- they just needed
  hanzi -> pinyin/unit for ANY taught token. Splitting them into separate
  tables would just re-introduce a 3-way UNION every time something needs
  "all known tokens up to unit N".
- `unit` on Vocab is now just a CACHE of the primary sense's home unit --
  see VocabSense for the real per-meaning home units. A word can be
  genuinely retaught with a different meaning in a later unit/hsk_level
  (e.g. 还 "still" in HSK1 vs. 还 "to return (something)" in HSK3); each
  such meaning gets its own VocabSense row instead of being collapsed by
  the old "lowest unit wins" rule, which silently discarded every meaning
  but the first one ever seen.
- `Sentence.tags` from the old JSON becomes the `sentence_vocab` join table.
  Every tag must resolve to a `Vocab.hanzi` row -- including the
  "unknown word" fallback tags that sentence_parser's greedy_segment /
  allow_unknown path used to emit for textbook sentences. Those get upserted
  into `Vocab` too, marked word_type="auto", unit=None (unit unknown/not
  from the printed index), so the FK is never violated and tag strength
  tracking in StrengthTable keeps working with zero special-casing.
"""
from sqlalchemy import (
    Column, Integer, Float, String, Text, ForeignKey, UniqueConstraint,
    DateTime, Enum, func,
)
from sqlalchemy.orm import relationship, declarative_base
import enum
import datetime

Base = declarative_base()


class WordType(str, enum.Enum):
    vocab = "vocab"
    grammar = "grammar"          # particle/aux-marker POS classification from the index
    proper_noun = "proper_noun"
    auto = "auto"                # unknown-word fallback, tagged but never in the printed index


class Unit(Base):
    __tablename__ = "units"

    id = Column(Integer, primary_key=True)
    unit_number = Column(Integer, nullable=False)
    title = Column(Text, nullable=True)
    hsk_level = Column(Integer, nullable=False, default=1, server_default="1")

    vocab = relationship("Vocab", back_populates="unit", cascade="all, delete-orphan")
    sentences = relationship("Sentence", back_populates="unit", cascade="all, delete-orphan")
    grammar_tips = relationship("GrammarTip", back_populates="unit", cascade="all, delete-orphan")

    __table_args__ = (
        UniqueConstraint("unit_number", "hsk_level", name="_unit_number_hsk_level_uc"),
    )
    

class Vocab(Base):
    """The hanzi IDENTITY row -- one per unique string, e.g. one row for
    "还" no matter how many different meanings it's taught with.

    pinyin/english/unit_id/word_type here are now a CACHED SNAPSHOT of this
    word's primary sense (see VocabSense.is_primary), kept in sync by
    db_utils whenever the primary sense changes. They exist so code that
    hasn't been migrated to be sense-aware yet (old queries, the app layer)
    keeps returning a reasonable single definition instead of breaking.
    New code should prefer VocabSense rows via `senses` / db_utils sense
    helpers -- a word can have several taught meanings, each introduced in
    its own unit, and Vocab alone can no longer represent that."""
    __tablename__ = "vocab"

    id = Column(Integer, primary_key=True)
    hanzi = Column(Text, nullable=False, unique=True, index=True)
    pinyin = Column(Text, nullable=False, default="")
    english = Column(Text, nullable=False, default="")
    word_type = Column(Enum(WordType), nullable=False, default=WordType.vocab)
    unit_id = Column(Integer, ForeignKey("units.id"), nullable=True)  # nullable: "auto" words may have no known unit

    unit = relationship("Unit", back_populates="vocab")
    sentence_links = relationship("SentenceVocab", back_populates="vocab", cascade="all, delete-orphan")
    senses = relationship("VocabSense", back_populates="vocab", cascade="all, delete-orphan",
                           order_by="VocabSense.id")


class VocabSense(Base):
    """One taught MEANING of a Vocab hanzi. A hanzi can carry several senses
    across the curriculum -- e.g. 还 as "still/also" (introduced HSK1) vs.
    "to return (something)" (introduced HSK3) -- and each gets its own row
    here instead of being collapsed into a single (pinyin, english,
    unit_id) the way Vocab alone used to force.

    unit_id is this SENSE's home unit (first taught here) -- nullable only
    for senses discovered by append_orphan_tags.py before a confident home
    unit is resolved.

    is_primary marks the sense used as the word's default/fallback
    definition wherever no sentence context is available (flashcard review,
    any reader not yet updated to be sense-aware). Exactly one sense per
    vocab_id should be primary at a time -- enforced by db_utils (which
    clears the old primary before setting a new one), not a DB constraint.

    Uniqueness is on (vocab_id, unit_id, english) rather than just
    (vocab_id, unit_id): a unit can legitimately re-teach a word with the
    SAME meaning it already had (plain review) without that becoming a
    second sense -- only a genuinely different `english` for that
    (word, unit) pair creates a new row."""
    __tablename__ = "vocab_senses"

    id = Column(Integer, primary_key=True)
    vocab_id = Column(Integer, ForeignKey("vocab.id"), nullable=False)
    unit_id = Column(Integer, ForeignKey("units.id"), nullable=True)
    pinyin = Column(Text, nullable=False, default="")
    english = Column(Text, nullable=False, default="")
    word_type = Column(Enum(WordType), nullable=False, default=WordType.vocab)
    is_primary = Column(Integer, nullable=False, default=0, server_default="0")  # SQLite: 0/1 in place of Boolean
    # HanLP's POS tag for this specific sense, e.g. "n" (noun), "v" (verb).
    # Nullable for senses created before tagging existed (index-parser-only
    # senses, pre-HanLP migration rows). Combined with `pinyin`, this is the
    # SenseCache lookup key -- see SenseCache below for why.
    pos_tag = Column(Text, nullable=True)

    vocab = relationship("Vocab", back_populates="senses")
    unit = relationship("Unit")
    sentence_links = relationship("SentenceVocab", back_populates="sense")
    question_links = relationship("Question", back_populates="sense")

    __table_args__ = (
        UniqueConstraint("vocab_id", "unit_id", "english", name="_vocab_unit_english_uc"),
    )


class SenseCache(Base):
    """Deterministic (hanzi, pos_tag, pinyin_reading) -> VocabSense lookup,
    so tag_sentences.py doesn't have to make an AI call every time it sees
    a word it's already resolved before -- only the FIRST time a given
    (word, POS, reading) combination is encountered does it need Haiku (new
    word) or a same/different-sense comparison call (word exists, but this
    exact POS+reading combo hasn't been seen yet). Every subsequent sentence
    using the same word with the same POS+reading hits this table instead
    and costs nothing.

    This is a cache in the sense that it's fully derivable from VocabSense
    (pos_tag, pinyin) -- but keeping it as its own table with a clean unique
    index makes the lookup a single indexed query instead of a scan-and-
    compare over every sense of a word, and gives tag_sentences.py one place
    to write to that doesn't also need to touch VocabSense.is_primary /
    unit rehoming logic.

    NOTE: pos_tag+pinyin is a strong but not perfect proxy for "same sense"
    -- two genuinely different meanings CAN share both (rare, e.g. 老 as
    adjective "old" vs adjective "always/constantly", both lao3). This
    table only shortcuts the COMMON case; tag_sentences.py still falls
    through to an AI comparison whenever a word's existing senses don't
    already have a cache entry for the exact (pos_tag, pinyin) it just
    encountered.
    """
    __tablename__ = "sense_cache"

    id = Column(Integer, primary_key=True)
    hanzi = Column(Text, nullable=False)
    pos_tag = Column(Text, nullable=False)
    pinyin = Column(Text, nullable=False)
    vocab_sense_id = Column(Integer, ForeignKey("vocab_senses.id"), nullable=False)

    sense = relationship("VocabSense")

    __table_args__ = (
        UniqueConstraint("hanzi", "pos_tag", "pinyin", name="_hanzi_pos_pinyin_uc"),
    )


class Sentence(Base):
    __tablename__ = "sentences"

    id = Column(Integer, primary_key=True)
    unit_id = Column(Integer, ForeignKey("units.id"), nullable=False)
    hanzi = Column(Text, nullable=False)
    english = Column(Text, nullable=False, default="")
    pinyin = Column(Text, nullable=False, default="")
    source = Column(Text, nullable=True)  # "textbook" | "workbook"

    unit = relationship("Unit", back_populates="sentences")
    vocab_links = relationship("SentenceVocab", back_populates="sentence",
                                cascade="all, delete-orphan", order_by="SentenceVocab.position")
    fitb_questions = relationship("FitbQuestion", back_populates="sentence", cascade="all, delete-orphan")

    __table_args__ = (
        UniqueConstraint("unit_id", "hanzi", name="_unit_hanzi_uc"),
    )


class SentenceVocab(Base):
    __tablename__ = "sentence_vocab"

    id = Column(Integer, primary_key=True)
    sentence_id = Column(Integer, ForeignKey("sentences.id"), nullable=False)
    vocab_id = Column(Integer, ForeignKey("vocab.id"), nullable=False)
    # Which taught MEANING this occurrence demonstrates -- resolved at write
    # time (see db_utils.resolve_sense_for_sentence) by picking the sense of
    # `vocab_id` whose home unit is the latest one already <= this sentence's
    # own unit. Nullable for legacy rows written before senses existed, and
    # for "auto" words that were never given a real sense.
    vocab_sense_id = Column(Integer, ForeignKey("vocab_senses.id"), nullable=True)
    position = Column(Integer, nullable=False, default=0)
    # Fine-grained override for the RARE case where even the resolved
    # sense's wording doesn't fit this specific sentence (e.g. a sense's
    # definition is technically right but awkwardly phrased for this
    # context). This is no longer the primary mechanism for "different unit,
    # different meaning" -- that's now VocabSense's job. NULL = not yet
    # checked. "" = checked, the resolved sense's english is fine as-is.
    # non-empty = a corrected wording for THIS occurrence only.
    context_definition = Column(Text, nullable=True)

    sentence = relationship("Sentence", back_populates="vocab_links")
    vocab = relationship("Vocab", back_populates="sentence_links")
    sense = relationship("VocabSense", back_populates="sentence_links")

    __table_args__ = (
        UniqueConstraint("sentence_id", "position", name="_sentence_position_uc"),
    )


class FitbQuestion(Base):
    """Fill-in-the-blank questions, previously the `fill_in_the_blank` array
    inside each unit's units_output.json entry."""
    __tablename__ = "fitb_questions"

    id = Column(Integer, primary_key=True)
    sentence_id = Column(Integer, ForeignKey("sentences.id"), nullable=True)
    unit_id = Column(Integer, ForeignKey("units.id"), nullable=False)
    question = Column(Text, nullable=False)
    answer = Column(Text, nullable=False)
    full_sentence = Column(Text, nullable=False, default="")

    sentence = relationship("Sentence", back_populates="fitb_questions")

    __table_args__ = (
        UniqueConstraint("unit_id", "question", "answer", name="_unit_q_a_uc"),
    )


class GrammarTip(Base):
    """
    One row per raw grammar-tip block extracted from a unit's Notes section.
    Many-to-many with Sentence via SentenceGrammar (unchanged from the old
    JSON version's semantics: a sentence can demonstrate several tips, and
    the same tip -- e.g. "measure words", "了 for change of state" -- can be
    the reason several different sentences were selected).

    raw_text is the original scraped tip (used as the dedup/idempotency key
    within a unit: re-running the pipeline for a unit shouldn't create a
    second row for the same tip, and shouldn't re-call the reformatting
    agent for text it's already reformatted).

    content_json holds the full reformatted {"sections": [{"title","body",
    "table"}, ...]} structure produced by reformat_grammar_tip_text, stored
    verbatim as JSON text -- it's inherently a small nested document, not
    something with a fixed relational shape (sections vary in count, and a
    section's table is optional and variable-width), so JSON-in-a-column is
    the right call here rather than modeling tables-within-tables in SQL.
    """
    __tablename__ = "grammar_tips"

    id = Column(Integer, primary_key=True)
    unit_id = Column(Integer, ForeignKey("units.id"), nullable=False)
    raw_text = Column(Text, nullable=False)
    content_json = Column(Text, nullable=False, default="{}")

    unit = relationship("Unit", back_populates="grammar_tips")
    sentence_links = relationship("SentenceGrammar", back_populates="grammar_tip", cascade="all, delete-orphan")

    __table_args__ = (
        UniqueConstraint("unit_id", "raw_text", name="_unit_raw_tip_uc"),
    )


class SentenceGrammar(Base):
    """The sentence <-> grammar-tip join table. Many-to-many both ways:
    one sentence can link to several tips, one tip can link to several
    sentences -- exactly the same relationship shape as SentenceVocab."""
    __tablename__ = "sentence_grammar"

    sentence_id = Column(Integer, ForeignKey("sentences.id"), primary_key=True)
    grammar_tip_id = Column(Integer, ForeignKey("grammar_tips.id"), primary_key=True)

    sentence = relationship("Sentence", backref="grammar_links")
    grammar_tip = relationship("GrammarTip", back_populates="sentence_links")


class Question(Base):
    """create_questions.py's output -- unit_questions_hsk1.json equivalent.

    vocab_id: set for WORD-level questions (listening vocab, translate word,
    etc.) -- the single word being tested.
    sentence_id: set for SENTENCE-level questions (listening sentence,
    translate sentence, etc.) -- lets us recover the FULL list of vocab tags
    a sentence question exercises (via Sentence -> SentenceVocab), which the
    old JSON's per-question `tags: [...]` array used to carry directly. A
    sentence question is "about" every word in that sentence, not just one,
    so this can't be collapsed into vocab_id the way word questions can.
    FITB questions may have neither (best-effort match to a sentence at
    creation time; nullable if unmatched).
    """
    __tablename__ = "questions"

    id = Column(Integer, primary_key=True)
    legacy_id = Column(Text, nullable=True)
    question_type = Column(Text, nullable=False)
    question = Column(Text, nullable=False)
    answer = Column(Text, nullable=False)
    unit_id = Column(Integer, ForeignKey("units.id"), nullable=False)
    vocab_id = Column(Integer, ForeignKey("vocab.id"), nullable=True)
    # Which specific sense this word-question tested, e.g. so a wrong-answer
    # review screen can show the exact definition the question was built
    # from rather than falling back to Vocab's (possibly different-sense)
    # cached snapshot. Nullable: sentence-level questions don't test one
    # word/sense, and legacy rows predate this column.
    vocab_sense_id = Column(Integer, ForeignKey("vocab_senses.id"), nullable=True)
    sentence_id = Column(Integer, ForeignKey("sentences.id"), nullable=True)

    sense = relationship("VocabSense", back_populates="question_links")

    __table_args__ = (
        UniqueConstraint("legacy_id", name="_legacy_id_uc"),
    )