"""用条文特征表述核对刑法增补条与条号是否错位。"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import server  # noqa: E402

KEYS = [
    ("追逐竞驶", "危险驾驶罪 133条之一"),
    ("抢夺方向盘", "妨害安全驾驶罪 133条之二"),
    ("高利放贷", "催收非法债务罪 293条之一"),
    ("关系密切的人", "对有影响力的人行贿 390条之一"),
    ("给予国家工作人员以财物", "行贿罪 389条"),
    ("侵吞、窃取、骗取", "贪污罪 382条"),
]


def main() -> None:
    store = server.STORE
    doc = [d for d in store.document_list() if d["title"] == "中华人民共和国刑法"][0]
    doc_id = doc["document_id"]
    items = [(number, item) for (candidate, number), item in store._article_lookup.items()
             if candidate == doc_id]
    items.sort()

    print("=== 130–136 条的标签 ===")
    for number, item in items:
        if 130 <= number <= 136:
            print(f"  {number}: {item.get('article_label')} | {(item.get('text') or '')[:34]}")

    print("\n=== 特征表述命中 ===")
    for phrase, name in KEYS:
        found = [(number, item) for number, item in items if phrase in (item.get("text") or "")]
        label = found[0][1].get("article_label") if found else "未入库"
        print(f"  {name:<26} {len(found)} 条 → {label}")


if __name__ == "__main__":
    main()
