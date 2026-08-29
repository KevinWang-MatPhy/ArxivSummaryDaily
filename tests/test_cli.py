"""命令行重试与 arXiv 临时故障降级测试。"""
import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch


# GitHub Actions 会从示例文件生成 settings.py；单元测试直接加载同一份配置。
if "config.settings" not in sys.modules:
    settings_path = Path(__file__).parents[1] / "config" / "settings.example.py"
    spec = importlib.util.spec_from_file_location("config.settings", settings_path)
    settings = importlib.util.module_from_spec(spec)
    sys.modules["config.settings"] = settings
    spec.loader.exec_module(settings)

from src import cli


class FakeArxivHTTPError(Exception):
    __module__ = "arxiv"

    def __init__(self, status):
        self.status = status
        super().__init__(
            f"Page request resulted in HTTP {status} "
            "(https://export.arxiv.org/api/query)"
        )


class TestCliRetryHandling(unittest.TestCase):
    def test_transient_arxiv_error_uses_backoff_then_stale_data(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            Path(temp_dir, "summary_20260829_120000.md").write_text(
                "existing summary", encoding="utf-8"
            )
            client = Mock()
            client.search_papers.side_effect = FakeArxivHTTPError(429)
            sleeps = []

            config = {
                "workflow_retry_attempts": 3,
                "workflow_retry_delay": 2,
                "allow_stale_on_transient_error": True,
            }
            with (
                patch.object(cli, "ArxivClient", return_value=client),
                patch.object(cli, "PaperSummarizer"),
                patch.dict(cli.SEARCH_CONFIG, config),
            ):
                cli.main(
                    ["--output-dir", temp_dir],
                    sleep_func=sleeps.append,
                )

            self.assertEqual(client.search_papers.call_count, 3)
            self.assertEqual(sleeps, [2, 4])

    def test_transient_arxiv_error_without_history_still_fails(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            client = Mock()
            client.search_papers.side_effect = FakeArxivHTTPError(503)

            config = {
                "workflow_retry_attempts": 1,
                "allow_stale_on_transient_error": True,
            }
            with (
                patch.object(cli, "ArxivClient", return_value=client),
                patch.object(cli, "PaperSummarizer"),
                patch.dict(cli.SEARCH_CONFIG, config),
                self.assertRaises(SystemExit) as raised,
            ):
                cli.main(["--output-dir", temp_dir], sleep_func=Mock())

            self.assertEqual(raised.exception.code, 1)

    def test_non_transient_error_never_uses_stale_data(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            Path(temp_dir, "summary_existing.md").write_text(
                "existing summary", encoding="utf-8"
            )
            client = Mock()
            client.search_papers.side_effect = RuntimeError("invalid config")

            with (
                patch.object(cli, "ArxivClient", return_value=client),
                patch.object(cli, "PaperSummarizer"),
                patch.dict(cli.SEARCH_CONFIG, {"workflow_retry_attempts": 1}),
                self.assertRaises(SystemExit),
            ):
                cli.main(["--output-dir", temp_dir], sleep_func=Mock())


if __name__ == "__main__":
    unittest.main()
