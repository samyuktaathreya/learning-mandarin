import random

from sqlalchemy.orm import Session

from session import crud
from session.constants import (
    SESSION_SIZE,
    NUM_OF_UNIT_TEST_QUESTIONS,
    UNIT_TEST_TIER_WEIGHTS,
    TIER_QUESTION_TYPES,
    ALL_TIER_QUESTION_TYPES,
    PERCENTAGE_TO_PASS_UNIT_TEST,
    SPEAKING_TYPES,
)
from session.schemas import SessionResponse
from session.services.progress import get_collapsed_progress, is_unit_graduated
from session.services.tier_engine import generate_tier_questions
from session.services.review_engine import (
    generate_review_questions,
    review_due_word_count,
    is_facet_review_eligible,
)
from session.services.tips import attach_tips
from session.services.sound import _tag_sounds
from session_log import log_session
from textbook import services as textbook_services
from textbook.services import META_TAGS
from characters.services import generate_character_questions

# ----------------------------- SESSION GENERATION -----------------------------


def generate_practice_session(db: Session, textbook_db: Session, user_id, unit) -> SessionResponse:
    used_ids = set()
    review_picks = generate_review_questions(db, textbook_db, user_id, used_ids, limit=SESSION_SIZE)

    remaining = SESSION_SIZE - len(review_picks)
    tier_picks, stop_reason, min_counts = [], "pure_review", {}
    tiers = {}
    if remaining > 0:
        unit_tags = textbook_services.get_unit_vocab_tags(textbook_db, unit)
        tiers = crud.get_tiers_for_tags(db, user_id, unit_tags)
        tier_picks, stop_reason, min_counts = generate_tier_questions(db, textbook_db, user_id, unit, tiers)
        tier_picks = [p for p in tier_picks if p[0]["id"] not in used_ids][:remaining]

    log_session(user_id, unit, tier_picks, review_picks, tiers, min_counts, stop_reason)
    question_set = [q for q, _ in review_picks] + [q for q, _ in tier_picks]
    random.shuffle(question_set)
    return SessionResponse(user_id=user_id, session_type="practice_session", question_set=question_set)


def generate_review_session(db: Session, textbook_db: Session, user_id) -> SessionResponse:
    used_ids = set()
    review_picks = generate_review_questions(db, textbook_db, user_id, used_ids, limit=SESSION_SIZE)

    log_session(user_id, None, [], review_picks, {}, {}, "review_session")

    question_set = [q for q, _ in review_picks]
    random.shuffle(question_set)
    return SessionResponse(user_id=user_id, session_type="review_session", question_set=question_set)


def generate_unit_test(textbook_db: Session, user_id: int, unit: int) -> SessionResponse:
    # was: unit_questions.get(str(unit), []) against a module-level dict
    # loaded from unit_questions_hsk1.json at import time. Now:
    # textbook_services.get_all_questions_for_unit, a DB query (see
    # textbook/crud.py's get_all_questions_for_unit) -- every question row
    # in the unit, filtered here exactly as before.
    eligible = [q for q in textbook_services.get_all_questions_for_unit(textbook_db, unit)
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


def generate_full_session(db: Session, characters_db: Session, textbook_db: Session, user_id: int,
                           mode: str = "sentence", skip_review: bool = False) -> SessionResponse:
    """Full composition used by GET /api/generate_session/{user_id}:
    graduation check -> review check -> standard practice session (+ character
    questions).

    Gained a `textbook_db` parameter -- callers (router.py) need to inject
    it via Depends(get_textbook_db) alongside `db` and `characters_db`,
    same pattern already used for characters_db.
    """
    user = crud.get_user(db, user_id)
    user_unit = user.current_unit

    unit_tags = textbook_services.get_unit_vocab_tags(textbook_db, user_unit)
    all_records = get_collapsed_progress(db, user_id)
    unit_records = [r for r in all_records if r.tag in unit_tags]

    # 1. Check for graduation (returns early if true)
    if is_unit_graduated(db, user_id, unit_records, unit_tags):
        return attach_tips(db, generate_unit_test(textbook_db, user_id, user_unit))

    # 2. Check for reviews (returns early if true)
    if not skip_review and review_due_word_count(db, textbook_db, user_id) > 0:
        review_session = generate_review_session(db, textbook_db, user_id)
        if review_session.question_set:
            return attach_tips(db, review_session)

    # 3. Standard practice session: generate, attach tips, add character questions, shuffle
    session = attach_tips(db, generate_practice_session(db, textbook_db, user_id, user_unit))

    character_qs = generate_character_questions(db, characters_db, user_id, num_questions=2)
    session.question_set.extend(character_qs)

    random.shuffle(session.question_set)

    return session


# ----------------------------- SUBMISSION -----------------------------
# process_submission is UNCHANGED below -- it never touched
# unit_to_vocab_tags_dict/unit_questions/inverted_index, only META_TAGS
# (still a plain constant, still importable directly from textbook.services)
# and session-DB-only crud calls.


def process_submission(
    db: Session,
    user_id: int,
    list_of_question_data: list,
    is_correct: list,
    is_unit_test: bool,
    mode: str = "sentence",
) -> dict:
    """Business logic for PATCH /api/submit_session/{user_id}."""
    graded_correct = [
        True if q.get("question_type") in SPEAKING_TYPES else is_correct[i]
        for i, q in enumerate(list_of_question_data)
    ]
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
    facet_probation_clears = set()   # (tag, facet) pairs to hard-reset this submit

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

            # Probation clear check: this tag's real tier is starting_tiers[tag].
            # If this question was served at exactly one tier below that, and
            # answered correctly, the learner just proved they know it -- clear
            # struggle for whichever facet(s) this question exercised.
            real_tier = starting_tiers.get(tag, 1)
            if correct and real_tier > 1 and question_type in TIER_QUESTION_TYPES.get(real_tier - 1, set()):
                for facet in crud.facets_for_question_type(question_type):
                    facet_probation_clears.add((tag, facet))

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
        if (tag, facet) in facet_probation_clears:
            crud.reset_miss_count(db, user_id, tag, facet)
            continue
        misses = facet_misses.get((tag, facet), 0)
        delta = misses if misses > 0 else -1
        crud.update_miss_count(db, user_id, tag, facet, delta,
                                attempts=facet_attempts[(tag, facet)])

    unit_test_result = None
    if is_unit_test:
        num_correct = sum(graded_correct)
        needed = PERCENTAGE_TO_PASS_UNIT_TEST * NUM_OF_UNIT_TEST_QUESTIONS
        if num_correct >= needed:
            user_unit = crud.get_user(db, user_id).current_unit
            crud.graduate_unit(db, user_id, user_unit)
            unit_test_result = "unit test passed"
        else:
            unit_test_result = "unit test failed"

    return {"user_id": user_id, "unit_test_result": unit_test_result}