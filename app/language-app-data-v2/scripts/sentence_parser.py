"""
Extracts sentences and fill-in-the-blank exercises from the textbook AND workbook,
per unit, and produces the merged units_output.json consumed by create_questions.py.

Per source (textbook / workbook), per unit:
  1. split PDF -> unit PDF -> OCR agent (cached in OCR_cache; delete cache file or
     set FORCE_OCR=True to re-run)
  2. sentence finder agent -> {hanzi: english}
  3. verbatim filter (code, line-scoped) -> drops hallucinated sentences
  4. vocab gate (code, char-level)      -> drops sentences using not-yet-taught vocab
  5. FITB finder agent -> FITB solver agent -> verbatim filter -> vocab gate
  6. tagger agent segments sentences into known words -> code-validated, greedy
     longest-match fallback -> word-to-pinyin lookup -> tone sandhi (不 / 一) -> pinyin
  7. per-unit record with counts

Merge: textbook + workbook results per unit -> ../data/clean/units_output.json
  { "3": { "sentences": [{"hanzi","english","tags","pinyin"}...],
           "fill_in_the_blank": [{"question","answer","full_sentence"}...],
           "counts": {...} } }

Multi-blank FITB entries are expanded into one single-blank question per blank
(other blanks filled in), matching the schema create_questions.py expects.

Depends on vocab_index_parser.py having run first (word_to_pinyin.json /
word_to_unit.json). Not HSK1-specific: point SOURCES at any book's PDFs/page ranges.
"""

import os
import io
import json
import base64
import re
from pathlib import Path

import anthropic
from pypdf import PdfReader, PdfWriter
from dotenv import load_dotenv

# --------------------------------- CONSTANTS ---------------------------------

BASE_DIR = Path(__file__).resolve().parent.parent
SOP_FILEPATH = BASE_DIR / "SOPs"
SENTENCE_FINDER_FILENAME = os.path.join("sentence_parser", "sentence_finder.txt")
FITB_FINDER_FILENAME = os.path.join("sentence_parser", "fitb_finder.txt")
FITB_SOLVER_FILENAME = os.path.join("sentence_parser", "fitb_solver.txt")
TAGGER_FILENAME = os.path.join("sentence_parser", "tagger.txt")

SOURCES = {
    "textbook": {
        "PDF_FILEPATH": BASE_DIR / "data" / "raw",
        "PDF_FILENAME": "hsk1_textbook.pdf",
        "OCR_SOP_FILENAME": os.path.join("sentence_parser", "ocr.txt"),
        "UNIT_STARTS": [34, 42, 50, 60, 68, 76, 84, 92, 102, 110, 118, 124, 132],
        "LAST_UNIT_END_PAGE": 139,
        "FIRST_UNIT_NUMBER": 3,
    },
    "workbook": {
        "PDF_FILEPATH": BASE_DIR / "data" / "raw",
        "PDF_FILENAME": "hsk1_workbook.pdf",
        "OCR_SOP_FILENAME": os.path.join("workbook_parser", "ocr.txt"),
        "UNIT_STARTS": [15, 23, 31, 39, 47, 55, 63, 71, 87, 96, 105, 113],
        "LAST_UNIT_END_PAGE": 120,
        "FIRST_UNIT_NUMBER": 4,
    },
}

PINYIN_DICT_FILEPATH = BASE_DIR / "data" / "intermediate"
PINYIN_DICT_FILENAME = "word_to_pinyin.json"
UNIT_DICT_FILENAME = "word_to_unit.json"
LEGACY_UNITS_OUTPUT_PATH = BASE_DIR.parent / "language-app-data" / "data" / "clean" / "units_output.json"

OCR_CACHE_FILEPATH = BASE_DIR / "data" / "intermediate" / "OCR_cache"
FORCE_OCR = False

LLM_RESPONSES_FILEPATH = BASE_DIR / "data" / "intermediate" / "LLM_RESPONSES"

INTERMEDIATE_FILEPATH = BASE_DIR / "data" / "intermediate"      # per-source outputs land here
UNITS_OUTPUT_FILEPATH = BASE_DIR / "data" / "clean"
UNITS_OUTPUT_FILENAME = "units_output.json"

MODEL = "claude-sonnet-4-6"
OCR_MAX_TOKENS = 8192
AGENT_MAX_TOKENS = 8192
TEMPERATURE = 0

# module-level overrides from main.py
UNITS_TO_PROCESS = [4,5]      # e.g. [3, 4]
SOURCES_TO_PROCESS = None        # e.g. ["textbook"]

# --------------------------------- SETUP ---------------------------------

load_dotenv()
api_key = os.environ.get("CLAUDE_API_KEY")
client = anthropic.Anthropic(api_key=api_key) if api_key else None

_CJK_RE = re.compile(r"[\u4e00-\u9fff]")


# --------------------------------- HELPERS ---------------------------------

def load_sop(filename: str) -> str:
    with open(SOP_FILEPATH / filename, "r", encoding="utf-8") as f:
        return f.read()


def load_word_dicts():
    with open(os.path.join(str(PINYIN_DICT_FILEPATH), PINYIN_DICT_FILENAME), encoding="utf-8") as f:
        word_to_pinyin = json.load(f)
    with open(os.path.join(str(PINYIN_DICT_FILEPATH), UNIT_DICT_FILENAME), encoding="utf-8") as f:
        word_to_unit = json.load(f)
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
    for page_num in range(start_page - 1, end_page):  # 1-indexed inclusive -> 0-indexed
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
    os.makedirs(LLM_RESPONSES_FILEPATH, exist_ok=True)
    path = os.path.join(LLM_RESPONSES_FILEPATH, f"{source}_unit{unit_number}_{call_name}.txt")
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


# --------------------------------- VALIDATION ---------------------------------

_PUNCTUATION_EQUIVALENTS = {
    "，": ",", "。": ".", "？": "?", "！": "!", "：": ":", "；": ";",
    "（": "(", "）": ")", "“": '"', "”": '"', "‘": "'", "’": "'",
    "　": " ",
}


def normalize_for_match(s: str) -> str:
    """Unify punctuation width; strip spaces/tabs but keep newlines as hard boundaries."""
    for full, half in _PUNCTUATION_EQUIVALENTS.items():
        s = s.replace(full, half)
    return re.sub(r"[ \t]+", "", s)


def filter_verbatim_sentences(sentences: dict, ocr_markdown: str, label: str) -> dict:
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


def build_known_chars(word_to_unit: dict, unit_number: int) -> set:
    known = set()
    for word, unit in word_to_unit.items():
        if unit <= unit_number:
            known.update(cjk_only(word))
    return known


def passes_vocab_gate(text: str, known_chars: set) -> bool:
    return all(ch in known_chars for ch in cjk_only(text))


def filter_vocab_gate_sentences(sentences: dict, known_chars: set, label: str):
    verified, dropped = {}, []
    for zh, en in sentences.items():
        (verified.__setitem__(zh, en) if passes_vocab_gate(zh, known_chars)
         else dropped.append(zh))
    if dropped:
        print(f"  [vocab-gate] {label}: dropped {len(dropped)} sentence(s) with un-taught vocab:")
        for zh in dropped:
            print(f"    - {zh}")
    return verified, len(dropped)


def filter_vocab_gate_fitb(fitb_list: list, known_chars: set, label: str):
    verified, dropped = [], []
    for entry in fitb_list:
        full = entry.get("full_sentence_answer", "")
        (verified.append(entry) if passes_vocab_gate(full, known_chars)
         else dropped.append(full))
    if dropped:
        print(f"  [vocab-gate] {label}: dropped {len(dropped)} FITB entr(y/ies) with un-taught vocab:")
        for f_ in dropped:
            print(f"    - {f_}")
    return verified, len(dropped)


# --------------------------------- TAGGING & PINYIN ---------------------------------

def known_words_for_unit(word_to_unit: dict, unit_number: int) -> list:
    words = [w for w, u in word_to_unit.items() if u <= unit_number]
    words.sort(key=len, reverse=True)  # longest first for greedy matching
    return words


def greedy_segment(sentence: str, allowed_words: list):
    """Deterministic fallback: longest-match segmentation over CJK chars only."""
    target = cjk_only(sentence)
    tags, pos = [], 0
    while pos < len(target):
        match = next((w for w in allowed_words if target.startswith(cjk_only(w), pos)), None)
        if match is None:
            return None
        tags.append(match)
        pos += len(cjk_only(match))
    return tags


def validate_tags(sentence: str, tags, allowed_set: set) -> bool:
    if not isinstance(tags, list) or not tags:
        return False
    if any(t not in allowed_set for t in tags):
        return False
    return "".join(cjk_only(t) for t in tags) == cjk_only(sentence)


FIRST_TONE_RE = re.compile(r"\d")


def first_tone(pinyin_word: str):
    m = FIRST_TONE_RE.search(pinyin_word)
    return int(m.group()) if m else None


def apply_sandhi(tags: list, pinyins: list) -> list:
    """
    Runtime tone sandhi for standalone 不 / 一 tags, based on the FIRST tone of
    the NEXT word. (Multi-character dictionary entries already carry the sandhi
    the index printed, e.g. 不客气 bú kèqi.)
      不 (bu4): -> bu2 before a 4th tone; otherwise bu4.
      一 (yi1): -> yi2 before 4th/neutral tone; -> yi4 before tones 1/2/3;
                stays yi1 when final (counting/ordinal reading).
    Third-tone sandhi (3+3 -> 2+3) is a pronunciation rule and, per convention,
    is NOT rewritten in pinyin text, so it is deliberately not applied here.
    """
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
    return parse_json_response(extract_text_from_response(response), {}, source, unit_number, "tagger")


def tag_and_pinyin(sentences: dict, word_to_pinyin: dict, word_to_unit: dict,
                   unit_number: int, source: str):
    """Returns (records, n_dropped). Each record: {hanzi, english, tags, pinyin}."""
    if not sentences:
        return [], 0
    allowed_words = known_words_for_unit(word_to_unit, unit_number)
    allowed_set = set(allowed_words)
    agent_tags = run_tagger(list(sentences.keys()), allowed_words, source, unit_number)

    records, dropped = [], []
    for zh, en in sentences.items():
        tags = agent_tags.get(zh)
        if not validate_tags(zh, tags, allowed_set):
            tags = greedy_segment(zh, allowed_words)  # deterministic fallback
        if tags is None or not validate_tags(zh, tags, allowed_set):
            dropped.append(zh)
            continue
        pinyins = apply_sandhi(tags, [word_to_pinyin[t] for t in tags])
        records.append({"hanzi": zh, "english": en, "tags": tags, "pinyin": " ".join(pinyins)})
    if dropped:
        print(f"  [tagging] {source} unit {unit_number}: dropped {len(dropped)} unsegmentable sentence(s):")
        for zh in dropped:
            print(f"    - {zh}")
    return records, len(dropped)


# --------------------------------- FITB -> QUESTIONS ---------------------------------

def expand_fitb(entry: dict) -> list:
    """
    One single-blank question per blank; other blanks filled with their answers.
    {"question": "<blanked sentence> (<english>)", "answer": word, "full_sentence": ...}
    """
    blanked = entry.get("fill in the blank", "")
    answers = entry.get("answer", [])
    translation = (entry.get("translation") or "").strip()
    full = entry.get("full_sentence_answer", "")
    segments = blanked.split("___")
    if len(segments) - 1 != len(answers) or not answers:
        print(f"  [fitb-warning] blank/answer count mismatch, skipping: {blanked}")
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


# --------------------------------- AGENT CALLS ---------------------------------

def run_ocr(pdf_bytes: bytes, ocr_sop: str, source: str, unit_number: int) -> str:
    os.makedirs(OCR_CACHE_FILEPATH, exist_ok=True)
    cache_path = os.path.join(str(OCR_CACHE_FILEPATH), f"{source}_unit{unit_number}.md")
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


# --------------------------------- PIPELINE ---------------------------------

def process_unit(source: str, unit_number: int, start_page: int, end_page: int,
                 reader: PdfReader, sops: dict, word_to_pinyin: dict, word_to_unit: dict) -> dict:
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
    sentences, counts["sentences_dropped_vocab_gate"] = filter_vocab_gate_sentences(sentences, known_chars, label)

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
    fitb, counts["fitb_dropped_verbatim"] = filter_verbatim_fitb(fitb, ocr_md, label)
    fitb, counts["fitb_dropped_vocab_gate"] = filter_vocab_gate_fitb(fitb, known_chars, label)

    sentence_records, counts["sentences_dropped_tagging"] = tag_and_pinyin(
        sentences, word_to_pinyin, word_to_unit, unit_number, source)
    counts["sentences_final"] = len(sentence_records)

    fitb_questions = [q for entry in fitb for q in expand_fitb(entry)]
    counts["fitb_questions_final"] = len(fitb_questions)

    return {
        "unit": unit_number,
        "start_page": start_page,
        "end_page": end_page,
        "sentences": sentence_records,
        "fill_in_the_blank": fitb_questions,
        "counts": counts,
    }


def load_existing_source_output(source: str):
    candidates = [
        os.path.join(str(INTERMEDIATE_FILEPATH), f"{source}_sentence_output.json"),
        os.path.join(str(INTERMEDIATE_FILEPATH), "sentence_output.json"),
    ]
    for path in candidates:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
    return None


def load_legacy_units_output():
    if not LEGACY_UNITS_OUTPUT_PATH.exists():
        return None
    with open(LEGACY_UNITS_OUTPUT_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def run_source(source: str, word_to_pinyin: dict, word_to_unit: dict) -> list:
    cfg = SOURCES[source]
    sops = {
        "ocr": load_sop(cfg["OCR_SOP_FILENAME"]),
        "sentence_finder": load_sop(SENTENCE_FINDER_FILENAME),
        "fitb_finder": load_sop(FITB_FINDER_FILENAME),
        "fitb_solver": load_sop(FITB_SOLVER_FILENAME),
    }
    legacy = load_legacy_units_output()
    if client is None and legacy is not None:
        print(f"  [warning] {source}: using legacy units output because OCR is unavailable")
        converted = []
        for unit_key, unit_data in sorted(legacy.items(), key=lambda item: int(item[0])):
            converted.append({
                "unit": int(unit_key),
                "sentences": unit_data.get("sentences", []),
                "fill_in_the_blank": unit_data.get("fill_in_the_blank", []),
                "counts": {"legacy": {"sentences_final": len(unit_data.get("sentences", [])), "fitb_questions_final": len(unit_data.get("fill_in_the_blank", []))}},
            })
        return converted

    pdf_path = os.path.join(str(cfg["PDF_FILEPATH"]), cfg["PDF_FILENAME"])
    if not os.path.exists(pdf_path):
        existing = load_existing_source_output(source)
        if existing is not None:
            print(f"  [warning] {source} PDF missing; using existing intermediate data")
            return existing
        print(f"  [warning] {source} PDF not found at {pdf_path}; skipping")
        return []

    reader = PdfReader(pdf_path)
    unit_ranges = get_unit_page_ranges(cfg)
    if UNITS_TO_PROCESS is not None:
        unit_ranges = [u for u in unit_ranges if u[0] in UNITS_TO_PROCESS]

    results = [process_unit(source, n, s, e, reader, sops, word_to_pinyin, word_to_unit)
               for n, s, e in unit_ranges]

    os.makedirs(INTERMEDIATE_FILEPATH, exist_ok=True)
    out_path = os.path.join(str(INTERMEDIATE_FILEPATH), f"{source}_sentence_output.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f"Wrote {len(results)} {source} unit(s) to {out_path}")
    return results


def merge_sources(per_source_results: dict) -> dict:
    """textbook first, then workbook; dedupe sentences by CJK content, FITB by (q, a)."""
    merged = {}
    order = [s for s in ["textbook", "workbook"] if s in per_source_results] + \
            [s for s in per_source_results if s not in ("textbook", "workbook")]
    for source in order:
        for unit_result in per_source_results[source]:
            unit_str = str(unit_result["unit"])
            bucket = merged.setdefault(unit_str, {
                "sentences": [], "fill_in_the_blank": [], "counts": {},
                "_seen_sentences": set(), "_seen_fitb": set(),
            })
            for rec in unit_result["sentences"]:
                key = cjk_only(rec["hanzi"])
                if key and key not in bucket["_seen_sentences"]:
                    bucket["_seen_sentences"].add(key)
                    bucket["sentences"].append(rec)
            for q in unit_result["fill_in_the_blank"]:
                key = (q["question"], q["answer"])
                if key not in bucket["_seen_fitb"]:
                    bucket["_seen_fitb"].add(key)
                    bucket["fill_in_the_blank"].append(q)
            bucket["counts"][source] = unit_result["counts"]
    for unit_str, bucket in merged.items():
        bucket.pop("_seen_sentences")
        bucket.pop("_seen_fitb")
        bucket["counts"]["merged"] = {
            "sentences_final": len(bucket["sentences"]),
            "fitb_questions_final": len(bucket["fill_in_the_blank"]),
        }
    return merged


def run_pipeline():
    word_to_pinyin, word_to_unit = load_word_dicts()
    sources = SOURCES_TO_PROCESS or list(SOURCES.keys())
    per_source = {s: run_source(s, word_to_pinyin, word_to_unit) for s in sources}

    merged = merge_sources(per_source)
    os.makedirs(UNITS_OUTPUT_FILEPATH, exist_ok=True)
    out_path = os.path.join(str(UNITS_OUTPUT_FILEPATH), UNITS_OUTPUT_FILENAME)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(merged, f, ensure_ascii=False, indent=2)
    print(f"Done. Merged {len(merged)} unit(s) into {out_path}")
    for unit_str in sorted(merged, key=int):
        m = merged[unit_str]["counts"]["merged"]
        print(f"  unit {unit_str}: {m['sentences_final']} sentences, "
              f"{m['fitb_questions_final']} FITB questions")
    return merged


if __name__ == "__main__":
    run_pipeline()