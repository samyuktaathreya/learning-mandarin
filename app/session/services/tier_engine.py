import math
import random

from sqlalchemy.orm import Session

from session import crud
from session.constants import (
    TIER_QUESTION_TYPES,
    TIER4_DOWNSHIFT_PROBABILITY,
    SESSION_SIZE,
    MAX_SAME_TAG_PER_SESSION,
    WEIGHT_FLOOR,
    TIER_BONUS_FACTOR,
    MAX_TIER,
    MISS_WEIGHT_FACTOR,
    MISS_DOWNSHIFT_THRESHOLD,
    GRADUATION_THRESHOLD,
    FINAL_PUSH_UNGRADUATED_THRESHOLD,
)
from textbook.services import inverted_index, unit_to_vocab_tags_dict


def _tier_types_for_facet(tier: int, facet: str) -> list:
    return [qt for qt in TIER_QUESTION_TYPES[tier]
            if facet in crud.QUESTION_TYPE_FACETS.get(qt, [])]


def _active_tier_for_serve(tier: int, final_push: bool) -> int:
    if tier >= MAX_TIER and not final_push:
        return (MAX_TIER - 1) if random.random() < TIER4_DOWNSHIFT_PROBABILITY else MAX_TIER
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
            tier_bonus = (MAX_TIER - tiers.get(t, 1)) * TIER_BONUS_FACTOR
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

        print(f"[generate_tier_questions] WARNING: tag '{tag}' has NO available "
              f"question at tier {serve_tier} (unit {unit}) -- content gap, "
              f"this tag cannot advance until data is added")
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