"""
data_pipelines/textbook/scripts/assign_units.py

Pipeline stage 5: builds one HSK level's units from the dataset topics and
gives every sentence, word, sense and grammar point of that level a unit.
No AI. Run one level at a time, lowest first.

1. TOPICS
   Real topics in dataset order (the order they first appear in the
   dataset, by sentence id). The catch-all topics (questions, objects_misc,
   misc) are not units of their own: their sentences are spread into the
   real topics (step 2).

2. CATCH-ALL SENTENCES -- placed in a draft built from the real topics:
   a. only units at or after the first unit where each of the sentence's
      grammar points appears (a point no draft unit has doesn't restrict);
   b. among those, the unit sharing the most of the sentence's words
      (words found in CORE_TOPICS+ topics don't count -- they match everything);
   c. tie: the unit with the fewest new words, then the earliest.

3. ORDER inside a topic: by the latest official grammar point a sentence
   uses (grammar list order, points of this level or below), so sentences
   with only earlier grammar come first. Ties: dataset id.

4. FIRST APPEARANCE: walking units in order and sentences in order, a word
   (and each sense) belongs to the unit where it first appears. Words and
   senses already taught at a lower level don't count as new. Every
   sentence stays in its own topic's unit, so its words are always taught
   by then.

5. BALANCE (repeat until nothing changes):
   - over MAX_WORDS new words, or over MAX_GRAMMAR new grammar points with
     room to split -> split the unit, preferring a cut where a new grammar
     point starts. A cut is only made if both parts keep MIN_WORDS words
     and MIN_SENTENCES sentences, so splitting never creates a unit that
     then has to be merged back.
   - under MIN_WORDS new words or MIN_SENTENCES sentences -> merge with the
     neighbouring unit that has fewer words.
   Anything still outside the limits is flagged in the report, not forced.
   Many grammar points (pronouns, 是, sentence types) appear in the very
   first sentences, so the first unit always introduces many: the grammar
   limit is a target, not a hard rule.

6. WRITE: clears this level's old units (content rows are kept, only
   un-assigned), creates the new ones, and sets unit_id on sentences, words,
   senses (first appearance) and grammar points of this level or below
   (the unit where each first appears). Every word and sense therefore
   appears in at least one sentence of its own unit -- checked after
   writing. List words of this level that no sentence uses get no unit
   until a sentence for them exists (e.g. from Tatoeba); they are listed.

Re-running a level rebuilds it from scratch. Re-run the levels above it
afterwards: their idea of which words are "new" depends on this one.

USAGE (from the repo root)
    python -m data_pipelines.textbook.scripts.assign_units --hsk-level 1 --dry-run   # report only
    python -m data_pipelines.textbook.scripts.assign_units --hsk-level 1
"""
import argparse
import time
from collections import Counter, defaultdict

from app.textbook.db_utils import get_session, init_db, clear_units, create_unit
from app.textbook.models import (
    GrammarTip, Sentence, SentenceGrammar, SentenceVocab, Unit, Vocab, VocabSense,
)

MIN_WORDS, MAX_WORDS = 20, 40
MIN_SENTENCES = 10   # 15 was too strict: early units add ~2.7 new words per sentence
MAX_GRAMMAR = 3

# Per-level limits that replace the defaults above. HSK 1 learners are still
# learning pinyin, so its units are short: a topic with more words than fit
# is split into "Part 1", "Part 2", ... Command-line flags override these.
LEVEL_LIMITS = {
    1: {"min_words": 8, "max_words": 20, "min_sentences": 5, "max_grammar": 3},
    2: {"min_words": 10, "max_words": 20, "min_sentences": 6, "max_grammar": 3},
}
MAX_TITLE_TOPICS = 2     # a unit merged from more topics: "Food, Nature + 3 more"
CORE_TOPICS = 5
MAX_ROUNDS = 50

CATCH_ALL_TOPICS = {"questions", "objects_misc", "misc"}
TOPIC_TITLES = {
    "daily_actions": "Daily Actions", "school_work": "School & Work",
    "weather_state": "Weather", "health_body": "Health & Body",
    "sports_leisure": "Sports & Leisure",
}


def topic_title(slug: str) -> str:
    return TOPIC_TITLES.get(slug, slug.replace("_", " ").title())


# --------------------------------- DATA ---------------------------------

class Level:
    """Everything about one HSK level that the algorithm needs, read once."""

    def __init__(self, db, level: int):
        self.level = level
        rows = (db.query(Sentence.id, Sentence.external_id, Sentence.topic)
                .filter(Sentence.hsk_level == level).all())
        self.sentences = {sid: {"ext": ext or "", "topic": topic or "misc"} for sid, ext, topic in rows}

        self.words = defaultdict(list)      # sentence -> [vocab_id, ...] in order
        self.senses = defaultdict(list)     # sentence -> [sense_id, ...]
        for sid, vid, sense_id in (db.query(SentenceVocab.sentence_id, SentenceVocab.vocab_id,
                                            SentenceVocab.vocab_sense_id)
                                   .filter(SentenceVocab.sentence_id.in_(self.sentences))
                                   .order_by(SentenceVocab.sentence_id, SentenceVocab.position).all()):
            self.words[sid].append(vid)
            if sense_id:
                self.senses[sid].append(sense_id)

        # Official grammar order = the order of the grammar list (load order).
        tips = db.query(GrammarTip.id, GrammarTip.grammar_code, GrammarTip.hsk_level).order_by(GrammarTip.id).all()
        self.grammar_rank = {gid: i + 1 for i, (gid, _, _) in enumerate(tips)}
        self.grammar_level = {gid: lvl for gid, _, lvl in tips}
        self.grammar_code = {gid: code for gid, code, _ in tips}
        self.grammar = defaultdict(set)     # sentence -> {grammar_tip_id} of this level or below
        for sid, gid in (db.query(SentenceGrammar.sentence_id, SentenceGrammar.grammar_tip_id)
                         .filter(SentenceGrammar.sentence_id.in_(self.sentences)).all()):
            if self.grammar_level.get(gid, 99) <= level:
                self.grammar[sid].add(gid)

        # Words / senses / grammar already homed in a LOWER level are not new here.
        lower = {uid for (uid,) in db.query(Unit.id).filter(Unit.hsk_level < level).all()}
        self.taught_words = {vid for (vid,) in db.query(Vocab.id).filter(Vocab.unit_id.in_(lower)).all()}
        self.taught_senses = {s for (s,) in db.query(VocabSense.id).filter(VocabSense.unit_id.in_(lower)).all()}
        self.taught_grammar = {g for (g,) in db.query(GrammarTip.id).filter(GrammarTip.unit_id.in_(lower)).all()}

        self.vocab_level = {vid: lvl for vid, lvl in db.query(Vocab.id, Vocab.hsk_level).all()}

    def rank(self, sid) -> int:
        return max((self.grammar_rank[g] for g in self.grammar[sid]), default=0)

    def topic_order(self) -> list[str]:
        """Dataset topic order for this level: taken from the dataset's own
        section for the level (ids "hsk2-...") first, so sentences that moved
        here from another level's section don't decide the order."""
        own = f"hsk{self.level}-"
        seen = []
        for sid in sorted(self.sentences, key=lambda s: (not self.sentences[s]["ext"].startswith(own),
                                                         self.sentences[s]["ext"])):
            t = self.sentences[sid]["topic"]
            if t not in seen:
                seen.append(t)
        return seen


# --------------------------------- FIRST APPEARANCE ---------------------------------

def walk(lv: Level, segments: list[list[int]]):
    """Per segment (= a unit's sentences in order): new word ids, new sense
    ids, new grammar ids, and the index of the sentence where each new
    grammar point starts."""
    words, senses, grammar = set(lv.taught_words), set(lv.taught_senses), set(lv.taught_grammar)
    out = []
    for seg in segments:
        nw, ns, ng, starts = set(), set(), set(), set()
        for k, sid in enumerate(seg):
            for v in lv.words[sid]:
                if v not in words:
                    words.add(v); nw.add(v)
            for s in lv.senses[sid]:
                if s not in senses:
                    senses.add(s); ns.add(s)
            for g in lv.grammar[sid]:
                if g not in grammar:
                    grammar.add(g); ng.add(g); starts.add(k)
        out.append({"words": nw, "senses": ns, "grammar": ng, "grammar_starts": starts})
    return out


def prefix_new_words(lv: Level, before: set, seg: list[int]) -> list[int]:
    """Number of new words in seg[:k] for every k (given words taught before)."""
    seen, counts, n = set(before), [0], 0
    for sid in seg:
        for v in lv.words[sid]:
            if v not in seen:
                seen.add(v); n += 1
        counts.append(n)
    return counts


# --------------------------------- PLACEMENT + BALANCING ---------------------------------

class Segment:
    def __init__(self, topics: list[str], sentences: list[int]):
        self.topics = topics
        self.sentences = sentences
        self.unsplittable = False


def place_catch_alls(lv: Level, real_topics: list[str], by_topic: dict, catch: list[int], log: list):
    draft = [sorted(by_topic[t], key=lambda s: (lv.rank(s), lv.sentences[s]["ext"])) for t in real_topics]
    stats = walk(lv, draft)
    first_unit = {}
    for i, st in enumerate(stats):
        for g in st["grammar"]:
            first_unit.setdefault(g, i)
    unit_words = [set(v for sid in seg for v in lv.words[sid]) for seg in draft]
    topics_of_word = Counter(v for seg in draft for v in {w for sid in seg for w in lv.words[sid]})
    core = {v for v, n in topics_of_word.items() if n >= CORE_TOPICS}

    reasons = Counter()
    for sid in sorted(catch, key=lambda s: lv.sentences[s]["ext"]):
        floor = max((first_unit[g] for g in lv.grammar[sid] if g in first_unit), default=0)
        mine = set(lv.words[sid]) - core
        best = max(range(floor, len(draft)),
                   key=lambda u: (len(mine & unit_words[u]), -len(stats[u]["words"]), -u))
        shared = len(mine & unit_words[best])
        reasons["by shared words" if shared else "no shared words: smallest unit"] += 1
        if floor:
            reasons["grammar ruled out earlier units"] += 1
        by_topic[real_topics[best]].append(sid)
        log.append((lv.sentences[sid]["topic"], sid, real_topics[best], shared))
    return reasons


def try_split(lv: Level, before: set, seg: Segment, st: dict) -> tuple[Segment, Segment] | None:
    sents = seg.sentences
    counts = prefix_new_words(lv, before, sents)
    total = counts[-1]
    parts = max(2, -(-total // MAX_WORDS))
    target = total / parts
    best = None
    for k in range(MIN_SENTENCES, len(sents) - MIN_SENTENCES + 1):
        left = counts[k]
        right = total - left
        if not (MIN_WORDS <= left <= MAX_WORDS and right >= MIN_WORDS):
            continue
        score = (k not in st["grammar_starts"], abs(left - target))
        if best is None or score < best[0]:
            best = (score, k)
    if best is None:
        return None
    k = best[1]
    return Segment(list(seg.topics), sents[:k]), Segment(list(seg.topics), sents[k:])


def balance(lv: Level, segments: list[Segment], log: list) -> list[Segment]:
    for _ in range(MAX_ROUNDS):
        stats = walk(lv, [s.sentences for s in segments])
        changed = False
        before = set(lv.taught_words)
        for i, (seg, st) in enumerate(zip(segments, stats)):
            n_w = len(st["words"])
            too_big = n_w > MAX_WORDS or (len(st["grammar"]) > MAX_GRAMMAR and n_w >= 2 * MIN_WORDS)
            if too_big and not seg.unsplittable:
                parts = try_split(lv, before, seg, st)
                if parts:
                    segments[i:i + 1] = list(parts)
                    log.append(f"split {'+'.join(seg.topics)} ({n_w} words, {len(st['grammar'])} new grammar) "
                               f"into {len(parts[0].sentences)} + {len(parts[1].sentences)} sentences")
                    changed = True
                    break
                seg.unsplittable = True
            before |= st["words"]
        if changed:
            continue
        for i, (seg, st) in enumerate(zip(segments, stats)):
            if len(segments) > 1 and (len(st["words"]) < MIN_WORDS or len(seg.sentences) < MIN_SENTENCES):
                nbrs = [j for j in (i - 1, i + 1) if 0 <= j < len(segments)]
                j = min(nbrs, key=lambda j: len(stats[j]["words"]))
                a, b = sorted((i, j))
                merged = Segment(segments[a].topics + [t for t in segments[b].topics if t not in segments[a].topics],
                                 segments[a].sentences + segments[b].sentences)
                log.append(f"merged {'+'.join(seg.topics)} ({len(st['words'])} words, {len(seg.sentences)} "
                           f"sentences) into {'+'.join(segments[j].topics)}")
                segments[a:b + 1] = [merged]
                changed = True
                break
        if not changed:
            break
    return segments


def titles_for(segments: list[Segment]) -> list[tuple[str, str, int | None]]:
    """(title, topic, part) per segment; split topics get "Part 1", "Part 2", ..."""
    def name(topics):
        shown = ", ".join(topic_title(t) for t in topics[:MAX_TITLE_TOPICS])
        rest = len(topics) - MAX_TITLE_TOPICS
        return f"{shown} + {rest} more" if rest > 0 else shown
    names = [name(s.topics) for s in segments]
    totals = Counter(names)
    seen = Counter()
    out = []
    for name, seg in zip(names, segments):
        seen[name] += 1
        part = seen[name] if totals[name] > 1 else None
        out.append((f"{name}, Part {part}" if part else name, seg.topics[0], part))
    return out


# --------------------------------- MAIN ---------------------------------

def build(lv: Level):
    order = lv.topic_order()
    real_topics = [t for t in order if t not in CATCH_ALL_TOPICS]
    if not real_topics:
        raise SystemExit(f"HSK {lv.level} has no non-catch-all topics.")
    by_topic = defaultdict(list)
    catch = []
    for sid, s in lv.sentences.items():
        (catch if s["topic"] in CATCH_ALL_TOPICS else by_topic[s["topic"]]).append(sid)

    placement_log = []
    reasons = place_catch_alls(lv, real_topics, by_topic, catch, placement_log)
    segments = [Segment([t], sorted(by_topic[t], key=lambda s: (lv.rank(s), lv.sentences[s]["ext"])))
                for t in real_topics if by_topic[t]]
    balance_log = []
    segments = balance(lv, segments, balance_log)
    return segments, reasons, placement_log, balance_log


def report(lv: Level, segments, reasons, placement_log, balance_log, stats, titles):
    print(f"\nHSK {lv.level}: {len(lv.sentences)} sentences -> {len(segments)} units")
    print(f"  catch-all sentences placed: {len(placement_log)} "
          f"({', '.join(f'{k}: {v}' for k, v in sorted(reasons.items()))})")
    for line in balance_log:
        print(f"  {line}")
    print(f"\n  {'#':>3}  {'Unit':<34}{'Sentences':>10}{'New words':>11}{'New grammar':>13}  Flags")
    flagged = 0
    for i, (seg, st, (title, _, _)) in enumerate(zip(segments, stats, titles), 1):
        flags = []
        if len(st["words"]) > MAX_WORDS: flags.append(f"over {MAX_WORDS} words")
        if len(st["words"]) < MIN_WORDS: flags.append(f"under {MIN_WORDS} words")
        if len(seg.sentences) < MIN_SENTENCES: flags.append(f"under {MIN_SENTENCES} sentences")
        if len(st["grammar"]) > MAX_GRAMMAR: flags.append(f"over {MAX_GRAMMAR} new grammar")
        flagged += bool(flags)
        print(f"  {i:>3}  {title:<34}{len(seg.sentences):>10}{len(st['words']):>11}{len(st['grammar']):>13}  "
              f"{'; '.join(flags)}")
    print(f"\n  {flagged} unit(s) flagged. Words: {sum(len(s['words']) for s in stats)} new, "
          f"grammar points: {sum(len(s['grammar']) for s in stats)} new.")


def write(db, lv: Level, segments, stats, titles) -> dict:
    cleared = clear_units(db, lv.level)
    unit_ids = []
    for i, (title, topic, part) in enumerate(titles, 1):
        unit_ids.append(create_unit(db, i, lv.level, title, topic=topic, part=part).id)

    sentence_unit, word_unit, sense_unit, grammar_unit = {}, {}, {}, {}
    for uid, seg, st in zip(unit_ids, segments, stats):
        for sid in seg.sentences:
            sentence_unit[sid] = uid
        for v in st["words"]:
            word_unit[v] = uid
        for s in st["senses"]:
            sense_unit[s] = uid
        for g in st["grammar"]:
            grammar_unit[g] = uid

    def bulk(model, mapping):
        for row_id, uid in mapping.items():
            db.query(model).filter(model.id == row_id).update({model.unit_id: uid}, synchronize_session=False)

    bulk(Sentence, sentence_unit)
    bulk(Vocab, word_unit)
    bulk(VocabSense, sense_unit)
    bulk(GrammarTip, grammar_unit)

    # List words of this level that no sentence uses get NO unit: a word is
    # only taught in a unit that has a sentence using it. They get one once a
    # sentence source (e.g. Tatoeba) adds a sentence for them.
    hanzi_of = dict(db.query(Vocab.id, Vocab.hanzi).all())
    unused = sorted(hanzi_of[v] for (v,) in db.query(Vocab.id)
                    .filter(Vocab.hsk_level == lv.level, Vocab.unit_id.is_(None)).all() if v not in word_unit)
    db.flush()
    db.expire_all()
    return {"cleared": cleared, "unused": unused, "check": check_words_in_their_unit(db, lv.level),
            "sentences": len(sentence_unit), "senses": len(sense_unit), "grammar": len(grammar_unit)}


def check_words_in_their_unit(db, level: int) -> dict:
    """Every word and sense homed in this level's units must appear in at
    least one sentence of that same unit. Returns the counts that fail."""
    from sqlalchemy import and_, exists
    units = [uid for (uid,) in db.query(Unit.id).filter(Unit.hsk_level == level).all()]
    words = db.query(Vocab.id).filter(Vocab.unit_id.in_(units), ~exists().where(and_(
        SentenceVocab.vocab_id == Vocab.id, SentenceVocab.sentence_id == Sentence.id,
        Sentence.unit_id == Vocab.unit_id))).count()
    senses = db.query(VocabSense.id).filter(VocabSense.unit_id.in_(units), ~exists().where(and_(
        SentenceVocab.vocab_sense_id == VocabSense.id, SentenceVocab.sentence_id == Sentence.id,
        Sentence.unit_id == VocabSense.unit_id))).count()
    return {"words": words, "senses": senses}


def main():
    global MIN_WORDS, MAX_WORDS, MIN_SENTENCES, MAX_GRAMMAR
    parser = argparse.ArgumentParser(description="Build one HSK level's units and assign everything to them.")
    parser.add_argument("--hsk-level", type=int, required=True)
    parser.add_argument("--dry-run", action="store_true", help="Print the units without writing anything.")
    parser.add_argument("--min-words", type=int, default=None, help="Default: LEVEL_LIMITS, else 20.")
    parser.add_argument("--max-words", type=int, default=None, help="Default: LEVEL_LIMITS, else 40.")
    parser.add_argument("--min-sentences", type=int, default=None, help="Default: LEVEL_LIMITS, else 10.")
    parser.add_argument("--max-grammar", type=int, default=None, help="Default: LEVEL_LIMITS, else 3.")
    args = parser.parse_args()
    limits = {"min_words": MIN_WORDS, "max_words": MAX_WORDS, "min_sentences": MIN_SENTENCES,
              "max_grammar": MAX_GRAMMAR, **LEVEL_LIMITS.get(args.hsk_level, {})}
    for key in limits:
        if getattr(args, key) is not None:
            limits[key] = getattr(args, key)
    MIN_WORDS, MAX_WORDS = limits["min_words"], limits["max_words"]
    MIN_SENTENCES, MAX_GRAMMAR = limits["min_sentences"], limits["max_grammar"]
    print(f"HSK {args.hsk_level} unit limits: {MIN_WORDS}-{MAX_WORDS} new words, "
          f"at least {MIN_SENTENCES} sentences, up to {MAX_GRAMMAR} new grammar points")

    t0 = time.time()
    init_db()
    with get_session() as db:
        L = args.hsk_level
        if L > 1 and not db.query(Unit).filter(Unit.hsk_level == L - 1).count():
            raise SystemExit(f"HSK {L - 1} has no units yet -- build the levels in order, lowest first.")
        if db.query(SentenceVocab).count() == 0:
            raise SystemExit("No tagged sentences -- run tag_sentences.py first.")
        if not args.dry_run and db.query(Unit).filter(Unit.hsk_level > L).count():
            print(f"  [note] levels above {L} already have units; re-run them after this level.")

        unresolved = (db.query(SentenceVocab).join(Sentence, Sentence.id == SentenceVocab.sentence_id)
                      .join(Vocab, Vocab.id == SentenceVocab.vocab_id)
                      .filter(Sentence.hsk_level == L, SentenceVocab.vocab_sense_id.is_(None),
                              Vocab.hsk_level.isnot(None)).count())
        if unresolved:
            raise SystemExit(f"{unresolved} word(s) in HSK {L} sentences have no meaning picked yet -- "
                             f"run resolve_senses.py --levels {L} first.")

        lv = Level(db, L)
        if not lv.sentences:
            raise SystemExit(f"No sentences at HSK {L}.")
        untagged = [s for s in lv.sentences if not lv.words[s]]
        if untagged:
            print(f"  [warning] {len(untagged)} HSK {L} sentence(s) have no tags and add no words.")
        no_grammar = sum(1 for s in lv.sentences if not lv.grammar[s])
        if no_grammar == len(lv.sentences):
            print("  [warning] no grammar links found -- run detect_grammar.py first; ordering uses grammar.")

        segments, reasons, placement_log, balance_log = build(lv)
        stats = walk(lv, [s.sentences for s in segments])
        titles = titles_for(segments)
        report(lv, segments, reasons, placement_log, balance_log, stats, titles)

        if args.dry_run:
            db.rollback()
            print(f"\nDry run: nothing written ({time.time() - t0:.0f}s).")
            return
        result = write(db, lv, segments, stats, titles)
    print(f"\nWrote {len(segments)} unit(s) for HSK {L} (replaced {result['cleared']}): "
          f"{result['sentences']} sentences, {sum(len(s['words']) for s in stats)} words, "
          f"{result['senses']} senses, {result['grammar']} grammar points.")
    check = result["check"]
    print(f"  Check: every word and sense appears in a sentence of its own unit -- "
          f"{'OK' if not (check['words'] or check['senses']) else f'FAILED for {check}'}")
    print(f"  {len(result['unused'])} HSK {L} list word(s) appear in no sentence and have no unit yet: "
          f"{' '.join(result['unused'][:40])}{' ...' if len(result['unused']) > 40 else ''}")
    print(f"Done in {time.time() - t0:.0f}s. Next: finalize (primary senses, sandhi pinyin).")


if __name__ == "__main__":
    main()