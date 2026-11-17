import unittest
from unittest.mock import patch

import server
from web_search_client import WebSearchError


CASE_QUESTION = "我想了解吴谢宇案件"
CASE_QUERY = "吴谢宇案 判决"
INSTITUTION = "中国政法大学"


def result(url, title="公开资料", snippet="", summary=""):
    return {
        "url": f"https://example.com/{url}",
        "title": title,
        "snippet": snippet,
        "summary": summary,
        "site_name": "公开网页",
        "rerank_score": 0.8,
    }


class FakeWeb:
    configured = True

    def __init__(self, batches, pages=None):
        self.batches = list(batches)
        self.pages = pages or {}
        self.queries = []
        self.fetched = []

    def search(self, query, _question):
        self.queries.append(query)
        index = len(self.queries) - 1
        return self.batches[index] if index < len(self.batches) else []

    def fetch(self, url):
        self.fetched.append(url)
        page = self.pages.get(url)
        if page is None:
            raise WebSearchError("模拟正文不可读")
        return {"url": url, "title": page.get("title", "正文"),
                "content": page["content"]}


class CaseEntityExtractionTest(unittest.TestCase):
    def test_requires_real_name_and_adjacent_institution_teacher_identity(self):
        self.assertEqual(
            server.extract_case_scholar_names("中国政法大学教授罗翔表示，应当核对裁判事实。", INSTITUTION),
            ["罗翔"],
        )
        self.assertEqual(
            server.extract_case_scholar_names("车浩教授（北京大学法学院）就案件发表评论。", "北京大学法学院"),
            ["车浩"],
        )
        self.assertEqual(
            server.extract_case_scholar_names("吴谢宇案件引发关注。教授表示，应等待法院通报。", INSTITUTION),
            [],
        )
        self.assertEqual(server.extract_case_scholar_names("中国政法大学教授出席本次活动。", INSTITUTION), [])

    def test_does_not_cross_speaker_or_book_section_boundaries(self):
        self.assertEqual(server.extract_case_scholar_names(
            "原周刊主编朱伟，中国政法大学社会学院副教授王楠，共同围绕案件展开对谈。", INSTITUTION,
        ), ["王楠"])
        self.assertEqual(server.extract_case_scholar_names(
            "困在二手时间里的“宇神” / 刘云杉（北京大学教育学院教授）", "北京大学法学院",
        ), ["刘云杉"])
        self.assertEqual(server.extract_case_scholar_names(
            "甘阳（清华大学新雅书院教授）评论该案。", "清华大学法学院",
        ), ["甘阳"])

    def test_discovery_uses_search_text_and_fetched_body(self):
        candidates = [(
            result("first", snippet="中国政法大学教授罗翔评论吴谢宇案。"),
            "吴谢宇案 中国政法大学",
        )]
        fetched = [(
            candidates[0][0], candidates[0][1],
            {"title": "访谈", "content": "中国政法大学副教授赵宏也就吴谢宇案接受了访谈。"},
        )]
        entities = server.discover_case_entities(candidates, fetched, INSTITUTION)
        self.assertEqual({entity["name"] for entity in entities}, {"罗翔", "赵宏"})
        sources = {source["source"] for entity in entities for source in entity["sources"]}
        self.assertEqual(sources, {"search_result", "fetched_page"})


class CaseEntitySecondRoundTest(unittest.TestCase):
    def test_first_round_entity_gets_precise_second_round_and_fetched_evidence(self):
        first_url = "https://example.com/first"
        second_url = "https://example.com/second-1"
        web = FakeWeb(
            batches=[
                [result("first", snippet="中国政法大学教授罗翔评论吴谢宇案。")],
                [result("second-1"), result("second-2"), result("second-3")],
            ],
            pages={
                first_url: {
                    "title": "讲座活动报道",
                    "content": "中国政法大学教授罗翔出席讲座并发言，但本页没有讨论具体案件。",
                },
                second_url: {
                    "title": "罗翔访谈",
                    "content": "中国政法大学教授罗翔在访谈中就吴谢宇案表示，应当区分媒体报道与裁判事实。",
                },
                "https://example.com/second-2": {
                    "title": "另一篇报道",
                    "content": "中国政法大学教授罗翔评论吴谢宇案，并介绍了公开裁判资料。",
                },
                "https://example.com/second-3": {
                    "title": "超出抓取上限",
                    "content": "中国政法大学教授罗翔评论吴谢宇案。",
                },
            },
        )
        with patch.object(server, "WEB", web):
            evidence, info = server._search_one_institution(
                CASE_QUESTION, INSTITUTION, CASE_QUERY, candidate_limit=3,
            )

        self.assertEqual(info["search_count"], 2)
        self.assertEqual(web.queries, [
            "吴谢宇案 吴谢宇 评论 中国政法大学",
            "罗翔 吴谢宇案 判决 评论 访谈 中国政法大学",
        ])
        self.assertEqual(info["entities"], ["罗翔"])
        self.assertTrue(info["second_round"])
        self.assertEqual(info["second_round_entity"], "罗翔")
        self.assertEqual(len(evidence), 1)
        self.assertEqual(evidence[0]["source_url"], second_url)
        self.assertEqual(evidence[0]["entity"], "罗翔")
        self.assertEqual(evidence[0]["retrieval_round"], "second")
        self.assertEqual(evidence[0]["query_source"], "first_round_entity")
        self.assertEqual(evidence[0]["relevance_basis"], "case_topic_and_opinion_text")
        self.assertNotIn("一手原话", evidence[0]["source_notice"])
        # 每轮按传入的候选上限抓取，全部重试仍受累计预算限制。
        self.assertEqual(len([url for url in web.fetched if "/second-" in url]), 3)
        self.assertLessEqual(info["fetch_attempt_count"], server.MAX_SCHOOL_FETCHES)
        second_events = [event for event in info["events"] if event.get("round") == "second"]
        self.assertTrue(second_events)
        self.assertTrue(all(event.get("entity") == "罗翔" for event in second_events))
        self.assertTrue(any(event["stage"] == "second_round" and event.get("entity_sources")
                            for event in second_events))

    def test_no_reliable_entity_still_uses_bounded_topic_retries(self):
        first_url = "https://example.com/no-entity"
        web = FakeWeb(
            batches=[[result(
                "no-entity", title="吴谢宇案件通报",
                snippet="被告人吴谢宇的案件引发关注，教授泛称不构成教师姓名。",
            )]],
            pages={first_url: {
                "title": "案件通报",
                "content": "吴谢宇案件的公开通报没有提及任何目标机构教师。",
            }},
        )
        with patch.object(server, "WEB", web):
            evidence, info = server._search_one_institution(
                CASE_QUESTION, INSTITUTION, CASE_QUERY, candidate_limit=3,
            )
        self.assertEqual(evidence, [])
        self.assertEqual(info["search_count"], 3)
        self.assertEqual(len(web.queries), 3)
        self.assertEqual(info["entities"], [])
        self.assertTrue(info["second_round"])
        self.assertTrue(any(event["stage"] == "retry" for event in info["events"]))

    def test_search_summary_is_not_evidence_when_second_fetch_fails(self):
        first_url = "https://example.com/discovery"
        web = FakeWeb(
            batches=[
                [result("discovery", summary="中国政法大学教授罗翔评论吴谢宇案。")],
                [result("unreadable", snippet="罗翔就吴谢宇案给出完整观点。")],
            ],
            pages={first_url: {
                "title": "个人简介",
                "content": "罗翔是中国政法大学教授、博士生导师。本页仅介绍其工作经历。",
            }},
        )
        with patch.object(server, "WEB", web):
            evidence, info = server._search_one_institution(
                CASE_QUESTION, INSTITUTION, CASE_QUERY, candidate_limit=3,
            )
        self.assertEqual(info["search_count"], 3)
        self.assertEqual(evidence, [])
        self.assertEqual(info["fetched_count"] if "fetched_count" in info else 0, 0)
        self.assertTrue(any(event["stage"] == "fetch" and not event["ok"]
                            and event.get("round") == "second" for event in info["events"]))

    def test_second_round_requires_identity_and_opinion_relevance_separately(self):
        first_url = "https://example.com/identity-source"
        second_url = "https://example.com/wrong-identity"
        web = FakeWeb(
            batches=[
                [result("identity-source", snippet="中国政法大学教授罗翔评论吴谢宇案。")],
                [result("wrong-identity")],
            ],
            pages={
                first_url: {
                    "title": "个人简介",
                    "content": "罗翔是中国政法大学教授、博士生导师。本页仅介绍其工作经历。",
                },
                second_url: {
                    "title": "访谈",
                    "content": "罗翔在访谈中就吴谢宇案表示，应当核对证据，但全文未说明其目标机构教师身份。",
                },
            },
        )
        with patch.object(server, "WEB", web):
            evidence, info = server._search_one_institution(
                CASE_QUESTION, INSTITUTION, CASE_QUERY, candidate_limit=3,
            )
        self.assertEqual(evidence, [])
        self.assertEqual(info["search_count"], 3)
        rejection = next(event for event in info["events"]
                         if event.get("url") == second_url and event["stage"] == "filter")
        self.assertIn("目标机构教师关联", rejection["reason"])

    def test_first_round_entity_with_usable_evidence_does_not_search_again(self):
        url = "https://example.com/first-hit"
        web = FakeWeb(
            batches=[[result("first-hit")]],
            pages={url: {
                "title": "案件访谈",
                "content": "中国政法大学教授罗翔在访谈中就吴谢宇案表示，应当区分媒体报道与裁判事实。",
            }},
        )
        with patch.object(server, "WEB", web):
            evidence, info = server._search_one_institution(
                CASE_QUESTION, INSTITUTION, CASE_QUERY, candidate_limit=3,
            )
        self.assertEqual(len(evidence), 1)
        self.assertEqual(evidence[0]["entities"], ["罗翔"])
        self.assertEqual(info["search_count"], 1)
        self.assertFalse(info["second_round"])
        self.assertTrue(any(event["stage"] == "second_round" and event.get("skipped")
                            and "停止扩检" in event["reason"] for event in info["events"]))

    def test_non_case_biography_uses_topic_retries_without_inventing_opinion(self):
        url = "https://example.com/statute"
        web = FakeWeb(
            batches=[[result("statute", snippet="中国政法大学教授罗翔评论居住权。")]],
            pages={url: {
                "title": "个人简介",
                "content": "罗翔是中国政法大学教授、博士生导师。本页仅介绍其工作经历。",
            }},
        )
        with patch.object(server, "WEB", web):
            _evidence, info = server._search_one_institution(
                "民法典第368条，需要高校老师论文", INSTITUTION,
                "民法典 第368条 居住权", candidate_limit=8,
            )
        self.assertFalse(info["case_mode"])
        self.assertEqual(info["search_count"], 3)
        self.assertEqual(len(web.queries), 3)
        self.assertTrue(info["second_round"])
        self.assertFalse(any(event["stage"] in {"entity", "second_round"} for event in info["events"]))


if __name__ == "__main__":
    unittest.main()
