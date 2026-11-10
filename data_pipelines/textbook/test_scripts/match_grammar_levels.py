"""
data_pipelines/textbook/test_scripts/match_grammar_levels.py

Our grammar list is the 2021 standard; the word list is the 2025 syllabus.
This compares each grammar point with the 2025 syllabus grammar lists
(github.com/krmanik/HSK-3.0, "New HSK (2025)": data/raw/hsk_grammar_krmanik/
HSK 1.json, HSK 2.json, ...) BY ITS WORDS, and proposes a new level where the
2025 syllabus teaches the point later -- so 愿意 (2-03, HSK 2 in 2021) moves
to HSK 3, where 2025 has it (能愿动词：需要、该、应该、愿意、得).

Nothing else changes: same codes, same rules, same wiki pages. No AI.

RULES FOR A MOVE (conservative):
  - every one of the point's words is found as a word in the 2025 lists
    (a word they don't mention says nothing, so the point stays put);
  - the point moves to the LOWEST 2025 level any of its words has, and only
    if that is higher than its 2021 level (points are never moved down).

OUTPUT: test_scripts/grammar_level_overrides.json -- the moves, each with the
2025 items that justify it. Check it (delete any move you disagree with);
the trial scripts apply it to their scratch copy of the database.

USAGE (from the repo root)
    python data_pipelines/textbook/test_scripts/match_grammar_levels.py
"""
import json
import re
import sys
from pathlib import Path

TEST_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(TEST_DIR))

import try_grammar_rule as tg   # noqa: E402

KRMANIK_DIR = tg.DATA_DIR / "raw" / "hsk_grammar_krmanik"
_CJK = re.compile(r"[㐀-鿿]+")


def item_words(item: dict) -> list[str]:
    """Words of one 2025 item: 需要、该、应该 / 先……，再/然后…… -> 先 再 然后 /
    就3 -> 就 (sense numbers dropped); descriptions (专用名量词, 动词+...) are
    dropped by the same grammar-term filter the trial uses for labels."""
    text = item.get("语法内容") or ""
    text = re.sub(r"[（(][0-9一二三四五六七八九十]+[）)]|[①-⑳]", " ", text)
    if "：" in text:
        text = text.split("：", 1)[1]
    text = re.sub(r"(?<=[㐀-鿿])[0-9]+", "", text)
    words = []
    for run in _CJK.findall(text):
        if any(t in run for t in tg._TERMS) or run in tg._LABEL_STOP:
            continue
        words.append(run)
    return words


def main():
    files = sorted(KRMANIK_DIR.glob("HSK *.json"), key=lambda p: int(re.findall(r"\d+", p.stem)[0]))
    if not files:
        raise SystemExit(f"No 'HSK n.json' files in {KRMANIK_DIR}")
    have = {int(re.findall(r"\d+", p.stem)[0]) for p in files}
    if not {1, 2, 3} <= have:
        # without the lower lists, every word looks like it's only taught later
        raise SystemExit(f"Need at least HSK 1.json, HSK 2.json and HSK 3.json in {KRMANIK_DIR} (found {sorted(have)})")
    by_word = {}
    for f in files:
        level = int(re.findall(r"\d+", f.stem)[0])
        for item in json.loads(f.read_text(encoding="utf-8")):
            desc = "：".join(x for x in (item.get("细目"), item.get("语法内容")) if x)
            for w in item_words(item):
                by_word.setdefault(w, []).append((level, desc))
    print(f"2025 lists: {', '.join(f.name for f in files)} -- {len(by_word)} distinct words")

    real = tg.point_db_at_scratch_copy()
    from app.textbook.db_utils import get_session, init_db
    from app.textbook.models import GrammarTip
    init_db()
    moves, partly, down = {}, [], []
    with get_session() as db:
        for tip in db.query(GrammarTip).order_by(GrammarTip.id).all():
            try:
                point = tg.Point(db, tip.grammar_code, None, need_sources=False)
            except SystemExit:
                continue                                  # no words: nothing to compare
            found = {w: by_word[w] for w in point.markers if w in by_word}
            if not found:
                continue
            if len(found) < len(point.markers):
                partly.append((tip.grammar_code, tip.hsk_level, [w for w in point.markers if w not in found]))
                continue
            new = min(lv for hits in found.values() for lv, _ in hits)
            evidence = {w: sorted({f"HSK {lv}: {d}" for lv, d in hits})[:3] for w, hits in found.items()}
            if new > tip.hsk_level:
                moves[tip.grammar_code] = {"level": new, "was": tip.hsk_level, "label": tip.raw_text,
                                           "evidence": evidence}
            elif new < tip.hsk_level:
                down.append((tip.grammar_code, tip.hsk_level, new, tip.raw_text))
        db.rollback()

    out = tg.TEST_DIR / "grammar_level_overrides.json"
    out.write_text(json.dumps({
        "_readme": ("Grammar points moved to the level where the 2025 syllabus teaches them (all their words "
                    "are only in higher 2025 lists). Written by match_grammar_levels.py; delete any move you "
                    "disagree with. Applied to the trial scripts' scratch database."),
        **moves}, ensure_ascii=False, indent=1), encoding="utf-8")

    print(f"\n=== {len(moves)} point(s) move UP to their 2025 level ===")
    for c, m in moves.items():
        print(f"  {c}: HSK {m['was']} -> HSK {m['level']}   {m['label']}")
        for w, ev in m["evidence"].items():
            print(f"      {w}: {' | '.join(ev)}")
    print(f"\n({len(down)} point(s) are taught EARLIER in 2025 -- left as they are:)")
    for c, old, new, label in down[:30]:
        print(f"  {c}: 2021 HSK {old}, 2025 has it at HSK {new}   {label}")
    print(f"\n({len(partly)} point(s) have words the 2025 lists don't mention -- left as they are)")
    print(f"\nWritten: {out}")


if __name__ == "__main__":
    main()