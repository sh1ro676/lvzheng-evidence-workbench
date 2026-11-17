"""教材知识卡：加载、校验、按条文检索。

为什么单独一层：教材是"通说"，论文是"争点"，两者证据等级不同，
不能混在一个证据包里。教材卡要求可溯源到"书名+版次+页码"，
且区分「概括」（thesis，自己的话）与「原文」（quote，逐字摘录）。

卡片字段（CSV 表头）：
    card_id, law, article, textbook, author, school, edition, publisher,
    year, chapter, section, page, thesis, quote, note, verified_by, verified_at

约束：
- quote 逐字摘录，≤300 字；不得改写、不得拼接不同段落；
- thesis 是自己的概括，≤120 字，不得与 quote 混同；
- page 必填：没有页码的摘录不可核验，一律拒收；
- article 会对照本地法条库校验，防止条号写错。
"""
from __future__ import annotations

import csv
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

MAX_QUOTE = 300
MAX_THESIS = 120
REQUIRED = ("card_id", "law", "article", "textbook", "author", "school",
            "publisher", "page", "thesis")


@dataclass
class Card:
    card_id: str
    law: str
    article: str
    textbook: str
    author: str
    school: str
    edition: str = ""
    publisher: str = ""
    year: str = ""
    chapter: str = ""
    section: str = ""
    page: str = ""
    thesis: str = ""
    quote: str = ""
    note: str = ""
    verified_by: str = ""
    verified_at: str = ""
    problems: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.problems

    def render(self) -> str:
        head = f"《{self.law}》第{self.article}条 · {self.textbook}"
        if self.edition:
            head += f"（{self.edition}）"
        head += f" · {self.author}（{self.school}） · {self.page}"
        body = f"\n通说：{self.thesis}"
        if self.quote:
            body += f"\n原文：{self.quote}"
        return head + body


def load_cards(path: Path) -> list[Card]:
    if not path.exists():
        raise FileNotFoundError(f"未找到教材卡：{path}")
    cards = []
    with path.open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            if not any((value or "").strip() for value in row.values()):
                continue
            cards.append(Card(**{key: (value or "").strip() for key, value in row.items()
                                if key in Card.__dataclass_fields__}))
    return cards


PLACEHOLDERS = {"待填", "TODO", "TBD", "-"}


def _missing(value: str) -> bool:
    return not value or value.strip() in PLACEHOLDERS


KNOWN_LAWS = {
    "中华人民共和国民法典", "中华人民共和国刑法",
    "中华人民共和国民事诉讼法", "中华人民共和国刑事诉讼法",
    "最高人民法院关于适用《中华人民共和国民事诉讼法》的解释",
    "最高人民法院关于适用《中华人民共和国刑事诉讼法》的解释",
}


def validate(card: Card, known_articles: set[tuple[str, str]] | None = None) -> list[str]:
    problems = [f"缺少必填字段：{name}" for name in REQUIRED if _missing(getattr(card, name))]
    if card.law and card.law not in KNOWN_LAWS:
        problems.append(f"law 不在六部法清单内：{card.law}")
    if len(card.quote) > MAX_QUOTE:
        problems.append(f"quote 超长（{len(card.quote)} > {MAX_QUOTE}）")
    if len(card.thesis) > MAX_THESIS:
        problems.append(f"thesis 超长（{len(card.thesis)} > {MAX_THESIS}）")
    if len(card.quote) >= 20 and card.quote in card.thesis:
        problems.append("thesis 直接复述了 quote，应写自己的概括")
    if card.article and not re.fullmatch(r"\d{1,4}(?:-\d{1,2})?", card.article):
        problems.append(f"article 格式不对：{card.article}")
    if known_articles and (card.law, card.article) not in known_articles:
        problems.append(f"条号在本地法条库中不存在：{card.law}第{card.article}条")
    return problems


def lookup(cards: list[Card], law: str, article: str) -> list[Card]:
    short = re.sub(r"^中华人民共和国", "", law)
    hits = []
    for card in cards:
        card_law = re.sub(r"^中华人民共和国", "", card.law)
        if card.article == article and (card_law == short or short in card.law):
            hits.append(card)
    return hits


def search(cards: list[Card], terms: list[str]) -> list[Card]:
    scored = []
    for card in cards:
        haystack = " ".join([card.law, card.thesis, card.quote, card.chapter,
                             card.section, card.note, card.textbook])
        score = sum(3.0 if term in card.thesis else 1.0 for term in terms if term in haystack)
        if score:
            scored.append((score, card))
    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [card for _, card in scored]


def main(argv: list[str]) -> int:
    path = Path(argv[1]) if len(argv) > 1 else Path("data/textbook_cards.csv")
    cards = load_cards(path)

    bad = 0
    for card in cards:
        card.problems = validate(card)
        if card.problems:
            bad += 1
            print(f"[×] {card.card_id or '(无编号)'} {card.law}第{card.article}条")
            for problem in card.problems:
                print(f"      - {problem}")
    print(f"\n共 {len(cards)} 张卡，{len(cards) - bad} 张通过，{bad} 张需修。")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
