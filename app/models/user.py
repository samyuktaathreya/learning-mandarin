from sqlalchemy import Column, Integer, Float, DateTime, UniqueConstraint
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
    # Phase within the current unit, gating which activity/tab is available:
    # "listening" -> "character" -> "sentences". A freshly unlocked unit
    # starts at "listening".
    unit_phase = Column(TEXT, default="listening")


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