"""
data_pipelines/textbook/scripts/grammar_tips.py

The SHORT grammar tips learners see (GrammarTipPart), their examples
(GrammarTipExample), and word-by-word glosses (Sentence.glosses).

  load_tip_parts(db)   data/grammar/grammar_tip_parts.json -> GrammarTipPart rows
                       (written by test_scripts/run_all_grammar.py --export; the
                       text is WRITTEN BY AI, generated_by says which model).
                       Run by load_hsk_lists.py.
  assign_parts(db)     sentence_grammar.part_id: which tip of the point each
                       sentence uses -- the tip whose words are in the sentence,
                       on word boundaries (the 多 tip doesn't match 多少; 有一点
                       matches 有 + 一点), the longest one winning -- else the
                       point's general tip (words []), else NULL.
  pick_examples(db)    up to EXAMPLES_PER_TIP course sentences per tip, chosen in
                       code: sentences that use the tip, with English and a full
                       word-by-word gloss. Sentences with the tip's own words
                       first (its "words", or the Chinese in its title and pattern
                       lines: 很 for "Subj. + 很 + Adj."), then units at the point's
                       level before the next units; earliest unit first (the
                       learner knows its words), then with audio, then real before
                       AI-written, then shortest.
  sentence_glosses(..) the word-by-word line for one sentence (finalize writes
                       Sentence.glosses with it): each word's VocabSense.gloss for
                       the meaning used there; numbers glossed in code.
finalize.py runs assign_parts + pick_examples after the sentences' pinyin and
glosses; load_hsk_lists.py runs them too after loading the tips, so either
order works. All of it is safe to re-run.
"""
import json
import re
from collections import Counter

from data_pipelines.textbook.scripts import grammar_dsl

EXAMPLES_PER_TIP = 3
_CJK_RUN = re.compile(r"[㐀-鿿]+")

_DIGITS = {"零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
_UNITS = {"十": 10, "百": 100, "千": 1000, "万": 10000, "亿": 100000000}
_ONES = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven",
         "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen", "nineteen"]
_TENS = ["", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"]


def is_numeral(ch: str) -> bool:
    return ch in _DIGITS or ch in _UNITS


def chinese_number(text: str) -> int | None:
    """"二十" -> 20, "十五" -> 15, "一百零五" -> 105, "两万" -> 20000; digit by
    digit when there are no units ("一九九八" -> 1998). None if not a number."""
    if not text or not all(is_numeral(c) for c in text):
        return None
    if not any(c in _UNITS for c in text):
        return int("".join(str(_DIGITS[c]) for c in text))
    total, section, digit = 0, 0, None
    for c in text:
        if c in _DIGITS:
            digit = _DIGITS[c]
        else:
            unit = _UNITS[c]
            if unit >= 10000:
                section = (section + (digit or 0)) * unit
                total += section
                section = 0
            else:
                section += (1 if digit is None else digit) * unit
            digit = None
    return total + section + (digit or 0)


_ORDINALS = ["zeroth", "first", "second", "third", "fourth", "fifth", "sixth", "seventh", "eighth", "ninth",
             "tenth", "eleventh", "twelfth"]


def number_gloss(text: str) -> str | None:
    """"二十" -> "twenty", "第三" -> "third"; None if not a number."""
    if text.startswith("第") and len(text) > 1:
        n = chinese_number(text[1:])
        if n is None:
            return None
        if n < len(_ORDINALS):
            return _ORDINALS[n]
        return f"{n}{'th' if 10 <= n % 100 <= 20 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"
    n = chinese_number(text)
    if n is None:
        return None
    if n < 20:
        return _ONES[n]
    if n < 100:
        return _TENS[n // 10] + ("" if n % 10 == 0 else "-" + _ONES[n % 10])
    return str(n)


def sense_gloss(row) -> str | None:
    """The gloss of the meaning used in this sentence; a word whose meaning
    isn't resolved here falls back to its primary meaning's gloss."""
    if row.sense is not None and row.sense.gloss:
        return row.sense.gloss
    primary = next((s for s in row.vocab.senses if s.is_primary and s.gloss), None)
    return primary.gloss if primary else None


def sentence_glosses(pieces: list[tuple]) -> list[list]:
    """pieces: finalize's [(surface, syllables, SentenceVocab row or None), ...]
    covering every Chinese character. Runs of characters with no word row
    that form a number become one entry ("二十" -> "twenty")."""
    out, i = [], 0
    while i < len(pieces):
        surface, _, row = pieces[i]
        if row is not None:
            out.append([surface, sense_gloss(row)])
            i += 1
            continue
        j = i + (surface == "第")          # 第一 "first": tagging stores ordinals as numbers, not words
        while j < len(pieces) and pieces[j][2] is None and len(pieces[j][0]) == 1 and is_numeral(pieces[j][0]):
            j += 1
        if j > i + (surface == "第"):
            text = "".join(p[0] for p in pieces[i:j])
            out.append([text, number_gloss(text)])
            i = j
        else:
            out.append([surface, None])
            i += 1
    return out


def sentence_dictionary(pieces: list[tuple], readings: list[str]) -> list[dict]:
    """Each word of the sentence with its reading here, gloss and the
    definition of the meaning used here (Sentence.dictionary). Same grouping
    as sentence_glosses: a number written in several characters is one entry."""
    out, i = [], 0
    while i < len(pieces):
        surface, _, row = pieces[i]
        if row is not None:
            sense = row.sense
            if sense is None or not sense.english:
                sense = next((s for s in row.vocab.senses if s.is_primary), sense)
            out.append({"word": surface, "list_word": row.vocab.hanzi if row.vocab.hanzi != surface else None,
                        "pinyin": readings[i], "gloss": sense_gloss(row),
                        "definition": sense.english if sense is not None else None})
            i += 1
            continue
        j = i + (surface == "第")
        while j < len(pieces) and pieces[j][2] is None and len(pieces[j][0]) == 1 and is_numeral(pieces[j][0]):
            j += 1
        if j > i + (surface == "第"):
            text = "".join(p[0] for p in pieces[i:j])
            g = number_gloss(text)
            out.append({"word": text, "list_word": None, "pinyin": "".join(readings[i:j]), "gloss": g,
                        "definition": g})
            i = j
        else:
            out.append({"word": surface, "list_word": None, "pinyin": readings[i], "gloss": None, "definition": None})
            i += 1
    return out


# --------------------------------- tips ---------------------------------

def _loads(text, default):
    try:
        return json.loads(text) if text else default
    except (TypeError, json.JSONDecodeError):
        return default


def load_tip_parts(db) -> dict:
    """Replaces each point's tips with the file's. Their examples and the
    sentences' part_id are cleared for those points (assign_parts and
    pick_examples rebuild them)."""
    from app.textbook.models import GrammarTip, GrammarTipExample, GrammarTipPart, SentenceGrammar
    data = grammar_dsl.read_json(grammar_dsl.TIP_PARTS_FILE, None)
    if data is None:
        print(f"  short tips: {grammar_dsl.TIP_PARTS_FILE} not found -- skipped "
              "(run test_scripts/run_all_grammar.py --export)")
        return {"points": 0, "tips": 0}
    tips = {t.grammar_code: t for t in db.query(GrammarTip).all()}
    unknown, n_points, n_parts = [], 0, 0
    for code, parts in data.items():
        if code.startswith("_"):
            continue
        tip = tips.get(code)
        if tip is None:
            unknown.append(code)
            continue
        old = [p.id for p in db.query(GrammarTipPart.id).filter(GrammarTipPart.grammar_tip_id == tip.id)]
        if old:
            db.query(SentenceGrammar).filter(SentenceGrammar.part_id.in_(old)).update(
                {SentenceGrammar.part_id: None}, synchronize_session=False)
            db.query(GrammarTipExample).filter(GrammarTipExample.part_id.in_(old)).delete(synchronize_session=False)
            db.query(GrammarTipPart).filter(GrammarTipPart.id.in_(old)).delete(synchronize_session=False)
        for pos, p in enumerate(parts):
            db.add(GrammarTipPart(
                grammar_tip_id=tip.id, position=pos,
                words=json.dumps(p.get("words") or [], ensure_ascii=False),
                title=p.get("title") or "", structure=json.dumps(p.get("structure") or [], ensure_ascii=False),
                meaning=p.get("meaning") or "", placement=p.get("placement") or None,
                english_note=p.get("english_note") or None, text=p.get("text") or "",
                generated_by=p.get("generated_by"),
                sources=json.dumps(p.get("sources") or [], ensure_ascii=False)))
            n_parts += 1
        n_points += 1
    db.flush()
    db.expire_all()
    print(f"  short tips (written by AI): {n_parts} tip(s) for {n_points} grammar point(s)")
    if unknown:
        print(f"    [warning] {len(unknown)} code(s) not in the grammar list (skipped): {unknown[:10]}")
    return {"points": n_points, "tips": n_parts}


def word_spans(hanzi: str, glosses) -> set | None:
    """Every run of 1-4 consecutive words of the sentence (from its
    word-by-word split), so a tip's word matches only on word boundaries.
    A list word written without 儿 also counts with it (有点 -> 有点儿).
    None when the sentence has no split yet (then plain text is used)."""
    if not glosses:
        return None
    words = [w for w, _ in glosses]
    out = set()
    for i in range(len(words)):
        for j in range(i + 1, min(i + 4, len(words)) + 1):
            run = "".join(words[i:j])
            out.update((run, run + "儿"))
        if len(words[i]) > 1 and all(is_numeral(ch) for ch in words[i]):
            out.update(words[i])          # 一千 is one number token: the 千 tip still matches it
    return out


def assign_parts(db) -> Counter:
    from app.textbook.models import GrammarTipPart, Sentence, SentenceGrammar
    parts_of = {}
    for p in db.query(GrammarTipPart).order_by(GrammarTipPart.grammar_tip_id, GrammarTipPart.position):
        parts_of.setdefault(p.grammar_tip_id, []).append((p.id, [w for w in _loads(p.words, []) if w]))
    stats = Counter()
    rows = (db.query(SentenceGrammar, Sentence.hanzi, Sentence.glosses)
            .join(Sentence, Sentence.id == SentenceGrammar.sentence_id).all())
    for link, hanzi, glosses in rows:
        parts = parts_of.get(link.grammar_tip_id)
        if not parts:
            if link.part_id is not None:
                link.part_id = None
            continue
        spans = word_spans(hanzi, _loads(glosses, None))
        best, best_len = None, 0
        for pid, words in parts:
            found = max((len(w) for w in words if (w in spans if spans is not None else w in hanzi)), default=0)
            if found > best_len:
                best, best_len = pid, found
        if best is None:
            best = next((pid for pid, words in parts if not words), None)
            stats["sentence links given the point's general tip"] += best is not None
        stats["sentence links with no tip that fits (part_id NULL)"] += best is None
        stats["sentence links matched to a tip by its words"] += best is not None and best_len > 0
        link.part_id = best
    db.flush()
    return stats


def pick_examples(db) -> Counter:
    from app.textbook.models import (GrammarTip, GrammarTipExample, GrammarTipPart, Sentence, SentenceGrammar,
                                     SentenceSource, Unit)
    db.query(GrammarTipExample).delete(synchronize_session=False)
    stats = Counter()
    level_of = dict(db.query(GrammarTipPart.id, GrammarTip.hsk_level)
                    .join(GrammarTip, GrammarTip.id == GrammarTipPart.grammar_tip_id).all())
    keys_of = {}
    for p in db.query(GrammarTipPart):
        keys = set(_loads(p.words, [])) | set(_CJK_RUN.findall(p.title or ""))
        for line in _loads(p.structure, []):
            keys.update(_CJK_RUN.findall(str(line)))
        keys_of[p.id] = {k for k in keys if k}
    rows = (db.query(SentenceGrammar.part_id, Sentence, Unit.hsk_level, Unit.unit_number)
            .join(Sentence, Sentence.id == SentenceGrammar.sentence_id)
            .join(Unit, Unit.id == Sentence.unit_id)
            .filter(SentenceGrammar.part_id.isnot(None), Sentence.english != "")
            .all())
    candidates, sentence_of = {}, {}
    for part_id, s, unit_level, unit_number in rows:
        glosses = _loads(s.glosses, None)
        if not glosses or any(g is None for _, g in glosses):
            stats["candidate sentences skipped: word-by-word gloss incomplete"] += 1
            continue
        spans = word_spans(s.hanzi, glosses)
        has_key = any(k in spans for k in keys_of.get(part_id, ()))
        key = (not has_key, unit_level > level_of.get(part_id, 0), unit_level, unit_number, s.audio_normal is None, s.source == SentenceSource.ai_generated,
               len(s.hanzi), s.id)
        candidates.setdefault(part_id, []).append((key, s.id, "".join(_CJK_RUN.findall(s.hanzi))))
        sentence_of[s.id] = s
    for part_id in level_of:
        chosen, seen = [], set()
        for _, sid, text in sorted(candidates.get(part_id, [])):
            if text not in seen:                   # 为什么? and 为什么？ are the same example
                seen.add(text)
                chosen.append(sid)
            if len(chosen) == EXAMPLES_PER_TIP:
                break
        for pos, sid in enumerate(chosen):
            s = sentence_of[sid]
            db.add(GrammarTipExample(part_id=part_id, sentence_id=sid, position=pos, hanzi=s.hanzi,
                                     pinyin=s.pinyin, english=s.english, dictionary=s.dictionary))
        stats[f"tips with {len(chosen)} example(s)"] += 1
    db.flush()
    return stats


def refresh(db, label: str = "") -> None:
    """assign_parts + pick_examples, with a short summary."""
    stats = assign_parts(db) + pick_examples(db)
    print(f"  short tips{label}: " + "; ".join(f"{k}: {v}" for k, v in sorted(stats.items())))
