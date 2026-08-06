from .shared import ROOT_DIR, PIPELINE_DIR, APP_DIR

# 1. Textbook Data Directories
# Using ROOT_DIR keeps data outside your source code package, sitting at the repository root.

# raw and intermediate data is in the data pipeline and clean data is in the data folder
TEXTBOOK_APP_DIR = APP_DIR / "textbook"

TEXTBOOK_DATA_PIPELINES_DIR = PIPELINE_DIR / "textbook"
TEXTBOOK_DATA_PIPELINES_DATA_DIR = TEXTBOOK_DATA_PIPELINES_DIR / "data"
TEXTBOOK_RAW_DIR = TEXTBOOK_DATA_PIPELINES_DATA_DIR / "raw"
TEXTBOOK_INTERMEDIATE_DIR = TEXTBOOK_DATA_PIPELINES_DATA_DIR / "intermediate"

# JSON FILE PATHS ARE DEPRECATED
'''
QUESTIONS_FILEPATH = TEXTBOOK_APP_DATA_DIR / "unit_questions_hsk1.json"
UNIT_VOCAB_TAGS_JSON = TEXTBOOK_APP_DATA_DIR / "unit_vocab_tags.json"
DICTIONARY_FILEPATH = TEXTBOOK_APP_DATA_DIR / "hsk1_dictionary.json"
WORD_TO_PINYIN_FILEPATH = TEXTBOOK_INTERMEDIATE_DIR / "word_to_pinyin.json"
INDEX_OUTPUT_JSON = TEXTBOOK_APP_DATA_DIR / "index_output.json"
UNITS_OUTPUT_JSON = TEXTBOOK_APP_DATA_DIR / "units_output.json"
'''

# 3. CC-CEDICT Dictionary Path
DICT_PATH = TEXTBOOK_RAW_DIR / "chinese_english_dictionary.u8"

# pipeline paths
REJECTED_VOCAB_CACHE = TEXTBOOK_DATA_PIPELINES_DIR / "data" / "intermediate" / "hsk1-rejected-vocab-cache.txt"
SOP_PATH = TEXTBOOK_DATA_PIPELINES_DIR / "SOPs"
GRAMMAR_SOP_PATH = SOP_PATH / "grammar_tip"
GRAMMAR_TIP_SOP = SOP_PATH / "grammar_tip" / "grammar_tip.txt"
REFORMAT_GRAMMAR_TIP_SOP = GRAMMAR_SOP_PATH / "reformat_grammar_tip.txt"
OCR_PATH = TEXTBOOK_INTERMEDIATE_DIR / "OCR_cache"

PIPELINE_SCRIPTS_DIR = PIPELINE_DIR / "textbook" / "scripts"