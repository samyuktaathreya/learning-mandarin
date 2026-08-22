GENERAL RULES:

You will be given the full OCR markdown for a textbook unit, and a list of candidate fill-in-the-blank sentences found in that unit. Your job is to decide, for each candidate, whether its blank(s) can be answered with high confidence using only information present in the OCR'd unit text — and if so, provide the answer, the completed sentence, and its English translation.

DECIDING WHETHER TO ANSWER:

1. Use ONLY information available in the provided unit text to determine each blank's answer.
2. If a sentence is open-ended (e.g. the student is meant to fill in their own name or nationality) and there is no single correct answer, do not answer it — drop it.
3. If a sentence has multiple blanks, every blank must be answerable with high confidence from the unit text. If it isn't, drop the entire sentence.
4. If you are not highly confident in the correct answer, drop the sentence entirely.

WORD BANKS — RESOLVE LETTERS TO WORDS (CRITICAL):
Word banks are printed as a letter/number label paired with a word, e.g. "A 几  B 月  C 号  D 名字  E 星期" or "F 问  G 去  H 做  I 会". The label (A, B, C, F, G, H, I, ...) is only an index into the bank — it is NOT the answer. The answer is always the WORD that the label points to.

* When you choose an option from a word bank, look up the WORD paired with that label and emit that WORD as the answer. Never emit the bare letter/number label.
* Example: for the bank "F 问  G 去  H 做  I 会", if the blank is answered by option H, the answer is "做" (NOT "H"). If it is answered by option I, the answer is "会" (NOT "I").
* This applies to every blank in a multi-blank sentence and to "full_sentence_answer": the completed sentence must contain the resolved WORDS, never any bank labels. A completed sentence that still contains a stray letter like "H" or "I" is wrong — re-resolve it.
* A single bank may serve several consecutive questions; resolve each blank against the bank that governs it.

WORKED EXAMPLE 1 (correct behavior — answer, single blank):
Candidate: "李月（___）中国人，她是老师。" printed with word bank "A 什么 B 是 C 不 D 名字 E 吗"
Only option B fits the blank grammatically. Option B points to the word "是", so the answer is the WORD "是" (not the label "B").

Answer:

```json
[
  {
    "fill in the blank": "李月（___）中国人，她是老师。",
    "answer": ["是"],
    "full_sentence_answer": "李月是中国人，她是老师。",
    "translation": "Li Yue is Chinese. She is a teacher."
  }
]

```

WORKED EXAMPLE 2 (correct behavior — answer, multi-blank, letter→word resolution):
Candidate: "我___说 汉语，不___写 汉字。" printed with word bank "F 问 G 去 H 做 I 会"
Both blanks are answered by option I, which points to the word "会". Emit the resolved WORD for each blank, never the label:

```json
[
  {
    "fill in the blank": "我___说 汉语，不___写 汉字。",
    "answer": ["会，会"],
    "full_sentence_answer": "李月是中国人，她是老师。",
    "translation": "I can spreak Chinese, but I cannot write Chinese characters."
  }
]

```

ANSWERING:
7. "answer" is always a list of strings, in the order the blanks appear in the sentence.
8. "full_sentence_answer" is the complete sentence with all blanks filled in with the resolved WORDS.
9. "translation" is the English translation of the completed sentence.
10. Do your reasoning internally. Do not output your reasoning, analysis, or explanation for each candidate — output only the final JSON array. The response must start with "[" and contain nothing else.

OUTPUT FORMAT:
Output a JSON list of objects. Sentences you dropped must not appear in the output at all. If no candidates are answerable, output [].

```json
[
  {
    "fill in the blank": "李月（___）中国人，她是老师。",
    "answer": ["是"],
    "full_sentence_answer": "李月是中国人，她是老师。",
    "translation": "Li Yue is Chinese. She is a teacher."
  }
]

```

Output JSON only. No commentary, preamble, or meta-remarks before or after the JSON.