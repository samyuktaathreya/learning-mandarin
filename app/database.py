from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
import json

SQLALCHEMY_DATABASE_URL = "sqlite:///./mandarin_app.db"

engine = create_engine(SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# ----------------------------- LOAD QUESTION BANK -----------------------------

QUESTIONS_FILEPATH = './language-app-data/unit_questions_hsk1.json'

try:
    with open(QUESTIONS_FILEPATH, 'r', encoding='utf-8') as f:
        unit_questions = json.load(f)  # { "1": [...], "2": [...], ... }
    print(f"Questions loaded successfully! ({len(unit_questions)} units)")
except FileNotFoundError:
    print(f"Error: {QUESTIONS_FILEPATH} not found.")
    unit_questions = {}
except json.JSONDecodeError:
    print("Error: Failed to decode unit_questions_hsk1.json.")
    unit_questions = {}

# ----------------------------- BUILD IN-MEMORY DICTS -----------------------------

inverted_index = {}     # tag -> [question, ...]
tags_to_unit_dict = {}  # tag -> unit (int)
unit_to_tags_dict = {}  # unit (int) -> set of tags

for unit_str, questions in unit_questions.items():
    unit = int(unit_str)
    unit_to_tags_dict[unit] = set()

    for q in questions:
        for tag in q.get("tags", []):
            # tags_to_unit_dict: tag -> unit
            if tag not in tags_to_unit_dict:
                tags_to_unit_dict[tag] = unit

            # unit_to_tags_dict: unit -> tags
            unit_to_tags_dict[unit].add(tag)

            # inverted_index: tag -> list of questions
            if tag not in inverted_index:
                inverted_index[tag] = []
            inverted_index[tag].append({**q, "unit": unit})

print(f"Built inverted_index with {len(inverted_index)} tags.")
print(f"Built tags_to_unit_dict with {len(tags_to_unit_dict)} tags.")
print(f"Built unit_to_tags_dict with {len(unit_to_tags_dict)} from sqlalchemy import create_engine, Column, Integer, Text, Float, DateTime
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from datetime import datetime
import json

SQLALCHEMY_DATABASE_URL = "sqlite:///./mandarin_app.db"

engine = create_engine(SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# ----------------------------- SQL MODELS -----------------------------

class StrengthTable(Base):
    __tablename__ = "strength_table"

    id = Column(Integer, primary_key=True, autoincrement=True)
    tag = Column(Text, nullable=False)
    user_id = Column(Integer, nullable=False)
    stability = Column(Float, default=1.0)
    last_practice = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        __import__('sqlalchemy').UniqueConstraint('tag', 'user_id'),
    )


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, autoincrement=True)
    current_unit = Column(Integer, default=1)


# ----------------------------- LOAD QUESTION BANK -----------------------------

QUESTIONS_FILEPATH = './language-app-data/unit_questions_hsk1.json'

try:
    with open(QUESTIONS_FILEPATH, 'r', encoding='utf-8') as f:
        unit_questions = json.load(f)  # { "1": [...], "2": [...], ... }
    print(f"Questions loaded! ({len(unit_questions)} units)")
except FileNotFoundError:
    print(f"Error: {QUESTIONS_FILEPATH} not found.")
    unit_questions = {}
except json.JSONDecodeError:
    print("Error: Failed to decode unit_questions_hsk1.json.")
    unit_questions = {}

# ----------------------------- BUILD IN-MEMORY DICTS -----------------------------

inverted_index = {}     # tag -> [question, ...]
tags_to_unit_dict = {}  # tag -> unit (int)
unit_to_tags_dict = {}  # unit (int) -> set of tags
unique_tags = set()

for unit_str, questions in unit_questions.items():
    unit = int(unit_str)
    unit_to_tags_dict[unit] = set()

    for q in questions:
        for tag in q.get("tags", []):
            unique_tags.add(tag)

            if tag not in tags_to_unit_dict:
                tags_to_unit_dict[tag] = unit

            unit_to_tags_dict[unit].add(tag)

            if tag not in inverted_index:
                inverted_index[tag] = []
            inverted_index[tag].append({**q, "unit": unit})

print(f"Built inverted_index: {len(inverted_index)} tags")
print(f"Built tags_to_unit_dict: {len(tags_to_unit_dict)} tags")
print(f"Built unit_to_tags_dict: {len(unit_to_tags_dict)} units")
print(f"Total unique tags: {len(unique_tags)}")

# ----------------------------- INIT SQL TABLES + SEED -----------------------------

def init_db():
    # Creates tables if they don't exist
    Base.metadata.create_all(bind=engine)

    db = SessionLocal()
    try:
        # Seed default user if not present
        if not db.query(User).filter(User.id == 1).first():
            db.add(User(id=1, current_unit=1))
            print("Default user created.")

        # Seed strength table with all unique tags for user 1
        existing_tags = {row.tag for row in db.query(StrengthTable.tag).filter(StrengthTable.user_id == 1).all()}
        new_tags = unique_tags - existing_tags

        for tag in new_tags:
            db.add(StrengthTable(
                tag=tag,
                user_id=1,
                stability=1.0,
                last_practice=datetime.utcnow()
            ))

        db.commit()
        print(f"Strength table seeded: {len(new_tags)} new tags added.")

    finally:
        db.close()units.")