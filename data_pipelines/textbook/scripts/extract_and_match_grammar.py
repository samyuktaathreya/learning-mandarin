import os
import json
import re
from pathlib import Path
from dotenv import load_dotenv
import anthropic
from typing import Union, Optional
from app.core.config.textbook import OCR_PATH, UNITS_OUTPUT_JSON, GRAMMAR_TIP_SOP, REFORMAT_GRAMMAR_TIP_SOP
from app.core.config.shared import ENV_FILE

# ---------------------------------------------------------
# Configuration & Setup
# ---------------------------------------------------------
MODEL = "claude-haiku-4-5"
MAX_TOKENS = 1024
TEMPERATURE = 0
MAX_RETRIES = 2  # retries for malformed JSON / bad table shape

load_dotenv(ENV_FILE)
api_key = os.environ.get("CLAUDE_API_KEY")
client = anthropic.Anthropic(api_key=api_key) if api_key else None


def remove_grammar_tips(json_file_path: Union[str, Path]) -> None:
    path = Path(json_file_path)
    if not path.exists():
        print(f"Error: File not found at {path}")
        return

    with open(path, 'r', encoding='utf-8') as f:
        data = json.load(f)

    for unit_id, unit_data in data.items():
        if "sentences" in unit_data:
            for sentence in unit_data["sentences"]:
                sentence.pop("grammar_tip", None)

    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print(f"Successfully removed grammar tips from {path.name}")


# ---------------------------------------------------------
# Step 1: Extract Grammar Tips from Markdown (unchanged)
# ---------------------------------------------------------
def parse_grammar_tips() -> dict:
    output_data = {}
    problem_units = []

    section_pattern = re.compile(
        r'##\s*注释\s*Notes(.*?)##\s*练习\s*Exercises',
        re.DOTALL | re.IGNORECASE
    )
    section_marker_pattern = re.compile(r'##\s*\[Section:\s*Note\s*\d+\]\s*\n*')
    heading_pattern = re.compile(r'(?m)^(?:##\s*)?(\d+)\s+(?=[^\s\d:])')

    for unit in range(3, 16):
        file_path = OCR_PATH / f"textbook_unit{unit}.md"
        if not file_path.exists():
            print(f" [warning] File not found: {file_path.name}")
            continue

        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()

        match = section_pattern.search(content)
        if not match:
            print(f" [warning] Could not find Notes/Exercises boundaries in Unit {unit}")
            continue

        notes_text = section_marker_pattern.sub('', match.group(1)).strip()
        matches = list(heading_pattern.finditer(notes_text))

        accepted = []
        expected = 1
        for m in matches:
            num = int(m.group(1))
            if num == expected:
                accepted.append(m.start())
                expected += 1

        if not accepted:
            print(f" [warning] No tip headings found in Unit {unit}")
            problem_units.append(unit)
            continue

        tips = []
        for i, start in enumerate(accepted):
            end = accepted[i + 1] if i + 1 < len(accepted) else len(notes_text)
            tips.append(notes_text[start:end].strip())

        if accepted[0] > 20:
            print(f" [warning] Unit {unit}: {accepted[0]} chars before first "
                  f"recognized heading -- possible missed tip 1")
            problem_units.append(unit)

        output_data[str(unit)] = tips

    if problem_units:
        print(f"\n [flagged for manual review] Units: {sorted(set(problem_units))}")

    return output_data


# ---------------------------------------------------------
# Step 2: Claude API Reformatting (structured JSON) & Matching
# ---------------------------------------------------------
def _validate_reformatted(obj) -> bool:
    """Check the JSON has the expected shape and no malformed tables."""
    if not isinstance(obj, dict) or "sections" not in obj:
        return False
    if not isinstance(obj["sections"], list) or len(obj["sections"]) == 0:
        return False

    for sec in obj["sections"]:
        if not isinstance(sec, dict):
            return False
        if "title" not in sec or "body" not in sec:
            return False
        if "|" in sec.get("body", ""):
            return False  # model leaked markdown table syntax into prose

        table = sec.get("table")
        if table is not None:
            if not isinstance(table, dict) or "headers" not in table or "rows" not in table:
                return False
            headers = table["headers"]
            rows = table["rows"]
            if not isinstance(headers, list) or not isinstance(rows, list) or len(rows) == 0:
                return False
            ncols = len(headers)
            for row in rows:
                if not isinstance(row, list) or len(row) != ncols:
                    return False
                if all((cell is None or str(cell).strip() == "") for cell in row):
                    return False  # fully blank row -> reject
    return True


def reformat_grammar_tip_text(reformat_sop_text: str, raw_tip: str) -> Optional[dict]:
    """Returns a structured dict: {"sections": [{"title","body","table"}]}."""
    if client is None:
        print(" [error] CLAUDE_API_KEY not configured; skipping API call.")
        return None

    user_content = (
        f"Here is the raw grammar tip:\n\n{raw_tip}\n\n"
        f"Reformat it per the system instructions. Return JSON only."
    )

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            response = client.messages.create(
                model=MODEL,
                max_tokens=MAX_TOKENS,
                temperature=TEMPERATURE,
                system=reformat_sop_text,
                messages=[{
                    "role": "user",
                    "content": [{"type": "text", "text": user_content}],
                }],
            )
            raw_text = response.content[0].text.strip()

            # Strip accidental code fences just in case
            raw_text = re.sub(r'^```(?:json)?\s*|\s*```$', '', raw_text.strip())

            parsed = json.loads(raw_text)
            if _validate_reformatted(parsed):
                return parsed

            print(f"  [retry {attempt}/{MAX_RETRIES}] malformed structure, retrying...")

        except (json.JSONDecodeError, IndexError) as e:
            print(f"  [retry {attempt}/{MAX_RETRIES}] parse error: {e}")
        except Exception as e:
            print(f" [error] API call failed during reformatting: {e}")
            return None

    print(" [error] Failed to get well-formed reformat after retries; skipping this tip.")
    return None


def get_matching_sentences(sop_text: str, structured_tip: dict, hanzi_list: list) -> list:
    if client is None:
        return []

    # Flatten to plain text for the matching prompt (tables included as simple text)
    plain_text_parts = []
    for sec in structured_tip["sections"]:
        plain_text_parts.append(sec["title"])
        plain_text_parts.append(sec["body"])
        if sec.get("table"):
            plain_text_parts.append(" | ".join(sec["table"]["headers"]))
            for row in sec["table"]["rows"]:
                plain_text_parts.append(" | ".join(str(c) for c in row))
    plain_text = "\n".join(plain_text_parts)

    user_content = (
        f"Here is the Grammar Tip:\n\n{plain_text}\n\n"
        f"Here is the list of Hanzi sentences for this unit:\n"
        f"{json.dumps(hanzi_list, ensure_ascii=False)}\n\n"
        f"Output ONLY a JSON list of strings containing the exact hanzi sentences from the list above that demonstrate this grammar tip.\n"
        f"Example format: [\"sentence 1\", \"sentence 2\"]"
    )

    try:
        response = client.messages.create(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            temperature=TEMPERATURE,
            system=sop_text,
            messages=[{
                "role": "user",
                "content": [{"type": "text", "text": user_content}],
            }],
        )
        response_text = response.content[0].text

        match = re.search(r'\[.*\]', response_text, re.DOTALL)
        if match:
            return json.loads(match.group(0))
        else:
            print(f" [warning] Could not parse JSON from response: {response_text}")
            return []

    except Exception as e:
        print(f" [error] API call failed during matching: {e}")
        return []


# ---------------------------------------------------------
# Step 3: Main Execution Flow
# ---------------------------------------------------------
def main():
    remove_grammar_tips(UNITS_OUTPUT_JSON)
    print("1. Extracting grammar tips from OCR cache...")
    unit_tips = parse_grammar_tips()

    if not unit_tips:
        print("No grammar tips extracted. Exiting.")
        return

    print("2. Loading SOPs and clean sentences data...")
    if not GRAMMAR_TIP_SOP.exists():
        print(f" [error] Matching SOP file not found at {GRAMMAR_TIP_SOP}")
        return
    with open(GRAMMAR_TIP_SOP, 'r', encoding='utf-8') as f:
        sop_text = f.read()

    if not REFORMAT_GRAMMAR_TIP_SOP.exists():
        print(f" [error] Reformat SOP file not found at {REFORMAT_GRAMMAR_TIP_SOP}")
        return
    with open(REFORMAT_GRAMMAR_TIP_SOP, 'r', encoding='utf-8') as f:
        reformat_sop_text = f.read()

    if not UNITS_OUTPUT_JSON.exists():
        print(f" [error] Clean sentences file not found at {UNITS_OUTPUT_JSON}")
        return
    with open(UNITS_OUTPUT_JSON, 'r', encoding='utf-8') as f:
        units_data = json.load(f)

    print("3. Reformatting tips and matching to sentences via Claude...")
    for unit_str, tips in unit_tips.items():
        if unit_str not in units_data:
            print(f" [warning] Unit {unit_str} not found in units_output.json. Skipping.")
            continue

        sentences_list = units_data[unit_str].get("sentences", [])
        if not sentences_list:
            continue

        hanzi_list = [s["hanzi"] for s in sentences_list]
        print(f"\nProcessing Unit {unit_str} ({len(tips)} tips, {len(hanzi_list)} sentences)...")

        for idx, tip in enumerate(tips, 1):
            print(f"  -> Calling Claude to Reformat Tip {idx}...")
            structured_tip = reformat_grammar_tip_text(reformat_sop_text, tip)
            if structured_tip is None:
                continue  # already logged; skip this tip entirely rather than save garbage

            print(f"  -> Calling Claude to Match Tip {idx}...")
            matched_hanzi = get_matching_sentences(sop_text, structured_tip, hanzi_list)

            if matched_hanzi:
                print(f"     Found {len(matched_hanzi)} matching sentence(s).")
                for matched_sentence in matched_hanzi:
                    for s_obj in sentences_list:
                        if s_obj["hanzi"] == matched_sentence:
                            # grammar_tip is now a list of structured tip objects,
                            # so multiple tips per sentence just append cleanly --
                            # no more string concatenation.
                            if "grammar_tip" in s_obj and s_obj["grammar_tip"]:
                                s_obj["grammar_tip"].append(structured_tip)
                            else:
                                s_obj["grammar_tip"] = [structured_tip]
            else:
                print("     No matches found.")

    print("\n4. Saving updated sentences back to units_output.json...")
    with open(UNITS_OUTPUT_JSON, 'w', encoding='utf-8') as f:
        json.dump(units_data, f, ensure_ascii=False, indent=2)

    print(f"✅ Done! Updated {UNITS_OUTPUT_JSON.name} successfully.")


if __name__ == "__main__":
    main()