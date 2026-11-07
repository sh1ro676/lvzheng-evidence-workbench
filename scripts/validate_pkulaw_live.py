from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from server import PKULAW  # noqa: E402


def main() -> int:
    if not PKULAW.configured:
        print(json.dumps({"configured": False, "error": "未检测到 PKULAW_AUTH_TOKEN，请确认已保存 .env"}, ensure_ascii=False, indent=2))
        return 2
    question = sys.argv[1] if len(sys.argv) > 1 else "《中华人民共和国民法典》第1165条"
    strategy = sys.argv[2] if len(sys.argv) > 2 else ("fatiao" if len(sys.argv) == 1 else None)
    try:
        result = PKULAW.search(question, strategy=strategy)
    except Exception as error:
        print(json.dumps({"configured": True, "ok": False, "error": str(error)}, ensure_ascii=False, indent=2))
        return 1
    safe_result = {
        "configured": True,
        "ok": bool(result["evidence"]),
        "provider": result["provider"],
        "strategy": result["strategy"],
        "tool": result["tool"],
        "cached": result["cached"],
        "queried_at": result["queried_at"],
        "evidence_count": len(result["evidence"]),
        "evidence": [
            {
                "document_title": item["document_title"],
                "article_label": item["article_label"],
                "text": item["text"],
                "status": item["status"],
                "effect_level": item.get("effect_level", ""),
                "published_at": item["published_at"],
                "effective_at": item["effective_at"],
                "source_name": item["source_name"],
                "source_url": item["source_url"],
            }
            for item in result["evidence"]
        ],
    }
    print(json.dumps(safe_result, ensure_ascii=False, indent=2))
    return 0 if result["evidence"] else 3


if __name__ == "__main__":
    raise SystemExit(main())
