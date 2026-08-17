# for data_pipelines/external_sources
from app.core.config.shared import ROOT_DIR, APP_DIR, PIPELINE_DIR

EXTERNAL_SOURCES = PIPELINE_DIR / "external_sources"
HSK_VOCAB_LIST = EXTERNAL_SOURCES / "hsk-vocab-list"
VOCAB_LIST_JSON = HSK_VOCAB_LIST / "data" / "clean" / "hsk_vocab_list.json"