"""
data_pipelines/textbook/scripts/create_questions.py

Pipeline stage 7: generates each unit's question bank from the finished DB
and writes Question rows (and FitbQuestion rows for fill-in-the-blank).
No AI. Run after assign_units.py and finalize.py.

Questions are derived data, so each run DELETES a level's questions and
rebuilds them -- re-running never leaves stale questions behind (they get
new ids each time).

WORD QUESTIONS -- one set per sense homed in the unit (a word re-taught with
a new meaning in a later unit gets questions there too). Pinyin is the
sense's tone-sandhi display pinyin.
  vocab / unlisted words: listening, speaking, translate both ways, pinyin
  grammar words (了, 的, 吗...): listening, speaking, pinyin -- no
                                  translation, a particle has no English word
  proper nouns: listening, speaking, pinyin, translate to English

SENTENCE QUESTIONS -- every sentence in the unit: listening, speaking
(answer = the sentence's sandhi pinyin), translate both ways (skipped when
the sentence has no English translation).

FILL IN THE BLANK -- one per sentence, generated: the blank is a word whose
sense is new in this unit (spread so the unit's new words each get blanked
before any repeats), else the most recently taught word. Proper nouns are
never blanked. Format, as before: "我很___。 (I'm very happy.)"

TYPING questions (listening sentence, translate to Chinese, fill in the
blank) are skipped when a SENTENCE uses a word not taught yet. Every
sentence's words are taught by its unit (assign_units.py checks this), so
this is a safety net. The old extra rule for WORD questions -- skip a
compound if any shorter word inside it is taught later -- is gone: against
the full 11,000-word list it blocked 老师 because 老 comes later, and typing
a word needs its pinyin, not its characters as separate words.

USAGE (from the repo root)
    python -m data_pipelines.textbook.scripts.create_questions               # every level with units
    python -m data_pipelines.textbook.scripts.create_questions --hsk-level 1
"""
import argparse
import re
import time
from collections import Counter, defaultdict
from enum import Enum

from app.textbook.db_utils import (
    get_session, init_db, get_senses_for_unit,
    get_sentences_for_unit, get_tags_for_sentence, upsert_question, get_word_to_unit_map,
)
from app.textbook.models import (
    FitbQuestion, Question, Sentence, SentenceVocab, Unit, VocabSense, WordType,
)


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

WORD_QTYPES = {
    WordType.vocab: [QuestionType.LISTENING_VOCAB, QuestionType.SPEAKING_VOCAB,
                     QuestionType.TRANSLATE_EN_TO_ZH_WORD, QuestionType.TRANSLATE_ZH_TO_EN_WORD,
                     QuestionType.TRANSCRIBE_WORD_TO_PINYIN, QuestionType.TRANSCRIBE_HANZI_TO_PINYIN],
    WordType.grammar: [QuestionType.LISTENING_VOCAB, QuestionType.SPEAKING_VOCAB,
                       QuestionType.TRANSCRIBE_WORD_TO_PINYIN, QuestionType.TRANSCRIBE_HANZI_TO_PINYIN],
    WordType.proper_noun: [QuestionType.LISTENING_VOCAB, QuestionType.SPEAKING_VOCAB,
                           QuestionType.TRANSCRIBE_WORD_TO_PINYIN, QuestionType.TRANSLATE_ZH_TO_EN_WORD,
                           QuestionType.TRANSCRIBE_HANZI_TO_PINYIN],
}
WORD_QTYPES[WordType.auto] = WORD_QTYPES[WordType.vocab]   # unlisted words: same as vocab

_CJK_RE = re.compile(r"[一-鿿]")


# --------------------------------- HELPERS ---------------------------------

def has_unlearned_vocab(tags: list[str], unit_number: int, home_unit: dict, hsk_level: int) -> bool:
    """True if any tag's home unit is later than (hsk_level, unit_number).
    home_unit values are (unit_number, hsk_level); words with no unit don't
    block (unchanged)."""
    for t in tags:
        u, lvl = home_unit.get(t, (unit_number, hsk_level))
        if (lvl, u) > (hsk_level, unit_number):
            return True
    return False


def display_pinyin(sense: VocabSense) -> str:
    return (sense.pinyin_display or sense.pinyin or "").strip()


def word_spans(sentence: Sentence, rows: list[SentenceVocab]) -> list[tuple[int, int] | None]:
    """(start, end) in sentence.hanzi of each tagged word, matched in order;
    characters with no word row (numbers, punctuation) are skipped."""
    text, spans, pos = sentence.hanzi, [], 0
    for row in rows:
        word = row.vocab.hanzi
        idx = text.find(word, pos)
        if idx == -1:
            spans.append(None)
            continue
        between = text[pos:idx]
        if any(_CJK_RE.match(c) for c in between) and not all(c in "零一二三四五六七八九十百千万两" or not _CJK_RE.match(c) for c in between):
            spans.append(None)   # skipped over a real word: alignment is off
            continue
        spans.append((idx, idx + len(word)))
        pos = idx + len(word)
    return spans


def pick_blank(rows, spans, unit_id: int, blank_counts: Counter):
    """The word to blank: a sense new in this unit, least-blanked first, then
    longest; else the most recently taught word. Never a proper noun."""
    best = None
    for row, span in zip(rows, spans):
        if span is None or row.sense is None or row.vocab.word_type == WordType.proper_noun:
            continue
        sense_unit = row.sense.unit
        new_here = row.sense.unit_id == unit_id
        recency = (sense_unit.hsk_level, sense_unit.unit_number) if sense_unit else (0, 0)
        key = (new_here, -blank_counts[row.vocab.hanzi] if new_here else 0, recency, len(row.vocab.hanzi))
        if best is None or key > best[0]:
            best = (key, row, span)
    return (best[1], best[2]) if best else (None, None)


# --------------------------------- PER UNIT ---------------------------------

def build_questions_for_unit(db, unit: Unit, home_unit: dict, counts: Counter):
    n, lvl = unit.unit_number, unit.hsk_level
    seen = set()   # (qtype, question text): no duplicates within a unit

    def add(qtype, q, a, **links):
        if not q or not a or (qtype, q) in seen:
            return
        seen.add((qtype, q))
        upsert_question(db, n, qtype, q, a, hsk_level=lvl, **links)
        counts[qtype] += 1

    # --- word questions: every sense homed in this unit ---
    for sense in get_senses_for_unit(db, n, hsk_level=lvl):
        hanzi, pinyin, english = sense.vocab.hanzi, display_pinyin(sense), (sense.english or "").strip()
        if not pinyin or "UNKNOWN" in pinyin:
            counts["word skipped: no pinyin"] += 1
            continue
        options = {
            QuestionType.LISTENING_VOCAB: (hanzi, pinyin),
            QuestionType.SPEAKING_VOCAB: (hanzi, pinyin),
            QuestionType.TRANSLATE_EN_TO_ZH_WORD: (english, hanzi),
            QuestionType.TRANSLATE_ZH_TO_EN_WORD: (hanzi, english),
            QuestionType.TRANSCRIBE_WORD_TO_PINYIN: (hanzi, pinyin),
            QuestionType.TRANSCRIBE_HANZI_TO_PINYIN: (hanzi, pinyin),
        }
        for qtype in WORD_QTYPES.get(sense.vocab.word_type, WORD_QTYPES[WordType.vocab]):
            if "UNKNOWN" in english and qtype in (QuestionType.TRANSLATE_EN_TO_ZH_WORD,
                                                  QuestionType.TRANSLATE_ZH_TO_EN_WORD):
                continue
            q, a = options[qtype]
            add(qtype.value, q, a, vocab_id=sense.vocab_id, vocab_sense_id=sense.id)

    # --- sentence questions + generated fill in the blank ---
    blank_counts = Counter()
    for sentence in sorted(get_sentences_for_unit(db, n, hsk_level=lvl), key=lambda s: s.id):
        hanzi, pinyin, english = sentence.hanzi, sentence.pinyin, (sentence.english or "").strip()
        blocked = has_unlearned_vocab(get_tags_for_sentence(db, sentence.id), n, home_unit, lvl)
        for qtype, q, a in [
            (QuestionType.LISTENING_SENTENCE.value, hanzi, hanzi),
            (QuestionType.SPEAKING_SENTENCE.value, hanzi, pinyin),
            (QuestionType.TRANSLATE_EN_TO_ZH_SENTENCE.value, english, hanzi),
            (QuestionType.TRANSLATE_ZH_TO_EN_SENTENCE.value, hanzi, english),
        ]:
            if qtype in TYPING_REQUIRED_TYPES and blocked:
                counts["typing question skipped: unlearned word inside"] += 1
                continue
            add(qtype, q, a, sentence_id=sentence.id)

        if blocked:
            continue
        rows = sorted(sentence.vocab_links, key=lambda r: r.position)
        row, span = pick_blank(rows, word_spans(sentence, rows), unit.id, blank_counts)
        if row is None:
            counts["fill in the blank skipped: no word to blank"] += 1
            continue
        answer = row.vocab.hanzi
        question = hanzi[:span[0]] + "___" + hanzi[span[1]:]
        if english:
            question += f" ({english})"
        if not db.query(FitbQuestion).filter_by(unit_id=unit.id, question=question, answer=answer).first():
            db.add(FitbQuestion(sentence_id=sentence.id, unit_id=unit.id, question=question,
                                answer=answer, full_sentence=hanzi))
        add(QuestionType.FILL_IN_THE_BLANK.value, question, answer,
            sentence_id=sentence.id, vocab_id=row.vocab_id, vocab_sense_id=row.vocab_sense_id)
        blank_counts[answer] += 1
    db.flush()


def main():
    parser = argparse.ArgumentParser(description="Generate each unit's questions.")
    parser.add_argument("--hsk-level", type=int, default=None, help="Only this level (default: every level with units).")
    args = parser.parse_args()

    t0 = time.time()
    init_db()
    with get_session() as db:
        missing = db.query(VocabSense).filter(VocabSense.unit_id.isnot(None),
                                              VocabSense.pinyin_display.is_(None)).count()
        if missing:
            raise SystemExit(f"{missing} taught sense(s) have no display pinyin -- run finalize.py first.")

        levels = ([args.hsk_level] if args.hsk_level else
                  sorted({lvl for (lvl,) in db.query(Unit.hsk_level).distinct().all()}))
        if not levels:
            raise SystemExit("No units yet -- run assign_units.py first.")

        home_unit = get_word_to_unit_map(db)

        for lvl in levels:
            units = db.query(Unit).filter(Unit.hsk_level == lvl).order_by(Unit.unit_number).all()
            unit_ids = [u.id for u in units]
            old = db.query(Question).filter(Question.unit_id.in_(unit_ids)).delete(synchronize_session=False)
            db.query(FitbQuestion).filter(FitbQuestion.unit_id.in_(unit_ids)).delete(synchronize_session=False)
            db.flush()
            counts = Counter()
            per_unit = []
            for unit in units:
                before = sum(v for k, v in counts.items() if not k.startswith(("word skipped", "typing", "fill in the blank skipped")))
                build_questions_for_unit(db, unit, home_unit, counts)
                after = sum(v for k, v in counts.items() if not k.startswith(("word skipped", "typing", "fill in the blank skipped")))
                per_unit.append((unit.unit_number, unit.title, after - before))
            total = sum(q for *_, q in per_unit)
            print(f"\nHSK {lvl}: {total} question(s) in {len(units)} unit(s) (replaced {old})")
            for n, title, q in per_unit:
                print(f"  {n:>3}  {title:<34}{q:>6}")
            for k, v in sorted(counts.items()):
                print(f"    {k}: {v}")
            db.commit()
    print(f"\nDone in {time.time() - t0:.0f}s.")


if __name__ == "__main__":
    main()