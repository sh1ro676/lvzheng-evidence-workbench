"""端到端计时：模拟前端那条问题（法条 + 教材，跳过全网检索）。"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import server  # noqa: E402


def main() -> None:
    question = "刑法第77条，为我讲解"

    start = time.time()
    evidence, retrieval = server.retrieve_evidence(question)
    textbook = server.retrieve_textbook_evidence(question, evidence)
    if textbook:
        evidence = evidence + textbook
        retrieval["textbook"] = {"count": len(textbook),
                                 "pages": [item["article_label"] for item in textbook]}
    need_web = not textbook or server.WANTS_FRESH_WEB_RE.search(question)
    web, web_info = server.retrieve_web_evidence(question, need_web)
    evidence += web
    mid = time.time() - start
    print(f"检索耗时 {mid:5.1f}s｜法条 {len(evidence)-len(web)}｜教材 {len(textbook)}｜网页 {len(web)}")

    start = time.time()
    payload = server.response_payload(question, evidence)
    print(f"生成耗时 {time.time()-start:5.1f}s｜状态 {payload['status']}")
    for check in payload["verification"]["checks"]:
        print("  ", "✓" if check["passed"] else "×", check["name"])
    print("\n--- 回答 ---")
    print(payload["answer"][:900])


if __name__ == "__main__":
    main()
