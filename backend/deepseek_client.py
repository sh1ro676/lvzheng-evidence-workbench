from __future__ import annotations

import json
import os
import re
import socket
import unicodedata
import urllib.error
import urllib.request

from legal_store import ARTICLE_QUERY_RE, chinese_number_to_int, normalize_text
from conversation import HISTORY_RULE, normalize_history
from retrieval_tasks import network_timeout


def _norm(text: str) -> str:
    """比较引文时统一全半角与空白：OCR 出来的括号常是全角，模型引用会写成半角。"""
    return normalize_text(unicodedata.normalize("NFKC", text or ""))


WEB_EVIDENCE_RULES = """网页证据分级使用规则：身份核验与观点核验分开。一手资料只能支持其明确记载的内容；媒体报道须写成“某媒体报道/转述”，不得写成司法机关认定；转述待核不得写成教授直接原话或确定案件评价，只能明确标注未核实，或不采用。官方活动页、预告只证明其明确记载的出席活动、身份或安排，不证明具体发言。搜索摘要永远不作为原话证据，正文出现引号也不提高证据等级。"""


DEFAULT_SYSTEM_PROMPT = """你是一名通用法律 AI 助手，读者包括律师、法务、法学生和普通用户。你的首要任务是完整理解并回答用户的问题，检索资料是专业能力增强，不是你的知识边界，也不是回答格式的主导者。

回答原则：
1. 先直接回应用户真正关心的问题，再根据任务自然组织内容。可以解释、推理、比较、归纳、改写、起草和提出处理建议，不要机械套用固定段数、固定标题或每条字数限制。
2. 你可以正常使用模型已有的语言能力和法律知识进行分析，不得因为某句话没有证据编号就删除它，也不得把“未检索到资料”误当成“无法回答”。
3. 检索资料用于核对具体事实、法律原文、最新信息和可验证出处。实际采用某份资料时，在对应句段末尾标注【证据N】；一般概念解释、逻辑分析和写作衔接不必强行逐句标注。
4. 严格区分检索资料明确记载的事实、媒体或当事人说法、司法机关认定，以及你自己的法律分析。具体案件事实不得凭印象补成已核实事实；确需使用模型记忆且本次资料未核实时，要明确标为“未经本次检索核实”。
5. 资料不完整时仍然完成能够完成的部分，并自然指出关键缺口、不同可能性及需要补充的信息。只有用户明确要求逐字原文或直接引语时，才将可核验原文作为硬性前提。
6. 不编造证据编号、文献、案号、判决、教授观点或直接引语。中文引号用于真正的引文或必要的概念标示，不要把普通概括伪装成他人原话。教材 OCR 内容若需逐字使用，提示核对纸书。
7. 表达应有实质内容和推理过程，避免重复堆砌“证据未载明”。简单问题简洁回答，复杂问题充分展开；正式法律意见提示由律师结合完整材料复核。

""" + WEB_EVIDENCE_RULES + """

输出一个 JSON 对象，不要 Markdown 代码块，格式为 {"answer":"回答正文","unresolved":["仍未解决的问题"]}。"""

CASE_SYSTEM_PROMPT = DEFAULT_SYSTEM_PROMPT + """

当用户询问具体案件、事件或新闻时，还要遵守：
1. 先讲清楚“发生了什么”，再讲法律。根据资料充分梳理人物及关系、事件背景与起因、关键行为、损害结果、重要时间节点，以及侦查、起诉、审判、复核或执行等程序经过。
2. 不要只罗列罪名和裁判结果。对资料能够支持的重要案情按时间或阶段展开；不同来源存在出入时说明出入，不要为了凑时间线虚构日期和细节。
3. 在案情之后自然分析涉及的法律问题、构成要件、责任认定、量刑或程序意义，并给出有依据的结论与不确定性。用户只需要某一方面时，以其需求为准。
4. 具体事实使用检索资料时标注【证据N】，你的构成要件分析、比较和评价可以连贯展开，不要求每一句都附证据。
"""

# 两种输出模式共用的轻量自检：只防止伪引用，不再过滤正常分析。
SELF_CHECK = ("输出前自检：引用编号只能来自本次资料；具体事实与司法认定不得混写；"
              "不要为了通过校验删掉有必要的背景、推理或结论，也不要给每句话机械添加引用。")


SECTION_HEADING_RE = re.compile(r"^(?:[一二三四五六七八九十]+、|\d+[.、]|[（(](?:[一二三四五六七八九十]+|\d+)[）)])[^【】\n]{2,24}$")


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

    def plan_route(self, question: str, timeout: float | None = None,
                   history: list[dict] | None = None) -> dict:
        """第一环：让模型决定该用哪个数据源、查什么。

        非思考模型只输出一份小 JSON；思考模型的推理也计入 max_tokens，
        必须留出足够的思考与最终 JSON 空间，避免内容尚未产出就被截断。
        """
        if not self.configured:
            raise DeepSeekError("未配置 DEEPSEEK_API_KEY")
        thinking = "reasoner" in self.model.lower()
        request_timeout = timeout if timeout is not None else (90 if thinking else 12)
        instruction = (
            "你是法律检索调度器。根据用户问题，决定先查哪类数据源，只输出一个 JSON 对象：\n"
            '{"kind":"statute|case|concept",'
            '"law":"法律全称，如 中华人民共和国刑法；没有就空串",'
            '"article":"条号阿拉伯数字，如 232；没有就空串",'
            '"textbook_query":"用于检索刑法教材的实词，如 故意杀人 罪；没有就空串",'
            '"law_query":"无法确定条号时，用几个法条关键词，如 自甘风险 组织者责任 不作为；没有就空串",'
            '"need_web":true|false,'
            '"web_query":"需要联网时用的检索词；不需要就空串",'
            '"need_schools":true|false}\n'
            '另加 "resolved_question"：结合此前对话，将当前追问改写为可独立检索的完整问题；'
            '保留用户当前意图，不能凭空增加事实。没有上下文时原样使用用户问题。\n'
            "判断规则：\n"
            "- 点名条号或问某条规则 → statute，只需 law+article。\n"
            "- 问具体案件、新闻、判决结果 → case，need_web=true，web_query 写案件名+关键词。\n"
            "- 问概念、学理、四校教授观点 → concept，need_schools=true。\n"
            "- textbook_query 永远填：即使是案件，也写最可能涉及的罪名（如 故意杀人）。\n"
            "- 不确定要不要联网时选 true；但纯法条讲解选 false，避免无谓等待。"
        )
        body = {
            "model": self.model,
            "messages": [{"role": "system", "content": instruction + "\n" + HISTORY_RULE},
                         *normalize_history(history),
                         {"role": "user", "content": question[:1500]}],
            "temperature": 0.0,
            "max_tokens": 4096 if thinking else 500,
            "response_format": {"type": "json_object"},
            "stream": False,
        }
        request = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers={"Authorization": f"Bearer {self.api_key}",
                     "Content-Type": "application/json"},
            method="POST")
        try:
            with urllib.request.urlopen(request, timeout=network_timeout(request_timeout)) as response:
                payload = json.loads(response.read().decode("utf-8"))
            content = payload["choices"][0]["message"]["content"]
        except urllib.error.HTTPError as error:
            detail = error.read().decode("utf-8", errors="replace")[:200]
            raise DeepSeekError(f"调度模型返回 {error.code}：{detail}") from error
        except (urllib.error.URLError, TimeoutError, socket.timeout, KeyError,
                IndexError, json.JSONDecodeError) as error:
            raise DeepSeekError(f"调度模型调用失败：{error}") from error
        try:
            plan = json.loads(content)
        except json.JSONDecodeError:
            match = re.search(r"\{.*\}", content, re.S)
            if not match:
                raise DeepSeekError("调度模型未返回 JSON")
            plan = json.loads(match.group(0))
        return {
            "kind": str(plan.get("kind") or "").strip(),
            "law": str(plan.get("law") or "").strip(),
            "article": str(plan.get("article") or "").strip(),
            "textbook_query": str(plan.get("textbook_query") or "").strip(),
            "law_query": str(plan.get("law_query") or "").strip(),
            "need_web": bool(plan.get("need_web")),
            "web_query": str(plan.get("web_query") or "").strip(),
            "need_schools": bool(plan.get("need_schools")),
            "resolved_question": str(plan.get("resolved_question") or question).strip()[:2000],
        }

    def plan_web_queries(self, question: str, institution: str | None = None) -> list[str]:
        """Plan a precise first search and diverse fallbacks without executing them all."""
        if not self.configured:
            raise DeepSeekError("未配置 DEEPSEEK_API_KEY")
        if institution:
            planning_instruction = (
                f"你是法律研究检索规划器。本次只检索{institution}的教师或该院系的学术论文。"
                "第一步：从用户任务附带的本地方官方条文原文中提炼二至四个能概括该条制度、行为或法律效果的术语。"
                "例如条文规范“不能完全辨认自己行为的成年人”，术语应提炼为“成年监护”“限制民事行为能力”“行为能力宣告”，"
                "而不是“第22条”。第二步：调用 web_search 两至三次，每次只给一个有差异的精准查询词。"
                "查询必须由主题术语构成，不要把“第N条”或法律全名当作主检索词——论文标题和正文很少出现条号，"
                "带条号只会召回百科词条、讲座预告和学者简介。"
                "第一条：主题术语（空格分隔）+ 目标机构名称 + “论文”。"
                "第二条：主题术语 + 目标机构 + “学报”或“期刊”或“site:cnki.net”。"
                "第三条：主题术语 + 条文的前身法或相邻制度名称（如《民法总则》），"
                "或主题术语 + “硕士学位论文”“学位论文”，覆盖期刊之外的研究生论文。"
                "每条查询都必须包含目标机构名称。不得换成其他学校，不得编造搜索结果。"
                "系统会先执行第一条，只有未命中时才执行后续查询。"
            )
            user_content = f"目标机构：{institution}\n用户任务：{question}"
        else:
            planning_instruction = (
                "你是法律研究检索规划器。需要最新信息、教授或高校观点、公开文章、新闻及其他网页资料时，"
                "调用 web_search 一至三次，每次只给一个精准查询词。查询应保留法律名称、条号、人名、机构等关键限定。"
                "仅要求本地法条原文或无需公开网页时，不调用工具。不得编造搜索结果。"
            )
            user_content = question
        payload = json.dumps({
            "model": self.model,
            "messages": [
                {"role": "system", "content": planning_instruction},
                {"role": "user", "content": user_content},
            ],
            "tools": [{
                "type": "function",
                "function": {
                    "name": "web_search",
                    "description": "检索公开网页资料；定向机构检索时不得替换目标机构。",
                    "parameters": {
                        "type": "object",
                        "properties": {"query": {"type": "string", "description": "精准中文搜索查询词"}},
                        "required": ["query"],
                    },
                },
            }],
            "tool_choice": "auto",
            "temperature": 0.1,
            "max_tokens": 700,
            "stream": False,
        }, ensure_ascii=False).encode("utf-8")
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
            with urllib.request.urlopen(request, timeout=network_timeout(60)) as response:
                result = json.loads(response.read().decode("utf-8"))
            calls = result["choices"][0]["message"].get("tool_calls") or []
        except urllib.error.HTTPError as error:
            detail = error.read().decode("utf-8", errors="replace")[:500]
            raise DeepSeekError(f"DeepSeek 检索规划返回 {error.code}：{detail}") from error
        except (urllib.error.URLError, TimeoutError, KeyError, IndexError, json.JSONDecodeError) as error:
            raise DeepSeekError(f"DeepSeek 检索规划失败：{error}") from error
        queries = []
        for call in calls:
            if call.get("function", {}).get("name") != "web_search":
                continue
            try:
                arguments = json.loads(call["function"].get("arguments") or "{}")
                query = str(arguments.get("query") or "").strip()[:300]
            except (json.JSONDecodeError, KeyError, TypeError):
                continue
            if query and institution and institution not in query:
                query = f"{query} {institution}"[:300]
            if query and query not in queries:
                queries.append(query)
            if len(queries) >= 3:
                break
        return queries

    def _messages(self, question: str, evidence: list[dict], system_prompt: str | None,
                  plain: bool = False, history: list[dict] | None = None) -> tuple[list[dict], str]:
        def render_evidence(index: int, item: dict) -> str:
            is_web = item.get("provider") == "deepseek_web"
            source_type = ("公开网页正文" if is_web else
                           "教材内容（OCR，逐字引用需核对）" if item.get("provider") == "textbook"
                           else "法律文献原文")
            header = (
                f"【证据{index}】\n资料类型：{source_type}\n"
                f"文献或网页：{item['document_title']}\n来源：{item['source_name']} {item.get('source_url', '')}\n"
                f"位置：{' / '.join(item.get('section_path', []))} / {item['article_label']}\n"
                f"效力或资料状态：{item.get('status', '')}；公布：{item.get('published_at', '')}；施行：{item.get('effective_at', '')}\n"
            )
            if is_web:
                tier = item.get("evidence_tier") or "secondary_unverified"
                label = item.get("evidence_tier_label") or "转述待核"
                notice = item.get("source_notice") or "来源与内容形态无法确认，只能标注未核实或不采用。"
                header += f"证据等级：{label}（{tier}）\n使用边界：{notice}\n"
            if str(item.get("excerpt") or "").strip():
                return header + f"与所问条文直接相关的原文摘录（逐字，未改写）：\n{item['excerpt']}"
            return header + f"原文：{item['text']}"

        evidence_text = "\n\n".join(
            render_evidence(index, item) for index, item in enumerate(evidence, start=1)
        )
        prompt = system_prompt or DEFAULT_SYSTEM_PROMPT
        json_instruction = '输出一个 JSON 对象，不要 Markdown 代码块，格式为 {"answer":"回答正文","unresolved":["仍未解决的问题"]}。'
        # 只移除输出格式那一行，不能用贪婪匹配删掉案件提示等后续内容。
        prompt = re.sub(r"\n\s*输出一个 JSON 对象[^\n]*", "", prompt).rstrip()
        if any(item.get("provider") == "deepseek_web" for item in evidence) and WEB_EVIDENCE_RULES not in prompt:
            prompt += "\n" + WEB_EVIDENCE_RULES
        if any(item.get("provider") == "textbook" for item in evidence):
            prompt += (
                "\n本次参考资料包含教材内容。必须结合教材对用户问题中的核心法律概念、构成要件、解释理由或适用边界"
                "进行实质讲评，不得只把教材列在证据栏或机械摘句。可以设置“教材讲评”小节，将教材理解与本案事实、"
                "法条或争议问题作对照；实际采用教材时在相应句段标注【证据N】。教材内容与问题不完全对应时应限定"
                "其适用范围，不得杜撰教材观点。教材为 OCR 文本，无法逐字核对时不要写成直接引语。"
            )
        if plain:
            prompt += "\n直接输出正文，不要 JSON 包装，不要代码块围栏。"
        else:
            prompt += "\n" + json_instruction
        if SELF_CHECK not in prompt:
            prompt += "\n" + SELF_CHECK
        prior = normalize_history(history)
        if prior:
            prompt += "\n" + HISTORY_RULE
        reference = evidence_text or "本次没有检索到可引用的外部资料。仍请完整回答用户；具体事实和现行法若无法确认，应自然说明不确定性。"
        messages = [{"role": "system", "content": prompt},
                    *prior,
                    {"role": "user", "content": f"用户任务：{question}\n\n以下是本次检索到的参考资料（用于增强与核对，不是你的知识边界）：\n{reference}"}]
        return messages, evidence_text

    def generate_stream(self, question: str, evidence: list[dict],
                        system_prompt: str | None = None, history: list[dict] | None = None):
        """流式生成：边生成边吐字，前端首字延迟从 6 秒降到 1 秒左右。"""
        if not self.configured:
            raise DeepSeekError("未配置 DEEPSEEK_API_KEY")
        messages, _ = self._messages(question, evidence, system_prompt, plain=True, history=history)
        body = {"model": self.model, "messages": messages, "temperature": 0.1,
                "max_tokens": 5200, "stream": True}
        request = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers={"Authorization": f"Bearer {self.api_key}",
                     "Content-Type": "application/json", "Accept": "text/event-stream"},
            method="POST")
        try:
            with urllib.request.urlopen(request, timeout=network_timeout(180)) as response:
                for raw in response:
                    network_timeout(180)
                    line = raw.decode("utf-8", "ignore").strip()
                    if not line.startswith("data:"):
                        continue
                    payload = line[5:].strip()
                    if payload == "[DONE]":
                        break
                    try:
                        chunk = json.loads(payload)
                    except json.JSONDecodeError:
                        continue
                    choices = chunk.get("choices") or []
                    if not choices:
                        continue
                    delta = (choices[0].get("delta") or {}).get("content")
                    if delta:
                        yield delta
        except urllib.error.HTTPError as error:
            detail = error.read().decode("utf-8", errors="replace")[:300]
            raise DeepSeekError(f"DeepSeek 流式接口返回 {error.code}：{detail}") from error
        except (urllib.error.URLError, TimeoutError, socket.timeout) as error:
            raise DeepSeekError(f"DeepSeek 流式调用失败：{error}") from error

    def generate(self, question: str, evidence: list[dict], correction_notes: list[str] | None = None,
                 previous_answer: str | None = None, system_prompt: str | None = None,
                 history: list[dict] | None = None) -> str:
        if not self.configured:
            raise DeepSeekError("未配置 DEEPSEEK_API_KEY")
        messages, _ = self._messages(question, evidence, system_prompt, history=history)
        user_prompt = messages[-1]["content"]
        if correction_notes:
            notes = "\n".join(f"- {note}" for note in correction_notes)
            if previous_answer:
                user_prompt += f"\n\n上一版完整草稿：\n{previous_answer}"
            user_prompt += (
                "\n\n上一版回答存在可机械确认的引用问题，请定向修订并重新输出完整回答。"
                "只修复下列问题，保留其中有价值的背景、案情、推理和结论，不得为缩短篇幅而遗漏原任务的子问题。"
                f"引用编号必须有效；使用检索资料时在相应句段标注，不要给每句话机械添加引用：\n{notes}"
            )
        messages[-1]["content"] = user_prompt
        payload = json.dumps({
            "model": self.model,
            "messages": messages,
            "temperature": 0.1,
            "max_tokens": 5200,
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
            with urllib.request.urlopen(request, timeout=network_timeout(100)) as response:
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


def _quote_sources(quote: str, evidence: list[dict]) -> list[dict]:
    normalized = _norm(quote)
    return [
        item for item in evidence
        if normalized in _norm(str(item.get("article_label") or "") + str(item.get("text") or ""))
    ]


def _only_unverified_secondary(sources: list[dict]) -> bool:
    return bool(sources) and all(
        item.get("evidence_tier") == "secondary_unverified" for item in sources
    )


def repair_unverified_quotes(answer: str, evidence: list[dict]) -> tuple[str, int]:
    """把无法逐字核对或仅有待核转述支持的长直接引语改为间接表述。"""
    repairs = 0

    def _as_indirect(match: re.Match) -> str:
        nonlocal repairs
        quote = match.group(1)
        sources = _quote_sources(quote, evidence)
        if sources and not _only_unverified_secondary(sources):
            return match.group(0)
        repairs += 1
        return quote

    return re.sub(r"“([^”]{20,})”", _as_indirect, answer), repairs


def verify_answer(answer: str, evidence: list[dict]) -> dict:
    """只校验可机械确认的引用问题，不再审查模型能使用哪些知识。"""
    checks = []

    def add(name: str, passed: bool, detail: str) -> None:
        checks.append({"name": name, "passed": passed, "detail": detail})

    citations = [int(value) for value in re.findall(r"【证据\s*(\d+)\s*】", answer)]
    invalid = sorted({value for value in citations if value < 1 or value > len(evidence)})
    citation_detail = ("存在越界编号：" + "、".join(map(str, invalid))) if invalid else (
        "所有引用均指向本次检索资料" if citations else "未使用外部资料编号，不影响一般分析"
    )
    add("引用编号有效", not invalid, citation_detail)
    source_detail = (
        "已检索到资料但回答没有标注任何资料编号，请确认是否漏引"
        if evidence and not citations else
        "本次采用的检索资料已标注来源" if citations else "本次没有使用外部资料，可直接进行模型分析"
    )
    add("检索资料已标注", not evidence or bool(citations), source_detail)

    uncited_institutions = list(dict.fromkeys(
        item["institution"] for index, item in enumerate(evidence, start=1)
        if item.get("institution") and index not in citations
    ))
    add(
        "命中学校均有回应", not uncited_institutions,
        "每条机构定向证据均已引用" if not uncited_institutions
        else "以下命中学校未被引用：" + "、".join(uncited_institutions),
    )

    textbook_ids = {
        index for index, item in enumerate(evidence, start=1)
        if item.get("provider") == "textbook"
    }
    textbook_cited = bool(textbook_ids.intersection(citations))
    textbook_discussed = bool(re.search(r"教材|刑法学|张明楷|学者认为|学理", answer))
    add(
        "教材已用于实质讲评", not textbook_ids or (textbook_cited and textbook_discussed),
        "已结合教材进行讲评并标注出处" if textbook_cited and textbook_discussed else
        "本次没有教材证据" if not textbook_ids else
        "已检索到教材，但回答没有实际结合教材讲评并引用对应证据；请补充教材观点及其与问题的关系",
    )

    # 有检索资料时才核对其长引文；没有资料时不因此拦截正常回答。
    evidence_text = _norm("".join(item["article_label"] + item["text"] for item in evidence))
    quotes = re.findall(r"“([^”]{20,})”", answer) if evidence else []
    mismatched = [quote for quote in quotes if _norm(quote) not in evidence_text]
    quote_detail = "无法在资料中匹配的引文：" + "；".join(mismatched[:3]) if mismatched else (
        "长引文均可在本次资料中找到" if quotes else "未发现需要核对的长引文"
    )
    add("引文逐字匹配", not mismatched, quote_detail)

    secondary_quotes = [
        quote for quote in quotes
        if _only_unverified_secondary(_quote_sources(quote, evidence))
    ]
    add(
        "转述待核不作为直接引语", not secondary_quotes,
        "以下引语仅出现在转述待核资料中：" + "；".join(secondary_quotes[:3])
        if secondary_quotes else "未发现仅由转述待核资料支持的直接引语",
    )

    return {
        "passed": all(check["passed"] for check in checks),
        "checks": checks,
        "_uncovered_segments": [],
    }
