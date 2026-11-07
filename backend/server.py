from __future__ import annotations

import json
import os
import re
import sys
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from deepseek_client import DeepSeekClient, DeepSeekError, verify_answer
from legal_store import LegalStore
from pkulaw_mcp import PkulawError, PkulawProvider, extract_law_title

ROOT = Path(__file__).resolve().parent


def load_project_env() -> None:
    env_path = ROOT.parent / ".env"
    if not env_path.exists():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key.strip()):
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


load_project_env()
STORE = LegalStore()
DEEPSEEK = DeepSeekClient()
PKULAW = PkulawProvider()


def numbered_evidence(evidence: list[dict]) -> list[dict]:
    return [{"citation_id": index, **item} for index, item in enumerate(evidence, start=1)]


def retrieve_evidence(question: str, document_id: str | None = None, source: str = "auto") -> tuple[list[dict], dict]:
    if source not in {"auto", "local", "pkulaw"}:
        raise ValueError("不支持的数据源")
    explicit_title = extract_law_title(question)
    title_is_local = not explicit_title or any(
        document["title"] == explicit_title or document["title"].endswith(explicit_title)
        for document in STORE.documents
    )
    evidence = STORE.search(question, document_id) if source in {"auto", "local"} and title_is_local else []
    if evidence or source == "local":
        return evidence, {"source": "local_official", "provider": "flk", "cached": True}
    if source == "auto" and not PKULAW.configured:
        return [], {"source": "none", "provider": None, "notice": "本地未命中；北大法宝 MCP 尚未配置"}
    result = PKULAW.search(question)
    return result["evidence"], {
        "source": "pkulaw", "provider": "pkulaw", "strategy": result["strategy"],
        "tool": result["tool"], "cached": result["cached"], "queried_at": result["queried_at"],
    }


def response_payload(question: str, evidence: list[dict]) -> dict:
    numbered = [{"citation_id": index, **item} for index, item in enumerate(evidence, start=1)]
    if not evidence:
        return {
            "status": "insufficient_evidence",
            "answer": "在当前已入库的官方文献中，没有找到足以支持这项任务的原文依据。为避免编造，本次未调用模型生成法律结论。你可以尝试明确法律名称、条文编号，或先扩充法律文库。",
            "evidence": [],
            "verification": {"passed": True, "checks": [{"name": "无依据不生成", "passed": True, "detail": "未向模型提供证据，未生成法律回答"}]},
        }
    if not DEEPSEEK.configured:
        return {
            "status": "retrieval_only",
            "answer": "已定位到右侧所列官方原文。当前未配置 DeepSeek API Key，因此本次只完成检索与原文核验，没有生成模型解释。配置密钥后，可继续完成解释、比较、归纳和改写等任务。",
            "evidence": numbered,
            "verification": {"passed": True, "checks": [{"name": "原文检索完成", "passed": True, "detail": f"返回 {len(evidence)} 条可追溯证据"}, {"name": "模型生成", "passed": True, "detail": "未配置密钥，已明确跳过"}]},
        }
    try:
        answer = DEEPSEEK.generate(question, evidence)
        verification = verify_answer(answer, evidence)
        if not verification["passed"]:
            return {
                "status": "verification_failed",
                "answer": "模型生成内容未通过独立引用校验，因此没有展示该回答。右侧仅保留已核验的官方原文；你可以重试，或改为更明确的条文任务。",
                "evidence": numbered,
                "verification": verification,
            }
        return {"status": "verified", "answer": answer, "evidence": numbered, "verification": verification}
    except DeepSeekError as error:
        return {
            "status": "model_unavailable",
            "answer": f"官方原文检索已完成，但模型暂时不可用：{error}。本次未展示未经生成的解释，右侧证据仍可正常核验。",
            "evidence": numbered,
            "verification": {"passed": False, "checks": [{"name": "模型调用", "passed": False, "detail": str(error)}]},
        }


class Handler(BaseHTTPRequestHandler):
    server_version = "LvzhengEvidence/0.1"

    def log_message(self, format: str, *args) -> None:
        sys.stdout.write(f"[api] {self.address_string()} {format % args}\n")
        sys.stdout.flush()

    def send_json(self, data: dict | list, status: int = 200) -> None:
        content = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(content)

    def read_json(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0 or length > 65536:
            raise ValueError("请求体为空或过大")
        return json.loads(self.rfile.read(length).decode("utf-8"))

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        params = urllib.parse.parse_qs(parsed.query)
        if path == "/api/health":
            self.send_json({
                "ok": True,
                "model_configured": DEEPSEEK.configured,
                "model": DEEPSEEK.model,
                "document_count": len(STORE.documents),
                "article_count": len(STORE.articles),
                "pkulaw_configured": PKULAW.configured,
            })
            return
        if path == "/api/providers":
            self.send_json({"providers": [PKULAW.status()]})
            return
        if path == "/api/documents":
            self.send_json({"documents": STORE.document_list()})
            return
        match = re.fullmatch(r"/api/documents/([^/]+)/articles/([^/]+)", path)
        if match:
            article = STORE.get_article(match.group(1), match.group(2))
            if article:
                self.send_json(article)
            else:
                self.send_json({"error": "未找到条文"}, 404)
            return
        if path in {"/api/search", "/api/legal/search"}:
            query = params.get("q", [""])[0].strip()
            document_id = params.get("document_id", [None])[0]
            source = params.get("source", ["local" if path == "/api/search" else "auto"])[0]
            if not query:
                self.send_json({"error": "请输入检索内容"}, 400)
                return
            try:
                evidence, retrieval = retrieve_evidence(query[:1000], document_id, source)
            except PkulawError as error:
                self.send_json({"error": str(error), "provider": "pkulaw"}, 502)
                return
            except ValueError as error:
                self.send_json({"error": str(error)}, 400)
                return
            self.send_json({"query": query, "evidence": numbered_evidence(evidence), "retrieval": retrieval})
            return
        self.send_json({"error": "接口不存在"}, 404)

    def do_POST(self) -> None:
        path = urllib.parse.urlparse(self.path).path
        if path != "/api/chat":
            self.send_json({"error": "接口不存在"}, 404)
            return
        try:
            data = self.read_json()
            question = str(data.get("question", "")).strip()
            document_id = data.get("document_id") or None
            source = str(data.get("source") or "auto")
            if not question:
                self.send_json({"error": "问题不能为空"}, 400)
                return
            if len(question) > 2000:
                self.send_json({"error": "单次任务不能超过 2000 字"}, 400)
                return
            try:
                evidence, retrieval = retrieve_evidence(question, document_id, source)
            except PkulawError as error:
                self.send_json({
                    "status": "source_unavailable",
                    "answer": f"本地官方文库未命中，北大法宝在线检索也未能完成：{error}。本次未调用模型补写法律内容。请检查连接配置后重试。",
                    "evidence": [],
                    "retrieval": {"source": "pkulaw", "provider": "pkulaw", "error": str(error)},
                    "verification": {"passed": False, "checks": [{"name": "在线证据检索", "passed": False, "detail": str(error)}]},
                })
                return
            payload = response_payload(question, evidence)
            payload["retrieval"] = retrieval
            self.send_json(payload)
        except (ValueError, json.JSONDecodeError) as error:
            self.send_json({"error": f"请求格式错误：{error}"}, 400)
        except Exception as error:
            self.log_error("Unhandled error: %r", error)
            self.send_json({"error": "服务暂时不可用，请查看后端日志"}, 500)


def run() -> None:
    host = os.environ.get("API_HOST", "127.0.0.1")
    port = int(os.environ.get("API_PORT", "8787"))
    server = ThreadingHTTPServer((host, port), Handler)
    print(f"法律证据 API 已启动：http://{host}:{port}；条文数：{len(STORE.articles)}；DeepSeek：{'已配置' if DEEPSEEK.configured else '未配置'}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    run()
