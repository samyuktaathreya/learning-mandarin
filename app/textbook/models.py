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
- `unit` on Vocab is the unit the word is FIRST introduced in (lowest-unit-wins,
  same dedup rule as the old process_entries()).
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
    unit_number = Column(Integer, nullable=False, unique=True)
    title = Column(Text, nullable=True)

    vocab = relationship("Vocab", back_populates="unit", cascade="all, delete-orphan")
    sentences = relationship("Sentence", back_populates="unit", cascade="all, delete-orphan")
    grammar_tips = relationship("GrammarTip", back_populates="unit", cascade="all, delete-orphan")


class Vocab(Base):
    __tablename__ = "vocab"

    id = Column(Integer, primary_key=True)
    hanzi = Column(Text, nullable=False, unique=True, index=True)
    pinyin = Column(Text, nullable=False, default="")
    english = Column(Text, nullable=False, default="")
    word_type = Column(Enum(WordType), nullable=False, default=WordType.vocab)
    unit_id = Column(Integer, ForeignKey("units.id"), nullable=True)  # nullable: "auto" words may have no known unit

    unit = relationship("Unit", back_populates="vocab")
    sentence_links = relationship("SentenceVocab", back_populates="vocab", cascade="all, delete-orphan")


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

    id = Column(Integer, primary_key=True)          # surrogate PK
    sentence_id = Column(Integer, ForeignKey("sentences.id"), nullable=False)
    vocab_id = Column(Integer, ForeignKey("vocab.id"), nullable=False)
    position = Column(Integer, nullable=False, default=0)

    sentence = relationship("Sentence", back_populates="vocab_links")
    vocab = relationship("Vocab", back_populates="sentence_links")

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
    """create_questions.py's output -- unit_questions_hsk1.json equivalent."""
    __tablename__ = "questions"

    id = Column(Integer, primary_key=True)
    legacy_id = Column(Text, nullable=True)  # e.g. "u3_speaking_vocab_2", kept for backward compat if referenced elsewhere
    question_type = Column(Text, nullable=False)
    question = Column(Text, nullable=False)
    answer = Column(Text, nullable=False)
    unit_id = Column(Integer, ForeignKey("units.id"), nullable=False)
    vocab_id = Column(Integer, ForeignKey("vocab.id"), nullable=True)  # the word being tested, if applicable

    __table_args__ = (
        UniqueConstraint("legacy_id", name="_legacy_id_uc"),
    )