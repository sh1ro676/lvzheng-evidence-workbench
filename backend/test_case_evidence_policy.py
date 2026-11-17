import unittest

import server
from deepseek_client import verify_answer


class CaseEvidencePolicyTest(unittest.TestCase):
    def test_named_case_without_common_case_suffix_enters_case_mode(self):
        self.assertTrue(server.CASE_QUERY_RE.search("清华大学教授怎么看吴谢宇案"))
        self.assertFalse(server.CASE_QUERY_RE.search("请解释这个答案"))

    def test_guest_list_does_not_turn_authors_or_departments_into_scholars(self):
        text = (
            "嘉宾：吴琪（《三联生活周刊》副主编）、王珊（《三联生活周刊》主笔）、"
            "甘阳（清华大学新雅书院讲席教授）、梁鸿（作家、中国人民大学文学院教授）"
        )
        self.assertEqual(server.extract_case_scholar_names(text, "清华大学法学院"), ["甘阳"])
        self.assertEqual(
            server.extract_case_scholar_names("中国政法大学社会学副教授王楠，围绕案件发言。", "中国政法大学"),
            ["王楠"],
        )

    def test_generic_education_page_is_not_case_opinion_evidence(self):
        reason = server.case_opinion_relevance_error(
            {},
            {
                "title": "罗翔教育访谈",
                "content": "中国政法大学教授罗翔在访谈中一般性讨论教育反思，没有提及具体案件。",
            },
            "吴谢宇案 教育 反思",
            "罗翔",
        )
        self.assertIn("案件核心词", reason)

    def test_school_prompt_separates_activity_identity_and_opinion(self):
        for phrase in ("活动", "具体发言", "转述待核", "直接引语", "其他院校及其教师不得另起段落"):
            self.assertIn(phrase, server.SCHOOL_SECTION_PROMPT)

    def test_unverified_secondary_source_cannot_support_professor_quote(self):
        quote = "智商顶尖，人伦全丢，这是半人的极端体现。"
        evidence = [{
            "article_label": "网页全文",
            "document_title": "自媒体案件评论",
            "text": f"文章转述彭林教授称“{quote}”",
            "provider": "deepseek_web",
            "evidence_tier": "secondary_unverified",
        }]
        answer = f"彭林教授评价吴谢宇案称“{quote}”【证据1】"
        result = verify_answer(answer, evidence)
        self.assertFalse(result["passed"])
        self.assertTrue(any(
            not check["passed"] and "直接引语" in check["name"]
            for check in result["checks"]
        ), result)

    def test_primary_transcript_can_support_exact_quote(self):
        quote = "智商顶尖，人伦全丢，这是半人的极端体现。"
        evidence = [{
            "article_label": "网页全文",
            "document_title": "演讲实录",
            "text": f"彭林在演讲中说：“{quote}”",
            "provider": "deepseek_web",
            "evidence_tier": "primary",
        }]
        answer = f"彭林在演讲中说：“{quote}”【证据1】"
        result = verify_answer(answer, evidence)
        self.assertTrue(result["passed"], result)

    def test_legacy_evidence_without_tier_keeps_existing_quote_check(self):
        quote = "这是一段能够在旧格式证据正文中逐字找到的完整引语。"
        evidence = [{
            "article_label": "网页全文",
            "document_title": "旧格式资料",
            "text": f"资料记载：“{quote}”",
            "provider": "deepseek_web",
        }]
        answer = f"资料记载：“{quote}”【证据1】"
        self.assertTrue(verify_answer(answer, evidence)["passed"])


if __name__ == "__main__":
    unittest.main()
