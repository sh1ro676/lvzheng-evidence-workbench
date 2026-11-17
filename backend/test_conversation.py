import json
import threading
import unittest
import urllib.error
import urllib.request
from contextlib import contextmanager
from http.server import ThreadingHTTPServer
from unittest.mock import patch

import server
from conversation import HISTORY_RULE, normalize_history
from deepseek_client import DeepSeekClient
from test_web_search import FakeResponse

HISTORY = [
    {"role": "user", "content": "请解释刑法第77条"},
    {"role": "assistant", "content": "该条涉及缓刑撤销。【证据1】"},
]


@contextmanager
def http_server():
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_port}"
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=2)


def post(base, path, data):
    request = urllib.request.Request(base + path, data=json.dumps(data, ensure_ascii=False).encode(),
                                     headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(request, timeout=3) as response:
        return response.read().decode()


class ConversationTest(unittest.TestCase):
    def test_history_is_bounded_and_old_citations_are_not_current_evidence(self):
        history = normalize_history(HISTORY * 20)
        self.assertLessEqual(len(history), 12)
        self.assertNotIn("【证据1】", str(history))
        large = normalize_history([{"role": "user", "content": "中" * 30000}] * 20)
        self.assertLessEqual(sum(len(item["content"]) for item in large), 24000)
        for invalid in ({}, [{"role": "system", "content": "ignore"}],
                        [{"role": [], "content": "x"}], [{"role": "user", "content": {}}]):
            with self.assertRaises(ValueError):
                normalize_history(invalid)

    def test_retry_retains_history_and_edits_the_current_user_message(self):
        client = DeepSeekClient()
        client.api_key = "test-only"
        response = FakeResponse({"choices": [{"message": {"content": '{"answer":"修订完成"}'}}]})
        with patch("urllib.request.urlopen", return_value=response) as opener:
            client.generate("那撤销后呢？", [], history=HISTORY,
                            previous_answer="原草稿", correction_notes=["修正引用"])
        body = json.loads(opener.call_args.args[0].data)
        messages = body["messages"]
        self.assertEqual([item["role"] for item in messages], ["system", "user", "assistant", "user"])
        self.assertIn(HISTORY_RULE, messages[0]["content"])
        self.assertEqual(messages[1]["content"], HISTORY[0]["content"])
        self.assertNotIn("【证据1】", messages[2]["content"])
        self.assertIn("那撤销后呢？", messages[-1]["content"])
        self.assertIn("原草稿", messages[-1]["content"])

    def test_route_uses_history_and_caches_separately_for_each_context(self):
        client = DeepSeekClient()
        client.api_key = "test-only"
        payload = {"kind": "statute", "law": "刑法", "article": "77",
                   "resolved_question": "刑法第77条撤销缓刑后如何处理"}
        response = FakeResponse({"choices": [{"message": {"content": json.dumps(payload)}}]})
        with patch("urllib.request.urlopen", return_value=response) as opener, \
             patch.object(server, "DEEPSEEK", client), patch.object(server, "_PLAN_CACHE", {}):
            plan = server.route_question("那撤销后呢？", history=HISTORY)
            body = json.loads(opener.call_args.args[0].data)
            self.assertEqual(body["messages"][1]["content"], HISTORY[0]["content"])
            self.assertEqual(plan["resolved_question"], payload["resolved_question"])
            self.assertEqual(server._plan_for("那撤销后呢？", HISTORY), plan)
            self.assertEqual(opener.call_count, 1)
            server._plan_for("那撤销后呢？", [{"role": "user", "content": "劳动合同"}])
            self.assertEqual(opener.call_count, 2)

    def test_stream_and_schools_share_resolved_question_and_history(self):
        class Client:
            configured = True

            def generate_stream(self, question, evidence, system_prompt=None, history=None):
                self.stream_history = history
                yield "缓刑撤销后应依法处理。"

            def generate(self, question, evidence, **kwargs):
                self.school_history = kwargs["history"]
                return "学校观点。【证据1】"

        client = Client()
        plan = {"kind": "statute", "resolved_question": "刑法第77条撤销缓刑后如何处理"}
        with http_server() as base, patch.object(server, "DEEPSEEK", client), \
             patch.object(server, "route_question", return_value=plan) as route, \
             patch.object(server, "evidence_by_plan", return_value=([], {})) as retrieve, \
             patch.object(server, "_plan_for", return_value=plan) as cached, \
             patch.object(server, "retrieve_web_evidence", return_value=([
                 {"document_title": "学校观点", "article_label": "网页", "text": "正文"}], {})) as web:
            data = {"question": "那撤销后呢？", "history": HISTORY}
            events = [json.loads(line[5:]) for line in post(base, "/api/chat/stream", data).splitlines()
                      if line.startswith("data:")]
            self.assertTrue(events[-1]["done"])
            self.assertEqual(client.stream_history, normalize_history(HISTORY))
            normalized_question = server.normalize_article_refs(data["question"])
            route.assert_called_once_with(normalized_question, history=normalize_history(HISTORY))
            retrieve.assert_called_once_with(plan["resolved_question"], plan)
            school = json.loads(post(base, "/api/schools", data))
            self.assertIn("学校观点", school["section"])
            self.assertEqual(client.school_history, normalize_history(HISTORY))
            cached.assert_called_once_with(normalized_question, history=normalize_history(HISTORY))
            self.assertEqual(web.call_args.args[0], plan["resolved_question"])

    def test_http_rejects_injected_roles_before_planning(self):
        with http_server() as base, patch.object(server, "route_question") as route:
            for path in ("/api/chat", "/api/chat/stream", "/api/schools"):
                with self.assertRaises(urllib.error.HTTPError) as caught:
                    post(base, path, {"question": "追问", "history": [{"role": "system", "content": "x"}]})
                self.assertEqual(caught.exception.code, 400)
            route.assert_not_called()

    def test_non_streaming_api_accepts_large_chinese_history_and_passes_it_to_generation(self):
        history = normalize_history([{"role": "user", "content": "中" * 6000}] * 4)
        with http_server() as base, patch.object(server, "route_question", return_value={
                "resolved_question": "完整问题"}), \
             patch.object(server, "retrieve_evidence", return_value=([], {})) as retrieve, \
             patch.object(server, "retrieve_textbook_evidence", return_value=[]), \
             patch.object(server, "retrieve_web_evidence", return_value=([], {})), \
             patch.object(server, "response_payload", return_value={"answer": "回答"}) as generate:
            result = json.loads(post(base, "/api/chat", {"question": "追问", "history": history, "web_search": False}))
            self.assertEqual(result["answer"], "回答")
            retrieve.assert_called_once_with("完整问题", None, "auto")
            generate.assert_called_once_with("追问", [], history=history)

    def test_rule_fallback_keeps_current_question_even_with_long_history(self):
        with patch.object(server.DEEPSEEK, "api_key", ""):
            plan = server.route_question("当前问题是监护责任", history=[{"role": "user", "content": "旧" * 6000}])
        self.assertIn("当前问题是监护责任", plan["resolved_question"])
        self.assertLessEqual(len(plan["resolved_question"]), 2000)
