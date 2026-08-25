"""Per-run session log. Truncated on startup (see main.py) so it only ever
holds the current backend run. Deliberately not a DB table -- this is debug
output, not state, and it should vanish with the process."""
import json
from datetime import datetime

LOG_PATH = "./session_log.jsonl"


def reset_log():
    """Truncate. Called once from main.py's startup hook."""
    open(LOG_PATH, "w").close()


def log_session(user_id, unit, tier_picks, review_picks, tiers, min_counts, stop_reason):
    """One JSON line per generated session.

    tier_picks / review_picks are [(question, served_for_tag), ...] -- the tag
    is the word the generator CHOSE the question for, which cannot be
    recovered from the question afterward (a sentence carries every
    constituent word as a tag).
    """
    def row(q, tag, source):
        return {
            "id": q["id"],
            "type": q["question_type"],
            "for_tag": tag,
            "tier": tiers.get(tag, 1),
            "min_count": min_counts.get(tag),   # None for review words
            "source": source,
        }

    entry = {
        "ts": datetime.utcnow().isoformat(timespec="seconds"),
        "user_id": user_id,
        "unit": unit,
        "n_tier": len(tier_picks),
        "n_review": len(review_picks),
        "stop_reason": stop_reason,
        "questions": (
            [row(q, t, "tier") for q, t in tier_picks]
            + [row(q, t, "review") for q, t in review_picks]
        ),
    }
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")