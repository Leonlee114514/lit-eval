"""摘要结构完整性检测：纯规则、零模型依赖，中英双语正则。

四要素：研究问题 / 方法（含样本量）/ 结果（含显著性）/ 结论。
降级档的核心——无 LLM 密钥时仍能给出内容质量信号。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

# ---- 研究问题 ----
_PROBLEM_RE = re.compile(
    r"we\s+(study|investigate|examine|aim|explore|address|ask)|"
    r"this\s+(paper|work|study|article)\s+(aims?|investigates?|addresses?|examines?)|"
    r"the\s+(purpose|objective|goal)\s+(of\s+this\s+(paper|study|work))|"
    r"研究|目的|目标|探讨|考察|解决(了)?|问题",
    re.IGNORECASE,
)
_QUESTION_RE = re.compile(r"[?？]")

# ---- 方法 / 样本量 ----
_METHOD_RE = re.compile(
    r"we\s+(used|conducted|performed|employed|propose|proposed|developed)|"
    r"experimental|experimentally|method|methodology|approach|framework|"
    r"experiment|synthesized|synthesis|制备|合成|实验|方法|采用|构建|提出",
    re.IGNORECASE,
)
_SAMPLE_RE = re.compile(
    r"\bn\s*[=:：]\s*\d+|"
    r"participants?\s*[=:：]?\s*\d+|"
    r"subjects?\s*[=:：]?\s*\d+|"
    r"samples?\s*[=:：]?\s*\d+|"
    r"样本量\s*[:：]?\s*\d+|"
    r"被试\s*\d+",
    re.IGNORECASE,
)

# ---- 结果 / 显著性 ----
_RESULT_RE = re.compile(
    r"we\s+found|results?\s+show|results?\s+indicat|findings?\s+(show|indicate|demonstrate)|"
    r"demonstrated|significant|significantly|enhanced|improved|"
    r"\bp\s*[<≤=]\s*0\.0\d|"
    r"结果(表明|显示|提示)|显著|提升|改善|降低",
    re.IGNORECASE,
)

# ---- 结论 ----
_CONCLUSION_RE = re.compile(
    r"we\s+conclude|in\s+conclusion|our\s+findings?\s+suggest|we\s+demonstrate|"
    r"taken\s+together|overall[,:]|these\s+results\s+provide|"
    r"综上|总而言之|结论|我们(表明|证明|认为)|本文(表明|证明|提供)",
    re.IGNORECASE,
)

# ---- 显著性记号提取（结论可靠性交叉验证用） ----
_SIGNIFICANCE_TOKEN_RE = re.compile(
    r"p\s*[<≤=]\s*0?\.?\d+|"
    r"-?\d+\.\d+\s*±\s*-?\d+\.\d+|"
    r"-?\d+\.\d+\s*\(±\s*-?\d+\.\d+\)|"
    r"\d+(?:\.\d+)?\s*%|"
    r"95%\s*CI",
    re.IGNORECASE,
)


@dataclass
class StructureResult:
    has_problem: bool = False
    has_method: bool = False
    has_result: bool = False
    has_conclusion: bool = False
    sample_size: int | None = None
    evidence_snippet: str = ""
    score: float = 0.0
    sentences: dict = field(default_factory=dict)  # 分句供前端展示


def _split_sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.;:!?。；：！？])\s+", text.strip())
    return [p for p in parts if p]


def detect(text: str | None) -> StructureResult:
    if not text or not text.strip():
        return StructureResult(score=0.0, evidence_snippet="无摘要内容")

    sentences = _split_sentences(text)
    joined = text

    has_problem = bool(_PROBLEM_RE.search(joined)) or any(
        _QUESTION_RE.search(s) for s in sentences
    )
    has_method = bool(_METHOD_RE.search(joined))
    has_result = bool(_RESULT_RE.search(joined))
    has_conclusion = bool(_CONCLUSION_RE.search(joined))

    sample = _extract_sample_size(joined)
    evidence = _build_evidence(joined)

    score = round(100 * (has_problem + has_method + has_result + has_conclusion) / 4, 1)
    return StructureResult(
        has_problem=has_problem,
        has_method=has_method,
        has_result=has_result,
        has_conclusion=has_conclusion,
        sample_size=sample,
        evidence_snippet=evidence,
        score=score,
        sentences={"count": len(sentences)},
    )


def _extract_sample_size(text: str) -> int | None:
    m = _SAMPLE_RE.search(text)
    if not m:
        return None
    digits = re.findall(r"\d+", m.group(0))
    return int(digits[0]) if digits else None


def _build_evidence(text: str) -> str:
    """提取与显著性相关的证据片段。"""
    for s in _split_sentences(text):
        if _RESULT_RE.search(s) and _SIGNIFICANCE_TOKEN_RE.search(s):
            return s[:300]
    for s in _split_sentences(text):
        if _RESULT_RE.search(s):
            return s[:300]
    return ""


def conclusion_reliability_proxy(text: str | None) -> dict:
    """降级档结论可靠性：结论句与结果句的数值/显著性记号是否一致。"""
    if not text:
        return {"score": 50.0, "basis": "无摘要", "matched_tokens": []}
    sentences = _split_sentences(text)
    result_sentences = [s for s in sentences if _RESULT_RE.search(s)]
    conclusion_sentences = [s for s in sentences if _CONCLUSION_RE.search(s)]

    def tokens(ss: list[str]) -> set[str]:
        out: set[str] = set()
        for s in ss:
            out.update(t.lower() for t in _SIGNIFICANCE_TOKEN_RE.findall(s))
        return out

    result_tokens = tokens(result_sentences)
    conclusion_tokens = tokens(conclusion_sentences)

    if not result_tokens:
        return {"score": 50.0, "basis": "结果句无显著性记号", "matched_tokens": []}
    if not conclusion_sentences:
        return {"score": 55.0, "basis": "无明确结论句", "matched_tokens": []}

    matched = sorted(result_tokens & conclusion_tokens)
    ratio = len(matched) / len(result_tokens)
    score = round(50 + 50 * ratio, 1)
    basis = f"结果句 {len(result_tokens)} 个记号，结论句匹配 {len(matched)} 个"
    return {"score": score, "basis": basis, "matched_tokens": matched}
