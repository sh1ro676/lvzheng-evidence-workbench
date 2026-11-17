from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from deepseek_client import (CASE_SYSTEM_PROMPT, DeepSeekClient, DeepSeekError,
                             repair_unverified_quotes, verify_answer)
from legal_store import (ARTICLE_KEY_RE, ARTICLE_QUERY_RE, LegalStore,
                         chinese_number_to_int, normalize_article_refs)
from pkulaw_mcp import PkulawError, PkulawProvider, extract_law_title
from web_search_client import DeepSeekWebClient, WebSearchError, normalize_web_evidence, utc_now

ROOT = Path(__file__).resolve().parent
CRIMINAL_QUERY_RE = re.compile(r"判刑|量刑|偷盗|盗窃|强奸|犯罪|刑事责任|数罪并罚|有期徒刑|死刑|罚金|罪名|刑事拘留|缓刑")
CIVIL_QUERY_RE = re.compile(r"民法|民事|侵权|合同|婚姻|离婚|继承|监护|收养")
PROFESSOR_QUERY_RE = re.compile(r"教授|高校|老师|教师|学者|观点|学说|四校|法学院")
# 用户明确要"观点/最新/案例"时才值得再跑全网检索；单纯问"讲解"用条文本条足够。
WANTS_FRESH_WEB_RE = re.compile(r"教授|老师|学者|观点|四校|最新|案例|判例|实务|新闻|解读|争议|学者")

# 具体案件/新闻类问题，法条与教材给不出事实，必须联网。
# 注意「北大杀母案」这类写法：不能只认"案件/案情"，动词+案也要算。
CASE_SINGLE_SURNAMES = (
    "赵钱孙李周吴郑王冯陈褚卫蒋沈韩杨朱秦许何吕施张孔曹严华金魏陶姜谢邹苏潘葛范彭鲁韦马方任袁柳鲍史唐费薛雷贺倪汤罗毕郝安常乐于时傅皮齐康伍余元卜顾孟平黄和穆萧尹姚邵汪祁毛米贝明臧成戴谈宋庞熊纪舒屈项祝董梁杜阮蓝季贾路江童颜郭梅盛林钟徐邱骆高夏蔡田樊胡凌霍虞万柯卢莫房解应宗丁邓单杭洪包诸左石崔吉龚程邢裴陆荣翁荀羊惠甄曲封芮储靳段富巫乌焦巴弓牧山谷车侯全秋仲伊宫宁仇栾甘祖武符刘景詹束龙叶幸司黎白怀蒲从鄂索赖卓蔺屠蒙池乔阴闻莘党翟谭贡劳姬申扶堵冉宰桑桂濮牛寿通边燕冀浦尚农温别庄晏柴瞿阎连茹习艾鱼容向古易慎戈廖庾终暨居衡步都耿满弘匡国文寇广阙东欧沃利越隆师巩聂晁勾敖融冷辛阚那简饶空曾沙鞠须丰巢关相查后荆红游竺权盖益桓公"
)
CASE_COMPOUND_SURNAMES = (
    "欧阳", "太史", "端木", "上官", "司马", "东方", "独孤", "南宫", "万俟", "闻人",
    "夏侯", "诸葛", "尉迟", "公羊", "赫连", "澹台", "皇甫", "宗政", "濮阳", "公冶",
    "太叔", "申屠", "公孙", "慕容", "仲孙", "钟离", "长孙", "宇文", "司徒", "鲜于",
    "司空", "闾丘", "子车", "亓官", "巫马", "公西", "颛孙", "壤驷", "公良", "漆雕",
    "乐正", "宰父", "谷梁", "拓跋", "夹谷", "轩辕", "令狐", "段干", "百里", "呼延",
    "东郭", "南门", "羊舌", "微生", "梁丘", "左丘", "西门", "第五", "东宫", "仲长",
)
CASE_QUERY_RE = re.compile(
    rf"[杀弑害伤奸盗骗抢绑虐][^，。；]{{0,6}}案|"
    rf"(?:[{CASE_SINGLE_SURNAMES}][\u4e00-\u9fff]{{1,2}}|"
    rf"(?:{'|'.join(CASE_COMPOUND_SURNAMES)})[\u4e00-\u9fff]{{1,2}})案|"
    r"案[件例情发]|命案|凶案|事件|新闻|热搜|通报|涉嫌|被告人|被害人|最近|最新"
)

# 案件由通用 Agent 在同一轮完整生成：先理解案情，再以检索资料核对事实与法律。
CASE_FULL_PROMPT = CASE_SYSTEM_PROMPT

# 第二段只写"四校观点"，法条与教材由第一段负责，避免重复。
SCHOOL_SECTION_PROMPT = """你只负责撰写"四校观点"这一节，不要重复法条原文，也不要写理解与适用。
只能使用用户提供的证据；每引用一位学者或一所学校，必须写明学校与出处，段落末尾标注【证据N】。
只允许介绍中国政法大学、西北政法大学、北京大学法学院、清华大学法学院四校；同一网页出现的其他院校及其教师不得另起段落或列入总结。
每个命中学校使用独立标题，固定写成“（一）学校全称：教授姓名或资料性质”，正文另起一段；编号依次递增，不要使用其他标题格式。
只介绍证据中实际出现且有明确机构关联的四校；未命中的学校不要写、不要暗示，也不要为凑齐四校而编造。
需要引用原话时用中文引号逐字复制证据原文，否则用不带引号的客观概述。区分网页转述与学者本人的直接引语。
活动预告、活动新闻或人物简介只能证明活动与身份，不能据此虚构具体发言；网页转述须注明“转述待核”，不得写成学者的直接引语。
输出一个 JSON 对象，不要 Markdown 代码块，格式为 {"answer":"正文","unresolved":["仍未解决的问题"]}。"""

# 案件/新闻类问题：只报事实与出处，不替法院定性。
CASE_SECTION_PROMPT = """你只负责撰写"公开资料"这一节，回答用户关于具体案件或新闻的问题。
只能使用用户提供的证据；每条事实后面标注【证据N】，编号只来自给定证据。
按要点写，每条不超过 60 字，一条一行；不要写成大段连续文字，不要重复法条原文。
分清三种信息：已生效裁判认定的事实、媒体报道的说法、尚未证实的主张；证据没写清的，直接说没写清，不要补。
不得对案件作定性结论或预测裁判结果；如证据本身未给出结论，只客观陈述争议与已知事实。
媒体转述不等于法院认定；只有证据明确写明裁判结果时才能说"法院认定"。
输出一个 JSON 对象，不要 Markdown 代码块，格式为 {"answer":"正文","unresolved":["仍未解决的问题"]}。"""
TARGET_INSTITUTIONS = ["中国政法大学", "西北政法大学", "北京大学法学院", "清华大学法学院"]


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
WEB = DeepSeekWebClient()


def numbered_evidence(evidence: list[dict]) -> list[dict]:
    return [{"citation_id": index, **item} for index, item in enumerate(evidence, start=1)]


def criminal_core_queries(question: str) -> list[str]:
    """Split supported sentencing issues into precise Criminal Law article lookups."""
    queries = []
    if re.search(r"未成年|不满十八|未满十八|不满18|未满18", question):
        queries.extend(["《刑法》第十七条", "《刑法》第四十九条"])
    offense_count = 0
    if re.search(r"偷盗|盗窃", question):
        queries.append("《刑法》第二百六十四条")
        offense_count += 1
    if "强奸" in question:
        queries.append("《刑法》第二百三十六条")
        offense_count += 1
    if offense_count >= 2 or "数罪并罚" in question:
        queries.append("《刑法》第六十九条")
    return queries


def retrieve_core_criminal_evidence(queries: list[str]) -> tuple[list[dict], dict]:
    evidence = []
    seen = set()
    results = []
    for query in queries:
        result = PKULAW.search(query, strategy="fatiao")
        results.append(result)
        for item in result["evidence"]:
            identity = (item["document_title"], item["article_label"])
            if identity not in seen:
                seen.add(identity)
                evidence.append(item)
    notices = [result["notice"] for result in results if result.get("notice")]
    notices.insert(0, "已将复合量刑问题拆分为刑事责任年龄、具体罪名及数罪并罚的精准刑法检索。")
    return evidence, {
        "source": "pkulaw", "provider": "pkulaw", "strategy": "criminal_core_articles",
        "tool": "get_law_article", "cached": all(result["cached"] for result in results),
        "queried_at": max(result["queried_at"] for result in results),
        "queries": queries, "notice": "；".join(notices),
    }


_BOOK_CACHE = None


def _book_index():
    """懒加载教材索引；没有索引文件时返回 None，不影响其他检索。"""
    global _BOOK_CACHE
    if _BOOK_CACHE is None:
        try:
            from book_index import BookIndex, load
            chunks = []
            for path in sorted((ROOT / "data" / "books").glob("*.jsonl")):
                chunks.extend(load(path))
            _BOOK_CACHE = BookIndex(chunks) if chunks else False
        except Exception:  # noqa: BLE001
            _BOOK_CACHE = False
    return _BOOK_CACHE or None


# 目前入库的只有刑法教材。判据有两路：问题里的刑法用语，或本地检索命中的就是刑法条文。
CRIMINAL_SCOPE_RE = re.compile(
    r"刑法|犯罪|罪名|量刑|判刑|刑事责任|刑罚|自首|立功|累犯|缓刑|假释|追诉时效|"
    r"共同犯罪|未遂|中止|预备|正当防卫|紧急避险|不作为|因果关系|罪数|数罪并罚|单位犯罪|"
    r"盗窃|诈骗|抢劫|抢夺|侵占|受贿|行贿|贪污|挪用|渎职|滥用职权|玩忽职守|"
    r"故意伤害|故意杀人|过失致人|交通肇事|危险驾驶|非法拘禁|绑架|强奸|猥亵|虐待|遗弃|"
    r"寻衅滋事|聚众斗殴|妨害公务|伪证|窝藏|包庇|掩饰|隐瞒|走私|毒品|赌博|淫秽|"
    r"黑社会|恐怖活动|洗钱|帮助信息网络|信息网络犯罪"
)


def _law_key(text: str) -> str:
    """把书名或法名归到六部法的短名上，用于教材与问题的匹配。"""
    if "刑法" in text:
        return "刑法"
    if "刑事诉讼法" in text:
        return "刑诉"
    if "民事诉讼法" in text:
        return "民诉"
    if "民法典" in text or "民法" in text:
        return "民法"
    return ""


def case_topic_hint(question: str) -> str:
    """从案件描述里猜个罪名，只用来检索教材，不作为法律结论。"""
    hints = [
        (r"杀母|弑母|杀妻|杀子|杀人|命案|凶杀|灭门|投毒", "故意杀人 故意杀人罪"),
        (r"伤害|殴打|致死|重伤", "故意伤害 故意伤害罪"),
        (r"强奸|性侵|猥亵", "强奸罪 强制猥亵"),
        (r"抢劫|抢夺", "抢劫罪 抢夺罪"),
        (r"盗窃|扒窃", "盗窃罪"),
        (r"诈骗|骗", "诈骗罪"),
        (r"受贿|行贿", "受贿罪 行贿罪"),
        (r"贪污|挪用公款", "贪污罪 挪用公款罪"),
        (r"醉驾|酒驾|交通肇事", "危险驾驶罪 交通肇事罪"),
        (r"正当防卫|防卫过当", "正当防卫 防卫过当"),
    ]
    for pattern, hint in hints:
        if re.search(pattern, question):
            return hint
    return ""


def retrieve_textbook_evidence(question: str, evidence: list[dict] | None = None,
                               k: int = 4, min_score: float = 8.0,
                               query_hint: str = "") -> list[dict]:
    """教材是默认证据源：问题落在哪部法的射程内，就带上那部法的教材。

    判据两路：问题里的部门法用语，或本地检索命中的法条属于哪部法。
    宁缺毋滥：相关度低于阈值就整条不带，绝不为凑格式硬塞一段无关论述。
    """
    index = _book_index()
    if not index:
        return []
    keys: set[str] = set()
    hint = case_topic_hint(question)
    if CRIMINAL_SCOPE_RE.search(question) or hint:
        keys.add("刑法")
    # 问「某某杀母案」这类具体案件时，证据往往是典型案例汇编，
    # 文书标题里没有"刑法"字样；但案件本身落在刑法射程内，教材必须带上。
    if CASE_QUERY_RE.search(question):
        keys.add("刑法")
    for item in evidence or []:
        key = _law_key(str(item.get("document_title", "")))
        if key:
            keys.add(key)
        if re.search(r"杀人|伤害|致死|死亡|杀母|弑母", str(item.get("text", "") or "")):
            keys.add("刑法")
    if not keys:
        return []
    from book_index import to_evidence
    # 用户的口语（"第77条，为我讲解"）里几乎没有可用于检索的实词；
    # 把已取到的条文原文接进检索词，效果远好于原问题：命中缓刑撤销页的分数
    # 从 16 提到 600 量级。
    query = question
    if evidence:
        law_text = " ".join(str(item.get("text") or "")[:220] for item in evidence[:2])
        if law_text.strip():
            query = f"{question} {law_text[:440]}"
    elif query_hint:
        query = f"{question} {query_hint}"
    elif CASE_QUERY_RE.search(question):
        # 案件问题没有条文可借用，用推断出的罪名补足检索词，否则只会命中法理通论。
        hint = case_topic_hint(question)
        if hint:
            query = f"{question} {hint}"
    hits = index.search(query, k * 3)
    if not hits:
        return []
    # 相对阈值：低于最高分 35% 的一律不要，避免为凑数塞进无关段落。
    floor = max(min_score, hits[0][0] * 0.35)
    out = []
    for score, chunk in hits:
        if score < floor:
            continue
        if _law_key(chunk.book) not in keys:
            continue
        out.append(to_evidence(chunk, len(out) + 1))
        if len(out) >= k:
            break
    return out


def _settle(future, label: str, timeout: float = 150) -> tuple[list[dict], dict]:
    """等一个检索任务，超时或异常都返回空结果并记录原因，绝不让第二段永远挂着。"""
    try:
        return future.result(timeout=timeout)
    except Exception as error:  # noqa: BLE001
        return [], {"enabled": True, "failure": f"{label}超时或失败：{error}",
                    "notice": f"{label}超时或失败，本次未纳入该部分资料"}


def dedupe_evidence(evidence: list[dict]) -> list[dict]:
    """同一网页常被多校/多次搜索命中，同一法条也可能重复取到，编号会翻倍。"""
    seen: set[tuple] = set()
    out: list[dict] = []
    for item in evidence:
        identity = (item.get("provider"), item.get("source_url"),
                    item.get("document_title"), item.get("article_label"))
        if identity in seen:
            continue
        seen.add(identity)
        out.append(item)
    return out


def evidence_covers(evidence: list[dict], numbers: list[int]) -> bool:
    """证据里是否真的包含所问的条号；防止语义检索拿近邻条冒充。"""
    wanted = set(numbers)
    for item in evidence:
        match = ARTICLE_KEY_RE.search(str(item.get("article_label", "")))
        if match and chinese_number_to_int(match.group(1)) in wanted:
            return True
    return False


_PLAN_CACHE: dict[str, tuple[dict, float]] = {}


def fallback_plan(question: str) -> dict:
    """调度模型不可用时的规则兜底，保证链路不断。"""
    numbers = article_numbers(question)
    return {
        "kind": "case" if CASE_QUERY_RE.search(question) else ("statute" if numbers else "concept"),
        "law": extract_law_title(question) or "",
        "article": str(numbers[0]) if numbers else "",
        "textbook_query": case_topic_hint(question),
        "law_query": "",
        "need_web": bool(CASE_QUERY_RE.search(question)),
        "web_query": question,
        "need_schools": not CASE_QUERY_RE.search(question),
    }


def route_question(question: str) -> dict:
    """第一环：模型决定查哪里。失败就用规则，绝不因为调度失败而卡住。"""
    plan = None
    if DEEPSEEK.configured:
        try:
            plan = DEEPSEEK.plan_route(question)
        except Exception as error:  # noqa: BLE001
            plan = None
    if not plan:
        plan = fallback_plan(question)
    if not plan.get("textbook_query"):
        plan["textbook_query"] = case_topic_hint(question)
    _PLAN_CACHE[question] = (plan, time.time())
    return plan


def _plan_for(question: str, plan_id: str | None = None) -> dict:
    """同一个问题的第二段复用第一环的调度结果，避免再调一次模型。"""
    cached = _PLAN_CACHE.get(question)
    if cached and time.time() - cached[1] < 3600:
        return cached[0]
    return route_question(question)


def evidence_by_plan(question: str, plan: dict) -> tuple[list[dict], dict]:
    """按调度的结论取证据：先走确定性的法条，再补教材。"""
    law, article = plan.get("law", ""), plan.get("article", "")
    evidence: list[dict] = []
    retrieval: dict = {"source": "none", "provider": None}
    if law and article:
        try:
            evidence, retrieval = retrieve_evidence(f"《{law}》第{article}条")
        except PkulawError as error:
            retrieval = {"source": "none", "provider": None, "error": str(error)}
    if not evidence and plan.get("kind") != "case":
        evidence, retrieval = retrieve_evidence(question)
    elif not evidence and plan.get("kind") == "case":
        retrieval = {"source": "none", "provider": None,
                     "notice": "案件类问题不采用法条语义近似结果，事实与法条交由公开检索与后续匹配"}
    # 定不出条号时（例如"学生遇险、民警牺牲"这类事件），用关键词去法宝找相关条文，
    # 否则"法律依据"一节只能写"证据未载明"。
    law_query = plan.get("law_query") or ""
    if not evidence and law_query and PKULAW.configured:
        try:
            found = PKULAW.search(law_query)
            if found.get("evidence"):
                evidence, retrieval = found["evidence"], {
                    "source": "pkulaw", "provider": "pkulaw",
                    "strategy": found.get("strategy"), "tool": found.get("tool"),
                    "cached": found.get("cached"), "queried_at": found.get("queried_at"),
                    "notice": "按调度给出的法条关键词检索，非精确条号",
                }
        except PkulawError:
            pass
    textbook = retrieve_textbook_evidence(question, evidence,
                                          query_hint=plan.get("textbook_query", ""))
    if textbook:
        evidence = dedupe_evidence(evidence + textbook)
        retrieval["textbook"] = {"count": len(textbook),
                                 "pages": [item["article_label"] for item in textbook]}
    retrieval["plan"] = plan
    return evidence, retrieval


# 常见罪名 → 条文。案件类问题先靠这个把"法律依据"补齐，再交给法宝取原文。
CRIME_ARTICLES = [
    (r"故意杀人|杀人", "232"),
    (r"故意伤害|伤害致死", "234"),
    (r"强奸", "236"),
    (r"绑架", "239"),
    (r"抢劫", "263"),
    (r"盗窃", "264"),
    (r"诈骗", "266"),
    (r"买卖身份证件|伪造、变造、买卖身份证件", "280"),
    (r"危险驾驶", "133-1"),
    (r"交通肇事", "133"),
    (r"受贿", "385"),
    (r"贪污", "382"),
]


def case_articles(question: str, evidence: list[dict], limit: int = 3) -> list[dict]:
    """从问题与已取证据里识别罪名，并行回法宝取条文且保持条号顺序。"""
    if not PKULAW.configured:
        return []
    haystack = question + " " + " ".join(str(item.get("text") or "")[:400] for item in evidence[:6])
    wanted: list[str] = []
    for pattern, article in CRIME_ARTICLES:
        if re.search(pattern, haystack) and article not in wanted:
            wanted.append(article)
    selected = wanted[:limit]
    if not selected:
        return []

    from concurrent.futures import ThreadPoolExecutor

    def _retrieve(article: str) -> list[dict]:
        try:
            result = PKULAW.search(f"《中华人民共和国刑法》第{article}条")
        except PkulawError:
            return []
        return [item for item in result.get("evidence", [])[:1]
                if evidence_covers([item], [int(article.split("-")[0])])]

    # pool.map 按输入顺序返回；单个条号失败不会拖掉其他有效条文。
    with ThreadPoolExecutor(max_workers=min(3, len(selected))) as pool:
        batches = list(pool.map(_retrieve, selected))
    return [item for batch in batches for item in batch]


def retrieve_evidence(question: str, document_id: str | None = None, source: str = "auto") -> tuple[list[dict], dict]:
    """主问答只使用北大法宝在线证据；本地文库仅在用户明确查文库时使用。"""
    if source not in {"auto", "local", "pkulaw"}:
        raise ValueError("不支持的数据源")
    if source == "local":
        evidence = STORE.search(question, document_id)
        return evidence, {"source": "local_official", "provider": "flk", "cached": True,
                          "notice": "文库页面的显式本地检索，不作为主问答的证据来源"}

    notice = "北大法宝未配置，本次由模型直接回答"
    if PKULAW.configured:
        try:
            result = PKULAW.search(question)
            if result.get("evidence"):
                return result["evidence"], {
                    "source": "pkulaw", "provider": "pkulaw",
                    "strategy": result.get("strategy"), "tool": result.get("tool"),
                    "cached": result.get("cached"), "queried_at": result.get("queried_at"),
                    "notice": result.get("notice") or "条文取自北大法宝现行文本",
                }
            notice = "北大法宝未检索到相关条文，本次仍由模型结合其他资料完整回答"
        except PkulawError as error:
            notice = f"北大法宝暂不可用（{error}），本次仍由模型结合其他资料完整回答"

    return [], {"source": "none", "provider": None, "cached": False, "notice": notice}


def web_result_rank(item: dict) -> float:
    try:
        score = float(item.get("rerank_score") or 0)
    except (TypeError, ValueError):
        score = 0
    authority = str(item.get("authority") or "").lower()
    bonus = 0.2 if authority in {"high", "official", "权威", "高"} else 0
    return score + bonus


INSTITUTION_MARKERS = {
    "中国政法大学": ("中国政法大学", "中国政法"),
    "西北政法大学": ("西北政法大学", "西北政法"),
    "北京大学法学院": ("北京大学法学院", "北大法学院", "北京大学", "北大"),
    "清华大学法学院": ("清华大学法学院", "清华法学院", "清华大学", "清华"),
}
ACADEMIC_TITLE_RE = r"(?:教授|副教授|研究员|讲师|教师|博士生导师)"
AFFILIATION_LABEL_RE = r"(?:作者单位|作者简介|作者信息|工作单位|所在单位|机构|单位)[：:\s]{0,3}(?:\[?\d+\]?\s*)?"


def log_web_event(info: dict, event: dict, institution: str | None = None) -> None:
    if institution:
        event["institution"] = institution
    if info.get("_active_round"):
        event.setdefault("round", info["_active_round"])
    if info.get("_query_source"):
        event.setdefault("query_source", info["_query_source"])
    if info.get("_active_entity"):
        event.setdefault("entity", info["_active_entity"])
    info["events"].append(event)


def article_numbers(text: str) -> list[int]:
    text = normalize_article_refs(text)
    numbers = []
    for match in ARTICLE_QUERY_RE.finditer(text):
        number = chinese_number_to_int(match.group(1))
        if number not in numbers:
            numbers.append(number)
    return numbers


def integer_to_chinese(number: int) -> str:
    digits = "零一二三四五六七八九"
    if number < 10:
        return digits[number]
    if number < 100:
        tens, remainder = divmod(number, 10)
        return ("" if tens == 1 else digits[tens]) + "十" + (digits[remainder] if remainder else "")
    if number < 1000:
        hundreds, remainder = divmod(number, 100)
        suffix = ""
        if 0 < remainder < 10:
            suffix = "零" + digits[remainder]
        elif remainder:
            suffix = integer_to_chinese(remainder)
        return digits[hundreds] + "百" + suffix
    if number < 10000:
        thousands, remainder = divmod(number, 1000)
        suffix = ""
        if 0 < remainder < 100:
            suffix = "零" + integer_to_chinese(remainder)
        elif remainder:
            suffix = integer_to_chinese(remainder)
        return digits[thousands] + "千" + suffix
    return str(number)


def article_label_variants(question: str) -> list[str]:
    labels = []
    for match in ARTICLE_QUERY_RE.finditer(question):
        number = chinese_number_to_int(match.group(1))
        for label in (f"第{integer_to_chinese(number)}条", f"第{number}条", f"第{match.group(1)}条"):
            if label not in labels:
                labels.append(label)
    return labels


def law_title_aliases(question: str) -> list[str]:
    title = extract_law_title(question)
    if not title:
        return []
    short_title = re.sub(r"^中华人民共和国", "", title)
    return list(dict.fromkeys((title, short_title)))


def article_planning_context(question: str) -> str:
    """Ground topic extraction in the local official article instead of hard-coded topics."""
    aliases = law_title_aliases(question)
    if not aliases or not ARTICLE_QUERY_RE.search(question):
        return question
    matches = [
        item for item in STORE.exact_matches(question)
        if any(alias in item["document_title"] for alias in aliases)
    ]
    if not matches:
        return question
    context = "\n".join(f"{item['article_label']}：{item['text']}" for item in matches)[:1800]
    return f"{question}\n\n本地官方条文（仅用于提炼检索主题，不作为网页证据）：\n{context}"


ACADEMIC_PAPER_RE = re.compile(
    r"论文|期刊|学刊|学报|内容提要|内容摘要|摘要|关键词|作者简介|【作者】|作者单位|"
    r"基金项目|中图分类号|文献标识码|引用本文|参考文献|Abstract"
)

# 学术源优先级：题录页与期刊门户的可核验度远高于自媒体转载。
ACADEMIC_HOST_BONUS = (
    ("cbpt.cnki.net", 1.4),
    ("wap.cnki.net", 1.2),
    ("cnki.net", 1.0),
    ("cqvip.com", 0.9),
    ("wanfangdata.com.cn", 0.9),
    ("xsyk021.com", 0.8),
    ("ajcass.com", 0.7),
)
NON_ACADEMIC_HOST_RE = re.compile(
    r"baike\.baidu|zhihu\.com|sohu\.com|wenku\.baidu|renrendoc|doc88|docin|360doc|"
    r"ppkao|px-edu|zhulang|ais\.cn|llyj\.net|ndhx\.net|zhaopin|fabao|lawtime"
)
PAPER_TITLE_RE = re.compile(r"论文|学报|期刊|内容提要|摘要|关键词|硕士学位论文|博士学位论文|作者单位|【作者】")
NON_PAPER_TITLE_RE = re.compile(r"讲座|预告|课程|视频|百科|简介|招聘|培训|访谈实录")


def academic_rank_bonus(item: dict, searchable: str) -> float:
    """学术源与论文特征加权，压掉百科、文库和讲座页。"""
    host = (urllib.parse.urlparse(str(item.get("url") or "")).hostname or "").lower()
    bonus = 0.0
    for suffix, weight in ACADEMIC_HOST_BONUS:
        if host.endswith(suffix):
            bonus += weight
            break
    if NON_ACADEMIC_HOST_RE.search(host):
        bonus -= 1.2
    if PAPER_TITLE_RE.search(searchable):
        bonus += 0.6
    if NON_PAPER_TITLE_RE.search(searchable):
        bonus -= 0.8
    return bonus


def candidate_rank(item: dict, question: str) -> float:
    score = web_result_rank(item)
    searchable = " ".join(str(item.get(key) or "") for key in ("title", "snippet", "summary"))
    if set(article_numbers(question)) & set(article_numbers(searchable)):
        score += 2.0
    if any(alias in searchable for alias in law_title_aliases(question)):
        score += 0.4
    if re.search(r"论文|教授|副教授|法学院|学刊|学报", searchable):
        score += 0.15
    if "论文" in question:
        score += academic_rank_bonus(item, searchable)
        if not ACADEMIC_PAPER_RE.search(searchable):
            score -= 0.4
    return score


def fetched_page_text(item: dict, fetched: dict) -> str:
    return str(item.get("title") or "") + " " + fetched["title"] + " " + fetched["content"]


def topic_query_terms(question: str, query: str) -> list[str]:
    generic = {
        "相关", "法律", "法学", "研究", "解读", "观点", "教授", "副教授", "老师", "教师",
        "论文", "期刊", "学刊", "学报", "高校", "权威", "学校", "法学院",
    }
    aliases = law_title_aliases(question)
    institution_markers = [marker for markers in INSTITUTION_MARKERS.values() for marker in markers]
    terms = []
    for raw_term in re.split(r"[\s，,；;、《》〈〉“”\"'|｜]+", query):
        term = raw_term.strip()
        if len(term) < 4 or term in generic or ARTICLE_QUERY_RE.search(term):
            continue
        if any(alias in term for alias in aliases) or any(marker in term for marker in institution_markers):
            continue
        if term not in terms:
            terms.append(term)
    return terms


def page_relevance_error(question: str, item: dict, fetched: dict, query: str = "") -> str | None:
    requested_articles = set(article_numbers(question))
    if not requested_articles:
        return None
    searchable = fetched_page_text(item, fetched)
    direct_article_match = bool(requested_articles & set(article_numbers(searchable)))
    is_academic_paper = "论文" in question and bool(ACADEMIC_PAPER_RE.search(searchable[:3000]))
    # 学术论文常只写“《民法总则》”“行为能力宣告”，不逐字写“民法典第22条”。
    # 只有标题/摘要级命中，或正文同时出现两个以上不同的制度术语，才算"这篇论文就是讲这个的"；
    # 单个泛化术语在正文里擦边出现（如“法定代理”）不足以把一篇无关论文纳入证据。
    topic_terms = topic_query_terms(question, query) if is_academic_paper else []
    metadata = str(item.get("title") or "") + " " + fetched["title"]
    body_hits = {term for term in topic_terms if term in fetched["content"]}
    topic_hit = any(term in metadata for term in topic_terms) or len(body_hits) >= 2
    if not direct_article_match and not topic_hit:
        return "网页正文既未明确提及所问法条，也未体现足够的法条主题相关性"
    aliases = law_title_aliases(question)
    if aliases and not any(alias in searchable for alias in aliases) and not topic_hit:
        return "网页正文未明确提及所问法律，未作为该法律条文证据"
    if "论文" in question and not is_academic_paper:
        return "用户明确要求论文，该网页未体现论文、期刊或学术文章特征"
    return None


def should_search_target_institutions(question: str) -> bool:
    return bool(ARTICLE_QUERY_RE.search(question) and PROFESSOR_QUERY_RE.search(question))


def plan_queries(question: str, info: dict, institution: str | None = None) -> list[str]:
    try:
        queries = DEEPSEEK.plan_web_queries(article_planning_context(question), institution=institution)
    except DeepSeekError as error:
        queries = []
        log_web_event(info, {"stage": "plan", "ok": False, "error": str(error)}, institution)
    if not queries:
        info["fallback_query"] = True
        queries = [f"{question} {institution} 教授" if institution else question]
    return queries[:3]


def plan_topic_retry(question: str, info: dict, institution: str, previous_queries: list[str]) -> list[str]:
    retry_input = (
        f"{question}\n\n此前实际查询均未获得有效证据：{'；'.join(previous_queries)}\n"
        "这一次只规划一个明显不同的主题查询：优先使用本地法条原文中的核心制度、行为或法律效果术语，"
        "结合目标机构和论文、期刊、教授等限定，不要只改变条号的数字或中文写法，不要重复此前查询。"
    )
    try:
        queries = DEEPSEEK.plan_web_queries(article_planning_context(retry_input), institution=institution)
    except DeepSeekError as error:
        log_web_event(info, {"stage": "plan", "ok": False, "error": str(error)}, institution)
        return []
    previous = {re.sub(r"\s+", "", query) for query in previous_queries}
    return [query for query in queries if re.sub(r"\s+", "", query) not in previous][:1]


def institution_retry_queries(question: str, institution: str, planned: list[str]) -> list[str]:
    retries = list(planned[1:])
    primary = planned[0] if planned else ""
    labels = article_label_variants(question)
    labels.sort(key=lambda label: label in primary)
    law_title = extract_law_title(question) or "相关法律"
    for label in labels:
        retries.append(f"{law_title} {label} {institution} 教授 论文")
    queries = []
    seen = {re.sub(r"\s+", "", query) for query in planned[:1]}
    for query in retries:
        identity = re.sub(r"\s+", "", query)
        if identity not in seen:
            seen.add(identity)
            queries.append(query)
    return queries[:2]


def search_candidates(question: str, queries: list[str], info: dict, seen_urls: set,
                      institution: str | None = None) -> list[tuple[dict, str]]:
    candidates = []
    for query in queries:
        info["search_count"] += 1
        if query not in info["queries"]:
            info["queries"].append(query)
        try:
            results = WEB.search(query, question)
            log_web_event(info, {
                "stage": "search", "query": query, "ok": True, "result_count": len(results),
            }, institution)
        except WebSearchError as error:
            log_web_event(info, {"stage": "search", "query": query, "ok": False, "error": str(error)}, institution)
            continue
        for item in sorted(results, key=lambda result: candidate_rank(result, question), reverse=True):
            url = urllib.parse.urldefrag(str(item.get("url") or "").strip())[0]
            if not url:
                continue
            identity = (institution, url)
            if identity in seen_urls:
                log_web_event(info, {
                    "stage": "filter", "query": query, "url": url, "ok": False,
                    "reason": "同一机构检索中的重复网址，已跳过",
                }, institution)
                info["rejected_count"] += 1
                continue
            seen_urls.add(identity)
            candidates.append(({**item, "url": url}, query))
            info["candidate_count"] += 1
            log_web_event(info, {
                "stage": "candidate", "query": query, "url": url, "ok": True,
                "title": str(item.get("title") or ""),
            }, institution)
    return sorted(candidates, key=lambda pair: candidate_rank(pair[0], question), reverse=True)


def institution_association(item: dict, fetched: dict, institution: str) -> bool:
    # A university domain only proves who published or hosted the page, not the scholar's affiliation.
    searchable = " ".join(str(item.get(key) or "") for key in ("title", "snippet", "summary"))
    searchable += " " + fetched["title"] + " " + fetched["content"]
    between = r"[^。！？；\n]{0,50}"
    for marker in INSTITUTION_MARKERS[institution]:
        escaped = re.escape(marker)
        if re.search(escaped + between + ACADEMIC_TITLE_RE, searchable):
            return True
        if re.search(ACADEMIC_TITLE_RE + between + escaped, searchable):
            return True
        # 题录页（知网/万方/维普）常只给"机构：北京大学法学院"，不含职称词，
        # 这类元数据同样能证明作者单位，不能因为缺"教授"二字就整篇丢弃。
        if re.search(AFFILIATION_LABEL_RE + escaped, searchable):
            return True
    return False


# 案件人物只能来自网页实际文本，且必须与目标机构教师身份近距离共现。
# 姓氏约束和右侧边界用于避免把“据介绍罗翔”“教授罗翔表示”等误切成错误姓名。
CASE_NAME_RE = (
    rf"(?:[{CASE_SINGLE_SURNAMES}][\u4e00-\u9fff]{{1,2}}?|"
    rf"(?:{'|'.join(CASE_COMPOUND_SURNAMES)})[\u4e00-\u9fff]{{1,2}}?)"
)
CASE_NAME_RIGHT_RE = (
    r"(?=\s|[，,。；;：:、（）()“”\"'《》]|$|表示|认为|指出|分析|评论|接受|应邀|围绕|在|就|对|曾|系|是|的|也|还|均|则|并|又|再|将|现|同时)"
)
CASE_OPINION_RE = re.compile(
    r"认为|指出|表示|分析|评论|评析|解读|观点|看法|演讲|讲座|访谈|新书|著作|发言|"
    r"对话|受访|接受采访|提出|强调|建议|主张|讨论|阐释|评议"
)
CASE_CORE_GENERIC = {
    "案件", "案例", "案情", "事件", "新闻", "热搜", "通报", "最新", "最近", "判决",
    "法院", "法律", "法学", "观点", "评论", "演讲", "访谈", "新书", "教授", "老师",
    "教师", "学者", "四校", "法学院", "高校", "怎么看", "如何看", "分析", "解读",
}


def extract_case_scholar_names(text: str, institution: str) -> list[str]:
    """保守提取与指定学校教师身份相邻的中文姓名，不使用模型或预设名单。"""
    # 保留换行作为段落边界，避免从书名、职务一路跨段匹配到另一位教师。
    normalized = re.sub(r"[^\S\n]+", " ", str(text or ""))
    if not normalized or institution not in INSTITUTION_MARKERS:
        return []
    markers = "|".join(re.escape(marker) for marker in
                       sorted(INSTITUTION_MARKERS[institution], key=len, reverse=True))
    # 允许“甘阳（清华大学……教授）”，但不能跨过右括号或顿号串到另一位嘉宾。
    between = r"[^。！？；\n）)、]{0,30}?"
    # “姓名在前、机构在后”不能跨嘉宾分隔符或书稿目录：否则会把
    # “主编朱伟，中国政法大学教授王楠”和“时间里的……（北京大学教授）”错误关联。
    name_before_institution = r"[^。！？；\n）、，,：:“”/《》]{0,30}?"
    patterns = (
        rf"(?P<name>{CASE_NAME_RE})\s*{ACADEMIC_TITLE_RE}{between}(?:{markers})",
        rf"(?P<name>{CASE_NAME_RE}){CASE_NAME_RIGHT_RE}{name_before_institution}(?:{markers}){between}{ACADEMIC_TITLE_RE}",
        rf"(?:{markers}){between}{ACADEMIC_TITLE_RE}\s*(?P<name>{CASE_NAME_RE}){CASE_NAME_RIGHT_RE}",
        rf"(?:{markers}){between}(?P<name>{CASE_NAME_RE})\s*{ACADEMIC_TITLE_RE}",
    )
    names = []
    for pattern in patterns:
        for match in re.finditer(pattern, normalized):
            name = match.group("name")
            # 避免把“文学院”“周刊”“公司”“谢宇案”“师辩护”等片段当成人名。
            if re.search(r"(?:院|案|刊|司|副|护|活动)$", name):
                continue
            if name not in names and name not in CASE_CORE_GENERIC:
                names.append(name)
    return names


def discover_case_entities(candidates: list[tuple[dict, str]],
                           fetched_pages: list[tuple[dict, str, dict]],
                           institution: str) -> list[dict]:
    """汇总真实搜索摘要与已抓正文中的人物；这些记录本身绝不作为观点证据。"""
    entities: dict[str, dict] = {}

    def inspect(text: str, item: dict, query: str, source: str, field: str) -> None:
        for name in extract_case_scholar_names(text, institution):
            entity = entities.setdefault(name, {"name": name, "institution": institution, "sources": []})
            if len(entity["sources"]) >= 6:
                continue
            position = text.find(name)
            entity["sources"].append({
                "source": source, "field": field, "url": str(item.get("url") or ""),
                "query": query, "excerpt": text[max(0, position - 35):position + len(name) + 55],
            })

    for item, query in candidates:
        for field in ("title", "snippet", "summary"):
            inspect(str(item.get(field) or ""), item, query, "search_result", field)
    for item, query, fetched in fetched_pages:
        for field in ("title", "content"):
            inspect(str(fetched.get(field) or ""), item, query, "fetched_page", field)
    return list(entities.values())[:12]


def case_core_text(question: str, base_query: str) -> str:
    """使用路由检索词保留案件核心词，去掉学校与资料形式限定。"""
    text = (base_query or question).strip()
    markers = [marker for values in INSTITUTION_MARKERS.values() for marker in values]
    for marker in sorted(markers, key=len, reverse=True):
        text = text.replace(marker, " ")
    text = re.sub(r"四校|高校|法学院|教授|副教授|老师|教师|学者|论文|期刊", " ", text)
    return re.sub(r"\s+", " ", text).strip()[:160]


def case_relevance_terms(case_core: str, entity: str = "") -> list[str]:
    text = case_core
    if entity:
        text = text.replace(entity, " ")
    text = re.sub(r"评论|演讲|访谈|新书|著作|观点|解读|教授|副教授|老师|教师|学者", " ", text)
    text = re.sub(r"^(?:我想了解|我想知道|请介绍|请分析|请讲解|介绍一下|分析|讲解)", "", text.strip())
    terms = []
    for raw in re.split(r"[\s，,。；;：:、？?！!《》〈〉“”\"'|｜]+", text):
        term = raw.strip()
        if len(term) < 2 or term in CASE_CORE_GENERIC:
            continue
        candidates = [term]
        if len(term) > 24:
            candidates.extend(re.findall(r"[\u4e00-\u9fff]{2,12}(?:案|事件)", term))
        for candidate in candidates:
            if candidate not in CASE_CORE_GENERIC and candidate not in terms:
                terms.append(candidate)
    return terms[:12]


def case_opinion_relevance_error(search_item: dict, fetched: dict, case_core: str,
                                 entity: str = "") -> str | None:
    """案件观点单独核验案件主题、评论内容与实体；绝不套用论文过滤规则。"""
    del search_item  # 搜索摘要只用于发现人物，不参与最终观点证据的相关性判定。
    body = str(fetched.get("title") or "") + "\n" + str(fetched.get("content") or "")
    terms = case_relevance_terms(case_core, entity)
    # “教育”“反思”等泛词只能辅助扩检，不能单独证明网页在讨论本案。
    # 有案件名、当事人或其他具体术语时，至少命中其中一个。
    specific_terms = [
        term for term in terms
        if re.search(r"案|事件", term) or len(term) >= 3
    ]
    required_terms = specific_terms or terms
    if required_terms and not any(term in body for term in required_terms):
        return "网页正文未体现案件核心词，未作为该案件的观点证据"
    if not terms and case_core.strip() and case_core.strip() not in body:
        return "网页正文未体现案件核心内容，未作为该案件的观点证据"
    if entity and entity not in body:
        return f"网页正文未出现本轮人物 {entity}，未作为该人物的观点证据"
    if not CASE_OPINION_RE.search(str(fetched.get("content") or "")):
        return "网页正文未体现评论、演讲、访谈、新书或明确观点内容"
    return None


def reject_candidate(info: dict, url: str, institution: str | None, reason: str, query: str) -> None:
    info["rejected_count"] = info.get("rejected_count", 0) + 1
    log_web_event(info, {
        "stage": "filter", "query": query, "url": url, "ok": False, "reason": reason,
    }, institution)


def extract_excerpt(text: str, terms: list[str], labels: list[str], limit: int = 1500) -> str:
    """从整页正文中截出与所问条文相关的段落，作为可核验的"论文语句"。

    只做段落级筛选，不在句中断开，保证被引用的语句仍是原文的完整子串。
    """
    if not text:
        return ""
    keys = [term for term in (terms or []) if len(term) >= 4] + [label for label in (labels or []) if label]
    if not keys:
        return ""
    picked, total = [], 0
    for paragraph in re.split(r"\n+", text):
        stripped = paragraph.strip()
        if len(stripped) < 12 or not any(key in stripped for key in keys):
            continue
        picked.append(stripped)
        total += len(stripped)
        if total >= limit:
            break
    return "\n".join(picked)


def fetch_candidates(candidates: list[tuple[dict, str]], info: dict, institution: str | None = None,
                     evidence_limit: int = 4, candidate_limit: int = 5,
                     question: str | None = None,
                     discovery_sink: list[tuple[dict, str, dict]] | None = None,
                     case_mode: bool = False, case_core: str = "",
                     entity: str = "") -> list[dict]:
    evidence = []
    processed_urls = set()
    batch = candidates[:candidate_limit]

    # 逐条串行抓取时，每个页面要等 2–5 秒，十个候选就是半分钟。
    # 这里并发抓取、串行判定：顺序和日志不变，墙钟时间降到最慢那一条。
    from concurrent.futures import ThreadPoolExecutor

    def _fetch(pair: tuple[dict, str]):
        search_item, query = pair
        url = str(search_item["url"])
        try:
            return pair, WEB.fetch(url), None
        except WebSearchError as error:
            return pair, None, error

    if batch:
        with ThreadPoolExecutor(max_workers=min(len(batch), 8)) as pool:
            fetched_results = list(pool.map(_fetch, batch))
    else:
        fetched_results = []

    for (search_item, query), fetched, error in fetched_results:
        url = str(search_item["url"])
        processed_urls.add(url)
        info["fetch_attempt_count"] = info.get("fetch_attempt_count", 0) + 1
        if error is not None:
            log_web_event(info, {
                "stage": "fetch", "query": query, "url": url, "ok": False, "error": str(error),
            }, institution)
            continue
        info["content_fetched_count"] = info.get("content_fetched_count", 0) + 1
        log_web_event(info, {"stage": "fetch", "query": query, "url": url, "ok": True}, institution)
        if discovery_sink is not None:
            discovery_sink.append((search_item, query, fetched))
        if institution and not institution_association(search_item, fetched, institution):
            reject_candidate(info, url, institution, "网页未明确体现目标机构教师关联，未作为该校证据", query)
            continue
        if case_mode:
            relevance_error = case_opinion_relevance_error(search_item, fetched, case_core, entity)
            if relevance_error:
                reject_candidate(info, url, institution, relevance_error, query)
                continue
        if question:
            relevance_error = page_relevance_error(question, search_item, fetched, query)
            if relevance_error:
                reject_candidate(info, url, institution, relevance_error, query)
                continue
        item = normalize_web_evidence(search_item, fetched, query)
        if question:
            item["excerpt"] = extract_excerpt(
                item["text"],
                topic_query_terms(question, query),
                article_label_variants(question),
            )
        if question and article_numbers(question):
            direct_article_match = bool(
                set(article_numbers(question)) & set(article_numbers(fetched_page_text(search_item, fetched)))
            )
            item["relevance_basis"] = "article_text" if direct_article_match else "topic_paper"
            if not direct_article_match:
                item["article_label"] = "主题相关论文网页"
                item["source_notice"] = "该网页未逐字引用所问条号，仅按法条主题相关性采用，不能作为逐条释义。" + item["source_notice"]
        if case_mode:
            item["relevance_basis"] = "case_topic_and_opinion_text"
            item["retrieval_round"] = info.get("_active_round", "first")
            item["query_source"] = info.get("_query_source", "case_base_query")
            if entity:
                item["entity"] = entity
        if institution:
            item["institution"] = institution
            item["section_path"] = [institution, item["source_name"], "四校定向检索"]
        evidence.append(item)
        log_web_event(info, {"stage": "evidence", "query": query, "url": url, "ok": True}, institution)
        if len(evidence) >= evidence_limit:
            break
    for search_item, query in candidates:
        url = str(search_item["url"])
        if url in processed_urls:
            continue
        reason = "已获得本轮所需有效证据，停止继续抓取" if len(evidence) >= evidence_limit else f"超出本轮最多 {candidate_limit} 个正文抓取候选的上限"
        reject_candidate(info, url, institution, reason, query)
    return evidence


def general_web_search(question: str, info: dict, seen_urls: set,
                       initial_query: str | None = None) -> list[dict]:
    # 路由阶段已经给出检索词时直接复用，避免为同一问题再做一次模型规划。
    if initial_query and initial_query.strip():
        queries = [initial_query.strip()[:300]]
        log_web_event(info, {"stage": "plan", "ok": True, "query": queries[0],
                             "reason": "复用路由阶段的联网检索词"})
    else:
        queries = plan_queries(question, info)
    for query in queries:
        candidates = search_candidates(question, [query], info, seen_urls)
        evidence = fetch_candidates(candidates, info, question=question)
        # 普通全网检索首条查询已有有效正文即停止；备选词只在失败时使用。
        if evidence:
            return evidence
    return []


def _search_one_institution(question: str, institution: str, base_query: str = "",
                            candidate_limit: int = 8) -> tuple[list[dict], dict]:
    """法条仍只搜一次；案件最多两轮，第二轮只使用首轮真实文本发现的人物。"""
    local = {
        "queries": [], "search_count": 0, "candidate_count": 0,
        "fetch_attempt_count": 0, "content_fetched_count": 0,
        "rejected_count": 0, "events": [], "entities": [],
        "case_mode": bool(CASE_QUERY_RE.search(question)), "second_round": False,
        "second_round_entity": "",
        "_active_round": "first", "_query_source": "case_base_query",
    }
    seen_urls: set = set()
    evidence: list[dict] = []
    try:
        core = case_core_text(question, base_query or question)
        query = f"{base_query} {institution}".strip() if base_query else question
        if local["case_mode"]:
            # 观点检索去掉“判决”等泛化任务词，保留案件名和当事人。
            core_terms = case_relevance_terms(core)
            query = f"{' '.join(core_terms) if core_terms else core} {institution}".strip()
            # 首轮就覆盖观点常见载体，避免只搜“案件名＋教授评论”而漏掉新书对谈。
            source_forms = " ".join(
                term for term in ("评论", "演讲", "访谈", "新书", "对谈") if term not in query
            )
            query = f"{query} {source_forms}".strip()
        query = query[:300]
        fetched_pages: list[tuple[dict, str, dict]] = []
        candidates = search_candidates(question, [query], local, seen_urls, institution)
        evidence = fetch_candidates(
            candidates, local, institution, evidence_limit=1,
            candidate_limit=candidate_limit, question=question,
            discovery_sink=fetched_pages if local["case_mode"] else None,
            case_mode=local["case_mode"], case_core=core,
        )
        if not local["case_mode"]:
            return evidence, local

        entities = discover_case_entities(candidates, fetched_pages, institution)
        local["entities"] = [entity["name"] for entity in entities]
        for entity in entities:
            log_web_event(local, {
                "stage": "entity", "ok": True, "entity": entity["name"],
                "sources": entity["sources"], "reason": "姓名与目标机构教师身份在真实检索文本中相邻出现",
            }, institution)
        if not entities:
            log_web_event(local, {
                "stage": "entity", "ok": True, "entity_count": 0,
                "reason": "首轮真实文本未发现可可靠提取的目标机构教师姓名",
            }, institution)

        for item in evidence:
            haystack = str(item.get("document_title") or "") + "\n" + str(item.get("text") or "")
            item["entities"] = [entity["name"] for entity in entities if entity["name"] in haystack]
        covered = {name for item in evidence for name in item.get("entities", [])}
        pending = [entity for entity in entities if entity["name"] not in covered]
        if not pending or covered:
            reason = (
                "首轮未发现人物，不追加搜索" if not entities else
                "首轮人物均已获得可用正文观点证据" if not pending else
                "首轮已获得至少一位人物的可用正文证据，其他线索不追加检索"
            )
            log_web_event(local, {
                "stage": "second_round", "ok": True, "skipped": True,
                "reason": reason,
                "deferred_entities": [entity["name"] for entity in pending],
            }, institution)
            return evidence, local

        # 每校第二轮只有这一次搜索，因此只取首个尚无证据的人物；其余人物有界跳过。
        selected = pending[0]
        name = selected["name"]
        local["second_round"] = True
        local["second_round_entity"] = name
        local["_active_round"] = "second"
        local["_query_source"] = "first_round_entity"
        local["_active_entity"] = name
        second_query = f"{name} {core} {institution} 评论 演讲 访谈 新书"
        second_query = re.sub(r"\s+", " ", second_query).strip()[:300]
        log_web_event(local, {
            "stage": "second_round", "ok": True, "query": second_query,
            "entity_sources": selected["sources"],
            "reason": "首轮发现该人物但未获得其可用正文观点证据，执行一次精准查询",
            "deferred_entities": [entity["name"] for entity in pending[1:]],
        }, institution)
        second_candidates = search_candidates(
            question, [second_query], local, seen_urls, institution,
        )
        evidence.extend(fetch_candidates(
            second_candidates, local, institution, evidence_limit=1,
            candidate_limit=2, question=question, case_mode=True,
            case_core=core, entity=name,
        ))
    except Exception as error:  # noqa: BLE001
        local["events"].append({"stage": "error", "ok": False, "error": f"单校检索异常：{error}"})
    return evidence, local


def _merge_institution_info(info: dict, institution: str, evidence: list[dict], local: dict) -> None:
    info["queries"].extend(query for query in local["queries"] if query not in info["queries"])
    for key in ("search_count", "candidate_count", "fetch_attempt_count",
                "content_fetched_count", "rejected_count"):
        info[key] += local.get(key, 0)
    info["events"].extend(local["events"])
    empty_reason = ("全部查询后仍未获得同时满足案件主题、观点内容与机构关联的有效正文"
                    if local.get("case_mode") else "全部查询后仍未获得同时满足法条相关性与机构关联的有效正文")
    info["institution_results"].append({
        "institution": institution,
        "found": bool(evidence),
        "search_count": local.get("search_count", 0),
        "candidate_count": local.get("candidate_count", 0),
        "content_fetched_count": local.get("content_fetched_count", 0),
        "rejected_count": local.get("rejected_count", 0),
        "entities": local.get("entities", []),
        "second_round": local.get("second_round", False),
        "second_round_entity": local.get("second_round_entity", ""),
        **({} if evidence else {"reason": empty_reason}),
    })


def retrieve_web_evidence(question: str, enabled: bool = True,
                          force_targeted: bool = False,
                          initial_query: str | None = None) -> tuple[list[dict], dict]:
    targeted = force_targeted or should_search_target_institutions(question)
    info = {
        "enabled": enabled,
        "provider": "deepseek_web",
        "configured": WEB.configured,
        "mode": "four_institutions" if targeted else "general",
        "queries": [],
        "search_count": 0,
        "candidate_count": 0,
        "fetch_attempt_count": 0,
        "content_fetched_count": 0,
        "rejected_count": 0,
        "fetched_count": 0,
        "events": [],
        "queried_at": utc_now(),
        "fallback_general": False,
    }
    if not enabled:
        info["notice"] = "先给出法条与教材；公开资料与四校观点随后在后台补充"
        return [], info
    if not WEB.configured:
        info["notice"] = "未配置 DeepSeek API Key，无法联网搜索"
        return [], info

    evidence = []
    seen_urls = set()
    if targeted:
        info["institution_results"] = []
        case_query = bool(CASE_QUERY_RE.search(question))
        from concurrent.futures import ThreadPoolExecutor

        # 路由已有案件搜索词时直接复用；法条论文检索仍保留主题规划。
        if initial_query and initial_query.strip():
            base_query = initial_query.strip()[:300]
            log_web_event(info, {"stage": "plan", "ok": True, "query": base_query,
                                 "reason": "复用路由阶段的案件检索词"})
        else:
            base_queries = plan_queries(question, info)
            base_query = base_queries[0] if base_queries else question
        results: dict[str, tuple[list[dict], dict]] = {}
        with ThreadPoolExecutor(max_workers=len(TARGET_INSTITUTIONS)) as pool:
            futures = {
                pool.submit(_search_one_institution, question, institution, base_query,
                            3 if case_query else 8): institution
                for institution in TARGET_INSTITUTIONS
            }
            for future, institution in futures.items():
                try:
                    results[institution] = future.result(timeout=100)
                except Exception as error:  # noqa: BLE001
                    results[institution] = ([], {"queries": [], "search_count": 0, "candidate_count": 0,
                                                 "fetch_attempt_count": 0, "content_fetched_count": 0,
                                                 "rejected_count": 0,
                                                 "events": [{"stage": "error", "ok": False,
                                                             "error": f"单校检索超时或失败：{error}"}]})
        for institution in TARGET_INSTITUTIONS:
            institution_evidence, local_info = results[institution]
            evidence.extend(institution_evidence)
            _merge_institution_info(info, institution, institution_evidence, local_info)
        # 法条论文检索保留普通全网兜底；案件不再叠加一轮可达数分钟的泛化搜索。
        if not evidence and not case_query:
            info["fallback_general"] = True
            evidence = general_web_search(question, info, seen_urls)
    else:
        evidence = general_web_search(question, info, seen_urls, initial_query=initial_query)

    info["fetched_count"] = len(evidence)
    failures = [event for event in info["events"] if not event["ok"]]
    failure_note = f"；另有 {len(failures)} 个检索、抓取或筛选步骤未采用" if failures else ""
    if not evidence:
        if targeted:
            info["notice"] = ("四校定向检索未获得可核验的公开观点，本次不追加其他网页冒充四校观点"
                              if case_query else "四校定向检索及普通全网兜底均未获得有效正文")
        else:
            info["notice"] = "联网检索未获得可读取的网页正文；本次没有使用搜索摘要代替原文"
    elif info["fallback_general"]:
        info["notice"] = f"四校均未获得有效正文，已退而求其次采用 {len(evidence)} 个其他公开网页{failure_note}"
    elif targeted:
        info["notice"] = f"四校分别检索后，已读取 {len(evidence)} 个有效网页；回答只介绍实际命中的学校{failure_note}"
    else:
        info["notice"] = f"已通过 DeepSeek 官方工具读取 {len(evidence)} 个公开网页正文{failure_note}"
    return evidence, info


def response_payload(question: str, evidence: list[dict], system_prompt: str | None = None) -> dict:
    """生成通用 Agent 回答；资料与校验用于增强，不再决定模型能否作答。"""
    numbered = numbered_evidence(evidence)
    if not DEEPSEEK.configured:
        return {
            "status": "model_unavailable",
            "answer": "当前未配置 DeepSeek API Key，无法生成模型回答。已检索到的资料仍可在右侧查看；配置密钥后即可直接提问，不要求必须先命中证据。",
            "evidence": numbered,
            "verification": {"passed": False, "checks": [{"name": "模型配置", "passed": False, "detail": "未配置 DEEPSEEK_API_KEY"}]},
        }
    try:
        answer = DEEPSEEK.generate(question, evidence, system_prompt=system_prompt)
        verification = verify_answer(answer, evidence)
        attempts = 1
        if not verification["passed"]:
            failed_checks = [
                f"{check['name']}：{check['detail']}"
                for check in verification["checks"] if not check["passed"]
            ]
            if any(check["name"] == "引文逐字匹配" and not check["passed"] for check in verification["checks"]):
                failed_checks.append("无法匹配的长引文请改为不带引号的概括，或注明出处尚未核验。")
            answer = DEEPSEEK.generate(
                question, evidence, correction_notes=failed_checks,
                previous_answer=answer, system_prompt=system_prompt,
            )
            verification = verify_answer(answer, evidence)
            attempts = 2
        verification.pop("_uncovered_segments", None)
        verification["attempts"] = attempts
        # 校验问题需要提示，但不能再把有价值的完整回答替换成一段拦截说明。
        return {
            "status": "verified" if verification["passed"] else "verification_failed",
            "answer": answer, "evidence": numbered, "verification": verification,
        }
    except DeepSeekError as error:
        return {
            "status": "model_unavailable",
            "answer": f"模型暂时不可用：{error}。已检索到的资料仍可在右侧核验，请稍后重试。",
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
                "web_search_configured": WEB.configured,
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

    def handle_schools(self) -> None:
        """后台只补充四校观点；案件与法条主回答均不重写、不重复检索。"""
        try:
            data = self.read_json()
            question = normalize_article_refs(str(data.get("question", "")).strip())
            if not question:
                self.send_json({"error": "问题不能为空"}, 400)
                return
            plan = _plan_for(question)
            is_case = plan.get("kind") == "case"
            # 所有法律问题都可补充四校专业讲评，不再受路由模型的布尔标记限制。
            # 案件保留原问题中的案件名，并复用路由搜索词，避免再次规划。
            evidence, info = retrieve_web_evidence(
                question, True, force_targeted=True,
                initial_query=(plan.get("web_query") or question) if is_case else None,
            )
            section = ""
            verification = None
            section_status = "insufficient_evidence"
            numbered = numbered_evidence(evidence)
            if evidence and DEEPSEEK.configured:
                try:
                    generated = response_payload(
                        question, evidence, system_prompt=SCHOOL_SECTION_PROMPT,
                    )
                    section = generated["answer"]
                    verification = generated["verification"]
                    section_status = generated["status"]
                    numbered = generated["evidence"]
                except Exception as error:  # noqa: BLE001
                    info["generation_error"] = str(error)
            self.send_json({
                "status": section_status,
                "replace": False,
                "section_title": "四校教授观点",
                "section": section,
                "verification": verification,
                "evidence": numbered,
                "retrieval": {"web_search": info, "plan": plan},
            })
        except (ValueError, json.JSONDecodeError) as error:
            self.send_json({"error": f"请求格式错误：{error}"}, 400)
        except Exception as error:  # noqa: BLE001
            self.log_error("schools error: %r", error)
            self.send_json({"error": "四校观点检索失败"}, 500)

    def _sse(self, payload: dict) -> None:
        self.wfile.write(f"data: {json.dumps(payload, ensure_ascii=False)}\n\n".encode("utf-8"))
        self.wfile.flush()

    def handle_stream(self) -> None:
        """通用 Agent 主链路：并行准备增强资料，再一次流式生成完整回答。"""
        try:
            data = self.read_json()
        except (ValueError, json.JSONDecodeError) as error:
            self.send_json({"error": f"请求格式错误：{error}"}, 400)
            return
        question = normalize_article_refs(str(data.get("question", "")).strip())
        if not question:
            self.send_json({"error": "问题不能为空"}, 400)
            return
        if len(question) > 2000:
            self.send_json({"error": "单次任务不能超过 2000 字"}, 400)
            return
        web_enabled = data.get("web_search", True) is not False

        # 先建立 SSE，检索期间也能向前端报告真实阶段，不再只显示固定转圈。
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache, no-transform")
        self.send_header("Connection", "close")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()
        self.close_connection = True

        started = time.monotonic()
        timings: dict[str, float] = {}

        def _elapsed_ms() -> float:
            return round((time.monotonic() - started) * 1000, 1)

        def _stage(name: str) -> None:
            self._sse({"stage": name, "elapsed_ms": _elapsed_ms()})

        def _timed_retrieval() -> dict:
            timings["total"] = _elapsed_ms()
            return {**retrieval, "timings_ms": dict(timings)}

        _stage("planning")
        plan = route_question(question)
        timings["routing"] = _elapsed_ms()
        is_case = plan.get("kind") == "case"
        _stage("retrieving")

        if is_case and web_enabled:
            # 两路检索互不依赖，并行执行；网页查询复用路由结果，不再规划第二次。
            from concurrent.futures import ThreadPoolExecutor

            with ThreadPoolExecutor(max_workers=2) as pool:
                future_base = pool.submit(evidence_by_plan, question, plan)
                future_web = pool.submit(
                    retrieve_web_evidence, question, True, False,
                    plan.get("web_query") or question,
                )
                evidence, retrieval = _settle(future_base, "法条与教材检索")
                web_evidence, web_info = _settle(future_web, "公开网页检索")
            retrieval.setdefault("source", "none")
            retrieval.setdefault("provider", None)
        else:
            try:
                evidence, retrieval = evidence_by_plan(question, plan)
            except PkulawError as error:
                evidence, retrieval = [], {"source": "none", "provider": None, "error": str(error)}
            web_evidence, web_info = [], {}
        timings["retrieval"] = _elapsed_ms()

        if is_case and web_enabled:
            _stage("supplementing")
            laws = case_articles(question, web_evidence or evidence)
            evidence = dedupe_evidence(laws + evidence + web_evidence)
            retrieval["web_search"] = web_info
            notices = [value for value in (retrieval.get("notice"), web_info.get("notice")) if value]
            retrieval["notice"] = "；".join(dict.fromkeys(notices))
            timings["supplemental_laws"] = _elapsed_ms()

        numbered = numbered_evidence(evidence)
        system_prompt = CASE_FULL_PROMPT if is_case else None
        # 用户需要四校专业讲评，不再由模型的 need_schools 标记决定是否启动。
        # 主回答保持一次成稿，四校命中后在同一回答下追加，不能只留在检索记录中。
        followup = "schools" if web_enabled else None
        _stage("generating")

        if not DEEPSEEK.configured:
            payload = response_payload(question, evidence, system_prompt=system_prompt)
            self._sse({"done": True, **payload, "retrieval": _timed_retrieval(), "followup": followup})
            return

        chunks: list[str] = []
        try:
            for delta in DEEPSEEK.generate_stream(question, numbered, system_prompt=system_prompt):
                if "first_token" not in timings:
                    timings["first_token"] = _elapsed_ms()
                chunks.append(delta)
                self._sse({"delta": delta})
            timings["generation_complete"] = _elapsed_ms()
        except DeepSeekError as error:
            self._sse({"error": str(error)})
        except (BrokenPipeError, ConnectionResetError):
            return

        answer = "".join(chunks).strip()
        if not answer:
            payload = response_payload(question, evidence, system_prompt=system_prompt)
            self._sse({"done": True, **payload, "retrieval": _timed_retrieval(), "followup": followup})
            return
        _stage("verifying")
        verification = verify_answer(answer, numbered)
        if not verification["passed"]:
            failed_checks = [check for check in verification["checks"] if not check["passed"]]
            # 只有长引语无法逐字匹配时，就地去掉引号并按间接表述提示；
            # 其他引用问题仍交给模型完整修订，不降低编号与来源要求。
            if all(check["name"] == "引文逐字匹配" for check in failed_checks):
                repaired, repair_count = repair_unverified_quotes(answer, numbered)
                if repair_count:
                    answer = repaired
                    verification = verify_answer(answer, numbered)
                    verification["checks"].append({
                        "name": "直接引语风险处理", "passed": True,
                        "detail": f"{repair_count} 处未逐字匹配的长引语已改为不带引号的间接表述，相关内容仍请结合来源复核",
                    })
                    self._sse({"replace": True, "answer": answer})
            if not verification["passed"]:
                failed = [f"{check['name']}：{check['detail']}"
                          for check in verification["checks"] if not check["passed"]]
                try:
                    retry = DEEPSEEK.generate(
                        question, numbered, correction_notes=failed,
                        previous_answer=answer, system_prompt=system_prompt,
                    )
                    retry_verification = verify_answer(retry, numbered)
                    if retry and retry_verification["passed"]:
                        self._sse({"replace": True, "answer": retry})
                        answer, verification = retry, retry_verification
                except DeepSeekError:
                    pass
        verification.pop("_uncovered_segments", None)
        self._sse({"done": True, "status": "verified" if verification["passed"] else "verification_failed",
                   "answer": answer, "evidence": numbered, "verification": verification,
                   "retrieval": _timed_retrieval(), "followup": followup})

    def do_POST(self) -> None:
        path = urllib.parse.urlparse(self.path).path
        if path == "/api/chat/stream":
            self.handle_stream()
            return
        if path == "/api/schools":
            self.handle_schools()
            return
        if path != "/api/chat":
            self.send_json({"error": "接口不存在"}, 404)
            return
        try:
            data = self.read_json()
            question = normalize_article_refs(str(data.get("question", "")).strip())
            document_id = data.get("document_id") or None
            source = str(data.get("source") or "auto")
            if not question:
                self.send_json({"error": "问题不能为空"}, 400)
                return
            if len(question) > 2000:
                self.send_json({"error": "单次任务不能超过 2000 字"}, 400)
                return
            web_enabled = data.get("web_search", True) is not False
            try:
                evidence, retrieval = retrieve_evidence(question, document_id, source)
            except PkulawError as error:
                evidence = []
                retrieval = {
                    "source": "none", "provider": None, "error": str(error),
                    "notice": f"北大法宝检索失败，仍将由模型结合其他资料回答：{error}",
                }
            textbook_evidence = retrieve_textbook_evidence(question, evidence)
            # 法条 + 教材已经能答"讲解"类问题；此时再跑全网检索，用户只会干等二十多秒。
            need_web = web_enabled and not (textbook_evidence and not WANTS_FRESH_WEB_RE.search(question))
            web_evidence, web_info = retrieve_web_evidence(question, need_web)
            if web_enabled and not need_web:
                web_info["notice"] = "已命中法条与教材，本次未做全网检索（需要教授观点或最新资料请直接说明）"
            if textbook_evidence:
                # 教材排在网页之前：它比自媒体和文库更权威，也比教案更接近通说。
                evidence = evidence + textbook_evidence
                retrieval["textbook"] = {
                    "count": len(textbook_evidence),
                    "pages": [item["article_label"] for item in textbook_evidence],
                }
            seen_identities = {(item.get("provider"), item["source_url"], item["article_label"]) for item in evidence}
            for item in web_evidence:
                identity = (item.get("provider"), item["source_url"], item["article_label"])
                if identity not in seen_identities:
                    evidence.append(item)
                    seen_identities.add(identity)
            retrieval["web_search"] = web_info
            notices = [value for value in [retrieval.get("notice"), web_info.get("notice")] if value]
            retrieval["notice"] = "；".join(dict.fromkeys(notices))
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
