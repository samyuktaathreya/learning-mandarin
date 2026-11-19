"""
data_pipelines/textbook/scripts/finalize.py

Pipeline stage 6: the last pass before questions. No AI, no HanLP; runs
once over everything and can be re-run any time (it recomputes, never
accumulates).

1. SENSE DISPLAY PINYIN -- VocabSense.pinyin_display = the sense's citation
   pinyin with tone sandhi applied WITHIN the word (不客气 bu4ke4qi5 ->
   bu2ke4qi5, 你好 ni3hao3 -> ni2hao3). VocabSense.pinyin is left alone: it
   is the citation-tone matching key tag_sentences.py uses on later runs.

2. SENTENCE PINYIN -- the whole sentence is sandhied as ONE string, so the
   rules see across word boundaries (一个 yi1 ge4 -> yi2 ge4), then split
   back per word:
     SentenceVocab.pinyin = this occurrence's reading in this sentence
     Sentence.pinyin      = the words' readings joined by spaces
                            ("ni2hao3 hen3 gao1xing4"), the old format
   Each word's reading is its sense's citation pinyin; characters with no
   word row (numbers -- tagging doesn't store them as vocabulary) are read
   with pypinyin. A list word the text writes without 儿 (有点 for the list's
   有点儿 -- imported sentences carry these) is read as written, without the
   final r. If the syllables can't be lined up one-per-character the
   sentence keeps citation tones and is counted (and the first few listed)
   in the summary.

3. PRIMARY SENSE -- each word's primary sense = its earliest-taught sense
   (by unit; JSON meaning order breaks ties), which also refreshes Vocab's
   cached pinyin / english / unit from it. Run after assign_units.py: senses
   without a unit sort last.

4. GRAMMAR EXPLANATION EXAMPLES -- an example that is also a course
   sentence gets that sentence's pinyin (just written in step 2), so it reads
   the same everywhere. AllSet Learning's own pinyin is kept as written.
   See grammar_explanations.fill_course_pinyin.

5. SHORT GRAMMAR TIPS -- each sentence's grammar links get the short tip
   they use (sentence_grammar.part_id), then each tip gets up to 3 example
   course sentences (grammar_tip_examples). See grammar_tips.py.

Step 2 also writes Sentence.glosses, the word-by-word English line: each
word's VocabSense.gloss for the meaning used there, numbers glossed in code
(二十 "twenty"); and Sentence.dictionary, each word with its reading there,
its gloss and the definition of the meaning used there. Words whose meaning has no gloss yet are null -- run
gloss_senses.py first.

Tone sandhi itself is vocab_pinyin_utils.apply_tone_sandhi: 不/一 by rule,
third-tone chains by borrowing only pypinyin's POSITIONS, never its
readings, so a verified reading is never replaced by pypinyin's guess.

USAGE (from the repo root)
    python -m data_pipelines.textbook.scripts.finalize
    python -m data_pipelines.textbook.scripts.finalize --dry-run    # report only
"""
import argparse
import contextlib
import io
import json
import re
import time
from collections import Counter

from pypinyin import lazy_pinyin, Style

from app.textbook.db_utils import get_session, init_db, set_primary_senses_by_earliest_unit
from app.textbook.models import Sentence, SentenceVocab, Vocab, VocabSense

from data_pipelines.textbook.scripts.vocab_pinyin_utils import apply_tone_sandhi
from data_pipelines.textbook.scripts.grammar_explanations import fill_course_pinyin
from data_pipelines.textbook.scripts import grammar_tips
from data_pipelines.textbook.scripts.grammar_tips import sentence_dictionary, sentence_glosses

_SYLLABLE_RE = re.compile(r"([A-Za-züÜvV]+)([1-5])")
_CJK_RE = re.compile(r"[一-鿿]")


def syllables(numeric: str) -> list[str]:
    """"ai4hao4" -> ["ai4", "hao4"]. A 儿 written into the syllable
    ("dianr3", the dataset's style) is split off as its own syllable
    ("dian3", "r5"), so there is one syllable per character, as
    apply_tone_sandhi requires. Case is kept (Bei3jing1)."""
    out = []
    for letters, tone in _SYLLABLE_RE.findall(numeric or ""):
        if letters.lower().endswith("r") and letters.lower() != "er" and len(letters) > 1:
            out += [f"{letters[:-1]}{tone}", "r5"]
        else:
            out.append(f"{letters}{tone}")
    return out


def pypinyin_syllables(text: str) -> list[str]:
    return lazy_pinyin(text, style=Style.TONE3, neutral_tone_with_five=True)


def quiet_sandhi(hanzi: str, numeric: str, stats: Counter) -> str:
    """apply_tone_sandhi prints a line whenever it has to skip; count those
    instead of printing thousands of them."""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        out = apply_tone_sandhi(hanzi, numeric)
    if "[sandhi-skip]" in buf.getvalue():
        stats["sandhi skipped (syllables don't line up with characters)"] += 1
    return out


def is_erhua(word: str, reading: str) -> bool:
    """儿 read as the erhua "r" here (玩儿 wánr), not as "er"."""
    return word == "儿" and (reading or "").lower().startswith("r")


def reading_of(sv: SentenceVocab, word: str) -> list[str]:
    """The sense's citation pinyin; else this occurrence's stored reading
    (words whose sense isn't resolved yet); else pypinyin. Erhua 儿 keeps
    this occurrence's "r5": the dictionary sense says "er2"."""
    if is_erhua(word, sv.reading):
        return ["r5"]
    for source in (sv.sense.pinyin if sv.sense is not None else "", sv.reading or ""):
        if source and "UNKNOWN" not in source:
            sylls = syllables(source)
            if len(sylls) == len(word):
                return sylls
    return pypinyin_syllables(word)


def reading_as_written(sv: SentenceVocab, word: str, surface: str) -> list[str]:
    """The list word 有点儿 written 有点 in the text: its reading without the
    final erhua syllable (you3 dian3, not you3 dian3 r5); else this
    occurrence's stored reading, which tagging took from the text as written;
    else pypinyin of the text."""
    full = reading_of(sv, word)
    if len(full) == len(word):
        return full[:-1]
    stored = syllables(sv.reading or "")
    return stored if len(stored) == len(surface) else pypinyin_syllables(surface)


def finalize_senses(db, stats: Counter) -> list[tuple]:
    examples = []
    for sense, hanzi in db.query(VocabSense, Vocab.hanzi).join(Vocab, Vocab.id == VocabSense.vocab_id).all():
        base = "".join(syllables(sense.pinyin)) or sense.pinyin
        display = quiet_sandhi(hanzi, base, stats) if base else base
        if display != sense.pinyin_display:
            stats["sense display pinyin written"] += 1
        if display != base and len(examples) < 8:
            examples.append((hanzi, base, display))
        sense.pinyin_display = display
    db.flush()
    return examples


UNALIGNED = []          # sentences whose words don't line up with the text (for the summary)


def finalize_sentence(db, sentence: Sentence, stats: Counter):
    """See module docstring, step 2."""
    rows = sorted(sentence.vocab_links, key=lambda r: r.position)
    chars = "".join(_CJK_RE.findall(sentence.hanzi))
    pieces, pos, k = [], 0, 0      # (hanzi, [syllables], row or None)
    while pos < len(chars):
        row = rows[k] if k < len(rows) else None
        word = row.vocab.hanzi if row is not None else None
        if word and chars.startswith(word, pos):
            pieces.append((word, reading_of(row, word), row))
            pos += len(word)
            k += 1
        elif word and word.endswith("儿") and len(word) > 1 and chars.startswith(word[:-1], pos):
            surface = word[:-1]          # 有点 in the text is the list word 有点儿
            pieces.append((surface, reading_as_written(row, word, surface), row))
            pos += len(surface)
            k += 1
        else:                       # a character with no word row (a number)
            pieces.append((chars[pos], pypinyin_syllables(chars[pos]), None))
            pos += 1
    if k != len(rows):
        stats["sentences whose words don't line up with the text (left as is)"] += 1
        UNALIGNED.append((sentence.hanzi, [r.vocab.hanzi for r in rows]))
        return

    text, readings = sandhi_pieces(chars, [(w, s) for w, s, _ in pieces], stats)
    for (_, _, row), reading in zip(pieces, readings):
        if row is not None:
            row.pinyin = reading
    sentence.pinyin = text
    glosses = sentence_glosses(pieces)
    if any(g is None for _, g in glosses):
        stats["sentences whose word-by-word gloss has gaps"] += 1
    sentence.glosses = json.dumps(glosses, ensure_ascii=False)
    sentence.dictionary = json.dumps(sentence_dictionary(pieces, readings), ensure_ascii=False)


def sandhi_pieces(chars: str, pieces: list[tuple[str, list[str]]], stats: Counter) -> tuple[str, list[str]]:
    """[(word, citation syllables), ...] covering `chars` -> (the sentence's
    pinyin in Sentence.pinyin's format, each word's reading). Sandhi runs on
    the whole sentence at once. Also used for grammar-explanation examples
    that are not course sentences (grammar_explanations.py), so both look
    the same."""
    flat = [s for _, sylls in pieces for s in sylls]
    sandhied = quiet_sandhi(chars, "".join(flat), stats)
    out = syllables(sandhied)
    if len(out) != len(flat):
        out = flat
        stats["sentences left in citation tones"] += 1
    elif out != flat:
        stats["sentences changed by tone sandhi"] += 1

    i, words, readings = 0, [], []
    for word, sylls in pieces:
        reading = "".join(out[i:i + len(sylls)])
        i += len(sylls)
        readings.append(reading)
        if is_erhua(word, reading) and words and words[-1][-1:].isdigit():
            # erhua joins the word before it, the dataset's style: 玩儿 -> "wanr2"
            words[-1] = words[-1][:-1] + "r" + words[-1][-1]
            continue
        words.append(reading)
    return " ".join(words), readings


def main():
    parser = argparse.ArgumentParser(description="Display pinyin with tone sandhi, and primary senses.")
    parser.add_argument("--dry-run", action="store_true", help="Compute and report, write nothing.")
    args = parser.parse_args()

    t0 = time.time()
    init_db()
    stats = Counter()
    with get_session() as db:
        print("1. Sense display pinyin (sandhi within the word)...")
        examples = finalize_senses(db, stats)
        for hanzi, base, display in examples:
            print(f"     {hanzi}: {base} -> {display}")

        print("2. Sentence pinyin (sandhi across the whole sentence)...")
        sentences = (db.query(Sentence).join(SentenceVocab, SentenceVocab.sentence_id == Sentence.id)
                     .distinct().order_by(Sentence.id).all())
        shown = 0
        for s in sentences:
            before = s.pinyin
            finalize_sentence(db, s, stats)
            if before and s.pinyin != before and shown < 6 and any(c in s.hanzi for c in "不一"):
                print(f"     {s.hanzi}  {before}  ->  {s.pinyin}")
                shown += 1
        db.flush()

        print("3. Primary sense = earliest-taught sense...")
        changed = set_primary_senses_by_earliest_unit(db)
        stats["words whose primary sense changed"] = changed
        no_unit = db.query(VocabSense).filter(VocabSense.unit_id.is_(None)).count()
        stats["senses with no unit (levels not built yet)"] = no_unit

        print("4. Grammar explanation examples: pinyin from course sentences...")
        filled = fill_course_pinyin(db)
        stats["grammar explanation examples given their course sentence's pinyin"] = filled

        print("5. Short grammar tips: which tip each sentence uses, and examples...")
        grammar_tips.refresh(db)

        print(f"\n{len(sentences)} sentence(s), "
              f"{db.query(VocabSense).count()} sense(s):")
        for k, v in sorted(stats.items()):
            print(f"  {k}: {v}")
        for hanzi, words in UNALIGNED[:20]:
            print(f"    not lined up: {hanzi}  words: {' / '.join(words)}")
        if args.dry_run:
            db.rollback()
            print("Dry run: nothing written.")
    print(f"Done in {time.time() - t0:.0f}s. Next: create_questions.py")


if __name__ == "__main__":
    main()