from pydantic import BaseModel
from typing import List, Dict, Optional


class QuestionBase(BaseModel):
    id: str
    question_type: str
    question: str
    answer: str
    tags: List[str]
    grammar_tip: Optional[str] = None


class SessionSubmission(BaseModel):
    list_of_question_data: List[QuestionBase]
    is_correct: List[bool]
    is_unit_test: bool


class SessionResponse(BaseModel):
    user_id: int
    session_type: str
    question_set: List[Dict]
    # grammar_code -> full grammar point (explanations, credit, is_new), sent
    # once per session; questions refer to them via grammar_tips[].grammar_code.
    # See session/services/sources.py.
    grammar_points: Dict[str, Dict] = {}