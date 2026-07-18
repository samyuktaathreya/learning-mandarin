from fastapi import APIRouter, Depends, Body
from sqlalchemy.orm import Session
from database import SessionLocal, inverted_index, tags_to_unit_dict, unit_to_vocab_tags_dict, unit_questions, META_TAGS, hsk1_dictionary, word_to_pinyin
from schemas.user import SessionResponse
from pinyin_utils import split_pinyin_sounds, GATED_INITIALS, GATED_FINALS
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

def _tier_types_for_facet(tier: int, facet: str) -> list:
    return [qt for qt in TIER_QUESTION_TYPES[tier]
            if facet in crud.QUESTION_TYPE_FACETS.get(qt, [])]

def _active_tier_for_serve(tier: int, final_push: bool) -> int:
    """The tier a word is actually served on for one pick. Tiers 1-3 serve as
    themselves. Tier 4 downshifts to tier 3 TIER4_DOWNSHIFT_PROBABILITY of the
    time, unless the unit is in its final push (see
    FINAL_PUSH_UNGRADUATED_THRESHOLD), in which case tier 4 always serves
    tier 4."""
    if tier >= 4 and not final_push:
        return 3 if random.random() < TIER4_DOWNSHIFT_PROBABILITY else 4
    return tier

def _type_cap_for_tier(tier: int) -> int:
    """Per-type cap for a draw served on `tier`, proportional to how many
    types that tier has: ceil(SESSION_SIZE / n_types). Tiers with 2 types cap
    at 5, tiers with 3 cap at 4 -- so the cap can never itself make a full
    session impossible (2*5 and 3*4 both >= SESSION_SIZE). It's a variety
    preference, not a wall: the relaxed second pass ignores it entirely."""
    n_types = len(TIER_QUESTION_TYPES[tier])
    return math.ceil(SESSION_SIZE / n_types)

def generate_tier_questions(db: Session, user_id: int, unit: int, tiers: dict):
    """Returns (picks, stop_reason, min_counts).
    picks = [(question, served_for_tag), ...] -- the tag is recorded because
    it's the generator's INTENT and is unrecoverable from the question later.

    Type choice within a tier PREFERS the word's weaker facet. Graduation is
    min(character, pinyin) >= GRADUATION_THRESHOLD, so a word whose weak facet
    never gets served stays pinned at its min forever regardless of how much
    the strong facet climbs -- the selector has to close that gap explicitly.
    """
    unit_tags = unit_to_vocab_tags_dict.get(unit, set())
    if not unit_tags:
        return [], "no_unit_tags", {}

    # per-facet counts, not the collapsed min -- we need to know WHICH facet
    # is weak, which the collapse throws away. One query; min falls out free.
    facet_counts = {t: {"character": 0, "pinyin": 0} for t in unit_tags}
    for r in crud.get_progress_by_user(db, user_id):
        if r.tag in facet_counts and r.facet in facet_counts[r.tag]:
            facet_counts[r.tag][r.facet] = r.correct_count

    min_counts = {t: min(facet_counts[t]["character"], facet_counts[t]["pinyin"])
                  for t in unit_tags}

    ungraduated = sum(1 for t in unit_tags if min_counts[t] < GRADUATION_THRESHOLD)
    final_push = ungraduated < FINAL_PUSH_UNGRADUATED_THRESHOLD

    used_ids = set()
    tag_counts = {}
    type_counts = {}
    picks = []

    def _ordered_types(tag: str, tier: int) -> list:
        """The tier's types, weak-facet ones first. Ties (equal counts) ->
        the whole tier shuffled; whichever facet gets served becomes the
        stronger one, so the next draw self-corrects to the other."""
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
        return preferred + rest        # fall back to the other facet if the
                                       # weak one has nothing available

    def _draw(respect_type_cap: bool) -> bool:
        pool = [t for t in unit_tags if tag_counts.get(t, 0) < MAX_SAME_TAG_PER_SESSION]
        if not pool:
            return False
        weights = [1.0 / (min_counts[t] + 1) for t in pool]
        tag = random.choices(pool, weights=weights, k=1)[0]

        serve_tier = _active_tier_for_serve(tiers.get(tag, 1), final_push)
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
            chosen = random.choice(avail)
            picks.append((chosen, tag))
            used_ids.add(chosen["id"])
            tag_counts[tag] = tag_counts.get(tag, 0) + 1
            type_counts[qt] = type_counts.get(qt, 0) + 1
            # keep facet counts live within the session so a word's second
            # draw prefers the facet the first draw didn't serve
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
# Review is decoupled from units entirely. A word becomes review-eligible on
# its OWN merits -- once it has climbed to tier 4 AND its weaker facet has hit
# the graduation bar -- not when its unit graduates. Unit graduation now only
# gates new-word introduction and unlocks the unit test.

def is_facet_review_eligible(tier: int, facet_count: int) -> bool:
    """A single FACET of a word graduates into review once (a) the word has
    climbed the tier ladder to tier 4, AND (b) that facet has been answered
    correctly enough -- facet_count >= GRADUATION_THRESHOLD. Per-facet: a
    word's character facet can enter review while its pinyin facet is still
    learning. Tier is a per-WORD property (the ladder is shared); the count bar
    is per-facet.

    Note this is safe from orphaning because unit graduation still requires
    BOTH facets past the bar (is_unit_graduated uses the min-collapsed count),
    so a lagging facet always finishes learning while its unit's questions are
    still available -- it can't get stranded past its unit."""
    return tier >= MAX_TIER_FOR_REVIEW and facet_count >= GRADUATION_THRESHOLD


def _all_review_eligible_facets(db: Session, user_id: int) -> list:
    """Every (word, facet) pair that is now review-eligible, with the raw
    StrengthTable row for that facet. Returns [(tag, facet, row), ...]. No
    collapse -- review is per-facet now, so each facet's own count, stability,
    and last_practice drive its own review timing."""
    progress = crud.get_progress_by_user(db, user_id)
    if not progress:
        return []

    tags = {r.tag for r in progress}
    tiers = crud.get_tiers_for_tags(db, user_id, tags)

    eligible = []
    for r in progress:
        if r.facet not in ("character", "pinyin"):
            continue
        if is_facet_review_eligible(tiers.get(r.tag, 1), r.correct_count):
            eligible.append((r.tag, r.facet, r))
    return eligible


def _due_review_facets(db: Session, user_id: int) -> list:
    """Per-(word, facet) review, NOT unit-gated. Any review-eligible facet
    whose decayed strength has dropped below REVIEW_THRESHOLD is due, weakest
    first. Returns [(tag, facet), ...]. A word can be due on character but not
    pinyin, or vice versa -- each facet reviews at its own pace."""
    now = datetime.utcnow()
    scored = []
    for tag, facet, r in _all_review_eligible_facets(db, user_id):
        strength = 0.5 ** ((now - r.last_practice).total_seconds() / 86400 / r.stability)
        if strength < REVIEW_THRESHOLD:
            scored.append((tag, facet, strength))
    scored.sort(key=lambda x: x[2])
    return [(tag, facet) for tag, facet, _ in scored]


def _pick_review_question(tag: str, facet: str, used_ids: set):
    """Pick a tier-3/4 question that exercises THIS facet for this word. No
    weak-facet logic and no downshift -- the due facet is already known, so we
    just serve a sentence-level question that trains it. If none exists for
    this word/facet, return None (the facet stays due).

    Unit-agnostic: a due facet can be reviewed with a question from ANY unit
    that teaches this word."""
    types = list(REVIEW_TYPES_BY_FACET[facet])
    random.shuffle(types)
    for qt in types:
        pool = [
            q for q in inverted_index.get(tag, [])
            if q["question_type"] == qt
            and q["id"] not in used_ids
        ]
        if pool:
            return random.choice(pool)
    return None


def generate_review_questions(db, user_id, used_ids):
    """[(question, tag), ...] for every due (word, facet) we can find a
    question for. No cap -- all due facets are served (Anki-style).

    A single question can cover a facet for a word; if the same word is due on
    both facets, it may yield two questions (one per facet), which is correct:
    the two facets are independent review items."""
    picks = []
    for tag, facet in _due_review_facets(db, user_id):
        q = _pick_review_question(tag, facet, used_ids)
        if q:
            picks.append((q, tag))
            used_ids.add(q["id"])
    return picks


def generate_practice_session(db, user_id, unit) -> SessionResponse:
    """Due-first, uncapped review; learning fills the remainder up to
    SESSION_SIZE.

      - All due review words are served (session can exceed SESSION_SIZE).
      - remaining = SESSION_SIZE - len(due); if > 0, fill with that many
        learning words from the current unit (tier ladder, weak-facet-first).
      - >= SESSION_SIZE due words -> pure review session, no learning.

    Review is per-word and unit-agnostic; units only gate which learning words
    are available to fill the remainder."""
    # 1. review first -- all due words, no cap
    used_ids = set()
    review_picks = generate_review_questions(db, user_id, used_ids)

    # 2. remaining slots -> learning words from the current unit
    remaining = SESSION_SIZE - len(review_picks)
    tier_picks, stop_reason, min_counts = [], "pure_review", {}
    if remaining > 0:
        tiers = crud.get_tiers_for_tags(db, user_id, unit_to_vocab_tags_dict.get(unit, set()))
        tier_picks, stop_reason, min_counts = generate_tier_questions(db, user_id, unit, tiers)
        # generate_tier_questions fills up to SESSION_SIZE; we only want
        # `remaining`, so trim (and drop any that collide with review ids).
        tier_picks = [p for p in tier_picks if p[0]["id"] not in used_ids][:remaining]

    log_session(user_id, unit, tier_picks, review_picks, {}, min_counts, stop_reason)

    question_set = [q for q, _ in review_picks] + [q for q, _ in tier_picks]
    random.shuffle(question_set)
    return SessionResponse(user_id=user_id, session_type="practice_session", question_set=question_set)

# unit test composition: by the time a unit tests, every word is at tier 4
# (it took 4+ answers to get there), so tier-1/2 recognition questions are
# free points that inflate the pass rate. Weight the test toward the tiers
# the learner has actually proven.
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

# ----------------------------- ENDPOINTS -----------------------------

@router.get("/api/generate_session/{user_id}", response_model=SessionResponse)
def generate_session(user_id: int, mode: str = "sentence", db: Session = Depends(get_db)):
    user = crud.get_user(db, user_id)
    user_unit = user.current_unit

    unit_tags = unit_to_vocab_tags_dict.get(user_unit, set())
    all_records = get_collapsed_progress(db, user_id)
    unit_records = [r for r in all_records if r.tag in unit_tags]

    # unit graduation now ONLY decides "is this unit's test unlocked" -- it no
    # longer has anything to do with review, which is per-word.
    if is_unit_graduated(unit_records, unit_tags):
        return generate_unit_test(user_id, user_unit)
    return generate_practice_session(db, user_id, user_unit)


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

    # per-FACET review-eligibility as of the START of this submission. Option C:
    # stability only grows once a facet is review-eligible, so we compute each
    # (word, facet)'s phase from the pre-submit snapshot (same fixed-snapshot
    # logic as tier advancement) and pass it down. Eligibility is per-facet now:
    # {tag: {"character": bool, "pinyin": bool}}. The answer that CROSSES a
    # facet into eligibility still uses learning-phase (floor) rules for that
    # one answer; subsequent submits then grow that facet's stability.
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

    for i, question_data in enumerate(list_of_question_data):
        question_type = question_data.get("question_type", "")
        for tag in question_data.get("tags", []):
            if tag in META_TAGS or tag.startswith("unit_"):
                continue
            crud.update_after_answer_for_question(
                db, user_id, tag, question_type, is_correct[i],
                facet_eligible=starting_facet_eligible.get(tag, {}),
            )

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
    session = generate_practice_session(db, user_id, user.current_unit)
    tiers = crud.get_tiers_for_tags(db, user_id, unit_tags)

    # per-facet review visibility: how many (word, facet) items are
    # review-eligible and how many are currently due. Counts are per-facet now,
    # so a word contributes up to 2 (character + pinyin).
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