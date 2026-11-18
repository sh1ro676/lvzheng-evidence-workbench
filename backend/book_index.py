"""教材文本索引：把 PDF 教材抽成可检索的段落，带页码。

设计约束（因为这批 PDF 是 OCR 出来的）：
- 文本有错字（"贵任""笫一款"之类），所以只能用于**检索与理解**，
  不能直接当"逐字引文"用；引用前必须回书核对。
- 每块都保留页码，回答时能指到"上册 p345"，用户可翻书验证。
- 不依赖第三方分词：中文用「字符二元组」做 BM25，零依赖、对错字有一定容错。

用法：
    python book_index.py build --pdf "C:/.../刑法学 上.pdf" --out data/xfx_up.jsonl
    python book_index.py search --index data/xfx_up.jsonl --query "正当防卫 限度"
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sys
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

HEADER_RE = re.compile(r"^刑法学[（(]第六版[）)]$")
PAGE_NO_RE = re.compile(r"^\s*\d{1,4}\s*$")
SENT_END = "。！？；：”』）】…"
CJK_RE = re.compile(r"[\u4e00-\u9fff]")
TOKEN_RE = re.compile(r"[a-zA-Z]+|\d+")

CHUNK_TARGET = 420
CHUNK_MAX = 700


@dataclass
class Chunk:
    book: str
    page: int          # PDF 页码
    text: str
    printed: int = 0   # 书内页码（纸书上印的那个数字）
    tokens: Counter = field(default_factory=Counter, repr=False)


def clean_page(raw: str) -> tuple[list[str], int]:
    """去页眉页脚与行号，合并被 OCR 拆断的句子，并抽出书内页码。"""
    lines = []
    printed = 0
    for line in raw.splitlines():
        line = unicodedata.normalize("NFKC", line).strip()
        if not line:
            continue
        if PAGE_NO_RE.match(line):
            if not printed:
                printed = int(line)
            continue
        if HEADER_RE.match(line):
            continue
        lines.append(line)

    merged: list[str] = []
    for line in lines:
        if merged:
            prev = merged[-1]
            # 上一行没有句末标点、且本行不是新段落标记 → 视为同一句被折断
            starts_new = re.match(r"^(第[一二三四五六七八九十百]+[章节]|[一二三四五六七八九十]+、|（[一二三四五六七八九十]+）|\d+[.、])", line)
            if prev and prev[-1] not in SENT_END and not starts_new:
                merged[-1] = prev + line
                continue
        merged.append(line)
    return merged, printed


def extract_pages(pdf_path: Path) -> list[tuple[int, int, list[str]]]:
    import fitz  # PyMuPDF

    doc = fitz.open(pdf_path)
    pages = []
    for number in range(doc.page_count):
        raw = doc[number].get_text("text")
        paragraphs, printed = clean_page(raw)
        if paragraphs:
            pages.append((number + 1, printed, paragraphs))
    doc.close()
    return pages


def build_chunks(pdf_path: Path, book: str) -> list[Chunk]:
    chunks: list[Chunk] = []
    for page, printed, paragraphs in extract_pages(pdf_path):
        buffer, buffer_page, buffer_printed = "", page, printed
        for paragraph in paragraphs:
            if not buffer:
                buffer_page, buffer_printed = page, printed
            buffer += paragraph
            if len(buffer) >= CHUNK_TARGET:
                chunks.append(Chunk(book=book, page=buffer_page, printed=buffer_printed,
                                    text=buffer[:CHUNK_MAX]))
                buffer = buffer[CHUNK_MAX:] if len(buffer) > CHUNK_MAX else ""
                buffer_page, buffer_printed = page, printed
        if buffer.strip():
            chunks.append(Chunk(book=book, page=buffer_page, printed=buffer_printed, text=buffer))
    for chunk in chunks:
        chunk.tokens = tokenize(chunk.text)
    return chunks


def tokenize(text: str) -> Counter:
    """中文按字符二元组，英文数字按词。"""
    counter: Counter = Counter()
    for match in TOKEN_RE.finditer(text):
        counter[match.group(0).lower()] += 1
    chars = [ch for ch in text if CJK_RE.match(ch)]
    for first, second in zip(chars, chars[1:]):
        counter[first + second] += 1
    return counter


class BookIndex:
    def __init__(self, chunks: list[Chunk]):
        self.chunks = chunks
        self.books: dict[str, list[Chunk]] = {}
        self.n = len(chunks)
        self.avg_len = sum(sum(c.tokens.values()) for c in chunks) / max(self.n, 1)
        df: Counter = Counter()
        for chunk in chunks:
            df.update(chunk.tokens.keys())
            book_id = hashlib.sha256(chunk.book.encode("utf-8")).hexdigest()[:20]
            self.books.setdefault(book_id, []).append(chunk)
        self.df = df
        self.k1, self.b = 1.5, 0.75

    def idf(self, term: str) -> float:
        df = self.df.get(term, 0)
        return math.log(1 + (self.n - df + 0.5) / (df + 0.5))

    def search(self, query: str, k: int = 8, *, book_id: str | None = None) -> list[tuple[float, Chunk]]:
        terms = tokenize(query)
        scored = []
        for chunk in self.chunks if book_id is None else self.books[book_id]:
            length = sum(chunk.tokens.values()) or 1
            score = 0.0
            for term, qcount in terms.items():
                tf = chunk.tokens.get(term, 0)
                if not tf:
                    continue
                denom = tf + self.k1 * (1 - self.b + self.b * length / self.avg_len)
                score += self.idf(term) * qcount * tf * (self.k1 + 1) / denom
            if score > 0:
                scored.append((score, chunk))
        scored.sort(key=lambda pair: pair[0], reverse=True)
        return scored[:k]

    def catalog(self) -> list[dict]:
        return [
            {"book_id": book_id, "title": chunks[0].book, "source_name": book_source(chunks[0].book),
             "page_count": len({chunk.page for chunk in chunks}), "chunk_count": len(chunks)}
            for book_id, chunks in sorted(self.books.items(), key=lambda entry: entry[1][0].book)
        ]

    def library_search(self, book_id: str, query: str) -> list[Chunk]:
        chunks = self.books[book_id]
        page = re.fullmatch(r"\s*(?P<kind>PDF|书内|第)\s*[pP]?\s*(?P<number>[0-9]{1,5})\s*页?\s*", query, re.I)
        if page:
            number = int(page["number"])
            if not number:
                return []
            return [chunk for chunk in chunks
                    if number == (chunk.page if page["kind"].lower() == "pdf" else chunk.printed)]
        return [chunk for _, chunk in self.search(query, book_id=book_id)]


def book_source(book: str) -> str:
    if re.match(r"^(?:张明楷.{0,3})?刑法学(?:[（(]第六版[）)])?[\s·・]*(?:[上下]册?)?$", book):
        return "张明楷《刑法学》（第六版）· 法律出版社"
    return book


def to_evidence(chunk: Chunk, index: int) -> dict:
    """把教材段落转成与 server 证据格式一致的字典（供生成与校验复用）。"""
    page_label = f"书内 p{chunk.printed}" if chunk.printed else f"PDF p{chunk.page}"
    source_name = book_source(chunk.book)
    return {
        "document_id": f"book-{index}",
        "article_id": f"p{chunk.page}",
        "document_title": chunk.book,
        "article_label": f"{page_label}（PDF p{chunk.page}）" if chunk.printed else page_label,
        "section_path": [chunk.book, page_label],
        "text": chunk.text,
        "provider": "textbook",
        "source_name": source_name,
        "source_url": "",
        "published_at": "2021" if source_name != chunk.book else "",
        "effective_at": "",
        "status": "教材原文（OCR，可能有个别错字，引用前请核对纸书）",
        "source_notice": "该证据由 OCR 提取，可能存在错字或标点误差；对外引用前须与纸书第对应页核对。",
        "match_type": "textbook",
        "relevance": 1.0,
    }


def save(chunks: list[Chunk], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for chunk in chunks:
            handle.write(json.dumps({"book": chunk.book, "page": chunk.page,
                                     "printed": chunk.printed,
                                     "text": chunk.text}, ensure_ascii=False) + "\n")


def load(path: Path) -> list[Chunk]:
    chunks = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        book, page, text, printed = row["book"], row["page"], row["text"], row.get("printed", 0)
        if (not isinstance(book, str) or not book.strip() or not isinstance(text, str) or not text.strip()
                or type(page) is not int or page < 1 or type(printed) is not int or printed < 0):
            raise ValueError("教材索引的书名、正文或页码无效")
        chunk = Chunk(book=book, page=page, text=text, printed=printed)
        chunk.tokens = tokenize(chunk.text)
        chunks.append(chunk)
    return chunks


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="教材 PDF → 可检索段落")
    sub = parser.add_subparsers(dest="cmd", required=True)

    build = sub.add_parser("build")
    build.add_argument("--pdf", required=True)
    build.add_argument("--book", default="")
    build.add_argument("--out", required=True)

    search = sub.add_parser("search")
    search.add_argument("--index", required=True)
    search.add_argument("--query", required=True)
    search.add_argument("--k", type=int, default=5)

    args = parser.parse_args(argv)
    if args.cmd == "build":
        pdf = Path(args.pdf)
        book = args.book or pdf.stem
        chunks = build_chunks(pdf, book)
        save(chunks, Path(args.out))
        print(f"{book}: {len(chunks)} 块 → {args.out}")
        return 0

    index = BookIndex(load(Path(args.index)))
    for score, chunk in index.search(args.query, args.k):
        label = f"PDF p{chunk.page}" + (f" / 书内 p{chunk.printed}" if chunk.printed else "")
        print(f"[{score:6.2f}] {chunk.book} {label}")
        print("   ", chunk.text[:160].replace("\n", " "))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
