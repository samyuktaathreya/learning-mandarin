"""One-off: make every unit 3-7 vocab tag look like it was learned to 100%
YESTERDAY, so per-facet review surfaces them as due TODAY.

For each unit-3..7 vocab tag (from the real unit_to_vocab_tags_dict):
  - StrengthTable, BOTH facets: correct_count = 3 (exactly over the review
    bar), stability = 1.0 (Option C floor -> first review ~1 day out),
    last_practice = ~24h ago. With stability 1.0 and 1 day elapsed, decayed
    strength = 0.5**(1/1) = 0.5 < REVIEW_THRESHOLD(0.80) => DUE today.
  - WordTierProgress: tier = 4 (fully climbed, required for review eligibility).

Does NOT touch: the user row, unit 8, or any unit outside 3-7. Idempotent --
re-running just re-stamps the same rows. Run once from the project root:

    python seed_review_test.py
"""
from datetime import datetime, timedelta

from database import SessionLocal, unit_to_vocab_tags_dict
from models.user import StrengthTable, WordTierProgress

USER_ID = 1
UNITS = (3, 4, 5, 6, 7)
FACETS = ("character", "pinyin")
CORRECT_COUNT = 3          # exactly over GRADUATION_THRESHOLD
STABILITY = 1.0            # Option C floor -> tight first review
TIER = 4                   # required for review eligibility
YESTERDAY = datetime.utcnow() - timedelta(days=1)


def main():
    db = SessionLocal()
    try:
        # gather the real vocab tags for units 3-7
        tags = set()
        for u in UNITS:
            tags |= unit_to_vocab_tags_dict.get(u, set())
        if not tags:
            print("No tags found for units 3-7 -- is unit_vocab_tags.json loaded?")
            return

        strength_updated = strength_created = 0
        for tag in tags:
            for facet in FACETS:
                row = db.query(StrengthTable).filter(
                    StrengthTable.user_id == USER_ID,
                    StrengthTable.tag == tag,
                    StrengthTable.facet == facet,
                ).first()
                if row:
                    strength_updated += 1
                else:
                    row = StrengthTable(tag=tag, user_id=USER_ID, facet=facet)
                    db.add(row)
                    strength_created += 1
                row.correct_count = CORRECT_COUNT
                row.times_seen = max(row.times_seen or 0, CORRECT_COUNT)
                row.stability = STABILITY
                row.last_practice = YESTERDAY

        tier_updated = tier_created = 0
        for tag in tags:
            trow = db.query(WordTierProgress).filter(
                WordTierProgress.user_id == USER_ID,
                WordTierProgress.tag == tag,
            ).first()
            if trow:
                tier_updated += 1
            else:
                trow = WordTierProgress(user_id=USER_ID, tag=tag)
                db.add(trow)
                tier_created += 1
            trow.tier = TIER

        db.commit()
        print(f"Units 3-7: {len(tags)} vocab tags stamped.")
        print(f"  StrengthTable rows: {strength_updated} updated, {strength_created} created "
              f"({len(tags)} tags x {len(FACETS)} facets).")
        print(f"  WordTierProgress rows: {tier_updated} updated, {tier_created} created (tier={TIER}).")
        print(f"  correct_count={CORRECT_COUNT}, stability={STABILITY}, "
              f"last_practice={YESTERDAY.isoformat(timespec='seconds')} (UTC).")
        print("  -> all should be DUE for review today. Check /api/debug/1 review_due_facet_count.")
    finally:
        db.close()


if __name__ == "__main__":
    main()