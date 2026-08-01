from .shared import ROOT_DIR

# 1. Textbook Data Directories
# Using ROOT_DIR keeps data outside your source code package, sitting at the repository root.
TEXTBOOK_DATA_DIR = ROOT_DIR / "language-app-data" / "data"
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
# parents=True ensures TEXTBOOK_DATA_DIR is also created automatically
for directory in (TEXTBOOK_RAW_DIR, TEXTBOOK_INTERMEDIATE_DIR, TEXTBOOK_CLEAN_DIR):
    directory.mkdir(parents=True, exist_ok=True)