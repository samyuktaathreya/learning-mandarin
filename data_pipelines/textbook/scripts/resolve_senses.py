"""
data_pipelines/textbook/scripts/resolve_senses.py

Pipeline stage 3b: picks the meaning of every ambiguous word occurrence --
ONE AI call per occurrence, so every sentence decides for itself. Runs for
the HSK levels you build (cost scales with them: about 3,800 calls for
HSK 1-2), in parallel.

WHICH TAGS
  tag_sentences.py saved every word with its POS tag and reading, and
  already set the sense when the reading leaves only ONE dictionary meaning
  and that reading is the word's usual one (no AI). A rare reading's lone
  meaning is sent here instead, since the dataset's pinyin is sometimes
  wrong (的 as dì -> "bull's-eye"). Dictionary notes ("also pr. [yāo]...")
  are never offered as meanings. This stage handles the rest: HSK-list words whose reading leaves
  several meanings. Words outside the list have one meaning and are never
  sent.

EACH CALL
  The sentence with the word marked 【like this】 (so a word used twice is
  resolved at each spot), the English translation, the word's POS tag and
  reading, and the NUMBERED meanings of that reading from the vocab JSON.
  Haiku must answer with a number; "none fits" is accepted only with a
  reason. Then it is asked once more with EVERY reading of the word (the
  dataset's pinyin is sometimes wrong: 长 as zhǎng in 又长又平), and only if
  that also fails is a new meaning written (origin = ai_added, listed in
  the log for review). When the dataset's reading isn't the word's main
  one (了 as liǎo), every reading is offered in the first call already.
  Same meaning in two sentences -> same VocabSense row, so units, primary
  senses and questions work as before. Nothing is reused BETWEEN sentences.

Failed calls (after the API client's own retries) are tried again, one at
a time, up to RETRY_ROUNDS times. Any still failing leave the tag without a
sense and the script EXITS WITH AN ERROR, so main.py stops at this stage;
run it again -- only tags still without a sense are sent.
Senses no tag uses any more are deleted at the end.

AFTERWARDS re-run the later stages: assign_units (each level), finalize,
create_questions -- or `python main.py --levels 1 2 --from units`.

USAGE (from the repo root)
    python -m data_pipelines.textbook.scripts.resolve_senses --levels 1 2 --estimate   # count calls
    python -m data_pipelines.textbook.scripts.resolve_senses --levels 1 2 --limit 20   # try 20 sentences
    python -m data_pipelines.textbook.scripts.resolve_senses --levels 1 2
    python -m data_pipelines.textbook.scripts.resolve_senses --levels 1 2 --redo       # re-pick every ambiguous tag
    python -m data_pipelines.textbook.scripts.resolve_senses --levels 1 2 --recheck    # re-send rare-reading / note meanings
    python -m data_pipelines.textbook.scripts.resolve_senses --levels 3 4 5 6 7 --clear-suspect   # same, no AI, unbuilt levels
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
from app.core.config.textbook import TEXTBOOK_RAW_DIR
from app.textbook.db_utils import get_session, init_db, upsert_vocab_sense, delete_unused_senses
from app.textbook.models import Sentence, SentenceVocab, Vocab, VocabOrigin, VocabSense

from data_pipelines.textbook.scripts import hsk_vocab_source as hvs

HAIKU_MODEL = "claude-haiku-4-5"
MAX_TOKENS = 300
DEFAULT_WORKERS = 8
CHUNK = 200                 # occurrences per commit
RETRY_ROUNDS = 2            # extra passes over failed calls, one at a time

VOCAB_PATH = TEXTBOOK_RAW_DIR / hvs.VOCAB_FILE
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_DIR = os.path.abspath(os.path.join(SCRIPT_DIR, "..", "debug", "resolve_senses"))

api_key = settings.CLAUDE_API_KEY
client = anthropic.Anthropic(api_key=api_key, max_retries=6) if api_key else None


# --------------------------------- AI ---------------------------------

def _extract_json(text: str):
    """The first JSON object in the answer, even when Haiku wraps it in a
    code fence or adds an explanation before or after it (common in
    borderline cases like 了 after 到)."""
    text = text.strip()
    start = text.find("{")
    if start == -1:
        raise ValueError("no JSON object in the answer")
    depth, in_str, esc = 0, False, False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
        elif ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return json.loads(text[start:i + 1])
    raise ValueError("unbalanced JSON object in the answer")


def choose_meaning(job: dict, candidates: list[dict], need_definition: bool = True) -> dict:
    """ONE call. Returns {"choice": i}, {"choice": None, "english", "reason"},
    or {"error": "..."} if the call or its answer failed.

    need_definition=False is for the first pass when a retry with every
    reading follows: a bare "none fits" is enough there, since the retry
    usually finds the right reading's meaning (e.g. the dataset reads 了 after
    到 as liao3, so only liao3's verb meanings are offered; le5's particle
    meaning only appears in the retry)."""
    listing = "\n".join(f"{i}. {c['english']}  [{c['pinyin']}]" for i, c in enumerate(candidates))
    prompt = (
        f"Which dictionary meaning does the marked word have in this Mandarin sentence?\n\n"
        f"Sentence: {job['marked']}\n"
        f"English translation: {job['english'] or '(none)'}\n"
        f"Marked word: {job['word']} (pinyin: {job['reading']}, part-of-speech tag: {job['pos'] or '?'})\n\n"
        f"Dictionary meanings:\n{listing}\n\n"
        "Choose the number of the meaning the marked word has HERE. The list is the source of "
        "truth: choose a meaning even if its wording is broader, narrower or more formal than the "
        "sentence. If the sentence fits more than one meaning equally well, choose the FIRST of "
        "them in the list -- that is not a reason to answer null. Answer null ONLY if none of the "
        "meanings could possibly describe this use.\n"
        "Respond with ONLY a JSON object, no other text. Either "
        '{"choice": <number>} or '
        '{"choice": null, "reason": "<why none fits>", "english": "<concise definition as used here>"}'
    )
    try:
        response = client.messages.create(model=HAIKU_MODEL, max_tokens=MAX_TOKENS,
                                          messages=[{"role": "user", "content": prompt}])
    except Exception as e:
        return {"error": f"API call failed: {e!r}"}
    text = "".join(b.text for b in response.content if hasattr(b, "text"))
    try:
        result = _extract_json(text)
        choice = result.get("choice")
        if isinstance(choice, str):
            choice = None if choice.strip().lower() in ("", "null", "none") else choice.strip()
        if choice is None:
            english = (result.get("english") or result.get("definition") or "").strip()
            reason = (result.get("reason") or "").strip()
            if english or not need_definition:
                return {"choice": None, "english": english, "reason": reason or "(no reason given)"}
            return {"error": f"'none' without a definition: {text!r}"}
        choice = int(choice)
        if 0 <= choice < len(candidates):
            return {"choice": choice}
        return {"error": f"out-of-range choice {choice}: {text!r}"}
    except Exception as e:
        return {"error": f"unparseable answer ({e!r}): {text!r}"}


def decide(job: dict) -> dict:
    """Reading's meanings first; if none fits, every reading's meanings."""
    retry_follows = job["all_candidates"] is not None
    result = choose_meaning(job, job["candidates"], need_definition=not retry_follows)
    result["candidates"], result["retried"] = job["candidates"], False
    if result.get("choice", 0) is None and job["all_candidates"] is not None:
        retry = choose_meaning(job, job["all_candidates"])
        retry["candidates"], retry["retried"] = job["all_candidates"], True
        return retry
    return result


# --------------------------------- JOBS ---------------------------------

def mark(text: str, start: int, end: int) -> str:
    return text[:start] + "【" + text[start:end] + "】" + text[end:]


def spans(sentence: Sentence, rows: list[SentenceVocab]) -> list[tuple[int, int] | None]:
    """Where each tagged word sits in the sentence text, matched in order."""
    out, pos = [], 0
    for row in rows:
        idx = sentence.hanzi.find(row.vocab.hanzi, pos)
        if idx == -1:
            out.append(None)
            continue
        out.append((idx, idx + len(row.vocab.hanzi)))
        pos = idx + len(row.vocab.hanzi)
    return out


def needs_recheck(db, row: SentenceVocab, entry: dict) -> bool:
    """--recheck: a meaning set before the rare-reading and note fixes that
    may be wrong -- a dictionary note picked as the meaning (一 "also pr.
    [yāo]..."), or a lone meaning of a rare reading set without AI (的 as
    'really and truly'). AI-added meanings are left alone."""
    sense = db.get(VocabSense, row.vocab_sense_id)
    if sense is None or sense.origin != VocabOrigin.hsk_list:
        return False
    if hvs.is_note(sense.english or ""):
        return True
    return (sense.form_index is not None and len(entry["forms"]) > 1
            and sense.form_index != hvs.default_form_index(entry))


def _norm_meaning(text: str) -> str:
    return re.sub(r"[\W_]+", " ", (text or "").lower()).strip()


def dedupe_candidates(candidates: list[dict], prefer_forms: list[int]) -> list[dict]:
    """Keeps one candidate per meaning text. The dictionary repeats a meaning
    under two readings (看 kān and kàn are both "to watch"), and the AI then
    picked whichever came first: 他在看电视 got kān. Of identical meanings,
    the one from the sentence's own reading wins, then the main reading,
    then the first listed. The list keeps its order."""
    rank = lambda c: prefer_forms.index(c["form_index"]) if c["form_index"] in prefer_forms else len(prefer_forms)
    best, order = {}, []
    for c in candidates:
        key = _norm_meaning(c["english"])
        if key not in best:
            best[key] = c
            order.append(key)
        elif rank(c) < rank(best[key]):
            best[key] = c
    return [best[k] for k in order]


def build_jobs(db, levels: list[int], entries: dict, redo: bool, limit: int | None, stats: Counter,
               recheck: bool = False):
    q = db.query(Sentence).filter(Sentence.hsk_level.in_(levels)).order_by(Sentence.id)
    sentences = q.all()
    if limit:
        sentences = sentences[:limit]
    jobs = []
    for s in sentences:
        rows = sorted(s.vocab_links, key=lambda r: r.position)
        for row, span in zip(rows, spans(s, rows)):
            v = row.vocab
            if v.hsk_level is None or v.hanzi not in entries:
                continue                                  # outside the list: one meaning, set by tagging
            entry = entries[v.hanzi]
            if row.vocab_sense_id is not None and not redo:
                if not (recheck and needs_recheck(db, row, entry)):
                    stats["already has a sense"] += 1
                    continue
                stats["rechecked"] += 1
            reading = row.reading or ""
            all_forms = list(range(len(entry["forms"])))
            matched = hvs.match_forms(entry, reading)
            forms = matched or all_forms
            candidates = hvs.candidates(entry, forms)
            if not hvs.single_meaning_is_safe(entry, forms):
                # the dataset's reading points away from the word's main one
                # (了 as liǎo in 找到了): it is often wrong, so offer every
                # reading from the start -- offering only that reading's one
                # meaning left the AI nothing to choose but the wrong one
                forms = all_forms
                candidates = hvs.candidates(entry, forms)
            prefer = matched + [hvs.default_form_index(entry)]
            candidates = dedupe_candidates(candidates, prefer)
            if len(candidates) == 1 and hvs.single_meaning_is_safe(entry, forms):
                if row.vocab_sense_id is None:
                    c = candidates[0]
                    row.vocab_sense_id = upsert_vocab_sense(
                        db, v, pinyin=c["pinyin"].replace(" ", ""), english=c["english"],
                        origin=VocabOrigin.hsk_list, word_type=v.word_type, pos_tag=row.pos_tag,
                        form_index=c["form_index"], meaning_index=c["meaning_index"]).id
                    stats["one meaning (no AI)"] += 1
                continue
            jobs.append({
                "row_id": row.id, "vocab_id": v.id, "word": v.hanzi, "pos": row.pos_tag,
                "reading": reading, "english": s.english, "hanzi": s.hanzi,
                "marked": mark(s.hanzi, *span) if span else s.hanzi,
                "candidates": candidates,
                "all_candidates": (dedupe_candidates(hvs.candidates(entry, all_forms), prefer)
                                   if forms != all_forms else None),
            })
    db.flush()
    return sentences, jobs


def apply(db, job: dict, result: dict, log, stats: Counter, ai_added: list) -> bool:
    row = db.get(SentenceVocab, job["row_id"])
    vocab = db.get(Vocab, job["vocab_id"])
    if "error" in result:
        stats["failed calls (including ones that later succeeded)"] += 1
        log.warning(f"FAILED   {job['marked']}  {job['word']}: {result['error']}")
        return False
    if result["retried"]:
        stats["retried with every reading"] += 1
    if result["choice"] is None:
        stats["AI added a new meaning"] += 1
        ai_added.append((job["word"], result["english"], result["reason"], job["hanzi"]))
        sense = upsert_vocab_sense(db, vocab, pinyin=job["reading"], english=result["english"],
                                   origin=VocabOrigin.ai_added, word_type=vocab.word_type, pos_tag=job["pos"])
        log.warning(f"AI-ADDED {job['marked']}  {job['word']} ({job['pos']}, {job['reading']}) -> "
                    f"{result['english']!r} -- {result['reason']}")
    else:
        c = result["candidates"][result["choice"]]
        sense = upsert_vocab_sense(db, vocab, pinyin=c["pinyin"].replace(" ", ""), english=c["english"],
                                   origin=VocabOrigin.hsk_list, word_type=vocab.word_type, pos_tag=job["pos"],
                                   form_index=c["form_index"], meaning_index=c["meaning_index"])
        stats["meaning chosen"] += 1
        log.info(f"CHOSEN   {job['marked']}  {job['word']} ({job['pos']}, {job['reading']}) -> "
                 f"[{c['form_index']}.{c['meaning_index']}] {c['english']}")
    if row.vocab_sense_id is not None and row.vocab_sense_id != sense.id:
        stats["changed from the previous sense"] += 1
    row.vocab_sense_id = sense.id
    return True


# --------------------------------- MAIN ---------------------------------

def setup_logging():
    os.makedirs(LOG_DIR, exist_ok=True)
    path = os.path.join(LOG_DIR, f"{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt")
    log = logging.getLogger(f"resolve_senses_{path}")
    log.setLevel(logging.INFO)
    log.propagate = False
    fh = logging.FileHandler(path, encoding="utf-8")
    fh.setFormatter(logging.Formatter("%(asctime)s  %(levelname)-7s  %(message)s"))
    log.addHandler(fh)
    return log, path


def main():
    parser = argparse.ArgumentParser(description="One AI call per ambiguous word occurrence.")
    parser.add_argument("--levels", nargs="+", type=int, required=True, help="HSK levels to resolve, e.g. 1 2")
    parser.add_argument("--redo", action="store_true", help="Re-pick ambiguous tags that already have a sense.")
    parser.add_argument("--recheck", action="store_true",
                        help="Re-send tags whose meaning is a dictionary note or a rare reading's only "
                             "meaning set without AI (needed once after the 2026-10-23 fix).")
    parser.add_argument("--clear-suspect", action="store_true",
                        help="No AI: just un-set the meanings --recheck would re-send, so they are picked "
                             "normally when those levels are built (for levels you aren't building yet).")
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS, help="Parallel AI requests (default 8).")
    parser.add_argument("--limit", type=int, default=None, help="Only the first N sentences (testing).")
    parser.add_argument("--estimate", action="store_true", help="Count the calls needed; write nothing.")
    args = parser.parse_args()

    if args.clear_suspect:
        init_db()
        entries = hvs.load_entries(str(VOCAB_PATH))
        with get_session() as db:
            rows = (db.query(SentenceVocab).join(Sentence, Sentence.id == SentenceVocab.sentence_id)
                    .filter(Sentence.hsk_level.in_(args.levels), SentenceVocab.vocab_sense_id.isnot(None)).all())
            cleared = 0
            for row in rows:
                entry = entries.get(row.vocab.hanzi)
                if row.vocab.hsk_level is not None and entry and needs_recheck(db, row, entry):
                    row.vocab_sense_id = None
                    cleared += 1
            db.flush()
            deleted = delete_unused_senses(db)
            db.commit()
        print(f"HSK {' '.join(map(str, args.levels))}: un-set {cleared} suspect meaning(s); "
              f"deleted {deleted} sense(s) nothing uses now. They'll be picked when those levels are built.")
        return

    if client is None and not args.estimate:
        raise SystemExit("CLAUDE_API_KEY is not set. Set it, or use --estimate.")

    t0 = time.time()
    init_db()
    log, log_path = setup_logging()
    entries = hvs.load_entries(str(VOCAB_PATH))
    stats, ai_added = Counter(), []

    with get_session() as db:
        if not db.query(SentenceVocab).count():
            raise SystemExit("No tagged sentences -- run tag_sentences.py first.")
        sentences, jobs = build_jobs(db, args.levels, entries, args.redo, args.limit, stats, recheck=args.recheck)
        print(f"HSK {' '.join(map(str, args.levels))}: {len(sentences)} sentence(s), "
              f"{len(jobs)} ambiguous occurrence(s) need an AI call"
              f"{' (plus a retry for any where no meaning fits)' if jobs else ''}.")
        if args.estimate:
            db.rollback()
            print("Estimate only: nothing written.")
            return

        done, failed = 0, []
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            for start in range(0, len(jobs), CHUNK):
                chunk = jobs[start:start + CHUNK]
                futures = {pool.submit(decide, job): job for job in chunk}
                for f in as_completed(futures):
                    if not apply(db, futures[f], f.result(), log, stats, ai_added):
                        failed.append(futures[f])
                db.commit()
                done += len(chunk)
                print(f"  ...{done}/{len(jobs)}  ({time.time() - t0:.0f}s)")

        # Failures are usually rate limits or a malformed answer: try them
        # again, one at a time, before giving up on them.
        for attempt in range(1, RETRY_ROUNDS + 1):
            if not failed:
                break
            print(f"  retrying {len(failed)} failed call(s), round {attempt} of {RETRY_ROUNDS}...")
            time.sleep(5 * attempt)
            still = []
            for job in failed:
                result = decide(job)
                if apply(db, job, result, log, stats, ai_added):
                    stats["succeeded on retry"] += 1
                else:
                    still.append(job)
            db.commit()
            failed = still

        if failed:
            stats["still without a meaning (run again)"] = len(failed)
        removed = delete_unused_senses(db)
        stats["unused senses deleted"] = removed

    print(f"\nDone in {time.time() - t0:.0f}s.")
    for k, v in sorted(stats.items()):
        print(f"  {k}: {v}")
    if ai_added:
        print(f"  AI-added meanings to review ({len(ai_added)}):")
        for word, english, reason, s in ai_added[:20]:
            print(f"    {word}: {english!r} -- {reason} -- in {s}")
    print(f"Every decision is in {log_path}")
    if failed:
        print(f"\n❌ {len(failed)} occurrence(s) still have no meaning after {RETRY_ROUNDS} retry round(s). "
              f"See the FAILED lines in the log for why, then run this stage again "
              f"(it only sends what's missing):")
        for job in failed[:10]:
            print(f"    {job['marked']}  ({job['word']})")
        raise SystemExit(1)
    print("Next: python main.py --levels <levels> --from units")


if __name__ == "__main__":
    main()