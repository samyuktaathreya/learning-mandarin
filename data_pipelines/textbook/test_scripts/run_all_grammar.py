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
  rules    per point: sources -> write -> labeled test -> one automatic revise
           if it fails -> labeled test. A rule is OK when it wrongly matches
           NO counter-example and catches at least MIN_RECALL of the examples
           (with at least MIN_EXAMPLES examples to test on). Rules that fail
           are flagged and not used.
  tatoeba  per OK teach point: the Tatoeba step (guard rules active).

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
MIN_RECALL = 0.8
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

def plan(db, levels):
    from app.textbook.models import GrammarTip, Vocab
    from data_pipelines.textbook.scripts import detect_grammar as dg
    from data_pipelines.textbook.scripts.assign_units import GRAMMAR_NOT_COUNTED, GRAMMAR_ALIASES
    vocab = {h for (h,) in db.query(Vocab.hanzi).all()}
    vocab_chars = {ch for h in vocab for ch in h}
    teach, skipped = [], []
    for p in db.query(GrammarTip).filter(GrammarTip.hsk_level.in_(levels)).order_by(GrammarTip.id).all():
        c = p.grammar_code
        if c in dg.SKIP:
            skipped.append((c, p.raw_text, "describes nearly every sentence"))
        elif c in GRAMMAR_NOT_COUNTED:
            skipped.append((c, p.raw_text, "in nearly every sentence (not counted for pacing)"))
        elif c in GRAMMAR_ALIASES:
            skipped.append((c, p.raw_text, f"same point as {GRAMMAR_ALIASES[c]}"))
        else:
            words = (dg.GRAMMAR_MARKERS.get(c) or tg.EXTRA_MARKERS.get(c)
                     or tg.real_label_words(p.raw_text or "", vocab, vocab_chars))
            if words:
                teach.append(c)
            else:
                skipped.append((c, p.raw_text, "a sentence shape, no marker word"))
    teach_set = set(teach)
    guard = [c for c, _, _ in tg.harder_points_with_easy_words(db, max(levels)) if c not in teach_set]
    return teach, guard, skipped


# --------------------------------- PHASE 1: RULES ---------------------------------

def rule_for(db, code, st, tagger, client):
    """sources -> write -> test -> (revise once) -> test. Fills st."""
    point = tg.Point(db, code, None, need_sources=False)
    if code not in tg.load_json(tg.SOURCES_PATH, {}):
        tg.cmd_sources(db, point, trial_args(code=code))
        point = tg.Point(db, code, None)
    if not point.is_pages:
        st.update(rule="failed", note="no AllSet Learning page teaches this point")
        return
    _, sections, errors = gr.load_rules(tg.RULES_PATH)
    if code not in sections or code in errors:
        tg.cmd_write(db, point, trial_args(code=code, redo=True))
        _, sections, errors = gr.load_rules(tg.RULES_PATH)
    if code in errors:
        st.update(rule="failed", note=f"rule can't be read: {errors[code]}")
        return
    pos, neg = tg.labeled(point, tagger)

    def score(rule):
        missed, wrong = tg.labeled_test(rule, pos, neg)
        matched = len(pos) - len(missed)
        good = not wrong and len(pos) >= MIN_EXAMPLES and matched >= MIN_RECALL * len(pos)
        return good, missed, wrong, matched

    rule = gr.parse_rule_block(code, sections[code])
    good, missed, wrong, matched = score(rule)
    if not good and not st.get("revised") and len(pos) >= MIN_EXAMPLES:
        prompt = gr.revise_prompt(code, point.context(), sections[code], missed[:40],
                                  [(h, t) for h, t, _ in wrong][:40], [], [])
        new = tg.ask_writer(client, point, prompt, trial_args(), "revise")
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
                                      f"{matched}/{len(pos)} examples caught")


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
    parser.add_argument("--phase", choices=["all", "rules", "tatoeba"], default="all")
    parser.add_argument("--only", nargs="+", default=None, help="Only these codes.")
    parser.add_argument("--limit", type=int, default=None, help="Only the first N points (to try it out).")
    parser.add_argument("--target", type=int, default=tg.DEFAULT_TARGET)
    parser.add_argument("--redo-tatoeba", action="store_true", help="Run the Tatoeba step again for done points.")
    parser.add_argument("--retry-failed", action="store_true", help="Try failed/error rules again (one more revise).")
    parser.add_argument("--dry-run", action="store_true", help="List the points and the work left; spend nothing.")
    args = parser.parse_args()

    t0 = time.time()
    real = tg.point_db_at_scratch_copy()
    from app.textbook.db_utils import get_session, init_db
    init_db()
    BATCH_DIR.mkdir(parents=True, exist_ok=True)
    state = tg.load_json(STATE_PATH, {})
    with get_session() as db:
        teach, guard, skipped = plan(db, args.levels)
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
        sonnet = len(need_sources) + len(need_rule) * 1.5
        print(f"Rough cost: ${sonnet * 0.04 + len(need_tatoeba) * 0.03:.0f}-"
              f"${sonnet * 0.10 + len(need_tatoeba) * 0.10:.0f} (a guess -- check your Usage page after a few points)")
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
                            rule_for(db, c, st, tagger, client)
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
            if args.phase in ("all", "tatoeba"):
                guards_ok = sum(1 for c, s2 in state.items() if isinstance(s2, dict)
                                and s2.get("kind") == "guard" and s2.get("rule") == "ok")
                # a point filled before some guard rules existed is filled again (cheap --
                # only sentences that weren't checked before cost anything)
                todo = [c for c in teach if state[c].get("rule") == "ok"
                        and (state[c].get("tatoeba") != "done" or args.redo_tatoeba
                             or state[c].get("guards_ok", 0) < guards_ok)]
                clock = [time.time()]
                for i, c in enumerate(todo, 1):
                    st = state[c]
                    st["guards_ok"] = guards_ok
                    with open(BATCH_DIR / f"{c}.log", "a", encoding="utf-8") as log, contextlib.redirect_stdout(log):
                        print(f"\n########## tatoeba  {time.strftime('%Y-%m-%d %H:%M')} ##########")
                        try:
                            tatoeba_for(db, c, st, args.levels, args.target)
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
        finally:
            save_state(state)
            tagger.save()
            write_reports(state, teach, guard, skipped)
            print_summary(state, teach, guard, skipped, args.levels, args.target)
            db.rollback()
    print(f"\nDone in {(time.time() - t0) / 60:.0f} min. The real database was not changed.")


if __name__ == "__main__":
    main()