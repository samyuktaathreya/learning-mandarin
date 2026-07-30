# app/core/config.py
from pathlib import Path

# 1. Base Directories
# This file lives in: app/core/config.py
CORE_DIR = Path(__file__).resolve().parent                       # app/core
ROOT_DIR = CORE_DIR.parent.parent                                # learning-mandarin (Repo Root)

# Navigate from Root down to the correct pipeline folder (using underscore)
PIPELINE_DIR = ROOT_DIR / "data_pipelines" / "characters"        # data_pipelines/characters
DATA_RAW_DIR = PIPELINE_DIR / "data" / "raw"                     # data_pipelines/characters/data/raw
OUTPUT_DATA_DIR = ROOT_DIR / "data" / "characters"               # data/characters

# 2. Input File Paths (Raw Data)
RAW_RADICALS_PATH = DATA_RAW_DIR / "radical-data.csv"
RAW_IDS_PATH = DATA_RAW_DIR / "ids.txt"
RAW_CONFUSIBLES_PATH = DATA_RAW_DIR / "hanzi_confusibles.txt"

# External vocabulary reference file
VOCAB_JSON_PATH = ROOT_DIR / "app" / "language-app-data" / "data" / "clean" / "unit_vocab_tags.json"

# 3. Output Database Path
OUTPUT_DB_PATH = OUTPUT_DATA_DIR / "characters.db"

# Ensure output directory exists when config is imported
OUTPUT_DATA_DIR.mkdir(parents=True, exist_ok=True)