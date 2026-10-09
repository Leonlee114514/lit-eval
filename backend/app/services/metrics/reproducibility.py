"""可重复性权重：基于 TOP Guidelines 的五指标加权 + 惩罚项。

w₅ = Σ(weight_i × indicator_i)
指标：数据可用性(0.25) 代码/协议公开(0.25) 补充材料(0.20) 预注册(0.15) 利益冲突声明(0.15)

惩罚项（从 w₅ 扣除）：
- 撤稿标记 is_retracted → w₅ = 0
- 商业资助且无 COI 声明 → -0.15
- 关键实验 n=1 → -0.10

时代意识（era_neutral_year，默认 2000）：数据可用性声明/代码仓/预注册/COI 声明是
2010s 后才普及的现代学术实践。用它们去罚 1981 年的奠基论文是"时代错位"（Dieterich
诊断三件套之一）。老文献（publication_year < era_neutral_year）若五项声明全无证据
→ 返回中性 50 而非 0，basis 注明"非当时惯例"；有证据则照常计分。

设计哲学：w₅ 是对前四个"间接指标"的纠偏——顶刊高被引但缺乏透明度，结论应降权。
"""
from __future__ import annotations

import re

from app.services.rules import get_rules

# ---- 指标正则（中英双语） ----
_DATA_AVAIL_RE = re.compile(
    r"data\s+availability|数据可用|data\s+and\s+materials|code\s+availability|可供|公开数据",
    re.IGNORECASE,
)
_CODE_REPO_RE = re.compile(
    r"(?:https?://)?(?:www\.)?(?:github|gitee|gitlab|zenodo|osf\.io)\.|"
    r"code\.oss|repository\s+available|code\s+available\s+at",
    re.IGNORECASE,
)
_SUPPLEMENT_RE = re.compile(
    r"supplement(?:ary|al)?|supporting\s+information|补充材料|附件",
    re.IGNORECASE,
)
_PREREG_RE = re.compile(
    r"preregist|pre-regist|registered\s+(?:trial|protocol)|trial\s+registration|"
    r"clinicaltrials\.gov|预注册|试验注册|研究方案注册",
    re.IGNORECASE,
)
_COI_RE = re.compile(
    r"conflict\s+of\s+interest|competing\s+interest|利益冲突|潜在利益冲突|"
    r"declare\s+no\s+(?:conflict|competing|financial)",
    re.IGNORECASE,
)
_COMMERCIAL_RE = re.compile(
    r"industry[- ]?sponsor|industry[- ]?funded|commercial\s+funding|"
    r"corporate\s+sponsor|company\s+funded|公司资助|企业资助|商业资助",
    re.IGNORECASE,
)
_N1_RE = re.compile(
    r"\bn\s*[=:：]\s*1\b(?=\s*(?:sample|subject|participant|animal|patient|mouse|rat|cell|unit|"
    r"trial|case|人|例|只|头|样本|被试|重复|组))|样本量\s*[:：]?\s*1\b"
)


def compute(paper, text: str | None = None) -> dict:
    rules = get_rules()
    weights = rules["reproducibility"]["weights"]
    penalties = rules["reproducibility"]["penalties"]

    data = bool(paper.has_data_availability_stmt)
    code = bool(paper.has_code_repo)
    supp = bool(paper.has_supplement)
    prereg = False
    coi = False
    commercial = False
    n1 = False

    # 撤稿 → 直接归零（最高优先级，先于任何中性处理）
    if getattr(paper, "is_retracted", False):
        return {
            "score": 0.0,
            "flags": {
                "data_availability": data,
                "code_repo": code,
                "supplement": supp,
                "preregistration": False,
                "coi_statement": False,
            },
            "basis": "该文献已被标记撤稿，可重复性归零",
            "penalties": ["撤稿"],
            "data_status": "ok",
        }

    # 缺数据中性化：无全文 且 三个 has_* 标志全为 None（从未判断过）
    # → 中性 50 而非 0（"没有数据来源"≠"不可重复"，只是未评估）。
    # 与作者/期刊/被引缺数据的"中性 50"原则保持一致。
    has_any_flag = any(
        getattr(paper, f, None) is not None
        for f in ("has_data_availability_stmt", "has_code_repo", "has_supplement")
    )
    if not text and not has_any_flag:
        return {
            "score": 50.0,
            "flags": {
                "data_availability": False,
                "code_repo": False,
                "supplement": False,
                "preregistration": False,
                "coi_statement": False,
            },
            "basis": "无全文且无数据声明，未评估（中性）",
            "penalties": [],
            "data_status": "missing",
        }

    if text:
        data = data or bool(_DATA_AVAIL_RE.search(text))
        code = code or bool(_CODE_REPO_RE.search(text))
        supp = supp or bool(_SUPPLEMENT_RE.search(text))
        prereg = bool(_PREREG_RE.search(text))
        coi = bool(_COI_RE.search(text))
        commercial = bool(_COMMERCIAL_RE.search(text))
        n1 = bool(_N1_RE.search(text))

    flags = {
        "data_availability": data,
        "code_repo": code,
        "supplement": supp,
        "preregistration": prereg,
        "coi_statement": coi,
    }

    # 时代意识：老文献无任何现代可重复性声明 → 中性 50（时代错位保护）
    # 数据/代码/预注册/COI 声明是 2010s 后的学术惯例；拿 2024 标准罚 1981 的
    # 奠基论文是系统性不公平（Dieterich 诊断）。有证据（如补充材料）则照常计分。
    era_neutral_year = rules["reproducibility"].get("era_neutral_year", 2000)
    year = paper.publication_year
    if year is not None and year < era_neutral_year and not any(flags.values()):
        return {
            "score": 50.0,
            "flags": flags,
            "basis": (
                f"{year} 年发表：数据/代码/预注册/COI 声明非当时学术惯例，"
                f"无可重复性证据按中性 50 计，而非按现代标准罚 0"
            ),
            "penalties": [],
            "data_status": "ok",
            "era_neutral": True,
        }

    base = 100 * (
        weights["data"] * data
        + weights["code"] * code
        + weights["supplement"] * supp
        + weights["preregistration"] * prereg
        + weights["coi"] * coi
    )

    penalties_applied: list[str] = []
    if commercial and not coi:
        base -= 100 * penalties["commercial_without_coi"]
        penalties_applied.append("商业资助且无利益冲突声明 -15")
    if n1:
        base -= 100 * penalties["n1"]
        penalties_applied.append("关键实验 n=1 -10")

    score = round(max(0.0, base), 1)
    present = [k for k, v in flags.items() if v]
    basis = "、".join(present) or "无可重复性证据"
    if penalties_applied:
        basis += " | 惩罚: " + "、".join(penalties_applied)

    return {
        "score": score,
        "flags": flags,
        "basis": basis,
        "penalties": penalties_applied,
        "data_status": "ok",
    }
