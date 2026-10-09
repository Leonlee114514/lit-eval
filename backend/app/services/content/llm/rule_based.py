"""规则化降级 Provider：无 LLM 密钥时保证内容评估可用。

基于 abstract_structure 的四要素检测 + 结论可靠性 proxy + 引用意图关键词判定。
"""
from __future__ import annotations

import re
from typing import Any

from app.services.content.abstract_structure import (
    conclusion_reliability_proxy,
    detect,
)
from app.services.content.llm.base import LLMProvider

# 引用意图关键词（双语）
_BACKGROUND_WORDS = re.compile(r"recent|近年来|background|概述|背景|综述|in recent", re.IGNORECASE)
_METHOD_WORDS = re.compile(
    r"we (propose|proposed|develop|developed|design|present|introduce)|我们(提出|设计|构建|开发|构建了)|framework|framework",
    re.IGNORECASE,
)
_COMPARE_WORDS = re.compile(
    r"compared?|contrast|versus|vs\.?|相比之下|对比|优于|相较于|相比|outperform",
    re.IGNORECASE,
)
_THEORY_WORDS = re.compile(r"based on|依据|framework|理论|模型|基于|theory|concept|paradigm", re.IGNORECASE)


class RuleBasedProvider(LLMProvider):
    name = "rule_based"

    async def analyze(self, paper: Any, content_ctx: dict, user_topic: str) -> dict:
        abstract = paper.abstract or ""
        struct = detect(abstract)
        cr = conclusion_reliability_proxy(abstract)

        # 研究设计分：结构分 + 样本量加分
        design_score = struct.score
        if struct.sample_size:
            design_score = min(100, design_score + 10)

        # 结论可靠性：proxy 分
        rel_score = cr["score"]

        # 引用价值：意图 + 启发式置信度（关键词命中占比）
        intent, confidence = await self.classify_citation_intent(abstract, user_topic)
        cv_detail = {
            "background": "摘要含背景性描述，可作背景综述引用",
            "method": "摘要含方法/实验描述，可作方法借鉴引用",
            "compare": "摘要含对比性表述，可作结果对比引用",
            "theory": "摘要基于理论/框架，可作理论支持引用",
        }.get(intent, "背景引用")

        # 争议性：规则无法检测，标 False 并说明
        return {
            "source": self.name,
            "research_design": {
                "score": round(design_score, 1),
                "detail": (
                    f"规则化检测：问题{int(struct.has_problem)}/方法{int(struct.has_method)}/"
                    f"结果{int(struct.has_result)}/结论{int(struct.has_conclusion)}"
                    + (f"，检测到样本量 n={struct.sample_size}" if struct.sample_size else "")
                    + "（无 LLM 密钥，规则降级）"
                ),
            },
            "conclusion_reliability": {
                "score": rel_score,
                "detail": cr["basis"] + "（规则降级：结论与结果显著性记号一致性）",
            },
            "content_quality": {
                "score": round((design_score + rel_score) / 2, 1),
                "detail": "规则降级：研究设计与结论可靠性的均值",
            },
            "citation_value": {
                "intent": intent,
                "confidence": round(confidence),
                "detail": cv_detail + "（规则降级）",
            },
            "controversy": {
                "detected": False,
                "detail": "规则档无法检测撤稿/勘误，建议人工核对 Retraction Watch",
            },
        }

    async def classify_citation_intent(self, abstract: str | None, user_topic: str) -> tuple[str, float]:
        """返回 (intent, confidence 0-100)。confidence = 命中词数在总命中词数中的占比。"""
        text = abstract or ""
        if not text:
            return "background", 30.0
        counts = {
            "method": len(_METHOD_WORDS.findall(text)),
            "compare": len(_COMPARE_WORDS.findall(text)),
            "theory": len(_THEORY_WORDS.findall(text)),
            "background": len(_BACKGROUND_WORDS.findall(text)),
        }
        total = sum(counts.values())
        if total == 0:
            return "background", 30.0

        def _pick(cands: list[str]) -> tuple[str, float]:
            top = max(cands, key=lambda c: counts[c])
            if counts[top] == 0:
                return "background", 30.0
            return top, 100.0 * counts[top] / total

        if counts["method"] >= counts["background"] and counts["method"] > 0:
            return _pick(["method", "background"])
        if counts["compare"] > 0:
            return _pick(["compare", "background", "method"])
        if counts["theory"] >= counts["background"] and counts["theory"] > 0:
            return _pick(["theory", "background"])
        if counts["background"] > 0:
            return "background", 100.0 * counts["background"] / total
        return "background", 30.0
