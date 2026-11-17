import threading
import time
import unittest
from unittest.mock import patch

import server
from retrieval_tasks import network_timeout, parallel_calls, timed_call
from test_conversation import http_server, post
from test_server import StreamingClient


class DeadlineTest(unittest.TestCase):
    def test_timeout_returns_without_waiting_and_keeps_completed_results(self):
        release = threading.Event()
        stopped = threading.Event()

        def slow():
            try:
                release.wait(2)
                return "late"
            finally:
                stopped.set()

        try:
            start = time.monotonic()
            results = parallel_calls([slow, lambda: "ready"], timeout=0.05)
            self.assertLess(time.monotonic() - start, 0.5)
            self.assertIsInstance(results[0], TimeoutError)
            self.assertEqual(results[1], "ready")
            self.assertFalse(release.is_set())
        finally:
            release.set()
            self.assertTrue(stopped.wait(2))

    def test_nested_network_requests_inherit_deadline_and_cannot_restart_after_expiry(self):
        release = threading.Event()
        finished = threading.Event()
        budgets = []
        errors = []

        def slow():
            try:
                budgets.append(network_timeout(45))
                release.wait(2)
                try:
                    network_timeout(45)
                except TimeoutError as error:
                    errors.append(error)
            finally:
                finished.set()

        try:
            result = parallel_calls([lambda: timed_call(slow, 10)], timeout=0.05)[0]
            self.assertIsInstance(result, TimeoutError)
        finally:
            release.set()
            self.assertTrue(finished.wait(2))
        self.assertGreater(budgets[0], 0)
        self.assertLessEqual(budgets[0], 0.05)
        self.assertEqual(len(errors), 1)
        self.assertEqual(network_timeout(45), 45, "deadline must not leak into the next request")

    def test_stream_keeps_fast_web_evidence_when_base_retrieval_times_out(self):
        import json
        release = threading.Event()
        finished = threading.Event()

        def slow(*_args):
            try:
                release.wait(2)
                return [], {}
            finally:
                finished.set()

        evidence = [{"document_title": "公开资料", "article_label": "网页", "text": "事实"}]
        client = StreamingClient("案件分析。【证据1】")
        with http_server() as base, patch.object(server, "RETRIEVAL_TIMEOUT", 0.05), \
             patch.object(server, "DEEPSEEK", client), \
             patch.object(server, "route_question", return_value={"kind": "case"}), \
             patch.object(server, "evidence_by_plan", side_effect=slow), \
             patch.object(server, "case_articles", return_value=[]), \
             patch.object(server, "retrieve_web_evidence", return_value=(evidence, {})):
            try:
                start = time.monotonic()
                body = post(base, "/api/chat/stream", {"question": "案件分析"})
                self.assertLess(time.monotonic() - start, 0.5)
                final = [json.loads(line[5:]) for line in body.splitlines() if line.startswith("data:")][-1]
                self.assertEqual(final["evidence"][0]["document_title"], "公开资料")
                self.assertIn("超时", final["retrieval"]["notice"])
                self.assertEqual(final["answer"], client.answer)
            finally:
                release.set()
                self.assertTrue(finished.wait(2))

    def test_school_endpoint_has_a_total_budget(self):
        import json
        release = threading.Event()
        finished = threading.Event()

        def slow(*_args):
            try:
                release.wait(2)
                return {}
            finally:
                finished.set()

        with http_server() as base, patch.object(server, "SCHOOL_TIMEOUT", 0.05), \
             patch.object(server, "school_payload", side_effect=slow):
            try:
                start = time.monotonic()
                result = json.loads(post(base, "/api/schools", {"question": "问题"}))
                self.assertLess(time.monotonic() - start, 0.5)
                self.assertEqual(result["status"], "source_unavailable")
                self.assertEqual(result["section"], "")
                self.assertFalse(result["verification"]["passed"])
            finally:
                release.set()
                self.assertTrue(finished.wait(2))

    def test_slow_school_does_not_discard_other_schools(self):
        release = threading.Event()
        finished = threading.Event()

        def search(_question, institution, *_args):
            if institution == server.TARGET_INSTITUTIONS[0]:
                try:
                    release.wait(2)
                finally:
                    finished.set()
            return [{"institution": institution}], {"queries": [], "events": []}

        with patch.object(server.WEB, "api_key", "test-only"), \
             patch.object(server, "INSTITUTION_TIMEOUT", 0.05), \
             patch.object(server, "_search_one_institution", side_effect=search):
            try:
                start = time.monotonic()
                evidence, info = server.retrieve_web_evidence(
                    "某案件", force_targeted=True, initial_query="某案件")
                self.assertLess(time.monotonic() - start, 0.5)
                self.assertEqual([item["institution"] for item in evidence], server.TARGET_INSTITUTIONS[1:])
                self.assertFalse(info["institution_results"][0]["found"])
                self.assertIn("超时", info["institution_results"][0]["reason"])
            finally:
                release.set()
                self.assertTrue(finished.wait(2))
