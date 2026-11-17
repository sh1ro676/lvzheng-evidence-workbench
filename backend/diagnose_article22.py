from __future__ import annotations

import json
import sys
from pathlib import Path

import server


QUESTION = "搜索《中华人民共和国民法典》第22条相关的论文，必须是北京大学、清华大学、中国政法大学或者西北政法大学老师的"


def main() -> None:
    output = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("article22-diagnostic.json")
    evidence, info = server.retrieve_web_evidence(QUESTION, True)
    payload = {
        "question": QUESTION,
        "target_institutions": server.TARGET_INSTITUTIONS,
        "evidence_count": len(evidence),
        "evidence": evidence,
        "retrieval": info,
    }
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "output": str(output.resolve()),
        "search_count": info["search_count"],
        "evidence_count": len(evidence),
        "queries": info["queries"],
        "institution_results": info.get("institution_results", []),
        "fallback_general": info["fallback_general"],
        "notice": info.get("notice"),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
