import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
import urllib.error
import urllib.parse
import urllib.request
from unittest.mock import patch

import server
from book_index import Chunk, save
from test_conversation import http_server, post


def get(url):
    with urllib.request.urlopen(url, timeout=3) as response:
        return json.load(response)


class ExpertLibraryTest(unittest.TestCase):
    def test_catalog_and_search_use_imported_textbook_indexes_and_preserve_page_locations(self):
        with TemporaryDirectory() as directory, patch.object(server, "ROOT", Path(directory)), \
             patch.object(server, "_BOOK_CACHE", None), patch.object(server, "PKULAW") as provider, http_server() as base:
            books = Path(directory) / "data" / "books"
            save([Chunk("刑法学（第六版）· 上册", 418, "正当防卫 测试上册原文", 397)], books / "upper.jsonl")
            save([Chunk("刑法学（第六版）· 下册", 15, "正当防卫 测试下册原文")], books / "lower.jsonl")
            (books / "bad.jsonl").write_text("{}", encoding="utf-8")
            catalog = get(base + "/api/expert-library/books")["books"]
            self.assertEqual([item["title"] for item in catalog], ["刑法学（第六版）· 上册", "刑法学（第六版）· 下册"])
            indexed = {item["title"]: item for item in catalog}
            upper = indexed["刑法学（第六版）· 上册"]
            self.assertEqual(upper["page_count"], 1)
            self.assertIn("张明楷", upper["source_name"])
            for query in ("正当防卫", "书内 397", "PDF p418"):
                params = urllib.parse.urlencode({"book_id": upper["book_id"], "q": query})
                evidence = get(f"{base}/api/expert-library/search?{params}")["evidence"]
                self.assertEqual([item["text"] for item in evidence], ["正当防卫 测试上册原文"])
                self.assertEqual(evidence[0]["provider"], "textbook")
                self.assertEqual(evidence[0]["article_label"], "书内 p397（PDF p418）")
            provider.search.assert_not_called()

    def test_empty_catalog_and_retired_local_statute_endpoints(self):
        with patch.object(server, "_book_index", return_value=None), \
             patch.object(server.STORE, "search", side_effect=AssertionError("不读取本地法条")), http_server() as base:
            self.assertEqual(get(base + "/api/expert-library/books"), {"books": []})
            for path, status in [("/api/expert-library/search?book_id=missing&q=test", 404),
                                 ("/api/expert-library/search?book_id=missing", 400),
                                 ("/api/documents", 404),
                                 ("/api/documents/criminal-law/articles/20", 404),
                                 ("/api/search?q=test&source=local", 400)]:
                with self.assertRaises(urllib.error.HTTPError) as error:
                    get(base + path)
                self.assertEqual(error.exception.code, status)
            with self.assertRaises(urllib.error.HTTPError) as error:
                post(base, "/api/chat", {"question": "测试", "source": "local"})
            self.assertEqual(error.exception.code, 400)

    def test_legacy_search_path_gets_legal_text_from_pkulaw(self):
        with patch.object(server, "PKULAW") as provider, http_server() as base:
            provider.configured = True
            provider.search.return_value = {
                "evidence": [{"text": "北大法宝测试正文", "provider": "pkulaw"}],
                "strategy": "fatiao", "tool": "get_law_article", "cached": False, "queried_at": "",
            }
            for path in ("/api/search", "/api/legal/search"):
                result = get(f"{base}{path}?q=test")
                self.assertEqual(result["retrieval"]["source"], "pkulaw")
                self.assertEqual(result["evidence"][0]["text"], "北大法宝测试正文")
