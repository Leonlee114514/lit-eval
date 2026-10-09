"""评估数据溯源：指纹（数据变没变）+ 完整性（算的时候够不够）。

`paper.status == "done"` 只说明"评估跑过了"，说明不了两件事：

1. **指纹** —— 这份分数是基于哪一版数据与规则算出来的。抓取补全了指标、用户改了研究主题、
   `scoring_rules.json` 调了权重、PDF 换了文件……都不会改 `status`，分数却已经过时。
2. **完整性** —— 算的时候关键维度是不是本来就缺数据。缺数据会被决策三态门判 `unknown`
   并"封顶"档位，结果看起来和"真的低分"一模一样。

2026-09-16 端到端踩到过：OpenAlex API key 没覆盖直连调用（见 ~/.dsh/AGENTS.md 第 7 条），
`journal_2yr_mean` 51/51 全空 → 全批档位被"缺数据封顶"卡死，而 `status` 全是 `done`；
当时只能靠手动 force + offset 分段重跑才定位到。本模块让这类情况可被自动识别。

判定分两类，用途不同：

- ``is_actionable`` —— **重跑就可能改善**（没评过 / 没指纹 / 数据变了）→ 适合喂给"只重评需要的"。
- ``missing_dimensions`` —— **源头本来就缺这份数据**（三源都查不到的中文 DOI 等）→ 重跑也不会变，
  应当如实标"数据不足/无法核验"，而不是反复重跑。这一项同时充当"无法核验"标记。

注意：``get_rules()`` 是 lru_cache 的，进程内改了 `scoring_rules.json` 要重启才生效，
指纹也才会跟着变 —— 与本项目的常规操作一致。
"""
from __future__ import annotations

import hashlib
import json
import os

from app.models.fields import SCORING_FIELDS
from app.services.rules import get_rules

# 进指纹的指标字段直接取自字段注册表（``scoring=True``），本文件不再维护第二份清单：
# 过去"新增指标字段忘了加进指纹"是静默降级 —— 分数已过时却仍显示可信。
# relevance 不在其中：它走主题 + 正文摘要，见 scoring_inputs 的 topic / text 两项。

# 决策三态门看的六个维度（与 evaluation_service 落库的 data_status 键一致）
CRITICAL_DIMENSIONS = (
    "journal",
    "citation",
    "timeliness",
    "author",
    "reproducibility",
    "content_quality",
)

# 重跑可能改善的原因（"源头缺数据"不在此列）
ACTIONABLE_REASONS = ("no_evaluation", "no_fingerprint", "data_changed")


def _norm(value: object) -> object:
    """规范化成稳定可 JSON 化的值：浮点定长，避免无关精度抖动被误判成"数据变了"。"""
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, float):
        return round(value, 6)
    if isinstance(value, int):
        return value
    return str(value)


def _digest(payload: object) -> str:
    blob = json.dumps(
        payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str
    )
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _rules_signature() -> list:
    """[版本号, 规则内容哈希前 12 位]。内容哈希能抓住"改了权重但忘了升版本号"。"""
    rules = get_rules()
    return [str(rules.get("version") or ""), _digest(rules)[:12]]


def _fulltext_size(path: str | None) -> int | None:
    """全文文件字节数；-1 表示"库里有路径但文件不在"（与"没有全文"区分开）。"""
    if not path:
        return None
    try:
        return os.stat(path).st_size
    except OSError:
        return -1


def scoring_inputs(paper) -> dict:
    """打分实际吃到的输入。刻意不含时间戳等易变值。"""
    project = getattr(paper, "project", None)
    keywords = list(getattr(project, "keywords", None) or [])
    return {
        "rules": _rules_signature(),
        "topic": _digest(
            [
                getattr(project, "research_topic", "") or "",
                sorted(str(k) for k in keywords),  # 词序不影响打分 → 排序消除误判
            ]
        ),
        "text": _digest([paper.title or "", paper.abstract or ""]),
        "fulltext_bytes": _fulltext_size(getattr(paper, "fulltext_path", None)),
        "metrics": {f: _norm(getattr(paper, f, None)) for f in SCORING_FIELDS},
    }


def compute_fingerprint(paper) -> str:
    """当前 paper 状态对应的指纹。与评估落库时存的值比对即可判定是否过时。"""
    return _digest(scoring_inputs(paper))


def missing_dimensions(evaluation) -> list[str]:
    """记录在案的缺数据维度（决策三态门据此判 unknown）。"""
    status = getattr(evaluation, "data_status", None) or {}
    return [d for d in CRITICAL_DIMENSIONS if status.get(d) not in (None, "ok")]


def recheck_reason(paper, evaluation) -> str | None:
    """返回"这份评估需要重做"的原因；None 表示仍然可信。

    四种原因，便于区分"数据变了"和"当初就缺数据"：
      ``no_evaluation`` / ``no_fingerprint``（本功能上线前算的）/ ``data_changed`` /
      ``missing_data:<维度,...>``
    """
    if evaluation is None:
        return "no_evaluation"
    if not getattr(evaluation, "data_fingerprint", None):
        return "no_fingerprint"
    if evaluation.data_fingerprint != compute_fingerprint(paper):
        return "data_changed"
    missing = missing_dimensions(evaluation)
    if missing:
        return "missing_data:" + ",".join(missing)
    return None


def is_actionable(paper, evaluation) -> bool:
    """重跑就可能改善的（区别于"源头本来就没有这份数据"）。"""
    return recheck_reason(paper, evaluation) in ACTIONABLE_REASONS


def is_stale(paper, evaluation) -> bool:
    """任何原因都算"这份评估不可全信"（含源头缺数据）。"""
    return recheck_reason(paper, evaluation) is not None
