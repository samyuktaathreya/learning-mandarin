from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from datetime import datetime, timedelta
import json

SQLALCHEMY_DATABASE_URL = "sqlite:///./mandarin_app.db"

engine = create_engine(SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# meta-tags that should not be tracked in SRS
META_TAGS = {
    "speaking_vocab", "speaking_sentence", "listening_vocab",
    "listening_sentence", "fill_in_the_blank", "transcribe_word_to_pinyin",
    "translate_chinese_word_to_english", "translate_chinese_sentence_to_english",
    "translate_english_word_to_chinese", "translate_english_sentence_to_chinese",
}

# all question types we track strength for
QUESTION_TYPES = [
    "listening sentence",
    "speaking sentence",
    "speaking vocab",
    "listening vocab",
    "transcribe word to pinyin",
    "translate english sentence to chinese",
    "translate english word to chinese",
    "fill in the blank",
    "translate chinese sentence to english",
    "translate chinese word to english",
]

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
unique_vocab_tags = set()  # only real vocab/grammar tags, no meta-tags

for unit_str, questions in unit_questions.items():
    unit = int(unit_str)
    unit_to_tags_dict[unit] = set()

    for q in questions:
        for tag in q.get("tags", []):
            # skip meta-tags and unit tags for SRS
            if tag in META_TAGS or tag.startswith("unit_"):
                continue

            unique_vocab_tags.add(tag)

            if tag not in tags_to_unit_dict or unit < tags_to_unit_dict[tag]:
                tags_to_unit_dict[tag] = unit

            unit_to_tags_dict[unit].add(tag)

            if tag not in inverted_index:
                inverted_index[tag] = []
            inverted_index[tag].append({**q, "unit": unit})

print(f"Built inverted_index: {len(inverted_index)} vocab tags")
print(f"Built tags_to_unit_dict: {len(tags_to_unit_dict)} vocab tags")
print(f"Built unit_to_tags_dict: {len(unit_to_tags_dict)} units")
print(f"Total unique vocab tags: {len(unique_vocab_tags)}")

# ----------------------------- INIT DB -----------------------------

def init_db():
    from models.user import StrengthTable, User

    db = SessionLocal()
    try:
        if not db.query(User).filter(User.id == 1).first():
            db.add(User(id=1, current_unit=3))
            print("Default user created.")

        # seed one row per (tag, question_type) combo
        existing = {
            (row.tag, row.question_type)
            for row in db.query(StrengthTable.tag, StrengthTable.question_type)
            .filter(StrengthTable.user_id == 1).all()
        }

        new_rows = 0
        for tag in unique_vocab_tags:
            for question_type in QUESTION_TYPES:
                if (tag, question_type) not in existing:
                    db.add(StrengthTable(
                        tag=tag,
                        question_type=question_type,
                        user_id=1,
                        stability=1.0,
                        last_practice=datetime.utcnow() - timedelta(days=365)
                    ))
                    new_rows += 1

        db.commit()
        print(f"Strength table seeded: {new_rows} new rows added.")

    finally:
        db.close()