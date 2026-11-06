"""
data_pipelines/textbook/test_scripts/parse_asg.py

TRIAL: parses the AllSet Learning Chinese Grammar Wiki pages ("ASG") into
one JSON file of grammar points, for writing and testing grammar detection
rules. Nothing from it is shown to learners.

LICENSE: ASG content is CC BY-NC-SA 3.0 -- non-commercial only, with
attribution ("AllSet Learning Chinese Grammar Wiki",
https://resources.allsetlearning.com/chinese/grammar/). Fine while the app
earns nothing; replace this source before it ever does.

INPUT: data/raw/allsetlearning_chinese_grammar_wiki/ASGxxxxx.txt (title in a
<!-- --> comment on line 1, then raw MediaWiki markup).

OUTPUT: data/intermediate/allsetlearning/asg_points.json, one entry per page:
  id, title, keyword, cefr, hsk_2021 (levels from {{2021-HSK|HSKn}}), hsk_old,
  structures    the <div class="jiegou"> patterns ("Number + 多 + Measure word")
  examples      [{hanzi, words, marked, english, pinyin, section}] from
                <div class="liju">: hanzi without the wiki's spaces, words = the
                wiki's word split, marked = the <em> word(s) -- the grammar
                point itself in that sentence
  forms         [{text, ok}] the correct (o) / incorrect (x) form lists
  similar       {{Similar|...}} + "See also" pages, as ids where they resolve
  text          the explanation, markup removed
plus a report on what couldn't be parsed (output/asg_report.txt).

USAGE (from the repo root)
    python data_pipelines/textbook/test_scripts/parse_asg.py
    python data_pipelines/textbook/test_scripts/parse_asg.py --find 都 一起 一块儿
"""
import argparse
import json
import re
from collections import Counter
from pathlib import Path

TEST_DIR = Path(__file__).resolve().parent
DATA_DIR = TEST_DIR.parent / "data"
WIKI_DIR = DATA_DIR / "raw" / "allsetlearning_chinese_grammar_wiki"
OUT_PATH = DATA_DIR / "intermediate" / "allsetlearning" / "asg_points.json"
REPORT_PATH = TEST_DIR / "output" / "asg_report.txt"

_TITLE_RE = re.compile(r"^\s*<!--\s*(.*?)\s*-->")
_TEMPLATE_RE = re.compile(r"\{\{([^{}]*)\}\}")
_DIV_RE = re.compile(r'<div\s+class="([^"]+)"\s*>(.*?)</div>', re.S)
_HEADING_RE = re.compile(r"^(=+)\s*(.*?)\s*\1\s*$")
_LINK_RE = re.compile(r"\[\[([^\]|]*)(?:\|([^\]]*))?\]\]")
_CATEGORY_RE = re.compile(r"\[\[Category:([^\]]*)\]\]", re.I)
_TAG_RE = re.compile(r"<[^>]+>")
_SPAN_RE = re.compile(r'<span\s+class="([^"]+)"\s*>(.*?)</span>', re.S)
_LI_RE = re.compile(r'^<li(?:\s+class="([^"]*)")?\s*>', re.I)
_CJK_RE = re.compile(r"[㐀-鿿]")
_HSK_RE = re.compile(r"HSK\s*(\d)", re.I)


def norm_title(t: str) -> str:
    t = t.replace("_", " ").replace("“", '"').replace("”", '"').replace("’", "'")
    return re.sub(r"\s+", " ", t).strip().lower()


def strip_markup(s: str) -> str:
    s = _CATEGORY_RE.sub("", s)
    s = _LINK_RE.sub(lambda m: m.group(2) or m.group(1), s)
    s = re.sub(r"'''?", "", s)
    s = _TAG_RE.sub("", s)
    return s.replace("&nbsp;", " ").strip()


def templates(text: str) -> list[tuple[str, list[str]]]:
    out = []
    for m in _TEMPLATE_RE.finditer(text):
        parts = [p.strip() for p in m.group(1).split("|")]
        out.append((parts[0], parts[1:]))
    return out


def parse_example(line: str):
    """One liju line -> (example dict, ok flag or None) or (None, reason)."""
    s = line.strip()
    ok = None
    li = _LI_RE.match(s)
    if li:
        cls = (li.group(1) or "").split()
        ok = False if "x" in cls else True if "o" in cls else None
        s = s[li.end():]
    elif s[:1] in "*#":
        s = s.lstrip("*#: ").strip()
    else:
        return None, "not a list line"
    s = re.sub(r"</li>\s*$", "", s).strip()
    spans = {cls: strip_markup(v) for cls, v in _SPAN_RE.findall(s)}
    first_span = s.find("<span")
    chinese = s if first_span == -1 else s[:first_span]
    if not _CJK_RE.search(chinese):
        return None, "no Chinese before the first <span>"
    speaker = re.match(r"\s*(?:<[^>]+>\s*)*([A-Z])\s*[:：]\s*", chinese)
    if speaker:                                   # dialogue line "A: ..."
        chinese = chinese[speaker.end():]
    # keep <em> positions, drop every other tag
    marked_src = chinese.replace("<em>", "\x01").replace("</em>", "\x02")
    marked_src = strip_markup(marked_src)
    words = [w for w in re.split(r"\s+", marked_src.replace("\x01", "").replace("\x02", "")) if w]
    hanzi, marked, cur, inside = "", [], "", False
    for ch in re.sub(r"\s+", "", marked_src):
        if ch == "\x01":
            inside, cur = True, ""
        elif ch == "\x02":
            inside = False
            if cur:
                marked.append({"text": cur, "start": len(hanzi) - len(cur)})
        else:
            hanzi += ch
            if inside:
                cur += ch
    ex = {"hanzi": hanzi, "words": words, "marked": marked,
          "english": spans.get("trans", ""), "pinyin": spans.get("pinyin", "")}
    if speaker:
        ex["speaker"] = speaker.group(1)
    extra = {k: v for k, v in spans.items() if k not in ("trans", "pinyin")}
    if extra:
        ex["other"] = extra
    return ex, ok


def parse_page(path: Path, stats: Counter, unparsed: list):
    raw = path.read_text(encoding="utf-8")
    m = _TITLE_RE.match(raw)
    title = m.group(1) if m else path.stem
    body = raw[m.end():] if m else raw
    page = {"id": path.stem, "title": title, "keyword": "", "cefr": "", "hsk_2021": [], "hsk_old": [],
            "structures": [], "examples": [], "forms": [], "similar_titles": [], "categories": [], "text": ""}

    for name, params in templates(body):
        stats[f"template: {name}"] += 1
        low = name.lower()
        if low == "2021-hsk":
            page["hsk_2021"] += [int(x) for p in params for x in _HSK_RE.findall(p)]
        elif low == "hsk":
            page["hsk_old"] += [int(x) for p in params for x in _HSK_RE.findall(p)]
        elif low == "basic grammar" and params:
            page["keyword"] = params[0]
            page["cefr"] = params[1] if len(params) > 1 else ""
            if len(params) > 2 and params[2]:
                page["basic_pattern"] = strip_markup(params[2])
        elif low == "similar" and params:
            page["similar_titles"].append(params[0])
    page["categories"] = [c.strip() for c in _CATEGORY_RE.findall(body)]

    # sections, so each example knows which heading it is under
    section_at = []
    pos = 0
    for line in body.splitlines(keepends=True):
        h = _HEADING_RE.match(line.strip())
        if h:
            section_at.append((pos, h.group(2)))
        pos += len(line)

    def section_of(offset):
        name = ""
        for p, n in section_at:
            if p > offset:
                break
            name = n
        return name

    for dm in _DIV_RE.finditer(body):
        cls, content = dm.group(1).split(), dm.group(2)
        stats[f"div class: {' '.join(cls)}"] += 1
        if "jiegou" in cls:
            for line in content.splitlines():
                t = strip_markup(line)
                if t:
                    page["structures"].append(t)
        elif "liju" in cls:
            sec = section_of(dm.start())
            # an example can run over several lines (its <span>s on the next
            # ones): join continuation lines onto the item they belong to
            items = []
            for line in content.splitlines():
                t = line.strip()
                if not t or t.lower() in ("<ul>", "</ul>", "<ol>", "</ol>", "</li>"):
                    continue
                if t[:1] in "*#" or t.lower().startswith("<li"):
                    items.append(t)
                elif items and not t.startswith(("|", "{|", "!")) and "trans" not in items[-1]:
                    items[-1] += " " + t
                else:
                    items.append(t)
            for t in items:
                if t.startswith(("|", "{|", "!")):
                    stats["liju table lines skipped (structure tables, not sentences)"] += 1
                    continue
                ex, ok = parse_example(t)
                if ex is None:
                    stats["liju line not parsed"] += 1
                    unparsed.append((page["id"], ok, t[:160]))
                    continue
                if ok is None:
                    ex["section"] = sec
                    page["examples"].append(ex)
                    stats["examples"] += 1
                    stats["examples with no English"] += not ex["english"]
                    stats["examples with no <em>"] += not ex["marked"]
                else:
                    page["forms"].append({"text": ex["hanzi"], "ok": ok, "marked": ex["marked"]})
                    stats["o/x forms"] += 1

    # "See also" links count as similar too
    see = re.search(r"==\s*See\s+also\s*==(.*?)(?:\n==[^=]|\Z)", body, re.S | re.I)
    if see:
        page["similar_titles"] += [m.group(1) for m in _LINK_RE.finditer(see.group(1))
                                   if not m.group(1).lower().startswith("category:")]

    text = _DIV_RE.sub(" ", body)
    text = _TEMPLATE_RE.sub("", text)
    text = re.split(r"\n==\s*(?:See\s+also|Sources and further reading)\s*==", text, flags=re.I)[0]
    lines = []
    for line in text.splitlines():
        h = _HEADING_RE.match(line.strip())
        t = f"## {h.group(2)}" if h else strip_markup(line)
        if t:
            lines.append(t)
    page["text"] = "\n".join(lines)
    return page


def main():
    parser = argparse.ArgumentParser(description="Parse the AllSet Learning grammar wiki into JSON.")
    parser.add_argument("--wiki", default=str(WIKI_DIR))
    parser.add_argument("--find", nargs="+", default=None,
                        help="Also list pages whose keyword, title or marked words contain these.")
    args = parser.parse_args()

    files = sorted(p for p in Path(args.wiki).rglob("ASG*.txt"))
    if not files:
        raise SystemExit(f"No ASG*.txt files under {args.wiki}")
    stats, unparsed = Counter(), []
    pages = [parse_page(p, stats, unparsed) for p in files]

    by_title = {norm_title(p["title"]): p["id"] for p in pages}
    unresolved = Counter()
    for p in pages:
        ids = []
        for t in dict.fromkeys(p["similar_titles"]):
            pid = by_title.get(norm_title(t))
            if pid and pid != p["id"]:
                ids.append(pid)
            elif not pid:
                unresolved[t] += 1
        p["similar"] = list(dict.fromkeys(ids))
        del p["similar_titles"]

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps({
        "_source": "AllSet Learning Chinese Grammar Wiki (https://resources.allsetlearning.com/chinese/grammar/), "
                   "CC BY-NC-SA 3.0. Parsed by test_scripts/parse_asg.py.",
        "points": pages}, ensure_ascii=False, indent=1), encoding="utf-8")

    levels = Counter(l for p in pages for l in set(p["hsk_2021"]))
    rep = [f"{len(pages)} page(s) parsed from {args.wiki}", ""]
    rep.append("2021-HSK level tags: " + ", ".join(f"HSK{k}: {v}" for k, v in sorted(levels.items())))
    rep.append(f"  pages with no 2021-HSK tag: {sum(not p['hsk_2021'] for p in pages)}")
    rep.append(f"  pages with no structure: {sum(not p['structures'] for p in pages)}")
    rep.append(f"  pages with no examples: {sum(not p['examples'] for p in pages)}")
    rep.append(f"  pages with no keyword: {sum(not p['keyword'] for p in pages)}")
    rep.append(f"  'Similar'/'See also' titles that match no page: {sum(unresolved.values())} "
               f"(e.g. {', '.join(list(unresolved)[:6])})")
    rep.append("")
    for k, v in sorted(stats.items()):
        rep.append(f"  {v:>6}  {k}")
    rep.append(f"\nLiju lines not parsed ({len(unparsed)}), first 25:")
    rep += [f"  {pid}: {line}" for pid, _, line in unparsed[:25]]
    odd = [(p["id"], e["hanzi"]) for p in pages for e in p["examples"]
           if "other" in e]
    rep.append(f"\nExamples with extra spans ({len(odd)}), first 15:")
    rep += [f"  {pid}: {h}" for pid, h in odd[:15]]
    if args.find:
        rep.append(f"\nPages for {' '.join(args.find)}:")
        for p in pages:
            marked = {m["text"] for e in p["examples"] for m in e["marked"]}
            if any(w in p["keyword"] or w in p["title"] or w in marked for w in args.find):
                rep.append(f"  {p['id']}  {p['title']}  [HSK2021 {p['hsk_2021']}, keyword {p['keyword']!r}, "
                           f"{len(p['examples'])} examples]")
                for s in p["structures"][:3]:
                    rep.append(f"      structure: {s}")
                if p["examples"]:
                    e = p["examples"][0]
                    rep.append(f"      e.g. {e['hanzi']}  ({e['english']})  marked {[m['text'] for m in e['marked']]}")
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text("\n".join(rep) + "\n", encoding="utf-8")
    print("\n".join(rep))
    print(f"\nWritten: {OUT_PATH}\nReport: {REPORT_PATH}")


if __name__ == "__main__":
    main()