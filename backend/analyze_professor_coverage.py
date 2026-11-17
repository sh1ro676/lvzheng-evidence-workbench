"""Build a per-article, per-school professor-reference coverage matrix.

Coverage means that a collected professor text explicitly references the current
article. It does not prove that the article is the main subject of the text.
"""
from __future__ import annotations

import csv
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(r"E:\lvzheng-data")
CORE = [
    ("中华人民共和国民法典", "ff808081729d1efe01729d50b5c500bf", 1260),
    ("中华人民共和国刑法", "ff808181796a636a0179822a19640c92", 505),
    ("中华人民共和国民事诉讼法", "ff8081818a21dc13018b425303b7086d", 306),
    ("中华人民共和国刑事诉讼法", "ff8080816f135f46016f1d1b81b01351", 308),
]
SCHOOLS = [("pku", "北京大学"), ("tsinghua", "清华大学"),
           ("cupl", "中国政法大学"), ("nwupl", "西北政法大学")]


def load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def pct(value: int, total: int) -> float:
    return round(value * 100 / total, 2) if total else 0.0


def main() -> None:
    matrix = {}
    for law_title, document_id, expected in CORE:
        document = load(ROOT / "normalized" / "documents" / f"{document_id}.json")
        if len(document["articles"]) != expected:
            raise ValueError(f"{law_title} 条数变化，需重新核对：{len(document['articles'])}")
        for article in document["articles"]:
            key = (document_id, article["article_id"])
            matrix[key] = {
                "law_title": law_title, "document_id": document_id,
                "article_id": article["article_id"], "article_label": article["article_label"],
                "article_text": article["text"], "schools": {slug: set() for slug, _ in SCHOOLS},
                "documents": {},
            }

    for path in (ROOT / "normalized" / "professors").glob("*/*.json"):
        professor = load(path)
        school = path.parent.name
        for reference in professor.get("legal_references", []):
            if not reference.get("matched_current_law"):
                continue
            key = (reference.get("document_id"), reference.get("article_id"))
            if key not in matrix:
                continue
            cell = matrix[key]
            cell["schools"][school].add(professor["slug"])
            cell["documents"][professor["slug"]] = {
                "title": professor["title"], "authors": professor["authors"], "school": school,
            }

    rows = []
    law_stats = {title: {"total_articles": expected, "school_sets": {slug: set() for slug, _ in SCHOOLS},
                         "any_set": set(), "school_count_distribution": Counter(),
                         "professor_documents": {slug: set() for slug, _ in SCHOOLS}}
                 for title, _, expected in CORE}
    for (document_id, article_id), cell in matrix.items():
        law_title = cell["law_title"]
        stats = law_stats[law_title]
        present = [school for school, docs in cell["schools"].items() if docs]
        stats["school_count_distribution"][len(present)] += 1
        row = {
            "法律": law_title, "条文ID": article_id, "条号": cell["article_label"],
            "条文正文": cell["article_text"], "覆盖学校数": len(present),
            "教授文档数（去重）": len(cell["documents"]),
        }
        for slug, school_name in SCHOOLS:
            docs = sorted(cell["schools"][slug])
            stats["professor_documents"][slug].update(docs)
            if docs:
                stats["school_sets"][slug].add(article_id)
                stats["any_set"].add(article_id)
            row[f"{school_name}文档数"] = len(docs)
            row[f"{school_name}作者"] = "；".join(sorted({
                author for slug_value in docs for author in cell["documents"][slug_value]["authors"]
            }))
            row[f"{school_name}讲评标题"] = "；".join(cell["documents"][slug_value]["title"] for slug_value in docs)
        rows.append(row)

    summary_laws = []
    for law_title, _, expected in CORE:
        stats = law_stats[law_title]
        school_coverage = {}
        for slug, school_name in SCHOOLS:
            covered = len(stats["school_sets"][slug])
            school_coverage[school_name] = {
                "covered_articles": covered, "total_articles": expected,
                "coverage_percent": pct(covered, expected),
                "professor_document_count": len(stats["professor_documents"][slug]),
            }
        any_count = len(stats["any_set"])
        summary_laws.append({
            "law_title": law_title, "total_articles": expected,
            "any_school_covered_articles": any_count,
            "any_school_coverage_percent": pct(any_count, expected),
            "uncovered_articles": expected - any_count,
            "articles_with_2plus_schools": sum(count for schools, count in stats["school_count_distribution"].items() if schools >= 2),
            "articles_with_3plus_schools": sum(count for schools, count in stats["school_count_distribution"].items() if schools >= 3),
            "articles_with_4_schools": stats["school_count_distribution"][4],
            "schools": school_coverage,
        })

    total_articles = sum(item["total_articles"] for item in summary_laws)
    total_covered = sum(item["any_school_covered_articles"] for item in summary_laws)
    overall_schools = {}
    for slug, school_name in SCHOOLS:
        covered = sum(item["schools"][school_name]["covered_articles"] for item in summary_laws)
        overall_schools[school_name] = {
            "covered_article_instances": covered,
            "coverage_percent_of_all_articles": pct(covered, total_articles),
        }

    generated_at = datetime.now(timezone.utc).isoformat()
    summary = {
        "generated_at": generated_at,
        "metric": "已收集教授全文中，明确链接到现行条文的可检索引用覆盖率；包含直接评析和论证引用，不等于深度讲评覆盖率。",
        "total_articles": total_articles, "any_school_covered_articles": total_covered,
        "any_school_coverage_percent": pct(total_covered, total_articles),
        "articles_with_2plus_schools": sum(item["articles_with_2plus_schools"] for item in summary_laws),
        "articles_with_3plus_schools": sum(item["articles_with_3plus_schools"] for item in summary_laws),
        "articles_with_4_schools": sum(item["articles_with_4_schools"] for item in summary_laws),
        "overall_schools": overall_schools, "laws": summary_laws,
        "coverage_csv": str(ROOT / "manifest" / "professor-article-coverage.csv"),
        "notes": [
            "一个条文被同一教授文档多次引用，只计一次。",
            "不同法律的条文分别计数，总计为法条实例数。",
            "未建立链接的旧法、简称和司法解释不进入这四部法律的覆盖率。",
            "零覆盖表示当前已收集且已链接的资料未命中，不表示学界没有观点。",
        ],
    }
    (ROOT / "manifest" / "professor-coverage-summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    with (ROOT / "manifest" / "professor-article-coverage.csv").open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
