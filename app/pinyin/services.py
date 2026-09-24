# app/pinyin/services.py
"""
Pinyin onboarding logic: level sequencing, unlock state, question/session
generation, and cross-crediting from advanced (non-pinyin) questions.

Mirrors session/services/tier_engine.py's role for the textbook feature,
but pool-based-per-level rather than per-question-type tiers -- see the
design discussion that led here. No Question rows exist for pinyin;
everything is generated on the fly from PinyinSyllable + these tags.

Levels are ordered lists of GROUPS, not flat tag sets: a group unlocks
once the group before it (within the same level) is mastered. This
covers level 1's tone1/4 -> tone2/3 split and level 3's
b/p/d/t/g/k -> j/q/x -> zh/ch/sh/r/z/c/s split with one mechanism.
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

# Each level is an ordered list of groups. Group 0 of a level unlocks as
# soon as the level is reached; each later group unlocks once the group
# before it is mastered.
LEVEL_GROUPS: dict[int, list[set[str]]] = {
    1: [{"tone1", "tone4"}, {"tone2", "tone3"}],
    2: [{"a", "o", "e", "i", "u", "v"}],  # ü represented as "v", per app convention
    3: [
        {"b", "p", "d", "t", "g", "k"},
        {"j", "q", "x"},
        {"zh", "ch", "sh", "r", "z", "c", "s"},
    ],
    4: [{"an", "en", "in", "ang", "eng", "ing", "ong"}],
}

DEMO_SYLLABLE_TAGS = {"m", "a"}
MASTERY_THRESHOLD = 0.85
MIN_ATTEMPTS_FOR_MASTERY = 5

QUESTION_TYPES = ("listening_pinyin", "speaking_pinyin")


def decompose_pinyin(syllable: str) -> tuple[str | None, str]:
    """Split a bare (toneless) syllable into (initial, final).

    initial is None for syllables that are a bare final (e.g. "a", "an", "er").
    NOTE: process_pinyin_answer below is UNUSED now that
    process_pinyin_submission uses split_pinyin_sounds (the shared,
    ü-aware parser) instead. Kept only if something else still calls it --
    otherwise safe to delete along with process_pinyin_answer.
    """
    for initial in INITIALS:
        if syllable.startswith(initial):
            return initial, syllable[len(initial):]
    return None, syllable


def _level_all_tags(level: int) -> set[str]:
    return set().union(*LEVEL_GROUPS[level])


def _is_mastered(row) -> bool:
    if row is None or (row.attempts or 0) < MIN_ATTEMPTS_FOR_MASTERY:
        return False
    return (row.successes or 0) / row.attempts >= MASTERY_THRESHOLD


def get_unlocked_tags(db: Session, user_id: int) -> set[str]:
    progress = pinyin_crud.get_sound_progress_map(db, user_id)
    unlocked = set(progress.keys())
    if unlocked & _level_all_tags(1):
        unlocked |= DEMO_SYLLABLE_TAGS
    return unlocked


def is_level_mastered(db: Session, user_id: int, level: int) -> bool:
    progress = pinyin_crud.get_sound_progress_map(db, user_id)
    return all(_is_mastered(progress.get(t)) for t in _level_all_tags(level))


def get_current_level(db: Session, user_id: int) -> int:
    """First level not yet MASTERED -- distinct from get_unlocked_tags,
    which means 'introduced,' not 'mastered.' Conflating the two caused
    early level-jumping the moment a tag was seeded at 0/0."""
    for level in sorted(LEVEL_GROUPS):
        if not is_level_mastered(db, user_id, level):
            return level
    return max(LEVEL_GROUPS) + 1


def _seed_group(db: Session, user_id: int, group: set[str]) -> None:
    for tag in group:
        pinyin_crud.upsert_sound_progress(db, user_id, tag, correct=False)


def maybe_unlock_next_group(db: Session, user_id: int) -> None:
    """Call every session. Walks the current level's groups in order,
    seeding the first one not yet introduced whose predecessor is
    mastered. Only unlocks one group per call."""
    level = get_current_level(db, user_id)
    if level not in LEVEL_GROUPS:
        return
    progress = pinyin_crud.get_sound_progress_map(db, user_id)
    groups = LEVEL_GROUPS[level]

    for i, group in enumerate(groups):
        introduced = bool(group & set(progress.keys()))
        if not introduced:
            if i == 0:
                _seed_group(db, user_id, group)
            else:
                prev_group = groups[i - 1]
                if all(_is_mastered(progress.get(t)) for t in prev_group):
                    _seed_group(db, user_id, group)
            return


def maybe_advance_level(db: Session, user_id: int) -> None:
    """Once every group in the current level is mastered, get_current_level
    naturally reports the next level -- this seeds that level's first
    group so questions can actually be generated from it."""
    level = get_current_level(db, user_id)
    if level not in LEVEL_GROUPS:
        return
    progress = pinyin_crud.get_sound_progress_map(db, user_id)
    first_group = LEVEL_GROUPS[level][0]
    if not (first_group & set(progress.keys())):
        _seed_group(db, user_id, first_group)


def _is_tone_tag(tag: str) -> bool:
    return tag.startswith("tone") and tag[4:].isdigit()


def pick_target_tag(db: Session, user_id: int) -> str:
    progress = pinyin_crud.get_sound_progress_map(db, user_id)
    unlocked = set(progress.keys())
    if not unlocked:
        raise ValueError("No unlocked tags yet -- call generate_pinyin_session first")

    def score(tag):
        row = progress[tag]
        return (row.successes or 0) / max(row.attempts or 1, 1)

    min_score = min(score(t) for t in unlocked)
    weakest = [t for t in unlocked if score(t) == min_score]
    return random.choice(weakest)


def generate_pinyin_question(db: Session, textbook_db: Session, user_id: int) -> dict:
    unlocked = get_unlocked_tags(db, user_id)
    target_tag = pick_target_tag(db, user_id)

    if _is_tone_tag(target_tag):
        tone = int(target_tag[4:])
        candidates = pinyin_crud.get_syllables_by_tone_within_unlocked(textbook_db, tone, unlocked)
    else:
        tone = random.randint(1, 4)
        candidates = pinyin_crud.get_syllables_matching_tags(textbook_db, target_tag, tone, unlocked)

    if not candidates:
        candidates = pinyin_crud.get_any_syllable_within_unlocked(textbook_db, unlocked)
    if not candidates:
        raise ValueError(f"No syllables available for user {user_id} given unlocked tags {unlocked}")

    syllable_row = random.choice(candidates)
    question_type = random.choice(QUESTION_TYPES)
    numbered_pinyin = f"{syllable_row.syllable}{syllable_row.tone}"

    return {
        "id": f"pinyin:{syllable_row.syllable}:{syllable_row.tone}:{question_type}",
        "question_type": question_type,
        "question": numbered_pinyin,   # what's displayed/spoken -- e.g. "shu1"
        "answer": numbered_pinyin,     # what the response is graded against
        "tags": [],                    # no vocab tags for pinyin
        "target_tag": target_tag,      # extra field, harmless -- QuestionBase doesn't forbid extras
    }


def generate_pinyin_session(db: Session, textbook_db: Session, user_id: int, num_questions: int = 10) -> list[dict]:
    if not get_unlocked_tags(db, user_id):
        maybe_advance_level(db, user_id)  # seeds level 1's first group for a fresh user
    maybe_unlock_next_group(db, user_id)
    maybe_advance_level(db, user_id)
    return [generate_pinyin_question(db, textbook_db, user_id) for _ in range(num_questions)]


def process_pinyin_submission(
    db: Session,
    user_id: int,
    list_of_question_data: list[dict],
    is_correct: list[bool],
) -> dict:
    """PATCH /api/submit/pinyin path (routed through process_submission).
    Every answer credits sound_progress for that syllable's initial,
    final, and tone. Uses the real is_correct (not a forced-true speaking
    value) -- level-gating needs honest signal.
    """
    for i, q in enumerate(list_of_question_data):
        numbered_pinyin = q["question"]   # e.g. "shu1" -- question == answer for pinyin's own questions
        tone = int(numbered_pinyin[-1])
        bare_syllable = numbered_pinyin[:-1]
        correct = is_correct[i]

        for initial, final, _ in split_pinyin_sounds(bare_syllable):
            if initial:
                record_sound_attempt(db, user_id, initial, correct)
            record_sound_attempt(db, user_id, final, correct)

        record_sound_attempt(db, user_id, f"tone{tone}", correct)

    return {"user_id": user_id}


def get_pinyin_progress(db: Session, user_id: int) -> dict:
    """What the user sees: unlocked sounds grouped by category, for
    reference -- not level number, since level is an internal gating
    concept, not user-facing."""
    progress = pinyin_crud.get_sound_progress_map(db, user_id)
    consonant_tags = _level_all_tags(3)
    tones, vowels, consonants = [], [], []
    for tag, row in progress.items():
        entry = {"tag": tag, "attempts": row.attempts, "successes": row.successes}
        if tag.startswith("tone"):
            tones.append(entry)
        elif tag in consonant_tags:
            consonants.append(entry)
        else:
            vowels.append(entry)
    return {
        "tones": sorted(tones, key=lambda x: x["tag"]),
        "vowels": sorted(vowels, key=lambda x: x["tag"]),
        "consonants": sorted(consonants, key=lambda x: x["tag"]),
        "graduated": get_current_level(db, user_id) > max(LEVEL_GROUPS),
    }