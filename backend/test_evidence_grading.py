from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from deepseek_client import DEFAULT_SYSTEM_PROMPT, DeepSeekClient  # noqa: E402
from web_search_client import normalize_web_evidence  # noqa: E402


def evidence_for(url: str, title: str, content: str, **search_fields) -> dict:
    search_item = {"title": title, "url": url, "site_name": "测试来源", **search_fields}
    fetched = {"title": title, "url": url, "content": content}
    return normalize_web_evidence(search_item, fetched, "离线分级测试")


class WebEvidenceGradingTest(unittest.TestCase):
    def test_official_speech_transcript_is_primary(self):
        item = evidence_for(
            "https://law.example.edu.cn/speech/1",
            "某教授学术讲座实录",
            "以下为演讲实录。某教授围绕民事行为能力制度展开讲解，并明确说明其论证理由。",
        )
        self.assertEqual(item["evidence_tier"], "primary")
        self.assertEqual(item["evidence_tier_label"], "一手资料")
        self.assertEqual(item["attribution_scope"], "official_record")
        self.assertIn("明确记载", item["source_notice"])

    def test_official_paper_original_is_primary(self):
        item = evidence_for(
            "https://www.court.gov.cn/papers/1",
            "论文原文：数字司法的边界",
            "作者：某研究员。内容摘要：本文讨论数字司法。关键词：数字司法；程序保障。",
        )
        self.assertEqual(item["evidence_tier"], "primary")
        self.assertEqual(item["attribution_scope"], "official_record")

    def test_official_event_preview_does_not_support_specific_speech(self):
        item = evidence_for(
            "https://law.example.edu.cn/events/2",
            "学术讲座预告",
            "某教授将出席本次活动并担任主讲人，活动安排如下。",
            snippet="搜索摘要声称这里有演讲实录和教授原话。",
        )
        self.assertEqual(item["evidence_tier"], "reliable_report")
        self.assertEqual(item["evidence_tier_label"], "官方活动页")
        self.assertEqual(item["attribution_scope"], "official_event")
        self.assertIn("不证明具体发言", item["source_notice"])
        self.assertIn("不得写成教授原话", item["source_notice"])

    def test_official_title_alone_cannot_upgrade_preview_to_primary(self):
        item = evidence_for(
            "https://law.example.edu.cn/events/title-only",
            "某教授讲座实录",
            "本页实际为活动预告，仅列出拟出席人员、时间和活动地点，未刊载任何发言内容。",
        )
        self.assertNotEqual(item["evidence_tier"], "primary")
        self.assertEqual(item["attribution_scope"], "official_event")

    def test_reliable_media_report_is_not_primary_even_with_quotes(self):
        item = evidence_for(
            "https://www.legaldaily.com.cn/legal/3",
            "媒体报道某教授评析案件",
            "报道转述某教授的话说：“这是一段媒体转述中的引文。”本文仅代表作者采访整理。",
        )
        self.assertEqual(item["evidence_tier"], "reliable_report")
        self.assertEqual(item["evidence_tier_label"], "媒体报道")
        self.assertEqual(item["attribution_scope"], "media_report")
        self.assertIn("某媒体报道/转述", item["source_notice"])

    def test_platform_self_media_on_media_domain_is_not_upgraded(self):
        item = evidence_for(
            "https://www.thepaper.cn/newsDetail_forward_1",
            "新书对谈",
            "来源：澎湃新闻·澎湃号·湃客。本文刊载嘉宾对谈内容。",
        )
        self.assertEqual(item["evidence_tier"], "secondary_unverified")
        self.assertEqual(item["evidence_tier_label"], "转述待核")

    def test_self_media_and_163_reposts_are_secondary_unverified(self):
        for url in ("https://dy.163.com/article/4", "https://www.sohu.com/a/5"):
            with self.subTest(url=url):
                item = evidence_for(
                    url,
                    "所谓讲座实录：教授原话曝光",
                    "自媒体文章称：“教授明确认为本案应当这样评价。”文内出现大量引号。",
                )
                self.assertEqual(item["evidence_tier"], "secondary_unverified")
                self.assertEqual(item["evidence_tier_label"], "转述待核")
                self.assertEqual(item["attribution_scope"], "unverified_secondary")
                self.assertIn("不得作为教授直接原话", item["source_notice"])
                self.assertIn("引号", item["source_notice"])

    def test_unknown_or_spoofed_domain_falls_back_to_secondary(self):
        item = evidence_for(
            "https://law.example.edu.cn.content-aggregator.test/page",
            "讲座实录",
            "无法确认的网站刊载所谓演讲实录，并给出确定的案件评价。",
        )
        self.assertEqual(item["evidence_tier"], "secondary_unverified")
        self.assertIn("无法确认来源", item["source_notice"])


class EvidencePromptTest(unittest.TestCase):
    def test_messages_show_tier_and_usage_boundary_for_custom_prompt(self):
        item = evidence_for(
            "https://dy.163.com/article/6",
            "自媒体转述",
            "文章转述某教授观点，但没有可核验的一手实录。",
        )
        messages, evidence_text = DeepSeekClient()._messages(
            "分析该教授观点", [item], "自定义学校观点提示词", plain=True,
        )
        system_prompt = messages[0]["content"]
        self.assertIn("自定义学校观点提示词", system_prompt)
        self.assertIn("身份核验与观点核验分开", system_prompt)
        self.assertIn("只能明确标注未核实，或不采用", system_prompt)
        self.assertIn("活动页、预告只证明", system_prompt)
        self.assertIn("搜索摘要永远不作为原话证据", system_prompt)
        self.assertIn("证据等级：转述待核（secondary_unverified）", evidence_text)
        self.assertIn("使用边界：", evidence_text)
        self.assertIn("不得作为教授直接原话", evidence_text)

    def test_default_prompt_preserves_free_legal_analysis(self):
        self.assertIn("不得因为某句话没有证据编号就删除它", DEFAULT_SYSTEM_PROMPT)
        self.assertIn("不必强行逐句标注", DEFAULT_SYSTEM_PROMPT)
        self.assertIn("某媒体报道/转述", DEFAULT_SYSTEM_PROMPT)
        self.assertIn("不证明具体发言", DEFAULT_SYSTEM_PROMPT)


if __name__ == "__main__":
    unittest.main()
