from pydantic import BaseModel
from typing import List, Dict


class QuestionBase(BaseModel):
    id: str
    question_type: str
    question: str
    answer: str
    tags: List[str]


class SessionSubmission(BaseModel):
    list_of_question_data: List[QuestionBase]
    is_correct: List[bool]
    is_unit_test: bool


class SessionResponse(BaseModel):
    user_id: int
    session_type: str
    question_set: List[Dict]