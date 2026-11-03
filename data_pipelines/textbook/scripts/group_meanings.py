"""
data_pipelines/textbook/scripts/group_meanings.py

Pipeline stage 1b (after load_hsk_lists, before tagging): groups each
word's dictionary meanings that are only synonyms into one meaning.

WHY: CC-CEDICT -- and most dictionaries -- list synonyms as separate
meanings. 找 has "to try to find", "to look for", "to find", "to seek"...
resolve_senses picked whichever wording fit each sentence's English best, so
找 became three "meanings" taught in units 3, 8 and 15. With the synonyms
grouped, every sentence picks from real senses only, and 找 is taught once:
"to try to find; to look for; to find; to seek".

ONE AI CALL PER WORD (Haiku, in parallel), only for words of the levels
being built that have a reading with 2+ meanings. The call sees every
reading with its numbered meanings and returns groups of meaning numbers:
synonyms and near-paraphrases together, different uses apart (家 "home" vs
家 "classifier for families or businesses"). Every meaning must be in exactly
one group, or the answer is rejected and the word stays ungrouped.

OUTPUT: data/raw/meaning_groups.json, stored as the meanings' text so you
can read it and fix a grouping by hand (move a meaning to another group,
split a group). It is kept across --reset-db, and a re-run only asks about
words not in the file yet -- or whose dictionary meanings changed since they
were grouped (--redo asks about every word again). hsk_vocab_source applies
it whenever the vocab list is loaded.

USAGE (from the repo root)
    python -m data_pipelines.textbook.scripts.group_meanings --levels 1 2
    python -m data_pipelines.textbook.scripts.group_meanings --levels 1 2 --estimate
    python -m data_pipelines.textbook.scripts.group_meanings --levels 1 2 --words 找 家 --redo
"""
import argparse
import json
import re
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed

import anthropic

from app.core.config.shared import settings
from app.core.config.textbook import TEXTBOOK_RAW_DIR

from data_pipelines.textbook.scripts import hsk_vocab_source as hvs

HAIKU_MODEL = "claude-haiku-4-5"
MAX_TOKENS = 600
DEFAULT_WORKERS = 8

VOCAB_PATH = TEXTBOOK_RAW_DIR / hvs.VOCAB_FILE
GROUPS_PATH = TEXTBOOK_RAW_DIR / hvs.GROUPS_FILE
README = ("Synonym groups for each word's dictionary meanings, written by group_meanings.py and safe to "
          "edit by hand: each inner list is ONE meaning shown to learners (its texts joined with '; '). "
          "Every meaning of a reading must appear in exactly one group, with its exact text; a word whose "
          "groups don't match its current meanings is left ungrouped (load_hsk_lists.py lists it).")

api_key = settings.CLAUDE_API_KEY
client = anthropic.Anthropic(api_key=api_key, max_retries=6) if api_key else None


def build_prompt(word: str, forms: list[tuple[str, list[str]]]) -> str:
    listing = []
    for fi, (reading, meanings) in enumerate(forms):
        listing.append(f"Reading {fi} ({reading}):")
        listing += [f"  {mi}. {m}" for mi, m in enumerate(meanings)]
    return (
        f"These are the dictionary meanings of the Mandarin word {word}, by reading. Dictionaries list "
        "synonyms as separate meanings; a language course needs each real meaning once.\n\n"
        + "\n".join(listing) + "\n\n"
        "For each reading, group its meanings: put meanings in the SAME group when they say the same "
        "thing in different words (synonyms or paraphrases, e.g. \"to look for\" / \"to try to find\" / "
        "\"to seek\"; \"quick\" / \"rapid\"). Keep meanings in DIFFERENT groups when a learner would "
        "learn them as different uses of the word (e.g. 家 \"home\" vs \"classifier for families or "
        "businesses\"; 找 \"to look for\" vs \"to give change\"). Every meaning number must appear in "
        "exactly one group.\n\n"
        'Respond with ONLY a JSON object: {"forms": [{"reading": 0, "groups": [[0, 2], [1], ...]}, ...]}'
    )


def _extract_json(text: str) -> dict:
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise ValueError("no JSON object in the answer")
    return json.loads(m.group(0))


def ask(word: str, forms: list[tuple[str, list[str]]]) -> dict:
    """ONE call. {"forms": [{"reading", "groups": [[text, ...], ...]}]} with
    texts, or {"error": "..."}."""
    try:
        response = client.messages.create(model=HAIKU_MODEL, max_tokens=MAX_TOKENS, messages=[
            {"role": "user", "content": build_prompt(word, forms)}])
        text = "".join(b.text for b in response.content if hasattr(b, "text"))
        answer = _extract_json(text)
    except Exception as e:
        return {"error": f"{e!r}"}
    by_form = {}
    for item in answer.get("forms") or []:
        try:
            by_form[int(item["reading"])] = [[int(n) for n in g] for g in item["groups"]]
        except (KeyError, TypeError, ValueError):
            return {"error": f"malformed answer: {text!r}"}
    out = []
    for fi, (reading, meanings) in enumerate(forms):
        groups = by_form.get(fi) if len(meanings) > 1 else [[0]] if meanings else []
        if groups is None:
            return {"error": f"no groups for reading {fi}: {text!r}"}
        flat = sorted(n for g in groups for n in g)
        if flat != list(range(len(meanings))):
            return {"error": f"reading {fi}: every meaning must be in exactly one group, got {groups}"}
        out.append({"reading": reading, "groups": [[meanings[n] for n in g] for g in groups if g]})
    return {"forms": out}


def needs_grouping(entry: dict) -> bool:
    return any(len(hvs.groupable_meanings(f)) > 1 for f in entry["forms"])


def matches(entry: dict, stored: dict | None) -> bool:
    """Stored groups still hold exactly this entry's current meanings."""
    if not stored or len(stored.get("forms") or []) != len(entry["forms"]):
        return False
    for form, sf in zip(entry["forms"], stored["forms"]):
        flat = [m for g in sf.get("groups") or [] for m in g]
        if sf.get("reading") != hvs.numeric(form) or sorted(flat) != sorted(hvs.groupable_meanings(form)):
            return False
    return True


def main():
    parser = argparse.ArgumentParser(description="Group each word's synonym meanings into one (AI, once per word).")
    parser.add_argument("--levels", nargs="+", type=int, required=True)
    parser.add_argument("--words", nargs="+", default=None, help="Only these words.")
    parser.add_argument("--redo", action="store_true", help="Ask again even for words already grouped.")
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS)
    parser.add_argument("--estimate", action="store_true", help="Count the calls needed; change nothing.")
    args = parser.parse_args()

    t0 = time.time()
    entries = hvs.load_entries(str(VOCAB_PATH), grouped=False)
    stored = dict(hvs.load_groups(str(GROUPS_PATH)))
    levels = set(args.levels)
    words = [w for w, e in entries.items() if e.get("level") in levels and (not args.words or w in args.words)]
    stats = Counter()
    todo = []
    for w in words:
        e = entries[w]
        if not needs_grouping(e):
            stats["one meaning per reading already (no call)"] += 1
        elif not args.redo and matches(e, stored.get(w)):
            stats["already grouped (kept)"] += 1
        else:
            if w in stored and not args.redo:
                stats["stored groups don't match the meanings (dictionary changed or a bad edit): asked again"] += 1
            todo.append(w)
    print(f"HSK {' '.join(map(str, sorted(levels)))}: {len(words)} word(s), {len(todo)} need an AI call.")
    if args.estimate or not todo:
        for k, v in sorted(stats.items()):
            print(f"  {k}: {v}")
        return
    if client is None:
        raise SystemExit("CLAUDE_API_KEY is not set. Set it, or use --estimate.")

    failed, changed = [], []
    jobs = {w: [(hvs.numeric(f), hvs.groupable_meanings(f)) for f in entries[w]["forms"]] for w in todo}
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(ask, w, forms): w for w, forms in jobs.items()}
        for k, f in enumerate(as_completed(futures), 1):
            w, result = futures[f], f.result()
            if "error" in result:
                failed.append((w, result["error"]))
                continue
            stored[w] = {"forms": result["forms"]}
            before = sum(len(m) for _, m in jobs[w])
            after = sum(len(sf["groups"]) for sf in result["forms"])
            stats["grouped"] += 1
            if after < before:
                changed.append((w, before, after, result["forms"]))
            if k % 100 == 0:
                print(f"  ...{k}/{len(jobs)}")
    retry, failed = failed, []
    for w, _ in retry:                        # usually a malformed answer: one more try each
        result = ask(w, jobs[w])
        if "error" in result:
            failed.append((w, result["error"]))
            continue
        stored[w] = {"forms": result["forms"]}
        stats["grouped (on retry)"] += 1

    with open(GROUPS_PATH, "w", encoding="utf-8") as fh:
        json.dump({"_readme": README, **dict(sorted(stored.items()))}, fh, ensure_ascii=False, indent=2)

    print(f"\nDone in {time.time() - t0:.0f}s. Groups written to {GROUPS_PATH}")
    for k, v in sorted(stats.items()):
        print(f"  {k}: {v}")
    print(f"  words whose synonyms were merged: {len(changed)}")
    for w, before, after, forms in changed[:25]:
        shown = " | ".join("; ".join(g) for sf in forms for g in sf["groups"])
        print(f"    {w}: {before} meanings -> {after}:  {shown[:150]}")
    if failed:
        # not fatal: an ungrouped word keeps its separate dictionary meanings
        print(f"\n  [warning] {len(failed)} word(s) failed and stay ungrouped this run "
              f"(run this stage again to retry them):")
        for w, err in failed[:10]:
            print(f"    {w}: {err[:150]}")


if __name__ == "__main__":
    main()