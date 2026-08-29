"""使用 LLM 对已生成的论文摘要进行研究方法分类。"""
from __future__ import annotations

import hashlib
import html
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence


METHOD_LABELS = {
    "theory": "理论",
    "computation": "计算",
    "experiment": "实验",
    "unclassified": "未判定",
}
VALID_METHODS = {"theory", "computation", "experiment"}
METHOD_ORDER = ("theory", "computation", "experiment")
CLASSIFIER_VERSION = 1

SECTION_PATTERN = re.compile(
    r'<section\s+class="paper-summary"(?P<attrs>[^>]*)>(?P<body>.*?)</section>',
    re.DOTALL,
)
TITLE_LINK_PATTERN = re.compile(r'^###\s+\[(?P<title>.*?)\]\((?P<url>.*?)\)', re.MULTILINE)
FIELD_PATTERN_TEMPLATE = r'-\s*\*\*{field}:\*\*\s*(?P<value>.*)'


@dataclass
class SummaryRecord:
    """从一个 paper-summary 区块解析出的分类输入。"""

    paper_id: str
    title: str
    url: str
    authors: str
    objective: str
    findings: str
    categories: List[str]
    source_hash: str

    def to_prompt_payload(self) -> Dict[str, Any]:
        return {
            "id": self.paper_id,
            "title": self.title,
            "authors": self.authors,
            "objective": self.objective,
            "findings": self.findings,
            "categories": self.categories,
        }


class ClassificationResponseError(ValueError):
    """LLM 分类响应无法通过结构与枚举校验。"""


class MethodClassifier:
    """批量分类摘要文件，并把结果写回 HTML 数据属性和缓存。"""

    def __init__(self, client: Any, config: Optional[Dict[str, Any]] = None):
        self.client = client
        self.config = config or {}
        self.batch_size = max(1, int(self.config.get("batch_size", 40)))
        self.confidence_threshold = float(
            self.config.get("confidence_threshold", 0.60)
        )
        self.response_retries = max(1, int(self.config.get("response_retries", 2)))
        self.max_tokens = int(self.config.get("max_tokens", 4096))

    def classify_directory(
        self,
        data_dir: Path | str,
        cache_file: Optional[Path | str] = None,
        force: bool = False,
        backfill_existing: bool = True,
    ) -> Dict[str, int]:
        """分类目录中的摘要文件；重复运行会复用内容哈希一致的缓存。"""
        data_path = Path(data_dir)
        summary_files = sorted(data_path.glob("summary_*.md"))
        if not backfill_existing and summary_files:
            summary_files = [max(summary_files, key=lambda item: item.stat().st_mtime)]

        cache_path = Path(cache_file) if cache_file else data_path / "method_classifications.json"
        cache = self._load_cache(cache_path)
        records_by_id: Dict[str, SummaryRecord] = {}
        file_contents: Dict[Path, str] = {}

        for file_path in summary_files:
            content = file_path.read_text(encoding="utf-8")
            file_contents[file_path] = content
            for record in self.extract_records(content):
                records_by_id[record.paper_id] = record

        pending: List[SummaryRecord] = []
        reused = 0
        for record in records_by_id.values():
            cached = cache["records"].get(record.paper_id)
            if (
                not force
                and cached
                and cached.get("source_hash") == record.source_hash
                and cached.get("classifier_version") == CLASSIFIER_VERSION
                and not cached.get("retryable", False)
            ):
                reused += 1
            else:
                pending.append(record)

        classified = 0
        unclassified = 0
        for batch in self._chunks(pending, self.batch_size):
            try:
                results = self._classify_batch(batch)
            except Exception as exc:
                results = {
                    record.paper_id: self._unclassified_result(
                        record,
                        f"批量分类失败: {exc}",
                        retryable=True,
                    )
                    for record in batch
                }

            for record in batch:
                result = results.get(record.paper_id) or self._unclassified_result(
                    record,
                    "LLM 响应缺少该论文",
                    retryable=True,
                )
                cache["records"][record.paper_id] = result
                if result["methods"] == ["unclassified"]:
                    unclassified += 1
                else:
                    classified += 1

        changed_files = 0
        for file_path, content in file_contents.items():
            updated = self.apply_classifications(content, cache["records"])
            if updated != content:
                file_path.write_text(updated, encoding="utf-8")
                changed_files += 1

        cache["version"] = 1
        cache["updated_at"] = datetime.now(timezone.utc).isoformat()
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(
            json.dumps(cache, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return {
            "files": len(summary_files),
            "records": len(records_by_id),
            "classified": classified,
            "unclassified": unclassified,
            "reused": reused,
            "changed_files": changed_files,
        }

    def extract_records(self, content: str) -> List[SummaryRecord]:
        """从摘要 Markdown 中提取稳定的分类输入。"""
        records = []
        for match in SECTION_PATTERN.finditer(content):
            attrs = match.group("attrs")
            body = match.group("body")
            title_match = TITLE_LINK_PATTERN.search(body)
            if not title_match:
                continue
            title = self._normalize_markdown_text(title_match.group("title"))
            url = title_match.group("url").strip()
            paper_id = self._paper_id(url, title)
            authors = self._normalize_markdown_text(self._extract_field(body, "作者"))
            objective = self._normalize_markdown_text(self._extract_field(body, "研究目的"))
            findings = self._normalize_markdown_text(self._extract_field(body, "主要发现"))
            categories = self._extract_attribute(attrs, "data-categories").split(",")
            categories = [item.strip() for item in categories if item.strip()]
            source_payload = json.dumps(
                {
                    "title": title,
                    "authors": authors,
                    "objective": objective,
                    "findings": findings,
                    "categories": categories,
                },
                ensure_ascii=False,
                sort_keys=True,
            )
            records.append(SummaryRecord(
                paper_id=paper_id,
                title=title,
                url=url,
                authors=authors,
                objective=objective,
                findings=findings,
                categories=categories,
                source_hash=hashlib.sha256(source_payload.encode("utf-8")).hexdigest(),
            ))
        return records

    def apply_classifications(
        self,
        content: str,
        classifications: Dict[str, Dict[str, Any]],
    ) -> str:
        """把缓存分类写回 section 属性和可见元数据。"""
        def replace(match: re.Match[str]) -> str:
            attrs = match.group("attrs")
            body = match.group("body")
            title_match = TITLE_LINK_PATTERN.search(body)
            if not title_match:
                return match.group(0)
            paper_id = self._paper_id(
                title_match.group("url").strip(),
                title_match.group("title").strip(),
            )
            result = classifications.get(paper_id)
            if not result:
                return match.group(0)

            methods = result.get("methods") or ["unclassified"]
            primary = result.get("primary_method") or "unclassified"
            confidence = float(result.get("confidence", 0.0))
            attrs = self._upsert_attribute(attrs, "data-methods", ",".join(methods))
            attrs = self._upsert_attribute(attrs, "data-primary-method", primary)
            attrs = self._upsert_attribute(
                attrs,
                "data-method-confidence",
                f"{confidence:.2f}",
            )
            body = self._upsert_method_meta(body, methods, primary, confidence)
            return f'<section class="paper-summary"{attrs}>{body}</section>'

        return SECTION_PATTERN.sub(replace, content)

    def _classify_batch(
        self,
        records: Sequence[SummaryRecord],
    ) -> Dict[str, Dict[str, Any]]:
        prompt = self._build_prompt(records)
        last_error: Optional[Exception] = None
        for attempt in range(self.response_retries):
            messages = [
                {
                    "role": "system",
                    "content": "你是严谨的科研文献方法学分类器，只输出合法 JSON。",
                },
                {
                    "role": "user",
                    "content": prompt + (
                        "\n上一次输出未通过校验，请严格遵守 JSON 结构和枚举。"
                        if attempt else ""
                    ),
                },
            ]
            response = self.client.chat_completion(
                messages,
                temperature=0,
                max_tokens=self.max_tokens,
            )
            try:
                parsed = self.parse_response(
                    response["choices"][0]["message"]["content"],
                    records,
                )
                return {item["paper_id"]: item for item in parsed}
            except ClassificationResponseError as exc:
                last_error = exc
        raise ClassificationResponseError(str(last_error or "分类响应无效"))

    def parse_response(
        self,
        response_text: str,
        records: Sequence[SummaryRecord],
    ) -> List[Dict[str, Any]]:
        """解析并严格校验 LLM 返回的 JSON。"""
        cleaned = response_text.strip()
        cleaned = re.sub(r'^```(?:json)?\s*', '', cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r'\s*```$', '', cleaned)
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start < 0 or end <= start:
            raise ClassificationResponseError("响应中没有 JSON 对象")
        try:
            payload = json.loads(cleaned[start:end + 1])
        except json.JSONDecodeError as exc:
            raise ClassificationResponseError(f"JSON 解析失败: {exc}") from exc

        raw_items = payload.get("classifications")
        if not isinstance(raw_items, list):
            raise ClassificationResponseError("缺少 classifications 数组")

        expected = {record.paper_id: record for record in records}
        seen = set()
        validated = []
        for item in raw_items:
            if not isinstance(item, dict):
                raise ClassificationResponseError("分类项必须是对象")
            paper_id = str(item.get("id", ""))
            if paper_id not in expected or paper_id in seen:
                raise ClassificationResponseError(f"未知或重复的论文 ID: {paper_id}")
            methods = item.get("methods")
            if not isinstance(methods, list) or not methods:
                raise ClassificationResponseError(f"{paper_id} 缺少 methods")
            normalized_methods = []
            for method in methods:
                method = str(method).lower()
                if method not in VALID_METHODS:
                    raise ClassificationResponseError(f"非法方法标签: {method}")
                if method not in normalized_methods:
                    normalized_methods.append(method)
            primary = str(item.get("primary_method", "")).lower()
            if primary not in normalized_methods:
                raise ClassificationResponseError(f"{paper_id} 的主方法不在 methods 中")
            try:
                confidence = float(item.get("confidence"))
            except (TypeError, ValueError) as exc:
                raise ClassificationResponseError(f"{paper_id} 置信度无效") from exc
            if not 0 <= confidence <= 1:
                raise ClassificationResponseError(f"{paper_id} 置信度超出范围")
            reason = str(item.get("reason", "")).strip()
            record = expected[paper_id]
            if confidence < self.confidence_threshold:
                validated.append(self._unclassified_result(
                    record,
                    f"低置信度 {confidence:.2f}: {reason}",
                ))
            else:
                ordered = [primary] + [
                    method for method in METHOD_ORDER
                    if method in normalized_methods and method != primary
                ]
                validated.append({
                    "paper_id": paper_id,
                    "source_hash": record.source_hash,
                    "classifier_version": CLASSIFIER_VERSION,
                    "methods": ordered,
                    "primary_method": primary,
                    "confidence": round(confidence, 4),
                    "reason": reason,
                    "retryable": False,
                })
            seen.add(paper_id)

        if seen != set(expected):
            missing = sorted(set(expected) - seen)
            raise ClassificationResponseError(f"缺少论文分类: {missing}")
        return validated

    def _build_prompt(self, records: Sequence[SummaryRecord]) -> str:
        papers = [record.to_prompt_payload() for record in records]
        return f"""请根据下列已经整理好的论文中文摘要，判断研究技术方法。

允许的标签只有：
- theory：核心证据来自理论模型、解析推导、形式化证明或对称性分析。
- computation：核心证据来自 DFT、分子动力学、Monte Carlo、数值模拟、机器学习、图像重建或计算分析。
- experiment：核心证据来自样品制备、电子显微镜、谱学、输运、原位实验或其他实际测量。

规则：
1. methods 可以包含一个或多个标签；不要为了凑数添加弱相关标签。
2. primary_method 必须是 methods 中的主导证据方法。
3. confidence 是 0 到 1 之间的数字。
4. reason 用一句简短中文说明判定依据。
5. 必须为每个输入 id 返回且只返回一项。
6. 只输出下面结构的 JSON，不要使用 Markdown 代码块：
{{"classifications":[{{"id":"...","methods":["experiment","computation"],"primary_method":"experiment","confidence":0.91,"reason":"..."}}]}}

论文数据：
{json.dumps(papers, ensure_ascii=False)}"""

    def _unclassified_result(
        self,
        record: SummaryRecord,
        reason: str,
        retryable: bool = False,
    ) -> Dict[str, Any]:
        return {
            "paper_id": record.paper_id,
            "source_hash": record.source_hash,
            "classifier_version": CLASSIFIER_VERSION,
            "methods": ["unclassified"],
            "primary_method": "unclassified",
            "confidence": 0.0,
            "reason": reason,
            "retryable": retryable,
        }

    @staticmethod
    def _load_cache(path: Path) -> Dict[str, Any]:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(payload, dict) and isinstance(payload.get("records"), dict):
                return payload
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            pass
        return {"version": 1, "records": {}}

    @staticmethod
    def _chunks(items: Sequence[SummaryRecord], size: int) -> Iterable[Sequence[SummaryRecord]]:
        for index in range(0, len(items), size):
            yield items[index:index + size]

    @staticmethod
    def _extract_field(body: str, field: str) -> str:
        pattern = re.compile(FIELD_PATTERN_TEMPLATE.format(field=re.escape(field)))
        match = pattern.search(body)
        return match.group("value").strip() if match else ""

    @staticmethod
    def _normalize_markdown_text(value: str) -> str:
        """消除站点生成器添加的 Markdown 转义，保持分类缓存哈希稳定。"""
        return value.strip().replace(r"\_", "_").replace(r"\|", "|")

    @staticmethod
    def _paper_id(url: str, title: str) -> str:
        arxiv_match = re.search(r'arxiv\.org/(?:abs|pdf)/([^/?#]+)', url, re.IGNORECASE)
        if arxiv_match:
            return arxiv_match.group(1).removesuffix(".pdf")
        return hashlib.sha256(f"{url}|{title}".encode("utf-8")).hexdigest()[:20]

    @staticmethod
    def _extract_attribute(attrs: str, name: str) -> str:
        match = re.search(rf'\s{name}="([^"]*)"', attrs)
        return html.unescape(match.group(1)) if match else ""

    @staticmethod
    def _upsert_attribute(attrs: str, name: str, value: str) -> str:
        escaped = html.escape(value, quote=True)
        pattern = re.compile(rf'\s{name}="[^"]*"')
        replacement = f' {name}="{escaped}"'
        if pattern.search(attrs):
            return pattern.sub(replacement, attrs, count=1)
        return attrs + replacement

    @staticmethod
    def _upsert_method_meta(
        body: str,
        methods: Sequence[str],
        primary: str,
        confidence: float,
    ) -> str:
        labels = " · ".join(METHOD_LABELS.get(method, method) for method in methods)
        method_meta = (
            '<span class="paper-method-meta" '
            f'data-primary-method="{html.escape(primary, quote=True)}" '
            f'title="分类置信度 {confidence:.2f}">'
            f'<strong>研究方法:</strong> {html.escape(labels)}</span>'
        )
        existing = re.compile(r'<span class="paper-method-meta".*?</span>', re.DOTALL)
        if existing.search(body):
            return existing.sub(method_meta, body, count=1)
        meta_end = body.find("</div>")
        if meta_end < 0:
            return body
        return body[:meta_end] + "  " + method_meta + "\n" + body[meta_end:]
