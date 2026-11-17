"""复现「第44条取不到」：测各种写法下的条号解析与检索结果。"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import server  # noqa: E402

VARIANTS = [
    "刑法第44条",
    "刑法第44条怎么规定",
    "刑法第 44 条",
    "刑法第４４条",
    "刑法第４４条怎么规定",
    "刑法 第44条",
    "第四十四条",
    "刑法第44条之一",
]


def main() -> None:
    for question in VARIANTS:
        numbers = server.article_numbers(question)
        evidence, info = server.retrieve_evidence(question)
        first = [(item["document_title"][-4:], item["article_label"]) for item in evidence[:2]]
        print(f"{question!r:<26} nums={numbers} src={info.get('source')} -> {first}")


if __name__ == "__main__":
    main()
