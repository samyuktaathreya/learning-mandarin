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
import io
import json
import re
import sys
import time
import traceback
from collections import Counter
from pathlib import Path
from types import SimpleNamespace

TEST_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(TEST_DIR))

import grammar_rules as gr          # noqa: E402
from data_pipelines.textbook.scripts import grammar_explanations as gx     # noqa: E402
from data_pipelines.textbook.scripts.grammar_explanations import blocks_markdown     # noqa: E402
import try_grammar_rule as tg       # noqa: E402

BATCH_DIR = tg.OUT_DIR / "batch"
STATE_PATH = tg.BATCH_STATE_PATH
MIN_RECALL = {"teach": 0.6, "guard": 0.8}   # a guard that misses harder sentences lets them leak down
FIRST_WRITER = "claude-haiku-4-5"   # writes every rule first; a rule that fails its test is revised by Sonnet
MIN_EXAMPLES = 3
# 2: a rule must also catch the EASY examples -- the ones whose words are all at or below the
# point's level, i.e. what a learner there will actually see (2-17 caught 如果/只要...就, all HSK 3-4,
# and none of the plain "clause, 就 + result" sentences an HSK 2 learner can read)
TEST_VERSION = 2
# a point is moved up by its examples only if at least this many of them use its words
MOVE_MIN_EXAMPLES = 6


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


# --------------------------------- LEVELS FROM EXAMPLES ---------------------------------

def levels_from_examples(db, codes, tagger) -> dict:
    """A point belongs at the level where its OWN words can be read. For each
    wiki example, the hardest of the point's words it uses (by the 2025 vocab
    list); the point's level is the lowest level that at least MIN_EXAMPLES
    examples reach. 2-47 is listed at HSK 2, but 以前 / 以后 are HSK 3 words,
    so no HSK 2 learner can read its examples: it moves to HSK 3. Words
    outside the point (an HSK 5 noun in an example) don't count -- 2-09's
    examples are hard, but 千 / 万 aren't. Only moves points UP.
    Returns {code: {"level", "from", "why"}}; also sets the scratch DB."""
    from collections import Counter
    from app.textbook.models import GrammarTip
    saved = {c: v for c, v in tg.load_json(tg.EXAMPLE_LEVELS_PATH, {}).items()
             if not c.startswith("_") and isinstance(v, dict)}
    out = {}
    for c in dict.fromkeys(list(codes) + list(saved)):
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                p = tg.Point(db, c, None)
                pos, _ = tg.labeled(p, tagger)
        except (SystemExit, Exception):
            if c in saved:
                out[c] = saved[c]                  # can't recompute now: keep the old answer
            continue
        base = saved.get(c, {}).get("from", p.level)
        per = []
        for r in pos.values():
            own = [(w, lv) for w, lv, _ in r.get("vocab", []) if w in p.markers and lv is not None]
            if own:
                per.append((max(lv for _, lv in own), own))
        if len(per) < MOVE_MIN_EXAMPLES:          # too little evidence to move a point (2-06 had 3)
            continue
        per.sort(key=lambda x: x[0])
        need = per[MIN_EXAMPLES - 1][0]
        if need > base:
            hard = Counter(f"{w} L{lv}" for _, own in per for w, lv in own if lv > base)
            readable = sum(1 for lv, _ in per if lv <= base)
            out[c] = {"level": need, "from": base,
                      "why": f"{readable} of {len(per)} examples use only HSK {base} words of the point; "
                             f"the rest need {', '.join(w for w, _ in hard.most_common(4))}"}
    for tip in db.query(GrammarTip).filter(GrammarTip.grammar_code.in_(list(set(out) | set(saved)))).all():
        c = tip.grammar_code
        tip.hsk_level = out[c]["level"] if c in out else saved[c]["from"]    # moved, or moved back
    db.commit()
    tg.EXAMPLE_LEVELS_PATH.write_text(json.dumps(
        {"_readme": "Grammar points moved UP because their own words (2025 vocab list) are above their "
                    "listed level in most of their wiki examples. Written by run_all_grammar.py each run; "
                    "applied to the scratch DB only. Delete an entry to see it recomputed.",
         **dict(sorted(out.items()))}, ensure_ascii=False, indent=1), encoding="utf-8")
    return out


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
        easy_missed = [m for m in missed if m[0] in easy]
        easy_ok = len(easy) < MIN_EXAMPLES or len(easy) - len(easy_missed) >= min_recall * len(easy)
        good = not wrong and len(pos) >= MIN_EXAMPLES and matched >= min_recall * len(pos) and easy_ok
        return good, missed, wrong, matched

    # examples a learner at the point's level can read (the guard check uses the same ones)
    easy = {h for h, r in pos.items() if r.get("level") is not None and r["level"] <= point.first_level}

    rule = gr.parse_rule_block(code, sections[code])
    good, missed, wrong, matched = score(rule)
    if not good and not st.get("revised") and len(pos) >= MIN_EXAMPLES:
        missed_easy_first = sorted(missed, key=lambda m: m[0] not in easy)
        prompt = gr.revise_prompt(code, point.context(), sections[code], missed_easy_first[:40],
                                  [(h, t) for h, t, _ in wrong][:40], [], [])
        small = gr.revise_prompt(code, point.context(full=False), sections[code], missed_easy_first[:12],
                                 [(h, t) for h, t, _ in wrong][:12], [], [])
        easy_missed = [h for h, _ in missed if h in easy]
        if len(easy) >= MIN_EXAMPLES and easy_missed:
            note = (f"\n\nMOST IMPORTANT: this point is taught at HSK {point.first_level}. These missed examples "
                    f"use only words a learner at that level knows, so they are the kind of sentence the rule "
                    f"will actually be used on. Catching them matters more than the examples with harder "
                    f"words (如果/只要-style openers etc.). Missed easy examples:\n"
                    + "\n".join(f"  {h}" for h in easy_missed[:25]))
            prompt, small = prompt + note, small + note
        try:
            new = tg.ask_writer(client, point, prompt, trial_args(), "revise", small_prompt=small)
        except SystemExit as e:
            if "empty" not in str(e):
                raise
            new = tg.ask_writer(client, point, small, trial_args(writer_model=first_writer), "revise")
        gr.save_section(tg.RULES_PATH, code, new)
        st["revised"] = True
        good, missed, wrong, matched = score(gr.parse_rule_block(code, new))
    tg.print_labeled(point, pos, neg, missed, wrong)
    easy_matched = len(easy) - sum(1 for h, _ in missed if h in easy)
    print(f"  easy examples (words all HSK {point.first_level} or below) matched: {easy_matched}/{len(easy)}"
          + ("   (too few to test)" if len(easy) < MIN_EXAMPLES else f"   <- needs {min_recall:.0%}"))
    st.update(examples=len(pos), counter_examples=len(neg), matched=matched, wrongly_matched=len(wrong),
              easy_examples=len(easy), easy_matched=easy_matched, test_version=TEST_VERSION,
              first_level=point.first_level, words=" ".join(point.markers))
    if good:
        st.update(rule="ok", note="")
    elif len(pos) < MIN_EXAMPLES:
        st.update(rule="failed", note=f"only {len(pos)} labeled example(s): can't test the rule")
    else:
        st.update(rule="failed", note=f"{len(wrong)} counter-example(s) wrongly matched, "
                                      f"{matched}/{len(pos)} examples caught, {easy_matched}/{len(easy)} easy "
                                      f"ones (each needs {min_recall:.0%})")


# --------------------------------- PHASE 1b: GUARD CHECK ---------------------------------

def guard_check(db, state, teach, tagger, client):
    """Every OK rule acts as a guard when a LOWER point is filled (an HSK 2
    rule for an HSK 1 fill, an HSK 3 rule for HSK 1-2 fills...). Here each one
    is run on the labeled examples of every lower teach point -- only the ones
    whose words are at that point's level. Each flagged example is checked by
    AI as a pair: does it use the guard's point BEYOND what the lower point
    already teaches? If yes, fine; if no, the guard is wrong there. A wrong guard is revised once
    (Sonnet), told these sentences are the lower point, not its own; if it
    still flags them, it is not used for those points (state "not_for")."""
    from app.textbook.models import GrammarTip
    level_of = dict(db.query(GrammarTip.grammar_code, GrammarTip.hsk_level).all())
    cache = tg.load_json(tg.CACHE_PATH, {})
    lower, points = {}, {}
    for c in teach:
        if state.get(c, {}).get("rule") == "ok":
            p = tg.Point(db, c, None)
            points[c] = p
            # only examples that are themselves at the point's level by their words (and the
            # HSK 1 grammar restriction): one with harder words says nothing about a guard
            pos = {h: r for h, r in tg.labeled(p, tagger)[0].items()
                   if r.get("level") is not None and r["level"] <= p.first_level}
            lower[c] = (p.first_level, pos)
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
            g_words = (st.get("words") or "").split()      # as in the fills: only with the point's words
            flagged = {(c, h): r for c, pos in targets.items() for h, r in pos.items()
                       if (not g_words or any(w in h for w in g_words)) and rule.found(tg.as_clauses(r))}
            if not flagged:
                return {}
            per_point = {}
            for (c, h), r in flagged.items():
                per_point.setdefault(c, []).append((h, gr.render(tg.as_clauses(r))))
            # Each flagged example is checked as a PAIR: does it use the guard's point beyond
            # what the lower point already teaches? The lists overlap (1-11 lists 在 too, but
            # 他在看书 is just 1-42), so "it contains the guard's point" isn't enough. A "no"
            # means the guard is wrong there; unsure counts as "yes" (the guard stays).
            gp = tg.Point(db, g, None)
            bad = {}
            for c, items in per_point.items():
                beyond = tg.uses_beyond(client, points[c], gp, items, cache)
                wrong = [x for x in items if not beyond.get(x[0], True)]
                if wrong:
                    bad[c] = wrong
            return bad

        with open(BATCH_DIR / f"{g}.log", "a", encoding="utf-8") as log, contextlib.redirect_stdout(log):
            print(f"\n########## guard check  {time.strftime('%Y-%m-%d %H:%M')} ##########")
            bad = {}
            try:
                bad = wrong_flags(rules[g])
                new_rule = None
                if bad and not st.get("guard_revised3"):
                    gp = tg.Point(db, g, None)
                    examples = [x for c in sorted(bad) for x in bad[c]][:40]
                    print(f"Flags examples of {', '.join(sorted(bad))} that don't contain {g}: revising.")
                    prompt = gr.revise_prompt(g, gp.context(), sections[g], [], examples, [], [])
                    prompt += (f"\n\nThe counter-examples above are example sentences of EASIER grammar points "
                               f"({', '.join(sorted(bad))}), not of {g}. Your rule must not match them.")
                    st["guard_revised3"] = True
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
              + (" (revised)" if any(st.get(k) for k in ("guard_revised", "guard_revised2", "guard_revised3")) else ""), flush=True)
    return changed


def guard_version(state) -> str:
    """Changes whenever the rules or which guards apply where change: a point
    filled under an older version is filled again (cheap -- cached)."""
    import hashlib
    rules_text = tg.RULES_PATH.read_text(encoding="utf-8") if tg.RULES_PATH.exists() else ""
    rules_text += tg.LEVELS_PATH.read_text(encoding="utf-8") if tg.LEVELS_PATH.exists() else ""
    rules_text += tg.EXAMPLE_LEVELS_PATH.read_text(encoding="utf-8") if tg.EXAMPLE_LEVELS_PATH.exists() else ""
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


# --------------------------------- PHASE 3: AI-WRITTEN SENTENCES ---------------------------------

AI_GEN_PATH = tg.TEST_DIR / "ai_generated.json"      # every AI-written sentence, kept or not
AI_SOURCE = "ai_generated"
MAX_AI_ROUNDS = 2                                     # writing calls per point per run


def ai_fill_for(db, code, st, target, client, write=True):
    """Points Tatoeba left short: Sonnet writes new sentences (saved in
    ai_generated.json, so they are written once), and they go through the
    same steps as Tatoeba's -- stricter on vocabulary, no second opinion.
    Kept ones are labelled ai_generated everywhere."""
    import hashlib
    import math
    point = tg.Point(db, code, None)
    level = st["level"]
    short = target - st["course_sentences"] - st["added"]
    if short <= 0:
        st.update(ai="not needed", ai_added=0, ai_survivors=[])
        return
    from app.textbook.models import Vocab
    words = [h for (h,) in db.query(Vocab.hanzi).filter(Vocab.hsk_level.isnot(None), Vocab.hsk_level <= level)
             .order_by(Vocab.hsk_level, Vocab.id).all()]
    _, sections, _ = gr.load_rules(tg.RULES_PATH)
    avoid = [f"{c} (HSK {lv}) {lab}" for c, lv, lab in tg.harder_points_with_easy_words(db, level)]
    have = ([s.hanzi for s in tg.pipeline_sentences(db, point) if s.hsk_level == level]
            + [x["hanzi"] for x in st.get("survivors", [])])
    store = tg.load_json(AI_GEN_PATH, {})
    mine = store.setdefault(code, [])
    tid_of = lambda zh: "ai-" + hashlib.md5(zh.encode()).hexdigest()[:10]
    res, written = None, 0
    for rnd in range(MAX_AI_ROUNDS + 1):
        pairs = {tid_of(x["zh"]): {"hanzi": x["zh"], "original": x["zh"], "english": [x["en"]]} for x in mine}
        print(f"\n----- AI-written sentences for {code}, round {rnd + 1}: {len(pairs)} to check, "
              f"{short} needed -----")
        res = (tg.cmd_tatoeba(db, point, trial_args(code=code, target=target, level=level, ai_pairs=pairs,
                                                    need=short, exclude=have))
               if pairs else {"added": 0, "survivors": [], "outcome": {}})
        if res["added"] >= short or rnd == MAX_AI_ROUNDS or not write:
            break
        n = max(10, math.ceil(2.5 * (short - res["added"])))
        print(f"  Asking {tg.WRITER_MODEL} to write {n} sentence(s)...", flush=True)
        prompt = gr.generate_prompt(code, level, point.context(full=False), sections.get(code, ""), words,
                                    point.markers, avoid, have, [x["zh"] for x in mine], n)
        try:
            items = gr.extract_json_array(tg.call(client, tg.WRITER_MODEL, prompt, 300 + 120 * n))
        except (ValueError, json.JSONDecodeError) as e:
            print(f"  The answer couldn't be read ({e}); trying once more next round.")
            continue
        seen = {x["zh"] for x in mine}
        for it in items:
            zh, en = str(it.get("zh", "")).strip(), str(it.get("en", "")).strip()
            if zh and en and zh not in seen:
                mine.append({"zh": zh, "en": en, "by": tg.WRITER_MODEL, "date": time.strftime("%Y-%m-%d")})
                seen.add(zh)
                written += 1
        tg.save_json(AI_GEN_PATH, store)
    by = {x["zh"]: x for x in mine}
    # checks run in batches, so more can pass than were needed: keep only what the point needs
    # (shortest first) -- AI sentences are a last resort, never extras
    keep, spare = res["survivors"][:short], res["survivors"][short:]
    for x in spare:
        res["outcome"][x["tatoeba_id"]] = ["-", "passed every step, but not needed (target reached)"]
    st.update(ai="done", ai_added=len(keep), ai_written=len(mine),
              ai_survivors=[dict(x, generated_by=f"{by[x['hanzi']]['by']} {by[x['hanzi']]['date']}")
                            for x in keep],
              ai_log=[{"hanzi": x["zh"], "english": x["en"], "generated_by": f"{x['by']} {x['date']}",
                       "result": " ".join(res["outcome"].get(tid_of(x["zh"]), ["-", "not checked"]))}
                      for x in mine])


# --------------------------------- EXPORT FOR THE PIPELINE ---------------------------------

def surface_split(hanzi: str, clauses) -> list | None:
    """The trial's word split as [[surface, list word or None], ...] covering
    every Chinese character of the sentence, for the pipeline to tag with
    (Sentence.word_split). The list word differs only where the text writes
    it without 儿 (有点 for 有点儿). None if the words don't line up."""
    text = "".join(tg._CJK_RE.findall(hanzi))
    out, pos = [], 0
    for clause in clauses:
        for w, _ in clause:
            w = "".join(tg._CJK_RE.findall(w))
            if not w:
                continue
            if text.startswith(w, pos):
                out.append([w, None])
                pos += len(w)
            elif w.endswith("儿") and len(w) > 1 and text.startswith(w[:-1], pos):
                out.append([w[:-1], w])
                pos += len(w) - 1
            else:
                return None
    return out if pos == len(text) else None


ASG_URL = "https://resources.allsetlearning.com/chinese/grammar/{}"
ASG_LICENSE = "CC BY-NC-SA 3.0"
HSK_LIST_ATTRIBUTION = ("Official HSK grammar list (Chinese Proficiency Grading Standards for International "
                        "Chinese Language Education)")


EXPLANATION_AI_PATH = tg.TEST_DIR / "explanation_ai.json"   # every AI answer, so each is paid for once
EXPLAIN_MODEL = "claude-sonnet-5-5"
PINYIN_MODEL = tg.CHECK_MODEL            # a cheap model: it only writes readings, and code checks every one
PINYIN_BATCH = 10                        # sentences per call
PINYIN_FIXES_PATH = tg.TEST_DIR / "pinyin_fixes.json"   # readings fixed by hand after review; win over any AI


# ---- pinyin for examples that are not course sentences ----

_READINGS = {}


class CacheSaver:
    """Puts AI answers into the cache from worker threads and writes the
    file every `every` answers, so a run that is cut off (a dead battery)
    loses at most a few paid answers. The write itself is atomic."""
    def __init__(self, cache: dict, every: int = 10):
        import threading
        self.cache, self.every, self.n, self.lock = cache, every, 0, threading.Lock()

    def put(self, key, value):
        with self.lock:
            self.cache[key] = value
            self.n += 1
            if self.n % self.every == 0:
                tg.save_json(EXPLANATION_AI_PATH, self.cache, merge=False)


def readings_of(ch: str) -> set:
    """Every reading pypinyin lists for the character ("xing2", "hang2", ...)."""
    if ch not in _READINGS:
        from pypinyin import pinyin, Style
        _READINGS[ch] = {r.lower() for r in pinyin(ch, style=Style.TONE3, heteronym=True,
                                                   neutral_tone_with_five=True)[0]}
    return _READINGS[ch]


def check_pinyin(words: list[str], answer) -> tuple[list | None, str]:
    """(pieces [(word, citation syllables)], "") if the answer has one
    syllable per character and each syllable is a reading of that character
    (any reading in the neutral tone is allowed too); else (None, what was
    wrong). An answer that groups the words differently (早点儿 as one string
    where the split has 早点 | 儿) is regrouped when its syllables still line
    up one per character. 不 / 一 are put back in citation tones (code does
    sandhi)."""
    from data_pipelines.textbook.scripts import finalize as fz
    if not isinstance(answer, list) or not answer:
        return None, f"expected {len(words)} pinyin strings, one per word"
    strings = [str(p).lower().replace("ü", "v").replace("u:", "v").replace(" ", "").replace("'", "")
               for p in answer]
    for p in strings:
        if not re.fullmatch(r"(?:[a-z]+[1-5])+", p):
            return None, f"'{p}' is not pinyin with tone numbers"
    if len(strings) == len(words) and all(len(fz.syllables(p)) == len(w) for w, p in zip(words, strings)):
        grouped = [fz.syllables(p) for p in strings]
    else:
        flat = [x for p in strings for x in fz.syllables(p)]
        if len(flat) != sum(len(w) for w in words):
            return None, (f"{len(flat)} syllables for {sum(len(w) for w in words)} characters "
                          f"(one pinyin string per word: {' | '.join(words)})")
        grouped, i = [], 0
        for w in words:
            grouped.append(flat[i:i + len(w)])
            i += len(w)
    pieces = []
    for w, sylls in zip(words, grouped):
        out = []
        for ch, syl in zip(w, sylls):
            base, tone = syl[:-1], syl[-1]
            allowed = readings_of(ch)
            bases = {r[:-1] for r in allowed}
            if ch == "儿" and syl == "r5":
                pass
            elif ch in "不一" and base in bases:
                syl = "bu4" if ch == "不" else "yi1"
            elif allowed and syl not in allowed and not (tone == "5" and base in bases):
                return None, f"{w}: {ch} can't be read '{syl}' (readings: {', '.join(sorted(allowed))})"
            out.append(syl)
        pieces.append((w, out))
    return pieces, ""


def pypinyin_words(words: list[str]) -> list[str]:
    from pypinyin import lazy_pinyin, Style
    return ["".join(x.lower() for x in lazy_pinyin(w, style=Style.TONE3, neutral_tone_with_five=True))
            for w in words]


def pypinyin_disagrees(pieces) -> list[str]:
    """A second, free opinion: pypinyin reads each word with its phrase
    dictionary (长大 zhang3da4). The words where it reads a character
    differently -- not just neutral vs full tone, and not 不/一 -- or []."""
    from data_pipelines.textbook.scripts import finalize as fz
    where = []
    for (w, sylls), theirs in zip(pieces, pypinyin_words([w for w, _ in pieces])):
        theirs = fz.syllables(theirs)
        if len(theirs) != len(sylls):
            continue
        for ch, mine, other in zip(w, sylls, theirs):
            if ch in "不一" or (ch == "儿" and mine == "r5"):
                continue
            if mine[:-1] != other[:-1] or (mine[-1] != other[-1] and "5" not in (mine[-1], other[-1])):
                where.append(w)
                break
    return where


def ask_pinyin(client, model: str, prompt_fn, keys: list, entry_of, key_of, saver: CacheSaver, today: str):
    """Asks `model` for the pinyin of `keys` (batches, in parallel), checks
    every answer, asks once more for the ones that failed (saying why).
    Saves {"pieces"} or, after two failed checks, {"failed"}; a sentence with
    no readable answer is not saved, so the next export asks again."""
    from concurrent.futures import ThreadPoolExecutor
    problems = {}
    for round_ in (1, 2):
        entries = [dict(entry_of(k), i=n, problem=problems.get(k)) for n, k in enumerate(keys)]
        batches = [entries[i:i + PINYIN_BATCH] for i in range(0, len(entries), PINYIN_BATCH)]

        def run(batch):
            words = sum(len(e["words"]) for e in batch)
            try:
                ans = gr.extract_json(tg.call(client, model, prompt_fn(batch), 400 + 40 * words))
            except (ValueError, json.JSONDecodeError) as e:
                print(f"    [warning] a pinyin answer couldn't be read ({e}); asked again next export")
                return
            got = {a.get("i"): a.get("pinyin") for a in ans.get("answers") or [] if isinstance(a, dict)}
            for e in batch:
                k = keys[e["i"]]
                if e["i"] not in got:
                    problems[k] = "no answer for this sentence"
                    continue
                pieces, problem = check_pinyin(e["words"], got[e["i"]])
                if pieces:
                    saver.put(key_of(k), {"pieces": pieces, "_by": f"{model} {today}"})
                    problems.pop(k, None)
                elif round_ == 2:
                    saver.put(key_of(k), {"failed": problem, "_by": f"{model} {today}"})
                else:
                    problems[k] = problem
        try:
            with ThreadPoolExecutor(max_workers=tg.WORKERS) as pool:
                list(pool.map(run, batches))
        finally:
            tg.save_json(EXPLANATION_AI_PATH, saver.cache, merge=False)
        keys = [k for k in keys if key_of(k) not in saver.cache and k in problems]
        if not keys:
            break


def ai_pinyin(items: list[dict], tags: dict, client, ai_cache: dict, stats: Counter) -> None:
    """Example items with no pinyin (not course sentences, not the wiki's):
      1. PINYIN_MODEL (cheap) writes the readings; check_pinyin checks each
         is a real reading of its character (a failed answer is asked again
         once, saying what was wrong);
      2. pypinyin reads it too (free). Where they disagree on a character,
         or step 1 failed twice, JUDGE_MODEL sees both drafts and writes the
         correct one -- checked the same way;
      3. sandhi in code, exactly like course sentences; labelled
         pinyin_by "AI (model date)" with the model that wrote the final answer.
    No checked answer: left without pinyin and listed. Never guessed."""
    from data_pipelines.textbook.scripts import finalize as fz
    from data_pipelines.textbook.scripts import grammar_explanations as gx
    today = time.strftime("%Y-%m-%d")
    fixes = {gx.cjk_key(k): v for k, v in tg.load_json(PINYIN_FIXES_PATH, {}).items() if not k.startswith("_")}
    todo, fixed, bad_fixes = {}, set(), set()     # todo: cjk text -> (hanzi, english, words)
    for it in items:
        if it.get("pinyin") or not gx.cjk_key(it.get("hanzi")):
            continue
        key = gx.cjk_key(it["hanzi"])
        split = surface_split(it["hanzi"], (tags.get(it["hanzi"]) or {}).get("clauses") or [])
        words = [w for w, _ in split] if split else list(key)
        if key in fixes:                          # fixed by hand: checked like an AI answer, no AI asked
            pieces, problem = check_pinyin(words, str(fixes[key].get("pinyin") or "").split())
            if pieces:
                text, _ = fz.sandhi_pieces(key, pieces, Counter())
                it.update(pinyin=text, pinyin_format=gx.NUMERIC, pinyin_by="hand (fixed after review)")
                fixed.add(key)
                continue
            if key not in bad_fixes:
                print(f"    [warning] {PINYIN_FIXES_PATH.name}: the fix for {it['hanzi']} fails the checks "
                      f"({problem}) -- not used")
                bad_fixes.add(key)
        if key not in todo:
            todo[key] = (it["hanzi"], it.get("english") or "", words)
    if fixed:
        stats[f"example pinyin fixed by hand ({PINYIN_FIXES_PATH.name})"] = len(fixed)

    def first_key(k):
        return json.dumps(["pinyin", gr.PINYIN_VERSION, PINYIN_MODEL, k, todo[k][2]], ensure_ascii=False)

    def judge_key(k):
        return json.dumps(["pinyin_judge", gr.PINYIN_VERSION, tg.JUDGE_MODEL, k, todo[k][2]], ensure_ascii=False)

    def entry(k):
        return {"hanzi": todo[k][0], "english": todo[k][1], "words": todo[k][2]}

    saver = CacheSaver(ai_cache)
    asked = [k for k in todo if first_key(k) not in ai_cache]
    if asked and client is None:
        raise SystemExit("The example pinyin needs AI calls but no client was given.")
    if asked:
        print(f"  {PINYIN_MODEL}: pinyin for {len(asked)} example(s) that are not course sentences...", flush=True)
        ask_pinyin(client, PINYIN_MODEL, gr.pinyin_prompt, asked, entry, first_key, saver, today)

    # step 2: disagreements and failures go to the judge
    first, doubt = {}, {}
    for k in todo:
        a = ai_cache.get(first_key(k))
        if not isinstance(a, dict):
            continue                              # no readable answer yet: asked again next export
        first[k] = a
        if a.get("pieces"):
            where = pypinyin_disagrees(a["pieces"])
            if where:
                doubt[k] = where
        elif a.get("failed"):
            doubt[k] = ["(first answer failed: " + a["failed"] + ")"]

    def judge_entry(k):
        a = first[k]
        return dict(entry(k), drafts={"A": ["".join(sy) for _, sy in a["pieces"]] if a.get("pieces") else None,
                                      "B": pypinyin_words(todo[k][2])})
    to_judge = [k for k in doubt if judge_key(k) not in ai_cache]
    if to_judge:
        if client is None:
            raise SystemExit("The example pinyin needs AI calls but no client was given.")
        print(f"  {tg.JUDGE_MODEL}: settling {len(to_judge)} example(s) where {PINYIN_MODEL} and pypinyin "
              f"disagree...", flush=True)
        ask_pinyin(client, tg.JUDGE_MODEL, gr.pinyin_judge_prompt, to_judge, judge_entry, judge_key, saver, today)

    # step 3: the final answer into the items
    final, failed, report = {}, {}, []
    for k in todo:
        a = ai_cache.get(judge_key(k)) if k in doubt else first.get(k)
        if isinstance(a, dict) and a.get("pieces"):
            final[k] = a
        elif isinstance(a, dict) and a.get("failed"):
            failed[k] = a["failed"]
        if k in doubt:
            f = first.get(k) or {}
            report.append([todo[k][0], " ".join("".join(sy) for _, sy in f["pieces"]) if f.get("pieces") else "",
                           " ".join(pypinyin_words(todo[k][2])),
                           " ".join("".join(sy) for _, sy in final[k]["pieces"]) if k in final else
                           ("(not answered yet)" if k not in failed else "(no pinyin: " + failed[k] + ")"),
                           " ".join(doubt[k])])
    for it in items:
        k = gx.cjk_key(it.get("hanzi"))
        if it.get("pinyin") or k not in todo:
            continue
        if k in final:
            text, _ = fz.sandhi_pieces(k, [(w, list(sy)) for w, sy in final[k]["pieces"]], Counter())
            it.update(pinyin=text, pinyin_format=gx.NUMERIC, pinyin_by=f"AI ({final[k]['_by']})")
        else:
            it.update(pinyin="", pinyin_format=None, pinyin_by=None)
    stats["example pinyin written by AI (checked)"] = len(final)
    stats[f"  of which settled by {tg.JUDGE_MODEL} (it and pypinyin disagreed, or the first answer failed)"] = \
        sum(1 for k in final if k in doubt)
    if report:
        path = tg.OUT_DIR / "example_pinyin_disagreements.csv"
        with open(path, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.writer(f)
            w.writerow(["hanzi", PINYIN_MODEL, "pypinyin", f"final ({tg.JUDGE_MODEL})", "words that differ"])
            w.writerows(sorted(report))
        print(f"  disagreements and what was chosen: {path}")
    unanswered = [k for k in todo if k not in final and k not in failed]
    if unanswered:
        stats["examples with no readable AI answer yet (asked again next export)"] = len(unanswered)
    if failed:
        stats["examples left without pinyin (no answer passed the checks)"] = len(failed)
        for k, why in sorted(failed.items())[:15]:
            print(f"    no pinyin: {todo[k][0]}  ({why})")


def numbered_blocks(blocks: list[dict]) -> str:
    lines = []
    for n, b in enumerate(blocks):
        if b["type"] in ("heading", "paragraph"):
            lines.append(f"[{n}] {b['type'].upper()}: {b['text']}")
        elif b["type"] == "structure":
            lines.append(f"[{n}] STRUCTURE (fixed): " + " | ".join(b["lines"]))
        else:
            lines.append(f"[{n}] {b['type'].upper()} (fixed): "
                         + " / ".join(it["hanzi"] for it in b["items"][:4]) + (" ..." if len(b["items"]) > 4 else ""))
    return "\n".join(lines)


def apply_edits(blocks: list[dict], edits: list) -> tuple[list[dict], bool]:
    """Only headings and paragraphs can change; anything else in the answer is ignored."""
    blocks = [dict(b) for b in blocks]
    remove, changed = set(), False
    for e in edits if isinstance(edits, list) else []:
        try:
            n = int(e.get("n"))
        except (TypeError, ValueError, AttributeError):
            continue
        if not (0 <= n < len(blocks)) or blocks[n]["type"] not in ("heading", "paragraph"):
            continue
        if e.get("remove"):
            remove.add(n)
            changed = True
        elif isinstance(e.get("text"), str) and e["text"].strip() and e["text"].strip() != blocks[n]["text"]:
            blocks[n]["text"] = e["text"].strip()
            changed = True
    blocks = [b for i, b in enumerate(blocks) if i not in remove]
    # a heading left with nothing under it goes too
    blocks = [b for i, b in enumerate(blocks)
              if not (b["type"] == "heading" and (i + 1 == len(blocks) or blocks[i + 1]["type"] == "heading"))]
    return blocks, changed


def ask_json(client, prompt: str, max_tokens: int):
    return gr.extract_json(tg.call(client, EXPLAIN_MODEL, prompt, max_tokens))


def build_explanations(db, tagger, client=None) -> dict:
    """{code: [rows]} for EVERY grammar point, in display order:
      1. the AllSet Learning pages that teach it, each in the wiki's own order
         (blocks); for a point learners meet (it has a unit), an AI read-through
         first fixes prose that makes no sense in the app (edited_by is set);
      2. for a point learners meet that NO page teaches: a short tip WRITTEN BY
         AI (source ai_generated, generated_by), with real course sentences as
         its examples;
      3. the official HSK list's entry (Chinese label + its examples), last.
    `readable` on every example: all its words at or below the point's level.
    AI answers are saved in explanation_ai.json, so each is paid for once."""
    import hashlib
    from concurrent.futures import ThreadPoolExecutor
    from app.textbook.models import GrammarTip, Sentence, SentenceGrammar, SentenceSource
    asg = {p["id"]: p for p in tg.load_json(tg.ASG_PATH, {}).get("points", [])}
    if asg and not any("blocks" in p for p in asg.values()):
        raise SystemExit(f"{tg.ASG_PATH.name} has no page blocks yet: run parse_asg.py first.")
    sources = tg.load_json(tg.SOURCES_PATH, {})
    manual = tg.load_json(tg.MANUAL_PATH, {})
    rules_text = {}
    _, sections, _ = gr.load_rules(tg.RULES_PATH)
    for c, sec in sections.items():
        m = re.search(r"\*\*Covers:\*\*\s*(.+)", sec)
        rules_text[c] = m.group(1).strip()[:400] if m else ""
    ai_cache = tg.load_json(EXPLANATION_AI_PATH, {})
    today = time.strftime("%Y-%m-%d")

    plan_, texts = {}, set()
    for tip in db.query(GrammarTip).order_by(GrammarTip.id).all():
        c = tip.grammar_code
        content = tg.load_json_str(tip.content_json)
        official = list(dict.fromkeys(s for k, v in content.items() if "example" in k.lower()
                                      for s in tg._cjk_strings(v)))
        m = manual.get(c, {}) if isinstance(manual.get(c), dict) else {}
        ids = list(dict.fromkeys(list((sources.get(c) or {}).get("is", {})) + list(m.get("is", {}))))
        pages = [asg[i] for i in ids if i in asg and i not in m.get("not", {})]
        course = []
        if tip.unit_id is not None and not pages:      # an AI tip: real course sentences as its examples
            course = [(s.hanzi, s.english) for s in
                      db.query(Sentence).join(SentenceGrammar, SentenceGrammar.sentence_id == Sentence.id)
                      .filter(SentenceGrammar.grammar_tip_id == tip.id, Sentence.unit_id.isnot(None),
                              Sentence.hsk_level <= tip.hsk_level, Sentence.english != "",
                              Sentence.source.in_([SentenceSource.hsa, SentenceSource.external]))
                      .all()]
            course = sorted(dict.fromkeys(course), key=lambda x: len(x[0]))[:20]
        plan_[c] = (tip, official, pages, course)
        texts.update(official)
        texts.update(it["hanzi"] for p in pages for b in p.get("blocks", []) if b["type"] == "examples"
                     for it in b["items"])
    tags = tagger.tag_all(sorted(texts), "explanation example(s)")

    def readable(h, level):
        r = tags.get(h) or {}
        return r.get("level") is not None and r["level"] <= level

    # ---- AI work, in parallel, cached ----
    jobs = {}
    for c, (tip, official, pages, course) in plan_.items():
        if tip.unit_id is None:
            continue
        for p in pages:
            key = json.dumps(["edit", gr.EXPLANATION_AI_VERSION, EXPLAIN_MODEL, p["id"], tip.hsk_level,
                              hashlib.md5(json.dumps(p["blocks"], ensure_ascii=False).encode()).hexdigest()])
            if key not in ai_cache:
                jobs[key] = (gr.edit_page_prompt(p["title"], tip.hsk_level, numbered_blocks(p["blocks"])), 1500)
        if not pages:
            key = json.dumps(["tip", gr.EXPLANATION_AI_VERSION, EXPLAIN_MODEL, c, tip.hsk_level],
                             ensure_ascii=False)
            if key not in ai_cache:
                jobs[key] = (gr.tip_prompt(c, tip.hsk_level, tip.raw_text, official, rules_text.get(c, ""), course),
                             1500)
    if jobs:
        if client is None:
            raise SystemExit("The explanations need AI calls but no client was given.")
        n_edit = sum(1 for k in jobs if k.startswith('["edit"'))
        print(f"  {EXPLAIN_MODEL}: reading {n_edit} wiki page(s) and writing {len(jobs) - n_edit} tip(s) "
              f"(saved to {EXPLANATION_AI_PATH.name})...", flush=True)

        def run(item):
            key, (prompt, max_tokens) = item
            try:
                ans = ask_json(client, prompt, max_tokens)
                if isinstance(ans, dict):
                    ans["_by"] = f"{EXPLAIN_MODEL} {today}"     # who wrote / edited it, and when
                saver.put(key, ans)
            except (ValueError, json.JSONDecodeError) as e:
                print(f"    [warning] an answer couldn't be read ({e}); asked again next export")
        saver = CacheSaver(ai_cache)
        try:
            with ThreadPoolExecutor(max_workers=tg.WORKERS) as pool:
                list(pool.map(run, jobs.items()))
        finally:
            tg.save_json(EXPLANATION_AI_PATH, ai_cache, merge=False)

    out = {"_readme": "Grammar explanations for the pipeline (load_hsk_lists.py -> grammar_explanations), in display "
                      "order. AllSet Learning Chinese Grammar Wiki pages (" + ASG_LICENSE + ": credit + link, "
                      "non-commercial only; edited_by = an AI fixed prose that made no sense in the app), then for "
                      "points no page teaches a tip WRITTEN BY AI (source ai_generated, generated_by), then the "
                      "official HSK list. Written by run_all_grammar.py --export."}
    stats = Counter()
    for c, (tip, official, pages, course) in plan_.items():
        lvl = tip.hsk_level
        wiki = []
        for p in pages:
            blocks, edited = p["blocks"], False
            key = json.dumps(["edit", gr.EXPLANATION_AI_VERSION, EXPLAIN_MODEL, p["id"], lvl,
                              hashlib.md5(json.dumps(p["blocks"], ensure_ascii=False).encode()).hexdigest()])
            if tip.unit_id is not None and isinstance(ai_cache.get(key), dict):
                blocks, edited = apply_edits(blocks, ai_cache[key].get("edits") or [])
                stats["wiki pages edited by AI" if edited else "wiki pages read by AI, left as is"] += 1
            for b in blocks:
                if b["type"] == "examples":
                    for it in b["items"]:
                        it["readable"] = readable(it["hanzi"], lvl)
            ex = [it for b in blocks if b["type"] == "examples" for it in b["items"]]
            hint = min(p["hsk_2021"]) if p.get("hsk_2021") else None
            credit = f'"{p["title"]}", AllSet Learning Chinese Grammar Wiki'
            wiki.append({"source": "allset_learning", "source_ref": p["id"], "title": p["title"],
                         "structure": "\n".join(l for b in blocks if b["type"] == "structure" for l in b["lines"])
                                      or None,
                         "explanation": blocks_markdown(blocks) or None, "body": blocks, "level_hint": hint,
                         "url": ASG_URL.format(p["id"]),
                         "attribution": (f"Adapted from {credit} (prose edited by AI for this app)" if edited
                                         else credit),
                         "license": ASG_LICENSE, "examples": ex,
                         "edited_by": ai_cache[key].get("_by") if edited else None, "generated_by": None})
        wiki.sort(key=lambda r: (r["level_hint"] is None or r["level_hint"] > lvl,
                                 -sum(e["readable"] for e in r["examples"])))
        rows = wiki
        if not pages and tip.unit_id is not None:
            key = json.dumps(["tip", gr.EXPLANATION_AI_VERSION, EXPLAIN_MODEL, c, lvl], ensure_ascii=False)
            a = ai_cache.get(key)
            if isinstance(a, dict) and a.get("paragraphs"):
                picks = [course[i] for i in a.get("examples") or [] if isinstance(i, int) and 0 <= i < len(course)]
                ex = [{"hanzi": h, "pinyin": "", "english": e, "marked": [], "readable": readable(h, lvl)}
                      for h, e in picks[:5]]
                blocks = [{"type": "paragraph", "text": str(t)} for t in a["paragraphs"] if str(t).strip()]
                lines = [str(x) for x in a.get("structure") or [] if str(x).strip()]
                if lines:
                    blocks.insert(1 if blocks else 0, {"type": "structure", "lines": lines})
                if ex:
                    blocks.append({"type": "examples", "items": ex})
                gen = a.get("_by") or f"{EXPLAIN_MODEL} {today}"
                rows.append({"source": "ai_generated", "source_ref": c, "title": str(a.get("title") or tip.raw_text),
                             "structure": "\n".join(lines) or None, "explanation": blocks_markdown(blocks),
                             "body": blocks, "level_hint": lvl, "url": None,
                             "attribution": f"Written by AI ({EXPLAIN_MODEL}) for this app", "license": None,
                             "examples": ex, "edited_by": None, "generated_by": gen})
                stats["tips written by AI"] += 1
        hsk_ex = [{"hanzi": h, "pinyin": "", "english": "", "marked": [], "readable": readable(h, lvl)}
                  for h in official]
        hsk_blocks = [{"type": "paragraph", "text": tip.raw_text}] + (
            [{"type": "examples", "items": hsk_ex}] if hsk_ex else [])
        rows.append({"source": "hsk_list", "source_ref": c, "title": tip.raw_text, "structure": None,
                     "explanation": blocks_markdown(hsk_blocks), "body": hsk_blocks, "level_hint": lvl, "url": None,
                     "attribution": HSK_LIST_ATTRIBUTION, "license": None, "examples": hsk_ex,
                     "edited_by": None, "generated_by": None})
        out[c] = rows

    # ---- pinyin of the examples: the wiki's as written; course sentences'; else AI (checked) ----
    items = [it for c, rows in out.items() if not c.startswith("_") for r in rows
             for it in gx.example_items(r["examples"], r["body"])]
    gx.mark_as_written(items)
    gx.fill_from_course(items, gx.course_pinyin_map(db))
    stats["examples with a course sentence's pinyin"] = len(
        {gx.cjk_key(it["hanzi"]) for it in items if it.get("pinyin_by") == gx.BY_COURSE})
    ai_pinyin(items, tags, client, ai_cache, stats)
    for c, rows in out.items():
        for r in rows if not c.startswith("_") else []:
            if r.get("body"):
                r["explanation"] = blocks_markdown(r["body"]) or None
    for k, v in sorted(stats.items()):
        print(f"  {k}: {v}")
    return out


# ---- the SHORT tips learners see (grammar_tip_parts.json) ----

TIP_ATTEMPTS = 3          # answers per point before it gets no tips (each retry says what broke)
TIP_MAX_WORDS = 50        # the prompt asks for 45; one or two words over isn't worth losing a tip

_TONE_MARKED = re.compile(r"[āáǎàēéěèīíǐìōóǒòūúǔùǖǘǚǜ]")
_CJK_RUN = re.compile(r"[㐀-鿿]+")


_ABBREVIATIONS = re.compile(r"\b(Subj|Obj|Adj|Adv|Num|Prep|Pron|etc|e\.g|i\.e|vs)\.", re.I)


def _sentences(text: str) -> int:
    """Sentences in the text; "Subj. + Verb + Obj." and "e.g." don't end one."""
    text = _ABBREVIATIONS.sub(lambda m: m.group(1).replace(".", ""), (text or "").strip())
    return len([x for x in re.split(r"(?<=[.!?])\s+", text) if x.strip()])


def _word_count(text: str) -> int:
    return len(re.findall(r"[A-Za-z0-9'’]+", text or "")) + len(_CJK_RUN.findall(text or ""))


def check_tip_parts(ans, point_words: list[str], level: int, tags: dict,
                    needed: list[str] | None = None, need_general: bool = False) -> tuple[list, list[str]]:
    """The rules of gr.tip_parts_prompt, checked: (tips, []) or (None, problems).
    `tags`: tagger results for every Chinese run in the answer (tip_chinese).
    `needed`: the point's words its course sentences really use (tip_sample)
    -- each must be in some tip's "words" (有点 / 有点儿 count as one)."""
    tips = ans.get("tips") if isinstance(ans, dict) else None
    if not isinstance(tips, list) or not 1 <= len(tips) <= 16:
        return None, ["give 1 to 16 tips in a \"tips\" list"]
    problems, out, general = [], [], 0
    banned = re.compile(r"\b(" + "|".join(gr.TIP_TERMS_BANNED) + r")", re.I)
    for n, t in enumerate(tips, 1):
        if not isinstance(t, dict):
            problems.append(f"tip {n} is not an object")
            continue
        name = str(t.get("title") or f"tip {n}")
        words = [str(w).strip() for w in t.get("words") or [] if str(w).strip()]
        if any(not _CJK_RUN.fullmatch(w) for w in words):
            problems.append(f"{name}: \"words\" must be Chinese words only")
        general += not words
        meaning = str(t.get("meaning") or "").strip()
        placement = str(t.get("placement") or "").strip() or None
        note = str(t.get("english_note") or "").strip() or None
        structure = [str(x).strip() for x in t.get("structure") or [] if str(x).strip()]
        text = " ".join(x for x in (meaning, placement, note) if x)
        if not str(t.get("title") or "").strip() or len(name) > 40:
            problems.append(f"{name}: give a short title (at most 40 characters)")
        if not meaning:
            problems.append(f"{name}: \"meaning\" is missing")
        elif words and not any(meaning.startswith(w) for w in words + [str(t.get("title") or "")] if w):
            problems.append(f"{name}: \"meaning\" must start with the word itself, e.g. \"{words[0]} means ...\"")
        if _sentences(meaning) > 2:
            problems.append(f"{name}: \"meaning\" must be one short sentence")
        if placement and _sentences(placement) > 1:
            problems.append(f"{name}: \"placement\" must be one sentence")
        if note and _sentences(note) > 1:
            problems.append(f"{name}: \"english_note\" must be at most one sentence")
        if _word_count(text) > TIP_MAX_WORDS:
            problems.append(f"{name}: {_word_count(text)} words -- meaning + placement + english_note must be "
                            "at most 45")
        if len(structure) > 2 or any(len(x) > 60 for x in structure):
            problems.append(f"{name}: at most 2 short structure lines")
        everything = " ".join([name, text] + structure)
        hit = banned.search(" ".join([name, text]))
        if hit:
            problems.append(f"{name}: no grammar terms like \"{hit.group(1)}\"")
        if _TONE_MARKED.search(everything):
            problems.append(f"{name}: no pinyin")
        own = set(words) | set(point_words)
        for run in _CJK_RUN.findall(everything):
            for hanzi, lvl, proper in (tags.get(run) or {}).get("vocab") or []:
                if proper or any(hanzi in w or w in hanzi for w in own):
                    continue
                if lvl is None or lvl > level:
                    problems.append(f"{name}: {hanzi} is not an HSK {level} word -- use easier Chinese or none")
        out.append({"words": words, "title": name, "structure": structure, "meaning": meaning,
                    "placement": placement, "english_note": note, "text": text})
    if general > 1:
        problems.append("at most one tip may have no words")
    covered = [w for t in out for w in t["words"]]
    if need_general and not general:
        problems.append("many course sentences use none of the point's words: add ONE general tip with "
                        "\"words\": [] that explains the point for them")
    if needed is not None:
        for w in needed:
            if not any(w == c or w + "儿" == c or c + "儿" == w for c in covered):
                problems.append(f"the course sentences use {w}: give it a tip, or add \"{w}\" to a tip's \"words\"")
    else:
        for w in point_words:
            if _CJK_RUN.fullmatch(w) and not general and not any(w in c or c in w for c in covered):
                problems.append(f"the point's word {w} has no tip -- add it to a tip's \"words\" or give it a tip")
    return (out, []) if not problems else (None, list(dict.fromkeys(problems)))


def tip_chinese(ans) -> list[str]:
    tips = ans.get("tips") if isinstance(ans, dict) else None
    runs = set()
    for t in tips if isinstance(tips, list) else []:
        if isinstance(t, dict):
            for k in ("title", "meaning", "placement", "english_note"):
                runs.update(_CJK_RUN.findall(str(t.get(k) or "")))
            for x in t.get("structure") or []:
                runs.update(_CJK_RUN.findall(str(x)))
    return sorted(runs)


GENERAL_TIP_SHARE = 0.2   # this share of a point's sentences using none of its words -> it needs a general tip


def tip_sample(linked, words: list[str], size: int = 20) -> tuple[list[str], list[tuple[str, str]], list[str]]:
    """(needed, sentences) for a point's tips, from ALL its course sentences:
    needed = the point's words they really use, on word boundaries (a word
    of 2+ characters inside a longer word counts as that word: 怎么 in
    怎么样 -> 怎么样; a 1-character one doesn't: 常 in 经常 isn't 常);
    sentences = the 2 shortest for each needed word, the 2 shortest using
    none of them, then the shortest of the rest, up to `size`;
    plain = sentences using none of the words, when they are at least
    GENERAL_TIP_SHARE of all (1-33 "any question": 你呢？) -- the point then
    needs a general tip. Fixed order, so the prompt is the same each run."""
    from data_pipelines.textbook.scripts.grammar_tips import word_spans
    uses, none = {}, set()
    for x in linked:
        try:
            gl = json.loads(x.glosses) if x.glosses else None
        except json.JSONDecodeError:
            gl = None
        spans = word_spans(x.hanzi, gl) if isinstance(gl, list) else None
        tokens = [t for t, _ in gl] if isinstance(gl, list) else []
        found = False
        for w in words:
            if spans is None:
                hits = [w] if w in x.hanzi else []
            elif w in spans:
                hits = [w]
            elif len(w) >= 2:
                hits = [t for t in tokens if w in t and t != w]
            else:
                hits = []
            for h in hits:
                uses.setdefault(h, set()).add((len(x.hanzi), x.hanzi, x.english))
                found = True
        if not found:
            none.add((len(x.hanzi), x.hanzi, x.english))
    plain = sorted(none) if linked and len(none) >= GENERAL_TIP_SHARE * len(linked) else []
    picked = []
    for w in sorted(uses):
        picked += sorted(uses[w])[:2]
    picked += plain[:2]
    for r in sorted({(len(x.hanzi), x.hanzi, x.english) for x in linked}):
        if len(dict.fromkeys(picked)) >= max(size, len(picked)):
            break
        picked.append(r)
    return sorted(uses), [(h, e) for _, h, e in dict.fromkeys(picked)], [h for _, h, _ in plain[:2]]


_VOCAB_SETS = {}


def point_words(code: str, label: str, rule_words) -> list[str]:
    """Every word that marks the point, from all the places the course knows
    them: the tested rule's words, hand-matched words (manual_points.json),
    the old detectors' markers (points found without a tested rule, like
    1-33 吗/呢) and the real words of its label (1-04 什么、谁、多少、怎么样 ...)."""
    from data_pipelines.textbook.scripts import detect_grammar as dg
    if not _VOCAB_SETS:
        from app.textbook.db_utils import get_session
        from app.textbook.models import Vocab
        with get_session() as db:
            vocab = {h for (h,) in db.query(Vocab.hanzi).filter(Vocab.hsk_level.isnot(None))}
        _VOCAB_SETS["vocab"], _VOCAB_SETS["chars"] = vocab, {ch for h in vocab for ch in h}
    manual = tg.load_json(tg.MANUAL_PATH, {}).get(code, {})
    found = (list(rule_words or []) + list((manual or {}).get("words") or [])
             + list(dg.GRAMMAR_MARKERS.get(code) or []) + list(tg.EXTRA_MARKERS.get(code) or [])
             + tg.real_label_words(label or "", _VOCAB_SETS["vocab"], _VOCAB_SETS["chars"]))
    return [w for w in dict.fromkeys(found) if _CJK_RUN.fullmatch(w)]


def real_course_sentences(db) -> dict:
    """{grammar code: [course sentences linked to it]} from the REAL
    database, read-only -- the trial's copy leaves out the Tatoeba and
    AI-written sentences, but they are in the course and learners see the
    tips on them. Falls back to `db` when there is no real database."""
    from types import SimpleNamespace
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session
    from app.textbook.models import GrammarTip, Sentence, SentenceGrammar
    real = getattr(tg, "REAL_DB", None)
    engine = create_engine(f"sqlite:///file:{real}?mode=ro&uri=true") if real else None
    src = Session(engine) if engine else db
    try:
        rows = (src.query(GrammarTip.grammar_code, Sentence.hanzi, Sentence.english, Sentence.glosses)
                .join(SentenceGrammar, SentenceGrammar.grammar_tip_id == GrammarTip.id)
                .join(Sentence, Sentence.id == SentenceGrammar.sentence_id)
                .filter(Sentence.unit_id.isnot(None), Sentence.english != "").all())
    finally:
        if engine:
            src.close()
            engine.dispose()
    out = {}
    for code, hanzi, english, glosses in rows:
        out.setdefault(code, []).append(SimpleNamespace(hanzi=hanzi, english=english, glosses=glosses))
    return out


def build_tip_parts(db, tagger, client, words_of: dict) -> dict:
    """{code: [tip, ...]} for every point learners meet (it has a unit):
    WRITTEN BY AI (EXPLAIN_MODEL) from the official entry, the AllSet
    Learning pages and the point's course sentences, following
    gr.tip_parts_prompt; checked by check_tip_parts, asked again once with
    the broken rules listed. A point whose tips fail twice gets none (listed)."""
    import hashlib
    from app.textbook.models import GrammarTip
    asg = {p["id"]: p for p in tg.load_json(tg.ASG_PATH, {}).get("points", [])}
    sources = tg.load_json(tg.SOURCES_PATH, {})
    manual = tg.load_json(tg.MANUAL_PATH, {})
    ai_cache = tg.load_json(EXPLANATION_AI_PATH, {})
    saver = CacheSaver(ai_cache)
    today = time.strftime("%Y-%m-%d")
    jobs, seen_by_tips = {}, {}
    course_of = real_course_sentences(db)
    for tip in db.query(GrammarTip).filter(GrammarTip.unit_id.isnot(None)).order_by(GrammarTip.id).all():
        c = tip.grammar_code
        content = tg.load_json_str(tip.content_json)
        official = list(dict.fromkeys(x for k, v in content.items() if "example" in k.lower()
                                      for x in tg._cjk_strings(v)))[:6]
        m = manual.get(c, {}) if isinstance(manual.get(c), dict) else {}
        ids = list(dict.fromkeys(list((sources.get(c) or {}).get("is", {})) + list(m.get("is", {}))))
        pages = [asg[i] for i in ids if i in asg and i not in m.get("not", {})][:3]
        words = point_words(c, tip.raw_text, words_of.get(c))
        linked = course_of.get(c, [])
        needed, course, plain = tip_sample(linked, words)
        seen_by_tips[c] = {"words": words, "needed": needed, "general_needed_for": plain,
                           "course_sentences": len(linked),
                           "with_word_split": sum(1 for x in linked if x.glosses),
                           "sample": [h for h, _ in course]}
        args = (c, tip.hsk_level, tip.raw_text, official, words,
                [blocks_markdown(p.get("blocks") or [])[:2500] for p in pages], course, needed, plain)
        prompt = gr.tip_parts_prompt(*args)
        key = json.dumps(["tip_parts", gr.TIP_PARTS_VERSION, EXPLAIN_MODEL, c, tip.hsk_level,
                          hashlib.md5(prompt.encode()).hexdigest()], ensure_ascii=False)
        jobs[c] = {"args": args, "key": key, "prompt": prompt, "level": tip.hsk_level, "words": words,
                   "needed": needed, "plain": bool(plain),
                   "sources": [f"AllSet Learning: {p['title']} ({ASG_URL.format(p['id'])})" for p in pages]
                              + ["Official HSK grammar list"]}

    def ask_all(todo: dict):
        def run(item):
            c, (key, prompt) = item
            try:
                ans = ask_json(client, prompt, 6000)
                if isinstance(ans, dict):
                    ans["_by"] = f"{EXPLAIN_MODEL} {today}"
                    saver.put(key, ans)
            except (ValueError, json.JSONDecodeError) as e:
                print(f"    [warning] {c}: the tips couldn't be read ({e}); asked again next export")
        if not todo:
            return
        if client is None:
            raise SystemExit("The short tips need AI calls but no client was given.")
        try:
            with ThreadPoolExecutor(max_workers=tg.WORKERS) as pool:
                list(pool.map(run, todo.items()))
        finally:
            tg.save_json(EXPLANATION_AI_PATH, ai_cache, merge=False)

    from concurrent.futures import ThreadPoolExecutor

    def checked(c, key):
        ans = ai_cache.get(key)
        if not isinstance(ans, dict):
            return None, None
        tags = tagger.tag_all(tip_chinese(ans), "tip Chinese") if tip_chinese(ans) else {}
        return check_tip_parts(ans, jobs[c]["words"], jobs[c]["level"], tags, jobs[c]["needed"], jobs[c]["plain"])

    # A point keeps a saved answer that passes the checks (any saved for this version, model and level),
    # so answers already paid for are never paid for again; bump gr.TIP_PARTS_VERSION to rewrite them.
    results, last = {}, {c: None for c in jobs}
    saved = {}
    for k in sorted(ai_cache):
        try:
            kk = json.loads(k)
        except (TypeError, json.JSONDecodeError):
            continue
        if isinstance(kk, list) and len(kk) >= 5 and kk[:3] == ["tip_parts", gr.TIP_PARTS_VERSION, EXPLAIN_MODEL]:
            saved.setdefault((kk[3], kk[4]), []).append(k)
    for c, j in jobs.items():
        for k in saved.get((c, j["level"]), []):
            parts, _ = checked(c, k)
            if parts:
                results[c] = (parts, ai_cache[k]["_by"])
                break
    reused = len(results)
    # up to TIP_ATTEMPTS answers per point: each retry lists the rules the last answer broke
    for attempt in range(TIP_ATTEMPTS):
        suffix = ["", ', "retry"]', ', "retry2"]'][attempt]
        todo = {}
        for c, j in jobs.items():
            if c in results:
                continue
            key = j["key"] if not suffix else j["key"][:-1] + suffix
            if key not in ai_cache:
                if attempt == 0:
                    todo[c] = (key, j["prompt"])
                elif last[c]:                     # the previous answer broke rules: ask again, saying which
                    todo[c] = (key, gr.tip_parts_prompt(*j["args"], problems=last[c]))
        if todo:
            print(f"  {EXPLAIN_MODEL}: " + (f"writing the short tips for {len(todo)} grammar point(s)..."
                                            if attempt == 0 else f"asking again for {len(todo)} grammar point(s) "
                                            f"whose tips broke a rule (attempt {attempt + 1})..."), flush=True)
        ask_all(todo)
        for c, j in jobs.items():
            if c in results:
                continue
            key = j["key"] if not suffix else j["key"][:-1] + suffix
            parts, problems = checked(c, key)
            if parts:
                results[c] = (parts, ai_cache[key]["_by"])
            elif problems:
                last[c] = problems
    out = {"_readme": "The SHORT grammar tips learners see: one per word or pattern of each point learners meet, "
                      "WRITTEN BY AI (generated_by) from the official list and AllSet Learning pages (sources), "
                      "following fixed rules checked in code (meaning first, where it goes, at most one sentence "
                      "on English, <= 45 words, no grammar terms, no pinyin, Chinese at the level). "
                      "Loaded by load_hsk_lists.py into grammar_tip_parts. Written by run_all_grammar.py --export."}
    failed = []
    for c, j in jobs.items():
        if c not in results:
            failed.append((c, last[c] or ["no readable answer yet (asked again next export)"]))
        parts, by = results.get(c, ([], None))
        out[c] = [dict(t, generated_by=by, sources=j["sources"]) for t in parts]
    tagger.save()
    n = sum(len(v) for k, v in out.items() if not k.startswith("_"))
    print(f"  short tips: {n} tip(s) for {len(results)} of {len(jobs)} grammar point(s) learners meet "
          f"({reused} from answers saved before)")
    report = tg.OUT_DIR / "short_tips_problems.txt"
    report.parent.mkdir(parents=True, exist_ok=True)
    for c in seen_by_tips:
        seen_by_tips[c]["tip_words"] = [t["words"] for t in out.get(c) or []]
    (tg.OUT_DIR / "short_tips_inputs.json").write_text(json.dumps(seen_by_tips, ensure_ascii=False, indent=1),
                                                       encoding="utf-8")
    with open(report, "w", encoding="utf-8") as f:
        for c, problems in failed:
            f.write(f"{c}\n" + "".join(f"  - {p}\n" for p in problems))
            print(f"    no tips for {c}: {'; '.join(problems)[:200]}")
    if failed:
        print(f"  why (full list): {report}")
    return out


def export_for_pipeline(db, state, teach, tagger) -> Path:
    """Everything the pipeline reads, into data_pipelines/textbook/data/grammar/."""
    import shutil
    from data_pipelines.textbook.scripts import grammar_dsl
    out_dir = grammar_dsl.GRAMMAR_DATA_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    # rules + which passed, levels
    shutil.copyfile(tg.RULES_PATH, grammar_dsl.RULES_FILE)
    status = {c: {"rule": st.get("rule"), "kind": st.get("kind"), "not_for": st.get("not_for") or [],
                  "words": (st.get("words") or "").split(),
                  "examples_caught": f"{st.get('matched')}/{st.get('examples')}" if "examples" in st else None}
              for c, st in sorted(state.items()) if isinstance(st, dict) and st.get("rule")}
    grammar_dsl.RULE_STATUS_FILE.write_text(json.dumps(
        {"_readme": "Which rules in grammar_rules.md passed their test (rule: ok) and may be used, and the "
                    "easier points each must not be used against (not_for). From run_all_grammar.py --export.",
         **status}, ensure_ascii=False, indent=1), encoding="utf-8")
    for src, dst in ((tg.LEVELS_PATH, grammar_dsl.LEVELS_2025_FILE),
                     (tg.EXAMPLE_LEVELS_PATH, grammar_dsl.LEVELS_EXAMPLES_FILE)):
        if src.exists():
            shutil.copyfile(src, dst)
    # sentences, with the word split they were checked with and their credit
    owner = {}
    with open(tg.DEFAULT_TSV, encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f, delimiter="\t", quoting=csv.QUOTE_NONE):
            owner.setdefault((row.get("id") or "").strip(), (row.get("owner") or "").strip())
    rows, no_split = [], []
    for c in teach:
        st = state.get(c, {})
        if st.get("tatoeba") != "done":
            continue
        items = [("tatoeba", s, f"tatoeba-{s['tatoeba_id']}") for s in st.get("survivors", [])]
        if st.get("ai") == "done":
            items += [(AI_SOURCE, s, s["tatoeba_id"]) for s in st.get("ai_survivors", [])]
        for src, s, ext in items:
            res = tagger.tag(s["hanzi"])
            split = surface_split(s["hanzi"], res.get("clauses") or []) if "error" not in res else None
            if split is None:
                no_split.append(s["hanzi"])
            tid = s["tatoeba_id"] if src == "tatoeba" else ""
            rows.append({"grammar_code": c, "hsk_level": st.get("level", ""), "source": src, "external_id": ext,
                         "hanzi": s["hanzi"], "english": s["english"],
                         "generated_by": s.get("generated_by", "") if src == AI_SOURCE else "",
                         "source_author": owner.get(tid, "") if tid else "",
                         "source_url": f"https://tatoeba.org/en/sentences/show/{tid}" if tid else "",
                         "word_split": json.dumps(split, ensure_ascii=False) if split else ""})
    tagger.save()
    with open(grammar_dsl.SENTENCES_FILE, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=["grammar_code", "hsk_level", "source", "external_id", "hanzi", "english",
                                          "generated_by", "source_author", "source_url", "word_split"])
        w.writeheader()
        w.writerows(rows)
    expl = build_explanations(db, tagger, tg.get_client())
    tagger.save()
    grammar_dsl.EXPLANATIONS_FILE.write_text(json.dumps(expl, ensure_ascii=False, indent=1), encoding="utf-8")
    words_of = {c: (st.get("words") or "").split() for c, st in state.items() if isinstance(st, dict)}
    tips = build_tip_parts(db, tagger, tg.get_client(), words_of)
    grammar_dsl.TIP_PARTS_FILE.write_text(json.dumps(tips, ensure_ascii=False, indent=1), encoding="utf-8")
    n_src = Counter(r["source"] for r in rows)
    print(f"\nExported to {out_dir}:")
    print(f"  grammar_rules.md + rule_status.json: {sum(1 for v in status.values() if v['rule'] == 'ok')} "
          f"usable rule(s) of {len(status)}")
    print(f"  selected_sentences.csv: {len(rows)} row(s) -- " + ", ".join(f"{k}: {v}" for k, v in n_src.items())
          + f"; {len({r['hanzi'] for r in rows})} distinct sentence(s)")
    if no_split:
        print(f"  [note] {len(no_split)} sentence(s) have no word split (tagging falls back to HanLP), "
              f"e.g. {no_split[:3]}")
    has = lambda src: sum(1 for c, v in expl.items() if not c.startswith("_") and any(r["source"] == src for r in v))
    print(f"  grammar_explanations.json: {len(expl) - 1} point(s): {has('allset_learning')} with an AllSet Learning "
          f"page, {has('ai_generated')} with a tip written by AI, the rest only the official list")
    print(f"  {grammar_dsl.TIP_PARTS_FILE.name}: {sum(len(v) for k, v in tips.items() if not k.startswith('_'))} "
          f"short tip(s) for {sum(1 for k, v in tips.items() if not k.startswith('_') and v)} point(s)")
    print(f"  grammar levels: {grammar_dsl.LEVELS_2025_FILE.name}, {grammar_dsl.LEVELS_EXAMPLES_FILE.name}")
    return out_dir


# --------------------------------- REPORT ---------------------------------

def write_reports(state, teach, guard, skipped):
    BATCH_DIR.mkdir(parents=True, exist_ok=True)
    with open(BATCH_DIR / "summary.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["code", "kind", "rule", "examples_caught", "easy_examples_caught",
                    "counter_examples_wrongly_matched", "revised",
                    "tatoeba", "level", "course_sentences", "added", "ai_added", "total", "note", "words"])
        for kind, codes in (("teach", teach), ("guard", guard)):
            for c in codes:
                st = state.get(c, {})
                caught = f"{st['matched']}/{st['examples']}" if "examples" in st else ""
                wrong = f"{st['wrongly_matched']}/{st['counter_examples']}" if "examples" in st else ""
                ai_n = st.get("ai_added", 0) if st.get("ai") == "done" else 0
                total = (st.get("course_sentences", 0) + st.get("added", 0) + ai_n) if st.get("tatoeba") == "done" else ""
                easy = f"{st['easy_matched']}/{st['easy_examples']}" if "easy_examples" in st else ""
                w.writerow([c, kind, st.get("rule", "not run"), caught, easy, wrong,
                            "yes" if st.get("revised") else "",
                            st.get("tatoeba", "" if kind == "guard" else "not run"), st.get("level", ""),
                            st.get("course_sentences", ""), st.get("added", ""), ai_n or "", total, st.get("note", ""),
                            st.get("words", "")])
        for c, label, why in skipped:
            w.writerow([c, "skipped", "", "", "", "", "", "", "", "", "", "", "", why, label])
    with open(BATCH_DIR / "selected_sentences.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["grammar_code", "hsk_level", "source", "tatoeba_id", "hanzi", "english", "generated_by"])
        for c in teach:
            st = state.get(c, {})
            for s in st.get("survivors", []):
                w.writerow([c, st.get("level", ""), "tatoeba", s["tatoeba_id"], s["hanzi"], s["english"], ""])
            for s in st.get("ai_survivors", []) if st.get("ai") == "done" else []:
                w.writerow([c, st.get("level", ""), AI_SOURCE, "", s["hanzi"], s["english"], s["generated_by"]])
    if any(state.get(c, {}).get("ai_log") for c in teach):
        with open(BATCH_DIR / "ai_sentences.csv", "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f)
            w.writerow(["grammar_code", "hsk_level", "source", "hanzi", "english", "generated_by", "result"])
            for c in teach:
                st = state.get(c, {})
                for x in st.get("ai_log", []):
                    w.writerow([c, st.get("level", ""), AI_SOURCE, x["hanzi"], x["english"], x["generated_by"],
                                x["result"]])


def print_summary(state, teach, guard, skipped, levels, target):
    rules_ok = [c for c in teach + guard if state.get(c, {}).get("rule") == "ok"]
    failed = [c for c in teach + guard if state.get(c, {}).get("rule") in ("failed", "error")]
    done = [c for c in teach if state.get(c, {}).get("tatoeba") == "done"]
    ai_of = lambda c: state[c].get("ai_added", 0) if state[c].get("ai") == "done" else 0
    short = [c for c in done if state[c]["course_sentences"] + state[c]["added"] + ai_of(c) < target]
    print(f"\n=== Summary: HSK {' '.join(map(str, levels))} ===")
    print(f"  points with a rule to teach: {len(teach)}   guard points (harder, easy words): {len(guard)}   "
          f"skipped: {len(skipped)}")
    print(f"  rules OK: {len(rules_ok)}   rules failed/error: {len(failed)}")
    print(f"  filled from Tatoeba: {len(done)}   sentences added: "
          f"{sum(state[c]['added'] for c in done)}   AI-written sentences kept (labelled {AI_SOURCE}): "
          f"{sum(ai_of(c) for c in done)}")
    print(f"  points still under the target of {target} sentences: {len(short)}")
    if failed:
        print("\n--- rules that failed (not used; worth a look) ---")
        for c in failed:
            print(f"  {c} ({state[c].get('kind')}): {state[c].get('rule')} -- {state[c].get('note', '')}")
    if short:
        print(f"\n--- under {target} sentences (Tatoeba had too few good ones) ---")
        for c in short:
            st = state[c]
            print(f"  {c} HSK {st['level']}: {st['course_sentences']} in the course + {st['added']} from Tatoeba"
                  + (f" + {ai_of(c)} AI-written" if ai_of(c) else ""))
    print(f"\nFull table: {BATCH_DIR / 'summary.csv'}")
    print(f"Chosen sentences: {BATCH_DIR / 'selected_sentences.csv'}")


# --------------------------------- MAIN ---------------------------------

def main():
    parser = argparse.ArgumentParser(description="Run the grammar trial over every grammar point.")
    parser.add_argument("--levels", nargs="+", type=int, default=[1, 2])
    parser.add_argument("--phase", choices=["all", "rules", "guards", "tatoeba", "ai"], default="all")
    parser.add_argument("--ai", action="store_true",
                        help=f"After Tatoeba, fill points still under the target with AI-written sentences "
                             f"(checked the same way, labelled {AI_SOURCE}). Also: --phase ai.")
    parser.add_argument("--show-unmatched", action="store_true",
                        help="List the points that need words/pages matched by hand; spend nothing.")
    parser.add_argument("--only", nargs="+", default=None, help="Only these codes.")
    parser.add_argument("--limit", type=int, default=None, help="Only the first N points (to try it out).")
    parser.add_argument("--target", type=int, default=tg.DEFAULT_TARGET)
    parser.add_argument("--redo-tatoeba", action="store_true", help="Run the Tatoeba step again for done points.")
    parser.add_argument("--retry-failed", action="store_true", help="Try failed/error rules again (one more revise).")
    parser.add_argument("--dry-run", action="store_true", help="List the points and the work left; spend nothing.")
    parser.add_argument("--export", action="store_true",
                        help="Write what the pipeline reads (rules, levels, chosen sentences with their word split "
                             "and credit, grammar explanations) to data_pipelines/textbook/data/grammar/. AI only "
                             "for grammar explanations not made before (saved in explanation_ai.json).")
    parser.add_argument("--rewrite", nargs="+", default=[], metavar="CODE",
                        help="Throw away these points' rules and write them again from scratch (e.g. rules "
                             "written before patterns could link two clauses with ‖).")
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
        # levels from the points' own examples change the DB levels; planning again afterwards puts
        # a point moved back down (2-06) into the teach list and takes a point moved up out of it
        moved = levels_from_examples(db, teach, tg.Tagger(db))
        if moved:
            print("Levels from the points' own examples (their words are harder than their listed level):")
            for c, v in moved.items():
                print(f"  {c}: HSK {v['from']} -> {v['level']}  ({v['why']})")
        teach, guard, skipped = plan(db, args.levels, args.max_guard_level)
        for c, v in moved.items():
            if v["level"] > max(args.levels) and v["from"] in args.levels:
                teach = [x for x in teach if x != c]
                guard = [x for x in guard if x != c]
                skipped.append((c, (state.get(c) or {}).get("words", ""),
                                f"moved to HSK {v['level']}, above the levels built: {v['why']}"))
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
        if not tg.MANUAL_PATH.exists():
            print(f"Hand-matched points: none ({tg.MANUAL_PATH} not found)")
        else:
            try:
                manual = json.loads(tg.MANUAL_PATH.read_text(encoding="utf-8"))
            except json.JSONDecodeError as e:
                sys.exit(f"{tg.MANUAL_PATH.name} isn't valid JSON ({e}). Fix it or send it to Claude.")
            n_skip = sum(1 for k, v in manual.items() if not k.startswith("_") and v.get("skip"))
            print(f"Hand-matched points ({tg.MANUAL_PATH.name}): {len(manual) - 1 - n_skip} matched, "
                  f"{n_skip} skipped")
        print(f"Points: {len(teach)} to teach, {len(guard)} guard (harder points made of easy words), "
              f"{len(skipped)} skipped")
        print(f"Work left: {len(need_sources)} sources call(s), {len(need_rule)} rule(s) to write "
              f"(+ up to one revise each), {len(need_tatoeba)} Tatoeba fill(s)")
        retest = [c for c in teach + guard if state[c].get("rule") == "ok"
                  and state[c].get("test_version") != TEST_VERSION]
        if retest:
            print(f"  + {len(retest)} passing rule(s) tested again against their easy examples (free; "
                  "one Sonnet revise each for any that fail)")
        low = len(need_sources) * 0.02 + len(need_rule) * 0.03 + len(need_tatoeba) * 0.03
        high = (len(need_sources) * 0.04 + len(need_rule) * 0.08 + len(need_tatoeba) * 0.10
                + len(retest) * 0.02)
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

        if args.export:
            export_for_pipeline(db, state, teach, tg.Tagger(db))
            db.rollback()
            print("Next, in the real pipeline: python main.py --levels 1 2   (migrates the DB, loads all of it)")
            return
        tagger, client = tg.Tagger(db), tg.get_client()
        try:
            if args.phase in ("all", "rules"):
                for c in args.rewrite:                    # written again from scratch
                    if c in state:
                        gr.remove_section(tg.RULES_PATH, c)
                        for k in ("rule", "revised", "test_version", "retest", "guard_revised3", "not_for"):
                            state[c].pop(k, None)
                        print(f"{c}: old rule removed, writing it again")
                _, _, unreadable = gr.load_rules(tg.RULES_PATH)
                for c in unreadable:                      # passed before, but can't be read now:
                    if c in state and state[c].get("rule") == "ok":   # write it again
                        state[c].update(rule="failed", note="rule can't be read any more")
                        state[c].pop("revised", None)
                for c in teach + guard:                   # passed an older test: tested again
                    if state[c].get("rule") == "ok" and state[c].get("test_version") != TEST_VERSION:
                        state[c]["retest"] = True
                        state[c].pop("revised", None)       # gets one revise if it fails the new test
                todo = [c for c in teach + guard
                        if not ((state[c].get("rule") == "ok" and not state[c].get("retest"))
                                or (state[c].get("rule") in ("failed", "error") and not args.retry_failed
                                    and c not in unreadable))]
                clock = [time.time()]
                for i, c in enumerate(todo, 1):
                    st = state[c]
                    if args.retry_failed and st.get("rule") in ("failed", "error"):
                        st.pop("revised", None)
                    with open(BATCH_DIR / f"{c}.log", "a", encoding="utf-8") as log, contextlib.redirect_stdout(log):
                        print(f"\n########## rules  {time.strftime('%Y-%m-%d %H:%M')} ##########")
                        try:
                            st.pop("retest", None)
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
                    if st.get("ai") == "done":
                        st["ai"] = "stale"               # its AI sentences are checked again below
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
            ai_on = args.phase == "ai" or (args.ai and args.phase == "all")
            # with --ai: every point still short (Sonnet writes more if needed). Without it: only points
            # refilled from Tatoeba this run whose AI sentences must be checked again (nothing is written).
            todo = [c for c in teach if state[c].get("tatoeba") == "done"
                    and ((ai_on and state[c]["course_sentences"] + state[c]["added"] < args.target)
                         or state[c].get("ai") == "stale")]
            if todo:
                print(f"\nAI-written sentences for {len(todo)} point(s) still under {args.target}...", flush=True)
                clock = [time.time()]
                for i, c in enumerate(todo, 1):
                    st = state[c]
                    with open(BATCH_DIR / f"{c}.log", "a", encoding="utf-8") as log, contextlib.redirect_stdout(log):
                        print(f"\n########## ai  {time.strftime('%Y-%m-%d %H:%M')} ##########")
                        try:
                            ai_fill_for(db, c, st, args.target, client, write=ai_on)
                        except (SystemExit, Exception) as e:
                            st.update(ai="error", ai_note=(str(e) or repr(e)).splitlines()[0][:200])
                            traceback.print_exc(file=log)
                        db.rollback()
                    save_state(state)
                    total = st["course_sentences"] + st["added"] + (st.get("ai_added", 0) if st.get("ai") == "done" else 0)
                    print(f"[ai {i}/{len(todo)}] {c} HSK {st['level']}: {st.get('ai_added', 0)} AI-written kept "
                          f"(of {st.get('ai_written', 0)} written) -> {total} in all"
                          + (f" -- {st.get('ai_note')}" if st.get("ai") == "error" else "")
                          + pace(clock, i, len(todo)), flush=True)
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
