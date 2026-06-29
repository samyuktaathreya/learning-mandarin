"""
Main data pipeline orchestrator.

Runs all four pipeline steps in order:
  1. parse_textbook.py          — OCR + JSON structuring of the textbook PDF
  2. fill_template_sentences.py — fills exercise template sentences via Claude
  3. create_questions.py        — generates the full question bank
  4. build_dictionary.py        — builds the flat word lookup dictionary

Run everything from scratch:
  python3 main.py

Or import individual steps to run selectively:
  from main import run_fill_templates, run_create_questions
  run_fill_templates()
  run_create_questions()
"""

import sys
import traceback


def run_parse_textbook():
    from parse_textbook import main
    print("=" * 50)
    print("STEP 1: OCR + JSON structuring")
    print("=" * 50)
    main()


def run_fill_templates():
    from fill_template_sentences import main
    print("=" * 50)
    print("STEP 2: Fill template sentences")
    print("=" * 50)
    main()


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
    steps = [
        ("parse_textbook",          run_parse_textbook),
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
            if name == "parse_textbook":
                print("Continuing with remaining steps using existing units_output.json...")
            else:
                print("Continuing with remaining steps...")
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