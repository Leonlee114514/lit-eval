"""学科分布快照加载：单学科（化学领域）归一化的离线基准。

数据由 scripts/calibrate_distributions.py 一次性生成并提交仓库，
运行期 O(1) 查表。若未校准，回退到内置默认分布（保证功能可用）。

2026-08-02 起收敛为单学科基准：分布文件只含 "global" 键（化学领域
15 化工/16 化学/25 材料的合并分布），任何 subfield 打标都回退到该基准，
打标错误不再影响分数。跨学科可比目标已放弃（用户只评估化学领域文献）。

评分口径（v4）：期刊/被引均右偏对数正态 → 取 ln 近似正态 → z-score → Φ(z) CDF
映射到 0-100。只需 μ_ln、σ_ln 两个参数（journal_lognormal / citation_lognormal）。
"""
from __future__ import annotations

import bisect
import json
import logging
import math
from functools import lru_cache
from typing import Any

from app.config import get_settings

logger = logging.getLogger(__name__)

# 内置默认：宏观科学大致基准（未校准时兜底，避免除零/缺失）
_DEFAULT_JOURNAL_DIST = list(range(0, 101))  # 0..100 均匀，中位数 50
_DEFAULT_H_INDEX_MEAN = 20.0


@lru_cache
def _load_json(filename: str) -> dict[str, Any]:
    settings = get_settings()
    path = settings.distributions_dir / filename
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        logger.warning("分布文件缺失 %s，使用内置默认", path.name)
        return {}


def journal_dist_for(subfield_id: str | None) -> list[float]:
    """返回某 subfield 的期刊 2yr_mean_citedness 分布（升序列表）。"""
    data = _load_json("journal_2yr_citedness.json")
    key = subfield_id or "global"
    dist = data.get(key) or data.get("global")
    if dist:
        return [float(x) for x in dist]
    return _DEFAULT_JOURNAL_DIST


def h_index_mean_for(subfield_id: str | None) -> float:
    data = _load_json("field_h_index.json")
    key = subfield_id or "global"
    return float(data.get(key) or data.get("global") or _DEFAULT_H_INDEX_MEAN)


def h_index_calibrated() -> bool:
    """field_h_index.json 是否已真校准（为空 → 作者维度降级，basis 需标注）。"""
    return bool(_load_json("field_h_index.json"))


# 经典阈值兜底（year_citation_median.json 缺失时用）：与非零被引 p90 校准值一致（2026-08-07）
_DEFAULT_CITATION_MEDIAN = 18.0


def recent_citation_median_for(subfield_id: str | None) -> float:
    """决策门"老文献经典阈值"：化学领域经典被引线（2026-08-07 起为非零被引 p90≈17）。

    函数名保留历史（原为被引中位数，217 是降序偏置的虚高值）；data 结构
    {subfield_id: value, "global": value}。
    """
    data = _load_json("year_citation_median.json")
    key = subfield_id or "global"
    val = data.get(key) or data.get("global")
    return float(val) if val else _DEFAULT_CITATION_MEDIAN


def percentile_in(values: list[float], x: float | None) -> float | None:
    """x 在升序 values 中的百分位（0-100）。x 缺失返回 None。"""
    if x is None or not values:
        return None
    idx = bisect.bisect_left(values, float(x))
    return round(idx / len(values) * 100, 1)


def median_of(values: list[float]) -> float:
    if not values:
        return 0.0
    n = len(values)
    s = sorted(values)
    mid = n // 2
    if n % 2:
        return s[mid]
    return (s[mid - 1] + s[mid]) / 2


# ---- 对数正态映射（v3）----
# 文献计量学：期刊 IF / 论文被引分布右偏对数正态。取 ln 后近似正态，
# 用 z-score 定位，Φ(z)（标准正态 CDF）映射到 0-100。
# 每学科只需 {mu: mean(ln x), sigma: std(ln x)}，校准脚本生成。

_DEFAULT_LOGNORMAL = {"mu": 1.5, "sigma": 1.0}  # 未校准兜底（近似 global 中位数 e^1.5≈4.5）


def lognormal_percentile(mu: float, sigma: float, x: float | None) -> float | None:
    """Φ((ln(x)-μ)/σ)×100。x≤0（ln 无定义）或 σ≤0 返回 None。"""
    if x is None or x <= 0 or sigma is None or sigma <= 0:
        return None
    z = (math.log(x) - mu) / sigma
    return round(100.0 * 0.5 * (1.0 + math.erf(z / math.sqrt(2.0))), 1)


def _lognormal_params(data: dict[str, Any], subfield_id: str | None) -> tuple[float, float] | None:
    key = subfield_id or "global"
    entry = data.get(key) or data.get("global") or _DEFAULT_LOGNORMAL
    mu = float(entry.get("mu"))
    sigma = float(entry.get("sigma"))
    if sigma <= 0:
        return None
    return mu, sigma


@lru_cache
def _load_lognormal(filename: str) -> dict[str, Any]:
    return _load_json(filename)


def journal_lognormal_for(subfield_id: str | None) -> tuple[float, float] | None:
    """期刊 2yr_mean 的对数正态参数 (μ_ln, σ_ln)。"""
    data = _load_lognormal("journal_lognormal.json")
    return _lognormal_params(data, subfield_id)


def citation_lognormal_for(subfield_id: str | None) -> tuple[float, float] | None:
    """论文被引分布的对数正态参数 (μ_ln, σ_ln)。"""
    data = _load_lognormal("citation_lognormal.json")
    return _lognormal_params(data, subfield_id)


# ---- v5 活跃度：老文献同龄段位 / 新文献热点词表 ----

def age_band(age: int) -> str | None:
    """发表年限 → 老文献段位；≤5 年返回 None（走新文献前沿度分支）。

    段位 6-9 / 10-19 / 20-29 / 30+，供同龄近5年被引分布查找。
    """
    if age <= 5:
        return None
    if age <= 9:
        return "6-9"
    if age <= 19:
        return "10-19"
    if age <= 29:
        return "20-29"
    return "30+"


def recent_citation_band_for(age: int) -> list[float] | None:
    """某老文献段位的近5年被引分布（升序 ln(1+x) 数组）；缺校准返回 None。

    由 calibrate_distributions.py 生成；段位映射见 age_band()。
    """
    band = age_band(age)
    if band is None:
        return None
    data = _load_json("recent_citation_by_age.json")
    dist = (data.get("global") or {}).get(band)
    if not dist:
        return None
    return sorted(float(x) for x in dist)


def hot_keywords() -> dict[str, Any]:
    """新文献热点主题词表：{terms:[{term,idf}], top_text}；缺校准返回 {}。

    由 calibrate_distributions.py 从近2年高被引化学论文生成（TF-IDF top 200）。
    """
    return _load_json("hot_keywords.json").get("global") or {}
