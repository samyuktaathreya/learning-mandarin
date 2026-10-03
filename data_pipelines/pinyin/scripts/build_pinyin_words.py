"""
data_pipelines/build_pinyin_words.py

Builds the pinyin_words table: every two-character Vocab word whose pinyin
splits into two syllables that both exist in pinyin_syllables. The pinyin
feature uses these rows for tone-pair drills, so a learner hears real
textbook words instead of two random syllables whenever one fits the sounds
they have unlocked.

Each row stores both syllables' tones and initial/final tags (copied from
pinyin_syllables, so words are tagged exactly the way single syllables are),
which lets the app find "a word with tone pair 2-4 using only unlocked
sounds" in one query.

Special cases, kept in the table and marked so the app can treat them
differently:
  - Neutral-tone second syllable (谢谢 xie4xie5): tone2 = 5, tone_pair "4-5".
    pinyin_syllables has no tone-5 rows, so syllable2_id is NULL and the
    initial/final tags are borrowed from the same syllable at another tone.
    Intended for speaking questions.
  - 3-3 words (水果 shui3guo3): sandhi = 1. Written 3-3, pronounced 2-3.
  - 一 / 不 words need no flag: Vocab already stores the tone as spoken
    (一起 is yi4qi3), so written and heard tones agree.

Still skipped: word_type="auto" rows (no reliable pinyin), erhua words like
哪儿 (one syllable for two characters), and a neutral first syllable.

Safe to re-run: the table is dropped and rebuilt each time, which also picks
up any column changes to PinyinWord. Nothing else references its ids.

Run:  python -m data_pipelines.build_pinyin_words
"""
import re
from collections import Counter, defaultdict

from app.textbook.db_utils import SessionLocal
from app.textbook.models import PinyinSyllable, PinyinWord, Vocab, WordType

FULL_TONES = {1, 2, 3, 4}
NEUTRAL_TONE = 5
SAMPLES_PER_REASON = 8

# Whole string must be syllable+digit repeated, e.g. "cao3mei2".
_WORD_RE = re.compile(r"(?:[a-z]+[0-5])+")
_SYLLABLE_RE = re.compile(r"([a-z]+)([0-5])")


def _normalize(pinyin: str) -> str:
    """Lowercase, write ü as "v" (app convention), drop spaces/apostrophes."""
    s = pinyin.strip().lower().replace("u:", "v").replace("ü", "v")
    return re.sub(r"[\s'’\-·]", "", s)


def split_numbered_pinyin(pinyin: str) -> list[tuple[str, int]] | None:
    """"cao3mei2" -> [("cao", 3), ("mei", 2)]. Neutral tone (0 or 5) comes
    back as 5.

    Returns None when the string isn't fully numbered pinyin (tone marks
    instead of digits, a syllable with no digit, stray punctuation), so the
    caller can report it instead of silently mis-splitting.
    """
    s = _normalize(pinyin)
    if not _WORD_RE.fullmatch(s):
        return None
    return [(syl, int(tone) or NEUTRAL_TONE) for syl, tone in _SYLLABLE_RE.findall(s)]


def build_pinyin_words() -> None:
    db = SessionLocal()
    try:
        # (syllable, tone) -> row, plus syllable -> any row for neutral-tone
        # syllables, which only need the initial/final tags.
        by_syllable_tone: dict[tuple[str, int], PinyinSyllable] = {}
        by_syllable: dict[str, PinyinSyllable] = {}
        for row in db.query(PinyinSyllable).all():
            key = _normalize(row.syllable)
            by_syllable_tone[(key, row.tone)] = row
            by_syllable.setdefault(key, row)

        vocab_rows = db.query(Vocab).filter(Vocab.word_type != WordType.auto).all()

        new_rows: list[PinyinWord] = []
        skip_counts: Counter[str] = Counter()
        skip_samples: dict[str, list[str]] = defaultdict(list)

        def skip(reason: str, vocab: Vocab) -> None:
            skip_counts[reason] += 1
            if len(skip_samples[reason]) < SAMPLES_PER_REASON:
                skip_samples[reason].append(f"{vocab.hanzi} ({vocab.pinyin})")

        for vocab in vocab_rows:
            if len(vocab.hanzi) != 2:
                skip_counts["not two characters"] += 1
                continue
            if not (vocab.pinyin or "").strip():
                skip("no pinyin", vocab)
                continue

            parts = split_numbered_pinyin(vocab.pinyin)
            if parts is None:
                skip("pinyin not in numbered format", vocab)
                continue
            if len(parts) != 2:
                skip("not two syllables", vocab)
                continue

            (syl1, tone1), (syl2, tone2) = parts
            if tone1 not in FULL_TONES:
                skip("neutral first syllable", vocab)
                continue

            row1 = by_syllable_tone.get((syl1, tone1))
            if row1 is None:
                skip("syllable not in pinyin_syllables", vocab)
                continue

            if tone2 == NEUTRAL_TONE:
                tag_row2 = by_syllable.get(syl2)
                if tag_row2 is None:
                    skip("neutral syllable not in pinyin_syllables at any tone", vocab)
                    continue
                syllable2_id = None
            else:
                tag_row2 = by_syllable_tone.get((syl2, tone2))
                if tag_row2 is None:
                    skip("syllable not in pinyin_syllables", vocab)
                    continue
                syllable2_id = tag_row2.id

            new_rows.append(PinyinWord(
                vocab_id=vocab.id,
                hanzi=vocab.hanzi,
                tone_pair=f"{tone1}-{tone2}",
                sandhi=1 if (tone1 == 3 and tone2 == 3) else 0,
                syllable1_id=row1.id,
                syllable1=row1.syllable,
                tone1=tone1,
                initial1=row1.initial_tag,
                final1=row1.final_tag,
                syllable2_id=syllable2_id,
                syllable2=tag_row2.syllable,
                tone2=tone2,
                initial2=tag_row2.initial_tag,
                final2=tag_row2.final_tag,
            ))

        print(f"Checked {len(vocab_rows)} vocab rows, {len(new_rows)} usable words.")
        print("\nSkipped:")
        for reason, count in skip_counts.most_common():
            print(f"  {count:5d}  {reason}")
            for sample in skip_samples[reason]:
                print(f"           {sample}")

        if not new_rows:
            print("\nNo usable words found -- leaving pinyin_words untouched.")
            return

        # Drop and recreate so column changes to PinyinWord take effect.
        db.commit()
        bind = db.get_bind()
        PinyinWord.__table__.drop(bind=bind, checkfirst=True)
        PinyinWord.__table__.create(bind=bind)

        db.add_all(new_rows)
        db.commit()

        per_pair = Counter(row.tone_pair for row in new_rows)
        print("\nWords per tone pair (x-5 = neutral second syllable):")
        for t1 in sorted(FULL_TONES):
            counts = "   ".join(
                f"{t1}-{t2}: {per_pair.get(f'{t1}-{t2}', 0):2d}"
                for t2 in (1, 2, 3, 4, NEUTRAL_TONE)
            )
            print(f"  {counts}")
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    build_pinyin_words()