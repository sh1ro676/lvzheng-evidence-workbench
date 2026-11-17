import json
import threading
import unittest
import urllib.request
from http.server import ThreadingHTTPServer
from unittest.mock import patch, PropertyMock

import server
from deepseek_client import CASE_SYSTEM_PROMPT, DeepSeekClient


EVIDENCE = [{
    "document_title": "中华人民共和国民法典",
    "article_label": "第一千一百六十五条",
    "text": "行为人因过错侵害他人民事权益造成损害的，应当承担侵权责任。",
}]
SUPPORTED_ANSWER = "根据所引条文，行为人因过错侵害他人民事权益并造成损害时，应当承担侵权责任。【证据1】"
UNCITED_ANSWER = "这是一段没有标注证据的实质性法律回答，其长度已经超过十二个字。"


class FakeDeepSeek:
    configured = True

    def __init__(self, answers):
        self.answers = iter(answers)
        self.calls = []

    def generate(self, question, evidence, correction_notes=None, previous_answer=None, system_prompt=None):
        self.calls.append({
            "question": question,
            "evidence": evidence,
            "correction_notes": correction_notes,
            "previous_answer": previous_answer,
            "system_prompt": system_prompt,
        })
        return next(self.answers)


class RetrievalRoutingTest(unittest.TestCase):
    def test_auto_mode_prefers_pkulaw_and_skips_local(self):
        article = {
            "document_title": "中华人民共和国刑法", "article_label": "第二百六十四条",
            "text": "盗窃公私财物，数额较大的……", "source_name": "北大法宝",
            "source_url": "", "section_path": [], "status": "现行有效",
            "published_at": "", "effective_at": "",
        }
        with patch.object(type(server.PKULAW), "configured", new_callable=PropertyMock, return_value=True), \
             patch.object(server.PKULAW, "search", return_value={
                "evidence": [article], "strategy": "fatiao", "tool": "get_law_article",
                "cached": False, "queried_at": "now", "notice": ""}), \
             patch.object(server.STORE, "search", return_value=[]) as local:
            evidence, retrieval = server.retrieve_evidence("盗窃怎么判")
        self.assertEqual(retrieval["source"], "pkulaw")
        self.assertEqual(len(evidence), 1)
        local.assert_not_called()

    def test_pkulaw_failure_does_not_fall_back_to_local_or_block_answer(self):
        with patch.object(type(server.PKULAW), "configured", new_callable=PropertyMock, return_value=True), \
             patch.object(server.PKULAW, "search", side_effect=server.PkulawError("超时")), \
             patch.object(server.STORE, "search", return_value=[]) as local:
            evidence, retrieval = server.retrieve_evidence("盗窃怎么判")
        self.assertEqual(evidence, [])
        self.assertEqual(retrieval["source"], "none")
        self.assertIn("仍由模型", retrieval["notice"])
        local.assert_not_called()

    def test_forced_local_source_is_available_for_library_only(self):
        with patch.object(server.STORE, "search", return_value=[]) as local:
            server.retrieve_evidence("民事责任和刑事责任的区别", source="local")
        local.assert_called_once()

    def test_composite_sentencing_question_retrieves_all_core_issues(self):
        queries = server.criminal_core_queries("未成年人，并且偷盗，强奸，大概会怎么判刑")
        self.assertEqual(queries, [
            "《刑法》第十七条", "《刑法》第四十九条", "《刑法》第二百六十四条",
            "《刑法》第二百三十六条", "《刑法》第六十九条",
        ])


class AnswerRetryTest(unittest.TestCase):
    def test_missing_source_is_regenerated_once_without_filtering_analysis(self):
        client = FakeDeepSeek([UNCITED_ANSWER, SUPPORTED_ANSWER])
        with patch.object(server, "DEEPSEEK", client):
            result = server.response_payload("请解释第1165条", EVIDENCE)
        self.assertEqual(result["status"], "verified")
        self.assertEqual(result["answer"], SUPPORTED_ANSWER)
        self.assertEqual(result["verification"]["attempts"], 2)
        self.assertEqual(len(client.calls), 2)
        self.assertIsNone(client.calls[0]["correction_notes"])
        self.assertEqual(client.calls[1]["previous_answer"], UNCITED_ANSWER)
        self.assertTrue(any("检索资料已标注" in note for note in client.calls[1]["correction_notes"]))
        self.assertNotIn("_uncovered_segments", result["verification"])

    def test_fake_quote_retry_allows_unquoted_summary(self):
        fake_quote = "有观点认为“这是一段并非证据原文、只是由模型生成且长度足够触发核对的概括性表述”，应当重新表述。【证据1】"
        client = FakeDeepSeek([fake_quote, SUPPORTED_ANSWER])
        with patch.object(server, "DEEPSEEK", client):
            result = server.response_payload("请解释第1165条", EVIDENCE)
        self.assertEqual(result["status"], "verified")
        self.assertTrue(any("引文逐字匹配" in note for note in client.calls[1]["correction_notes"]))

    def test_second_citation_failure_still_displays_complete_answer(self):
        client = FakeDeepSeek([UNCITED_ANSWER, UNCITED_ANSWER])
        with patch.object(server, "DEEPSEEK", client):
            result = server.response_payload("请解释第1165条", EVIDENCE)
        self.assertEqual(result["status"], "verification_failed")
        self.assertEqual(result["answer"], UNCITED_ANSWER)
        self.assertNotIn("未展示原始回答", result["answer"])

    def test_no_evidence_still_generates_general_agent_answer(self):
        general = "可以从构成要件、归责原则和举证责任三个角度理解这一问题，无需强行引用外部资料。"
        client = FakeDeepSeek([general])
        with patch.object(server, "DEEPSEEK", client):
            result = server.response_payload("民事责任和刑事责任有什么区别？", [])
        self.assertEqual(result["status"], "verified")
        self.assertEqual(result["answer"], general)
        self.assertEqual(result["evidence"], [])

    def test_general_legal_knowledge_outside_evidence_is_not_rejected(self):
        answer = "第1165条规定一般过错责任；比较第1077条可以帮助理解不同制度的规范目的。【证据1】"
        client = FakeDeepSeek([answer])
        with patch.object(server, "DEEPSEEK", client):
            result = server.response_payload("比较两个法律规则", EVIDENCE)
        self.assertEqual(result["status"], "verified")
        self.assertEqual(result["answer"], answer)

    def test_retrieved_textbook_must_be_used_in_substantive_commentary(self):
        textbook = {
            "provider": "textbook", "document_title": "刑法学（第六版）",
            "source_name": "张明楷《刑法学》", "article_label": "书内 p100",
            "text": "责任主义要求行为与责任之间具有对应关系。",
        }
        supported = "教材讲评：张明楷《刑法学》从责任主义说明行为与责任的对应关系，可用于理解本问题。【证据1】"
        client = FakeDeepSeek(["只回答刑法的一般概念，没有使用已检索教材。", supported])
        with patch.object(server, "DEEPSEEK", client):
            result = server.response_payload("讲解刑法中的责任主义", [textbook])
        self.assertEqual(result["status"], "verified")
        self.assertEqual(result["answer"], supported)
        self.assertEqual(result["verification"]["attempts"], 2)
        self.assertTrue(any("教材已用于实质讲评" in note for note in client.calls[1]["correction_notes"]))


class AgentPromptTest(unittest.TestCase):
    def test_case_prompt_requires_background_timeline_procedure_and_analysis(self):
        for phrase in ("事件背景与起因", "关键行为", "重要时间节点", "侦查、起诉、审判", "不要只罗列罪名"):
            self.assertIn(phrase, CASE_SYSTEM_PROMPT)
        self.assertNotIn("每条要点末尾必须标", CASE_SYSTEM_PROMPT)

    def test_evidence_is_reference_not_knowledge_boundary(self):
        client = DeepSeekClient()
        messages, _ = client._messages("测试", [], None)
        self.assertIn("不是你的知识边界", messages[1]["content"])
        self.assertIn("仍请完整回答", messages[1]["content"])
        self.assertNotIn("唯一可用的证据", messages[1]["content"])

    def test_stream_prompt_preserves_case_instructions_after_removing_json(self):
        client = DeepSeekClient()
        stream_messages, _ = client._messages("案件", [], CASE_SYSTEM_PROMPT, plain=True)
        stream_prompt = stream_messages[0]["content"]
        self.assertIn("事件背景与起因", stream_prompt)
        self.assertIn("不要只罗列罪名", stream_prompt)
        self.assertNotIn('"answer"', stream_prompt)
        json_messages, _ = client._messages("案件", [], CASE_SYSTEM_PROMPT, plain=False)
        self.assertIn("事件背景与起因", json_messages[0]["content"])
        self.assertIn('"answer"', json_messages[0]["content"])

    def test_textbook_evidence_requires_substantive_commentary(self):
        client = DeepSeekClient()
        textbook = {
            "provider": "textbook", "document_title": "刑法学（第六版）下册",
            "source_name": "张明楷《刑法学》", "source_url": "", "section_path": ["故意杀人罪"],
            "article_label": "书内 p1000", "status": "教材 OCR", "published_at": "", "effective_at": "",
            "text": "故意杀人罪的构成要件与责任判断需要分别分析。",
        }
        messages, _ = client._messages("分析故意杀人罪", [textbook], CASE_SYSTEM_PROMPT, plain=True)
        prompt = messages[0]["content"]
        self.assertIn("教材讲评", prompt)
        self.assertIn("不得只把教材列在证据栏", prompt)
        self.assertIn("本案事实", prompt)


class CaseStreamTest(unittest.TestCase):
    def test_case_is_fully_answered_once_without_placeholder_or_school_rewrite(self):
        class StreamingDeepSeek(FakeDeepSeek):
            def __init__(self):
                super().__init__([])
                self.stream_calls = []

            def generate_stream(self, question, evidence, system_prompt=None):
                self.stream_calls.append({"question": question, "evidence": evidence, "system_prompt": system_prompt})
                yield "一、事件经过\n公开资料记载了案件背景、行为和审判经过。【证据1】\n"
                yield "二、法律分析\n结合构成要件进一步分析，不要求每句话都有编号。"

        client = StreamingDeepSeek()
        web_evidence = [{
            "document_title": "案件公开报道", "article_label": "网页正文",
            "text": "公开报道记载了案件背景、行为和审判经过。", "provider": "deepseek_web",
        }]
        plan = {"kind": "case", "need_web": True, "need_schools": False, "web_query": "某案件"}
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            with patch.object(server, "DEEPSEEK", client), \
                 patch.object(server, "route_question", return_value=plan), \
                 patch.object(server, "evidence_by_plan", return_value=([], {"source": "none", "provider": None})), \
                 patch.object(server, "retrieve_web_evidence", return_value=(web_evidence, {"enabled": True, "notice": ""})), \
                 patch.object(server, "case_articles", return_value=[]):
                request = urllib.request.Request(
                    f"http://127.0.0.1:{httpd.server_port}/api/chat/stream",
                    data=json.dumps({"question": "请讲解某案件", "web_search": True}).encode("utf-8"),
                    headers={"Content-Type": "application/json"}, method="POST",
                )
                with urllib.request.urlopen(request, timeout=5) as response:
                    body = response.read().decode("utf-8")
            events = [json.loads(line[5:]) for line in body.splitlines() if line.startswith("data:")]
            final = events[-1]
            self.assertTrue(final["done"])
            self.assertEqual(final["followup"], "schools")
            self.assertNotIn("正在检索", final["answer"])
            self.assertIn("事件经过", final["answer"])
            self.assertIn("法律分析", final["answer"])
            self.assertEqual(len(client.stream_calls), 1)
            self.assertEqual(client.stream_calls[0]["system_prompt"], CASE_SYSTEM_PROMPT)
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=2)


class SchoolEndpointTest(unittest.TestCase):
    def test_case_school_search_only_appends_without_case_rewrite(self):
        question = "我想了解北大杀母案"
        plan = {"kind": "case", "need_web": True, "need_schools": False,
                "web_query": "北大杀母案 吴谢宇 判决"}
        school_evidence = [{
            "document_title": "中国政法大学教师案件评析", "article_label": "网页正文",
            "text": "中国政法大学教授在本文中分析案件涉及的死刑适用问题。",
            "provider": "deepseek_web", "institution": "中国政法大学",
        }]
        client = FakeDeepSeek(["中国政法大学教授从死刑适用角度评析本案。【证据1】"])
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            with patch.object(server, "DEEPSEEK", client), \
                 patch.object(server, "_plan_for", return_value=plan), \
                 patch.object(server, "retrieve_web_evidence",
                              return_value=(school_evidence, {"enabled": True, "notice": ""})) as retrieve, \
                 patch.object(server, "evidence_by_plan", side_effect=AssertionError("不得重复检索并重写案件")), \
                 patch.object(server, "case_articles", side_effect=AssertionError("不得重复检索法条")):
                request = urllib.request.Request(
                    f"http://127.0.0.1:{httpd.server_port}/api/schools",
                    data=json.dumps({"question": question}).encode("utf-8"),
                    headers={"Content-Type": "application/json"}, method="POST",
                )
                with urllib.request.urlopen(request, timeout=5) as response:
                    payload = json.loads(response.read().decode("utf-8"))
            retrieve.assert_called_once_with(question, True, force_targeted=True,
                                             initial_query="北大杀母案 吴谢宇 判决")
            self.assertFalse(payload["replace"])
            self.assertEqual(payload["section_title"], "四校教授观点")
            self.assertIn("中国政法大学", payload["section"])
            self.assertEqual(client.calls[0]["system_prompt"], server.SCHOOL_SECTION_PROMPT)
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=2)


class StreamingClient(FakeDeepSeek):
    def __init__(self, answer="这是结合模型自身法律知识生成的完整回答，不需要等待外部证据。"):
        super().__init__([])
        self.answer = answer
        self.stream_calls = []

    def generate_stream(self, question, evidence, system_prompt=None):
        self.stream_calls.append({"question": question, "evidence": evidence, "system_prompt": system_prompt})
        yield self.answer[:12]
        yield self.answer[12:]


class StreamPerformanceTest(unittest.TestCase):
    def _run_stream(self, client, plan, base_retrieval, web_retrieval):
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            with patch.object(server, "DEEPSEEK", client), \
                 patch.object(server, "route_question", return_value=plan), \
                 patch.object(server, "evidence_by_plan", side_effect=base_retrieval), \
                 patch.object(server, "retrieve_web_evidence", side_effect=web_retrieval), \
                 patch.object(server, "case_articles", return_value=[]):
                request = urllib.request.Request(
                    f"http://127.0.0.1:{httpd.server_port}/api/chat/stream",
                    data=json.dumps({"question": "请分析这个法律问题", "web_search": True}).encode("utf-8"),
                    headers={"Content-Type": "application/json"}, method="POST",
                )
                with urllib.request.urlopen(request, timeout=5) as response:
                    body = response.read().decode("utf-8")
            return [json.loads(line[5:]) for line in body.splitlines() if line.startswith("data:")]
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=2)

    def test_case_retrievals_overlap_and_real_stages_precede_delta(self):
        barrier = threading.Barrier(2)
        seen_web_query = []

        def base_retrieval(_question, _plan):
            barrier.wait(timeout=2)
            return [], {"source": "none", "provider": None}

        def web_retrieval(_question, _enabled, _targeted, initial_query):
            seen_web_query.append(initial_query)
            barrier.wait(timeout=2)
            return [], {"enabled": True, "notice": ""}

        client = StreamingClient()
        plan = {"kind": "case", "need_web": True, "need_schools": False,
                "web_query": "路由阶段已经规划的检索词"}
        events = self._run_stream(client, plan, base_retrieval, web_retrieval)

        self.assertFalse(barrier.broken, "两路检索若串行执行，同步屏障会被打破")
        self.assertEqual(seen_web_query, ["路由阶段已经规划的检索词"])
        stages = [event["stage"] for event in events if "stage" in event]
        self.assertEqual(stages, ["planning", "retrieving", "supplementing", "generating", "verifying"])
        first_delta = next(index for index, event in enumerate(events) if "delta" in event)
        self.assertGreater(first_delta, stages.index("generating"))
        self.assertEqual(len(client.stream_calls), 1)
        self.assertIn("first_token", events[-1]["retrieval"]["timings_ms"])
        self.assertIn("total", events[-1]["retrieval"]["timings_ms"])

    def test_no_evidence_still_streams_before_done(self):
        client = StreamingClient("没有检索到外部资料，也应直接使用模型已有知识完整作答。")
        plan = {"kind": "concept", "need_web": False, "need_schools": False, "web_query": ""}
        events = self._run_stream(
            client, plan,
            lambda *_args: ([], {"source": "none", "provider": None}),
            lambda *_args: self.fail("非案件主链路不应调用网页检索"),
        )
        content_events = [event for event in events if "delta" in event or event.get("done")]
        self.assertEqual(len(client.stream_calls), 1)
        self.assertEqual(client.stream_calls[0]["evidence"], [])
        self.assertEqual([key for event in content_events for key in event if key in {"delta", "done"}][-1], "done")
        self.assertTrue(any("delta" in event for event in content_events[:-1]))
        self.assertEqual(events[-1]["answer"], "没有检索到外部资料，也应直接使用模型已有知识完整作答。")
        self.assertEqual(events[-1]["followup"], "schools",
                         "即使路由将 need_schools 设为 false，也必须追加四校讲评")

    def test_non_case_web_request_is_honored_by_the_main_answer(self):
        calls = []
        client = StreamingClient("已核对最新法规资料。【证据1】")
        plan = {"kind": "concept", "need_web": True, "web_query": "劳动合同 最新规定"}

        def web_retrieval(question, enabled, targeted, initial_query):
            calls.append((question, enabled, targeted, initial_query))
            return EVIDENCE, {"enabled": True}

        events = self._run_stream(client, plan, lambda *_args: ([], {}), web_retrieval)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][3], "劳动合同 最新规定")
        self.assertEqual(events[-1]["evidence"][0]["text"], EVIDENCE[0]["text"])
        self.assertTrue(events[-1]["retrieval"]["web_search"]["enabled"])

    def test_unverified_long_quote_is_repaired_without_full_regeneration(self):
        indirect = "这是一段并非证据原文而只是模型概括且长度超过二十个字的表述"
        client = StreamingClient(f"根据条文，有观点称“{indirect}”，仍应继续分析。【证据1】")
        plan = {"kind": "concept", "need_web": False, "need_schools": False, "web_query": ""}
        events = self._run_stream(
            client, plan,
            lambda *_args: (EVIDENCE, {"source": "pkulaw", "provider": "pkulaw"}),
            lambda *_args: self.fail("非案件主链路不应调用网页检索"),
        )
        self.assertEqual(client.calls, [], "仅引号不匹配时不应重新生成整篇回答")
        self.assertNotIn("“", events[-1]["answer"])
        self.assertIn(indirect, events[-1]["answer"])
        self.assertEqual(events[-1]["status"], "verified")
        repair = next(check for check in events[-1]["verification"]["checks"]
                      if check["name"] == "直接引语风险处理")
        self.assertIn("间接表述", repair["detail"])


class CaseArticlePerformanceTest(unittest.TestCase):
    def test_case_articles_are_parallel_and_keep_crime_order(self):
        barrier = threading.Barrier(3)
        labels = {"232": "第二百三十二条", "234": "第二百三十四条", "266": "第二百六十六条"}

        class FakeProvider:
            configured = True

            def search(self, query):
                barrier.wait(timeout=2)
                number = next(number for number in labels if f"第{number}条" in query)
                return {"evidence": [{
                    "document_title": "中华人民共和国刑法", "article_label": labels[number],
                    "text": f"刑法第{number}条原文",
                }]}

        with patch.object(server, "PKULAW", FakeProvider()):
            evidence = server.case_articles("故意杀人、故意伤害并诈骗", [])
        self.assertFalse(barrier.broken, "三个法条查询应同时开始")
        self.assertEqual([item["article_label"] for item in evidence],
                         ["第二百三十二条", "第二百三十四条", "第二百六十六条"])


if __name__ == "__main__":
    unittest.main()
