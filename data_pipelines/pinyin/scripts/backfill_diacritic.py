"""
One-time backfill: populates PinyinSyllable.diacritic_pinyin for every
existing row, using convert_diacritic.numbered_to_diacritic. Safe to
rerun -- only touches rows where the column is still NULL.
"""
from app.textbook.db_utils import SessionLocal
from app.textbook.models import PinyinSyllable
from data_pipelines.pinyin.scripts.convert_diacritic import numbered_to_diacritic


def backfill():
    db = SessionLocal()
    try:
        rows = db.query(PinyinSyllable).filter(PinyinSyllable.diacritic_pinyin.is_(None)).all()
        for row in rows:
            numbered = f"{row.syllable}{row.tone}"
            row.diacritic_pinyin = numbered_to_diacritic(numbered)
        db.commit()
        print(f"Backfilled {len(rows)} rows.")
    finally:
        db.close()


if __name__ == "__main__":
    backfill()