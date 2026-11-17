"""
data_pipelines/textbook/scripts/gloss_senses.py

Pipeline stage 4d (after resolve_senses): VocabSense.gloss, each word
meaning in 1-3 English words, for word-by-word lines under sentences:

    多   吃   饭
    more eat  food

WRITTEN BY AI (Haiku), one batch of meanings per call. Each meaning is
sent with its dictionary definition and a short course sentence that uses
it (with the English), so the gloss fits the meaning actually taught ("to
return" for 还 huan2, "still" for 还 hai2). Function words get a short hint
in brackets: 吗 "(question)", 了 "(done)", 的 "'s".
Checked in code: 1-3 words outside brackets, at most 24 characters, plain
English letters. A failed answer is asked again once, saying why; after
that the meaning keeps no gloss and is listed (finalize then leaves that
word's gloss null, and the sentence isn't used as a tip example).

Only meanings that sentences of the levels you build use (plus those
words' primary meanings) and that have no gloss yet are sent, so a re-run
asks nothing new. --redo asks again for all of them.

HAND GLOSSES: data/gloss_fixes.json ({"正在": "(in progress)"}) -- every
meaning of that word gets the gloss, before and instead of the AI.

USAGE (from the repo root)
    python -m data_pipelines.textbook.scripts.gloss_senses --levels 1 2
    python -m data_pipelines.textbook.scripts.gloss_senses --levels 1 2 --estimate   # count, no AI
    python -m data_pipelines.textbook.scripts.gloss_senses --levels 1 2 --redo
"""
import argparse
from pathlib import Path
import json
import re
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

import anthropic
from sqlalchemy import func

from app.core.config.shared import settings
from app.textbook.db_utils import get_session, init_db
from app.textbook.models import Sentence, SentenceVocab, Vocab, VocabSense

MODEL = "claude-haiku-4-5"
BATCH = 25
WORKERS = 8

api_key = settings.CLAUDE_API_KEY
client = anthropic.Anthropic(api_key=api_key, max_retries=6) if api_key else None

FIXES_PATH = Path(__file__).resolve().parents[1] / "data" / "gloss_fixes.json"

_ALLOWED = re.compile(r"^[A-Za-z0-9 '’()\-/.,?!]+$")


def check_gloss(gloss) -> str | None:
    """None if fine, else what's wrong."""
    if not isinstance(gloss, str) or not gloss.strip():
        return "empty"
    g = gloss.strip()
    if len(g) > 24:
        return f"'{g}' is longer than 24 characters"
    if not _ALLOWED.match(g):
        return f"'{g}' has characters other than plain English letters"
    words = re.sub(r"\([^)]*\)", " ", g).split()
    if len(words) > 3:
        return f"'{g}' is more than 3 words"
    return None


def prompt_for(items: list[dict]) -> str:
    lines = []
    for it in items:
        line = f'{it["i"]}. {it["hanzi"]} [{it["pinyin"]}]  meaning: {it["english"]}'
        if it.get("sentence"):
            line += f'\n   used in: {it["sentence"]}' + (f'  ({it["sentence_en"]})' if it.get("sentence_en") else "")
        if it.get("problem"):
            line += f"\n   (your last answer was wrong: {it['problem']})"
        lines.append(line)
    return f"""For a Chinese course, write a word-by-word gloss for each word meaning below: what a learner
would see written under the word, like

    多   吃   饭
    more eat  food

Rules:
- 1 to 3 English words, for THIS meaning (the "used in" sentence shows it).
- Verbs without "to" (eat, not "to eat"). Nouns without "a"/"the". Names as the name (Beijing).
- Words that have no English equivalent get a very short hint in brackets:
  吗 "(question)", 呢 "(question)", 了 "(done)" or "(change)", 的 "'s", 吧 "(suggestion)",
  个 "(measure word)", 着 "(-ing)", 过 "(ever)". Keep these short.
- Plain English letters only, no Chinese, no pinyin.

{chr(10).join(lines)}

Respond with ONLY a JSON object:
{{"glosses": [{{"i": <number>, "gloss": "<gloss>"}}, ...]}}"""


def extract_json(text: str) -> dict:
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("no JSON object in the answer")
    return json.loads(text[start:end + 1])


def ask(items: list[dict]) -> dict:
    response = client.messages.create(model=MODEL, max_tokens=200 + 25 * len(items),
                                      messages=[{"role": "user", "content": prompt_for(items)}])
    text = "".join(b.text for b in response.content if hasattr(b, "text"))
    return {a.get("i"): a.get("gloss") for a in extract_json(text).get("glosses") or [] if isinstance(a, dict)}


def senses_to_gloss(db, levels: list[int], redo: bool) -> list[VocabSense]:
    used = (db.query(SentenceVocab.vocab_sense_id, SentenceVocab.vocab_id)
            .join(Sentence, Sentence.id == SentenceVocab.sentence_id)
            .filter(Sentence.hsk_level.in_(levels)).distinct().all())
    sense_ids = {s for s, _ in used if s is not None}
    vocab_ids = {v for _, v in used}
    primaries = {sid for (sid,) in db.query(VocabSense.id)
                 .filter(VocabSense.vocab_id.in_(vocab_ids), VocabSense.is_primary == 1)}
    q = db.query(VocabSense).filter(VocabSense.id.in_(sense_ids | primaries))
    if not redo:
        q = q.filter(VocabSense.gloss.is_(None))
    return q.order_by(VocabSense.id).all()


def example_for(db, sense: VocabSense, levels: list[int]) -> tuple[str, str]:
    s = (db.query(Sentence.hanzi, Sentence.english)
         .join(SentenceVocab, SentenceVocab.sentence_id == Sentence.id)
         .filter(SentenceVocab.vocab_sense_id == sense.id, Sentence.hsk_level.in_(levels))
         .order_by(func.length(Sentence.hanzi), Sentence.id).first())
    return (s[0], s[1]) if s else ("", "")


def main():
    parser = argparse.ArgumentParser(description="Short English glosses for word meanings (AI).")
    parser.add_argument("--levels", nargs="+", type=int, required=True)
    parser.add_argument("--redo", action="store_true", help="Gloss every meaning again.")
    parser.add_argument("--estimate", action="store_true", help="Count what would be sent; no AI.")
    args = parser.parse_args()

    t0 = time.time()
    init_db()
    fixes = {k: v for k, v in (json.loads(FIXES_PATH.read_text(encoding="utf-8")) if FIXES_PATH.exists()
                               else {}).items() if not k.startswith("_")}
    with get_session() as db:
        if fixes:
            n = 0
            for sense, hanzi in (db.query(VocabSense, Vocab.hanzi).join(Vocab, Vocab.id == VocabSense.vocab_id)
                                 .filter(Vocab.hanzi.in_(list(fixes)))):
                n += sense.gloss != fixes[hanzi]
                sense.gloss = fixes[hanzi]
            db.flush()
            print(f"Hand glosses ({FIXES_PATH.name}): {len(fixes)} word(s), {n} meaning(s) changed.")
        senses = [s for s in senses_to_gloss(db, args.levels, args.redo)
                  if not (s.gloss and s.vocab and s.vocab.hanzi in fixes)]
        print(f"{len(senses)} word meaning(s) to gloss ({(len(senses) + BATCH - 1) // BATCH} call(s) to {MODEL}).")
        if args.estimate:
            db.rollback()                    # count only: not even the hand glosses are written
            return
        if not senses:
            return
        if client is None:
            raise SystemExit("CLAUDE_API_KEY is not set.")
        word = dict(db.query(Vocab.id, Vocab.hanzi).filter(Vocab.id.in_({s.vocab_id for s in senses})).all())
        items = {}
        for s in senses:
            sent, sent_en = example_for(db, s, args.levels)
            items[s.id] = {"hanzi": word[s.vocab_id], "pinyin": s.pinyin, "english": s.english,
                           "sentence": sent, "sentence_en": sent_en}
        done, problems, stats = {}, {}, Counter()
        todo = list(items)
        for round_ in (1, 2):
            entries = [dict(items[sid], i=n, problem=problems.get(sid)) for n, sid in enumerate(todo)]
            batches = [entries[i:i + BATCH] for i in range(0, len(entries), BATCH)]

            def run(batch):
                try:
                    got = ask(batch)
                except Exception as e:                   # one bad batch shouldn't stop the others
                    for e_ in batch:
                        problems[todo[e_["i"]]] = f"no readable answer ({type(e).__name__})"
                    return
                for e_ in batch:
                    sid = todo[e_["i"]]
                    problem = check_gloss(got.get(e_["i"])) if e_["i"] in got else "no answer"
                    if problem is None:
                        done[sid] = got[e_["i"]].strip()
                        problems.pop(sid, None)
                    else:
                        problems[sid] = problem
            with ThreadPoolExecutor(max_workers=WORKERS) as pool:
                list(pool.map(run, batches))
            todo = [sid for sid in todo if sid not in done]
            if not todo:
                break
        for s in senses:
            if s.id in done:
                s.gloss = done[s.id]
                stats["glossed"] += 1
        db.flush()
        print(f"  glossed: {stats['glossed']}   failed: {len(todo)}")
        examples = [(items[sid]["hanzi"], items[sid]["english"][:40], done[sid]) for sid in list(done)[:12]]
        for h, en, g in examples:
            print(f"    {h}  ({en})  ->  {g}")
        for sid in todo[:20]:
            print(f"    no gloss: {items[sid]['hanzi']} [{items[sid]['pinyin']}] {items[sid]['english'][:50]}"
                  f"  ({problems.get(sid)})")
    print(f"Done in {time.time() - t0:.0f}s. Next: assign_units / finalize (the glosses go into sentences there).")


if __name__ == "__main__":
    main()
