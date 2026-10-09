"""LLM Provider 抽象：统一出参，三实现（OpenAI 兼容 / Ollama / 规则降级）。

出参形状（dict）：
{
  "source": "openai_compat"|"ollama"|"rule_based",
  "research_design":      {"score": 0-100, "detail": str},
  "conclusion_reliability":{"score": 0-100, "detail": str},
  "content_quality":      {"score": 0-100, "detail": str},
  "citation_value":       {"intent": str, "detail": str},
  "controversy":          {"detected": bool, "detail": str},
}
"""
from __future__ import annotations

import json
import logging
from abc import ABC, abstractmethod
from typing import Any

logger = logging.getLogger(__name__)


class LLMProvider(ABC):
    name: str = "base"

    @abstractmethod
    async def analyze(self, paper: Any, content_ctx: dict, user_topic: str) -> dict:
        """深度评估：研究设计、结论可靠性、引用价值、争议性。"""

    @abstractmethod
    async def classify_citation_intent(self, abstract: str | None, user_topic: str) -> tuple[str, float]:
        """引用意图：返回 (intent, confidence 0-100)。intent ∈ background|method|compare|theory。"""


def _default_assessment(source: str, reason: str = "") -> dict:
    return {
        "source": source,
        "research_design": {"score": 50, "detail": reason or "未分析"},
        "conclusion_reliability": {"score": 50, "detail": reason or "未分析"},
        "content_quality": {"score": 50, "detail": reason or "未分析"},
        "citation_value": {"intent": "background", "confidence": 50, "detail": reason or "未分析"},
        "controversy": {"detected": False, "detail": ""},
    }


def get_llm_provider(cfg) -> LLMProvider:
    """工厂：按配置选择 provider。无密钥/未知类型 → 规则降级。"""
    from app.services.content.llm.openai_compat import OpenAICompatProvider
    from app.services.content.llm.rule_based import RuleBasedProvider

    provider = (cfg.llm_provider or "none").strip().lower()
    if provider == "openai_compat" and cfg.llm_api_key:
        return OpenAICompatProvider(
            base_url=cfg.llm_base_url.rstrip("/"),
            api_key=cfg.llm_api_key,
            model=cfg.llm_model,
        )
    if provider == "ollama":
        try:
            from app.services.content.llm.ollama import OllamaProvider

            return OllamaProvider(base_url=cfg.llm_base_url or "http://localhost:11434")
        except Exception as e:
            logger.warning("Ollama provider 初始化失败，降级规则档: %s", e)

    logger.info("LLM_PROVIDER=%r 无有效配置，使用规则化降级档", cfg.llm_provider)
    return RuleBasedProvider()


def parse_json_response(text: str) -> dict | None:
    """宽容解析 LLM 返回的 JSON（去 markdown 代码围栏）。"""
    if not text:
        return None
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[-1]
        if cleaned.endswith("```"):
            cleaned = cleaned[:-3]
        cleaned = cleaned.strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass
    # 提取第一个 {...}
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start != -1 and end > start:
        try:
            return json.loads(cleaned[start : end + 1])
        except json.JSONDecodeError:
            return None
    return None
