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
    """Adds sentence credit and grammar to every textbook question.

    On each question with a textbook db_id:
      sentence_source: None, or for sentences that need a label / credit --
          {"source": "ai_generated", "label", "generated_by"}
          {"source": "tatoeba", "label", "author", "url", "license", "license_url"}
      grammar_tips: the points the question practises (QuestionGrammar), new
          ones first -- each {"grammar_code", "label", "label_full",
          "hsk_level", "unit_number", "is_new", "sections"}. `sections` is
          the explanations flattened (credit / AI label included) for the
          existing grammar-tip renderer.

    On the session:
      grammar_points: {grammar_code: full point}, each point sent ONCE
          however many questions link it (a point is ~7KB, a question links
          up to 12). Adds "explanations": [{position, source, title,
          structure, body (JSON blocks), explanation (Markdown), examples,
          generated_by, edited_by, credit, ...}] in position order, plus
          "is_new".

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
        codes = sorted(extra["grammar_codes"], key=lambda c: c not in new_codes)  # stable: new first
        q["grammar_tips"] = [
            {
                "grammar_code": code,
                "label": points[code]["label"],
                "label_full": points[code]["label_full"],
                "hsk_level": points[code]["hsk_level"],
                "unit_number": points[code]["unit_number"],
                "is_new": code in new_codes,
                # Full pages only for points new in this unit; the rest of the
                # points a sentence happens to use get a one-line reminder
                # (full content is in grammar_points either way).
                "sections": points[code]["sections"] if code in new_codes else _summary_sections(points[code]),
            }
            for code in codes
        ]

    session_response.grammar_points = {
        code: _point_payload(p, code in new_codes) for code, p in points.items()
    }
    return session_response


def _summary_sections(point: dict) -> list:
    """One short section for a point that isn't new: its label, its pattern
    (first explanation that has one) and where it's taught."""
    structure = next((e["structure"] for e in point["explanations"] if e["structure"]), None)
    parts = [structure] if structure else []
    if point["unit_number"] is not None:
        parts.append(f"Taught in HSK {point['unit_hsk_level']} unit {point['unit_number']}.")
    return [{"title": point["label"], "body": "\n\n".join(parts), "table": None}]


def _point_payload(point: dict, is_new: bool) -> dict:
    """A copy (the point dicts are cached and shared across requests) without
    content `body` already carries: the Markdown `explanation` and the flat
    `examples` list are dropped when body is present -- together they doubled
    the payload."""
    explanations = []
    for e in point["explanations"]:
        if e["body"] is not None:
            e = {k: v for k, v in e.items() if k not in ("explanation", "examples")}
        explanations.append(e)
    return {
        **{k: v for k, v in point.items() if k != "sections"},
        "explanations": explanations,
        "is_new": is_new,
    }
