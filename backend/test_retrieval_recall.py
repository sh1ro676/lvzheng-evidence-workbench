import unittest
from unittest.mock import patch

import server
from deepseek_client import DeepSeekClient
from test_case_entity_retrieval import FakeWeb, result

SCHOOL = "中国政法大学"
QUESTION = "请找四校教授对《民法典》第22条的观点"
QUERY = "成年监护 行为能力宣告 中国政法大学"


def page(title, content, url="https://example.com/opinion"):
    return {"title": title, "content": content, "url": url}


class RetrievalRecallTest(unittest.TestCase):
    def test_topic_match_does_not_depend_on_user_saying_paper(self):
        text = page("成年监护制度研究", "摘要：本文讨论民法典的成年监护制度与行为能力宣告。作者单位：中国政法大学。")
        self.assertIsNone(server.page_relevance_error(QUESTION, {}, text, QUERY))
        self.assertIsNone(server.page_relevance_error(QUESTION + "相关论文", {}, text, QUERY))

    def test_short_legal_terms_remain_usable_but_generic_terms_do_not(self):
        terms = server.topic_query_terms(QUESTION, "监护 代理 合同 论文 教授 制度 中国政法大学")
        self.assertEqual(terms, ["监护", "代理", "合同"])
        text = page("监护的适用", "本文阐释监护与代理的边界。")
        self.assertIsNone(server.page_relevance_error(QUESTION, {}, text, "监护 代理 中国政法大学"))

    def test_topic_webpage_is_not_mislabeled_as_paper_or_direct_article(self):
        text = page("成年监护规则的解释", "中国政法大学教授王五认为，应区分成年监护和行为能力宣告。")
        evidence, reason = server.page_evidence({}, text, QUERY, QUESTION, SCHOOL)
        self.assertIsNone(reason)
        self.assertEqual(evidence["relevance_basis"], "topic_related")
        self.assertIn("不能作为逐条释义", evidence["source_notice"])

    def test_wrong_law_and_unrelated_papers_are_still_rejected(self):
        wrong = page("刑法第22条", "中国政法大学教授讨论《刑法》第22条犯罪预备。")
        self.assertIn("所问法律", server.page_relevance_error(QUESTION, {}, wrong, QUERY))
        unrelated = page("合同效力研究", "摘要：本文分析民法典中的合同效力及违约责任，作者单位：中国政法大学。")
        self.assertIn("主题相关性", server.page_relevance_error(QUESTION, {}, unrelated, QUERY))
        self.assertIn("论文", server.page_relevance_error("请找监护论文", {}, page("课程", "监护课程预告"), "监护"))

    def test_search_title_cannot_supply_missing_article_or_law_in_fetched_body(self):
        item = {"title": "民法典第22条成年监护"}
        text = page("刑法第22条", "本页讨论刑法第22条犯罪预备。")
        self.assertIsNotNone(server.page_relevance_error(QUESTION, item, text, QUERY))

    def test_topic_page_about_another_law_is_not_labeled_as_the_requested_article(self):
        text = page("成年监护的比较", "中国政法大学教授王五认为，另一法律第22条的成年监护与行为能力宣告值得比较。")
        evidence, reason = server.page_evidence({}, text, QUERY, QUESTION, SCHOOL)
        self.assertIsNone(reason)
        self.assertEqual(evidence["relevance_basis"], "topic_related")

    def test_failed_school_executes_second_topic_query_then_stops_on_hit(self):
        web = FakeWeb([[], [result("paper")]], {
            "https://example.com/paper": page("成年监护制度研究", "摘要：作者单位：中国政法大学。本文分析民法典的成年监护与行为能力宣告。"),
        })
        planned = ["成年监护 论文 中国政法大学", "行为能力宣告 期刊 中国政法大学", "监护 学报 中国政法大学"]
        with patch.object(server, "WEB", web):
            evidence, info = server._search_one_institution(QUESTION, SCHOOL, planned[0], planned_queries=planned)
        self.assertEqual(len(evidence), 1)
        self.assertEqual(info["search_count"], 2)
        self.assertIn("行为能力宣告", web.queries[1])
        self.assertTrue(any(event["stage"] == "retry" for event in info["events"]))

    def test_case_without_initial_scholar_still_expands(self):
        web = FakeWeb([[], [result("interview")]], {
            "https://example.com/interview": page("罗翔访谈", "中国政法大学教授罗翔认为，应区分吴谢宇案的公开报道与司法认定。"),
        })
        with patch.object(server, "WEB", web):
            evidence, info = server._search_one_institution("请分析吴谢宇案", SCHOOL, "吴谢宇案")
        self.assertEqual(len(evidence), 1)
        self.assertEqual(info["search_count"], 2)
        self.assertIn("访谈", web.queries[1])
        self.assertNotIn("新书", web.queries[0])

    def test_case_alias_is_preserved_and_short_name_can_match(self):
        core = server.case_core_text("北大杀母案", "北大杀母案 吴谢宇案")
        self.assertIn("北大杀母案", core)
        self.assertIn("吴谢宇", server.case_relevance_terms(core))
        self.assertIsNone(server.case_opinion_relevance_error({}, page("访谈", "罗翔认为，吴谢宇的行为需要结合裁判认定分析。"), "吴谢宇案"))

    def test_school_replacement_handles_adjacent_titles_and_keeps_the_target_when_truncated(self):
        query = server.school_query("中国政法大学教授 成年监护 论文", "北京大学法学院")
        self.assertNotIn("中国政法大学", query)
        self.assertIn("成年监护", query)
        long = server.school_query("监护" * 200, "北京大学法学院")
        self.assertLessEqual(len(long), 300)
        self.assertTrue(long.endswith("北京大学法学院"))

    def test_identity_and_opinion_can_be_on_different_pages(self):
        opinion_url = "https://example.com/opinion"
        profile_url = "https://law.example.edu.cn/profile"
        web = FakeWeb([
            [result("opinion")],
            [{"url": profile_url, "title": "罗翔教师简介"}],
        ], {
            opinion_url: page("罗翔：成年监护制度研究", "摘要：罗翔认为，民法典的成年监护与行为能力宣告需要分别分析。"),
            profile_url: page("罗翔教师简介", "罗翔是中国政法大学教授。此页仅介绍教师任职与教育经历。", profile_url),
        })
        with patch.object(server, "WEB", web):
            evidence, info = server._search_one_institution(QUESTION, SCHOOL, QUERY)
        self.assertEqual(info["search_count"], 2)
        self.assertIn("教师简介", web.queries[1])
        self.assertEqual(len(evidence), 1)
        self.assertEqual(evidence[0]["source_url"], opinion_url)
        self.assertEqual(evidence[0]["identity_sources"][0]["source_url"], profile_url)
        self.assertEqual(evidence[0]["evidence_tier"], "secondary_unverified")
        self.assertNotEqual(evidence[0]["text"], web.pages[profile_url]["content"])
        self.assertFalse(info.get("research_leads"), "verified opinions should appear only as evidence")

    def test_unverified_relevant_fetched_page_is_a_lead_not_a_school_hit(self):
        url = "https://example.com/opinion"
        web = FakeWeb([[result("opinion"), result("wrong-law"), result("unreadable")]], {
            url: page("成年监护评论", "正文讨论成年监护与行为能力宣告。"),
            "https://example.com/wrong-law": page("刑法第22条", "该条讨论犯罪预备。"),
        })
        with patch.object(server, "WEB", web):
            evidence, local = server._search_one_institution(QUESTION, SCHOOL, QUERY)
        info = dict(queries=[], events=[], institution_results=[], search_count=0, candidate_count=0,
                    fetch_attempt_count=0, content_fetched_count=0, rejected_count=0)
        server._merge_institution_info(info, SCHOOL, evidence, local)
        self.assertEqual(evidence, [])
        self.assertFalse(info["institution_results"][0]["found"])
        self.assertEqual(info["research_leads"], [{
            "title": "成年监护评论", "source_url": url, "excerpt": "正文讨论成年监护与行为能力宣告。",
            "search_institution": SCHOOL,
        }])

    def test_identity_later_in_same_batch_can_validate_earlier_opinion(self):
        profile_url = "https://law.example.edu.cn/profile"
        pages = {
            "https://example.com/opinion": page("罗翔：成年监护制度研究", "摘要：罗翔认为，民法典中的成年监护应结合行为能力宣告理解。"),
            profile_url: page("罗翔教师简介", "中国政法大学教授罗翔，研究方向为法律。", profile_url),
        }
        web = FakeWeb([[result("opinion"), {"url": profile_url, "title": "罗翔简介"}]], pages)
        with patch.object(server, "WEB", web):
            evidence, info = server._search_one_institution(QUESTION, SCHOOL, QUERY)
        self.assertEqual(info["search_count"], 1)
        self.assertEqual(evidence[0]["identity_sources"][0]["name"], "罗翔")

    def test_search_snippet_and_non_university_profile_cannot_prove_identity(self):
        opinion = page("罗翔：成年监护制度研究", "摘要：罗翔认为成年监护应结合行为能力宣告理解。")
        identities = {}
        fake_profile = page("罗翔简介", "中国政法大学教授罗翔。", "https://example.com/profile")
        server.remember_identities([({}, "", fake_profile)], SCHOOL, identities)
        self.assertEqual(identities, {})
        evidence, reason = server.page_evidence({"snippet": "中国政法大学教授罗翔"}, opinion, QUERY, QUESTION, SCHOOL, identities=identities)
        self.assertIsNone(evidence)
        self.assertIn("身份正文", reason)

    def test_official_profile_heading_can_link_a_separate_affiliation_line(self):
        identities = {}
        profile = page("罗翔教师简介", "任职信息\n中国政法大学教授，主要从事刑法学研究。", "https://faculty.example.edu.cn/profile")
        server.remember_identities([({}, "", profile)], SCHOOL, identities)
        self.assertIn("罗翔", identities)
        opinion = page("罗翔：成年监护研究", "摘要：罗翔认为，成年监护应结合行为能力宣告分析。")
        evidence, reason = server.page_evidence({}, opinion, QUERY, QUESTION, SCHOOL, identities=identities)
        self.assertIsNone(reason)
        self.assertEqual(evidence["identity_sources"][0]["source_url"], profile["url"])

    def test_matching_name_does_not_override_another_school_or_bare_mention(self):
        identities = {"罗翔": [{"name": "罗翔", "institution": SCHOOL, "source_url": "https://example.edu.cn/profile"}]}
        other = page("罗翔：成年监护制度", "北京大学法学院教授罗翔认为成年监护需要研究。")
        evidence, reason = server.page_evidence({}, other, QUERY, QUESTION, SCHOOL, identities=identities)
        self.assertIsNone(evidence)
        self.assertIn("其他目标院校", reason)
        mention = page("成年监护", "本文只是提到罗翔的名字，并没有把本文观点归属于他。")
        self.assertIsNone(server.page_evidence({}, mention, QUERY, QUESTION, SCHOOL, identities=identities)[0])

    def test_search_and_fetch_budgets_include_identity_lookups_and_retries(self):
        batches = [[result(f"{r}-{i}") for i in range(8)] for r in range(3)]
        pages = {item["url"]: page("无关网页", "没有机构或观点正文") for batch in batches for item in batch}
        web = FakeWeb(batches, pages)
        with patch.object(server, "WEB", web):
            evidence, info = server._search_one_institution(QUESTION, SCHOOL, QUERY)
        self.assertEqual(evidence, [])
        self.assertLessEqual(len(web.queries), 3)
        self.assertEqual(len(web.fetched), 10)
        self.assertEqual(len(set(web.fetched)), 10)
        self.assertIn("抓取预算", info["stop_reason"])

    def test_model_receives_identity_source_as_identity_only(self):
        evidence = {"document_title": "观点", "article_label": "网页", "source_name": "媒体",
                    "text": "观点正文", "provider": "deepseek_web", "identity_sources": [{
                        "name": "罗翔", "institution": SCHOOL, "source_url": "https://example.edu.cn/profile",
                        "excerpt": "中国政法大学教授罗翔。",
                    }]}
        _messages, rendered = DeepSeekClient()._messages("问题", [evidence], None)
        self.assertIn("独立身份核验", rendered)
        self.assertIn("不证明观点", rendered)

    def test_targeted_planner_and_fallback_queries_are_wired_into_all_four_schools(self):
        class Planner:
            calls = []

            def plan_web_queries(self, _question, institution=None):
                self.calls.append(institution)
                return [f"成年监护 论文 {institution}", f"行为能力宣告 期刊 {institution}", f"监护 学报 {institution}"]

        class Web:
            configured = True

            def __init__(self):
                self.queries = []

            def search(self, query, _question):
                self.queries.append(query)
                if "行为能力宣告" not in query:
                    return []
                school = next(name for name in server.TARGET_INSTITUTIONS if name in query)
                return [{"url": f"https://example.com/{server.TARGET_INSTITUTIONS.index(school)}", "title": "行为能力宣告研究"}]

            def fetch(self, url):
                school = server.TARGET_INSTITUTIONS[int(url.rsplit("/", 1)[1])]
                return page("行为能力宣告研究", f"摘要：作者单位：{school}。本文研究民法典的成年监护与行为能力宣告。", url)

        planner, web = Planner(), Web()
        with patch.object(server, "DEEPSEEK", planner), patch.object(server, "WEB", web):
            evidence, info = server.retrieve_web_evidence(QUESTION, force_targeted=True)
        self.assertEqual(planner.calls, [server.TARGET_INSTITUTIONS[0]])
        self.assertEqual(len(evidence), 4)
        self.assertEqual([result["search_count"] for result in info["institution_results"]], [2, 2, 2, 2])
        self.assertFalse(info["fallback_general"])
        for query in web.queries:
            self.assertEqual(sum(name in query for name in server.TARGET_INSTITUTIONS), 1)
