from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from server import DEEPSEEK, STORE, response_payload  # noqa: E402


def main() -> int:
    if not DEEPSEEK.configured:
        print(json.dumps({"configured": False, "error": "未检测到 DEEPSEEK_API_KEY，请确认已保存 .env"}, ensure_ascii=False, indent=2))
        return 2
    question = "请给出《中华人民共和国民法典》第1165条原文，并解释其含义。"
    evidence = STORE.search(question)
    result = response_payload(question, evidence)
    output = {
        "configured": True,
        "model": DEEPSEEK.model,
        "status": result["status"],
        "evidence_count": len(result["evidence"]),
        "answer": result["answer"],
        "verification": result["verification"],
    }
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "verified" else 1


if __name__ == "__main__":
    raise SystemExit(main())
