from pathlib import Path

from app.core.config.shared import DATA_DIR
from app.textbook.db_utils import SessionLocal
from app.textbook.models import PinyinSyllable
from app.pinyin.services import decompose_pinyin

RAW_PATH = "../data/raw/pinyin.txt"

# Longest-first so "zh"/"ch"/"sh" match before "z"/"c"/"s".
INITIALS = sorted(
    ["zh", "ch", "sh", "b", "p", "m", "f", "d", "t", "n", "l", "g", "k",
     "h", "j", "q", "x", "r", "z", "c", "s", "y", "w"],
    key=len, reverse=True,
)

TONES = (1, 2, 3, 4)

def seed_pinyin_syllables() -> None:
    db = SessionLocal()
    try:
        with open(RAW_PATH, encoding="utf-8") as f:
            syllables = [line.strip() for line in f if line.strip()]

        added, skipped = 0, 0
        for syllable in syllables:
            initial, final = decompose_pinyin(syllable)
            for tone in TONES:
                exists = (
                    db.query(PinyinSyllable)
                    .filter_by(syllable=syllable, tone=tone)
                    .first()
                )
                if exists:
                    skipped += 1
                    continue
                db.add(PinyinSyllable(
                    syllable=syllable,
                    tone=tone,
                    initial_tag=initial,
                    final_tag=final,
                ))
                added += 1
        db.commit()
        print(f"Seeded {added} PinyinSyllable rows, skipped {skipped} existing.")
    finally:
        db.close()


if __name__ == "__main__":
    seed_pinyin_syllables()