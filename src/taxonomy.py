"""研究主题与 arXiv 分类的共享辅助函数。"""
from typing import Iterable, List

from config.settings import RESEARCH_TOPICS


def topic_keys_for_categories(categories: Iterable[str]) -> List[str]:
    """按配置顺序返回一组 arXiv 分类所属的研究主题。"""
    category_set = set(categories or [])
    return [
        topic["key"]
        for topic in RESEARCH_TOPICS
        if any(
            category["id"] in category_set
            for category in topic["categories"]
        )
    ]


def taxonomy_payload():
    """返回可安全序列化给网页的主题分类结构。"""
    return [
        {
            "key": topic["key"],
            "label": topic["label"],
            "categories": [dict(category) for category in topic["categories"]],
        }
        for topic in RESEARCH_TOPICS
    ]
