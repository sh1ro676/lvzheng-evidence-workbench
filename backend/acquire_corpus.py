"""Download public legal source snapshots to a resumable local corpus.

Examples:
    python backend/acquire_corpus.py --stage laws
    python backend/acquire_corpus.py --stage pages
    python backend/acquire_corpus.py --stage report

No credentials, paywall bypass, or video download is used.
"""
from __future__ import annotations

import argparse
import hashlib
import html
import io
import json
import re
import shutil
import subprocess
import time
import xml.etree.ElementTree as ET
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

from ingest_flk import STATUS, ARTICLE_RE, flatten_toc, official_detail_url, parse_articles

BACKEND = Path(__file__).resolve().parent
DEFAULT_ROOT = Path(r"E:\lvzheng-data")
FLK = "https://flk.npc.gov.cn"
W_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
LAW_QUERIES = {
    "civil-code": "中华人民共和国民法典",
    "criminal-law": "中华人民共和国刑法",
    "civil-procedure-law": "中华人民共和国民事诉讼法",
    "criminal-procedure-law": "中华人民共和国刑事诉讼法",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def clean_title(value: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", value or "")).strip()


def atomic_write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".part")
    temporary.write_bytes(content)
    temporary.replace(path)


class Corpus:
    def __init__(self, root: Path, delay: float = 0.35):
        self.root = root
        self.delay = delay
        self.manifest_path = root / "manifest" / "downloads.jsonl"
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "LvzhengEvidenceWorkbench/0.1 (internal legal research; public sources)",
            "Accept-Language": "zh-CN,zh;q=0.9",
        })
        for directory in [
            "raw/npc/details", "raw/npc/docx", "raw/npc/searches",
            "raw/court", "raw/spp", "raw/universities",
            "normalized/documents", "normalized/pages", "manifest", "logs",
        ]:
            (root / directory).mkdir(parents=True, exist_ok=True)

    def record(self, **kwargs) -> None:
        entry = {"retrieved_at": utc_now(), **kwargs}
        self.manifest_path.parent.mkdir(parents=True, exist_ok=True)
        with self.manifest_path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def save(self, content: bytes, path: Path, *, url: str, title: str,
             source: str, status: str = "downloaded") -> dict:
        atomic_write(path, content)
        entry = {
            "url": url, "title": title, "source": source,
            "http_status": 200, "local_path": str(path),
            "content_type": "application/octet-stream", "bytes": len(content),
            "sha256": sha256(content), "status": status, "error": None,
        }
        self.record(**entry)
        return entry

    def get(self, url: str, *, params: dict | None = None, json_body: dict | None = None,
            referer: str | None = None, attempts: int = 3) -> tuple[bytes, requests.Response]:
        headers = {"Referer": referer} if referer else None
        # PKU's public site currently has an incomplete certificate chain on this machine.
        # This exception is host-specific and is never used for authenticated endpoints.
        verify_tls = urlparse(url).netloc != "www.law.pku.edu.cn"
        last_error: Exception | None = None
        for attempt in range(attempts):
            try:
                if json_body is None:
                    response = self.session.get(url, params=params, headers=headers,
                                                timeout=(15, 45), verify=verify_tls)
                else:
                    response = self.session.post(url, json=json_body, headers=headers,
                                                 timeout=(15, 45), verify=verify_tls)
                response.raise_for_status()
                if len(response.content) > 80 * 1024 * 1024:
                    raise ValueError("单个文件超过 80 MB 安全上限")
                time.sleep(self.delay)
                return response.content, response
            except (requests.RequestException, ValueError) as error:
                last_error = error
                if attempt + 1 < attempts:
                    time.sleep(2 ** attempt)
        raise RuntimeError(f"请求失败：{url}：{last_error}")

    def get_json(self, url: str, **kwargs) -> tuple[dict, bytes]:
        content, _ = self.get(url, **kwargs)
        return json.loads(content.decode("utf-8-sig")), content

    def search_laws(self, slug: str, query: str) -> list[dict]:
        payload = {
            "searchRange": 1, "sxrq": [], "gbrq": [], "sxx": [],
            "searchType": 1, "xgzlSearch": False, "searchContent": query,
            "gbrqYear": [], "flfgCodeId": [], "zdjgCodeId": [],
            "orderByParam": {"order": "-1", "sort": ""},
            "pageNum": 1, "pageSize": 100,
        }
        url = f"{FLK}/law-search/search/list"
        try:
            data, content = self.get_json(url, json_body=payload, referer=f"{FLK}/search")
            if data.get("code") != 200:
                raise RuntimeError(f"检索接口错误：{data.get('msg')}")
            self.save(content, self.root / "raw" / "npc" / "searches" / f"{slug}.json",
                      url=url, title=f"检索：{query}", source="国家法律法规数据库")
            rows = data.get("rows", [])
            print(f"检索 {query}：{len(rows)} / {data.get('total')} 条", flush=True)
            return rows
        except Exception as error:
            self.record(url=url, title=f"检索：{query}", source="国家法律法规数据库",
                        http_status=None, local_path=None, content_type="application/json",
                        bytes=0, sha256=None, status="failed", error=str(error))
            print(f"失败 {query}：{error}", flush=True)
            return []

    def download_law(self, row: dict) -> None:
        bbbs = row.get("bbbs")
        title = clean_title(row.get("title", ""))
        if not bbbs or not title:
            return
        detail_url = f"{FLK}/law-search/search/flfgDetails"
        download_api = f"{FLK}/law-search/download/pc"
        detail_path = self.root / "raw" / "npc" / "details" / f"{bbbs}.json"
        docx_path = self.root / "raw" / "npc" / "docx" / f"{bbbs}.docx"
        legacy_path = self.root / "raw" / "npc" / "docx" / f"{bbbs}.doc"
        normalized_path = self.root / "normalized" / "documents" / f"{bbbs}.json"
        if normalized_path.exists() and (docx_path.exists() or legacy_path.exists()):
            print(f"跳过已保存 {title}", flush=True)
            return
        try:
            detail_response, detail_bytes = self.get_json(
                detail_url, params={"bbbs": bbbs}, referer=f"{FLK}/"
            )
            detail = detail_response.get("data")
            if detail_response.get("code") != 200 or not detail:
                raise RuntimeError("官方详情为空")
            self.save(detail_bytes, detail_path, url=f"{detail_url}?bbbs={bbbs}",
                      title=title, source="国家法律法规数据库")
            download_response, _ = self.get_json(
                download_api, params={"format": "docx", "bbbs": bbbs, "fileId": ""},
                referer=f"{FLK}/"
            )
            download_url = (download_response.get("data") or {}).get("url")
            if not download_url:
                raise RuntimeError("官方下载地址为空")
            source_bytes, _ = self.get(download_url, referer=f"{FLK}/")
            is_docx = source_bytes.startswith(b"PK") and zipfile.is_zipfile(io.BytesIO(source_bytes))
            is_legacy_doc = source_bytes.startswith(bytes.fromhex("D0CF11E0A1B11AE1"))
            if not is_docx and not is_legacy_doc:
                raise ValueError("下载结果既不是 DOCX，也不是旧版 DOC")
            source_path = docx_path if is_docx else legacy_path
            self.save(source_bytes, source_path, url=download_url, title=title,
                      source="国家法律法规数据库")
            parse_error = None
            articles = []
            if is_docx:
                try:
                    articles = extract_articles(source_bytes, detail)
                except Exception as error:
                    parse_error = str(error)
            else:
                parse_error = "官方提供的是旧版 DOC；原始文件已保留，未伪装成 DOCX"
            document = {
                "document_id": bbbs, "bbbs": bbbs, "title": detail.get("title") or title,
                "category": detail.get("flxz") or row.get("flxz"),
                "issuing_authority": detail.get("zdjgName") or row.get("zdjgName"),
                "published_at": detail.get("gbrq") or row.get("gbrq"),
                "effective_at": detail.get("sxrq") or row.get("sxrq"),
                "status_code": detail.get("sxx") or row.get("sxx"),
                "status": STATUS.get(detail.get("sxx") or row.get("sxx"), "未标注"),
                "source_name": "国家法律法规数据库", "source_url": official_detail_url(bbbs),
                "source_snapshot": str(source_path), "snapshot_sha256": sha256(source_bytes),
                "ingested_at": utc_now(), "article_count": len(articles),
                "parse_status": "parsed" if not parse_error else "source_retained",
                "parse_error": parse_error, "articles": articles,
            }
            normalized_bytes = json.dumps(document, ensure_ascii=False, indent=2).encode("utf-8")
            self.save(normalized_bytes, normalized_path, url=official_detail_url(bbbs),
                      title=title, source="本地结构化", status="parsed" if not parse_error else "source_retained")
            print(f"已保存 {title}｜{document['published_at']}｜{document['status']}｜{len(articles)} 条", flush=True)
        except Exception as error:
            self.record(url=official_detail_url(bbbs), title=title, source="国家法律法规数据库",
                        http_status=None, local_path=str(docx_path), content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                        bytes=0, sha256=None, status="failed", error=str(error))
            print(f"失败 {title}：{error}", flush=True)

    def reparse_saved(self) -> None:
        for normalized_path in sorted((self.root / "normalized" / "documents").glob("*.json")):
            document = json.loads(normalized_path.read_text(encoding="utf-8"))
            docx_path = self.root / "raw" / "npc" / "docx" / f"{document['bbbs']}.docx"
            legacy_path = self.root / "raw" / "npc" / "docx" / f"{document['bbbs']}.doc"
            source_path = docx_path if docx_path.exists() else legacy_path
            detail_path = self.root / "raw" / "npc" / "details" / f"{document['bbbs']}.json"
            if not source_path.exists() or not detail_path.exists():
                continue
            if document.get("parse_status") == "parsed":
                continue
            detail = json.loads(detail_path.read_text(encoding="utf-8"))["data"]
            is_legacy_doc = source_path.suffix.lower() == ".doc"
            try:
                articles = extract_articles(
                    source_path.read_bytes(), detail,
                    legacy_doc=is_legacy_doc, source_path=source_path,
                )
                document.update(articles=articles, article_count=len(articles),
                                parse_status="parsed", parse_error=None, ingested_at=utc_now())
                if is_legacy_doc:
                    document["text_extraction"] = "antiword 0.37（原 DOC 保持不变）"
            except Exception as error:
                document.update(parse_status="source_retained", parse_error=str(error))
                articles = document.get("articles", [])
            self.save(json.dumps(document, ensure_ascii=False, indent=2).encode("utf-8"),
                      normalized_path, url=document["source_url"], title=document["title"],
                      source="本地结构化", status=document["parse_status"])
            print(f"重建索引 {document['title']}｜{len(articles)} 条｜{document['parse_status']}", flush=True)

    def acquire_laws(self) -> None:
        seen: dict[str, dict] = {}
        for slug, query in LAW_QUERIES.items():
            for row in self.search_laws(slug, query):
                if row.get("bbbs"):
                    seen[row["bbbs"]] = row
        index = {
            "created_at": utc_now(), "source": "国家法律法规数据库",
            "queries": LAW_QUERIES, "document_count": len(seen),
            "documents": [{**row, "title": clean_title(row.get("title", ""))} for row in seen.values()],
        }
        self.save(json.dumps(index, ensure_ascii=False, indent=2).encode("utf-8"),
                  self.root / "manifest" / "flk-index.json", url=f"{FLK}/search",
                  title="四部法律及相关版本、司法解释检索索引", source="本地索引")
        print(f"去重后准备下载 {len(seen)} 份官方文献", flush=True)
        for row in seen.values():
            self.download_law(row)

    def acquire_judicial_interpretations(self) -> None:
        categories = {320: "spc", 330: "spp", 340: "joint"}
        seen: dict[str, dict] = {}
        url = f"{FLK}/law-search/search/list"
        for code, slug in categories.items():
            page = 1
            while True:
                payload = {
                    "searchRange": 1, "sxrq": [], "gbrq": [], "sxx": [3],
                    "searchType": 1, "xgzlSearch": False, "searchContent": "",
                    "gbrqYear": [], "flfgCodeId": [code], "zdjgCodeId": [],
                    "orderByParam": {"order": "-1", "sort": ""},
                    "pageNum": page, "pageSize": 100,
                }
                data, content = self.get_json(url, json_body=payload, referer=f"{FLK}/search")
                if data.get("code") != 200:
                    raise RuntimeError(f"司法解释分类 {code} 检索失败：{data.get('msg')}")
                self.save(content, self.root / "raw" / "npc" / "searches" / f"judicial-{slug}-{page}.json",
                          url=url, title=f"现行司法解释分类 {code} 第 {page} 页", source="国家法律法规数据库")
                rows = data.get("rows", [])
                total = int(data.get("total", 0))
                for row in rows:
                    if row.get("bbbs"):
                        seen[row["bbbs"]] = row
                print(f"司法解释分类 {code} 第 {page} 页：{len(rows)} 条，共 {total} 条", flush=True)
                if page * 100 >= total or not rows:
                    break
                page += 1
        index = {
            "created_at": utc_now(), "source": "国家法律法规数据库", "status": "有效",
            "categories": categories, "document_count": len(seen),
            "documents": [{**row, "title": clean_title(row.get("title", ""))} for row in seen.values()],
        }
        self.save(json.dumps(index, ensure_ascii=False, indent=2).encode("utf-8"),
                  self.root / "manifest" / "judicial-index.json", url=f"{FLK}/search",
                  title="现行两高司法解释及联合解释索引", source="本地索引")
        print(f"去重后准备检查 {len(seen)} 份现行司法解释；已存在的会自动跳过", flush=True)
        for row in seen.values():
            self.download_law(row)

    def acquire_pages(self) -> None:
        seeds_path = BACKEND / "corpus_seeds.json"
        seeds = json.loads(seeds_path.read_text(encoding="utf-8"))
        for seed in seeds:
            url = seed["url"]
            host = urlparse(url).netloc
            folder = seed.get("folder", "universities")
            digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:12]
            stem = f"{seed['slug']}-{digest}"
            raw_path = self.root / "raw" / folder / f"{stem}.html"
            normalized_path = self.root / "normalized" / "pages" / f"{stem}.json"
            try:
                content, response = self.get(url, referer=f"{urlparse(url).scheme}://{host}/")
                try:
                    text = content.decode("utf-8-sig")
                except UnicodeDecodeError:
                    text = content.decode(response.apparent_encoding or "gb18030", errors="replace")
                soup = BeautifulSoup(text, "html.parser")
                for node in soup(["script", "style", "noscript"]):
                    node.decompose()
                page_title = soup.title.get_text(" ", strip=True) if soup.title else seed["title"]
                body = "\n".join(line.strip() for line in soup.get_text("\n").splitlines() if line.strip())
                if len(body) < 100:
                    raise ValueError("网页正文过短，未作为有效资料保存")
                self.save(content, raw_path, url=url, title=page_title, source=host)
                page = {**seed, "title": page_title, "retrieved_at": utc_now(),
                        "source_url": url, "raw_snapshot": str(raw_path),
                        "raw_sha256": sha256(content), "text": body,
                        "usage_notice": "仅保存公开网页快照；教授观点用于定位出处，不代表学校或司法机关统一立场。"}
                self.save(json.dumps(page, ensure_ascii=False, indent=2).encode("utf-8"),
                          normalized_path, url=url, title=page_title, source="本地网页结构化")
                print(f"已保存网页 {page_title}｜{len(body)} 字", flush=True)
            except Exception as error:
                self.record(url=url, title=seed["title"], source=host, http_status=None,
                            local_path=str(raw_path), content_type="text/html", bytes=0,
                            sha256=None, status="failed", error=str(error))
                print(f"失败网页 {seed['title']}：{error}", flush=True)

    def report(self) -> dict:
        entries = []
        if self.manifest_path.exists():
            for line in self.manifest_path.read_text(encoding="utf-8").splitlines():
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
        files = [p for p in self.root.rglob("*") if p.is_file() and ".part" not in p.suffixes]
        by_status: dict[str, int] = {}
        for entry in entries:
            by_status[entry["status"]] = by_status.get(entry["status"], 0) + 1
        failures = [e for e in entries if e["status"] == "failed"]
        report = {
            "generated_at": utc_now(), "root": str(self.root),
            "actual_file_count": len(files),
            "actual_bytes": sum(p.stat().st_size for p in files),
            "manifest_events": len(entries), "events_by_status": by_status,
            "latest_failures": failures[-100:],
            "notes": [
                "未下载课程视频。",
                "人民法院案例库完整接口需要登录，本任务未绕过访问控制。",
                "教材和论文仅采集公开或已授权内容；公开网页不代表全量学术观点覆盖。",
            ],
        }
        atomic_write(self.root / "manifest" / "report.json",
                     json.dumps(report, ensure_ascii=False, indent=2).encode("utf-8"))
        print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)
        return report


def docx_paragraphs(content: bytes) -> list[str]:
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        xml_bytes = archive.read("word/document.xml")
    root = ET.fromstring(xml_bytes)
    body = root.find(f"{W_NS}body")
    if body is None:
        raise ValueError("DOCX 缺少正文")
    paragraphs = []
    for paragraph in body.iter(f"{W_NS}p"):
        text = "".join(node.text or "" for node in paragraph.iter(f"{W_NS}t"))
        text = text.replace("\xa0", " ").strip()
        if text:
            paragraphs.append(text)
    return paragraphs


def legacy_doc_paragraphs(path: Path) -> list[str]:
    executable = shutil.which("antiword")
    if not executable:
        raise RuntimeError("未安装 antiword，无法提取旧版 DOC；原文件仍已保留")
    result = subprocess.run(
        [executable, "-m", "UTF-8.txt", "-w", "0", str(path)],
        capture_output=True,
        timeout=60,
        check=False,
    )
    if result.returncode != 0:
        error = result.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"antiword 提取失败：{error or result.returncode}")
    text = result.stdout.decode("utf-8").replace("\x0c", "\n").replace("\xa0", " ")
    paragraphs = [line.strip() for line in text.splitlines() if line.strip()]
    if not paragraphs:
        raise ValueError("旧版 DOC 未提取到正文")
    return paragraphs


def extract_articles(content: bytes, detail: dict, *, legacy_doc: bool = False,
                     source_path: Path | None = None) -> list[dict]:
    if legacy_doc:
        if source_path is None:
            raise ValueError("提取旧版 DOC 时必须提供文件路径")
        paragraphs = legacy_doc_paragraphs(source_path)
    else:
        paragraphs = docx_paragraphs(content)
    toc_paths: dict[str, list[str]] = {}
    flatten_toc(detail.get("content") or {}, [], toc_paths)
    if not any(ARTICLE_RE.match(label) for label in toc_paths):
        # Empty official TOC: build provisional article keys from official text.
        for paragraph in paragraphs:
            compact = paragraph.replace(" ", "").replace("　", "")
            match = ARTICLE_RE.match(compact)
            if match:
                toc_paths.setdefault(match.group(1), [])
    return parse_articles(paragraphs, toc_paths)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--stage", choices=["laws", "judicial", "reparse", "pages", "all", "report"], default="all")
    parser.add_argument("--delay", type=float, default=0.35)
    args = parser.parse_args()
    corpus = Corpus(args.root, max(0.2, args.delay))
    if args.stage in {"laws", "all"}:
        corpus.acquire_laws()
    if args.stage in {"judicial", "all"}:
        corpus.acquire_judicial_interpretations()
    if args.stage == "reparse":
        corpus.reparse_saved()
    if args.stage in {"pages", "all"}:
        corpus.acquire_pages()
    corpus.report()


if __name__ == "__main__":
    main()
