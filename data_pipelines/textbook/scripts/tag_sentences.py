"""
data_pipelines/textbook/scripts/tag_sentences.py

Pipeline stage 3: tags every bare Sentence row (written by sentence_parser.py,
which no longer does any segmentation/tagging itself) with its SentenceVocab
links, creating/updating VocabSense rows along the way.

REPLACES: sentence_parser.py's old greedy_segment/tag_and_pinyin logic AND
append_orphan_tags.py entirely. append_orphan_tags.py is deleted -- its
core mistake was inventing vocab words from the printed INDEX (which lists
compound words like 东西, so a naive gap-filler would register the
substring 西 as if it were independently taught) with no sentence evidence
at all. This script only ever creates a VocabSense because a real sentence
actually contains that word -- there is no other path to vocab creation
left in the pipeline.

SEGMENTATION: HanLP's output is trusted directly, no greedy-match-against-
known-vocab step. HanLP gives (word, pos_tag) pairs; pinyin for each word
comes from CEDICT (compound-aware, e.g. 学生 -> xue2sheng5) with pypinyin
as a fallback for words CEDICT doesn't know.

SENSE RESOLUTION (per word, per occurrence):
  1. SenseCache lookup on (hanzi, pos_tag, pinyin) -- if hit, use that sense.
     Zero AI calls.
  2. Cache miss, word has NO existing senses at all -> brand new word ->
     ONE Haiku call to write a definition FROM SENTENCE CONTEXT -> create
     the sense (primary, since it's the word's first) -> write cache.
  3. Cache miss, word HAS existing senses, but none share this exact
     (pos_tag, pinyin) -> check get_senses_matching_pos_pinyin as a
     backfill path (handles senses created by vocab_index_parser.py, which
     doesn't populate pos_tag) -- if found, reuse + write cache, no AI call.
  4. Still nothing -> genuinely ambiguous -> ONE Haiku call comparing
     against the word's nearest existing sense(s): SAME (reuse, cache it)
     or DIFFERENT (write a new sense with Haiku's definition, cache it).
  5. REHOME: whichever sense got resolved, if this sentence's
     (hsk_level, unit_number) is EARLIER than the sense's current home,
     move the sense's home earlier (db_utils.rehome_sense already no-ops
     if it isn't actually earlier) -- "the word showed up sooner than we
     thought" should always win over whatever unit a sense was originally
     created at.

Every step above is a full sense-resolution outcome BEFORE any sentence
gets its SentenceVocab rows written -- once tag_sentences.py finishes a
unit, "every word used in a sentence is documented" (create_questions.py's
precondition) is actually true, not aspirational.

USAGE
-----
    python tag_sentences.py                    # tag every untagged sentence, HSK_LEVEL from env
    HSK_LEVEL=2 python tag_sentences.py         # explicit level
    python tag_sentences.py --unit 5            # only sentences in unit 5
    python tag_sentences.py --retag             # re-tag sentences that already have tags
                                                 # (use after a segmentation/model change)
"""
import os
import re
import argparse
import json

import anthropic
from dotenv import load_dotenv

from app.core.config.shared import ENV_FILE
from app.textbook.db_utils import (
    get_session, init_db, get_or_create_vocab, get_senses_for_vocab,
    get_cached_sense, write_sense_cache, get_senses_matching_pos_pinyin,
    get_nearest_sense, upsert_vocab_sense, rehome_sense,
    upsert_sentence_bare, set_sentence_tags, get_all_vocab_hanzi,
)
from app.textbook.models import Sentence, SentenceVocab, WordType

from data_pipelines.textbook.scripts.vocab_pinyin_utils import pypinyin_numeric, diacritic_to_numeric
from data_pipelines.textbook.scripts.cedict_utils import lookup_word

HSK_LEVEL = int(os.environ.get("HSK_LEVEL", "1"))

load_dotenv(ENV_FILE)
api_key = os.environ.get("CLAUDE_API_KEY")
client = anthropic.Anthropic(api_key=api_key) if api_key else None

HAIKU_MODEL = "claude-haiku-4-5"
MAX_TOKENS_DEFINITION = 300
MAX_TOKENS_COMPARE = 200

_CONTENT_RE = re.compile(r"[\u4e00-\u9fff]|\d+")


# --------------------------------- SEGMENTATION ---------------------------------

_hanlp_pipeline = None


def _get_hanlp():
    """Lazy-load HanLP's tokenizer + POS tagger once per process -- these
    are large models, no reason to reload per sentence."""
    global _hanlp_pipeline
    if _hanlp_pipeline is None:
        import hanlp
        # Chain models safely into a unified pipeline
        _hanlp_pipeline = hanlp.pipeline() \
            .append(hanlp.load(hanlp.pretrained.tok.COARSE_ELECTRA_SMALL_ZH), output_key='tok') \
            .append(hanlp.load(hanlp.pretrained.pos.CTB9_POS_ELECTRA_SMALL), input_key='tok', output_key='pos')
    return _hanlp_pipeline


def segment_sentence(hanzi: str) -> list[tuple[str, str]]:
    """HanLP's word segmentation + POS tagging, trusted directly -- no
    greedy-match-against-known-vocab step. Returns [(word, pos_tag), ...]
    in sentence order. Punctuation/whitespace tokens are dropped (POS tag
    'PU' in CTB9's tagset); content_only() isn't applied to the INPUT since
    HanLP's tokenizer wants real sentence punctuation for accurate
    boundaries, but the OUTPUT is filtered to drop punctuation tokens."""
    pipeline = _get_hanlp()
    
    # The pipeline outputs a dict-like Document natively mapping your keys
    doc = pipeline(hanzi)
    words = doc['tok']
    tags = doc['pos']
    
    # Flatten just in case HanLP auto-segments a long string into a list-of-lists
    if words and isinstance(words[0], list):
        words = [w for sent in words for w in sent]
        tags = [t for sent in tags for t in sent]
        
    return [(w, t) for w, t in zip(words, tags) if t != "PU" and w.strip()]

def get_reading_for_word(word: str) -> str:
    """CEDICT first (compound-aware, correct tone sandhi for real words) --
    takes the FIRST candidate CEDICT returns (its own most-common-sense
    ordering) purely for a PRONUNCIATION guess to seed segmentation/lookup;
    this is not a sense/definition choice, so picking candidate 0 here
    doesn't risk mis-selecting a meaning -- resolve_word_sense's cache/AI
    logic is what actually decides which VocabSense this occurrence gets,
    using this reading only as part of the (hanzi, pos_tag, pinyin) key.
    pypinyin fallback for anything CEDICT doesn't know (proper nouns, very
    recent/informal words, etc)."""
    candidates = lookup_word(word)
    if candidates:
        return diacritic_to_numeric(candidates[0]["pinyin"])
    py = pypinyin_numeric(word)
    return py or "UNKNOWN_PINYIN"


# --------------------------------- AI-ASSISTED SENSE RESOLUTION ---------------------------------

def _extract_json(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
    return json.loads(text)


def haiku_write_definition(hanzi: str, pos_tag: str, pinyin: str, sentence: str) -> dict:
    """ONE call, only for a word with NO existing senses at all. Writes a
    definition grounded in the actual sentence it was found in -- this is
    the ONLY place in the entire pipeline a brand-new VocabSense gets
    invented, and it always has real sentence evidence behind it (unlike
    the old append_orphan_tags.py, which invented senses from bare index
    membership with no sentence at all)."""
    prompt = (
        f"A Mandarin textbook pipeline found the word \"{hanzi}\" (POS tag: {pos_tag}, "
        f"pinyin: {pinyin}) in this sentence:\n\n{sentence}\n\n"
        f"Write a concise English definition for \"{hanzi}\" AS USED IN THIS SENTENCE. "
        f"Respond with ONLY a JSON object, no other text: "
        f'{{"english": "<definition>"}}'
    )
    if client is None:
        return {"english": "UNKNOWN_ENGLISH"}
    response = client.messages.create(
        model=HAIKU_MODEL, max_tokens=MAX_TOKENS_DEFINITION, temperature=0,
        messages=[{"role": "user", "content": prompt}],
    )
    text = "".join(b.text for b in response.content if hasattr(b, "text"))
    try:
        data = _extract_json(text)
        return {"english": (data.get("english") or "UNKNOWN_ENGLISH").strip()}
    except Exception:
        return {"english": "UNKNOWN_ENGLISH"}


def haiku_compare_senses(hanzi: str, candidate_senses: list, sentence: str) -> dict:
    """ONE call, only when a word has existing senses but none share this
    exact (pos_tag, pinyin) combo already cached. Asks Claude to judge
    whether the word AS USED IN THIS SENTENCE matches one of the
    candidates' meanings, or is genuinely a new meaning. Returns either
    {"match": <candidate index>} or {"match": null, "english": "<new def>"}.
    """
    candidates_text = "\n".join(
        f"{i}. {s.english} (pinyin: {s.pinyin}, POS: {s.word_type.value if hasattr(s.word_type,'value') else s.word_type})"
        for i, s in enumerate(candidate_senses)
    )
    prompt = (
        f"The Mandarin word \"{hanzi}\" was found in this sentence:\n\n{sentence}\n\n"
        f"Existing known meanings of \"{hanzi}\":\n{candidates_text}\n\n"
        f"Does this sentence use \"{hanzi}\" with ONE of the meanings above, or a "
        f"DIFFERENT meaning not yet recorded? Respond with ONLY a JSON object, no "
        f"other text. If it matches an existing meaning: "
        f'{{"match": <index number>}}. If it is a new/different meaning: '
        f'{{"match": null, "english": "<concise definition as used here>"}}'
    )
    if client is None:
        return {"match": 0 if candidate_senses else None, "english": "UNKNOWN_ENGLISH"}
    response = client.messages.create(
        model=HAIKU_MODEL, max_tokens=MAX_TOKENS_COMPARE, temperature=0,
        messages=[{"role": "user", "content": prompt}],
    )
    text = "".join(b.text for b in response.content if hasattr(b, "text"))
    try:
        return _extract_json(text)
    except Exception:
        # default to "same as nearest" on parse failure -- safer than
        # silently minting a duplicate sense from a malformed AI response
        return {"match": 0}


def resolve_word_sense(db, hanzi: str, pos_tag: str, sentence: str,
                        unit_number: int, hsk_level: int) -> "VocabSense":
    """The full per-word resolution pipeline described in the module
    docstring. Returns the VocabSense this occurrence should link to,
    rehomed earlier if this sentence predates the sense's current home."""
    pinyin = get_reading_for_word(hanzi)

    # 1. cache hit -- zero AI calls
    sense = get_cached_sense(db, hanzi, pos_tag, pinyin)

    if sense is None:
        existing_senses = get_senses_for_vocab(db, hanzi)

        if not existing_senses:
            # 2. brand new word -- one Haiku call, grounded in this sentence
            definition = haiku_write_definition(hanzi, pos_tag, pinyin, sentence)
            sense = upsert_vocab_sense(
                db, hanzi, pinyin, definition["english"], unit_number,
                word_type=WordType.vocab, hsk_level=hsk_level, make_primary=True,
            )
            sense.pos_tag = pos_tag
            db.flush()
        else:
            # 3. backfill path -- an existing sense already has this exact
            # (pos_tag, pinyin), it just predates pos_tag being tracked /
            # predates this exact combo being cached
            pos_pinyin_matches = get_senses_matching_pos_pinyin(db, hanzi, pos_tag, pinyin)
            if pos_pinyin_matches:
                sense = pos_pinyin_matches[0]
            else:
                # 4. genuinely ambiguous -- one Haiku call against the
                # nearest existing sense(s)
                nearest = get_nearest_sense(db, hanzi, unit_number, hsk_level)
                candidates = [nearest] if nearest else existing_senses[:1]
                result = haiku_compare_senses(hanzi, candidates, sentence)
                match_idx = result.get("match")
                if match_idx is not None and 0 <= match_idx < len(candidates):
                    sense = candidates[match_idx]
                else:
                    english = result.get("english") or "UNKNOWN_ENGLISH"
                    sense = upsert_vocab_sense(
                        db, hanzi, pinyin, english, unit_number,
                        word_type=WordType.vocab, hsk_level=hsk_level, make_primary=False,
                    )
                    sense.pos_tag = pos_tag
                    db.flush()

        write_sense_cache(db, hanzi, pos_tag, pinyin, sense)

    # 5. rehome earlier if this sentence predates the sense's current home
    # (no-ops if it isn't actually earlier -- see rehome_sense docstring)
    rehome_sense(db, sense, unit_number, hsk_level)

    return sense


# --------------------------------- MAIN ---------------------------------

def get_sentences_to_tag(db, hsk_level: int, unit_number: int | None, retag: bool) -> list[Sentence]:
    from app.textbook.models import Unit
    q = db.query(Sentence).join(Unit, Sentence.unit_id == Unit.id).filter(Unit.hsk_level == hsk_level)
    if unit_number is not None:
        q = q.filter(Unit.unit_number == unit_number)
    sentences = q.all()
    if retag:
        return sentences
    # only untagged sentences: zero SentenceVocab rows
    return [s for s in sentences if not s.vocab_links]


def tag_sentence(db, sentence: Sentence, hsk_level: int) -> int:
    """Tags one sentence, returns the number of tag occurrences resolved.
    Also fills in Sentence.pinyin (left blank by sentence_parser.py) by
    joining each resolved tag's own reading -- this is the first point in
    the pipeline where a real, sense-resolved reading is known for every
    word in the sentence, so it's the right place to compute it rather
    than guessing per-character before tagging existed."""
    unit_number = sentence.unit.unit_number
    words_and_pos = segment_sentence(sentence.hanzi)

    resolved_tags = []
    readings = []
    for word, pos_tag in words_and_pos:
        vocab = get_or_create_vocab(db, word)
        sense = resolve_word_sense(db, word, pos_tag, sentence.hanzi, unit_number, hsk_level)
        resolved_tags.append((vocab.id, sense.id if sense else None))
        readings.append(sense.pinyin if sense and sense.pinyin else get_reading_for_word(word))

    set_sentence_tags(db, sentence, resolved_tags)
    sentence.pinyin = " ".join(readings)
    db.flush()
    return len(resolved_tags)


def main():
    parser = argparse.ArgumentParser(description="Tag sentences with HanLP segmentation + AI-assisted sense resolution.")
    parser.add_argument("--unit", type=int, default=None, help="Only tag sentences in this unit number.")
    parser.add_argument("--retag", action="store_true", help="Re-tag sentences that already have tags.")
    args = parser.parse_args()

    init_db()
    with get_session() as db:
        sentences = get_sentences_to_tag(db, HSK_LEVEL, args.unit, args.retag)
        print(f"Tagging {len(sentences)} sentence(s) in HSK level {HSK_LEVEL}"
              f"{f' (unit {args.unit})' if args.unit else ''}...")

        total_tags = 0
        for i, sentence in enumerate(sentences, 1):
            n = tag_sentence(db, sentence, HSK_LEVEL)
            total_tags += n
            if i % 25 == 0:
                print(f"  ...{i}/{len(sentences)}")

        print(f"✅ Tagged {len(sentences)} sentence(s), {total_tags} tag occurrence(s) resolved.")


if __name__ == "__main__":
    main()