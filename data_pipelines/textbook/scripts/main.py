"""
SQL-based textbook data pipeline runner.

Replaces the JSON-file pipeline with direct-to-database writes:
  1. vocab_index_parser.py        → Vocab rows
  2. sentence_parser.py           → Sentence + SentenceVocab + FitbQuestion rows
  3. extract_and_match_grammar.py → GrammarTip + SentenceGrammar rows
  4. create_questions.py          → Question rows
  5. sync_index_definitions.py    → Repair/fill vocab definitions

Each script is idempotent: re-running doesn't duplicate data, and selective
reprocessing (e.g., only unit 3) updates that unit's data in-place without
losing prior work.

Usage:
    python main.py                          # Run full pipeline
    python main.py --vocab-only             # Skip past vocab_index_parser
    python main.py --from-sentences         # Start from sentence_parser (vocab already done)
    python main.py --units 3 4 5            # Selective: only reprocess units 3, 4, 5 in sentence_parser
    python main.py --sources textbook       # Only process textbook (not workbook)
    python main.py --hsk-level 2            # Process HSK2's raw PDFs/units instead of HSK1 (default)
"""

import json
import os
import sys
import argparse
from collections import defaultdict
from pathlib import Path
from typing import Optional

from app.core.config.textbook import PIPELINE_SCRIPTS_DIR

# Import the DB module to initialize and query
from app.textbook.db_utils import init_db, get_session
from app.textbook.models import Unit, Vocab, Sentence, FitbQuestion, Question


def run_script(script_name: str, env_overrides: dict = None) -> bool:
    """Run a pipeline script as a subprocess.
    
    Args:
        script_name: Name of the script (e.g., "vocab_index_parser")
        env_overrides: Dict of environment variable overrides for the script
            (e.g., {"UNITS_TO_PROCESS": "3,4,5", "HSK_LEVEL": "2"})
    
    Returns:
        True if successful, False if failed (errors logged to stderr).
    """
    script_path = PIPELINE_SCRIPTS_DIR / f"{script_name}.py"
    if not script_path.exists():
        print(f"❌ Script not found: {script_path}", file=sys.stderr)
        return False
    
    env = os.environ.copy()
    if env_overrides:
        env.update(env_overrides)

    # NOTE: previously this called os.system(f"python3 {script_path}") without
    # ever passing `env` through, so env_overrides (UNITS_TO_PROCESS,
    # SOURCES_TO_PROCESS, and now HSK_LEVEL) were silently ignored by every
    # subprocess run -- switched to subprocess.run(..., env=env) so overrides
    # actually reach the child script.
    import subprocess
    result = subprocess.run([sys.executable, str(script_path)], env=env)
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
            sentences_count = db.query(Sentence).count()
            fitb_count = db.query(FitbQuestion).count()
            questions_count = db.query(Question).count()
            
            print("\n" + "=" * 50)
            print("📊 DATABASE SUMMARY")
            print("=" * 50)
            print(f"  Units:       {units_count}")
            print(f"  Vocab:       {vocab_count}")
            print(f"  Sentences:   {sentences_count}")
            print(f"  FITB Qs:     {fitb_count}")
            print(f"  Questions:   {questions_count}")

            # Units per hsk_level -- flat totals above can't distinguish an
            # HSK1 gap from an HSK2 gap once more than one level is loaded
            # (see migration doc section 7, item 5).
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


def main():
    parser = argparse.ArgumentParser(
        description="Run the SQL-based textbook data pipeline.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python main.py                          # Full pipeline
  python main.py --vocab-only             # Stop after vocab_index_parser
  python main.py --from-sentences         # Start from sentence_parser
  python main.py --units 3 4 5            # Reprocess only units 3, 4, 5
  python main.py --sources textbook       # Only process textbook PDFs
  python main.py --units 3 4 --sources workbook # Units 3, 4 from workbook only
  python main.py --hsk-level 2                  # Full pipeline for HSK2 raw PDFs
        """
    )
    
    parser.add_argument(
        "--vocab-only",
        action="store_true",
        help="Run only vocab_index_parser and exit (skip sentences, grammar, questions)."
    )
    parser.add_argument(
        "--from-sentences",
        action="store_true",
        help="Start from sentence_parser (vocab_index_parser already done)."
    )
    parser.add_argument(
        "--from-grammar",
        action="store_true",
        help="Start from extract_and_match_grammar (vocab and sentences already done)."
    )
    parser.add_argument(
        "--from-questions",
        action="store_true",
        help="Start from create_questions (vocab, sentences, grammar already done)."
    )
    parser.add_argument(
        "--from-sync",
        action="store_true",
        help="Run only sync_index_definitions (all other scripts already done)."
    )
    parser.add_argument(
        "--hsk-level",
        type=int,
        default=1,
        help="HSK level to process (e.g., --hsk-level 2). Defaults to 1 to "
             "match prior behavior. Threaded to every pipeline script via the "
             "HSK_LEVEL env var, since raw PDFs, OCR caches, and every Unit "
             "lookup/upsert are now keyed on (unit_number, hsk_level)."
    )
    parser.add_argument(
        "--units",
        nargs="+",
        type=int,
        help="Selective reprocessing: only process these unit numbers "
             "(e.g., --units 3 4 5). Passed to sentence_parser."
    )
    parser.add_argument(
        "--sources",
        nargs="+",
        choices=["textbook", "workbook"],
        help="Selective reprocessing: only process these sources "
             "(e.g., --sources textbook). Passed to sentence_parser."
    )
    parser.add_argument(
        "--no-stats",
        action="store_true",
        help="Skip printing database statistics at the end."
    )
    
    args = parser.parse_args()
    
    # Build the pipeline order
    pipeline = [
        "vocab_index_parser",
        "sentence_parser",
        "extract_and_match_grammar",
        "create_questions",
        "append_orphan_tags",
    ]
    
    # Determine which scripts to run based on flags
    if args.vocab_only:
        pipeline = ["vocab_index_parser"]
    elif args.from_sentences:
        pipeline = pipeline[1:]  # skip vocab_index_parser
    elif args.from_grammar:
        pipeline = pipeline[2:]  # skip vocab_index_parser, sentence_parser
    elif args.from_questions:
        pipeline = pipeline[3:]  # skip up to create_questions
    elif args.from_sync:
        pipeline = ["sync_index_definitions"]  # only sync
    
    # HSK_LEVEL is threaded to EVERY script (raw PDF/OCR paths and every Unit
    # lookup/upsert now depend on it) -- unlike UNITS_TO_PROCESS/SOURCES_TO_PROCESS,
    # which stay sentence_parser-only.
    common_env_overrides = {"HSK_LEVEL": str(args.hsk_level)}

    # Prepare additional environment overrides for sentence_parser
    sentence_parser_env_overrides = {}
    if args.units:
        sentence_parser_env_overrides["UNITS_TO_PROCESS"] = ",".join(str(u) for u in args.units)
    if args.sources:
        sentence_parser_env_overrides["SOURCES_TO_PROCESS"] = ",".join(args.sources)
    
    print(f"🎯 HSK level: {args.hsk_level}")

    # Initialize the database (all scripts call init_db themselves, but explicit
    # init here ensures tables exist before we start)
    print("🗄️  Initializing database...")
    try:
        init_db()
        print("✓ Database initialized\n")
    except Exception as e:
        print(f"❌ Failed to initialize database: {e}", file=sys.stderr)
        sys.exit(1)
    
    # Run the pipeline
    failed_scripts = []
    for i, script_name in enumerate(pipeline, start=1):
        print(f"[{i}/{len(pipeline)}] Running {script_name}...")
        
        script_env = dict(common_env_overrides)
        if script_name == "sentence_parser":
            script_env.update(sentence_parser_env_overrides)
        if not run_script(script_name, script_env):
            failed_scripts.append(script_name)
            # Continue to next script rather than failing hard, so partial progress is saved
            print(f"⚠️  Continuing despite failure...\n", file=sys.stderr)
        else:
            print(f"✓ {script_name} completed\n")
    
    # Summary
    if failed_scripts:
        print(f"\n❌ Pipeline completed with errors:", file=sys.stderr)
        for script in failed_scripts:
            print(f"  - {script}", file=sys.stderr)
        if not args.no_stats:
            print_stats()
        sys.exit(1)
    else:
        print("\n✅ Pipeline completed successfully!")
        if not args.no_stats:
            print_stats()


if __name__ == "__main__":
    main()