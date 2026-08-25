"""
data_pipelines/external_sources/hsk_sentences_audio/import_sentences.py

Pipeline stage 4: imports supplementary sentences from the `hsk_sentences_audio`
package into textbook.db.

REWRITE: no more greedy_segment against known vocab -- HanLP segments every
external sentence directly (tag_sentences.segment_sentence, the SAME
function stage 3 uses), and sense resolution goes through the SAME
resolve_word_sense() stage 3 uses (cache -> new-word Haiku call -> ambiguous
compare Haiku call -> rehome-earlier-if-warranted). There is now exactly one
place in the whole pipeline that decides "what does this word mean here,"
and this script and tag_sentences.py both call it.

PLACEMENT ALGORITHM
--------------------
Must run AFTER vocab_index_parser / sentence_parser / tag_sentences have
fully completed for this HSK level (see main.py's per-level ordering) --
placement depends on which words are already known.

For each card (already labeled with its own hsk_level by the source):
  1. Segment with HanLP.
  2. If EVERY word already has at least one VocabSense (nothing
     unregistered): place the sentence at the HIGHEST (unit_number) among
     those words' EARLIEST known homes (get_word_to_unit_map) -- i.e. the
     sentence goes wherever its hardest already-known word was introduced.
     Then resolve_word_sense() for every tag AT THAT unit -- this is what
     "make sure the word definition matches the word's sense for that
     unit" means: resolve_word_sense() itself checks cache / compares
     against existing senses via AI when ambiguous, so a word already known
     with a DIFFERENT meaning than what's taught by that unit gets a new
     sense created rather than silently mislabeled.
  3. If ANY word has NO senses at all (genuinely new, no textbook anchor):
     skip the "highest known word" placement entirely -- the sentence AND
     every unregistered word go to the HIGHEST unit_number that exists for
     this card's own hsk_level (get_highest_unit_number). This is a
     deliberate simplification: there's no reliable signal for WHEN in the
     curriculum an externally-sourced new word belongs, so it goes at the
     end of the level rather than guessing.
  4. Whichever placement was chosen, resolve_word_sense() is called for
     EVERY tag (known or new) at that (unit_number, hsk_level) -- this
     creates senses for new words, matches/creates senses for known words,
     and rehomes any sense that turns out to belong earlier.
  5. Word-level questions for any NEWLY created sense are generated the
     same simplified way as before (still a stand-in for
     create_questions.py's full builder -- rerun that afterward).

WHAT'S GONE FROM THE OLD VERSION: greedy_segment/resolve_new_word_pinyin/
CEDICT-first pinyin-vs-token-source cross-checking, and the old two-tier
"registered vs unregistered" split that only ever registered NEW words at
the sentence's own placement unit. All of that is now resolve_word_sense's
job (shared with tag_sentences.py), and CEDICT is used only inside
get_reading_for_word for the deterministic cache-key reading, not for
picking a definition.

USAGE
-----
    python import_sentences.py --hsk-level 1          # required: scope to one level
    python import_sentences.py --hsk-level 1 --topic food
    python import_sentences.py --hsk-level 1 --dry-run
"""
import argparse
from collections import defaultdict

from hsk_sentences_audio import iter_sentences

from app.textbook.db_utils import (
    get_session, init_db, get_word_to_unit_map, get_highest_unit_number,
    get_or_create_vocab, get_senses_for_vocab, upsert_sentence_bare,
    set_sentence_tags, upsert_question,
)
from app.textbook.models import Unit

from data_pipelines.textbook.scripts.tag_sentences import (
    _get_hanlp, resolve_word_sense,
)

import json

from data_pipelines.textbook.scripts.tag_sentences import (
    _get_hanlp, resolve_word_sense, get_reading_for_word,
)
from data_pipelines.textbook.scripts.vocab_pinyin_utils import diacritic_to_numeric
from app.textbook.db_utils import upsert_vocab_sense, write_sense_cache
from app.textbook.models import WordType
from app.core.config.external_sources import VOCAB_LIST_JSON

SOURCE_LABEL = "hsk_sentences_audio"

# Must match create_questions.py's QuestionType enum values exactly, so a
# later create_questions.py rerun's upsert_question() dedup key
# (unit, question_type, question, answer) lines up with what we write here
# instead of creating duplicates.
VOCAB_QUESTION_TYPES = [
    "listening vocab",
    "speaking vocab",
    "translate english word to chinese",
    "translate chinese word to english",
    "transcribe word to pinyin",
    "transcribe hanzi to pinyin",
]


def load_hsk_vocab_list() -> dict[int, dict[str, dict]]:
    """Loads the official per-hsk-level vocab JSON (id/category/pinyin/
    english per hanzi word), keyed by hsk_level (int) -> {hanzi: {...}}.
    This is the SAME source vocab_index_parser.py reads from -- the
    authoritative list of what's actually taught at each level."""
    with open(VOCAB_LIST_JSON, encoding="utf-8") as f:
        raw = json.load(f)
    return {int(level): words for level, words in raw.items()}

def get_vocab_list_entry(word: str, hsk_level: int, vocab_list: dict) -> dict | None:
    """Returns the vocab list entry for `word` at `hsk_level`, or None if
    the word isn't part of that level's official vocab list."""
    return vocab_list.get(hsk_level, {}).get(word)

def resolve_placement(db, tags: list[tuple[str, str]], card_hsk_level: int,
                       word_to_unit: dict, vocab_list: dict):
    print(f"\n  resolve_placement: {card_hsk_level}, {len(tags)} raw tags")
    
    taggable = []
    unregistered_words = set()
    dropped_words = []
    
    for word, pos_tag in tags:
        has_senses = bool(get_senses_for_vocab(db, word))
        in_vocab_list = bool(get_vocab_list_entry(word, card_hsk_level, vocab_list))
        
        if has_senses:
            taggable.append((word, pos_tag))
            print(f"    ✓ {word}: already has senses")
        elif in_vocab_list:
            taggable.append((word, pos_tag))
            unregistered_words.add(word)
            print(f"    + {word}: in vocab list, will register")
        else:
            dropped_words.append(word)
            print(f"    ✗ {word}: NOT in vocab list, dropping")

    print(f"  → taggable: {len(taggable)}, unregistered: {len(unregistered_words)}, dropped: {len(dropped_words)}")
    
    if not taggable:
        return None, None, False, []

    if unregistered_words:
        target_unit = get_highest_unit_number(db, card_hsk_level)
        print(f"  → placement: end-of-level unit {target_unit} (has new words)")
        return target_unit, card_hsk_level, False, taggable

    known = [(w, word_to_unit[w]) for w, _pos in taggable if w in word_to_unit]
    if not known:
        target_unit = get_highest_unit_number(db, card_hsk_level)
        print(f"  → placement: end-of-level unit {target_unit} (no words in word_to_unit)")
        return target_unit, card_hsk_level, False, taggable

    _, (unit_number, hsk_level) = max(known, key=lambda item: (item[1][1], item[1][0]))
    print(f"  → placement: unit {unit_number}, HSK{hsk_level} (known words)")
    return unit_number, hsk_level, True, taggable

def create_sense_from_vocab_list(db, word: str, pos_tag: str, entry: dict,
                                  unit_number: int, hsk_level: int):
    """Creates a VocabSense straight from the official HSK vocab list entry
    -- no AI call, unlike tag_sentences.py's brand-new-word path, since the
    vocab list's pinyin/english IS the authoritative definition, not a
    guess from a single sentence's context."""
    pinyin = diacritic_to_numeric(entry["pinyin"])
    sense = upsert_vocab_sense(
        db, word, pinyin, entry["english"], unit_number,
        word_type=WordType.vocab, hsk_level=hsk_level, make_primary=True,
    )
    sense.pos_tag = pos_tag
    db.flush()
    write_sense_cache(db, word, pos_tag, pinyin, sense)
    return sense

def generate_vocab_questions(db, vocab_id: int, sense, hanzi: str, unit_number: int, hsk_level: int):
    """Simplified stand-in for create_questions.py's full word-question
    builder, for a sense that was JUST newly created by this run. Not
    sense-ambiguity-aware (that's create_questions.py's job on the next
    full pipeline run) -- just enough coverage that a brand new word isn't
    completely un-quizzable until then."""
    candidates = {
        "listening vocab": (hanzi, sense.pinyin),
        "speaking vocab": (hanzi, sense.pinyin),
        "translate english word to chinese": (sense.english, hanzi),
        "translate chinese word to english": (hanzi, sense.english),
        "transcribe word to pinyin": (hanzi, sense.pinyin),
        "transcribe hanzi to pinyin": (hanzi, sense.pinyin),
    }
    for qtype in VOCAB_QUESTION_TYPES:
        q_text, a_text = candidates[qtype]
        upsert_question(db, unit_number, qtype, q_text, a_text,
                         vocab_id=vocab_id, vocab_sense_id=sense.id, hsk_level=hsk_level)

def get_pos_for_tokens(hanzi: str, tokens: list[dict]) -> list[tuple[str, str]]:
    tok, pos = _get_hanlp()
    hanlp_words = tok(hanzi)
    hanlp_tags = pos(hanlp_words)
    result = []
    hanlp_idx = 0
    for token in tokens:
        token_word = token.get("word", "").strip()
        if not token_word:
            continue
        if hanlp_idx < len(hanlp_words) and hanlp_words[hanlp_idx] == token_word:
            result.append((token_word, hanlp_tags[hanlp_idx]))
            hanlp_idx += 1
        else:
            result.append((token_word, "UNK"))
    return result

def process_card(db, card: dict, word_to_unit: dict, vocab_list: dict, dry_run: bool) -> dict:
    hanzi = card.get("chinese", "")
    if not hanzi:
        return {"status": "skipped", "reason": "no chinese text"}

    card_hsk_level = card.get("hsk_level") or card.get("level")
    if card_hsk_level is None:
        return {"status": "skipped", "reason": "no hsk_level on card", "hanzi": hanzi}

    tokens = card.get("tokens")
    if not tokens:
        return {"status": "skipped", "reason": "no tokens on card", "hanzi": hanzi}
    
    print(f"\n📝 Card: {hanzi} (HSK{card_hsk_level})")
    print(f"   tokens: {[t.get('word') for t in tokens]}")
    
    raw_tags = get_pos_for_tokens(hanzi, tokens)
    print(f"   raw_tags after HanLP: {raw_tags}")
    
    if not raw_tags:
        return {"status": "skipped", "reason": "tokens produced no usable words", "hanzi": hanzi}

    target_unit, target_hsk_level, all_known, tags = resolve_placement(
        db, raw_tags, card_hsk_level, word_to_unit, vocab_list
    )
    
    print(f"   final tags to write: {tags}")
    
    if target_unit is None:
        return {"status": "skipped",
                "reason": f"no words in tokens are known or in HSK{card_hsk_level} vocab list",
                "hanzi": hanzi}

    if dry_run:
        return {
            "status": "would_write", "hanzi": hanzi,
            "target_unit": target_unit, "hsk_level": target_hsk_level,
            "tags": [w for w, _ in tags], "all_known": all_known,
        }

    resolved = []
    new_sense_count = 0
    for word, pos_tag in tags:
        vocab = get_or_create_vocab(db, word)
        had_senses_before = bool(get_senses_for_vocab(db, word))
        
        if had_senses_before:
            print(f"     {word}: resolving existing sense")
            sense = resolve_word_sense(db, word, pos_tag, hanzi, target_unit, target_hsk_level)
        else:
            vocab_entry = get_vocab_list_entry(word, card_hsk_level, vocab_list)
            print(f"     {word}: creating sense from vocab list: {vocab_entry.get('english')}")
            sense = create_sense_from_vocab_list(db, word, pos_tag, vocab_entry, target_unit, target_hsk_level)
            new_sense_count += 1
            generate_vocab_questions(db, vocab.id, sense, word, target_unit, target_hsk_level)
        
        resolved.append((vocab.id, sense.id if sense else None))
        if sense and sense.unit:
            key = word
            candidate = (sense.unit.unit_number, sense.unit.hsk_level)
            if key not in word_to_unit or candidate < word_to_unit[key]:
                word_to_unit[key] = candidate

    english_full = (card.get("translation") or {}).get("en", "") or card.get("english", "")
    pinyin_full = card.get("pinyin_numbered") or card.get("pinyin", "")

    sentence = upsert_sentence_bare(
        db, unit_number=target_unit, hanzi=hanzi, english=english_full,
        pinyin=pinyin_full, source=SOURCE_LABEL, hsk_level=target_hsk_level,
    )
    set_sentence_tags(db, sentence, resolved)
    
    print(f"   ✓ Written: unit {target_unit}, {new_sense_count} new sense(s)")

    return {
        "status": "written", "hanzi": hanzi, "target_unit": target_unit,
        "hsk_level": target_hsk_level, "new_senses": new_sense_count, "all_known": all_known,
    }

def main():
    parser = argparse.ArgumentParser(description="Import hsk_sentences_audio sentences into textbook.db")
    parser.add_argument("--hsk-level", type=int, required=True,
                         help="Only import cards at this HSK level. Must be run AFTER the textbook "
                              "pipeline (vocab_index_parser -> sentence_parser -> tag_sentences) has "
                              "fully completed for this level -- placement depends on it.")
    parser.add_argument("--topic", type=str, default=None,
                         help="Only import cards matching this topic (e.g. 'food').")
    parser.add_argument("--dry-run", action="store_true",
                         help="Print what would be written without touching the DB.")
    args = parser.parse_args()

    init_db()
    print(f"📚 Loading vocab list from {VOCAB_LIST_JSON}...")
    vocab_list = load_hsk_vocab_list()
    for level, words in vocab_list.items():
        print(f"   HSK{level}: {len(words)} words")

    with get_session() as db:
        units_exist = db.query(Unit).filter(Unit.hsk_level == args.hsk_level).count() > 0
        if not units_exist:
            print(f"❌ No units found for HSK level {args.hsk_level} -- run the textbook pipeline "
                  f"(vocab_index_parser, sentence_parser, tag_sentences) for this level first.")
            return

        word_to_unit = get_word_to_unit_map(db)

        results = defaultdict(list)
        total_considered = 0

        kwargs = {"level": args.hsk_level}
        if args.topic:
            kwargs["topic"] = args.topic
        for card in iter_sentences(**kwargs):
            total_considered += 1
            result = process_card(db, card, word_to_unit, vocab_list, args.dry_run)
            results[result["status"]].append(result)
            
        written = results.get("written", [])
        would_write = results.get("would_write", [])
        skipped = results.get("skipped", [])

        print(f"\n{'='*60}")
        print(f"Considered {total_considered} card(s) at HSK level {args.hsk_level}.")
        
        new_sense_total = sum(r["new_senses"] for r in written)
        print(f"Written: {len(written)} sentence(s), {new_sense_total} new vocab sense(s)")
        
        # Break down by all_known vs has new words
        all_known_count = sum(1 for r in written if r["all_known"])
        has_new_words_count = sum(1 for r in written if not r["all_known"])
        print(f"  - {all_known_count} sentences with all words already known")
        print(f"  - {has_new_words_count} sentences that added new words")

        if skipped:
            print(f"Skipped: {len(skipped)}")
            skip_reasons = defaultdict(int)
            for r in skipped:
                skip_reasons[r["reason"]] += 1
            for reason, count in sorted(skip_reasons.items(), key=lambda x: -x[1]):
                print(f"  - {count}x: {reason}")
        
        print(f"{'='*60}\n")

        print(f"\nConsidered {total_considered} card(s) at HSK level {args.hsk_level}.")
        if args.dry_run:
            print(f"Would write: {len(would_write)}")
            for r in would_write[:20]:
                known_note = "" if r["all_known"] else " (has NEW word -> end-of-level placement)"
                print(f"  [HSK{r['hsk_level']} unit {r['target_unit']}] {r['hanzi']}  "
                      f"tags={r['tags']}{known_note}")
            if len(would_write) > 20:
                print(f"  ... and {len(would_write) - 20} more")
        else:
            new_sense_total = sum(r["new_senses"] for r in written)
            print(f"Written: {len(written)} sentence(s), {new_sense_total} new vocab sense(s) registered")

        if skipped:
            print(f"Skipped: {len(skipped)}")
            for r in skipped[:20]:
                print(f"  - {r.get('hanzi', '?')}: {r['reason']}")
            if len(skipped) > 20:
                print(f"  ... and {len(skipped) - 20} more")

    if not args.dry_run and written:
        print(
            "\n⚠️  Reminder: sentence-level questions and grammar-tip links "
            "still need the full pipeline stage. Run:"
        )
        print(f"    python data_pipelines/textbook/scripts/main.py --from-grammar --hsk-level {args.hsk_level}")


if __name__ == "__main__":
    main()