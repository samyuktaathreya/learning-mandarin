# Public entry point of the character-practice services, re-exported so
# callers can keep doing `from app.characters.services import
# generate_character_questions` (e.g. session/services/session_builder.py).
from app.characters.services.shared import generate_character_questions

__all__ = ["generate_character_questions"]
