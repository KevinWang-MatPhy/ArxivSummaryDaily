"""研究技术方法分类器测试。"""
import json
import tempfile
import unittest
from pathlib import Path

from src.method_classifier import (
    ClassificationResponseError,
    MethodClassifier,
)
from src.site_manager import SiteManager


SAMPLE_SUMMARY = """# Arxiv论文总结报告

<section class="paper-summary" data-categories="cond-mat.mtrl-sci,physics.ins-det" data-published="2026-08-29" markdown="1">
<div class="paper-summary-meta">
  <span><strong>分类:</strong> cond-mat.mtrl-sci, physics.ins-det</span>
  <span><strong>发布日期:</strong> 2026-08-29</span>
</div>

### [Atomic-resolution 4D-STEM](https://arxiv.org/abs/2608.12345v1)
- **作者:** A. Researcher
- **研究目的:** 使用四维扫描透射电子显微镜研究材料局域结构。
- **主要发现:** 实验测量结合数值图像重建揭示了原子尺度应变。
</section>
"""


class FakeClient:
    def __init__(self, content):
        self.content = content
        self.calls = []

    def chat_completion(self, messages, temperature=None, max_tokens=None):
        self.calls.append({
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        })
        return {"choices": [{"message": {"content": self.content}}]}


class TestMethodClassifier(unittest.TestCase):
    def setUp(self):
        self.classifier = MethodClassifier(FakeClient("{}"), {
            "confidence_threshold": 0.6,
            "response_retries": 1,
        })
        self.records = self.classifier.extract_records(SAMPLE_SUMMARY)

    def test_extracts_stable_summary_record(self):
        self.assertEqual(len(self.records), 1)
        record = self.records[0]
        self.assertEqual(record.paper_id, "2608.12345v1")
        self.assertEqual(record.title, "Atomic-resolution 4D-STEM")
        self.assertEqual(
            record.categories,
            ["cond-mat.mtrl-sci", "physics.ins-det"],
        )
        self.assertIn("数值图像重建", record.findings)

    def test_site_markdown_escaping_does_not_invalidate_cache_hash(self):
        plain = SAMPLE_SUMMARY.replace("Atomic-resolution", "Atomic_resolution")
        escaped = SAMPLE_SUMMARY.replace("Atomic-resolution", r"Atomic\_resolution")
        plain_record = self.classifier.extract_records(plain)[0]
        escaped_record = self.classifier.extract_records(escaped)[0]
        self.assertEqual(plain_record.title, escaped_record.title)
        self.assertEqual(plain_record.source_hash, escaped_record.source_hash)

    def test_parses_multilabel_response_and_primary_method(self):
        response = json.dumps({
            "classifications": [{
                "id": "2608.12345v1",
                "methods": ["computation", "experiment"],
                "primary_method": "experiment",
                "confidence": 0.92,
                "reason": "电子显微实验结合数值重建。",
            }]
        }, ensure_ascii=False)
        result = self.classifier.parse_response(response, self.records)[0]
        self.assertEqual(result["methods"], ["experiment", "computation"])
        self.assertEqual(result["primary_method"], "experiment")

    def test_low_confidence_becomes_unclassified(self):
        response = json.dumps({
            "classifications": [{
                "id": "2608.12345v1",
                "methods": ["experiment"],
                "primary_method": "experiment",
                "confidence": 0.4,
                "reason": "摘要证据不足。",
            }]
        }, ensure_ascii=False)
        result = self.classifier.parse_response(response, self.records)[0]
        self.assertEqual(result["methods"], ["unclassified"])
        self.assertEqual(result["primary_method"], "unclassified")
        self.assertFalse(result["retryable"])

    def test_rejects_unknown_method(self):
        response = json.dumps({
            "classifications": [{
                "id": "2608.12345v1",
                "methods": ["review"],
                "primary_method": "review",
                "confidence": 0.9,
                "reason": "综述。",
            }]
        }, ensure_ascii=False)
        with self.assertRaises(ClassificationResponseError):
            self.classifier.parse_response(response, self.records)

    def test_directory_classification_is_cached_and_idempotent(self):
        response = json.dumps({
            "classifications": [{
                "id": "2608.12345v1",
                "methods": ["experiment", "computation"],
                "primary_method": "experiment",
                "confidence": 0.92,
                "reason": "电子显微实验结合数值重建。",
            }]
        }, ensure_ascii=False)
        client = FakeClient(response)
        classifier = MethodClassifier(client, {
            "batch_size": 40,
            "confidence_threshold": 0.6,
            "response_retries": 1,
        })

        with tempfile.TemporaryDirectory() as temp_dir:
            data_dir = Path(temp_dir)
            summary_file = data_dir / "summary_20260829_120000.md"
            summary_file.write_text(SAMPLE_SUMMARY, encoding="utf-8")

            first = classifier.classify_directory(data_dir)
            classified_content = summary_file.read_text(encoding="utf-8")
            second = classifier.classify_directory(data_dir)

            self.assertEqual(len(client.calls), 1)
            self.assertEqual(first["classified"], 1)
            self.assertEqual(second["reused"], 1)
            self.assertIn('data-methods="experiment,computation"', classified_content)
            self.assertIn('data-primary-method="experiment"', classified_content)
            self.assertIn("<strong>研究方法:</strong> 实验 · 计算", classified_content)
            self.assertEqual(
                classified_content,
                summary_file.read_text(encoding="utf-8"),
            )

    def test_transient_batch_failure_is_retried_on_next_run(self):
        client = FakeClient("not valid json")
        classifier = MethodClassifier(client, {"response_retries": 1})

        with tempfile.TemporaryDirectory() as temp_dir:
            data_dir = Path(temp_dir)
            (data_dir / "summary_20260829_120000.md").write_text(
                SAMPLE_SUMMARY,
                encoding="utf-8",
            )
            classifier.classify_directory(data_dir)
            classifier.classify_directory(data_dir)

            self.assertEqual(len(client.calls), 2)
            cache = json.loads(
                (data_dir / "method_classifications.json").read_text(encoding="utf-8")
            )
            self.assertTrue(cache["records"]["2608.12345v1"]["retryable"])

    def test_classification_survives_site_generation(self):
        response = json.dumps({
            "classifications": [{
                "id": "2608.12345v1",
                "methods": ["experiment", "computation"],
                "primary_method": "experiment",
                "confidence": 0.92,
                "reason": "电子显微实验结合数值重建。",
            }]
        }, ensure_ascii=False)
        project_root = Path(__file__).parents[1]

        with tempfile.TemporaryDirectory() as temp_dir:
            data_dir = Path(temp_dir)
            (data_dir / "summary_20260829_120000.md").write_text(
                SAMPLE_SUMMARY,
                encoding="utf-8",
            )
            MethodClassifier(FakeClient(response), {
                "response_retries": 1,
            }).classify_directory(data_dir)

            site = SiteManager(data_dir, project_root / ".github")
            files = site.get_sorted_summary_files()
            site.copy_latest_to_index(files)
            site.create_archive_page(files)
            site.setup_site_structure()

            index = (data_dir / "index.md").read_text(encoding="utf-8")
            layout = (data_dir / "_layouts" / "default.html").read_text(
                encoding="utf-8"
            )
            self.assertIn('data-methods="experiment,computation"', index)
            self.assertIn("<strong>研究方法:</strong> 实验 · 计算", index)
            self.assertIn('id="method-filter-row"', layout)
            self.assertIn("const selectedMethods", layout)


if __name__ == "__main__":
    unittest.main()
