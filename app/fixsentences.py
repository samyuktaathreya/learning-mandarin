"""
Fixes combined sentences in unit_questions_hsk1.json by splitting them
into individual sentences and distributing them across questions.
"""

import json

FILEPATH = './language-app-data/data/clean/unit_questions_hsk1.json'

# map combined sentence -> list of sub-sentences to cycle through
SPLITS = {
    "我叫李月，我是中国人，我是老师。": [
        "我叫李月。",
        "我是中国人。",
        "我是老师。",
    ],
    "我叫大卫，我是美国人，我是学生。": [
        "我叫大卫。",
        "我是美国人。",
        "我是学生。",
    ],
}

SENTENCE_QUESTION_TYPES = {
    "listening sentence",
    "translate chinese sentence to english",
    "speaking sentence",
    "translate english sentence to chinese",
}

with open(FILEPATH, 'r', encoding='utf-8') as f:
    data = json.load(f)

# track cycle index per combined sentence so we distribute evenly
cycle_counters = {k: 0 for k in SPLITS}
fixed_count = 0

for unit, questions in data.items():
    for q in questions:
        if q['question_type'] not in SENTENCE_QUESTION_TYPES:
            continue

        question_text = q['question']
        if question_text not in SPLITS:
            continue

        sub_sentences = SPLITS[question_text]
        idx = cycle_counters[question_text] % len(sub_sentences)
        replacement = sub_sentences[idx]
        cycle_counters[question_text] += 1

        print(f"Unit {unit} [{q['question_type']}]: {question_text} -> {replacement}")
        q['question'] = replacement

        # also fix the english answer if it's a translate-to-english question
        # we can't auto-fix the answer perfectly so just flag it
        if q['question_type'] in ('translate chinese sentence to english', 'listening sentence'):
            print(f"  ⚠️  Answer may need manual update: {q.get('answer', '')}")

        fixed_count += 1

with open(FILEPATH, 'w', encoding='utf-8') as f:
    json.dump(data, f, ensure_ascii=False, indent=2)

print(f"\nFixed {fixed_count} questions. File saved.")
print("Note: check English answers for translate/listening sentence questions manually.")