"""免费学术源适配器：中国民商法律网（civillaw.com.cn）。

为什么选它：四本目标刊（中外法学/清华法学/政法论坛/法律科学）的官网
均无可稳定抓取的免费全文（实测 404/504/域名不可解析），而知网、万方、
维普的全文在付费墙后。民商法律网免费、静态 HTML、结构稳定，提供
题录 + 作者单位 + 内容摘要 + 结构框架，足以支撑"按作者单位归属到四校"
和"带出作者本人写的核心表述"。

数据边界（必须如实展示给用户）：
- 本适配器拿到的是 **摘要级** 证据，不是论文正文；
- 摘要为作者原文，可逐字引用；结构框架是章节标题，不可当作观点引用；
- 证据需标注 `evidence_level = "abstract"`，界面不得显示为"全文"。

合规：仅抓公开列表页与文章页，限速 1 请求/秒，不镜像、不转载全文，
只保留题录与摘要片段用于检索与引用定位。
"""
from __future__ import annotations

import json
import re
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

BASE = "http://www.civillaw.com.cn"
UA = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36",
    "Accept-Language": "zh-CN,zh;q=0.9",
}

# 目标四校在摘要页的"作者单位"字段里通常直接出现校名，无需再做职称邻近推断。
INSTITUTION_KEYS = {
    "中国政法大学": ("中国政法大学", "中国政法"),
    "西北政法大学": ("西北政法大学", "西北政法"),
    "北京大学法学院": ("北京大学法学院", "北大法学院", "北京大学", "北大"),
    "清华大学法学院": ("清华大学法学院", "清华法学院", "清华大学", "清华"),
}


class CivillawError(RuntimeError):
    pass


@dataclass
class Article:
    article_id: str
    title: str
    journal: str = ""
    year: str = ""
    issue: str = ""
    abstract: str = ""
    tags: list[str] = field(default_factory=list)
    authors: str = ""
    affiliation: str = ""
    keywords: list[str] = field(default_factory=list)
    outline: list[str] = field(default_factory=list)
    url: str = ""

    def to_dict(self) -> dict:
        return {
            "article_id": self.article_id,
            "title": self.title,
            "journal": self.journal,
            "year": self.year,
            "issue": self.issue,
            "abstract": self.abstract,
            "tags": self.tags,
            "authors": self.authors,
            "affiliation": self.affiliation,
            "keywords": self.keywords,
            "outline": self.outline,
            "url": self.url,
            "evidence_level": "abstract",
        }


def _fetch(url: str, *, data: bytes | None = None, timeout: int = 30, retries: int = 3) -> str:
    headers = dict(UA)
    if data is not None:
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    last_error: Exception | None = None
    for attempt in range(retries):
        request = urllib.request.Request(url, data=data, headers=headers,
                                         method="POST" if data is not None else "GET")
        try:
            raw = urllib.request.urlopen(request, timeout=timeout).read()
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            time.sleep(1.5 * (attempt + 1))
            continue
        for encoding in ("utf-8", "gb18030", "gbk"):
            try:
                return raw.decode(encoding)
            except UnicodeDecodeError:
                continue
        return raw.decode("utf-8", "ignore")
    raise CivillawError(f"抓取失败：{type(last_error).__name__} {url}") from last_error


def _strip(text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).replace("&nbsp;", " ").strip()


def parse_list(html: str) -> list[Article]:
    """解析列表页。每条记录：刊名/期/年 + 标题 + 摘要 + 标签。"""
    items: list[Article] = []
    for match in re.finditer(r'href="/lw/t/\?id=(\d+)"[^>]*>(.*?)</a>', html, re.S):
        article_id = match.group(1)
        title = _strip(match.group(2))
        if not title:
            continue
        window = html[max(0, match.start() - 800):match.start()]
        head = window.rsplit("icon_left", 1)[-1] if "icon_left" in window else window
        h3 = re.findall(r"<h3>(.*?)</h3>", head, re.S)
        h4 = re.findall(r"<h4>(.*?)</h4>", head, re.S)
        h5 = re.findall(r"<h5>(.*?)</h5>", head, re.S)
        tail = html[match.end():match.end() + 2000]
        para = re.search(r"<p>(.*?)</p>", tail, re.S)
        tags = re.findall(r'#([^<#]{1,20})</a>', tail)
        items.append(Article(
            article_id=article_id,
            title=title,
            journal=_strip(h3[-1]) if h3 else "",
            issue=_strip(h4[-1]) if h4 else "",
            year=_strip(h5[-1]) if h5 else "",
            abstract=_strip(para.group(1)) if para else "",
            tags=[t.strip() for t in tags][:8],
            url=f"{BASE}/lw/t/?id={article_id}",
        ))
    return items


def parse_article(html: str, article: Article) -> Article:
    """解析文章页：作者+单位、刊名年期、内容摘要、关键词、结构框架。"""
    text = _strip(re.sub(r"<script.*?</script>", " ", html, flags=re.S))
    author = re.search(r"作者[:：]\s*([^\s，,]{2,8})\s+([^\s，,第]{2,20})", text)
    if author:
        article.authors = author.group(1).strip()
        article.affiliation = author.group(2).strip()
    source = re.search(r"《([^》]{2,20})》\s*(\d{4})\s*年\s*第?\s*(\d{1,2})\s*期", text)
    if source:
        article.journal = article.journal or source.group(1)
        article.year = article.year or source.group(2)
        article.issue = article.issue or source.group(3)
    abstract = re.search(r"内容摘要\s*(.{20,1500}?)\s*(?:###\s*)?关键词", text)
    if abstract:
        article.abstract = abstract.group(1).strip()
    keywords = re.search(r"关键词\s*(.{2,300}?)\s*(?:###\s*)?结构框架", text)
    if keywords:
        article.keywords = [k.strip() for k in re.split(r"[；;、,，]", keywords.group(1)) if k.strip()][:8]
    outline_block = re.search(r"结构框架\s*(.{0,2000}?)\s*(?:（实习编辑|相关文章|$)", text)
    if outline_block:
        article.outline = [
            line.strip() for line in re.split(r"(?=[一二三四五六七八九十]+、)", outline_block.group(1))
            if line.strip()
        ][:20]
    return article


def institutions_of(text: str) -> list[str]:
    hits = []
    for institution, markers in INSTITUTION_KEYS.items():
        if any(marker in text for marker in markers):
            hits.append(institution)
    return hits


class CivillawSource:
    def __init__(self, delay: float = 1.0, cache_dir: Path | None = None):
        self.delay = delay
        self.cache_dir = cache_dir
        self._last = 0.0

    def _wait(self) -> None:
        gap = time.time() - self._last
        if gap < self.delay:
            time.sleep(self.delay - gap)
        self._last = time.time()

    def list_page(self, page: int, pid: int = 1) -> list[Article]:
        self._wait()
        url = f"{BASE}/lw/?pid={pid}&page={page}"
        return parse_list(_fetch(url))

    def article(self, article: Article) -> Article:
        self._wait()
        try:
            html = _fetch(article.url)
        except CivillawError:
            return article
        return parse_article(html, article)

    def crawl(self, pages: int, pid: int = 1, enrich: bool = False) -> list[Article]:
        seen: dict[str, Article] = {}
        failed_pages = 0
        for page in range(1, pages + 1):
            try:
                items = self.list_page(page, pid)
            except CivillawError:
                failed_pages += 1
                continue
            for item in items:
                seen.setdefault(item.article_id, item)
        self.failed_pages = failed_pages
        articles = list(seen.values())
        if enrich:
            for item in articles:
                self.article(item)
        return articles

    def search(self, terms: list[str], articles: list[Article], limit: int = 20) -> list[Article]:
        """按主题词在标题/摘要/标签/大纲上做加权打分。"""
        scored = []
        for item in articles:
            haystack_title = item.title
            haystack_body = " ".join([item.abstract, " ".join(item.tags),
                                      " ".join(item.keywords), " ".join(item.outline)])
            score = 0.0
            for term in terms:
                if not term:
                    continue
                if term in haystack_title:
                    score += 3.0
                if term in haystack_body:
                    score += 1.0
            if score > 0:
                scored.append((score, item))
        scored.sort(key=lambda pair: pair[0], reverse=True)
        return [item for _, item in scored[:limit]]


def save_index(articles: list[Article], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for item in articles:
            handle.write(json.dumps(item.to_dict(), ensure_ascii=False) + "\n")


def load_index(path: Path) -> list[Article]:
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        rows.append(Article(
            article_id=row["article_id"], title=row["title"], journal=row.get("journal", ""),
            year=row.get("year", ""), issue=row.get("issue", ""), abstract=row.get("abstract", ""),
            tags=row.get("tags", []), authors=row.get("authors", ""),
            affiliation=row.get("affiliation", ""), keywords=row.get("keywords", []),
            outline=row.get("outline", []), url=row.get("url", ""),
        ))
    return rows


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="抓中国民商法律网题录")
    parser.add_argument("--pages", type=int, default=5)
    parser.add_argument("--pid", type=int, default=1, help="0=全部 1=民事法学 2=商事法学")
    parser.add_argument("--enrich", action="store_true", help="逐篇抓文章页补作者单位")
    parser.add_argument("--out", default="data/civillaw.jsonl")
    args = parser.parse_args()

    source = CivillawSource(delay=1.0)
    rows = source.crawl(args.pages, args.pid, enrich=args.enrich)
    save_index(rows, Path(args.out))
    with_affiliation = sum(1 for row in rows if row.affiliation)
    print(f"抓取 {len(rows)} 条，其中 {with_affiliation} 条已补作者单位 -> {args.out}")
