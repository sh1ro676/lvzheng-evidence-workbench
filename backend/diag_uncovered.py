"""定位哪一段没有标注证据。"""
from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import server  # noqa: E402
from deepseek_client import SECTION_HEADING_RE, verify_answer  # noqa: E402

QUESTION = "为我讲解北大杀母案"


def main() -> None:
    plan = server.route_question(QUESTION)
    evidence, _ = server.evidence_by_plan(QUESTION, plan)
    numbered = server.numbered_evidence(evidence)
    answer = server.DEEPSEEK.generate(QUESTION, numbered)
    report = verify_answer(answer, numbered)
    for check in report["checks"]:
        print(("OK  " if check["passed"] else "FAIL"), check["name"], "—", check["detail"][:80])

    print("\n未标注证据的段落：")
    for raw in re.split(r"\n+", answer):
        line = raw.strip()
        plain = line.strip("*").strip()
        if len(plain) < 12:
            continue
        if SECTION_HEADING_RE.fullmatch(plain):
            continue
        if re.search(r"证据(?:不足|缺(?:失|少)|中(?:没有|未|均未)|未(?:提供|涉及|包含|载明))", plain):
            continue
        if not re.search(r"【证据\s*\d+\s*】", plain):
            print("  →", plain[:110])


if __name__ == "__main__":
    main()
