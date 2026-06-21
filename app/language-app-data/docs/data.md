# Mandarin App — Data Pipeline & Schemas

## Pipeline

1. **`scripts/parse_textbook.py`** reads `data/raw/hsk1_textbook.pdf` and, for each unit, makes two Claude calls:
   - **OCR step** (`SOPs/ocr_transcription.txt`) — uploads the unit's pages and gets back a plain-text transcription, saved to `data/clean/raw_transcriptions/unit_N_raw.txt` for inspection.
   - **Structuring step** (`SOPs/textbook_parsing.txt`) — takes that transcription and returns structured JSON (vocab, grammar, sentences, fill-in-the-blank).

   All units are compiled into **`data/clean/units_output.json`**, keyed by unit number.

2. **`scripts/create_questions.py`** reads `units_output.json` and generates the full question bank, written to **`data/clean/unit_questions_hsk1.json`**.

```
data/raw/hsk1_textbook.pdf
        │  (parse_textbook.py)
        ▼
data/clean/units_output.json
        │  (create_questions.py)
        ▼
data/clean/unit_questions_hsk1.json
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

### `sentences`
```json
{
  "hanzi": "我喝了一杯水。",
  "pinyin": "Wo3 he1 le5 yi1 bei1 shui3.",
  "english": "I drank a cup of water."
}
```

### `grammar`
```json
{
  "marker": "了",
  "english": "indicates a completed action",
  "type": "aspect_particle"
}
```
- `type`: `aspect_particle | sentence_particle | structural_particle | conjunction | coverb_preposition | modal_verb | ba_construction | bei_construction | resultative_complement | directional_complement | potential_complement | degree_complement | comparative_construction | tone_sandhi | other`

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

**Pinyin format throughout:** no accent marks, tone as a trailing number (`peng2you5`), neutral tone = `5`, `ü` written as `v`, tones reflect conversational pronunciation rather than citation-form/per-character tones.

---

## Question types (`create_questions.py` output)

| `question_type` | Prompt | Answer | Tests |
|---|---|---|---|
| `fill in the blank` | Sentence with a particle/measure word blanked + English translation | The blanked word | Particle/measure word usage |
| `listening vocab` | Audio of a vocab word | Typed pinyin + tone | Tone perception by ear |
| `listening sentence` | Audio of a sentence | English translation | Listening comprehension |
| `speaking vocab` | Vocab word (hanzi) | Spoken aloud (not tone-graded) | Pronunciation |
| `speaking sentence` | Sentence (hanzi) | Spoken aloud (not tone-graded) | Pronunciation at sentence speed |
| `translate english sentence to chinese` | English sentence | Chinese sentence | Production |
| `translate chinese sentence to english` | Chinese sentence | English translation | Comprehension |
| `translate english word to chinese` | English word | Chinese word | Vocab recall (production) |
| `translate chinese word to english` | Chinese word | English meaning | Vocab recall (comprehension) |
| `transcribe word to pinyin` | Chinese word (no audio) | Typed pinyin + tone | Tone recall from the character |

Speaking types are never auto-graded on tone — STT tends to "correct" tone errors before grading sees them. Tone accuracy is tested only via `listening vocab` and `transcribe word to pinyin`.

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

- `id` pattern: `u{unit}_{question_type with spaces replaced by underscores}_{n}`
- `tags`: the vocab/grammar marker(s) the question was generated from — this is what ties a question back into SRS strength tracking. (Shown as `[]` in early drafts since this hadn't been wired up yet — every generated question should have at least one tag.)