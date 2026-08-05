"""
Parses the textbook vocabulary index PDF into structured vocab data and
writes it DIRECTLY to the textbook SQL database (Unit + Vocab rows) --
no more index_output.json / hsk1_dictionary.json / word_to_pinyin.json /
word_to_unit.json intermediates. Those files' entire purpose was to hand
data to the next script or to a consumer that queried them by hanzi; both
of those are now just DB reads (see db.get_word_to_pinyin_map /
get_word_to_unit_map, used by sentence_parser.py).

Pipeline (unchanged from the JSON version):
  1. OCR the index PDF (cached in OCR_cache) -> markdown tables
  2. Extraction agent -> JSON entries {hanzi, pinyin, pos, english, unit, section}
  3. Code: classify type (vocab / grammar / proper_noun), convert diacritic
     pinyin to numeric, dedupe (first-seen wins, lowest unit)
  4. Merge in language-app-data/added_vocab/hsk1.txt -- hand-added entries
  5. Upsert every record into `vocab` (+ implicitly `units`) via db.upsert_vocab

NOTE ON GRAMMAR CLASSIFICATION: unchanged -- see classify_type().
"""

import os
import base64
import re

import anthropic
from dotenv import load_dotenv
from app.core.config.shared import ENV_FILE
from app.core.config.textbook import (
    TEXTBOOK_RAW_DIR,
    TEXTBOOK_APP_DATA_DIR,
    SOP_PATH,
    OCR_PATH,
)

from app.textbook.database import get_session, init_db, upsert_vocab
from app.textbook.models import WordType

# --------------------------------- CONSTANTS ---------------------------------

OCR_SOP_FILENAME = os.path.join("vocab", "ocr.txt")
EXTRACTOR_SOP_FILENAME = os.path.join("vocab", "index_extractor.txt")

INDEX_PDF_FILEPATH = TEXTBOOK_RAW_DIR
INDEX_PDF_FILENAME = "hsk1_textbook_index.pdf"

OCR_CACHE_FILEPATH = OCR_PATH
OCR_CACHE_FILENAME = "vocab_index.md"
FORCE_OCR = False

# LLM raw-response dumps stay on disk for debugging -- these were never part
# of the app's data model, just crash forensics, so they're untouched.
from app.core.config.textbook import TEXTBOOK_INTERMEDIATE_DIR
LLM_RESPONSES_FILEPATH = TEXTBOOK_INTERMEDIATE_DIR / "LLM_RESPONSES"

ADDED_VOCAB_FILEPATH = TEXTBOOK_APP_DATA_DIR.parent / "language-app-data" / "added_vocab" / "hsk1.txt"

MODEL = "claude-sonnet-4-6"
OCR_MAX_TOKENS = 8192
AGENT_MAX_TOKENS = 8192
TEMPERATURE = 0

GRAMMAR_POS_PREFIXES = ("part", "aux", "助")

# --------------------------------- SETUP ---------------------------------

load_dotenv(ENV_FILE)
api_key = os.environ.get("CLAUDE_API_KEY")
client = anthropic.Anthropic(api_key=api_key) if api_key else None


# --------------------------------- HELPERS (unchanged) ---------------------------------

def load_sop(filename: str) -> str:
    with open(SOP_PATH / filename, "r", encoding="utf-8") as f:
        return f.read()


def extract_text_from_response(response) -> str:
    return "".join(b.text for b in response.content if b.type == "text").strip()


def extract_json_block(text: str) -> str:
    import json
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
    os.makedirs(str(LLM_RESPONSES_FILEPATH), exist_ok=True)
    path = os.path.join(str(LLM_RESPONSES_FILEPATH), f"vocab_index_{call_name}.txt")
    with open(path, "w", encoding="utf-8") as f:
        f.write(raw_text)
    return path


def load_added_vocab() -> list:
    """Unchanged: still reads the hand-maintained JSONL override file --
    that file is source-controlled input, not generated output, so there's
    nothing to migrate here."""
    import json
    if not ADDED_VOCAB_FILEPATH.exists():
        return []
    entries = []
    with open(ADDED_VOCAB_FILEPATH, "r", encoding="utf-8") as f:
        for lineno, line in enumerate(f, start=1):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError as e:
                print(f"  [warning] added_vocab/hsk1.txt line {lineno}: invalid JSON ({e}); skipping")
                continue
            entry["section"] = entry.get("section", "vocab")
            entries.append(entry)
    if entries:
        print(f"  [added-vocab] loaded {len(entries)} hand-added entr(y/ies) from {ADDED_VOCAB_FILEPATH}")
    return entries


# ------------------------- PINYIN: DIACRITIC -> NUMERIC (unchanged) -------------------------
# ... identical to the JSON version: _TONE_TABLE, _demark, _split_syllables,
# diacritic_to_numeric. Copy verbatim from the original file -- no data-model
# implications, pure string transform. Omitted here for brevity; paste the
# same block from vocab_index_parser.py unchanged.
from pinyin_utils import diacritic_to_numeric  # see note below


# --------------------------------- AGENT CALLS (unchanged) ---------------------------------

def run_index_ocr() -> str:
    os.makedirs(str(OCR_CACHE_FILEPATH), exist_ok=True)
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
    import json
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

def classify_type(entry: dict) -> WordType:
    if entry.get("section") == "proper_noun":
        return WordType.proper_noun
    pos = (entry.get("pos") or "").strip().lower()
    if pos.startswith(GRAMMAR_POS_PREFIXES):
        return WordType.grammar
    return WordType.vocab


def process_entries(raw_entries: list) -> list[dict]:
    """Classify + convert pinyin. Dedup-by-hanzi (lowest unit wins) now
    happens implicitly in db.upsert_vocab, called once per record in
    caller order -- so records must still be produced in a stable order
    (first-seen-in-source), same as before, for that rule to behave
    identically. We keep an in-memory dedup pass here too so a single
    call to main() doesn't do N redundant DB round-trips for the same word
    appearing twice in one run."""
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
            by_hanzi[hanzi] = record

    if skipped:
        print(f"  [warning] skipped {len(skipped)} unusable index row(s) (unclear/invalid unit):")
        for e in skipped:
            print(f"    - {e}")
    return list(by_hanzi.values())


def main():
    init_db()
    print("Parsing vocabulary index...")
    ocr_md = run_index_ocr()
    raw_entries = run_extractor(ocr_md)
    added_entries = load_added_vocab()

    if not raw_entries and not added_entries:
        print("  [warning] no raw entries extracted and no added vocab found; nothing to write")
        return

    print(f"  extracted {len(raw_entries)} raw rows from index")
    records = process_entries(raw_entries + added_entries)

    with get_session() as db:
        counts = {WordType.vocab: 0, WordType.grammar: 0, WordType.proper_noun: 0}
        for r in records:
            upsert_vocab(
                db,
                hanzi=r["hanzi"],
                pinyin=r["pinyin"],
                english=r["english"],
                unit_number=r["unit"],
                word_type=r["type"],
            )
            counts[r["type"]] += 1

    print(f"  vocab: {counts[WordType.vocab]}, grammar: {counts[WordType.grammar]}, "
          f"proper_nouns: {counts[WordType.proper_noun]}")
    print(f"Done. Wrote {len(records)} record(s) directly to the textbook DB.")


if __name__ == "__main__":
    main()