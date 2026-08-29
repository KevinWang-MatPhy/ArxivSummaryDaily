"""arXiv API 客户端与按分类增量检索。"""
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import arxiv

from config.settings import QUERY, SEARCH_CONFIG


class ArxivClient:
    """按 arXiv 分类均衡检索论文，并维护分类级增量状态。"""

    STATE_VERSION = 2
    ENTRY_ID_PATTERN = re.compile(
        r"arxiv\.org/(?:abs|pdf)/([^\s)\]\"'?#]+)",
        re.IGNORECASE,
    )

    def __init__(self, config=None):
        self.config = config or SEARCH_CONFIG
        # GitHub-hosted runners share outbound IP addresses, so arXiv can
        # occasionally answer with 429/5xx responses. Keep the library-level
        # retry policy explicit instead of relying on package defaults.
        self.client = arxiv.Client(
            page_size=self.config.get("page_size", 100),
            delay_seconds=self.config.get("delay_seconds", 10),
            num_retries=self.config.get("num_retries", 5),
        )
        self.pending_state = None
        self.last_search_report = {}

    @staticmethod
    def _normalize_entry_id(entry_id: str) -> str:
        value = str(entry_id or "").strip().rstrip("/")
        value = value.rsplit("/", 1)[-1]
        return value.removesuffix(".pdf")

    def _safe_get_categories(self, paper: arxiv.Result) -> List[str]:
        """安全地获取论文分类。"""
        try:
            if isinstance(paper.categories, (list, tuple, set)):
                return list(paper.categories)
            if isinstance(paper.categories, str):
                return [paper.categories]
            return [str(paper.categories)]
        except Exception as error:
            print(f"调试 - 获取分类出错: {error}")
            return [paper.primary_category] if paper.primary_category else []

    def _empty_state(self) -> Dict[str, Any]:
        return {
            "version": self.STATE_VERSION,
            "categories": {},
            "seen_entry_ids": [],
        }

    def _load_run_state(self, last_run_file: Optional[str]) -> Dict[str, Any]:
        """读取新版状态，并兼容只有 latest_entry_id 的旧版文件。"""
        state = self._empty_state()
        if not last_run_file:
            return state

        try:
            data = json.loads(Path(last_run_file).read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return state

        if isinstance(data.get("categories"), dict):
            state["categories"] = data["categories"]
        if isinstance(data.get("seen_entry_ids"), list):
            state["seen_entry_ids"] = [
                self._normalize_entry_id(entry_id)
                for entry_id in data["seen_entry_ids"]
                if entry_id
            ]

        # 旧版全局游标无法安全映射到每个分类，只把它加入去重集合；
        # 新的分类游标会在本次成功查询后分别建立。
        legacy_entry_id = data.get("latest_entry_id")
        if legacy_entry_id:
            state["seen_entry_ids"].append(
                self._normalize_entry_id(legacy_entry_id)
            )
        return state

    def _historical_entry_ids(self, last_run_file: Optional[str]) -> set:
        """从已部署的历史摘要中恢复论文 ID，避免状态升级后重复总结。"""
        if not last_run_file:
            return set()
        data_dir = Path(last_run_file).parent
        seen = set()
        for summary_file in data_dir.glob("summary_*.md"):
            try:
                content = summary_file.read_text(encoding="utf-8")
            except OSError:
                continue
            for match in self.ENTRY_ID_PATTERN.finditer(content):
                seen.add(self._normalize_entry_id(match.group(1)))
        return seen

    def save_last_run_info(
        self,
        latest_entry_id: Optional[str] = None,
        last_run_file: Optional[str] = None,
        total_results: int = 0,
    ):
        """在摘要成功后原子写入分类游标与去重状态。"""
        if not last_run_file:
            return

        state = dict(self.pending_state or self._load_run_state(last_run_file))
        if latest_entry_id:
            # 保留参数仅用于兼容旧调用；新版检索通常通过 pending_state 保存。
            state["latest_entry_id"] = latest_entry_id
        state["version"] = self.STATE_VERSION
        state["timestamp"] = datetime.now().isoformat()
        state["total_results"] = total_results

        output_path = Path(last_run_file)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = output_path.with_suffix(output_path.suffix + ".tmp")
        temporary_path.write_text(
            json.dumps(state, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temporary_path.replace(output_path)
        print(
            "已更新分类级运行记录："
            f"{len(state.get('categories', {}))} 个分类，"
            f"本次 {total_results} 篇论文"
        )

    def _create_search_query(
        self,
        query: str = "",
        categories: Optional[List[str]] = None,
        keywords: Optional[Dict[str, List[str]]] = None,
    ) -> str:
        """构建高级搜索查询。"""
        del keywords  # 保留旧接口参数，主题相关性后续可独立扩展。
        search_parts = []
        if query:
            if self.config["title_only"]:
                search_parts.append(f"ti:{query}")
            elif self.config["abstract_only"]:
                search_parts.append(f"abs:{query}")
            elif self.config["author_only"]:
                search_parts.append(f"au:{query}")
            else:
                search_parts.append(query)

        if categories:
            category_prefix = (
                "cat" if self.config["include_cross_listed"] else "primary_cat"
            )
            category_parts = [
                f"{category_prefix}:{category}"
                for category in categories
                if category
            ]
            if category_parts:
                search_parts.append(f"({' OR '.join(category_parts)})")

        return " AND ".join(search_parts) if search_parts else "*:*"

    def _paper_metadata(self, paper: arxiv.Result) -> Dict[str, Any]:
        return {
            "title": paper.title,
            "authors": [author.name for author in paper.authors],
            "published": paper.published.isoformat(),
            "updated": paper.updated.isoformat(),
            "summary": paper.summary,
            "doi": paper.doi,
            "primary_category": paper.primary_category,
            "categories": self._safe_get_categories(paper),
            "links": [link.href for link in paper.links],
            "pdf_url": paper.pdf_url,
            "entry_id": paper.entry_id,
            "comment": getattr(paper, "comment", ""),
        }

    def _search_one_category(
        self,
        category: str,
        query: str,
        checkpoint: Optional[str],
    ) -> Tuple[List[Dict[str, Any]], Optional[str]]:
        """查询一个分类，并在遇到该分类自己的上次游标时停止。"""
        search_query = self._create_search_query(query, [category])
        print(f"检索分类 {category}: {search_query}")
        search_kwargs = {
            "query": search_query,
            "max_results": self.config.get("scan_results_per_category", 25),
            "sort_by": getattr(arxiv.SortCriterion, self.config["sort_by"]),
            "sort_order": getattr(arxiv.SortOrder, self.config["sort_order"]),
        }
        if self.config.get("id_list") is not None:
            search_kwargs["id_list"] = self.config["id_list"]

        search = arxiv.Search(**search_kwargs)
        results = []
        latest_entry_id = None
        normalized_checkpoint = self._normalize_entry_id(checkpoint)
        for paper in self.client.results(search):
            if latest_entry_id is None:
                latest_entry_id = paper.entry_id
            if (
                normalized_checkpoint
                and self._normalize_entry_id(paper.entry_id)
                == normalized_checkpoint
            ):
                break
            try:
                results.append(self._paper_metadata(paper))
            except Exception as error:
                raise RuntimeError(f"处理单篇文章时出错: {error}") from error
        return results, latest_entry_id

    def search_papers(
        self,
        categories: Optional[List[str]] = None,
        query: str = QUERY,
        last_run_file: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """逐分类均衡检索，去重后返回按发布日期排序的论文。"""
        requested_categories = list(dict.fromkeys(categories or []))
        state = self._load_run_state(last_run_file)
        category_state = dict(state.get("categories", {}))
        seen_entry_ids = set(state.get("seen_entry_ids", []))
        seen_entry_ids.update(self._historical_entry_ids(last_run_file))

        per_category_limit = max(
            1, int(self.config.get("max_results_per_category", 10))
        )
        max_total_results = max(1, int(self.config["max_total_results"]))
        candidates = {}
        successful_categories = []
        failed_categories = {}

        for category in requested_categories:
            checkpoint_data = category_state.get(category, {})
            checkpoint = (
                checkpoint_data.get("latest_entry_id")
                if isinstance(checkpoint_data, dict)
                else checkpoint_data
            )
            try:
                papers, latest_entry_id = self._search_one_category(
                    category,
                    query,
                    checkpoint,
                )
            except Exception as error:
                failed_categories[category] = error
                print(f"::warning::分类 {category} 检索失败，将在下次运行重试：{error}")
                continue

            successful_categories.append(category)
            if latest_entry_id:
                category_state[category] = {
                    "latest_entry_id": latest_entry_id,
                    "timestamp": datetime.now().isoformat(),
                }

            selected_for_category = 0
            for paper in papers:
                paper_id = self._normalize_entry_id(paper["entry_id"])
                if paper_id in seen_entry_ids:
                    continue
                selected_for_category += 1
                if paper_id not in candidates:
                    paper["matched_categories"] = [category]
                    candidates[paper_id] = paper
                elif category not in candidates[paper_id]["matched_categories"]:
                    candidates[paper_id]["matched_categories"].append(category)
                if selected_for_category >= per_category_limit:
                    break

            print(
                f"分类 {category}：发现 {len(papers)} 篇增量候选，"
                f"选择 {selected_for_category} 篇"
            )

        if failed_categories and not successful_categories:
            raise next(iter(failed_categories.values()))
        if failed_categories:
            print(
                "::warning::本次有部分分类检索失败："
                + ", ".join(failed_categories)
                + "；成功分类仍将继续处理。"
            )

        all_results = sorted(
            candidates.values(),
            key=lambda paper: paper.get("published", ""),
            reverse=True,
        )[:max_total_results]

        updated_seen_ids = seen_entry_ids | {
            self._normalize_entry_id(paper["entry_id"])
            for paper in all_results
        }
        seen_limit = max(1, int(self.config.get("seen_entry_ids_limit", 10000)))
        self.pending_state = {
            "version": self.STATE_VERSION,
            "categories": category_state,
            "seen_entry_ids": sorted(updated_seen_ids)[-seen_limit:],
        }
        self.last_search_report = {
            "requested_categories": requested_categories,
            "successful_categories": successful_categories,
            "failed_categories": list(failed_categories),
            "selected_papers": len(all_results),
        }

        if all_results:
            print(
                f"均衡检索完成：{len(successful_categories)}/"
                f"{len(requested_categories)} 个分类成功，"
                f"去重后选择 {len(all_results)} 篇论文"
            )
        else:
            print("未找到新的论文")
        return all_results
