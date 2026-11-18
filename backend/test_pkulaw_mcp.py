import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from pkulaw_mcp import PkulawError, PkulawMCPClient, PkulawProvider, build_arguments, normalize_evidence, select_strategy


class MockMCPHandler(BaseHTTPRequestHandler):
    calls = []
    fail_auth = False

    def log_message(self, *args):
        pass

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        payload = json.loads(self.rfile.read(length))
        self.__class__.calls.append(payload)
        if self.__class__.fail_auth:
            self.send_response(401)
            self.end_headers()
            self.wfile.write(b'{"message":"invalid token"}')
            return
        request_id = payload.get("id")
        method = payload.get("method")
        if method == "initialize":
            result = {"protocolVersion": "2025-03-26", "capabilities": {}}
        elif method == "notifications/initialized":
            self.send_response(202)
            self.send_header("Mcp-Session-Id", "mock-session")
            self.end_headers()
            return
        elif method == "tools/list":
            result = {"tools": [{
                "name": "get_law_item_content",
                "inputSchema": {
                    "type": "object",
                    "properties": {"title": {"type": "string"}, "tiao_num": {"type": "integer"}},
                    "required": ["title", "tiao_num"],
                },
            }]}
        elif method == "tools/call":
            self.assert_call(payload)
            result = {
                "structuredContent": {"result": [{
                    "title": "中华人民共和国民法典",
                    "article_label": "第一千一百六十五条",
                    "content": "行为人因过错侵害他人民事权益造成损害的，应当承担侵权责任。",
                    "timeliness": "现行有效",
                    "effect_level": "法律",
                    "publish_date": "2020-05-28",
                    "effective_date": "2021-01-01",
                    "id": "mock-doc",
                    "url": "https://www.pkulaw.com/mock",
                }]},
                "isError": False,
            }
        else:
            result = {}
        body = json.dumps({"jsonrpc": "2.0", "id": request_id, "result": result}, ensure_ascii=False)
        if self.path == "/sse":
            body = f"event: message\ndata: {body}\n\n".encode("utf-8")
            content_type = "text/event-stream"
        else:
            body = body.encode("utf-8")
            content_type = "application/json"
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Mcp-Session-Id", "mock-session")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    @staticmethod
    def assert_call(payload):
        if payload["params"]["arguments"] != {"title": "中华人民共和国民法典", "tiao_num": 1165}:
            raise AssertionError("unexpected tool arguments")


class PkulawMCPTest(unittest.TestCase):
    def setUp(self):
        MockMCPHandler.calls = []
        MockMCPHandler.fail_auth = False
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), MockMCPHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_port}"
        self.old_endpoints = dict(__import__("pkulaw_mcp").ENDPOINTS)

    def tearDown(self):
        __import__("pkulaw_mcp").ENDPOINTS.update(self.old_endpoints)
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def provider(self):
        client = PkulawMCPClient(token="test-secret")
        return PkulawProvider(client=client, cache_ttl=900)

    def test_json_protocol_discovery_call_and_cache(self):
        __import__("pkulaw_mcp").ENDPOINTS["fatiao"] = f"{self.base}/json"
        provider = self.provider()
        response = provider.search("《中华人民共和国民法典》第1165条")
        self.assertEqual(response["tool"], "get_law_item_content")
        self.assertEqual(response["evidence"][0]["article_label"], "第一千一百六十五条")
        self.assertEqual(response["evidence"][0]["status"], "现行有效")
        self.assertEqual(response["evidence"][0]["provider"], "pkulaw")
        calls_after_first_search = len(MockMCPHandler.calls)
        cached = provider.search("《中华人民共和国民法典》第1165条")
        self.assertTrue(cached["cached"])
        self.assertEqual(len(MockMCPHandler.calls), calls_after_first_search)
        methods = [call["method"] for call in MockMCPHandler.calls]
        self.assertEqual(methods[:3], ["initialize", "notifications/initialized", "tools/list"])
        self.assertIn("tools/call", methods)

    def test_sse_response(self):
        __import__("pkulaw_mcp").ENDPOINTS["fatiao"] = f"{self.base}/sse"
        response = self.provider().search("《中华人民共和国民法典》第1165条")
        self.assertIn("行为人因过错", response["evidence"][0]["text"])

    def test_auth_error_does_not_expose_token(self):
        MockMCPHandler.fail_auth = True
        __import__("pkulaw_mcp").ENDPOINTS["fatiao"] = f"{self.base}/json"
        with self.assertRaises(PkulawError) as context:
            self.provider().search("《中华人民共和国民法典》第1165条")
        self.assertNotIn("test-secret", str(context.exception))
        self.assertIn("鉴权失败", str(context.exception))

    def test_unconfigured_does_not_call_network(self):
        provider = PkulawProvider(client=PkulawMCPClient(token=""))
        self.assertFalse(provider.status()["configured"])
        with self.assertRaises(PkulawError):
            provider.search("第1165条")

    def test_runtime_schema_drives_arguments(self):
        tool = {"inputSchema": {"properties": {"title": {"type": "string"}, "tiao_num": {"type": "string"}}, "required": ["title", "tiao_num"]}}
        self.assertEqual(build_arguments(tool, "《刑法》第三条", "fatiao"), {"title": "刑法", "tiao_num": "3"})
        live_tool = {"inputSchema": {"properties": {"law_title": {"type": "string"}, "article_number": {"type": "string"}}, "required": ["law_title", "article_number"]}}
        self.assertEqual(build_arguments(live_tool, "《民法典》第1165条", "fatiao"), {"law_title": "民法典", "article_number": "1165"})
        semantic_tool = {"inputSchema": {"properties": {"query_text": {"anyOf": [{"type": "string"}, {"type": "null"}]}, "timeliness": {"anyOf": [{"type": "string"}, {"type": "null"}]}, "size": {"anyOf": [{"type": "integer"}, {"type": "null"}]}}}}
        self.assertEqual(build_arguments(semantic_tool, "跟我讲讲刑法中的低7条，以及你有什么理解", "law_semantic"), {
            "query_text": "跟我讲讲刑法中的低7条，以及你有什么理解", "timeliness": "现行有效", "size": 5,
        })
        self.assertEqual(select_strategy("跟我讲讲刑法中的低7条，以及你有什么理解"), "law_semantic")
        self.assertEqual(select_strategy("格式条款"), "law_keyword")
        self.assertEqual(select_strategy("《刑法》第七条"), "fatiao")

    def test_plain_text_result_still_has_traceable_source(self):
        result = {"content": [{"type": "text", "text": "检索结果原文"}]}
        evidence = normalize_evidence(result, "测试问题", "keyword")
        self.assertEqual(evidence[0]["source_name"], "北大法宝")
        self.assertEqual(evidence[0]["match_type"], "keyword")

    def test_live_result_shape_is_cleaned_for_display(self):
        result = {"structuredContent": {"result": [{
            "title": "中华人民共和国民法典",
            "article_label": "第一千一百六十五条",
            "content": "第一千一百六十五条 行为人因过错侵害他人民事权益造成损害的，应当承担侵权责任。",
            "effect_level": ["民法"],
        }]}}
        item = normalize_evidence(result, "《民法典》第1165条", "exact")[0]
        self.assertEqual(item["text"], "行为人因过错侵害他人民事权益造成损害的，应当承担侵权责任。")
        self.assertEqual(item["effect_level"], "民法")

    def test_articles_from_same_law_have_unique_ids(self):
        result = {"structuredContent": {"result": [
            {"gid": "same-law-id", "title": "中华人民共和国刑法", "article_label": "第十七条", "content": "第一条文内容"},
            {"gid": "same-law-id", "title": "中华人民共和国刑法", "article_label": "第四十九条", "content": "第二条文内容"},
        ]}}
        evidence = normalize_evidence(result, "刑法", "exact")
        self.assertEqual(len({item["article_id"] for item in evidence}), 2)

    def test_keyword_empty_result_falls_back_to_semantic_once(self):
        class FallbackClient:
            configured = True
            services = []

            def list_tools(self, service):
                name = "search_law_articles_by_keyword" if service == "law_keyword" else "search_law_articles"
                return [{"name": name, "inputSchema": {"properties": {"searchKey": {"type": "string"}}, "required": ["searchKey"]}}]

            def call_tool(self, service, tool_name, arguments):
                self.services.append(service)
                if service == "law_keyword":
                    return {"structuredContent": {"result": []}}
                return {"content": [{"type": "text", "text": "语义检索命中的法条原文"}]}

        client = FallbackClient()
        response = PkulawProvider(client=client).search("格式条款")
        self.assertEqual(client.services, ["law_keyword", "law_semantic"])
        self.assertEqual(response["fallback_from"], "law_keyword")
        self.assertEqual(response["evidence"][0]["match_type"], "semantic")

    def test_digit_article_typo_uses_precise_lookup_with_notice(self):
        class PreciseClient:
            configured = True
            service = None
            arguments = None

            def list_tools(self, service):
                self.service = service
                return [{"name": "get_law_article", "inputSchema": {"properties": {"law_title": {"type": "string"}, "article_number": {"type": "string"}}, "required": ["law_title", "article_number"]}}]

            def call_tool(self, service, tool_name, arguments):
                self.arguments = arguments
                return {"structuredContent": {"result": [{"law_title": "中华人民共和国刑法(2023修正)", "article_number": "7", "article_content": "第七条 中华人民共和国公民在中华人民共和国领域外犯本法规定之罪的，适用本法。", "timeliness": "现行有效"}]}}

        client = PreciseClient()
        response = PkulawProvider(client=client).search("跟我讲讲刑法中的低7条，以及你有什么理解")
        self.assertEqual(client.service, "fatiao")
        self.assertEqual(client.arguments, {"law_title": "刑法", "article_number": "7"})
        self.assertIn("第7条", response["notice"])
        self.assertEqual(response["evidence"][0]["article_label"], "第7条")

    def test_explicit_other_law_never_matches_civil_code_article(self):
        from server import STORE
        evidence = STORE.search("《刑法》第三条")
        self.assertEqual(len(evidence), 1)
        self.assertEqual(evidence[0]["document_id"], "criminal-law")
        self.assertEqual(evidence[0]["article_label"], "第三条")
        civil = STORE.search("《民法典》第一千一百六十五条")
        self.assertEqual(civil[0]["article_label"], "第一千一百六十五条")


if __name__ == "__main__":
    unittest.main()
