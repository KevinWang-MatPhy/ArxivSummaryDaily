"""按分类均衡检索与增量状态测试。"""
import importlib.util
import json
import re
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


if "config.settings" not in sys.modules:
    settings_path = Path(__file__).parents[1] / "config" / "settings.example.py"
    spec = importlib.util.spec_from_file_location("config.settings", settings_path)
    settings = importlib.util.module_from_spec(spec)
    sys.modules["config.settings"] = settings
    spec.loader.exec_module(settings)

from config.settings import CATEGORIES, RESEARCH_TOPICS, SEARCH_CONFIG
from src.arxiv_client import ArxivClient


class FakeSearch:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


class FakeArxivBackend:
    def __init__(self, results_by_category):
        self.results_by_category = results_by_category
        self.searches = []

    def results(self, search):
        self.searches.append(search)
        match = re.search(r"(?:cat|primary_cat):([A-Za-z0-9.-]+)", search.query)
        category = match.group(1)
        result = self.results_by_category.get(category, [])
        if isinstance(result, Exception):
            raise result
        return iter(result)


def make_paper(paper_id, category, day=29, extra_categories=None):
    categories = [category] + list(extra_categories or [])
    published = datetime(2026, 8, day, 8, 0, tzinfo=timezone.utc)
    return SimpleNamespace(
        title=f"Paper {paper_id}",
        authors=[SimpleNamespace(name="A. Researcher")],
        published=published,
        updated=published,
        summary="Abstract",
        doi=None,
        primary_category=category,
        categories=categories,
        links=[SimpleNamespace(href=f"https://arxiv.org/abs/{paper_id}")],
        pdf_url=f"https://arxiv.org/pdf/{paper_id}",
        entry_id=f"https://arxiv.org/abs/{paper_id}",
        comment="",
    )


class TestArxivClient(unittest.TestCase):
    def make_client(self, results_by_category, **overrides):
        config = dict(SEARCH_CONFIG)
        config.update({
            "max_total_results": 120,
            "max_results_per_category": 8,
            "scan_results_per_category": 25,
            **overrides,
        })
        backend = FakeArxivBackend(results_by_category)
        client_patch = patch("src.arxiv_client.arxiv.Client", return_value=backend)
        search_patch = patch("src.arxiv_client.arxiv.Search", FakeSearch)
        client_patch.start()
        search_patch.start()
        self.addCleanup(client_patch.stop)
        self.addCleanup(search_patch.stop)
        return ArxivClient(config), backend

    def test_taxonomy_contains_four_topics_and_fifteen_unique_categories(self):
        category_ids = [
            category["id"]
            for topic in RESEARCH_TOPICS
            for category in topic["categories"]
        ]
        self.assertEqual(len(RESEARCH_TOPICS), 4)
        self.assertEqual(len(category_ids), 15)
        self.assertEqual(len(set(category_ids)), 15)
        self.assertEqual(category_ids, CATEGORIES)

    def test_queries_categories_individually_and_deduplicates_results(self):
        materials = "cond-mat.mtrl-sci"
        microscopy = "physics.ins-det"
        shared = make_paper(
            "2608.10002v1",
            materials,
            day=28,
            extra_categories=[microscopy],
        )
        client, backend = self.make_client({
            materials: [make_paper("2608.10001v1", materials), shared],
            microscopy: [shared, make_paper("2608.10003v1", microscopy, day=27)],
        })

        with tempfile.TemporaryDirectory() as temp_dir:
            state_file = Path(temp_dir) / "last_run.json"
            papers = client.search_papers(
                [materials, microscopy],
                last_run_file=str(state_file),
            )
            client.save_last_run_info(
                last_run_file=str(state_file),
                total_results=len(papers),
            )
            state = json.loads(state_file.read_text(encoding="utf-8"))

        self.assertEqual(len(backend.searches), 2)
        self.assertNotIn(" OR ", backend.searches[0].query)
        self.assertNotIn(" OR ", backend.searches[1].query)
        self.assertEqual(len(papers), 3)
        self.assertEqual(len({paper["entry_id"] for paper in papers}), 3)
        self.assertEqual(set(state["categories"]), {materials, microscopy})
        self.assertEqual(state["version"], 2)

    def test_legacy_state_and_historical_summaries_are_migrated_without_duplicates(self):
        category = "physics.data-an"
        client, _ = self.make_client({
            category: [
                make_paper("2608.20002v1", category),
                make_paper("2608.20001v1", category, day=28),
            ]
        })

        with tempfile.TemporaryDirectory() as temp_dir:
            data_dir = Path(temp_dir)
            state_file = data_dir / "last_run.json"
            state_file.write_text(
                json.dumps({"latest_entry_id": "https://arxiv.org/abs/2608.19999v1"}),
                encoding="utf-8",
            )
            (data_dir / "summary_20260828_120000.md").write_text(
                "### [Old](https://arxiv.org/abs/2608.20001v1)",
                encoding="utf-8",
            )

            papers = client.search_papers(
                [category],
                last_run_file=str(state_file),
            )
            client.save_last_run_info(
                last_run_file=str(state_file),
                total_results=len(papers),
            )
            state = json.loads(state_file.read_text(encoding="utf-8"))

        self.assertEqual([paper["entry_id"] for paper in papers], [
            "https://arxiv.org/abs/2608.20002v1"
        ])
        self.assertIn("2608.20001v1", state["seen_entry_ids"])
        self.assertIn(category, state["categories"])

    def test_category_quota_and_partial_failure_are_isolated(self):
        working = "physics.comp-ph"
        failing = "physics.optics"
        client, _ = self.make_client(
            {
                working: [
                    make_paper(f"2608.3000{index}v1", working, day=29 - index)
                    for index in range(4)
                ],
                failing: RuntimeError("temporary failure"),
            },
            max_results_per_category=2,
        )

        papers = client.search_papers([working, failing])

        self.assertEqual(len(papers), 2)
        self.assertEqual(client.last_search_report["successful_categories"], [working])
        self.assertEqual(client.last_search_report["failed_categories"], [failing])
        self.assertIn(working, client.pending_state["categories"])
        self.assertNotIn(failing, client.pending_state["categories"])


if __name__ == "__main__":
    unittest.main()
