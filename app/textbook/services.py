# app/textbook/services.py
import json
from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy.orm import Session

from database import SessionLocal
from core.config.textbook import (
    QUESTIONS_FILEPATH,
    UNIT_VOCAB_TAGS_FILEPATH,
    DICTIONARY_FILEPATH,
    WORD_TO_PINYIN_FILEPATH,
    DICT_PATH,
)

META_TAGS = {
    "speaking_vocab", "speaking_sentence", "listening_vocab",
    "listening_sentence", "fill_in_the_blank", "transcribe_word_to_pinyin",
    "translate_chinese_word_to_english", "translate_chinese_sentence_to_english",
    "translate_english_word_to_chinese", "translate_english_sentence_to_chinese",
    "transcribe_hanzi_to_pinyin",
}

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
    "transcribe hanzi to pinyin"
]

FACETS = ("character", "pinyin")

# JSON File Loaders
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

try:
    with open(UNIT_VOCAB_TAGS_FILEPATH, 'r', encoding='utf-8') as f:
        unit_to_vocab_tags_dict = {int(k): set(v) for k, v in json.load(f).items()}
    print(f"Loaded unit_vocab_tags: {len(unit_to_vocab_tags_dict)} units")
except FileNotFoundError:
    print(f"Error: {UNIT_VOCAB_TAGS_FILEPATH} not found.")
    unit_to_vocab_tags_dict = {}
except json.JSONDecodeError:
    print("Error: Failed to decode unit_vocab_tags.json.")
    unit_to_vocab_tags_dict = {}

inverted_index = {}     # tag -> [question, ...]
tags_to_unit_dict = {}  # tag -> unit (int)
unit_to_tags_dict = {}  # unit (int) -> set of tags
unique_vocab_tags = set()

for unit_str, questions in unit_questions.items():
    unit = int(unit_str)
    unit_to_tags_dict[unit] = set()

    for q in questions:
        for tag in q.get("tags", []):
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

try:
    with open(DICTIONARY_FILEPATH, 'r', encoding='utf-8') as f:
        hsk1_dictionary = json.load(f)
    print(f"Dictionary loaded! ({len(hsk1_dictionary)} entries)")
except FileNotFoundError:
    print(f"Error: {DICTIONARY_FILEPATH} not found.")
    hsk1_dictionary = {}

try:
    with open(WORD_TO_PINYIN_FILEPATH, 'r', encoding='utf-8') as f:
        word_to_pinyin = json.load(f)
    print(f"word_to_pinyin loaded! ({len(word_to_pinyin)} entries)")
except FileNotFoundError:
    print(f"Error: {WORD_TO_PINYIN_FILEPATH} not found.")
    word_to_pinyin = {}
except json.JSONDecodeError:
    print("Error: Failed to decode word_to_pinyin.json.")
    word_to_pinyin = {}


def seed_cedict(db: Session):
    from models.user import DictionaryEntry
    """Bulk inserts CC-CEDICT file into database if dictionary_entries is empty."""
    if db.query(DictionaryEntry).first():
        return

    print("Seeding CC-CEDICT dictionary into database...")
    if not DICT_PATH.exists():
        print(f"Warning: CC-CEDICT file not found at {DICT_PATH}")
        return

    entries = []
    with open(DICT_PATH, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#'):
                continue

            parts = line.split('/')
            if len(parts) <= 1:
                continue

            english_defs = " / ".join([p for p in parts[1:] if p])

            char_and_pinyin = parts[0].split('[')
            if len(char_and_pinyin) < 2:
                continue

            characters = char_and_pinyin[0].split()
            traditional = characters[0]
            simplified = characters[1] if len(characters) > 1 else traditional
            pinyin = char_and_pinyin[1].rstrip(']').strip()

            entries.append({
                "traditional": traditional,
                "simplified": simplified,
                "pinyin": pinyin,
                "english": english_defs
            })

    if entries:
        db.bulk_insert_mappings(DictionaryEntry, entries)
        db.commit()
        print(f"CC-CEDICT seeding complete! ({len(entries)} entries added)")


def init_db():
    from session.models import StrengthTable
    from auth.models import User
    db = SessionLocal()
    try:
        if not db.query(User).filter(User.id == 1).first():
            db.add(User(id=1, current_unit=3, graduated_units=""))
            print("Default user created.")

        existing = {
            (row.tag, row.facet) for row in
            db.query(StrengthTable.tag, StrengthTable.facet)
              .filter(StrengthTable.user_id == 1).all()
        }
        added = 0
        for tag in unique_vocab_tags:
            for facet in FACETS:
                if (tag, facet) in existing:
                    continue
                db.add(StrengthTable(
                    tag=tag,
                    user_id=1,
                    facet=facet,
                    correct_count=0,
                    stability=1.0,
                    last_practice=datetime.utcnow() - timedelta(days=365),
                ))
                added += 1

        db.commit()
        print(f"Strength table seeded: {added} new (tag, facet) rows added "
              f"across {len(unique_vocab_tags)} tags x {len(FACETS)} facets.")

        seed_cedict(db)

    finally:
        db.close()