from textbook.services import word_to_pinyin
from pinyin_utils import split_pinyin_sounds, GATED_INITIALS, GATED_FINALS


def _tag_sounds(tag: str) -> set:
    p = word_to_pinyin.get(tag)
    if not p:
        return set()
    sounds = set()
    for initial, final, _tone in split_pinyin_sounds(p):
        if initial in GATED_INITIALS:
            sounds.add(initial)
        if final in GATED_FINALS:
            sounds.add(final)
    return sounds