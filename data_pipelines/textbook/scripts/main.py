"""
SQL-based textbook data pipeline runner (hsk-sentences-audio version).

STAGES, in order:
  once, over everything:
    1. load_hsk_lists     vocab list (2025 syllabus + overrides, radicals) + grammar points list
    2. import_hsa         hsk-sentences-audio sentences, bare (no unit, no tags)
    3. tag_sentences      words, POS tags, readings, senses for the main reading,
                          sentence levels from words
    4. detect_grammar     rule-based grammar links; recomputes levels and moves
                          HSK 1 sentences with higher grammar up
  for the levels you build:
    4b. fill_gaps         every list word gets a sentence of its level, from the
                          500-sentence CSV if the dataset has none; the rest flagged
    4c. resolve_senses    one AI call per occurrence the main reading doesn't
                          settle (alternate readings, mismatched pinyin)
  per HSK level, lowest first:
    5. assign_units       units, then unit_id on sentences / words / senses / grammar
  once, after the levels are built:
    6. finalize           tone-sandhi display pinyin, primary senses
    7. create_questions   question bank (rebuilt from scratch each time)

Grammar runs before senses now: it can move a sentence to another level,
and fill_gaps needs final levels. Every stage is safe to re-run: 1-2 update
in place, 3 only tags untagged sentences, 4 recomputes, 4b only looks at
uncovered words, 4c only sends tags still without a meaning (unless
--redo-senses), 5-7 rebuild what they own.

--reset-db deletes everything the pipeline writes before stage 1 (pinyin
syllables and sound guides are kept; pinyin_words points at vocab ids, so
re-run build_pinyin_words.py afterwards).

Each stage runs as `python -m data_pipelines.textbook.scripts.<stage>` from
the repo root, same as running it by hand.

USAGE (from the repo root)
    python main.py --levels 1 2 --reset-db            # wipe, then everything for HSK 1 and 2
    python main.py --levels 1 2                       # everything, building HSK 1 and 2
    python main.py --levels 1 2 --from units          # rebuild units onward (no AI)
    python main.py --levels 1 2 --from finalize       # just pinyin, primary senses, questions
    python main.py --levels 1 2 --only tag            # one stage
    python main.py --estimate-tagging                 # count tagging's AI calls, change nothing
    python main.py --levels 1 2 --redo-senses         # re-pick every ambiguous meaning, then units onward
    python main.py --stats                            # just print the DB summary

After a run, check it:  sqlite3 <db> < check_pipeline.sql
"""
import argparse
import subprocess
import sys
from collections import defaultdict

from app.core.config.textbook import PIPELINE_SCRIPTS_DIR
from app.textbook.db_utils import init_db, get_session, reset_textbook_data
from app.textbook.models import Unit, Vocab, VocabSense, Sentence, SentenceVocab, FitbQuestion, Question

REPO_ROOT = PIPELINE_SCRIPTS_DIR.resolve().parents[2]       # .../data_pipelines/textbook/scripts -> repo root
MODULE_PREFIX = "data_pipelines.textbook.scripts"

# (short name used by --from / --only, script module, runs per level?)
STAGES = [
    ("lists",     "load_hsk_lists",   False),
    ("import",    "import_hsa",       False),
    ("tag",       "tag_sentences",    False),
    ("grammar",   "detect_grammar",   False),
    ("gaps",      "fill_gaps",        "levels"),   # one call with every level
    ("senses",    "resolve_senses",   "levels"),
    ("units",     "assign_units",     True),
    ("finalize",  "finalize",         False),
    ("questions", "create_questions", False),
]
STAGE_NAMES = [name for name, _, _ in STAGES]


def run_module(module: str, args: list[str] | None = None) -> bool:
    """Runs one stage as `python -m <module> [args]` from the repo root."""
    cmd = [sys.executable, "-m", f"{MODULE_PREFIX}.{module}"] + (args or [])
    print(f"$ {' '.join(cmd[1:])}")
    result = subprocess.run(cmd, cwd=REPO_ROOT)
    if result.returncode != 0:
        print(f"❌ {module} exited with status {result.returncode}", file=sys.stderr)
        return False
    return True


def stage_args(name: str, level: int | None, args) -> list[str]:
    if name == "units":
        return ["--hsk-level", str(level)]
    if name == "senses":
        return ["--levels", *map(str, sorted(set(args.levels)))] + (["--redo"] if args.redo_senses else [])
    if name == "gaps":
        return ["--levels", *map(str, sorted(set(args.levels)))]
    if name == "tag":
        out = []
        if args.retag:
            out.append("--retag")
        if args.tag_limit:
            out += ["--limit", str(args.tag_limit)]
        return out
    return []              # create_questions does every level that has units


def print_stats():
    """Summary of what's currently in the database."""
    try:
        with get_session() as db:
            untagged = db.query(Sentence).filter(~Sentence.vocab_links.any()).count()
            print("\n" + "=" * 50)
            print("📊 DATABASE SUMMARY")
            print("=" * 50)
            print(f"  Vocab (identities): {db.query(Vocab).count()}"
                  f"   (in the HSK list: {db.query(Vocab).filter(Vocab.hsk_level.isnot(None)).count()})")
            print(f"  Vocab senses:       {db.query(VocabSense).count()}")
            print(f"  Sentences:          {db.query(Sentence).count()}")
            print(f"    untagged:         {untagged}" + (" ⚠️" if untagged else ""))
            print(f"    with a unit:      {db.query(Sentence).filter(Sentence.unit_id.isnot(None)).count()}")
            print(f"  Units:              {db.query(Unit).count()}")
            print(f"  FITB Qs:            {db.query(FitbQuestion).count()}")
            print(f"  Questions:          {db.query(Question).count()}")
            by_level = defaultdict(lambda: [0, 0])
            for u in db.query(Unit).all():
                by_level[u.hsk_level][0] += 1
            for (lvl,) in db.query(Unit.hsk_level).join(Question, Question.unit_id == Unit.id).all():
                by_level[lvl][1] += 1
            for lvl in sorted(by_level):
                print(f"    HSK{lvl}: {by_level[lvl][0]} unit(s), {by_level[lvl][1]} question(s)")
            print("=" * 50)
            print("Full check: sqlite3 <db> < check_pipeline.sql\n")
    except Exception as e:
        print(f"⚠️  Could not fetch stats: {e}", file=sys.stderr)


def main():
    parser = argparse.ArgumentParser(
        description="Run the textbook data pipeline (hsk-sentences-audio).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__[__doc__.index("USAGE"):],
    )
    parser.add_argument("--levels", nargs="+", type=int,
                        help="HSK levels to build units for, e.g. --levels 1 2 (built lowest first).")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--from", dest="start", choices=STAGE_NAMES,
                       help="Start at this stage and run everything after it.")
    group.add_argument("--only", choices=STAGE_NAMES, help="Run just this stage.")
    group.add_argument("--estimate-tagging", action="store_true",
                       help="Run tag_sentences --estimate (counts AI calls, changes nothing) and stop.")
    group.add_argument("--stats", action="store_true", help="Only print the DB summary.")
    parser.add_argument("--redo-senses", action="store_true",
                        help="Re-pick every ambiguous meaning in the built levels, then rebuild units onward "
                             "(implies --from senses).")
    parser.add_argument("--retag", action="store_true", help="Pass --retag to tag_sentences (re-tag everything).")
    parser.add_argument("--tag-limit", type=int, default=None,
                        help="Pass --limit N to tag_sentences (try it on a few sentences first).")
    parser.add_argument("--no-stats", action="store_true", help="Skip the DB summary at the end.")
    parser.add_argument("--reset-db", action="store_true",
                        help="Delete everything the pipeline wrote, then run from stage 1. "
                             "Re-run build_pinyin_words.py afterwards.")
    args = parser.parse_args()

    init_db()
    if args.stats:
        print_stats()
        return
    if args.estimate_tagging:
        ok = run_module("tag_sentences", ["--estimate"])
        sys.exit(0 if ok else 1)

    if args.reset_db and (args.start or args.only or args.redo_senses):
        parser.error("--reset-db runs the whole pipeline: don't combine it with --from / --only / --redo-senses")
    if args.redo_senses and not (args.start or args.only):
        args.start = "senses"
    if args.only:
        selected = [s for s in STAGES if s[0] == args.only]
    else:
        start = STAGE_NAMES.index(args.start) if args.start else 0
        selected = STAGES[start:]

    needs_levels = any(per_level for _, _, per_level in selected)
    if needs_levels and not args.levels:
        parser.error("--levels is required when the gaps, senses or units stage runs, e.g. --levels 1 2")
    levels = sorted(set(args.levels or []))

    steps = []
    for name, module, per_level in selected:
        if per_level == "levels":
            steps.append((f"{name} (HSK{' '.join(map(str, levels))})", name, module, None))
        elif per_level:
            steps += [(f"{name} (HSK{lvl})", name, module, lvl) for lvl in levels]
        else:
            steps.append((name, name, module, None))

    if args.reset_db:
        with get_session() as db:
            counts = reset_textbook_data(db)
        print("🧹 Reset: deleted " + (", ".join(f"{t} {n}" for t, n in counts.items() if n) or "nothing") +
              "\n   (pinyin_words was cleared too: run build_pinyin_words.py after this pipeline)\n")
    print(f"🎯 Stages: {' -> '.join(label for label, *_ in steps)}\n")
    for i, (label, name, module, level) in enumerate(steps, 1):
        print(f"[{i}/{len(steps)}] {label}")
        if not run_module(module, stage_args(name, level, args)):
            print(f"⛔ Stopped: {label} failed. Fix it, then resume with --from {name}"
                  f"{' --levels ' + ' '.join(map(str, levels)) if levels else ''}.", file=sys.stderr)
            if not args.no_stats:
                print_stats()
            sys.exit(1)
        print(f"✓ {label} done\n")

    print("✅ Pipeline completed successfully!")
    if not args.no_stats:
        print_stats()


if __name__ == "__main__":
    main()