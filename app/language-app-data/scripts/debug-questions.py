import json
from pathlib import Path

DATA_PATH = Path(__file__).resolve().parent.parent / "data" / "clean" / "unit_questions_hsk1.json"


def main():
    with open(DATA_PATH, encoding="utf-8") as f:
        data = json.load(f)

    for unit in sorted(data, key=int):
        questions = data[unit]
        vocab_count = sum(1 for q in questions if q["question_type"] == "listening vocab")
        sentence_count = sum(1 for q in questions if q["question_type"] == "listening sentence")
        print(f"Unit {unit}: {vocab_count} vocab, {sentence_count} sentences")


if __name__ == "__main__":
    main()
