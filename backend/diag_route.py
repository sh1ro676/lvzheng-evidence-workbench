"""复现长问题/口语问题下的检索路由漂移。"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import server  # noqa: E402

QUESTIONS = [
    "根据教学要求，刑法第44条规定的拘役刑期怎么算，先行羁押怎么折抵？",
    "老师让我讲讲刑法第44条，还要结合教学要求说明",
    "拘役的刑期从什么时候开始算，羁押能折抵吗？",
    "刑法第44条 拘役 羁押折抵 教学要求 案例",
]


def main() -> None:
    for question in QUESTIONS:
        numbers = server.article_numbers(question)
        evidence, info = server.retrieve_evidence(question)
        print("Q:", question)
        print(f"   nums={numbers} source={info.get('source')} strategy={info.get('strategy')}")
        for item in evidence[:5]:
            print("    -", item["document_title"][-12:], item["article_label"],
                  "|", (item.get("text") or "")[:30].replace("\n", " "))
        print()


if __name__ == "__main__":
    main()
