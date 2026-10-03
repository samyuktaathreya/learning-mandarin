# auth/models.py
from sqlalchemy import Column, Integer, String, Boolean, DateTime
from sqlalchemy.dialects.sqlite import TEXT
from app.core.database import Base

class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)

    # Identity — nullable so guests can still have rows if you want to persist them
    clerk_user_id = Column(String, unique=True, index=True, nullable=True)
    email = Column(String, nullable=True)
    is_guest = Column(Boolean, default=False, nullable=False)
    guest_id = Column(String, unique=True, index=True, nullable=True)  # client-generated UUID for guests

    # Existing learning profile fields
    current_unit = Column(Integer, default=0)
    graduated_units = Column(TEXT, default="")
    hsk_level = Column(Integer, default=1, nullable=False)