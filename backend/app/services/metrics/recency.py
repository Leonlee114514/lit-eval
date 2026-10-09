"""活跃度权重 v5：老文献（发表>5年）按近5年被引在同龄段分位；新文献（≤5年）按前沿度四子信号。

分支 A · 老文献（age > 5）：同龄近5年被引对数分位
    score = percentile_in(band_dist_ln, ln(1 + cited_by_5yr))
    band_dist_ln = 该校准段化学论文近5年被引的 ln(1+x) 升序数组（recent_citation_by_age.json）
    近5年被引缺失 / 段分布未校准 → 中性 50（缺数据=未知，v4 一致）
    is_classic 且 age>5 → 保底 classic_floor（默认 75，scoring_rules 可调）

分支 B · 新文献（age ≤ 5）：前沿度模块
    frontier = Σ(可用信号 w_i·v_i) / Σ(可用 w_i)
    score    = clamp(frontier − 方法过时惩罚, 0, 100)
    - age_freshness（0.40）：55 + 45×(1−age/5)，新文献天然前沿（age0=100 / age5=55 平滑交棒）
    - hot_topic（0.30）：标题/摘要 vs 热点词表（TF-IDF 余弦 + 语义余弦取 max）
    - ref_freshness（0.30）：参考文献中 [发表年−2, 发表年] 占比 ×100
    - 方法过时惩罚：methods_blacklist 规则（pattern+context 双命中）−15/条，封顶 30

v5 取代 v3/v4 的 e^(-λt) 指数衰减 + 经典冻结（绝对被引≥P90 会误杀 392 被引的
奠基性老文献——1981 的 Dieterich 论文被引百分位 100 却时效 0.2）。新口径是"引用衰减
比同龄同行慢多少"，老经典天然高分、已死老文献判中位、新文献看前沿度。
"""
from __future__ import annotations

import math
import re
from datetime import date

from app.config import get_settings
from app.services import distributions
from app.services.rules import get_rules


def compute(
    paper,
    *,
    cited_by_5yr: int | None = None,
    ref_years: list[int] | None = None,
    fulltext: str | None = None,
) -> dict:
    """返回 {score, basis, data_status, branch, ...}。

    keyword-only 参数：近5年被引 / 参考引用年份 / 全文（新文献前沿度用）。
    """
    if paper.publication_year is None:
        return {"score": 50.0, "basis": "无发表年份→中性", "data_status": "missing", "branch": None}
    age = date.today().year - paper.publication_year
    if age <= 5:
        return _branch_frontier(paper, age, ref_years, fulltext)
    return _branch_cohort(paper, age, cited_by_5yr)


def _branch_cohort(paper, age: int, cited_by_5yr: int | None) -> dict:
    """分支 A：老文献 → 同龄段近5年被引对数分位。"""
    rules = get_rules()
    recency = rules.get("recency", {})
    cited_5yr = cited_by_5yr if cited_by_5yr is not None else getattr(paper, "cited_by_5yr", None)
    band = distributions.age_band(age)

    if cited_5yr is None:
        return {
            "score": 50.0,
            "basis": f"发表 {age} 年前（{band} 段），近5年被引缺失→中性",
            "data_status": "missing",
            "branch": "cohort",
            "band": band,
        }
    dist = distributions.recent_citation_band_for(age)
    if not dist:
        return {
            "score": 50.0,
            "basis": f"发表 {age} 年前（{band} 段），缺该段校准分布→中性",
            "data_status": "missing",
            "branch": "cohort",
            "band": band,
        }
    pct = distributions.percentile_in(dist, math.log(1 + cited_5yr)) or 50.0

    score = pct
    floor = float(recency.get("classic_floor", 75))
    floored = False
    if bool(getattr(paper, "is_classic", False)) and score < floor:
        score = floor
        floored = True
    basis = f"发表 {age} 年前（{band} 段），近5年被引 {cited_5yr} 在同段对数分布分位≈{pct:.1f}"
    if floored:
        basis += f"；手动标记经典，保底 {floor:.0f}"
    return {
        "score": round(score, 1),
        "basis": basis,
        "data_status": "ok",
        "branch": "cohort",
        "band": band,
        "percentile": round(pct, 1),
        "classic_floor": floored,
    }


def _branch_frontier(paper, age: int, ref_years, fulltext) -> dict:
    """分支 B：新文献 → 前沿度模块（新鲜度 + 热点主题 + 引用新鲜度 − 方法过时惩罚）。"""
    rules = get_rules()
    recency = rules.get("recency", {})
    weights = recency.get(
        "frontier_weights", {"age_freshness": 0.40, "hot_topic": 0.30, "ref_freshness": 0.30}
    )

    age_fresh = min(100.0, max(55.0, 55.0 + 45.0 * (1 - age / 5.0)))
    hot = _hot_topic(paper, fulltext)
    ref_fresh = _ref_freshness(ref_years, paper.publication_year)

    used: list[tuple[str, float, float]] = [
        ("age_freshness", float(weights["age_freshness"]), age_fresh)
    ]
    if hot is not None:
        used.append(("hot_topic", float(weights["hot_topic"]), hot))
    if ref_fresh is not None:
        used.append(("ref_freshness", float(weights["ref_freshness"]), ref_fresh))

    denom = sum(w for _, w, _ in used)
    frontier = sum(w * v for _, w, v in used) / denom if denom else 50.0

    penalty, pen_note = _method_penalty(
        fulltext, recency.get("methods_blacklist", []), float(recency.get("method_penalty_cap", 30))
    )
    score = max(0.0, min(100.0, frontier - penalty))

    parts = [f"新鲜度 {age_fresh:.0f}"]
    if hot is not None:
        parts.append(f"热点 {hot:.0f}")
    if ref_fresh is not None:
        parts.append(f"引用新鲜 {ref_fresh:.0f}")
    used_keys = {k for k, _, _ in used}
    missing = [k for k in ("hot_topic", "ref_freshness") if k not in used_keys]
    basis = f"新文献前沿度（{age}岁，{'/'.join(parts)}）"
    if missing:
        basis += "；缺失信号按可用权重重归一化（" + "、".join(missing) + "）"
    if pen_note:
        basis += pen_note
    return {
        "score": round(score, 1),
        "basis": basis,
        "data_status": "ok",
        "branch": "frontier",
        "age": age,
    }


def _hot_topic(paper, fulltext: str | None) -> float | None:
    """热点主题匹配：标题/摘要 vs 热点词表，TF-IDF 余弦 + 语义余弦取 max。"""
    vocab = distributions.hot_keywords()
    terms = vocab.get("terms") or []
    if not terms:
        return None
    text = f"{getattr(paper, 'title', '') or ''} {getattr(paper, 'abstract', '') or ''}".strip().lower()
    if not text and fulltext:
        text = fulltext[:3000].lower()
    if not text:
        return None
    tfidf = _tfidf_hot(text, terms)
    sem = _semantic_hot(text, vocab.get("top_text") or "")
    return tfidf if sem is None else max(tfidf, sem)


def _tfidf_hot(text: str, terms: list[dict]) -> float:
    """二进制 TF-IDF 余弦：paper 词向量（热点词表内词 tf=1）与热点 idf 向量余弦。"""
    idf = {t.get("term"): float(t.get("idf")) for t in terms if t.get("term")}
    present = [term for term in idf if term in text]
    if not idf or not present:
        return 0.0
    num = sum(idf[t] for t in present)
    den = math.sqrt(len(present)) * math.sqrt(sum(v * v for v in idf.values()))
    if den <= 0:
        return 0.0
    return min(100.0, num / den * 100)


def _semantic_hot(text: str, top_text: str) -> float | None:
    """语义余弦：fastembed/SBERT 可用时，paper 文本 vs 热点词表 top_text。"""
    if not top_text:
        return None
    settings = get_settings()
    from app.services.content.relevance import _clean

    if settings.use_fastembed:
        from app.services.content.relevance import _get_fastembed

        model = _get_fastembed()
        if model:
            import numpy as np

            # fastembed 0.8 的 embed() 返回 list[ndarray]（旧版为生成器）→ list() 兼容两种
            va, vb = list(model.embed([_clean(top_text), _clean(text)]))[:2]
            cos = float((va @ vb) / (np.linalg.norm(va) * np.linalg.norm(vb)))
            return max(0.0, min(1.0, cos)) * 100
    if settings.use_sbert:
        from app.services.content.relevance import _get_sbert

        model = _get_sbert()
        if model:
            from numpy.linalg import norm

            a, b = model.encode([_clean(top_text), _clean(text)])
            cos = float((a @ b) / (norm(a) * norm(b)))
            return max(0.0, min(1.0, cos)) * 100
    return None


def _ref_freshness(ref_years, pub_year: int | None) -> float | None:
    """引用新鲜度：参考文献中 [发表年−2, 发表年] 占比 ×100；无引用年份 → None。"""
    if not ref_years or pub_year is None:
        return None
    years = [y for y in ref_years if y is not None]
    if not years:
        return None
    lo, hi = pub_year - 2, pub_year
    fresh = sum(1 for y in years if lo <= y <= hi)
    return fresh / len(years) * 100


def _method_penalty(fulltext: str | None, blacklist: list[dict], cap: float) -> tuple[float, str]:
    """方法过时检测：blacklist 规则（pattern + context_pattern 双命中）→ −penalty/条，封顶 cap。"""
    if not fulltext:
        return 0.0, ""
    total, notes = 0.0, []
    for rule in blacklist or []:
        pat, ctx = rule.get("pattern"), rule.get("context_pattern")
        if not pat or not ctx:
            continue
        try:
            if re.search(pat, fulltext, re.I) and re.search(ctx, fulltext, re.I):
                hits = len(list(re.finditer(pat, fulltext, re.I)))
                total += hits * float(rule.get("penalty", 15))
                notes.append(rule.get("description") or pat)
        except re.error:
            continue  # JSON 正则转义错误 → 静默跳过该规则
    total = min(total, cap)
    if total > 0:
        return total, f"；方法过时惩罚 -{total:.0f}（{'、'.join(notes)}）"
    return 0.0, ""
