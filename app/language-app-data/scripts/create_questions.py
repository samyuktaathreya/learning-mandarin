"""
Reads data/clean/units_output.json (vocab/grammar/sentences/fill_in_the_blank
per unit, produced by parse_textbook.py) and generates the full practice
question bank, written to data/clean/unit_questions_hsk1.json.

Question generation per source item:
  - vocab item        -> listening vocab, speaking vocab, translate english word
                         to chinese, translate chinese word to english,
                         transcribe word to pinyin
  - proper noun item  -> listening vocab, speaking vocab, transcribe word to pinyin
                         (no translate english->chinese since drilling that direction
                         for proper nouns is not useful)
  - sentence          -> listening sentence, speaking sentence, translate english
                         sentence to chinese, translate chinese sentence to english
  - fill_in_the_blank -> reconstructed into its original full sentence (blank
                         replaced with the answer, trailing English translation
                         stripped) for tagging purposes, then passed through

Every question is tagged with: every vocab/grammar/proper noun item from this
unit that appears in the source text, plus a question-type tag and a unit tag.
"""

import os
import json
from enum import Enum

# --------------------------------- QUESTION TYPES ---------------------------------

class QuestionType(str, Enum):
    FILL_IN_THE_BLANK = "fill in the blank"
    LISTENING_VOCAB = "listening vocab"
    LISTENING_SENTENCE = "listening sentence"
    SPEAKING_VOCAB = "speaking vocab"
    SPEAKING_SENTENCE = "speaking sentence"
    TRANSLATE_EN_TO_ZH_SENTENCE = "translate english sentence to chinese"
    TRANSLATE_ZH_TO_EN_SENTENCE = "translate chinese sentence to english"
    TRANSLATE_EN_TO_ZH_WORD = "translate english word to chinese"
    TRANSLATE_ZH_TO_EN_WORD = "translate chinese word to english"
    TRANSCRIBE_WORD_TO_PINYIN = "transcribe word to pinyin"

# --------------------------------- CONSTANTS ---------------------------------

TESTING = False

INPUT_FILEPATH = "../data/clean"
INPUT_FILENAME = "units_output.json"

if TESTING:
    OUTPUT_FILEPATH = "../test_data"
    OUTPUT_FILENAME = "test_unit_questions_hsk1.json"
else:
    OUTPUT_FILEPATH = "../data/clean"
    OUTPUT_FILENAME = "unit_questions_hsk1.json"

# --------------------------------- HELPERS ---------------------------------

def load_units_output(filepath: str, filename: str) -> dict:
    full_path = os.path.join(filepath, filename)
    with open(full_path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_questions(questions_dict: dict, filepath: str, filename: str):
    full_path = os.path.join(filepath, filename)
    with open(full_path, "w", encoding="utf-8") as f:
        json.dump(questions_dict, f, ensure_ascii=False, indent=2)


def make_question(unit_number: str, question_type: QuestionType, question_text: str,
                   answer_text: str, tags: list, counters: dict) -> dict:
    slug = question_type.value.replace(" ", "_")
    counters[slug] = counters.get(slug, 0) + 1
    n = counters[slug]

    return {
        "id": f"u{unit_number}_{slug}_{n}",
        "question_type": question_type.value,
        "question": question_text,
        "answer": answer_text,
        "tags": tags,
    }


def build_tags(hanzi_text: str, question_type: QuestionType, unit_number: str,
               vocab_hanzi_set: set, grammar_marker_set: set, proper_noun_set: set) -> list:
    """
    Tags = every vocab/grammar/proper noun item from this unit that appears
    in the source text + a question-type tag + a unit tag.
    """
    tags = []

    for word in vocab_hanzi_set | grammar_marker_set | proper_noun_set:
        if word in hanzi_text and word not in tags:
            tags.append(word)

    tags.append(question_type.value.replace(" ", "_"))
    tags.append(f"unit_{unit_number}")

    return tags


def reconstruct_fitb_sentence(question: str, answer: str) -> str:
    paren_index = question.rfind("(")
    core = question[:paren_index].strip() if paren_index != -1 else question.strip()
    return core.replace("___", answer)


def generate_questions_for_unit(unit_number: str, unit_data: dict) -> list:
    questions = []
    counters = {}

    vocab_list = unit_data.get("vocab", [])
    proper_noun_list = unit_data.get("proper_nouns", [])
    sentence_list = unit_data.get("sentences", [])
    grammar_list = unit_data.get("grammar", [])
    fitb_list = unit_data.get("fill_in_the_blank", [])

    vocab_hanzi_set = {item["hanzi"] for item in vocab_list}
    grammar_marker_set = {item["marker"] for item in grammar_list}
    proper_noun_set = {item["hanzi"] for item in proper_noun_list}

    # --- vocab-derived questions ---
    for item in vocab_list:
        hanzi = item["hanzi"]
        pinyin = item["pinyin"]
        english = item["english"]

        for qtype, q_text, a_text in [
            (QuestionType.LISTENING_VOCAB, hanzi, pinyin),
            (QuestionType.SPEAKING_VOCAB, hanzi, pinyin),
            (QuestionType.TRANSLATE_EN_TO_ZH_WORD, english, hanzi),
            (QuestionType.TRANSLATE_ZH_TO_EN_WORD, hanzi, english),
            (QuestionType.TRANSCRIBE_WORD_TO_PINYIN, hanzi, pinyin),
        ]:
            tags = build_tags(hanzi, qtype, unit_number, vocab_hanzi_set, grammar_marker_set, proper_noun_set)
            questions.append(make_question(unit_number, qtype, q_text, a_text, tags, counters))

    # --- proper noun-derived questions ---
    # listening, speaking, transcribe only — no translate english->chinese
    for item in proper_noun_list:
        hanzi = item["hanzi"]
        pinyin = item["pinyin"]
        english = item["english"]

        for qtype, q_text, a_text in [
            (QuestionType.LISTENING_VOCAB, hanzi, pinyin),
            (QuestionType.SPEAKING_VOCAB, hanzi, pinyin),
            (QuestionType.TRANSCRIBE_WORD_TO_PINYIN, hanzi, pinyin),
        ]:
            tags = build_tags(hanzi, qtype, unit_number, vocab_hanzi_set, grammar_marker_set, proper_noun_set)
            questions.append(make_question(unit_number, qtype, q_text, a_text, tags, counters))

    # --- sentence-derived questions ---
    seen_sentences = set()
    deduped_sentences = []
    for item in sentence_list:
        if item["hanzi"] not in seen_sentences:
            seen_sentences.add(item["hanzi"])
            deduped_sentences.append(item)

    for item in deduped_sentences:
        hanzi = item["hanzi"]
        pinyin = item["pinyin"]
        english = item["english"]

        for qtype, q_text, a_text in [
            (QuestionType.LISTENING_SENTENCE, hanzi, hanzi),
            (QuestionType.SPEAKING_SENTENCE, hanzi, pinyin),
            (QuestionType.TRANSLATE_EN_TO_ZH_SENTENCE, english, hanzi),
            (QuestionType.TRANSLATE_ZH_TO_EN_SENTENCE, hanzi, english),
        ]:
            tags = build_tags(hanzi, qtype, unit_number, vocab_hanzi_set, grammar_marker_set, proper_noun_set)
            questions.append(make_question(unit_number, qtype, q_text, a_text, tags, counters))

    # --- fill in the blank ---
    seen_fitb = set()
    for item in fitb_list:
        key = (item["question"], item["answer"])
        if key in seen_fitb:
            continue
        seen_fitb.add(key)

        full_sentence = reconstruct_fitb_sentence(item["question"], item["answer"])
        tags = build_tags(full_sentence, QuestionType.FILL_IN_THE_BLANK, unit_number, vocab_hanzi_set, grammar_marker_set, proper_noun_set)

        questions.append(make_question(
            unit_number, QuestionType.FILL_IN_THE_BLANK, item["question"], item["answer"], tags, counters
        ))

    return questions


# --------------------------------- MAIN ---------------------------------

def main():
    units_output = load_units_output(INPUT_FILEPATH, INPUT_FILENAME)
    all_questions = {}

    if TESTING:
        for unit_number, unit_data in units_output.items():
            questions = generate_questions_for_unit(unit_number, unit_data)
            all_questions[unit_number] = questions
            print(f"Unit {unit_number}: {len(questions)} questions generated")
            break
    else:
        for unit_number, unit_data in units_output.items():
            questions = generate_questions_for_unit(unit_number, unit_data)
            all_questions[unit_number] = questions
            print(f"Unit {unit_number}: {len(questions)} questions generated")

    save_questions(all_questions, OUTPUT_FILEPATH, OUTPUT_FILENAME)
    print(f"\nWritten to {os.path.join(OUTPUT_FILEPATH, OUTPUT_FILENAME)}")


if __name__ == "__main__":
    main()