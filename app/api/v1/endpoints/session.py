from fastapi import APIRouter, Depends, Body
from sqlalchemy.orm import Session
from database import SessionLocal, inverted_index, tags_to_unit_dict, unit_to_vocab_tags_dict, unit_questions, META_TAGS, hsk1_dictionary, word_to_pinyin
from schemas.user import SessionResponse
from pinyin_utils import split_pinyin_sounds, GATED_INITIALS, GATED_FINALS
import crud
from datetime import datetime
import random

router = APIRouter()

# ----------------------------- CONSTANTS -----------------------------

NUM_OF_UNIT_TEST_QUESTIONS = 20
PERCENTAGE_TO_PASS_UNIT_TEST = 0.80
GRADUATION_THRESHOLD = 3          # collapsed (min-facet) correct_count needed to consider a word graduated
SESSION_SIZE = 10                 # number of current-unit tier questions per session (review is appended on top)
REVIEW_THRESHOLD = 0.80           # below this decayed strength, a graduated word is "due" for review
MAX_SAME_TAG_PER_SESSION = 2      # per-tag cap within the tier-question portion of a session
MAX_SAME_TYPE_PER_SESSION = 3     # per-question-type cap within the tier-question portion of a session

# A word's tier gates which question types it can be served on. Tiers only
# move forward (see crud.advance_tier) -- a word's current tier IS its
# highest unlocked tier.
TIER_QUESTION_TYPES = {
    1: {"listening vocab", "translate chinese word to english"},
    2: {"speaking vocab", "transcribe word to pinyin", "translate english word to chinese"},
    3: {"translate chinese sentence to english", "fill in the blank"},
    4: {"listening sentence", "speaking sentence", "translate english sentence to chinese"},
}
ALL_TIER_QUESTION_TYPES = set().union(*TIER_QUESTION_TYPES.values())

# A tier-4 word is served a tier-3 question this fraction of the time (a
# "downshift" back to sentence-adjacent practice) instead of tier 4.
# Answering on the downshifted (lower-tier) type does NOT advance the word --
# only an answer on the word's actual current tier counts as exposure.
TIER4_DOWNSHIFT_PROBABILITY = 0.20

# Once fewer than this many current-unit words remain ungraduated, the unit
# is in its "final push" -- tier-4 words stop downshifting so every serve
# pushes directly toward graduation.
FINAL_PUSH_UNGRADUATED_THRESHOLD = 5

# ----------------------------- DB DEPENDENCY -----------------------------

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# ----------------------------- FACET COLLAPSE -----------------------------
# StrengthTable stores two rows per (user, tag): a "character" facet and a
# "pinyin" facet. Graduation and review logic are written against ONE record
# per tag, so we collapse the two facets to a single synthetic record per tag
# using the MINIMUM across facets -- a word counts as "known" only as well as
# its weaker aspect. This keeps sentence review / graduation honest: strong
# meaning but weak pinyin (or vice versa) is still weak.

class _CollapsedRecord:
    __slots__ = ("tag", "correct_count", "stability", "last_practice")

    def __init__(self, tag, correct_count, stability, last_practice):
        self.tag = tag
        self.correct_count = correct_count
        self.stability = stability
        self.last_practice = last_practice


def collapse_facets(records):
    """[(tag,facet)-rows] -> [one _CollapsedRecord per tag], min across facets.
    For last_practice we take the OLDER timestamp (min), so a word looks as
    stale as its least-recently-practiced facet -- consistent with the min
    strength rule."""
    by_tag = {}
    for r in records:
        by_tag.setdefault(r.tag, []).append(r)
    collapsed = []
    for tag, rows in by_tag.items():
        collapsed.append(_CollapsedRecord(
            tag=tag,
            correct_count=min(r.correct_count for r in rows),
            stability=min(r.stability for r in rows),
            last_practice=min(r.last_practice for r in rows),
        ))
    return collapsed


def get_collapsed_progress(db: Session, user_id: int):
    """One record per tag (min across facets) -- drop-in for the old
    crud.get_progress_by_user which used to return one row per tag."""
    return collapse_facets(crud.get_progress_by_user(db, user_id))


def is_unit_graduated(tag_records: list, unit_tags: set) -> bool:
    """unit_tags should be the unit's own vocab/grammar/proper-noun words
    (unit_to_vocab_tags_dict), not unit_to_tags_dict -- the latter also
    includes any word a sentence in this unit happens to contain as a
    substring, even one taught in a later unit, which would make graduation
    depend on words this unit never actually teaches."""
    record_map = {r.tag: r for r in tag_records}
    for tag in unit_tags:
        record = record_map.get(tag)
        if not record or record.correct_count < GRADUATION_THRESHOLD:
            return False
    return True


def _tag_facet_counts(db: Session, user_id: int, tag: str) -> tuple:
    """(character_count, pinyin_count) of correct_count for a tag, read
    straight from the two per-facet StrengthTable rows."""
    char_row = crud.get_strength_row(db, user_id, tag, "character")
    pin_row = crud.get_strength_row(db, user_id, tag, "pinyin")
    char_c = char_row.correct_count if char_row else 0
    pin_c = pin_row.correct_count if pin_row else 0
    return char_c, pin_c


# ----------------------------- SOUND TAGGING -----------------------------
# A word's pinyin may contain a sound (zh/ch/sh/r/j/q/x/z/c, or the v/er/e
# finals -- see GATED_INITIALS/GATED_FINALS in audio.py) with no English
# equivalent. record_sound_attempt tracks per-sound success on "speaking
# vocab" answers so pronunciation progress is visible; there is no reactive
# sentence-level gate anymore since tier 2 (speaking vocab) always precedes
# tier 4 (speaking sentence) structurally.

def _tag_sounds(tag: str) -> set:
    """The GATED sounds (initials/finals with no English equivalent) present
    in a single word's pinyin, per word_to_pinyin.json."""
    p = word_to_pinyin.get(tag)
    if not p:
        return set()
    sounds = set()
    for initial, final, _tone in split_pinyin_sounds(p):
        if initial in GATED_INITIALS:
            sounds.add(initial)
        if final in GATED_FINALS:
            sounds.add(final)
    return sounds


# ----------------------------- TIER SESSION (current-unit words) -----------------------------

def _active_tier_for_serve(tier: int, final_push: bool) -> int:
    """The tier a word is actually served on for one pick. Tiers 1-3 serve as
    themselves. Tier 4 downshifts to tier 3 TIER4_DOWNSHIFT_PROBABILITY of the
    time, unless the unit is in its final push (see
    FINAL_PUSH_UNGRADUATED_THRESHOLD), in which case tier 4 always serves
    tier 4."""
    if tier >= 4 and not final_push:
        return 3 if random.random() < TIER4_DOWNSHIFT_PROBABILITY else 4
    return tier


def generate_tier_questions(db: Session, user_id: int, unit: int) -> list:
    """SESSION_SIZE questions drawn from the current unit's words. Weaker
    words (lower min-facet correct_count) are weighted to surface more often.
    Each serve: pick a word (weighted), pick a random question type from its
    active tier, respecting per-type and per-tag caps."""
    unit_tags = unit_to_vocab_tags_dict.get(unit, set())
    if not unit_tags:
        return []

    tiers = crud.get_tiers_for_tags(db, user_id, unit_tags)

    min_counts = {}
    ungraduated = 0
    for tag in unit_tags:
        char_c, pin_c = _tag_facet_counts(db, user_id, tag)
        min_count = min(char_c, pin_c)
        min_counts[tag] = min_count
        if min_count < GRADUATION_THRESHOLD:
            ungraduated += 1
    final_push = ungraduated < FINAL_PUSH_UNGRADUATED_THRESHOLD

    tag_pool = list(unit_tags)
    weights = [1.0 / (min_counts[t] + 1) for t in tag_pool]

    used_ids = set()
    tag_counts = {}
    type_counts = {}
    question_set = []

    max_attempts = SESSION_SIZE * 25
    attempts = 0
    while len(question_set) < SESSION_SIZE and attempts < max_attempts:
        attempts += 1
        tag = random.choices(tag_pool, weights=weights, k=1)[0]
        if tag_counts.get(tag, 0) >= MAX_SAME_TAG_PER_SESSION:
            continue

        active_tier = _active_tier_for_serve(tiers.get(tag, 1), final_push)
        qt = random.choice(list(TIER_QUESTION_TYPES[active_tier]))
        if type_counts.get(qt, 0) >= MAX_SAME_TYPE_PER_SESSION:
            continue

        pool = [
            q for q in inverted_index.get(tag, [])
            if q["question_type"] == qt
            and q["id"] not in used_ids
            and q.get("unit") == unit
        ]
        if not pool:
            continue

        chosen = random.choice(pool)
        question_set.append(chosen)
        used_ids.add(chosen["id"])
        tag_counts[tag] = tag_counts.get(tag, 0) + 1
        type_counts[qt] = type_counts.get(qt, 0) + 1

    return question_set


# ----------------------------- REVIEW (graduated-unit words) -----------------------------

def _due_review_tags(db: Session, user_id: int, graduated_units: set) -> list:
    """Graduated-unit words whose collapsed strength has decayed below
    REVIEW_THRESHOLD, weakest first."""
    now = datetime.utcnow()
    scored = []
    for r in get_collapsed_progress(db, user_id):
        unit = tags_to_unit_dict.get(r.tag)
        if unit is None or unit not in graduated_units:
            continue
        strength = 0.5 ** ((now - r.last_practice).total_seconds() / 86400 / r.stability)
        if strength < REVIEW_THRESHOLD:
            scored.append((r.tag, strength))
    scored.sort(key=lambda x: x[1])
    return [tag for tag, _ in scored]


def _pick_review_question(tag: str, graduated_units: set, used_ids: set):
    """Review is sentence-level only: recognition-level tier-1/2 review is too
    easy to be worth it. Try tier 4 then tier 3; if neither has an available
    question for this word, return None -- the word stays due for next time."""
    for tier in (4, 3):
        types = list(TIER_QUESTION_TYPES[tier])
        random.shuffle(types)
        for qt in types:
            pool = [
                q for q in inverted_index.get(tag, [])
                if q["question_type"] == qt
                and q["id"] not in used_ids
                and q.get("unit") in graduated_units
            ]
            if pool:
                return random.choice(pool)
    return None


def generate_review_questions(db: Session, user_id: int, graduated_units: set, used_ids: set) -> list:
    review_questions = []
    for tag in _due_review_tags(db, user_id, graduated_units):
        q = _pick_review_question(tag, graduated_units, used_ids)
        if q:
            review_questions.append(q)
            used_ids.add(q["id"])
    return review_questions


def generate_practice_session(db: Session, user_id: int, unit: int, graduated_units: set) -> SessionResponse:
    """SESSION_SIZE tier questions from the current unit, plus every currently
    due review question appended on top."""
    tier_questions = generate_tier_questions(db, user_id, unit)
    used_ids = {q["id"] for q in tier_questions}
    review_questions = generate_review_questions(db, user_id, graduated_units, used_ids)

    question_set = tier_questions + review_questions
    random.shuffle(question_set)
    return SessionResponse(user_id=user_id, session_type="practice_session", question_set=question_set)


def generate_unit_test(user_id: int, unit: int) -> SessionResponse:
    eligible = [q for q in unit_questions.get(str(unit), []) if q["question_type"] in ALL_TIER_QUESTION_TYPES]
    selected = random.sample(eligible, min(NUM_OF_UNIT_TEST_QUESTIONS, len(eligible)))
    return SessionResponse(user_id=user_id, session_type="unit_test", question_set=selected)


# ----------------------------- ENDPOINTS -----------------------------

@router.get("/api/generate_session/{user_id}", response_model=SessionResponse)
def generate_session(user_id: int, mode: str = "sentence", db: Session = Depends(get_db)):
    user = crud.get_user(db, user_id)
    user_unit = user.current_unit
    graduated_units = crud.get_graduated_units(db, user_id)

    unit_tags = unit_to_vocab_tags_dict.get(user_unit, set())
    all_records = get_collapsed_progress(db, user_id)
    unit_records = [r for r in all_records if r.tag in unit_tags]

    if is_unit_graduated(unit_records, unit_tags):
        return generate_unit_test(user_id, user_unit)
    return generate_practice_session(db, user_id, user_unit, graduated_units)


@router.patch("/api/submit_session/{user_id}")
def submit_session(
    user_id: int,
    list_of_question_data: list[dict] = Body(...),
    is_correct: list[bool] = Body(...),
    is_unit_test: bool = Body(...),
    mode: str = Body("sentence"),
    db: Session = Depends(get_db)
):
    submit_tags = set()
    for question_data in list_of_question_data:
        for tag in question_data.get("tags", []):
            if tag not in META_TAGS and not tag.startswith("unit_"):
                submit_tags.add(tag)
    # tiers as of the START of this submission -- advancement checks are
    # matched against this fixed snapshot for every question below, which is
    # what makes the per-(submit, tag) dedupe correct.
    starting_tiers = crud.get_tiers_for_tags(db, user_id, submit_tags) if submit_tags else {}
    advanced_this_submit = set()

    for i, question_data in enumerate(list_of_question_data):
        question_type = question_data.get("question_type", "")
        for tag in question_data.get("tags", []):
            if tag in META_TAGS or tag.startswith("unit_"):
                continue
            crud.update_after_answer_for_question(db, user_id, tag, question_type, is_correct[i])

            # exposure on the word's current tier's own question types
            # advances it one tier, right or wrong; a lower-tier (downshifted)
            # question never advances it. At most one advance per tag here.
            if tag not in advanced_this_submit:
                current_tier = starting_tiers.get(tag, 1)
                if question_type in TIER_QUESTION_TYPES.get(current_tier, set()):
                    crud.advance_tier(db, user_id, tag)
                    advanced_this_submit.add(tag)

        if question_type == "speaking vocab":
            for sound in _tag_sounds(question_data.get("question", "")):
                crud.record_sound_attempt(db, user_id, sound, is_correct[i])

    unit_test_result = "unit test not taken"

    if is_unit_test:
        num_correct = sum(is_correct)
        needed = PERCENTAGE_TO_PASS_UNIT_TEST * NUM_OF_UNIT_TEST_QUESTIONS
        if num_correct >= needed:
            user_unit = crud.get_user(db, user_id).current_unit
            crud.graduate_unit(db, user_id, user_unit)
            unit_test_result = "unit test passed"
        else:
            unit_test_result = "unit test failed"

    return {"user_id": user_id, "unit_test_result": unit_test_result}


@router.get("/api/debug/{user_id}")
def debug(user_id: int, db: Session = Depends(get_db)):
    user = crud.get_user(db, user_id)
    unit_tags = unit_to_vocab_tags_dict.get(user.current_unit, set())
    all_records = get_collapsed_progress(db, user_id)
    unit_records = [r for r in all_records if r.tag in unit_tags]
    graduated = is_unit_graduated(unit_records, unit_tags)
    graduated_units = crud.get_graduated_units(db, user_id)
    session = generate_practice_session(db, user_id, user.current_unit, graduated_units)
    tiers = crud.get_tiers_for_tags(db, user_id, unit_tags)
    return {
        "current_unit": user.current_unit,
        "graduated_units": user.graduated_units,
        "unit_tags_count": len(unit_tags),
        "unit_ready_to_graduate": graduated,
        "questions_found": len(session.question_set),
        "sample_question_types": list(set(q["question_type"] for q in session.question_set)),
        "sample_units": sorted(set(q.get("unit") for q in session.question_set)),
        "sample_correct_counts": [
            {"tag": r.tag, "correct_count": r.correct_count, "tier": tiers.get(r.tag, 1)}
            for r in unit_records
        ],
    }


@router.get("/api/progress/{user_id}")
def get_progress(user_id: int, db: Session = Depends(get_db)):
    user = crud.get_user(db, user_id)
    user_unit = user.current_unit
    graduated_units = crud.get_graduated_units(db, user_id)
    all_records = get_collapsed_progress(db, user_id)
    record_map = {r.tag: r for r in all_records}

    unit_progress = {}
    for unit_str in unit_questions.keys():
        unit = int(unit_str)
        unit_tags = unit_to_vocab_tags_dict.get(unit, set())
        if not unit_tags:
            continue

        total = len(unit_tags)
        graduated_tags = sum(
            1 for tag in unit_tags
            if record_map.get(tag) and record_map[tag].correct_count >= GRADUATION_THRESHOLD
        )
        avg_correct = (
            sum(record_map[tag].correct_count for tag in unit_tags if tag in record_map) / total
            if total > 0 else 0
        )

        unit_progress[unit_str] = {
            "unit": unit,
            "total_tags": total,
            "graduated_tags": graduated_tags,
            "progress_pct": round(graduated_tags / total * 100) if total > 0 else 0,
            "avg_correct_count": round(avg_correct, 1),
            "is_graduated": unit in graduated_units,
            "is_current": unit == user_unit,
        }

    current_unit_tags = unit_to_vocab_tags_dict.get(user_unit, set())
    current_unit_tiers = crud.get_tiers_for_tags(db, user_id, current_unit_tags)
    current_unit_words = sorted([
        {
            "tag": tag,
            "correct_count": record_map[tag].correct_count if tag in record_map else 0,
            "tier": current_unit_tiers.get(tag, 1),
        }
        for tag in current_unit_tags
    ], key=lambda x: x["tag"])

    return {
        "user_id": user_id,
        "current_unit": user_unit,
        "graduated_units": list(graduated_units),
        "unit_progress": unit_progress,
        "current_unit_words": current_unit_words,
    }


@router.get("/api/lookup/{hanzi}")
def lookup(hanzi: str):
    if hanzi in hsk1_dictionary:
        entry = hsk1_dictionary[hanzi]
        return {"hanzi": hanzi, "pinyin": entry["pinyin"], "english": entry["english"]}

    from pypinyin import pinyin, Style
    try:
        result = pinyin(hanzi, style=Style.TONE3, heteronym=False)
        py = ''.join([s[0] for s in result]).lower()
    except Exception:
        py = None
    return {"hanzi": hanzi, "pinyin": py, "english": None}
