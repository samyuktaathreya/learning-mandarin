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
left in the pipeline. Every sense minted here is stamped
VocabOrigin.textbook_sentence (see resolve_word_sense) so downstream code
can always tell "this meaning came from the printed index" apart from
"this meaning was only ever evidenced by a sentence."

SEGMENTATION (2024 rewrite): HanLP's output is the default, but it is no
longer trusted blindly. Every non-numeral word HanLP produces is checked
against the cumulative HSK-{hsk_level} vocab list already loaded into the
db (see get_valid_vocab_set). If everything checks out, HanLP's output is
used as-is -- this is the common case and costs nothing extra.

If HanLP produces a word that ISN'T in the vocab list, that's a signal of
one of two things:
  (a) HanLP over-glued a real compound HanLP treats as one token but the
      textbook teaches as separate words -- the canonical example is
      不是, which HanLP keeps as one word but HSK1/2 teach 不 (AD) and 是
      (VC) separately, so 不是 itself is never a taught vocab entry.
  (b) The word is genuinely not taught at this HSK level at all.
There's no way to tell these apart cheaply, so the fallback is: ask Haiku
to re-segment the WHOLE sentence (see haiku_resegment_sentence), the way
a textbook would teach the words individually, then re-check ITS output
against the vocab list. If Haiku's segmentation still has an out-of-vocab
word, we genuinely don't know how to tag this sentence correctly, so it
is DROPPED (skipped, not tagged, logged) rather than silently minting a
bogus vocab entry -- this is what was happening before (see 不是 bug).

Each word that triggers the Haiku fallback gets its resolved replacement
cached (SEG_CACHE, keyed on (word, pos_tag), persisted to
segmentation_cache.json) so the same over-glued compound never needs a
second Haiku call, in this run or a future one.

SENSE RESOLUTION (per word, per occurrence) -- unchanged in shape from
before, only now guaranteed to only ever run on a word that has already
passed the HSK-vocab-list gate above:
  1. SenseCache lookup on (hanzi, pos_tag, pinyin) -- if hit, use that sense.
     Zero AI calls.
  2. Cache miss, word has NO existing senses at all -> brand new word ->
     ONE Haiku call to write a definition FROM SENTENCE CONTEXT -> create
     the sense (primary, since it's the word's first, origin=
     textbook_sentence) -> write cache.
  3. Cache miss, word HAS existing senses, but none share this exact
     (pos_tag, pinyin) -> check get_senses_matching_pos_pinyin as a
     backfill path (handles senses created by vocab_index_parser.py, which
     doesn't populate pos_tag) -- if found, reuse + write cache, no AI call.
  4. Still nothing -> genuinely ambiguous -> ONE Haiku call comparing
     against the word's nearest existing sense(s): SAME (reuse, cache it)
     or DIFFERENT (write a new sense with Haiku's definition, origin=
     textbook_sentence, cache it).
  5. REHOME: whichever sense got resolved, if this sentence's
     (hsk_level, unit_number) is EARLIER than the sense's current home,
     move the sense's home earlier (db_utils.rehome_sense already no-ops
     if it isn't actually earlier) -- "the word showed up sooner than we
     thought" should always win over whatever unit a sense was originally
     created at.

Every step above is a full sense-resolution outcome BEFORE any sentence
gets its SentenceVocab rows written -- once tag_sentences.py finishes a
unit, "every word used in a sentence is documented" (create_questions.py's
precondition) is actually true, not aspirational. Dropped sentences are
the one exception -- they are deliberately NOT tagged and NOT included in
that guarantee; see the note in main() about re-processing them.

READINGS: pypinyin only (CEDICT has been removed entirely from this
script). Style.TONE3 numeric tones, tone_sandhi=True so 不/一 tone changes
and neutral-tone handling reflect how the word is actually spoken, not
just its citation-form reading. Requires pypinyin >= 0.44 for the
tone_sandhi kwarg (confirmed working on 0.55).

LOGGING: every run writes a timestamped txt log to
data_pipelines/textbook/scripts/logs/. It contains, in order: (1) a line
per action taken (HanLP segmentation accepted, fallback triggered, cache
hit/miss, Haiku calls, sense resolution branch taken, rehomes), (2) a
warning line for every dropped sentence, (3) a final summary dump of the
full segmentation cache and the full list of drops for that run.

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
import logging
from datetime import datetime
import time
import anthropic
from dotenv import load_dotenv
from pypinyin import lazy_pinyin, Style
import time
from app.core.config.shared import ENV_FILE
from app.textbook.db_utils import (
    get_session, init_db, get_or_create_vocab, get_senses_for_vocab,
    get_cached_sense, write_sense_cache, get_senses_matching_pos_pinyin,
    get_nearest_sense, upsert_vocab_sense, rehome_sense,
    set_sentence_tags, get_vocab_hanzi_through_level
)
from app.textbook.models import Sentence, SentenceVocab, WordType, VocabOrigin

HSK_LEVEL = int(os.environ.get("HSK_LEVEL", "1"))

load_dotenv(ENV_FILE)
api_key = os.environ.get("CLAUDE_API_KEY")
client = anthropic.Anthropic(api_key=api_key) if api_key else None

HAIKU_MODEL = "claude-haiku-4-5"
MAX_TOKENS_DEFINITION = 300
MAX_TOKENS_COMPARE = 200
MAX_TOKENS_RESEGMENT = 500

_CONTENT_RE = re.compile(r"[\u4e00-\u9fff]|\d+")

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_DIR = os.path.abspath(os.path.join(SCRIPT_DIR, "..", "debug", "tag_sentences"))
SEG_CACHE_PATH = os.path.join(SCRIPT_DIR, "segmentation_cache.json")


# --------------------------------- LOGGING ---------------------------------

def setup_logging(hsk_level: int) -> logging.Logger:
    """One txt log file per run in ../debug/tag_sentences/ named by datetime."""
    os.makedirs(LOG_DIR, exist_ok=True)
    
    # Formats datetime as YYYYMMDD_HHMMSS (or use "%Y-%m-%d_%H-%M-%S" for dashes)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = os.path.join(LOG_DIR, f"{timestamp}.txt")

    logger = logging.getLogger(f"tag_sentences_{timestamp}")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    logger.handlers.clear()

    fh = logging.FileHandler(log_path, encoding="utf-8")
    fh.setLevel(logging.INFO)
    fh.setFormatter(logging.Formatter("%(asctime)s  %(levelname)-7s  %(message)s"))
    logger.addHandler(fh)

    ch = logging.StreamHandler()
    ch.setLevel(logging.WARNING)
    ch.setFormatter(logging.Formatter("%(levelname)s  %(message)s"))
    logger.addHandler(ch)

    logger.info(f"=== tag_sentences.py run started -- HSK level {hsk_level} ===")
    print(f"Logging full trace to {log_path}")
    return logger


# --------------------------------- SEGMENTATION CACHE ---------------------------------

def load_segmentation_cache() -> dict:
    """Keys are (word, pos_tag) tuples; values are the replacement
    [(word, pos_tag), ...] list Haiku produced for that word the first
    time it was seen as out-of-vocab. Persisted to disk so this survives
    across separate runs, not just within one."""
    if not os.path.exists(SEG_CACHE_PATH):
        return {}
    with open(SEG_CACHE_PATH, "r", encoding="utf-8") as f:
        raw = json.load(f)
    return {
        tuple(k.split("\t")): [tuple(pair) for pair in v]
        for k, v in raw.items()
    }


def save_segmentation_cache(cache: dict) -> None:
    raw = {
        f"{word}\t{pos}": [[w, p] for w, p in replacement]
        for (word, pos), replacement in cache.items()
    }
    with open(SEG_CACHE_PATH, "w", encoding="utf-8") as f:
        json.dump(raw, f, ensure_ascii=False, indent=2, sort_keys=True)


# --------------------------------- SEGMENTATION (HanLP) ---------------------------------

_hanlp_pipeline = None

# Numeral character class: cardinal digits (零-九), positional units
# (十百千万亿), and 两 ("two", used before measure words instead of 二).
# 第 is included ONLY as an ordinal prefix directly attached to a
# following numeral run (第二 "second", 第一百 "the hundredth").
#
# ONLY runs of length >= 2 (or 第-prefixed, any length) are extracted --
# a BARE SINGLE numeral character is left untouched, glued to its
# neighbors, so HanLP sees the whole original span and can decide for
# itself whether it's one cohesive real word. This matters a lot: single
# numeral characters are extremely common inside ordinary FIXED
# vocabulary that has nothing to do with counting -- 星期二 (Tuesday),
# 一样 (same/alike), 一定 (certainly), 一起 (together), 一下 (briefly),
# 十字路口 (crossroads), 十分 (very) -- extracting the numeral char out of
# any of these would wrongly shred a real word into a fake "NUM" span
# plus a meaningless leftover fragment. A length-2+ run (十二, 二零一一,
# 一百, ...) or an explicit 第-prefixed ordinal (第二, 第一百, ...) is
# reliably compositional/unbounded (you can construct infinitely many),
# which is genuinely never worth treating as a fixed vocabulary item --
# that boundary is where extraction is safe.
_NUMERAL_CHARS = "零一二三四五六七八九十百千万亿两"
_NUMERAL_RUN_RE = re.compile(rf"第[{_NUMERAL_CHARS}]+|[{_NUMERAL_CHARS}]{{2,}}")

# Small manual exception list: real, closed-set FIXED words that happen to
# be built entirely from numeral characters and would otherwise match the
# length>=2 rule above. Extend this set if more are found -- it's meant to
# stay short (most 2+-char numeral-only runs genuinely are numbers).
_NUMERAL_LOOKING_FIXED_WORDS = {"万一"}  # "in case / what if", not "10,001"


def extract_numeral_runs(text: str) -> list[tuple[str, bool]]:
    """Splits `text` into ordered (segment, is_numeral) pieces. Numeral
    runs (dates, ages, counts, durations -- 二零一一年九月, 十二个月, 一百块钱,
    第二次, ...) are isolated as their own pieces so they never get handed
    to HanLP glued to adjacent real words; everything else is returned as
    plain non-numeral segments for normal segmentation. An empty non-
    numeral segment can occur between two adjacent numeral runs or at the
    string's edges -- callers should skip empty segments."""
    pieces = []
    pos = 0
    for m in _NUMERAL_RUN_RE.finditer(text):
        if m.start() > pos:
            pieces.append((text[pos:m.start()], False))
        matched = m.group()
        is_numeral = matched not in _NUMERAL_LOOKING_FIXED_WORDS
        pieces.append((matched, is_numeral))
        pos = m.end()
    if pos < len(text):
        pieces.append((text[pos:], False))
    return pieces


def _get_hanlp():
    """Lazy-load HanLP's tokenizer + POS tagger once per process -- these
    are large models, no reason to reload per sentence."""
    global _hanlp_pipeline
    if _hanlp_pipeline is None:
        import hanlp
        tok = hanlp.load(hanlp.pretrained.tok.COARSE_ELECTRA_SMALL_ZH)
        pos = hanlp.load(hanlp.pretrained.pos.CTB9_POS_ELECTRA_SMALL)
        _hanlp_pipeline = (tok, pos)
    return _hanlp_pipeline


def segment_sentence(hanzi: str) -> list[tuple[str, str]]:
    """Numeral runs (dates, ages, quantities, durations -- see
    extract_numeral_runs) are pulled out and pre-split BEFORE HanLP ever
    sees the sentence, then HanLP's word segmentation + POS tagging is
    used for everything else. This is the DEFAULT segmenter -- its output
    is validated against the HSK vocab list by get_validated_segmentation,
    which only falls back to Haiku when HanLP produces something the
    vocab list doesn't recognize.

    WHY PRE-SPLIT RATHER THAN POST-FILTER ON HANLP'S OWN POS OUTPUT:
    real CTB9 tagging of numeral phrases is inconsistent -- a short date
    like 六岁 splits cleanly into 六/CD + 岁/M (fine either way, both are
    legitimate closed-set vocab), but a longer date like 二零一一年九月
    gets coalesced into ONE token tagged CD, swallowing 年 and 月 (both
    legitimate, likely-already-taught vocab) into the numeral blob instead
    of leaving them separately taggable. Stripping the numeral run out of
    the TEXT first (see extract_numeral_runs -- only length>=2 or
    第-prefixed runs are extracted; a bare single numeral character is
    left alone so real fixed vocabulary like 星期二/一样/一定/一起 is never
    wrongly shredded) means HanLP only ever has to segment the genuinely-
    word parts, and never has the opportunity to over-coalesce a number
    with adjacent real vocabulary.

    Punctuation/whitespace tokens are dropped (POS tag 'PU' in CTB9's
    tagset); content_only() isn't applied to the INPUT since HanLP's
    tokenizer wants real sentence punctuation for accurate boundaries, but
    the OUTPUT is filtered to drop punctuation tokens."""
    tok, pos = _get_hanlp()
    results = []
    for segment, is_numeral in extract_numeral_runs(hanzi):
        if not segment:
            continue
        if is_numeral:
            results.append((segment, "NUM"))
            continue
        words = tok(segment)
        tags = pos(words)
        results.extend((w, t) for w, t in zip(words, tags) if t != "PU" and w.strip())
    return results


def get_reading_for_word(word: str) -> str:
    """pypinyin ONLY -- CEDICT has been removed from this pipeline
    entirely. Style.TONE3 (numeric tones) with tone_sandhi=True, which
    applies pypinyin's built-in 不/一 tone-change rules and neutral-tone
    handling, so the reading reflects how the word is actually SPOKEN
    (e.g. 不是 -> bu2shi4, 一个 -> yi2ge4) rather than each character's
    citation-form tone. Falls back to no-sandhi if an old pypinyin version
    doesn't accept the kwarg (added ~0.44; confirmed present on 0.55)."""
    try:
        syllables = lazy_pinyin(
            word, style=Style.TONE3, neutral_tone_with_five=True, tone_sandhi=True,
        )
    except TypeError:
        syllables = lazy_pinyin(word, style=Style.TONE3, neutral_tone_with_five=True)
    return "".join(syllables) or "UNKNOWN_PINYIN"


# --------------------------------- HSK VOCAB LIST GATE ---------------------------------

def get_valid_vocab_set(db, hsk_level: int) -> set:
    """The set of hanzi words allowed to be tagged/created at this HSK
    level -- cumulative across every level up to and including hsk_level
    (an HSK2 sentence freely reuses HSK1 words)."""
    return get_vocab_hanzi_through_level(db, hsk_level)


def _char_spans(words_and_pos: list[tuple[str, str]]) -> list[tuple[int, int, str, str]]:
    """(start_char, end_char, word, pos) for each token, assuming the
    tokens concatenate back to the original string in order. Used to line
    up HanLP's and Haiku's segmentations of the same sentence so a
    reusable replacement can be extracted for the cache."""
    spans = []
    pos_cursor = 0
    for w, p in words_and_pos:
        spans.append((pos_cursor, pos_cursor + len(w), w, p))
        pos_cursor += len(w)
    return spans


def haiku_resegment_sentence(hanzi: str, logger: logging.Logger) -> list[tuple[str, str]]:
    """ONE Haiku call, only used as a fallback when HanLP's segmentation
    produced a word not in the target HSK level's vocab list. Asks Haiku
    to segment the way a textbook teaches words individually -- e.g. 不是
    should usually come back as 不 (AD) + 是 (VC) rather than one token,
    since HSK1/2 teach the negation adverb and the copula separately.

    If the API key is missing, or the response fails to parse, or Haiku's
    reconstructed text doesn't match the original sentence character-for-
    character (which would break the cache-diffing in
    get_validated_segmentation and make the output untrustworthy), this
    falls back to returning HanLP's own segmentation unchanged -- the
    caller's revalidation step will then correctly drop the sentence
    rather than tag it with something wrong."""
    if client is None:
        logger.warning("No CLAUDE_API_KEY configured -- cannot call Haiku for resegmentation, "
                        "falling back to HanLP's own segmentation")
        return segment_sentence(hanzi)

    prompt = (
        "Segment this Mandarin sentence into words the way a Chinese "
        "textbook teaches them individually -- for example 不是 should "
        "usually be split into 不 and 是 rather than kept as one word, "
        "since textbooks teach the negation adverb 不 and the copula 是 "
        "as separate vocabulary items. Give each word a CTB9-style POS "
        "tag (e.g. NN, VV, VC, AD, PN, VA, M, CD, CC, P, DEG, DEC, ...). "
        "The words must concatenate back to EXACTLY the original "
        "sentence, including all punctuation, with nothing added, "
        "removed, or reordered.\n\n"
        f"Sentence: {hanzi}\n\n"
        "Respond with ONLY a JSON array, no other text: "
        '[{"word": "...", "pos": "..."}, ...]'
    )
    response = client.messages.create(
        model=HAIKU_MODEL, max_tokens=MAX_TOKENS_RESEGMENT, temperature=0,
        messages=[{"role": "user", "content": prompt}],
    )
    text = "".join(b.text for b in response.content if hasattr(b, "text"))
    try:
        data = _extract_json(text)
        words_and_pos = [(item["word"], item["pos"]) for item in data]
    except Exception as e:
        logger.warning(f"Failed to parse Haiku resegmentation response ({e!r}): {text!r} "
                        f"-- falling back to HanLP's own segmentation")
        return segment_sentence(hanzi)

    reconstructed = "".join(w for w, _ in words_and_pos)
    if reconstructed != hanzi:
        logger.warning(f"Haiku resegmentation text mismatch for {hanzi!r} -- got {reconstructed!r}, "
                        f"falling back to HanLP's own segmentation")
        return segment_sentence(hanzi)

    return words_and_pos


def _has_content(word: str) -> bool:
    """Returns True if the word contains at least one Chinese character
    or digit -- i.e., it's not pure punctuation/whitespace. Used to skip
    validation of punctuation tokens against the vocab list."""
    return bool(_CONTENT_RE.search(word))


def get_validated_segmentation(db, sentence: Sentence, hsk_level: int, valid_vocab: set,
                                seg_cache: dict, logger: logging.Logger) -> list[tuple[str, str]]:
    """Returns the final (word, pos) segmentation for `sentence`. NEVER
    drops a sentence -- a real textbook sentence is ground truth by
    definition, so a word not yet in valid_vocab just means the printed
    index (or an earlier sentence) hasn't taught it yet, not that the
    sentence is unusable. resolve_word_sense (downstream, in
    tag_sentence) registers any such word as a brand new VocabSense.

    valid_vocab is still used to trigger the Haiku fallback below -- that
    fallback's real purpose is catching HanLP over-gluing a real compound
    the textbook teaches as separate words (the canonical example: 不是,
    which HanLP keeps as one token but HSK1/2 teach 不 and 是 separately).
    Whatever comes out the other end is accepted as final, whether or not
    it ends up matching valid_vocab.

    1. Run HanLP as always. If every non-NUM, content-bearing word is
       already in valid_vocab, done -- the common case, no extra cost.
    2. Otherwise, for each out-of-vocab word: check seg_cache for a
       previously-learned replacement first.
    3. If every out-of-vocab word has a cache hit, splice the cached
       replacements in directly -- ZERO Haiku calls.
    4. If any out-of-vocab word is uncached, make ONE Haiku call to
       re-segment the WHOLE sentence (cheaper than one call per word, and
       gives Haiku full sentence context), then best-effort diff its
       output against HanLP's to learn a reusable replacement for each
       originally-invalid word (written into seg_cache).
    5. Whatever segmentation results from 1-4 is FINAL. Any word still
       not in valid_vocab is simply new vocabulary -- logged, not dropped.
    """
    hanzi = sentence.hanzi
    hanlp_words = segment_sentence(hanzi)
    invalid = [(w, p) for w, p in hanlp_words
               if p != "NUM" and _has_content(w) and w not in valid_vocab]

    if not invalid:
        logger.info(f"OK        hanlp-valid   {hanzi!r} -> {hanlp_words}")
        return hanlp_words

    logger.info(f"FALLBACK  {hanzi!r} -- hanlp produced word(s) not yet in the vocab list "
                f"(checking for an over-glued compound before accepting): {[w for w, _ in invalid]}")

    cache_misses = [(w, p) for w, p in invalid if (w, p) not in seg_cache]

    if not cache_misses:
        final_words = []
        for w, p in hanlp_words:
            replacement = seg_cache.get((w, p))
            final_words.extend(replacement if replacement is not None else [(w, p)])
        logger.info(f"  CACHE-ONLY resegmentation for {hanzi!r} -> {final_words}")
    else:
        logger.warning(f"  HAIKU resegment call for {hanzi!r} (cache miss on {cache_misses})")
        haiku_words = haiku_resegment_sentence(hanzi, logger)
        logger.info(f"  HAIKU result for {hanzi!r} -> {haiku_words}")

        # Best-effort: learn a reusable replacement for each originally-
        # invalid word by diffing character spans between the two
        # segmentations. Only attempted if both reconstruct the exact
        # same string (haiku_resegment_sentence already guarantees this
        # for its own output, but we re-check here since this branch may
        # also run after a fallback-to-HanLP inside that function, in
        # which case hanlp_words == haiku_words and there's nothing to
        # learn).
        if "".join(w for w, _ in hanlp_words) == "".join(w for w, _ in haiku_words):
            hanlp_spans = _char_spans(hanlp_words)
            haiku_spans = _char_spans(haiku_words)
            for w, p in cache_misses:
                for (s, e, ww, pp) in hanlp_spans:
                    if (ww, pp) == (w, p):
                        replacement = [
                            (hw, hp) for (hs, he, hw, hp) in haiku_spans
                            if hs < e and he > s
                        ]
                        if replacement and replacement != [(w, p)]:
                            seg_cache[(w, p)] = replacement
                            logger.info(f"  CACHE WRITE  ({w}, {p}) -> {replacement}")
                        break
        else:
            logger.warning(f"  Haiku resegmentation degraded to HanLP's own output for {hanzi!r} "
                            f"-- nothing new to cache")

        final_words = haiku_words

    still_new = [(w, p) for w, p in final_words
                 if p != "NUM" and _has_content(w) and w not in valid_vocab]
    if still_new:
        logger.info(f"NEW-VOCAB {hanzi!r} -- word(s) not previously taught, will be registered: "
                    f"{[w for w, _ in still_new]}")

    logger.info(f"RESOLVED  {hanzi!r} -> {final_words}")
    return final_words


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
                        unit_number: int, hsk_level: int, logger: logging.Logger) -> "VocabSense":
    """The full per-word resolution pipeline described in the module
    docstring. Returns the VocabSense this occurrence should link to,
    rehomed earlier if this sentence predates the sense's current home.

    By the time this is called, `hanzi` has already passed the HSK vocab
    list gate in get_validated_segmentation -- this function no longer
    needs to (and does not) do its own vocab-list check.

    Every NEW sense this function creates (branches 2 and 4 below) is
    stamped origin=VocabOrigin.textbook_sentence -- there is no other
    origin a sense minted here could legitimately have, since arriving
    here at all means the word was found in a sentence, not the printed
    index (that's vocab_index_parser.py's job, upstream of this script)."""
    pinyin = get_reading_for_word(hanzi)

    # 1. cache hit -- zero AI calls
    sense = get_cached_sense(db, hanzi, pos_tag, pinyin)

    if sense is not None:
        logger.info(f"  SENSE cache-hit  {hanzi} ({pos_tag}, {pinyin})")
    else:
        existing_senses = get_senses_for_vocab(db, hanzi)

        if not existing_senses:
            # 2. brand new word -- one Haiku call, grounded in this sentence
            definition = haiku_write_definition(hanzi, pos_tag, pinyin, sentence)
            sense = upsert_vocab_sense(
                db, hanzi, pinyin, definition["english"], unit_number,
                word_type=WordType.vocab, hsk_level=hsk_level, make_primary=True,
                origin=VocabOrigin.textbook_sentence,
            )
            sense.pos_tag = pos_tag
            db.flush()
            logger.info(f"  SENSE new-word   {hanzi} ({pos_tag}, {pinyin}) -> "
                        f"\"{definition['english']}\" (unit {unit_number})")
        else:
            # 3. backfill path -- an existing sense already has this exact
            # (pos_tag, pinyin), it just predates pos_tag being tracked /
            # predates this exact combo being cached
            pos_pinyin_matches = get_senses_matching_pos_pinyin(db, hanzi, pos_tag, pinyin)
            if pos_pinyin_matches:
                sense = pos_pinyin_matches[0]
                logger.info(f"  SENSE backfill   {hanzi} ({pos_tag}, {pinyin}) -> existing sense id={sense.id}")
            else:
                # 4. genuinely ambiguous -- one Haiku call against the
                # nearest existing sense(s)
                nearest = get_nearest_sense(db, hanzi, unit_number, hsk_level)
                candidates = [nearest] if nearest else existing_senses[:1]
                result = haiku_compare_senses(hanzi, candidates, sentence)
                match_idx = result.get("match")
                if match_idx is not None and 0 <= match_idx < len(candidates):
                    sense = candidates[match_idx]
                    logger.info(f"  SENSE ambiguous  {hanzi} ({pos_tag}, {pinyin}) -> matched existing sense id={sense.id}")
                else:
                    english = result.get("english") or "UNKNOWN_ENGLISH"
                    sense = upsert_vocab_sense(
                        db, hanzi, pinyin, english, unit_number,
                        word_type=WordType.vocab, hsk_level=hsk_level, make_primary=False,
                        origin=VocabOrigin.textbook_sentence,
                    )
                    sense.pos_tag = pos_tag
                    db.flush()
                    logger.info(f"  SENSE new-sense  {hanzi} ({pos_tag}, {pinyin}) -> "
                                f"\"{english}\" (new sense, unit {unit_number})")

        write_sense_cache(db, hanzi, pos_tag, pinyin, sense)

    # 5. rehome earlier if this sentence predates the sense's current home
    # (no-ops if it isn't actually earlier -- see rehome_sense docstring)
    prior_unit = sense.unit.unit_number if sense.unit else None
    rehome_sense(db, sense, unit_number, hsk_level)
    new_unit = sense.unit.unit_number if sense.unit else None
    if prior_unit != new_unit:
        logger.info(f"  REHOME  {hanzi} sense id={sense.id}: unit {prior_unit} -> {new_unit}")
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


def tag_sentence(db, sentence: Sentence, hsk_level: int, valid_vocab: set,
                  seg_cache: dict, logger: logging.Logger, new_vocab_log: list) -> int:
    """Tags one sentence, returns the number of tag occurrences resolved.
    Also fills in Sentence.pinyin (left blank by sentence_parser.py) by
    joining each resolved tag's own reading.

    NO TEXTBOOK SENTENCE IS EVER DROPPED. A word not already in
    valid_vocab is registered as a brand-new VocabSense via
    resolve_word_sense (origin=textbook_sentence) instead of causing the
    sentence to be skipped -- the printed index or an earlier sentence
    simply hadn't taught it yet. valid_vocab is updated in place as new
    words are registered so later sentences IN THIS SAME RUN that reuse
    the word are recognized immediately (no repeat Haiku fallback).

    Tokens tagged "NUM" by segment_sentence (numeral runs -- dates, ages,
    counts, durations) are NEVER registered as vocabulary: no
    get_or_create_vocab, no resolve_word_sense, no SentenceVocab row.
    They're generated/compositional strings, not taught words. They're
    still included in the sentence's pinyin (via pypinyin), and since
    they're simply absent from the SentenceVocab tag list, the frontend
    (ClickableText) naturally renders them as plain non-clickable text."""
    unit_number = sentence.unit.unit_number
    words_and_pos = get_validated_segmentation(
        db, sentence, hsk_level, valid_vocab, seg_cache, logger,
    )

    resolved_tags = []
    readings = []
    for word, pos_tag in words_and_pos:
        if pos_tag == "NUM":
            readings.append(get_reading_for_word(word))
            continue
        is_new_word = word not in valid_vocab
        vocab = get_or_create_vocab(db, word)
        sense = resolve_word_sense(db, word, pos_tag, sentence.hanzi, unit_number, hsk_level, logger)
        if is_new_word:
            valid_vocab.add(word)
            new_vocab_log.append({
                "hanzi": word, "unit": unit_number, "sentence": sentence.hanzi,
            })
        resolved_tags.append((vocab.id, sense.id if sense else None))
        readings.append(sense.pinyin if sense and sense.pinyin else get_reading_for_word(word))

    set_sentence_tags(db, sentence, resolved_tags)
    sentence.pinyin = " ".join(readings)
    db.flush()
    logger.info(f"TAGGED    {sentence.hanzi!r} ({len(resolved_tags)} tags)")
    return len(resolved_tags)


def main():
    parser = argparse.ArgumentParser(description="Tag sentences with HanLP segmentation + AI-assisted sense resolution.")
    parser.add_argument("--unit", type=int, default=None, help="Only tag sentences in this unit number.")
    parser.add_argument("--retag", action="store_true", help="Re-tag sentences that already have tags.")
    args = parser.parse_args()

    logger = setup_logging(HSK_LEVEL)
    seg_cache = load_segmentation_cache()
    logger.info(f"Loaded segmentation cache: {len(seg_cache)} entr{'y' if len(seg_cache)==1 else 'ies'}")
    new_vocab_log: list = []

    init_db()
    with get_session() as db:
        valid_vocab = get_valid_vocab_set(db, HSK_LEVEL)
        logger.info(f"Valid vocab set for HSK<={HSK_LEVEL}: {len(valid_vocab)} words")

        sentences = get_sentences_to_tag(db, HSK_LEVEL, args.unit, args.retag)
        print(f"Tagging {len(sentences)} sentence(s) in HSK level {HSK_LEVEL}"
              f"{f' (unit {args.unit})' if args.unit else ''}...")

        total_tags = 0
        for i, sentence in enumerate(sentences, 1):
            n = tag_sentence(db, sentence, HSK_LEVEL, valid_vocab, seg_cache, logger, new_vocab_log)
            total_tags += n
            if i % 25 == 0:
                print(f"  ...{i}/{len(sentences)}")

        save_segmentation_cache(seg_cache)

        # --- end-of-run summary, written to the log file ---
        logger.info("=== SEGMENTATION CACHE (end of run) ===")
        for (word, pos), replacement in sorted(seg_cache.items()):
            logger.info(f"  ({word}, {pos}) -> {replacement}")

        logger.info(f"=== NEW VOCABULARY REGISTERED ({len(new_vocab_log)}) ===")
        for d in new_vocab_log:
            logger.info(f"  [unit {d['unit']}] {d['hanzi']!r} -- first evidenced by {d['sentence']!r}")

        print(f"✅ Tagged {len(sentences)} sentence(s), {total_tags} tag occurrence(s) resolved. "
              f"No sentences were dropped.")
        if new_vocab_log:
            print(f"📚 Registered {len(new_vocab_log)} word(s) not previously in the vocab index "
                  f"-- see the log file for details.")
        print(f"Segmentation cache now has {len(seg_cache)} entries.")
        
if __name__ == "__main__":
    main()