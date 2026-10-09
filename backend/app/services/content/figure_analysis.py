"""PDF 图表质量检测：从全文提取图表题注，统计图表数量并区分原始数据图 vs 示意图。

纯规则、零模型依赖。用正则定位 "Figure N" / "Fig. N" / "图 N" 图注与
"Table N" / "表 N" 表注，按题注关键词把图分类为：
- 原始数据图（data）：plot / graph / curve / scatter / spectrum / XRD / FTIR /
  SEM / TEM 等实测信号与显微图
- 示意图（schematic）：schematic / diagram / illustration / mechanism / workflow
  等概念图示

仅在全文可用（PDF 上传）时才有意义；无全文返回 None。评分口径：
- 可分类图中"原始数据图"占比 → 实证性信号（全示意图 → 0，全数据图 → 100）
- 全部无法分类或无图 → score=None（不参与内容分，避免无原料惩罚）
"""
from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)

# 图注/表注标记：Figure / Fig. / 图 / Table / 表
_FIG_MARK = re.compile(r"\b(?:Figure|Fig\.?|图)\s*(\d+)(?:[a-z])?\b", re.IGNORECASE)
_TAB_MARK = re.compile(r"\b(?:Table|Tab\.?|表)\s*(\d+)\b", re.IGNORECASE)
# 任意后续标记（找"下一张图的起点"用）
_NEXT_MARK = re.compile(r"\b(?:Figure|Fig\.?|图|Table|Tab\.?|表)\s*\d+", re.IGNORECASE)

# 原始数据图题注特征词（tokens）
_DATA_TOKENS = {
    "plot", "graph", "curve", "scatter", "histogram", "spectrum", "spectra",
    "xrd", "ftir", "ft-ir", "raman", "sem", "tem", "afm", "tga", "dsc",
    "absorbance", "transmittance", "intensity", "correlation", "dependence",
    "distribution", "micrograph", "stress", "strain", "impedance", "voltammogram",
    "chromatogram", "bode", "nyquist", "frequency", "potentiostatic",
}
# 示意图题注特征词（tokens）
_SCHEMATIC_TOKENS = {
    "schematic", "diagram", "illustration", "representation", "overview",
    "mechanism", "workflow", "layout", "architecture", "depiction", "cartoon",
    "flowchart", "illustrating", "depicting", "scheme",
}


def analyze(text: str | None) -> dict | None:
    """从全文文本提取图表题注并分类。无全文/无文本返回 None。"""
    if not text or not text.strip():
        return None

    figures = _collect_figures(text)
    if not figures:
        return {
            "total_figures": 0,
            "tables": 0,
            "data_figures": 0,
            "schematic_figures": 0,
            "unclassified_figures": 0,
            "score": None,
            "basis": "未检测到图表题注",
            "figures": [],
        }

    data_count = sum(1 for f in figures if f["kind"] == "data")
    schem_count = sum(1 for f in figures if f["kind"] == "schematic")
    unknown_count = len(figures) - data_count - schem_count
    tables = _count_tables(text)

    classifiable = data_count + schem_count
    score = round(100 * data_count / classifiable) if classifiable else None

    basis_parts = [f"图 {len(figures)} 张"]
    if data_count:
        basis_parts.append(f"原始数据图 {data_count}")
    if schem_count:
        basis_parts.append(f"示意图 {schem_count}")
    if unknown_count:
        basis_parts.append(f"未分类 {unknown_count}")
    if tables:
        basis_parts.append(f"表 {tables}")
    basis = "、".join(basis_parts)
    if score is not None:
        basis += f" → 数据图占比 {score}/100"

    return {
        "total_figures": len(figures),
        "tables": tables,
        "data_figures": data_count,
        "schematic_figures": schem_count,
        "unclassified_figures": unknown_count,
        "score": score,
        "basis": basis,
        "figures": figures,
    }


def _collect_figures(text: str) -> list[dict]:
    """提取唯一图号及其最长题注（正文里的 in-text 引用是短句，会被真实题注覆盖）。"""
    best: dict[str, str] = {}
    for m in _FIG_MARK.finditer(text):
        num = m.group(1)
        caption = _caption_after(text, m.end())
        if len(caption) > len(best.get(num, "")):
            best[num] = caption

    out: list[dict] = []
    for num, caption in best.items():
        kind = _classify(caption)
        out.append({"number": num, "caption": caption[:150], "kind": kind})
    out.sort(key=lambda f: int(f["number"]) if f["number"].isdigit() else 0)
    return out


def _caption_after(text: str, start: int) -> str:
    """图号后的题注：到下一张图/表标记或约 220 字符，切到句号。

    注意图号后常紧跟分隔符（"Figure 1. Caption"），需先剥掉分隔符再切句，
    否则图号后的句点会被误当句边界、截掉整个题注。
    """
    tail = text[start:]
    tail = tail.lstrip(" \t\n:：-.·—–")
    nxt = _NEXT_MARK.search(tail)
    end = nxt.start() if nxt else min(220, len(tail))
    cap = tail[:end]
    cap = re.split(r"[.。]\s+", cap, maxsplit=1)[0]
    cap = cap.strip(" \t\n:：-")
    return cap


def _tokens(caption: str) -> set[str]:
    """题注分词：按非字母切分（含连字符拆分），并做基础复数归一（-s/-es）。"""
    raw = re.findall(r"[a-z][a-z0-9]*", caption.lower())
    tokens = set()
    for w in raw:
        tokens.add(w)
        if w.endswith("ies") and len(w) > 4:
            tokens.add(w[:-3] + "y")
        elif w.endswith("es") and len(w) > 3:
            tokens.add(w[:-2])
        elif w.endswith("s") and len(w) > 3:
            tokens.add(w[:-1])
    return tokens


def _classify(caption: str) -> str:
    """数据图优先；无数据词但有示意图词 → schematic；否则 unknown。"""
    tokens = _tokens(caption)
    has_data = bool(tokens & _DATA_TOKENS)
    has_schematic = bool(tokens & _SCHEMATIC_TOKENS)
    if has_data:
        return "data"
    if has_schematic:
        return "schematic"
    return "unknown"


def _count_tables(text: str) -> int:
    return len(set(_TAB_MARK.findall(text)))
