"""在国家法律法规数据库里找刑法的最新版本记录（bbbs）。"""
from __future__ import annotations

import json

import requests

BASE = "https://flk.npc.gov.cn"
HEADERS = {"User-Agent": "Mozilla/5.0", "Content-Type": "application/json"}


def try_endpoint(path: str, payload: dict) -> None:
    url = BASE + path
    for method in ("post", "get"):
        try:
            response = getattr(requests, method)(url, json=payload if method == "post" else None,
                                                 params=None if method == "post" else payload,
                                                 headers=HEADERS, timeout=30)
            text = response.text[:300]
            print(f"{method.upper()} {path} -> {response.status_code} {text[:200]}")
        except Exception as error:  # noqa: BLE001
            print(f"{method.upper()} {path} -> {type(error).__name__}: {str(error)[:80]}")


def main() -> None:
    payload = {"searchValue": "刑法", "pageNum": 1, "pageSize": 10}
    for path in ("/law-search/search/list", "/law-search/search/flfgList",
                 "/law-search/search", "/api/search/list"):
        try_endpoint(path, payload)


if __name__ == "__main__":
    main()
