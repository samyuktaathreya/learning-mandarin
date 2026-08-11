"""
Extracts sentences and FITB exercises from the textbook AND workbook, per
unit, and writes them DIRECTLY to the textbook SQL database via
db.upsert_sentence (Sentence + SentenceVocab rows -- the tags array is now
that join table) and a small helper for FitbQuestion rows.

units_output.json is gone entirely. LEGACY_UNITS_OUTPUT_PATH fallback (for
when OCR is unavailable) still reads the *old* JSON file if present, purely
as a one-time bridge -- delete that branch once you've migrated your
existing units_output.json into the DB (see migrate_legacy_json.py).

Everything else -- OCR, sentence-finder/FITB-finder/solver agent calls,
verbatim filtering, vocab gating, the tagger + greedy_segment fallback, tone
sandhi, digit expansion -- is UNCHANGED from the JSON version. The only
things that differ are:
  1. word_to_pinyin / word_to_unit now come from the DB (db.get_word_to_pinyin_map
     / get_word_to_unit_map) instead of reading word_to_pinyin.json /
     word_to_unit.json.
  2. Each unit's sentence + tag records are upserted straight into the DB
     instead of being accumulated into a dict and dumped to JSON at the end.
  3. Per-unit reprocessing (UNITS_TO_PROCESS) is naturally idempotent because
     db.upsert_sentence does an update-in-place keyed on (unit, hanzi) --
     no more manual "merge into whatever's already on disk" bookkeeping.
"""

import os
import io
import json
import base64
import re

import anthropic
from pypdf import PdfReader, PdfWriter
from dotenv import load_dotenv
from app.core.config.shared import ENV_FILE
from app.core.config.textbook import (
    TEXTBOOK_RAW_DIR,
    TEXTBOOK_INTERMEDIATE_DIR,
    SOP_PATH,
    OCR_PATH,
)
from pypinyin import pinyin as pypinyin_pinyin, Style as PypinyinStyle

from app.textbook.db_utils import get_session, init_db, get_word_to_pinyin_map, get_word_to_unit_map, upsert_sentence
from app.textbook.models import FitbQuestion

# --------------------------------- CONSTANTS (unchanged) ---------------------------------
SENTENCE_PARSER_SOP_FILEPATH = SOP_PATH / "sentence_parser"

SENTENCE_FINDER_FILENAME = SENTENCE_PARSER_SOP_FILEPATH / "sentence_finder.txt"
FITB_FINDER_FILENAME = SENTENCE_PARSER_SOP_FILEPATH / "fitb_finder.txt"
FITB_SOLVER_FILENAME = SENTENCE_PARSER_SOP_FILEPATH / "fitb_solver.txt"
TAGGER_FILENAME = SENTENCE_PARSER_SOP_FILEPATH / "tagger.txt"

# NOTE: UNIT_STARTS/LAST_UNIT_END_PAGE/FIRST_UNIT_NUMBER below are HSK1's
# page layout. Once HSK2+ PDFs are loaded, these will almost certainly
# differ per level (different books, different page counts) -- this config
# will need to become per-hsk-level (e.g. a dict keyed by HSK_LEVEL) rather
# than a single flat literal per source. Left as-is structurally for now
# since only the file *paths* are known to have changed; flagging this so
# it isn't missed when HSK2 data actually gets loaded.
SOURCES = {
    "textbook": {
        # Raw PDFs are now split per level: .../data/raw/hsk_textbook/{level}.pdf
        "RAW_SUBDIR": "hsk_textbook",
        "OCR_SOP_FILENAME": SOP_PATH / "sentence_parser" / "ocr.txt",
        "UNIT_STARTS": [34, 42, 50, 60, 68, 76, 84, 92, 102, 110, 118, 124, 132],
        "LAST_UNIT_END_PAGE": 139,
        "FIRST_UNIT_NUMBER": 3,
    },
    "workbook": {
        # .../data/raw/hsk_workbook/{level}.pdf
        "RAW_SUBDIR": "hsk_workbook",
        "OCR_SOP_FILENAME": os.path.join("workbook_parser", "ocr.txt"),
        "UNIT_STARTS": [15, 23, 31, 39, 47, 55, 63, 71, 87, 96, 105, 113],
        "LAST_UNIT_END_PAGE": 120,
        "FIRST_UNIT_NUMBER": 4,
    },
}

OCR_CACHE_FILEPATH = OCR_PATH
FORCE_OCR = False

LLM_RESPONSES_FILEPATH = TEXTBOOK_INTERMEDIATE_DIR / "LLM_RESPONSES"

MODEL = "claude-sonnet-4-6"
OCR_MAX_TOKENS = 8192
AGENT_MAX_TOKENS = 8192
TEMPERATURE = 0

# module-level overrides from main.py
UNITS_TO_PROCESS = []
SOURCES_TO_PROCESS = None
# HSK level being processed this run (main.py passes this the same way it
# already passes UNITS_TO_PROCESS / SOURCES_TO_PROCESS -- via env override).
HSK_LEVEL = int(os.environ.get("HSK_LEVEL", "1"))

# --------------------------------- SETUP ---------------------------------

load_dotenv(ENV_FILE)
api_key = os.environ.get("CLAUDE_API_KEY")
client = anthropic.Anthropic(api_key=api_key) if api_key else None

_CJK_RE = re.compile(r"[\u4e00-\u9fff]")


# --------------------------------- HELPERS (unchanged) ---------------------------------

def load_sop(filename: str) -> str:
    with open(SOP_PATH / filename, "r", encoding="utf-8") as f:
        return f.read()
'''
def load_added_vocab() -> dict:
    if not ADDED_VOCAB_PATH.exists():
        return {}
    out = {}
    with open(ADDED_VOCAB_PATH, encoding="utf-8") as f:
        for lineno, line in enumerate(f, start=1):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError as e:
                print(f"  [warning] added_vocab line {lineno}: invalid JSON ({e}); skipping")
                continue
            if entry.get("hanzi") and entry.get("pinyin"):
                out[entry["hanzi"]] = entry["pinyin"]
    return out
'''



def load_word_dicts(db):
    """Was: read word_to_pinyin.json / word_to_unit.json off disk.
    Now: query the DB (populated by vocab_index_parser.py's run) directly.

    word_to_pinyin stays a flat {hanzi: pinyin} map (pinyin doesn't depend
    on hsk_level). word_to_unit needs to become {hanzi: (unit_number,
    hsk_level)} in db_utils.get_word_to_unit_map -- see _word_is_known_by()
    above -- since "unit 3" alone is ambiguous once more than one level's
    units exist."""
    word_to_pinyin = get_word_to_pinyin_map(db)
    word_to_unit = get_word_to_unit_map(db)
    # added = load_added_vocab()
    # word_to_pinyin.update(added)
    return word_to_pinyin, word_to_unit


def get_unit_page_ranges(source_cfg: dict):
    starts = source_cfg["UNIT_STARTS"]
    ranges = []
    for i, start in enumerate(starts):
        unit_number = source_cfg["FIRST_UNIT_NUMBER"] + i
        end = starts[i + 1] - 1 if i + 1 < len(starts) else source_cfg["LAST_UNIT_END_PAGE"]
        ranges.append((unit_number, start, end))
    return ranges


def split_unit_to_pdf_bytes(reader: PdfReader, start_page: int, end_page: int) -> bytes:
    writer = PdfWriter()
    for page_num in range(start_page - 1, end_page):
        writer.add_page(reader.pages[page_num])
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


def extract_text_from_response(response) -> str:
    return "".join(b.text for b in response.content if b.type == "text").strip()


def extract_json_block(text: str) -> str:
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(\[.*?\]|\{.*?\})\s*```", text, re.DOTALL)
    if fence:
        return fence.group(1)
    for oc, cc in [("[", "]"), ("{", "}")]:
        start = text.find(oc)
        if start == -1:
            continue
        depth = 0
        for i in range(start, len(text)):
            if text[i] == oc:
                depth += 1
            elif text[i] == cc:
                depth -= 1
                if depth == 0:
                    return text[start:i + 1]
    return text


def save_llm_response(source: str, unit_number: int, call_name: str, raw_text: str) -> str:
    # .../LLM_RESPONSES/hsk_textbook/{level}/unit{n}_{call}.txt
    # .../LLM_RESPONSES/hsk_workbook/{level}/unit{n}_{call}.txt
    responses_dir = LLM_RESPONSES_FILEPATH / f"hsk_{source}" / str(HSK_LEVEL)
    os.makedirs(str(responses_dir), exist_ok=True)
    path = os.path.join(str(responses_dir), f"unit{unit_number}_{call_name}.txt")
    with open(path, "w", encoding="utf-8") as f:
        f.write(raw_text)
    return path


def parse_json_response(raw_text: str, fallback, source: str, unit_number: int, call_name: str):
    saved = save_llm_response(source, unit_number, call_name, raw_text)
    try:
        return json.loads(extract_json_block(raw_text))
    except json.JSONDecodeError as e:
        print(f"  [warning] {call_name} JSON parse failed ({source} unit {unit_number}): {e}")
        print(f"  [warning] raw response saved to: {saved}")
        return fallback


def cjk_only(s: str) -> str:
    return "".join(_CJK_RE.findall(s))


_CONTENT_RE = re.compile(r"[一-鿿]|\d+")


def content_only(s: str) -> str:
    return "".join(_CONTENT_RE.findall(s))


# --------------------------------- VALIDATION (unchanged) ---------------------------------

_PUNCTUATION_EQUIVALENTS = {
    "，": ",", "。": ".", "？": "?", "！": "!", "：": ":", "；": ";",
    "（": "(", "）": ")", """: '"', """: '"', "'": "'", "'": "'",
    "　": " ",
}

_PAREN_ANNOTATION_RE = re.compile(r"[（(][^）)]*[）)]")
_LATIN_RUN_RE = re.compile(r"[A-Za-z0-9]+\s*[:：]?")


def normalize_for_match(s: str) -> str:
    s = _PAREN_ANNOTATION_RE.sub("", s)
    s = _LATIN_RUN_RE.sub("", s)
    s = s.replace("|", "")
    for full, half in _PUNCTUATION_EQUIVALENTS.items():
        s = s.replace(full, half)
    return re.sub(r"[ \t]+", "", s)


def filter_verbatim_sentences(sentences: dict, ocr_markdown: str, label: str):
    lines = [normalize_for_match(line) for line in ocr_markdown.split("\n")]
    verified, dropped = {}, []
    for zh, en in sentences.items():
        if any(normalize_for_match(zh) in line for line in lines):
            verified[zh] = en
        else:
            dropped.append(zh)
    if dropped:
        print(f"  [verbatim] {label}: dropped {len(dropped)} non-verbatim sentence(s):")
        for zh in dropped:
            print(f"    - {zh}")
    return verified, len(dropped)


def filter_verbatim_fitb(fitb_list: list, ocr_markdown: str, label: str):
    lines = [normalize_for_match(line) for line in ocr_markdown.split("\n")]
    verified, dropped = [], []
    for entry in fitb_list:
        blanked = entry.get("fill in the blank", "")
        segments = [normalize_for_match(seg) for seg in blanked.split("___")]
        found = False
        for line in lines:
            pos, ok = 0, True
            for seg in segments:
                if not seg:
                    continue
                idx = line.find(seg, pos)
                if idx == -1:
                    ok = False
                    break
                pos = idx + len(seg)
            if ok:
                found = True
                break
        if found:
            verified.append(entry)
        else:
            dropped.append(blanked)
    if dropped:
        print(f"  [verbatim] {label}: dropped {len(dropped)} non-verbatim FITB entr(y/ies):")
        for b in dropped:
            print(f"    - {b}")
    return verified, len(dropped)


_BLANK_PLACEHOLDER_RE = re.compile(
    r"（\s*　*\s*）"
    r"|\(\s*　*\s*\)"
    r"|_{2,}"
)


def normalize_fitb_blanks(fitb_list: list, label: str = "") -> list:
    normalized = []
    n_fixed = 0
    for entry in fitb_list:
        blanked = entry.get("fill in the blank", "")
        fixed, count = _BLANK_PLACEHOLDER_RE.subn("___", blanked)
        if count and fixed != blanked:
            n_fixed += 1
        entry = {**entry, "fill in the blank": fixed}
        normalized.append(entry)
    if n_fixed and label:
        print(f"  [fitb-normalize] {label}: rewrote non-'___' blank placeholder(s) "
              f"in {n_fixed} entr(y/ies) to '___'")
    return normalized


def _word_is_known_by(word_location, target_hsk_level: int, target_unit_number: int) -> bool:
    """A word counts as already-taught by the time we reach
    (target_hsk_level, target_unit_number) if its home unit is in an earlier
    hsk_level entirely, or the same hsk_level at an earlier-or-equal unit.
    HSK levels are sequential curricula (all of HSK1 precedes all of HSK2),
    so (hsk_level, unit_number) tuples compare correctly with plain <=.

    word_to_unit's values now need to carry hsk_level alongside unit_number
    (db.get_word_to_unit_map must return {hanzi: (unit_number, hsk_level)}
    tuples rather than a bare unit_number) -- otherwise "unit 3" is
    ambiguous once more than one level exists. Falls back to treating a bare
    int as hsk_level 1 for backward compatibility if db_utils hasn't been
    updated yet."""
    if isinstance(word_location, tuple):
        word_unit_number, word_hsk_level = word_location
    else:
        word_unit_number, word_hsk_level = word_location, 1
    return (word_hsk_level, word_unit_number) <= (target_hsk_level, target_unit_number)


def build_known_chars(word_to_unit: dict, unit_number: int, hsk_level: int = HSK_LEVEL) -> set:
    known = set()
    for word, location in word_to_unit.items():
        if _word_is_known_by(location, hsk_level, unit_number):
            known.update(cjk_only(word))
    return known


def passes_vocab_gate(text: str, known_chars: set) -> bool:
    return all(ch in known_chars for ch in cjk_only(text))


def unknown_chars_in(text: str, known_chars: set) -> list:
    seen = []
    for ch in cjk_only(text):
        if ch not in known_chars and ch not in seen:
            seen.append(ch)
    return seen


VOCAB_GATED_SOURCES = {"workbook"}


def filter_vocab_gate_sentences(sentences: dict, known_chars: set, label: str, source: str):
    if source not in VOCAB_GATED_SOURCES:
        return sentences, 0
    verified, dropped = {}, []
    for zh, en in sentences.items():
        if passes_vocab_gate(zh, known_chars):
            verified[zh] = en
        else:
            dropped.append((zh, unknown_chars_in(zh, known_chars)))
    if dropped:
        print(f"  [vocab-gate] {label}: dropped {len(dropped)} sentence(s) with un-taught vocab:")
        for zh, bad_chars in dropped:
            print(f"    - {zh}  (not yet taught: {', '.join(bad_chars)})")
    return verified, len(dropped)


def filter_vocab_gate_fitb(fitb_list: list, known_chars: set, label: str, source: str):
    if source not in VOCAB_GATED_SOURCES:
        return fitb_list, 0
    verified, dropped = [], []
    for entry in fitb_list:
        full = entry.get("full_sentence_answer", "")
        if passes_vocab_gate(full, known_chars):
            verified.append(entry)
        else:
            dropped.append((full, unknown_chars_in(full, known_chars)))
    if dropped:
        print(f"  [vocab-gate] {label}: dropped {len(dropped)} FITB entr(y/ies) with un-taught vocab:")
        for f_, bad_chars in dropped:
            print(f"    - {f_}  (not yet taught: {', '.join(bad_chars)})")
    return verified, len(dropped)


# --------------------------------- TAGGING & PINYIN (unchanged) ---------------------------------

UNKNOWN_WORD_FALLBACK_SOURCES = {"textbook"}


def pypinyin_for_word(word: str) -> str:
    syllables = pypinyin_pinyin(word, style=PypinyinStyle.TONE3, neutral_tone_with_five=True)
    return "".join(s[0] for s in syllables)


_DIGIT_HANZI = "零一二三四五六七八九"


def _number_to_hanzi(n: int) -> str:
    if n == 0:
        return "零"
    digits = str(n)
    length = len(digits)
    units = ["", "十", "百", "千"]
    parts = []
    for i, ch in enumerate(digits):
        d = int(ch)
        power = length - i - 1
        if d == 0:
            if power != 0 and any(c != "0" for c in digits[i + 1:]):
                parts.append("零")
            continue
        if d == 1 and power == 1 and i == 0 and length == 2:
            parts.append(units[power])
        else:
            parts.append(_DIGIT_HANZI[d] + units[power])
    return "".join(parts)


def digit_run_to_pinyin(run: str) -> str:
    hanzi = ("".join(_DIGIT_HANZI[int(d)] for d in run) if len(run) >= 4
             else _number_to_hanzi(int(run)))
    return pypinyin_for_word(hanzi)


def digit_run_to_hanzi(run: str) -> str:
    if len(run) >= 4:
        return "".join(_DIGIT_HANZI[int(d)] for d in run)
    return _number_to_hanzi(int(run))


def expand_digit_tags(tags: list, pinyins: list):
    out_tags, out_pinyins, origin_runs = [], [], []
    for tag, py in zip(tags, pinyins):
        if not tag.isdigit():
            out_tags.append(tag)
            out_pinyins.append(py)
            origin_runs.append(None)
            continue
        hanzi = digit_run_to_hanzi(tag)
        chars = list(hanzi)
        out_tags.extend(chars)
        out_pinyins.extend(pypinyin_for_word(c) for c in chars)
        origin_runs.extend(tag for _ in chars)
    return out_tags, out_pinyins, origin_runs


_MEASURE_WORDS = {
    "个", "口", "岁", "块", "本", "杯子", "张", "分钟", "点", "些", "年",
}


def fix_liang(tags: list, pinyins: list, original_runs: list):
    out_tags, out_pinyins = list(tags), list(pinyins)
    for i, tag in enumerate(out_tags):
        if tag != "二" or original_runs[i] != "2":
            continue
        nxt = out_tags[i + 1] if i + 1 < len(out_tags) else None
        if nxt in _MEASURE_WORDS:
            out_tags[i] = "两"
            out_pinyins[i] = "liang3"
    return out_tags, out_pinyins


def known_words_for_unit(word_to_unit: dict, unit_number: int, hsk_level: int = HSK_LEVEL) -> list:
    words = [w for w, loc in word_to_unit.items() if _word_is_known_by(loc, hsk_level, unit_number)]
    words.sort(key=len, reverse=True)
    return words


_DIGIT_RUN_RE = re.compile(r"\d+")


def greedy_segment(sentence: str, allowed_words: list, allow_unknown: bool = False):
    target = content_only(sentence)
    tags, pos = [], 0
    while pos < len(target):
        if target[pos].isdigit():
            m = _DIGIT_RUN_RE.match(target, pos)
            tags.append(m.group())
            pos = m.end()
            continue
        match = next((w for w in allowed_words if target.startswith(cjk_only(w), pos)), None)
        if match is not None:
            tags.append(match)
            pos += len(cjk_only(match))
            continue
        if allow_unknown:
            tags.append(target[pos])
            pos += 1
            continue
        return None
    return tags


def validate_tags(sentence: str, tags, allowed_set: set, allow_unknown: bool = False) -> bool:
    if not isinstance(tags, list) or not tags:
        return False
    if not allow_unknown and any(t not in allowed_set and not t.isdigit() for t in tags):
        return False
    reconstructed = "".join(t if t.isdigit() else cjk_only(t) for t in tags)
    return reconstructed == content_only(sentence)


FIRST_TONE_RE = re.compile(r"\d")


def first_tone(pinyin_word: str):
    m = FIRST_TONE_RE.search(pinyin_word)
    return int(m.group()) if m else None


def apply_sandhi(tags: list, pinyins: list) -> list:
    adjusted = list(pinyins)
    for i, tag in enumerate(tags):
        nxt = first_tone(pinyins[i + 1]) if i + 1 < len(pinyins) else None
        if tag == "不":
            adjusted[i] = "bu2" if nxt == 4 else "bu4"
        elif tag == "一":
            if nxt in (4, 5):
                adjusted[i] = "yi2"
            elif nxt in (1, 2, 3):
                adjusted[i] = "yi4"
            else:
                adjusted[i] = "yi1"
    return adjusted


def run_tagger(sentences: list, allowed_words: list, source: str, unit_number: int) -> dict:
    if not sentences:
        return {}
    payload = (
        "Sentences to segment:\n" + json.dumps(sentences, ensure_ascii=False, indent=2) +
        "\n\nAllowed word list:\n" + json.dumps(allowed_words, ensure_ascii=False)
    )
    response = client.messages.create(
        model=MODEL,
        max_tokens=AGENT_MAX_TOKENS,
        temperature=TEMPERATURE,
        system=load_sop(TAGGER_FILENAME),
        messages=[{"role": "user", "content": payload}],
    )
    result = parse_json_response(extract_text_from_response(response), {}, source, unit_number, "tagger")

    if isinstance(result, dict):
        return result

    if isinstance(result, list):
        coerced = {}
        if len(result) == len(sentences) and all(isinstance(x, list) for x in result):
            coerced = dict(zip(sentences, result))
        else:
            for item in result:
                if isinstance(item, dict) and "sentence" in item and "tags" in item:
                    coerced[item["sentence"]] = item["tags"]
        if coerced:
            print(f"  [tagger-warning] {source} unit {unit_number}: tagger returned a JSON "
                  f"array instead of an object; coerced {len(coerced)}/{len(sentences)} "
                  f"entr(y/ies) into the expected shape")
            return coerced
        print(f"  [tagger-warning] {source} unit {unit_number}: tagger returned a JSON array "
              f"in an unrecognized shape; falling back to greedy_segment for all sentences")
        return {}

    print(f"  [tagger-warning] {source} unit {unit_number}: tagger returned unexpected type "
          f"{type(result).__name__}; falling back to greedy_segment for all sentences")
    return {}


def tag_and_pinyin(sentences: dict, word_to_pinyin: dict, word_to_unit: dict,
                    unit_number: int, source: str):
    """Returns (records, n_dropped). Each record: {hanzi, english, tags, pinyin}
    -- unchanged shape, it's just fed into db.upsert_sentence by the caller
    now instead of being appended to a JSON list."""
    if not sentences:
        return [], 0
    allow_unknown = source in UNKNOWN_WORD_FALLBACK_SOURCES
    allowed_words = known_words_for_unit(word_to_unit, unit_number)
    allowed_set = set(allowed_words)
    agent_tags = run_tagger(list(sentences.keys()), allowed_words, source, unit_number)
    if not isinstance(agent_tags, dict):
        agent_tags = {}

    records, dropped = [], []
    for zh, en in sentences.items():
        tags = agent_tags.get(zh)
        if not validate_tags(zh, tags, allowed_set, allow_unknown):
            tags = greedy_segment(zh, allowed_words, allow_unknown)
        if tags is None or not validate_tags(zh, tags, allowed_set, allow_unknown):
            dropped.append(zh)
            continue

        def pinyin_for_tag(t: str) -> str:
            if t.isdigit():
                return digit_run_to_pinyin(t)
            return word_to_pinyin[t] if t in word_to_pinyin else pypinyin_for_word(t)

        pinyins = apply_sandhi(tags, [pinyin_for_tag(t) for t in tags])
        tags, pinyins, origin_runs = expand_digit_tags(tags, pinyins)
        tags, pinyins = fix_liang(tags, pinyins, origin_runs)
        records.append({"hanzi": zh, "english": en, "tags": tags, "tag_pinyins": pinyins,
                        "pinyin": " ".join(pinyins)})

    if dropped:
        print(f"  [tagging] {source} unit {unit_number}: dropped {len(dropped)} unsegmentable sentence(s):")
        for zh in dropped:
            print(f"    - {zh}")
    return records, len(dropped)


# --------------------------------- FITB -> QUESTIONS (unchanged) ---------------------------------

def _answers_are_words(answers: list, full: str, blanked: str) -> bool:
    for a in answers:
        a_str = (a or "").strip()
        if not a_str:
            return False
        needle = cjk_only(a_str) or a_str
        if needle not in cjk_only(full) and a_str not in full:
            return False
    return True


def expand_fitb(entry: dict) -> list:
    blanked = entry.get("fill in the blank", "")
    answers = entry.get("answer", [])
    translation = (entry.get("translation") or "").strip()
    full = entry.get("full_sentence_answer", "")
    segments = blanked.split("___")
    n_blanks_found = len(segments) - 1
    if n_blanks_found != len(answers) or not answers:
        print(f"  [fitb-warning] blank/answer count mismatch, skipping: {blanked}")
        return []
    if not _answers_are_words(answers, full, blanked):
        print(f"  [fitb-warning] answer not found in full sentence (likely a leaked "
              f"word-bank label, not a word); dropping: {blanked}")
        return []
    questions = []
    for i in range(len(answers)):
        parts = []
        for j, seg in enumerate(segments):
            parts.append(seg)
            if j < len(answers):
                parts.append("___" if j == i else answers[j])
        q_text = "".join(parts)
        if translation:
            q_text += f" ({translation})"
        questions.append({"question": q_text, "answer": answers[i], "full_sentence": full})
    return questions


# --------------------------------- AGENT CALLS (unchanged) ---------------------------------

def run_ocr(pdf_bytes: bytes, ocr_sop: str, source: str, unit_number: int) -> str:
    # .../OCR_cache/hsk_textbook/{level}_unit{n}.md or .../hsk_workbook/{level}_unit{n}.md
    # .../OCR_cache/hsk_textbook/{level}/unit{n}.md
    # .../OCR_cache/hsk_workbook/{level}/unit{n}.md
    cache_dir = OCR_CACHE_FILEPATH / f"hsk_{source}" / str(HSK_LEVEL)
    os.makedirs(str(cache_dir), exist_ok=True)
    cache_path = os.path.join(str(cache_dir), f"unit{unit_number}.md")
    if not FORCE_OCR and os.path.exists(cache_path):
        print(f"  [cache] using cached OCR: {cache_path}")
        with open(cache_path, "r", encoding="utf-8") as f:
            return f.read()

    if client is None:
        print("  [warning] CLAUDE_API_KEY not configured; skipping OCR")
        return ""

    pdf_b64 = base64.standard_b64encode(pdf_bytes).decode("utf-8")
    response = client.messages.create(
        model=MODEL,
        max_tokens=OCR_MAX_TOKENS,
        temperature=TEMPERATURE,
        system=ocr_sop,
        messages=[{
            "role": "user",
            "content": [
                {"type": "document",
                 "source": {"type": "base64", "media_type": "application/pdf", "data": pdf_b64}},
                {"type": "text", "text": "Transcribe this unit per the SOP."},
            ],
        }],
    )
    ocr_md = extract_text_from_response(response)
    with open(cache_path, "w", encoding="utf-8") as f:
        f.write(ocr_md)
    return ocr_md


def run_text_agent(ocr_markdown: str, sop: str, source: str, unit_number: int,
                    call_name: str, fallback, extra_content: str = ""):
    if client is None or not ocr_markdown:
        return fallback
    content = f"Here is the OCR result for this unit:\n\n{ocr_markdown}"
    if extra_content:
        content += f"\n\n{extra_content}"
    response = client.messages.create(
        model=MODEL,
        max_tokens=AGENT_MAX_TOKENS,
        temperature=TEMPERATURE,
        system=sop,
        messages=[{"role": "user", "content": content}],
    )
    return parse_json_response(extract_text_from_response(response), fallback,
                                source, unit_number, call_name)


# --------------------------------- PIPELINE (DB-writing) ---------------------------------

def process_unit(db, source: str, unit_number: int, start_page: int, end_page: int,
                  reader: PdfReader, sops: dict, word_to_pinyin: dict, word_to_unit: dict) -> dict:
    """Same extraction/filtering pipeline as before, but ends by writing
    straight into the DB via db.upsert_sentence / FitbQuestion rows instead
    of returning a dict destined for JSON."""
    label = f"{source} unit {unit_number}"
    print(f"Processing {label} (pages {start_page}-{end_page})...")
    known_chars = build_known_chars(word_to_unit, unit_number)

    pdf_bytes = split_unit_to_pdf_bytes(reader, start_page, end_page)
    ocr_md = run_ocr(pdf_bytes, sops["ocr"], source, unit_number)

    counts = {}
    sentences = run_text_agent(ocr_md, sops["sentence_finder"], source, unit_number,
                                "sentence_finder", fallback={})
    counts["sentences_extracted"] = len(sentences)
    sentences, counts["sentences_dropped_verbatim"] = filter_verbatim_sentences(sentences, ocr_md, label)
    sentences, counts["sentences_dropped_vocab_gate"] = filter_vocab_gate_sentences(
        sentences, known_chars, label, source)

    fitb_candidates = run_text_agent(ocr_md, sops["fitb_finder"], source, unit_number,
                                      "fitb_finder", fallback=[])
    counts["fitb_candidates"] = len(fitb_candidates)
    if fitb_candidates:
        extra = ("Here are the candidate fill-in-the-blank sentences to solve:\n\n"
                 + json.dumps(fitb_candidates, ensure_ascii=False, indent=2))
        fitb = run_text_agent(ocr_md, sops["fitb_solver"], source, unit_number,
                               "fitb_solver", fallback=[], extra_content=extra)
    else:
        fitb = []
    counts["fitb_solved"] = len(fitb)
    fitb = normalize_fitb_blanks(fitb, label)
    fitb, counts["fitb_dropped_verbatim"] = filter_verbatim_fitb(fitb, ocr_md, label)
    fitb, counts["fitb_dropped_vocab_gate"] = filter_vocab_gate_fitb(fitb, known_chars, label, source)

    sentence_records, counts["sentences_dropped_tagging"] = tag_and_pinyin(
        sentences, word_to_pinyin, word_to_unit, unit_number, source)
    counts["sentences_final"] = len(sentence_records)

    # --- write sentences + tags straight to the DB ---
    written_sentences = []
    for rec in sentence_records:
        sentence_row = upsert_sentence(
            db,
            unit_number=unit_number,
            hsk_level=HSK_LEVEL,
            hanzi=rec["hanzi"],
            english=rec["english"],
            pinyin=rec["pinyin"],
            tags=rec["tags"],
            tag_pinyins=rec["tag_pinyins"],
            source=source,
        )
        written_sentences.append(sentence_row)

    fitb_questions = [q for entry in fitb for q in expand_fitb(entry)]
    counts["fitb_questions_final"] = len(fitb_questions)

    # --- write FITB questions straight to the DB ---
    from app.textbook.db_utils import get_or_create_unit
    unit_row = get_or_create_unit(db, unit_number, hsk_level=HSK_LEVEL)
    # best-effort link back to the sentence a FITB question came from, by
    # matching full_sentence's hanzi content against sentences just written
    sentence_by_content = {cjk_only(s.hanzi): s for s in written_sentences}
    for q in fitb_questions:
        sentence_id = None
        match = sentence_by_content.get(cjk_only(q["full_sentence"]))
        if match:
            sentence_id = match.id
        existing = (
            db.query(FitbQuestion)
            .filter(FitbQuestion.unit_id == unit_row.id,
                    FitbQuestion.question == q["question"],
                    FitbQuestion.answer == q["answer"])
            .first()
        )
        if existing:
            continue
        db.add(FitbQuestion(
            sentence_id=sentence_id,
            unit_id=unit_row.id,
            question=q["question"],
            answer=q["answer"],
            full_sentence=q["full_sentence"],
        ))
    db.flush()

    return {"unit": unit_number, "counts": counts}


def run_source(db, source: str, word_to_pinyin: dict, word_to_unit: dict) -> list:
    cfg = SOURCES[source]
    sops = {
        "ocr": load_sop(cfg["OCR_SOP_FILENAME"]),
        "sentence_finder": load_sop(SENTENCE_FINDER_FILENAME),
        "fitb_finder": load_sop(FITB_FINDER_FILENAME),
        "fitb_solver": load_sop(FITB_SOLVER_FILENAME),
    }

    pdf_path = os.path.join(str(TEXTBOOK_RAW_DIR), cfg["RAW_SUBDIR"], f"{HSK_LEVEL}.pdf")
    if not os.path.exists(pdf_path):
        print(f"  [warning] {source} PDF not found at {pdf_path}; skipping")
        return []

    reader = PdfReader(pdf_path)
    unit_ranges = get_unit_page_ranges(cfg)
    if UNITS_TO_PROCESS:
        unit_ranges = [u for u in unit_ranges if u[0] in UNITS_TO_PROCESS]

    results = [process_unit(db, source, n, s, e, reader, sops, word_to_pinyin, word_to_unit)
               for n, s, e in unit_ranges]
    return results


def run_pipeline():
    init_db()
    with get_session() as db:
        word_to_pinyin, word_to_unit = load_word_dicts(db)
        sources = SOURCES_TO_PROCESS or list(SOURCES.keys())
        all_results = []
        for s in sources:
            all_results.extend(run_source(db, s, word_to_pinyin, word_to_unit))

    print(f"Done. Wrote {len(all_results)} unit-source result(s) directly to the textbook DB "
          f"(HSK level {HSK_LEVEL}).")
    for r in all_results:
        c = r["counts"]
        print(f"  {r['unit']}: {c['sentences_final']} sentences, {c['fitb_questions_final']} FITB questions")


if __name__ == "__main__":
    run_pipeline()