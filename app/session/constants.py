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

SPEAKING_TYPES = {"speaking vocab", "speaking sentence"}

QUESTION_TYPE_TO_TIER = {
    qt: tier for tier, qtypes in TIER_QUESTION_TYPES.items() for qt in qtypes
}

REVIEW_ONLY_TYPES = {"transcribe hanzi to pinyin"}
# A tier-4 word is served a tier-3 question this fraction of the time (a
# "downshift" back to sentence-adjacent practice) instead of tier 4.
# Answering on the downshifted (lower-tier) type does NOT advance the word --
# only an answer on the word's actual current tier counts as exposure.
TIER4_DOWNSHIFT_PROBABILITY = 0.20

# Once fewer than this many current-unit words remain ungraduated, the unit
# is in its "final push" -- tier-4 words stop downshifting so every serve
# pushes directly toward graduation.
FINAL_PUSH_UNGRADUATED_THRESHOLD = 5

# A struggling word (miss_count >= 1 for either facet) is served one tier
# EASIER as a single-miss safety net -- given MAX_SAME_TAG_PER_SESSION=2, a
# tag can't rack up multiple misses in one session anyway, so any miss at all
# is enough to warrant an easier serve next time. It exits probation the
# moment it answers correctly at that easier tier (see reset_miss_count in
# submit_session) -- not via gradual -1 decay, which could take several
# sessions to actually clear even after the learner clearly knows it.
MISS_DOWNSHIFT_THRESHOLD = 1
MISS_WEIGHT_FACTOR = 0.5            # added weight per point of miss_count

# The tier a word must reach before it can enter per-word review. == crud.MAX_TIER.
MAX_TIER_FOR_REVIEW = 4

# Tier 3/4 question types, split by the facet they exercise (derived from
# crud.QUESTION_TYPE_FACETS). Review is sentence-level only, so tier 1/2 types
# never appear here. "listening sentence" trains both facets, so it's in both
# lists. Used by the review picker for weak-facet-first selection.
REVIEW_TYPES_BY_FACET = {
    "pinyin":    ["listening sentence", "speaking sentence"],
    "character": ["translate english sentence to chinese",
                  "fill in the blank"],
}

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
    "transcribe hanzi to pinyin":       ["pinyin"],
}

STABILITY_FLOOR = 1.0

MAX_MISS_COUNT = 5
MAX_TIER = 4

SOUND_UNLOCK_SUCCESSES = 1
SOUND_UNLOCK_ATTEMPTS_CAP = 5

UNIT_TEST_TIER_WEIGHTS = {1: 1, 2: 1, 3: 4, 4: 4}