"""
Removes blocked sentences from unit_questions_hsk1.json without regenerating
the entire question bank.

Add sentences to remove to:
  ../remove_these_sentences/remove_these_sentences.txt
(one sentence per line, punctuation is normalized before matching)

Then run:
  python3 apply_blocklist.py

This is faster than rerunning create_questions.py when you just want to
remove a few bad sentences during user testing.
"""

import os
import json
import re

QUESTIONS_FILEPATH = "../data/clean"
QUESTIONS_FILENAME = "unit_questions_hsk1.json"
BLOCKLIST_FILEPATH = "../remove_these_sentences"
BLOCKLIST_FILENAME = "remove_these_sentences.txt"

SENTENCE_QUESTION_TYPES = {
    "listening sentence",
    "speaking sentence",
    "translate english sentence to chinese",
    "translate chinese sentence to english",
}


def normalize(text: str) -> str:
    return re.sub(r'[。？！，、；：""''…\.\?\!,]', '', text).strip()


def load_blocklist() -> set:
    path = os.path.join(BLOCKLIST_FILEPATH, BLOCKLIST_FILENAME)
    if not os.path.exists(path):
        print(f"No blocklist found at {path}")
        return set()
    with open(path, 'r', encoding='utf-8') as f:
        lines = [normalize(line.strip()) for line in f if line.strip()]
    print(f"Loaded {len(lines)} blocked sentence(s).")
    return set(lines)


def main():
    blocklist = load_blocklist()
    if not blocklist:
        print("Nothing to do.")
        return

    path = os.path.join(QUESTIONS_FILEPATH, QUESTIONS_FILENAME)
    with open(path, 'r', encoding='utf-8') as f:
        questions = json.load(f)

    total_removed = 0

    for unit_number, unit_questions in questions.items():
        before = len(unit_questions)
        filtered = []
        for q in unit_questions:
            if q["question_type"] in SENTENCE_QUESTION_TYPES:
                # check both question and answer against blocklist
                if normalize(q["question"]) in blocklist or normalize(q["answer"]) in blocklist:
                    print(f"  Unit {unit_number}: removing [{q['question_type']}] {q['question']}")
                    continue
            filtered.append(q)

        removed = before - len(filtered)
        total_removed += removed
        questions[unit_number] = filtered

    with open(path, 'w', encoding='utf-8') as f:
        json.dump(questions, f, ensure_ascii=False, indent=2)

    print(f"\nDone. Removed {total_removed} question(s) from {QUESTIONS_FILENAME}.")


if __name__ == "__main__":
    main()