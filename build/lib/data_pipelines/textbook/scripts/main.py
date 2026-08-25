"""
SQL-based textbook data pipeline runner.

REWRITTEN PIPELINE (replaces the old vocab_index_parser -> sentence_parser
-> extract_and_match_grammar -> create_questions -> append_orphan_tags
order):

  1. vocab_index_parser.py   -> Vocab/VocabSense rows FROM THE PRINTED INDEX
  2. sentence_parser.py      -> bare Sentence rows ONLY (NO FITB, no tagging
                                 -- OCR + LLM extraction + verbatim-check
                                 only)
  3. fitb_parser.py          -> bare FitbQuestion rows ONLY, matched back to
                                 the Sentence rows sentence_parser.py just
                                 wrote. Split out of sentence_parser.py
                                 because it's a different kind of row and
                                 depends on sentence_parser.py having
                                 already committed for this unit -- it reads
                                 Sentence rows fresh from the DB rather than
                                 sharing in-memory state. Also applies two
                                 new programmatic filters: drops any
                                 question whose answer isn't pure hanzi, and
                                 drops any question whose text leaked pinyin
                                 with tone diacritics.
  4. tag_sentences.py        -> HanLP-segments every bare sentence, resolves
                                 SentenceVocab tags + VocabSense creation/
                                 matching/rehoming via AI where needed
  5. import_sentences.py     -> external supplementary sentences (SEPARATE
                                 script, run per level, AFTER 1-4 complete
                                 for that level -- its placement logic
                                 depends on what's already known)
  6. extract_and_match_grammar.py -> GrammarTip + SentenceGrammar rows
  7. create_questions.py     -> Question rows (every word used in a
                                 sentence is now documented by this point)

append_orphan_tags.py IS DELETED. Its core mistake was inventing vocab
entries from the printed INDEX with no sentence evidence at all -- e.g. the
index lists 东西 (a compound) at some unit, and the old gap-filler would
register the SUBSTRING 西 as if it were independently taught there, because
nothing gated vocab creation on "does a real sentence actually use this."
tag_sentences.py and import_sentences.py are now the ONLY two places new
vocab senses get created, and both require actual sentence evidence.

PER-HSK-LEVEL EXECUTION: stages 1-3 write directly from the textbook's own
per-level source (index PDF, unit pages) and don't depend on other levels.
Stage 4 (tag_sentences) depends on stage 2 (sentence_parser) for that same
level. Stage 5 (import_sentences) DOES depend on 1-4 having fully completed
for that same level -- its placement logic reads "what's already known" for
that level. Stage 6-7 depend on all of 1-5 for that level. So the pipeline
runs FULLY through stage 7 for one level before starting the next level, not
stage-by-stage across every level. --hsk-level scopes a run to ONE level;
omit it (or pass --all-levels) to run every level in order.

A level > 1 requires the level below it to already be populated (rehoming
and import_sentences placement both implicitly assume lower levels are
"settled" reference data) -- main.py checks this and refuses to run a level
out of order.

Usage:
    python main.py --hsk-level 1                    # full pipeline for HSK1 only
    python main.py --hsk-level 2                    # full pipeline for HSK2 (requires HSK1 done)
    python main.py --all-levels                     # HSK1, then HSK2, ... in order
    python main.py --hsk-level 1 --vocab-only        # stop after vocab_index_parser
    python main.py --hsk-level 1 --from-sentences    # start from sentence_parser
    python main.py --hsk-level 1 --from-fitb         # start from fitb_parser
    python main.py --hsk-level 1 --from-tagging      # start from tag_sentences
    python main.py --hsk-level 1 --from-external     # start from import_sentences
    python main.py --hsk-level 1 --from-grammar      # start from extract_and_match_grammar
    python main.py --hsk-level 1 --from-questions    # start from create_questions
    python main.py --hsk-level 1 --units 3 4 5       # selective: only these units in sentence_parser/fitb_parser
    python main.py --hsk-level 1 --sources textbook  # only process textbook (not workbook)
    python main.py --hsk-level 1 --skip-external     # don't run import_sentences this run
"""

import os
import sys
import argparse
from collections import defaultdict

from app.core.config.textbook import PIPELINE_SCRIPTS_DIR

from app.textbook.db_utils import init_db, get_session
from app.textbook.models import Unit, Vocab, VocabSense, Sentence, FitbQuestion, Question


def run_script(script_name: str, env_overrides: dict = None, script_dir=None, script_args: list = None) -> bool:
    """Run a pipeline script as a subprocess."""
    base_dir = script_dir or PIPELINE_SCRIPTS_DIR
    script_path = base_dir / f"{script_name}.py"
    if not script_path.exists():
        print(f"❌ Script not found: {script_path}", file=sys.stderr)
        return False

    env = os.environ.copy()
    if env_overrides:
        env.update(env_overrides)

    import subprocess
    
    # Build the base command
    cmd = [sys.executable, str(script_path)]
    # Append any command-line arguments if they were provided
    if script_args:
        cmd.extend(script_args)
        
    result = subprocess.run(cmd, env=env)
    if result.returncode != 0:
        print(f"❌ {script_name} exited with status {result.returncode}", file=sys.stderr)
        return False

    return True


def print_stats():
    """Print a summary of what's currently in the database."""
    try:
        with get_session() as db:
            units_count = db.query(Unit).count()
            vocab_count = db.query(Vocab).count()
            senses_count = db.query(VocabSense).count()
            sentences_count = db.query(Sentence).count()
            fitb_count = db.query(FitbQuestion).count()
            questions_count = db.query(Question).count()
            untagged_sentences = db.query(Sentence).filter(~Sentence.vocab_links.any()).count()

            print("\n" + "=" * 50)
            print("📊 DATABASE SUMMARY")
            print("=" * 50)
            print(f"  Units:              {units_count}")
            print(f"  Vocab (identities): {vocab_count}")
            print(f"  Vocab senses:       {senses_count}")
            print(f"  Sentences:          {sentences_count}")
            print(f"    untagged:         {untagged_sentences}" + (" ⚠️" if untagged_sentences else ""))
            print(f"  FITB Qs:            {fitb_count}")
            print(f"  Questions:          {questions_count}")

            level_counts = defaultdict(int)
            for u in db.query(Unit).all():
                level_counts[u.hsk_level] += 1
            if len(level_counts) > 1:
                print("  --- by hsk_level ---")
                for level in sorted(level_counts):
                    print(f"    HSK{level}: {level_counts[level]} unit(s)")
            print("=" * 50 + "\n")
    except Exception as e:
        print(f"⚠️  Could not fetch stats: {e}", file=sys.stderr)


def check_level_prerequisites(hsk_level: int) -> bool:
    """A level > 1 requires the level below it to already have units --
    rehoming and import_sentences placement both implicitly treat lower
    levels as settled reference data. Returns True if OK to proceed."""
    if hsk_level <= 1:
        return True
    with get_session() as db:
        prior_count = db.query(Unit).filter(Unit.hsk_level == hsk_level - 1).count()
    if prior_count == 0:
        print(f"❌ HSK level {hsk_level - 1} has no units yet -- run the pipeline for "
              f"HSK level {hsk_level - 1} before HSK level {hsk_level}.", file=sys.stderr)
        return False
    return True


def run_pipeline_for_level(hsk_level: int, args) -> list[str]:
    """Runs the full per-level pipeline (stages 1-7) for one HSK level,
    honoring the --from-*/--vocab-only/--skip-external flags. Returns the
    list of stage names that failed (empty if all succeeded)."""
    pipeline = [
        "vocab_index_parser",
        "sentence_parser",
        "fitb_parser",
        "tag_sentences",
        "import_sentences",
        "extract_and_match_grammar",
        "create_questions",
    ]

    if args.vocab_only:
        pipeline = ["vocab_index_parser"]
    elif args.from_sentences:
        pipeline = pipeline[1:]
    elif args.from_fitb:
        pipeline = pipeline[2:]
    elif args.from_tagging:
        pipeline = pipeline[3:]
    elif args.from_external:
        pipeline = pipeline[4:]
    elif args.from_grammar:
        pipeline = pipeline[5:]
    elif args.from_questions:
        pipeline = pipeline[6:]

    if args.skip_external and "import_sentences" in pipeline:
        pipeline.remove("import_sentences")

    common_env_overrides = {"HSK_LEVEL": str(hsk_level)}

    # sentence_parser and fitb_parser both accept the same UNITS/SOURCES
    # selective-reprocessing overrides (fitb_parser needs to scope to the
    # same units/sources it's matching Sentence rows against).
    extraction_env_overrides = {}
    if args.units:
        extraction_env_overrides["UNITS_TO_PROCESS"] = ",".join(str(u) for u in args.units)
    if args.sources:
        extraction_env_overrides["SOURCES_TO_PROCESS"] = ",".join(args.sources)

    import_sentences_env_overrides = {}
    if args.topic:
        import_sentences_env_overrides["TOPIC"] = args.topic

    print(f"\n🎯 HSK level: {hsk_level}")
    if not check_level_prerequisites(hsk_level):
        return ["prerequisite_check"]

    failed_scripts = []
    for i, script_name in enumerate(pipeline, start=1):
        print(f"[{i}/{len(pipeline)}] Running {script_name} (HSK{hsk_level})...")

        script_env = dict(common_env_overrides)
        script_args = [] # Initialize empty list for CLI arguments

        if script_name in ("sentence_parser", "fitb_parser"):
            script_env.update(extraction_env_overrides)
            
        if script_name == "import_sentences":
            script_env.update(import_sentences_env_overrides)
            # import_sentences requires CLI arguments, not just env vars
            script_args.extend(["--hsk-level", str(hsk_level)])
            if args.topic:
                script_args.extend(["--topic", args.topic])

        # import_sentences.py lives outside PIPELINE_SCRIPTS_DIR
        script_dir = None
        if script_name == "import_sentences":
            from app.core.config.textbook import HSK_SENTENCES_AUDIO_SCRIPTS_DIR
            script_dir = HSK_SENTENCES_AUDIO_SCRIPTS_DIR

        # Pass the script_args into the function
        if not run_script(script_name, script_env, script_dir=script_dir, script_args=script_args):
            failed_scripts.append(script_name)
            print(f"❌ {script_name} failed. Aborting the pipeline!\n", file=sys.stderr)
            # Return immediately on failure to stop the pipeline
            return failed_scripts
        else:
            print(f"✓ {script_name} completed\n")

    return failed_scripts


def main():
    parser = argparse.ArgumentParser(
        description="Run the SQL-based textbook data pipeline.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python main.py --hsk-level 1                    # Full pipeline, HSK1 only
  python main.py --all-levels                      # Every level, in order
  python main.py --hsk-level 1 --vocab-only        # Stop after vocab_index_parser
  python main.py --hsk-level 1 --from-fitb         # Start from fitb_parser
  python main.py --hsk-level 1 --from-tagging      # Start from tag_sentences
  python main.py --hsk-level 1 --units 3 4 5       # Reprocess only units 3, 4, 5
  python main.py --hsk-level 1 --skip-external     # Skip import_sentences this run
        """
    )

    level_group = parser.add_mutually_exclusive_group(required=True)
    level_group.add_argument("--hsk-level", type=int, help="Run the pipeline for this HSK level only.")
    level_group.add_argument("--all-levels", action="store_true",
                              help="Run every HSK level found under the raw data directory, in order.")

    parser.add_argument("--vocab-only", action="store_true",
                         help="Run only vocab_index_parser and exit.")
    parser.add_argument("--from-sentences", action="store_true",
                         help="Start from sentence_parser (vocab already done).")
    parser.add_argument("--from-fitb", action="store_true",
                         help="Start from fitb_parser (vocab and sentences already done).")
    parser.add_argument("--from-tagging", action="store_true",
                         help="Start from tag_sentences (vocab, sentences, and FITB already done).")
    parser.add_argument("--from-external", action="store_true",
                         help="Start from import_sentences (vocab, sentences, FITB, tagging already done).")
    parser.add_argument("--from-grammar", action="store_true",
                         help="Start from extract_and_match_grammar.")
    parser.add_argument("--from-questions", action="store_true",
                         help="Start from create_questions (everything else already done).")
    parser.add_argument("--skip-external", action="store_true",
                         help="Don't run import_sentences this run (e.g. no network access, or "
                              "iterating quickly on the textbook-only stages).")
    parser.add_argument("--units", nargs="+", type=int,
                         help="Selective reprocessing: only these unit numbers. Passed to "
                              "sentence_parser and fitb_parser.")
    parser.add_argument("--sources", nargs="+", choices=["textbook", "workbook"],
                         help="Selective reprocessing: only these sources. Passed to "
                              "sentence_parser and fitb_parser.")
    parser.add_argument("--topic", type=str, default=None,
                         help="Only import external sentences matching this topic. Passed to import_sentences.")
    parser.add_argument("--no-stats", action="store_true",
                         help="Skip printing database statistics at the end.")

    args = parser.parse_args()

    print("🗄️  Initializing database...")
    try:
        init_db()
        print("✓ Database initialized\n")
    except Exception as e:
        print(f"❌ Failed to initialize database: {e}", file=sys.stderr)
        sys.exit(1)

    if args.all_levels:
        from app.core.config.textbook import TEXTBOOK_RAW_DIR
        index_dir = TEXTBOOK_RAW_DIR / "hsk_textbook_index"
        levels = sorted(
            int(p.stem) for p in index_dir.glob("*.pdf") if p.stem.isdigit()
        ) if index_dir.exists() else []
        if not levels:
            print(f"❌ No HSK level PDFs found under {index_dir}.", file=sys.stderr)
            sys.exit(1)
    else:
        levels = [args.hsk_level]

    all_failed = {}
    for level in levels:
        failed = run_pipeline_for_level(level, args)
        if failed:
            all_failed[level] = failed
            if failed == ["prerequisite_check"]:
                if args.all_levels:
                    print(f"⛔ Stopping --all-levels run: HSK{level} prerequisites not met.", file=sys.stderr)
            else:
                print(f"⛔ Stopping run: Pipeline aborted due to failure in HSK{level}.", file=sys.stderr)
            # Break immediately to stop processing any subsequent levels
            break

    if all_failed:
        print(f"\n❌ Pipeline completed with errors:", file=sys.stderr)
        for level, scripts in all_failed.items():
            print(f"  HSK{level}: {', '.join(scripts)}", file=sys.stderr)
        if not args.no_stats:
            print_stats()
        sys.exit(1)
    else:
        print("\n✅ Pipeline completed successfully!")
        if not args.no_stats:
            print_stats()


if __name__ == "__main__":
    main()