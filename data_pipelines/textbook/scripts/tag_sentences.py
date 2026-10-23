"""
data_pipelines/textbook/scripts/tag_sentences.py

Pipeline stage 3: tags every bare Sentence row with its SentenceVocab links
(word, POS tag, reading, and the sense when there is only one meaning), then
sets each sentence's HSK level. Ambiguous senses are resolve_senses.py's job.
Runs ONCE over all sentences (not per level): a sentence's level is the
highest level among its words, so it's only known after tagging.

WORD SPLIT
  hsk-sentences-audio sentences come with a word split (hsa_source.tokens):
  it covers every character and 99.7% of its words are in the HSK list, so it
  is used as-is. A token NOT in the list that can be split entirely into list
  words (早点儿 -> 早 + 点儿, 今晚 -> 今 + 晚) is split -- the same job the
  Haiku re-segmentation used to do for 不是, done by rule. An out-of-list
  token that can't be split that way (一再表示: 表示 isn't in the 2021 list)
  gets ONE Haiku call for that occurrence, which splits it (preferring list
  words) or keeps it whole (a real word, a name, an idiom). New pieces
  outside the list are unlisted words as usual.
  Adjacent tokens that together form a LOWER-level list word are joined
  (不 + 客气 -> 不客气, HSK 1 instead of 客气's HSK 5), so a sentence isn't
  levelled up by the dataset splitting a set phrase.
  Sentences with no dataset tokens (e.g. Tatoeba, later) fall back to the
  original HanLP segmentation + vocab-list gate + Haiku re-segmentation
  (segment_sentence / get_validated_segmentation, unchanged).
  HanLP's POS tagger tags the words either way (CTB tags: P, VV, AD, NR...).

READING (which pronunciation, e.g. 还 hai2 vs huan2)
  The dataset's per-word pinyin, which is chosen in context (了 le / liǎo,
  长 cháng / zhǎng), converted to numeric citation tones. pypinyin (no tone
  sandhi) only when there is none. Tone-sandhi display pinyin is written
  later by the finalize stage.

SENSES for a word in the HSK list -- this stage makes NO AI calls for them:
  The reading picks the word's form(s) in the vocab JSON
  (hsk_vocab_source.match_forms; no matching form -> every form). If those
  forms hold exactly ONE meaning, that is the sense (origin = hsk_list,
  meaning text copied exactly, with form_index / meaning_index). Otherwise
  the tag is saved with NO sense, plus this occurrence's POS tag and
  reading, and resolve_senses.py later asks the AI which meaning THIS
  sentence uses -- one call per sentence, for the levels you build.
  (Choices are never reused across sentences: the old cache let the first
  sentence decide for every later one, e.g. 来 "substitute verb" from
  我来办 ended up on every 来.)
  Senses are created only on sentence evidence, with no unit --
  assign_units.py homes them.

WORDS NOT IN THE LIST (kept, never dropped)
  - HanLP tags it NR (proper noun) and its dictionary text looks like a name
    (capitalised, or "surname ...") -> word_type = proper_noun,
    origin = proper_noun, definition "(name) <dataset gloss>"
  - otherwise -> word_type = auto, origin = unlisted, definition = the
    dataset's gloss (CC-CEDICT text); Haiku writes one (ai_added) only if
    there is no gloss.
  Neither counts toward the sentence's level.

The dataset's gloss_en is NOT used to pick a meaning: it is the same
dictionary text for a given word and reading in every sentence, so it says
nothing about context.

USAGE (from the repo root)
    python -m data_pipelines.textbook.scripts.tag_sentences              # all untagged sentences
    python -m data_pipelines.textbook.scripts.tag_sentences --limit 50   # first 50
    python -m data_pipelines.textbook.scripts.tag_sentences --estimate   # report only, write nothing
    python -m data_pipelines.textbook.scripts.tag_sentences --retag      # re-tag every sentence
    python -m data_pipelines.textbook.scripts.tag_sentences --retag-changed    # only sentences whose split changes
Then: resolve_senses.py --levels 1 2
"""
import os
import re
import argparse
import json
import logging
import time
from collections import Counter
from datetime import datetime

import anthropic
from pypinyin import lazy_pinyin, Style
from pypinyin.contrib.tone_convert import to_tone3

from app.core.config.shared import settings
from app.core.config.textbook import TEXTBOOK_RAW_DIR
from app.textbook.db_utils import (
    get_session, init_db, get_or_create_vocab, get_senses_for_vocab, upsert_vocab_sense,
    set_sentence_tags, set_sentence_level, get_vocab_hanzi_through_level,
    get_untagged_sentences, delete_unused_non_list_vocab,
)
from app.textbook.models import Sentence, SentenceVocab, Vocab, WordType, VocabOrigin

from data_pipelines.textbook.scripts import hsa_source
from data_pipelines.textbook.scripts import hsk_vocab_source as hvs

api_key = settings.CLAUDE_API_KEY
client = anthropic.Anthropic(api_key=api_key) if api_key else None

HAIKU_MODEL = "claude-haiku-4-5"
MAX_TOKENS_DEFINITION = 300
MAX_TOKENS_RESEGMENT = 500
COMMIT_EVERY = 50          # sentences

ALL_LEVELS = 7             # 7 = levels 7-9: every listed word is valid
PROPER_NOUN_POS = {"NR"}

VOCAB_PATH = TEXTBOOK_RAW_DIR / "hsk_vocab.json"

_CONTENT_RE = re.compile(r"[一-鿿]|\d+")

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_DIR = os.path.abspath(os.path.join(SCRIPT_DIR, "..", "debug", "tag_sentences"))
SEG_CACHE_PATH = os.path.join(SCRIPT_DIR, "segmentation_cache.json")


# --------------------------------- LOGGING ---------------------------------

def setup_logging() -> tuple[logging.Logger, str]:
    """One txt log file per run in ../debug/tag_sentences/ named by datetime."""
    os.makedirs(LOG_DIR, exist_ok=True)
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

    logger.info("=== tag_sentences.py run started ===")
    print(f"Logging full trace to {log_path}")
    return logger, log_path


# --------------------------------- SEGMENTATION CACHE ---------------------------------

def load_segmentation_cache() -> dict:
    """Keys are (word, pos_tag) tuples; values are the replacement
    [(word, pos_tag), ...] list Haiku produced for that word the first time
    it was seen as out-of-vocab. Only used by the HanLP fallback path."""
    if not os.path.exists(SEG_CACHE_PATH):
        return {}
    with open(SEG_CACHE_PATH, "r", encoding="utf-8") as f:
        raw = json.load(f)
    return {tuple(k.split("\t")): [tuple(pair) for pair in v] for k, v in raw.items()}


def save_segmentation_cache(cache: dict) -> None:
    raw = {f"{word}\t{pos}": [[w, p] for w, p in replacement]
           for (word, pos), replacement in cache.items()}
    with open(SEG_CACHE_PATH, "w", encoding="utf-8") as f:
        json.dump(raw, f, ensure_ascii=False, indent=2, sort_keys=True)


# --------------------------------- HANLP ---------------------------------

_hanlp_tok = None
_hanlp_pos = None


def _get_hanlp_pos():
    """Lazy-load HanLP's POS tagger once per process (large model)."""
    global _hanlp_pos
    if _hanlp_pos is None:
        import hanlp
        _hanlp_pos = hanlp.load(hanlp.pretrained.pos.CTB9_POS_ELECTRA_SMALL)
    return _hanlp_pos


def _get_hanlp_tok():
    """Lazy-load HanLP's tokenizer -- only needed for sentences with no
    dataset word split."""
    global _hanlp_tok
    if _hanlp_tok is None:
        import hanlp
        _hanlp_tok = hanlp.load(hanlp.pretrained.tok.COARSE_ELECTRA_SMALL_ZH)
    return _hanlp_tok


def tag_pos(words: list[str]) -> list[str]:
    """CTB POS tags for an already-split list of words."""
    if not words:
        return []
    return list(_get_hanlp_pos()(words))


# ----------------------- NUMERALS (HanLP fallback path, unchanged) -----------------------
#
# ONLY runs of length >= 2 (or 第-prefixed) are extracted, so real fixed
# vocabulary containing a single numeral character (星期二, 一样, 一定, 一起,
# 十分) is never shredded. See the original docstring history for why.

_NUMERAL_CHARS = "零一二三四五六七八九十百千万亿两"
_NUMERAL_RUN_RE = re.compile(rf"第[{_NUMERAL_CHARS}]+|[{_NUMERAL_CHARS}]{{2,}}")
_NUMERAL_ONLY_RE = re.compile(rf"^(第[{_NUMERAL_CHARS}]+|[{_NUMERAL_CHARS}]{{2,}})$")
_NUMERAL_LOOKING_FIXED_WORDS = {"万一"}  # "in case / what if", not "10,001"


def extract_numeral_runs(text: str) -> list[tuple[str, bool]]:
    """Splits `text` into ordered (segment, is_numeral) pieces so numeral
    runs (二零一一年九月, 十二个月, 第二次) never reach HanLP glued to real words."""
    pieces, pos = [], 0
    for m in _NUMERAL_RUN_RE.finditer(text):
        if m.start() > pos:
            pieces.append((text[pos:m.start()], False))
        matched = m.group()
        pieces.append((matched, matched not in _NUMERAL_LOOKING_FIXED_WORDS))
        pos = m.end()
    if pos < len(text):
        pieces.append((text[pos:], False))
    return pieces


def segment_sentence(hanzi: str) -> list[tuple[str, str]]:
    """HanLP fallback segmentation: numeral runs pre-split as "NUM", HanLP
    word split + POS for the rest, punctuation (PU) dropped."""
    tok = _get_hanlp_tok()
    results = []
    for segment, is_numeral in extract_numeral_runs(hanzi):
        if not segment:
            continue
        if is_numeral:
            results.append((segment, "NUM"))
            continue
        words = tok(segment)
        tags = tag_pos(words)
        results.extend((w, t) for w, t in zip(words, tags) if t != "PU" and w.strip())
    return results


def split_numeral_tokens(words_and_pos: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """Re-applies the numeral rule to a segmentation that didn't come from
    segment_sentence (Haiku's, or a cached replacement)."""
    out = []
    for word, pos_tag in words_and_pos:
        if pos_tag != "NUM" and any(is_num for _, is_num in extract_numeral_runs(word)):
            out.extend(segment_sentence(word))
        else:
            out.append((word, pos_tag))
    return out


def _has_content(word: str) -> bool:
    return bool(_CONTENT_RE.search(word))


def _char_spans(words_and_pos):
    spans, cursor = [], 0
    for w, p in words_and_pos:
        spans.append((cursor, cursor + len(w), w, p))
        cursor += len(w)
    return spans


def _extract_json(text: str):
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
    return json.loads(text)


def haiku_resegment_sentence(hanzi: str, logger: logging.Logger) -> list[tuple[str, str]]:
    """HanLP fallback path only: ONE Haiku call to re-segment a sentence the
    way a textbook teaches words individually (不是 -> 不 + 是). Falls back to
    HanLP's own output if the call fails or doesn't reconstruct the text."""
    if client is None:
        logger.warning("No CLAUDE_API_KEY -- cannot resegment, using HanLP's output")
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
    response = client.messages.create(model=HAIKU_MODEL, max_tokens=MAX_TOKENS_RESEGMENT,
                                      messages=[{"role": "user", "content": prompt}])
    text = "".join(b.text for b in response.content if hasattr(b, "text"))
    try:
        words_and_pos = [(item["word"], item["pos"]) for item in _extract_json(text)]
    except Exception as e:
        logger.warning(f"Failed to parse Haiku resegmentation ({e!r}): {text!r} -- using HanLP's output")
        return segment_sentence(hanzi)
    if "".join(w for w, _ in words_and_pos) != hanzi:
        logger.warning(f"Haiku resegmentation text mismatch for {hanzi!r} -- using HanLP's output")
        return segment_sentence(hanzi)
    return words_and_pos


def get_validated_segmentation(hanzi: str, valid_vocab: set, seg_cache: dict,
                               logger: logging.Logger) -> list[tuple[str, str]]:
    """HanLP fallback path (sentences with no dataset tokens): HanLP output,
    checked against the vocab list; out-of-list words trigger a cached or
    Haiku re-segmentation. Never drops a sentence."""
    hanlp_words = segment_sentence(hanzi)
    invalid = [(w, p) for w, p in hanlp_words
               if p != "NUM" and _has_content(w) and w not in valid_vocab]
    if not invalid:
        logger.info(f"OK        hanlp-valid   {hanzi!r} -> {hanlp_words}")
        return hanlp_words

    logger.info(f"FALLBACK  {hanzi!r} -- out-of-list word(s): {[w for w, _ in invalid]}")
    cache_misses = [(w, p) for w, p in invalid if (w, p) not in seg_cache]
    if not cache_misses:
        final_words = []
        for w, p in hanlp_words:
            final_words.extend(seg_cache.get((w, p)) or [(w, p)])
        logger.info(f"  CACHE-ONLY resegmentation -> {final_words}")
    else:
        logger.warning(f"  HAIKU resegment call for {hanzi!r} (cache miss on {cache_misses})")
        haiku_words = split_numeral_tokens(haiku_resegment_sentence(hanzi, logger))
        if "".join(w for w, _ in hanlp_words) == "".join(w for w, _ in haiku_words):
            haiku_spans = _char_spans(haiku_words)
            for w, p in cache_misses:
                for (s, e, ww, pp) in _char_spans(hanlp_words):
                    if (ww, pp) == (w, p):
                        replacement = [(hw, hp) for (hs, he, hw, hp) in haiku_spans if hs < e and he > s]
                        if replacement and replacement != [(w, p)]:
                            seg_cache[(w, p)] = replacement
                            logger.info(f"  CACHE WRITE  ({w}, {p}) -> {replacement}")
                        break
        final_words = haiku_words
    return split_numeral_tokens(final_words)


# --------------------------------- READINGS ---------------------------------

def citation_reading(word: str) -> str:
    """pypinyin fallback: numeric CITATION tones, no tone sandhi, neutral = 5."""
    return " ".join(lazy_pinyin(word, style=Style.TONE3, neutral_tone_with_five=True))


def dataset_reading(pinyin: str) -> str | None:
    """Dataset token pinyin ("gāo xìng", "diǎnr", "le") -> numeric citation
    tones ("gao1 xing4", "dianr3", "le5")."""
    if not pinyin or not pinyin.strip():
        return None
    return " ".join(to_tone3(sy, neutral_tone_with_five=True) for sy in pinyin.split())


def compact(reading: str) -> str:
    """"gao1 xing4" -> "gao1xing4": the stored pinyin format (matches the
    vocab rows and the old tagger)."""
    return reading.replace(" ", "")


# --------------------------------- WORD SPLIT ---------------------------------

def split_into_list_words(word: str, valid_vocab: set) -> list[str] | None:
    """Split an out-of-list token into 2+ list words, trying every split (so
    结果实 -> 结 + 果实, which longest-first would miss) and keeping the one
    with the fewest pieces. None if no split uses only list words."""
    best = {0: []}
    for i in range(1, len(word) + 1):
        options = [best[j] + [word[j:i]] for j in range(i) if j in best and word[j:i] in valid_vocab]
        if options:
            best[i] = min(options, key=len)
    pieces = best.get(len(word))
    return pieces if pieces and len(pieces) > 1 else None


def _split_reading(reading: str | None, pieces: list[str]) -> list[str | None]:
    """Shares a token's reading out among its pieces when there is one
    syllable per character; otherwise each piece falls back to pypinyin."""
    sylls = reading.split() if reading else []
    if len(sylls) != sum(len(p) for p in pieces):
        return [None] * len(pieces)
    out, k = [], 0
    for p in pieces:
        out.append(" ".join(sylls[k:k + len(p)]))
        k += len(p)
    return out


MAX_MERGE = 4  # tokens


def merge_lower_level_words(words: list[dict], levels: dict, stats: Counter,
                            merges_log: list, hanzi: str) -> list[dict]:
    """Joins adjacent words that together form a list word at the SAME or a
    LOWER level than their highest piece: the dataset splits 不客气 (HSK 1)
    into 不 + 客气 (客气 is HSK 5), which would make 谢谢你！不客气。 an HSK 5
    sentence, and 有的 ("some", HSK 1) into 有 + 的, which gave 有 the meaning
    "having; -ful". A join that would RAISE the level is never made. Only
    list words are joined, so 在 + 家 stays apart (在家 isn't one). Over the
    whole dataset the same-level joins are just ~20: 不对, 请坐, 有的, 不大,
    不一定, 好吃, 挺好, 大海, 请进, 报到, 泪水, 水库, 岸上. Longest join first."""
    # where each word starts in the sentence, to never join across
    # punctuation (食欲不好，吃得很少 must not become 好吃)
    starts, pos = [], 0
    for w in words:
        idx = hanzi.find(w["word"], pos)
        starts.append(idx)
        if idx != -1:
            pos = idx + len(w["word"])
    out, i = [], 0
    while i < len(words):
        for n in range(min(MAX_MERGE, len(words) - i), 1, -1):
            span = words[i:i + n]
            joined = "".join(w["word"] for w in span)
            if starts[i] == -1 or hanzi[starts[i]:starts[i] + len(joined)] != joined:
                continue
            joined_level = levels.get(joined)
            piece_levels = [levels.get(w["word"]) or 99 for w in span]
            if joined_level is not None and joined_level <= max(piece_levels):
                readings = [w["reading"] for w in span]
                out.append({"word": joined, "gloss": "",
                            "reading": " ".join(readings) if all(readings) else None})
                stats["adjacent words joined into a list word"] += 1
                merges_log.append((" + ".join(w["word"] for w in span), joined, hanzi))
                i += n
                break
        else:
            out.append(words[i])
            i += 1
    return out


def words_from_dataset(sentence: Sentence, run: "Run", allow_ai: bool = True) -> list[dict] | None:
    """The dataset's word split for this sentence as
    [{"word", "reading", "gloss"}], with out-of-list compounds split into
    list words: by rule when every piece is a list word, otherwise by one
    Haiku call for this occurrence (haiku_split_token). None if the sentence
    has no usable dataset tokens."""
    if not sentence.external_id:
        return None
    tokens = hsa_source.tokens(sentence.external_id)
    if not tokens or "".join(t["word"] for t in tokens) != "".join(re.findall(r"[一-鿿]", sentence.hanzi)):
        return None
    out = []
    for t in tokens:
        word, reading = t["word"], dataset_reading(t.get("pinyin"))
        gloss = (t.get("gloss_en") or "").strip()
        pieces = None if word in run.valid_vocab else split_into_list_words(word, run.valid_vocab)
        if pieces:
            if allow_ai:
                run.stats["out-of-list token split into list words"] += 1
                run.splits.append((word, pieces, sentence.hanzi))
        elif allow_ai and needs_ai_split(word, run.valid_vocab):
            pieces = ai_split(run, word, sentence)
        if pieces:
            for piece, piece_reading in zip(pieces, _split_reading(reading, pieces)):
                # a piece outside the 2021 list but in another HSK list (表示) gets that list's text
                gloss = "" if piece in run.valid_vocab else run.known_words.get(piece, "")
                out.append({"word": piece, "reading": piece_reading, "gloss": gloss})
        else:
            out.append({"word": word, "reading": reading, "gloss": gloss})
    return out


def needs_ai_split(word: str, valid_vocab: set) -> bool:
    """An out-of-list token worth asking about: 2+ characters, not a number,
    with at least one list word inside it. Haiku may still keep it whole
    (警方 "police" is one word)."""
    if word in valid_vocab or len(word) < 2 or _NUMERAL_ONLY_RE.match(word):
        return False
    return any(word[i:j] in valid_vocab for i in range(len(word)) for j in range(i + 1, len(word) + 1)
               if (i, j) != (0, len(word)))


def ai_split(run: "Run", word: str, sentence: Sentence) -> list[str] | None:
    """One Haiku call for THIS occurrence (nothing reused between sentences).
    Returns 2+ pieces, or None to keep the token whole."""
    if run.estimate:
        run.stats["out-of-list token: AI split call needed"] += 1
        return None
    run.stats["out-of-list token: AI split call"] += 1
    pieces = haiku_split_token(word, sentence.hanzi, run.valid_vocab, run.known_words, run.logger)
    if pieces and len(pieces) > 1:
        run.stats["out-of-list token: AI split it"] += 1
        run.ai_splits.append((word, pieces, sentence.hanzi))
        return pieces
    run.stats["out-of-list token: AI kept it whole"] += 1
    run.ai_kept.append((word, sentence.hanzi))
    return None


# --------------------------------- AI CALLS ---------------------------------

def haiku_split_token(word: str, sentence: str, valid_vocab: set, known_words: dict,
                      logger: logging.Logger) -> list[str] | None:
    """ONE call: should this out-of-list token be split into separate words
    a learner studies (一再表示 -> 一再 + 表示)? The HSK-list words inside the
    token are shown so Haiku prefers them. The pieces must rebuild the token
    exactly, and every piece must be a word in SOME HSK list in the JSON
    (2021, 2.0 or 2025: known_words) -- so 一再表示 -> 一再 + 表示 is kept
    (表示 is in the 2.0 and 2025 lists), while 今晚 -> 今 + 晚, 五颗星 ->
    五颗 + 星 or 下达 -> 下 + 达 are rejected. Anything else keeps it whole."""
    inside = sorted({word[i:j] for i in range(len(word)) for j in range(i + 1, len(word) + 1)
                     if (word[i:j] in valid_vocab or word[i:j] in known_words) and (i, j) != (0, len(word))}, key=lambda w: (-len(w), w))
    prompt = (
        f"A Mandarin learning app's word splitter treated \"{word}\" as one word in this sentence, "
        f"but it is not in the HSK vocabulary list:\n\n{sentence}\n\n"
        f"HSK words found inside it: {', '.join(inside) or '(none)'}\n\n"
        "Should a learner study it as separate words? Split it into words if it is really several "
        "words written together (a word plus another word, a verb plus its object or complement, "
        "a phrase), preferring HSK-list words for the pieces. Every piece must be a word that "
        "can stand on its own: never split off a single character that is only part of a word "
        "(今 in 今晚, 馆 in 美术馆, 达 in 下达). Keep it whole if it is a single word in its own "
        "right (even if not in the list), a name, or a fixed idiom (chengyu).\n"
        "The pieces must join back to EXACTLY the original text. Respond with ONLY a JSON object, "
        'no other text: {"pieces": ["...", "..."]}  (one piece = keep it whole)'
    )
    try:
        response = client.messages.create(model=HAIKU_MODEL, max_tokens=MAX_TOKENS_RESEGMENT,
                                          messages=[{"role": "user", "content": prompt}])
        text = "".join(b.text for b in response.content if hasattr(b, "text"))
        m = re.search(r"\{.*\}", text, re.S)
        pieces = [p.strip() for p in json.loads(m.group(0))["pieces"] if p.strip()]
    except Exception as e:
        logger.warning(f"  AI-SPLIT  {word!r} in {sentence!r}: failed ({e!r}) -- kept whole")
        return None
    if "".join(pieces) != word:
        logger.warning(f"  AI-SPLIT  {word!r} -> {pieces} doesn't rebuild the token -- kept whole")
        return None
    if len(pieces) > 1:
        unknown = [p for p in pieces if p not in known_words]
        if unknown:
            # 今晚 -> 今 + 晚 would make 今 a "word" with an invented meaning
            logger.info(f"  AI-SPLIT  {word!r} -> {pieces} rejected (not a word in any HSK list: "
                        f"{', '.join(unknown)}) -- kept whole")
            return None
    logger.info(f"  AI-SPLIT  {word!r} -> {pieces}  in {sentence!r}")
    return pieces


def haiku_write_definition(hanzi: str, pos_tag: str, pinyin: str, sentence: str) -> str:
    """ONE call, only for a word that is neither in the list nor has a
    dataset gloss. Definition grounded in the sentence."""
    if client is None:
        return "UNKNOWN_ENGLISH"
    prompt = (
        f"A Mandarin learning app found the word \"{hanzi}\" (POS tag: {pos_tag}, "
        f"pinyin: {pinyin}) in this sentence:\n\n{sentence}\n\n"
        f"Write a concise English definition for \"{hanzi}\" AS USED IN THIS SENTENCE. "
        f"Respond with ONLY a JSON object, no other text: "
        f'{{"english": "<definition>"}}'
    )
    response = client.messages.create(model=HAIKU_MODEL, max_tokens=MAX_TOKENS_DEFINITION,
                                      messages=[{"role": "user", "content": prompt}])
    text = "".join(b.text for b in response.content if hasattr(b, "text"))
    try:
        return (_extract_json(text).get("english") or "UNKNOWN_ENGLISH").strip()
    except Exception:
        return "UNKNOWN_ENGLISH"


# --------------------------------- SENSE RESOLUTION ---------------------------------

def load_known_words() -> dict:
    """Every word in the vocab JSON under ANY HSK version (2021, 2.0, 2025),
    with a short gloss from its first lowercase reading. Used to check AI
    splits: a piece must be a real word somewhere in these lists."""
    out = {}
    with open(VOCAB_PATH, encoding="utf-8") as f:
        for e in json.load(f):
            forms = e.get("forms") or []
            if not forms:
                continue
            form = forms[hvs.default_form_index(e)]
            meanings = [m for m in form.get("meanings", []) if not hvs.is_note(m)][:2]
            out[e["simplified"]] = "; ".join(meanings)
    return out


class Run:
    """Per-run state shared by every sentence."""
    def __init__(self, logger, entries, valid_vocab, seg_cache, estimate):
        self.logger = logger
        self.entries = entries
        self.valid_vocab = valid_vocab
        self.seg_cache = seg_cache
        self.estimate = estimate
        self.stats = Counter()
        self.ai_added = []        # (hanzi, english, reason, sentence)
        self.non_list = []        # (hanzi, kind, english, sentence)
        self.splits = []          # (token, pieces, sentence)
        self.merges = []          # (pieces, joined word, sentence)
        self.known_words = load_known_words()   # every word in the JSON, any HSK version -> gloss
        self.ai_splits = []       # (token, pieces, sentence)
        self.ai_kept = []         # (token, sentence)
        self.levels = {w: hvs.level_of(e) for w, e in entries.items()}
        self.errors = []


def resolve_listed_word(db, run: Run, vocab: Vocab, reading: str, pos_tag: str):
    """The sense of an HSK-list word if its reading leaves only ONE meaning,
    else None (resolve_senses.py decides per sentence). No AI."""
    entry = run.entries[vocab.hanzi]
    forms = hvs.match_forms(entry, reading)
    if not forms:
        run.stats["reading matched no form (all forms offered)"] += 1
        run.logger.info(f"  READING  {vocab.hanzi} {reading!r} matches no form -- offering every form")
        forms = list(range(len(entry["forms"])))
    candidates = hvs.candidates(entry, forms)
    if len(candidates) != 1:
        run.stats["ambiguous: left for resolve_senses (one AI call per sentence)"] += 1
        return None
    if not hvs.single_meaning_is_safe(entry, forms):
        run.stats["rare reading: left for resolve_senses to check"] += 1
        return None
    chosen = candidates[0]
    run.stats["sense: only one meaning (no AI)"] += 1
    return upsert_vocab_sense(
        db, vocab, pinyin=compact(chosen["pinyin"]), english=chosen["english"],
        origin=VocabOrigin.hsk_list, word_type=vocab.word_type, pos_tag=pos_tag,
        form_index=chosen["form_index"], meaning_index=chosen["meaning_index"],
    )


def looks_like_name(gloss: str) -> bool:
    """Backs up the tagger's NR (proper noun) tag, which is sometimes wrong
    (棉衣 "cotton-padded clothes"): a name's dictionary text starts with a
    capital letter or says "surname". No gloss -> trust the tag."""
    if not gloss:
        return True
    return gloss[:1].isupper() or "surname" in gloss.lower()


def resolve_non_list_word(db, run: Run, word: str, pos_tag: str, reading: str,
                          gloss: str, sentence: Sentence):
    """A word outside the HSK list: proper noun or unlisted word. Kept. One
    meaning per word (there is no dictionary list to choose from)."""
    known = db.query(Vocab).filter(Vocab.hanzi == word).first()
    if known is not None:
        existing = get_senses_for_vocab(db, word)
        if existing:
            return known, existing[0]
    is_name = pos_tag in PROPER_NOUN_POS and looks_like_name(gloss)
    if is_name:
        word_type, origin = WordType.proper_noun, VocabOrigin.proper_noun
        english = f"(name) {gloss}" if gloss else f"(name) {compact(reading)}"
    else:
        word_type, origin = WordType.auto, VocabOrigin.unlisted
        english = gloss
        if not english:
            if run.estimate:
                run.stats["unlisted: AI definition needed"] += 1
                english = "UNKNOWN_ENGLISH"
            else:
                run.stats["unlisted: AI definition"] += 1
                english = haiku_write_definition(word, pos_tag, reading, sentence.hanzi)
                origin = VocabOrigin.ai_added
                run.ai_added.append((word, english, "word not in the HSK list and no dataset gloss",
                                     sentence.hanzi))

    vocab = get_or_create_vocab(db, word, word_type=word_type, origin=origin,
                                pinyin=compact(reading), english=english)
    existing = get_senses_for_vocab(db, word)
    sense = existing[0] if existing else upsert_vocab_sense(
        db, vocab, pinyin=compact(reading), english=english, origin=origin,
        word_type=word_type, pos_tag=pos_tag)
    if not existing:
        run.stats["proper noun added" if is_name else "unlisted word added"] += 1
        run.non_list.append((word, "proper noun" if is_name else "unlisted", english, sentence.hanzi))
        run.logger.info(f"  NON-LIST {word} ({pos_tag}) -> {english!r} [{origin.value}]")
    return vocab, sense


# --------------------------------- TAGGING ---------------------------------

def tag_sentence(db, run: Run, sentence: Sentence) -> int:
    """Tags one sentence and sets its level. Returns the number of tags."""
    words = words_from_dataset(sentence, run)
    if words is not None:
        words = merge_lower_level_words(words, run.levels, run.stats, run.merges, sentence.hanzi)
        tags = tag_pos([w["word"] for w in words])
        for w, t in zip(words, tags):
            w["pos"] = t
        run.stats["sentences: dataset word split"] += 1
    else:
        words = [{"word": w, "pos": p, "reading": None, "gloss": ""}
                 for w, p in get_validated_segmentation(sentence.hanzi, run.valid_vocab,
                                                        run.seg_cache, run.logger)]
        run.stats["sentences: HanLP word split"] += 1

    resolved, readings = [], []
    for w in words:
        word, pos_tag = w["word"], w["pos"]
        if not _has_content(word):
            continue
        reading = w["reading"] or citation_reading(word)
        is_listed = word in run.valid_vocab
        if pos_tag == "NUM" or (not is_listed and _NUMERAL_ONLY_RE.match(word)
                                and word not in _NUMERAL_LOOKING_FIXED_WORDS):
            readings.append(compact(reading))   # numbers: read aloud, never vocabulary
            continue
        if is_listed:
            vocab = db.query(Vocab).filter(Vocab.hanzi == word).one()
            sense = resolve_listed_word(db, run, vocab, reading, pos_tag)
        else:
            vocab, sense = resolve_non_list_word(db, run, word, pos_tag, reading, w["gloss"], sentence)
        resolved.append({"vocab_id": vocab.id, "sense_id": sense.id if sense else None,
                         "pos_tag": pos_tag, "reading": compact(reading)})
        readings.append(compact(reading))

    set_sentence_tags(db, sentence, resolved)
    # Citation pinyin for now; the finalize stage rewrites it with tone sandhi.
    sentence.pinyin = " ".join(readings)
    level = set_sentence_level(db, sentence)
    run.stats[f"sentence level {level}"] += 1
    run.logger.info(f"TAGGED    {sentence.hanzi!r} (level {level}, {len(resolved)} tags)")
    return len(resolved)


def split_changed(run: Run, sentence: Sentence) -> bool:
    """--retag-changed: True if the sentence has a word outside the list, or
    the current rules (no AI) would split it into different list words than
    its tags have."""
    rows = sorted(sentence.vocab_links, key=lambda r: r.position)
    if not rows or any(r.vocab.hsk_level is None for r in rows):
        return True
    words = words_from_dataset(sentence, run, allow_ai=False)
    if words is None:
        return False                      # HanLP path: left as it is
    words = merge_lower_level_words(words, run.levels, Counter(), [], sentence.hanzi)
    new = [w["word"] for w in words if w["word"] in run.valid_vocab and _has_content(w["word"])]
    old = [r.vocab.hanzi for r in rows if r.vocab.hsk_level is not None]
    return new != old


def write_summary(run: Run, n_sentences: int, total_tags: int, elapsed: float, log_path: str):
    log = run.logger
    log.info("")
    log.info("=" * 80)
    log.info(f"SUMMARY  {n_sentences} sentence(s), {total_tags} tag(s), {elapsed:.0f}s"
             f"{'  [ESTIMATE: nothing written]' if run.estimate else ''}")
    log.info("=" * 80)
    for k, v in sorted(run.stats.items()):
        log.info(f"  {k}: {v}")
    log.info("")
    log.info("=" * 80)
    log.info(f"AI-ADDED MEANINGS ({len(run.ai_added)}) -- review these")
    log.info("=" * 80)
    for hanzi, english, reason, s in run.ai_added:
        log.info(f"  {hanzi}: {english!r}  -- {reason}  -- e.g. {s}")
    log.info("")
    log.info("=" * 80)
    log.info(f"WORDS NOT IN THE HSK LIST ({len(run.non_list)})")
    log.info("=" * 80)
    for word, kind, english, s in run.non_list:
        log.info(f"  [{kind}] {word}: {english!r}  -- first seen in {s}")
    log.info("")
    log.info("=" * 80)
    log.info(f"OUT-OF-LIST TOKENS SPLIT INTO LIST WORDS ({len(run.splits)})")
    log.info("=" * 80)
    for token, pieces, s in run.splits:
        log.info(f"  {token} -> {' + '.join(pieces)}  -- in {s}")
    log.info("")
    log.info("=" * 80)
    log.info(f"OUT-OF-LIST TOKENS SPLIT BY AI ({len(run.ai_splits)}) -- review these")
    log.info("=" * 80)
    for token, pieces, s in run.ai_splits:
        log.info(f"  {token} -> {' + '.join(pieces)}  -- in {s}")
    log.info("")
    log.info(f"OUT-OF-LIST TOKENS THE AI KEPT WHOLE ({len(run.ai_kept)})")
    for token, s in run.ai_kept:
        log.info(f"  {token}  -- in {s}")
    log.info("")
    log.info("=" * 80)
    log.info(f"ADJACENT WORDS JOINED INTO A LIST WORD ({len(run.merges)})")
    log.info("=" * 80)
    for pieces, joined, s in run.merges:
        log.info(f"  {pieces} -> {joined}  -- in {s}")
    if run.errors:
        log.info("")
        log.info(f"ERRORS ({len(run.errors)})")
        for e in run.errors:
            log.info(f"  - {e}")

    print("")
    print(f"{'Estimated' if run.estimate else 'Tagged'} {n_sentences} sentence(s), {total_tags} tag(s) "
          f"in {elapsed:.0f}s.")
    for k, v in sorted(run.stats.items()):
        print(f"  {k}: {v}")
    if run.ai_added:
        print(f"  {len(run.ai_added)} AI-added meaning(s) to review -- listed in the log")
    if run.ai_splits:
        print(f"  AI splits to review ({len(run.ai_splits)}):")
        for token, pieces, _ in run.ai_splits[:15]:
            print(f"    {token} -> {' + '.join(pieces)}")
    if run.errors:
        print(f"  {len(run.errors)} error(s) -- listed in the log")
    print(f"Full log: {log_path}")
    if not run.estimate:
        print("Next: resolve_senses.py --levels <the levels you build> (one AI call per ambiguous word)")


def main():
    parser = argparse.ArgumentParser(description="Tag sentences: words, POS, readings, one-meaning senses, level.")
    parser.add_argument("--limit", type=int, default=None, help="Only tag the first N untagged sentences.")
    parser.add_argument("--retag", action="store_true", help="Re-tag every sentence, not only untagged ones.")
    parser.add_argument("--retag-changed", "--retag-unlisted", dest="retag_changed", action="store_true",
                        help="Re-tag only sentences that contain a word outside the HSK list or whose word "
                             "split the current rules would change (e.g. 有 + 的 -> 有的). Their meanings "
                             "are re-picked by resolve_senses afterwards; every other sentence keeps its tags.")
    parser.add_argument("--estimate", action="store_true",
                        help="Run without AI calls and roll everything back (report only).")
    args = parser.parse_args()

    if client is None and not args.estimate:
        raise SystemExit("CLAUDE_API_KEY is not set. It is needed for the few words outside the HSK list "
                         "with no dataset gloss (and sentences without a dataset word split). "
                         "Set the key, or use --estimate.")

    logger, log_path = setup_logging()
    seg_cache = load_segmentation_cache()
    entries = hvs.load_entries(str(VOCAB_PATH))
    t0 = time.time()

    init_db()
    with get_session() as db:
        valid_vocab = get_vocab_hanzi_through_level(db, ALL_LEVELS)
        if not valid_vocab:
            raise RuntimeError("No HSK-list words in the DB -- run load_hsk_lists.py first.")
        run = Run(logger, entries, valid_vocab, seg_cache, args.estimate)

        if args.retag_changed:
            sentences = [x for x in db.query(Sentence).order_by(Sentence.id).all() if split_changed(run, x)]
            if args.limit:
                sentences = sentences[:args.limit]
        elif args.retag:
            sentences = db.query(Sentence).order_by(Sentence.id).all()
            if args.limit:
                sentences = sentences[:args.limit]
        else:
            sentences = get_untagged_sentences(db, limit=args.limit)
        print(f"{'Estimating' if args.estimate else 'Tagging'} {len(sentences)} sentence(s)...")

        total_tags = 0
        for i, sentence in enumerate(sentences, 1):
            try:
                with db.begin_nested():
                    total_tags += tag_sentence(db, run, sentence)
            except Exception as e:
                msg = f"sentence {sentence.id} {sentence.hanzi!r}: {e!r}"
                logger.error(msg)
                run.errors.append(msg)
            if i % COMMIT_EVERY == 0:
                if not args.estimate:
                    db.commit()
                print(f"  ...{i}/{len(sentences)}")

        if args.estimate:
            db.rollback()
        else:
            senses_gone, words_gone = delete_unused_non_list_vocab(db)
            if senses_gone or words_gone:
                run.stats["cleanup: unused senses deleted"] += senses_gone
                run.stats["cleanup: unused non-list words deleted"] += words_gone
            db.commit()
            save_segmentation_cache(seg_cache)
        write_summary(run, len(sentences), total_tags, time.time() - t0, log_path)


if __name__ == "__main__":
    main()