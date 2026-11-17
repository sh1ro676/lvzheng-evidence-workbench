"""Independently verify professor commentary files, metadata and article links."""
from __future__ import annotations

import argparse
import hashlib
import json
import zipfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_ROOT = Path(r"E:\lvzheng-data")


def sha(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def load(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    args = parser.parse_args()
    root = args.root.resolve()
    index = load(root / "manifest" / "professor-index.json")
    items = index["items"]
    errors = []
    warnings = []

    def error(item: str, detail: str) -> None:
        errors.append({"item": item, "detail": detail})

    ids = [item.get("document_id") for item in items]
    slugs = [item.get("slug") for item in items]
    urls = [item.get("source_url") for item in items]
    text_hashes = [item.get("text_sha256") for item in items]
    for label, values in [["document_id", ids], ["slug", slugs], ["source_url", urls], ["text_sha256", text_hashes]]:
        duplicates = [value for value, count in Counter(values).items() if count > 1]
        if duplicates:
            error(label, f"重复：{duplicates}")

    legal_documents = {}
    for path in (root / "normalized" / "documents").glob("*.json"):
        document = load(path)
        legal_documents[document["document_id"]] = document

    reference_count = 0
    linked_count = 0
    for item in items:
        slug = item["slug"]
        normalized_path = Path(item["normalized_path"])
        raw_path = Path(item["raw_snapshot"])
        text_path = Path(item["text_path"])
        try:
            normalized = load(normalized_path)
        except (OSError, json.JSONDecodeError) as exc:
            error(slug, f"规范化 JSON 不可读：{exc}")
            continue
        if not normalized.get("authors"):
            error(slug, "作者为空")
        if not normalized.get("affiliation_evidence"):
            error(slug, "缺少作者身份依据")
        for evidence in normalized.get("affiliation_evidence", []):
            if not evidence.get("url"):
                error(slug, f"作者 {evidence.get('author')} 的身份依据 URL 为空")
        if "书目" in str(normalized.get("source_type")):
            error(slug, "纯书目被计入讲评全文")
        if "不代表学校或司法机关统一立场" not in normalized.get("usage_notice", ""):
            error(slug, "缺少作者个人观点边界声明")

        if not raw_path.is_file():
            error(slug, f"原始文件不存在：{raw_path}")
        else:
            raw = raw_path.read_bytes()
            if sha(raw) != item.get("raw_sha256"):
                error(slug, "原始文件 SHA-256 不一致")
            if len(raw) != item.get("raw_bytes"):
                error(slug, "原始文件字节数不一致")
            suffix = raw_path.suffix.lower()
            if suffix == ".pdf" and not raw.startswith(b"%PDF-"):
                error(slug, "PDF 文件头无效")
            if suffix == ".docx":
                try:
                    with zipfile.ZipFile(raw_path) as archive:
                        if "word/document.xml" not in archive.namelist():
                            error(slug, "DOCX 缺少正文 XML")
                except zipfile.BadZipFile:
                    error(slug, "DOCX ZIP 无效")
            if suffix in {".html", ".htm", ".md"} and b"Please enable JavaScript and refresh" in raw:
                error(slug, "原始文件为 JavaScript 验证页")

        if not text_path.is_file():
            error(slug, f"正文文件不存在：{text_path}")
            continue
        try:
            text = text_path.read_text(encoding="utf-8-sig").strip()
        except UnicodeDecodeError as exc:
            error(slug, f"正文不是有效 UTF-8：{exc}")
            continue
        if sha(text.encode("utf-8")) != item.get("text_sha256"):
            error(slug, "正文 SHA-256 不一致")
        if len(text) != item.get("text_chars"):
            error(slug, f"正文字符数不一致：声明 {item.get('text_chars')}，实际 {len(text)}")
        if len(text) < 400:
            error(slug, "正文不足 400 字")
        if normalized.get("text", "").strip() != text:
            error(slug, "JSON 内正文与独立正文文件不一致")

        references = normalized.get("legal_references", [])
        if normalized.get("related_articles") != [ref.get("reference") for ref in references]:
            error(slug, "related_articles 与 legal_references 不一致")
        for reference in references:
            reference_count += 1
            offset = reference.get("text_offset")
            matched_text = reference.get("matched_text", "")
            if not isinstance(offset, int) or text[offset:offset + len(matched_text)] != matched_text:
                error(slug, f"法条引用原文定位失败：{reference.get('reference')}")
            if reference.get("matched_current_law"):
                linked_count += 1
                document = legal_documents.get(reference.get("document_id"))
                if not document or document.get("status") != "有效":
                    error(slug, f"法条链接未指向现行文档：{reference.get('reference')}")
                    continue
                article_id = reference.get("article_id")
                base_label = reference.get("base_article_label")
                matches = [article for article in document.get("articles", [])
                           if article.get("article_id") == article_id and article.get("article_label") == base_label]
                if len(matches) != 1:
                    error(slug, f"法条 ID 或条号不一致：{reference.get('reference')}")

    actual_files = list((root / "normalized" / "professors").glob("*/*.json"))
    if len(actual_files) != len(items):
        error("document_count", f"索引 {len(items)}，实际 JSON {len(actual_files)}")
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "root": str(root), "network_used": False,
        "integrity_passed": not errors, "document_count": len(items),
        "by_institution": dict(Counter(item["institution"] for item in items)),
        "by_topic": dict(Counter(topic for item in items for topic in item["topics"])),
        "unique_author_count": len({author for item in items for author in item["authors"]}),
        "total_raw_bytes": sum(item["raw_bytes"] for item in items),
        "total_text_chars": sum(item["text_chars"] for item in items),
        "legal_reference_count": reference_count,
        "current_article_link_count": linked_count,
        "unlinked_reference_count": reference_count - linked_count,
        "errors": errors, "warnings": warnings,
        "scope_note": "完整性和链接校验通过不等于观点覆盖全量；未联网重新判断作者当前任职状态。",
    }
    output = root / "manifest" / "professor-independent-verification.json"
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
