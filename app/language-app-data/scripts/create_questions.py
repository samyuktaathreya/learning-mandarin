"""
Reads index_output.json (vocab/grammar/proper_nouns) and units_output.json
(sentences/fill_in_the_blank) and generates the full question bank,
written to data/clean/unit_questions_hsk1.json.

Source of truth for each question type:
  index_output.json  → vocab, grammar, proper_noun questions
  units_output.json  → sentence and fill-in-the-blank questions
"""

import os
import json
import re
from enum import Enum
from collections import defaultdict

class QuestionType(str, Enum):
    FILL_IN_THE_BLANK           = "fill in the blank"
    LISTENING_VOCAB             = "listening vocab"
    LISTENING_SENTENCE          = "listening sentence"
    SPEAKING_VOCAB              = "speaking vocab"
    SPEAKING_SENTENCE           = "speaking sentence"
    TRANSLATE_EN_TO_ZH_SENTENCE = "translate english sentence to chinese"
    TRANSLATE_ZH_TO_EN_SENTENCE = "translate chinese sentence to english"
    TRANSLATE_EN_TO_ZH_WORD     = "translate english word to chinese"
    TRANSLATE_ZH_TO_EN_WORD     = "translate chinese word to english"
    TRANSCRIBE_WORD_TO_PINYIN   = "transcribe word to pinyin"

TESTING = False

INDEX_FILEPATH    = "../data/clean"
INDEX_FILENAME    = "index_output.json"
UNITS_FILEPATH    = "../data/clean"
UNITS_FILENAME    = "units_output.json"
BLOCKLIST_FILEPATH = "../remove_these_sentences"
BLOCKLIST_FILENAME = "remove_these_sentences.txt"

if TESTING:
    OUTPUT_FILEPATH = "../test_data"
    OUTPUT_FILENAME = "test_unit_questions_hsk1.json"
else:
    OUTPUT_FILEPATH = "../data/clean"
    OUTPUT_FILENAME = "unit_questions_hsk1.json"


def load_json(filepath, filename):
    with open(os.path.join(filepath, filename), "r", encoding="utf-8") as f:
        return json.load(f)


def normalize(text: str) -> str:
    return re.sub(r'[。？！，、；：""\'\'…\.\?\!,]', '', text).strip()


def load_blocklist() -> set:
    path = os.path.join(BLOCKLIST_FILEPATH, BLOCKLIST_FILENAME)
    if not os.path.exists(path):
        return set()
    with open(path, "r", encoding="utf-8") as f:
        lines = [normalize(line.strip()) for line in f if line.strip()]
    print(f"Loaded blocklist: {len(lines)} sentence(s) to exclude.")
    return set(lines)


def save_questions(questions_dict, filepath, filename):
    with open(os.path.join(filepath, filename), "w", encoding="utf-8") as f:
        json.dump(questions_dict, f, ensure_ascii=False, indent=2)


def make_question(unit_number, question_type, question_text, answer_text, tags, counters):
    slug = question_type.value.replace(" ", "_")
    counters[slug] = counters.get(slug, 0) + 1
    return {
        "id": f"u{unit_number}_{slug}_{counters[slug]}",
        "question_type": question_type.value,
        "question": question_text,
        "answer": answer_text,
        "tags": tags,
        "unit": int(unit_number),
    }


def build_tags(hanzi_text, question_type, unit_number, all_hanzi):
    """Tag with any known word that appears in the source text."""
    tags = [w for w in all_hanzi if w in hanzi_text]
    tags.append(question_type.value.replace(" ", "_"))
    tags.append(f"unit_{unit_number}")
    return tags


def reconstruct_fitb_sentence(question, answer):
    paren_index = question.rfind("(")
    core = question[:paren_index].strip() if paren_index != -1 else question.strip()
    return core.replace("___", answer)


def main():
    index       = load_json(INDEX_FILEPATH, INDEX_FILENAME)
    units_data  = load_json(UNITS_FILEPATH, UNITS_FILENAME)
    blocklist   = load_blocklist()

    all_vocab        = index.get("vocab", [])
    all_grammar      = index.get("grammar", [])
    all_proper_nouns = index.get("proper_nouns", [])

    # group by unit
    vocab_by_unit   = defaultdict(list)
    grammar_by_unit = defaultdict(list)
    proper_by_unit  = defaultdict(list)

    for item in all_vocab:
        vocab_by_unit[item["unit"]].append(item)
    for item in all_grammar:
        grammar_by_unit[item["unit"]].append(item)
    for item in all_proper_nouns:
        proper_by_unit[item["unit"]].append(item)

    # collect all known hanzi for tagging
    all_hanzi = (
        [v["hanzi"] for v in all_vocab] +
        [g["hanzi"] for g in all_grammar] +
        [p["hanzi"] for p in all_proper_nouns]
    )
    # sort longest first so multi-char words match before single chars
    all_hanzi.sort(key=len, reverse=True)

    # get all unit numbers from both sources
    all_units = sorted(set(
        list(vocab_by_unit.keys()) +
        list(grammar_by_unit.keys()) +
        list(proper_by_unit.keys()) +
        [int(k) for k in units_data.keys()]
    ))

    all_questions = {}

    for unit_number in all_units:
        unit_str = str(unit_number)
        counters = {}
        questions = []

        unit_vocab   = vocab_by_unit.get(unit_number, [])
        unit_grammar = grammar_by_unit.get(unit_number, [])
        unit_proper  = proper_by_unit.get(unit_number, [])
        unit_data    = units_data.get(unit_str, {})

        # --- vocab questions ---
        for item in unit_vocab:
            hanzi   = item["hanzi"]
            pinyin  = item["pinyin"]
            english = item["english"]
            tags = build_tags(hanzi, QuestionType.LISTENING_VOCAB, unit_str, all_hanzi)

            for qtype, q_text, a_text in [
                (QuestionType.LISTENING_VOCAB,         hanzi,   pinyin),
                (QuestionType.SPEAKING_VOCAB,          hanzi,   pinyin),
                (QuestionType.TRANSLATE_EN_TO_ZH_WORD, english, hanzi),
                (QuestionType.TRANSLATE_ZH_TO_EN_WORD, hanzi,   english),
                (QuestionType.TRANSCRIBE_WORD_TO_PINYIN, hanzi, pinyin),
            ]:
                tags = build_tags(hanzi, qtype, unit_str, all_hanzi)
                questions.append(make_question(unit_str, qtype, q_text, a_text, tags, counters))

        # --- grammar questions (listening, speaking, transcribe only) ---
        for item in unit_grammar:
            hanzi   = item["hanzi"]
            pinyin  = item["pinyin"]
            tags = build_tags(hanzi, QuestionType.LISTENING_VOCAB, unit_str, all_hanzi)

            for qtype, q_text, a_text in [
                (QuestionType.LISTENING_VOCAB,           hanzi, pinyin),
                (QuestionType.SPEAKING_VOCAB,            hanzi, pinyin),
                (QuestionType.TRANSCRIBE_WORD_TO_PINYIN, hanzi, pinyin),
            ]:
                tags = build_tags(hanzi, qtype, unit_str, all_hanzi)
                questions.append(make_question(unit_str, qtype, q_text, a_text, tags, counters))

        # --- proper noun questions (listening, speaking, transcribe only) ---
        for item in unit_proper:
            hanzi   = item["hanzi"]
            pinyin  = item["pinyin"]
            tags = build_tags(hanzi, QuestionType.LISTENING_VOCAB, unit_str, all_hanzi)

            for qtype, q_text, a_text in [
                (QuestionType.LISTENING_VOCAB,           hanzi, pinyin),
                (QuestionType.SPEAKING_VOCAB,            hanzi, pinyin),
                (QuestionType.TRANSCRIBE_WORD_TO_PINYIN, hanzi, pinyin),
            ]:
                tags = build_tags(hanzi, qtype, unit_str, all_hanzi)
                questions.append(make_question(unit_str, qtype, q_text, a_text, tags, counters))

        # --- sentence questions ---
        seen_sentences = set()
        for item in unit_data.get("sentences", []):
            hanzi   = item["hanzi"]
            pinyin  = item["pinyin"]
            english = item["english"]

            if hanzi in seen_sentences:
                continue
            if normalize(hanzi) in blocklist:
                print(f"  Skipping blocked sentence: {hanzi}")
                continue
            seen_sentences.add(hanzi)

            for qtype, q_text, a_text in [
                (QuestionType.LISTENING_SENTENCE,          hanzi,   hanzi),
                (QuestionType.SPEAKING_SENTENCE,           hanzi,   pinyin),
                (QuestionType.TRANSLATE_EN_TO_ZH_SENTENCE, english, hanzi),
                (QuestionType.TRANSLATE_ZH_TO_EN_SENTENCE, hanzi,   english),
            ]:
                tags = build_tags(hanzi, qtype, unit_str, all_hanzi)
                questions.append(make_question(unit_str, qtype, q_text, a_text, tags, counters))

        # --- fill in the blank ---
        seen_fitb = set()
        for item in unit_data.get("fill_in_the_blank", []):
            key = (item["question"], item["answer"])
            if key in seen_fitb:
                continue
            seen_fitb.add(key)

            full_sentence = reconstruct_fitb_sentence(item["question"], item["answer"])
            if normalize(full_sentence) in blocklist:
                print(f"  Skipping blocked FITB: {full_sentence}")
                continue

            tags = build_tags(full_sentence, QuestionType.FILL_IN_THE_BLANK, unit_str, all_hanzi)
            questions.append(make_question(
                unit_str, QuestionType.FILL_IN_THE_BLANK,
                item["question"], item["answer"], tags, counters
            ))

        all_questions[unit_str] = questions
        print(f"Unit {unit_number}: {len(questions)} questions generated")

    save_questions(all_questions, OUTPUT_FILEPATH, OUTPUT_FILENAME)
    print(f"\nWritten to {os.path.join(OUTPUT_FILEPATH, OUTPUT_FILENAME)}")


if __name__ == "__main__":
    main()