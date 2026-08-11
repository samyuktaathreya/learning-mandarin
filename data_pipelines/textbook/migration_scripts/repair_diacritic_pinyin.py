"""
data_pipelines/textbook/scripts/repair_diacritic_pinyin.py

One-off repair: append_orphan_tags.py's clean_pinyin() used to only strip
whitespace/brackets and never actually converted diacritic pinyin (e.g.
"tàirelè") to this app's numeric-tone storage format (e.g. "tai4re4le5").
Words extracted from the printed index by vocab_index_parser.py were never
affected (it already calls diacritic_to_numeric explicitly) -- only words
filled in by append_orphan_tags.py's Claude-backed gap-filling were.

This script finds every Vocab row whose pinyin has NO digit in it (the
signature of an unconverted diacritic string -- diacritic_to_numeric always
appends a 1-5 tone digit per syllable) and re-normalizes it in place via the
now-fixed clean_pinyin()/diacritic_to_numeric(). Safe to re-run: a pinyin
that's already numeric is left untouched (diacritic_to_numeric is a no-op on
strings that already contain a digit).

Usage:
    python data_pipelines/textbook/scripts/repair_diacritic_pinyin.py            # apply fixes
    python data_pipelines/textbook/scripts/repair_diacritic_pinyin.py --dry-run  # preview only
"""
import argparse
import re

from app.textbook.db_utils import get_session, init_db
from app.textbook.models import Vocab
from data_pipelines.textbook.scripts.vocab_pinyin_utils import diacritic_to_numeric

_HAS_TONE_DIGIT = re.compile(r"[1-5]")


def main():
    parser = argparse.ArgumentParser(description="Repair un-converted diacritic pinyin in the vocab table.")
    parser.add_argument("--dry-run", action="store_true", help="Preview changes without writing.")
    args = parser.parse_args()

    init_db()
    with get_session() as db:
        all_vocab = db.query(Vocab).all()

        suspect = [
            v for v in all_vocab
            if v.pinyin and v.pinyin != "UNKNOWN_PINYIN" and not _HAS_TONE_DIGIT.search(v.pinyin)
        ]

        if not suspect:
            print("✅ No un-converted diacritic pinyin found -- nothing to repair.")
            return

        print(f"Found {len(suspect)} vocab row(s) with likely un-converted diacritic pinyin:\n")

        fixed = 0
        for v in suspect:
            new_pinyin = diacritic_to_numeric(v.pinyin)
            if new_pinyin == v.pinyin:
                # diacritic_to_numeric couldn't do anything with it either --
                # flag for manual review rather than silently leaving it
                print(f"  [unchanged, needs manual review] {v.hanzi}: '{v.pinyin}'")
                continue
            print(f"  {v.hanzi}: '{v.pinyin}' -> '{new_pinyin}'")
            if not args.dry_run:
                v.pinyin = new_pinyin
            fixed += 1

        if args.dry_run:
            print(f"\nDry run: would fix {fixed} row(s). Re-run without --dry-run to apply.")
        else:
            print(f"\n✅ Repaired {fixed} row(s).")


if __name__ == "__main__":
    main()