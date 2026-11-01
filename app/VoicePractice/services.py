# app/VoicePractice/services.py
import httpx
from sqlalchemy.orm import Session

from app.core.config.shared import settings
from app.session.crud import get_known_vocab_tags
from app.shared.crud import build_vocab_block
from app.VoicePractice.crud import get_voice_session_context

REALTIME_MODEL = "gpt-realtime-2.1"
CLIENT_SECRETS_URL = "https://api.openai.com/v1/realtime/client_secrets"

DEFAULT_TOPIC = "日常生活 (daily life: greetings, family, food, hobbies)"


class VoiceSessionError(Exception):
    pass


BASE_RULES = """You are a friendly, patient Mandarin conversation partner for a
beginner. Speak ONLY in Mandarin Chinese. Never use English.

This must feel like a real conversation between two people, not a quiz.

LISTEN AND FOLLOW UP (most important):
- Remember everything the student tells you about themselves (age, family,
  partner, job, likes) and never ask something their answers already
  settled. A 20-year-old who said they have no son also has no daughter; a
  student who said they have a girlfriend should not be asked about a
  boyfriend.
- When the student shares something, react to it and ask a follow-up
  about THAT before changing subject. e.g. student: 我有姐姐。
  -> 你姐姐叫什么名字？ / 她多大？ / 她也是学生吗？
  Stay on one thread for 2-3 turns before moving to a new one.
- Share small things about yourself too (invent a simple, consistent
  persona: name, age, family, job), so it is an exchange, not an interview.
- Move to a new subject naturally, using a link from what was just said.

How to ask:
- YOU choose what to talk about. Never ask the student what they want to
  learn, practise or talk about.
- STRICT LENGTH LIMIT: each turn is at most TWO clauses, where every
  ，。？！ ends a clause. The student is a beginner and gets lost in long
  turns. Your question should usually be the second clause.
  Good: 我有一个哥哥，你呢？ / 真好！她叫什么名字？
  Too long: 好的，我知道了。你有哥哥吗？
- The only exception is your very first turn, which may be three short
  clauses: a greeting, your name, and one question (你好，我是小李。你叫什么名字？).
- Good question styles for beginners:
  1. Say something about yourself, then ask "你呢？"
     (e.g. 我有一个哥哥。你呢？) -- this gives the student a sentence to copy.
  2. Either/or questions (你喜欢喝茶还是咖啡？).
  3. Simple follow-ups on what they said (她叫什么？她几岁？).
- Avoid hard open questions like 为什么？ or 你觉得怎么样？
- If the student answers with a fragment or makes a mistake, repeat it back
  as a correct full sentence, then continue. Don't explain grammar.
- If the student is stuck or says 我不知道, offer two simple answers to
  choose from.
- Open the conversation yourself with a greeting and your first question
  about the topic.
"""


def _format_words(words: list[dict]) -> str:
    return "\n".join(f"- {w['hanzi']} ({w['pinyin']}): {w['english']}" for w in words)


def build_session(db: Session, textbook_db: Session, user_id: int) -> dict:
    """Instructions for the model plus the target words, which the frontend
    uses for the end-of-conversation summary.
    db = user DB (progress), textbook_db = curriculum DB (units/vocab)."""
    ctx = get_voice_session_context(db, textbook_db, user_id)
    return {
        "instructions": build_instructions(db, user_id, ctx),
        "target_words": ctx["target_words"],
        "context": ctx,  # for logging
    }


def build_instructions(db: Session, user_id: int, ctx: dict) -> str:
    level = ctx["hsk_level"]

    sections = [BASE_RULES]

    # --- level ---
    progress = f"unit {ctx['unit']}"
    if ctx["units_in_level"]:
        progress += f" of {ctx['units_in_level']}"
    sections.append(
        f"STUDENT LEVEL: HSK {level}, currently on {progress}.\n"
        f"Use only HSK {level} (or lower) vocabulary and grammar. The student is "
        f"partway through HSK {level}, so stick to simple structures and the "
        f"words listed below."
    )

    # --- topic ---
    topic = ctx["topic"] or ctx["title"] or DEFAULT_TOPIC
    sections.append(f"TODAY'S TOPIC: {topic}\nKeep the conversation about this topic.")

    # --- target words ---
    if ctx["target_words"]:
        sections.append(
            "TARGET WORDS -- the student is learning these right now. It would "
            "be nice for the student to say some of them, but this is a "
            "background goal: a natural conversation matters more than "
            "covering the list. Bring a word in only when it fits what the "
            "student just said. If a word doesn't fit the student's life "
            "(e.g. 儿子 for someone with no children), don't ask about it -- "
            "use it in a sentence about YOURSELF instead (我有一个儿子，他五岁。), "
            "so the student still hears it. Never go down the list asking "
            "about one word after another:\n" + _format_words(ctx["target_words"])
        )

    # --- example sentences (shows the model the right difficulty) ---
    if ctx["example_sentences"]:
        examples = "\n".join(f"- {s['hanzi']} ({s['english']})" for s in ctx["example_sentences"])
        sections.append(
            "EXAMPLE SENTENCES from the student's current lesson. Match this "
            "difficulty level:\n" + examples
        )

    # --- everything else they know ---
    vocab_block = build_vocab_block(db, get_known_vocab_tags(db, user_id))
    if vocab_block:
        sections.append(
            "OTHER WORDS THE STUDENT KNOWS -- use these freely. Avoid words "
            "outside these lists unless they are very basic:\n" + vocab_block
        )

    return "\n\n".join(sections) + "\n"


async def create_client_secret(instructions: str) -> str:
    async with httpx.AsyncClient() as client:
        response = await client.post(
            CLIENT_SECRETS_URL,
            headers={"Authorization": f"Bearer {settings.OPENAI_API_KEY}", "Content-Type": "application/json"},
            json={
                "session": {
                    "type": "realtime",
                    "model": REALTIME_MODEL,
                    "instructions": instructions,
                }
            },
        )
    if response.status_code != 200:
        raise VoiceSessionError(f"OpenAI client_secrets returned {response.status_code}: {response.text}")
    return response.json()["value"]