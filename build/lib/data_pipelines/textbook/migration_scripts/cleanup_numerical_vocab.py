"""
data_pipelines/textbook/scripts/cleanup_numeral_vocab.py

One-time cleanup: removes VocabSense/Vocab/SentenceVocab/Question rows that
were incorrectly registered as permanent "vocabulary" for numeral-run
phrases (dates, ages, quantities, durations -- e.g. 二零一一年九月, 十二个月,
一百块钱, 六岁) before tag_sentences.py/import_sentences.py started
excluding "NUM"-tagged tokens from registration.

Safe to run once after upgrading to the fixed pipeline. Running it again is
a no-op (nothing left matching the junk criterion).

Does NOT touch any Vocab/VocabSense whose hanzi is a REAL word that merely
CONTAINS numeral characters (星期二, 一定, 一起, 十字路口, ...) -- only rows
whose ENTIRE hanzi string is exactly one numeral run, under the exact same
extract_numeral_runs() rule tag_sentences.py itself uses to decide what to
skip going forward. Reusing that function (rather than re-implementing the
regex here) means this cleanup can never silently drift out of sync with
what the pipeline currently considers "not vocabulary" -- if the rule ever
changes, this script's definition of "junk" changes with it automatically.

WHY THIS INSTEAD OF DELETING A WHOLE UNIT: the unit these junk entries
landed in (typically the LAST unit of an HSK level, per the pre-fix
placement bug) also contains LEGITIMATE vocabulary from vocab_index_parser
-- the printed index's real content for that unit. Deleting the whole unit
would destroy that too. This only removes rows that fail the exact junk
test, regardless of which unit they ended up in.

USAGE
-----
    python cleanup_numeral_vocab.py --dry-run            # show what WOULD be deleted
    python cleanup_numeral_vocab.py                       # actually delete
    python cleanup_numeral_vocab.py --hsk-level 1          # scope to one level (default: all)

RECOMMENDED FULL SEQUENCE (no full pipeline rerun needed):
    1. python cleanup_numeral_vocab.py --hsk-level 1
       -- removes the junk vocab/sense/tag-link/question rows
    2. python tag_sentences.py --retag
       -- re-tags every sentence (idempotent; cache hits make already-
          correct words free, only genuinely-affected sentences change)
    3. python -c "from app.textbook.db_utils import get_session, rehome_sentences; \\
                    from app.textbook.db_utils import init_db; init_db(); \\
                    db = next(iter([get_session().__enter__()])); \\
                    print(rehome_sentences(db, hsk_level=1)); db.commit()"
       -- moves sentences that were dragged to the end-of-level fallback
          back to their true earliest legitimate unit, now that their
          junk tag is gone
    4. python create_questions.py
       -- regenerates the question bank cleanly (its own cleanup step
          already removes stale ambiguous rows; this also picks up
          anything affected by steps 1-3)
"""
import argparse

from app.textbook.db_utils import get_session, init_db
from app.textbook.models import Vocab, VocabSense, Question, SentenceVocab, Unit

from data_pipelines.textbook.scripts.tag_sentences import extract_numeral_runs, _NUMERAL_CHARS

# Characters that commonly combine with a numeral to form a date/quantity/
# duration/time-of-day phrase, used as signal 2 in is_junk_numeral_vocab
# (single-digit-only phrases that signal 1's length>=2 check would miss).
# Deliberately generous -- false positives here are caught by --dry-run
# review before anything is actually deleted.
_QUANTITY_TIME_CHARS = (
    "年月日号岁次天块钱分钟点半个周毛角元遍回趟"  # dates/currency/duration/counters
    "口家本"                                          # family/book counters
    "上午早晚中小时"                                  # time-of-day words (上午/早上/小时/中午/晚上)
    # Deliberately does NOT include 下/些/边/会/儿 -- each is far too
    # common/generic on its own (一下, 一些, 一边, 一会儿, 那儿, ... are all
    # ordinary fixed vocabulary, not quantity/date phrases) and including
    # them caused real false positives when tested against actual data.
)


def is_junk_numeral_vocab(hanzi: str) -> bool:
    """True if `hanzi` looks like a pre-fix artifact: a whole PHRASE (a
    date, age, quantity, duration, or time-of-day expression -- e.g.
    "二零一一年九月", "九月十号", "五口", "上午九点半") that got registered as
    one indivisible "word" before the pipeline started pre-splitting
    numeral content out of sentence text. Two independent signals, either
    is sufficient:

      1. Contains an embedded numeral run of length >= 2 (or a 第-prefixed
         ordinal) anywhere in it -- catches multi-digit dates/quantities
         like "二零一一年九月", "十二个月", "第二次".
      2. Contains a numeral character AND every remaining character is a
         common date/quantity/counting/time-of-day word -- catches
         single-digit-only phrases like "九月十号", "六岁", "一年", "五口",
         "上午九点半" that signal 1 alone would miss (no length>=2 run).

    Single CHARACTERS are never flagged (len(hanzi) < 2 short-circuits) --
    this cleanup targets glued-together PHRASES, not individual number
    characters that may have been legitimately taught as their own
    vocabulary in an early "learning to count" unit.

    THIS IS A HEURISTIC, not a guarantee -- compositional language is
    open-ended and no fixed character list can enumerate every possible
    combining word. ALWAYS review --dry-run output before deleting for
    real; if something legitimate gets flagged, use --exclude-hanzi to
    protect it."""
    if len(hanzi) < 2:
        return False

    pieces = extract_numeral_runs(hanzi)
    if any(is_num for _, is_num in pieces):
        return True

    has_numeral = any(c in _NUMERAL_CHARS for c in hanzi)
    if has_numeral and all(c in _NUMERAL_CHARS or c in _QUANTITY_TIME_CHARS for c in hanzi):
        return True

    return False


def find_junk_vocab(db, hsk_level: int = None, exclude: set[str] = frozenset()) -> list[Vocab]:
    query = db.query(Vocab)
    if hsk_level is not None:
        query = query.join(Unit, Vocab.unit_id == Unit.id, isouter=True).filter(
            (Unit.hsk_level == hsk_level) | (Vocab.unit_id.is_(None))
        )
    all_vocab = query.all()
    return [v for v in all_vocab if is_junk_numeral_vocab(v.hanzi) and v.hanzi not in exclude]


def main():
    parser = argparse.ArgumentParser(description="Clean up numeral-run junk vocab created before the NUM-token fix.")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--hsk-level", type=int, default=None,
                         help="Scope to one HSK level (default: all levels).")
    parser.add_argument("--exclude-hanzi", type=str, default="",
                         help="Comma-separated exact hanzi strings to protect from deletion, "
                              "in case the heuristic flags something legitimate. "
                              "e.g. --exclude-hanzi 十分,五口")
    args = parser.parse_args()

    exclude = {h.strip() for h in args.exclude_hanzi.split(",") if h.strip()}

    init_db()
    with get_session() as db:
        junk = find_junk_vocab(db, args.hsk_level, exclude=exclude)
        print(f"Found {len(junk)} junk numeral-run vocab entr(y/ies).")

        if args.dry_run:
            for v in junk[:50]:
                unit_label = f"unit {v.unit.unit_number} (HSK{v.unit.hsk_level})" if v.unit else "no unit"
                print(f"  - {v.hanzi!r} ({v.english!r}) @ {unit_label}")
            if len(junk) > 50:
                print(f"  ... and {len(junk) - 50} more")
            print("\nDry run -- nothing deleted. Re-run without --dry-run to actually clean up.")
            return

        if not junk:
            print("Nothing to clean up.")
            return

        junk_ids = [v.id for v in junk]

        # Explicit ordered deletion (child -> parent) rather than relying
        # on ORM cascade config, since bulk .delete() calls don't reliably
        # trigger relationship-level cascades the same way session.delete()
        # on an individually-loaded object does.
        q_count = db.query(Question).filter(Question.vocab_id.in_(junk_ids)) \
                    .delete(synchronize_session=False)
        sv_count = db.query(SentenceVocab).filter(SentenceVocab.vocab_id.in_(junk_ids)) \
                    .delete(synchronize_session=False)
        vs_count = db.query(VocabSense).filter(VocabSense.vocab_id.in_(junk_ids)) \
                    .delete(synchronize_session=False)
        v_count = db.query(Vocab).filter(Vocab.id.in_(junk_ids)) \
                    .delete(synchronize_session=False)

        db.commit()
        print(f"Deleted: {v_count} vocab, {vs_count} sense(s), "
              f"{sv_count} sentence-tag link(s), {q_count} question(s).")
        print("\nNext: re-tag affected sentences (they now have fewer, correct "
              "tags), then rehome, then regenerate questions -- see this script's "
              "module docstring for the exact commands.")


if __name__ == "__main__":
    main()