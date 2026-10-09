"""期刊权重：手填双轨取高；无手填时用对数正态 z-score 映射。

w₁ = max(P_JCR, P_CAS)   （手填分区，用户可控）
- P_JCR：JCR 分区映射（Q1=90, Q2=70, Q3=45, Q4=20）
- P_CAS：中科院分区映射（1区=100, 2区=80, 3区=50, 4区=25）

无手填时自动档（对数正态，v3）：
    w₁ = 100 × Φ((ln(2yr_mean) - μ_ln) / σ_ln)
μ_ln/σ_ln = 该学科所有期刊 ln(2yr_mean) 的均值/标准差（校准）。
IF 分布右偏对数正态 → 取 ln 近似正态，z-score 相对位置比绝对 IF 学科内可比
（2026-08-02 起为化学领域统一基准），
且 Φ(z) 单调映射到 [0,1]，z=0（期刊=学科均值）即 50 中性分。

全无数据：中性 50。
"""
from __future__ import annotations

import math

from app.services import distributions
from app.services.rules import get_rules


def _lognormal_basis(paper, mu: float, sigma: float, percentile: float) -> str:
    z = (math.log(paper.journal_2yr_mean) - mu) / sigma
    return (
        f"期刊 2yr_mean={paper.journal_2yr_mean:.2f} 在该学科的对数正态位置 "
        f"z={z:.2f}（μ_ln={mu:.2f}, σ_ln={sigma:.2f}）→ Φ(z)={percentile:.1f}"
    )


def compute(paper) -> dict:
    rules = get_rules()
    jmap = rules["journal"]["jcr_mapping"]  # {"Q1":90,...}
    cmap = {int(k): v for k, v in rules["journal"]["cas_mapping"].items()}

    p_jcr = jmap.get(paper.jcr_quartile.upper()) if paper.jcr_quartile else None
    p_cas = cmap.get(paper.cas_zone) if paper.cas_zone else None

    # 自动档（对数正态）：手填分支也附带，供 basis 注记"自动百分位更高"
    auto_percentile = None
    if paper.journal_2yr_mean is not None:
        params = distributions.journal_lognormal_for(paper.subfield_id)
        if params:
            mu, sigma = params
            auto_percentile = distributions.lognormal_percentile(mu, sigma, paper.journal_2yr_mean)

    # 1. 双轨取高（用户手填 JCR 和/或中科院分区，且与自动对数正态取高）
    #    手填分区是粗粒度映射（Q3=45），自动对数正态是连续百分位——若自动档更高，
    #    说明该期刊在学科内真实位置优于手填档位，取高（v5.1 修正：原只注记不生效）。
    if p_jcr is not None or p_cas is not None:
        parts = []
        if p_jcr is not None:
            parts.append(f"JCR {paper.jcr_quartile.upper()}={p_jcr}")
        if p_cas is not None:
            parts.append(f"中科院{paper.cas_zone}区={p_cas}")
        manual = max(p for p in (p_jcr, p_cas) if p is not None)
        if auto_percentile is not None and auto_percentile > manual:
            score = auto_percentile
            basis = (
                f"w₁=max(手填 {', '.join(parts)}，自动对数正态百分位 {auto_percentile:.1f})"
                "——自动档更高，取自动值"
            )
        else:
            score = manual
            basis = f"w₁=max({', '.join(parts)})"
        return {
            "score": float(score),
            "percentile": float(score),
            "auto_percentile": auto_percentile,
            "basis": basis,
            "source": "manual_dual",
            "data_status": "ok",
        }

    # 2. 对数正态自动档（v3）
    if auto_percentile is not None:
        return {
            "score": round(auto_percentile, 1),
            "percentile": auto_percentile,
            "basis": _lognormal_basis(paper, mu, sigma, auto_percentile),
            "source": "lognormal",
            "data_status": "ok",
        }

    # 3. 无任何数据 → 中性 50
    #    注意：fetch 期可能用经验分位兜底落库了 journal_percentile（journal_lognormal 为空时），
    #    此时 source=missing 但门已有真实百分位信号 → data_status 按百分位判定，避免误报缺失。
    return {
        "score": 50.0,
        "percentile": None,
        "basis": "无期刊指标数据",
        "source": "missing",
        "data_status": "missing" if paper.journal_percentile is None else "ok",
    }
