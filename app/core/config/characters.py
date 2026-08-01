# app/core/config.py
from pathlib import Path
from .shared import DATA_DIR, ROOT_DIR, PIPELINE_DIR

# Navigate from Root down to the correct pipeline folder (using underscore)
DATA_RAW_DIR = PIPELINE_DIR / "data" / "raw"                     # data_pipelines/characters/data/raw

# 2. Input File Paths (Raw Data)
RAW_RADICALS_PATH = DATA_RAW_DIR / "radical-data.csv"
RAW_IDS_PATH = DATA_RAW_DIR / "ids.txt"
RAW_CONFUSIBLES_PATH = DATA_RAW_DIR / "hanzi_confusibles.txt"

# External vocabulary reference file
VOCAB_JSON_PATH = ROOT_DIR / "app" / "language-app-data" / "data" / "clean" / "unit_vocab_tags.json"

# 3. Output Database Path
OUTPUT_DB_PATH = DATA_DIR / "characters.db"

# Ensure output directory exists when config is imported
DATA_DIR.mkdir(parents=True, exist_ok=True)