import json
from collections import Counter
from app.core.logger import logger

qs = json.load(open("data/clean/unit_questions_hsk1.json"))
for unit in ["6", "7", "8", "10", "13"]:
    for tag in ["好吃", "中国菜", "汉字", "饭馆儿", "今天", "请", "多少钱", "没有", "电话"]:
        types = Counter(q["question_type"] for q in qs.get(unit, []) if tag in q["tags"])
        if types:
            logger.debug(f"unit {unit} {tag}: {dict(types)}")