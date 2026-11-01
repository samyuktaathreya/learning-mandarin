"""
data_pipelines/textbook/scripts/review_sentences.py

Pipeline stage 4b: an AI difficulty check on every sentence of the levels
being built. Word lists and rule-based grammar detection can't see
everything: 他不怎么爱说话 has only HSK 1 words and passes the grammar rules,
but 不怎么 "not very" is far beyond a beginner, and 我在一家大公司工作 uses
家 as a measure word when HSK 1 learners only know 家 = home.

ONE AI CALL PER SENTENCE (Haiku, in parallel) with the sentence, its English,
its level, and the grammar points of that level and below. A sentence is
TOO HARD if it
  1. needs grammar beyond the level, or stacks several constructions;
  2. uses a word in a meaning beyond the basic one a learner at this level
     knows for it (家 as a measure word, 爱 as "tend to");
  3. is an idiom / set phrase whose meaning isn't clear from its words.
A too-hard sentence moves UP ONE LEVEL (HSK 1 -> 2) and is checked again
there in the same run, against that level's grammar. Levels are done lowest
first. Its grammar links are redone at the new level (detect_grammar applies
the stored verdicts whenever it computes a level, so re-running the grammar
stage keeps the move).

Verdicts are kept per sentence and level (Sentence.review, JSON), so a
re-run only asks about sentences not judged at their current level yet --
and re-applies the moves it already decided (e.g. after detect_grammar
recomputed levels). --redo asks again.

fill_gaps.py uses review_one() on each sentence it would import, so
fill-in sentences pass the same check.

Every verdict is logged to debug/review_sentences/<timestamp>.txt; the
summary lists the sentences moved up and why.

USAGE (from the repo root)
    python -m data_pipelines.textbook.scripts.review_sentences --levels 1 2
    python -m data_pipelines.textbook.scripts.review_sentences --levels 1 --estimate   # count calls only
    python -m data_pipelines.textbook.scripts.review_sentences --levels 1 2 --redo     # ask again
"""
import argparse
import json
import logging
import os
import re
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

import anthropic

from app.core.config.shared import settings
from app.textbook.db_utils import get_session, init_db
from app.textbook.models import GrammarTip, Sentence

from data_pipelines.textbook.scripts import detect_grammar as dg

HAIKU_MODEL = "claude-haiku-4-5"
MAX_TOKENS = 300
DEFAULT_WORKERS = 8
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_DIR = os.path.abspath(os.path.join(SCRIPT_DIR, "..", "debug", "review_sentences"))

api_key = settings.CLAUDE_API_KEY
client = anthropic.Anthropic(api_key=api_key, max_retries=6) if api_key else None

# Shown to the reviewer at HSK 1 only, as examples of what is out of scope.
# Shown to the reviewer at HSK 1 only: what IS too hard there.
HSK1_EXAMPLES = (
    'Examples of TOO HARD at HSK 1: 不怎么 "not very" (他不怎么爱说话); 不怎么样; a verb + 一下 '
    '(我来介绍一下); 家 as a measure word for a company or shop (我在一家大公司工作 -- learners know '
    '家 only as home/family; 我家 "my home" is fine); 爱 meaning "tend to"; 再也不 / 不再; double '
    'negatives like 不能不; potential complements like 想不到; 看起来; 游来游去.'
)

# Ordinary beginner material the reviewer must NOT reject (it did, before
# REVIEW_PROMPT_VERSION 2: about 40% of HSK 1 rejected, mostly for
# "vocabulary not in the list", which it can't know).
FINE_EXAMPLES = (
    "These are all FINE: 谢谢你！不客气。 / 你好，很高兴认识你。 / 今天是星期几？ / 现在是上午九点半。 / "
    "我早上七点起床。 / 这个八块钱。 / 他喝了一杯冷水。 / 一半是我的，一半是你的。 / 我们是一家人。 / "
    "你怎么了？ / 水果很好吃，我买一些。 -- greetings and courtesy phrases, times and dates, prices, "
    "possessive 的, simple 了, a measure word with a number, two short clauses joined by a comma."
)


# --------------------------------- VERDICTS ---------------------------------

def verdicts(s: Sentence) -> dict:
    """{level: verdict} from Sentence.review, only verdicts made with the
    current prompt (an older prompt's verdict counts as not asked yet)."""
    return {lvl: v for lvl, v in _all_verdicts(s).items()
            if isinstance(v, dict) and v.get("v") == dg.REVIEW_PROMPT_VERSION}


def _all_verdicts(s: Sentence) -> dict:
    try:
        return json.loads(s.review) if s.review else {}
    except json.JSONDecodeError:
        return {}


def store(s: Sentence, level: int, verdict: dict):
    v = verdicts(s)
    v[str(level)] = {**verdict, "v": dg.REVIEW_PROMPT_VERSION}
    s.review = json.dumps(v, ensure_ascii=False)


_GRAMMAR = {}


def relink(db, s: Sentence):
    """After a verdict: recompute the sentence's level (detect_grammar
    applies the stored verdicts, so a too-hard sentence moves up) and its
    grammar links at that level."""
    if not _GRAMMAR:
        points = db.query(GrammarTip).all()
        _GRAMMAR["detectors"], _ = dg.build_detectors(points)
        _GRAMMAR["level_of_point"] = {p.grammar_code: p.hsk_level for p in points}
        _GRAMMAR["id_of"] = {p.grammar_code: p.id for p in points}
    found, _ = dg.detect_sentence(db, s, _GRAMMAR["detectors"], _GRAMMAR["level_of_point"])
    dg.write_links(db, s, found, _GRAMMAR["id_of"])


# --------------------------------- AI ---------------------------------

def grammar_scope(db, level: int) -> str:
    """The grammar points of `level` and below, one per line ("1-16 介词：在")."""
    rows = (db.query(GrammarTip.grammar_code, GrammarTip.raw_text)
            .filter(GrammarTip.hsk_level <= level).order_by(GrammarTip.id).all())
    return "\n".join(f"{code} {text}" for code, text in rows)


def build_prompt(hanzi: str, english: str, level: int, scope: str, words: list[str]) -> str:
    known = "HSK 1" if level == 1 else f"HSK 1-{level}"
    return (
        "You are checking example sentences for a Mandarin course for beginners. This sentence is "
        f"taught at HSK {level} (2025 syllabus).\n\n"
        f"Sentence: {hanzi}\n"
        f"English: {english or '(none)'}\n"
        f"Its words: {' / '.join(words) if words else '(not available)'}\n\n"
        "VOCABULARY IS ALREADY CHECKED: every word above is in the course's word list for this "
        "level, or is a name. Never reject a sentence because of vocabulary or because you think a "
        "word is not on the list -- you do not have the list.\n\n"
        f"Grammar points learners know ({known}):\n{scope}\n\n"
        "Mark the sentence TOO HARD only if a learner at this level clearly could NOT work it out:\n"
        "1. it relies on a construction well beyond this level (not just a word you find advanced);\n"
        "2. a word is used in a clearly different meaning from its everyday one;\n"
        "3. it is an idiom whose meaning can't be guessed from its words (everyday set phrases "
        "like 不客气, 没关系, 对不起 are fine -- they are taught as words).\n"
        + (HSK1_EXAMPLES + "\n" if level == 1 else "")
        + FINE_EXAMPLES + "\n"
        "If you are unsure, it is FINE.\n\n"
        'Respond with ONLY a JSON object: {"ok": true} or {"ok": false, "problems": ["<short reason>", ...]}'
    )


def _extract_json(text: str) -> dict:
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise ValueError("no JSON object in the answer")
    return json.loads(m.group(0))


def sentence_words(s: Sentence) -> list[str]:
    return [r.vocab.hanzi for r in sorted(s.vocab_links, key=lambda r: r.position)]


def ask(hanzi: str, english: str, level: int, scope: str, words: list[str] | None = None) -> dict:
    """ONE call. {"ok": bool, "problems": [...]} or {"error": "..."}."""
    if client is None:
        return {"error": "CLAUDE_API_KEY is not set"}
    try:
        response = client.messages.create(model=HAIKU_MODEL, max_tokens=MAX_TOKENS, messages=[
            {"role": "user", "content": build_prompt(hanzi, english, level, scope, words or [])}])
        text = "".join(b.text for b in response.content if hasattr(b, "text"))
        result = _extract_json(text)
    except Exception as e:
        return {"error": f"{e!r}"}
    ok = result.get("ok")
    if not isinstance(ok, bool):
        return {"error": f"answer has no true/false 'ok': {result!r}"}
    problems = [str(p) for p in (result.get("problems") or [])] if not ok else []
    return {"ok": ok, "problems": problems}


def review_one(db, s: Sentence, level: int, scope_cache: dict | None = None) -> dict:
    """fill_gaps.py: judge one sentence at `level` now (or reuse a stored
    verdict), store the verdict, and return it. Does NOT move the sentence:
    the caller decides. An API failure returns {"ok": True, "error": ...}
    so a flaky call never silently drops a fill-in sentence -- it is logged
    and the next review_sentences run asks again."""
    stored = verdicts(s).get(str(level))
    if stored is not None:
        return stored
    if scope_cache is None:
        scope_cache = {}
    if level not in scope_cache:
        scope_cache[level] = grammar_scope(db, level)
    result = ask(s.hanzi, s.english, level, scope_cache[level], sentence_words(s))
    if "error" in result:
        return {"ok": True, "error": result["error"]}
    store(s, level, result)
    return result


# --------------------------------- MAIN ---------------------------------

def setup_logging():
    os.makedirs(LOG_DIR, exist_ok=True)
    path = os.path.join(LOG_DIR, f"{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt")
    log = logging.getLogger(f"review_sentences_{path}")
    log.setLevel(logging.INFO)
    log.propagate = False
    fh = logging.FileHandler(path, encoding="utf-8")
    fh.setFormatter(logging.Formatter("%(asctime)s  %(levelname)-7s  %(message)s"))
    log.addHandler(fh)
    return log, path


def review_level(db, level: int, args, log, stats: Counter, moved: list):
    sentences = db.query(Sentence).filter(Sentence.hsk_level == level).order_by(Sentence.id).all()
    if args.limit:
        sentences = sentences[:args.limit]
    todo = []
    for s in sentences:
        stored = None if args.redo else verdicts(s).get(str(level))
        if stored is None:
            todo.append(s)
        elif not stored.get("ok", True):
            relink(db, s)                            # decided on an earlier run: apply it again
            stats[f"HSK {level}: moved up (verdict from an earlier run)"] += 1
        else:
            stats[f"HSK {level}: fine (verdict from an earlier run)"] += 1
    print(f"HSK {level}: {len(sentences)} sentence(s), {len(todo)} need an AI check.")
    if args.estimate or not todo:
        return len(todo)

    scope = grammar_scope(db, level)
    jobs = {s.id: (s.hanzi, s.english, sentence_words(s)) for s in todo}
    by_id = {s.id: s for s in todo}
    failed = []
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(ask, hanzi, english, level, scope, words): sid
                   for sid, (hanzi, english, words) in jobs.items()}
        for k, f in enumerate(as_completed(futures), 1):
            sid, result = futures[f], f.result()
            s = by_id[sid]
            if "error" in result:
                failed.append(s)
                log.warning(f"FAILED   HSK {level}  {s.hanzi}: {result['error']}")
                continue
            store(s, level, result)
            if result["ok"]:
                stats[f"HSK {level}: fine"] += 1
                log.info(f"OK       HSK {level}  {s.hanzi}")
            else:
                relink(db, s)
                stats[f"HSK {level}: too hard, moved up"] += 1
                moved.append((level, s.hanzi, s.english, result["problems"]))
                log.info(f"TOO HARD HSK {level}  {s.hanzi}  -- {'; '.join(result['problems'])}")
            if k % 100 == 0:
                db.commit()
                print(f"  ...{k}/{len(jobs)}")
    db.commit()
    if failed:
        stats[f"HSK {level}: AI call failed (run again)"] += len(failed)
    return len(todo)


def main():
    parser = argparse.ArgumentParser(description="AI check: is each sentence right for its HSK level?")
    parser.add_argument("--levels", nargs="+", type=int, required=True)
    parser.add_argument("--redo", action="store_true", help="Ask again even where a verdict is stored.")
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS)
    parser.add_argument("--limit", type=int, default=None, help="Only the first N sentences per level (testing).")
    parser.add_argument("--estimate", action="store_true", help="Count the calls needed; change nothing.")
    args = parser.parse_args()

    if client is None and not args.estimate:
        raise SystemExit("CLAUDE_API_KEY is not set. Set it, or use --estimate.")
    t0 = time.time()
    init_db()
    log, log_path = setup_logging()
    stats, moved = Counter(), []
    with get_session() as db:
        if not db.query(GrammarTip).count():
            raise SystemExit("No grammar points in the DB -- run load_hsk_lists.py first.")
        calls = 0
        for level in sorted(set(args.levels)):        # lowest first: moved sentences are checked again
            calls += review_level(db, level, args, log, stats, moved)
        if args.estimate:
            db.rollback()
            print(f"Estimate only: about {calls} call(s) (more if sentences move up a level). Nothing written.")
            return

    print(f"\nDone in {time.time() - t0:.0f}s.")
    for k, v in sorted(stats.items()):
        print(f"  {k}: {v}")
    if moved:
        print(f"\n  Moved up ({len(moved)}), first 30:")
        for level, hanzi, english, problems in moved[:30]:
            print(f"    HSK {level} -> {level + 1}: {hanzi}  ({english})  -- {'; '.join(problems)}")
    print(f"Every verdict: {log_path}")
    if any("failed" in k for k in stats):
        raise SystemExit("Some AI calls failed -- run this stage again (it only asks what's missing).")


if __name__ == "__main__":
    main()