"""测没有「第」字或写法不规范的条号，复现证据漂移。"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import server  # noqa: E402

QUESTIONS = [
    "刑法44条",
    "刑法44条怎么规定",
    "44条拘役的刑期怎么算",
    "刑法第44条",
    "刑法第 44 条 拘役",
    "《刑法》44条",
]


def main() -> None:
    for question in QUESTIONS:
        numbers = server.article_numbers(question)
        evidence, info = server.retrieve_evidence(question)
        print("Q:", question)
        print(f"   nums={numbers} source={info.get('source')} strategy={info.get('strategy')}")
        for item in evidence[:4]:
            print("    -", item["document_title"][-10:], item["article_label"],
                  "|", (item.get("text") or "")[:28].replace("\n", " "))
        print()


if __name__ == "__main__":
    main()
