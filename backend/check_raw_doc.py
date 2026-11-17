"""看刑法入库文件里"之一"条与重复条号的处理方式。"""
from __future__ import annotations

import json
from pathlib import Path

PATH = Path(__file__).resolve().parent / "data" / "documents" / "criminal-law.json"


def main() -> None:
    data = json.loads(PATH.read_text(encoding="utf-8"))
    articles = data["articles"] if isinstance(data, dict) and "articles" in data else data
    print("总条数:", len(articles))
    print("首条字段:", list(articles[0].keys()))

    zhina = [a for a in articles if "之" in str(a.get("article_label", ""))]
    print("含『之』的条:", len(zhina))

    print("\n=== 找 133 附近 ===")
    for index, article in enumerate(articles):
        label = str(article.get("article_label", ""))
        if "一百三十三" in label:
            print(f"  #{index} {label} | {str(article.get('text',''))[:40]}")

    print("\n=== 找 390 附近 ===")
    for index, article in enumerate(articles):
        label = str(article.get("article_label", ""))
        if "三百九十" in label:
            print(f"  #{index} {label} | {str(article.get('text',''))[:40]}")

    print("\n=== article_id 样例 ===")
    for article in articles[128:136]:
        print("  ", article.get("article_id"), article.get("article_label"))


if __name__ == "__main__":
    main()
