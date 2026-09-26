"""Runs the pinyin data pipeline end to end: seed the syllable inventory,
backfill diacritic pinyin for TTS/display, backfill representative characters,
then clean out rows with no or inaccurate characters."""
from data_pipelines.pinyin.scripts.pinyin_parser import seed_pinyin_syllables
from data_pipelines.pinyin.scripts.backfill_diacritic import backfill
from data_pipelines.pinyin.scripts.backfill_character import backfill_character
from data_pipelines.pinyin.scripts.clean_character import clean_character
from app.textbook.db_utils import init_db

if __name__ == "__main__":
    init_db()
    seed_pinyin_syllables()
    backfill()
    backfill_character()
    clean_character()