from database import SessionLocal, engine, Base, init_db, unit_to_tags_dict
from models.user import StrengthTable, User
import crud
from datetime import datetime

Base.metadata.create_all(bind=engine)
init_db()

db = SessionLocal()
USER_ID = 1
MASTERED_COUNT = 10
MASTERED_STABILITY = 365
now = datetime.utcnow()

for unit in (3, 4):
    for tag in unit_to_tags_dict.get(unit, set()):
        row = crud.get_strength_row(db, USER_ID, tag)
        if row is None:
            row = StrengthTable(user_id=USER_ID, tag=tag)
            db.add(row)
        row.correct_count = MASTERED_COUNT
        row.stability = MASTERED_STABILITY
        row.last_practice = now
db.commit()

for unit in (3, 4):
    crud.graduate_unit(db, USER_ID, unit)

user = crud.get_user(db, USER_ID)
print('graduated_units:', crud.get_graduated_units(db, USER_ID))
print('current_unit:', user.current_unit)