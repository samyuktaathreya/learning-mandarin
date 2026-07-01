"""
Reads the raw OCR transcriptions (data/raw/raw_transcriptions/unit_N_raw.txt)
and units_output.json, identifies template sentences in the exercise/application
sections, calls Claude to fill them in using only unit-appropriate content,
then adds the filled sentences directly to units_output.json.

Run this after parse_textbook.py and before create_questions.py.
"""

import os
import json
import re
import anthropic
from dotenv import load_dotenv

load_dotenv()

# --------------------------------- CONSTANTS ---------------------------------

RAW_TRANSCRIPTIONS_FILEPATH = "../data/raw/raw_transcriptions"
UNITS_OUTPUT_FILEPATH = "../data/clean"
UNITS_OUTPUT_FILENAME = "units_output.json"

SOP_FILEPATH = "../SOPs"
SOP_FILENAME = "template_filling.txt"

FIRST_UNIT_NUMBER = 3
LAST_UNIT_NUMBER = 15

MODEL = "claude-sonnet-4-6"
MAX_TOKENS = 4096

# set to a list of unit numbers to only process specific units e.g. [3, 4]
# set to None to process all units
UNITS_TO_PROCESS = None

# --------------------------------- SETUP ---------------------------------

api_key = os.environ.get("CLAUDE_API_KEY")
if not api_key:
    raise ValueError("CLAUDE_API_KEY not found in environment.")

client = anthropic.Anthropic(api_key=api_key)

# --------------------------------- HELPERS ---------------------------------

def load_sop(filepath: str, filename: str) -> str:
    with open(os.path.join(filepath, filename), "r", encoding="utf-8") as f:
        return f.read()


def load_raw_transcription(unit_number: int) -> str:
    path = os.path.join(RAW_TRANSCRIPTIONS_FILEPATH, f"unit_{unit_number}_raw.txt")
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def load_units_output() -> dict:
    path = os.path.join(UNITS_OUTPUT_FILEPATH, UNITS_OUTPUT_FILENAME)
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_units_output(data: dict):
    path = os.path.join(UNITS_OUTPUT_FILEPATH, UNITS_OUTPUT_FILENAME)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def has_chinese(text: str) -> bool:
    return bool(re.search(r'[\u4e00-\u9fff]', text))


def has_parenthetical_hint_name(text: str) -> bool:
    return bool(re.search(r'[\u4e00-\u9fff]+[（(][A-Za-z][A-Za-z\s]*[）)]', text))


def is_template_sentence(text: str) -> bool:
    if not has_chinese(text):
        return False
    if len(text.strip()) < 4:
        return False
    if has_parenthetical_hint_name(text):
        return False

    has_blank = "______" in text or "___" in text
    has_ellipsis = "……" in text
    has_slash = bool(re.search(r'[\u4e00-\u9fff]/[\u4e00-\u9fff]', text)) or \
                bool(re.search(r'[\u4e00-\u9fff]／[\u4e00-\u9fff]', text))

    return has_blank or has_ellipsis or has_slash


def extract_exercise_section(transcription: str) -> str:
    """
    Extract just the exercise/application sections from the transcription
    where template sentences appear. This avoids picking up warmup vocab
    matching items which also have blanks.
    
    Looks for sections labeled: 练习, Exercises, 运用, Application, 小组活动, 双人活动
    """
    # section headers that indicate exercise content
    exercise_markers = ['练习', 'Exercises', '运用', 'Application', '小组活动', '双人活动', 'Group Work', 'Pair Work']
    # section headers that indicate we've moved past exercises
    end_markers = ['拼音', 'Pinyin', '汉字', 'Characters', '文化', 'CULTURE']

    lines = transcription.split('\n')
    in_exercise = False
    exercise_lines = []

    for line in lines:
        # check if we're entering an exercise section
        if any(marker in line for marker in exercise_markers):
            in_exercise = True

        # check if we've left the exercise section
        if in_exercise and any(marker in line for marker in end_markers):
            in_exercise = False

        if in_exercise:
            exercise_lines.append(line)

    return '\n'.join(exercise_lines)


def extract_template_sentences(transcription: str) -> list:
    """
    Extract template sentences from the exercise sections only.
    Returns a deduplicated list of template strings.
    """
    exercise_text = extract_exercise_section(transcription)
    
    # if no exercise section found, fall back to full transcription
    if not exercise_text.strip():
        exercise_text = transcription

    templates = []
    seen = set()

    for line in exercise_text.split('\n'):
        line = line.strip()
        if not line:
            continue

        # skip pure pinyin lines (no Chinese characters)
        if not has_chinese(line):
            continue

        # skip lines that are just section headers or instructions
        if len(line) < 6 and not is_template_sentence(line):
            continue

        if is_template_sentence(line):
            # strip leading numbers/bullets like "1 " or "（1）"
            cleaned = re.sub(r'^[（(]?\d+[）)]?\s*', '', line).strip()
            if cleaned and cleaned not in seen:
                templates.append(cleaned)
                seen.add(cleaned)

    return templates


def fill_templates_with_claude(transcription: str, templates: list, sop: str, unit_number: int) -> list:
    """Call Claude to fill in template sentences using only unit content."""
    if not templates:
        return []

    templates_str = "\n".join(f"- {t}" for t in templates)

    prompt = f"""Here is the full transcription for unit {unit_number}:

{transcription}

Here are the template sentences from this unit's exercises that need to be filled in:

{templates_str}

Fill in each template 2-3 times using ONLY vocabulary, names, and numbers explicitly present in the transcription above. Do not use any content not found in this unit."""

    response = client.messages.create(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        system=[
            {
                "type": "text",
                "text": sop,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        messages=[{"role": "user", "content": prompt}]
    )

    raw = response.content[0].text.strip()

    # strip markdown code fences if present
    if raw.startswith("```"):
        raw = raw.strip("`")
        if raw.startswith("json"):
            raw = raw[4:]
        raw = raw.strip()

    try:
        filled = json.loads(raw)
        if isinstance(filled, list):
            return filled
        return []
    except json.JSONDecodeError:
        debug_path = f"failed_template_unit{unit_number}.txt"
        with open(debug_path, "w", encoding="utf-8") as f:
            f.write(raw)
        print(f"  Failed to parse JSON — saved raw response to {debug_path}")
        return []


def dedup_sentences(existing: list, new_sentences: list) -> list:
    """Add new sentences, skipping any whose hanzi already exists. Returns list of added hanzi."""
    existing_hanzi = {s["hanzi"] for s in existing}
    added = []
    for s in new_sentences:
        if s.get("hanzi") and s["hanzi"] not in existing_hanzi:
            existing.append(s)
            existing_hanzi.add(s["hanzi"])
            added.append(s["hanzi"])
    return added


# --------------------------------- MAIN ---------------------------------

def main():
    sop = load_sop(SOP_FILEPATH, SOP_FILENAME)
    units_output = load_units_output()

    units = UNITS_TO_PROCESS or list(range(FIRST_UNIT_NUMBER, LAST_UNIT_NUMBER + 1))

    for unit_number in units:
        print(f"\nUnit {unit_number}:")

        transcription = load_raw_transcription(unit_number)
        if not transcription:
            print(f"  No transcription found at unit_{unit_number}_raw.txt, skipping.")
            continue

        templates = extract_template_sentences(transcription)
        if not templates:
            print(f"  No template sentences found in exercise sections.")
            continue

        print(f"  Found {len(templates)} template(s):")
        for t in templates:
            print(f"    {t}")

        filled = fill_templates_with_claude(transcription, templates, sop, unit_number)
        if not filled:
            print(f"  Claude returned no filled sentences.")
            continue

        unit_data = units_output.get(str(unit_number), {})
        existing_sentences = unit_data.get("sentences", [])
        added = dedup_sentences(existing_sentences, filled)
        unit_data["sentences"] = existing_sentences
        units_output[str(unit_number)] = unit_data

        print(f"  Added {len(added)} new sentence(s):")
        for s in added:
            print(f"    {s}")

        # save after each unit so a crash doesn't lose everything
        save_units_output(units_output)

    print(f"\nDone. Now run create_questions.py to regenerate the question bank.")


if __name__ == "__main__":
    main()