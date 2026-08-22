You are given a JSON of fill-in-the-blank sentences.

1. **Look at the "full_sentences" attribute.** If this Chinese sentence is gramatically incorrect, drop it.
**Example:**
```json
{
    "question": "你们好，你们是___学生吗？ (Hello, are you students?)",
    "answer": "什么",
    "full_sentence": "你们好，你们是什么学生吗？",
    "source": "workbook"
}

```


"你们好，你们是什么学生吗?" is not gramatically correct. Drop this question.
2. **Now look at "question" attribute.** If the English translation doesn't match the hanzi, drop it.
**Example:**
```json
{
    "question": "我女儿是___，她不在医院。 My daughter is a doctor. She does not work in a hospital.",
    "answer": "医生",
    "full_sentence": "我女儿是医生，她不在医院。",
    "source": "textbook"
}

```


Drop this sentence because 她不在医院 does not mean "she does not work in a hospital."
3. **Return a JSON in the same format as the input.** Don't include any commentary or other reasoning, just the JSON.