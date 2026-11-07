from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from server import response_payload, retrieve_evidence  # noqa: E402


def main() -> int:
    question = sys.argv[1] if len(sys.argv) > 1 else "未成年人，并且偷盗，强奸，大概会怎么判刑"
    try:
        evidence, retrieval = retrieve_evidence(question)
        result = response_payload(question, evidence)
        output = {
            "question": question,
            "retrieval": retrieval,
            "evidence": [
                {
                    "citation_id": index,
                    "document_title": item["document_title"],
                    "article_label": item["article_label"],
                    "text": item["text"],
                    "status": item["status"],
                }
                for index, item in enumerate(evidence, start=1)
            ],
            "status": result["status"],
            "answer": result["answer"],
            "verification": result["verification"],
        }
        print(json.dumps(output, ensure_ascii=False, indent=2))
        return 0 if result["status"] == "verified" else 1
    except Exception as error:
        print(json.dumps({"question": question, "error": str(error)}, ensure_ascii=False, indent=2))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
