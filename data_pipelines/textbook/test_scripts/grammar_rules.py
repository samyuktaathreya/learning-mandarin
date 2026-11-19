"""
data_pipelines/textbook/test_scripts/grammar_rules.py

TRIAL (used by try_grammar_rule.py and run_all_grammar.py): the AI prompts
for writing and checking grammar rules. The rule language itself (parsing,
matching, the rules file) lives in scripts/grammar_dsl.py, shared with the
pipeline, and is re-exported here so `gr.load_rules`, `gr.render` etc. work
as before.
"""
import json
import re
from pathlib import Path

from data_pipelines.textbook.scripts.grammar_dsl import *  # noqa: F401,F403
from data_pipelines.textbook.scripts.grammar_dsl import TAG_LEGEND, _BLOCK_RE, _SECTION_RE  # noqa: F401

SYNTAX_HELP = """RULE SYNTAX. A ```rule block holds lines `match: <pattern>` and `unless: <pattern>`.
A sentence has the point when at least one match: line matches and NO unless: line matches
(unless: lines are checked against the whole sentence). A pattern is items separated by spaces:
  都          the word 都 (a whole word as tokenized, not a character inside a longer word)
  都/AD       the word 都 tagged AD
  /AD         any word tagged AD
  都|全/AD    either word, tagged AD;   /AD|VV  any word tagged AD or VV
  _           any one word
  ...         any number of words, including none
  ^  $        start / end of a clause
  item?       optional item
  ‖           a clause break (punctuation, shown as ‖ in the tagged sentences below)
Without ‖ a pattern matches inside ONE clause; `...` and `_` never cross a break. Use ‖ to link two
NEIGHBOURING clauses, e.g. a condition clause and a result clause:
  match: /VV|VA ... ‖ /PN? 就/AD /VV      (a clause with a verb, then "(pronoun) 就 + verb")
WRITE GENERAL PATTERNS that describe the grammar, not patterns that fit one example sentence
(e.g. not `^ 喜欢 就 ... 吧 $`). The rule is used on thousands of new sentences, so a pattern that
only reproduces one example is useless there.
Use only these tags: """ + TAG_LEGEND


def _tagged_list(items) -> str:
    return "\n".join(f"  {h}\n      {r}" for h, r in items) or "  (none)"


def writer_prompt(code: str, context: str, positives, negatives, unlabeled) -> str:
    """positives / negatives / unlabeled: [(hanzi, tagged)]"""
    return f"""You are writing a detection rule for one grammar point of the official HSK grammar list,
for a Mandarin course. Code will apply your rule to tokenized, POS-tagged sentences (Penn Chinese
Treebank tags) to find sentences that demonstrate this point.

{context}

PRECISION FIRST: a sentence the rule wrongly matches will be taught as an example of this point,
which is bad. Missing some good sentences is fine. When a use is borderline, exclude it.

{SYNTAX_HELP}

LABELED EXAMPLES OF THIS POINT (written by people), as the course's tagger splits and tags them.
The rule must MATCH these:
{_tagged_list(positives)}

LABELED COUNTER-EXAMPLES (other grammar points that use the same words). The rule must NOT match
these:
{_tagged_list(negatives)}

UNLABELED SENTENCES FROM THE COURSE containing the words, to show how the tagger handles real
sentences (tags can be wrong -- don't rely on a rare tag):
{_tagged_list(unlabeled)}

Write the rule as ONE Markdown section, exactly in this shape, and nothing else:

## {code}
**Point:** <one line: what this point is>
**Covers:** <the uses that count>
**Does not cover:** <the uses that don't, and why>

```rule
match: ...
unless: ...
```

**Notes:** <anything a person editing the rule should know>
"""


def revise_prompt(code: str, context: str, section: str, ex_missed, ex_wrong, chk_wrong_in, chk_missed) -> str:
    def lines(items, why=False):
        if why:
            return "\n".join(f"  {h}\n      {r}\n      checker: {w}" for h, r, w in items) or "  (none)"
        return _tagged_list(items)
    return f"""You wrote this detection rule for HSK grammar point {code}.

{context}

YOUR RULE:
{section}

1. LABELED EXAMPLES (written by people -- these labels are RIGHT) that the rule gets wrong:
   Examples of {code} the rule does NOT match:
{lines(ex_missed)}
   Counter-examples (other points) the rule DOES match -- these matter most:
{lines(ex_wrong)}

2. COURSE SENTENCES where an AI checker disagrees with the rule (the checker CAN BE WRONG; use your
   own judgement and the definition above):
   Rule matched, checker says it is not {code}:
{lines(chk_wrong_in, True)}
   Rule did not match, checker says it is {code}:
{lines(chk_missed, True)}

Fix the rule. PRECISION FIRST: a wrong match is much worse than a miss, so never loosen the rule in
a way that would let other wrong sentences in.

{SYNTAX_HELP}

Reply with the complete revised section in the same shape (## {code}, **Point:**, **Covers:**,
**Does not cover:**, the rule lines inside a ```rule fence, **Notes:**), and nothing else.
"""


CHECK_VERSION = 3


def check_prompt(code: str, context: str, hanzi: str, tagged: str, english: list[str] | None) -> str:
    eng = ""
    if english:
        opts = "\n".join(f"  {i}. {e}" for i, e in enumerate(english))
        eng = (f"\n\nENGLISH TRANSLATIONS:\n{opts}\nAlso pick the translation that is accurate and complete "
               "(nothing added, nothing important left out -- including the meaning of the grammar point). "
               "null if none of them is.")
    eng_field = ', "english": <number or null>' if english else ""
    return f"""You are checking an example sentence for a Mandarin course.

{context}

SENTENCE: {hanzi}
WORDS: {tagged}

Does this sentence demonstrate grammar point {code} as defined above -- the word used the way the
examples of this point use it? Answer false if the word is only used in one of the "NOT this point"
ways, is part of a longer word or fixed expression, or if you are not sure. The sentence must also
be natural, correct Mandarin; answer false if it is not.{eng}

Respond with ONLY a JSON object: {{"uses_point": true or false, "reason": "<short>"{eng_field}}}"""


def check_batch_prompt(code: str, context: str, items: list[dict]) -> str:
    """items: [{"n", "hanzi", "tagged", "english": [..] or None}] -- several
    sentences in one call, so the definition is sent once."""
    blocks = []
    for it in items:
        b = f"[{it['n']}] {it['hanzi']}\n    words: {it['tagged']}"
        if it.get("english"):
            b += "\n    translations: " + " | ".join(f"({i}) {e}" for i, e in enumerate(it["english"]))
        blocks.append(b)
    has_eng = any(it.get("english") for it in items)
    eng_rule = ("\nFor sentences with translations, also give \"english\": the number of the translation that is "
                "accurate and complete (nothing added, nothing important left out -- including the meaning of "
                "the grammar point), or null if none is.") if has_eng else ""
    eng_field = ', "english": <number or null>' if has_eng else ""
    return f"""You are checking example sentences for a Mandarin course.

{context}

SENTENCES:
{chr(10).join(blocks)}

For EACH sentence, judge it on its own: does it demonstrate grammar point {code} as defined above
-- the word used the way the examples of this point use it? Answer false if the word is only used
in one of the "NOT this point" ways, is part of a longer word or fixed expression, or if you are
not sure. The sentence must also be natural, correct Mandarin; answer false if it is not.{eng_rule}

Respond with ONLY a JSON array, one object per sentence, in any order:
[{{"n": <sentence number>, "uses_point": true or false, "reason": "<at most 12 words>"{eng_field}}}, ...]"""


def extract_json_array(text: str) -> list:
    m = re.search(r"\[.*\]", text, re.S)
    if not m:
        raise ValueError("no JSON array in the answer")
    return json.loads(m.group(0))


SOURCES_PROMPT = """You are matching grammar reference pages to one point of the official HSK grammar list.

GRAMMAR POINT {code} (HSK {level}): {label}
{official}
Below are pages from the AllSet Learning Chinese Grammar Wiki that contain the word(s) {markers}
somewhere. Put each page in ONE group:
  "is"        -- the page teaches this grammar point (or one of the uses this point lists).
  "not"       -- the page TEACHES the same word(s) in a different way: the word is part of what
                 the page is about (another meaning of it, a longer word or pattern built on it).
                 Its example sentences are counter-examples for this point.
  "unrelated" -- the page teaches something else, and the word only appears in its examples or
                 its patterns by chance. (For a point about 在, a page about 的时候 is unrelated
                 even if one of its examples says 我在吃饭的时候.)
  "unsure"    -- the page overlaps this point only partly, so its examples are neither clearly
                 this point nor clearly not (e.g. a pattern that contains this point plus more).

PAGES:
{pages}

Respond with ONLY a JSON object mapping every page id to its group, e.g.
{{"ASG9YK09": "is", "ASGT1CIR": "not", "ASGWN185": "unrelated"}}"""


HIDDEN_PROMPT = """A Mandarin course splits some dictionary words into smaller words its learners already
know, because the dictionary word itself is not on the course's word list. For each word below,
decide whether a beginner who knows ONLY the parts (with the meanings given) would understand the
whole word in a sentence without being taught it:
  "clear"  -- its meaning follows from the parts (有人 = 有 "have" + 人 "person" -> "someone";
              看到 = 看 "look" + 到 "arrive" -> "see"; 这个 = 这 "this" + 个 (measure word))
  "hidden" -- it does not (从没 = 从 "from" + 没 "not" -> "never"; 好几 = 好 "good" + 几 "how many"
              -> "several")
The dictionary text lists ALL the word's meanings. Answer "clear" when the meaning built from the
parts is a normal, common reading of the word, even if the word has other meanings too:
  不想 = 不 "not" + 想 "want" -> "don't want" is how 不想 is normally read (its other meaning,
  "unexpectedly", is rare) -> "clear";  下雪 = 下 "fall" + 雪 "snow" -> "it snows" -> "clear".
Answer "hidden" only when the word is normally read with a meaning the parts don't give.
When unsure, answer "hidden".

WORDS:
{words}

Respond with ONLY a JSON object mapping each word to "clear" or "hidden"."""


CONTAINS_VERSION = 2


def contains_prompt(code: str, context: str, items: list[dict]) -> str:
    """Does each sentence CONTAIN the point at all -- even alongside a
    harder construction? Used to clean the labeled test: a counter-example
    that does contain the point isn't a counter-example."""
    listing = "\n".join(f"[{it['n']}] {it['hanzi']}\n    words: {it['tagged']}" for it in items)
    return f"""You are checking example sentences for a Mandarin course.

{context}

SENTENCES:
{listing}

For EACH sentence: does it contain grammar point {code} anywhere in it -- the word used the way
this point uses it -- EVEN IF the sentence also uses another, harder construction? (For a point
about negation with 不, 我一点也不累 DOES contain it: 不 negates 累, even though 一点也不 is a
harder pattern. 不管你去不去 does NOT: there 不 is part of 不管 "no matter".) A sentence can use
the word more than once: if ANY one of its uses is this point, answer true (这家超市不仅种类多，
价格也不贵 contains it -- 不贵 -- even though 不仅 doesn't). Answer false if every use of the word is
part of another word or pattern with a different meaning, or if you are not sure.

Respond with ONLY a JSON array, one object per sentence:
[{{"n": <sentence number>, "contains": true or false}}, ...]"""


def beyond_prompt(lower_def: str, higher_def: str, items: list[dict]) -> str:
    listing = "\n".join(f"[{it['n']}] {it['hanzi']}\n    words: {it['tagged']}" for it in items)
    return f"""Two grammar points from a Mandarin course, an easier one and a harder one:

EASIER POINT (taught first):
{lower_def}

HARDER POINT (taught later):
{higher_def}

The course's lists overlap: the harder point's description can also cover a use the easier point
already teaches (both may list the same word). Each sentence below is an example of the EASIER point.

For EACH sentence: does it ALSO use the HARDER point in a way the easier point does NOT already
cover -- something a learner who only knows the easier point hasn't learned? Answer false if the
harder point's word is only used the way the easier point uses it. If you are unsure, answer true.

SENTENCES:
{listing}

Respond with ONLY a JSON array, one object per sentence:
[{{"n": <sentence number>, "beyond": true or false}}, ...]"""


def extract_json(text: str) -> dict:
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise ValueError("no JSON object in the answer")
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        # the answer had text after the JSON (or a second object): take the first whole object
        return json.JSONDecoder().raw_decode(text[m.start():])[0]


def extract_section(code: str, text: str) -> str:
    """The `## code` section of a model answer (drops any chatter or a
    ```markdown fence around it)."""
    m = re.search(rf"^#+[^\n]*?{re.escape(code)}\b[^\n]*$", text, re.M)   # "## 1-10", "# Rule for 1-10"
    if not m:
        raise RuleError(f"answer has no '## {code}' section")
    lines = [f"## {code}"] + text[m.end():].strip().splitlines()
    fences = [k for k, line in enumerate(lines) if line.strip().startswith("```")]
    if len(fences) % 2 == 1:                   # the whole answer was fenced: drop its closing fence
        del lines[fences[-1]]
    return "\n".join(lines).strip() + "\n"


def generate_prompt(code: str, level: int, context: str, rule_section: str, words: list[str], markers: list[str],
                    avoid_points: list[str], have: list[str], tried: list[str], n: int) -> str:
    """For points Tatoeba can't fill: asks for NEW example sentences. Every
    one is then checked by code and by AI exactly like a Tatoeba sentence,
    and is labelled ai_generated wherever it goes."""
    definition = "\n".join(l for l in rule_section.splitlines()
                           if l.startswith(("**Point:**", "**Covers:**", "**Does not cover:**")))
    known = "HSK 1" if level == 1 else f"HSK 1-{level}"
    return f"""You are writing example sentences for a Mandarin course for beginners at HSK {level}.

{context}

THE COURSE'S DEFINITION OF THIS POINT:
{definition}

Write {n} NEW sentences that each clearly demonstrate grammar point {code} the way it is taught at
HSK {level}, using its word(s) {'、'.join(markers)}.

STRICT RULES -- a sentence that breaks one is thrown away:
1. Use ONLY words from this list of the {known} words learners know (plus the point's own words).
   No other words, no names of people or places:
   {'、'.join(words)}
2. Do not use any of these harder grammar points, even though their words are easy:
{chr(10).join('   ' + a for a in avoid_points) or '   (none)'}
3. Natural, everyday Mandarin that a native speaker would really say -- not textbook-stiff.
   Short: about 5-14 characters, one or two clauses.
4. Vary them: different subjects, verbs and situations. Do not copy or lightly change the
   examples above or these sentences the course already has:
{chr(10).join('   ' + h for h in (have + tried)[:80]) or '   (none)'}
5. Give an accurate, natural English translation for each.

Respond with ONLY a JSON array: [{{"zh": "<sentence>", "en": "<translation>"}}, ...]"""


EXPLANATION_AI_VERSION = 1


def edit_page_prompt(title: str, level: int, numbered: str) -> str:
    """A wiki page's text blocks, numbered: the AI may only edit or remove
    prose (headings and paragraphs), never examples or structures."""
    return f"""This is a page of the AllSet Learning Chinese Grammar Wiki, shown inside a Mandarin learning app to
beginners (HSK {level}) as the explanation of a grammar point: "{title}". It was taken from the wiki
automatically, so some prose may not make sense in the app. Below are its blocks in order; the
example sentences and structure boxes are shown for context and CANNOT be changed.

{numbered}

Only fix prose that does not make sense here, e.g.:
- it points to something the app does not have: a link ("learn about ..."), another article,
  a video, a quiz, "this page";
- it refers to examples or a section that is not there, or is a broken fragment.
Keep everything else word for word: do not rewrite good prose, do not add new explanations,
do not change any Chinese. Remove a block if it has no use without the missing thing.

Respond with ONLY a JSON object: {{"edits": [{{"n": <block number>, "text": "<new text>"}} or
{{"n": <block number>, "remove": true}}, ...]}} -- an empty list if the page reads fine."""


def tip_prompt(code: str, level: int, label: str, official: list[str], definition: str,
               sentences: list[tuple[str, str]]) -> str:
    """For a grammar point no wiki page teaches: the AI writes a short
    explanation. It picks its examples from REAL course sentences."""
    listing = "\n".join(f"  [{i}] {h}  ({e})" for i, (h, e) in enumerate(sentences)) or "  (none)"
    return f"""Write a short grammar explanation for beginners learning Mandarin, shown in a learning app the first
time they meet this grammar point in a sentence. It will be labelled as written by AI.

GRAMMAR POINT {code} (HSK {level}) from the official HSK grammar list: {label}
Official examples: {' / '.join(official) or '(none)'}
{('What the course takes it to mean: ' + definition) if definition else ''}

Sentences from the course that use it (pick the examples from these, by number):
{listing}

Write for an HSK {level} learner, in plain English: what the point is, when to use it, and how the
sentence is built. Short: 2-4 short paragraphs, under 120 words in all. Accurate -- no claims you
are unsure of. Use Chinese only for the point's own words and short patterns.

Respond with ONLY a JSON object:
{{"title": "<a short English title, e.g. Expressing location with 在>",
  "structure": ["<pattern line, e.g. Subj. + 在 + Place + Verb>", ...],
  "paragraphs": ["...", ...],
  "examples": [<up to 5 sentence numbers>]}}"""


# --------------------------------- EXAMPLE PINYIN ---------------------------------

PINYIN_VERSION = 2        # 2: tones as spoken (v1's "citation tones" made 了 liao3); Sonnet settles disagreements

_PINYIN_RULES = """- Tone numbers after each syllable: 1-4, and 5 for the neutral tone.
- Write each syllable's tone AS SPOKEN in this sentence, with ONE exception: do not apply the
  tone-change rules (they are added afterwards in code). So:
    不 is always bu4 and 一 is always yi1 (even in 不是, 一个, 一五一十);
    third tones stay 3 even before another third tone (你好 ni3hao3, 我有 wo3 you3);
    but neutral tones ARE written as neutral: 了 le5 (only liao3 in words like 了解, 不得了),
    的 de5, 吗 ma5, 们 men5, 子 zi5 in 桌子, 地 de5 after an adverb (大声地), 得 de5 after a verb.
- One syllable per character. A 儿 read as erhua is "r5" (哪儿 -> na3r5). Use v for ü (lv4).
- Pick the reading THIS sentence uses for characters with several: 长 chang2/zhang3,
  得 de5/de2/dei3, 行 xing2/hang2, 还 hai2/huan2, 都 dou1/du1, 地 de5/di4, 着 zhe5/zhao2,
  觉 jue2/jiao4, 重 zhong4/chong2, 好 hao3/hao4, 为 wei4/wei2, 便 bian4/pian2, 教 jiao1/jiao4,
  调 diao4/tiao2 (强调 qiang2diao4).
- Names: the tones the name really has (丽丽 li4li4).
- Lower case, no spaces inside a word, no punctuation."""


def _pinyin_lines(entries: list[dict]) -> str:
    lines = []
    for e in entries:
        line = f'{e["i"]}. {e["hanzi"]}'
        if e.get("english"):
            line += f'  ({e["english"]})'
        line += "\n   words: " + " | ".join(e["words"])
        for name, draft in (e.get("drafts") or {}).items():
            if draft:
                line += f"\n   draft {name}: " + " | ".join(draft)
        if e.get("problem"):
            line += f"\n   (your last answer was wrong: {e['problem']})"
        lines.append(line)
    return "\n".join(lines)


_PINYIN_ANSWER = """Respond with ONLY a JSON object, one pinyin string for EACH word listed, in the same order:
{"answers": [{"i": <number>, "pinyin": ["<word 1 pinyin>", "<word 2 pinyin>", ...]}, ...]}"""


def pinyin_prompt(entries: list[dict]) -> str:
    """entries: [{"i", "hanzi", "english", "words": [...], "problem"?}].
    Answer: {"answers": [{"i", "pinyin": [one string per word]}]}."""
    return f"""Write the Mandarin pinyin of these example sentences, for a Chinese course.

{_PINYIN_RULES}

{_pinyin_lines(entries)}

{_PINYIN_ANSWER}"""


def pinyin_judge_prompt(entries: list[dict]) -> str:
    """Like pinyin_prompt, but each entry has two drafts that disagree
    ("drafts": {"A": [...], "B": [...]}) -- write the correct one."""
    return f"""These are example sentences for a Chinese course. For each, two drafts of its pinyin
disagree (draft A from one AI, draft B from a dictionary program -- either can be wrong, and a draft
may be missing). Write the CORRECT pinyin, following these rules exactly:

{_PINYIN_RULES}

{_pinyin_lines(entries)}

{_PINYIN_ANSWER}"""


# --------------------------------- SHORT TIPS ---------------------------------

TIP_PARTS_VERSION = 2        # 2: tips for the uses the course sentences have; meaning starts "X means"

TIP_TERMS_BANNED = ("complement", "particle", "adverb", "aspect", "predicate", "preposition", "conjunction",
                    "clause", "auxiliary", "modal", "classifier", "connotation", "morpheme", "reduplicat",
                    "interrogative", "copula", "perfective", "resultative", "nominal", "pronoun", "syntax",
                    "grammatical", "construction")


def tip_parts_prompt(code: str, level: int, label: str, official: list[str], words: list[str],
                     pages: list[str], sentences: list[tuple[str, str]], needed: list[str] | None = None,
                     plain: list[str] | None = None, problems: list[str] | None = None) -> str:
    """The SHORT tips learners see. The rules here are checked again in code
    (run_all_grammar.check_tip_parts); `problems` = what the last answer broke."""
    page_text = "\n\n".join(f"--- wiki page {i + 1} ---\n{p}" for i, p in enumerate(pages)) or "(no wiki page)"
    sent = "\n".join(f"  {h}  ({e})" for h, e in sentences) or "  (none yet)"
    redo = ("\n\nYOUR LAST ANSWER BROKE THESE RULES -- fix them:\n" + "\n".join(f"- {p}" for p in problems)
            if problems else "")
    return f"""You write the grammar tips for a Mandarin learning app. A learner sees a tip when they meet a
sentence that uses the grammar. Grammar should feel easy -- children pick it up without explanations --
so a tip is tiny: it tells them what the thing means and roughly where it goes, nothing more.

GRAMMAR POINT {code}, for HSK {level} learners. Official HSK list: {label}
Official examples: {' / '.join(official) or '(none)'}
Words the course uses to find this point in sentences: {' '.join(words) or '(none: a sentence pattern)'}
Words of this point the course sentences ACTUALLY use -- EVERY one needs a tip (or must be in a tip's
"words"): {' '.join(needed) if needed else '(none: a sentence pattern)'}
{('Many course sentences use none of these words (e.g. ' + ' / '.join(plain) + '): give ONE general tip with '
  '"words": [] that explains the point for them.') if plain else ''}

Make ONE tip per word or pattern this point teaches (a point listing 有点、挺、更 gets three tips; a point
with one word gets one). Write tips ONLY for the uses the course sentences below actually have -- those
are what learners will see. If the official list or the wiki has a use that no course sentence has, leave
it out. (If there are no course sentences, follow the official list.) Only what an HSK {level} learner
needs: the most common use. Leave other uses for later levels.

Each tip has:
- "words": the Chinese words that show a sentence uses THIS tip, in every spelling (有点, 有一点, 有点儿,
  有一点儿). Use [] only for a sentence pattern with no special word -- at most one such tip per point.
- "title": the word or pattern itself, short, e.g. "有(一)点(儿)" or "Verb + 完".
- "meaning": ONE short sentence saying what it means or does, in very simple English. It STARTS with the
  word itself: "已经 means ...", "有点 means ...". A pattern with no special word starts with the pattern:
  "Verb + 来 means ...". This comes first.
- "placement": ONE short sentence suggesting where it goes in a sentence -- a suggestion, not a rule with
  exceptions. null if the structure line already says it.
- "english_note": at most ONE short sentence on how this differs from English (word order, a word English
  doesn't need). null when a learner wouldn't trip over anything.
- "structure": 1 or 2 pattern lines, e.g. "Subj. + 有点 + Adj." (slots: Subj., Verb, Adj., Noun, Obj.,
  Place, Time, Number, Measure word).

Rules for all of it:
- meaning + placement + english_note together: at most 45 words. One short paragraph.
- No grammar terms except verb, noun, adjective, question, subject, object, measure word. Never words like
  complement, particle, adverb, aspect, predicate, preposition, conjunction, clause.
- No exceptions, no "note that", no regional differences, no comparing with other grammar points.
- No example sentences (they are shown separately) and no pinyin.
- Chinese only for the tip's own words and words an HSK {level} learner knows.
- Accurate: say only what the sources below support.

SOURCES
{page_text}

Course sentences that use this point:
{sent}{redo}

Respond with ONLY a JSON object:
{{"tips": [{{"words": [...], "title": "...", "meaning": "...", "placement": "..." or null,
            "english_note": "..." or null, "structure": ["..."]}}, ...]}}"""
