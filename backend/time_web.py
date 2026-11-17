"""计时：全网检索（案件类）与四校检索，改并发抓取前后对比用。"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import server  # noqa: E402


def run(question: str, force_targeted: bool) -> None:
    start = time.time()
    evidence, info = server.retrieve_web_evidence(question, True, force_targeted=force_targeted)
    elapsed = time.time() - start
    print(f"{'四校' if force_targeted else '全网'}｜{elapsed:5.1f}s｜搜索 {info['search_count']} 次"
          f"｜候选 {info.get('candidate_count')}｜抓取 {info.get('fetch_attempt_count')}"
          f"｜成功 {info.get('content_fetched_count')}｜证据 {len(evidence)}")


if __name__ == "__main__":
    run("北大弑母案 吴谢宇 判决结果", False)
    run("正当防卫的限度 教授 观点", True)
