import io
import unittest
import zipfile
from pathlib import Path

import file_ingest
import server


def make_docx(paragraphs: list[str]) -> bytes:
    body = "".join(f"<w:p><w:r><w:t>{text}</w:t></w:r></w:p>" for text in paragraphs)
    document = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f"<w:body>{body}</w:body></w:document>"
    )
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("word/document.xml", document)
    return buffer.getvalue()


class IngestTest(unittest.TestCase):
    def setUp(self) -> None:
        self.saved: list[str] = []

    def tearDown(self) -> None:
        for file_id in self.saved:
            file_ingest.delete_record(file_id)

    def test_docx_paragraphs_become_evidence(self) -> None:
        data = make_docx(["第一段：租赁期限五年。", "第二段：押金为两个月租金。"])
        record = file_ingest.ingest(data, "租赁合同.docx")
        self.saved.append(record["file_id"])
        self.assertEqual(record["kind"], "docx")
        self.assertEqual(record["engine"], "docx")
        self.assertGreater(record["chars"], 10)
        items = file_ingest.evidence_from_files([record["file_id"]], "租期多久")
        self.assertTrue(items)
        self.assertEqual(items[0]["provider"], "upload")
        self.assertIn("租赁期限五年", items[0]["text"])
        self.assertEqual(items[0]["document_title"], "租赁合同.docx")

    def test_non_document_is_rejected(self) -> None:
        with self.assertRaises(file_ingest.FileError):
            file_ingest.ingest(b"MZ\x90\x00 not a document", "virus.exe")

    def test_long_document_uses_chunk_retrieval(self) -> None:
        paragraphs = [f"第{i}段：押金条款与违约金计算方式说明。" + "补充说明" * 120 for i in range(40)]
        record = file_ingest.ingest(make_docx(paragraphs), "长合同.docx")
        self.saved.append(record["file_id"])
        self.assertGreater(record["chars"], file_ingest.MAX_FULL_CHARS)
        items = file_ingest.evidence_from_files([record["file_id"]], "押金 违约金")
        self.assertTrue(items)
        self.assertLessEqual(len(items), 3)
        self.assertTrue(all(item["article_label"].startswith("第") for item in items))

    def test_upload_route_registered(self) -> None:
        handler_source = Path(server.__file__).read_text(encoding="utf-8")
        self.assertIn('path == "/api/files"', handler_source)
        self.assertIn("def handle_upload", handler_source)
