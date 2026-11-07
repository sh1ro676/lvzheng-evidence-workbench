from __future__ import annotations

import hashlib
import html
import json
import os
import re
import threading
import time
import urllib.error
import urllib.request
from collections import OrderedDict
from datetime import datetime, timezone
from typing import Any

from legal_store import ARTICLE_QUERY_RE, chinese_number_to_int

GATEWAY = "https://apim-gateway.pkulaw.com"
ENDPOINTS = {
    "fatiao": f"{GATEWAY}/mcp-fatiao",
    "law_keyword": f"{GATEWAY}/mcp-law",
    "law_semantic": f"{GATEWAY}/mcp-law-search-service",
}
PREFERRED_TOOLS = {
    "fatiao": "get_law_article",
    "law_keyword": "search_law_articles_by_keyword",
    "law_semantic": "search_law_articles",
}
TITLE_RE = re.compile(r"《([^》]+)》")
COMPLEX_QUERY_RE = re.compile(r"什么|如何|怎么|怎样|哪些|为什么|为何|讲讲|解释|理解|分析|怎么办|能否|是否|案情|应当|责任|适用")
HISTORY_QUERY_RE = re.compile(r"历史|旧版|废止|失效|修正|沿革")


class PkulawError(RuntimeError):
    """A safe, user-displayable MCP connection or tool error."""


def _plain_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (list, tuple, set)):
        return "、".join(_plain_text(item) for item in value if item is not None)
    if not isinstance(value, str):
        return str(value)
    text = html.unescape(re.sub(r"<[^>]+>", "", value))
    return re.sub(r"[ \t\r\f\v]+", " ", text).strip()


def _first(record: dict, aliases: tuple[str, ...]) -> Any:
    for key in aliases:
        value = record.get(key)
        if value is not None and value != "":
            return value
    return None


class PkulawMCPClient:
    """Small Streamable HTTP MCP client with runtime tool discovery."""

    def __init__(self, token: str | None = None, timeout: float = 45, opener=None):
        self.token = (token if token is not None else os.environ.get("PKULAW_AUTH_TOKEN", "")).strip()
        self.timeout = timeout
        self.opener = opener or urllib.request.urlopen

    @property
    def configured(self) -> bool:
        return bool(self.token)

    def _safe_error(self, value: Any) -> str:
        text = str(value)
        if self.token:
            text = text.replace(self.token, "[已隐藏]")
        return re.sub(r"Bearer\s+\S+", "Bearer [已隐藏]", text, flags=re.IGNORECASE)[:800]

    def _decode_response(self, body: str, content_type: str, request_id: int | None) -> dict:
        if "text/event-stream" in content_type.lower() or body.lstrip().startswith(("event:", "data:")):
            events: list[str] = []
            data_lines: list[str] = []
            for line in body.splitlines() + [""]:
                if not line.strip():
                    if data_lines:
                        events.append("\n".join(data_lines))
                        data_lines = []
                elif line.startswith("data:"):
                    data_lines.append(line[5:].lstrip())
            for event in events:
                try:
                    payload = json.loads(event)
                except json.JSONDecodeError:
                    continue
                if request_id is None or payload.get("id") == request_id:
                    return payload
            raise PkulawError("MCP 的 SSE 响应中没有找到当前请求结果")
        if not body.strip():
            return {}
        try:
            return json.loads(body)
        except json.JSONDecodeError as error:
            raise PkulawError("MCP 返回了无法解析的响应") from error

    def _request(self, endpoint: str, method: str, params: dict | None = None,
                 request_id: int | None = None, session_id: str | None = None) -> tuple[dict, str | None]:
        if not self.configured:
            raise PkulawError("尚未配置 PKULAW_AUTH_TOKEN")
        payload: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if request_id is not None:
            payload["id"] = request_id
        if params is not None:
            payload["params"] = params
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "MCP-Protocol-Version": "2025-03-26",
        }
        if session_id:
            headers["Mcp-Session-Id"] = session_id
        request = urllib.request.Request(
            endpoint, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers=headers, method="POST",
        )
        try:
            with self.opener(request, timeout=self.timeout) as response:
                body = response.read(8 * 1024 * 1024 + 1).decode("utf-8", errors="replace")
                if len(body) > 8 * 1024 * 1024:
                    raise PkulawError("MCP 响应过大，已停止读取")
                content_type = response.headers.get("Content-Type", "")
                returned_session = response.headers.get("Mcp-Session-Id") or session_id
        except urllib.error.HTTPError as error:
            detail = error.read().decode("utf-8", errors="replace")[:500]
            if error.code in (401, 403):
                message = "北大法宝鉴权失败，请检查 Token 是否有效及是否已订阅对应能力"
            elif error.code == 429:
                message = "北大法宝请求受限，请稍后重试或检查账户积分"
            else:
                message = f"北大法宝网关返回 HTTP {error.code}：{detail}"
            raise PkulawError(self._safe_error(message)) from error
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            raise PkulawError(f"北大法宝连接失败：{self._safe_error(error)}") from error
        result = self._decode_response(body, content_type, request_id)
        if "error" in result:
            error = result["error"]
            detail = error.get("message", error) if isinstance(error, dict) else error
            raise PkulawError(f"MCP 调用失败：{self._safe_error(detail)}")
        return result.get("result", result), returned_session

    def list_tools(self, service: str) -> list[dict]:
        endpoint = ENDPOINTS[service]
        _, session_id = self._request(endpoint, "initialize", {
            "protocolVersion": "2025-03-26",
            "capabilities": {},
            "clientInfo": {"name": "lvzheng-evidence-workbench", "version": "0.1.0"},
        }, request_id=1)
        self._request(endpoint, "notifications/initialized", session_id=session_id)
        result, _ = self._request(endpoint, "tools/list", {}, request_id=2, session_id=session_id)
        tools = result.get("tools", [])
        if not isinstance(tools, list) or not tools:
            raise PkulawError("当前账号未发现该服务的可用工具，请检查订阅能力")
        return tools

    def call_tool(self, service: str, tool_name: str, arguments: dict) -> dict:
        endpoint = ENDPOINTS[service]
        _, session_id = self._request(endpoint, "initialize", {
            "protocolVersion": "2025-03-26",
            "capabilities": {},
            "clientInfo": {"name": "lvzheng-evidence-workbench", "version": "0.1.0"},
        }, request_id=1)
        self._request(endpoint, "notifications/initialized", session_id=session_id)
        result, _ = self._request(endpoint, "tools/call", {
            "name": tool_name, "arguments": arguments,
        }, request_id=2, session_id=session_id)
        if result.get("isError"):
            detail = "；".join(_plain_text(item.get("text")) for item in result.get("content", []) if isinstance(item, dict))
            raise PkulawError(self._safe_error(detail or "工具返回错误"))
        return result


def extract_law_title(question: str) -> str:
    match = TITLE_RE.search(question)
    if match:
        return match.group(1)
    for name in ("民法典", "刑法", "刑事诉讼法", "民事诉讼法", "劳动合同法"):
        if name in question:
            return name
    return ""


def select_strategy(question: str) -> str:
    if extract_law_title(question) and ARTICLE_QUERY_RE.search(question):
        return "fatiao"
    if len(question.strip()) > 24 or COMPLEX_QUERY_RE.search(question):
        return "law_semantic"
    return "law_keyword"


def schema_accepts(spec: dict, expected_type: str) -> bool:
    if spec.get("type") == expected_type:
        return True
    return any(variant.get("type") == expected_type for variant in spec.get("anyOf", []) if isinstance(variant, dict))


def keyword_query(question: str) -> str:
    title = extract_law_title(question)
    cleaned = TITLE_RE.sub("", question)
    if title:
        cleaned = cleaned.replace(title, "", 1)
    cleaned = re.sub(r"^(?:请|帮我|给我|查一下|查询|检索|找找|找出)", "", cleaned)
    return cleaned.strip(" \t，,。；;：:？?！!") or question


def build_arguments(tool: dict, question: str, service: str) -> dict:
    schema = tool.get("inputSchema") or {}
    properties = schema.get("properties") or {}
    required = schema.get("required") or []
    title = extract_law_title(question)
    article_match = ARTICLE_QUERY_RE.search(question)
    article_number = chinese_number_to_int(article_match.group(1)) if article_match else None
    arguments: dict[str, Any] = {}
    names = list(properties)
    if not names:
        if service == "fatiao" and title and article_number is not None:
            return {"law_title": title, "article_number": str(article_number)}
        return {"query_text": question} if service == "law_semantic" else {"fulltext": keyword_query(question)}

    for name in names:
        key = name.lower()
        spec = properties.get(name) or {}
        value: Any = None
        if key in {"title", "law_title", "lawtitle", "law_name", "lawname", "fgmc"}:
            value = title
        elif "tiao" in key or key in {"article", "article_num", "article_number", "articlenumber", "ft_num", "ftnum"}:
            value = article_number
        elif key == "fulltext":
            value = keyword_query(question)
        elif key in {"searchkey", "query", "query_text", "querytext", "keyword", "keywords", "search", "search_text", "searchtext", "text"}:
            value = question
        elif key == "timeliness" and not HISTORY_QUERY_RE.search(question):
            value = "现行有效" if schema_accepts(spec, "string") else ["现行有效"]
        elif key in {"page", "page_num", "pagenum", "pageindex", "page_index"}:
            value = 1
        elif key in {"page_size", "pagesize", "size", "limit", "top_k", "topk", "return_num"}:
            value = 5
        if name in required and value is None:
            if service == "fatiao" and title and article_number is not None:
                value = title if len(arguments) == 0 else article_number
            else:
                value = question
        if value is not None:
            is_article_field = "tiao" in key or key in {"article", "article_num", "article_number", "articlenumber", "ft_num", "ftnum"}
            if is_article_field and schema_accepts(spec, "string"):
                value = str(value)
            arguments[name] = value

    missing = [name for name in required if name not in arguments or arguments[name] in (None, "")]
    if missing:
        raise PkulawError(f"无法从问题中确定工具必填参数：{', '.join(missing)}；请补充法规名称或条号")
    if not arguments:
        arguments[names[0]] = question
    return arguments


def _tool_payload(result: dict) -> Any:
    if result.get("structuredContent") is not None:
        return result["structuredContent"]
    blocks = result.get("content") or []
    parsed = []
    for block in blocks:
        if not isinstance(block, dict) or block.get("type") != "text":
            continue
        text = block.get("text", "")
        try:
            parsed.append(json.loads(text))
        except (json.JSONDecodeError, TypeError):
            parsed.append(text)
    return parsed[0] if len(parsed) == 1 else parsed


def _records(payload: Any) -> list[dict]:
    found: list[dict] = []

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            if _first(value, ("text", "content", "article_content", "articleContent", "law_content", "lawContent", "fulltext", "fullText", "条文内容", "法条内容")):
                found.append(value)
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)

    walk(payload)
    return found


def normalize_evidence(result: dict, question: str, match_type: str) -> list[dict]:
    payload = _tool_payload(result)
    records = _records(payload)
    if not records and isinstance(payload, str) and payload.strip():
        records = [{"content": payload}]
    if not records and isinstance(payload, list) and all(isinstance(item, str) for item in payload):
        records = [{"content": "\n".join(payload)}]
    title_hint = extract_law_title(question)
    article_match = ARTICLE_QUERY_RE.search(question)
    queried_at = datetime.now(timezone.utc).isoformat()
    evidence = []
    seen = set()
    for index, record in enumerate(records, start=1):
        title = _plain_text(_first(record, ("document_title", "documentTitle", "law_title", "lawTitle", "title", "law_name", "lawName", "fgmc", "name", "法规名称", "法规名"))) or title_hint or "北大法宝检索结果"
        label = _plain_text(_first(record, ("article_label", "articleLabel", "article_number", "articleNumber", "tiao_num", "tiaoNum", "ft_num", "ftNum", "article", "条号")))
        if not label and article_match and len(records) == 1:
            label = article_match.group(0)
        if re.fullmatch(r"\d+(?:\.\d+)?", label):
            label = f"第{label}条"
        text = _plain_text(_first(record, ("text", "content", "article_content", "articleContent", "law_content", "lawContent", "fulltext", "fullText", "条文内容", "法条内容", "内容")))
        if label and text.startswith(label):
            text = text[len(label):].lstrip()
        if not text:
            continue
        citation = record.get("citation") if isinstance(record.get("citation"), dict) else {}
        source_id = _plain_text(_first(record, ("document_id", "documentId", "law_id", "lawId", "gid", "id", "法规ID")) or citation.get("gid"))
        url = _plain_text(_first(record, ("source_url", "sourceUrl", "url", "link", "detail_url", "detailUrl", "详情链接")) or citation.get("url"))
        identity = (title, label, text)
        if identity in seen:
            continue
        seen.add(identity)
        evidence.append({
            "provider": "pkulaw",
            "document_id": source_id or f"pkulaw-{hashlib.sha256(title.encode('utf-8')).hexdigest()[:16]}",
            "article_id": source_id or f"pkulaw-result-{index}",
            "document_title": title,
            "article_label": label or "检索命中",
            "section_path": [],
            "text": text,
            "content_hash": hashlib.sha256(text.encode("utf-8")).hexdigest(),
            "source_name": "北大法宝",
            "source_url": url,
            "snapshot_sha256": "",
            "published_at": _plain_text(_first(record, ("published_at", "publish_date", "issue_date", "promulgation_date", "fbrq", "公布日期", "发布日期"))),
            "effective_at": _plain_text(_first(record, ("effective_at", "effective_date", "implement_date", "ssrq", "施行日期", "实施日期"))),
            "status": _plain_text(_first(record, ("status", "timeliness", "validity", "sxx", "时效性", "效力状态"))) or "以来源页面为准",
            "effect_level": _plain_text(_first(record, ("category", "effect_level", "effectiveness", "effectiveness_level", "xljb", "效力级别"))),
            "source_notice": "北大法宝 MCP 实时返回；正式引用前请打开来源链接，核对标准原文、效力与适用版本。",
            "match_type": match_type,
            "relevance": 1.0 if match_type == "exact" else 0.8,
            "queried_at": queried_at,
        })
    return evidence


class PkulawProvider:
    def __init__(self, client: PkulawMCPClient | None = None, cache_ttl: int = 900):
        self.client = client or PkulawMCPClient()
        self.cache_ttl = cache_ttl
        self._cache: OrderedDict[str, tuple[float, dict]] = OrderedDict()
        self._lock = threading.Lock()

    @property
    def configured(self) -> bool:
        return self.client.configured

    def status(self) -> dict:
        return {
            "provider": "pkulaw",
            "name": "北大法宝 MCP",
            "configured": self.configured,
            "capabilities": ["精准法条", "法规关键词检索", "法规语义检索"],
            "strategy": "本地官方法条优先；在线精准／关键词优先，语义检索兜底",
            "cache_ttl_seconds": self.cache_ttl,
        }

    def _call_service(self, service: str, question: str) -> dict:
        tools = self.client.list_tools(service)
        preferred = PREFERRED_TOOLS[service]
        tool = next((item for item in tools if item.get("name") == preferred), tools[0])
        arguments = build_arguments(tool, question, service)
        result = self.client.call_tool(service, tool["name"], arguments)
        match_type = "exact" if service == "fatiao" else ("semantic" if service == "law_semantic" else "keyword")
        return {
            "provider": "pkulaw",
            "strategy": service,
            "tool": tool["name"],
            "arguments": arguments,
            "evidence": normalize_evidence(result, question, match_type),
            "queried_at": datetime.now(timezone.utc).isoformat(),
            "cached": False,
        }

    def search(self, question: str, strategy: str | None = None) -> dict:
        if not self.configured:
            raise PkulawError("尚未配置 PKULAW_AUTH_TOKEN")
        retrieval_question = question
        correction_notice = None
        if strategy is None:
            retrieval_question, replacements = re.subn(r"低\s*(\d+)\s*条", r"第\1条", question)
            if replacements:
                correction_notice = f"已将“低{re.search(r'低\s*(\d+)\s*条', question).group(1)}条”按“第{re.search(r'低\s*(\d+)\s*条', question).group(1)}条”理解，并改用精准法条检索；请核对。"
        selected = strategy or select_strategy(retrieval_question)
        cache_key = f"{selected}\n{question.strip()}"
        now = time.monotonic()
        with self._lock:
            cached = self._cache.get(cache_key)
            if cached and now - cached[0] < self.cache_ttl:
                self._cache.move_to_end(cache_key)
                return {**cached[1], "cached": True}
        response = self._call_service(selected, retrieval_question)
        if selected == "law_keyword" and not response["evidence"]:
            response = self._call_service("law_semantic", retrieval_question)
            response["fallback_from"] = "law_keyword"
        if correction_notice:
            response["notice"] = correction_notice
            response["normalized_query"] = retrieval_question
        with self._lock:
            self._cache[cache_key] = (now, response)
            self._cache.move_to_end(cache_key)
            while len(self._cache) > 64:
                self._cache.popitem(last=False)
        return response
