"""
Session-domain crud: everything that reads/writes the models in
session/models.py (StrengthTable, SoundProgress, WordTierProgress,
SeenQuestion, FlaggedMismatch), plus the handful of auth.User helpers
(get_user, update_user_unit, graduate_unit, get_graduated_units) that are
really about session/unit progression rather than user account data.

NOT here -- left in the old top-level crud.py, since they only touch
DictionaryEntry (not a session model) and aren't part of the progress-
tracking domain:
    get_dictionary_entries
    build_vocab_block

get_known_vocab_tags is the borderline case: it queries StrengthTable (a
session model) but its output feeds `build_vocab_block`, which stayed
top-level. I kept it here because it's a StrengthTable query like everything
else in this file -- if that pairing is awkward in practice, the two-line
function is cheap to duplicate or re-import from the top-level module.
"""
from sqlalchemy.orm import Session
from datetime import datetime

from app.session.models import StrengthTable, SoundProgress, WordTierProgress, SeenQuestion, FlaggedMismatch
from app.auth.models import User
from app.session.constants import (
    QUESTION_TYPE_FACETS,
    STABILITY_FLOOR,
    MAX_MISS_COUNT,
    MAX_TIER,
    SOUND_UNLOCK_SUCCESSES,
    SOUND_UNLOCK_ATTEMPTS_CAP,
)
from app.core.config.data import MANDARIN_APP_DB


def facets_for_question_type(question_type: str) -> list:
    """Facet(s) a question type updates. Unknown types default to character
    so a mis-tagged question still records *something* rather than silently
    updating nothing."""
    return QUESTION_TYPE_FACETS.get(question_type, ["character"])


# ----------------------------- STRENGTH -----------------------------

def get_progress_by_user(db: Session, user_id: int, facet: str = None):
    """All strength rows for a user. Pass facet to restrict to one aspect
    (e.g. the listening tab reads facet='pinyin', the flashcard/review tab
    reads facet='character')."""
    q = db.query(StrengthTable).filter(StrengthTable.user_id == user_id)
    if facet is not None:
        q = q.filter(StrengthTable.facet == facet)
    return q.all()


def get_strength_row(db: Session, user_id: int, tag: str, facet: str):
    return db.query(StrengthTable).filter(
        StrengthTable.user_id == user_id,
        StrengthTable.tag == tag,
        StrengthTable.facet == facet,
    ).first()


def _apply_answer_to_row(row, is_correct: bool, grow_stability: bool):
    """Apply one answer to a single (tag, facet) strength row.

    OPTION C -- stability is purely a REVIEW mechanism and does nothing during
    learning:
      - grow_stability=False (word not yet review-eligible): correct_count and
        last_practice still update, but stability is pinned to STABILITY_FLOOR.
        This means a word ENTERS review at floor stability, so its first
        review comes ~1-2 days out (tight), then the doubling stretches it.
      - grow_stability=True (word is review-eligible): normal SRS -- double on
        correct (cap 365), halve on wrong (floor STABILITY_FLOOR).

    last_practice always updates: even a learning-phase answer is a real
    exposure, and once the word becomes review-eligible we want its decay
    clock measured from its most recent contact, not from whenever it
    graduated."""
    row.times_seen = (row.times_seen or 0) + 1   # every answer counts as "seen"
    if is_correct:
        row.correct_count += 1

    if grow_stability:
        if is_correct:
            row.stability = min(row.stability * 2, 365)
        else:
            row.stability = max(row.stability * 0.5, STABILITY_FLOOR)
    else:
        # learning phase: stability is inert, held at the floor
        row.stability = STABILITY_FLOOR

    row.last_practice = datetime.utcnow()


def update_after_answer(db: Session, user_id: int, tag: str, facet: str,
                        is_correct: bool, grow_stability: bool = False):
    """Update a single (tag, facet) strength row. Creates the row if missing
    so a word first met in a session still gets tracked. grow_stability
    defaults False -- callers that know the word is review-eligible pass True
    (see update_after_answer_for_question)."""
    row = get_strength_row(db, user_id, tag, facet)
    if not row:
        row = StrengthTable(
            tag=tag, user_id=user_id, facet=facet,
            correct_count=0, stability=STABILITY_FLOOR, last_practice=datetime.utcnow(),
        )
        db.add(row)
    _apply_answer_to_row(row, is_correct, grow_stability)
    db.commit()
    db.refresh(row)
    return {"tag": tag, "facet": facet, "correct_count": row.correct_count, "stability": row.stability}


def update_miss_count(db: Session, user_id: int, tag: str, facet: str,
                      delta: int, attempts: int = 0):
    """Adjust a (tag, facet) row's recent-struggle signal (Option B).

    miss_count is a single integer of RECENT net struggle: a submit with misses
    adds them (delta = +misses), a clean submit forgives one (delta = -1). It's
    floored at 0, so demonstrated success decays struggle -- no timestamps, no
    time-based decay. attempt_count accumulates total question appearances for a
    true rate later if wanted. Creates the row if missing."""
    row = get_strength_row(db, user_id, tag, facet)
    if not row:
        row = StrengthTable(
            tag=tag, user_id=user_id, facet=facet,
            correct_count=0, stability=STABILITY_FLOOR, last_practice=datetime.utcnow(),
        )
        db.add(row)
    row.miss_count = max((row.miss_count or 0) + delta, 0)
    row.miss_count = min(row.miss_count, MAX_MISS_COUNT)
    row.attempt_count = (row.attempt_count or 0) + attempts
    db.commit()
    db.refresh(row)
    return row


def update_after_answer_for_question(db: Session, user_id: int, tag: str,
                                     question_type: str, is_correct: bool,
                                     facet_eligible: dict = None):
    """Update whichever facet(s) the question type exercises (see
    QUESTION_TYPE_FACETS). This is what submit_session calls.

    facet_eligible is a per-FACET phase map for this word AS OF THE START of
    the submit, e.g. {"character": True, "pinyin": False} -- computed by the
    caller from the pre-submit snapshot. Eligibility is per-facet now: a word's
    character facet can be in review while its pinyin facet is still learning.
    Option C: stability grows/decays only for a facet that's already
    review-eligible; a still-learning facet stays pinned to the floor.

    A question type like 'listening sentence' touches both facets, so each
    facet grows or stays inert according to its OWN eligibility."""
    facet_eligible = facet_eligible or {}
    results = []
    for facet in facets_for_question_type(question_type):
        results.append(update_after_answer(db, user_id, tag, facet, is_correct,
                                            grow_stability=facet_eligible.get(facet, False)))
    return results


def reset_miss_count(db: Session, user_id: int, tag: str, facet: str):
    """Hard-reset a (tag, facet)'s miss_count to 0."""
    row = get_strength_row(db, user_id, tag, facet)
    if row:
        row.miss_count = 0
        db.commit()
        db.refresh(row)
    return row


def get_seen_tags(db: Session, user_id: int, facet: str) -> set:
    """Tags the user has been SHOWN at least once for a given facet
    (times_seen >= 1). "Shown", not "answered correctly" -- used for phase
    coverage."""
    rows = db.query(StrengthTable).filter(
        StrengthTable.user_id == user_id,
        StrengthTable.facet == facet,
        StrengthTable.times_seen >= 1,
    ).all()
    return {r.tag for r in rows}


def get_known_vocab_tags(db: Session, user_id: int, min_correct: int = 1) -> list[str]:
    rows = (
        db.query(StrengthTable.tag)
        .filter(
            StrengthTable.user_id == user_id,
            StrengthTable.facet == "character",
            StrengthTable.correct_count >= min_correct,
        )
        .distinct()
        .all()
    )
    return [r.tag for r in rows]


# ----------------------------- USER / UNIT PROGRESSION -----------------------------

def get_user(db: Session, user_id: int):
    return db.query(User).filter(User.id == user_id).first()


# Units per HSK level. current_unit is now the unit number WITHIN the user's
# hsk_level (1-based), not a running total across levels.
# HSK 1 = 15 matches your data (global unit 16 -> HSK 2 unit 1). The other
# counts are the HSK Standard Course lesson counts -- check them against your
# units_output.json and adjust. Consider moving this to session/constants.py.
UNITS_PER_HSK_LEVEL = {1: 15, 2: 15, 3: 20, 4: 20, 5: 36, 6: 40}
MAX_HSK_LEVEL = max(UNITS_PER_HSK_LEVEL)


def next_position(hsk_level: int, unit: int) -> tuple[int, int]:
    """(hsk_level, unit) that follows finishing `unit` in `hsk_level`.
    Rolls over to unit 1 of the next level when the level is complete.
    At the last unit of the last level it stays put."""
    units_in_level = UNITS_PER_HSK_LEVEL.get(hsk_level, 0)
    if unit < units_in_level:
        return hsk_level, unit + 1
    if hsk_level < MAX_HSK_LEVEL:
        return hsk_level + 1, 1
    return hsk_level, unit


def global_to_level_unit(global_unit: int) -> tuple[int, int]:
    """Convert an old running-total unit number (e.g. 16) to (hsk_level, unit)
    (e.g. (2, 1)). Used to read graduated_units saved in the old format."""
    remaining = global_unit
    for level in sorted(UNITS_PER_HSK_LEVEL):
        count = UNITS_PER_HSK_LEVEL[level]
        if remaining <= count:
            return level, remaining
        remaining -= count
    return MAX_HSK_LEVEL, remaining  # past the end; leave it on the last level


def _parse_graduated(raw: str) -> set[tuple[int, int]]:
    """graduated_units is stored as 'level:unit' entries, e.g. '1:14,1:15,2:1'.
    Bare numbers from the old format are treated as running totals and
    converted, so existing rows keep working without a migration."""
    result = set()
    for entry in (raw or "").split(","):
        entry = entry.strip()
        if not entry:
            continue
        if ":" in entry:
            level, unit = entry.split(":", 1)
            result.add((int(level), int(unit)))
        else:
            result.add(global_to_level_unit(int(entry)))
    return result


def get_user_position(db: Session, user_id: int) -> dict | None:
    """{'hsk_level': 2, 'unit': 1} -- what the app should show as 'where we are'."""
    user = get_user(db, user_id)
    if not user:
        return None
    return {"hsk_level": user.hsk_level or 1, "unit": user.current_unit or 1}


def update_user_unit(db: Session, user_id: int, new_unit: int, hsk_level: int = None):
    """Set the user's unit (within a level). Pass hsk_level to move levels too;
    leave it out to keep the current level."""
    user = db.query(User).filter(User.id == user_id).first()
    if user:
        if hsk_level is not None:
            user.hsk_level = hsk_level
        user.current_unit = new_unit
        db.commit()
        db.refresh(user)
    return user


def graduate_unit(db: Session, user_id: int, unit: int, hsk_level: int = None):
    """Mark (hsk_level, unit) graduated and advance the user, rolling over to
    the next HSK level when the current one is finished. hsk_level defaults to
    the user's current level."""
    user = db.query(User).filter(User.id == user_id).first()
    if user:
        level = hsk_level if hsk_level is not None else (user.hsk_level or 1)

        graduated = _parse_graduated(user.graduated_units)
        graduated.add((level, unit))
        user.graduated_units = ",".join(f"{l}:{u}" for l, u in sorted(graduated))

        user.hsk_level, user.current_unit = next_position(level, unit)
        db.commit()
        db.refresh(user)
    return user


def get_graduated_units(db: Session, user_id: int, hsk_level: int = None) -> set:
    """Graduated unit numbers WITHIN one HSK level (defaults to the user's
    current level), so callers comparing against current_unit keep working.
    Use get_all_graduated_units for every level."""
    user = get_user(db, user_id)
    if not user or not user.graduated_units:
        return set()
    level = hsk_level if hsk_level is not None else (user.hsk_level or 1)
    return {u for l, u in _parse_graduated(user.graduated_units) if l == level}


def get_all_graduated_units(db: Session, user_id: int) -> set[tuple[int, int]]:
    """Every graduated (hsk_level, unit) pair across all levels."""
    user = get_user(db, user_id)
    if not user:
        return set()
    return _parse_graduated(user.graduated_units)


# ----------------------------- SOUND PROGRESS -----------------------------

def get_sound_progress(db: Session, user_id: int):
    return db.query(SoundProgress).filter(SoundProgress.user_id == user_id).all()


def get_unlocked_sounds(db: Session, user_id: int) -> set:
    """A sound with no row yet has never been attempted, so it stays locked."""
    rows = get_sound_progress(db, user_id)
    return {
        r.sound for r in rows
        if r.successes >= SOUND_UNLOCK_SUCCESSES or r.attempts >= SOUND_UNLOCK_ATTEMPTS_CAP
    }


def record_sound_attempt(db: Session, user_id: int, sound: str, is_correct: bool):
    row = db.query(SoundProgress).filter(
        SoundProgress.user_id == user_id,
        SoundProgress.sound == sound,
    ).first()
    if not row:
        row = SoundProgress(user_id=user_id, sound=sound, attempts=0, successes=0)
        db.add(row)

    row.attempts += 1
    if is_correct:
        row.successes += 1

    db.commit()
    db.refresh(row)
    return row


# ----------------------------- WORD TIER -----------------------------

def get_tier(db: Session, user_id: int, tag: str) -> int:
    """A word with no row yet hasn't started the tier progression, so it's tier 1."""
    row = db.query(WordTierProgress).filter(
        WordTierProgress.user_id == user_id,
        WordTierProgress.tag == tag,
    ).first()
    return row.tier if row else 1


def get_tiers_for_tags(db: Session, user_id: int, tags) -> dict:
    """Batch tier lookup -> {tag: tier}, defaulting to 1 for tags with no row."""
    rows = db.query(WordTierProgress).filter(
        WordTierProgress.user_id == user_id,
        WordTierProgress.tag.in_(list(tags)),
    ).all()
    tiers = {r.tag: r.tier for r in rows}
    return {tag: tiers.get(tag, 1) for tag in tags}


def advance_tier(db: Session, user_id: int, tag: str):
    """Bump a word's tier by one, capped at MAX_TIER. Creates the row at
    tier 1 first if missing, then bumps it."""
    row = db.query(WordTierProgress).filter(
        WordTierProgress.user_id == user_id,
        WordTierProgress.tag == tag,
    ).first()
    if not row:
        row = WordTierProgress(user_id=user_id, tag=tag, tier=1)
        db.add(row)
    row.tier = min(row.tier + 1, MAX_TIER)
    db.commit()
    db.refresh(row)
    return row


# ----------------------------- PER-QUESTION EXPOSURE -----------------------------
# Tag-level correct_count/tier tell the selector WHICH tag to pick, but a tag
# can have many question variants -- these track WHICH SPECIFIC questions a
# user has actually been shown, so selection can prefer variety over uniform
# random choice (see generate_tier_questions in tier_engine.py).

def get_seen_question_counts(db: Session, user_id: int) -> dict:
    """{question_id: times_shown} for every question this user has ever been
    shown. One query, used once per session-generation call."""
    rows = db.query(SeenQuestion).filter(SeenQuestion.user_id == user_id).all()
    return {r.question_id: r.times_shown for r in rows}


def record_question_shown(db: Session, user_id: int, question_id: str):
    """Increment exposure for one question ID. Called from submit_session for
    every question actually completed (right or wrong -- a missed, requeued
    question was still SEEN each time it appeared, which is the correct
    exposure count even though it's not what advances tier/strength)."""
    if not question_id:
        return
    row = db.query(SeenQuestion).filter(
        SeenQuestion.user_id == user_id,
        SeenQuestion.question_id == question_id,
    ).first()
    if not row:
        row = SeenQuestion(user_id=user_id, question_id=question_id, times_shown=0)
        db.add(row)
    row.times_shown = (row.times_shown or 0) + 1
    row.last_shown = datetime.utcnow()
    db.commit()


# ----------------------------- FLAGGED MISMATCHES -----------------------------
# Detection log for (question, expected_answer) pairs where the AI grader
# determined 'expected' doesn't actually match 'question' -- almost always an
# upstream OCR/data-pipeline bug (see FlaggedMismatch docstring in models.py).
# This does NOT fix grading itself; the route grades the learner against the
# question's real meaning regardless of what's in here. This table exists
# purely so you can query it later and batch-reprocess the affected sentences
# instead of discovering and hand-fixing them one at a time.

def log_mismatch(db: Session, question: str, expected_answer: str, direction: str, reasoning: str = ""):
    """Insert a new flagged pair, or bump flagged_count/last_flagged_at if this
    exact (question, expected_answer) pair has been flagged before. Best-effort:
    a failure here should never take down grading, so callers should wrap this
    in try/except and just log on failure."""
    row = db.query(FlaggedMismatch).filter(
        FlaggedMismatch.question == question,
        FlaggedMismatch.expected_answer == expected_answer,
    ).first()

    if row:
        row.flagged_count = (row.flagged_count or 1) + 1
        row.last_flagged_at = datetime.utcnow()
    else:
        row = FlaggedMismatch(
            question=question,
            expected_answer=expected_answer,
            direction=direction,
            reasoning=reasoning,
        )
        db.add(row)

    db.commit()
    return row


def get_flagged_mismatches(db: Session, min_flagged_count: int = 1, limit: int = 200):
    """Pairs flagged at least `min_flagged_count` times, most-recurring first.
    Meant to drive a batch reprocessing script over units_output.json /
    index_output.json -- min_flagged_count lets you filter out one-off AI
    misjudgments and focus on pairs that are consistently wrong."""
    return (
        db.query(FlaggedMismatch)
        .filter(FlaggedMismatch.flagged_count >= min_flagged_count)
        .order_by(FlaggedMismatch.flagged_count.desc())
        .limit(limit)
        .all()
    )