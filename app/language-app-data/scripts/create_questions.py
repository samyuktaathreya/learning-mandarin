"""
Generate the per-unit question bank from the cleaned vocab and sentence outputs.
"""

import json
import os
import re
from collections import defaultdict
from enum import Enum
from pathlib import Path


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

    @classmethod
    def values(cls):
        return [item.value for item in cls]


BASE_DIR = Path(__file__).resolve().parent.parent
INDEX_FILEPATH = BASE_DIR / "data" / "clean" / "index_output.json"
UNITS_FILEPATH = BASE_DIR / "data" / "clean" / "units_output.json"
LEGACY_UNITS_FILEPATH = BASE_DIR.parent / "language-app-data" / "data" / "clean" / "units_output.json"
OUTPUT_FILEPATH = BASE_DIR / "data" / "clean" / "unit_questions_hsk1.json"
BLOCKLIST_PATH = BASE_DIR / "remove_these_sentences" / "remove_these_sentences.txt"


def load_json(path: Path):
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def normalize(text: str) -> str:
    return re.sub(r"[。？！，、；：\"\'\.\?\!,]", "", text).strip()


def load_blocklist() -> set:
    if not BLOCKLIST_PATH.exists():
        return set()
    with open(BLOCKLIST_PATH, "r", encoding="utf-8") as fh:
        return {normalize(line.strip()) for line in fh if line.strip()}


def make_question(unit_number, question_type, question_text, answer_text, tags, counters):
    slug = question_type.replace(" ", "_")
    counters[slug] = counters.get(slug, 0) + 1
    return {
        "id": f"u{unit_number}_{slug}_{counters[slug]}",
        "question_type": question_type,
        "question": question_text,
        "answer": answer_text,
        "tags": tags,
        "unit": int(unit_number),
    }


def build_tags(hanzi_text, question_type, unit_number, all_hanzi):
    tags = [w for w in all_hanzi if w in hanzi_text]
    tags.append(question_type.replace(" ", "_"))
    tags.append(f"unit_{unit_number}")
    return tags


def reconstruct_fitb_sentence(question, answer):
    paren_index = question.rfind("(")
    core = question[:paren_index].strip() if paren_index != -1 else question.strip()
    return core.replace("___", answer)


def build_questions_for_unit(index_data, units_data, unit_number):
    unit_str = str(unit_number)
    counters = {}
    questions = []
    unit_data = units_data.get(unit_str, {})

    all_vocab = index_data.get("vocab", [])
    all_grammar = index_data.get("grammar", [])
    all_proper_nouns = index_data.get("proper_nouns", [])

    all_hanzi = [item["hanzi"] for item in all_vocab + all_grammar + all_proper_nouns]
    all_hanzi = sorted(set(all_hanzi), key=len, reverse=True)

    vocab_by_unit = defaultdict(list)
    grammar_by_unit = defaultdict(list)
    proper_by_unit = defaultdict(list)
    for item in all_vocab:
        vocab_by_unit[item["unit"]].append(item)
    for item in all_grammar:
        grammar_by_unit[item["unit"]].append(item)
    for item in all_proper_nouns:
        proper_by_unit[item["unit"]].append(item)

    for item in vocab_by_unit.get(unit_number, []):
        hanzi = item["hanzi"]
        pinyin = item.get("pinyin", "")
        english = item.get("english", "")
        for qtype, q_text, a_text in [
            (QuestionType.LISTENING_VOCAB.value, hanzi, pinyin),
            (QuestionType.SPEAKING_VOCAB.value, hanzi, pinyin),
            (QuestionType.TRANSLATE_EN_TO_ZH_WORD.value, english, hanzi),
            (QuestionType.TRANSLATE_ZH_TO_EN_WORD.value, hanzi, english),
            (QuestionType.TRANSCRIBE_WORD_TO_PINYIN.value, hanzi, pinyin),
        ]:
            questions.append(make_question(unit_str, qtype, q_text, a_text, build_tags(hanzi, qtype, unit_str, all_hanzi), counters))

    for item in grammar_by_unit.get(unit_number, []):
        hanzi = item["hanzi"]
        pinyin = item.get("pinyin", "")
        for qtype, q_text, a_text in [
            (QuestionType.LISTENING_VOCAB.value, hanzi, pinyin),
            (QuestionType.SPEAKING_VOCAB.value, hanzi, pinyin),
            (QuestionType.TRANSCRIBE_WORD_TO_PINYIN.value, hanzi, pinyin),
        ]:
            questions.append(make_question(unit_str, qtype, q_text, a_text, build_tags(hanzi, qtype, unit_str, all_hanzi), counters))

    for item in proper_by_unit.get(unit_number, []):
        hanzi = item["hanzi"]
        pinyin = item.get("pinyin", "")
        for qtype, q_text, a_text in [
            (QuestionType.LISTENING_VOCAB.value, hanzi, pinyin),
            (QuestionType.SPEAKING_VOCAB.value, hanzi, pinyin),
            (QuestionType.TRANSCRIBE_WORD_TO_PINYIN.value, hanzi, pinyin),
        ]:
            questions.append(make_question(unit_str, qtype, q_text, a_text, build_tags(hanzi, qtype, unit_str, all_hanzi), counters))

    seen_sentences = set()
    for item in unit_data.get("sentences", []):
        hanzi = item.get("hanzi", "")
        pinyin = item.get("pinyin", "")
        english = item.get("english", "")
        if not hanzi or hanzi in seen_sentences:
            continue
        if normalize(hanzi) in load_blocklist():
            continue
        seen_sentences.add(hanzi)
        for qtype, q_text, a_text in [
            (QuestionType.LISTENING_SENTENCE.value, hanzi, hanzi),
            (QuestionType.SPEAKING_SENTENCE.value, hanzi, pinyin),
            (QuestionType.TRANSLATE_EN_TO_ZH_SENTENCE.value, english, hanzi),
            (QuestionType.TRANSLATE_ZH_TO_EN_SENTENCE.value, hanzi, english),
        ]:
            questions.append(make_question(unit_str, qtype, q_text, a_text, build_tags(hanzi, qtype, unit_str, all_hanzi), counters))

    seen_fitb = set()
    for item in unit_data.get("fill_in_the_blank", []):
        key = (item.get("question"), item.get("answer"))
        if key in seen_fitb:
            continue
        seen_fitb.add(key)
        full_sentence = reconstruct_fitb_sentence(item.get("question", ""), item.get("answer", ""))
        if normalize(full_sentence) in load_blocklist():
            continue
        questions.append(make_question(unit_str, QuestionType.FILL_IN_THE_BLANK.value, item.get("question", ""), item.get("answer", ""), build_tags(full_sentence, QuestionType.FILL_IN_THE_BLANK.value, unit_str, all_hanzi), counters))

    return questions


def main():
    index_data = load_json(INDEX_FILEPATH)
    units_path = UNITS_FILEPATH if UNITS_FILEPATH.exists() else LEGACY_UNITS_FILEPATH
    if not units_path.exists():
        raise FileNotFoundError("Neither v2 nor legacy units_output.json exists")
    units_data = load_json(units_path)

    all_questions = {}
    for unit_number in sorted({int(k) for k in units_data.keys()} | {item["unit"] for item in index_data.get("vocab", [])} | {item["unit"] for item in index_data.get("grammar", [])} | {item["unit"] for item in index_data.get("proper_nouns", [])}):
        all_questions[str(unit_number)] = build_questions_for_unit(index_data, units_data, unit_number)

    OUTPUT_FILEPATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_FILEPATH, "w", encoding="utf-8") as fh:
        json.dump(all_questions, fh, ensure_ascii=False, indent=2)

    print(f"Wrote {OUTPUT_FILEPATH}")
    return all_questions


if __name__ == "__main__":
    main()
