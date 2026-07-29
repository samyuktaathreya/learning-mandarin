from sqlalchemy import Column, Integer, Text, UniqueConstraint, ForeignKey, Index
from characters.database import CharactersBase


class Character(CharactersBase):
    __tablename__ = "characters"

    codepoint       = Column(Text, primary_key=True)
    char            = Column(Text, unique=True, nullable=False)
    ids_raw         = Column(Text)                  # raw IDS string e.g. ⿱艹禺
    decomp_operator = Column(Text)                  # top-level operator e.g. ⿱, ⿰, None if atomic
    is_radical      = Column(Integer, default=0)    # 1 if this char is a Kangxi radical (or a radical variant)


class RadicalMeta(CharactersBase):
    """Radical-specific metadata -- only populated for rows where
    Character.is_radical == 1. Kept separate from Character since pinyin/
    english/stroke_count/radical_number don't apply to ordinary characters."""
    __tablename__ = "radical_meta"

    char           = Column(Text, ForeignKey("characters.char"), primary_key=True)
    radical_number = Column(Integer)
    pinyin         = Column(Text)
    english        = Column(Text)
    stroke_count   = Column(Integer)


class CharacterComponent(CharactersBase):
    __tablename__ = "character_components"

    id                 = Column(Integer, primary_key=True, autoincrement=True)
    char               = Column(Text, ForeignKey("characters.char"), nullable=False)
    component_char     = Column(Text, nullable=False)
    depth              = Column(Integer, nullable=False)   # 0=direct child, 1=grandchild, 2=great-grandchild
    position           = Column(Text)                     # left/right/top/bottom/enclosing/nested etc.
    frequency_in_corpus = Column(Integer, default=0)      # how many chars in the DB share this component

    __table_args__ = (
        Index("idx_component_lookup", "component_char"),
        Index("idx_char_lookup", "char"),
    )


class ConfusionPair(CharactersBase):
    __tablename__ = "confusion_pairs"

    id     = Column(Integer, primary_key=True, autoincrement=True)
    char_a = Column(Text, nullable=False)
    char_b = Column(Text, nullable=False)
    source = Column(Text, nullable=False, default="human_curated")

    __table_args__ = (
        UniqueConstraint("char_a", "char_b", name="_char_a_char_b_uc"),
        Index("idx_confusion_a", "char_a"),
        Index("idx_confusion_b", "char_b"),
    )