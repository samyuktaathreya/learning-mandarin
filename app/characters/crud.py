"""
Read-only CRUD for characters.db.

All functions take a SQLAlchemy Session bound to characters_engine
(use get_characters_db() as the FastAPI dependency).

Three query families:
  1. get_character             — fetch one character's metadata
  2. get_similar_by_components — IDS-derived structural similarity
  3. get_confusibles           — human-curated confusion pairs
"""

from sqlalchemy.orm import Session
from sqlalchemy import func
from models.characters import Character, CharacterComponent, ConfusionPair


# ---------------------------------------------------------------------------
# 1. Single character lookup
# ---------------------------------------------------------------------------

def get_character(db: Session, char: str) -> Character | None:
    """Fetch a character's metadata row, or None if not in the database."""
    return db.query(Character).filter(Character.char == char).first()


# ---------------------------------------------------------------------------
# 2. IDS-derived structural similarity
# ---------------------------------------------------------------------------

def get_similar_by_components(
    db: Session,
    char: str,
    depth: int = 0,
    max_frequency: int = 50,
    limit: int = 10,
) -> list[dict]:
    """
    Find characters that share at least one component with `char` at the
    given depth (0 = direct children only, the most visually similar).

    Shared components with frequency_in_corpus > max_frequency are excluded
    to avoid common radicals like 亻 or 艹 dominating results with noise.

    Returns a list of dicts, sorted by number of shared components descending
    (more overlap = more similar), then by ascending frequency (rarer shared
    component = more distinctive match).

    Example usage:
        get_similar_by_components(db, "清", depth=0, max_frequency=50)
        # -> [{"char": "晴", "shared_components": ["青"], "shared_count": 1}, ...]
    """
    # Get this character's components at the requested depth, excluding noise
    own_components = (
        db.query(CharacterComponent.component_char)
        .filter(
            CharacterComponent.char == char,
            CharacterComponent.depth == depth,
            CharacterComponent.frequency_in_corpus <= max_frequency,
        )
        .all()
    )
    own_component_set = {row.component_char for row in own_components}

    if not own_component_set:
        return []

    # Find all other characters that share any of those components
    matches = (
        db.query(
            CharacterComponent.char,
            CharacterComponent.component_char,
        )
        .filter(
            CharacterComponent.component_char.in_(own_component_set),
            CharacterComponent.depth == depth,
            CharacterComponent.char != char,
        )
        .all()
    )

    # Group by character: collect shared components, count them
    grouped: dict[str, list[str]] = {}
    for row in matches:
        grouped.setdefault(row.char, []).append(row.component_char)

    results = [
        {
            "char": target_char,
            "shared_components": components,
            "shared_count": len(components),
        }
        for target_char, components in grouped.items()
    ]

    # Sort: most shared components first, ties broken by component rarity
    results.sort(key=lambda x: -x["shared_count"])

    return results[:limit]


def get_similar_by_position(
    db: Session,
    char: str,
    position: str,
    depth: int = 0,
    limit: int = 10,
) -> list[dict]:
    """
    Find characters that share a component with `char` at a specific position
    (e.g. "left", "right", "top", "bottom").

    Useful for generating targeted quiz distractors:
    "same left radical, different right" or "same top, different bottom".

    Example usage:
        get_similar_by_position(db, "清", position="right", depth=0)
        # -> chars that also have 青 on the right: 晴, 情, 请, ...
    """
    # Components of the target char at the given position
    own = (
        db.query(CharacterComponent.component_char)
        .filter(
            CharacterComponent.char == char,
            CharacterComponent.depth == depth,
            CharacterComponent.position == position,
        )
        .all()
    )
    own_components = {row.component_char for row in own}

    if not own_components:
        return []

    matches = (
        db.query(CharacterComponent.char, CharacterComponent.component_char)
        .filter(
            CharacterComponent.component_char.in_(own_components),
            CharacterComponent.depth == depth,
            CharacterComponent.position == position,
            CharacterComponent.char != char,
        )
        .all()
    )

    grouped: dict[str, list[str]] = {}
    for row in matches:
        grouped.setdefault(row.char, []).append(row.component_char)

    results = [
        {"char": c, "shared_components": comps, "position": position}
        for c, comps in grouped.items()
    ]

    return results[:limit]


# ---------------------------------------------------------------------------
# 3. Human-curated confusion pairs
# ---------------------------------------------------------------------------

def get_confusibles(db: Session, char: str) -> list[str]:
    """
    Return all characters that are human-curated confusibles of `char`.
    Handles the bidirectional storage (char_a < char_b) transparently.

    Example usage:
        get_confusibles(db, "人")
        # -> ["入", "八", "儿"]
    """
    rows = (
        db.query(ConfusionPair)
        .filter(
            (ConfusionPair.char_a == char) | (ConfusionPair.char_b == char)
        )
        .all()
    )

    return [
        row.char_b if row.char_a == char else row.char_a
        for row in rows
    ]


def get_all_confusible_pairs_for_chars(
    db: Session,
    chars: list[str],
) -> dict[str, list[str]]:
    """
    Batch version of get_confusibles for a list of characters.
    Returns {char: [confusible, ...]} for every char in the input list
    that has at least one known confusible.

    Useful for pre-loading the full confusion map for a user's known vocab
    at session start rather than querying one character at a time.

    Example usage:
        get_all_confusible_pairs_for_chars(db, ["人", "大", "清"])
        # -> {"人": ["入", "八", "儿"], "清": [...]}
    """
    rows = (
        db.query(ConfusionPair)
        .filter(
            (ConfusionPair.char_a.in_(chars)) | (ConfusionPair.char_b.in_(chars))
        )
        .all()
    )

    result: dict[str, list[str]] = {}
    for row in rows:
        # Add in both directions, filtered to only include chars the caller asked about
        if row.char_a in chars:
            result.setdefault(row.char_a, []).append(row.char_b)
        if row.char_b in chars:
            result.setdefault(row.char_b, []).append(row.char_a)

    return result