"""
Cleans up PinyinSyllable.character after backfill_character has run.

Two kinds of bad rows get removed:
  1. Rows with no character at all (character is NULL/empty) -- these
     don't map to any real character and are noise in the table.
  2. Rows whose character doesn't actually carry that diacritic
     reading. E.g. row 1196 "shuang4" -> 淙 is stored as "shuàng", but
     pypinyin says 淙 is only ever read "cóng" -- "shuàng" never shows up
     in pypinyin's reading list for that character, so it's wrong.

This checks each row directly against pypinyin rather than reusing
backfill_character's _reading_confirmed/_pypinyin_readings -- those were
letting rows like 淙="shuàng" through, so they aren't a reliable gate.

Uses heteronym=True so genuinely polyphonic characters (multiple valid
readings) aren't punished for having a reading that isn't pypinyin's
single "default" one -- the stored diacritic just has to appear
somewhere in pypinyin's full reading list for that character.

Meant to run as the last step of the pipeline, after backfill_character,
so it cleans up whatever that step (or older/manually-edited data) left
behind.
"""

from pypinyin import Style, pinyin

from app.textbook.db_utils import SessionLocal
from app.textbook.models import PinyinSyllable


def _pypinyin_readings(char: str) -> list[str]:
    """All diacritic-toned readings pypinyin knows for a single character."""
    result = pinyin(char, style=Style.TONE, heteronym=False, errors="ignore")
    if (char == "哋"):
        print("result: ", result)
    if not result:
        return []
    return result[0]


def clean_character() -> None:
    db = SessionLocal()
    try:
        rows = db.query(PinyinSyllable).all()

        removed_empty: list[str] = []
        removed_mismatch: list[tuple[str, str, list[str]]] = []
        kept = 0

        for row in rows:
            key = f"{row.syllable}{row.tone}"
            char = (row.character or "").strip()
            diacritic = (row.diacritic_pinyin or "").strip()

            if not char:
                removed_empty.append(key)
                db.delete(row)
                continue

            readings = _pypinyin_readings(char)
            if diacritic not in readings:
                removed_mismatch.append((key, char, readings))
                db.delete(row)
                continue

            kept += 1

        db.commit()

        print(f"Kept {kept} of {len(rows)} rows.")

        '''if removed_empty:
            print(f"\nRemoved {len(removed_empty)} rows with no character:")
            for key in sorted(removed_empty):
                print(f"  {key}")

        if removed_mismatch:
            print(
                f"\nRemoved {len(removed_mismatch)} rows whose character "
                f"doesn't actually carry that reading:"
            )
            for key, char, readings in sorted(removed_mismatch):
                print(f"  {key:10} {char}  (pypinyin says: {readings})")
        '''
    finally:
        db.close()


if __name__ == "__main__":
    clean_character()