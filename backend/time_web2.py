"""拆解全网检索耗时：规划 / 搜索 / 抓取各占多少。"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import server  # noqa: E402

QUESTION = "北大弑母案 吴谢宇 判决结果"


def main() -> None:
    info = {"queries": [], "search_count": 0, "candidate_count": 0,
            "fetch_attempt_count": 0, "content_fetched_count": 0,
            "rejected_count": 0, "events": []}

    start = time.time()
    queries = server.plan_queries(QUESTION, info)
    print(f"规划   {time.time()-start:5.1f}s  {queries}")

    start = time.time()
    candidates = server.search_candidates(QUESTION, queries, info, set())
    print(f"搜索   {time.time()-start:5.1f}s  候选 {len(candidates)}")

    start = time.time()
    evidence = server.fetch_candidates(candidates, info, question=QUESTION)
    print(f"抓取   {time.time()-start:5.1f}s  成功 {len(evidence)}")


if __name__ == "__main__":
    main()
