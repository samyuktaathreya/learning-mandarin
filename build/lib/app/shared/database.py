# app/textbook/database.py
from datetime import datetime, timedelta

from sqlalchemy.orm import Session
from core.database import SessionLocal
from core.config.textbook import DICT_PATH
from app.core.logger import logger
# Import required parsed data and constants from services
from textbook.services import unique_vocab_tags, FACETS

def seed_cedict(db: Session):
    from shared.models import DictionaryEntry
    """Bulk inserts CC-CEDICT file into database if dictionary_entries is empty."""
    if db.query(DictionaryEntry).first():
        return

    logger.debug("Seeding CC-CEDICT dictionary into database...")
    if not DICT_PATH.exists():
        logger.debug(f"Warning: CC-CEDICT file not found at {DICT_PATH}")
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
        logger.debug(f"CC-CEDICT seeding complete! ({len(entries)} entries added)")


def init_db():
    from session.models import StrengthTable
    from auth.models import User
    
    db = SessionLocal()
    try:
        if not db.query(User).filter(User.id == 1).first():
            db.add(User(id=1, current_unit=3, graduated_units=""))
            logger.debug("Default user created.")

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
        logger.debug(f"Strength table seeded: {added} new (tag, facet) rows added "
              f"across {len(unique_vocab_tags)} tags x {len(FACETS)} facets.")

        seed_cedict(db)

    finally:
        db.close()