"""验证四类路径：增补条走本地、普通条走法宝、法宝返回错条、法宝故障。"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import server  # noqa: E402
from pkulaw_mcp import PkulawError  # noqa: E402


def show(question: str, label: str) -> None:
    evidence, info = server.retrieve_evidence(question)
    first = evidence[0] if evidence else {}
    text = (first.get("text") or "")[:28].replace("\n", " ")
    print(f"{label:<10} source={str(info.get('source')):<14} {first.get('article_label', '—'):<12} | {text}")
    notice = info.get("notice")
    if notice:
        print(f"{'':<10} notice={notice[:60]}")


class WrongArticle:
    """模拟法宝返回了别的条（语义近邻）。"""
    configured = True

    def search(self, *_args, **_kwargs):
        return {
            "evidence": [{"document_title": "中华人民共和国刑法(2023修正)",
                          "article_label": "第四条", "text": "对任何人犯罪，在适用法律上一律平等。"}],
            "strategy": "law_semantic", "tool": "search_law_articles",
            "cached": False, "queried_at": "now", "notice": "",
        }


class Broken:
    configured = True

    def search(self, *_args, **_kwargs):
        raise PkulawError("模拟超时")


def main() -> None:
    print("=== 正常 ===")
    show("刑法第133条之一", "增补条")
    show("刑法第390条", "普通条")

    original = server.PKULAW
    print("\n=== 法宝返回错条 ===")
    server.PKULAW = WrongArticle()
    try:
        show("刑法第390条", "错条")
    finally:
        server.PKULAW = original

    print("\n=== 法宝故障 ===")
    server.PKULAW = Broken()
    try:
        show("刑法第390条", "故障")
    finally:
        server.PKULAW = original


if __name__ == "__main__":
    main()
