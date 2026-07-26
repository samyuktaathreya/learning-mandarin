import os
import json
import re
from pathlib import Path
from dotenv import load_dotenv
import anthropic
from typing import Union

# ---------------------------------------------------------
# Configuration & Setup
# ---------------------------------------------------------
MODEL = "claude-haiku-4-5"  # standard API identifier for Haiku 3.5
MAX_TOKENS = 1024                    # reduced since we only need a small JSON list back
TEMPERATURE = 0

# Resolve paths dynamically relative to script location
script_dir = Path(__file__).parent
data_dir = script_dir.parent / "data"
sop_dir = script_dir.parent / "SOPs"

ocr_cache_dir = data_dir / "intermediate" / "OCR_cache"
units_output_path = data_dir / "clean" / "units_output.json"
sop_path = sop_dir / "grammar_tip" / "grammar_tip.txt"
reformat_sop_path = sop_dir / "grammar_tip" / "reformat_grammar_tip.txt"

# Load environment variables
load_dotenv(script_dir.parent.parent / ".env")
api_key = os.environ.get("CLAUDE_API_KEY")
client = anthropic.Anthropic(api_key=api_key) if api_key else None

def remove_grammar_tips(json_file_path: Union[str, Path]) -> None:
    path = Path(json_file_path)
    if not path.exists():
        print(f"Error: File not found at {path}")
        return

    # Read the JSON data
    with open(path, 'r', encoding='utf-8') as f:
        data = json.load(f)

    # Remove grammar_tip from every sentence
    for unit_id, unit_data in data.items():
        if "sentences" in unit_data:
            for sentence in unit_data["sentences"]:
                sentence.pop("grammar_tip", None)

    # Save the cleaned data back to the file
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        
    print(f"Successfully removed grammar tips from {path.name}")

# ---------------------------------------------------------
# Step 1: Extract Grammar Tips from Markdown
# ---------------------------------------------------------
def parse_grammar_tips() -> dict:
    output_data = {}
    problem_units = []  # units that failed the sequential check -> flag for review

    section_pattern = re.compile(
        r'##\s*注释\s*Notes(.*?)##\s*练习\s*Exercises',
        re.DOTALL | re.IGNORECASE
    )
    # Strip the redundant "## [Section: Note N]" marker lines entirely --
    # they carry no content, and when present they just duplicate the
    # numbered heading that follows them.
    section_marker_pattern = re.compile(r'##\s*\[Section:\s*Note\s*\d+\]\s*\n*')

    # A tip heading: start of line, optional "## ", then digits, then
    # whitespace, then a non-digit, non-colon character (excludes "9:00",
    # and full-width （1） starts with a different character entirely so
    # it never matches here).
    heading_pattern = re.compile(r'(?m)^(?:##\s*)?(\d+)\s+(?=[^\s\d:])')

    for unit in range(3, 16):
        file_path = ocr_cache_dir / f"textbook_unit{unit}.md"
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

        # Only accept matches that form a clean 1,2,3... sequence.
        # Anything that breaks the sequence is a false positive (or a
        # genuinely malformed unit) -- either way, don't silently split
        # on it.
        accepted = []
        expected = 1
        for m in matches:
            num = int(m.group(1))
            if num == expected:
                accepted.append(m.start())
                expected += 1
            # else: skip -- doesn't match the expected next number

        if not accepted:
            print(f" [warning] No tip headings found in Unit {unit}")
            problem_units.append(unit)
            continue

        tips = []
        for i, start in enumerate(accepted):
            end = accepted[i + 1] if i + 1 < len(accepted) else len(notes_text)
            tips.append(notes_text[start:end].strip())

        # Sanity check: did we consume basically the whole notes section?
        # If there's a big leftover gap before the first heading, something's off.
        if accepted[0] > 20:
            print(f" [warning] Unit {unit}: {accepted[0]} chars before first "
                  f"recognized heading -- possible missed tip 1")
            problem_units.append(unit)

        output_data[str(unit)] = tips

    if problem_units:
        print(f"\n [flagged for manual review] Units: {sorted(set(problem_units))}")

    return output_data

# ---------------------------------------------------------
# Step 2: Claude API Reformatting & Matching
# ---------------------------------------------------------
def reformat_grammar_tip_text(reformat_sop_text: str, raw_tip: str) -> str:
    if client is None:
        print(" [error] CLAUDE_API_KEY not configured; skipping API call.")
        return raw_tip

    user_content = (
        f"Here is the raw grammar tip:\n\n{raw_tip}\n\n"
        f"Please reformat this grammar tip according to the system instructions."
    )

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
        return response.content[0].text.strip()
    except Exception as e:
        print(f" [error] API call failed during reformatting: {e}")
        return raw_tip  # fallback to raw tip on error


def get_matching_sentences(sop_text: str, grammar_tip: str, hanzi_list: list) -> list:
    if client is None:
        return []

    # Format the prompt carefully so Claude returns ONLY a valid JSON list
    user_content = (
        f"Here is the Grammar Tip:\n\n{grammar_tip}\n\n"
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
        
        # Clean markdown code blocks if Claude adds them
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
    remove_grammar_tips(units_output_path)
    print("1. Extracting grammar tips from OCR cache...")
    unit_tips = parse_grammar_tips()
    
    if not unit_tips:
        print("No grammar tips extracted. Exiting.")
        return

    print("2. Loading SOPs and clean sentences data...")
    if not sop_path.exists():
        print(f" [error] Matching SOP file not found at {sop_path}")
        return
    with open(sop_path, 'r', encoding='utf-8') as f:
        sop_text = f.read()

    if not reformat_sop_path.exists():
        print(f" [error] Reformat SOP file not found at {reformat_sop_path}")
        return
    with open(reformat_sop_path, 'r', encoding='utf-8') as f:
        reformat_sop_text = f.read()

    if not units_output_path.exists():
        print(f" [error] Clean sentences file not found at {units_output_path}")
        return
    with open(units_output_path, 'r', encoding='utf-8') as f:
        units_data = json.load(f)

    print("3. Reformatting tips and matching to sentences via Claude...")
    for unit_str, tips in unit_tips.items():
        if unit_str not in units_data:
            print(f" [warning] Unit {unit_str} not found in units_output.json. Skipping.")
            continue
            
        sentences_list = units_data[unit_str].get("sentences", [])
        if not sentences_list:
            continue
            
        # Extract just the Hanzi strings for Claude
        hanzi_list = [s["hanzi"] for s in sentences_list]
        print(f"\nProcessing Unit {unit_str} ({len(tips)} tips, {len(hanzi_list)} sentences)...")
        
        for idx, tip in enumerate(tips, 1):
            print(f"  -> Calling Claude to Reformat Tip {idx}...")
            # 1. Reformat the raw tip
            reformatted_tip = reformat_grammar_tip_text(reformat_sop_text, tip)

            print(f"  -> Calling Claude to Match Tip {idx}...")
            # 2. Use the reformatted tip to match sentences
            matched_hanzi = get_matching_sentences(sop_text, reformatted_tip, hanzi_list)
            
            if matched_hanzi:
                print(f"     Found {len(matched_hanzi)} matching sentence(s).")
                # Update the original data structure
                for matched_sentence in matched_hanzi:
                    for s_obj in sentences_list:
                        if s_obj["hanzi"] == matched_sentence:
                            # If multiple tips apply to one sentence, this string concatenation handles it
                            if "grammar_tip" in s_obj and s_obj["grammar_tip"]:
                                s_obj["grammar_tip"] += f"\n\n{reformatted_tip}"
                            else:
                                s_obj["grammar_tip"] = reformatted_tip
            else:
                print("     No matches found.")

    print("\n4. Saving updated sentences back to units_output.json...")
    with open(units_output_path, 'w', encoding='utf-8') as f:
        json.dump(units_data, f, ensure_ascii=False, indent=2)
        
    print(f"✅ Done! Updated {units_output_path.name} successfully.")

if __name__ == "__main__":
    main()