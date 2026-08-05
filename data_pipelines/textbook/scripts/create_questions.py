"""
Generate the per-unit question bank directly from the DB (Vocab, Sentence,
SentenceVocab, GrammarTip/SentenceGrammar, FitbQuestion) and write Question
rows. Replaces index_output.json + units_output.json + unit_questions_hsk1.json
+ unit_vocabs_tag.json as inputs/outputs entirely.

Key simplification vs. the JSON version: a sentence's tags used to be
recomputed from scratch here (`content_tags_for` via substring matching
against `all_hanzi`) because units_output.json's own "tags" field wasn't
fully trusted / wasn't always present for every consumer. In the DB, a
sentence's tags ARE db.get_tags_for_sentence(sentence_id) -- the exact,
already-validated SentenceVocab links written by sentence_parser.py -- so
that recomputation is gone for sentences. It's kept ONLY for vocab/grammar/
proper-noun entries, where a compound word's own hanzi may contain other
taught sub-words that aren't tracked anywhere else (there's no
"vocab_components" table -- this mirrors the old code's same use of
content_tags_for for vocab items).

Merge/idempotency: db.upsert_question is keyed on (unit, question_type,
question, answer) -- the same signature the old code used to merge into
existing_questions and avoid duplicate IDs on rerun. legacy_id preservation
was for a specific "u3_speaking_vocab_2" ID format some other part of the
app might reference directly; if nothing external depends on that string
anymore, it can be dropped -- included here for parity.
"""

import re
from collections import defaultdict
from enum import Enum

from app.textbook.database import (
    get_session, init_db, get_vocab_for_unit, get_all_vocab_hanzi,
    get_sentences_for_unit, get_tags_for_sentence, get_grammar_tips_for_sentence,
    rehome_sentences, upsert_question, get_word_to_unit_map,
)
from app.textbook.models import WordType, FitbQuestion, Unit


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


TYPING_REQUIRED_TYPES = {
    QuestionType.LISTENING_SENTENCE.value,
    QuestionType.TRANSLATE_EN_TO_ZH_SENTENCE.value,
    QuestionType.TRANSLATE_EN_TO_ZH_WORD.value,
    QuestionType.FILL_IN_THE_BLANK.value,
}


def content_tags_for(hanzi_text: str, all_hanzi: list[str]) -> list[str]:
    """Which known words are substrings of this text -- used only for vocab/
    grammar/proper-noun entries (compound-word decomposition). Sentences use
    their real DB tags instead; see module docstring."""
    return [w for w in all_hanzi if w in hanzi_text]


def has_unlearned_vocab(tags: list[str], unit_number: int, home_unit: dict) -> bool:
    return any(home_unit.get(t, unit_number) > unit_number for t in tags)


def reconstruct_fitb_sentence(question: str, answer: str) -> str:
    paren_index = question.rfind("(")
    core = question[:paren_index].strip() if paren_index != -1 else question.strip()
    return core.replace("___", answer)


def extract_fitb_translation(question: str) -> str:
    paren_index = question.rfind("(")
    if paren_index == -1:
        return ""
    return question[paren_index + 1:].rstrip(")").strip()


def build_vocab_style_questions(db, items, unit_number: int, all_hanzi: list[str],
                                 home_unit: dict, qtypes: list[str]):
    """Shared body for vocab / grammar / proper-noun word questions -- same
    (qtype, question, answer) triples the original code emitted per section,
    just parameterized by which qtypes each section allows."""
    for item in items:
        hanzi = item.hanzi
        pinyin = item.pinyin or ""
        english = item.english or ""
        tags = content_tags_for(hanzi, all_hanzi)
        blocked = has_unlearned_vocab(tags, unit_number, home_unit)

        candidates = {
            QuestionType.LISTENING_VOCAB.value: (hanzi, pinyin),
            QuestionType.SPEAKING_VOCAB.value: (hanzi, pinyin),
            QuestionType.TRANSLATE_EN_TO_ZH_WORD.value: (english, hanzi),
            QuestionType.TRANSLATE_ZH_TO_EN_WORD.value: (hanzi, english),
            QuestionType.TRANSCRIBE_WORD_TO_PINYIN.value: (hanzi, pinyin),
            QuestionType.TRANSCRIBE_HANZI_TO_PINYIN.value: (hanzi, pinyin),
        }
        for qtype in qtypes:
            if qtype not in candidates:
                continue
            if qtype in TYPING_REQUIRED_TYPES and blocked:
                continue
            q_text, a_text = candidates[qtype]
            upsert_question(db, unit_number, qtype, q_text, a_text, vocab_id=item.id)


def build_questions_for_unit(db, unit_number: int, all_hanzi: list[str], home_unit: dict):
    # --- vocab / grammar / proper-noun word questions ---
    vocab_items = get_vocab_for_unit(db, unit_number, word_types=[WordType.vocab])
    build_vocab_style_questions(db, vocab_items, unit_number, all_hanzi, home_unit, qtypes=[
        QuestionType.LISTENING_VOCAB.value, QuestionType.SPEAKING_VOCAB.value,
        QuestionType.TRANSLATE_EN_TO_ZH_WORD.value, QuestionType.TRANSLATE_ZH_TO_EN_WORD.value,
        QuestionType.TRANSCRIBE_WORD_TO_PINYIN.value, QuestionType.TRANSCRIBE_HANZI_TO_PINYIN.value,
    ])

    grammar_items = get_vocab_for_unit(db, unit_number, word_types=[WordType.grammar])
    build_vocab_style_questions(db, grammar_items, unit_number, all_hanzi, home_unit, qtypes=[
        QuestionType.LISTENING_VOCAB.value, QuestionType.SPEAKING_VOCAB.value,
        QuestionType.TRANSCRIBE_WORD_TO_PINYIN.value, QuestionType.TRANSCRIBE_HANZI_TO_PINYIN.value,
    ])

    proper_items = get_vocab_for_unit(db, unit_number, word_types=[WordType.proper_noun])
    build_vocab_style_questions(db, proper_items, unit_number, all_hanzi, home_unit, qtypes=[
        QuestionType.LISTENING_VOCAB.value, QuestionType.SPEAKING_VOCAB.value,
        QuestionType.TRANSCRIBE_WORD_TO_PINYIN.value, QuestionType.TRANSLATE_ZH_TO_EN_WORD.value,
        QuestionType.TRANSCRIBE_HANZI_TO_PINYIN.value,
    ])

    # --- sentence questions (real DB tags, no recomputation) ---
    for sentence in get_sentences_for_unit(db, unit_number):
        hanzi, pinyin, english = sentence.hanzi, sentence.pinyin, sentence.english
        tags = get_tags_for_sentence(db, sentence.id)
        blocked = has_unlearned_vocab(tags, unit_number, home_unit)
        # grammar tips ride along for display purposes; not stored on
        # Question itself (they belong to the sentence via SentenceGrammar --
        # fetch them at read-time in your API layer via
        # get_grammar_tips_for_sentence(sentence_id) rather than duplicating
        # them onto every question row).
        for qtype, q_text, a_text in [
            (QuestionType.LISTENING_SENTENCE.value, hanzi, hanzi),
            (QuestionType.SPEAKING_SENTENCE.value, hanzi, pinyin),
            (QuestionType.TRANSLATE_EN_TO_ZH_SENTENCE.value, english, hanzi),
            (QuestionType.TRANSLATE_ZH_TO_EN_SENTENCE.value, hanzi, english),
        ]:
            if qtype in TYPING_REQUIRED_TYPES and blocked:
                continue
            upsert_question(db, unit_number, qtype, q_text, a_text)

    # --- FITB questions ---
    unit_row = db.query(Unit).filter(Unit.unit_number == unit_number).first()
    if unit_row:
        fitb_rows = db.query(FitbQuestion).filter(FitbQuestion.unit_id == unit_row.id).all()
        for fq in fitb_rows:
            full_sentence = fq.full_sentence or reconstruct_fitb_sentence(fq.question, fq.answer)
            raw_tags = content_tags_for(full_sentence, all_hanzi)
            if has_unlearned_vocab(raw_tags, unit_number, home_unit):
                continue
            upsert_question(db, unit_number, QuestionType.FILL_IN_THE_BLANK.value,
                             fq.question, fq.answer)


def main():
    init_db()
    with get_session() as db:
        print("1. Rehoming sentences to their earliest legitimate unit...")
        rehome_sentences(db)

        home_unit = get_word_to_unit_map(db)
        all_hanzi = get_all_vocab_hanzi(db)

        unit_numbers = sorted(u.unit_number for u in db.query(Unit).all())
        print(f"2. Building questions for {len(unit_numbers)} unit(s)...")
        for unit_number in unit_numbers:
            print(f"  -> unit {unit_number}")
            build_questions_for_unit(db, unit_number, all_hanzi, home_unit)

    print("✅ Done! Questions written directly to the DB.")


if __name__ == "__main__":
    main()