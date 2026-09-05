# auth/models.py
from sqlalchemy import Column, Integer, String, Boolean, DateTime
from sqlalchemy.dialects.sqlite import TEXT
from app.core.database import Base

class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    
    # Existing learning profile fields
    current_unit = Column(Integer, default=1)
    graduated_units = Column(TEXT, default="")
    hsk_level = Column(Integer, default=1, nullable=False)