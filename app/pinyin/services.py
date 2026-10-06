# app/pinyin/services.py
"""
Pinyin onboarding logic: level sequencing, unlock state, question/session
generation, and cross-crediting from advanced (non-pinyin) questions.

Mirrors session/services/tier_engine.py's role for the textbook feature,
but pool-based-per-level rather than per-question-type tiers -- see the
design discussion that led here. No Question rows exist for pinyin;
everything is generated on the fly from pinyinSyllable + these tags.

Levels are ordered lists of GROUPS, not flat tag sets: a group unlocks
once the group before it (within the same level) is mastered. This
covers level 1's tone1/4 -> tone2/3 split and level 3's
b/p/d/t/g/k -> j/q/x -> zh/ch/sh/r/z/c/s split with one mechanism.

Tone pairs are a separate track on top of that. Each pair of tones gets its
own progress tag ("pair:2-4"), stored in sound_progress next to the sound
tags, and unlocks once both of its tones are mastered. Pair questions use a
real two-character textbook word (pinyin_words) when one fits the learner's
unlocked sounds, and two generated syllables otherwise. Pair tags never
count toward group unlocking or levels.

The neutral (fifth) tone is tracked as "tone5" and shown in progress, but it
is practice-only: it appears only as the second syllable of speaking pair
questions, never in listening, and never counts toward unlocking or levels.
"""
import random
from itertools import product
from sqlalchemy.orm import Session

from pinyin import crud as pinyin_crud
from session.crud import record_sound_attempt
from pinyin_utils import split_pinyin_sounds

INITIALS = sorted(
    ["zh", "ch", "sh", "b", "p", "m", "f", "d", "t", "n", "l", "g", "k",
     "h", "j", "q", "x", "r", "z", "c", "s", "y", "w"],
    key=len, reverse=True,
)

MAX_ATTEMPTS_PER_SLOT = 3

TONES = (1, 2, 3, 4)
NEUTRAL_TONE = 5
NEUTRAL_TONE_TAG = f"tone{NEUTRAL_TONE}"

# Each level is an ordered list of groups. Group 0 of a level unlocks as
# soon as the level is reached; each later group unlocks once the group
# before it is mastered.
LEVEL_GROUPS: dict[int, list[set[str]]] = {
    1: [{"tone1", "tone4"}, {"tone2", "tone3"}],
    2: [{"a", "o", "e", "i", "u", "v"}],  # ü represented as "v", per app convention
    3: [
        {"b", "p", "m", "f"},
        {"d", "t", "n", "l"},
        {"g", "k", "h"},
        {"j", "q", "x"},
        {"zh", "ch", "sh", "r", "z", "c", "s"},
    ],
    4: [{"an", "en", "in", "ang", "eng", "ing", "ong"}],
}

# Every group in teaching order, levels flattened. Unlocking walks this list
# and ignores level boundaries; levels are still used for `graduated`.
ORDERED_GROUPS: list[set[str]] = [
    group for level in sorted(LEVEL_GROUPS) for group in LEVEL_GROUPS[level]
]

# How many introduced-but-unmastered tags a learner can have before new
# material is held back. 1 = "only one straggler left, bring in the next group".
MAX_UNMASTERED_BEFORE_UNLOCK = 1

DEMO_SYLLABLE_TAGS = {"m", "a"}
MASTERY_THRESHOLD = 0.80
MIN_ATTEMPTS_FOR_MASTERY = 5

QUESTION_TYPES = ("listening_pinyin", "speaking_pinyin")

# --- Tone-pair (two-syllable) questions ---
PAIR_TAG_PREFIX = "pair:"
PAIR_QUESTION_TYPES = ("listening_pinyin_pair", "speaking_pinyin_pair")
# Share of session slots that are two-syllable, once any pair is unlocked.
MULTI_SYLLABLE_SHARE = 0.3
# When a real textbook word fits, how often to use it over generated syllables.
# Below 1.0 because some pairs only have one or two words and would repeat.
REAL_WORD_SHARE = 0.7
# Share of SPEAKING pair questions that swap in a neutral-tone word (谢谢).
# Neutral-tone words never appear in listening questions.
NEUTRAL_WORD_SHARE = 0.25
# For generated pairs with two different tones: how often to use the same
# syllable twice ("ma1 ma3") so only the tone changes.
SAME_SYLLABLE_SHARE = 0.5


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


def _is_pair_tag(tag: str) -> bool:
    return tag.startswith(PAIR_TAG_PREFIX)


def _is_sound_tag(tag: str) -> bool:
    """A tag single-syllable questions can be built from and that counts
    toward unlocking. Excludes the two tracking-only kinds: pair tags and
    the neutral tone (no single syllable has it)."""
    return not _is_pair_tag(tag) and tag != NEUTRAL_TONE_TAG


def _pair_tag(tone1: int, tone2: int) -> str:
    return f"{PAIR_TAG_PREFIX}{tone1}-{tone2}"


def _pair_tones(tag: str) -> tuple[int, int]:
    tone1, tone2 = tag[len(PAIR_TAG_PREFIX):].split("-")
    return int(tone1), int(tone2)


def get_unlocked_tags(db: Session, user_id: int) -> set[str]:
    """Unlocked SOUND tags (tones 1-4, initials, finals). Pair tags and the
    neutral tone are excluded: they track progress, they aren't sounds a
    syllable can be filtered by."""
    progress = pinyin_crud.get_sound_progress_map(db, user_id)
    unlocked = {t for t in progress if _is_sound_tag(t)}
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
    """Call every session. Seeds the next group in ORDERED_GROUPS once the
    learner has at most MAX_UNMASTERED_BEFORE_UNLOCK unmastered tags among
    everything already introduced. Level boundaries don't matter: someone
    who has mastered every tone except tone4 starts getting vowels.

    A group counts as introduced only when ALL its tags have progress rows.
    Demo tags ('a', 'm') seed single members early, and those shouldn't make
    their whole group look introduced; the missing members get seeded when
    that group's turn comes. Unlocks at most one group per call.

    Pair tags and the neutral tone are ignored here. Tone pairs are hard and
    stay unmastered for a long time, and the neutral tone comes up rarely;
    counting either would stall every later group.
    """
    progress = pinyin_crud.get_sound_progress_map(db, user_id)
    introduced = {t for t in progress if _is_sound_tag(t)}

    next_group = next((g for g in ORDERED_GROUPS if not g <= introduced), None)
    if next_group is None:
        return  # everything already introduced

    unmastered = [t for t in introduced if not _is_mastered(progress[t])]
    if len(unmastered) <= MAX_UNMASTERED_BEFORE_UNLOCK:
        _seed_group(db, user_id, next_group - introduced)


def maybe_unlock_tone_pairs(db: Session, user_id: int) -> None:
    """Call every session. Seeds "pair:a-b" once tone a and tone b are both
    mastered on their own, so two-syllable questions only start after the
    single tones are solid. Mastering tone1 + tone4 opens 1-1, 1-4, 4-1, 4-4;
    the rest follow as tone2 and tone3 are mastered. Once seeded, a pair
    stays even if a tone later dips below the threshold.

    The neutral tone is seeded alongside the first pairs, since it can only
    appear inside a two-syllable question. That makes it show up in progress
    from then on, at 0 attempts until the first neutral-tone word is spoken."""
    progress = pinyin_crud.get_sound_progress_map(db, user_id)
    mastered_tones = [t for t in TONES if _is_mastered(progress.get(f"tone{t}"))]
    for tone1, tone2 in product(mastered_tones, repeat=2):
        tag = _pair_tag(tone1, tone2)
        if tag not in progress:
            pinyin_crud.upsert_sound_progress(db, user_id, tag, correct=False)
    if mastered_tones and NEUTRAL_TONE_TAG not in progress:
        pinyin_crud.upsert_sound_progress(db, user_id, NEUTRAL_TONE_TAG, correct=False)


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


def _pick_weighted(progress: dict, tags: list[str]) -> str:
    """Weighted random instead of strict argmin: weaker tags come up more
    often, but every tag still has *some* chance each question.
    Without this, one tag being uniquely weakest (e.g. tone4 at 82% vs
    everything else at 92%+) meant it got picked for literally every
    question in every session, with no contrast against other tags --
    useless for something like telling tones apart. The 0.05 floor keeps
    even fully-mastered tags in light rotation instead of disappearing
    entirely once they cross the threshold. A tag at 0/0 scores 0, so
    never-practised tags (including unseen tone pairs) get the top weight."""
    def score(tag):
        row = progress[tag]
        return (row.successes or 0) / max(row.attempts or 1, 1)

    weights = [max(1.0 - score(t), 0.05) for t in tags]
    return random.choices(tags, weights=weights, k=1)[0]


def pick_target_tag(db: Session, user_id: int) -> str:
    progress = pinyin_crud.get_sound_progress_map(db, user_id)
    unlocked = [t for t in progress if _is_sound_tag(t)]
    if not unlocked:
        raise ValueError("No unlocked tags yet -- call generate_pinyin_session first")
    return _pick_weighted(progress, unlocked)


def generate_pinyin_question(db: Session, textbook_db: Session, user_id: int,
                             avoid: str | None = None) -> dict | None:
    """`avoid` is the previous question's numbered pinyin (e.g. 'ma3').
    Candidates with that same syllable+tone are skipped, whatever the question
    type, so the same sound never comes up twice in a row. Returns None when
    every available candidate is the avoided one."""
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

    candidates = [c for c in candidates if f"{c.syllable}{c.tone}" != avoid]
    if not candidates:
        return None

    syllable_row = random.choice(candidates)
    question_type = random.choice(QUESTION_TYPES)
    numbered_pinyin = f"{syllable_row.syllable}{syllable_row.tone}"

    return {
        "id": f"pinyin:{syllable_row.syllable}:{syllable_row.tone}:{question_type}",
        "question_type": question_type,
        "question": numbered_pinyin,
        "answer": numbered_pinyin,
        "audio_text": syllable_row.character,
        "audio_pinyin": f"{syllable_row.syllable} {syllable_row.tone}",
        "hanzi": syllable_row.character,
        "tags": pinyin_crud.syllable_row_to_tags(syllable_row),
        "target_tag": target_tag,
    }


def _syllable_entry(syllable: str, tone: int, character: str | None, tags: list) -> dict:
    """One syllable of a pair question, with the same audio fields a
    single-syllable question has so it can be played on its own."""
    return {
        "pinyin": f"{syllable}{tone}",
        "syllable": syllable,
        "tone": tone,
        "hanzi": character,
        "audio_text": character,
        "audio_pinyin": f"{syllable} {tone}",
        "tags": tags,
    }


def _pick_generated_pair(rows1: list, rows2: list, tone1: int, tone2: int) -> tuple:
    """Two syllable rows for a pair with no real word. For two different
    tones, often the same syllable twice ("ma1 ma3") so the tone is the only
    thing that changes; otherwise two random syllables."""
    rows1 = [r for r in rows1 if r.character] or rows1
    rows2 = [r for r in rows2 if r.character] or rows2

    if tone1 != tone2 and random.random() < SAME_SYLLABLE_SHARE:
        shared = sorted({r.syllable for r in rows1} & {r.syllable for r in rows2})
        if shared:
            syllable = random.choice(shared)
            return (
                next(r for r in rows1 if r.syllable == syllable),
                next(r for r in rows2 if r.syllable == syllable),
            )

    first = random.choice(rows1)
    different = [r for r in rows2 if (r.syllable, r.tone) != (first.syllable, first.tone)]
    return first, random.choice(different or rows2)


def generate_pinyin_pair_question(db: Session, textbook_db: Session, user_id: int,
                                  avoid: str | None = None) -> dict | None:
    """A two-syllable question aimed at one tone pair.

    Picks a pair tag (weak and unseen pairs first), then fills it with:
      1. for speaking only, sometimes a neutral-tone word starting with the
         pair's first tone (谢谢 for a 4-x pair);
      2. a real textbook word with that tone pair, when one fits the
         learner's unlocked sounds;
      3. otherwise two generated syllables.

    Returns None when no pair is unlocked, nothing fits, or the result would
    repeat `avoid` (the previous question's pinyin).
    """
    progress = pinyin_crud.get_sound_progress_map(db, user_id)
    pair_tags = [t for t in progress if _is_pair_tag(t)]
    if not pair_tags:
        return None

    target_tag = _pick_weighted(progress, pair_tags)
    tone1, tone2 = _pair_tones(target_tag)
    question_type = random.choice(PAIR_QUESTION_TYPES)
    speaking = question_type.startswith("speaking")

    unlocked = get_unlocked_tags(db, user_id)
    rows1 = pinyin_crud.get_syllables_by_tone_within_unlocked(textbook_db, tone1, unlocked)
    rows2 = pinyin_crud.get_syllables_by_tone_within_unlocked(textbook_db, tone2, unlocked)
    if not rows1 or not rows2:
        return None
    syllables1 = {r.syllable for r in rows1}
    syllables2 = {r.syllable for r in rows2}

    word = None
    if speaking and random.random() < NEUTRAL_WORD_SHARE:
        # A neutral syllable has no tone of its own to unlock, so it fits if
        # the learner can already produce that syllable at any tone.
        any_tone = {
            r.syllable
            for tone in TONES
            for r in pinyin_crud.get_syllables_by_tone_within_unlocked(textbook_db, tone, unlocked)
        }
        words = pinyin_crud.get_words_for_tone_pair(textbook_db, tone1, NEUTRAL_TONE, syllables1, any_tone)
        if words:
            word = random.choice(words)
    if word is None and random.random() < REAL_WORD_SHARE:
        words = pinyin_crud.get_words_for_tone_pair(textbook_db, tone1, tone2, syllables1, syllables2)
        if words:
            word = random.choice(words)

    if word is not None:
        row1 = next((r for r in rows1 if r.syllable == word.syllable1), None)
        row2 = (
            next((r for r in rows2 if r.syllable == word.syllable2), None)
            if word.tone2 in TONES else None   # neutral syllable: no row at that tone
        )
        entries = [
            _syllable_entry(word.syllable1, word.tone1, word.hanzi[0],
                            pinyin_crud.syllable_row_to_tags(row1) if row1 else []),
            _syllable_entry(word.syllable2, word.tone2, word.hanzi[1],
                            pinyin_crud.syllable_row_to_tags(row2) if row2 else []),
        ]
        hanzi = word.hanzi
        audio_text = word.hanzi
        english = word.vocab.english if word.vocab else None
        sandhi = bool(word.sandhi)
    else:
        row1, row2 = _pick_generated_pair(rows1, rows2, tone1, tone2)
        entries = [
            _syllable_entry(r.syllable, r.tone, r.character, pinyin_crud.syllable_row_to_tags(r))
            for r in (row1, row2)
        ]
        hanzi = "".join(e["hanzi"] or "" for e in entries)
        # The comma makes text-to-speech read two separate syllables instead
        # of treating them as one word (which would apply sandhi to 3-3).
        audio_text = "，".join(e["hanzi"] or "" for e in entries)
        english = None
        sandhi = False

    numbered_pinyin = " ".join(e["pinyin"] for e in entries)
    if numbered_pinyin == avoid:
        return None

    accepted_answers = [numbered_pinyin]
    if sandhi:
        # Written 3-3, spoken 2-3: writing down what was heard is also right.
        accepted_answers.append(f"{entries[0]['syllable']}2 {entries[1]['pinyin']}")

    tags = []
    for e in entries:
        for t in e["tags"]:
            if t not in tags:
                tags.append(t)

    return {
        "id": f"pinyin:{'-'.join(e['pinyin'] for e in entries)}:{question_type}",
        "question_type": question_type,
        "question": numbered_pinyin,
        "answer": numbered_pinyin,
        "accepted_answers": accepted_answers,
        "audio_text": audio_text,
        "audio_pinyin": " ".join(e["audio_pinyin"] for e in entries),
        "hanzi": hanzi,
        "english": english,
        "is_real_word": word is not None,
        "sandhi": sandhi,
        "syllables": entries,
        "tags": tags,
        "target_tag": target_tag,
    }


def generate_pinyin_session(db: Session, textbook_db: Session, user_id: int, num_questions: int = 10) -> list[dict]:
    """Never puts the same question back to back. The same sound can
    still appear more than once in a session, just not consecutively. If a
    slot can't find a different sound after MAX_ATTEMPTS_PER_SLOT tries,
    it's dropped, so a session can come back shorter than num_questions.

    Once any tone pair is unlocked, about MULTI_SYLLABLE_SHARE of the slots
    are two-syllable questions. A two-syllable slot that can't be filled
    falls back to a single syllable."""
    maybe_unlock_next_group(db, user_id)
    maybe_unlock_tone_pairs(db, user_id)

    progress = pinyin_crud.get_sound_progress_map(db, user_id)
    pairs_unlocked = any(_is_pair_tag(t) for t in progress)

    questions: list[dict] = []
    for _ in range(num_questions):
        avoid = questions[-1]["question"] if questions else None
        generators = [generate_pinyin_question]
        if pairs_unlocked and random.random() < MULTI_SYLLABLE_SHARE:
            generators.insert(0, generate_pinyin_pair_question)

        q = None
        for generate in generators:
            for _ in range(MAX_ATTEMPTS_PER_SLOT):
                q = generate(db, textbook_db, user_id, avoid=avoid)
                if q is not None:
                    break
            if q is not None:
                break
        if q is not None:
            questions.append(q)
    return questions


def process_pinyin_submission(
    db: Session,
    user_id: int,
    list_of_question_data: list[dict],
    is_correct: list[bool],
) -> dict:
    """PATCH /api/submit/pinyin path (routed through process_submission).
    Every answer credits sound_progress for each syllable's initial, final,
    and tone.

    Speaking questions always count as correct, whatever the client sends:
    learners aren't graded on accent.

    Two-syllable questions ("cao3 mei2") are credited syllable by syllable.
    If the question data carries "syllable_correct": [bool, bool], each
    syllable uses its own result, so one wrong tone doesn't mark down the
    sounds the learner got right. Without it, both syllables use the
    question's is_correct. The pair tag ("pair:3-2") is credited only when
    both syllables are right. A neutral-tone syllable credits its initial,
    final, and "tone5", but no pair.
    """
    for i, q in enumerate(list_of_question_data):
        speaking = str(q.get("question_type", "")).startswith("speaking")
        per_syllable = q.get("syllable_correct")
        parts = q["question"].split()   # "shu1" or "cao3 mei2" -- question == answer for pinyin's own questions

        results: list[bool] = []
        tones: list[int] = []
        for j, numbered_pinyin in enumerate(parts):
            if speaking:
                correct = True
            elif isinstance(per_syllable, list) and j < len(per_syllable):
                correct = bool(per_syllable[j])
            else:
                correct = is_correct[i]

            tone = int(numbered_pinyin[-1])
            bare_syllable = numbered_pinyin[:-1]

            for initial, final, _ in split_pinyin_sounds(bare_syllable):
                if initial:
                    record_sound_attempt(db, user_id, initial, correct)
                record_sound_attempt(db, user_id, final, correct)

            if tone in TONES or tone == NEUTRAL_TONE:
                record_sound_attempt(db, user_id, f"tone{tone}", correct)

            results.append(correct)
            tones.append(tone)

        if len(tones) == 2 and all(t in TONES for t in tones):
            record_sound_attempt(db, user_id, _pair_tag(tones[0], tones[1]), all(results))

    return {"user_id": user_id}


def get_pinyin_progress(db: Session, user_id: int) -> dict:
    """What the user sees: unlocked sounds grouped by category, for
    reference -- not level number, since level is an internal gating
    concept, not user-facing. "tone5" (neutral) is listed with the tones
    once two-syllable questions have unlocked."""
    progress = pinyin_crud.get_sound_progress_map(db, user_id)
    consonant_tags = _level_all_tags(3)
    tones, vowels, consonants, tone_pairs = [], [], [], []
    for tag, row in progress.items():
        entry = {"tag": tag, "attempts": row.attempts, "successes": row.successes}
        if _is_pair_tag(tag):
            tone_pairs.append(entry)
        elif tag.startswith("tone"):
            tones.append(entry)
        elif tag in consonant_tags:
            consonants.append(entry)
        else:
            vowels.append(entry)
    return {
        "tones": sorted(tones, key=lambda x: x["tag"]),
        "vowels": sorted(vowels, key=lambda x: x["tag"]),
        "consonants": sorted(consonants, key=lambda x: x["tag"]),
        "tone_pairs": sorted(tone_pairs, key=lambda x: x["tag"]),
        "graduated": get_current_level(db, user_id) > max(LEVEL_GROUPS),
        "mastery_threshold": MASTERY_THRESHOLD,

    }

def get_representative_row_by_tag(db: Session, tag: str, category: str) -> dict | None:
    row = (
        pinyin_crud.get_consonant_example_row(db, tag)
        if category == "initial"
        else pinyin_crud.get_final_example_row(db, tag)
    )
    if row is None:
        return None

    guide = pinyin_crud.get_sound_guide(db, tag, category)
    return {**row, "description": guide.description if guide else None}