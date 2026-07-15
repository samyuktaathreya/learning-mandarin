"""Startup validation of question-type coverage per word.

Graduation is min(character, pinyin) >= GRADUATION_THRESHOLD, so a word with
zero questions of one facet ANYWHERE in its unit can never graduate -- that's
an impossible case and we raise on it. Everything else (a word missing tier-1
or tier-4 coverage for a facet) is reported but tolerated: those words just
have a lower reachable ceiling, which the max_tier exemption will formalize
later.
"""
from database import unit_to_vocab_tags_dict, inverted_index
import crud

# kept local rather than imported from session.py to avoid a circular import
# (session imports crud, crud imports models, models imports database...).
# If the tier map changes, change it in both places -- or hoist it into
# database.py and import from there.
TIER_QUESTION_TYPES = {
    1: {"listening vocab", "translate chinese word to english"},
    2: {"speaking vocab", "transcribe word to pinyin", "translate english word to chinese"},
    3: {"translate chinese sentence to english", "fill in the blank"},
    4: {"listening sentence", "speaking sentence", "translate english sentence to chinese"},
}
FACETS = ("character", "pinyin")


class ImpossibleGraduationError(Exception):
    """A word has no question of one facet at any tier in its unit, so
    min(character, pinyin) can never reach the graduation threshold."""


def _word_coverage(tag: str, unit: int) -> dict:
    """{tier: {facet: n_questions}} for one word within one unit."""
    questions = [q for q in inverted_index.get(tag, []) if q.get("unit") == unit]
    cov = {tier: {f: 0 for f in FACETS} for tier in TIER_QUESTION_TYPES}
    for q in questions:
        qt = q["question_type"]
        for tier, types in TIER_QUESTION_TYPES.items():
            if qt in types:
                for f in crud.QUESTION_TYPE_FACETS.get(qt, []):
                    cov[tier][f] += 1
    return cov


def _max_reachable_tier(cov: dict) -> int:
    """Highest tier with any question at all. A word with no sentences tops
    out at 2; this is the number the max_tier exemption will need."""
    reachable = [t for t, facets in cov.items() if sum(facets.values()) > 0]
    return max(reachable) if reachable else 0


def check_coverage(raise_on_impossible: bool = True):
    """Print a per-unit coverage report; raise on the impossible case
    (a facet with zero questions at any tier). Called once at startup."""
    impossible = []
    gaps = []          # missing tier-1 or tier-4 facet coverage (tolerated)
    ceilings = {}      # tag -> max reachable tier, where < 4

    for unit in sorted(unit_to_vocab_tags_dict):
        for tag in sorted(unit_to_vocab_tags_dict[unit]):
            cov = _word_coverage(tag, unit)

            # impossible: a facet with no questions at ANY tier
            for f in FACETS:
                if sum(cov[t][f] for t in TIER_QUESTION_TYPES) == 0:
                    impossible.append((unit, tag, f))

            # tolerated gaps: tier 1 (introduces both facets) and tier 4
            # (where graduation normally lands) missing a facet
            for tier in (1, 4):
                for f in FACETS:
                    if cov[tier][f] == 0:
                        gaps.append((unit, tag, tier, f))

            top = _max_reachable_tier(cov)
            if top < 4:
                ceilings[(unit, tag)] = top

    # ---- report ----
    print("\n=== question coverage report ===")

    if ceilings:
        print(f"\nwords that cannot reach tier 4 ({len(ceilings)}):")
        by_unit = {}
        for (unit, tag), top in ceilings.items():
            by_unit.setdefault(unit, []).append(f"{tag}(max tier {top})")
        for unit in sorted(by_unit):
            print(f"  unit {unit}: {', '.join(by_unit[unit])}")

    if gaps:
        print(f"\nmissing tier-1 / tier-4 facet coverage ({len(gaps)}):")
        by_unit = {}
        for unit, tag, tier, f in gaps:
            by_unit.setdefault(unit, []).append(f"{tag} tier{tier}/{f}")
        for unit in sorted(by_unit):
            print(f"  unit {unit}: {', '.join(by_unit[unit])}")

    if not ceilings and not gaps:
        print("  all words have full tier 1-4 coverage on both facets.")

    if impossible:
        print(f"\nIMPOSSIBLE — facet with zero questions at any tier ({len(impossible)}):")
        for unit, tag, f in impossible:
            print(f"  unit {unit}: {tag} has no '{f}' question at all")
        if raise_on_impossible:
            detail = ", ".join(f"unit {u}: {t} ({f})" for u, t, f in impossible)
            raise ImpossibleGraduationError(
                f"{len(impossible)} word(s) can never graduate — {detail}"
            )

    print("=== end coverage report ===\n")
    return {"impossible": impossible, "gaps": gaps, "ceilings": ceilings}