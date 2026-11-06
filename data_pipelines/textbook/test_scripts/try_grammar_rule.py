"""
data_pipelines/textbook/test_scripts/try_grammar_rule.py

TRIAL: grammar detection rules for ONE grammar point, written by AI from
real grammar references, tested against examples labeled by people, then
used to pull sentences from Tatoeba.

WHAT DEFINES A POINT (the "context" every AI call gets)
  - the official HSK grammar list entry (label + any examples it has)
  - the AllSet Learning Chinese Grammar Wiki ("ASG") pages listed for the
    point in test_scripts/grammar_sources.json: "is" pages (this point:
    structure, explanation, examples) and "not" pages (other uses of the
    same words: structure + examples). Parsed by parse_asg.py into
    data/intermediate/allsetlearning/asg_points.json.
  ASG is CC BY-NC-SA 3.0: non-commercial use only, with attribution.

STEPS (run in order)
  sources   AI (Sonnet, one call) sorts the ASG pages that use the point's
            words into is / not / unsure and saves them to
            grammar_sources.json, marked "_proposed" -- check them.
  context   (optional) prints the definition every AI call will get.
  write     AI (Sonnet) writes the rule as a section of grammar_rules.md. It
            sees the context, the labeled examples ("is" pages = must match,
            "not" pages = must not match) as the course's tagger splits and
            tags them, and some unlabeled course sentences.
  validate  A. LABELED TEST (no AI): the rule on every labeled example --
               how many examples of the point it matches, and which
               counter-examples it wrongly matches.
            B. (only with --check) COURSE SENTENCES: the rule on every
               pipeline sentence with the words, compared with an AI check. Checks are cached in
               output/check_cache.json, so re-running costs nothing.
            --revise: sends A's failures (labels written by people: right)
            and B's disagreements (an AI's opinion: may be wrong) back to the
            writer once, keeps the old file as grammar_rules.md.bak, and
            re-runs A and B.
  tatoeba   Your filtered Tatoeba file through every gate:
              1. has an English translation   2. contains the word(s)
              3. new (not in the DB, not a duplicate once in Simplified)
              4. at most MAX_UNKNOWN_CHARS characters no word of the level uses
              5. tagged, and really at this HSK level
              6. the RULE finds the point
              7. TWO independent checks both say yes; the first picks an
                 accurate English among Tatoeba's (none -> dropped)
              8. the difficulty review passes it
            Steps 7-8 cost money, so they run on the shortest sentences
            first, CHECK_BATCH at a time, and stop once the point has
            --target sentences at the level (its course sentences count).

Tagging results (for labeled examples and Tatoeba sentences) are cached in
output/tag_cache.json; --retag tags again. THE REAL DATABASE IS NEVER
TOUCHED: everything runs on output/scratch_textbook.db and is rolled back.

USAGE (from the repo root)
    python data_pipelines/textbook/test_scripts/parse_asg.py          # once
    python data_pipelines/textbook/test_scripts/try_grammar_rule.py sources --code 1-42
    python data_pipelines/textbook/test_scripts/try_grammar_rule.py write --code 1-10 --redo
    python data_pipelines/textbook/test_scripts/try_grammar_rule.py validate --code 1-10
    python data_pipelines/textbook/test_scripts/try_grammar_rule.py validate --code 1-10 --revise
    python data_pipelines/textbook/test_scripts/try_grammar_rule.py tatoeba --code 1-10 --level 1
"""
import argparse
import csv
import hashlib
import json
import random
import re
import shutil
import sys
import threading
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]          # .../learning-mandarin
TEST_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(TEST_DIR))

import grammar_rules as gr  # noqa: E402

DATA_DIR = TEST_DIR.parent / "data"
OUT_DIR = TEST_DIR / "output"
SCRATCH_DB = OUT_DIR / "scratch_textbook.db"
RULES_PATH = TEST_DIR / "grammar_rules.md"
SOURCES_PATH = TEST_DIR / "grammar_sources.json"
ASG_PATH = DATA_DIR / "intermediate" / "allsetlearning" / "asg_points.json"
CACHE_PATH = OUT_DIR / "check_cache.json"
TAG_CACHE_PATH = OUT_DIR / "tag_cache.json"
DEFAULT_TSV = DATA_DIR / "intermediate" / "tatoeba" / "cmn_filtered.tsv"

WRITER_MODEL = "claude-sonnet-5-5"
CHECK_MODEL = "claude-haiku-4-5"
WORKERS = 8
CHECK_BATCH = 20              # sentences per check call
DEFAULT_TARGET = 20           # tatoeba: good sentences wanted per point at the level
MAX_UNKNOWN_CHARS = 2
SAMPLES_PER_STEP = 12
WRITER_EXAMPLES = 40          # labeled examples (each side) shown to the writer
WRITER_UNLABELED = 25         # unlabeled course sentences shown to the writer
TAG_CACHE_VERSION = 1
_CJK_RE = re.compile(r"[㐀-鿿]")


# --------------------------------- SETUP ---------------------------------

def point_db_at_scratch_copy():
    """Copies the real textbook.db and makes the app use the copy. Runs
    before anything imports app.textbook.db_utils (it opens the DB at import)."""
    import app.core.config.data as data_cfg
    real = Path(data_cfg.TEXTBOOK_DB)
    if not real.exists():
        raise SystemExit(f"No database at {real} -- run main.py first.")
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    shutil.copy(real, SCRATCH_DB)
    data_cfg.TEXTBOOK_DB = SCRATCH_DB if isinstance(data_cfg.TEXTBOOK_DB, Path) else str(SCRATCH_DB)
    from app.textbook import db_utils
    if Path(db_utils.engine.url.database).resolve() != SCRATCH_DB.resolve():
        raise SystemExit(f"Safety stop: the app opened {db_utils.engine.url.database}, not the scratch copy.")
    return real


def get_client():
    import anthropic
    from app.core.config.shared import settings
    if not settings.CLAUDE_API_KEY:
        raise SystemExit("CLAUDE_API_KEY is not set.")
    return anthropic.Anthropic(api_key=settings.CLAUDE_API_KEY, max_retries=6)


def call(client, model: str, prompt: str, max_tokens: int) -> str:
    response = client.messages.create(model=model, max_tokens=max_tokens,
                                      messages=[{"role": "user", "content": prompt}])
    return "".join(b.text for b in response.content if hasattr(b, "text"))


def load_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def save_json(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=0), encoding="utf-8")


def _cjk_strings(x) -> list[str]:
    """Every string with Chinese in it, anywhere inside x."""
    if isinstance(x, str):
        return [x.strip()] if _CJK_RE.search(x) else []
    if isinstance(x, dict):
        return [s for v in x.values() for s in _cjk_strings(v)]
    if isinstance(x, list):
        return [s for v in x for s in _cjk_strings(v)]
    return []


# --------------------------------- THE POINT ---------------------------------

class Point:
    """The grammar point: list entry, marker words, ASG pages, labeled examples."""
    def __init__(self, db, code: str, contains, need_sources=True):
        from app.textbook.models import GrammarTip
        from data_pipelines.textbook.scripts import detect_grammar as dg
        tip = db.query(GrammarTip).filter(GrammarTip.grammar_code == code).first()
        if tip is None:
            raise SystemExit(f"No grammar point {code} in the DB.")
        self.code, self.level, self.label = code, tip.hsk_level, tip.raw_text
        content = load_json_str(tip.content_json)
        self.list_examples = list(dict.fromkeys(
            s for k, v in content.items() if "example" in k.lower() for s in _cjk_strings(v)))
        self.markers = sorted(set(contains or dg.GRAMMAR_MARKERS.get(code, set())), key=len, reverse=True)
        if not self.markers:
            raise SystemExit(f"No marker words known for {code}: pass --contains <words>.")
        self.list_others = []
        for p in db.query(GrammarTip).order_by(GrammarTip.id).all():
            if p.grammar_code == code:
                continue
            c = load_json_str(p.content_json)
            hay = (p.raw_text or "") + " " + " ".join(str(c.get(k) or "") for k in ("label", "label_full", "sub", "pattern"))
            if any(m in hay for m in self.markers):
                self.list_others.append((p.grammar_code, p.hsk_level, p.raw_text))

        asg = {p["id"]: p for p in load_json(ASG_PATH, {}).get("points", [])}
        if not asg:
            raise SystemExit(f"{ASG_PATH} is missing: run parse_asg.py first.")
        self.asg = asg
        sources = load_json(SOURCES_PATH, {}).get(code)
        if not sources:
            if need_sources:
                raise SystemExit(f"No entry for {code} in {SOURCES_PATH}: run  sources --code {code}  first.")
            sources = {}
        missing = [i for kind in ("is", "not", "unsure") for i in sources.get(kind, {}) if i not in asg]
        if missing:
            raise SystemExit(f"grammar_sources.json names page(s) not in asg_points.json: {missing}")
        self.is_pages = [asg[i] for i in sources.get("is", {})]
        self.not_pages = [asg[i] for i in sources.get("not", {})]
        self.unsure = list(sources.get("unsure", {}))

        pos = {e["hanzi"]: f"{p['id']} {p['title']}" for p in self.is_pages for e in p["examples"]}
        for s in self.list_examples:
            if len(s) <= 40:
                pos.setdefault(s, "HSK list example")
        neg = {e["hanzi"]: f"{p['id']} {p['title']}" for p in self.not_pages for e in p["examples"]
               if any(m in e["hanzi"] for m in self.markers)}
        self.conflicts = sorted(set(pos) & set(neg))
        self.positives = {h: src for h, src in pos.items() if h not in neg}
        self.negatives = {h: src for h, src in neg.items() if h not in pos}

    def context(self, full=True) -> str:
        """The definition every AI call gets. full=False: shorter, for the
        per-sentence checks."""
        n_ex, n_text = (8, 1500) if full else (5, 600)
        out = [f"GRAMMAR POINT {self.code} (HSK {self.level}). Official HSK grammar list: {self.label}"]
        if self.list_examples:
            out.append("  Official examples: " + " / ".join(self.list_examples[:6]))
        out.append("\nWHAT IT IS (from the AllSet Learning Chinese Grammar Wiki):")
        for p in self.is_pages:
            out.append(f"\n### {p['title']}")
            if p["structures"]:
                out.append("Structure: " + "  |  ".join(dict.fromkeys(p["structures"])))
            text = "\n".join(l for l in p["text"].splitlines() if not l.startswith("## "))
            if text:
                out.append(text[:n_text] + ("..." if len(text) > n_text else ""))
            if p["examples"]:
                out.append("Examples:")
                out += [f"  {e['hanzi']}  ({e['english']})" for e in p["examples"][:n_ex]]
        out.append("\nNOT THIS POINT -- other grammar that uses the same words (a sentence that only shows "
                   "one of these must not count):")
        for p in self.not_pages:
            ex = "; ".join(f"{e['hanzi']} ({e['english']})" for e in p["examples"][:2])
            st = " | ".join(dict.fromkeys(p["structures"][:3]))
            out.append(f"- {p['title']}: {st}" + (f"  e.g. {ex}" if ex else ""))
        for c, lv, label in self.list_others:
            out.append(f"- HSK list {c} (HSK {lv}): {label}")
        return "\n".join(out)

    def show(self):
        print(f"Point {self.code} (HSK {self.level}): {self.label}")
        print(f"Words: {' '.join(self.markers)}")
        print(f"Official list examples found: {len(self.list_examples)}"
              + (f"  e.g. {' / '.join(self.list_examples[:3])}" if self.list_examples else ""))
        if not self.is_pages and not self.not_pages:
            print()
            return
        print("AllSet Learning pages for this point:")
        for p in self.is_pages:
            print(f"  {p['id']}  {p['title']}  ({len(p['examples'])} examples)")
        print(f"Pages for other uses (counter-examples): {len(self.not_pages)}; left out as unsure: {len(self.unsure)}")
        print(f"Labeled test sentences: {len(self.positives)} examples, {len(self.negatives)} counter-examples"
              + (f" ({len(self.conflicts)} on both sides, dropped)" if self.conflicts else ""))
        print()


def load_json_str(s):
    try:
        return json.loads(s or "{}")
    except json.JSONDecodeError:
        return {}


def clauses_of(sentence):
    from data_pipelines.textbook.scripts import detect_grammar as dg
    return dg.clauses(dg.Context(sentence.hanzi, dg.sentence_words(sentence), None))


def pipeline_sentences(db, point: Point):
    from sqlalchemy import or_
    from app.textbook.models import Sentence
    return (db.query(Sentence).filter(or_(*[Sentence.hanzi.contains(m) for m in point.markers]))
            .order_by(Sentence.id).all())


# --------------------------------- TAGGING (cached) ---------------------------------

class Tagger:
    """Tags any sentence exactly like the pipeline does, inside a savepoint
    that is rolled back. Results cached in output/tag_cache.json."""
    def __init__(self, db, retag=False):
        self.db, self.ready = db, False
        self.cache = {} if retag else load_json(TAG_CACHE_PATH, {})
        if self.cache.get("_v") != TAG_CACHE_VERSION:
            self.cache = {"_v": TAG_CACHE_VERSION}
        self.n = 0

    def _setup(self):
        from app.textbook.db_utils import get_vocab_hanzi_through_level
        from app.textbook.models import GrammarTip
        from data_pipelines.textbook.scripts import hsk_vocab_source as hvs
        from data_pipelines.textbook.scripts import tag_sentences as ts
        from data_pipelines.textbook.scripts import detect_grammar as dg
        self.ts, self.dg = ts, dg
        logger, _ = ts.setup_logging()
        self.run = ts.Run(logger, hvs.load_entries(str(ts.VOCAB_PATH)),
                          get_vocab_hanzi_through_level(self.db, hvs.MAX_LEVEL),
                          ts.load_segmentation_cache(), estimate=True)
        points = self.db.query(GrammarTip).all()
        self.detectors, _ = dg.build_detectors(points)
        self.level_of_point = {p.grammar_code: p.hsk_level for p in points}
        self.ready = True

    def _result(self, s, bump):
        from app.textbook.models import WordType
        from data_pipelines.textbook.scripts import review_sentences as rs
        rows = sorted(s.vocab_links, key=lambda r: r.position)
        return {"clauses": clauses_of(s), "words": rs.sentence_words(s), "level": s.hsk_level,
                "vocab": [[r.vocab.hanzi, r.vocab.hsk_level, r.vocab.word_type == WordType.proper_noun] for r in rows],
                "bump": list(bump[2]) if bump else []}

    def tag(self, hanzi: str) -> dict:
        if hanzi in self.cache:
            return self.cache[hanzi]
        if not self.ready:
            self._setup()
        from app.textbook.db_utils import upsert_import_sentence
        from app.textbook.models import SentenceSource
        self.n += 1
        sp = self.db.begin_nested()
        try:
            ext = "trial-" + hashlib.md5(hanzi.encode()).hexdigest()[:12]
            s, written = upsert_import_sentence(self.db, hanzi=hanzi, english="", external_id=ext,
                                                source=SentenceSource.external)
            if written:
                self.ts.tag_sentence(self.db, self.run, s)
                self.db.expire(s, ["vocab_links"])
                _, bump = self.dg.detect_sentence(self.db, s, self.detectors, self.level_of_point)
            else:
                bump = None                      # already a pipeline sentence: its stored tags
            res = self._result(s, bump)
            self.cache[hanzi] = res
        except Exception as e:
            res = {"error": f"{e!r}"[:200]}
        finally:
            sp.rollback()
        return res

    def tag_all(self, texts, label: str) -> dict:
        texts = list(dict.fromkeys(texts))
        todo = sum(t not in self.cache for t in texts)
        if todo:
            print(f"Tagging {todo} {label} (cached after this run)...", flush=True)
        out = {}
        for k, t in enumerate(texts, 1):
            out[t] = self.tag(t)
            if todo and k % 100 == 0:
                print(f"  ...{k}/{len(texts)}", flush=True)
        self.save()
        return out

    def save(self):
        save_json(TAG_CACHE_PATH, self.cache)
        if self.ready:
            self.ts.save_segmentation_cache(self.run.seg_cache)


def blockers(res: dict, level: int) -> list[str]:
    out = []
    for h, lv, is_name in res["vocab"]:
        if lv is None and not is_name:
            out.append(f"{h} (not in list)")
        elif lv is not None and lv > level:
            out.append(f"{h} L{lv}")
    return list(dict.fromkeys(out))


def as_clauses(res: dict):
    return [[tuple(t) for t in c] for c in res["clauses"]]


# --------------------------------- CHECKS (cached) ---------------------------------

_cache_lock = threading.Lock()


def check_key(point: Point, hanzi: str, english) -> str:
    return json.dumps([gr.CHECK_VERSION, CHECK_MODEL, point.code, hanzi, english or []], ensure_ascii=False)


def run_checks(client, point: Point, jobs: list[dict], n: int, cache: dict) -> dict:
    """jobs: [{"hanzi", "tagged", "english"}]. Makes sure every job has n
    independent answers in the cache; returns {hanzi: [answers]}. Each pass
    sends CHECK_BATCH sentences per call (the definition once per call), in
    a different order each pass. A failed call or a missing answer counts as
    "no" this run and isn't cached."""
    failed = defaultdict(list)
    context = point.context(full=False)
    calls = 0

    def one(chunk):
        items = [{"n": k + 1, "hanzi": j["hanzi"], "tagged": j["tagged"], "english": j["english"]}
                 for k, j in enumerate(chunk)]
        try:
            answers = gr.extract_json_array(call(client, CHECK_MODEL, gr.check_batch_prompt(point.code, context, items),
                                                 150 + 90 * len(items)))
            by_n = {int(a["n"]): a for a in answers if isinstance(a, dict) and "n" in a}
        except Exception as e:
            for j in chunk:
                failed[j["hanzi"]].append({"uses_point": False, "reason": f"AI call failed: {e!r}"[:200]})
            return
        for k, j in enumerate(chunk, 1):
            a = by_n.get(k)
            if not a or not isinstance(a.get("uses_point"), bool):
                failed[j["hanzi"]].append({"uses_point": False, "reason": "no answer for this sentence"})
                continue
            a = {"uses_point": a["uses_point"], "reason": str(a.get("reason", "")),
                 **({"english": a.get("english") if isinstance(a.get("english"), int)
                     and 0 <= a["english"] < len(j["english"]) else None} if j["english"] is not None else {})}
            with _cache_lock:
                cache.setdefault(check_key(point, j["hanzi"], j["english"]), []).append(a)

    for pass_no in range(n):
        todo = [j for j in jobs if len(cache.get(check_key(point, j["hanzi"], j["english"]), [])) <= pass_no
                and not failed[j["hanzi"]]]
        if not todo:
            continue
        random.Random(pass_no).shuffle(todo)
        chunks = [todo[i:i + CHECK_BATCH] for i in range(0, len(todo), CHECK_BATCH)]
        calls += len(chunks)
        with ThreadPoolExecutor(max_workers=WORKERS) as pool:
            list(pool.map(one, chunks))
    if calls:
        print(f"  {calls} check call(s) ({CHECK_BATCH} sentences each)...", flush=True)
        save_json(CACHE_PATH, cache)
    return {j["hanzi"]: (cache.get(check_key(point, j["hanzi"], j["english"]), []) + failed[j["hanzi"]])[:n]
            for j in jobs}


# --------------------------------- SOURCES ---------------------------------

MAX_SOURCE_PAGES = 40


def candidate_pages(point: Point) -> list[dict]:
    """ASG pages that use the point's words: keyword first, then pages whose
    examples mark one of the words, then pages whose structure has one."""
    scored = []
    for p in point.asg.values():
        marked = {m["text"] for e in p["examples"] for m in e.get("marked", [])}
        if any(m in p.get("keyword", "") for m in point.markers):
            score = 3
        elif any(m in t for t in marked for m in point.markers):
            score = 2
        elif any(m in st for st in p.get("structures", []) for m in point.markers):
            score = 1
        else:
            continue
        scored.append((score, p))
    scored.sort(key=lambda x: (-x[0], x[1]["id"]))
    return [p for _, p in scored]


def cmd_sources(db, point: Point, args):
    sources = load_json(SOURCES_PATH, {})
    if point.code in sources and not args.redo:
        raise SystemExit(f"{point.code} already has pages in {SOURCES_PATH}. Edit them, or pass --redo.")
    pages = candidate_pages(point)
    if not pages:
        raise SystemExit(f"No AllSet Learning page uses {' '.join(point.markers)}.")
    shown = pages[:MAX_SOURCE_PAGES]
    print(f"{len(pages)} page(s) use the word(s); asking about {len(shown)}.")
    listing = []
    for p in shown:
        st = " | ".join(dict.fromkeys(p["structures"][:2]))
        ex = "; ".join(e["hanzi"] for e in p["examples"][:2])
        listing.append(f"{p['id']} | {p['title']} | HSK(2021) {p.get('hsk_2021') or '?'} | structure: {st} | e.g. {ex}")
    prompt = gr.SOURCES_PROMPT.format(
        code=point.code, level=point.level, label=point.label, markers=" ".join(point.markers),
        official=("Official examples: " + " / ".join(point.list_examples[:6])) if point.list_examples else "",
        pages="\n".join(listing))
    print(f"  Asking {args.writer_model} to sort them...", flush=True)
    answer = gr.extract_json(call(get_client(), args.writer_model, prompt, 2000))
    groups = {"is": {}, "not": {}, "unsure": {}}
    for p in shown:
        g = str(answer.get(p["id"], "unsure")).lower()
        groups[g if g in groups else "unsure"][p["id"]] = p["title"]
    sources[point.code] = {"_proposed": f"by {args.writer_model} on {datetime.now():%Y-%m-%d} -- check it, "
                                        "then delete this line", **groups}
    SOURCES_PATH.write_text(json.dumps(sources, ensure_ascii=False, indent=2), encoding="utf-8")
    for g in ("is", "not", "unsure"):
        print(f"\n{g}:")
        for i, t in groups[g].items():
            print(f"  {i}  {t}")
    if not groups["is"]:
        print("\n[warning] no page teaches this point: the rule would only have the list's own examples to go on.")
    print(f"\nSaved to {SOURCES_PATH}. Check the groups (move a page if it's wrong), then: write --code {point.code}")


# --------------------------------- WRITE ---------------------------------

def ask_writer(client, point: Point, prompt: str, args, what: str) -> str:
    error, section = None, None
    for attempt in range(2):
        p = prompt if error is None else (prompt + f"\n\nYour previous answer could not be used: {error}\n"
                                                   "Reply again with the corrected section only.")
        print(f"  Asking {args.writer_model} to {what} the rule (usually under a minute)...", flush=True)
        answer = call(client, args.writer_model, p, 4000)
        raw = OUT_DIR / f"writer_{point.code}_{datetime.now():%Y%m%d_%H%M%S}.txt"
        raw.write_text(answer, encoding="utf-8")
        try:
            section = gr.normalize_section(gr.extract_section(point.code, answer))
            gr.parse_rule_block(point.code, section)
            stamp = f"<!-- written by {args.writer_model} on {datetime.now():%Y-%m-%d}; edit freely -->"
            head, _, rest = section.partition("\n")
            rest = "\n".join(l for l in rest.splitlines() if not l.strip().startswith("<!-- written by"))
            return f"{head}\n{stamp}\n{rest}\n"
        except gr.RuleError as e:
            error = str(e)
            print(f"  [retry] the rule couldn't be read: {error} (the AI's full answer: {raw})")
    raise SystemExit(f"The writer's rule couldn't be read twice: {error}\nLast answer:\n{section}")


def labeled(point: Point, tagger: Tagger):
    """{hanzi: tag result} for the examples and the counter-examples (tagging
    failures dropped, with a note)."""
    tags = tagger.tag_all(list(point.positives) + list(point.negatives), "labeled example(s)")
    bad = [h for h, r in tags.items() if "error" in r]
    if bad:
        print(f"  [note] {len(bad)} labeled sentence(s) couldn't be tagged and are left out, e.g. {bad[:3]}")
    pos = {h: tags[h] for h in point.positives if "error" not in tags[h]}
    neg = {h: tags[h] for h in point.negatives if "error" not in tags[h]}
    return pos, neg


def cmd_write(db, point: Point, args):
    _, sections, _ = gr.load_rules(RULES_PATH)
    if point.code in sections and not args.redo:
        raise SystemExit(f"{point.code} already has a rule in {RULES_PATH}. Edit it, or pass --redo.")
    pos, neg = labeled(point, Tagger(db, args.retag))
    rnd = random.Random(0)

    def sample(d, n):
        items = [(h, gr.render(as_clauses(r))) for h, r in d.items()]
        rnd.shuffle(items)
        return items[:n]
    unl = [(s.hanzi, gr.render(clauses_of(s))) for s in pipeline_sentences(db, point)]
    rnd.shuffle(unl)
    prompt = gr.writer_prompt(point.code, point.context(), sample(pos, WRITER_EXAMPLES),
                              sample(neg, WRITER_EXAMPLES), unl[:WRITER_UNLABELED])
    section = ask_writer(get_client(), point, prompt, args, "write")
    gr.save_section(RULES_PATH, point.code, section)
    print(f"\n{section}\nWritten to {RULES_PATH}. Next: validate --code {point.code}")


# --------------------------------- VALIDATE ---------------------------------

def labeled_test(rule, pos, neg):
    missed = [(h, gr.render(as_clauses(r))) for h, r in pos.items() if not rule.found(as_clauses(r))]
    wrong = [(h, gr.render(as_clauses(r)), rule.explain(as_clauses(r))[1]) for h, r in neg.items()
             if rule.found(as_clauses(r))]
    return missed, wrong


def print_labeled(point, pos, neg, missed, wrong):
    print(f"\n=== A. {point.code}: rule on labeled examples (no AI) ===")
    print(f"  examples of {point.code} matched:    {len(pos) - len(missed)}/{len(pos)}")
    print(f"  counter-examples wrongly matched: {len(wrong)}/{len(neg)}   <- should be 0")
    if wrong:
        print(f"\n--- counter-examples the rule WRONGLY matches ({len(wrong)}) ---")
        for h, _, m in wrong:
            print(f"  {h}  -- {point.negatives[h]}  [matched: {' ; '.join(m)}]")
    if missed:
        print(f"\n--- examples of {point.code} the rule misses ({len(missed)}) ---")
        for h, _ in missed:
            print(f"  {h}  -- {point.positives[h]}")


def compare(rule, rows, answers):
    out = {"both yes": [], "both no": [], "rule only": [], "check only": []}
    for s_hanzi, clauses in rows:
        found, m, u = rule.explain(clauses)
        a = answers[s_hanzi][0]
        key = ("both yes" if a["uses_point"] else "rule only") if found else \
              ("check only" if a["uses_point"] else "both no")
        out[key].append((s_hanzi, gr.render(clauses), a.get("reason", ""), m, u))
    return out


def print_comparison(point, result):
    total = sum(len(v) for v in result.values())
    print(f"\n=== B. {point.code}: rule vs AI check on {total} course sentence(s) ===")
    labels = {"both yes": "rule YES, check YES", "both no": "rule no,  check no",
              "rule only": "rule YES, check no   <- possible WRONG MATCHES",
              "check only": "rule no,  check YES  <- possible misses"}
    for k, lab in labels.items():
        print(f"  {len(result[k]):>5}  {lab}")
    for k in ("rule only", "check only"):
        if result[k]:
            print(f"\n--- {labels[k].split('<- ')[1]} ({len(result[k])}) ---")
            for h, _, why, m, u in result[k]:
                fired = f"  [matched: {' ; '.join(m)}]" if m else (f"  [unless: {' ; '.join(u)}]" if u else "")
                print(f"  {h}  -- check: {why}{fired}")


def cmd_validate(db, point: Point, args):
    rules, sections, errors = gr.load_rules(RULES_PATH)
    if point.code in errors:
        raise SystemExit(f"The rule in {RULES_PATH} can't be read: {errors[point.code]}")
    if point.code not in rules:
        raise SystemExit(f"No rule for {point.code} yet: run write --code {point.code} first.")
    rule = rules[point.code]
    pos, neg = labeled(point, Tagger(db, args.retag))
    missed, wrong = labeled_test(rule, pos, neg)
    print_labeled(point, pos, neg, missed, wrong)

    result, client = None, None
    if args.check:
        sentences = pipeline_sentences(db, point)[:args.limit] if args.limit else pipeline_sentences(db, point)
        rows = [(s.hanzi, clauses_of(s)) for s in sentences]
        client, cache = get_client(), load_json(CACHE_PATH, {})
        answers = run_checks(client, point, [{"hanzi": h, "tagged": gr.render(c), "english": None}
                                             for h, c in rows], 1, cache)
        result = compare(rule, rows, answers)
        print_comparison(point, result)
        report = OUT_DIR / f"validate_{point.code}_{datetime.now():%Y%m%d_%H%M%S}.csv"
        with open(report, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f)
            w.writerow(["sentence", "outcome", "rule_matched_lines", "rule_unless_lines", "check_reason", "tagged"])
            for k, items in result.items():
                for h, tagged, why, m, u in items:
                    w.writerow([h, k, " ; ".join(m), " ; ".join(u), why, tagged])
        print(f"\nEvery course sentence: {report}")

    if not args.revise:
        return
    chk_wrong = [(h, t, why) for h, t, why, _, _ in (result or {}).get("rule only", [])][:30]
    chk_missed = [(h, t, why) for h, t, why, _, _ in (result or {}).get("check only", [])][:30]
    if not (missed or wrong or chk_wrong or chk_missed):
        print("Nothing to revise.")
        return
    print(f"\nRevising: {len(wrong)} wrong + {len(missed)} missed labeled example(s), "
          f"{len(chk_wrong) + len(chk_missed)} checker disagreement(s)...")
    prompt = gr.revise_prompt(point.code, point.context(), sections[point.code], missed[:40],
                              [(h, t) for h, t, _ in wrong][:40], chk_wrong, chk_missed)
    new = ask_writer(client or get_client(), point, prompt, args, "revise")
    shutil.copy(RULES_PATH, RULES_PATH.with_name(RULES_PATH.name + ".bak"))
    gr.save_section(RULES_PATH, point.code, new)
    print(f"\n{new}\nSaved (old file: {RULES_PATH.name}.bak). Re-testing:")
    new_rule = gr.parse_rule_block(point.code, new)
    print_labeled(point, pos, neg, *labeled_test(new_rule, pos, neg))
    if result is not None:
        print_comparison(point, compare(new_rule, rows, answers))


# --------------------------------- TATOEBA ---------------------------------

def read_tatoeba(path: Path) -> dict:
    """{id: {"original", "english": [translations]}} from the filtered file
    (header row; one row per sentence + translation)."""
    out = {}
    with open(path, encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f, delimiter="\t", quoting=csv.QUOTE_NONE):
            tid, text, eng = (row.get("id") or "").strip(), (row.get("text") or "").strip(), (row.get("eng_text") or "").strip()
            if not tid or not text:
                continue
            item = out.setdefault(tid, {"original": text, "english": []})
            if eng and eng not in item["english"]:
                item["english"].append(eng)
    return out


def cmd_tatoeba(db, point: Point, args):
    try:
        from opencc import OpenCC
    except ImportError:
        raise SystemExit("pip install opencc-python-reimplemented  (Traditional -> Simplified)")
    from app.textbook.models import Sentence, Vocab
    from data_pipelines.textbook.scripts import detect_grammar as dg
    from data_pipelines.textbook.scripts import review_sentences as rs

    rules, _, errors = gr.load_rules(RULES_PATH)
    if point.code not in rules:
        raise SystemExit(errors.get(point.code) or f"No rule for {point.code} yet: run write first.")
    rule, level = rules[point.code], args.level
    tsv = Path(args.tsv) if args.tsv else DEFAULT_TSV
    print(f"Tatoeba file: {tsv}\n")
    cc = OpenCC("t2s")
    pairs = read_tatoeba(tsv)
    funnel, outcome, dropped = Counter(), {}, defaultdict(list)

    def drop(tid, step, reason):
        outcome[tid] = (step, reason)
        dropped[step].append((pairs[tid]["hanzi"], reason))

    in_db = {h for (h,) in db.query(Sentence.hanzi).all()}
    known_chars = {ch for (h,) in db.query(Vocab.hanzi).filter(Vocab.hsk_level.isnot(None),
                                                                 Vocab.hsk_level <= level).all() for ch in h}
    first_of, traditional, candidates = {}, 0, []
    for tid, p in pairs.items():
        p["hanzi"] = cc.convert(p["original"])
        traditional += p["hanzi"] != p["original"]
    for tid, p in pairs.items():
        if not p["english"]:
            outcome[tid] = ("1", "no English translation")
            continue
        funnel["1. have an English translation"] += 1
        if not any(m in p["hanzi"] for m in point.markers):
            outcome[tid] = ("1", "no marker word")
            continue
        funnel["2. contain the point's word(s)"] += 1
        if p["hanzi"] in in_db:
            drop(tid, "3", "already in the database")
            continue
        if p["hanzi"] in first_of:
            keep = pairs[first_of[p["hanzi"]]]
            keep["english"] += [e for e in p["english"] if e not in keep["english"]]
            drop(tid, "3", f"same text as tatoeba {first_of[p['hanzi']]} (translations pooled)")
            continue
        first_of[p["hanzi"]] = tid
        funnel["3. new (not in DB, not a duplicate)"] += 1
        unknown = {ch for ch in _CJK_RE.findall(p["hanzi"]) if ch not in known_chars}
        if len(unknown) > MAX_UNKNOWN_CHARS:
            drop(tid, "4", f"{len(unknown)} characters no HSK {level} word uses: {''.join(sorted(unknown))}")
            continue
        funnel[f"4. at most {MAX_UNKNOWN_CHARS} characters outside HSK {level} words"] += 1
        candidates.append(tid)
    if args.limit:
        candidates = candidates[:args.limit]

    # ---- 5-6: tag, level, rule ----
    tagger = Tagger(db, args.retag)
    tags = tagger.tag_all([pairs[t]["hanzi"] for t in candidates], "Tatoeba candidate(s)")
    passed_rule = []
    for tid in candidates:
        p = pairs[tid]
        res = tags[p["hanzi"]]
        if "error" in res:
            drop(tid, "5", f"error while tagging: {res['error']}")
            continue
        if res["level"] != level:
            causes = blockers(res, level) + ([f"grammar {' '.join(res['bump'])}"] if res["bump"] else [])
            drop(tid, "5", f"level {res['level']}: {', '.join(causes) or 'no list words'}")
            continue
        funnel[f"5. really HSK {level}"] += 1
        clauses = as_clauses(res)
        found, m, u = rule.explain(clauses)
        if not found:
            drop(tid, "6", f"rule: {'unless ' + ' ; '.join(u) if u else 'no match: line matched'}")
            continue
        funnel[f"6. the rule finds {point.code}"] += 1
        passed_rule.append({"tid": tid, "hanzi": p["hanzi"], "tagged": gr.render(clauses),
                            "english": list(p["english"]), "words": res["words"], "matched": m})
    db.rollback()

    # ---- how many does the point still need? ----
    have = [s.hanzi for s in pipeline_sentences(db, point)
            if s.hsk_level == level and rule.found(clauses_of(s))]
    need = max(0, args.target - len(have)) if args.target else len(passed_rule)
    print(f"\nCourse sentences at HSK {level} the rule already finds {point.code} in: {len(have)}"
          + (f"  -> Tatoeba needs to add {need} (target {args.target})" if args.target else ""))

    # ---- 7-8: checks + review, shortest sentences first, in rounds, until enough ----
    client, cache = get_client(), load_json(CACHE_PATH, {})
    scope = rs.grammar_scope(db, level)
    survivors, not_checked = [], 0

    def review(j):
        key = json.dumps(["review", dg.REVIEW_PROMPT_VERSION, level, j["hanzi"], j["chosen"]], ensure_ascii=False)
        if key not in cache:
            v = rs.ask(j["hanzi"], j["chosen"], level, scope, j["words"])
            if "error" in v:
                return {"ok": False, "problems": [f"AI call failed: {v['error'][:150]}"]}
            with _cache_lock:
                cache[key] = v
        return cache[key]

    queue = sorted(passed_rule, key=lambda j: len(j["hanzi"]))
    for start in range(0, len(queue), CHECK_BATCH):
        if len(survivors) >= need:
            not_checked = len(queue) - start
            for j in queue[start:]:
                outcome[j["tid"]] = ("-", "not checked: target reached")
            break
        batch = queue[start:start + CHECK_BATCH]
        answers = run_checks(client, point, batch, 2, cache)
        passed_check = []
        for j in batch:
            a = answers[j["hanzi"]]
            nos = [x.get("reason", "") for x in a if not x["uses_point"]]
            if nos:
                drop(j["tid"], "7", f"check said no ({len(nos)}/2): {nos[0]}")
            elif a[0].get("english") is None:
                drop(j["tid"], "7", f"no accurate English among: {' | '.join(j['english'])}")
            else:
                j["chosen"] = j["english"][a[0]["english"]]
                passed_check.append(j)
                funnel[f"7. both checks say {point.code}, English chosen"] += 1
        with ThreadPoolExecutor(max_workers=WORKERS) as pool:
            verdicts = list(pool.map(review, passed_check))
        for j, v in zip(passed_check, verdicts):
            if v.get("ok"):
                funnel["8. difficulty review passes it"] += 1
                survivors.append(j)
                outcome[j["tid"]] = ("8", "passed")
            else:
                drop(j["tid"], "8", f"difficulty review: {'; '.join(v.get('problems') or [])}")
    save_json(CACHE_PATH, cache)

    # ---- report ----
    chosen = {j["tid"]: j for j in passed_rule}
    report = OUT_DIR / f"tatoeba_{point.code}_level{level}_{datetime.now():%Y%m%d_%H%M%S}.csv"
    with open(report, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["tatoeba_id", "hanzi", "original", "english_chosen", "english_options",
                    "stopped_at_step", "reason", "rule_matched_lines"])
        for tid, (step, reason) in outcome.items():
            if reason in ("no marker word", "no English translation"):
                continue
            p, j = pairs[tid], chosen.get(tid, {})
            w.writerow([tid, p["hanzi"], p["original"], j.get("chosen", ""), " | ".join(p["english"]),
                        step, reason, " ; ".join(j.get("matched", []))])

    print(f"\n=== {point.code} at HSK {level}: funnel ===")
    print(f"  {len(pairs):>7}  Tatoeba sentences in the file")
    for k in sorted(funnel):
        print(f"  {funnel[k]:>7}  {k}")
    print(f"  ({traditional} were in Traditional characters and converted)")
    if not_checked:
        print(f"  ({not_checked} sentence(s) passed the rule but weren't checked: target reached)")
    print(f"\n=== Survivors ({len(survivors)}) ===")
    for j in survivors:
        print(f"  {j['hanzi']}  ({j['chosen']})  [tatoeba {j['tid']}]")
    for step in sorted(dropped):
        items = dropped[step]
        n = len(items) if step in ("6", "7", "8") else min(len(items), SAMPLES_PER_STEP)
        print(f"\n--- dropped at step {step} ({len(items)}){'' if n == len(items) else f', first {n}'} ---")
        for hanzi, reason in items[:n]:
            print(f"  {hanzi}  -- {reason}")
    print(f"\nEvery sentence and why: {report}")


# --------------------------------- MAIN ---------------------------------

def main():
    global CHECK_MODEL
    parser = argparse.ArgumentParser(description="Trial: grammar rule from references + Tatoeba, one point.")
    parser.add_argument("step", choices=["sources", "context", "write", "validate", "tatoeba"],
                        help="sources: AI proposes the point's AllSet Learning pages. "
                             "context: just print what the AI is told about the point.")
    parser.add_argument("--code", default="1-10", help="Grammar point (default 1-10).")
    parser.add_argument("--contains", nargs="+", default=None, help="Marker words instead of GRAMMAR_MARKERS.")
    parser.add_argument("--writer-model", default=WRITER_MODEL)
    parser.add_argument("--check-model", default=CHECK_MODEL, help=f"Model for the checks (default {CHECK_MODEL}).")
    parser.add_argument("--redo", action="store_true", help="sources/write: replace what's there.")
    parser.add_argument("--revise", action="store_true", help="validate: one revision from the failures.")
    parser.add_argument("--check", action="store_true",
                        help="validate: also compare with an AI check on the course sentences (costs a little).")
    parser.add_argument("--target", type=int, default=DEFAULT_TARGET,
                        help=f"tatoeba: stop once the point has this many sentences at the level, counting the "
                             f"course's own (default {DEFAULT_TARGET}; 0 = check every candidate).")
    parser.add_argument("--retag", action="store_true", help="Ignore the tagging cache.")
    parser.add_argument("--level", type=int, default=1, help="tatoeba: HSK level (default 1).")
    parser.add_argument("--tsv", default=None, help=f"tatoeba: file (default {DEFAULT_TSV.name}).")
    parser.add_argument("--limit", type=int, default=None, help="validate/tatoeba: at most N sentences.")
    args = parser.parse_args()

    CHECK_MODEL = args.check_model
    t0 = time.time()
    real = point_db_at_scratch_copy()
    from app.textbook.db_utils import get_session, init_db
    init_db()
    print(f"Working on a copy of {real}\n")
    with get_session() as db:
        point = Point(db, args.code, args.contains, need_sources=args.step not in ("sources", "context"))
        point.show()
        if args.step == "context":
            print(point.context())
        else:
            {"sources": cmd_sources, "write": cmd_write, "validate": cmd_validate,
             "tatoeba": cmd_tatoeba}[args.step](db, point, args)
        db.rollback()
    print(f"\nDone in {time.time() - t0:.0f}s. The real database was not changed.")


if __name__ == "__main__":
    main()