from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
import json
from datetime import datetime, timedelta

SQLALCHEMY_DATABASE_URL = "sqlite:///./mandarin_app.db"

engine = create_engine(SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# NOTE: models are imported in main.py so Base.metadata knows about them
# before create_all() is called. Don't import them here (circular import).

# ----------------------------- LOAD QUESTION BANK -----------------------------

QUESTIONS_FILEPATH = './language-app-data/data/clean/unit_questions_hsk1.json'

try:
    with open(QUESTIONS_FILEPATH, 'r', encoding='utf-8') as f:
        unit_questions = json.load(f)
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

            # store the lowest unit this tag appears in
            if tag not in tags_to_unit_dict or unit < tags_to_unit_dict[tag]:
                tags_to_unit_dict[tag] = unit

            unit_to_tags_dict[unit].add(tag)

            if tag not in inverted_index:
                inverted_index[tag] = []
            inverted_index[tag].append({**q, "unit": unit})

print(f"Built inverted_index: {len(inverted_index)} tags")
print(f"Built tags_to_unit_dict: {len(tags_to_unit_dict)} tags")
print(f"Built unit_to_tags_dict: {len(unit_to_tags_dict)} units")
print(f"Total unique tags: {len(unique_tags)}")

# ----------------------------- INIT DB -----------------------------

def init_db():
    from models.user import StrengthTable, User  # local import to avoid circular

    db = SessionLocal()
    try:
        if not db.query(User).filter(User.id == 1).first():
            db.add(User(id=1, current_unit=3))
            print("Default user created.")

        existing_tags = {
            row.tag for row in
            db.query(StrengthTable.tag).filter(StrengthTable.user_id == 1).all()
        }
        new_tags = unique_tags - existing_tags

        for tag in new_tags:
            db.add(StrengthTable(
                tag=tag,
                user_id=1,
                stability=1.0,
                last_practice=datetime.utcnow() - timedelta(days=365)  # force low strength
            ))

        db.commit()
        print(f"Strength table seeded: {len(new_tags)} new tags added.")

    finally:
        db.close()