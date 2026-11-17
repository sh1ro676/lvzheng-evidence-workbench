"""验证第一环调度：模型决定查哪里，以及取证据的耗时。"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import server  # noqa: E402

QUESTIONS = [
    "为我讲解北大杀母案",
    "刑法第77条为我讲解",
    "正当防卫的限度是什么",
    "醉驾撞人怎么判",
]


def main() -> None:
    for question in QUESTIONS:
        start = time.time()
        plan = server.route_question(question)
        route_seconds = time.time() - start

        start = time.time()
        evidence, info = server.evidence_by_plan(question, plan)
        fetch_seconds = time.time() - start

        print(f"\n{question}")
        print(f"  调度 {route_seconds:4.1f}s  kind={plan['kind']} law={plan['law']} "
              f"article={plan['article']} web={plan['need_web']} schools={plan['need_schools']}")
        print(f"  教材词={plan['textbook_query']!r} 联网词={plan['web_query'][:30]!r}")
        print(f"  取证据 {fetch_seconds:4.1f}s  {len(evidence)} 条  source={info.get('source')}")
        for item in evidence[:3]:
            print(f"    - {item['article_label']} | {(item['text'] or '')[:36]}".replace("\n", " "))


if __name__ == "__main__":
    main()
