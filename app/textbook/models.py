"""
SQL schema for the textbook database.

DATA SOURCES (see ATTRIBUTION.md):
- Vocabulary, HSK levels, pinyin and candidate meanings come from the
  complete-hsk-vocabulary JSON, using its `new-N` levels (the 2021 standard,
  GF0025-2021; `new-7` covers levels 7-9). load_hsk_vocab.py writes one Vocab
  row per listed word. It creates NO senses.
- Sentences and audio come from hsk-sentences-audio (CC BY-SA 4.0), written
  by import_hsa.py with no unit yet.

ORDER OF FILLING (why several unit_id columns are nullable):
  1. load_hsk_vocab  -> Vocab (hsk_level set, unit_id NULL)
  2. import_hsa      -> Sentence (unit_id NULL)
  3. tag_sentences   -> SentenceVocab, VocabSense (unit_id NULL), SenseCache,
                        Sentence.hsk_level
  4. grammar         -> GrammarTip (unit_id NULL), SentenceGrammar
  5. assign_units    -> Unit, then unit_id on Sentence / Vocab / VocabSense /
                        GrammarTip, by first appearance
  6. finalize        -> VocabSense.is_primary, sandhi display pinyin
  7. create_questions-> Question, FitbQuestion
After stage 5 every Sentence has a unit_id: that's how the app picks what to
show in a session. NULL only means "not assigned yet".

Design notes:
- `Vocab` is the single identity row per hanzi string. Proper nouns and
  unlisted words live here too, distinguished by word_type / origin, so
  every SentenceVocab tag always resolves to a Vocab row.
- `Vocab.unit_id` / pinyin / english are a CACHED SNAPSHOT of the word's
  primary sense (the earliest-taught one), kept in sync by db_utils. Before
  any sense exists, pinyin/english hold the JSON's first form / first meaning
  as a display default.
- A word can have several taught meanings (还 "still" vs 还 "to return"),
  each its own VocabSense row with its own unit, created only when a real
  sentence uses that meaning.
- Units are DERIVED data: assign_units.py may delete and rebuild them.
  Deleting a Unit therefore must NOT delete the sentences, words, senses or
  grammar points that point at it, so none of Unit's relationships cascade
  deletes (the old models did, which would wipe the vocab on a re-run).
"""
from sqlalchemy import (
    Column, Integer, String, Text, ForeignKey, UniqueConstraint, Enum,
)
from sqlalchemy.orm import relationship, declarative_base
import enum

Base = declarative_base()


class WordType(str, enum.Enum):
    vocab = "vocab"
    grammar = "grammar"          # particle / aux marker (from the JSON's POS, e.g. 了, 的, 吗)
    proper_noun = "proper_noun"  # names and places, e.g. 美国, 美林
    auto = "auto"                # in a sentence but not in the HSK list (origin = unlisted)


class VocabOrigin(str, enum.Enum):
    """Where a word / sense came from. Set once at creation, never changed."""
    hsk_list = "hsk_list"            # Vocab row from the JSON; or a sense whose meaning
                                     # was copied from the JSON's `meanings`
    ai_added = "ai_added"            # sense written by AI only after it judged that no
                                     # JSON meaning fits -- listed in the run log for review
    proper_noun = "proper_noun"      # name / place; definition "(name) ..." or "(place) ..."
    unlisted = "unlisted"            # in a sentence but absent from the HSK list
    # LEGACY: from the textbook pipeline. Nothing in the new pipeline writes
    # these; delete them once the old scripts that reference them are gone.
    vocab_index = "vocab_index"
    textbook_sentence = "textbook_sentence"


class SentenceSource(str, enum.Enum):
    hsa = "hsa"                  # hsk-sentences-audio
    # LEGACY: from the textbook pipeline; delete once nothing references them.
    textbook = "textbook"
    workbook = "workbook"
    external = "external"


class Unit(Base):
    """One learning unit. Built by assign_units.py from the dataset topics:
    a large topic is split into parts ("Daily Actions 1", "Daily Actions 2"),
    a small one is merged into its neighbour."""
    __tablename__ = "units"

    id = Column(Integer, primary_key=True)
    unit_number = Column(Integer, nullable=False)        # order within the HSK level
    title = Column(Text, nullable=True)                  # e.g. "Daily Actions 2"
    hsk_level = Column(Integer, nullable=False, default=1, server_default="1")
    topic = Column(Text, nullable=True)                  # dataset topic slug, e.g. "daily_actions";
                                                         # for a merged unit, the first topic
    part = Column(Integer, nullable=True)                # 1, 2, ... when a topic is split; NULL if not

    # No delete cascades: units are rebuilt, the content is not (see module docstring).
    vocab = relationship("Vocab", back_populates="unit")
    sentences = relationship("Sentence", back_populates="unit")
    grammar_tips = relationship("GrammarTip", back_populates="unit")

    __table_args__ = (
        UniqueConstraint("unit_number", "hsk_level", name="_unit_number_hsk_level_uc"),
    )


class Vocab(Base):
    """The hanzi IDENTITY row: one per unique string, however many meanings
    it has.

    hsk_level comes straight from the JSON (`new-N`). NULL for proper nouns
    and unlisted words: they don't count toward a sentence's level.

    pinyin / english / unit_id / word_type / origin: a cached snapshot of the
    primary sense, kept in sync by db_utils (see module docstring)."""
    __tablename__ = "vocab"

    id = Column(Integer, primary_key=True)
    hanzi = Column(Text, nullable=False, unique=True, index=True)
    pinyin = Column(Text, nullable=False, default="")
    english = Column(Text, nullable=False, default="")
    word_type = Column(Enum(WordType), nullable=False, default=WordType.vocab)
    unit_id = Column(Integer, ForeignKey("units.id"), nullable=True)  # set by assign_units.py
    origin = Column(Enum(VocabOrigin), nullable=True)

    hsk_level = Column(Integer, nullable=True, index=True)  # 1-7 (7 = levels 7-9); NULL = not in list
    frequency = Column(Integer, nullable=True)              # JSON `frequency` (lower = more common)
    pos = Column(Text, nullable=True)                       # JSON `pos` list as JSON text, e.g. '["n","v"]'

    unit = relationship("Unit", back_populates="vocab")
    sentence_links = relationship("SentenceVocab", back_populates="vocab", cascade="all, delete-orphan")
    senses = relationship("VocabSense", back_populates="vocab", cascade="all, delete-orphan",
                          order_by="VocabSense.id")


class VocabSense(Base):
    """One taught MEANING of a Vocab word, created only when a sentence uses it.

    The meaning normally comes from the JSON: form_index picks the form
    (reading) in the word's `forms` list, meaning_index the entry in that
    form's `meanings`. Both are NULL for ai_added, proper_noun and unlisted
    senses, which have no JSON meaning behind them.

    pinyin is the CITATION reading (e.g. bu4 shi4), as in the JSON. It is the
    matching key (with pos_tag) for SenseCache and is never rewritten.
    pinyin_display is the tone-sandhi form shown to learners (bu2 shi4),
    written by the finalize stage. Keeping them apart means a later run
    (e.g. HSK 2 tagging) still matches against the citation reading.

    unit_id is this sense's home unit: where it is first used, set by
    assign_units.py. is_primary marks the earliest-taught sense (JSON order
    breaks ties), set by the finalize stage; exactly one per vocab_id,
    enforced by db_utils rather than a constraint.

    Uniqueness is on (vocab_id, pinyin, english): one row per distinct
    meaning. The old (vocab_id, unit_id, english) key stopped working once
    senses are created with no unit -- SQLite treats NULLs as distinct, so it
    would no longer block duplicates."""
    __tablename__ = "vocab_senses"

    id = Column(Integer, primary_key=True)
    vocab_id = Column(Integer, ForeignKey("vocab.id"), nullable=False)
    unit_id = Column(Integer, ForeignKey("units.id"), nullable=True)
    pinyin = Column(Text, nullable=False, default="")             # citation tones, matching key
    pinyin_display = Column(Text, nullable=True)                  # tone sandhi, for display
    english = Column(Text, nullable=False, default="")
    word_type = Column(Enum(WordType), nullable=False, default=WordType.vocab)
    is_primary = Column(Integer, nullable=False, default=0, server_default="0")  # SQLite 0/1
    pos_tag = Column(Text, nullable=True)                         # HanLP CTB tag, e.g. "P", "VV"
    origin = Column(Enum(VocabOrigin), nullable=False)
    form_index = Column(Integer, nullable=True)                   # index into the JSON `forms`
    meaning_index = Column(Integer, nullable=True)                # index into that form's `meanings`

    vocab = relationship("Vocab", back_populates="senses")
    unit = relationship("Unit")
    sentence_links = relationship("SentenceVocab", back_populates="sense")
    question_links = relationship("Question", back_populates="sense")

    __table_args__ = (
        UniqueConstraint("vocab_id", "pinyin", "english", name="_vocab_pinyin_english_uc"),
    )


class SenseCache(Base):
    """(hanzi, pos_tag, citation pinyin) -> VocabSense lookup, so
    tag_sentences.py only needs AI the FIRST time it sees a given word / POS
    / reading combination. Fully derivable from VocabSense; kept as its own
    table for a single indexed lookup.

    pos_tag + pinyin is a strong but imperfect proxy for "same sense" (老 as
    "old" vs "always" share both); tag_sentences.py still checks the JSON
    candidates whenever a combination has no entry here."""
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
    """One example sentence.

    unit_id: set by assign_units.py; NULL only between import and unit
    assignment.
    hsk_level: the highest hsk_level among the sentence's words (proper nouns
    and unlisted words don't count), set by tag_sentences.py. The dataset's
    own level label is ignored.
    external_id: the dataset's sentence id (e.g. "hsk1-0002"), used for
    attribution and to find the audio. hanzi is unique: the dataset has a few
    identical sentences under different ids, and import keeps the first.
    pinyin: whole-sentence tone-sandhi pinyin, written by the finalize stage."""
    __tablename__ = "sentences"

    id = Column(Integer, primary_key=True)
    unit_id = Column(Integer, ForeignKey("units.id"), nullable=True)
    hanzi = Column(Text, nullable=False, unique=True)
    english = Column(Text, nullable=False, default="")
    pinyin = Column(Text, nullable=False, default="")
    source = Column(Enum(SentenceSource), nullable=True)

    external_id = Column(Text, nullable=True, unique=True)
    topic = Column(Text, nullable=True, index=True)       # dataset topic, e.g. "food"
    hsk_level = Column(Integer, nullable=True, index=True)
    audio_normal = Column(Text, nullable=True)            # dataset path, e.g. "audio/hsk1-0002.mp3"
    audio_slow = Column(Text, nullable=True)

    unit = relationship("Unit", back_populates="sentences")
    vocab_links = relationship("SentenceVocab", back_populates="sentence",
                               cascade="all, delete-orphan", order_by="SentenceVocab.position")
    fitb_questions = relationship("FitbQuestion", back_populates="sentence", cascade="all, delete-orphan")


class SentenceVocab(Base):
    """One word occurrence in a sentence.

    vocab_sense_id: the meaning used HERE. Set by tag_sentences.py when the
    word has only one meaning for its reading; otherwise NULL until
    resolve_senses.py asks the AI for this one sentence.
    pos_tag: HanLP's CTB tag for this occurrence (P, VV, AS...).
    reading: this occurrence's citation pinyin, compact numeric (gao1xing4),
    from the dataset's per-word pinyin or pypinyin. Picks the word's
    dictionary form(s) when resolving the sense.
    pinyin: this occurrence's display reading WITH context sandhi (一 is yí
    in 一个 but yī on its own), so a clicked word matches what the sentence
    shows. Written by the finalize stage.
    context_definition: the dataset's per-sentence gloss for this word when
    it has one (`tokens[].gloss_en`). NULL = none given."""
    __tablename__ = "sentence_vocab"

    id = Column(Integer, primary_key=True)
    sentence_id = Column(Integer, ForeignKey("sentences.id"), nullable=False)
    vocab_id = Column(Integer, ForeignKey("vocab.id"), nullable=False)
    vocab_sense_id = Column(Integer, ForeignKey("vocab_senses.id"), nullable=True)
    position = Column(Integer, nullable=False, default=0)
    pos_tag = Column(Text, nullable=True)
    reading = Column(Text, nullable=True)
    pinyin = Column(Text, nullable=True)
    context_definition = Column(Text, nullable=True)

    sentence = relationship("Sentence", back_populates="vocab_links")
    vocab = relationship("Vocab", back_populates="sentence_links")
    sense = relationship("VocabSense", back_populates="sentence_links")

    __table_args__ = (
        UniqueConstraint("sentence_id", "position", name="_sentence_position_uc"),
    )


class FitbQuestion(Base):
    """Fill-in-the-blank questions, generated by create_questions.py by
    blanking out a word taught in the sentence's unit."""
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
    """One grammar point from the official HSK grammar list.

    grammar_code: the list's id, e.g. "1-16" (在 as a preposition) or
    "x-erhua"; the idempotency key for re-runs.
    raw_text: the point's `label_full`.
    content_json: the list's whole entry as JSON text (cat, sub, label,
    pattern, examples, ...).
    unit_id: the unit where the point first appears, set by assign_units.py.
    """
    __tablename__ = "grammar_tips"

    id = Column(Integer, primary_key=True)
    unit_id = Column(Integer, ForeignKey("units.id"), nullable=True)
    grammar_code = Column(Text, nullable=False, unique=True)
    hsk_level = Column(Integer, nullable=False, index=True)
    raw_text = Column(Text, nullable=False)
    content_json = Column(Text, nullable=False, default="{}")

    unit = relationship("Unit", back_populates="grammar_tips")
    sentence_links = relationship("SentenceGrammar", back_populates="grammar_tip", cascade="all, delete-orphan")


class SentenceGrammar(Base):
    """Sentence <-> grammar point join table, written by the grammar
    detection stage."""
    __tablename__ = "sentence_grammar"

    sentence_id = Column(Integer, ForeignKey("sentences.id"), primary_key=True)
    grammar_tip_id = Column(Integer, ForeignKey("grammar_tips.id"), primary_key=True)

    sentence = relationship("Sentence", backref="grammar_links")
    grammar_tip = relationship("GrammarTip", back_populates="sentence_links")


class Question(Base):
    """create_questions.py's output.

    vocab_id / vocab_sense_id: set for WORD-level questions (the word and the
    exact sense tested). sentence_id: set for SENTENCE-level questions; the
    words exercised are recovered through Sentence -> SentenceVocab."""
    __tablename__ = "questions"

    id = Column(Integer, primary_key=True)
    legacy_id = Column(Text, nullable=True)
    question_type = Column(Text, nullable=False)
    question = Column(Text, nullable=False)
    answer = Column(Text, nullable=False)
    unit_id = Column(Integer, ForeignKey("units.id"), nullable=False)
    vocab_id = Column(Integer, ForeignKey("vocab.id"), nullable=True)
    vocab_sense_id = Column(Integer, ForeignKey("vocab_senses.id"), nullable=True)
    sentence_id = Column(Integer, ForeignKey("sentences.id"), nullable=True)

    sense = relationship("VocabSense", back_populates="question_links")

    __table_args__ = (
        UniqueConstraint("legacy_id", name="_legacy_id_uc"),
    )


class PinyinSyllable(Base):
    __tablename__ = "pinyin_syllables"
    id = Column(Integer, primary_key=True)
    syllable = Column(String, nullable=False)
    tone = Column(Integer, nullable=False)
    initial_tag = Column(String, nullable=True)
    final_tag = Column(String, nullable=False)
    diacritic_pinyin = Column(String, nullable=True)
    character = Column(String, nullable=True)  # representative hanzi for this syllable+tone, from CC-CEDICT
    __table_args__ = (UniqueConstraint("syllable", "tone", name="_syllable_tone_uc"),)


class PinyinSoundGuide(Base):
    __tablename__ = "pinyin_sound_guides"
    id = Column(Integer, primary_key=True)
    category = Column(String, nullable=False)   # 'initial' | 'final'
    tag = Column(String, nullable=False)        # 'zh', 'ang', ...
    description = Column(Text, nullable=False)
    __table_args__ = (UniqueConstraint("category", "tag", name="_guide_category_tag_uc"),)


class PinyinWord(Base):
    """A two-character Vocab word usable in pinyin tone-pair drills.
    Built by data_pipelines/build_pinyin_words.py; tags are copied from
    pinyin_syllables so words and single syllables are tagged identically."""
    __tablename__ = "pinyin_words"

    id = Column(Integer, primary_key=True)
    vocab_id = Column(Integer, ForeignKey("vocab.id"), nullable=False, unique=True)
    hanzi = Column(Text, nullable=False)
    tone_pair = Column(String, nullable=False, index=True)   # "2-4"

    syllable1_id = Column(Integer, ForeignKey("pinyin_syllables.id"), nullable=False)
    syllable1 = Column(String, nullable=False)
    tone1 = Column(Integer, nullable=False)
    initial1 = Column(String, nullable=True)
    final1 = Column(String, nullable=False)
    sandhi = Column(Integer, nullable=False, default=0, server_default="0")  # 1 = written 3-3, spoken 2-3

    syllable2_id = Column(Integer, ForeignKey("pinyin_syllables.id"), nullable=True)  # NULL for neutral tone
    syllable2 = Column(String, nullable=False)
    tone2 = Column(Integer, nullable=False)
    initial2 = Column(String, nullable=True)
    final2 = Column(String, nullable=False)

    vocab = relationship("Vocab")