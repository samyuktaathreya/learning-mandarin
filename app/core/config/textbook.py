from .shared import ROOT_DIR, PIPELINE_DIR, APP_DIR

# 1. Textbook Data Directories
# Using ROOT_DIR keeps data outside your source code package, sitting at the repository root.

# raw and intermediate data is in the data pipeline and clean data is in the data folder
TEXTBOOK_APP_DATA_DIR = APP_DIR / "textbook" / "data"

TEXTBOOK_DATA_PIPELINES_DIR = PIPELINE_DIR / "textbook" / "data"
TEXTBOOK_RAW_DIR = TEXTBOOK_DATA_PIPELINES_DIR / "raw"
TEXTBOOK_INTERMEDIATE_DIR = TEXTBOOK_DATA_PIPELINES_DIR / "intermediate"

# 2. JSON File Paths (Questions, Vocabulary, Dictionaries)
QUESTIONS_FILEPATH = TEXTBOOK_APP_DATA_DIR / "unit_questions_hsk1.json"
UNIT_VOCAB_TAGS_FILEPATH = TEXTBOOK_APP_DATA_DIR / "unit_vocab_tags.json"
DICTIONARY_FILEPATH = TEXTBOOK_APP_DATA_DIR / "hsk1_dictionary.json"
WORD_TO_PINYIN_FILEPATH = TEXTBOOK_INTERMEDIATE_DIR / "word_to_pinyin.json"
INDEX_OUTPUT_JSON = TEXTBOOK_APP_DATA_DIR / "index_output.json"

# 3. CC-CEDICT Dictionary Path
DICT_PATH = TEXTBOOK_RAW_DIR / "chinese_english_dictionary.u8"