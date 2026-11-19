from sqlalchemy.orm import Session

from app.session.schemas import SessionResponse
from app.textbook import crud as textbook_crud


def _is_new(point: dict, current_unit: int, hsk_level: int) -> bool:
    """A point is new to the learner when it is first taught in their
    current unit (GrammarTip.unit_id). Points with no unit are never new."""
    return (
        point["unit_number"] is not None
        and point["unit_number"] == current_unit
        and point["unit_hsk_level"] == hsk_level
    )


def attach_question_sources(textbook_db: Session, session_response: SessionResponse,
                            current_unit: int, hsk_level: int = 1) -> SessionResponse:
    """Adds sentence credit and short grammar tips to every textbook question.

    On each question with a textbook db_id:
      sentence_source: None, or for sentences that need a label / credit --
          {"source": "ai_generated", "label", "generated_by"}
          {"source": "tatoeba", "label", "author", "url", "license", "license_url"}
      grammar_tips: the points the question practises (QuestionGrammar), new
          ones first -- each
            {"grammar_code", "label", "label_full", "hsk_level", "unit_number",
             "is_new",
             "parts": [short tip: {"part_id", "words", "title", "structure": [lines],
                       "meaning", "placement", "english_note", "text",
                       "examples": [{"sentence_id", "hanzi", "pinyin", "pinyin_display",
                                     "english", "literal": [{"word", "pinyin", "gloss"}]}],
                       "credit": {"label", "generated_by", "sources", "license", ...}}],
             "sections": the parts flattened to [{title, body, table}] for the
                         existing grammar-tip renderer}
          `parts` is the tip the question's sentence uses (SentenceGrammar.part_id),
          usually one. Examples never include the question's own sentence --
          it would give the answer away -- and leave out the per-word
          `dictionary` (about 40% of the payload; word clicks already go
          through /api/lookup) -- `literal` keeps just each word's pinyin
          and short gloss for the word-by-word translation.

    Questions without a textbook db_id (character / pinyin questions) are
    left alone."""
    per_question, points = textbook_crud.get_question_extras(
        textbook_db, [q.get("db_id") for q in session_response.question_set]
    )
    new_codes = {code for code, p in points.items() if _is_new(p, current_unit, hsk_level)}

    for q in session_response.question_set:
        extra = per_question.get(q.get("db_id"))
        if extra is None:
            continue
        q["sentence_source"] = extra["sentence_source"]
        links = sorted(extra["grammar"], key=lambda g: g["grammar_code"] not in new_codes)  # stable: new first
        q["grammar_tips"] = []
        for link in links:
            point = points[link["grammar_code"]]
            parts = [_without_sentence(p, extra["sentence_id"]) for p in link["parts"]]
            q["grammar_tips"].append({
                "grammar_code": point["grammar_code"],
                "label": point["label"],
                "label_full": point["label_full"],
                "hsk_level": point["hsk_level"],
                "unit_number": point["unit_number"],
                "is_new": point["grammar_code"] in new_codes,
                "parts": parts,
                "sections": [_part_to_section(p) for p in parts],
            })

    return session_response


def _without_sentence(part: dict, sentence_id) -> dict:
    """A copy of a (cached, shared) part minus examples that are `sentence_id`,
    and minus each example's `dictionary`."""
    return {**part, "examples": [
        {k: v for k, v in x.items() if k != "dictionary"}
        for x in part["examples"] if x["sentence_id"] != sentence_id
    ]}


def _credit_line(credit: dict) -> str:
    line = credit["label"]
    if credit.get("generated_by"):
        line += f" ({credit['generated_by']})"
    if credit.get("sources"):
        line += ", based on: " + "; ".join(credit["sources"])
    if credit.get("license"):
        line += f". License: {credit['license']}"
    return line


def _part_to_section(part: dict) -> dict:
    """One short tip as a {title, body, table} section: the paragraph, the
    pattern lines, the examples (hanzi - pinyin - English), then the credit."""
    blocks = [part["text"]]
    if part["structure"]:
        blocks.append("\n".join(part["structure"]))
    if part["examples"]:
        blocks.append("\n".join(
            " - ".join(v for v in (x["hanzi"], x["pinyin_display"], x["english"]) if v)
            for x in part["examples"]
        ))
    blocks.append(f"Source: {_credit_line(part['credit'])}")
    return {"title": part["title"], "body": "\n\n".join(b for b in blocks if b), "table": None}
