"""综述类型检测。

OpenAlex 的 `work_type` 常把综述误标为 article（实测 "Waterborne polyurethanes: A
review" 被标成 article），导致 v5.1 的综述被引打折（`citation.review_discount`=0.7）
与 decision 层"综述被引泡沫"告警都不触发。本模块综合三路信号修正 work_type：

1. 源 work_type：任一源已标 review（OpenAlex `type` / S2 `publicationTypes`）
2. 标题启发式：标题含 "review" / "meta-analysis"（systematic review、review article、
   mini-review 等均被覆盖）

只做"升格"（→ review），绝不把真实 review 降成 article——保守、零误伤。
"""

from __future__ import annotations

import re

# 标题命中即视为综述。词边界 + 忽略大小写；"meta-analysis" 是系统综述的定量形式
_REVIEW_TITLE_RE = re.compile(r"\breview\b|\bmeta[-\s]?analysis\b", re.IGNORECASE)


def detect_review(
    work_type: str | None,
    title: str | None,
    s2_signal: str | None = None,
) -> str | None:
    """返回归一化 work_type：任一信号表明是综述 → 'review'；否则原样保留。

    - work_type: 已合并的 work_type（OpenAlex 优先，可能误标 article）
    - title: 论文标题（标题启发式）
    - s2_signal: Semantic Scholar `publicationTypes` 归一结果（"review"/"letter"/None）
    """
    if work_type and work_type.lower() == "review":
        return "review"
    if s2_signal and s2_signal.lower() == "review":
        return "review"
    if title and _REVIEW_TITLE_RE.search(title):
        return "review"
    return work_type
