"""
data_pipelines/textbook/test_scripts/run_all_grammar.py

Runs the grammar trial (try_grammar_rule.py) over EVERY grammar point of the
levels you build, so you run one command and read one report.

WHICH POINTS
  teach   every HSK 1-2 point (--levels) with marker words: gets a rule, then
          Tatoeba sentences at the first level its words allow, up to --target.
  guard   every HIGHER point made only of HSK 1-2 words (一……也/都+不/没,
          连⋯⋯也/都 ...): gets a rule only. Any Tatoeba sentence a guard rule
          finds is too hard for the lower level and is dropped (step 5b).
  skipped points that describe every sentence (try_grammar_rule SKIP, assign_units
          GRAMMAR_NOT_COUNTED), aliases of another point, and sentence-shape
          points with no marker word (serial verbs, noun predicates). Listed in
          the report.

PHASES (in order; --phase picks one)
  rules    per point: sources -> write (Haiku) -> labeled test -> one revise
           (Sonnet) if it fails -> labeled test. A counter-example the rule
           matches is first checked by AI: if it does contain the point
           (alongside something harder), it's not a mistake and leaves the
           pile -- placing it higher is the harder point's guard's job. A rule
           is OK when it wrongly matches NO counter-example and catches
           MIN_RECALL of the examples (60% teach, 80% guard; at least
           MIN_EXAMPLES examples). Failed rules are flagged and not used.
  guards   every OK rule, used as a guard for LOWER points, is run on those
           points' labeled examples. Wrong flags -> one revise; still wrong ->
           not used for those points.
  tatoeba  per OK teach point: the Tatoeba step (guard rules active). Points
           filled under older rules/guards are filled again (cached, cheap).

MANUAL STEP: --show-unmatched lists the points whose label names no words or
that got no wiki page, with the closest pages, for writing manual_points.json
({"code": {"words": [...], "is": {id: title}, "not": {...}}}).

RESUMABLE: progress is saved after every point in output/batch/state.json.
Stop it any time (Ctrl+C) and run the same command again -- finished steps are
skipped, nothing is paid for twice. A point's full output goes to
output/batch/<code>.log.

OUTPUT (output/batch/)
  summary.csv               one row per point: status, test scores, sentences
  selected_sentences.csv    every Tatoeba sentence chosen, with its point

USAGE (from the repo root)
    python data_pipelines/textbook/test_scripts/run_all_grammar.py --dry-run
    python data_pipelines/textbook/test_scripts/run_all_grammar.py --limit 10
    python data_pipelines/textbook/test_scripts/run_all_grammar.py
    python data_pipelines/textbook/test_scripts/run_all_grammar.py --only 1-14 2-31 --redo-tatoeba
"""
import argparse
import contextlib
import csv
import sys
import time
import traceback
from pathlib import Path
from types import SimpleNamespace

TEST_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(TEST_DIR))

import grammar_rules as gr          # noqa: E402
import try_grammar_rule as tg       # noqa: E402

BATCH_DIR = tg.OUT_DIR / "batch"
STATE_PATH = tg.BATCH_STATE_PATH
MIN_RECALL = {"teach": 0.6, "guard": 0.8}   # a guard that misses harder sentences lets them leak down
FIRST_WRITER = "claude-haiku-4-5"   # writes every rule first; a rule that fails its test is revised by Sonnet
MIN_EXAMPLES = 3


def trial_args(**kw):
    a = dict(code=None, contains=None, writer_model=tg.WRITER_MODEL, check_model=tg.CHECK_MODEL, redo=False,
             revise=False, check=False, retag=False, level=None, tsv=None, limit=None, target=tg.DEFAULT_TARGET)
    a.update(kw)
    return SimpleNamespace(**a)


def pace(t_start, done, total):
    """' (took 2m 10s; ~45 min left)' -- at the average pace of this phase so far."""
    took = time.time() - t_start[-1]
    t_start.append(time.time())
    left = (time.time() - t_start[0]) / done * (total - done) if done else 0
    fmt = lambda sec: f"{sec / 3600:.1f} h" if sec >= 5400 else f"{sec / 60:.0f} min" if sec >= 60 else f"{sec:.0f}s"
    return f"  (took {fmt(took)}" + (f"; ~{fmt(left)} left in this phase)" if total > done else ")")


def save_state(state):
    tg.save_json(STATE_PATH, state, merge=False)


# --------------------------------- PLAN ---------------------------------

def plan(db, levels, max_guard_level):
    from app.textbook.models import GrammarTip, Vocab
    from data_pipelines.textbook.scripts import detect_grammar as dg
    from data_pipelines.textbook.scripts.assign_units import GRAMMAR_NOT_COUNTED, GRAMMAR_ALIASES
    vocab = {h for (h,) in db.query(Vocab.hanzi).all()}
    vocab_chars = {ch for h in vocab for ch in h}
    teach, skipped = [], []
    for p in db.query(GrammarTip).filter(GrammarTip.hsk_level.in_(levels)).order_by(GrammarTip.id).all():
        c = p.grammar_code
        if tg.load_json(tg.MANUAL_PATH, {}).get(c, {}).get("skip"):
            skipped.append((c, p.raw_text, tg.load_json(tg.MANUAL_PATH, {})[c]["skip"]))
        elif c in dg.SKIP:
            skipped.append((c, p.raw_text, "describes nearly every sentence"))
        elif c in GRAMMAR_NOT_COUNTED:
            skipped.append((c, p.raw_text, "in nearly every sentence (not counted for pacing)"))
        elif c in GRAMMAR_ALIASES:
            skipped.append((c, p.raw_text, f"same point as {GRAMMAR_ALIASES[c]}"))
        else:
            words = (tg.load_json(tg.MANUAL_PATH, {}).get(c, {}).get("words")
                     or dg.GRAMMAR_MARKERS.get(c) or tg.EXTRA_MARKERS.get(c)
                     or tg.real_label_words(p.raw_text or "", vocab, vocab_chars))
            if words:
                teach.append(c)
            else:
                skipped.append((c, p.raw_text, "a sentence shape, no marker word"))
    teach_set = set(teach)
    manual = tg.load_json(tg.MANUAL_PATH, {})
    guard = []
    for c, lv, label in tg.harder_points_with_easy_words(db, max(levels)):
        if c in teach_set or lv > max_guard_level:
            continue
        if manual.get(c, {}).get("skip"):
            skipped.append((c, label, manual[c]["skip"]))
        else:
            guard.append(c)
    return teach, guard, skipped


# --------------------------------- PHASE 1: RULES ---------------------------------

def rule_for(db, code, st, tagger, client, first_writer):
    """sources -> write (cheap model) -> test -> (revise once, Sonnet) -> test. Fills st."""
    point = tg.Point(db, code, None, need_sources=False)
    manual = tg.load_json(tg.MANUAL_PATH, {}).get(code, {})
    saved = tg.load_json(tg.SOURCES_PATH, {}).get(code)
    new_words = bool(manual.get("words")) and sorted(manual["words"]) != sorted((saved or {}).get("_words") or [])
    if saved is None or new_words:             # new, or its words were (re)written by hand
        tg.cmd_sources(db, point, trial_args(code=code, redo=True))
        point = tg.Point(db, code, None)
    if not point.is_pages:
        st.update(rule="failed", note="no AllSet Learning page teaches this point")
        return
    _, sections, errors = gr.load_rules(tg.RULES_PATH)
    if code not in sections or code in errors or new_words:
        try:
            tg.cmd_write(db, point, trial_args(code=code, redo=True, writer_model=first_writer))
        except SystemExit:                      # its answer couldn't be read: let Sonnet write it
            tg.cmd_write(db, point, trial_args(code=code, redo=True))
        _, sections, errors = gr.load_rules(tg.RULES_PATH)
    if code in errors:
        st.update(rule="failed", note=f"rule can't be read: {errors[code]}")
        return
    pos, neg = tg.labeled(point, tagger)
    cache = tg.load_json(tg.CACHE_PATH, {})
    min_recall = MIN_RECALL.get(st.get("kind"), 0.8)

    def score(rule):
        """The labeled test. A counter-example the rule matches is checked: if
        it does contain this point (alongside something harder -- that's the
        harder point's guard's job), it isn't a mistake and leaves the pile."""
        missed, wrong = tg.labeled_test(rule, pos, neg)
        if wrong:
            has = tg.contains_point(client, point, [(h, t) for h, t, _ in wrong], cache)
            for h in [h for h, _, _ in wrong if has.get(h)]:
                neg.pop(h, None)
            wrong = [w for w in wrong if not has.get(w[0])]
        matched = len(pos) - len(missed)
        good = not wrong and len(pos) >= MIN_EXAMPLES and matched >= min_recall * len(pos)
        return good, missed, wrong, matched

    rule = gr.parse_rule_block(code, sections[code])
    good, missed, wrong, matched = score(rule)
    if not good and not st.get("revised") and len(pos) >= MIN_EXAMPLES:
        prompt = gr.revise_prompt(code, point.context(), sections[code], missed[:40],
                                  [(h, t) for h, t, _ in wrong][:40], [], [])
        small = gr.revise_prompt(code, point.context(full=False), sections[code], missed[:12],
                                 [(h, t) for h, t, _ in wrong][:12], [], [])
        new = tg.ask_writer(client, point, prompt, trial_args(), "revise", small_prompt=small)
        gr.save_section(tg.RULES_PATH, code, new)
        st["revised"] = True
        good, missed, wrong, matched = score(gr.parse_rule_block(code, new))
    tg.print_labeled(point, pos, neg, missed, wrong)
    st.update(examples=len(pos), counter_examples=len(neg), matched=matched, wrongly_matched=len(wrong),
              first_level=point.first_level, words=" ".join(point.markers))
    if good:
        st.update(rule="ok", note="")
    elif len(pos) < MIN_EXAMPLES:
        st.update(rule="failed", note=f"only {len(pos)} labeled example(s): can't test the rule")
    else:
        st.update(rule="failed", note=f"{len(wrong)} counter-example(s) wrongly matched, "
                                      f"{matched}/{len(pos)} examples caught (needs {min_recall:.0%})")


# --------------------------------- PHASE 1b: GUARD CHECK ---------------------------------

def guard_check(db, state, teach, tagger, client):
    """Every OK rule acts as a guard when a LOWER point is filled (an HSK 2
    rule for an HSK 1 fill, an HSK 3 rule for HSK 1-2 fills...). Here each one
    is run on the labeled examples of every lower teach point. A flagged
    example is checked: if it really contains the guard's point, fine;
    otherwise the guard is wrong there. A wrong guard is revised once
    (Sonnet), told these sentences are the lower point, not its own; if it
    still flags them, it is not used for those points (state "not_for")."""
    from app.textbook.models import GrammarTip
    level_of = dict(db.query(GrammarTip.grammar_code, GrammarTip.hsk_level).all())
    cache = tg.load_json(tg.CACHE_PATH, {})
    lower = {}
    for c in teach:
        if state.get(c, {}).get("rule") == "ok":
            p = tg.Point(db, c, None)
            lower[c] = (p.first_level, tg.labeled(p, tagger)[0])
    guards = [c for c, st in state.items() if isinstance(st, dict) and st.get("rule") == "ok"]
    changed = False
    for i, g in enumerate(guards, 1):
        st = state[g]
        targets = {c: pos for c, (lv, pos) in lower.items() if c != g and level_of.get(g, 0) > lv}
        if not targets:
            continue
        rules, sections, _ = gr.load_rules(tg.RULES_PATH)
        if g not in rules:
            continue

        def wrong_flags(rule):
            flagged = {(c, h): r for c, pos in targets.items() for h, r in pos.items()
                       if rule.found(tg.as_clauses(r))}
            if not flagged:
                return {}
            gp = tg.Point(db, g, None)
            has = tg.contains_point(client, gp, [(h, gr.render(tg.as_clauses(r))) for (_, h), r in flagged.items()],
                                    cache)
            bad = {}
            for (c, h), r in flagged.items():
                if not has.get(h):
                    bad.setdefault(c, []).append((h, gr.render(tg.as_clauses(r))))
            return bad

        with open(BATCH_DIR / f"{g}.log", "a", encoding="utf-8") as log, contextlib.redirect_stdout(log):
            print(f"\n########## guard check  {time.strftime('%Y-%m-%d %H:%M')} ##########")
            bad = {}
            try:
                bad = wrong_flags(rules[g])
                new_rule = None
                if bad and not st.get("guard_revised"):
                    gp = tg.Point(db, g, None)
                    examples = [x for c in sorted(bad) for x in bad[c]][:40]
                    print(f"Flags examples of {', '.join(sorted(bad))} that don't contain {g}: revising.")
                    prompt = gr.revise_prompt(g, gp.context(), sections[g], [], examples, [], [])
                    prompt += (f"\n\nThe counter-examples above are example sentences of EASIER grammar points "
                               f"({', '.join(sorted(bad))}), not of {g}. Your rule must not match them.")
                    st["guard_revised"] = True
                    try:
                        new = tg.ask_writer(client, gp, prompt, trial_args(), "revise")
                        new_rule = gr.parse_rule_block(g, new)
                    except (SystemExit, Exception) as e:
                        print(f"Revising failed ({str(e).splitlines()[0][:150]}): keeping the old rule.")
                        new_rule = None
                if bad and new_rule is not None:
                    pos_g, neg_g = tg.labeled(gp, tagger)
                    missed, wrong = tg.labeled_test(new_rule, pos_g, neg_g)
                    if wrong:                       # same cleaning as the rules phase
                        has = tg.contains_point(client, gp, [(h, t) for h, t, _ in wrong], cache)
                        wrong = [w for w in wrong if not has.get(w[0])]
                    if wrong or (len(pos_g) - len(missed)) < MIN_RECALL.get(st.get("kind"), 0.8) * len(pos_g):
                        print(f"The revised rule fails {g}'s own test ({len(wrong)} wrong, {len(missed)} missed): "
                              "keeping the old rule.")
                    else:
                        gr.save_section(tg.RULES_PATH, g, new)
                        bad = wrong_flags(new_rule)
                        changed = True
                        if st.get("kind") == "teach":
                            st["tatoeba"] = "not run"      # its own fill used the old rule
                for c, items in sorted(bad.items()):
                    print(f"Not used as a guard for {c}: it still flags {len(items)} of its examples, "
                          f"e.g. {items[0][0]}")
            except (SystemExit, Exception) as e:      # never let one guard stop the phase
                print(f"Guard check failed: {str(e).splitlines()[0][:200] if str(e) else repr(e)}")
                traceback.print_exc(file=log)
        not_for = sorted(bad)
        if not_for != st.get("not_for", []):
            changed = True
        st["not_for"] = not_for
        save_state(state)
        print(f"[guard check {i}/{len(guards)}] {g}: "
              + (f"not used for {', '.join(not_for)}" if not_for else "ok")
              + (" (revised)" if st.get("guard_revised") else ""), flush=True)
    return changed


def guard_version(state) -> str:
    """Changes whenever the rules or which guards apply where change: a point
    filled under an older version is filled again (cheap -- cached)."""
    import hashlib
    rules_text = tg.RULES_PATH.read_text(encoding="utf-8") if tg.RULES_PATH.exists() else ""
    rules_text += tg.LEVELS_PATH.read_text(encoding="utf-8") if tg.LEVELS_PATH.exists() else ""
    used = sorted((c, st.get("rule"), tuple(st.get("not_for") or ())) for c, st in state.items() if isinstance(st, dict))
    return hashlib.md5((rules_text + repr(used)).encode()).hexdigest()[:12]


# --------------------------------- MANUAL STEP HELP ---------------------------------

def show_unmatched(db, state, teach, guard):
    """Points that never got going (label names no words, or no wiki page
    found): their full label, the list's examples, and the wiki pages whose
    keyword/structures share the most characters -- for writing
    manual_points.json by hand."""
    from app.textbook.models import GrammarTip
    asg = tg.load_json(tg.ASG_PATH, {}).get("points", [])
    common = set("的了是我你他她们不在有一这那个吗呢吧就也都很和人")
    out = []
    for c in teach + guard:
        st = state.get(c, {})
        note = st.get("note", "")
        if not ("No AllSet Learning page uses" in note or "no AllSet Learning page teaches" in note
                or (st.get("rule") == "failed" and st.get("examples", 99) < MIN_EXAMPLES)):
            continue
        tip = db.query(GrammarTip).filter(GrammarTip.grammar_code == c).first()
        content = tg.load_json_str(tip.content_json)
        examples = [x for k, v in content.items() if "example" in k.lower() for x in tg._cjk_strings(v)][:4]
        chars = {ch for t in [tip.raw_text or ""] + examples for ch in t if "\u4e00" <= ch <= "\u9fff"} - common
        scored = []
        for p in asg:
            hay = p.get("keyword", "") + " ".join(p.get("structures", []))
            score = sum(ch in hay for ch in chars)
            if score:
                scored.append((score, p))
        scored.sort(key=lambda x: -x[0])
        out.append(f"\n== {c} (HSK {tip.hsk_level}, {st.get('kind')}): {tip.raw_text}\n   why: {note}")
        out.append(f"   list examples: {' / '.join(examples) or '(none)'}")
        for score, p in scored[:8]:
            st_ = " | ".join(p.get("structures", [])[:2])
            out.append(f"   {p['id']}  {p['title']}  [HSK2021 {p.get('hsk_2021') or '?'}] {st_}")
    text = "\n".join(out) or "Nothing to match by hand."
    (BATCH_DIR / "unmatched.txt").write_text(text, encoding="utf-8")
    print(text)
    print(f"\nSaved to {BATCH_DIR / 'unmatched.txt'} -- send it to Claude to write manual_points.json.")


# --------------------------------- PHASE 2: TATOEBA ---------------------------------

def tatoeba_for(db, code, st, levels, target):
    point = tg.Point(db, code, None)
    if point.first_level > max(levels):
        st.update(tatoeba="skipped", note=f"its words are HSK {point.first_level}: above the levels built")
        return
    res = tg.cmd_tatoeba(db, point, trial_args(code=code, target=target))
    st.update(tatoeba="done", level=res["level"], course_sentences=res["have"], added=res["added"],
              candidates_after_rule=res["candidates_after_rule"], survivors=res["survivors"], note="")


# --------------------------------- REPORT ---------------------------------

def write_reports(state, teach, guard, skipped):
    BATCH_DIR.mkdir(parents=True, exist_ok=True)
    with open(BATCH_DIR / "summary.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["code", "kind", "rule", "examples_caught", "counter_examples_wrongly_matched", "revised",
                    "tatoeba", "level", "course_sentences", "added", "total", "note", "words"])
        for kind, codes in (("teach", teach), ("guard", guard)):
            for c in codes:
                st = state.get(c, {})
                caught = f"{st['matched']}/{st['examples']}" if "examples" in st else ""
                wrong = f"{st['wrongly_matched']}/{st['counter_examples']}" if "examples" in st else ""
                total = (st.get("course_sentences", 0) + st.get("added", 0)) if st.get("tatoeba") == "done" else ""
                w.writerow([c, kind, st.get("rule", "not run"), caught, wrong, "yes" if st.get("revised") else "",
                            st.get("tatoeba", "" if kind == "guard" else "not run"), st.get("level", ""),
                            st.get("course_sentences", ""), st.get("added", ""), total, st.get("note", ""),
                            st.get("words", "")])
        for c, label, why in skipped:
            w.writerow([c, "skipped", "", "", "", "", "", "", "", "", "", why, label])
    with open(BATCH_DIR / "selected_sentences.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["grammar_code", "hsk_level", "tatoeba_id", "hanzi", "english"])
        for c in teach:
            st = state.get(c, {})
            for s in st.get("survivors", []):
                w.writerow([c, st.get("level", ""), s["tatoeba_id"], s["hanzi"], s["english"]])


def print_summary(state, teach, guard, skipped, levels, target):
    rules_ok = [c for c in teach + guard if state.get(c, {}).get("rule") == "ok"]
    failed = [c for c in teach + guard if state.get(c, {}).get("rule") in ("failed", "error")]
    done = [c for c in teach if state.get(c, {}).get("tatoeba") == "done"]
    short = [c for c in done if state[c]["course_sentences"] + state[c]["added"] < target]
    print(f"\n=== Summary: HSK {' '.join(map(str, levels))} ===")
    print(f"  points with a rule to teach: {len(teach)}   guard points (harder, easy words): {len(guard)}   "
          f"skipped: {len(skipped)}")
    print(f"  rules OK: {len(rules_ok)}   rules failed/error: {len(failed)}")
    print(f"  filled from Tatoeba: {len(done)}   sentences added: "
          f"{sum(state[c]['added'] for c in done)}")
    print(f"  points still under the target of {target} sentences: {len(short)}")
    if failed:
        print("\n--- rules that failed (not used; worth a look) ---")
        for c in failed:
            print(f"  {c} ({state[c].get('kind')}): {state[c].get('rule')} -- {state[c].get('note', '')}")
    if short:
        print(f"\n--- under {target} sentences (Tatoeba had too few good ones) ---")
        for c in short:
            st = state[c]
            print(f"  {c} HSK {st['level']}: {st['course_sentences']} in the course + {st['added']} added")
    print(f"\nFull table: {BATCH_DIR / 'summary.csv'}")
    print(f"Chosen sentences: {BATCH_DIR / 'selected_sentences.csv'}")


# --------------------------------- MAIN ---------------------------------

def main():
    parser = argparse.ArgumentParser(description="Run the grammar trial over every grammar point.")
    parser.add_argument("--levels", nargs="+", type=int, default=[1, 2])
    parser.add_argument("--phase", choices=["all", "rules", "guards", "tatoeba"], default="all")
    parser.add_argument("--show-unmatched", action="store_true",
                        help="List the points that need words/pages matched by hand; spend nothing.")
    parser.add_argument("--only", nargs="+", default=None, help="Only these codes.")
    parser.add_argument("--limit", type=int, default=None, help="Only the first N points (to try it out).")
    parser.add_argument("--target", type=int, default=tg.DEFAULT_TARGET)
    parser.add_argument("--redo-tatoeba", action="store_true", help="Run the Tatoeba step again for done points.")
    parser.add_argument("--retry-failed", action="store_true", help="Try failed/error rules again (one more revise).")
    parser.add_argument("--dry-run", action="store_true", help="List the points and the work left; spend nothing.")
    parser.add_argument("--max-guard-level", type=int, default=4,
                        help="Guard points up to this HSK level (default 4; 5-6 constructions are rare in "
                             "simple sentences, and the difficulty review is warned about them anyway).")
    parser.add_argument("--first-writer", default=FIRST_WRITER,
                        help=f"Model that writes rules first (default {FIRST_WRITER}; failing rules go to "
                             f"{tg.WRITER_MODEL}).")
    args = parser.parse_args()

    tg.install_credit_guard()
    t0 = time.time()
    real = tg.point_db_at_scratch_copy()
    from app.textbook.db_utils import get_session, init_db
    init_db()
    tg.apply_level_overrides()
    BATCH_DIR.mkdir(parents=True, exist_ok=True)
    state = tg.load_json(STATE_PATH, {})
    with get_session() as db:
        teach, guard, skipped = plan(db, args.levels, args.max_guard_level)
        if args.only:
            teach, guard = [c for c in teach if c in args.only], [c for c in guard if c in args.only]
        if args.limit:
            teach, guard = teach[:args.limit], guard[:max(0, args.limit - len(teach))]
        for c in teach:
            state.setdefault(c, {})["kind"] = "teach"
        for c in guard:
            state.setdefault(c, {})["kind"] = "guard"
        sources = tg.load_json(tg.SOURCES_PATH, {})
        _, sections, _ = gr.load_rules(tg.RULES_PATH)
        need_sources = [c for c in teach + guard if c not in sources]
        need_rule = [c for c in teach + guard if c not in sections]
        need_tatoeba = [c for c in teach if state[c].get("tatoeba") != "done" or args.redo_tatoeba]
        if args.limit:
            print("Note: with --limit, guard rules come after the teach points, so these points' Tatoeba fills run "
                  "with few guards; the full run fills them again once the guard rules exist.")
        print(f"Working on a copy of {real}")
        print(f"Points: {len(teach)} to teach, {len(guard)} guard (harder points made of easy words), "
              f"{len(skipped)} skipped")
        print(f"Work left: {len(need_sources)} sources call(s), {len(need_rule)} rule(s) to write "
              f"(+ up to one revise each), {len(need_tatoeba)} Tatoeba fill(s)")
        low = len(need_sources) * 0.02 + len(need_rule) * 0.03 + len(need_tatoeba) * 0.03
        high = len(need_sources) * 0.04 + len(need_rule) * 0.08 + len(need_tatoeba) * 0.10
        print(f"Rough cost: ${low:.0f}-${high:.0f} (a guess). If the account runs out of credit, the run stops "
              "cleanly and continues where it stopped once you add credit.")
        if args.show_unmatched:
            show_unmatched(db, state, teach, guard)
            return
        if args.dry_run:
            print("\nTeach:", " ".join(teach))
            print("Guard:", " ".join(guard))
            print("Skipped:")
            for c, label, why in skipped:
                print(f"  {c} {label}  -- {why}")
            return

        tagger, client = tg.Tagger(db), tg.get_client()
        try:
            if args.phase in ("all", "rules"):
                todo = [c for c in teach + guard
                        if not (state[c].get("rule") == "ok"
                                or (state[c].get("rule") in ("failed", "error") and not args.retry_failed))]
                clock = [time.time()]
                for i, c in enumerate(todo, 1):
                    st = state[c]
                    if args.retry_failed and st.get("rule") in ("failed", "error"):
                        st.pop("revised", None)
                    with open(BATCH_DIR / f"{c}.log", "a", encoding="utf-8") as log, contextlib.redirect_stdout(log):
                        print(f"\n########## rules  {time.strftime('%Y-%m-%d %H:%M')} ##########")
                        try:
                            rule_for(db, c, st, tagger, client, args.first_writer)
                        except (SystemExit, Exception) as e:
                            st.update(rule="error", note=(str(e) or repr(e)).splitlines()[0][:200])
                            traceback.print_exc(file=log)
                        db.rollback()
                    save_state(state)
                    extra = (f" {st['matched']}/{st['examples']} caught, {st['wrongly_matched']} wrong"
                             if "examples" in st else "")
                    print(f"[rules {i}/{len(todo)}] {c} ({st['kind']}): {st.get('rule')}{extra}"
                          f"{' (revised)' if st.get('revised') else ''}{' -- ' + st['note'] if st.get('note') else ''}"
                          f"{pace(clock, i, len(todo))}", flush=True)
            if args.phase in ("all", "guards"):
                guard_check(db, state, teach, tagger, client)
                db.rollback()
            if args.phase in ("all", "tatoeba"):
                version = guard_version(state)
                # a point filled under other rules/guards is filled again (cheap --
                # only sentences that weren't checked before cost anything)
                todo = [c for c in teach if state[c].get("rule") == "ok"
                        and (state[c].get("tatoeba") != "done" or args.redo_tatoeba
                             or state[c].get("guard_version") != version)]
                clock = [time.time()]
                for i, c in enumerate(todo, 1):
                    st = state[c]
                    st["tatoeba"] = "in progress"       # an interrupted fill is redone next run
                    with open(BATCH_DIR / f"{c}.log", "a", encoding="utf-8") as log, contextlib.redirect_stdout(log):
                        print(f"\n########## tatoeba  {time.strftime('%Y-%m-%d %H:%M')} ##########")
                        try:
                            tatoeba_for(db, c, st, args.levels, args.target)
                            if st.get("tatoeba") == "done":
                                st["guard_version"] = version
                        except (SystemExit, Exception) as e:
                            st.update(tatoeba="error", note=(str(e) or repr(e)).splitlines()[0][:200])
                            traceback.print_exc(file=log)
                        db.rollback()
                    save_state(state)
                    if st.get("tatoeba") == "done":
                        print(f"[tatoeba {i}/{len(todo)}] {c} HSK {st['level']}: {st['course_sentences']} in the "
                              f"course + {st['added']} added{pace(clock, i, len(todo))}", flush=True)
                    else:
                        print(f"[tatoeba {i}/{len(todo)}] {c}: {st.get('tatoeba')} -- {st.get('note', '')}"
                              f"{pace(clock, i, len(todo))}", flush=True)
        except KeyboardInterrupt:
            print("\nStopped. Progress is saved: run the same command again to continue.")
        except tg.OutOfCredits as e:
            print(f"\nSTOPPED: the Anthropic account is out of credit ({str(e)[:120]}).\n"
                  "Everything finished so far is saved. Add credit and run the same command again to continue.")
        finally:
            save_state(state)
            tagger.save()
            write_reports(state, teach, guard, skipped)
            print_summary(state, teach, guard, skipped, args.levels, args.target)
            db.rollback()
    print(f"\nDone in {(time.time() - t0) / 60:.0f} min. The real database was not changed.")


if __name__ == "__main__":
    main()