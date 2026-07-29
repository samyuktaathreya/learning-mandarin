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
    TRANSCRIBE_HANZI_TO_PINYIN = "transcribe hanzi to pinyin"

    @classmethod
    def values(cls):
        return [item.value for item in cls]


BASE_DIR = Path(__file__).resolve().parent.parent
INDEX_FILEPATH = BASE_DIR / "data" / "clean" / "index_output.json"
UNITS_FILEPATH = BASE_DIR / "data" / "clean" / "units_output.json"
OUTPUT_FILEPATH = BASE_DIR / "data" / "clean" / "unit_questions_hsk1.json"
UNIT_VOCAB_TAGS_FILEPATH = BASE_DIR / "data" / "clean" / "unit_vocab_tags.json"


def get_grammar_tips(raw_tip) -> list:
    """Return a clean list of structured grammar tip objects from whatever
    shape grammar_tip is stored as in units_output.json.

    New format (after pipeline rewrite): a list of { sections: [...] } objects.
    Legacy format (old markdown pipeline): a newline-joined string.
    Either way, return a list so callers don't need to care.
    """
    if not raw_tip:
        return []
    # New format: already a list of structured objects
    if isinstance(raw_tip, list):
        return [tip for tip in raw_tip if tip]
    # Legacy format: concatenated markdown string -- split and wrap so the
    # frontend at least gets something renderable until the pipeline is re-run
    if isinstance(raw_tip, str):
        return [t.strip() for t in raw_tip.split("\n\n") if t.strip()]
    return []


def load_json(path: Path):
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def normalize(text: str) -> str:
    return re.sub(r"[。？！，、；：\"\'\.\?\!,]", "", text).strip()


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


def content_tags_for(hanzi_text, all_hanzi):
    return [w for w in all_hanzi if w in hanzi_text]


def build_tags(hanzi_text, question_type, unit_number, all_hanzi):
    tags = content_tags_for(hanzi_text, all_hanzi)
    tags.append(question_type.replace(" ", "_"))
    tags.append(f"unit_{unit_number}")
    return tags

def build_tags_from_precomputed(content_tags, question_type, unit_number):
    tags = list(content_tags)
    tags.append(question_type.replace(" ", "_"))
    tags.append(f"unit_{unit_number}")
    return tags

def first_appearance_units(units_data) -> dict:
    first_seen = {}
    for unit_str, unit_data in units_data.items():
        unit = int(unit_str)
        for sentence in unit_data.get("sentences", []):
            for tag in sentence.get("tags", []):
                if tag not in first_seen or unit < first_seen[tag]:
                    first_seen[tag] = unit
    return first_seen


def effective_home_units(index_data, units_data) -> dict:
    home = hanzi_home_units(index_data)
    first_use = first_appearance_units(units_data)
    for word, unit in first_use.items():
        if word not in home or unit < home[word]:
            home[word] = unit
    return home

def sentence_home_unit(content_tags, home_unit, default_unit) -> int:
    units = [home_unit.get(tag, default_unit) for tag in content_tags]
    return max(units) if units else default_unit


def rehome_sentences(units_data, home_unit) -> dict:
    by_unit = {int(k): v for k, v in units_data.items()}
    seen = {u: {s["hanzi"] for s in data.get("sentences", [])}
            for u, data in by_unit.items()}

    moved, deleted = 0, 0
    for unit in sorted(by_unit):
        keep = []
        for sentence in by_unit[unit].get("sentences", []):
            target = sentence_home_unit(sentence.get("tags", []), home_unit, unit)
            if target >= unit:
                keep.append(sentence)
                continue
            if sentence["hanzi"] in seen.get(target, set()):
                deleted += 1
                continue
            by_unit.setdefault(target, {"sentences": [], "fill_in_the_blank": [], "counts": {}})
            by_unit[target]["sentences"].append(sentence)
            seen.setdefault(target, set()).add(sentence["hanzi"])
            moved += 1
        by_unit[unit]["sentences"] = keep

    print(f"  [rehome] moved {moved} sentence(s) to an earlier unit, "
          f"deleted {deleted} duplicate(s)")
    return {str(u): v for u, v in by_unit.items()}

def hanzi_home_units(index_data) -> dict:
    home = {}
    for section in ("vocab", "grammar", "proper_nouns"):
        for item in index_data.get(section, []):
            hanzi, unit = item["hanzi"], item["unit"]
            if hanzi not in home or unit < home[hanzi]:
                home[hanzi] = unit
    return home


TYPING_REQUIRED_TYPES = {
    QuestionType.LISTENING_SENTENCE.value,
    QuestionType.TRANSLATE_EN_TO_ZH_SENTENCE.value,
    QuestionType.TRANSLATE_EN_TO_ZH_WORD.value,
    QuestionType.FILL_IN_THE_BLANK.value,
}


def has_unlearned_vocab(content_tags, unit_number, home_unit) -> bool:
    return any(home_unit.get(tag, unit_number) > unit_number for tag in content_tags)


def reconstruct_fitb_sentence(question, answer):
    paren_index = question.rfind("(")
    core = question[:paren_index].strip() if paren_index != -1 else question.strip()
    return core.replace("___", answer)


def extract_fitb_translation(question: str) -> str:
    paren_index = question.rfind("(")
    if paren_index == -1:
        return ""
    return question[paren_index + 1:].rstrip(")").strip()


def enrich_content_tags(hanzi, segmentation_tags, all_hanzi, unit_number, home_unit):
    """Segmentation is non-overlapping, and the LLM tagger doesn't reliably
    prefer the longest match -- so a compound the learner is plainly reading
    (好吃, 中国菜, or 电话 inside 打电话) often never appears as a tag at all,
    and can never accumulate sentence-level tier-3/4 progress.

    Add any known word that literally appears in the sentence AND is already
    taught by this unit. The already-taught filter is what keeps this from
    repeating the earlier regression: a coincidental cross-boundary match
    (请问, from adjacent 请 + 问) or a component character taught later than
    its compound (天, from 今天) is excluded, so enrichment can never push a
    later-unit word into the tag list.
    """
    enriched = set(segmentation_tags)
    for w in content_tags_for(hanzi, all_hanzi):
        if home_unit.get(w, unit_number) <= unit_number:
            enriched.add(w)
    return sorted(enriched)


def build_questions_for_unit(index_data, units_data, unit_number, home_unit):
    unit_str = str(unit_number)
    counters = {}
    questions = []
    unit_data = units_data.get(unit_str, {})

    all_vocab = index_data.get("vocab", [])
    print("unit : ", unit_str)
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

    print("vocab by unit: ", vocab_by_unit[unit_number])
    print("--------")

    for item in vocab_by_unit.get(unit_number, []):
        hanzi = item["hanzi"]
        pinyin = item.get("pinyin", "")
        english = item.get("english", "")
        segmentation_tags = item.get("tags") or content_tags_for(hanzi, all_hanzi)

        # Gate on the REAL segmentation only. Enrichment exists to hand out tag
        # credit; it must never widen the not-yet-taught check, or one component
        # character taught later than its compound silently strips the typing
        # question types (and therefore the whole character facet) off every
        # sentence that uses that compound.
        blocked = has_unlearned_vocab(segmentation_tags, unit_number, home_unit)

        content_tags = enrich_content_tags(hanzi, segmentation_tags, all_hanzi,
                                        unit_number, home_unit)
        for qtype, q_text, a_text in [
            (QuestionType.LISTENING_VOCAB.value, hanzi, pinyin),
            (QuestionType.SPEAKING_VOCAB.value, hanzi, pinyin),
            (QuestionType.TRANSLATE_EN_TO_ZH_WORD.value, english, hanzi),
            (QuestionType.TRANSLATE_ZH_TO_EN_WORD.value, hanzi, english),
            (QuestionType.TRANSCRIBE_WORD_TO_PINYIN.value, hanzi, pinyin),
            (QuestionType.TRANSCRIBE_HANZI_TO_PINYIN.value, hanzi, pinyin),
        ]:
            if qtype in TYPING_REQUIRED_TYPES and blocked:
                continue
            question = make_question(unit_str, qtype, q_text, a_text, build_tags(hanzi, qtype, unit_str, all_hanzi), counters)
            question["hanzi"] = hanzi
            question["english"] = english
            questions.append(question)

    for item in grammar_by_unit.get(unit_number, []):
        hanzi = item["hanzi"]
        pinyin = item.get("pinyin", "")
        english = item.get("english", "")
        for qtype, q_text, a_text in [
            (QuestionType.LISTENING_VOCAB.value, hanzi, pinyin),
            (QuestionType.SPEAKING_VOCAB.value, hanzi, pinyin),
            (QuestionType.TRANSCRIBE_WORD_TO_PINYIN.value, hanzi, pinyin),
            (QuestionType.TRANSCRIBE_HANZI_TO_PINYIN.value, hanzi, pinyin),
        ]:
            question = make_question(unit_str, qtype, q_text, a_text, build_tags(hanzi, qtype, unit_str, all_hanzi), counters)
            question["hanzi"] = hanzi
            question["english"] = english
            questions.append(question)

    for item in proper_by_unit.get(unit_number, []):
        hanzi = item["hanzi"]
        pinyin = item.get("pinyin", "")
        english = item.get("english", "")
        for qtype, q_text, a_text in [
            (QuestionType.LISTENING_VOCAB.value, hanzi, pinyin),
            (QuestionType.SPEAKING_VOCAB.value, hanzi, pinyin),
            (QuestionType.TRANSCRIBE_WORD_TO_PINYIN.value, hanzi, pinyin),
            (QuestionType.TRANSLATE_ZH_TO_EN_WORD.value, hanzi, english),
            (QuestionType.TRANSCRIBE_HANZI_TO_PINYIN.value, hanzi, pinyin),
        ]:
            question = make_question(unit_str, qtype, q_text, a_text, build_tags(hanzi, qtype, unit_str, all_hanzi), counters)
            question["hanzi"] = hanzi
            question["english"] = english
            questions.append(question)

    seen_sentences = set()
    for item in unit_data.get("sentences", []):
        hanzi = item.get("hanzi", "")
        pinyin = item.get("pinyin", "")
        english = item.get("english", "")
        if not hanzi or hanzi in seen_sentences:
            continue
        seen_sentences.add(hanzi)
        segmentation_tags = item.get("tags") or content_tags_for(hanzi, all_hanzi)

        # Gate on the REAL segmentation only -- same reasoning as the vocab
        # loop above. Enrichment must never widen what counts as "used
        # vocab" for the not-yet-taught check, or a later-taught embedded
        # word (or a coincidental cross-tag-boundary match) would silently
        # strip typing-required question types off a sentence that's
        # actually fine.
        blocked = has_unlearned_vocab(segmentation_tags, unit_number, home_unit)

        content_tags = enrich_content_tags(hanzi, segmentation_tags, all_hanzi,
                                           unit_number, home_unit)
        grammar_tips = get_grammar_tips(item.get("grammar_tip"))
        for qtype, q_text, a_text in [
            (QuestionType.LISTENING_SENTENCE.value, hanzi, hanzi),
            (QuestionType.SPEAKING_SENTENCE.value, hanzi, pinyin),
            (QuestionType.TRANSLATE_EN_TO_ZH_SENTENCE.value, english, hanzi),
            (QuestionType.TRANSLATE_ZH_TO_EN_SENTENCE.value, hanzi, english),
        ]:
            if qtype in TYPING_REQUIRED_TYPES and blocked:
                continue
            question = make_question(unit_str, qtype, q_text, a_text,
                                     build_tags_from_precomputed(content_tags, qtype, unit_str), counters)
            question["hanzi"] = hanzi
            question["english"] = english
            if grammar_tips:
                question["grammar_tips"] = grammar_tips
            questions.append(question)

    seen_fitb = set()
    for item in unit_data.get("fill_in_the_blank", []):
        key = (item.get("question"), item.get("answer"))
        if key in seen_fitb:
            continue
        seen_fitb.add(key)
        full_sentence = reconstruct_fitb_sentence(item.get("question", ""), item.get("answer", ""))

        # FITB entries have no independent segmentation of their own, so
        # content_tags_for's raw substring scan is the only source of tags
        # here -- but used unfiltered for gating it's the same cross-boundary
        # trap as before (e.g. adjacent 请 + 问 spelling 请问 by coincidence).
        # Split into "tags for credit" (all substring matches, same as
        # always) vs. "tags for the not-yet-taught gate" (only genuinely
        # embedded compounds matter there, and a compound is only a real
        # signal if it's not simply two already-known neighboring words).
        raw_tags = content_tags_for(full_sentence, all_hanzi)
        if has_unlearned_vocab(raw_tags, unit_number, home_unit):
            continue
        content_tags = raw_tags
        question = make_question(
            unit_str, QuestionType.FILL_IN_THE_BLANK.value,
            item.get("question", ""), item.get("answer", ""),
            build_tags_from_precomputed(content_tags, QuestionType.FILL_IN_THE_BLANK.value, unit_str),
            counters,
        )
        question["hanzi"] = full_sentence
        question["english"] = extract_fitb_translation(item.get("question", ""))
        questions.append(question)

    return questions


def vocab_tags_for_unit(index_data, unit_number) -> list:
    tags = {
        item["hanzi"] for item in index_data.get("vocab", [])
        if item["unit"] == unit_number
    }
    tags |= {
        item["hanzi"] for item in index_data.get("grammar", [])
        if item["unit"] == unit_number
    }
    tags |= {
        item["hanzi"] for item in index_data.get("proper_nouns", [])
        if item["unit"] == unit_number
    }
    return sorted(tags)


def main():
    index_data = load_json(INDEX_FILEPATH)
    units_data = load_json(UNITS_FILEPATH)
    home_unit = effective_home_units(index_data, units_data)
    units_data = rehome_sentences(units_data, home_unit)

    all_questions = {}
    all_vocab_tags = {}
    for unit_number in sorted({int(k) for k in units_data.keys()} | {item["unit"] for item in index_data.get("vocab", [])} | {item["unit"] for item in index_data.get("grammar", [])} | {item["unit"] for item in index_data.get("proper_nouns", [])}):
        all_questions[str(unit_number)] = build_questions_for_unit(index_data, units_data, unit_number, home_unit)
        all_vocab_tags[str(unit_number)] = vocab_tags_for_unit(index_data, unit_number)

    OUTPUT_FILEPATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_FILEPATH, "w", encoding="utf-8") as fh:
        json.dump(all_questions, fh, ensure_ascii=False, indent=2)

    with open(UNIT_VOCAB_TAGS_FILEPATH, "w", encoding="utf-8") as fh:
        json.dump(all_vocab_tags, fh, ensure_ascii=False, indent=2)

    print(f"Wrote {OUTPUT_FILEPATH}")
    print(f"Wrote {UNIT_VOCAB_TAGS_FILEPATH}")
    return all_questions


if __name__ == "__main__":
    main()