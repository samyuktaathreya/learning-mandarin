"""
data_pipelines/textbook/test_scripts/try_tatoeba_grammar.py

TRIAL: can Tatoeba supply enough good sentences to drill ONE grammar point
at one HSK level? Runs Tatoeba's Mandarin-English sentence pairs through the
same checks the pipeline uses, and reports how many survive each step.

WORKS ON A COPY of textbook.db (test_scripts/output/scratch_textbook.db):
the real database is never touched. The copy needs a built pipeline run
(vocab list, grammar points), so run main.py first.

STEPS (each sentence stops at the first it fails):
  1. a Tatoeba sentence with an English translation (the first one listed)
     -- Traditional characters converted to Simplified (OpenCC)
  2. contains a marker word of the grammar point (detect_grammar.
     GRAMMAR_MARKERS, e.g. 都 / 一起 / 一块儿 for 1-10), or --contains
  3. not already in the database
  4. quick pre-check: at most MAX_UNKNOWN_CHARS characters that no word of
     this level uses (names like 汤姆 need a couple) -- saves tagging
     thousands of obviously too-hard sentences
  5. tagged into words like any other sentence: every word at this level
     or below (2025 list + overrides) or a name, and the HSK 1 grammar
     restriction doesn't move it up -- i.e. it really is a sentence of
     this level
  6. grammar detection finds the point (都 as the adverb "all", not just
     the character somewhere)
  7. the AI difficulty review passes it at this level (one Haiku call per
     sentence that gets this far; --no-ai skips this step)

OUTPUT: the funnel on screen, every survivor, a sample of what each step
dropped and why, and every sentence with its outcome in
test_scripts/output/<code>_level<N>_<timestamp>.csv.

COST: no AI for steps 1-6 (tagging runs without AI definitions for
unlisted words; HanLP may still ask Haiku to re-split a sentence with an
unknown word, cached in segmentation_cache.json). Step 7: one call per
sentence reaching it, usually a few dozen.

SETUP
    pip install opencc-python-reimplemented
USAGE (from the repo root)
    python data_pipelines/textbook/test_scripts/try_tatoeba_grammar.py --code 1-10 --level 1
    python data_pipelines/textbook/test_scripts/try_tatoeba_grammar.py --code 1-42 --level 1
    python data_pipelines/textbook/test_scripts/try_tatoeba_grammar.py --code 1-10 --level 1 --no-ai
    python data_pipelines/textbook/test_scripts/try_tatoeba_grammar.py --code 1-10 --contains 都   # only this marker
"""
import argparse
import csv
import re
import shutil
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]          # .../learning-mandarin
sys.path.insert(0, str(REPO_ROOT))

TEST_DIR = Path(__file__).resolve().parent
OUT_DIR = TEST_DIR / "output"
SCRATCH_DB = OUT_DIR / "scratch_textbook.db"
TATOEBA_DIR = TEST_DIR.parent / "data" / "raw" / "tatoeba"
MAX_UNKNOWN_CHARS = 2
SAMPLES_PER_STEP = 12
_CJK_RE = re.compile(r"[㐀-鿿]")


def point_db_at_scratch_copy():
    """Copies the real textbook.db and makes the app use the copy. Must
    run before anything imports app.textbook.db_utils (it opens the DB at
    import)."""
    import app.core.config.data as data_cfg
    real = Path(data_cfg.TEXTBOOK_DB)
    if not real.exists():
        raise SystemExit(f"No database at {real} -- run main.py first.")
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    shutil.copy(real, SCRATCH_DB)
    data_cfg.TEXTBOOK_DB = SCRATCH_DB if isinstance(data_cfg.TEXTBOOK_DB, Path) else str(SCRATCH_DB)
    return real


def newest_tsv() -> Path:
    files = sorted(TATOEBA_DIR.glob("*.tsv"), key=lambda p: p.stat().st_mtime)
    if not files:
        raise SystemExit(f"No .tsv in {TATOEBA_DIR}")
    return files[-1]


def read_pairs(path: Path) -> dict:
    """{tatoeba id: {"original": text, "english": first translation}} from
    the 4-column export: cmn id, cmn text, eng id, eng text."""
    out = {}
    with open(path, encoding="utf-8", newline="") as f:
        for line in f:
            parts = line.rstrip("\n").split("\t")
            if len(parts) < 4 or not parts[0].strip().isdigit():
                continue
            tid, text, _, english = parts[0].strip(), parts[1].strip(), parts[2], parts[3].strip()
            if tid not in out and text:
                out[tid] = {"original": text, "english": english}
    return out


def main():
    parser = argparse.ArgumentParser(description="Trial: Tatoeba sentences for one grammar point.")
    parser.add_argument("--code", default="1-10", help="Grammar point code (default 1-10: 都/一起).")
    parser.add_argument("--level", type=int, default=1, help="HSK level to drill it at (default 1).")
    parser.add_argument("--tsv", default=None, help="Tatoeba pairs file (default: newest .tsv in data/raw/tatoeba).")
    parser.add_argument("--contains", nargs="+", default=None, help="Marker words to search for instead.")
    parser.add_argument("--limit", type=int, default=None, help="Tag at most N candidates (after the pre-check).")
    parser.add_argument("--no-ai", action="store_true", help="Skip the AI review (step 7).")
    args = parser.parse_args()

    try:
        from opencc import OpenCC
    except ImportError:
        raise SystemExit("pip install opencc-python-reimplemented  (Traditional -> Simplified)")
    real_db = point_db_at_scratch_copy()

    from app.textbook.db_utils import get_session, init_db, upsert_import_sentence, get_vocab_hanzi_through_level
    from app.textbook.models import GrammarTip, Sentence, SentenceSource, Vocab
    from data_pipelines.textbook.scripts import hsk_vocab_source as hvs
    from data_pipelines.textbook.scripts import tag_sentences as ts
    from data_pipelines.textbook.scripts import detect_grammar as dg
    from data_pipelines.textbook.scripts import review_sentences as rs
    from data_pipelines.textbook.scripts.fill_gaps import blockers
    from app.textbook import db_utils
    if Path(db_utils.engine.url.database).resolve() != SCRATCH_DB.resolve():
        raise SystemExit(f"Safety stop: the app opened {db_utils.engine.url.database}, not the scratch copy. "
                         "Something imported db_utils before this script could redirect it.")

    t0 = time.time()
    tsv = Path(args.tsv) if args.tsv else newest_tsv()
    markers = set(args.contains or dg.GRAMMAR_MARKERS.get(args.code, set()))
    if not markers:
        raise SystemExit(f"No marker words known for {args.code}: pass --contains <words>.")
    print(f"Grammar point {args.code} at HSK {args.level}, marker words: {' '.join(sorted(markers))}")
    print(f"Tatoeba file: {tsv.name}")
    print(f"Working on a copy of {real_db} -> {SCRATCH_DB}\n")

    init_db()
    cc = OpenCC("t2s")
    pairs = read_pairs(tsv)
    funnel, outcome = Counter(), {}                 # tatoeba id -> (step reached, reason)
    dropped = defaultdict(list)                     # step -> [(hanzi, reason)]
    traditional = 0

    with get_session() as db:
        tip = db.query(GrammarTip).filter(GrammarTip.grammar_code == args.code).first()
        if tip is None:
            raise SystemExit(f"No grammar point {args.code} in the DB.")
        print(f"  {args.code}: {tip.raw_text}\n")
        in_db = {h for (h,) in db.query(Sentence.hanzi).all()}
        known_chars = {ch for (h,) in db.query(Vocab.hanzi).filter(Vocab.hsk_level.isnot(None),
                                                                     Vocab.hsk_level <= args.level).all()
                       for ch in h}

        def drop(tid, step, hanzi, reason):
            outcome[tid] = (step, reason)
            dropped[step].append((hanzi, reason))

        candidates = []
        funnel["1. Tatoeba sentences with an English translation"] = len(pairs)
        for tid, p in pairs.items():
            simp = cc.convert(p["original"])
            if simp != p["original"]:
                traditional += 1
            p["hanzi"] = simp
            if not any(m in simp for m in markers):
                outcome[tid] = ("1", "no marker word")
                continue
            funnel["2. contain a marker word"] += 1
            if simp in in_db:
                drop(tid, "3", simp, "already in the database")
                continue
            funnel["3. not already in the database"] += 1
            unknown = {ch for ch in _CJK_RE.findall(simp) if ch not in known_chars}
            if len(unknown) > MAX_UNKNOWN_CHARS:
                drop(tid, "4", simp, f"{len(unknown)} characters no HSK {args.level} word uses: {''.join(sorted(unknown))}")
                continue
            funnel[f"4. at most {MAX_UNKNOWN_CHARS} characters outside HSK {args.level} words"] += 1
            candidates.append(tid)
        if args.limit:
            candidates = candidates[:args.limit]
        print(f"Tagging {len(candidates)} candidate(s)...")

        logger, _ = ts.setup_logging()
        entries = hvs.load_entries(str(ts.VOCAB_PATH))
        run = ts.Run(logger, entries, get_vocab_hanzi_through_level(db, hvs.MAX_LEVEL),
                     ts.load_segmentation_cache(), estimate=True)    # no AI definitions for unlisted words
        points = db.query(GrammarTip).all()
        detectors, _ = dg.build_detectors(points)
        level_of_point = {p.grammar_code: p.hsk_level for p in points}
        scope_cache = {}
        survivors = []

        for k, tid in enumerate(candidates, 1):
            p = pairs[tid]
            sp = db.begin_nested()
            try:
                s, written = upsert_import_sentence(db, hanzi=p["hanzi"], english=p["english"],
                                                    external_id=f"tatoeba-{tid}", source=SentenceSource.external)
                if not written:
                    drop(tid, "5", p["hanzi"], "same text as a sentence in the DB")
                    sp.rollback()
                    continue
                ts.tag_sentence(db, run, s)
                db.expire(s, ["vocab_links"])
                found, bump = dg.detect_sentence(db, s, detectors, level_of_point)
                if s.hsk_level != args.level:
                    why = f"level {s.hsk_level}"
                    causes = blockers(s, args.level) + ([f"grammar {' '.join(bump[2])}"] if bump else [])
                    drop(tid, "5", p["hanzi"], f"{why}: {', '.join(causes) or 'no list words'}")
                    sp.rollback()
                    continue
                funnel[f"5. really HSK {args.level} (words, grammar restriction)"] += 1
                if args.code not in found:
                    drop(tid, "6", p["hanzi"], "marker word there, but not used as this grammar point")
                    sp.rollback()
                    continue
                funnel[f"6. grammar detection finds {args.code}"] += 1
                if args.no_ai:
                    survivors.append((tid, p))
                    outcome[tid] = ("6", "passed (AI review skipped)")
                    sp.rollback()
                    continue
                verdict = rs.review_one(db, s, args.level, scope_cache)
                if "error" in verdict:
                    drop(tid, "7", p["hanzi"], f"AI call failed: {verdict['error'][:80]}")
                elif not verdict.get("ok", True):
                    drop(tid, "7", p["hanzi"], f"AI review: {'; '.join(verdict.get('problems') or [])}")
                else:
                    funnel["7. AI review passes it"] += 1
                    survivors.append((tid, p))
                    outcome[tid] = ("7", "passed")
                sp.rollback()
            except Exception as e:
                sp.rollback()
                drop(tid, "5", p["hanzi"], f"error while tagging: {e!r}")
            if k % 100 == 0:
                print(f"  ...{k}/{len(candidates)}")
        db.rollback()
        ts.save_segmentation_cache(run.seg_cache)

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    report = OUT_DIR / f"{args.code}_level{args.level}_{stamp}.csv"
    with open(report, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["tatoeba_id", "hanzi", "original", "english", "stopped_at_step", "reason"])
        for tid, (step, reason) in outcome.items():
            if reason == "no marker word":
                continue
            p = pairs[tid]
            w.writerow([tid, p["hanzi"], p["original"], p["english"], step, reason])

    print(f"\n=== {args.code} at HSK {args.level}: funnel ===")
    for k in sorted(funnel):
        print(f"  {funnel[k]:>7}  {k}")
    print(f"  ({traditional} Tatoeba sentence(s) were in Traditional characters and converted)")
    print(f"\n=== Survivors ({len(survivors)}) ===")
    for tid, p in survivors:
        print(f"  {p['hanzi']}  ({p['english']})  [tatoeba {tid}]")
    for step in sorted(dropped):
        items = dropped[step]
        print(f"\n--- dropped at step {step} ({len(items)}), first {min(len(items), SAMPLES_PER_STEP)} ---")
        for hanzi, reason in items[:SAMPLES_PER_STEP]:
            print(f"  {hanzi}  -- {reason}")
    print(f"\nEvery sentence and why: {report}")
    print(f"Done in {time.time() - t0:.0f}s. The real database was not changed.")


if __name__ == "__main__":
    main()