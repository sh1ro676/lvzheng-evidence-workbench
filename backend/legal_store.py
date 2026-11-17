from __future__ import annotations

import json
import math
import re
from collections import Counter
from pathlib import Path
from typing import Iterable

ARTICLE_QUERY_RE = re.compile(r"第\s*([〇零一二三四五六七八九十百千万两0-9]+)\s*条")
# 刑法大量使用「第133条之一」这类增补条。若只按基础条号建索引，
# 增补条会覆盖同号的主条（第133条之二把交通肇事罪挤掉），必须连「之X」一起作键。
ARTICLE_KEY_RE = re.compile(
    r"第\s*([〇零一二三四五六七八九十百千万两0-9]+)\s*条(?:\s*之\s*([一二三四五六七八九十]+))?"
)


def article_key(label: str) -> str | None:
    """把条目标签转成检索键：『第133条』→ 133，『第133条之一』→ 133-1。"""
    match = ARTICLE_KEY_RE.search(label or "")
    if not match:
        return None
    number = chinese_number_to_int(match.group(1))
    if match.group(2):
        return f"{number}-{chinese_number_to_int(match.group(2))}"
    return str(number)
# 用户常写「刑法44条」「四十四条」，省略"第"字。若不先补成「第44条」，
# 条号解析会失败并回落到语义检索，语义检索会把 44 当成近义数字，
# 返回第4条、修正案(七)第四条这类噪声，真正的第44条反而丢失。
BARE_ARABIC_ARTICLE_RE = re.compile(r"(?<!第)(?<![0-9])([0-9]{1,4})\s*条")
BARE_CHINESE_ARTICLE_RE = re.compile(r"(?<!第)(?<![〇零一二三四五六七八九十百千万两])([〇零一二三四五六七八九十百千万两]{2,7})\s*条")


def normalize_article_refs(text: str) -> str:
    """把「44条」「四十四条」补成「第44条」，并统一全角数字。"""
    import unicodedata

    text = unicodedata.normalize("NFKC", text or "")
    text = BARE_ARABIC_ARTICLE_RE.sub(r"第\1条", text)
    text = BARE_CHINESE_ARTICLE_RE.sub(r"第\1条", text)
    return text
CHINESE_DIGITS = {"〇": 0, "零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
CHINESE_UNITS = {"十": 10, "百": 100, "千": 1000}
QUERY_EXPANSIONS = {
    "离婚冷静期": ["三十日", "撤回", "离婚登记"],
    "自甘风险": ["自愿参加", "具有一定风险", "文体活动"],
    "自助行为": ["情况紧迫", "合法权益受到侵害", "扣留"],
    "无过错责任": ["不论行为人有无过错"],
    "过错推定": ["推定行为人有过错", "不能证明自己没有过错"],
    "高空抛物": ["从建筑物中抛掷物品", "建筑物中坠落物品"],
    "隐私": ["私人生活安宁", "私密空间", "私密活动", "私密信息"],
}
STOP_BIGRAMS = {
    "请问", "什么", "怎么", "怎样", "如何", "一下", "法律", "规定", "民法", "法典", "可以", "是否",
    "帮我", "进行", "解释", "说明", "分析", "以及", "相关", "内容", "问题", "回答", "原文", "整理",
}


def chinese_number_to_int(value: str) -> int:
    if value.isdigit():
        return int(value)
    total = 0
    current = 0
    for char in value:
        if char in CHINESE_DIGITS:
            current = CHINESE_DIGITS[char]
        elif char in CHINESE_UNITS:
            unit = CHINESE_UNITS[char]
            total += (current or 1) * unit
            current = 0
        elif char == "万":
            total = (total + current) * 10000
            current = 0
    return total + current


def normalize_text(text: str) -> str:
    return re.sub(r"\s+", "", text).replace("　", "")


def tokenize(text: str) -> list[str]:
    normalized = normalize_text(text).lower()
    tokens = re.findall(r"[a-z]+[a-z0-9_-]*|\d+(?:\.\d+)?", normalized)
    for sequence in re.findall(r"[\u4e00-\u9fff]+", normalized):
        if len(sequence) == 1:
            tokens.append(sequence)
        else:
            tokens.extend(sequence[index:index + 2] for index in range(len(sequence) - 1))
    return [token for token in tokens if token not in STOP_BIGRAMS]


class LegalStore:
    def __init__(self, data_dir: Path | str | None = None):
        root = Path(__file__).resolve().parent / "data" / "documents"
        self.data_dir = Path(data_dir) if data_dir else root
        self.documents: list[dict] = []
        self.articles: list[dict] = []
        self._article_lookup: dict[tuple[str, str], dict] = {}
        self._article_by_id: dict[tuple[str, str], dict] = {}
        self._document_by_id: dict[str, dict] = {}
        self._tokens: list[Counter] = []
        self._document_frequency: Counter = Counter()
        self._idf: dict[str, float] = {}
        self._average_length = 1.0
        self.reload()

    def reload(self) -> None:
        self.documents = []
        self.articles = []
        self._article_lookup = {}
        self._article_by_id = {}
        self._document_by_id = {}
        for path in sorted(self.data_dir.glob("*.json")):
            document = json.loads(path.read_text(encoding="utf-8"))
            summary = {key: value for key, value in document.items() if key != "articles"}
            self.documents.append(summary)
            self._document_by_id[document["document_id"]] = summary
            for article in document["articles"]:
                enriched = {**article, "document": summary}
                key = article_key(article["article_label"])
                if key:
                    self._article_lookup[(document["document_id"], key)] = enriched
                self._article_by_id[(document["document_id"], article["article_id"])] = enriched
                self.articles.append(enriched)
        self._build_index()

    def _build_index(self) -> None:
        self._tokens = [Counter(tokenize(article["article_label"] + article["text"])) for article in self.articles]
        self._document_frequency = Counter()
        for tokens in self._tokens:
            self._document_frequency.update(tokens.keys())
        count = max(len(self.articles), 1)
        self._idf = {
            token: math.log(1 + (count - frequency + 0.5) / (frequency + 0.5))
            for token, frequency in self._document_frequency.items()
        }
        lengths = [sum(tokens.values()) for tokens in self._tokens]
        self._average_length = sum(lengths) / max(len(lengths), 1)

    def document_list(self) -> list[dict]:
        return self.documents

    def get_article(self, document_id: str, article_id: str) -> dict | None:
        article = self._article_by_id.get((document_id, article_id))
        return self.to_evidence(article) if article else None

    def to_evidence(self, article: dict, score: float = 1.0, match_type: str = "exact") -> dict:
        document = article["document"]
        return {
            "document_id": document["document_id"],
            "article_id": article["article_id"],
            "document_title": document["title"],
            "article_label": article["article_label"],
            "section_path": article["section_path"],
            "text": article["text"],
            "content_hash": article["content_hash"],
            "source_name": document["source_name"],
            "source_url": document["source_url"],
            "snapshot_sha256": document["snapshot_sha256"],
            "published_at": document["published_at"],
            "effective_at": document["effective_at"],
            "status": document["status"],
            "source_notice": document.get("source_notice", "国家法律法规数据库电子文本；与标准文本不一致时，请引用标准文本。"),
            "match_type": match_type,
            "relevance": round(max(0.0, min(score, 1.0)), 3),
        }

    def exact_matches(self, query: str, document_id: str | None = None) -> list[dict]:
        query = normalize_article_refs(query)
        explicit_titles = re.findall(r"《([^》]+)》", query)
        candidates = [document_id] if document_id else list(self._document_by_id)
        if explicit_titles and not document_id:
            def belongs(item: tuple[str, dict]) -> bool:
                title = re.sub(r"^中华人民共和国", "", item[1]["title"])
                return any(
                    re.sub(r"^中华人民共和国", "", requested) in title
                    for requested in explicit_titles
                )
            candidates = [item[0] for item in self._document_by_id.items() if belongs(item)]

        keys: list[str] = []
        for match in ARTICLE_KEY_RE.finditer(query):
            number = chinese_number_to_int(match.group(1))
            key = f"{number}-{chinese_number_to_int(match.group(2))}" if match.group(2) else str(number)
            if key not in keys:
                keys.append(key)
        matches = []
        for key in keys:
            for candidate in candidates:
                article = self._article_lookup.get((candidate, key))
                if article:
                    matches.append(self.to_evidence(article))
        if not document_id and len(matches) > 1:
            # 只说"第44条"不说法名时，四部法都有第44条；
            # 按问题与条文用词的贴合度排序，避免总是民法典排第一。
            query_tokens = set(tokenize(query))
            matches.sort(key=lambda item: -len(query_tokens & set(tokenize(item.get("text", "")))))
        return matches

    def search(self, query: str, document_id: str | None = None, limit: int = 6) -> list[dict]:
        exact = self.exact_matches(query, document_id)
        if exact:
            return exact[:limit]
        expanded_query = query
        for phrase, additions in QUERY_EXPANSIONS.items():
            if phrase in query:
                expanded_query += " " + " ".join(additions)
        query_tokens = tokenize(expanded_query)
        if not query_tokens:
            return []
        query_counts = Counter(query_tokens)
        scored: list[tuple[float, int]] = []
        for index, article in enumerate(self.articles):
            if document_id and article["document"]["document_id"] != document_id:
                continue
            tokens = self._tokens[index]
            length = sum(tokens.values())
            score = 0.0
            for token, query_weight in query_counts.items():
                frequency = tokens.get(token, 0)
                if not frequency:
                    continue
                numerator = frequency * 2.2
                denominator = frequency + 1.2 * (0.25 + 0.75 * length / self._average_length)
                score += self._idf.get(token, 0.0) * numerator / denominator * (1 + 0.15 * (query_weight - 1))
            normalized_article = normalize_text(article["text"])
            for phrase, additions in QUERY_EXPANSIONS.items():
                if phrase in query:
                    for addition in additions:
                        if addition in normalized_article:
                            score += 2.5
            if score > 0:
                scored.append((score, index))
        if not scored:
            return []
        scored.sort(reverse=True)
        best_score, best_index = scored[0]
        # Precision first: weak incidental matches must become an explicit abstention.
        if best_score < 18.0:
            return []
        results = []
        for score, index in scored:
            if score < max(3.0, best_score * 0.20):
                continue
            relevance = score / (score + 8.0)
            results.append(self.to_evidence(self.articles[index], relevance, "hybrid"))
            if len(results) >= limit:
                break
        return results

    def article_texts(self, evidence: Iterable[dict]) -> str:
        return "\n".join(normalize_text(item["article_label"] + item["text"]) for item in evidence)
