"""抓取层基类与编排器。

设计目标：
- 每个数据源一个 BaseFetcher 子类，统一出参 NormalizedMeta。
- FetcherOrchestrator 按字段级信任顺序合并三源：openalex > crossref > semantic_scholar。
- 缺失字段写入 Paper.missing_fields，供前端"人工补全"。
- 所有网络请求带 retry + 指数退避 + jitter（tenacity）。
"""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_random_exponential,
)

from app.models.fields import REQUIRED_KEYS

logger = logging.getLogger(__name__)


def _humanize_seconds(seconds: float | None) -> str:
    """秒数 → 中文时长（把 retry-after 说成人话）。"""
    if not seconds or seconds <= 0:
        return ""
    if seconds >= 3600:
        return f"约 {seconds / 3600:.1f} 小时"
    return f"约 {max(1, round(seconds / 60))} 分钟"


def _retry_after_seconds(resp: httpx.Response) -> float | None:
    """Retry-After 头：OpenAlex 给的是秒数；也容忍 HTTP-date 形态。"""
    raw = resp.headers.get("retry-after")
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        pass
    from datetime import datetime, timezone
    from email.utils import parsedate_to_datetime

    try:
        dt = parsedate_to_datetime(raw)
        return max(0.0, (dt - datetime.now(timezone.utc)).total_seconds())
    except Exception:  # noqa: BLE001 — 头格式不可解析就当没有
        return None


def _remaining_is_zero(resp: httpx.Response) -> bool:
    """OpenAlex 的 x-ratelimit-remaining 是否为 0（= 日配额耗尽）。"""
    raw = resp.headers.get("x-ratelimit-remaining")
    if raw is None:
        return False
    try:
        return float(raw) <= 0
    except ValueError:
        return False


class SourceUnavailable(Exception):
    """抓取源本次不可用：配额耗尽 / 限流 / 5xx / 网络失败。

    与"源返回空结果"是两回事 —— 这是"这次没问到"。调用方必须把它传达给用户，
    不能当成"没有数据"，否则会把"未知"渲染成"不存在"。

    kind 取值：budget_exhausted | rate_limited | server_error | timeout | not_found | unavailable
    """

    def __init__(
        self,
        source: str,
        reason: str,
        *,
        kind: str = "unavailable",
        retry_after: float | None = None,
    ):
        self.source = source
        self.reason = reason
        self.kind = kind
        self.retry_after = retry_after
        super().__init__(f"{source}: {reason}")

    def user_message(self) -> str:
        """给终端用户看的一句话：原因 + 恢复时间 + 可操作建议。"""
        if self.kind == "budget_exhausted":
            eta = _humanize_seconds(self.retry_after)
            tail = f"，{eta}后恢复（UTC 午夜重置）" if eta else "（UTC 午夜重置）"
            return (
                f"OpenAlex 今日请求额度已用尽{tail}。"
                "在 backend/.env 配置 OPENALEX_API_KEY（免费申请）可把额度提到 10×。"
            )
        if self.kind == "rate_limited":
            return f"OpenAlex 请求过于频繁被限流，请稍后重试（{self.reason}）"
        if self.kind == "server_error":
            return f"OpenAlex 服务端暂时异常（{self.reason}），请稍后重试"
        if self.kind == "timeout":
            return f"OpenAlex 连接失败（{self.reason}），请稍后重试"
        if self.kind == "not_found":
            return f"OpenAlex 没有这条记录（{self.reason}）"
        return f"数据源 {self.source} 不可用：{self.reason}"


# 字段级信任顺序（openalex 优先）
FIELD_PRIORITY: dict[str, list[str]] = {
    "title": ["openalex", "crossref", "s2"],
    "journal": ["openalex", "crossref"],
    "publication_year": ["openalex", "crossref", "s2"],
    "publication_date": ["crossref", "openalex"],
    "abstract": ["openalex", "s2"],
    "authors": ["openalex", "crossref"],
    "issn": ["openalex", "crossref"],
    "work_type": ["openalex", "s2"],
    "cited_by_count": ["openalex", "s2"],
}


@dataclass
class FetchResult:
    """单个源的单次抓取结果。"""
    source: str
    ok: bool
    error: str | None = None
    data: dict = field(default_factory=dict)


@dataclass
class NormalizedMeta:
    """统一元数据载荷。"""
    source: str
    title: str | None = None
    authors: list[dict] = field(default_factory=list)
    journal: str | None = None
    issn: str | None = None
    openalex_source_id: str | None = None
    openalex_work_id: str | None = None
    publication_year: int | None = None
    publication_date: str | None = None
    abstract: str | None = None
    keywords: list[str] = field(default_factory=list)
    cited_by_count: int | None = None
    citation_percentile: float | None = None
    field_of_study: str | None = None
    subfield_id: str | None = None
    journal_2yr_mean: float | None = None
    work_type: str | None = None
    open_access: dict | None = None

    def to_dict(self) -> dict:
        return self.__dict__


class BaseFetcher(ABC):
    """每个数据源实现原始抓取 + 字段归一。"""

    name: str = "base"

    def __init__(
        self,
        mailto: str = "",
        timeout: float = 20.0,
        extra_headers: dict[str, str] | None = None,
    ):
        self.mailto = mailto
        self.timeout = timeout
        headers = {"User-Agent": f"lit-eval/0.1 (mailto:{mailto})"}
        # 各源自己的鉴权头（如 OpenAlex 的 Authorization: Bearer）。
        # 放在请求头而不是 URL 参数：官方文档说明可省下 query string 空间，
        # 同时避免 key 出现在日志/报错的 URL 里。
        if extra_headers:
            headers.update(extra_headers)
        self._client = httpx.AsyncClient(
            timeout=timeout,
            headers=headers,
            follow_redirects=True,
        )

    async def close(self) -> None:
        await self._client.aclose()

    @retry(
        retry=retry_if_exception_type((httpx.HTTPError, httpx.TimeoutException)),
        stop=stop_after_attempt(3),
        wait=wait_random_exponential(multiplier=0.5, max=8),
        reraise=True,
    )
    async def _request(self, url: str, params: dict | None = None) -> dict:
        resp = await self._client.get(url, params=params)
        if resp.status_code == 429 and _remaining_is_zero(resp):
            # 日配额耗尽：本地重试不可能成功，只会白烧额度 —— 立即失败，不退避。
            # 依据 OpenAlex 官方降级表（references/openalex_api_protocol.md）：
            # `429 + X-RateLimit-Remaining: 0` = Daily budget exhausted, no retry.
            raise SourceUnavailable(
                self.name,
                "今日请求额度已用尽",
                kind="budget_exhausted",
                retry_after=_retry_after_seconds(resp),
            )
        resp.raise_for_status()  # 其它错误（含突发 429）交给 tenacity 退避重试
        return resp.json()

    async def _get(self, url: str, params: dict | None = None) -> dict:
        """统一出口：网络/限流失败一律翻译成 SourceUnavailable。

        不向上抛裸 httpx 异常，好处是调用方只有一种失败形态要处理，
        而且失败原因可以带 retry-after 一路传到界面（而不是变成 500）。
        """
        try:
            return await self._request(url, params)
        except SourceUnavailable:
            raise
        except httpx.HTTPStatusError as e:
            code = e.response.status_code
            if code == 429:
                raise SourceUnavailable(
                    self.name,
                    "突发限流，退避重试后仍未恢复",
                    kind="rate_limited",
                    retry_after=_retry_after_seconds(e.response),
                ) from e
            if code == 404:
                raise SourceUnavailable(self.name, "HTTP 404 无此记录", kind="not_found") from e
            raise SourceUnavailable(self.name, f"HTTP {code}", kind="server_error") from e
        except (httpx.TimeoutException, httpx.HTTPError) as e:
            raise SourceUnavailable(
                self.name, f"网络失败（{type(e).__name__}）", kind="timeout"
            ) from e

    @abstractmethod
    async def fetch(self, doi: str) -> FetchResult:
        """按 DOI 抓取并返回统一结果。失败必须返回 FetchResult(ok=False)，不抛异常。"""

    async def fetch_by_title(self, title: str) -> FetchResult:
        """按标题兜底（仅 OpenAlex 实现；其余返回 not_ok）。"""
        return FetchResult(source=self.name, ok=False, error="title_search_not_supported")


class FetcherOrchestrator:
    """三源编排合并。"""

    def __init__(self, mailto: str = "", openalex_api_key: str = ""):
        from app.services.fetchers.crossref import CrossrefFetcher
        from app.services.fetchers.openalex import OpenAlexFetcher
        from app.services.fetchers.semantic_scholar import SemanticScholarFetcher

        self.mailto = mailto
        self.sources: dict[str, BaseFetcher] = {
            "crossref": CrossrefFetcher(mailto=mailto),
            "openalex": OpenAlexFetcher(mailto=mailto, api_key=openalex_api_key),
            "s2": SemanticScholarFetcher(mailto=mailto),
        }

    async def close(self) -> None:
        for f in self.sources.values():
            await f.close()

    def merge(self, results: dict[str, FetchResult]) -> dict:
        """按 FIELD_PRIORITY 合并多源结果为一个元数据 dict + 留痕。"""
        merged: dict = {}
        trace: list[dict] = []
        for source, res in results.items():
            trace.append(
                {"source": source, "ok": res.ok, "error": res.error, "fields": list(res.data.keys())}
            )
            if res.ok:
                for k, v in res.data.items():
                    if v in (None, "", [], {}) or k in merged:
                        continue
                    merged[k] = v

        # 字段级覆盖：按优先级重排
        final: dict = {}
        for field_name, order in FIELD_PRIORITY.items():
            for source in order:
                src_res = results.get(source)
                if src_res and src_res.ok and field_name in src_res.data:
                    val = src_res.data[field_name]
                    if val not in (None, "", []):
                        final[field_name] = val
                        break
        # 合并非优先级字段（如 issn、journal_2yr_mean 等 openalex 独有字段）
        final.update({k: v for k, v in merged.items() if k not in final})
        return {"data": final, "trace": trace, "sources_ok": sum(1 for r in results.values() if r.ok)}

    async def fetch_by_doi(self, doi: str) -> tuple[dict, list[dict], list[str]]:
        """并发抓三源，返回 (merged_data, trace, missing_fields)。"""
        import asyncio

        results: dict[str, FetchResult] = {}

        async def _run(name: str):
            try:
                results[name] = await self.sources[name].fetch(doi)
            except Exception as e:  # 单个源异常不阻断整体
                results[name] = FetchResult(source=name, ok=False, error=f"{type(e).__name__}: {e}")

        await asyncio.gather(*[_run(n) for n in self.sources])
        # 三源全失败时（网络抖动/限流）整体重试一次
        if not any(r.ok for r in results.values()):
            await asyncio.sleep(2)
            await asyncio.gather(*[_run(n) for n in self.sources])
        merged = self.merge(results)
        data, trace = merged["data"], merged["trace"]
        missing = detect_missing(data)
        return data, trace, missing

    async def fetch_by_title(self, title: str) -> tuple[dict, list[dict], list[str]]:
        """标题兜底抓取（OpenAlex 全文搜索）。"""
        res = await self.sources["openalex"].fetch_by_title(title)
        data = res.data if res.ok else {}
        trace = [{"source": "openalex", "ok": res.ok, "error": res.error, "fields": list(res.data.keys())}]
        missing = detect_missing(data) if res.ok else ["title_search_failed"]
        return data, trace, missing

def detect_missing(data: dict) -> list[str]:
    """抓取结果缺少哪些关键字段 —— 驱动 ``status=partial`` 与内容质量的缺数据判定。

    键清单不写在这里：由 ``app/models/fields.py`` 注册表里 ``required=True`` 的字段派生，
    与落库、指纹共用同一份字段知识（过去三处各写一份，漏改即静默降级）。
    """
    return [key for key in REQUIRED_KEYS if data.get(key) in (None, "", [])]
