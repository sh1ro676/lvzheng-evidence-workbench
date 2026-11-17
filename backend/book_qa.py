"""基于教材 PDF 的问答：检索 → 组装证据 → DeepSeek 生成 → 引用校验。

证据格式沿用现有 server 的规范，所以生成与校验链路完全复用。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import book_index  # noqa: E402
import server  # noqa: E402
from deepseek_client import verify_answer  # noqa: E402

BOOKS = {
    "刑法学（第六版）上册": Path("data/books/xfx_up.jsonl"),
    "刑法学（第六版）下册": Path("data/books/xfx_down.jsonl"),
}


def load_all() -> list[book_index.Chunk]:
    chunks: list[book_index.Chunk] = []
    for path in BOOKS.values():
        if path.exists():
            chunks.extend(book_index.load(path))
    return chunks


def to_evidence(chunk: book_index.Chunk, index: int) -> dict:
    page_label = f"书内 p{chunk.printed}" if chunk.printed else f"PDF p{chunk.page}"
    return {
        "document_id": f"book-{index}",
        "article_id": f"p{chunk.page}",
        "document_title": chunk.book,
        "article_label": f"{page_label}（PDF p{chunk.page}）",
        "section_path": [chunk.book, page_label],
        "text": chunk.text,
        "provider": "textbook",
        "source_name": "张明楷《刑法学》（第六版）· 法律出版社",
        "source_url": "",
        "published_at": "2021",
        "effective_at": "",
        "status": "教材原文（OCR，可能有个别错字，引用前请核对纸书）",
        "source_notice": "该证据由 OCR 提取，可能存在错字或标点误差；对外引用前须与纸书第对应页核对。",
        "match_type": "textbook",
        "relevance": 1.0,
    }


TEXTBOOK_PROMPT = """你是法律教材讲解助手，服务对象是正在学习法条的学生和律师。
你只能依据用户提供的教材段落作答，不得使用记忆中的其他案例、司法解释或数字。
除独立短标题外，每一个非空实质性段落末尾都必须标注【证据N】，包括开头引言、限制说明和结尾；不要输出没有证据编号的收尾说明。
按以下三节组织，标题可依证据调整：
一、所问条文与教材定位
二、教材观点
三、理解与适用要点
教材观点必须写明是哪个学者、哪本书、第几页；证据中只出现一位作者时，不要写成"四校观点"或暗示存在其他学校论述。
证据来自 OCR，可能有个别错字或标点误差：引用原句时用中文引号逐字复制证据原文，并在该段注明"（OCR 原文，引用前请核对纸书）"；无法确认的字不要补写。
概括与引文必须分开，不得把自己的归纳放进引号。
输出一个 JSON 对象，不要 Markdown 代码块，格式为 {"answer":"回答正文","unresolved":["仍未解决的问题"]}。"""


def resolve_document(question: str) -> dict | None:
    """把问题里的法名解析到本地库的某一部法。

    exact_matches 只看条号、不看法律名，问"刑法第20条"会撞上"民法典第20条"，
    所以必须先把文书定下来再取条。
    """
    docs = server.STORE.document_list()
    title = server.extract_law_title(question) or ""
    if title:
        for doc in docs:
            if doc["title"] == title or doc["title"].endswith(title):
                return doc
    for doc in docs:
        short = doc["title"].replace("中华人民共和国", "")
        if short and short in question:
            return doc
    return None


def law_evidence(question: str) -> list[dict]:
    """按"法名＋条号"精确取条文原文，作为证据1。

    不能用相似度检索：问"刑法第20条"时，BM25 可能把"民法典第20条"排第一，
    模型拿到错条文就会露馅。这里强制走精确匹配 + 明确文书。
    """
    numbers = server.article_numbers(question)
    if not numbers:
        return []
    doc = resolve_document(question)
    if not doc:
        return []
    try:
        hits = server.STORE.exact_matches(f"第{numbers[0]}条", doc["document_id"])
    except Exception:  # noqa: BLE001
        return []
    out = []
    for item in hits[:1]:
        item = dict(item)
        item["provider"] = "local_official"
        item["source_name"] = "国家法律法规数据库"
        item["status"] = "官方法条原文"
        out.append(item)
    return out


def answer(question: str, k: int = 6) -> None:
    chunks = load_all()
    if not chunks:
        print("没有索引。先跑：python book_index.py build --pdf <路径> --out data/books/xxx.jsonl")
        return
    index = book_index.BookIndex(chunks)
    hits = index.search(question, k)
    if not hits:
        print("书中未检索到相关内容。")
        return

    print("=== 检索命中 ===")
    for score, chunk in hits:
        label = f"书内 p{chunk.printed}" if chunk.printed else f"PDF p{chunk.page}"
        print(f"  [{score:6.1f}] {chunk.book} {label}")

    laws = law_evidence(question)
    if laws:
        print(f"  [法条] {laws[0]['document_title']} {laws[0]['article_label']}")

    evidence = laws + [to_evidence(chunk, i) for i, (_, chunk) in enumerate(hits, start=1)]
    text = server.DEEPSEEK.generate(question, evidence, system_prompt=TEXTBOOK_PROMPT)
    verification = verify_answer(text, evidence)

    print("\n=== 回答 ===\n")
    print(text)
    print("\n=== 校验 ===")
    for check in verification["checks"]:
        print(f"  {'✓' if check['passed'] else '×'} {check['name']}：{check['detail'][:60]}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("question")
    parser.add_argument("--k", type=int, default=6)
    args = parser.parse_args()
    answer(args.question, args.k)
