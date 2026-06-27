"""
Reads data/clean/units_output.json and generates the full practice
question bank, written to data/clean/unit_questions_hsk1.json.

Question generation per source item:
  - vocab item        -> listening vocab, speaking vocab, translate english word
                         to chinese, translate chinese word to english,
                         transcribe word to pinyin
  - grammar marker    -> listening vocab, speaking vocab, transcribe word to pinyin
                         (no translation questions — grammar markers aren't translated,
                         they have descriptions shown in the character popup instead)
  - proper noun item  -> listening vocab, speaking vocab, transcribe word to pinyin
  - sentence          -> listening sentence, speaking sentence, translate english
                         sentence to chinese, translate chinese sentence to english
  - fill_in_the_blank -> passed through as-is
"""

import os
import json
from enum import Enum

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

TESTING = False

INPUT_FILEPATH = "../data/clean"
INPUT_FILENAME = "units_output.json"

if TESTING:
    OUTPUT_FILEPATH = "../test_data"
    OUTPUT_FILENAME = "test_unit_questions_hsk1.json"
else:
    OUTPUT_FILEPATH = "../data/clean"
    OUTPUT_FILENAME = "unit_questions_hsk1.json"


def load_units_output(filepath, filename):
    with open(os.path.join(filepath, filename), 'r', encoding='utf-8') as f:
        return json.load(f)


def save_questions(questions_dict, filepath, filename):
    with open(os.path.join(filepath, filename), 'w', encoding='utf-8') as f:
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
    }


def build_tags(hanzi_text, question_type, unit_number, vocab_hanzi_set, grammar_marker_set, proper_noun_set):
    tags = []
    for word in vocab_hanzi_set | grammar_marker_set | proper_noun_set:
        if word in hanzi_text and word not in tags:
            tags.append(word)
    tags.append(question_type.value.replace(" ", "_"))
    tags.append(f"unit_{unit_number}")
    return tags


def reconstruct_fitb_sentence(question, answer):
    paren_index = question.rfind("(")
    core = question[:paren_index].strip() if paren_index != -1 else question.strip()
    return core.replace("___", answer)


def generate_questions_for_unit(unit_number, unit_data):
    questions = []
    counters = {}

    vocab_list = unit_data.get("vocab", [])
    grammar_list = unit_data.get("grammar", [])
    proper_noun_list = unit_data.get("proper_nouns", [])
    sentence_list = unit_data.get("sentences", [])
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

    # --- grammar marker questions (listening, speaking, transcribe only) ---
    for item in grammar_list:
        marker = item["marker"]
        pinyin = item.get("pinyin")
        if not pinyin:
            continue  # skip if no pinyin (run add_grammar_pinyin.py first)
        for qtype, q_text, a_text in [
            (QuestionType.LISTENING_VOCAB, marker, pinyin),
            (QuestionType.SPEAKING_VOCAB, marker, pinyin),
            (QuestionType.TRANSCRIBE_WORD_TO_PINYIN, marker, pinyin),
        ]:
            tags = build_tags(marker, qtype, unit_number, vocab_hanzi_set, grammar_marker_set, proper_noun_set)
            questions.append(make_question(unit_number, qtype, q_text, a_text, tags, counters))

    # --- proper noun questions ---
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

    # --- sentence questions ---
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
        questions.append(make_question(unit_number, QuestionType.FILL_IN_THE_BLANK, item["question"], item["answer"], tags, counters))

    return questions


def main():
    units_output = load_units_output(INPUT_FILEPATH, INPUT_FILENAME)
    all_questions = {}

    units_to_process = list(units_output.items())
    if TESTING:
        units_to_process = units_to_process[:1]

    for unit_number, unit_data in units_to_process:
        questions = generate_questions_for_unit(unit_number, unit_data)
        all_questions[unit_number] = questions
        print(f"Unit {unit_number}: {len(questions)} questions generated")

    save_questions(all_questions, OUTPUT_FILEPATH, OUTPUT_FILENAME)
    print(f"\nWritten to {os.path.join(OUTPUT_FILEPATH, OUTPUT_FILENAME)}")


if __name__ == "__main__":
    main()