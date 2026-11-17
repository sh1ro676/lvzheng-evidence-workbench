"""核对每部法入库条数与可检索键数是否一致。"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import server  # noqa: E402


def main() -> None:
    store = server.STORE
    total_docs = total_keys = 0
    for doc in store.document_list():
        keys = sum(1 for candidate, _ in store._article_lookup if candidate == doc["document_id"])
        total_docs += doc["article_count"]
        total_keys += keys
        flag = "✓" if keys == doc["article_count"] else f"✗ 差 {doc['article_count'] - keys}"
        print(f"{doc['title'][:34]:<36} 声明{doc['article_count']:>5}  可检索{keys:>5}  {flag}")
    print(f"\n合计：声明 {total_docs} 条，可检索 {total_keys} 条")


if __name__ == "__main__":
    main()
