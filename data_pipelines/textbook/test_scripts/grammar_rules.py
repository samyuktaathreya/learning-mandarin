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
    return json.loads(m.group(0))


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
