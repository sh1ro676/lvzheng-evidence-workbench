"""验证网页正文缓存：同一网址第二次应命中本地缓存。"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import server  # noqa: E402

URL = "https://www.xsyk021.com/article/id/96cb1eaf-12e6-4b3c-b021-9df82ef87bd5"


def main() -> None:
    for attempt in (1, 2):
        start = time.time()
        result = server.WEB.fetch(URL)
        print(f"第 {attempt} 次：{time.time() - start:5.2f}s  正文 {len(result['content'])} 字")


if __name__ == "__main__":
    main()
