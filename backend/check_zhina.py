"""查刑法"之一/之二"条是否入库、是否造成条号错位。"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import server  # noqa: E402

KEYS = [
    ("醉酒驾驶机动车", "危险驾驶罪（133条之一）"),
    ("妨害安全驾驶", "妨害安全驾驶罪（133条之二）"),
    ("催收高利放贷", "催收非法债务罪（293条之一）"),
    ("对有影响力的人行贿", "对有影响力的人行贿罪（390条之一）"),
    ("给予国家工作人员以财物", "行贿罪（389条）"),
    ("贪污、受贿", "贪污罪（382条）"),
]


def main() -> None:
    store = server.STORE
    doc = [d for d in store.document_list() if d["title"] == "中华人民共和国刑法"][0]
    doc_id = doc["document_id"]

    # 全文里出现"之一"的条数
    hits = [item for (candidate, _), item in store._article_lookup.items() if candidate == doc_id]
    with_zhina = sum(1 for item in hits if "之一" in str(item.get("article_label", "")) or "之一" in (item.get("text") or ""))
    print(f"刑法入库 {len(hits)} 条；正文或标签含『之一』的：{with_zhina} 条\n")

    for phrase, name in KEYS:
        found = [item for item in hits if phrase in (item.get("text") or "")]
        print(f"{name:<26} 命中 {len(found)} 条", end="")
        if found:
            print(f" → {found[0].get('article_label')}")
        else:
            print(" → 未入库")


if __name__ == "__main__":
    main()
