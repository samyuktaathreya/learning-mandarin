# check_stability.py
from database import SessionLocal
from models.user import StrengthTable, WordTierProgress
from datetime import datetime

db = SessionLocal()
tags = ["米饭", "住", "电影"]

for tag in tags:
    tier_row = db.query(WordTierProgress).filter(
        WordTierProgress.user_id == 1,
        WordTierProgress.tag == tag,
    ).first()
    tier = tier_row.tier if tier_row else 1

    rows = db.query(StrengthTable).filter(
        StrengthTable.user_id == 1,
        StrengthTable.tag == tag,
    ).all()

    print(f"\n=== {tag} (tier={tier}) ===")
    for r in rows:
        now = datetime.utcnow()
        elapsed_days = (now - r.last_practice).total_seconds() / 86400
        strength = 0.5 ** (elapsed_days / r.stability) if r.stability else None
        print(f"  facet={r.facet:10s} correct_count={r.correct_count} "
              f"stability={r.stability} miss_count={getattr(r,'miss_count',None)} "
              f"last_practice={r.last_practice} elapsed_days={elapsed_days:.6f} "
              f"strength={strength}")