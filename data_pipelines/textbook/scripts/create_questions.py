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

import os
import re
from collections import defaultdict
from enum import Enum

from app.textbook.models import Vocab

from app.textbook.db_utils import (
    get_session, init_db, get_vocab_for_unit, get_all_vocab_hanzi,
    get_sentences_for_unit, get_tags_for_sentence, get_grammar_tips_for_sentence,
    rehome_sentences, upsert_question, get_word_to_unit_map,
)
from app.textbook.models import WordType, FitbQuestion, Unit
from vocab_pinyin_utils import diacritic_to_numeric

# HSK level being processed this run (threaded the same way main.py already
# threads UNITS_TO_PROCESS / SOURCES_TO_PROCESS to sentence_parser.py).
HSK_LEVEL = int(os.environ.get("HSK_LEVEL", "1"))

import anthropic
from dotenv import load_dotenv
from typing import Optional

from app.core.config.shared import ENV_FILE
from app.textbook.models import SentenceVocab

load_dotenv(ENV_FILE)
api_key = os.environ.get("CLAUDE_API_KEY")
client = anthropic.Anthropic(api_key=api_key) if api_key else None
MODEL = os.getenv("CLAUDE_MODEL", "claude-haiku-4-5-20251001")


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

def resolve_context_definition(word: str, default_english: str, sentence_hanzi: str) -> Optional[str]:
    """Checks whether `word`'s default Vocab definition actually fits its
    usage in `sentence_hanzi`. Returns None if the default is fine (caller
    stores "" to mark it checked). Returns a corrected definition string if
    the default is misleading for this specific sentence -- e.g. 告诉's
    default "to press charges" being wrong for "请告诉我你的电话", where it
    just means "to tell/inform"."""
    if client is None:
        return None

    prompt = f"""You are a Chinese-English dictionary editor fixing word
definitions that will be shown to a student who clicks on a specific word
inside a specific sentence.

Word: "{word}"
Default dictionary definition on file: "{default_english}"
Sentence it appears in: "{sentence_hanzi}"

Does the default definition accurately describe how "{word}" is used in THIS
sentence? If yes, respond with exactly: SAME
If the default definition is misleading or wrong for this specific sentence,
respond with ONLY a corrected, concise English definition that fits this
sentence (a few words, no explanation, no quotes, no markdown).
"""
    try:
        response = client.messages.create(
            model=MODEL,
            max_tokens=64,
            system="You are a precise bilingual dictionary editor.",
            messages=[{"role": "user", "content": prompt}],
        )
        raw = response.content[0].text.strip()
        if not raw or raw.upper() == "SAME":
            return None
        return raw
    except Exception as e:
        print(f"  [warning] context-definition check failed for '{word}': {e}")
        return None


def backfill_context_definitions(db, sentence) -> int:
    """For every word tagged in `sentence`, checks whether the word's default
    Vocab.english fits THIS sentence's usage, and if not, stores a corrected
    per-occurrence definition on the SentenceVocab link. Idempotent: rows
    already checked (context_definition is not None, including the ""
    sentinel for "checked, default is fine") are skipped, so re-running the
    pipeline doesn't re-spend API calls on words already resolved. Runs here
    -- not in sentence_parser.py or sync_index_definitions.py -- because this
    is the one place every sentence passes through regardless of source
    (textbook or any external DB feeding into create_questions.py)."""
    links = db.query(SentenceVocab).filter(SentenceVocab.sentence_id == sentence.id).all()
    updated = 0
    for link in links:
        if link.context_definition is not None:
            continue  # already checked this occurrence, one way or the other

        vocab = link.vocab
        if not vocab or not vocab.english or vocab.english == "UNKNOWN_ENGLISH":
            continue

        corrected = resolve_context_definition(vocab.hanzi, vocab.english, sentence.hanzi)
        if corrected:
            link.context_definition = corrected
            updated += 1
            print(f"  [context-def] '{vocab.hanzi}' in \"{sentence.hanzi}\" -> "
                  f"'{corrected}' (default was '{vocab.english}')")
        else:
            link.context_definition = ""  # mark checked, default definition is fine

    if updated:
        db.flush()
    return updated


def _word_location_after(word_location, hsk_level: int, unit_number: int) -> bool:
    """True if a word's home unit is STRICTLY LATER than (hsk_level, unit_number)
    -- i.e. not yet taught. home_unit's values need to be (unit_number,
    hsk_level) tuples now that unit_number alone is ambiguous across levels
    (see db.get_word_to_unit_map). HSK levels are sequential, so tuple
    comparison is correct. Falls back to treating a bare int as hsk_level 1
    for backward compatibility if db_utils hasn't been updated yet."""
    if isinstance(word_location, tuple):
        word_unit_number, word_hsk_level = word_location
    else:
        word_unit_number, word_hsk_level = word_location, 1
    return (word_hsk_level, word_unit_number) > (hsk_level, unit_number)


def has_unlearned_vocab(tags: list[str], unit_number: int, home_unit: dict,
                         hsk_level: int = HSK_LEVEL) -> bool:
    return any(_word_location_after(home_unit.get(t, (unit_number, hsk_level)), hsk_level, unit_number)
               for t in tags)


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
                                 home_unit: dict, qtypes: list[str], hsk_level: int = HSK_LEVEL):
    """Shared body for vocab / grammar / proper-noun word questions -- same
    (qtype, question, answer) triples the original code emitted per section,
    just parameterized by which qtypes each section allows."""
    for item in items:
        hanzi = item.hanzi
        pinyin = (item.pinyin or "").strip()
        if pinyin and pinyin != "UNKNOWN_PINYIN":
            pinyin = diacritic_to_numeric(pinyin)  # defensive: normalize regardless
        english = item.english or ""
        tags = content_tags_for(hanzi, all_hanzi)
        blocked = has_unlearned_vocab(tags, unit_number, home_unit, hsk_level=hsk_level)

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
            upsert_question(db, unit_number, qtype, q_text, a_text, vocab_id=item.id, hsk_level=hsk_level)


def build_questions_for_unit(db, unit_number: int, all_hanzi: list[str], home_unit: dict,
                              hsk_level: int = HSK_LEVEL):
    # --- vocab / grammar / proper-noun word questions ---
    vocab_items = get_vocab_for_unit(db, unit_number, word_types=[WordType.vocab], hsk_level=hsk_level)
    build_vocab_style_questions(db, vocab_items, unit_number, all_hanzi, home_unit, qtypes=[
        QuestionType.LISTENING_VOCAB.value, QuestionType.SPEAKING_VOCAB.value,
        QuestionType.TRANSLATE_EN_TO_ZH_WORD.value, QuestionType.TRANSLATE_ZH_TO_EN_WORD.value,
        QuestionType.TRANSCRIBE_WORD_TO_PINYIN.value, QuestionType.TRANSCRIBE_HANZI_TO_PINYIN.value,
    ], hsk_level=hsk_level)

    grammar_items = get_vocab_for_unit(db, unit_number, word_types=[WordType.grammar], hsk_level=hsk_level)
    build_vocab_style_questions(db, grammar_items, unit_number, all_hanzi, home_unit, qtypes=[
        QuestionType.LISTENING_VOCAB.value, QuestionType.SPEAKING_VOCAB.value,
        QuestionType.TRANSCRIBE_WORD_TO_PINYIN.value, QuestionType.TRANSCRIBE_HANZI_TO_PINYIN.value,
    ], hsk_level=hsk_level)

    proper_items = get_vocab_for_unit(db, unit_number, word_types=[WordType.proper_noun], hsk_level=hsk_level)
    build_vocab_style_questions(db, proper_items, unit_number, all_hanzi, home_unit, qtypes=[
        QuestionType.LISTENING_VOCAB.value, QuestionType.SPEAKING_VOCAB.value,
        QuestionType.TRANSCRIBE_WORD_TO_PINYIN.value, QuestionType.TRANSLATE_ZH_TO_EN_WORD.value,
        QuestionType.TRANSCRIBE_HANZI_TO_PINYIN.value,
    ], hsk_level=hsk_level)

    # --- sentence questions (real DB tags, no recomputation) ---
    for sentence in get_sentences_for_unit(db, unit_number, hsk_level=hsk_level):
        backfill_context_definitions(db, sentence)
        hanzi, pinyin, english = sentence.hanzi, sentence.pinyin, sentence.english
        tags = get_tags_for_sentence(db, sentence.id)
        blocked = has_unlearned_vocab(tags, unit_number, home_unit, hsk_level=hsk_level)
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
            upsert_question(db, unit_number, qtype, q_text, a_text, sentence_id=sentence.id,
                             hsk_level=hsk_level)

    # --- FITB questions ---
    # Unit lookup now needs BOTH unit_number and hsk_level -- see migration
    # doc section 3. Without the hsk_level filter this grabs whichever row
    # SQLite happens to return first once more than one level's units exist.
    unit_row = (
        db.query(Unit)
        .filter(Unit.unit_number == unit_number, Unit.hsk_level == hsk_level)
        .first()
    )
    if unit_row:
        fitb_rows = db.query(FitbQuestion).filter(FitbQuestion.unit_id == unit_row.id).all()
        for fq in fitb_rows:
            full_sentence = fq.full_sentence or reconstruct_fitb_sentence(fq.question, fq.answer)
            raw_tags = content_tags_for(full_sentence, all_hanzi)
            if has_unlearned_vocab(raw_tags, unit_number, home_unit, hsk_level=hsk_level):
                continue
            upsert_question(db, unit_number, QuestionType.FILL_IN_THE_BLANK.value,
                             fq.question, fq.answer, sentence_id=fq.sentence_id,
                             hsk_level=hsk_level)


def main():
    init_db()
    with get_session() as db:
        print(f"0. Make sure there is no bad pinyin.")
        bad_pinyin = db.query(Vocab).filter(
            Vocab.pinyin.notlike('%[1-5]%'),  # no digit 1-5
            Vocab.pinyin != "UNKNOWN_PINYIN",
            Vocab.pinyin.isnot(None),
        ).all()
        if bad_pinyin:
            print(f"⚠️  WARNING: Found {len(bad_pinyin)} vocab rows with un-converted pinyin. "
                f"Run repair_diacritic_pinyin.py first.")
            return
        print(f"1. Rehoming sentences to their earliest legitimate unit (HSK level {HSK_LEVEL})...")
        # Must stay within this one hsk_level -- a sentence using only HSK1
        # words shouldn't get rehomed to an HSK2 unit just because the
        # unit_number happens to be lower there (see migration doc section 3).
        rehome_sentences(db, hsk_level=HSK_LEVEL)

        home_unit = get_word_to_unit_map(db)
        all_hanzi = get_all_vocab_hanzi(db)

        unit_numbers = sorted(
            u.unit_number for u in db.query(Unit).filter(Unit.hsk_level == HSK_LEVEL).all()
        )
        print(f"2. Building questions for {len(unit_numbers)} unit(s) in HSK level {HSK_LEVEL}...")
        for unit_number in unit_numbers:
            print(f"  -> unit {unit_number}")
            build_questions_for_unit(db, unit_number, all_hanzi, home_unit, hsk_level=HSK_LEVEL)

    print("✅ Done! Questions written directly to the DB.")


if __name__ == "__main__":
    main()