"""OpenAI 兼容 Provider：DeepSeek / OpenRouter / 任何 OpenAI 协议端点。

用 httpx 手写 chat/completions 调用，不依赖 openai SDK。
"""
from __future__ import annotations

import logging
from typing import Any

import httpx
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_random_exponential

from app.services.content.llm.base import LLMProvider, _default_assessment, parse_json_response

logger = logging.getLogger(__name__)


def _is_retryable(exc: BaseException) -> bool:
    """429/5xx/超时 → 可重试；4xx 业务错误（如 400）不重试。"""
    if isinstance(exc, httpx.TimeoutException):
        return True
    if isinstance(exc, httpx.HTTPStatusError):  # resp.raise_for_status() 抛出
        return exc.response.status_code in (429, 500, 502, 503, 504)
    return False


_PROMPT = """你是文献质量评估专家。请评估以下论文是否值得引用，输出严格 JSON，不要额外文字。

论文信息：
标题: {title}
期刊: {journal} ({year})
摘要: {abstract}
用户研究主题: {user_topic}
全文片段(截断): {fulltext_snippet}

输出 JSON 结构（分数均为 0-100 整数）：
{{
  "research_design": {{
    "score": 0-100,
    "detail": "评估：对照组设置、样本量、统计方法、盲法是否充分，30字内"
  }},
  "conclusion_reliability": {{
    "score": 0-100,
    "detail": "对比摘要结论与结果数据，判断是否夸大，30字内"
  }},
  "content_quality": {{
    "score": 0-100,
    "detail": "综合研究设计严谨性、方法充分性、结果与结论一致性、数据/可复现信号给出内容质量分，30字内"
  }},
  "citation_value": {{
    "intent": "background|method|compare|theory",
    "confidence": 0-100,
    "detail": "适合作为背景综述/方法借鉴/结果对比/理论支持的引用，20字内"
  }},
  "controversy": {{
    "detected": false,
    "detail": "是否发现争议、勘误、撤稿信号，无则空串"
  }}
}}"""

_INTENT_PROMPT = """判断这篇论文对"用户研究主题"而言最可能被引用的意图类型，只输出一个单词：
- background: 提供背景综述
- method: 提供方法借鉴
- compare: 提供结果/数据对比
- theory: 提供理论/概念支持
论文摘要: {abstract}
用户主题: {user_topic}
只输出 background/method/compare/theory 之一。"""


class OpenAICompatProvider(LLMProvider):
    name = "openai_compat"

    def __init__(self, base_url: str, api_key: str, model: str, timeout: float = 60.0):
        self.base_url = base_url
        self.api_key = api_key
        self.model = model
        self._client = httpx.AsyncClient(timeout=timeout)

    async def close(self) -> None:
        await self._client.aclose()

    @retry(
        retry=retry_if_exception(_is_retryable),
        stop=stop_after_attempt(4),
        wait=wait_random_exponential(multiplier=1, max=30),
        reraise=True,  # 全部失败由 analyze() 捕获降级规则档
    )
    async def _chat(self, user_prompt: str, temperature: float = 0.2) -> str:
        resp = await self._client.post(
            f"{self.base_url}/chat/completions",
            headers={"Authorization": f"Bearer {self.api_key}"},
            json={
                "model": self.model,
                "messages": [{"role": "user", "content": user_prompt}],
                "temperature": temperature,
            },
        )
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"]

    async def analyze(self, paper: Any, content_ctx: dict, user_topic: str) -> dict:
        try:
            prompt = _PROMPT.format(
                title=paper.title or "未知",
                journal=paper.journal or "未知",
                year=paper.publication_year or "未知",
                abstract=(paper.abstract or "无摘要")[:2000],
                user_topic=user_topic or "未指定",
                fulltext_snippet=(content_ctx.get("fulltext") or "")[:3000],
            )
            text = await self._chat(prompt)
            parsed = parse_json_response(text)
            if not parsed:
                return _default_assessment(self.name, "LLM 返回无法解析")
            return self._shape(parsed)
        except Exception as e:
            logger.warning("LLM 深度评估失败，降级规则档: %s", e)
            from app.services.content.llm.rule_based import RuleBasedProvider

            return await RuleBasedProvider().analyze(paper, content_ctx, user_topic)

    async def classify_citation_intent(self, abstract: str | None, user_topic: str) -> tuple[str, float]:
        try:
            text = await self._chat(
                _INTENT_PROMPT.format(
                    abstract=(abstract or "无摘要")[:1500], user_topic=user_topic or "未指定"
                ),
                temperature=0.0,
            )
            intent = text.strip().lower().split()[0] if text.strip() else "background"
            intent = intent if intent in ("background", "method", "compare", "theory") else "background"
            # 模型明确只输出单词意图 → 置信度 90（给模型确定性留余量）
            return intent, 90.0
        except Exception:
            return "background", 30.0

    def _shape(self, parsed: dict) -> dict:
        rd = parsed.get("research_design", {})
        cr = parsed.get("conclusion_reliability", {})
        cq = parsed.get("content_quality", {})
        cv = parsed.get("citation_value", {})
        ct = parsed.get("controversy", {})
        return {
            "source": self.name,
            "research_design": {
                "score": _num(rd.get("score"), 50),
                "detail": str(rd.get("detail", ""))[:300],
            },
            "conclusion_reliability": {
                "score": _num(cr.get("score"), 50),
                "detail": str(cr.get("detail", ""))[:300],
            },
            "content_quality": {
                "score": _num(cq.get("score"), 50),
                "detail": str(cq.get("detail", ""))[:300],
            },
            "citation_value": {
                "intent": cv.get("intent", "background") if cv.get("intent") in ("background", "method", "compare", "theory") else "background",
                "confidence": _num(cv.get("confidence"), 50),
                "detail": str(cv.get("detail", ""))[:200],
            },
            "controversy": {
                "detected": bool(ct.get("detected", False)),
                "detail": str(ct.get("detail", ""))[:300],
            },
        }


def _num(v: Any, default: float) -> float:
    try:
        return max(0.0, min(100.0, float(v)))
    except (TypeError, ValueError):
        return default
