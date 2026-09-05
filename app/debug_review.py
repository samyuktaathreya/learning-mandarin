from app.core.database import SessionLocal as SessionDB
from textbook.db_utils import SessionLocal as TextbookSessionLocal
from session.services.review_engine import _due_review_facets
from textbook import crud
from app.core.logger import logger

db = SessionDB()
textbook_db = TextbookSessionLocal()

user_id = 1
hsk_level = 1

due = _due_review_facets(db, textbook_db, user_id, hsk_level)
logger.debug("Due (tag, facet) pairs:", due)

for tag, facet in due:
    home_unit = crud.get_tags_to_unit_map(textbook_db).get(tag)
    logger.debug(f"\ntag={tag!r} facet={facet}")
    logger.debug("  home unit:", home_unit)
    questions = crud.get_questions_for_tag_up_to_unit(textbook_db, tag, max_unit=13, max_hsk_level=hsk_level)
    logger.debug(f"  questions found (up to unit 13, hsk<=1): {len(questions)}")
    for q in questions[:5]:
        logger.debug("   -", q["question_type"], "| unit:", q["unit"])

db.close()
textbook_db.close()