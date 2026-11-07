from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request

from legal_store import ARTICLE_QUERY_RE, chinese_number_to_int, normalize_text


class DeepSeekError(RuntimeError):
    pass


class DeepSeekClient:
    def __init__(self):
        self.api_key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
        self.model = os.environ.get("DEEPSEEK_MODEL", "deepseek-chat")
        self.base_url = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com").rstrip("/")

    @property
    def configured(self) -> bool:
        return bool(self.api_key)

    def generate(self, question: str, evidence: list[dict], correction_notes: list[str] | None = None,
                 previous_answer: str | None = None) -> str:
        if not self.configured:
            raise DeepSeekError("未配置 DEEPSEEK_API_KEY")
        evidence_text = "\n\n".join(
            f"【证据{index}】\n文献：{item['document_title']}\n位置：{' / '.join(item['section_path'])} / {item['article_label']}\n"
            f"效力：{item['status']}；公布：{item['published_at']}；施行：{item['effective_at']}\n原文：{item['text']}"
            for index, item in enumerate(evidence, start=1)
        )
        system_prompt = """你是严谨的法律文献证据助手。你可以解释、比较、归纳、提取、改写和辅助起草，不局限于简单问答。
只能使用用户提供的证据原文支撑法律陈述，不得使用记忆中的其他法律、案例或数字。必须逐项完整回答用户任务中的子问题，不得只分析部分证据或部分行为。
每一个非空段落末尾都必须标注【证据N】，包括开头引言、分析、限制说明和结尾总结；不要输出没有证据编号的引言或总结。N只能来自给定证据，不能写成“证据3”等缺少括号的形式。
引用原文时使用中文引号，且必须逐字复制。明确区分原文内容与基于原文的分析；证据不足时直接说明，不得猜测。不要编造案例、司法解释或实务结论。
输出一个 JSON 对象，不要 Markdown 代码块，格式为 {"answer":"回答正文","unresolved":["仍未解决的问题"]}。"""
        user_prompt = f"用户任务：{question}\n\n以下是唯一可用的证据：\n{evidence_text}"
        if correction_notes:
            notes = "\n".join(f"- {note}" for note in correction_notes)
            if previous_answer:
                user_prompt += f"\n\n上一版完整草稿：\n{previous_answer}"
            user_prompt += (
                "\n\n上一版回答未通过独立校验，请在保留正确、已有证据内容的基础上定向修订，重新输出完整回答。"
                "必须逐项修复以下问题，同时完整回答原任务的全部子问题，不得为缩短篇幅而遗漏任何罪名、责任条件或量刑规则。"
                f"每个非空段落末尾都要使用有效的【证据N】格式：\n{notes}"
            )
        payload = json.dumps({
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0.1,
            "max_tokens": 1800,
            "stream": False,
        }).encode("utf-8")
        request = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=payload,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=100) as response:
                result = json.loads(response.read().decode("utf-8"))
            content = result["choices"][0]["message"]["content"]
        except urllib.error.HTTPError as error:
            detail = error.read().decode("utf-8", errors="replace")[:500]
            raise DeepSeekError(f"DeepSeek API 返回 {error.code}：{detail}") from error
        except (urllib.error.URLError, TimeoutError, KeyError, IndexError, json.JSONDecodeError) as error:
            raise DeepSeekError(f"DeepSeek 调用失败：{error}") from error
        try:
            cleaned = content.strip()
            cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", cleaned, flags=re.IGNORECASE)
            parsed = json.loads(cleaned)
            answer = parsed["answer"].strip()
            if not answer:
                raise ValueError("回答为空")
            return answer
        except (json.JSONDecodeError, KeyError, ValueError) as error:
            raise DeepSeekError("DeepSeek 未返回约定的 JSON 结构") from error


def verify_answer(answer: str, evidence: list[dict]) -> dict:
    checks = []

    def add(name: str, passed: bool, detail: str) -> None:
        checks.append({"name": name, "passed": passed, "detail": detail})

    citations = [int(value) for value in re.findall(r"【证据\s*(\d+)\s*】", answer)]
    invalid = sorted({value for value in citations if value < 1 or value > len(evidence)})
    add("引用编号有效", bool(citations) and not invalid, "所有引用均指向本次证据包" if citations and not invalid else "缺少引用或存在越界编号")

    substantive = [
        line.strip() for line in re.split(r"\n+", answer)
        if len(line.strip()) >= 12 and not line.strip().startswith(" unresolved")
    ]
    uncovered = [line for line in substantive if not re.search(r"【证据\s*\d+\s*】", line)]
    add("法律陈述有证据", bool(substantive) and not uncovered, "实质性段落均带引用" if substantive and not uncovered else "存在未标注证据的实质性段落")

    evidence_text = normalize_text("".join(item["article_label"] + item["text"] for item in evidence))
    quotes = re.findall(r"“([^”]{8,})”", answer)
    mismatched = [quote for quote in quotes if normalize_text(quote) not in evidence_text]
    add("引文逐字匹配", not mismatched, "中文引号内长引文均来自原文" if not mismatched else "存在无法在原文中匹配的引文")

    allowed_titles = {item["document_title"] for item in evidence}
    used_titles = set(re.findall(r"《([^》]+)》", answer))
    unknown_titles = sorted(used_titles - allowed_titles)
    add("未引入证据外文献", not unknown_titles, "未出现证据包之外的文献名称" if not unknown_titles else "发现证据包之外的文献名称")

    allowed_articles = set()
    for item in evidence:
        match = ARTICLE_QUERY_RE.search(item["article_label"])
        if match:
            allowed_articles.add(chinese_number_to_int(match.group(1)))
    used_articles = {chinese_number_to_int(match.group(1)) for match in ARTICLE_QUERY_RE.finditer(answer)}
    unknown_articles = sorted(used_articles - allowed_articles)
    add("未引入证据外条号", not unknown_articles, "回答中的条号均属于本次证据包" if not unknown_articles else "发现证据包之外的条号")

    return {
        "passed": all(check["passed"] for check in checks),
        "checks": checks,
        "_uncovered_segments": uncovered,
    }
