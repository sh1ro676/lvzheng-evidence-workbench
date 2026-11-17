"""确认本地库收录了哪些法律/司法解释，以及司法解释条文长什么样。"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from legal_store import LegalStore  # noqa: E402


def main() -> None:
    store = LegalStore()
    print("=== 本地库文档清单 ===")
    for doc in store.document_list():
        print(f"{doc.get('title', '?'):<60} 条数={doc.get('article_count')}")

    print("\n=== 司法解释条文样例 ===")
    hits = store.exact_matches("最高人民法院关于适用《中华人民共和国民事诉讼法》的解释第一条")
    for item in hits[:2]:
        print(f"{item['document_title']} {item['article_label']}")
        print("  ", item["text"][:200])


if __name__ == "__main__":
    main()
