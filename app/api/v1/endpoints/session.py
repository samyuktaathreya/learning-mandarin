from fastapi import APIRouter, Depends, Body, HTTPException
from sqlalchemy.orm import Session
from database import SessionLocal, inverted_index, tags_to_unit_dict, unit_to_vocab_tags_dict, unit_questions, META_TAGS, hsk1_dictionary, word_to_pinyin
from schemas.user import SessionResponse
from pinyin_utils import split_pinyin_sounds, GATED_INITIALS, GATED_FINALS
from models.user import QuestionTip
import crud
from datetime import datetime
import random
import math
from session_log import log_session

router = APIRouter()

# ----------------------------- CONSTANTS -----------------------------

NUM_OF_UNIT_TEST_QUESTIONS = 20
PERCENTAGE_TO_PASS_UNIT_TEST = 0.80
GRADUATION_THRESHOLD = 3          # collapsed (min-facet) correct_count needed to consider a word graduated
SESSION_SIZE = 10                 # target session length; review can push it higher (see generate_practice_session)
REVIEW_THRESHOLD = 0.80           # below this decayed strength, a review-eligible word is "due" for review
MAX_SAME_TAG_PER_SESSION = 2      # per-tag cap within the tier-question portion of a session

# Selection Weight & Tier Balancing
WEIGHT_FLOOR = 0.20               # min weight floor so high min_count tags are never starved out
TIER_BONUS_FACTOR = 0.50          # bonus weight per tier step lagging behind MAX_TIER_FOR_REVIEW

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

QUESTION_TYPE_TO_TIER = {
    qt: tier for tier, qtypes in TIER_QUESTION_TYPES.items() for qt in qtypes
}

# A tier-4 word is served a tier-3 question this fraction of the time (a
# "downshift" back to sentence-adjacent practice) instead of tier 4.
# Answering on the downshifted (lower-tier) type does NOT advance the word --
# only an answer on the word's actual current tier counts as exposure.
TIER4_DOWNSHIFT_PROBABILITY = 0.20

# Once fewer than this many current-unit words remain ungraduated, the unit
# is in its "final push" -- tier-4 words stop downshifting so every serve
# pushes directly toward graduation.
FINAL_PUSH_UNGRADUATED_THRESHOLD = 5

# Struggle-based selection (uses StrengthTable.miss_count, the Option-B recent
# struggle signal). A word's selection weight gets a bonus proportional to its
# collapsed (max across facets) miss_count, so words you keep missing surface
# more often. And a struggling word (miss >= MISS_DOWNSHIFT_THRESHOLD) is always
# served one tier EASIER, so it gets consistent gentle practice. It can't advance
# while downshifted (advancement needs a clean answer AT the word's real tier),
# so the only way out is answering cleanly until miss_count decays back below the
# threshold -- at which point downshift stops and it sees its real tier again.
MISS_WEIGHT_FACTOR = 1.0            # added weight per point of miss_count
MISS_DOWNSHIFT_THRESHOLD = 2        # miss_count at/above which the word is downshifted

# The tier a word must reach before it can enter per-word review. == crud.MAX_TIER.
MAX_TIER_FOR_REVIEW = 4

# Tier 3/4 question types, split by the facet they exercise (derived from
# crud.QUESTION_TYPE_FACETS). Review is sentence-level only, so tier 1/2 types
# never appear here. "listening sentence" trains both facets, so it's in both
# lists. Used by the review picker for weak-facet-first selection.
REVIEW_TYPES_BY_FACET = {
    "pinyin":    ["speaking sentence", "listening sentence"],
    "character": ["translate chinese sentence to english",
                  "translate english sentence to chinese",
                  "fill in the blank",
                  "listening sentence"],
}

# ----------------------------- DB DEPENDENCY -----------------------------

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# ----------------------------- FACET COLLAPSE -----------------------------

class _CollapsedRecord:
    __slots__ = ("tag", "correct_count", "stability", "last_practice")

    def __init__(self, tag, correct_count, stability, last_practice):
        self.tag = tag
        self.correct_count = correct_count
        self.stability = stability
        self.last_practice = last_practice


def collapse_facets(records):
    """[(tag,facet)-rows] -> [one _CollapsedRecord per tag], min across facets."""
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
    return collapse_facets(crud.get_progress_by_user(db, user_id))


def is_unit_graduated(db: Session, user_id: int, tag_records: list, unit_tags: set) -> bool:
    record_map = {r.tag: r for r in tag_records}
    tiers = crud.get_tiers_for_tags(db, user_id, unit_tags)
    for tag in unit_tags:
        record = record_map.get(tag)
        if not record or record.correct_count < GRADUATION_THRESHOLD:
            return False
        if tiers.get(tag, 1) < MAX_TIER_FOR_REVIEW:
            return False
    return True

# ----------------------------- SOUND TAGGING -----------------------------

def _tag_sounds(tag: str) -> set:
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

def _tier_types_for_facet(tier: int, facet: str) -> list:
    return [qt for qt in TIER_QUESTION_TYPES[tier]
            if facet in crud.QUESTION_TYPE_FACETS.get(qt, [])]

def _active_tier_for_serve(tier: int, final_push: bool) -> int:
    if tier >= 4 and not final_push:
        return 3 if random.random() < TIER4_DOWNSHIFT_PROBABILITY else 4
    return tier

def _type_cap_for_tier(tier: int) -> int:
    n_types = len(TIER_QUESTION_TYPES[tier])
    return math.ceil(SESSION_SIZE / n_types)

def generate_tier_questions(db: Session, user_id: int, unit: int, tiers: dict):
    unit_tags = unit_to_vocab_tags_dict.get(unit, set())
    if not unit_tags:
        return [], "no_unit_tags", {}

    facet_counts = {t: {"character": 0, "pinyin": 0} for t in unit_tags}
    facet_miss = {t: {"character": 0, "pinyin": 0} for t in unit_tags}
    for r in crud.get_progress_by_user(db, user_id):
        if r.tag in facet_counts and r.facet in facet_counts[r.tag]:
            facet_counts[r.tag][r.facet] = r.correct_count
            facet_miss[r.tag][r.facet] = r.miss_count or 0

    min_counts = {t: min(facet_counts[t]["character"], facet_counts[t]["pinyin"])
                  for t in unit_tags}
    miss_counts = {t: max(facet_miss[t]["character"], facet_miss[t]["pinyin"])
                   for t in unit_tags}

    ungraduated = sum(1 for t in unit_tags if min_counts[t] < GRADUATION_THRESHOLD)
    final_push = ungraduated < FINAL_PUSH_UNGRADUATED_THRESHOLD

    seen_counts = crud.get_seen_question_counts(db, user_id)

    used_ids = set()
    tag_counts = {}
    type_counts = {}
    picks = []

    def _ordered_types(tag: str, tier: int) -> list:
        char_c = facet_counts[tag]["character"]
        pin_c = facet_counts[tag]["pinyin"]
        if char_c == pin_c:
            types = list(TIER_QUESTION_TYPES[tier])
            random.shuffle(types)
            return types
        weak = "character" if char_c < pin_c else "pinyin"
        preferred = _tier_types_for_facet(tier, weak)
        rest = [qt for qt in TIER_QUESTION_TYPES[tier] if qt not in preferred]
        random.shuffle(preferred)
        random.shuffle(rest)
        return preferred + rest

    def _draw(respect_type_cap: bool) -> bool:
        pool = [t for t in unit_tags if tag_counts.get(t, 0) < MAX_SAME_TAG_PER_SESSION]
        if not pool:
            return False

        # Selection weights with weight floor clamp and lower-tier priority bonus
        weights = []
        for t in pool:
            base_weight = max(1.0 / (min_counts[t] + 1), WEIGHT_FLOOR)
            tier_bonus = (MAX_TIER_FOR_REVIEW - tiers.get(t, 1)) * TIER_BONUS_FACTOR
            w = base_weight + tier_bonus + (MISS_WEIGHT_FACTOR * miss_counts[t])
            weights.append(w)

        tag = random.choices(pool, weights=weights, k=1)[0]

        serve_tier = _active_tier_for_serve(tiers.get(tag, 1), final_push)
        if miss_counts[tag] >= MISS_DOWNSHIFT_THRESHOLD and serve_tier > 1:
            serve_tier -= 1
        cap = _type_cap_for_tier(serve_tier)

        for qt in _ordered_types(tag, serve_tier):
            if respect_type_cap and type_counts.get(qt, 0) >= cap:
                continue
            avail = [
                q for q in inverted_index.get(tag, [])
                if q["question_type"] == qt
                and q["id"] not in used_ids
                and q.get("unit") == unit
            ]
            if not avail:
                continue
            unseen = [q for q in avail if q["id"] not in seen_counts]
            if unseen:
                chosen = random.choice(unseen)
            else:
                min_shown = min(seen_counts.get(q["id"], 0) for q in avail)
                least_shown = [q for q in avail if seen_counts.get(q["id"], 0) == min_shown]
                chosen = random.choice(least_shown)
            picks.append((chosen, tag))
            used_ids.add(chosen["id"])
            tag_counts[tag] = tag_counts.get(tag, 0) + 1
            type_counts[qt] = type_counts.get(qt, 0) + 1
            for f in crud.QUESTION_TYPE_FACETS.get(qt, []):
                if f in facet_counts[tag]:
                    facet_counts[tag][f] += 1
            return True
        return False

    max_attempts = SESSION_SIZE * 25

    attempts = 0
    while len(picks) < SESSION_SIZE and attempts < max_attempts:
        attempts += 1
        _draw(respect_type_cap=True)

    if len(picks) >= SESSION_SIZE:
        return picks, "filled", min_counts

    attempts = 0
    while len(picks) < SESSION_SIZE and attempts < max_attempts:
        attempts += 1
        _draw(respect_type_cap=False)

    if len(picks) >= SESSION_SIZE:
        stop = "filled_after_relax"
    elif not [t for t in unit_tags if tag_counts.get(t, 0) < MAX_SAME_TAG_PER_SESSION]:
        stop = "short_pool_exhausted"
    else:
        stop = "short_no_questions"
    return picks, stop, min_counts

# ----------------------------- REVIEW (per-word, unit-agnostic) -----------------------------

def is_facet_review_eligible(tier: int, facet_count: int) -> bool:
    return tier >= MAX_TIER_FOR_REVIEW and facet_count >= GRADUATION_THRESHOLD


def _all_review_eligible_facets(db: Session, user_id: int) -> list:
    """Every (word, facet) pair that is SERVING-eligible for review: tier 4 +
    facet_count >= GRADUATION_THRESHOLD, AND the word's teaching unit is
    strictly before the current unit. A word still being learned in the current
    unit is never review-eligible -- review is for consolidated, past-unit
    material only, and its sentences are guaranteed to contain only known
    words."""
    progress = crud.get_progress_by_user(db, user_id)
    if not progress:
        return []
 
    tags = {r.tag for r in progress}
    tiers = crud.get_tiers_for_tags(db, user_id, tags)
    current_unit = crud.get_user(db, user_id).current_unit
 
    eligible = []
    for r in progress:
        if r.facet not in ("character", "pinyin"):
            continue
        if not is_facet_review_eligible(tiers.get(r.tag, 1), r.correct_count):
            continue
        teaching_unit = tags_to_unit_dict.get(r.tag)
        if teaching_unit is None or teaching_unit >= current_unit:
            continue  # word's own unit isn't finished -- not review-eligible yet
        eligible.append((r.tag, r.facet, r))
    return eligible


def _due_review_facets(db: Session, user_id: int) -> list:
    now = datetime.utcnow()
    scored = []
    for tag, facet, r in _all_review_eligible_facets(db, user_id):
        strength = 0.5 ** ((now - r.last_practice).total_seconds() / 86400 / r.stability)
        if strength < REVIEW_THRESHOLD:
            scored.append((tag, facet, strength))
    scored.sort(key=lambda x: x[2])
    return [(tag, facet) for tag, facet, _ in scored]


def _pick_review_question(tag: str, facet: str, used_ids: set, max_unit: int, seen_counts: dict):
    # The 20% "everything else" bucket. 
    # EXCLUDED: "speaking vocab", "translate chinese word to english", "translate chinese sentence to english".
    # INCLUDED: Only types where the answer is produced in Pinyin, Characters, or Spoken Chinese.
    other_types = [
        "translate english word to chinese",
        "translate english sentence to chinese",
        "speaking sentence",
        "fill in the blank",
        "listening sentence",
        "listening vocab"
    ]
    
    # Enforce the 80/20 split
    if random.random() < 0.80:
        random.shuffle(other_types)
        # 80% chance: Try Pinyin transcription first. If none exist for this word, fallback to others.
        ordered_types = ["transcribe word to pinyin"] + other_types
    else:
        random.shuffle(other_types)
        # 20% chance: Try the other output-based types first. Fallback to Pinyin if none exist.
        ordered_types = other_types + ["transcribe word to pinyin"]

    for qt in ordered_types:
        pool = [
            q for q in inverted_index.get(tag, [])
            if q["question_type"] == qt
            and q["id"] not in used_ids
            and q.get("unit", 0) <= max_unit
        ]
        if not pool:
            continue
            
        unseen = [q for q in pool if q["id"] not in seen_counts]
        if unseen:
            return random.choice(unseen)
            
        min_shown = min(seen_counts.get(q["id"], 0) for q in pool)
        least_shown = [q for q in pool if seen_counts.get(q["id"], 0) == min_shown]
        return random.choice(least_shown)
        
    return None


def generate_review_questions(db, user_id, used_ids, limit=None):
    max_unit = crud.get_user(db, user_id).current_unit - 1
    seen_counts = crud.get_seen_question_counts(db, user_id)
    picks = []
    for tag, facet in _due_review_facets(db, user_id):
        if limit is not None and len(picks) >= limit:
            break
        q = _pick_review_question(tag, facet, used_ids, max_unit, seen_counts)
        if q:
            picks.append((q, tag))
            used_ids.add(q["id"])
    return picks


def _project_strength(r, days_ahead: float) -> float:
    now = datetime.utcnow()
    elapsed_days = (now - r.last_practice).total_seconds() / 86400 + days_ahead
    return 0.5 ** (elapsed_days / r.stability)


def review_due_word_count(db, user_id) -> int:
    return len({tag for tag, _facet in _due_review_facets(db, user_id)})


def review_due_tomorrow_word_count(db, user_id) -> int:
    tags = set()
    for tag, facet, r in _all_review_eligible_facets(db, user_id):
        if _project_strength(r, 1.0) < REVIEW_THRESHOLD:
            tags.add(tag)
    return len(tags)


def generate_practice_session(db, user_id, unit) -> SessionResponse:
    used_ids = set()
    review_picks = generate_review_questions(db, user_id, used_ids, limit=SESSION_SIZE)

    remaining = SESSION_SIZE - len(review_picks)
    tier_picks, stop_reason, min_counts = [], "pure_review", {}
    tiers = {}
    if remaining > 0:
        tiers = crud.get_tiers_for_tags(db, user_id, unit_to_vocab_tags_dict.get(unit, set()))
        tier_picks, stop_reason, min_counts = generate_tier_questions(db, user_id, unit, tiers)
        tier_picks = [p for p in tier_picks if p[0]["id"] not in used_ids][:remaining]

    log_session(user_id, unit, tier_picks, review_picks, tiers, min_counts, stop_reason)
    question_set = [q for q, _ in review_picks] + [q for q, _ in tier_picks]
    random.shuffle(question_set)
    return SessionResponse(user_id=user_id, session_type="practice_session", question_set=question_set)


def generate_review_session(db, user_id) -> SessionResponse:
    used_ids = set()
    review_picks = generate_review_questions(db, user_id, used_ids, limit=SESSION_SIZE)

    log_session(user_id, None, [], review_picks, {}, {}, "review_session")

    question_set = [q for q, _ in review_picks]
    random.shuffle(question_set)
    return SessionResponse(user_id=user_id, session_type="review_session", question_set=question_set)

UNIT_TEST_TIER_WEIGHTS = {1: 1, 2: 1, 3: 4, 4: 4}


def generate_unit_test(user_id: int, unit: int) -> SessionResponse:
    eligible = [q for q in unit_questions.get(str(unit), [])
                if q["question_type"] in ALL_TIER_QUESTION_TYPES]
    if not eligible:
        return SessionResponse(user_id=user_id, session_type="unit_test", question_set=[])

    weights = []
    for q in eligible:
        tier = next((t for t, types in TIER_QUESTION_TYPES.items()
                     if q["question_type"] in types), 1)
        weights.append(UNIT_TEST_TIER_WEIGHTS[tier])

    selected, pool, pool_weights = [], list(eligible), list(weights)
    for _ in range(min(NUM_OF_UNIT_TEST_QUESTIONS, len(pool))):
        pick = random.choices(range(len(pool)), weights=pool_weights, k=1)[0]
        selected.append(pool.pop(pick))
        pool_weights.pop(pick)

    return SessionResponse(user_id=user_id, session_type="unit_test", question_set=selected)

# ----------------------------- TIPS -----------------------------

def attach_tips(db: Session, session_response: SessionResponse) -> SessionResponse:
    texts = set()
    for q in session_response.question_set:
        if q.get("question"):
            texts.add(q["question"])
        if q.get("answer"):
            texts.add(q["answer"])
    if not texts:
        return session_response

    rows = db.query(QuestionTip).filter(QuestionTip.key_value.in_(texts)).all()
    tip_map = {(r.key_type, r.key_value): r.tip for r in rows}

    for q in session_response.question_set:
        tip = tip_map.get(("question", q.get("question")))
        if tip is None:
            tip = tip_map.get(("answer", q.get("answer")))
        if tip is not None:
            q["tip"] = tip

    return session_response


# ----------------------------- ENDPOINTS -----------------------------

@router.get("/api/generate_session/{user_id}", response_model=SessionResponse)
def generate_session(user_id: int, mode: str = "sentence", skip_review: bool = False,
                     db: Session = Depends(get_db)):
    user = crud.get_user(db, user_id)
    user_unit = user.current_unit

    unit_tags = unit_to_vocab_tags_dict.get(user_unit, set())
    all_records = get_collapsed_progress(db, user_id)
    unit_records = [r for r in all_records if r.tag in unit_tags]

    if is_unit_graduated(db, user_id, unit_records, unit_tags):
        return attach_tips(db, generate_unit_test(user_id, user_unit))

    if not skip_review and review_due_word_count(db, user_id) > 0:
        review_session = generate_review_session(db, user_id)
        if review_session.question_set:
            return attach_tips(db, review_session)

    return attach_tips(db, generate_practice_session(db, user_id, user_unit))


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

    starting_tiers = crud.get_tiers_for_tags(db, user_id, submit_tags) if submit_tags else {}

    starting_facet_counts = {t: {"character": 0, "pinyin": 0} for t in submit_tags}
    for r in crud.get_progress_by_user(db, user_id):
        if r.tag in starting_facet_counts and r.facet in starting_facet_counts[r.tag]:
            starting_facet_counts[r.tag][r.facet] = r.correct_count
    starting_facet_eligible = {
        t: {
            facet: is_facet_review_eligible(starting_tiers.get(t, 1),
                                            starting_facet_counts[t][facet])
            for facet in ("character", "pinyin")
        }
        for t in submit_tags
    }

    facet_attempts = {}
    facet_misses = {}
    tag_misses = {}

    for i, question_data in enumerate(list_of_question_data):
        question_type = question_data.get("question_type", "")
        correct = is_correct[i]

        crud.record_question_shown(db, user_id, question_data.get("id"))

        for tag in question_data.get("tags", []):
            if tag in META_TAGS or tag.startswith("unit_"):
                continue
            crud.update_after_answer_for_question(
                db, user_id, tag, question_type, correct,
                facet_eligible=starting_facet_eligible.get(tag, {}),
            )

            for facet in crud.facets_for_question_type(question_type):
                key = (tag, facet)
                facet_attempts[key] = facet_attempts.get(key, 0) + 1
                if not correct:
                    facet_misses[key] = facet_misses.get(key, 0) + 1
            if not correct:
                tag_misses[tag] = tag_misses.get(tag, 0) + 1

        if question_type == "speaking vocab":
            for sound in _tag_sounds(question_data.get("question", "")):
                crud.record_sound_attempt(db, user_id, sound, is_correct[i])

    # Passive Tier Advancement Check:
    # A tag qualifies for advancement if it appears in a question whose tier meets or
    # exceeds the tag's current tier (and was answered cleanly across the session).
    tags_served_at_current_tier = set()
    for question_data in list_of_question_data:
        qt = question_data.get("question_type", "")
        for tag in question_data.get("tags", []):
            if tag in META_TAGS or tag.startswith("unit_"):
                continue
            # EXACT tier match: the question type must belong to this tag's
            # own current tier. A higher-tier question (e.g. a sentence the tag
            # is merely a bystander in) does NOT advance it.
            if qt in TIER_QUESTION_TYPES.get(starting_tiers.get(tag, 1), set()):
                tags_served_at_current_tier.add(tag)
 
    for tag in submit_tags:
        if tag in tags_served_at_current_tier and tag_misses.get(tag, 0) == 0:
            crud.advance_tier(db, user_id, tag)

    seen_facets = set(facet_attempts.keys())
    for (tag, facet) in seen_facets:
        misses = facet_misses.get((tag, facet), 0)
        delta = misses if misses > 0 else -1
        crud.update_miss_count(db, user_id, tag, facet, delta,
                               attempts=facet_attempts[(tag, facet)])

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
    graduated = is_unit_graduated(db, user_id, unit_records, unit_tags)
    session = generate_practice_session(db, user_id, user.current_unit)
    tiers = crud.get_tiers_for_tags(db, user_id, unit_tags)

    eligible = _all_review_eligible_facets(db, user_id)
    due = _due_review_facets(db, user_id)

    return {
        "current_unit": user.current_unit,
        "graduated_units": user.graduated_units,
        "unit_tags_count": len(unit_tags),
        "unit_ready_to_graduate": graduated,
        "review_eligible_facet_count": len(eligible),
        "review_due_facet_count": len(due),
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
        "review_due_word_count": review_due_word_count(db, user_id),
        "review_due_tomorrow_word_count": review_due_tomorrow_word_count(db, user_id),
    }


@router.get("/api/unit_detail/{user_id}/{unit}")
def unit_detail(user_id: int, unit: int, db: Session = Depends(get_db)):
    user = crud.get_user(db, user_id)
    graduated_units = crud.get_graduated_units(db, user_id)
    unlocked = (unit == user.current_unit) or (unit in graduated_units)
    if not unlocked:
        return {"unit": unit, "locked": True, "words": []}

    unit_tags = unit_to_vocab_tags_dict.get(unit, set())
    tiers = crud.get_tiers_for_tags(db, user_id, unit_tags)

    rows = {}
    for r in crud.get_progress_by_user(db, user_id):
        if r.tag in unit_tags and r.facet in ("character", "pinyin"):
            rows[(r.tag, r.facet)] = r

    now = datetime.utcnow()
    is_current = unit == user.current_unit

    def facet_detail(tag, facet):
        r = rows.get((tag, facet))
        if not r:
            return {"correct_count": 0, "stability": None, "strength": None,
                    "is_review_eligible": False, "is_due": False}
        strength = 0.5 ** ((now - r.last_practice).total_seconds() / 86400 / r.stability)
        # a current-unit word is never review-eligible regardless of tier/count,
        # since its own unit isn't finished (mirrors _all_review_eligible_facets)
        eligible = (not is_current) and is_facet_review_eligible(
            tiers.get(tag, 1), r.correct_count)
        return {
            "correct_count": r.correct_count,
            "stability": round(r.stability, 2),
            "strength": round(strength, 3),
            "is_review_eligible": eligible,
            "is_due": eligible and strength < REVIEW_THRESHOLD,
        }

    words = sorted([
        {
            "tag": tag,
            "tier": tiers.get(tag, 1),
            "character": facet_detail(tag, "character"),
            "pinyin": facet_detail(tag, "pinyin"),
        }
        for tag in unit_tags
    ], key=lambda w: w["tag"])

    return {
        "unit": unit,
        "locked": False,
        "is_current": unit == user.current_unit,
        "is_graduated": unit in graduated_units,
        "words": words,
    }


@router.post("/api/tips")
def save_tip(payload: dict = Body(...), db: Session = Depends(get_db)):
    key_type = payload.get("key_type")
    key_value = (payload.get("key_value") or "").strip()
    tip_text = (payload.get("tip") or "").strip()

    if key_type not in ("question", "answer"):
        raise HTTPException(status_code=400, detail="key_type must be 'question' or 'answer'")
    if not key_value or not tip_text:
        raise HTTPException(status_code=400, detail="key_value and tip are required")

    row = db.query(QuestionTip).filter(
        QuestionTip.key_type == key_type,
        QuestionTip.key_value == key_value,
    ).first()
    if row:
        row.tip = tip_text
        row.updated_at = datetime.utcnow()
    else:
        row = QuestionTip(key_type=key_type, key_value=key_value, tip=tip_text)
        db.add(row)
    db.commit()

    return {"key_type": key_type, "key_value": key_value, "tip": tip_text}


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