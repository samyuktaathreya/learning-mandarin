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
print(f"Built unit_to_tags_dict with {len(unit_to_tags_dict)} units.")