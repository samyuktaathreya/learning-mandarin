# session/models.py
from sqlalchemy import Column, Integer, Float, DateTime, UniqueConstraint, String, Text
from sqlalchemy.dialects.sqlite import TEXT
from datetime import datetime

# Assuming Base is defined in your main database.py file
from core.database import Base


class StrengthTable(Base):
    __tablename__ = "strength_table"

    id = Column(Integer, primary_key=True, index=True)
    tag = Column(TEXT, nullable=False)
    user_id = Column(Integer, nullable=False)
    # Which aspect of the word this row tracks: "character" (meaning /
    # recognition) or "pinyin" (sound). A word has one independent strength
    # row per facet, so knowing a word's meaning but not its pronunciation is
    # representable. See QUESTION_TYPE_FACETS in crud.py for which question
    # types update which facet.
    facet = Column(TEXT, nullable=False, default="character")
    correct_count = Column(Integer, default=0)
    times_seen = Column(Integer, default=0)
    stability = Column(Float, default=1.0)
    last_practice = Column(DateTime, default=datetime.utcnow)
    # Recent-struggle signal (Option B). miss_count is net recent struggle: a
    # submit with misses adds them, a clean submit forgives one (floored at 0),
    # so struggle decays through demonstrated success rather than time.
    # attempt_count is total question appearances (for a true miss rate later).
    # Both maintained by crud.update_miss_count from submit_session.
    miss_count = Column(Integer, default=0)
    attempt_count = Column(Integer, default=0)

    __table_args__ = (
        UniqueConstraint('tag', 'user_id', 'facet', name='_tag_user_facet_uc'),
    )


class SoundProgress(Base):
    """Tracks per-user mastery of atomic Mandarin sounds (initials/finals)
    that have no English equivalent -- see GATED_SOUNDS in audio.py. A sound
    is unlocked once successes >= 1 or attempts >= 5 (see crud.get_unlocked_sounds)."""
    __tablename__ = "sound_progress"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, nullable=False)
    sound = Column(TEXT, nullable=False)
    attempts = Column(Integer, default=0)
    successes = Column(Integer, default=0)

    __table_args__ = (
        UniqueConstraint('user_id', 'sound', name='_user_sound_uc'),
    )


class WordTierProgress(Base):
    """Per-user, per-word tier in the current unit's skill progression.
    tier 1..4 (see tier map in session.py). Advances one step when the word
    is answered on a question type belonging to its current tier."""
    __tablename__ = "word_tier_progress"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, nullable=False)
    tag = Column(TEXT, nullable=False)
    tier = Column(Integer, default=1)

    __table_args__ = (
        UniqueConstraint("user_id", "tag", name="_user_tag_tier_uc"),
    )


class SeenQuestion(Base):
    """Per-user exposure count for a specific question ID (not a tag -- a tag
    can have 15-25 question variants, and this is what lets the selector tell
    them apart). Used to make question CHOICE within a (tag, type) prefer
    variety: never-shown variants first, then least-shown, instead of a
    uniform random pick every time.

    Without this, a tag's selection WEIGHT drops fast as its correct_count
    climbs (see MISS_WEIGHT_FACTOR / min_count weighting in session.py), so a
    tag stops being drawn long before every one of its question variants has
    been sampled -- leaving some variants completely unseen until they turn up
    cold on the unit test."""
    __tablename__ = "seen_questions"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, nullable=False)
    question_id = Column(TEXT, nullable=False)
    times_shown = Column(Integer, default=0)
    last_shown = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint('user_id', 'question_id', name='_user_question_uc'),
    )


class AcceptedAnswer(Base):
    """Cache of learner answers that Claude judged CORRECT, so an identical
    (question, cleaned_answer) pair skips the AI call next time.

    Global (not per-user): whether an answer is acceptable for a question
    doesn't depend on who typed it. Keyed on the question + the CLEANED user
    answer so the cache works regardless of which expected_answer reference was
    stored (which may be wrong/mismatched to the question).

    Accepted-only: we never cache rejections, so a cache miss simply falls
    through to the AI (a cached row can only ever let an answer through, never
    block one). Delete rows to invalidate if the AI ever accepted something it
    shouldn't have."""
    __tablename__ = "accepted_answers"

    id = Column(Integer, primary_key=True, index=True)
    question = Column(TEXT, nullable=False)
    cleaned_answer = Column(TEXT, nullable=False)
    expected_answer = Column(TEXT, nullable=True)  # Legacy field, kept for reference only
    created_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint('question', 'cleaned_answer', name='_question_cleaned_uc'),
    )


class FlaggedMismatch(Base):
    """A (question, expected_answer) pair the AI grader flagged as NOT actually
    matching -- e.g. OCR/pipeline produced an 'expected' translation that
    doesn't correspond to the Chinese question shown (see the '六点三十分'
    bug: expected was a whole different sentence's translation).

    This is a detection log, not a fix: grading routes around a flagged pair
    live (grades the learner against the question's real meaning instead of
    the bad expected), and this table lets you batch-reprocess the underlying
    OCR/data pipeline for the affected sentences instead of hand-fixing them
    one at a time as they're discovered.

    Unique on (question, expected_answer) so repeated hits on the same bad
    pair don't spam duplicate rows -- flagged_count tracks how often it
    recurs, which doubles as a rough severity/frequency signal for triage."""
    __tablename__ = "flagged_mismatches"

    id = Column(Integer, primary_key=True, index=True)
    question = Column(TEXT, nullable=False)
    expected_answer = Column(TEXT, nullable=False)
    direction = Column(TEXT, nullable=False)  # "ch->en" or "en->ch"
    reasoning = Column(Text, nullable=True)
    flagged_count = Column(Integer, default=1)
    first_flagged_at = Column(DateTime, default=datetime.utcnow)
    last_flagged_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint('question', 'expected_answer', name='_question_expected_uc'),
    )

class QuestionTip(Base):
    """A learner-authored note attached to a question's TEXT (either its
    'question' string or its 'answer' string), shown to future learners after
    they submit that question. Keyed on (key_type, key_value) so the same tip
    applies everywhere that exact text appears (a question or answer can recur
    across multiple question objects/units).
 
    Global (not per-user) and overwrite-on-resave: re-adding a tip for the same
    (key_type, key_value) replaces the old text rather than erroring or
    duplicating -- it's editing your own note, not adding a second one."""
    __tablename__ = "question_tips"
 
    id = Column(Integer, primary_key=True, index=True)
    key_type = Column(TEXT, nullable=False)     # "question" or "answer"
    key_value = Column(TEXT, nullable=False)    # the exact question/answer text
    tip = Column(TEXT, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
 
    __table_args__ = (
        UniqueConstraint('key_type', 'key_value', name='_tip_keytype_keyvalue_uc'),
    )