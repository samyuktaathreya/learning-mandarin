"""
Pronunciation practice module.

Generates listening + speaking practice questions for sounds (initials,
finals, full syllables) that are difficult for English speakers (see
pinyin_utils.GATED_SOUNDS).

Gating: only a learner who has seen a target sound in the curriculum AND
whose strength on it is below threshold gets asked about it. Otherwise:
no question for that sound this round. This prevents wasting time on sounds
the learner already knows cold, and doesn't quiz sounds before they've been
taught (can't practice something you've never heard before).

Word/sentence selection: questions anchor on a real word from the curriculum
that contains the target sound, plus a distractors list of other words with
confusible sounds. The learner either hears audio and types the word
(listening question), or reads the word and speaks it (speaking question) --
both test the same thing (is this learner saying the sound correctly?), but
from different input modalities. See grading.py for how the speaking
transcription is grade against expected pinyin.

SENSE-AWARE UPDATE: optional unit_number/hsk_level parameters added throughout
so callers that know the curriculum context (e.g. a lesson focused on a
specific unit) can pass that context for pinyin resolution when needed. Most
call sites won't have this context, so defaults are fine.
"""

from typing import Optional
from sqlalchemy.orm import Session

from app.pinyin_utils import split_pinyin_sounds, GATED_INITIALS, GATED_FINALS
from textbook import crud

# Minimum required sounds per practicefor pronunciation practice to be viable
MIN_DISTRACTOR_WORDS = 3


def _tag_sounds(db: Session, tag: str, unit_number: Optional[int] = None, hsk_level: int = 1) -> set:
    """Which gated sounds (difficult-for-English-speakers initials/finals) are
    present in `tag`'s pronunciation. unit_number/hsk_level are optional: pass
    them if you know the curriculum context so a multi-sense word resolves to
    its relevant taught meaning."""
    p = crud.get_pinyin_for_word(db, tag, unit_number=unit_number, hsk_level=hsk_level)
    if not p:
        return set()
    sounds = set()
    for initial, final, _tone in split_pinyin_sounds(p):
        if initial in GATED_INITIALS:
            sounds.add(initial)
        if final in GATED_FINALS:
            sounds.add(final)
    return sounds


def get_words_with_sound(db: Session, target_sound: str, unit_number: Optional[int] = None, 
                         hsk_level: int = 1, limit: int = 20) -> list[str]:
    """Every word in Vocab that contains `target_sound` (one of the gated
    initials/finals). Limited to `limit` results for performance. unit_number/
    hsk_level are optional: pass them if you want sense resolution for multi-
    sense words (though this usually doesn't matter for this use case)."""
    all_hanzi = crud.get_all_vocab_hanzi(db)
    candidates = []
    for tag in all_hanzi:
        if target_sound in _tag_sounds(db, tag, unit_number=unit_number, hsk_level=hsk_level):
            candidates.append(tag)
        if len(candidates) >= limit:
            break
    return candidates


def get_confusible_words(db: Session, target_sound: str, exclude: set[str], 
                         unit_number: Optional[int] = None, hsk_level: int = 1,
                         limit: int = 10) -> list[str]:
    """Words that contain a DIFFERENT gated sound (i.e. confusible with
    target_sound), excluding anything already in `exclude`. Used to build
    distractors for multiple-choice listening questions. unit_number/hsk_level
    are optional context params, threaded through the same way."""
    all_hanzi = crud.get_all_vocab_hanzi(db)
    candidates = []
    for tag in all_hanzi:
        if tag in exclude:
            continue
        sounds = _tag_sounds(db, tag, unit_number=unit_number, hsk_level=hsk_level)
        # include if it has ANY gated sound, but NOT if it has the target sound
        if sounds and target_sound not in sounds:
            candidates.append(tag)
        if len(candidates) >= limit:
            break
    return candidates


def build_listening_question(target_sound: str, word: str, db: Session, 
                             unit_number: Optional[int] = None, hsk_level: int = 1) -> dict | None:
    """Listening comprehension question: "You hear [audio of word], which one
    is it?" -- multiple choice among the word and other words with confusible
    sounds."""
    from textbook import services
    definition = services.get_dictionary_entry(db, word, unit_number=unit_number, hsk_level=hsk_level)
    if not definition or not definition.get("english"):
        return None  # need a definition to anchor the prompt

    distractors = get_confusible_words(db, target_sound, exclude={word}, 
                                       unit_number=unit_number, hsk_level=hsk_level,
                                       limit=3)
    if len(distractors) < MIN_DISTRACTOR_WORDS:
        return None  # not enough confusible options to make a meaningful question

    options = [word] + distractors
    import random
    random.shuffle(options)

    return {
        "question_type": "sound_listening",
        "target_sound": target_sound,
        "word": word,
        "definition": definition["english"],
        "options": options,
        "answer": word,
    }


def build_speaking_question(target_sound: str, word: str, db: Session,
                            unit_number: Optional[int] = None, hsk_level: int = 1) -> dict | None:
    """Speaking production question: "You see [English meaning], say this word
    in Chinese" -- tests whether the learner can produce the target sound
    correctly."""
    from textbook import services
    definition = services.get_dictionary_entry(db, word, unit_number=unit_number, hsk_level=hsk_level)
    if not definition or not definition.get("english"):
        return None

    pinyin = get_pinyin(db, word, unit_number=unit_number, hsk_level=hsk_level)
    if not pinyin:
        return None

    return {
        "question_type": "sound_speaking",
        "target_sound": target_sound,
        "word": word,
        "definition": definition["english"],
        "pinyin": pinyin,
        "answer": word,
    }


def generate_sound_questions(db: Session, target_sound: str, 
                             unit_number: Optional[int] = None, hsk_level: int = 1,
                             num_questions: int = 2) -> list[dict]:
    """Generate up to `num_questions` pronunciation-practice questions for a
    target sound (one of the gated initials/finals). Each question tests
    either listening comprehension or speaking production. unit_number/hsk_level
    are optional: pass them if this sound practice is being generated within
    a specific curriculum context."""
    questions = []
    words = get_words_with_sound(db, target_sound, unit_number=unit_number, 
                                 hsk_level=hsk_level, limit=num_questions * 3)
    if not words:
        return []

    import random
    random.shuffle(words)

    for word in words[:num_questions]:
        if random.random() < 0.5:
            q = build_listening_question(target_sound, word, db, 
                                         unit_number=unit_number, hsk_level=hsk_level)
            if not q:
                q = build_speaking_question(target_sound, word, db,
                                            unit_number=unit_number, hsk_level=hsk_level)
        else:
            q = build_speaking_question(target_sound, word, db,
                                        unit_number=unit_number, hsk_level=hsk_level)
            if not q:
                q = build_listening_question(target_sound, word, db,
                                             unit_number=unit_number, hsk_level=hsk_level)
        if q:
            questions.append(q)

    return questions