# PATHS TO DATA!!!
from .shared import ROOT_DIR
from pathlib import Path
import os

DATA_DIR = ROOT_DIR / "data"

CHARACTERS_DB = DATA_DIR / "characters.db"
MANDARIN_APP_DB = Path(os.getenv("USER_DB_PATH", DATA_DIR / "mandarin_app.db"))
TEXTBOOK_DB = DATA_DIR / "textbook.db"