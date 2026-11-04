"""Ingest an official electronic text from 国家法律法规数据库.

Usage: python backend/ingest_flk.py
The source snapshot is retained so every article can be traced back to the exact
bytes used to build the local index.
"""
from __future__ import annotations

import base64
import hashlib
import json
import re
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
SOURCE_DIR = DATA_DIR / "sources"
DOCUMENT_DIR = DATA_DIR / "documents"
BASE_URL = "https://flk.npc.gov.cn"
CIVIL_CODE_ID = "ff808081729d1efe01729d50b5c500bf"
STATUS = {1: "已废止", 2: "已修改", 3: "有效", 4: "尚未生效"}
ARTICLE_RE = re.compile(
    r"^(第[〇零一二三四五六七八九十百千万两0-9]+条(?:之[〇零一二三四五六七八九十0-9]+)?)[\s　]*(.*)$"
)
HEADING_RE = re.compile(r"^(第[〇零一二三四五六七八九十百千万两0-9]+(?:编|分编|章|节)|附\s*则)")
W_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def get_bytes(url: str) -> bytes:
    response = requests.get(url, timeout=60)
    response.raise_for_status()
    return response.content


def get_json(url: str, params: dict[str, str]) -> dict:
    target = f"{url}?{urllib.parse.urlencode(params)}"
    return json.loads(get_bytes(target).decode("utf-8"))


def official_detail_url(bbbs: str) -> str:
    encoded = base64.b64encode(bbbs.encode("ascii")).decode("ascii")
    return f"{BASE_URL}/detail2.html?{urllib.parse.quote(encoded)}"


def flatten_toc(node: dict, parents: list[str], output: dict[str, list[str]]) -> None:
    title = str(node.get("title", "")).strip()
    if ARTICLE_RE.match(title):
        output[title] = parents
        return
    next_parents = [*parents, title] if title and node.get("index", 0) > 2 else parents
    for child in node.get("children", []):
        flatten_toc(child, next_parents, output)


def extract_docx_paragraphs(content: bytes) -> list[str]:
    archive_path = SOURCE_DIR / "_current.docx"
    archive_path.write_bytes(content)
    try:
        import zipfile

        with zipfile.ZipFile(archive_path) as archive:
            xml_bytes = archive.read("word/document.xml")
    finally:
        archive_path.unlink(missing_ok=True)
    root = ET.fromstring(xml_bytes)
    body = root.find(f"{W_NS}body")
    if body is None:
        raise ValueError("DOCX 缺少正文")
    paragraphs: list[str] = []
    for paragraph in body.iter(f"{W_NS}p"):
        parts = [node.text or "" for node in paragraph.iter(f"{W_NS}t")]
        text = "".join(parts).replace("\xa0", " ").strip()
        if text:
            paragraphs.append(text)
    return paragraphs


def parse_articles(paragraphs: list[str], toc_paths: dict[str, list[str]]) -> list[dict]:
    articles: list[dict] = []
    current: dict | None = None

    def flush() -> None:
        nonlocal current
        if current is not None:
            current["text"] = "\n".join(current.pop("paragraphs")).strip()
            current["content_hash"] = hashlib.sha256(
                f"{current['article_label']}\n{current['text']}".encode("utf-8")
            ).hexdigest()
            articles.append(current)
            current = None

    for paragraph in paragraphs:
        compact = paragraph.replace(" ", "").replace("　", "")
        match = ARTICLE_RE.match(compact)
        if match:
            flush()
            label, remainder = match.groups()
            current = {
                "article_id": f"article-{len(articles) + 1}",
                "article_label": label,
                "section_path": toc_paths.get(label, []),
                "paragraphs": [remainder] if remainder else [],
            }
            continue
        if HEADING_RE.match(compact):
            flush()
            continue
        if current is not None:
            current["paragraphs"].append(paragraph)
    flush()

    expected = sum(1 for label in toc_paths if ARTICLE_RE.match(label))
    if len(articles) != expected:
        raise ValueError(f"条文数量校验失败：DOCX={len(articles)}，官方目录={expected}")
    for index, article in enumerate(articles, start=1):
        article["article_id"] = f"article-{index}"
        if not article["text"]:
            raise ValueError(f"{article['article_label']} 正文为空")
    return articles


def ingest(bbbs: str = CIVIL_CODE_ID, document_id: str = "civil-code") -> dict:
    SOURCE_DIR.mkdir(parents=True, exist_ok=True)
    DOCUMENT_DIR.mkdir(parents=True, exist_ok=True)
    detail_response = get_json(f"{BASE_URL}/law-search/search/flfgDetails", {"bbbs": bbbs})
    if detail_response.get("code") != 200:
        raise RuntimeError(f"官方详情接口返回错误：{detail_response}")
    detail = detail_response["data"]
    download_response = get_json(
        f"{BASE_URL}/law-search/download/pc", {"format": "docx", "bbbs": bbbs, "fileId": ""}
    )
    if download_response.get("code") != 200:
        raise RuntimeError(f"官方下载接口返回错误：{download_response}")
    download_url = download_response["data"]["url"]
    docx_bytes = get_bytes(download_url)
    if not docx_bytes.startswith(b"PK"):
        raise ValueError("下载内容不是有效 DOCX")

    snapshot_hash = hashlib.sha256(docx_bytes).hexdigest()
    snapshot_path = SOURCE_DIR / f"{document_id}-{snapshot_hash[:12]}.docx"
    snapshot_path.write_bytes(docx_bytes)
    toc_paths: dict[str, list[str]] = {}
    flatten_toc(detail["content"], [], toc_paths)
    articles = parse_articles(extract_docx_paragraphs(docx_bytes), toc_paths)
    document = {
        "document_id": document_id,
        "bbbs": bbbs,
        "title": detail["title"],
        "category": detail["flxz"],
        "issuing_authority": detail["zdjgName"],
        "published_at": detail["gbrq"],
        "effective_at": detail["sxrq"],
        "status_code": detail["sxx"],
        "status": STATUS.get(detail["sxx"], "未知"),
        "source_name": "国家法律法规数据库",
        "source_url": official_detail_url(bbbs),
        "source_snapshot": str(snapshot_path.relative_to(ROOT)),
        "snapshot_sha256": snapshot_hash,
        "ingested_at": datetime.now(timezone.utc).isoformat(),
        "article_count": len(articles),
        "source_notice": "国家法律法规数据库提供的电子文本与法律规定的标准文本不一致时，使用时请引用标准文本。",
        "articles": articles,
    }
    output_path = DOCUMENT_DIR / f"{document_id}.json"
    output_path.write_text(json.dumps(document, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in document.items() if k != "articles"}, ensure_ascii=False, indent=2))
    return document


if __name__ == "__main__":
    ingest()
