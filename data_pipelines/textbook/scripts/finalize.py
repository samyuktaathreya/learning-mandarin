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
   with pypinyin. If the syllables can't be lined up one-per-character the
   sentence keeps citation tones and is counted in the summary.

3. PRIMARY SENSE -- each word's primary sense = its earliest-taught sense
   (by unit; JSON meaning order breaks ties), which also refreshes Vocab's
   cached pinyin / english / unit from it. Run after assign_units.py: senses
   without a unit sort last.

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
import re
import time
from collections import Counter

from pypinyin import lazy_pinyin, Style

from app.textbook.db_utils import get_session, init_db, set_primary_senses_by_earliest_unit
from app.textbook.models import Sentence, SentenceVocab, Vocab, VocabSense

from data_pipelines.textbook.scripts.vocab_pinyin_utils import apply_tone_sandhi

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


def reading_of(sv: SentenceVocab, word: str) -> list[str]:
    """The sense's citation pinyin; else this occurrence's stored reading
    (words whose sense isn't resolved yet); else pypinyin."""
    for source in (sv.sense.pinyin if sv.sense is not None else "", sv.reading or ""):
        if source and "UNKNOWN" not in source:
            sylls = syllables(source)
            if len(sylls) == len(word):
                return sylls
    return pypinyin_syllables(word)


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
        else:                       # a character with no word row (a number)
            pieces.append((chars[pos], pypinyin_syllables(chars[pos]), None))
            pos += 1
    if k != len(rows):
        stats["sentences whose words don't line up with the text (left as is)"] += 1
        return

    flat = [s for _, sylls, _ in pieces for s in sylls]
    sandhied = quiet_sandhi(chars, "".join(flat), stats)
    out = syllables(sandhied)
    if len(out) != len(flat):
        out = flat
        stats["sentences left in citation tones"] += 1
    elif out != flat:
        stats["sentences changed by tone sandhi"] += 1

    i, words = 0, []
    for word, sylls, row in pieces:
        reading = "".join(out[i:i + len(sylls)])
        i += len(sylls)
        words.append(reading)
        if row is not None:
            row.pinyin = reading
    sentence.pinyin = " ".join(words)


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

        print(f"\n{len(sentences)} sentence(s), "
              f"{db.query(VocabSense).count()} sense(s):")
        for k, v in sorted(stats.items()):
            print(f"  {k}: {v}")
        if args.dry_run:
            db.rollback()
            print("Dry run: nothing written.")
    print(f"Done in {time.time() - t0:.0f}s. Next: create_questions.py")


if __name__ == "__main__":
    main()