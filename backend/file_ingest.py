"""上传文件解析：DOCX（标准库）、PDF（pypdf）、扫描件 OCR 兜底（DeepSeek OCR）。

设计取舍：
- DOCX 只用标准库解析，避免为它引入依赖；
- PDF 走 pypdf（纯 Python，无系统依赖）；
- 文本层覆盖率过低的 PDF 视为扫描件，只有开启 OCR_ENABLED 时才调用云端 OCR，
  调用前由前端提示"文件将上传至模型服务商"。
"""
from __future__ import annotations

import io
import json
import os
import re
import time
import urllib.error
import urllib.request
import uuid
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

from book_index import BookIndex, Chunk, tokenize

ROOT = Path(__file__).resolve().parent
UPLOAD_DIR = ROOT / "data" / "uploads"
MAX_BYTES = 25 * 1024 * 1024
MAX_UNCOMPRESSED = 200 * 1024 * 1024
# 单页平均字符数低于此值，认为是扫描页
MIN_CHARS_PER_PAGE = 40
# 全文直接注入证据的字数上限；超过则改用分块检索
MAX_FULL_CHARS = 8000
OCR_ENDPOINT = "https://api.deepseek.com/v1/tools/ocr"

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
CJK_RE = re.compile(r"[\u4e00-\u9fff]")


class FileError(RuntimeError):
    """文件不可解析或不合规。"""


def ocr_enabled() -> bool:
    return os.environ.get("OCR_ENABLED", "off").strip().lower() in {"on", "true", "1", "yes"}


def _api_key() -> str:
    return os.environ.get("DEEPSEEK_API_KEY", "").strip()


def detect_kind(data: bytes, filename: str) -> str:
    """按内容判断类型，扩展名只作辅助。"""
    if data.startswith(b"%PDF-"):
        return "pdf"
    if data.startswith(b"PK\x03\x04"):
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as zf:
                if "word/document.xml" in zf.namelist():
                    return "docx"
        except zipfile.BadZipFile:
            pass
        raise FileError("这个压缩包不是 Word 文档，暂时只支持 .docx")
    raise FileError("只支持 .docx 和 .pdf；旧版 .doc 请先另存为 .docx")


def _normalize(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t\u3000]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def parse_docx(data: bytes) -> list[tuple[int, str]]:
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        total = sum(info.file_size for info in zf.infolist())
        if total > MAX_UNCOMPRESSED:
            raise FileError("解压后体积过大，已拒绝解析")
        xml = zf.read("word/document.xml")
    try:
        root = ET.fromstring(xml)
    except ET.ParseError as error:
        raise FileError(f"Word 文档结构异常：{error}") from error
    paragraphs = []
    for paragraph in root.iter(f"{W}p"):
        text = "".join(node.text or "" for node in paragraph.iter(f"{W}t"))
        text = text.strip()
        if text:
            paragraphs.append(text)
    if not paragraphs:
        raise FileError("这份 Word 里没有可提取的文字（可能是纯图片）")
    body = "\n".join(paragraphs)
    return [(1, body)]


def parse_pdf(data: bytes) -> list[tuple[int, str]]:
    try:
        from pypdf import PdfReader
    except ImportError as error:  # pragma: no cover - 部署环境缺依赖时给出可读提示
        raise FileError("服务器缺少 PDF 解析组件（pypdf），请联系管理员") from error
    try:
        reader = PdfReader(io.BytesIO(data))
    except Exception as error:  # noqa: BLE001
        raise FileError(f"PDF 无法读取：{error}") from error
    if getattr(reader, "is_encrypted", False):
        try:
            reader.decrypt("")
        except Exception as error:  # noqa: BLE001
            raise FileError("PDF 有密码保护，请先解除密码再上传") from error
    pages: list[tuple[int, str]] = []
    for index, page in enumerate(reader.pages, start=1):
        try:
            text = page.extract_text() or ""
        except Exception:  # noqa: BLE001 - 个别页损坏不应导致整份失败
            text = ""
        pages.append((index, _normalize(text)))
    if not pages:
        raise FileError("PDF 里没有页面")
    return pages


def _multipart(data: bytes, filename: str, purpose: str) -> tuple[bytes, str]:
    boundary = "----lvtu" + uuid.uuid4().hex
    safe_name = re.sub(r'["\r\n]', "_", filename or "document.pdf")
    chunks = [
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"purpose\"\r\n\r\n{purpose}\r\n".encode(),
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{safe_name}\"\r\n"
        "Content-Type: application/octet-stream\r\n\r\n".encode(),
        data,
        f"\r\n--{boundary}--\r\n".encode(),
    ]
    return b"".join(chunks), f"multipart/form-data; boundary={boundary}"


def ocr_document(data: bytes, filename: str, timeout: float = 180) -> list[tuple[int, str]]:
    """整份文件送 DeepSeek OCR，返回逐页文本。"""
    if not ocr_enabled():
        raise FileError("该文件没有文本层（疑似扫描件），管理员尚未开启 OCR")
    key = _api_key()
    if not key:
        raise FileError("未配置 DEEPSEEK_API_KEY，无法进行 OCR")
    body, content_type = _multipart(data, filename, "text_recognition")
    request = urllib.request.Request(
        OCR_ENDPOINT, data=body,
        headers={"Authorization": f"Bearer {key}", "Content-Type": content_type},
        method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")[:200]
        raise FileError(f"OCR 返回 {error.code}：{detail}") from error
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as error:
        raise FileError(f"OCR 调用失败：{error}") from error
    if "error" in payload:
        message = (payload.get("error") or {}).get("message") or "OCR 失败"
        raise FileError(f"OCR 失败：{message}")
    pages = []
    for page in payload.get("pages") or []:
        text = _normalize(str(page.get("text") or ""))
        pages.append((int(page.get("index", 0)) + 1, text))
    if not pages:
        raise FileError("OCR 没有返回任何页面")
    return pages


def build_chunks(pages: list[tuple[int, str]], title: str, size: int = 600, overlap: int = 80) -> list[Chunk]:
    chunks: list[Chunk] = []
    for page_number, text in pages:
        if not text:
            continue
        start = 0
        while start < len(text):
            piece = text[start:start + size].strip()
            if piece:
                chunks.append(Chunk(book=title, page=page_number, printed=page_number, text=piece))
            if start + size >= len(text):
                break
            start += size - overlap
    for chunk in chunks:
        chunk.tokens = tokenize(chunk.text)
    if not chunks:
        raise FileError("没有提取到可用段落")
    return chunks


def ingest(data: bytes, filename: str) -> dict:
    if len(data) > MAX_BYTES:
        raise FileError(f"文件超过 {MAX_BYTES // 1024 // 1024} MB")
    kind = detect_kind(data, filename)
    engine = "docx" if kind == "docx" else "pypdf"
    ocr_used = False
    parse_error: FileError | None = None

    try:
        pages = parse_docx(data) if kind == "docx" else parse_pdf(data)
    except FileError as error:
        # 本地解析失败（加密、损坏、纯图片 Word）不直接拒绝：开启 OCR 时交给云端兜底
        pages = []
        parse_error = error

    total_chars = sum(len(text) for _, text in pages)
    coverage = total_chars / max(len(pages), 1) if pages else 0
    # PDF 才有可靠页码，页均字数过低才判定为扫描件；Word 段落少不等于无文本
    needs_ocr = (not pages) or total_chars < 20 or (kind == "pdf" and coverage < MIN_CHARS_PER_PAGE)

    if needs_ocr:
        if not ocr_enabled():
            if parse_error is not None:
                raise parse_error
            raise FileError("文件里几乎没有可读文字（可能是扫描件），管理员尚未开启 OCR")
        try:
            pages = ocr_document(data, filename)
            engine = "ocr"
            ocr_used = True
            total_chars = sum(len(text) for _, text in pages)
        except FileError:
            # OCR 也失败时，保留本地能读出的那部分（如果有），而不是整份丢弃
            if parse_error is not None or not pages:
                raise

    if total_chars < 20:
        raise FileError("文件里几乎没有可读文字（OCR 识别结果也为空），请确认文件是否损坏")

    title = Path(filename or "未命名").name[:80]
    chunks = build_chunks(pages, title)
    file_id = uuid.uuid4().hex[:16]
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    record = {
        "file_id": file_id,
        "name": title,
        "kind": kind,
        "engine": engine,
        "ocr_used": ocr_used,
        "pages": len(pages),
        "chars": total_chars,
        "chunk_count": len(chunks),
        "created_at": int(time.time()),
        "chunks": [
            {"page": chunk.page, "printed": chunk.printed, "text": chunk.text,
             "tokens": dict(chunk.tokens)}
            for chunk in chunks
        ],
    }
    (UPLOAD_DIR / f"{file_id}.json").write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
    return {key: value for key, value in record.items() if key != "chunks"}


def load_record(file_id: str) -> dict | None:
    if not re.fullmatch(r"[0-9a-f]{16}", file_id or ""):
        return None
    path = UPLOAD_DIR / f"{file_id}.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def list_records() -> list[dict]:
    if not UPLOAD_DIR.exists():
        return []
    records = []
    for path in sorted(UPLOAD_DIR.glob("*.json"), key=lambda item: item.stat().st_mtime, reverse=True):
        record = load_record(path.stem)
        if record:
            records.append({key: value for key, value in record.items() if key != "chunks"})
    return records


def delete_record(file_id: str) -> bool:
    path = UPLOAD_DIR / f"{file_id}.json"
    if not path.exists():
        return False
    path.unlink()
    return True


def evidence_from_files(file_ids: list[str], question: str, per_file: int = 3) -> list[dict]:
    """把上传文件转成证据：短文件给全文，长文件按问题检索片段。"""
    items: list[dict] = []
    for file_id in file_ids[:5]:
        record = load_record(file_id)
        if not record:
            continue
        title = record["name"]
        chunks = [
            Chunk(book=title, page=item["page"], printed=item.get("printed", item["page"]), text=item["text"])
            for item in record.get("chunks") or []
        ]
        for chunk in chunks:
            chunk.tokens = chunk.tokens or tokenize(chunk.text)
        if not chunks:
            continue
        if record.get("chars", 0) <= MAX_FULL_CHARS:
            text = "\n".join(chunk.text for chunk in chunks)[:MAX_FULL_CHARS]
            label = f"全文（{record['pages']} 页）" if record["pages"] > 1 else "全文"
            picked = [(label, text)]
        else:
            index = BookIndex(chunks)
            hits = index.search(question, k=per_file) or [(0.0, chunk) for chunk in chunks[:per_file]]
            if record.get("kind") == "pdf":
                picked = [(f"第 {chunk.printed} 页", chunk.text) for _, chunk in hits]
            else:
                # Word 没有可靠页码，用片段序号，避免让用户以为能翻到第 N 页
                picked = [(f"片段 {position}", chunk.text)
                          for position, (_, chunk) in enumerate(hits, start=1)]
        for label, text in picked:
            items.append({
                "document_id": f"upload-{file_id}",
                "article_id": f"{file_id}-{label}",
                "document_title": title,
                "article_label": label,
                "source_name": "用户上传",
                "source_url": "",
                "section_path": [title, label],
                "text": text,
                "status": "用户上传文件",
                "effect_level": "",
                "published_at": "",
                "effective_at": "",
                "provider": "upload",
                "match_type": "upload",
            })
    return items
