# app/pinyin/services.py
"""
Pinyin onboarding logic: level sequencing, unlock state, question/session
generation, and cross-crediting from advanced (non-pinyin) questions.

Mirrors session/services/tier_engine.py's role for the textbook feature,
but pool-based-per-level rather than per-question-type tiers -- see the
design discussion that led here. No Question rows exist for pinyin;
everything is generated on the fly from PinyinSyllable + these tags.
"""
import random
from sqlalchemy.orm import Session

from app.pinyin import crud as pinyin_crud
from app.session.crud import record_sound_attempt
from app.pinyin_utils import split_pinyin_sounds

INITIALS = sorted(
    ["zh", "ch", "sh", "b", "p", "m", "f", "d", "t", "n", "l", "g", "k",
     "h", "j", "q", "x", "r", "z", "c", "s", "y", "w"],
    key=len, reverse=True,
)

TONES = (1, 2, 3, 4)

LEVEL_TAGS: dict[int, set[str]] = {
    1: {"tone1", "tone2", "tone3", "tone4"},
    2: {"a", "o", "e", "i", "u", "u:"},  # simple finals; "u:" placeholder for ü, adjust to your convention
    3: {"b", "p", "m", "f", "d", "t", "n", "l", "g", "k", "h",
        "j", "q", "x", "zh", "ch", "sh", "r", "z", "c", "s", "y", "w"},
    4: {"an", "en", "in", "ang", "eng", "ing", "ong"},  # extend with your full compound-final list
}

QUESTION_TYPES = ("listening_pinyin", "speaking_pinyin")

def decompose_pinyin(syllable: str) -> tuple[str | None, str]:
    """Split a bare (toneless) syllable into (initial, final).

    initial is None for syllables that are a bare final (e.g. "a", "an", "er").
    This is the SAME function pinyin_engine.py should reuse at grading time
    to decompose a user's answered pinyin for cross-crediting sound_progress
    -- don't reimplement the split logic twice.
    """
    for initial in INITIALS:
        if syllable.startswith(initial):
            return initial, syllable[len(initial):]
    return None, syllable


def get_unlocked_tags(db: Session, user_id: int) -> set[str]:
    """A tag is 'unlocked' the moment its level has been reached -- not
    the moment it's been individually drilled. See seed_level below."""
    progress = pinyin_crud.get_sound_progress_map(db, user_id)
    return set(progress.keys())


def get_current_level(db: Session, user_id: int) -> int:
    unlocked = get_unlocked_tags(db, user_id)
    for level in sorted(LEVEL_TAGS):
        if not LEVEL_TAGS[level].issubset(unlocked):
            return level
    return max(LEVEL_TAGS) + 1  # all levels unlocked -> "graduated" pinyin


def seed_level(db: Session, user_id: int, level: int) -> None:
    """Called when a user first reaches a level: creates zero-state
    SoundProgress rows for every tag in that level so they immediately
    count as 'seen' (available as the non-target part of a syllable),
    per the earlier decision that unlock = introduced, not drilled."""
    for tag in LEVEL_TAGS[level]:
        pinyin_crud.upsert_sound_progress(db, user_id, tag, correct=False)
        # upsert_sound_progress increments attempts, which isn't quite right
        # for a pure "introduce" event -- consider a separate crud fn that
        # inserts at attempts=0 without incrementing, if that distinction matters.


def pick_target_tag(db: Session, user_id: int) -> tuple[str, int]:
    """Weakest/due tag selection -- same shape as review_engine's due-item
    selection, applied to sound_progress instead of strength_table.
    Returns (tag, tone) -- tone is only meaningful when tag == a level-1 tone tag;
    otherwise a tone is chosen separately by generate_pinyin_question.
    """
    progress = pinyin_crud.get_sound_progress_map(db, user_id)
    unlocked = set(progress.keys())
    scored = sorted(
        unlocked,
        key=lambda t: (progress[t].successes or 0) / max(progress[t].attempts or 1, 1),
    )
    weakest = scored[0] if scored else None
    if weakest is None:
        raise ValueError("No unlocked tags yet -- seed level 1 for this user first")
    return weakest, random.randint(1, 4)


def generate_pinyin_question(db: Session, textbook_db: Session, user_id: int) -> dict:
    target_tag, tone = pick_target_tag(db, user_id)
    unlocked = get_unlocked_tags(db, user_id)
    candidates = pinyin_crud.get_syllables_matching_tags(textbook_db, target_tag, tone, unlocked)
    if not candidates:
        # target tag has no legal syllable yet given current unlocks --
        # fall back to a random unlocked tag instead of raising.
        target_tag, tone = random.choice(list(unlocked)), random.randint(1, 4)
        candidates = pinyin_crud.get_syllables_matching_tags(textbook_db, target_tag, tone, unlocked)

    syllable = random.choice(candidates)
    question_type = random.choice(QUESTION_TYPES)
    return {
        "question_id": f"pinyin:{syllable.syllable}:{syllable.tone}:{question_type}",
        "question_type": question_type,
        "syllable": syllable.syllable,
        "tone": syllable.tone,
        "target_tag": target_tag,
    }


def generate_pinyin_session(db: Session, textbook_db: Session, user_id: int, num_questions: int = 10) -> list[dict]:
    current_level = get_current_level(db, user_id)
    if not get_unlocked_tags(db, user_id):
        seed_level(db, user_id, 1)
    return [generate_pinyin_question(db, textbook_db, user_id) for _ in range(num_questions)]


def process_pinyin_answer(db: Session, user_id: int, syllable: str, tone: int, correct: bool) -> None:
    initial, final = decompose_pinyin(syllable)
    if initial:
        pinyin_crud.upsert_sound_progress(db, user_id, initial, correct)
    pinyin_crud.upsert_sound_progress(db, user_id, final, correct)
    pinyin_crud.upsert_sound_progress(db, user_id, f"tone{tone}", correct)

def process_pinyin_submission(
    db: Session,
    user_id: int,
    list_of_question_data: list[dict],
    is_correct: list[bool],
) -> dict:
    """PATCH /api/submit/pinyin. Unlike process_submission, there are no
    tags/tiers/unit-tests here -- every question maps to exactly one
    syllable, and every answer credits sound_progress directly for that
    syllable's initial, final, and tone.

    Speaking-question forgiveness: session completion always succeeds (no
    points lost for accent, matching the existing speaking-sentence
    behavior), but sound_progress crediting uses the REAL Azure-scored
    is_correct, not a forced-true value -- level-gating needs honest signal
    or the pinyin unit can't do its job. Confirm this split is intended.
    """
    for i, q in enumerate(list_of_question_data):
        syllable = q["syllable"]   # e.g. "shu", toneless
        tone = q["tone"]           # e.g. 1
        correct = is_correct[i]

        for initial, final, _ in split_pinyin_sounds(syllable):
            if initial:
                record_sound_attempt(db, user_id, initial, correct)
            record_sound_attempt(db, user_id, final, correct)

        record_sound_attempt(db, user_id, f"tone{tone}", correct)

    return {"user_id": user_id}


def get_pinyin_progress(db: Session, user_id: int) -> dict:
    """What the user sees: unlocked sounds grouped by category, for
    reference -- not level number, per the decision that level is an
    internal gating concept, not user-facing."""
    progress = pinyin_crud.get_sound_progress_map(db, user_id)
    tones, vowels, consonants = [], [], []
    for tag, row in progress.items():
        entry = {"tag": tag, "attempts": row.attempts, "successes": row.successes}
        if tag.startswith("tone"):
            tones.append(entry)
        elif tag in LEVEL_TAGS[3]:
            consonants.append(entry)
        else:
            vowels.append(entry)
    return {
        "tones": sorted(tones, key=lambda x: x["tag"]),
        "vowels": sorted(vowels, key=lambda x: x["tag"]),
        "consonants": sorted(consonants, key=lambda x: x["tag"]),
        "graduated": get_current_level(db, user_id) > max(LEVEL_TAGS),
    }