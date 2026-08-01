"""
Session engine tuning constants.

Two groups of constants live here:

1. REAL VALUES, moved from the old top-level crud.py (facets, strength/SRS,
   sound-unlock, tier cap). These are confirmed -- no action needed.

2. STILL-TODO placeholders for names your old session.py referenced via
   `import session.constants` (GRADUATION_THRESHOLD, TIER_QUESTION_TYPES,
   SESSION_SIZE, etc.) whose real values weren't in anything you've pasted
   yet. Replace these before running -- several (TIER_QUESTION_TYPES,
   REVIEW_TYPES_BY_FACET) directly control question selection.

CONSOLIDATION NOTE: crud.py's tier cap (`MAX_TIER = 4`, used by advance_tier)
and session.py's review-eligibility threshold (`MAX_TIER_FOR_REVIEW = 4`,
used by is_facet_review_eligible / is_unit_graduated / the tier-bonus weight)
were the same value, 4, and read as the same underlying concept -- "a word is
fully progressed / review-eligible once tier hits the cap." I merged them
into one name, MAX_TIER, and updated every call site (progress.py,
tier_engine.py, review_engine.py). If tier cap and review-eligibility are
actually meant to diverge later (e.g. cap raised to 5 but review still kicks
in at 4), split this back into two constants.
"""

# --- Facets ------------------------------------------------------------------

# The two facets a word's strength is tracked on.
FACETS = ("character", "pinyin")

# Which facet(s) each question type exercises, and therefore updates on answer.
# "character" = meaning / recognition; "pinyin" = sound.
# Single source of truth -- submit_session routes updates through here, and
# init_db seeds one row per (tag, facet).
QUESTION_TYPE_FACETS = {
    "speaking vocab":                        ["pinyin"],
    "speaking sentence":                     ["pinyin"],
    "transcribe word to pinyin":             ["pinyin"],
    "listening vocab":                       ["pinyin"],
    "listening sentence":                    ["pinyin", "character"],
    "translate chinese word to english":     ["character"],
    "translate english word to chinese":     ["character"],
    "translate chinese sentence to english": ["character"],
    "translate english sentence to chinese": ["character"],
    "fill in the blank":                     ["character"],
    "transcribe hanzi to pinyin":            ["pinyin"],
}

# --- Strength / SRS ------------------------------------------------------------

# The floor stability sits at during the learning phase. A word only starts
# growing (or losing) stability once it is review-eligible -- see
# _apply_answer_to_row in crud.py and Option C in the review design. Keeping
# this named makes the "stability does nothing during learning" rule explicit.
STABILITY_FLOOR = 1.0

MAX_MISS_COUNT = 5

# --- Tiering -------------------------------------------------------------------

MAX_TIER = 4  # tier cap (advance_tier) AND review-eligibility threshold -- see note above

# TODO: replace with real tier -> set(question_type) mapping
TIER_QUESTION_TYPES = {
    1: set(),
    2: set(),
    3: set(),
    4: set(),
}

# TODO: replace with the real flattened set of every question type across all tiers
ALL_TIER_QUESTION_TYPES = set().union(*TIER_QUESTION_TYPES.values()) if TIER_QUESTION_TYPES else set()

TIER4_DOWNSHIFT_PROBABILITY = 0.0  # TODO: confirm (probability tier 4 gets served as tier 3)
TIER_BONUS_FACTOR = 0.0  # TODO: confirm

# --- Sound ---------------------------------------------------------------------

SOUND_UNLOCK_SUCCESSES = 1
SOUND_UNLOCK_ATTEMPTS_CAP = 5

# --- Session composition ---------------------------------------------------------

SESSION_SIZE = 20  # TODO: confirm
MAX_SAME_TAG_PER_SESSION = 3  # TODO: confirm
WEIGHT_FLOOR = 0.0  # TODO: confirm
MISS_WEIGHT_FACTOR = 0.0  # TODO: confirm
MISS_DOWNSHIFT_THRESHOLD = 0  # TODO: confirm
FINAL_PUSH_UNGRADUATED_THRESHOLD = 0  # TODO: confirm

# --- Graduation / review -----------------------------------------------------------

GRADUATION_THRESHOLD = 0  # TODO: confirm
REVIEW_THRESHOLD = 0.0  # TODO: confirm

# TODO: replace with real facet -> fallback question type pool mapping
REVIEW_TYPES_BY_FACET = {
    "character": set(),
    "pinyin": set(),
}

# --- Unit test -----------------------------------------------------------------------

NUM_OF_UNIT_TEST_QUESTIONS = 20  # TODO: confirm
PERCENTAGE_TO_PASS_UNIT_TEST = 0.8  # TODO: confirm
UNIT_TEST_TIER_WEIGHTS = {1: 1, 2: 1, 3: 4, 4: 4}

# --- Misc --------------------------------------------------------------------------

SPEAKING_TYPES = set()  # TODO: confirm -- question types that are auto-graded correct