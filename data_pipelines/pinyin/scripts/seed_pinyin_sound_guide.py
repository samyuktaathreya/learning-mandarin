import csv
from pathlib import Path

from app.textbook.db_utils import SessionLocal
from app.textbook.models import PinyinSoundGuide, PinyinSyllable

# Resolved relative to this file, so it works no matter where you run it from
CSV_PATH = (Path(__file__).parent / "../data/raw/pinyin_sound_guide.csv").resolve()
DELIMITER = ","  # change to "," if your file is comma-separated
VALID_CATEGORIES = {"initial", "final"}


def seed_pinyin_sound_guides() -> None:
    db = SessionLocal()
    try:
        # Create the table if it doesn't exist yet (no-op if it does)
        PinyinSoundGuide.__table__.create(bind=db.get_bind(), checkfirst=True)

        # Known tags, used to catch typos in the CSV
        known = {
            "initial": {t for (t,) in db.query(PinyinSyllable.initial_tag).distinct() if t},
            "final": {t for (t,) in db.query(PinyinSyllable.final_tag).distinct() if t},
        }

        existing = {(g.category, g.tag): g for g in db.query(PinyinSoundGuide).all()}

        inserted = updated = skipped = 0

        with open(CSV_PATH, newline="", encoding="utf-8-sig") as f:
            for line_no, r in enumerate(csv.DictReader(f, delimiter=DELIMITER), start=2):
                tag = (r.get("sound") or "").strip()
                category = (r.get("type") or "").strip().lower()
                description = (r.get("pronunciation") or "").strip()

                if not tag or not description or category not in VALID_CATEGORIES:
                    print(f"line {line_no}: skipped, bad row {r}")
                    skipped += 1
                    continue

                if tag not in known[category]:
                    print(f"line {line_no}: warning, '{tag}' is not a known {category} tag")

                guide = existing.get((category, tag))
                if guide:
                    if guide.description != description:
                        guide.description = description
                        updated += 1
                else:
                    guide = PinyinSoundGuide(category=category, tag=tag, description=description)
                    db.add(guide)
                    existing[(category, tag)] = guide
                    inserted += 1

        db.commit()
        print(f"Done: {inserted} inserted, {updated} updated, {skipped} skipped")
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    seed_pinyin_sound_guides()