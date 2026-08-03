This documentation outlines the schema and purpose of four data files used to structure a Chinese language learning curriculum (likely aligned with HSK1 based on the filenames and vocabulary). The data is organized around textbook "units" and includes sentences, vocabulary lists, practice questions, and a master dictionary.

---

### 1. `units_output.json`

**Purpose:** Stores full sentences taught in each textbook unit, broken down by characters, translation, and pronunciation. This is ideal for reading practice, sentence comprehension, or building flashcards.

**Structure:** A JSON object where the keys are the **Unit Numbers** (as strings) and the values contain arrays of sentence objects.

**Schema Details:**

* `[Unit Number]` (String): The key representing the textbook unit (e.g., `"3"`).
* `sentences` (Array of Objects): A list of sentences taught in that unit.
* `hanzi` (String): The full sentence written in Chinese characters.
* `english` (String): The English translation of the sentence.
* `tags` (Array of Strings): The individual vocabulary words or characters that make up the sentence, used for tracking which words appear in which sentences.
* `pinyin` (String): The phonetic transcription of the sentence using numerical tones (e.g., `ni3` instead of `nǐ`).





---

### 2. `unit_vocabs_tag.json`

**Purpose:** Acts as a simple mapping of textbook units to their corresponding vocabulary words. This is useful for grouping words by lesson or generating unit-specific study lists.

**Structure:** A JSON object where the keys are **Unit Numbers** (as strings) and the values are arrays of vocabulary words.

**Schema Details:**

* `[Unit Number]` (String): The key representing the unit (e.g., `"1"`, `"2"`, `"3"`).
* `[Value]` (Array of Strings): A flat list of Chinese characters/words (Hanzi) introduced in that specific unit (e.g., `["中国", "人", "什么", ...]`).



---

### 3. `unit_questions_hsk1.json`

**Purpose:** A comprehensive question bank for testing HSK1 vocabulary. It contains dynamically generated questions covering different language skills (listening, speaking, reading, writing/transcribing).

**Structure:** A JSON object where the keys are **Unit Numbers** (as strings) and the values are arrays of question objects.

*Note: As specified, there are 11 different types of questions included in this dataset.*

**Schema Details:**

* `[Unit Number]` (String): The key representing the unit (e.g., `"1"`).
* `[Array of Objects]`: A list of questions belonging to that unit.
* `id` (String): A unique identifier for the question (format: `u[unit]_[question_type]_[index]`).
* `question_type` (String): The category of the exercise. Examples include:
* `listening vocab`
* `speaking vocab`
* `translate chinese word to english`
* `translate english word to chinese`
* `transcribe word to pinyin`
* `transcribe hanzi to pinyin`


* `question` (String): The prompt shown to the user (can be Hanzi or English depending on the `question_type`).
* `answer` (String): The expected correct answer (can be Pinyin with numerical tones, English, or Hanzi).
* `tags` (Array of Strings): Metadata tags for filtering, which include the question type, the unit number, and the specific Hanzi characters being tested.
* `unit` (Integer): The numerical unit number (e.g., `1`).
* `hanzi` (String): The base Chinese word being tested in the question.
* `english` (String): The English translation of the base Chinese word.





---

### 4. `index_output.json`

**Purpose:** Serves as a master glossary or dictionary for the entire curriculum. It aggregates all vocabulary words across all units into a single searchable index.

**Structure:** A JSON object containing a `vocab` key, which holds an array of all vocabulary objects.

**Schema Details:**

* `vocab` (Array of Objects): The master list of all vocabulary words.
* `hanzi` (String): The Chinese character or word (e.g., `"爱"`).
* `pinyin` (String): The phonetic transcription using numerical tones (e.g., `"ai4"`).
* `english` (String): The English meaning or translation (e.g., `"to like, to love"`).
* `unit` (Integer): The integer representing the specific textbook unit where this word is first introduced (e.g., `12`).