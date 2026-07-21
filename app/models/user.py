from sqlalchemy import Column, Integer, Float, DateTime, UniqueConstraint, String
from sqlalchemy.dialects.sqlite import TEXT
from database import Base
from datetime import datetime


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

    __table_args__ = (
        UniqueConstraint('tag', 'user_id', 'facet', name='_tag_user_facet_uc'),
    )


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    current_unit = Column(Integer, default=1)
    graduated_units = Column(TEXT, default="")


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
    __table_args__ = (UniqueConstraint("user_id", "tag", name="_user_tag_tier_uc"),)

class AcceptedAnswer(Base):
    """Cache of learner answers that Claude judged CORRECT for a chinese->english
    translation, so an identical (expected_answer, cleaned_answer) pair skips the
    AI call next time.
 
    Global (not per-user): whether an English rendering is an acceptable
    translation of an expected answer doesn't depend on who typed it. Keyed on
    the EXPECTED answer + the CLEANED user answer only -- not the question
    sentence -- because the same expected answer accepts the same responses
    regardless of which prompt produced it.
 
    Accepted-only: we never cache rejections, so a cache miss simply falls
    through to the AI (a cached row can only ever let an answer through, never
    block one). Delete rows to invalidate if the AI ever accepted something it
    shouldn't have."""
    __tablename__ = "accepted_answers"
 
    id = Column(Integer, primary_key=True, index=True)
    expected_answer = Column(TEXT, nullable=False)
    cleaned_answer = Column(TEXT, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
 
    __table_args__ = (
        UniqueConstraint('expected_answer', 'cleaned_answer', name='_expected_cleaned_uc'),
    )
 