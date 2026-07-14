from sqlalchemy.orm import Session
from models.user import StrengthTable, User, SoundProgress, WordTierProgress
from datetime import datetime, timedelta

SOUND_UNLOCK_SUCCESSES = 1
SOUND_UNLOCK_ATTEMPTS_CAP = 5
MAX_TIER = 4

# The two facets a word's strength is tracked on.
FACETS = ("character", "pinyin")

# Which facet(s) each question type exercises, and therefore updates on answer.
# "character" = meaning / recognition; "pinyin" = sound.
# This is the single source of truth -- submit_session routes updates through
# here, and init_db seeds one row per (tag, facet).
QUESTION_TYPE_FACETS = {
    "speaking vocab":                       ["pinyin"],
    "speaking sentence":                    ["pinyin"],
    "transcribe word to pinyin":            ["pinyin"],
    "listening vocab":                      ["pinyin"],
    "listening sentence":                   ["pinyin", "character"],
    "translate chinese word to english":    ["character"],
    "translate english word to chinese":    ["character"],
    "translate chinese sentence to english":["character"],
    "translate english sentence to chinese":["character"],
    "fill in the blank":                    ["character"],
}


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


def _apply_answer_to_row(row, is_correct: bool):
    row.times_seen = (row.times_seen or 0) + 1   # every answer counts as "seen"
    if is_correct:
        row.correct_count += 1
        row.stability = min(row.stability * 2, 365)
    else:
        row.stability = max(row.stability * 0.5, 1)
    row.last_practice = datetime.utcnow()


def update_after_answer(db: Session, user_id: int, tag: str, facet: str, is_correct: bool):
    """Update a single (tag, facet) strength row. Creates the row if missing
    so a word first met in a session still gets tracked."""
    row = get_strength_row(db, user_id, tag, facet)
    if not row:
        row = StrengthTable(
            tag=tag, user_id=user_id, facet=facet,
            correct_count=0, stability=1.0, last_practice=datetime.utcnow(),
        )
        db.add(row)
    _apply_answer_to_row(row, is_correct)
    db.commit()
    db.refresh(row)
    return {"tag": tag, "facet": facet, "correct_count": row.correct_count, "stability": row.stability}


def update_after_answer_for_question(db: Session, user_id: int, tag: str,
                                     question_type: str, is_correct: bool):
    """Update whichever facet(s) the question type exercises (see
    QUESTION_TYPE_FACETS). This is what submit_session calls."""
    results = []
    for facet in facets_for_question_type(question_type):
        results.append(update_after_answer(db, user_id, tag, facet, is_correct))
    return results


# ----------------------------- USER -----------------------------

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


def get_user(db: Session, user_id: int):
    return db.query(User).filter(User.id == user_id).first()


def update_user_unit(db: Session, user_id: int, new_unit: int):
    user = db.query(User).filter(User.id == user_id).first()
    if user:
        user.current_unit = new_unit
        db.commit()
        db.refresh(user)
    return user


def graduate_unit(db: Session, user_id: int, unit: int):
    user = db.query(User).filter(User.id == user_id).first()
    if user:
        graduated = set(user.graduated_units.split(",")) if user.graduated_units else set()
        graduated.discard("")
        graduated.add(str(unit))
        user.graduated_units = ",".join(graduated)
        user.current_unit = unit + 1
        db.commit()
        db.refresh(user)
    return user


def get_graduated_units(db: Session, user_id: int) -> set:
    user = get_user(db, user_id)
    if not user or not user.graduated_units:
        return set()
    return {int(u) for u in user.graduated_units.split(",") if u}


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
