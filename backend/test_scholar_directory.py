import os
import unittest
from unittest.mock import patch

import server
from test_case_entity_retrieval import FakeWeb, result


SCHOOL = "清华大学法学院"
QUESTION = "请找刑法第20条正当防卫的学者观点"
PLANS = ["刑法 第20条 正当防卫 防卫限度", "正当防卫 防卫过当", "防卫限度 讲评"]


class ScholarDirectoryTest(unittest.TestCase):
    def search(self, mode, title, content, snippet=""):
        path = "https://example.com/defense"

        class Web(FakeWeb):
            def search(self, query, _question):
                self.queries.append(query)
                return [result("defense", snippet=snippet)] if query.startswith("张明楷 ") else []

        web = Web([], {path: {"title": title, "content": content}})
        with patch.dict(os.environ, {"LVTU_CRIMINAL_DIRECTORY": mode}), patch.object(server, "WEB", web):
            evidence, info = server._search_one_institution(QUESTION, SCHOOL, PLANS[0], planned_queries=PLANS)
        return evidence, info, web

    def test_paired_search_uses_same_budget_and_recovers_a_signed_work_without_school_in_body(self):
        title = "德国刑法中的正当防卫"
        content = "作者：张明楷。用于测试的正文讨论正当防卫与防卫限度。"
        old, before, old_web = self.search("0", title, content)
        new, after, new_web = self.search("1", title, content)
        self.assertEqual(old, [])
        self.assertEqual(before["authors_found"], [])
        self.assertEqual(old_web.queries[0], new_web.queries[0])
        self.assertLessEqual(after["search_count"], before["search_count"])
        self.assertLessEqual(len(new_web.fetched), server.MAX_SCHOOL_FETCHES)
        self.assertEqual(after["authors_found"], ["张明楷"])
        self.assertEqual(after["authors_verified"], ["张明楷"])
        self.assertEqual(new[0]["entity"], "张明楷")
        self.assertEqual(new[0]["query_source"], "directory_author")
        self.assertEqual(new[0]["institution"], SCHOOL)
        self.assertEqual(new[0]["identity_sources"][0]["source_url"], "https://www.law.tsinghua.edu.cn/info/1145/9585.htm")
        self.assertEqual(new[0]["relevance_basis"], "topic_related")

    def test_name_or_search_snippet_alone_cannot_verify_same_name(self):
        evidence, info, _web = self.search(
            "1", "正当防卫随笔", "作者：张明楷。正文讨论正当防卫和防卫限度。",
            snippet="清华大学法学院教授张明楷",
        )
        self.assertEqual(evidence, [])
        self.assertEqual(info["authors_found"], ["张明楷"])
        self.assertEqual(info["authors_verified"], [])
        self.assertEqual(len(info["research_leads"]), 1)

    def test_first_round_signed_work_can_reuse_the_directory_without_another_search(self):
        url = "https://example.com/defense"
        web = FakeWeb([[result("defense")]], {url: {
            "title": "德国刑法中的正当防卫",
            "content": "作者：张明楷。用于测试的正文讨论正当防卫与防卫限度。",
        }})
        with patch.dict(os.environ, {"LVTU_CRIMINAL_DIRECTORY": "1"}), patch.object(server, "WEB", web):
            evidence, info = server._search_one_institution(QUESTION, SCHOOL, PLANS[0], planned_queries=PLANS)
        self.assertEqual(info["search_count"], 1)
        self.assertEqual(web.fetched, [url])
        self.assertEqual(evidence[0]["identity_sources"][0]["name"], "张明楷")

    def test_matching_title_without_byline_cannot_pass(self):
        evidence, info, _web = self.search(
            "1", "德国刑法中的正当防卫", "本文仅提到张明楷的名字。讨论正当防卫与防卫限度。",
        )
        self.assertEqual(evidence, [])
        self.assertEqual(info["authors_verified"], [])

    def test_same_byline_with_conflicting_institution_cannot_pass(self):
        evidence, info, _web = self.search(
            "1", "德国刑法中的正当防卫", "作者：张明楷。北京大学法学院教授张明楷认为要讨论正当防卫。",
        )
        self.assertEqual(evidence, [])
        self.assertFalse(info["research_leads"])

    def test_civil_topic_does_not_trigger_criminal_directory(self):
        with patch.dict(os.environ, {"LVTU_CRIMINAL_DIRECTORY": "1"}):
            self.assertIsNone(server.directory_search("民法侵权的因果关系", SCHOOL, "", ["侵权 因果关系"], False))
