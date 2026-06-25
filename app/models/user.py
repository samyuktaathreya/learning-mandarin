from sqlalchemy import Column, Integer, Float, DateTime, UniqueConstraint
from sqlalchemy.dialects.sqlite import TEXT
from database import Base
from datetime import datetime


class StrengthTable(Base):
    __tablename__ = "strength_table"

    id = Column(Integer, primary_key=True, index=True)
    tag = Column(TEXT, nullable=False)
    question_type = Column(TEXT, nullable=False)
    user_id = Column(Integer, nullable=False)
    stability = Column(Float, default=1.0)
    last_practice = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint('tag', 'question_type', 'user_id', name='_tag_qtype_user_uc'),
    )


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    current_unit = Column(Integer, default=1)