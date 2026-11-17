from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from retrieval_tasks import network_timeout


class WebSearchError(RuntimeError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def validate_public_url(url: str) -> str:
    """Allow public HTTP(S) pages only; never fetch local or credentialed URLs."""
    try:
        parsed = urllib.parse.urlparse(url.strip())
        host = parsed.hostname
        if parsed.scheme not in {"http", "https"} or not host or parsed.username or parsed.password:
            raise ValueError("仅支持公开 HTTP(S) 地址")
        if host.lower() == "localhost" or host.lower().endswith(".localhost"):
            raise ValueError("不允许读取本机地址")
        try:
            addresses = [info[4][0] for info in socket.getaddrinfo(host, parsed.port or 443, type=socket.SOCK_STREAM)]
        except socket.gaierror as error:
            raise ValueError("网页域名无法解析") from error
        for address in addresses:
            ip = ipaddress.ip_address(address)
            if not ip.is_global:
                raise ValueError("不允许读取内网或保留地址")
        return urllib.parse.urlunparse(parsed._replace(fragment=""))
    except (ValueError, UnicodeError) as error:
        raise WebSearchError(f"网页地址不安全或无效：{error}") from error


class DeepSeekWebClient:
    """Small standard-library client for DeepSeek's official Search and Fetch APIs."""

    def __init__(self, api_key: str | None = None, base_url: str | None = None):
        self.api_key = (api_key if api_key is not None else os.environ.get("DEEPSEEK_API_KEY", "")).strip()
        self.base_url = (base_url or os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com")).rstrip("/")
        self.timeout = float(os.environ.get("DEEPSEEK_WEB_TIMEOUT", "45"))
        self.max_content_chars = int(os.environ.get("DEEPSEEK_WEB_CONTENT_CHARS", "16000"))
        # 同一网址 24 小时内不重复抓取：四校检索常反复命中同几篇论文。
        self.cache_dir = Path(os.environ.get("WEB_CACHE_DIR", Path(__file__).resolve().parent / "cache" / "web"))
        self.cache_ttl = float(os.environ.get("WEB_CACHE_TTL", "86400"))

    @property
    def configured(self) -> bool:
        return bool(self.api_key)

    def _post(self, path: str, payload: dict) -> dict:
        if not self.configured:
            raise WebSearchError("未配置 DEEPSEEK_API_KEY，无法使用联网检索")
        request = urllib.request.Request(
            f"{self.base_url}{path}",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=network_timeout(self.timeout)) as response:
                raw = response.read(2_000_001)
                if len(raw) > 2_000_000:
                    raise WebSearchError("联网工具返回内容过大")
                result = json.loads(raw.decode("utf-8"))
        except urllib.error.HTTPError as error:
            detail = error.read().decode("utf-8", errors="replace")[:500]
            raise WebSearchError(f"联网工具返回 {error.code}：{detail}") from error
        except (urllib.error.URLError, TimeoutError, socket.timeout, json.JSONDecodeError, UnicodeError) as error:
            raise WebSearchError(f"联网工具调用失败：{error}") from error
        if not isinstance(result, dict):
            raise WebSearchError("联网工具返回格式不正确")
        return result

    def search(self, query: str, original_question: str, count: int = 8) -> list[dict]:
        query = query.strip()[:300]
        if not query:
            return []
        result = self._post("/v1/tools/search", {
            "query": query,
            "count": max(1, min(count, 10)),
            "ori_question": original_question[:2000],
            "need_content": False,
        })
        results = result.get("results", [])
        if not isinstance(results, list):
            raise WebSearchError("搜索接口未返回 results 列表")
        return [item for item in results if isinstance(item, dict) and item.get("url")]

    def fetch(self, url: str) -> dict:
        safe_url = validate_public_url(url)
        cached = self._read_cache(safe_url)
        if cached is not None:
            return cached
        result = self._post("/v1/tools/fetch", {"url": safe_url})
        content = result.get("content")
        if not isinstance(content, str) or not content.strip():
            raise WebSearchError("网页没有可读取的正文")
        content = content.strip()
        if len(content) < 200:
            raise WebSearchError("网页正文过短，可能是验证页或异常页，未纳入证据")
        blocked_markers = ("请输入验证码", "环境异常", "访问验证", "安全验证", "请完成验证")
        if len(content) < 5000 and any(marker in content[:1500] for marker in blocked_markers):
            raise WebSearchError("网页返回验证或异常提示，未纳入证据")
        payload = {
            "title": str(result.get("title") or "").strip(),
            "content": content[:self.max_content_chars],
            "url": safe_url,
        }
        self._write_cache(safe_url, payload)
        return payload

    # ---------------------------------------------------------------- 正文缓存
    def _cache_path(self, url: str) -> Path | None:
        if self.cache_ttl <= 0:
            return None
        digest = hashlib.sha1(url.encode("utf-8")).hexdigest()
        return self.cache_dir / f"{digest}.json"

    def _read_cache(self, url: str) -> dict | None:
        path = self._cache_path(url)
        if path is None or not path.exists():
            return None
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if time.time() - float(record.get("saved_at", 0)) > self.cache_ttl:
            return None
        payload = record.get("payload")
        return payload if isinstance(payload, dict) and payload.get("content") else None

    def _write_cache(self, url: str, payload: dict) -> None:
        path = self._cache_path(url)
        if path is None:
            return
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({"saved_at": time.time(), "payload": payload},
                                       ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass


OFFICIAL_DOMAIN_SUFFIXES = ("edu.cn", "gov.cn")
RELIABLE_MEDIA_DOMAINS = (
    "news.cn", "xinhuanet.com", "people.com.cn", "cctv.com",
    "chinanews.com.cn", "chinanews.com", "legaldaily.com.cn",
    "thepaper.cn", "caixin.com", "yicai.com", "chinacourt.org",
)
SECONDARY_HOSTS = (
    "163.com", "sohu.com", "toutiao.com", "baijiahao.baidu.com",
    "zhihu.com", "weixin.qq.com", "360doc.com", "douban.com",
    "baike.baidu.com", "wenku.baidu.com", "doc88.com", "docin.com",
)
PRIMARY_RECORD_MARKERS = (
    "演讲实录", "讲话实录", "发言实录", "讲座实录", "访谈实录",
    "活动实录", "文字实录", "庭审实录", "论文原文", "学术论文原文",
)
EVENT_PAGE_MARKERS = ("预告", "通知", "活动安排", "出席", "参加", "主讲")
PAPER_MARKERS = (
    "内容提要", "内容摘要", "【摘要】", "关键词", "作者单位",
    "基金项目", "中图分类号", "参考文献",
)


def _host_is(host: str, domain: str) -> bool:
    return host == domain or host.endswith("." + domain)


def grade_web_evidence(url: str, title: str, content: str) -> dict:
    """按来源域名和已抓取正文确定性分级；搜索摘要与引号不参与升级。"""
    host = (urllib.parse.urlparse(url).hostname or "").lower().rstrip(".")
    body_start = content[:1200]
    metadata = f"{title}\n{body_start}"
    is_official = any(_host_is(host, suffix) for suffix in OFFICIAL_DOMAIN_SUFFIXES)
    is_secondary_host = any(_host_is(host, domain) for domain in SECONDARY_HOSTS)
    is_reliable_media = any(_host_is(host, domain) for domain in RELIABLE_MEDIA_DOMAINS) and not any(
        marker in body_start for marker in ("澎湃号", "百家号作者", "搜狐号", "网易号")
    )

    # 标题中的“实录”可能只是转载标题或预告措辞；一手等级必须由正文特征支撑。
    record_like = any(marker in body_start for marker in PRIMARY_RECORD_MARKERS)
    paper_like = sum(marker in body_start for marker in PAPER_MARKERS) >= 2 and "作者" in body_start
    if is_official and not is_secondary_host and (record_like or paper_like):
        return {
            "evidence_tier": "primary",
            "evidence_tier_label": "一手资料",
            "attribution_scope": "official_record",
            "source_notice": "官方域名且正文具有实录或论文原文特征，可支持其明确记载的内容；不得超出文本推断发言、观点或案件评价。",
        }
    if is_official and not is_secondary_host:
        if any(marker in metadata for marker in EVENT_PAGE_MARKERS):
            notice = ("官方活动页或预告仅支持页面明确记载的出席活动、身份与安排，"
                      "不证明具体发言或观点；没有实录时不得写成教授原话。")
            scope = "official_event"
        else:
            notice = "官方网页但并非可确认的实录或论文原文，仅支持其明确记载的事实，不据此补写具体发言或观点。"
            scope = "official_page"
        return {
            "evidence_tier": "reliable_report",
            "evidence_tier_label": "官方活动页" if scope == "official_event" else "官方网页",
            "attribution_scope": scope,
            "source_notice": notice,
        }
    if is_reliable_media and not is_secondary_host:
        return {
            "evidence_tier": "reliable_report",
            "evidence_tier_label": "媒体报道",
            "attribution_scope": "media_report",
            "source_notice": "可靠媒体网页正文，应写成“某媒体报道/转述”；不得冒充教授直接原话、司法机关认定或确定案件评价。",
        }
    return {
        "evidence_tier": "secondary_unverified",
        "evidence_tier_label": "转述待核",
        "attribution_scope": "unverified_secondary",
        "source_notice": ("自媒体、内容聚合/转载站或无法确认来源的网页；不得作为教授直接原话或确定案件评价，"
                          "只能明确标注未核实，或不采用。正文出现引号、实录等字样不提高等级。"),
    }


def normalize_web_evidence(search_item: dict, fetched: dict, query: str) -> dict:
    url = fetched["url"]
    content = fetched["content"]
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:20]
    content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
    host = urllib.parse.urlparse(url).hostname or "公开网页"
    site_name = str(search_item.get("site_name") or host).strip()
    title = str(fetched.get("title") or search_item.get("title") or site_name).strip()
    grading = grade_web_evidence(url, title, content)
    try:
        relevance = max(0.0, min(float(search_item.get("rerank_score") or 0.5), 1.0))
    except (TypeError, ValueError):
        relevance = 0.5
    timestamp = utc_now()
    return {
        "document_id": f"web-{digest}",
        "article_id": f"page-{digest}",
        "document_title": title,
        "article_label": "网页全文",
        "section_path": [site_name, "DeepSeek 联网检索"],
        "text": content,
        "content_hash": content_hash,
        "snapshot_sha256": content_hash,
        "provider": "deepseek_web",
        "source_name": site_name,
        "source_url": url,
        "published_at": str(search_item.get("date") or ""),
        "effective_at": "",
        "status": "公开网页资料",
        **grading,
        "match_type": "web",
        "relevance": round(relevance, 3),
        "queried_at": timestamp,
        "web_query": query,
    }
