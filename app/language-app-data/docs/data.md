# Mandarin App — Data Pipeline & Schemas

## Pipeline

Run the full pipeline with:
```bash
cd language-app-data/scripts
python3 main.py
```

Or run individual steps selectively (e.g. after editing an SOP or fixing data):
```bash
python3 fill_template_sentences.py   # re-fill templates only
python3 create_questions.py          # regenerate question bank only
python3 build_dictionary.py          # regenerate dictionary only
```

After any pipeline run, delete the DB and restart uvicorn:
```bash
rm mandarin_app.db
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

### Scripts

| Script | What it does | Claude calls |
|---|---|---|
| `main.py` | Orchestrates all four steps in order | — |
| `parse_textbook.py` | OCR + JSON structuring of the textbook PDF | 2 per unit (OCR, JSON) |
| `fill_template_sentences.py` | Fills exercise template sentences with real content | 1 per unit |
| `create_questions.py` | Generates the full question bank from `units_output.json` | — |
| `build_dictionary.py` | Builds the flat word lookup dictionary from `units_output.json` | — |
| `add_grammar_pinyin.py` | One-time patch: adds pinyin to grammar markers in `units_output.json` | — |

All Claude calls use **prompt caching** on the system prompt (SOP). The SOP is sent once on the first unit and cached for subsequent units, saving ~90% of input token costs for units 4–15.

### SOPs

| File | Used by |
|---|---|
| `SOPs/ocr_transcription.txt` | `parse_textbook.py` — OCR step |
| `SOPs/textbook_parsing.txt` | `parse_textbook.py` — JSON structuring step |
| `SOPs/template_filling.txt` | `fill_template_sentences.py` — template sentence filling |

### Data flow

```
data/raw/hsk1_textbook.pdf
        │
        │  parse_textbook.py
        │  (OCR SOP + JSON structuring SOP, 2 Claude calls per unit)
        ▼
data/raw/raw_transcriptions/unit_N_raw.txt   (plain text, for inspection)
data/clean/units_output.json                 (structured JSON)
        │
        │  fill_template_sentences.py
        │  (template filling SOP, 1 Claude call per unit)
        ▼
data/clean/units_output.json                 (updated with filled sentences)
        │
        ├─ create_questions.py ──────────────▶ data/clean/unit_questions_hsk1.json
        │
        └─ build_dictionary.py ──────────────▶ data/clean/hsk1_dictionary.json
```

---

## `units_output.json` schema

One object per unit, keyed by unit number as a string.

### `vocab`
```json
{
  "hanzi": "朋友",
  "pinyin": "peng2you5",
  "english": "friend",
  "part_of_speech": "noun",
  "measure_word": "个"
}
```
- `part_of_speech`: `noun | verb | adjective | adverb | pronoun`
- `measure_word`: `null` if not a noun or no clear measure word applies
- Particles are never listed here — they belong only in `grammar`

### `grammar`
```json
{
  "marker": "了",
  "pinyin": "le5",
  "english": "indicates a completed action",
  "type": "aspect_particle"
}
```
- `pinyin`: conversational pronunciation; neutral tone particles use `5`
- `type`: `aspect_particle | sentence_particle | structural_particle | conjunction | coverb_preposition | modal_verb | ba_construction | bei_construction | resultative_complement | directional_complement | potential_complement | degree_complement | comparative_construction | tone_sandhi | other`

### `proper_nouns`
```json
{
  "hanzi": "中国",
  "pinyin": "Zhong1guo2",
  "english": "China"
}
```
- Place names and person names that appear in the unit but are not formal vocab items
- Get listening vocab, speaking vocab, and transcribe to pinyin questions

### `sentences`
```json
{
  "hanzi": "我喝了一杯水。",
  "pinyin": "Wo3 he1 le5 yi1 bei1 shui3.",
  "english": "I drank a cup of water."
}
```
- Includes both textbook dialogue sentences and filled template sentences
- Template sentences (blanks, slashes, ellipses) are excluded from this list and handled by `fill_template_sentences.py`

### `fill_in_the_blank`
```json
{
  "question": "我___一杯水。(I drank a cup of water.)",
  "answer": "了",
  "tags": ["了"]
}
```
- `answer` is a verbatim `grammar.marker` or a vocab item's `measure_word`
- Non-blanked text matches the source sentence exactly, character for character

**Pinyin format throughout:** no accent marks, tone as a trailing number (`peng2you5`), neutral tone = `5`, `ü` written as `v`, tones reflect conversational pronunciation.

---

## `hsk1_dictionary.json` schema

Flat dictionary keyed by hanzi. Used for instant in-app character/word lookup.

```json
{
  "朋友": {
    "pinyin": "peng2you5",
    "english": "friend",
    "type": "vocab",
    "unit": 5
  },
  "了": {
    "pinyin": "le5",
    "english": "indicates a completed action",
    "type": "grammar",
    "unit": 6
  },
  "中国": {
    "pinyin": "Zhong1guo2",
    "english": "China",
    "type": "proper_noun",
    "unit": 3
  }
}
```
- `type`: `vocab | grammar | proper_noun`
- `unit`: first unit this word appears in; first-seen wins

---

## `unit_questions_hsk1.json` schema

One array per unit, keyed by unit number as a string.

```json
{
  "3": [
    {
      "id": "u3_fill_in_the_blank_1",
      "question_type": "fill in the blank",
      "question": "我___一杯水。(I drank a cup of water.)",
      "answer": "了",
      "tags": ["了"]
    },
    {
      "id": "u3_listening_vocab_1",
      "question_type": "listening vocab",
      "question": "朋友",
      "answer": "peng2you5",
      "tags": ["朋友"]
    }
  ]
}
```

- `id` pattern: `u{unit}_{question_type_underscored}_{n}`
- `tags`: vocab/grammar/proper noun items the question was generated from — used for SRS strength tracking

### Question types

| `question_type` | Prompt | Answer | Tests |
|---|---|---|---|
| `fill in the blank` | Sentence with particle/measure word blanked + English | The blanked word | Particle/measure word usage |
| `listening vocab` | Audio of a word | Typed pinyin + tone | Tone perception by ear |
| `listening sentence` | Audio of a sentence | Chinese characters (dictation) | Listening + character recall |
| `speaking vocab` | Word (hanzi) | Spoken aloud (Azure STT graded) | Pronunciation |
| `speaking sentence` | Sentence (hanzi) | Spoken aloud (Azure STT graded) | Pronunciation at speed |
| `translate english sentence to chinese` | English sentence | Chinese sentence | Production |
| `translate chinese sentence to english` | Chinese sentence | English translation | Comprehension |
| `translate english word to chinese` | English word | Chinese word | Vocab recall (production) |
| `translate chinese word to english` | Chinese word | English meaning | Vocab recall (comprehension) |
| `transcribe word to pinyin` | Chinese word (no audio) | Typed pinyin + tone | Tone recall from character |

### Which source items generate which question types

| Source | Question types generated |
|---|---|
| `vocab` | listening vocab, speaking vocab, translate EN→ZH word, translate ZH→EN word, transcribe to pinyin |
| `grammar` | listening vocab, speaking vocab, transcribe to pinyin |
| `proper_nouns` | listening vocab, speaking vocab, transcribe to pinyin |
| `sentences` | listening sentence, speaking sentence, translate EN→ZH sentence, translate ZH→EN sentence |
| `fill_in_the_blank` | fill in the blank |