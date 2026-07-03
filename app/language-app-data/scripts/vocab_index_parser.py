"""
Parses the textbook vocabulary index PDF into structured vocab data.

Pipeline:
  1. OCR the index PDF (cached in OCR_cache) -> markdown tables
  2. Extraction agent -> JSON entries {hanzi, pinyin, pos, english, unit, section}
  3. Code: classify type (vocab / grammar / proper_noun), convert diacritic pinyin
     to numeric (ai4, peng2you5, Zhong1guo2), dedupe (first-seen wins, lowest unit)
  4. Write:
       ../data/clean/index_output.json        (consumed by create_questions.py)
       ../data/intermediate/word_to_pinyin.json (consumed by sentence_parser.py)

NOTE ON GRAMMAR CLASSIFICATION: entries whose POS is a particle/auxiliary marker
("part.", "aux.", "助") are typed as grammar; proper-nouns table entries are
proper_noun; everything else is vocab. Works for any HSK level using this index
layout — adjust GRAMMAR_POS_PREFIXES if a higher-level book uses other markers.
"""

import os
import io
import json
import base64
import re
from pathlib import Path

import anthropic
from dotenv import load_dotenv

# --------------------------------- CONSTANTS ---------------------------------

BASE_DIR = Path(__file__).resolve().parent.parent
SOP_FILEPATH = BASE_DIR / "SOPs"
OCR_SOP_FILENAME = os.path.join("vocab", "ocr.txt")
EXTRACTOR_SOP_FILENAME = os.path.join("vocab", "index_extractor.txt")

INDEX_PDF_FILEPATH = BASE_DIR / "data" / "raw"
INDEX_PDF_FILENAME = "hsk1_textbook_index.pdf"

OCR_CACHE_FILEPATH = BASE_DIR / "data" / "intermediate" / "OCR_cache"
OCR_CACHE_FILENAME = "vocab_index.md"
FORCE_OCR = False

LLM_RESPONSES_FILEPATH = BASE_DIR / "data" / "intermediate" / "LLM_RESPONSES"

OUTPUT_INDEX_FILEPATH = BASE_DIR / "data" / "clean"
OUTPUT_INDEX_FILENAME = "index_output.json"
OUTPUT_PINYIN_DICT_FILEPATH = BASE_DIR / "data" / "intermediate"
OUTPUT_PINYIN_DICT_FILENAME = "word_to_pinyin.json"

MODEL = "claude-sonnet-4-6"
OCR_MAX_TOKENS = 8192
AGENT_MAX_TOKENS = 8192
TEMPERATURE = 0

GRAMMAR_POS_PREFIXES = ("part", "aux", "助")

# --------------------------------- SETUP ---------------------------------

load_dotenv()
api_key = os.environ.get("CLAUDE_API_KEY")
client = anthropic.Anthropic(api_key=api_key) if api_key else None


# --------------------------------- HELPERS ---------------------------------

def load_sop(filename: str) -> str:
    with open(SOP_FILEPATH / filename, "r", encoding="utf-8") as f:
        return f.read()


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


def save_llm_response(call_name: str, raw_text: str) -> str:
    os.makedirs(LLM_RESPONSES_FILEPATH, exist_ok=True)
    path = os.path.join(str(LLM_RESPONSES_FILEPATH), f"vocab_index_{call_name}.txt")
    with open(path, "w", encoding="utf-8") as f:
        f.write(raw_text)
    return path


# ------------------------- PINYIN: DIACRITIC -> NUMERIC -------------------------

# accented char -> (base char, tone number)
_TONE_TABLE = {}
for base, marks in {
    "a": "āáǎà", "e": "ēéěè", "i": "īíǐì", "o": "ōóǒò", "u": "ūúǔù", "v": "ǖǘǚǜ",
}.items():
    for tone, ch in enumerate(marks, start=1):
        _TONE_TABLE[ch] = (base, tone)
        _TONE_TABLE[ch.upper()] = (base.upper(), tone)
_TONE_TABLE["ü"] = ("v", 0)
_TONE_TABLE["Ü"] = ("V", 0)

_VOWELS = set("aeiouvAEIOUV")


def _demark(word: str):
    """Strip tone marks; return (plain string, {char_index: tone})."""
    plain, tones = [], {}
    for ch in word:
        if ch in _TONE_TABLE:
            base, tone = _TONE_TABLE[ch]
            if tone:
                tones[len(plain)] = tone
            plain.append(base)
        else:
            plain.append(ch)
    return "".join(plain), tones


def _split_syllables(plain: str):
    """
    Split a plain (demarked) pinyin word into syllables.
    Boundaries: spaces/hyphens/apostrophes are explicit breaks. Otherwise a new
    syllable starts at a consonant that follows a vowel, keeping 'n'/'ng'/'r'
    finals with the preceding syllable ('n'+'g'+vowel splits as n | g+vowel).
    """
    syllables, current, seen_vowel = [], "", False
    i = 0
    while i < len(plain):
        ch = plain[i]
        low = ch.lower()
        if ch in " -'’":
            if current:
                syllables.append(current)
            current, seen_vowel = "", False
            i += 1
            continue
        if low in "aeiouv":
            current += ch
            seen_vowel = True
            i += 1
            continue
        # consonant
        if not seen_vowel:
            current += ch
            i += 1
            continue
        if low == "r" and (i + 1 == len(plain) or plain[i + 1].lower() not in "aeiouv"):
            current += ch  # erhua / syllable-final r
            i += 1
            continue
        if low == "n":
            nxt = plain[i + 1].lower() if i + 1 < len(plain) else ""
            nxt2 = plain[i + 2].lower() if i + 2 < len(plain) else ""
            if nxt == "g" and nxt2 in "aeiouv":
                current += ch          # n is final, g starts next syllable
                syllables.append(current)
                current, seen_vowel = "", False
                i += 1
                continue
            if nxt == "g":
                current += ch + plain[i + 1]  # ng final
                syllables.append(current)
                current, seen_vowel = "", False
                i += 2
                continue
            if nxt not in "aeiouv":
                current += ch          # n final before consonant / end
                syllables.append(current)
                current, seen_vowel = "", False
                i += 1
                continue
        # default: consonant begins a new syllable
        syllables.append(current)
        current, seen_vowel = ch, False
        i += 1
    if current:
        syllables.append(current)
    return syllables


def diacritic_to_numeric(pinyin: str) -> str:
    """Convert accented pinyin to the app's numeric form: 'Zhōngguó' -> 'Zhong1guo2'."""
    pinyin = (pinyin or "").strip()
    if not pinyin:
        return ""
    if re.search(r"[1-5]", pinyin):
        return pinyin
    plain, tone_positions = _demark(pinyin)
    syllables = _split_syllables(plain)
    out, cursor = [], 0
    stripped = plain.replace(" ", "").replace("-", "").replace("'", "").replace("’", "")
    tones_stripped = {}
    j = 0
    for i, ch in enumerate(plain):
        if ch in " -'’":
            continue
        if i in tone_positions:
            tones_stripped[j] = tone_positions[i]
        j += 1
    for syl in syllables:
        tone = 5
        for k in range(cursor, cursor + len(syl)):
            if k in tones_stripped:
                tone = tones_stripped[k]
                break
        out.append(f"{syl}{tone}")
        cursor += len(syl)
    result = "".join(out)
    if stripped != "".join(syllables):
        print(f"  [pinyin-warning] syllabification mismatch for '{pinyin}' -> '{result}'")
    return result


# --------------------------------- AGENT CALLS ---------------------------------

def run_index_ocr() -> str:
    os.makedirs(OCR_CACHE_FILEPATH, exist_ok=True)
    cache_path = os.path.join(str(OCR_CACHE_FILEPATH), OCR_CACHE_FILENAME)
    if not FORCE_OCR and os.path.exists(cache_path):
        print(f"  [cache] using cached index OCR: {cache_path}")
        with open(cache_path, "r", encoding="utf-8") as f:
            return f.read()

    pdf_path = os.path.join(str(INDEX_PDF_FILEPATH), INDEX_PDF_FILENAME)
    if not os.path.exists(pdf_path):
        print(f"  [warning] index PDF not found at {pdf_path}; skipping OCR")
        return ""
    if client is None:
        print("  [warning] CLAUDE_API_KEY not configured; skipping OCR")
        return ""

    with open(pdf_path, "rb") as f:
        pdf_b64 = base64.standard_b64encode(f.read()).decode("utf-8")

    response = client.messages.create(
        model=MODEL,
        max_tokens=OCR_MAX_TOKENS,
        temperature=TEMPERATURE,
        system=load_sop(OCR_SOP_FILENAME),
        messages=[{
            "role": "user",
            "content": [
                {"type": "document",
                 "source": {"type": "base64", "media_type": "application/pdf", "data": pdf_b64}},
                {"type": "text", "text": "Transcribe this vocabulary index per the SOP."},
            ],
        }],
    )
    ocr_md = extract_text_from_response(response)
    with open(cache_path, "w", encoding="utf-8") as f:
        f.write(ocr_md)
    return ocr_md


def run_extractor(ocr_markdown: str) -> list:
    if client is None or not ocr_markdown:
        return []
    response = client.messages.create(
        model=MODEL,
        max_tokens=AGENT_MAX_TOKENS,
        temperature=TEMPERATURE,
        system=load_sop(EXTRACTOR_SOP_FILENAME),
        messages=[{"role": "user",
                   "content": f"Here is the OCR result of the vocabulary index:\n\n{ocr_markdown}"}],
    )
    raw = extract_text_from_response(response)
    saved = save_llm_response("extractor", raw)
    try:
        return json.loads(extract_json_block(raw))
    except json.JSONDecodeError as e:
        raise ValueError(f"Index extractor JSON parse failed ({e}); raw saved to {saved}")


# --------------------------------- PROCESSING ---------------------------------

def classify_type(entry: dict) -> str:
    if entry.get("section") == "proper_noun":
        return "proper_noun"
    pos = (entry.get("pos") or "").strip().lower()
    if pos.startswith(GRAMMAR_POS_PREFIXES):
        return "grammar"
    return "vocab"


def process_entries(raw_entries: list):
    """Classify, convert pinyin, dedupe by hanzi (first seen / lowest unit wins)."""
    by_hanzi = {}
    skipped = []
    for entry in raw_entries:
        hanzi = (entry.get("hanzi") or "").strip()
        pinyin_raw = (entry.get("pinyin") or "").strip()
        if not hanzi or "[unclear]" in hanzi or "[unclear]" in pinyin_raw:
            skipped.append(entry)
            continue
        try:
            unit = int(entry.get("unit"))
        except (TypeError, ValueError):
            skipped.append(entry)
            continue
        record = {
            "hanzi": hanzi,
            "pinyin": diacritic_to_numeric(pinyin_raw),
            "english": (entry.get("english") or "").strip(),
            "unit": unit,
            "type": classify_type(entry),
        }
        existing = by_hanzi.get(hanzi)
        if existing is None or unit < existing["unit"]:
            by_hanzi[hanzi] = record  # first unit this word appears in wins

    if skipped:
        print(f"  [warning] skipped {len(skipped)} unusable index row(s) (unclear/invalid unit):")
        for e in skipped:
            print(f"    - {e}")
    return list(by_hanzi.values())


def load_fallback_index() -> list:
    fallback_path = Path("/workspaces/learning-mandarin/app/language-app-data/data/clean/index_output.json")
    if not fallback_path.exists():
        return []
    with open(fallback_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    records = []
    for category in ("vocab", "grammar", "proper_nouns"):
        for item in data.get(category, []):
            if item.get("hanzi"):
                records.append({
                    "hanzi": item["hanzi"],
                    "pinyin": item.get("pinyin", ""),
                    "english": item.get("english", ""),
                    "unit": item.get("unit", 3),
                    "section": "proper_noun" if category == "proper_nouns" else "vocab",
                    "pos": item.get("part_of_speech", ""),
                })
    return records


def main():
    print("Parsing vocabulary index...")
    ocr_md = run_index_ocr()
    raw_entries = run_extractor(ocr_md)
    if not raw_entries:
        raw_entries = load_fallback_index()
        print("  using fallback vocabulary data from the legacy pipeline")
    print(f"  extracted {len(raw_entries)} raw rows")

    records = process_entries(raw_entries)
    index_output = {
        "vocab": [r for r in records if r["type"] == "vocab"],
        "grammar": [r for r in records if r["type"] == "grammar"],
        "proper_nouns": [r for r in records if r["type"] == "proper_noun"],
    }
    for key in index_output:
        for r in index_output[key]:
            r.pop("type", None)

    os.makedirs(OUTPUT_INDEX_FILEPATH, exist_ok=True)
    index_path = os.path.join(str(OUTPUT_INDEX_FILEPATH), OUTPUT_INDEX_FILENAME)
    with open(index_path, "w", encoding="utf-8") as f:
        json.dump(index_output, f, ensure_ascii=False, indent=2)

    word_to_pinyin = {r["hanzi"]: r["pinyin"] for r in records}
    os.makedirs(OUTPUT_PINYIN_DICT_FILEPATH, exist_ok=True)
    dict_path = os.path.join(str(OUTPUT_PINYIN_DICT_FILEPATH), OUTPUT_PINYIN_DICT_FILENAME)
    with open(dict_path, "w", encoding="utf-8") as f:
        json.dump(word_to_pinyin, f, ensure_ascii=False, indent=2)

    # units are also needed by sentence_parser's vocab gate -> keep alongside pinyin
    units_dict_path = os.path.join(str(OUTPUT_PINYIN_DICT_FILEPATH), "word_to_unit.json")
    with open(units_dict_path, "w", encoding="utf-8") as f:
        json.dump({r["hanzi"]: r["unit"] for r in records}, f, ensure_ascii=False, indent=2)

    print(f"  vocab: {len(index_output['vocab'])}, grammar: {len(index_output['grammar'])}, "
          f"proper_nouns: {len(index_output['proper_nouns'])}")
    print(f"Done. Wrote {index_path} and {dict_path}")
    return index_output


if __name__ == "__main__":
    main()