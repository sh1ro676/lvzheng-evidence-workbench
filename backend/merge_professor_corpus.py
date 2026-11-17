"""Merge and deduplicate the four-school public professor commentary corpus.

Usage: python backend/merge_professor_corpus.py
Input staging files remain unchanged. Only public/authorized snapshots are imported.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

BACKEND = Path(__file__).resolve().parent
WORKSPACE = BACKEND.parent.parent
DEFAULT_ROOT = Path(r"E:\lvzheng-data")
CORE_TITLES = {
    "民法典": "中华人民共和国民法典",
    "刑法": "中华人民共和国刑法",
    "民事诉讼法": "中华人民共和国民事诉讼法",
    "刑事诉讼法": "中华人民共和国刑事诉讼法",
    "中华人民共和国民法典": "中华人民共和国民法典",
    "中华人民共和国刑法": "中华人民共和国刑法",
    "中华人民共和国民事诉讼法": "中华人民共和国民事诉讼法",
    "中华人民共和国刑事诉讼法": "中华人民共和国刑事诉讼法",
}
SCHOOLS = {
    "pku": "北京大学法学院",
    "tsinghua": "清华大学法学院",
    "cupl": "中国政法大学",
    "nwupl": "西北政法大学",
}
ARTICLE = r"第[〇零一二三四五六七八九十百千万两0-9]+条(?:之[〇零一二三四五六七八九十0-9]+)?"
SUFFIX = r"(?:第[〇零一二三四五六七八九十百千万两0-9]+[款项目]){0,3}"
QUOTED_REF = re.compile(rf"《(?P<title>[^》\n]{{2,160}})》(?P<article>{ARTICLE})(?P<suffix>{SUFFIX})")
CORE_REF = re.compile(
    rf"(?P<title>中华人民共和国民法典|中华人民共和国刑法|中华人民共和国民事诉讼法|"
    rf"中华人民共和国刑事诉讼法|民法典|刑法|民事诉讼法|刑事诉讼法)"
    rf"(?P<article>{ARTICLE})(?P<suffix>{SUFFIX})"
)


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def atomic_bytes(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".part")
    temporary.write_bytes(content)
    temporary.replace(path)


def atomic_json(path: Path, value: object) -> None:
    atomic_bytes(path, json.dumps(value, ensure_ascii=False, indent=2).encode("utf-8"))


def load(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))


def authors(value: list) -> list[str]:
    result = []
    for author in value:
        name = author.get("name") if isinstance(author, dict) else str(author)
        if name and name not in result:
            result.append(name)
    return result


def canonical_url(url: str) -> str:
    return (url or "").strip().split("#")[0].rstrip("/")


def normalized_text(path: Path) -> str:
    return path.read_text(encoding="utf-8-sig").replace("\r\n", "\n").replace("\r", "\n").strip()


def infer_topics(title: str, text: str, supplied: list[str]) -> list[str]:
    joined = " ".join(supplied)
    title_joined = f"{title} {joined}"
    topics = []
    rules = [
        ("中华人民共和国民法典", ["民法", "民商", "物权", "合同", "侵权", "婚姻", "私人关系", "债法", "监护"]),
        ("中华人民共和国刑事诉讼法", ["刑事诉讼", "刑诉", "刑事证据", "再审", "检察听证", "证人出庭", "少捕慎押"]),
        ("中华人民共和国民事诉讼法", ["民事诉讼", "民诉", "民事检察", "公益诉讼", "管辖"]),
        ("中华人民共和国刑法", ["刑法", "犯罪", "罪", "醉驾", "洗钱", "贿赂", "刑事归责"]),
    ]
    for canonical, keywords in rules:
        if any(word in title_joined for word in keywords):
            topics.append(canonical)
    if not topics:
        sample = text[:12000]
        scores = {
            canonical: sum(sample.count(word) for word in keywords)
            for canonical, keywords in rules
        }
        maximum = max(scores.values(), default=0)
        if maximum:
            topics = [name for name, score in scores.items() if score == maximum]
    return topics


def extract_references(text: str) -> list[dict]:
    found: dict[tuple[str, str], dict] = {}

    def add(match: re.Match, quoted: bool) -> None:
        raw_title = match.group("title").strip()
        canonical_title = CORE_TITLES.get(raw_title)
        if not canonical_title and raw_title.startswith("中华人民共和国") and raw_title[6:] in CORE_TITLES:
            canonical_title = CORE_TITLES[raw_title[6:]]
        is_judicial = any(word in raw_title for word in ["解释", "规定", "规则", "批复"])
        if not canonical_title and not (quoted and is_judicial):
            return
        law_title = canonical_title or raw_title
        article_label = match.group("article") + (match.group("suffix") or "")
        reference = f"《{law_title}》{article_label}"
        key = (law_title, article_label)
        if key in found:
            found[key]["occurrences"] += 1
            return
        found[key] = {
            "law_title": law_title, "article_label": article_label,
            "reference": reference, "matched_text": match.group(0),
            "text_offset": match.start(), "occurrences": 1,
            "document_id": None, "article_id": None, "matched_current_law": False,
        }

    for match in QUOTED_REF.finditer(text):
        add(match, True)
    for match in CORE_REF.finditer(text):
        add(match, False)
    return list(found.values())


def load_cupl_identities(path: Path) -> dict[str, str]:
    result = {}
    if not path.exists():
        return result
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.startswith("|") or "---" in line or "作者" in line:
            continue
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if len(cells) >= 3:
            urls = re.findall(r"https?://[^\s；）]+", cells[-1])
            if urls:
                result[cells[0]] = urls[0].rstrip("；;，,")
    return result


def staging_records(workspace: Path) -> list[dict]:
    records = []

    def add(*, school: str, slug: str, title: str, url: str, author_values: list,
            raw_path: Path, text_path: Path, topics: list[str], source_type: str,
            source_name: str, publication_date, rights_note: str,
            affiliation: list[dict], declared_hash: str | None = None,
            content_note: str | None = None, staging_manifest: str) -> None:
        records.append({
            "school": school, "slug": slug, "title": title, "source_url": url,
            "authors": authors(author_values), "raw_path": raw_path, "text_path": text_path,
            "supplied_topics": topics, "source_type": source_type, "source_name": source_name,
            "publication_date": publication_date, "rights_note": rights_note,
            "affiliation": affiliation, "declared_hash": declared_hash,
            "content_note": content_note, "staging_manifest": staging_manifest,
        })

    pku_root = workspace / "agent-imports" / "pku"
    for item in load(pku_root / "manifest.json"):
        author_list = item["authors"]
        add(school="pku", slug=f"pku-{item['slug']}", title=item["title"],
            url=item.get("source_url") or item["original_url"], author_values=author_list,
            raw_path=Path(item["raw_path"]), text_path=Path(item["text_path"]),
            topics=item.get("topics", []), source_type=item.get("work_type") or item.get("source_type"),
            source_name=item.get("original_publication") or item.get("source_name"),
            publication_date=item.get("publication_date"), rights_note=item["rights_note"],
            affiliation=[{"author": a["name"], "url": a.get("affiliation_evidence_url")} for a in author_list],
            declared_hash=item.get("raw_sha256"), content_note=item.get("author_affiliation_note"),
            staging_manifest=str(pku_root / "manifest.json"))

    tsinghua_root = workspace / "library"
    tsinghua_data = load(tsinghua_root / "library.json")
    for item in tsinghua_data["library"]:
        add(school="tsinghua", slug=f"tsinghua-{item['id']}", title=item["title"],
            url=item["source_url"], author_values=item["authors"],
            raw_path=Path(item["local_raw"]), text_path=Path(item["local_text"]),
            topics=[], source_type=item.get("source_type"), source_name=urlparse(item["source_url"]).netloc,
            publication_date=item.get("published_or_posted"),
            rights_note="公开网页或公开 PDF；权利归原作者及原出版方，未绕过访问控制。",
            affiliation=[{"author": name, "url": item.get("affiliation_evidence")} for name in item["authors"]],
            content_note=item.get("verification"), staging_manifest=str(tsinghua_root / "library.json"))

    cupl_root = workspace / "agent-imports" / "cupl"
    identities = load_cupl_identities(cupl_root / "author_identities.md")
    cupl_data = load(cupl_root / "manifest.json")
    for item in cupl_data["articles"]:
        add(school="cupl", slug=f"cupl-{item['id']}", title=item["title"],
            url=item["original_url"], author_values=item["authors"],
            raw_path=cupl_root / item["raw_path"], text_path=cupl_root / item["article_path"],
            topics=[item.get("discipline", "")], source_type=item.get("document_type"),
            source_name=item.get("source"), publication_date=item.get("published"),
            rights_note="公开全文检索整理；权利归原作者及原出版方，未获单独转载授权，未绕过访问控制。",
            affiliation=[{"author": name, "url": identities.get(name) or item["original_url"]} for name in item["authors"]],
            declared_hash=item.get("raw_sha256"), staging_manifest=str(cupl_root / "manifest.json"))

    nwupl_root = workspace / "agent-imports" / "nwupl"
    nwupl_data = load(nwupl_root / "manifest.json")
    for item in nwupl_data["items"]:
        add(school="nwupl", slug=f"nwupl-{item['slug']}", title=item["title"],
            url=item.get("source_url") or item["url"], author_values=item["authors"],
            raw_path=Path(item["local_path"]), text_path=Path(item["text_path"]),
            topics=item.get("topics", []), source_type=item.get("source_type"),
            source_name=item.get("source_name"), publication_date=item.get("publication_date"),
            rights_note=item.get("rights_note"),
            affiliation=[{"author": name, "url": item.get("affiliation_evidence_url")} for name in item["authors"]],
            declared_hash=item.get("sha256"), staging_manifest=str(nwupl_root / "manifest.json"))
    return records


def existing_seed_records(root: Path) -> list[dict]:
    records = []
    seed_titles = {item["slug"]: item["title"] for item in load(BACKEND / "corpus_seeds.json")}
    for path in sorted((root / "normalized" / "pages").glob("*.json")):
        item = load(path)
        if item.get("folder") != "universities" or not item.get("professors"):
            continue
        if "书目" in item.get("source_type", ""):
            continue
        text = item.get("text", "").strip()
        if len(text) < 400:
            continue
        institution = item.get("institution", "")
        school = next((key for key, value in SCHOOLS.items() if value.split("法学院")[0] in institution), None)
        if "北京大学" in institution:
            school = "pku"
        elif "清华大学" in institution:
            school = "tsinghua"
        elif "中国政法大学" in institution:
            school = "cupl"
        elif "西北政法大学" in institution:
            school = "nwupl"
        if not school:
            continue
        records.append({
            "school": school, "slug": item["slug"], "title": seed_titles.get(item["slug"], item["title"]),
            "source_url": item["source_url"], "authors": item["professors"],
            "raw_path": Path(item["raw_snapshot"]), "text_path": None, "text": text,
            "supplied_topics": item.get("topics", []), "source_type": item.get("source_type"),
            "source_name": item.get("institution"), "publication_date": None,
            "rights_note": item.get("usage_notice"),
            "affiliation": [{"author": name, "url": item["source_url"], "note": "原始文章署名页"} for name in item["professors"]],
            "declared_hash": item.get("raw_sha256"), "content_note": item.get("capture_scope"),
            "staging_manifest": str(path),
        })
    return records


def quality(record: dict) -> float:
    host = urlparse(record["source_url"]).netloc
    official = {
        "pku": "pku.edu.cn", "tsinghua": "tsinghua.edu.cn",
        "cupl": "cupl.edu.cn", "nwupl": "nwupl.edu.cn",
    }[record["school"]]
    return (100 if official in host else 0) + (20 if host.endswith("gov.cn") else 0) + min(record["text_chars"] / 1000, 50)


def duplicate_title(title: str) -> str:
    title = re.sub(r"^[^：:]{1,40}[：:]", "", title)
    return re.sub(r"[\s《》〈〉“”‘’'\"，,。．、—－\-（）()：:；;？?！!]", "", title).lower()


def chinese_number(value: str) -> int:
    if re.fullmatch(r"[0-9〇零]+", value):
        return int(value.replace("〇", "0").replace("零", "0"))
    digits = {"〇": 0, "零": 0, "一": 1, "二": 2, "两": 2, "三": 3,
              "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
    units = {"十": 10, "百": 100, "千": 1000, "万": 10000}
    total = section = number = 0
    for char in value:
        if char in digits:
            number = digits[char]
        elif char in units:
            unit = units[char]
            if unit == 10000:
                total += (section + number) * unit
                section = number = 0
            else:
                section += (number or 1) * unit
                number = 0
        else:
            raise ValueError(f"无法识别条号数字：{value}")
    return total + section + number


def article_key(label: str) -> str:
    match = re.match(r"^第([〇零一二三四五六七八九十百千万两0-9]+)条(?:之([〇零一二三四五六七八九十0-9]+))?", label)
    if not match:
        return label
    main = chinese_number(match.group(1))
    sub = chinese_number(match.group(2)) if match.group(2) else 0
    return f"{main}-{sub}"


def current_article_index(root: Path) -> tuple[dict, dict]:
    core = {}
    judicial = {}
    for path in (root / "normalized" / "documents").glob("*.json"):
        document = load(path)
        if document.get("status") != "有效":
            continue
        title = re.sub(r"\s+", "", document.get("title", ""))
        articles = {article_key(a["article_label"]): a["article_id"] for a in document.get("articles", [])}
        labels = {article_key(a["article_label"]): a["article_label"] for a in document.get("articles", [])}
        if title in CORE_TITLES.values():
            core[title] = {"document_id": document["document_id"], "articles": articles, "labels": labels}
        if any(word in title for word in ["解释", "规定", "规则", "批复"]):
            judicial.setdefault(title, []).append({"document_id": document["document_id"], "articles": articles, "labels": labels})
    return core, judicial


def link_references(references: list[dict], core: dict, judicial: dict) -> None:
    for reference in references:
        title = reference["law_title"]
        article = reference["article_label"]
        base_match = re.match(ARTICLE, article)
        base_article = base_match.group(0) if base_match else article
        key = article_key(base_article)
        reference["base_article_label"] = base_article
        candidates = []
        if title in core:
            candidates = [core[title]]
        else:
            candidates = judicial.get(re.sub(r"\s+", "", title), [])
        if len(candidates) == 1 and key in candidates[0]["articles"]:
            reference.update(document_id=candidates[0]["document_id"],
                             article_id=candidates[0]["articles"][key],
                             base_article_label=candidates[0]["labels"][key],
                             matched_current_law=True, link_granularity="article")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    args = parser.parse_args()
    root = args.root.resolve()
    records = staging_records(WORKSPACE) + existing_seed_records(root)
    errors = []
    prepared = []
    for record in records:
        raw_path = record["raw_path"]
        if not raw_path.is_file():
            errors.append({"slug": record["slug"], "error": f"原始文件不存在：{raw_path}"})
            continue
        raw = raw_path.read_bytes()
        raw_hash = sha(raw)
        if record.get("declared_hash") and record["declared_hash"] != raw_hash:
            errors.append({"slug": record["slug"], "error": "原始文件哈希与暂存清单不一致"})
            continue
        suffix = raw_path.suffix.lower()
        if suffix == ".pdf" and not raw.startswith(b"%PDF-"):
            errors.append({"slug": record["slug"], "error": "PDF 文件头无效"})
            continue
        if suffix in {".html", ".htm", ".md"} and b"Please enable JavaScript and refresh" in raw:
            errors.append({"slug": record["slug"], "error": "原始文件是 JavaScript 验证页"})
            continue
        try:
            text = record.get("text") if record.get("text_path") is None else normalized_text(record["text_path"])
        except (OSError, UnicodeDecodeError) as exc:
            errors.append({"slug": record["slug"], "error": f"正文读取失败：{exc}"})
            continue
        text = (text or "").strip()
        if len(text) < 400 or not record["authors"]:
            errors.append({"slug": record["slug"], "error": "正文不足 400 字或作者为空"})
            continue
        record.update(raw=raw, raw_hash=raw_hash, text=text, text_hash=sha(text.encode("utf-8")),
                      text_chars=len(text), url_key=canonical_url(record["source_url"]))
        record["title_key"] = duplicate_title(record["title"]) + "|" + "|".join(sorted(record["authors"]))
        prepared.append(record)

    seen = {}
    unique = []
    duplicates = []
    for record in sorted(prepared, key=quality, reverse=True):
        keys = [("url", record["url_key"]), ("text", record["text_hash"]), ("title_authors", record["title_key"])]
        matched = next((seen[key] for key in keys if key in seen), None)
        if matched:
            reason, duplicate = next((key[0], key[1]) for key in keys if key in seen)
            matched["alternates"].append({"url": record["source_url"], "reason": reason,
                                          "staging_raw_path": str(record["raw_path"])})
            duplicates.append({"kept_slug": matched["slug"], "duplicate_slug": record["slug"],
                               "url": record["source_url"], "reason": reason})
            continue
        record["alternates"] = []
        unique.append(record)
        for key in keys:
            seen[key] = record

    core, judicial = current_article_index(root)
    current_paths = []
    output_items = []
    for record in sorted(unique, key=lambda item: (item["school"], item["slug"])):
        school = record["school"]
        slug = record["slug"]
        suffix = record["raw_path"].suffix.lower()
        raw_destination = root / "raw" / "professors" / school / f"{slug}{suffix}"
        text_destination = root / "normalized" / "professor-text" / school / f"{slug}.txt"
        json_destination = root / "normalized" / "professors" / school / f"{slug}.json"
        atomic_bytes(raw_destination, record["raw"])
        atomic_bytes(text_destination, record["text"].encode("utf-8"))
        references = extract_references(record["text"])
        # Original seed topics are human-reviewed hints such as “民法典第1254条”.
        # They recover references written as “高空抛物（第1254条）” without a
        # repeated law name; unrelated standalone article numbers remain unlinked.
        for topic in record["supplied_topics"]:
            topic_match = re.fullmatch(
                rf"(?P<title>民法典|刑法|民事诉讼法|刑事诉讼法)(?P<article>{ARTICLE})", topic.strip())
            if not topic_match:
                continue
            law_title = CORE_TITLES[topic_match.group("title")]
            article_label = topic_match.group("article")
            if any(ref["law_title"] == law_title and article_key(ref["article_label"]) == article_key(article_label)
                   for ref in references):
                continue
            occurrence = re.search(re.escape(article_label), record["text"])
            if occurrence:
                references.append({
                    "law_title": law_title, "article_label": article_label,
                    "reference": f"《{law_title}》{article_label}",
                    "matched_text": occurrence.group(0), "text_offset": occurrence.start(),
                    "occurrences": 1, "document_id": None, "article_id": None,
                    "matched_current_law": False, "reference_source": "reviewed_seed_topic",
                })
        link_references(references, core, judicial)
        document = {
            "document_id": f"professor-{school}-{slug}", "slug": slug,
            "title": record["title"], "authors": record["authors"],
            "institution": SCHOOLS[school],
            "affiliation_evidence": record["affiliation"],
            "topics": infer_topics(record["title"], record["text"], record["supplied_topics"]),
            "source_type": record["source_type"], "source_name": record["source_name"],
            "publication_date": record["publication_date"], "source_url": record["source_url"],
            "alternate_sources": record["alternates"], "retrieved_at": now(),
            "raw_snapshot": str(raw_destination), "snapshot_format": suffix.lstrip("."),
            "raw_sha256": record["raw_hash"], "raw_bytes": len(record["raw"]),
            "text_path": str(text_destination), "text_sha256": record["text_hash"],
            "text_chars": record["text_chars"], "text": record["text"],
            "related_articles": [ref["reference"] for ref in references],
            "legal_references": references,
            "content_note": record["content_note"], "rights_note": record["rights_note"],
            "import_manifest": record["staging_manifest"],
            "usage_notice": "仅为署名作者个人学术观点，不代表学校或司法机关统一立场；使用时核对原文、发表版本及法律时效。",
        }
        atomic_json(json_destination, document)
        current_paths.extend([raw_destination, text_destination, json_destination])
        output_items.append({k: v for k, v in document.items() if k != "text"})

    previous_index = root / "manifest" / "professor-index.json"
    if previous_index.exists():
        previous = load(previous_index)
        for item in previous.get("items", []):
            for field in ["raw_snapshot", "text_path", "normalized_path"]:
                value = item.get(field)
                if value:
                    path = Path(value)
                    if path not in current_paths and path.is_file() and "professor" in str(path):
                        path.unlink()

    by_school = Counter(item["institution"] for item in output_items)
    by_topic = Counter(topic for item in output_items for topic in item["topics"])
    all_references = [ref for item in output_items for ref in item["legal_references"]]
    index = {
        "generated_at": now(), "root": str(root), "document_count": len(output_items),
        "new_staging_fulltext_count": len(staging_records(WORKSPACE)),
        "existing_seed_fulltext_candidates": len(existing_seed_records(root)),
        "by_institution": dict(by_school), "by_topic": dict(by_topic),
        "unique_authors": sorted({author for item in output_items for author in item["authors"]}),
        "legal_reference_count": len(all_references),
        "current_article_link_count": sum(ref["matched_current_law"] for ref in all_references),
        "duplicates": duplicates, "import_errors": errors,
        "items": [{**item, "normalized_path": str(root / "normalized" / "professors" /
                  next(key for key, value in SCHOOLS.items() if value == item["institution"]) /
                  f"{item['slug']}.json")} for item in output_items],
        "limitations": [
            "本库是公开教授讲评扩展库，不声称覆盖全部教授、全部论文或全部法条。",
            "教材书目不计入讲评全文；未下载视频、付费全文或受版权保护教材全书。",
            "转载页可能省略注释，content_note 或原页说明优先；观点均属于作者个人。",
            "法条关联为正文确定性提取，唯一现行条文才自动链接，其余保留引用原文。",
        ],
    }
    atomic_json(previous_index, index)
    verification = {
        "generated_at": now(), "document_count": len(output_items),
        "integrity_passed": not errors, "import_errors": errors,
        "raw_file_count": len(output_items), "text_file_count": len(output_items),
        "identity_evidence_item_count": sum(bool(item["affiliation_evidence"]) for item in output_items),
        "duplicate_count": len(duplicates), "by_institution": dict(by_school),
        "by_topic": dict(by_topic), "legal_reference_count": len(all_references),
        "current_article_link_count": sum(ref["matched_current_law"] for ref in all_references),
        "unlinked_reference_count": sum(not ref["matched_current_law"] for ref in all_references),
        "total_raw_bytes": sum(item["raw_bytes"] for item in output_items),
        "total_text_chars": sum(item["text_chars"] for item in output_items),
        "checks": ["原始文件存在", "声明哈希一致", "PDF 文件头", "非 JavaScript 验证页",
                   "UTF-8 正文不少于 400 字", "作者非空", "网址/正文/标题作者去重"],
    }
    atomic_json(root / "manifest" / "professor-verification.json", verification)
    print(json.dumps({k: v for k, v in verification.items() if k != "import_errors"}, ensure_ascii=False, indent=2))
    print(f"重复合并：{len(duplicates)}；导入错误：{len(errors)}")


if __name__ == "__main__":
    main()
