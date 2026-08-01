from sqlalchemy import Column, Integer, Float, DateTime, UniqueConstraint, String, Text
from sqlalchemy.dialects.sqlite import TEXT
from core.database import Base
from datetime import datetime

class DictionaryEntry(Base):
    __tablename__ = "dictionary_entries"

    id = Column(Integer, primary_key=True, index=True)
    traditional = Column(String, index=True)
    simplified = Column(String, index=True) # Indexed for fast lookups
    pinyin = Column(String)
    english = Column(Text) # Stores definition string/JSON