import json
import unittest
from unittest.mock import patch

import server
from deepseek_client import DeepSeekClient, verify_answer
from web_search_client import (
    DeepSeekWebClient,
    WebSearchError,
    normalize_web_evidence,
    validate_public_url,
)


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def read(self, *_args):
        return json.dumps(self.payload, ensure_ascii=False).encode("utf-8")


class FakePlanner:
    def plan_web_queries(self, _question, institution=None):
        if institution:
            return [f"民法典 第368条 居住权 {institution} 教授"]
        return ["民法典 第368条 李永军 居住权"]


class FakeWeb:
    configured = True

    def __init__(self):
        self.queries = []

    def search(self, query, _question):
        self.queries.append(query)
        return [
            {"title": "可用资料", "url": "https://law.example.edu.cn/article", "site_name": "某法学院", "rerank_score": 0.9},
            {"title": "不可读资料", "url": "https://example.com/blocked", "site_name": "示例网站", "rerank_score": 0.8},
        ]

    def fetch(self, url):
        if url.endswith("blocked"):
            raise WebSearchError("网页没有可读取的正文")
        return {"title": "李永军教授谈居住权", "content": "《中华人民共和国民法典》第368条规定，居住权无偿设立，但是当事人另有约定的除外。李永军教授在文中分析了居住权登记。", "url": url}


class InstitutionWeb:
    configured = True

    def __init__(self, found_institutions=None):
        self.found_institutions = found_institutions or set()
        self.queries = []

    def search(self, query, _question):
        self.queries.append(query)
        institution = next((name for name in server.TARGET_INSTITUTIONS if name in query), None)
        if institution:
            index = server.TARGET_INSTITUTIONS.index(institution)
            return [{"title": "公开研究资料", "url": f"https://example.com/institution-{index}", "site_name": "公开资料", "rerank_score": 0.9}]
        return [{"title": "公开权威解读", "url": "https://example.com/general", "site_name": "法律资料库", "rerank_score": 0.9}]

    def fetch(self, url):
        if url.endswith("general"):
            return {"title": "居住权登记规则解读", "content": "《中华人民共和国民法典》第368条涉及居住权登记。本文依据公开文本分析登记规则及其适用。", "url": url}
        index = int(url.rsplit("-", 1)[1])
        institution = server.TARGET_INSTITUTIONS[index]
        if institution not in self.found_institutions:
            return {"title": "其他机构资料", "content": "这篇资料讨论居住权登记，但没有提及本次目标学校或其教师。", "url": url}
        return {"title": f"{institution}教授谈居住权", "content": f"《中华人民共和国民法典》第368条规定居住权登记。{institution}一位教授在本文中分析了登记规则的体系意义和适用条件。", "url": url}


class WebRetrievalTest(unittest.TestCase):
    def test_only_successfully_fetched_pages_become_evidence(self):
        web = FakeWeb()
        with patch.object(server, "DEEPSEEK", FakePlanner()), patch.object(server, "WEB", web):
            evidence, info = server.retrieve_web_evidence("请查找民法典第368条居住权登记的公开网页资料")
        self.assertEqual(web.queries, ["民法典 第368条 李永军 居住权"])
        self.assertEqual(len(evidence), 1)
        self.assertEqual(evidence[0]["provider"], "deepseek_web")
        self.assertEqual(evidence[0]["match_type"], "web")
        self.assertEqual(info["fetched_count"], 1)
        self.assertTrue(any(event["stage"] == "fetch" and not event["ok"] for event in info["events"]))
        self.assertIn("未采用", info["notice"])

    def test_partial_institution_hits_do_not_trigger_general_fallback(self):
        found = {"中国政法大学", "北京大学法学院"}
        web = InstitutionWeb(found)
        with patch.object(server, "DEEPSEEK", FakePlanner()), patch.object(server, "WEB", web):
            evidence, info = server.retrieve_web_evidence("民法典第368条，需要四校高校老师的讲解")
        # 已命中学校不重试；两所未命中学校各执行三条查询。
        self.assertEqual(len(web.queries), 8)
        self.assertEqual({item["institution"] for item in evidence}, found)
        self.assertEqual([item["found"] for item in info["institution_results"]], [True, False, True, False])
        self.assertEqual([item["search_count"] for item in info["institution_results"]], [1, 3, 1, 3])
        self.assertFalse(info["fallback_general"])
        self.assertFalse(any("general" in event.get("url", "") for event in info["events"]))

    def test_general_fallback_runs_only_when_all_institutions_are_empty(self):
        web = InstitutionWeb()
        with patch.object(server, "DEEPSEEK", FakePlanner()), patch.object(server, "WEB", web):
            evidence, info = server.retrieve_web_evidence("民法典第368条，需要四校高校老师的讲解")
        self.assertEqual(len(web.queries), 13)  # 四校各三次 + 普通全网兜底一次
        self.assertTrue(info["fallback_general"])
        self.assertTrue(all(not item["found"] for item in info["institution_results"]))
        self.assertEqual(len(evidence), 1)
        self.assertNotIn("institution", evidence[0])
        self.assertEqual(evidence[0]["source_url"], "https://example.com/general")
        self.assertTrue(all(event.get("reason") for event in info["events"] if event["stage"] == "filter"))

    def test_unassociated_page_is_excluded_from_institution_evidence(self):
        info = {"events": []}
        candidates = [({"title": "无关页面", "url": "https://example.com/unrelated", "site_name": "某资讯站"}, "中国政法大学 教授 居住权")]
        with patch.object(server, "WEB") as web:
            web.fetch.return_value = {"title": "居住权资料", "content": "这篇网页讨论居住权登记，但全文没有目标机构或其教师的任何信息。", "url": "https://example.com/unrelated"}
            evidence = server.fetch_candidates(candidates, info, "中国政法大学", evidence_limit=1, candidate_limit=1)
        self.assertEqual(evidence, [])
        self.assertIn("未明确体现目标机构教师关联", info["events"][-1]["reason"])
        self.assertEqual(info["events"][-1]["stage"], "filter")

    def test_guest_lecturer_from_other_school_is_not_target_school_evidence(self):
        search_item = {"title": "学术讲座：民法典中的市场经济元素", "url": "https://msfxy.nwupl.edu.cn/xwdt/115674.htm", "site_name": "西北政法大学民商法学院"}
        fetched = {"title": "赵万一教授学术讲座", "content": "西南政法大学教授、博士生导师赵万一教授为我院师生作学术讲座。讲座分析了民法典第八条对公序良俗的规定。", "url": search_item["url"]}
        self.assertFalse(server.institution_association(search_item, fetched, "西北政法大学"))

    def test_section_headings_do_not_need_citations(self):
        search_item = {"title": "页面", "url": "https://law.example.edu.cn/article", "site_name": "某法学院"}
        fetched = {"title": "居住权登记规则", "content": "《中华人民共和国民法典》第368条规定，设立居住权的，应当向登记机构申请居住权登记。", "url": search_item["url"]}
        evidence = [normalize_web_evidence(search_item, fetched, "测试")]
        answer = "一、法条原文\n《中华人民共和国民法典》第368条规定，设立居住权的，应当向登记机构申请居住权登记。【证据1】\n（一）中国政法大学易军教授\n四、检索范围与证据限制说明\n上述内容仅概述当前证据明确支持的登记要求。【证据1】"
        self.assertTrue(verify_answer(answer, evidence)["passed"])

    def test_every_institution_hit_must_be_cited(self):
        evidence = []
        for index, institution in enumerate(("中国政法大学", "西北政法大学"), start=1):
            search_item = {"title": f"页面{index}", "url": f"https://example.com/{index}", "site_name": institution}
            fetched = {"title": f"{institution}教授观点", "content": f"{institution}教授在本文中分析居住权登记规则的适用。", "url": search_item["url"]}
            item = normalize_web_evidence(search_item, fetched, "测试")
            item["institution"] = institution
            evidence.append(item)
        incomplete = verify_answer("中国政法大学资料分析了居住权登记规则的适用。【证据1】", evidence)
        self.assertFalse(incomplete["passed"])
        coverage = next(check for check in incomplete["checks"] if check["name"] == "命中学校均有回应")
        self.assertIn("西北政法大学", coverage["detail"])
        complete = verify_answer("中国政法大学资料分析了居住权登记规则的适用。【证据1】\n西北政法大学资料也分析了居住权登记规则的适用。【证据2】", evidence)
        self.assertTrue(complete["passed"], complete)

    def test_disabled_web_search_does_not_call_tools(self):
        with patch.object(server, "WEB") as web:
            evidence, info = server.retrieve_web_evidence("测试", False)
        self.assertEqual(evidence, [])
        self.assertFalse(info["enabled"])
        web.search.assert_not_called()

    def test_web_evidence_verifier_uses_page_text_for_law_and_article(self):
        search_item = {"title": "页面", "url": "https://law.example.edu.cn/article", "site_name": "某法学院"}
        fetched = {"title": "教授观点", "content": "《中华人民共和国民法典》第368条规定，设立居住权的，应当向登记机构申请居住权登记。", "url": search_item["url"]}
        evidence = [normalize_web_evidence(search_item, fetched, "测试")]
        answer = "网页明确提及《中华人民共和国民法典》第368条，并据此说明居住权登记要求。【证据1】"
        result = verify_answer(answer, evidence)
        self.assertTrue(result["passed"], result)

    def test_web_article_title_allows_author_prefix_alias(self):
        search_item = {"title": "页面", "url": "https://law.example.edu.cn/article", "site_name": "某法学院"}
        fetched = {"title": "李永军：论居住权在民法典中的体系定位", "content": "《中华人民共和国民法典》第368条规定，居住权自登记时设立。文章分析了登记规则。", "url": search_item["url"]}
        evidence = [normalize_web_evidence(search_item, fetched, "测试")]
        answer = "《论居住权在民法典中的体系定位》一文讨论了《中华人民共和国民法典》第368条的登记规则。【证据1】"
        self.assertTrue(verify_answer(answer, evidence)["passed"])

    def test_local_or_private_fetch_urls_are_rejected(self):
        with self.assertRaises(WebSearchError):
            validate_public_url("http://127.0.0.1/internal")
        with self.assertRaises(WebSearchError):
            validate_public_url("file:///C:/Windows/win.ini")

    def test_empty_fetch_body_is_not_evidence(self):
        client = DeepSeekWebClient(api_key="test")
        with patch("web_search_client.validate_public_url", return_value="https://example.com/page"), patch.object(client, "_post", return_value={"title": "空页面", "content": ""}):
            with self.assertRaises(WebSearchError):
                client.fetch("https://example.com/page")

    def test_deepseek_function_call_plans_queries(self):
        client = DeepSeekClient()
        client.api_key = "test"
        payload = {"choices": [{"message": {"tool_calls": [
            {"function": {"name": "web_search", "arguments": json.dumps({"query": "民法典 第368条 李永军"})}},
            {"function": {"name": "web_search", "arguments": json.dumps({"query": "居住权 登记 中国政法大学"})}},
        ]}}]}
        with patch("urllib.request.urlopen", return_value=FakeResponse(payload)):
            self.assertEqual(client.plan_web_queries("测试"), ["民法典 第368条 李永军", "居住权 登记 中国政法大学"])

    def test_thinking_route_reserves_reasoning_tokens_and_timeout(self):
        client = DeepSeekClient()
        client.api_key = "test"
        client.model = "deepseek-reasoner"
        observed = {}

        def respond(request, timeout):
            observed["body"] = json.loads(request.data.decode("utf-8"))
            observed["timeout"] = timeout
            content = json.dumps({
                "kind": "case", "need_web": True, "need_schools": True,
                "web_query": "清华大学 吴谢宇案 教授 评论",
            }, ensure_ascii=False)
            return FakeResponse({"choices": [{"message": {"content": content}}]})

        with patch("urllib.request.urlopen", side_effect=respond):
            plan = client.plan_route("清华大学教授怎么看吴谢宇案")
        self.assertEqual(observed["body"]["max_tokens"], 4096)
        self.assertEqual(observed["timeout"], 90)
        self.assertEqual(plan["web_query"], "清华大学 吴谢宇案 教授 评论")

    def test_article_number_variants_and_fixed_institutions(self):
        question = "搜索《中华人民共和国民法典》第22条论文，必须是四校老师的"
        variants = server.article_label_variants(question)
        self.assertIn("第22条", variants)
        self.assertIn("第二十二条", variants)
        planned = ["民法典 第22条 北京大学法学院 教授 论文"]
        retries = server.institution_retry_queries(question, "北京大学法学院", planned)
        self.assertEqual(len(retries), 2)
        self.assertTrue(any("学报" in query or "期刊" in query for query in retries))
        self.assertTrue(all("北京大学法学院" in query for query in retries))
        self.assertEqual(server.TARGET_INSTITUTIONS, ["中国政法大学", "西北政法大学", "北京大学法学院", "清华大学法学院"])
        self.assertFalse(any("人民大学" in institution for institution in server.TARGET_INSTITUTIONS))

    def test_first_hit_does_not_execute_planned_fallback_queries(self):
        class MultiQueryPlanner:
            def plan_web_queries(self, _question, institution=None):
                return [
                    f"民法典 第368条 {institution} 教授 论文",
                    f"民法典 第三百六十八条 {institution} 教授 论文",
                    f"居住权 登记 {institution} 教授 论文",
                ]

        web = InstitutionWeb(set(server.TARGET_INSTITUTIONS))
        with patch.object(server, "DEEPSEEK", MultiQueryPlanner()), patch.object(server, "WEB", web):
            evidence, info = server.retrieve_web_evidence("民法典第368条，需要四校高校老师的讲解")
        self.assertEqual(len(evidence), 4)
        self.assertEqual(len(web.queries), 4)
        self.assertFalse(any(event["stage"] == "retry" for event in info["events"]))

    def test_only_unmatched_institutions_execute_bounded_retries(self):
        """每校独立停止：已命中一次结束，未命中最多三次。"""
        class CountWeb(InstitutionWeb):
            def __init__(self):
                super().__init__({"中国政法大学"})
                self.calls = {}

            def search(self, query, question):
                self.queries.append(query)
                institution = next((name for name in server.TARGET_INSTITUTIONS if name in query), None)
                self.calls[institution] = self.calls.get(institution, 0) + 1
                if institution == "中国政法大学":
                    return [{"title": "补回的研究资料", "url": "https://example.com/institution-0",
                             "site_name": "公开资料", "rerank_score": 0.9}]
                return []

        web = CountWeb()
        with patch.object(server, "DEEPSEEK", FakePlanner()), patch.object(server, "WEB", web):
            evidence, info = server.retrieve_web_evidence("民法典第368条，需要四校高校老师的讲解")
        self.assertEqual(web.calls, {"中国政法大学": 1, "西北政法大学": 3, "北京大学法学院": 3, "清华大学法学院": 3})
        self.assertEqual([item["institution"] for item in evidence], ["中国政法大学"])
        self.assertTrue(info["institution_results"][0]["found"])
        self.assertTrue(any(event["stage"] == "retry" for event in info["events"]))

    def test_irrelevant_professor_biography_is_rejected(self):
        info = {"events": []}
        search_item = {"title": "李永军教授简介", "url": "https://example.com/biography", "site_name": "百科"}
        fetched = {
            "title": "李永军教授简介",
            "content": "李永军是中国政法大学民商经济法学院教授、博士生导师。本页介绍其学习和工作经历。",
            "url": search_item["url"],
        }
        with patch.object(server, "WEB") as web:
            web.fetch.return_value = fetched
            evidence = server.fetch_candidates(
                [(search_item, "中国政法大学 民法典 第22条 教授")], info, "中国政法大学",
                evidence_limit=1, candidate_limit=1, question="民法典第22条，需要高校老师论文",
            )
        self.assertEqual(evidence, [])
        self.assertEqual(info["events"][-1]["stage"], "filter")
        self.assertIn("未明确提及所问法条", info["events"][-1]["reason"])

    def test_same_article_number_from_another_law_is_rejected(self):
        search_item = {"title": "刑事责任研究", "url": "https://example.com/criminal"}
        fetched = {
            "title": "刑事责任研究",
            "content": "北京大学法学院某教授在本文讨论《刑法》第22条的预备犯处罚规则。",
            "url": search_item["url"],
        }
        reason = server.page_relevance_error("民法典第22条，需要高校老师论文", search_item, fetched)
        self.assertIn("未明确提及所问法律", reason)

    def test_institution_planner_keeps_target_and_returns_three_variants(self):
        client = DeepSeekClient()
        client.api_key = "test"
        queries = ["民法典 第22条 教授 论文", "民法典 第二十二条 教师", "限制民事行为能力 法定代理人 论文"]
        payload = {"choices": [{"message": {"tool_calls": [
            {"function": {"name": "web_search", "arguments": json.dumps({"query": query})}}
            for query in queries
        ]}}]}
        with patch("urllib.request.urlopen", return_value=FakeResponse(payload)):
            planned = client.plan_web_queries("测试", institution="北京大学法学院")
        self.assertEqual(len(planned), 3)
        self.assertTrue(all(query.endswith("北京大学法学院") for query in planned))
        self.assertFalse(any("中国人民大学" in query for query in planned))


    def test_lecture_is_not_used_when_user_explicitly_requests_papers(self):
        search_item = {"title": "柳经纬教授主讲民法典编纂的体系问题", "url": "https://example.com/lecture"}
        fetched = {
            "title": search_item["title"],
            "content": "中国政法大学教授柳经纬在讲座中指出，《民法典》第19条、第22条与第145条的内容存在重复。",
            "url": search_item["url"],
        }
        reason = server.page_relevance_error("搜索民法典第22条相关论文，必须是四校老师的", search_item, fetched)
        self.assertIn("明确要求论文", reason)

    def test_topic_retry_uses_official_article_and_avoids_previous_query(self):
        class TopicPlanner:
            def __init__(self):
                self.question = ""

            def plan_web_queries(self, question, institution=None):
                self.question = question
                return [f"限制民事行为能力 法定代理人 {institution} 教授 论文"]

        planner = TopicPlanner()
        info = {"events": []}
        with patch.object(server, "DEEPSEEK", planner):
            queries = server.plan_topic_retry(
                "搜索《中华人民共和国民法典》第22条相关论文，必须是四校老师的",
                info, "北京大学法学院", ["民法典 第22条 北京大学法学院 教授 论文"],
            )
        self.assertEqual(queries, ["限制民事行为能力 法定代理人 北京大学法学院 教授 论文"])
        self.assertIn("此前实际查询", planner.question)
        self.assertIn("不能完全辨认自己行为", planner.question)


    def test_topic_related_paper_is_explicitly_labeled(self):
        info = {"events": []}
        search_item = {"title": "常鹏翱：意思能力、行为能力与意思自治", "url": "https://example.com/capacity"}
        fetched = {
            "title": search_item["title"],
            "content": "【作者】常鹏翱（北京大学法学院教授、博士生导师）。本文来源于法学期刊，围绕《民法典》自然人的意思能力、行为能力与意思自治展开分析。",
            "url": search_item["url"],
        }
        query = "北京大学法学院 成年限制民事行为能力人 意思能力 论文 教授"
        with patch.object(server, "WEB") as web:
            web.fetch.return_value = fetched
            evidence = server.fetch_candidates(
                [(search_item, query)], info, "北京大学法学院", evidence_limit=1,
                candidate_limit=1, question="搜索民法典第22条相关论文，必须是四校老师的",
            )
        self.assertEqual(len(evidence), 1)
        self.assertEqual(evidence[0]["article_label"], "主题相关论文网页")
        self.assertEqual(evidence[0]["relevance_basis"], "topic_paper")
        self.assertIn("不能作为逐条释义", evidence[0]["source_notice"])

    def test_unrelated_paper_does_not_pass_topic_fallback(self):
        search_item = {"title": "常鹏翱：合同效力研究", "url": "https://example.com/contract"}
        fetched = {
            "title": search_item["title"],
            "content": "【作者】常鹏翱（北京大学法学院教授）。本文来源于法学期刊，分析《民法典》合同编的效力规则。",
            "url": search_item["url"],
        }
        reason = server.page_relevance_error(
            "搜索民法典第22条相关论文，必须是四校老师的", search_item, fetched,
            "北京大学法学院 成年限制民事行为能力人 意思能力 论文 教授",
        )
        self.assertIn("主题相关性", reason)

    def test_route_query_is_reused_without_second_model_planning(self):
        class PlannerMustNotRun:
            def plan_web_queries(self, _question, institution=None):
                raise AssertionError("路由已有检索词时不应再次调用模型规划")

        web = FakeWeb()
        with patch.object(server, "DEEPSEEK", PlannerMustNotRun()), patch.object(server, "WEB", web):
            evidence, info = server.retrieve_web_evidence(
                "请查找民法典第368条居住权登记的公开网页资料",
                initial_query="民法典 第368条 居住权 登记",
            )
        self.assertEqual(web.queries[0], "民法典 第368条 居住权 登记")
        self.assertLessEqual(len(web.queries), 3)
        self.assertEqual(len(evidence), 1)
        self.assertTrue(any(event["stage"] == "plan" and "复用" in event.get("reason", "")
                            for event in info["events"]))

    def test_general_search_expands_when_first_query_has_only_one_source(self):
        class MultiQueryPlanner:
            def plan_web_queries(self, _question, institution=None):
                return ["第一条有效查询", "用于补充来源的备选查询"]

        web = FakeWeb()
        with patch.object(server, "DEEPSEEK", MultiQueryPlanner()), patch.object(server, "WEB", web):
            evidence, _info = server.retrieve_web_evidence("请查找民法典第368条居住权登记的公开网页资料")
        self.assertEqual(web.queries, ["第一条有效查询", "用于补充来源的备选查询"])
        self.assertEqual(len(evidence), 1)

    def test_case_institution_search_is_bounded_and_skips_general_fallback(self):
        class ManyCandidateWeb:
            configured = True

            def __init__(self):
                self.queries = []

            def search(self, query, _question):
                self.queries.append(query)
                institution = next(name for name in server.TARGET_INSTITUTIONS if name in query)
                index = server.TARGET_INSTITUTIONS.index(institution)
                return [{"title": "候选资料", "url": f"https://example.com/{index}-{number}",
                         "site_name": "公开资料", "rerank_score": 0.9} for number in range(10)]

            def fetch(self, url):
                return {"title": "无关资料", "content": "这篇资料没有提及任何目标学校或其教师，不能作为四校观点。",
                        "url": url}

        class PlannerMustNotRun:
            def plan_web_queries(self, _question, institution=None):
                raise AssertionError("案件四校检索不应再次规划")

        web = ManyCandidateWeb()
        with patch.object(server, "DEEPSEEK", PlannerMustNotRun()), patch.object(server, "WEB", web):
            evidence, info = server.retrieve_web_evidence(
                "我想了解北大杀母案", force_targeted=True,
                initial_query="北大杀母案 吴谢宇 判决",
            )
        self.assertEqual(evidence, [])
        self.assertEqual(len(web.queries), 8)
        self.assertTrue(all("吴谢宇" in query for query in web.queries))
        self.assertTrue(any("北大杀母案" in query for query in web.queries))
        self.assertFalse(any("判决" in query for query in web.queries))
        self.assertFalse(info["fallback_general"])
        self.assertEqual([item["candidate_count"] for item in info["institution_results"]], [10, 10, 10, 10])
        self.assertEqual([item["content_fetched_count"] for item in info["institution_results"]], [10, 10, 10, 10])
        self.assertIn("不追加其他网页冒充四校观点", info["notice"])


if __name__ == "__main__":
    unittest.main()
