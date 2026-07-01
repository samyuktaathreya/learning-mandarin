"""
Main data pipeline orchestrator.

Steps:
  0. parse_index.py             — OCR the vocabulary index → index_output.json
  1. parse_textbook.py          — OCR each unit → sentences + fill-in-the-blank
  2. fill_template_sentences.py — fills exercise template sentences via Claude
  3. create_questions.py        — generates the full question bank
  4. build_dictionary.py        — builds the flat word lookup dictionary

Run everything from scratch:
  python3 main.py

Run only specific units (steps 1 and 2 only):
  Set UNITS_TO_PROCESS = [3, 4, 5] below.
  Set to None to process all units.

Skip the index step if index_output.json already exists:
  Set SKIP_INDEX = True below.
"""

import sys
import traceback

UNITS_TO_PROCESS = [3,4,5]   # e.g. [3, 4, 5] or None for all
SKIP_INDEX = False        # set True to skip parse_index.py if already done


def run_parse_index():
    from parse_index import main
    print("=" * 50)
    print("STEP 0: Parse vocabulary index")
    print("=" * 50)
    main()


def run_parse_textbook():
    import parse_textbook
    parse_textbook.UNITS_TO_PROCESS = UNITS_TO_PROCESS
    print("=" * 50)
    print("STEP 1: OCR + sentence extraction")
    if UNITS_TO_PROCESS:
        print(f"  (units: {UNITS_TO_PROCESS})")
    print("=" * 50)
    parse_textbook.main()


def run_validate():
    from validate_units import main
    print("=" * 50)
    print("STEP 1b: Validate + clean extracted data")
    print("=" * 50)
    main()


def run_fill_templates():
    import fill_template_sentences
    fill_template_sentences.UNITS_TO_PROCESS = UNITS_TO_PROCESS
    print("=" * 50)
    print("STEP 2: Fill template sentences")
    if UNITS_TO_PROCESS:
        print(f"  (units: {UNITS_TO_PROCESS})")
    print("=" * 50)
    fill_template_sentences.main()


def run_create_questions():
    from create_questions import main
    print("=" * 50)
    print("STEP 3: Generate question bank")
    print("=" * 50)
    main()


def run_build_dictionary():
    from build_dictionary import main
    print("=" * 50)
    print("STEP 4: Build dictionary")
    print("=" * 50)
    main()


def main():
    steps = []

    if not SKIP_INDEX:
        steps.append(("parse_index", run_parse_index))

    steps += [
        ("parse_textbook",          run_parse_textbook),
        ("validate",                run_validate),
        ("fill_template_sentences", run_fill_templates),
        ("create_questions",        run_create_questions),
        ("build_dictionary",        run_build_dictionary),
    ]

    failed = []

    for name, fn in steps:
        try:
            fn()
            print()
        except Exception as e:
            print(f"\n!! Step '{name}' FAILED: {e}")
            traceback.print_exc()
            failed.append(name)
            if name in ("parse_index", "parse_textbook"):
                print("Continuing with remaining steps using existing JSON files...")
            print()

    print("=" * 50)
    if failed:
        print(f"Pipeline complete with errors in: {', '.join(failed)}")
        sys.exit(1)
    else:
        print("Pipeline complete. All steps succeeded.")
        print("\nNext: delete mandarin_app.db and restart uvicorn to pick up new data.")


if __name__ == "__main__":
    main()