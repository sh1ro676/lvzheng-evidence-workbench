from __future__ import annotations

import hashlib
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

SOURCE = Path(r"E:\lvzheng-data")
APP = Path(__file__).resolve().parents[1]
DATA = APP / "backend" / "data"
ARCHIVE = DATA / "lvzheng-archive"
DOCUMENTS = DATA / "documents"
SOURCES = DATA / "sources"

CORE = [
    ("civil-code", "中华人民共和国民法典", 1260),
    ("criminal-law", "中华人民共和国刑法", 505),
    ("civil-procedure-law", "中华人民共和国民事诉讼法", 306),
    ("criminal-procedure-law", "中华人民共和国刑事诉讼法", 308),
    ("civil-procedure-interpretation", "最高人民法院关于适用《中华人民共和国民事诉讼法》的解释", 550),
    ("criminal-procedure-interpretation", "最高人民法院关于适用《中华人民共和国民事诉讼法》的解释".replace("民事", "刑事"), 655),
]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def main() -> None:
    if not SOURCE.is_dir():
        raise SystemExit("未找到 E:\\lvzheng-data，请先连接硬盘")
    print("正在完整保留 E 盘资料……", flush=True)
    shutil.copytree(SOURCE, ARCHIVE, dirs_exist_ok=True)
    DOCUMENTS.mkdir(parents=True, exist_ok=True)
    SOURCES.mkdir(parents=True, exist_ok=True)
    migrated = []
    source_documents = ARCHIVE / "normalized" / "documents"
    for alias, title, expected_count in CORE:
        candidates = []
        for path in source_documents.glob("*.json"):
            document = json.loads(path.read_text(encoding="utf-8"))
            if document.get("title") == title and document.get("status") == "有效" and len(document.get("articles", [])) == expected_count:
                candidates.append((path, document))
        if len(candidates) != 1:
            raise RuntimeError(f"{title}：应找到 1 份现行完整文本，实际为 {len(candidates)}")
        source_path, document = candidates[0]
        target_path = DOCUMENTS / f"{alias}.json"
        if alias == "civil-code" and target_path.exists():
            active = json.loads(target_path.read_text(encoding="utf-8"))
            if len(active["articles"]) != expected_count:
                raise RuntimeError("现有民法典数据不完整")
        else:
            snapshot = Path(document["source_snapshot"])
            archived_snapshot = ARCHIVE / snapshot.relative_to(SOURCE)
            if not archived_snapshot.is_file():
                raise RuntimeError(f"缺少原始快照：{archived_snapshot}")
            target_snapshot = SOURCES / f"{alias}{archived_snapshot.suffix.lower()}"
            shutil.copy2(archived_snapshot, target_snapshot)
            actual_hash = sha256(target_snapshot)
            if actual_hash != document["snapshot_sha256"]:
                raise RuntimeError(f"{title} 原始快照 SHA-256 不一致")
            document["document_id"] = alias
            document["source_snapshot"] = f"data\\sources\\{target_snapshot.name}"
            document["article_count"] = expected_count
            target_path.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        migrated.append({"document_id": alias, "title": title, "articles": expected_count, "source_file": source_path.name})

    files = [path for path in ARCHIVE.rglob("*") if path.is_file()]
    archive_bytes = sum(path.stat().st_size for path in files)
    manifest_dir = DATA / "manifest"
    manifest_dir.mkdir(parents=True, exist_ok=True)
    manifest = {
        "migrated_at": datetime.now(timezone.utc).isoformat(),
        "source": str(SOURCE),
        "archive": "backend/data/lvzheng-archive",
        "archive_file_count": len(files),
        "archive_bytes": archive_bytes,
        "active_documents": migrated,
        "active_article_count": sum(item["articles"] for item in migrated),
    }
    (manifest_dir / "e-data-migration.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(manifest, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
