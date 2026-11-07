import unittest
from unittest.mock import patch

import server


EVIDENCE = [{
    "document_title": "中华人民共和国民法典",
    "article_label": "第一千一百六十五条",
    "text": "行为人因过错侵害他人民事权益造成损害的，应当承担侵权责任。",
}]
SUPPORTED_ANSWER = "根据所引条文，行为人因过错侵害他人民事权益并造成损害时，应当承担侵权责任。【证据1】"
UNSUPPORTED_ANSWER = "这是一段没有标注证据的实质性法律回答，其长度已经超过十二个字。"


class FakeDeepSeek:
    configured = True

    def __init__(self, answers):
        self.answers = iter(answers)
        self.calls = []

    def generate(self, question, evidence, correction_notes=None, previous_answer=None):
        self.calls.append({
            "question": question,
            "evidence": evidence,
            "correction_notes": correction_notes,
            "previous_answer": previous_answer,
        })
        return next(self.answers)


class RetrievalRoutingTest(unittest.TestCase):
    def test_criminal_sentencing_question_does_not_use_civil_code(self):
        with patch.object(server.STORE, "search", return_value=[]) as search:
            evidence, retrieval = server.retrieve_evidence(
                "未成年人，并且偷盗，强奸，大概会怎么判刑", source="local"
            )
        self.assertEqual(evidence, [])
        self.assertEqual(retrieval["source"], "local_official")
        search.assert_not_called()

    def test_mixed_civil_and_criminal_question_keeps_local_search(self):
        with patch.object(server.STORE, "search", return_value=[]) as search:
            server.retrieve_evidence("民事责任和刑事责任的区别", source="local")
        search.assert_called_once()

    def test_composite_sentencing_question_retrieves_all_core_issues(self):
        queries = server.criminal_core_queries("未成年人，并且偷盗，强奸，大概会怎么判刑")
        self.assertEqual(queries, [
            "《刑法》第十七条",
            "《刑法》第四十九条",
            "《刑法》第二百六十四条",
            "《刑法》第二百三十六条",
            "《刑法》第六十九条",
        ])


class AnswerRetryTest(unittest.TestCase):
    def test_failed_answer_is_regenerated_once_with_failure_details(self):
        client = FakeDeepSeek([UNSUPPORTED_ANSWER, SUPPORTED_ANSWER])
        with patch.object(server, "DEEPSEEK", client):
            result = server.response_payload("请解释第1165条", EVIDENCE)
        self.assertEqual(result["status"], "verified")
        self.assertEqual(result["answer"], SUPPORTED_ANSWER)
        self.assertEqual(result["verification"]["attempts"], 2)
        self.assertEqual(len(client.calls), 2)
        self.assertIsNone(client.calls[0]["correction_notes"])
        self.assertEqual(client.calls[1]["previous_answer"], UNSUPPORTED_ANSWER)
        self.assertTrue(any(
            "法律陈述有证据" in note for note in client.calls[1]["correction_notes"]
        ))
        self.assertTrue(any(
            UNSUPPORTED_ANSWER in note for note in client.calls[1]["correction_notes"]
        ))
        self.assertNotIn("_uncovered_segments", result["verification"])

    def test_second_failure_is_blocked_with_specific_reasons(self):
        client = FakeDeepSeek([UNSUPPORTED_ANSWER, UNSUPPORTED_ANSWER])
        with patch.object(server, "DEEPSEEK", client):
            result = server.response_payload("请解释第1165条", EVIDENCE)
        self.assertEqual(result["status"], "verification_failed")
        self.assertEqual(len(client.calls), 2)
        self.assertEqual(result["verification"]["attempts"], 2)
        self.assertIn("引用编号有效", result["answer"])
        self.assertIn("法律陈述有证据", result["answer"])
        self.assertNotIn(UNSUPPORTED_ANSWER, result["answer"])


if __name__ == "__main__":
    unittest.main()
