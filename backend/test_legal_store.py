import unittest

from deepseek_client import verify_answer
from legal_store import LegalStore, chinese_number_to_int


class LegalStoreTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.store = LegalStore()

    def test_official_document_is_complete(self):
        self.assertEqual(len(self.store.documents), 1)
        self.assertEqual(self.store.documents[0]["article_count"], 1260)
        self.assertEqual(self.store.documents[0]["status"], "有效")
        self.assertEqual(len(self.store.articles), 1260)

    def test_chinese_article_numbers(self):
        self.assertEqual(chinese_number_to_int("一千一百六十五"), 1165)
        self.assertEqual(chinese_number_to_int("一千零七十七"), 1077)
        self.assertEqual(chinese_number_to_int("十"), 10)

    def test_exact_article_lookup(self):
        result = self.store.search("请给出民法典第1165条原文")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["article_label"], "第一千一百六十五条")
        self.assertIn("行为人因过错侵害他人民事权益造成损害", result[0]["text"])
        self.assertEqual(result[0]["match_type"], "exact")

    def test_multiple_exact_articles(self):
        result = self.store.search("对比第一千一百六十五条与第一千一百六十六条")
        self.assertEqual([item["article_label"] for item in result], ["第一千一百六十五条", "第一千一百六十六条"])

    def test_natural_language_retrieval(self):
        result = self.store.search("提交离婚申请后三十日内反悔，可以撤回吗？")
        self.assertTrue(result)
        self.assertEqual(result[0]["article_label"], "第一千零七十七条")

    def test_irrelevant_question_abstains(self):
        result = self.store.search("量子计算机芯片的低温制造工艺是什么")
        self.assertEqual(result, [])

    def test_answer_verifier_rejects_unknown_citation(self):
        evidence = self.store.search("第1165条")
        answer = "行为人因过错造成损害的，应承担侵权责任，这是一般过错责任原则。【证据2】"
        self.assertFalse(verify_answer(answer, evidence)["passed"])

    def test_answer_verifier_accepts_supported_answer(self):
        evidence = self.store.search("第1165条")
        answer = "根据所引条文，行为人因过错侵害他人民事权益并造成损害时，应当承担侵权责任。【证据1】"
        self.assertTrue(verify_answer(answer, evidence)["passed"])

    def test_answer_verifier_rejects_external_article(self):
        evidence = self.store.search("第1165条")
        answer = "第1165条规定一般过错责任，而第1077条另外规定了离婚冷静期。【证据1】"
        self.assertFalse(verify_answer(answer, evidence)["passed"])


if __name__ == "__main__":
    unittest.main()
