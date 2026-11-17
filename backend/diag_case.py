"""复现「北大杀母案」的检索，打印每条证据的出处与正文开头。"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import server  # noqa: E402

QUESTION = "为我讲解北大杀母案"


def main() -> None:
    print("案件类判定:", bool(server.CASE_QUERY_RE.search(QUESTION)))
    print("刑事用语判定:", bool(server.CRIMINAL_SCOPE_RE.search(QUESTION)), "|", server.CRIMINAL_SCOPE_RE.search(QUESTION))

    evidence, info = server.retrieve_evidence(QUESTION)
    print("\n法条检索:", info.get("source"), info.get("strategy"), "| 证据数", len(evidence))
    for item in evidence:
        print(f"  - {item['document_title']} {item['article_label']} | {item['source_name']}")
        print(f"    {(item.get('text') or '')[:80]}".replace("\n", " "))

    textbook = server.retrieve_textbook_evidence(QUESTION, evidence)
    print("\n教材证据:", len(textbook), [t["article_label"] for t in textbook])


if __name__ == "__main__":
    main()
