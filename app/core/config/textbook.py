from .shared import APP_DIR

# 1. Textbook Data Directories
TEXTBOOK_DATA_DIR = APP_DIR / "language-app-data" / "data"
TEXTBOOK_RAW_DIR = TEXTBOOK_DATA_DIR / "raw"
TEXTBOOK_INTERMEDIATE_DIR = TEXTBOOK_DATA_DIR / "intermediate"
TEXTBOOK_CLEAN_DIR = TEXTBOOK_DATA_DIR / "clean"

# 2. JSON File Paths (Questions, Vocabulary, Dictionaries)
QUESTIONS_FILEPATH = TEXTBOOK_CLEAN_DIR / "unit_questions_hsk1.json"
UNIT_VOCAB_TAGS_FILEPATH = TEXTBOOK_CLEAN_DIR / "unit_vocab_tags.json"
DICTIONARY_FILEPATH = TEXTBOOK_CLEAN_DIR / "hsk1_dictionary.json"
WORD_TO_PINYIN_FILEPATH = TEXTBOOK_INTERMEDIATE_DIR / "word_to_pinyin.json"

# 3. CC-CEDICT Dictionary Path
DICT_PATH = TEXTBOOK_RAW_DIR / "chinese_english_dictionary.u8"

# 4. Ensure directories exist when config is imported
TEXTBOOK_DATA_DIR.mkdir(parents=True, exist_ok=True)
TEXTBOOK_RAW_DIR.mkdir(parents=True, exist_ok=True)
TEXTBOOK_INTERMEDIATE_DIR.mkdir(parents=True, exist_ok=True)
TEXTBOOK_CLEAN_DIR.mkdir(parents=True, exist_ok=True)