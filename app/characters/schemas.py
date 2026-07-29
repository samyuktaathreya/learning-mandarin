from pydantic import BaseModel


class CharacterSchema(BaseModel):
    codepoint: str
    char: str
    ids_raw: str | None
    decomp_operator: str | None

    class Config:
        from_attributes = True


class CharacterComponentSchema(BaseModel):
    component_char: str
    depth: int
    position: str | None
    frequency_in_corpus: int

    class Config:
        from_attributes = True


class SimilarCharacterSchema(BaseModel):
    char: str
    shared_components: list[str]
    shared_count: int


class SimilarByPositionSchema(BaseModel):
    char: str
    shared_components: list[str]
    position: str


class ConfusiblesSchema(BaseModel):
    char: str
    confusibles: list[str]


class ConfusiblesMapSchema(BaseModel):
    # {char: [confusible, ...]} — used for batch pre-loading
    pairs: dict[str, list[str]]