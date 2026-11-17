"""Independently verify the local legal evidence corpus without network access.

Usage: python backend/verify_corpus.py
The append-only download log is deliberately not used as evidence of coverage.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import zipfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

BACKEND = Path(__file__).resolve().parent
DEFAULT_ROOT = Path(r"E:\lvzheng-data")
DOC_MAGIC = bytes.fromhex("D0CF11E0A1B11AE1")
CORE_EXPECTED = {
    "中华人民共和国民法典": 1260,
    "中华人民共和国刑法": 505,
    "中华人民共和国民事诉讼法": 306,
    "中华人民共和国刑事诉讼法": 308,
    "最高人民法院关于适用《中华人民共和国民事诉讼法》的解释": 550,
    "最高人民法院关于适用《中华人民共和国刑事诉讼法》的解释": 655,
}


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def digest(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def compact_title(value: str) -> str:
    return re.sub(r"\s+", "", value or "")


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".part")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def load_json(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--seeds", type=Path, default=BACKEND / "corpus_seeds.json")
    args = parser.parse_args()
    root = args.root.resolve()
    errors: list[dict] = []
    review_items: list[dict] = []

    def error(kind: str, item: str, detail: str) -> None:
        errors.append({"kind": kind, "item": item, "detail": detail})

    documents: dict[str, dict] = {}
    for path in sorted((root / "normalized" / "documents").glob("*.json")):
        try:
            document = load_json(path)
        except (OSError, json.JSONDecodeError) as exc:
            error("normalized_json", str(path), str(exc))
            continue
        document_id = str(document.get("document_id") or path.stem)
        documents[document_id] = document
        articles = document.get("articles")
        if not isinstance(articles, list):
            error("articles", document_id, "articles 不是数组")
            articles = []
        if document.get("article_count") != len(articles):
            error("article_count", document_id,
                  f"声明 {document.get('article_count')}，实际 {len(articles)}")
        article_ids = [a.get("article_id") for a in articles]
        if len(article_ids) != len(set(article_ids)):
            error("article_id", document_id, "条文 ID 不唯一")
        for article in articles:
            if not str(article.get("text") or "").strip():
                error("empty_article", document_id, str(article.get("article_label")))
                continue
            expected_hash = digest(f"{article.get('article_label')}\n{article['text']}".encode("utf-8"))
            if article.get("content_hash") != expected_hash:
                error("article_hash", document_id, str(article.get("article_label")))

        snapshot_value = document.get("source_snapshot")
        snapshot = Path(snapshot_value) if snapshot_value else None
        if snapshot is None or not snapshot.is_file():
            error("missing_source", document_id, str(snapshot_value))
        else:
            content = snapshot.read_bytes()
            if document.get("snapshot_sha256") != digest(content):
                error("source_hash", document_id, str(snapshot))
            if snapshot.suffix.lower() == ".docx":
                try:
                    with zipfile.ZipFile(snapshot) as archive:
                        if "word/document.xml" not in archive.namelist():
                            raise ValueError("缺少 word/document.xml")
                        damaged = archive.testzip()
                        if damaged:
                            raise ValueError(f"ZIP 成员损坏：{damaged}")
                except (zipfile.BadZipFile, OSError, ValueError) as exc:
                    error("invalid_docx", document_id, str(exc))
            elif snapshot.suffix.lower() == ".doc":
                if not content.startswith(DOC_MAGIC):
                    error("invalid_doc", document_id, "缺少 OLE DOC 文件头")
            else:
                error("source_format", document_id, snapshot.suffix)

        detail_path = root / "raw" / "npc" / "details" / f"{document_id}.json"
        try:
            detail = load_json(detail_path)
            if (detail.get("data") or {}).get("bbbs") != document_id:
                error("detail_id", document_id, "官方详情 ID 不一致")
        except (OSError, json.JSONDecodeError, AttributeError) as exc:
            error("detail_snapshot", document_id, str(exc))

        if document.get("parse_status") == "source_retained":
            review_items.append({
                "document_id": document_id,
                "title": document.get("title"),
                "reason": document.get("parse_error"),
                "source_snapshot": snapshot_value,
            })

    core_results = []
    compact_documents = [(compact_title(d.get("title", "")), d) for d in documents.values()]
    for title, expected_count in CORE_EXPECTED.items():
        candidates = [d for compact, d in compact_documents
                      if compact == compact_title(title) and d.get("status") == "有效"]
        actual_counts = [d.get("article_count") for d in candidates]
        passed = len(candidates) == 1 and actual_counts == [expected_count]
        if not passed:
            error("core_document", title,
                  f"现行版本数={len(candidates)}，条数={actual_counts}，预期={expected_count}")
        core_results.append({
            "title": title, "expected_articles": expected_count,
            "actual_articles": actual_counts, "current_version_count": len(candidates),
            "passed": passed,
        })

    def coverage(index_name: str) -> dict:
        index_path = root / "manifest" / index_name
        index = load_json(index_path)
        expected = {row["bbbs"]: row for row in index["documents"]}
        missing = [row for bbbs, row in expected.items() if bbbs not in documents]
        by_category = {}
        for row in index["documents"]:
            code = str(row.get("flfgCodeId", "other"))
            bucket = by_category.setdefault(code, {"expected": 0, "saved": 0, "missing": 0})
            bucket["expected"] += 1
            if row["bbbs"] in documents:
                bucket["saved"] += 1
            else:
                bucket["missing"] += 1
        return {
            "expected": len(expected), "saved": len(expected) - len(missing),
            "missing": len(missing), "by_category": by_category,
            "missing_documents": [
                {"bbbs": row["bbbs"], "title": row.get("title"),
                 "flfgCodeId": row.get("flfgCodeId")} for row in missing
            ],
        }

    law_coverage = coverage("flk-index.json")
    judicial_coverage = coverage("judicial-index.json")

    seeds = load_json(args.seeds)
    pages = []
    for path in sorted((root / "normalized" / "pages").glob("*.json")):
        page = load_json(path)
        pages.append(page)
        snapshot_value = page.get("raw_snapshot")
        snapshot = Path(snapshot_value) if snapshot_value else None
        if snapshot is None or not snapshot.is_file():
            error("missing_page_snapshot", str(page.get("slug")), str(snapshot_value))
        else:
            content = snapshot.read_bytes()
            if page.get("raw_html_retained") is False:
                if snapshot.suffix.lower() != ".md" or page.get("snapshot_format") != "extracted_markdown":
                    error("extracted_snapshot", str(page.get("slug")), "正文提取页未明确标记 Markdown 快照")
            elif snapshot.suffix.lower() != ".html":
                error("html_snapshot", str(page.get("slug")), "完整网页应保留 HTML")
            if page.get("raw_sha256") and page["raw_sha256"] != digest(content):
                error("page_hash", str(page.get("slug")), str(snapshot))
        if len(str(page.get("text") or "").strip()) < 100:
            error("page_text", str(page.get("slug")), "正文不足 100 字")

    page_by_url = {page.get("source_url"): page for page in pages}
    for seed in seeds:
        page = page_by_url.get(seed["url"])
        if page is None:
            error("missing_seed", seed["slug"], seed["url"])
            continue
        url_digest = hashlib.sha256(seed["url"].encode("utf-8")).hexdigest()[:12]
        expected_name = f"{seed['slug']}-{url_digest}.json"
        actual_name = Path(page.get("raw_snapshot", "")).name
        if actual_name and not actual_name.startswith(f"{seed['slug']}-{url_digest}."):
            error("seed_snapshot_name", seed["slug"], actual_name)
        normalized_name = f"{seed['slug']}-{url_digest}.json"
        if not (root / "normalized" / "pages" / normalized_name).is_file():
            error("seed_normalized_name", seed["slug"], normalized_name)

    if len(pages) != len(seeds):
        error("page_count", "pages", f"种子={len(seeds)}，页面={len(pages)}")

    files = [p for p in root.rglob("*") if p.is_file() and ".part" not in p.suffixes]
    old_docs = [d for d in documents.values() if str(d.get("source_snapshot", "")).lower().endswith(".doc")]
    extracted_pages = [p for p in pages if p.get("raw_html_retained") is False]
    coverage_complete = law_coverage["missing"] == 0 and judicial_coverage["missing"] == 0
    verification = {
        "generated_at": now(), "root": str(root), "network_used": False,
        "integrity_passed": not errors, "coverage_complete": coverage_complete,
        "status": ("passed_with_review_items" if coverage_complete and not errors
                   else "coverage_incomplete" if not errors else "integrity_failed"),
        "counts": {
            "documents": len(documents),
            "parsed": sum(d.get("parse_status") == "parsed" for d in documents.values()),
            "source_retained": sum(d.get("parse_status") == "source_retained" for d in documents.values()),
            "articles": sum(len(d.get("articles") or []) for d in documents.values()),
            "zero_article_documents": sum(not d.get("articles") for d in documents.values()),
            "legacy_doc": len(old_docs),
            "legacy_doc_parsed": sum(d.get("parse_status") == "parsed" for d in old_docs),
            "pages": len(pages), "extracted_markdown_pages": len(extracted_pages),
        },
        "core_documents": core_results,
        "law_title_search_coverage": law_coverage,
        "judicial_interpretation_coverage": judicial_coverage,
        "errors": errors, "review_items": review_items,
        "limitations": [
            "零条文不自动等于解析失败：修正案、批复、通知和目录类文件可能没有独立条号。",
            "source_retained 文件保留原始证据，未绕过官方目录与正文条号冲突。",
            "公开教授讲评与案例资料不是全部学术观点或全部两高案例的完整数据库。",
            "正文提取快照只保留提取文本，不声称保留完整 HTML。",
            "未下载课程视频，未绕过登录、付费、反爬或 JavaScript 验证。",
        ],
    }
    write_json(root / "manifest" / "verification.json", verification)

    professor_summary = None
    professor_index_path = root / "manifest" / "professor-index.json"
    if professor_index_path.is_file():
        professor_index = load_json(professor_index_path)
        professor_summary = {
            "document_count": professor_index["document_count"],
            "by_institution": professor_index["by_institution"],
            "by_topic": professor_index["by_topic"],
            "unique_author_count": len(professor_index["unique_authors"]),
            "legal_reference_count": professor_index["legal_reference_count"],
            "current_article_link_count": professor_index["current_article_link_count"],
            "index": str(professor_index_path),
            "independent_verification": str(root / "manifest" / "professor-independent-verification.json"),
        }

    summary = {
        "generated_at": now(), "root": str(root),
        "actual_file_count": len(files),
        "actual_bytes": sum(p.stat().st_size for p in files),
        "actual_mib": round(sum(p.stat().st_size for p in files) / 1024 / 1024, 2),
        "documents": verification["counts"],
        "professor_commentaries": professor_summary,
        "core_documents": core_results,
        "law_title_search": {k: v for k, v in law_coverage.items() if k != "missing_documents"},
        "judicial_interpretations": {k: v for k, v in judicial_coverage.items() if k != "missing_documents"},
        "pages_by_folder": dict(Counter(p.get("folder") for p in pages)),
        "university_pages_by_institution": dict(Counter(
            p.get("institution") for p in pages if p.get("folder") == "universities")),
        "pages_by_source_type": dict(Counter(p.get("source_type") for p in pages)),
        "integrity_passed": not errors, "coverage_complete": coverage_complete,
        "review_item_count": len(review_items),
        "verification_report": str(root / "manifest" / "verification.json"),
        "notes": verification["limitations"],
    }
    write_json(root / "manifest" / "collection-summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"完整性错误：{len(errors)}；待人工复核：{len(review_items)}")


if __name__ == "__main__":
    main()
