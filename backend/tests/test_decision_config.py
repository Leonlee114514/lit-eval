"""决策门槛的"唯一事实源"测试。

守护 2026-10 review 的 Warning（Knowledge Duplication）：`decision.py` 里同一个门槛
曾有三种写法并存 —— 判定读 `scoring_rules.json`（`:92`）、提示文案把它硬编码成 "≥70"
（`:102`）、内容质量门槛 60 干脆不在配置里（`:142`）。

后果不是报错，而是**界面撒谎**：改了配置里的门槛，用户看到的理由还是旧数字。
这两个测试把门槛钉在配置上：改配置，文案与分支必须跟着变。
"""
from __future__ import annotations

import copy
from types import SimpleNamespace

from app.services import decision as dec


def _paper(**over):
    """decide 只按属性名读取，用最小替身即可。

    凑齐 decide 会读到的全部属性（缺一个就是 AttributeError）：
    门槛判定、告警分支（work_type / impact_factor / cited_by_5yr）、溯源文案（doi）。
    """
    base = dict(
        doi=None,
        journal_percentile=None,
        jcr_quartile=None,
        cas_zone=None,
        publication_year=2020,
        cited_by_count=0,
        cited_by_5yr=None,
        impact_factor=None,
        work_type="article",
        is_classic=False,
        is_retracted=False,
    )
    base.update(over)
    return SimpleNamespace(**base)


def _rules_with(monkeypatch, **dotted):
    """拿到配置的深拷贝，按 "step1.journal_percentile_ge" 这样的路径改值后注入。"""
    rules = copy.deepcopy(dec.get_rules())
    for path, value in dotted.items():
        node = rules
        parts = path.split(".")
        for key in parts[:-1]:
            node = node[key]
        node[parts[-1]] = value
    monkeypatch.setattr(dec, "get_rules", lambda: rules)
    return rules


def test_journal_threshold_in_text_follows_config(monkeypatch):
    """提示文案里的门槛数字必须来自配置，不能是写死的 "≥70"。"""
    _rules_with(monkeypatch, **{"decision.step1.journal_percentile_ge": 42})

    result = dec.decide(
        _paper(journal_percentile=95),
        {"journal": 90, "citation": 60, "content_quality": 70},
        composite_score=80,
        data_status={},
    )
    text = " ".join(result["reasons"])
    assert "≥42" in text, text
    assert "≥70" not in text, f"文案里还有硬编码的旧门槛: {text}"


def test_content_quality_branch_follows_config(monkeypatch):
    """内容质量分支必须读配置门槛（60 过去是写死在 elif 里的魔数）。"""
    rules = _rules_with(monkeypatch, **{"decision.step3.content_quality_ge": 100})
    scores = {"journal": 50, "citation": 50, "content_quality": 90}

    low = dec.decide(_paper(), scores, composite_score=50, data_status={})
    assert any("内容质量一般" in r for r in low["reasons"]), low["reasons"]

    # 把门槛降到 10，同一份分数应当翻转成"良好"
    rules["decision"]["step3"]["content_quality_ge"] = 10
    high = dec.decide(_paper(), scores, composite_score=50, data_status={})
    assert any("内容质量良好" in r for r in high["reasons"]), high["reasons"]


def test_content_quality_threshold_present_in_shipped_rules():
    """配置文件里必须真的存在这个键 —— 否则上面两条只是在测替身。"""
    rules = dec.get_rules()
    assert "content_quality_ge" in rules["decision"]["step3"]
    assert isinstance(rules["decision"]["step3"]["content_quality_ge"], (int, float))
