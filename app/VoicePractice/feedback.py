# app/VoicePractice/feedback.py
"""
End-of-conversation feedback: sends the finished transcript to Claude and
gets back the student's real mistakes, with likely speech-recognition errors
separated out.

Why a separate model: the student's lines come from Whisper, which often
mis-hears learner speech (wrong homophone, dropped syllable, tone confusion).
Claude sees the whole conversation, including how the tutor responded, so it
can tell "the student said it wrong" apart from "the transcript is wrong".

Needs: `pip install anthropic` and CLAUDE_API_KEY in settings (same key the
grammar-tip pipeline uses).
"""
import json
import re

from anthropic import AsyncAnthropic, APIError

from app.core.config.shared import settings

FEEDBACK_MODEL = "claude-sonnet-5-5"
MAX_MISTAKES = 5
# Guards against huge request bodies; a 12-20 turn chat is far below these.
MAX_LINES = 80
MAX_LINE_CHARS = 300
MAX_ATTEMPTS = 2


class FeedbackError(Exception):
    pass


_client = None


def _get_client() -> AsyncAnthropic:
    global _client
    if _client is None:
        _client = AsyncAnthropic(api_key=settings.CLAUDE_API_KEY)
    return _client


# Claude is told (in the system prompt) to answer by calling this tool, which
# gives us JSON in this exact shape. We can't FORCE the call with
# tool_choice={"type": "tool"} -- this model rejects that -- so if Claude
# replies in plain text instead, _extract_result falls back to parsing JSON.
FEEDBACK_TOOL = {
    "name": "report_feedback",
    "description": "Report feedback on the student's Mandarin in this conversation.",
    "input_schema": {
        "type": "object",
        "properties": {
            "mistakes": {
                "type": "array",
                "description": f"The student's genuine mistakes, most important first. At most {MAX_MISTAKES}.",
                "items": {
                    "type": "object",
                    "properties": {
                        "you_said": {"type": "string", "description": "The student's line (or the relevant part), as transcribed."},
                        "correction": {"type": "string", "description": "A natural, correct version in simplified Chinese."},
                        "correction_pinyin": {"type": "string", "description": "Pinyin with tone marks for the correction."},
                        "explanation": {"type": "string", "description": "One or two short, friendly sentences in English explaining the rule."},
                        "category": {"type": "string", "enum": ["grammar", "word_choice", "word_order", "missing_word", "measure_word", "other"]},
                    },
                    "required": ["you_said", "correction", "correction_pinyin", "explanation", "category"],
                },
            },
            "possible_mishearings": {
                "type": "array",
                "description": "Student lines that look wrong but are most likely speech-recognition errors, not the student's mistake.",
                "items": {
                    "type": "object",
                    "properties": {
                        "transcribed": {"type": "string"},
                        "probably_said": {"type": "string", "description": "What the student most likely said, in simplified Chinese."},
                        "pronunciation_tip": {"type": "string", "description": "Optional short English tip if unclear pronunciation may have caused it, else empty string."},
                    },
                    "required": ["transcribed", "probably_said", "pronunciation_tip"],
                },
            },
            "praise": {"type": "string", "description": "One short, specific English sentence about something the student did well."},
        },
        "required": ["mistakes", "possible_mishearings", "praise"],
    },
}


def _system_prompt(hsk_level: int) -> str:
    return f"""You are an experienced Mandarin teacher reviewing a spoken practice
conversation between an AI tutor and a student at HSK {hsk_level} level.

IMPORTANT: the student's lines were produced by automatic speech recognition
from a learner's voice, so some errors are the transcriber's, not the
student's. Before calling something a mistake, ask: could a correct sentence
have been mis-transcribed this way (homophones or near-homophones, a similar
syllable with a different tone, a dropped or merged syllable, a misplaced
word boundary)? Use context: what the tutor replied often shows what the
student meant. If a transcription error is the most likely explanation, put
it under possible_mishearings, NOT under mistakes. Only report a mistake when
the student most plausibly said it that way.

What to report as mistakes:
- Grammar, word order, missing or wrong particles (了, 的, 吗, 呢...), measure
  words, and clearly wrong word choices.
- Prioritise the mistakes most useful for an HSK {hsk_level} learner; at most
  {MAX_MISTAKES}. Skip trivial ones and anything above their level.
- Don't flag one-word or fragment answers that are natural in spoken
  Chinese (e.g. "有。" or "二十岁。" as an answer).
- Punctuation and missing tone marks are not mistakes; the punctuation comes
  from the transcriber.
- Never review the tutor's lines.

Write explanations in simple English a beginner understands. Keep corrections
close to what the student was trying to say, using HSK {hsk_level} vocabulary.
If the student made no real mistakes, return an empty mistakes list.
Always report your answer by calling the report_feedback tool, and do not
write any other text."""


def _format_transcript(transcript: list[dict]) -> str:
    lines = []
    for line in transcript[-MAX_LINES:]:
        speaker = "Student" if line["role"] == "user" else "Tutor"
        lines.append(f"{speaker}: {line['text'][:MAX_LINE_CHARS]}")
    return "\n".join(lines)


def _extract_result(response) -> dict | None:
    """The tool call's input if Claude used the tool, else a JSON object found
    in its text reply. None if neither is usable."""
    for block in response.content:
        if block.type == "tool_use" and block.name == FEEDBACK_TOOL["name"]:
            return block.input

    text = "".join(block.text for block in response.content if block.type == "text")
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        return None
    try:
        parsed = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) and "mistakes" in parsed else None


async def analyze_transcript(transcript: list[dict], hsk_level: int) -> dict:
    """transcript: [{"role": "user"|"assistant", "text": "..."}] in order.
    Returns {"mistakes": [...], "possible_mishearings": [...], "praise": "..."}."""
    messages = [{
        "role": "user",
        "content": "Here is the conversation:\n\n" + _format_transcript(transcript),
    }]

    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            response = await _get_client().messages.create(
                model=FEEDBACK_MODEL,
                max_tokens=2000,
                system=_system_prompt(hsk_level),
                tools=[FEEDBACK_TOOL],
                tool_choice={"type": "auto"},
                messages=messages,
            )
        except APIError as e:
            raise FeedbackError(f"Claude API error: {e}") from e

        result = _extract_result(response)
        if result is not None:
            result["mistakes"] = result.get("mistakes", [])[:MAX_MISTAKES]
            result.setdefault("possible_mishearings", [])
            result.setdefault("praise", "")
            return result

    raise FeedbackError(f"Claude did not return usable feedback after {MAX_ATTEMPTS} attempts")