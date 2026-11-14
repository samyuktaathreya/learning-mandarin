"""
"Which word does this character belong to?" questions.

Problem this targets: a learner memorizes 介绍 as one picture and can't
recognize 介 or 绍 on its own. We show a single character from a word
they've been taught and ask them to type that word.

Shape:
  - target: one character drawn from the user's taught words of 2+ characters
  - answer: free text; any taught word containing the character is correct
    (学 -> 学生 or 学习), so `accepted_answers` is the source of truth
  - tag: the character itself, on the shared "character" facet. A strong
    strength there already means "recognizes this character alone", which
    is exactly what this question tests, so sharing the tag is intended.

Needs no characters.db: everything comes from the textbook vocab and
StrengthTable. Questions are built live, not stored, because the correct
answers depend on what each user has been taught.
"""

import random
import re
from collections import defaultdict
from collections.abc import Iterable

from sqlalchemy.orm import Session

from app.characters.services.shared import REVIEW_STRENGTH_THRESHOLD, get_character_strengths

QUESTION_TYPE = "character_to_word"

# A character in more taught words than this (是 in 但是/还是/就是/可是...)
# makes the question too easy to guess, so those are asked last.
MAX_WORDS_PER_CHAR = 3

# Whole string must be CJK ideographs: drops entries with pinyin, spaces,
# punctuation or "(儿)"-style notes, so we never ask the user to type those.
_HANZI_ONLY = re.compile(
    r"^[㐀-䶿一-鿿豈-﫿\U00020000-\U0003134f]+$"
)


# ---------------------------------------------------------------------------
# Pure helpers (no DB -- easy to unit test)
# ---------------------------------------------------------------------------

def build_char_to_words(words: Iterable[str]) -> dict[str, list[str]]:
    """{character: sorted taught words of 2+ characters that contain it}."""
    index: dict[str, set[str]] = defaultdict(set)
    for w in words:
        if not w:
            continue
        w = w.strip()
        if len(w) < 2 or not _HANZI_ONLY.match(w):
            continue
        for ch in set(w):
            index[ch].add(w)
    return {ch: sorted(ws) for ch, ws in index.items()}


def pick_target_chars(
    char_to_words: dict[str, list[str]],
    strengths: dict[str, float],
    n: int,
    exclude: Iterable[str] = (),
) -> list[str]:
    """Up to n characters to ask about, weakest first.

    Characters already at/above the review threshold are skipped. Characters
    never practiced count as strength 0. Characters spread across many words
    go to the back of the line. Ties are broken randomly so the same few
    characters don't come up every session.
    """
    exclude = set(exclude)
    candidates = [
        ch for ch in char_to_words
        if ch not in exclude and strengths.get(ch, 0.0) < REVIEW_STRENGTH_THRESHOLD
    ]
    random.shuffle(candidates)  # random tie-break; sort below is stable
    candidates.sort(key=lambda ch: (
        len(char_to_words[ch]) > MAX_WORDS_PER_CHAR,
        strengths.get(ch, 0.0),
    ))
    return candidates[:n]


def build_character_to_word_question(char: str, words: list[str]) -> dict:
    return {
        "id": f"charquiz_word_{char}_{random.randint(0, 999999)}",
        "question_type": QUESTION_TYPE,
        "question": f"Type a word you've learned that contains {char}",
        "prompt_char": char,
        "answer": words[0],            # one example, for display after answering
        "accepted_answers": words,     # grade against this
        "tags": [char],
    }


def is_correct(question: dict, response: str) -> bool:
    """Grade a typed response: whitespace is ignored, any accepted word counts."""
    cleaned = re.sub(r"\s+", "", response or "")
    return cleaned in question["accepted_answers"]


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def generate_character_to_word_questions(
    db: Session,
    user_id: int,
    allowed_tags: set[str] | None,
    num_questions: int,
    exclude_tags: Iterable[str] = (),
) -> list[dict]:
    """
    Build up to `num_questions` character -> word questions.

    `allowed_tags` is the taught-vocab set from
    curriculum.get_allowed_curriculum. If it's None (user's progress is
    unknown) we return nothing: without knowing what they've learned there's
    no way to say which answers are fair.
    """
    if not allowed_tags or num_questions <= 0:
        return []

    char_to_words = build_char_to_words(allowed_tags)
    strengths = get_character_strengths(db, user_id)
    targets = pick_target_chars(char_to_words, strengths, num_questions, exclude_tags)
    return [build_character_to_word_question(ch, char_to_words[ch]) for ch in targets]