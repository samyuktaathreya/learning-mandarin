# app/core/config/textbook.py
from pathlib import Path

# 1. Base Directories
# This file lives in: app/core/config/textbook.py
CONFIG_DIR = Path(__file__).resolve().parent              # app/core/config
CORE_DIR = CONFIG_DIR.parent                              # app/core
APP_DIR = CORE_DIR.parent                                 # app
ROOT_DIR = APP_DIR.parent                                 # learning-mandarin (Repo Root)

# 2. Textbook Data Directories
TEXTBOOK_DATA_DIR = APP_DIR / "language-app-data" / "data"
TEXTBOOK_RAW_DIR = TEXTBOOK_DATA_DIR / "raw"
TEXTBOOK_INTERMEDIATE_DIR = TEXTBOOK_DATA_DIR / "intermediate"
TEXTBOOK_CLEAN_DIR = TEXTBOOK_DATA_DIR / "clean"

# 3. JSON File Paths (Questions, Vocabulary, Dictionaries)
QUESTIONS_FILEPATH = TEXTBOOK_CLEAN_DIR / "unit_questions_hsk1.json"
UNIT_VOCAB_TAGS_FILEPATH = TEXTBOOK_CLEAN_DIR / "unit_vocab_tags.json"
DICTIONARY_FILEPATH = TEXTBOOK_CLEAN_DIR / "hsk1_dictionary.json"
WORD_TO_PINYIN_FILEPATH = TEXTBOOK_INTERMEDIATE_DIR / "word_to_pinyin.json"

# 4. CC-CEDICT Dictionary Path
DICT_PATH = TEXTBOOK_RAW_DIR / "chinese_english_dictionary.u8"

# 5. Ensure directories exist when config is imported
TEXTBOOK_DATA_DIR.mkdir(parents=True, exist_ok=True)
TEXTBOOK_RAW_DIR.mkdir(parents=True, exist_ok=True)
TEXTBOOK_INTERMEDIATE_DIR.mkdir(parents=True, exist_ok=True)
TEXTBOOK_CLEAN_DIR.mkdir(parents=True, exist_ok=True)