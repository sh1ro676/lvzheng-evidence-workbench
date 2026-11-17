"""核对六部法入库完整性：条数、条号范围、版本与时效元数据。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import server  # noqa: E402


def main() -> None:
    store = server.STORE
    for doc in store.document_list():
        doc_id = doc["document_id"]
        numbers = sorted(number for (candidate, number) in store._article_lookup if candidate == doc_id)
        gaps = []
        if numbers:
            for expected in range(1, max(numbers) + 1):
                if expected not in numbers:
                    gaps.append(expected)
        print(doc["title"])
        print(f"   条数={len(numbers)}  条号范围={numbers[0]}–{numbers[-1]}  缺号={len(gaps)}"
              + (f"（例：{gaps[:8]}）" if gaps else ""))
        meta = {key: doc.get(key) for key in ("published_at", "effective_at", "status", "source_name")
                if doc.get(key)}
        print(f"   元数据：{json.dumps(meta, ensure_ascii=False)}")
        print()


if __name__ == "__main__":
    main()
