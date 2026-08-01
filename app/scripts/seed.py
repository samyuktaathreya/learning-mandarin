import sys
from pathlib import Path
from datetime import datetime, timedelta

# Ensure the app root is in the Python path so we can import our modules
# when running this script from the command line.
BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.append(str(BASE_DIR))

from database import SessionLocal, unique_vocab_tags, FACETS
from auth.models import User
from session.models import StrengthTable, DictionaryEntry

# Data path is relative to the project root
DICT_PATH = BASE_DIR / "language-app-data" / "data" / "raw" / "chinese_english_dictionary.u8"

def seed_cedict(db):
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

        # Seed Cedict
        seed_cedict(db)

    finally:
        db.close()


if __name__ == "__main__":
    print("Starting database seed...")
    init_db()
    print("Database seeding complete.")