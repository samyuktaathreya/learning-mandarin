from fastapi import APIRouter, Depends, Body, HTTPException
from sqlalchemy.orm import Session
from database import SessionLocal, inverted_index, tags_to_unit_dict, unit_to_tags_dict, unit_to_vocab_tags_dict, unit_questions, META_TAGS, hsk1_dictionary, word_to_pinyin
from schemas.user import SessionResponse
from pinyin_utils import split_pinyin_sounds, GATED_INITIALS, GATED_FINALS
import crud
from datetime import datetime
import random

router = APIRouter()

# ----------------------------- CONSTANTS -----------------------------

MIN_UNIT = 3
NUM_OF_UNIT_TEST_QUESTIONS = 20
PERCENTAGE_TO_PASS_UNIT_TEST = 0.80
MAX_SAME_TAG_PER_SESSION = 2
GRADUATION_THRESHOLD = 3

SPEAKING_TYPES = {
    "speaking vocab",
    "speaking sentence",
}

TIER_1_TYPES = {
    "listening vocab",
    "translate chinese word to english",
    "fill in the blank",
    "transcribe word to pinyin",
    "speaking vocab",
}
TIER_2_TYPES = {
    "translate chinese sentence to english",
    "listening sentence",
    "speaking sentence",
    "translate english word to chinese",
}
TIER_3_TYPES = {
    "translate english sentence to chinese",
}

TIER_2_UNLOCK = 2
TIER_3_UNLOCK = 4
TIER_1_DEPRIORITY_THRESHOLD = 2

QUESTION_TYPE_PRIORITY = [
    "listening sentence",
    "speaking sentence",
    "speaking vocab",
    "listening vocab",
    "transcribe word to pinyin",
    "translate english sentence to chinese",
    "translate english word to chinese",
    "fill in the blank",
    "translate chinese sentence to english",
    "translate chinese word to english",
]

PRIORITY_WEIGHTS = {
    qt: len(QUESTION_TYPE_PRIORITY) - i
    for i, qt in enumerate(QUESTION_TYPE_PRIORITY)
}

UNIT_TEST_QUESTION_TYPES = TIER_1_TYPES | TIER_2_TYPES | TIER_3_TYPES

# --- mixed-session tuning ---
VARIETY_WEIGHT = 6.0            # how hard type-variety fights count-priority (higher = more varied)
TYPE_DECAY = 0.6               # each pick of a type multiplies its variety bonus by this
REVIEW_THRESHOLD = 0.80       # below this strength counts as reviewable / "due" (Anki-style, one threshold for all activities)
REVIEW_WEAKEST_FRACTION = 0.8  # of review slots, this share goes to weakest-first; rest random-below-threshold
SESSION_SIZE = 10

# --- tab / phase system ---
# A unit progresses through four phases:
#   listening -> c2e -> e2c -> sentences
# listening: hear -> type pinyin (pinyin facet). One completed listening
#   session advances to c2e.
# c2e (chinese->english): one-time teaching cards. Full character-facet strength
#   update (feeds sentence review) PLUS a CharacterExposure row. Covering every
#   coverable word advances to e2c.
# e2c (english->chinese): Anki-style new+due, character facet. Covering every
#   coverable word (times_seen >= 2) advances to sentences.
# sentences: the mixed practice session / unit test.
PHASE_LISTENING = "listening"
PHASE_C2E = "c2e"
PHASE_E2C = "e2c"
PHASE_SENTENCES = "sentences"
PHASE_ORDER = [PHASE_LISTENING, PHASE_C2E, PHASE_E2C, PHASE_SENTENCES]

# Modes the frontend requests. The Vocab button always sends MODE_VOCAB; the
# backend chooses c2e vs e2c from the user's current phase.
MODE_LISTENING = "listening"
MODE_VOCAB = "vocab"
MODE_SENTENCE = "sentence"

LISTENING_QUESTION_TYPE = "listening vocab"
C2E_QUESTION_TYPE = "translate chinese word to english"
E2C_QUESTION_TYPE = "translate english word to chinese"

# Facet each Anki-style listening session reads. (Vocab handles its own facets
# inline; only listening goes through the shared MODE_FACET lookup.)
MODE_FACET = {
    MODE_LISTENING: "pinyin",
}

# strength -> review fraction. Flat-ish in normal use, ramps to 1.0 only at
# the catastrophic-decay extreme (user hasn't touched the app in a long time).
_RATIO_ANCHORS = [
    (0.00, 1.00),
    (0.15, 0.90),
    (0.30, 0.65),
    (0.50, 0.40),
    (0.80, 0.15),
    (1.00, 0.15),
]


def review_fraction_from_strength(avg_strength: float) -> float:
    """Piecewise-linear interpolation over the anchor points above."""
    s = max(0.0, min(1.0, avg_strength))
    for i in range(len(_RATIO_ANCHORS) - 1):
        s0, f0 = _RATIO_ANCHORS[i]
        s1, f1 = _RATIO_ANCHORS[i + 1]
        if s0 <= s <= s1:
            if s1 == s0:
                return f0
            t = (s - s0) / (s1 - s0)
            return f0 + t * (f1 - f0)
    return _RATIO_ANCHORS[-1][1]

# ----------------------------- DB DEPENDENCY -----------------------------

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# ----------------------------- FACET COLLAPSE -----------------------------
# StrengthTable stores two rows per (user, tag): a "character" facet and a
# "pinyin" facet. The generation/graduation logic below is written against ONE
# record per tag, so we collapse the two facets to a single synthetic record
# per tag using the MINIMUM across facets -- a word counts as "known" only as
# well as its weaker aspect. This keeps sentence review / graduation honest:
# strong meaning but weak pinyin (or vice versa) is still weak.

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

# ----------------------------- TIER HELPERS -----------------------------

def get_allowed_types(correct_count: int) -> set:
    if correct_count >= TIER_3_UNLOCK:
        return TIER_1_TYPES | TIER_2_TYPES | TIER_3_TYPES
    elif correct_count >= TIER_2_UNLOCK:
        return TIER_1_TYPES | TIER_2_TYPES
    else:
        return TIER_1_TYPES


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

# ----------------------------- SRS HELPERS -----------------------------

def get_srs_strength_scores(db: Session, user_id: int, unit_min: int, unit_max: int, graduated_units: set):
    records = get_collapsed_progress(db, user_id)
    now = datetime.utcnow()
    scores = []

    for record in records:
        tag = record.tag
        if tag not in tags_to_unit_dict:
            continue
        unit = tags_to_unit_dict[tag]
        if unit < unit_min or unit > unit_max:
            continue
        if unit not in graduated_units:
            continue

        delta_t = (now - record.last_practice).total_seconds() / 86400
        strength = 0.5 ** (delta_t / record.stability)
        scores.append({"tag": tag, "strength": strength, "stability": record.stability})

    return scores

# ----------------------------- SPEAKING-SENTENCE SOUND GATE -----------------------------
# A sentence can contain a sound (zh/ch/sh/r/j/q/x/z/c, or the v/er/e finals --
# see GATED_INITIALS/GATED_FINALS in audio.py) the user has never had to
# pronounce in isolation. Block "speaking sentence" candidates that contain a
# locked sound and surface the speaking vocab question that teaches it instead.

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


def _sentence_tag_sounds(question: dict) -> dict:
    """tag -> gated sounds contributed by that tag, for every content tag in
    the question (sentence questions carry every constituent word as a tag,
    including words from earlier units, so this is the complete sound set --
    the sentence string itself is never parsed)."""
    result = {}
    for tag in question.get("tags", []):
        if tag in META_TAGS or tag.startswith("unit_"):
            continue
        sounds = _tag_sounds(tag)
        if sounds:
            result[tag] = sounds
    return result


def _gate_speaking_sentences(relevant_units: set, unlocked_sounds: set):
    """Scans speaking-sentence questions in the units actually reachable this
    session (current unit + graduated units) and returns:
      blocked_ids: question ids that contain a sound not yet unlocked
      prereq_tags: the words responsible, so their own speaking-vocab
        question can be injected as a prerequisite
    """
    blocked_ids = set()
    prereq_tags = set()
    for unit_str, questions in unit_questions.items():
        if int(unit_str) not in relevant_units:
            continue
        for q in questions:
            if q["question_type"] != "speaking sentence":
                continue
            for tag, sounds in _sentence_tag_sounds(q).items():
                if sounds - unlocked_sounds:
                    blocked_ids.add(q["id"])
                    prereq_tags.add(tag)
    return blocked_ids, prereq_tags


def _build_prereq_candidates(prereq_tags: set) -> list:
    """One 'speaking vocab' candidate per word that's blocking a sentence,
    scored to sort to the very front of the pool."""
    return [{
        "tag": tag,
        "question_type": "speaking vocab",
        "effective_count": -1000,
        "priority_weight": PRIORITY_WEIGHTS.get("speaking vocab", 1),
    } for tag in prereq_tags]


# ----------------------------- VARIETY SELECTOR -----------------------------

def _select_with_variety(candidates, n, unit_filter, used_ids, tag_counts, blocked_ids=frozenset()):
    """
    Pick up to n questions, blending two goals:
      - review weak words first (lower effective_count is better)
      - keep question types varied (a fresh/underused type gets a bonus that
        can override small count differences)
    """
    picked = []
    type_penalty = {}

    while len(picked) < n:
        best = None
        best_score = None
        best_avail = None

        for item in candidates:
            tag = item["tag"]
            qt = item["question_type"]
            if tag_counts.get(tag, 0) >= MAX_SAME_TAG_PER_SESSION:
                continue

            variety_bonus = VARIETY_WEIGHT * (TYPE_DECAY ** type_penalty.get(qt, 0))
            score = item["effective_count"] - variety_bonus - 0.001 * item["priority_weight"]

            if best_score is None or score < best_score:
                questions = inverted_index.get(tag, [])
                avail = [
                    q for q in questions
                    if q["question_type"] == qt
                    and q["id"] not in used_ids
                    and q["id"] not in blocked_ids
                    and (unit_filter is None or q.get("unit") in unit_filter)
                ]
                if avail:
                    best, best_score, best_avail = item, score, avail

        if best is None:
            break

        chosen = random.choice(best_avail)
        picked.append(chosen)
        used_ids.add(chosen["id"])
        tag_counts[best["tag"]] = tag_counts.get(best["tag"], 0) + 1
        type_penalty[best["question_type"]] = type_penalty.get(best["question_type"], 0) + 1

    return picked

# ----------------------------- SESSION GENERATORS -----------------------------

def _build_learning_candidates(unit, record_map):
    unit_tags = unit_to_tags_dict.get(unit, set())
    candidates = []
    for tag in unit_tags:
        record = record_map.get(tag)
        correct_count = record.correct_count if record else 0
        for qt in get_allowed_types(correct_count):
            if correct_count >= TIER_1_DEPRIORITY_THRESHOLD and qt in TIER_1_TYPES:
                effective_count = max(correct_count, 10)
            else:
                effective_count = correct_count
            candidates.append({
                "tag": tag,
                "question_type": qt,
                "effective_count": effective_count,
                "correct_count": correct_count,
                "priority_weight": PRIORITY_WEIGHTS.get(qt, 1),
            })
    return candidates


def generate_mixed_session(db: Session, user_id: int, unit: int, graduated_units: set):
    """
    Learning-mode session that blends current-unit material with review of
    graduated units. The review share scales with retention of previous units.
    """
    records = get_collapsed_progress(db, user_id)
    record_map = {r.tag: r for r in records}

    review_scores = get_srs_strength_scores(db, user_id, MIN_UNIT, 99, graduated_units)
    if review_scores:
        avg_strength = sum(s["strength"] for s in review_scores) / len(review_scores)
        review_frac = review_fraction_from_strength(avg_strength)
    else:
        review_frac = 0.0

    n_review = round(SESSION_SIZE * review_frac)
    n_review = min(n_review, len(review_scores))
    n_new = SESSION_SIZE - n_review

    used_ids = set()
    tag_counts = {}
    question_set = []

    unlocked_sounds = crud.get_unlocked_sounds(db, user_id)
    blocked_ids, prereq_tags = _gate_speaking_sentences({unit} | graduated_units, unlocked_sounds)

    if prereq_tags:
        prereq_candidates = _build_prereq_candidates(prereq_tags)
        injected = _select_with_variety(
            prereq_candidates, min(len(prereq_candidates), n_new),
            set(range(MIN_UNIT, unit + 1)), used_ids, tag_counts, blocked_ids,
        )
        question_set += injected
        n_new = max(0, n_new - len(injected))

    learn_candidates = _build_learning_candidates(unit, record_map)
    question_set += _select_with_variety(learn_candidates, n_new, {unit}, used_ids, tag_counts, blocked_ids)

    if n_review > 0:
        review_scores.sort(key=lambda x: x["strength"])
        weakest = [s["tag"] for s in review_scores]
        below = [s["tag"] for s in review_scores if s["strength"] < REVIEW_THRESHOLD]

        n_weak = round(n_review * REVIEW_WEAKEST_FRACTION)
        review_tag_order = weakest[:n_weak]
        random.shuffle(below)
        review_tag_order += [t for t in below if t not in review_tag_order][: n_review - len(review_tag_order)]

        review_candidates = []
        for tag in review_tag_order:
            for qt in QUESTION_TYPE_PRIORITY:
                review_candidates.append({
                    "tag": tag,
                    "question_type": qt,
                    "effective_count": 0,
                    "priority_weight": PRIORITY_WEIGHTS.get(qt, 1),
                })
        question_set += _select_with_variety(review_candidates, n_review, graduated_units, used_ids, tag_counts, blocked_ids)

    if len(question_set) < SESSION_SIZE:
        question_set += _select_with_variety(
            learn_candidates, SESSION_SIZE - len(question_set), {unit}, used_ids, tag_counts, blocked_ids
        )

    random.shuffle(question_set)
    return SessionResponse(user_id=user_id, session_type="practice_session", question_set=question_set)


def generate_unit_test(user_id: int, user_unit: int):
    eligible = [q for q in unit_questions.get(str(user_unit), []) if q["question_type"] in UNIT_TEST_QUESTION_TYPES]
    selected = random.sample(eligible, min(NUM_OF_UNIT_TEST_QUESTIONS, len(eligible)))
    return SessionResponse(user_id=user_id, session_type="unit_test", question_set=selected)


# ----------------------------- ANKI-STYLE TAB SESSIONS -----------------------------

def _due_review_tags(db: Session, user_id: int, facet: str, graduated_units: set) -> list:
    """Graduated-unit words whose strength for this facet has decayed below
    REVIEW_THRESHOLD -- i.e. 'due' in the Anki sense. Weakest first."""
    now = datetime.utcnow()
    rows = crud.get_progress_by_user(db, user_id, facet=facet)
    due = []
    for r in rows:
        unit = tags_to_unit_dict.get(r.tag)
        if unit is None or unit not in graduated_units:
            continue
        strength = 0.5 ** ((now - r.last_practice).total_seconds() / 86400 / r.stability)
        if strength < REVIEW_THRESHOLD:
            due.append((r.tag, strength))
    due.sort(key=lambda x: x[1])   # weakest first
    return [tag for tag, _ in due]


def _new_unit_tags_unseen(db: Session, user_id: int, unit: int, facet: str) -> list:
    """Current-unit vocab words not yet shown for this facet (times_seen == 0)."""
    seen = crud.get_seen_tags(db, user_id, facet)
    unit_tags = unit_to_vocab_tags_dict.get(unit, set())
    return [t for t in unit_tags if t not in seen]


def _pick_question(tag: str, question_type: str, unit_filter, used_ids: set):
    """One question of the given type for a tag, from the allowed units."""
    pool = [
        q for q in inverted_index.get(tag, [])
        if q["question_type"] == question_type
        and q["id"] not in used_ids
        and (unit_filter is None or q.get("unit") in unit_filter)
    ]
    return random.choice(pool) if pool else None


def generate_listening_session(db, user_id, unit, graduated_units):
    """Listening tab: hear -> type pinyin. New unseen current-unit words (cap
    SESSION_SIZE) + every due pinyin-facet review word, shuffled Anki-style."""
    facet = MODE_FACET[MODE_LISTENING]
    qtype = LISTENING_QUESTION_TYPE

    used_ids = set()
    question_set = []

    new_tags = _new_unit_tags_unseen(db, user_id, unit, facet)
    random.shuffle(new_tags)
    for tag in new_tags[:SESSION_SIZE]:
        q = _pick_question(tag, qtype, {unit}, used_ids)
        if q:
            question_set.append(q)
            used_ids.add(q["id"])

    for tag in _due_review_tags(db, user_id, facet, graduated_units):
        q = _pick_question(tag, qtype, graduated_units, used_ids)
        if q:
            question_set.append(q)
            used_ids.add(q["id"])

    random.shuffle(question_set)
    return SessionResponse(user_id=user_id, session_type="listening_session", question_set=question_set)


# ----------------------------- VOCAB TAB (c2e -> e2c) -----------------------------

def generate_vocab_session(db, user_id, unit, graduated_units):
    """Vocab tab. Serves c2e (chinese->english teaching cards) while the unit
    is in the c2e phase, then e2c (english->chinese, Anki-tracked) after."""
    user = crud.get_user(db, user_id)
    if user.unit_phase == PHASE_C2E:
        return _generate_c2e_session(db, user_id, unit)
    return _generate_e2c_session(db, user_id, unit, graduated_units)


def _generate_c2e_session(db, user_id, unit):
    """One-time teaching cards: unshown-on-c2e coverable words, no review."""
    unit_tags = unit_to_vocab_tags_dict.get(unit, set())
    c2e_coverable = _tags_with_question(unit_tags, C2E_QUESTION_TYPE)
    seen = crud.get_c2e_seen_tags(db, user_id)
    unseen = [t for t in c2e_coverable if t not in seen]
    random.shuffle(unseen)

    used_ids, question_set = set(), []
    for tag in unseen[:SESSION_SIZE]:
        q = _pick_question(tag, C2E_QUESTION_TYPE, {unit}, used_ids)
        if q:
            question_set.append(q); used_ids.add(q["id"])
    random.shuffle(question_set)
    return SessionResponse(user_id=user_id, session_type="vocab_session", question_set=question_set)


def _generate_e2c_session(db, user_id, unit, graduated_units):
    """english->chinese Anki-style: new unseen-on-e2c + due review."""
    facet = "character"
    used_ids, question_set = set(), []

    seen_e2c = crud.get_e2c_seen_tags(db, user_id)      # times_seen >= 2
    unit_tags = unit_to_vocab_tags_dict.get(unit, set())
    e2c_coverable = _tags_with_question(unit_tags, E2C_QUESTION_TYPE)
    new_tags = [t for t in e2c_coverable if t not in seen_e2c]
    random.shuffle(new_tags)
    for tag in new_tags[:SESSION_SIZE]:
        q = _pick_question(tag, E2C_QUESTION_TYPE, {unit}, used_ids)
        if q:
            question_set.append(q); used_ids.add(q["id"])

    for tag in _due_review_tags(db, user_id, facet, graduated_units):
        q = _pick_question(tag, E2C_QUESTION_TYPE, graduated_units, used_ids)
        if q:
            question_set.append(q); used_ids.add(q["id"])

    random.shuffle(question_set)
    return SessionResponse(user_id=user_id, session_type="vocab_session", question_set=question_set)


# ----------------------------- PHASE / COVERAGE -----------------------------

def _tags_with_question(unit_tags, question_type):
    return {t for t in unit_tags
            if any(q["question_type"] == question_type for q in inverted_index.get(t, []))}


def unit_coverage(db, user_id, unit) -> dict:
    """Per-facet coverage of the unit's coverable vocab. A word is coverable
    for a phase only if it has a question of that phase's type -- grammar
    particles with no c2e/e2c question are excluded so coverage can complete."""
    unit_tags = unit_to_vocab_tags_dict.get(unit, set())

    pinyin_coverable = _tags_with_question(unit_tags, LISTENING_QUESTION_TYPE)
    c2e_coverable    = _tags_with_question(unit_tags, C2E_QUESTION_TYPE)
    e2c_coverable    = _tags_with_question(unit_tags, E2C_QUESTION_TYPE)

    seen_pinyin = crud.get_seen_tags(db, user_id, "pinyin") & pinyin_coverable
    seen_c2e    = crud.get_c2e_seen_tags(db, user_id) & c2e_coverable
    seen_e2c    = crud.get_e2c_seen_tags(db, user_id) & e2c_coverable

    def done(seen, coverable):
        return len(coverable) > 0 and len(seen) >= len(coverable)

    listening_complete = done(seen_pinyin, pinyin_coverable)
    c2e_complete       = done(seen_c2e, c2e_coverable)
    e2c_complete       = done(seen_e2c, e2c_coverable)

    return {
        "total": len(unit_tags),
        "listening_total": len(pinyin_coverable), "listening_seen": len(seen_pinyin),
        "c2e_total": len(c2e_coverable),          "c2e_seen": len(seen_c2e),
        "e2c_total": len(e2c_coverable),          "e2c_seen": len(seen_e2c),
        "listening_complete": listening_complete,
        "c2e_complete": c2e_complete,
        "e2c_complete": e2c_complete,
        "all_complete": listening_complete and c2e_complete and e2c_complete,
    }


def unlocked_phases(db, user_id, unit, current_phase):
    phases = [PHASE_LISTENING]
    idx = PHASE_ORDER.index(current_phase) if current_phase in PHASE_ORDER else 0
    for p in (PHASE_C2E, PHASE_E2C, PHASE_SENTENCES):
        if idx >= PHASE_ORDER.index(p):
            phases.append(p)
    return phases


def maybe_advance_phase(db, user_id, mode):
    """Called after a session is submitted. listening -> c2e after one listening
    session; c2e -> e2c once c2e coverage is complete; e2c -> sentences once e2c
    coverage is complete."""
    user = crud.get_user(db, user_id)
    if not user:
        return
    phase = user.unit_phase
    cov = unit_coverage(db, user_id, user.current_unit)

    if phase == PHASE_LISTENING and mode == MODE_LISTENING:
        crud.set_unit_phase(db, user_id, PHASE_C2E); return
    if phase == PHASE_C2E and cov["c2e_complete"]:
        crud.set_unit_phase(db, user_id, PHASE_E2C); return
    if phase == PHASE_E2C and cov["e2c_complete"]:
        crud.set_unit_phase(db, user_id, PHASE_SENTENCES); return


# ----------------------------- ENDPOINTS -----------------------------

@router.get("/api/generate_session/{user_id}", response_model=SessionResponse)
def generate_session(user_id: int, mode: str = MODE_SENTENCE, db: Session = Depends(get_db)):
    user = crud.get_user(db, user_id)
    user_unit = user.current_unit
    graduated_units = crud.get_graduated_units(db, user_id)

    # server-side phase lock: reject a mode the user hasn't unlocked yet.
    if mode == MODE_LISTENING:
        required = PHASE_LISTENING
    elif mode == MODE_VOCAB:
        required = PHASE_C2E          # vocab unlocks as soon as c2e does
    else:
        required = PHASE_SENTENCES

    allowed = unlocked_phases(db, user_id, user_unit, user.unit_phase)
    if required not in allowed:
        raise HTTPException(status_code=409, detail=f"phase '{required}' locked; unlocked: {allowed}")

    if mode == MODE_LISTENING:
        return generate_listening_session(db, user_id, user_unit, graduated_units)
    if mode == MODE_VOCAB:
        return generate_vocab_session(db, user_id, user_unit, graduated_units)

    # sentence (default): unit test if ready, else mixed session
    unit_tags = unit_to_vocab_tags_dict.get(user_unit, set())
    all_records = get_collapsed_progress(db, user_id)
    unit_records = [r for r in all_records if r.tag in unit_tags]
    if is_unit_graduated(unit_records, unit_tags):
        return generate_unit_test(user_id, user_unit)
    return generate_mixed_session(db, user_id, user_unit, graduated_units)


@router.patch("/api/submit_session/{user_id}")
def submit_session(
    user_id: int,
    list_of_question_data: list[dict] = Body(...),
    is_correct: list[bool] = Body(...),
    is_unit_test: bool = Body(...),
    mode: str = Body(MODE_SENTENCE),
    db: Session = Depends(get_db)
):
    for i, question_data in enumerate(list_of_question_data):
        question_type = question_data.get("question_type", "")
        for tag in question_data.get("tags", []):
            if tag in META_TAGS or tag.startswith("unit_"):
                continue
            # updates whichever facet(s) this question type exercises
            # (see QUESTION_TYPE_FACETS in crud.py). c2e and e2c both update
            # the character facet; c2e additionally records an exposure below.
            crud.update_after_answer_for_question(db, user_id, tag, question_type, is_correct[i])

            # c2e is a one-time teaching exposure: record 'shown' so c2e
            # coverage can complete (the character facet alone can't tell c2e
            # from e2c, since both bump it).
            if question_type == C2E_QUESTION_TYPE:
                crud.record_character_exposure(db, user_id, tag)

        # speaking vocab is the only place a gated sound gets attempted in
        # isolation -- record it so the sentence gate can unlock.
        if question_type == "speaking vocab":
            for sound in _tag_sounds(question_data.get("question", "")):
                crud.record_sound_attempt(db, user_id, sound, is_correct[i])

    # advance the unit phase if this session completed a gate
    if not is_unit_test:
        maybe_advance_phase(db, user_id, mode)

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
    session = generate_mixed_session(db, user_id, user.current_unit, graduated_units)
    return {
        "current_unit": user.current_unit,
        "graduated_units": user.graduated_units,
        "unit_tags_count": len(unit_tags),
        "unit_ready_to_graduate": graduated,
        "questions_found": len(session.question_set),
        "sample_question_types": list(set(q["question_type"] for q in session.question_set)),
        "sample_units": sorted(set(q.get("unit") for q in session.question_set)),
        "sample_correct_counts": [{"tag": r.tag, "correct_count": r.correct_count} for r in unit_records]
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
    current_unit_words = sorted([
        {
            "tag": tag,
            "correct_count": record_map[tag].correct_count if tag in record_map else 0,
        }
        for tag in current_unit_tags
    ], key=lambda x: x["tag"])

    phase = user.unit_phase
    coverage = unit_coverage(db, user_id, user_unit)

    return {
        "user_id": user_id,
        "current_unit": user_unit,
        "graduated_units": list(graduated_units),
        "unit_progress": unit_progress,
        "current_unit_words": current_unit_words,
        "unit_phase": phase,
        "unlocked_phases": unlocked_phases(db, user_id, user_unit, phase),
        "coverage": coverage,
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