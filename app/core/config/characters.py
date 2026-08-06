# app/core/config.py
from pathlib import Path
from .shared import ROOT_DIR, PIPELINE_DIR, APP_DIR

# Navigate from Root down to the correct pipeline folder (using underscore)
DATA_RAW_DIR = PIPELINE_DIR / "characters" / "data" / "raw"                     # data_pipelines/characters/data/raw

# 2. Input File Paths (Raw Data)
RAW_RADICALS_PATH = DATA_RAW_DIR / "radical-data.csv"
RAW_IDS_PATH = DATA_RAW_DIR / "ids.txt"
RAW_CONFUSIBLES_PATH = DATA_RAW_DIR / "hanzi_confusibles.txt"

# 4. data pipeline scripts directory
CHARACTER_SCRIPTS_DIR = PIPELINE_DIR / "characters" / "scripts"