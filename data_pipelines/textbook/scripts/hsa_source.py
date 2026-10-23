"""
Read-only access to the hsk-sentences-audio dataset (CC BY-SA 4.0; see
ATTRIBUTION.md), keyed by the dataset's sentence id -- the same id stored in
Sentence.external_id.

import_hsa.py writes the sentences; tag_sentences.py reads each sentence's
`tokens` back from here instead of copying them into the DB:
    [{"word": "高兴", "pinyin": "gāo xìng", "gloss_en": "happy; glad"}, ...]
The tokens cover every character of the sentence, in order, and are a
ready-made word split (99.7% of words are in the HSK list). gloss_en is a
CC-CEDICT definition of the word, close to -- but not always identical to --
one of the vocab list's meanings.
"""
import re
from functools import lru_cache

from hsk_sentences_audio import load_sentences, audio_url  # noqa: F401  (audio_url re-exported for the app)

# 80 HSK 4+ sentences ship with an unfinished translation like "placeholder-p1".
_PLACEHOLDER_RE = re.compile(r"placeholder", re.I)


@lru_cache(maxsize=1)
def sentences_by_id() -> dict:
    return {s["id"]: s for s in load_sentences()}


def get(external_id: str) -> dict | None:
    return sentences_by_id().get(external_id)


def tokens(external_id: str) -> list[dict]:
    s = get(external_id)
    return s["tokens"] if s else []


def english(s: dict) -> str:
    return (s.get("translation") or {}).get("en", "").strip()


def has_placeholder_translation(s: dict) -> bool:
    return bool(_PLACEHOLDER_RE.search(english(s)))