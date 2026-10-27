"""
data_pipelines/textbook/scripts/detect_grammar.py

Pipeline stage 4 (replaces extract_and_match_grammar.py). Links every tagged
sentence to the official grammar points it demonstrates (SentenceGrammar
rows). Rule-based: no AI, no textbook OCR.

The grammar points themselves were loaded by load_hsk_lists.py
(GrammarTip rows keyed on grammar_code, e.g. "1-16"). This stage only
writes the links; assign_units.py later homes each point in the unit where
it first appears.

WORDS + POS TAGS
  Grammar needs each word's part-of-speech tag IN THIS SENTENCE (在 as a
  preposition "P" vs in-progress adverb "AD"). Tagging stores one per word
  occurrence (SentenceVocab.pos_tag), so this stage reads the words and tags
  straight from the DB -- the exact split tagging made, AI splits and joins
  included -- and puts the numbers back in from the text (they aren't
  vocabulary). No HanLP run, no AI.

LEVELS
  A sentence's level is first recomputed from its words
  (db_utils.set_sentence_level), so re-running this stage gives the same
  result. Then it is only linked to points AT OR BELOW its level: an HSK 3
  sentence is checked against HSK 1, 2 and 3.

HSK 1 GRAMMAR RESTRICTION
  An HSK 1 sentence (by its words) is also checked against the higher
  points, with the hand-written RULES only -- the list's regex patterns
  have too many false positives. If any fires, the sentence moves up to
  that point's level (the highest one that fired), so 我来介绍一下... style
  grammar (verb + 一下, 2-52) can't sit in HSK 1. Only HSK 1 is restricted.
  The grammar list is the 2021 standard while the vocab is the 2025
  syllabus, so a few points an HSK 1 learner does meet are allowed through:
  HSK1_ALLOWED_ABOVE. Every bump is written to the report.

HOW EACH POINT IS DETECTED (first that applies)
  1. A hand-written rule on words + POS tags (RULES below) -- precise, and
     tells word senses apart (在 P vs 在 AD, 还是 "or" vs "still").
  2. The grammar list's own `pattern` regex on the sentence text, with its
     `exclude` words removed first -- covers most HSK 2 points.
  3. Neither -> not detectable yet. Listed in the summary so you can see
     the gaps (most HSK 3-6 points have no pattern in the list).
  Some points are deliberately never linked (SKIP): they describe every
  sentence (1-24 "noun as subject") and would only drown out real points.

Re-running replaces the links of the sentences it processes, so improved
rules take effect on the next run.

USAGE (from the repo root)
    python -m data_pipelines.textbook.scripts.detect_grammar
    python -m data_pipelines.textbook.scripts.detect_grammar --limit 200
    python -m data_pipelines.textbook.scripts.detect_grammar --report-only   # counts, writes nothing
"""
import argparse
import csv
import json
import os
import re
import time
from collections import Counter, defaultdict
from datetime import datetime

from app.textbook.db_utils import (
    get_session, init_db, link_sentence_grammar, set_sentence_level,
)
from app.textbook.models import GrammarTip, Sentence, SentenceGrammar, SentenceVocab

from data_pipelines.textbook.scripts import hsa_source

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPORT_DIR = os.path.abspath(os.path.join(SCRIPT_DIR, "..", "debug", "detect_grammar"))

COMMIT_EVERY = 200


# --------------------------------- SENTENCE WORDS ---------------------------------

class Context:
    """Everything a rule can look at for one sentence."""
    def __init__(self, text: str, tagged: list[tuple[str, str]], sentence_type: str | None):
        self.text = text                      # 我在家吃饭。
        self.tagged = tagged                  # [("我","PN"), ("在","P"), ...]
        self.words = [w for w, _ in tagged]
        self.type = sentence_type             # dataset: statement / question / imperative
        self.clause = self._clauses(text, self.words)   # clause number of each word
        self.is_question = text.rstrip().endswith(("？", "?"))
        self.is_exclamation = text.rstrip().endswith(("！", "!"))


    @staticmethod
    def _clauses(text: str, words: list[str]) -> list[int]:
        """Clause number of each word, counting punctuation in the original
        text (the word list has none): 书在床上，不在地上 -> 0 0 0 0 1 1 1 1."""
        out, pos, clause = [], 0, 0
        for w in words:
            while pos < len(text) and text[pos] not in w[:1]:
                if not text[pos].isspace() and not ("\u4e00" <= text[pos] <= "\u9fff") and not text[pos].isalnum():
                    clause += 1
                pos += 1
            out.append(clause)
            pos += len(w)
        return out

    def same_clause_after(self, i: int, n: int) -> list[tuple[str, str]]:
        """Up to n words after word i, stopping at the end of its clause."""
        return [tw for k, tw in enumerate(self.tagged[i + 1:i + 1 + n], start=i + 1)
                if self.clause[k] == self.clause[i]]


_NUM_CHARS = set("零〇一二两三四五六七八九十百千万亿几半第0123456789０１２３４５６７８９.．")


def sentence_words(sentence: Sentence) -> list[tuple[str, str]]:
    """The sentence's words + POS tags exactly as tagging stored them
    (SentenceVocab rows in order, with their pos_tag), so grammar sees the
    same split as everything else -- including AI splits and joins -- with
    no HanLP run and no AI. Numbers aren't stored as vocabulary, so the
    text between stored words is put back: numerals as CD, anything else
    as one NN chunk."""
    rows = sorted(sentence.vocab_links, key=lambda r: r.position)
    text = sentence.hanzi
    out, pos = [], 0

    def gap(chunk: str):
        han = "".join(ch for ch in chunk if "\u4e00" <= ch <= "\u9fff" or ch.isdigit())
        if han:
            out.append((han, "CD" if all(ch in _NUM_CHARS for ch in han) else "NN"))

    for r in rows:
        word = r.vocab.hanzi
        idx = text.find(word, pos)
        if idx == -1:
            out.append((word, r.pos_tag or "NN"))
            continue
        gap(text[pos:idx])
        out.append((word, r.pos_tag or "NN"))
        pos = idx + len(word)
    gap(text[pos:])
    return out


# --------------------------------- RULE HELPERS ---------------------------------
# CTB tags: P preposition, AD adverb, VV verb, VA predicative adjective,
# VC 是, VE 有, AS aspect marker (了 过 着), SP sentence-final particle,
# M measure word, CD number, OD ordinal, NT time noun, LC location word,
# DEG/DEC 的, DEV 地, DER 得, CC conjunction, DT determiner, PN pronoun,
# NR proper noun.

VERBISH = {"VV", "VA", "VC", "VE"}


def has(c: Context, words, tags=None) -> bool:
    return any(w in words and (tags is None or t in tags) for w, t in c.tagged)


def has_tag(c: Context, tags) -> bool:
    return any(t in tags for _, t in c.tagged)


def followed_by_verb(c: Context, words, skip=frozenset({"AD"})) -> bool:
    """One of `words` followed by a verb, allowing adverbs in between
    (我不会说 / 我会说)."""
    for i, (w, _) in enumerate(c.tagged):
        if w in words:
            for _, t in c.tagged[i + 1:i + 4]:
                if t in VERBISH:
                    return True
                if t not in skip:
                    break
    return False


def number_then(c: Context, nexts) -> bool:
    """A number (CD) directly followed by one of `nexts` (words or tags)."""
    for (w1, t1), (w2, t2) in zip(c.tagged, c.tagged[1:]):
        if t1 == "CD" and (w2 in nexts or t2 in nexts):
            return True
    return False


def a_not_a(c: Context) -> bool:
    """好不好 / 是不是 / 有没有 / 去不去 -- a word, 不/没, the same word."""
    w = c.words
    return any(w[i] == w[i + 2] and w[i + 1] in ("不", "没") for i in range(len(w) - 2)) \
        or bool(re.search(r"(.)(不|没)\1", c.text))


def zai_place_then_verb(c: Context) -> bool:
    """在 as "at/in" (1-16): 在 + place + verb (经理在办公室等你). Without a verb
    after the place, 在 is the verb "to be at" (车站在楼下), not this point.
    Judged by word order, not 在's own tag, which taggers often get wrong."""
    for i, (w, _) in enumerate(c.tagged):
        if w != "在" or i + 1 >= len(c.tagged):
            continue
        if c.tagged[i + 1][1] in VERBISH or c.tagged[i + 1][0] == "一起":
            continue                                   # 在 + verb = in progress; 在一起 = together
        if any(t in VERBISH and w != "在" for w, t in c.same_clause_after(i, 6)[1:]):
            return True
    return False


def zai_progressive(c: Context) -> bool:
    """In progress (1-42): 正在 / 正 / 在 directly before a verb, optionally
    with one adverb between (他在认真学习). 在一起 "together" excluded."""
    for i, (w, _) in enumerate(c.tagged):
        if w not in ("在", "正在", "正"):
            continue
        nxt = c.tagged[i + 1:i + 3]
        if not nxt or nxt[0][0] == "一起":
            continue
        if nxt[0][1] in VERBISH - {"VC", "VE"}:
            return True
        if len(nxt) == 2 and nxt[0][1] == "AD" and nxt[1][1] in VERBISH - {"VC", "VE"}:
            return True
    return False


QUESTION_WORDS = {"什么", "谁", "哪", "哪儿", "哪里", "哪些", "几", "多少", "怎么", "多"}
PRONOUNS = {"我", "你", "您", "他", "她", "我们", "你们", "他们", "她们"}
TIME_UNITS = {"点", "分", "号", "日", "月", "年", "星期", "岁"}
MONEY_UNITS = {"块", "元", "毛", "角"}



# --------------------------------- HSK 2 HELPERS ---------------------------------

PUNCT_END = "。！？!?；;"
CATENATIVE = {"想", "要", "会", "能", "可以", "喜欢", "爱", "开始", "觉得", "希望", "打算", "准备", "应该",
              "说", "听说", "看见", "听见", "发现", "告诉", "问", "等", "以为", "相信", "同意", "怕", "担心",
              "该", "愿意", "可能", "让", "叫", "请", "需要", "必须", "得", "敢", "知道", "认为", "觉", "记得",
              "忘", "忘记", "决定", "继续", "帮", "帮助", "教", "是", "有"}
PIVOT_VERBS = {"在", "不如", "要求", "通知", "劝", "派", "使", "让", "叫", "请", "求", "命令", "鼓励", "帮"}
DIRS = {"来", "去", "上", "下", "进", "出", "回", "过", "起", "出来", "出去", "过来", "过去", "回来", "回去",
        "进来", "进去", "起来", "上来", "上去", "下来", "下去"}
DIR_CHARS = set("来去上下进出回过起开")
DONGLIANG = {"次", "遍", "下", "回", "趟", "顿", "场", "下儿", "一下", "一下儿"}
ADJ_CHARS = set("高大小长短慢快好早晚红白黑绿蓝胖瘦亮热冷远近深浅轻重干净甜香美圆满细粗新老")
NUMERALS = set("一二两三四五六七八九十百千万几半")
PEOPLE_TAGS = {"PN", "NR"}


def clauses(c: Context) -> list[list[tuple[str, str]]]:
    """The tagged words grouped by clause (split at any punctuation)."""
    out = {}
    for (w, t), k in zip(c.tagged, c.clause):
        out.setdefault(k, []).append((w, t))
    return [out[k] for k in sorted(out)]


def verb_in(words) -> bool:
    return any(t in VERBISH for _, t in words)


def next_words(c: Context, i: int, n: int = 3) -> list[tuple[str, str]]:
    return c.same_clause_after(i, n)


def word_after(c: Context, i: int):
    nxt = c.same_clause_after(i, 1)
    return nxt[0] if nxt else (None, None)


def verb_reduplication(c: Context) -> bool:
    """2-04 看看 / 想一想 / 试了试 / 休息休息, as one token or two."""
    for i, (w, t) in enumerate(c.tagged):
        if w in ("谢谢",):
            continue
        if t == "VV":
            if len(w) == 2 and w[0] == w[1]:
                return True
            if len(w) == 3 and w[0] == w[2] and w[1] in "一了":
                return True
            if len(w) == 4 and w[:2] == w[2:]:
                return True
            nxt = c.tagged[i + 1:i + 3]
            if nxt and nxt[0][0] == w and len(w) <= 2:
                return True
            if len(nxt) == 2 and nxt[0][0] in ("一", "了") and nxt[1][0] == w and len(w) == 1:
                return True
    return False


def adj_reduplication(c: Context) -> bool:
    """2-08 高高的 / 慢慢地 / 干干净净."""
    for w, _ in c.tagged:
        if len(w) == 4 and w[0] == w[1] and w[2] == w[3] and w[0] != w[2]:
            return True
        if len(w) >= 2 and w[0] == w[1] and w[0] in ADJ_CHARS and (len(w) == 2 or w[2:] in ("的", "地", "儿", "儿的", "儿地")):
            return True
    return bool(re.search(r"([" + "".join(ADJ_CHARS) + r"])\1(的|地|儿)", c.text))


def state_complement(c: Context) -> bool:
    """2-51 动词+得+形容词: 跑得很快 / 玩儿得很高兴."""
    for i, (w, t) in enumerate(c.tagged):
        if w == "得" and t == "DER":
            for w2, t2 in next_words(c, i, 3):
                if t2 in ("VA", "JJ") or (t2 == "VV" and w2 in ("快", "慢", "好", "早", "晚")):
                    return True
                if t2 != "AD":
                    break
    return False


def verb_measure(c: Context) -> bool:
    """2-52 动词+动量补语: 去过一次 / 休息一下(儿) / 读了两遍."""
    for i, (w, t) in enumerate(c.tagged):
        if t != "VV":
            continue
        rest = [x for x, _ in next_words(c, i, 4)]
        k = 0
        if k < len(rest) and rest[k] in ("了", "过"):
            k += 1
        if k < len(rest) and rest[k] in ("一下", "一下儿"):
            return True
        if k + 1 < len(rest) and all(ch in NUMERALS for ch in rest[k]) and rest[k + 1] in DONGLIANG:
            return True
    return False


def adj_measure(c: Context) -> bool:
    """2-53 形容词+数量补语: 大两岁 / 凉快一点儿 / 高一些."""
    for i, (w, t) in enumerate(c.tagged):
        if t not in ("VA", "JJ"):
            continue
        rest = [x for x, _ in next_words(c, i, 2)]
        if rest and rest[0] in ("一点儿", "一点", "一些", "些", "点儿", "多了", "得多"):
            return True
        if len(rest) == 2 and all(ch in NUMERALS for ch in rest[0]) and c.tagged[i + 2][1] == "M":
            return True
    return False


def comparison_with_amount(c: Context) -> bool:
    """2-58 A比B + 形容词 + 数量/程度: 姐姐比我大两岁 / 外边比里边凉快一些."""
    if not has(c, {"比"}, {"P"}):
        return False
    i = next(k for k, (w, t) in enumerate(c.tagged) if w == "比" and t == "P")
    sub = Context.__new__(Context)
    sub.__dict__.update(c.__dict__)
    return adj_measure(c) and any(t in ("VA", "JJ") for _, t in c.tagged[i + 1:]) \
        or bool(re.search(r"比[^，。！？]{1,10}(多了|得多|一点儿|一些)", c.text))


def you_reaching(c: Context) -> bool:
    """2-55 "有"字句2 (reaching an amount): 他有一米八高 / 他有三十多岁."""
    return bool(re.search(r"有[" + "".join(NUMERALS) + r"\d]+[^，。！？]{0,4}?(高|长|重|大|远|宽|深|岁|多|克|公斤|斤|米|公里|厘米)(?=[，。！？了]|$)",
                          c.text)) and not re.search(r"有[" + "".join(NUMERALS) + r"]+(个|口|位|本|件|只|条)", c.text)


def existential_zhe(c: Context) -> bool:
    """2-56 存现句: 处所 + 动词 + 着 + 名词 (桌子上放着一本词典)."""
    for clause in clauses(c):
        words = [w for w, _ in clause]
        tags = [t for _, t in clause]
        if "着" not in words or not clause or tags[0] in PEOPLE_TAGS:
            continue
        k = words.index("着")
        if k >= 1 and tags[k - 1] == "VV" and k + 1 < len(clause) and \
                any(t == "LC" or (w[-1:] in "上下里外前后边面" and t in ("NN", "LC")) for w, t in clause[:k - 1]):
            return True
    return False


def serial_verbs(c: Context) -> bool:
    """2-57 连动句: two actions in a row in one clause (他开门出去了 /
    我们吃完饭去图书馆), not a modal + verb (我想去)."""
    for clause in clauses(c):
        verbs = [(i, w) for i, (w, t) in enumerate(clause) if t == "VV"]
        for (i1, w1), (i2, w2) in zip(verbs, verbs[1:]):
            if w1 in CATENATIVE or w1 in PIVOT_VERBS:
                continue
            between = clause[i1 + 1:i2]
            # verb + object + verb (开门出去 / 去外地工作 / 租了房子住). A person in
            # between is a pivot sentence (老师要求我们练习), a time or place
            # word is a time clause (下班以后我去) -- neither is this point.
            if between and any(t == "NN" for _, t in between) and \
                    all(t in ("NN", "M", "CD", "DT", "AS", "JJ") for _, t in between):
                return True
    return False


def same_as(c: Context) -> bool:
    """2-59 比较句3: A跟B一样 / 相同 / 不同 / 差不多."""
    return bool(re.search(r"(跟|和|同)[^，。！？]{1,10}?(一样|相同|不同|不一样|差不多)", c.text))


def shi_de(c: Context) -> bool:
    """2-60 是……的 emphasis: 我是昨天到北京的 / 他是在网上买的手机 --
    是, then a verb, then 的 closing the clause (or before a final noun)."""
    for clause in clauses(c):
        words = [w for w, _ in clause]
        for i, (w, t) in enumerate(clause):
            if w != "是":
                continue
            for j in range(i + 2, len(clause)):
                between = clause[i + 1:j]
                if clause[j][0] == "的" and any(t2 == "VV" for _, t2 in between):
                    after = clause[j + 1:]
                    # 是 + time/place/manner + verb + 的 (我是昨天到的 / 是用米做的);
                    # 是 + adjective + 的 (是新的) is description, not this point
                    marked = any(t2 in ("P", "NT") or w2 in ("用", "坐", "从", "在", "跟", "和") for w2, t2 in between)
                    if (not after and (marked or between[0][1] in PEOPLE_TAGS | {"NR"})) or \
                            (len(after) == 1 and after[0][1] == "NN" and marked):
                        return True
    return False


GIVE_VERBS = {"给", "送", "教", "问", "告诉", "借", "还", "找", "递", "交", "送给", "还给", "借给", "通知"}


def double_object(c: Context) -> bool:
    """2-61 双宾语句: 我给妹妹一本书 / 老师教我们汉语 -- verb + person +
    thing, with no second verb after the person (给我打电话 is 给 "for")."""
    for i, (w, t) in enumerate(c.tagged):
        if w not in GIVE_VERBS or t not in ("VV", "P"):
            continue
        rest = next_words(c, i, 4)
        if len(rest) >= 2 and rest[0][1] in PEOPLE_TAGS | {"NN"} and \
                not any(t2 in VERBISH for _, t2 in rest[1:3]) and \
                any(t2 in ("NN", "CD", "M", "DT") for _, t2 in rest[1:]):
            if rest[0][1] == "NN" and w == "给":
                continue
            return True
    return False


def choice_compound(c: Context) -> bool:
    """2-64 选择复句: （是）……，还是…… / 你坐火车，坐飞机？"""
    cl = clauses(c)
    if len(cl) >= 2 and has(c, {"还是"}):
        return True
    if c.is_question and len(cl) >= 2:
        firsts = [next((w for w, t in x if t in VERBISH), None) for x in cl[:2]]
        return firsts[0] is not None and firsts[0] == firsts[1]
    return False


def yi_jiu(c: Context) -> bool:
    """2-69 一……就……: 他一起床就去洗脸 -- 一 that isn't a number (no measure
    word after it), then 就 later."""
    for i, (w, t) in enumerate(c.tagged):
        if w != "一":
            continue
        nxt = c.tagged[i + 1] if i + 1 < len(c.tagged) else (None, None)
        if nxt[1] in ("M", "CD") or nxt[0] in ("下", "下儿", "点儿", "些", "起", "样", "定", "直", "共"):
            continue
        if any(x == "就" for x, _ in c.tagged[i + 2:]):
            return True
    return bool(re.search(r"一(?![个点些下次样起定直共边])[一-鿿]{1,6}就", c.text)) and "就" in c.words


def ordinal(c: Context) -> bool:
    """2-72 序数表示法: 第三 / 二楼 / 三号 / 302房间."""
    text = c.text.replace("一路", "□□")          # 一路平安 / 一路顺风
    return bool(re.search(r"第[" + "".join(NUMERALS) + r"\d]|[" + "".join(NUMERALS) + r"\d]+(楼|号楼|路|年级|层楼)", text))


def approx_number(c: Context) -> bool:
    """2-73 概数: 几个(not a question) / 三十多岁 / 一个多月 / 三四个."""
    t = c.text
    if re.search(r"[十百千万]多[个岁块天年本人次斤米]", t) or re.search(r"[一二两三四五六七八九十]+(个|年|天|块)多(?!少)", t):
        return True
    if re.search(r"(一两|两三|三四|四五|五六|六七|七八|八九)[个天年块岁本人次位]", t):
        return True
    return has(c, {"几"}) and not c.is_question


def no_verb_predicate(c: Context) -> bool:
    """2-48 / 2-54 名词谓语句: 明天星期五 / 他二十岁 / 这本书三十块 --
    no verb at all, a subject, and a time / number / noun predicate."""
    if verb_in(c.tagged) or c.is_question or len(c.tagged) < 2 or c.type == "imperative":
        return False
    if any(t in ("P", "AD", "VA", "JJ", "CC") for _, t in c.tagged):
        return False                # 他比我高 / 很多 / 在西北: a hidden predicate, not a noun one
    last_w, last_t = c.tagged[-1]
    return last_t in ("NT", "M", "CD") or bool(re.search(r"(星期|周)[一二三四五六日天]|[岁块元号点]$|晴天|阴天|雨天",
                                                         c.text.rstrip("。！")))


def no_subject(c: Context) -> bool:
    """1-31 非主谓句: 下雨了 / 车！ -- a one-word sentence, or a weather verb
    first."""
    content = [w for w, _ in c.tagged]
    return len(content) == 1 or (content and content[0] in ("下雨", "下雪", "刮风", "打雷", "出太阳", "起风", "下雨了"))


LINKERS = {"因为", "所以", "虽然", "但是", "但", "可是", "不过", "如果", "要是", "只要", "然后", "就", "而且",
           "不但", "还是", "或者"}


def parallel_compound(c: Context) -> bool:
    """1-39 并列复句: 我喜欢看电视，弟弟喜欢打球 / 他有一个哥哥，没有姐姐 /
    ……，也…… / ……，还…… -- two clauses with a verb each, no other linker."""
    cl = [x for x in clauses(c) if x]
    if len(cl) < 2 or any(w in LINKERS for w, _ in c.tagged):
        return False
    a, b = cl[0], cl[1]
    real_verb = lambda x: any(t in ("VV", "VE", "VC") for _, t in x)
    if not (real_verb(a) and real_verb(b)) or c.words[-1] == "吧":
        return False            # 大家好，我来… (greeting) / 他出来了，我们进去吧 (suggestion)
    if b[0][0] in ("也", "还") or (len(b) > 1 and b[1][0] in ("也", "还")):
        return True
    return a[0][1] in ("PN", "NR", "NN") and b[0][1] in ("PN", "NR", "NN")


def follows_then(c: Context) -> bool:
    """2-62 承接复句: ……，然后…… / 吃了晚饭，我们出去走走."""
    if has(c, {"然后", "接着", "于是"}):
        return True
    if re.search(r"先[^，。！？]{1,10}[，,]?\s*(再|然后)", c.text):
        return True
    cl = [x for x in clauses(c) if x]
    if len(cl) < 2 or any(w in LINKERS for w, _ in c.tagged):
        return False
    first = cl[0]
    # 吃了晚饭，我们出去走走: verb + 了 + object, then another action
    for k in range(len(first) - 2):
        if first[k][1] == "VV" and first[k + 1] == ("了", "AS") and first[k + 2][1] in ("NN", "CD", "M"):
            return any(t == "VV" for _, t in cl[1])
    return False


def directional(c: Context) -> bool:
    """2-50 趋向补语: verb + 来/去/上/下/进/出/回/过/起 (+ 来/去), e.g. 走来,
    拿出来, 跑过去 -- not 回来 / 出去 on their own as the main verb."""
    for i, (w, t) in enumerate(c.tagged):
        if t == "VV" and i + 1 < len(c.tagged) and c.tagged[i + 1][0] in DIRS and c.clause[i + 1] == c.clause[i] \
                and w not in DIRS and w not in CATENATIVE and w not in ("等", "骑", "坐", "开"):
            d = c.tagged[i + 1][0]
            after = c.tagged[i + 2] if i + 2 < len(c.tagged) and c.clause[i + 2] == c.clause[i] else (None, None)
            if d in ("来", "去") and after[1] in ("NN", "NR", "PN", "VV"):
                continue            # 骑车去学校 / 愿意去: 去 is the main verb, not a complement
            if d == "下" and after[0] in ("一", "个", "次"):
                continue            # 等下一辆: "next"
            return True
        if t == "VV" and 2 <= len(w) <= 4 and w[0] not in DIR_CHARS and \
                (w[1:] in DIRS or w[-2:] in DIRS) and w not in ("出来", "原来", "未来", "将来", "后来", "本来", "看来", "起来"):
            return True
    return False


def de_hua(c: Context) -> bool:
    """2-35 的话 (closing an if-clause, so 你的话 "your words" doesn't count)
    / 等 listing."""
    if re.search(r"的话[，,]", c.text):
        return True
    return bool(re.search(r"、[^，。！？]{1,8}等[。，等]|等等", c.text))


# --------------------------------- RULES ---------------------------------
# code -> rule(Context) -> bool. Ported from the HSK 1 prototype
# (hsk1_grammar_pool.py), plus sentence-type and a few HSK 2 aspect rules.

RULES = {
    "1-01": lambda c: has_tag(c, {"LC"}),
    "1-02": lambda c: followed_by_verb(c, {"会", "能"}),
    "1-03": lambda c: followed_by_verb(c, {"想", "要"}),
    "1-04": lambda c: has(c, QUESTION_WORDS - {"多"}),
    "1-05": lambda c: has(c, PRONOUNS),
    "1-06": lambda c: any(w[:1] in "这那" and t in ("DT", "PN") for w, t in c.tagged),
    "1-07": lambda c: has_tag(c, {"CD"}),
    "1-08": lambda c: has_tag(c, {"M"}),
    "1-09": lambda c: has(c, {"很", "非常", "太", "真", "最"}, {"AD"}),
    "1-10": lambda c: has(c, {"都", "一起", "一块儿"}, {"AD"}),
    "1-11": lambda c: has(c, {"马上", "先", "有时", "正"}, {"AD"}),
    "1-12": lambda c: has(c, {"常", "常常", "再"}, {"AD"}),
    "1-13": lambda c: has(c, {"也", "还"}, {"AD"}),
    "1-14": lambda c: has(c, {"不", "没", "没有", "别"}, {"AD"}),
    "1-15": lambda c: has(c, {"从"}, {"P"}),
    "1-16": zai_place_then_verb,
    "1-17": lambda c: has(c, {"跟", "和"}, {"P"}),
    "1-18": lambda c: has(c, {"比"}, {"P"}),
    "1-19": lambda c: has(c, {"跟", "和"}, {"CC"}) or (has(c, {"还是"}, {"CC"}) and c.is_question),
    "1-20": lambda c: has(c, {"的"}, {"DEG", "DEC"}) or has(c, {"地"}, {"DEV"}),
    "1-21": lambda c: has(c, {"了"}, {"AS"}),
    "1-22": lambda c: has(c, {"吧", "了", "吗", "呢"}, {"SP"}),
    "1-23": lambda c: number_then(c, {"M"}),
    "1-29": lambda c: has_tag(c, {"VV"}),
    "1-30": lambda c: has_tag(c, {"VA"}),
    "1-32": lambda c: c.type == "statement" and not c.is_exclamation,
    "1-33": lambda c: c.is_question or c.type == "question",
    "1-34": lambda c: c.type == "imperative",
    "1-35": lambda c: c.is_exclamation and c.type != "imperative",
    "1-36": lambda c: has(c, {"是"}, {"VC"}),
    "1-37": lambda c: has(c, {"有", "没有"}, {"VE"}),
    "1-38": lambda c: has(c, {"比"}, {"P"}),
    "1-40": lambda c: has(c, {"了"}, {"SP"}),
    "1-41": lambda c: has(c, {"了"}, {"AS"}),
    "1-42": zai_progressive,
    "1-43": lambda c: number_then(c, MONEY_UNITS),
    "1-44": lambda c: has_tag(c, {"NT"}) or number_then(c, TIME_UNITS),
    "1-45": lambda c: has(c, {"吗"}),
    "1-46": lambda c: c.is_question and has(c, QUESTION_WORDS),
    "1-47": lambda c: has(c, {"还是"}, {"CC"}) and c.is_question,
    "1-48": lambda c: a_not_a(c) and c.is_question,
    "1-31": no_subject,
    "1-39": parallel_compound,
    # ---- HSK 2 ----
    "2-01": lambda c: has(c, {"可能"}) and (followed_by_verb(c, {"可能"}) or has(c, {"可能"}, {"AD", "VV"})),
    "2-04": verb_reduplication,
    "2-08": adj_reduplication,
    "2-13": lambda c: (has(c, {"多么", "更", "十分", "特别", "挺", "有点儿", "有一点儿", "有点"}, {"AD"})
                       and not re.search(r"点十分", c.text))
                      or any(w in ("多", "好") and t == "AD" and nt in ("VA", "JJ")
                             for (w, t), (_, nt) in zip(c.tagged, c.tagged[1:])),
    "2-14": lambda c: has(c, {"全", "一共", "只"}, {"AD"}),
    "2-15": lambda c: has(c, {"刚", "刚刚", "忽然", "一直", "已经"}, {"AD"}),
    "2-16": lambda c: has(c, {"重新", "经常", "老", "老是", "又"}, {"AD"}),
    "2-17": lambda c: has(c, {"就"}, {"AD"}),
    "2-20": lambda c: has(c, {"才", "正好"}, {"AD"}),
    "2-21": lambda c: bool(re.search(r"当[^，。！？]{1,12}(的时候|时)", c.text)),
    "2-23": lambda c: any(w == "向" and any(t2 == "VV" for _, t2 in c.same_clause_after(i, 5))
                          for i, (w, _) in enumerate(c.tagged)),
    "2-24": lambda c: has(c, {"从"}, {"P"}) and bool(re.search(r"从[^，。！？]{1,8}?(走|过|经过|穿过|进去|进来|出去|出来)", c.text))
                      and not re.search(r"从[^，。！？]{1,10}到", c.text),
    "2-28": lambda c: has(c, {"为", "为了"}, {"P"}),
    "2-30": lambda c: has(c, {"不过", "但", "但是", "而且", "如果", "虽然", "只要"})
                      or bool(re.search(r"[，,]那(我|你|他|她|我们|你们|就)", c.text)),
    "2-35": de_hua,
    "2-43": lambda c: "什么的" in c.text and not re.search(r"(做|干|是|说|买|吃)什么的", c.text),
    "2-45": lambda c: bool(re.search(r"还是[^？?]*吧", c.text)) and not c.is_question,
    "2-48": no_verb_predicate,
    "2-51": state_complement,
    "2-52": verb_measure,
    "2-53": adj_measure,
    "2-50": directional,
    "2-54": no_verb_predicate,
    "2-55": you_reaching,
    "2-56": existential_zhe,
    "2-57": serial_verbs,
    "2-58": comparison_with_amount,
    "2-59": same_as,
    "2-60": shi_de,
    "2-61": double_object,
    "2-62": follows_then,
    "2-64": choice_compound,
    "2-66": lambda c: has(c, {"如果", "要是", "假如"}) or bool(re.search(r"的话[，,]", c.text)),
    "2-69": yi_jiu,
    "2-72": ordinal,
    "2-73": approx_number,
    "2-74": lambda c: bool(re.search(r"就(在|是)", c.text)) and has(c, {"就"}),
    "2-76": lambda c: c.is_question and bool(re.search(r"什么时候|什么样|为什么|怎么样|怎样", c.text)),
    "2-78": lambda c: "是不是" in c.text and (c.is_question or c.type == "question"),
    "2-79": lambda c: bool(re.search(r"吧[？?]", c.text)),
    "2-31": lambda c: has(c, {"得"}, {"DER"}),
    "2-32": lambda c: has(c, {"过"}, {"AS"}),
    "2-33": lambda c: has(c, {"着"}, {"AS"}),
    "2-70": lambda c: has(c, {"着"}, {"AS"}),
    "2-71": lambda c: has(c, {"过"}, {"AS"}),
    "3-54": lambda c: has(c, {"把"}, {"BA"}),
    "3-55": lambda c: has(c, {"被", "叫", "让"}, {"LB", "SB"}),
}

# HSK 1 restriction: 2021-standard points above level 1 that are basic
# enough for HSK 1 sentences, so they must NOT push a sentence out of
# HSK 1. Review the bump report and edit this set.
HSK1_ALLOWED_ABOVE = {
    "2-48",   # noun predicate: 今天星期三 / 我今年二十岁
    "2-54",   # noun predicate (number/time): 这本书三十块
    "2-60",   # 是……的: 我是坐飞机来的
    "2-57",   # serial verbs: 我去商店买东西 / 坐飞机来
}
RESTRICTED_LEVEL = 1

# Describe (nearly) every sentence; linking them would bury the real points.
SKIP = {
    "1-24": "noun/pronoun as subject: nearly every sentence",
    "1-25": "verb/adjective as predicate: nearly every sentence",
    "1-26": "noun/pronoun as object: nearly every sentence",
    "1-27": "attributive modifier: too general to detect usefully",
    "1-28": "adverbial modifier: too general to detect usefully",
    "2-37": "basic phrase types: every sentence has them",
    "2-38": "other phrase types: every sentence has them",
    "2-39": "noun phrases: every sentence has them",
    "2-40": "verb phrases: every sentence has them",
    "2-41": "adjective phrases: too general to detect usefully",
}


def compile_pattern_rule(content: dict):
    """The grammar list's own regex as a rule, or None if it has none.
    `exclude` words are blanked out of the text first, so 重要 doesn't count
    as 要 'want'."""
    pattern = content.get("pattern")
    if not pattern:
        return None
    try:
        rx = re.compile(pattern)
        ex = re.compile(content["exclude"]) if content.get("exclude") else None
    except re.error:
        return None

    def rule(c: Context) -> bool:
        text = ex.sub(lambda m: "□" * len(m.group()), c.text) if ex else c.text
        return bool(rx.search(text))
    return rule


# --------------------------------- PER SENTENCE ---------------------------------

def detect_sentence(db, s: Sentence, detectors: dict, level_of_point: dict):
    """Recomputes the sentence's level from its words, applies the HSK 1
    grammar restriction, and returns (codes found at its final level,
    bump or None). bump = (old level, new level, codes that caused it).
    Writes Sentence.hsk_level; the caller writes the links."""
    set_sentence_level(db, s)
    ds = hsa_source.get(s.external_id) if s.external_id else None
    c = Context(s.hanzi, sentence_words(s), ds.get("sentence_type") if ds else None)
    bump = None
    if s.hsk_level == RESTRICTED_LEVEL:
        above = sorted(code for code, (how, rule) in detectors.items()
                       if how == "rule" and level_of_point[code] > RESTRICTED_LEVEL
                       and code not in HSK1_ALLOWED_ABOVE and rule(c))
        if above:
            new_level = max(level_of_point[code] for code in above)
            bump = (s.hsk_level, new_level, above)
            s.hsk_level = new_level
    lvl = s.hsk_level or 99
    found = [code for code, (_, rule) in detectors.items()
             if level_of_point[code] <= lvl and rule(c)]
    return found, bump


def write_links(db, s: Sentence, found: list[str], id_of: dict):
    db.query(SentenceGrammar).filter(SentenceGrammar.sentence_id == s.id).delete()
    for code in found:
        link_sentence_grammar(db, s.id, id_of[code])


def write_bump_report(bumps: list, path_prefix: str):
    """bumps: [(sentence hanzi, old, new, codes)]. CSV + console summary."""
    if not bumps:
        print("  HSK 1 grammar restriction: no sentence moved up.")
        return None
    path = f"{path_prefix}_hsk1_bumps.csv"
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["sentence", "from_level", "to_level", "grammar_codes"])
        for hanzi, old, new, codes in bumps:
            w.writerow([hanzi, old, new, " ".join(codes)])
    by_code = Counter(code for *_, codes in bumps for code in codes)
    print(f"  HSK 1 grammar restriction: {len(bumps)} sentence(s) moved out of HSK 1. By point:")
    for code, n in by_code.most_common():
        example = next(h for h, *_, codes in bumps if code in codes)
        print(f"    {code}: {n}  e.g. {example}")
    print(f"  Every bump: {path}")
    return path


# --------------------------------- MAIN ---------------------------------

def build_detectors(points: list[GrammarTip]) -> tuple[dict, dict]:
    """code -> (how, rule). Second dict: code -> why it isn't detected."""
    detectors, undetected = {}, {}
    for p in points:
        code = p.grammar_code
        if code in SKIP:
            undetected[code] = f"skipped: {SKIP[code]}"
            continue
        if code in RULES:
            detectors[code] = ("rule", RULES[code])
            continue
        try:
            content = json.loads(p.content_json or "{}")
        except json.JSONDecodeError:
            content = {}
        rule = compile_pattern_rule(content)
        if rule:
            detectors[code] = ("pattern", rule)
        else:
            undetected[code] = "no rule and no pattern in the list"
    return detectors, undetected


def write_report(points, detectors, undetected, counts, examples, levels_of_points, n_sentences):
    os.makedirs(REPORT_DIR, exist_ok=True)
    path = os.path.join(REPORT_DIR, f"{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv")
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["grammar_code", "hsk_level", "label", "detected_by", "sentences", "example_1", "example_2"])
        for p in sorted(points, key=lambda p: (p.hsk_level, p.grammar_code)):
            how = detectors[p.grammar_code][0] if p.grammar_code in detectors else undetected[p.grammar_code]
            ex = examples.get(p.grammar_code, [])
            w.writerow([p.grammar_code, p.hsk_level, p.raw_text, how, counts.get(p.grammar_code, 0),
                        ex[0] if ex else "", ex[1] if len(ex) > 1 else ""])

    print(f"\n{n_sentences} sentence(s) checked against {len(points)} grammar point(s).")
    by_level = defaultdict(Counter)
    for p in points:
        code, lvl = p.grammar_code, p.hsk_level
        by_level[lvl]["points"] += 1
        if code in detectors:
            by_level[lvl]["by rule" if detectors[code][0] == "rule" else "by list pattern"] += 1
            by_level[lvl]["found in 1+ sentence" if counts.get(code) else "detector found nothing"] += 1
            if counts.get(code, 0) >= 20:
                by_level[lvl]["found in 20+ sentences"] += 1
        else:
            by_level[lvl]["not detectable yet" if code not in SKIP else "skipped (too general)"] += 1
    keys = ["points", "by rule", "by list pattern", "not detectable yet", "skipped (too general)",
            "found in 1+ sentence", "found in 20+ sentences", "detector found nothing"]
    print(f"  {'HSK':<5}" + "".join(f"{k:>14}" for k in ["points", "by rule", "by pattern", "undetectable",
                                                          "skipped", "found", "found 20+", "found none"]))
    for lvl in sorted(by_level):
        print(f"  {lvl:<5}" + "".join(f"{by_level[lvl][k]:>14}" for k in keys))
    print(f"  Per-point counts and two example sentences each: {path}")
    return path


def main():
    parser = argparse.ArgumentParser(description="Link tagged sentences to the HSK grammar points they use.")
    parser.add_argument("--limit", type=int, default=None, help="Only process the first N tagged sentences.")
    parser.add_argument("--report-only", action="store_true", help="Count matches but write nothing.")
    args = parser.parse_args()

    t0 = time.time()
    init_db()

    with get_session() as db:
        points = db.query(GrammarTip).all()
        if not points:
            raise SystemExit("No grammar points in the DB -- run load_hsk_lists.py first.")
        detectors, undetected = build_detectors(points)
        id_of = {p.grammar_code: p.id for p in points}
        level_of_point = {p.grammar_code: p.hsk_level for p in points}

        tagged_ids = {sid for (sid,) in db.query(SentenceVocab.sentence_id).distinct().all()}
        sentences = [s for s in db.query(Sentence).order_by(Sentence.id).all() if s.id in tagged_ids]
        untagged = db.query(Sentence).count() - len(sentences)
        if args.limit:
            sentences = sentences[:args.limit]
        print(f"Detecting grammar in {len(sentences)} tagged sentence(s)"
              f"{f' ({untagged} untagged sentence(s) skipped -- run tag_sentences.py)' if untagged else ''}...")

        counts, examples, errors, bumps = Counter(), defaultdict(list), [], []
        for i, s in enumerate(sentences, 1):
            try:
                found, bump = detect_sentence(db, s, detectors, level_of_point)
            except Exception as e:
                errors.append(f"{s.id} {s.hanzi!r}: {e!r}")
                continue
            if bump:
                bumps.append((s.hanzi, *bump))
            for code in found:
                counts[code] += 1
                if len(examples[code]) < 2:
                    examples[code].append(s.hanzi)
            if not args.report_only:
                write_links(db, s, found, id_of)
                if i % COMMIT_EVERY == 0:
                    db.commit()
            if i % COMMIT_EVERY == 0:
                print(f"  ...{i}/{len(sentences)}")

        if args.report_only:
            db.rollback()
        report_path = write_report(points, detectors, undetected, counts, examples, level_of_point, len(sentences))
        write_bump_report(bumps, report_path[:-len(".csv")])
        if errors:
            print(f"  {len(errors)} error(s), e.g. {errors[:3]}")
    print(f"Done in {time.time() - t0:.0f}s.{' (report only: nothing written)' if args.report_only else ''}")


if __name__ == "__main__":
    main()