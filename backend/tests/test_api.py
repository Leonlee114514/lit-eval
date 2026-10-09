"""API 测试：无外网的 CRUD + 状态；外网的抓取/评估集成测试打 integration 标记。"""
from __future__ import annotations

import pytest


def test_health(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json()["ok"] is True


def test_rules_endpoint(client):
    r = client.get("/api/meta/rules")
    assert r.status_code == 200
    data = r.json()["data"]
    assert "weights" in data and "decision" in data


def test_project_crud(client):
    r = client.post("/api/projects", json={"name": "测试课题", "research_topic": "polyurethane coatings"})
    assert r.status_code == 200
    pid = r.json()["data"]["id"]

    r = client.get("/api/projects")
    assert any(p["id"] == pid for p in r.json()["data"])

    r = client.delete(f"/api/projects/{pid}")
    assert r.json()["ok"] is True

    r = client.get("/api/projects")
    assert all(p["id"] != pid for p in r.json()["data"])


def test_import_invalid_doi_marked(client):
    """格式合法但不存在 → 不应 500，应标记缺失字段。"""
    r = client.post("/api/projects", json={"name": "坏DOI测试"})
    pid = r.json()["data"]["id"]
    r = client.post(
        f"/api/projects/{pid}/papers",
        json={"inputs": [{"doi": "10.9999/fake.1"}]},
    )
    assert r.status_code == 200
    papers = r.json()["data"]["papers"]
    assert papers, "应至少返回一行（缺失标记）"
    paper = papers[0]
    assert paper["status"] in ("partial", "failed")


@pytest.mark.integration
def test_end_to_end_nature_doi(client):
    """外网：抓取 LeCun 深度学习综述 → 评估 → 报告。"""
    r = client.post("/api/projects", json={"name": "集成测试", "research_topic": "deep learning"})
    pid = r.json()["data"]["id"]

    r = client.post(
        f"/api/projects/{pid}/papers",
        json={"inputs": [{"doi": "10.1038/nature14539"}]},
    )
    assert r.status_code == 200
    papers = r.json()["data"]["papers"]
    assert len(papers) == 1
    paper = papers[0]
    assert paper["title"]
    assert paper["cited_by_count"] and paper["cited_by_count"] > 10000
    assert paper["status"] == "metadata_ok" or paper["status"] == "partial"

    paper_id = paper["id"]
    r = client.post(f"/api/papers/{paper_id}/evaluate")
    assert r.status_code == 200

    r = client.get(f"/api/papers/{paper_id}/evaluation")
    ev = r.json()["data"]
    assert 0 <= ev["composite_score"] <= 100
    assert set(ev["radar_scores"].keys()) == {
        "journal_level", "citation_impact", "content_quality", "timeliness", "relevance"
    }
    assert ev["llm_assessment"]["source"] in ("rule_based", "openai_compat")  # 无密钥降级 / 有密钥真实
    assert ev["decision"] in ("deep_read", "background_only", "reject")

    r = client.get(f"/api/papers/{paper_id}/report")
    report = r.json()["data"]
    assert report["title"] == paper["title"]
    assert report["source_trace"]
    assert report["decision_reasons"]

    r = client.get(f"/api/papers/{paper_id}/report/export?format=md")
    assert r.status_code == 200
    assert "# 文献评估报告" in r.text


@pytest.mark.integration
def test_import_by_title_resolves_doi(client):
    """外网：标题导入走 OpenAlex 搜索 → 应解析出 DOI 与元数据。"""
    r = client.post("/api/projects", json={"name": "标题导入测试", "research_topic": "deep learning"})
    pid = r.json()["data"]["id"]

    r = client.post(
        f"/api/projects/{pid}/papers",
        json={"inputs": [{"title": "Deep learning"}]},
    )
    assert r.status_code == 200
    papers = r.json()["data"]["papers"]
    assert len(papers) == 1
    paper = papers[0]
    assert paper["doi"] == "10.1038/nature14539"
    assert paper["title"]
    assert paper["status"] in ("metadata_ok", "partial")

    # 同 DOI 再导入 → 去重，仍只有一篇
    r2 = client.post(
        f"/api/projects/{pid}/papers",
        json={"inputs": [{"title": "Deep learning"}]},
    )
    papers2 = r2.json()["data"]["papers"]
    assert len(papers2) == 1
    assert papers2[0]["id"] == paper["id"]


@pytest.mark.integration
def test_pmid_lookup_converts_doi(client):
    """外网：PMID 26017442 (Deep learning) → DOI 10.1038/nature14539。"""
    r = client.post("/api/projects", json={"name": "PMID测试"})
    pid = r.json()["data"]["id"]
    r = client.post(
        f"/api/projects/{pid}/pmid-lookup",
        json={"pmids": ["26017442"]},
    )
    assert r.status_code == 200
    mapping = r.json()["data"]
    assert mapping.get("26017442") == "10.1038/nature14539"


@pytest.mark.integration
def test_import_by_pmid(client):
    """外网：PMID 导入 → 转 DOI → 抓取落库。"""
    r = client.post("/api/projects", json={"name": "PMID导入测试", "research_topic": "deep learning"})
    pid = r.json()["data"]["id"]
    mapping = client.post(f"/api/projects/{pid}/pmid-lookup", json={"pmids": ["26017442"]}).json()["data"]
    doi = mapping["26017442"]
    r = client.post(f"/api/projects/{pid}/papers", json={"inputs": [{"doi": doi}]})
    papers = r.json()["data"]["papers"]
    assert len(papers) == 1
    assert papers[0]["doi"] == "10.1038/nature14539"
    assert papers[0]["title"]


@pytest.mark.integration
def test_retracted_paper_detected(client):
    """外网：被撤稿论文 10.1038/nature00870 → is_retracted=True，决策 reject。"""
    r = client.post("/api/projects", json={"name": "撤稿测试", "research_topic": "stem cell"})
    pid = r.json()["data"]["id"]
    r = client.post(
        f"/api/projects/{pid}/papers",
        json={"inputs": [{"doi": "10.1038/nature00870"}]},
    )
    assert r.status_code == 200
    paper = r.json()["data"]["papers"][0]
    assert paper["is_retracted"] is True

    paper_id = paper["id"]
    r = client.post(f"/api/papers/{paper_id}/evaluate")
    assert r.status_code == 200
    ev = client.get(f"/api/papers/{paper_id}/evaluation").json()["data"]
    assert ev["decision"] == "reject"
    assert any("撤稿" in w for w in ev["warnings"])


@pytest.mark.integration
def test_arxiv_no_journal_metric(client):
    """外网：arXiv 论文无期刊指标，journal_percentile 应为 null。"""
    r = client.post("/api/projects", json={"name": "arXiv测试"})
    pid = r.json()["data"]["id"]
    r = client.post(
        f"/api/projects/{pid}/papers",
        json={"inputs": [{"doi": "10.48550/arXiv.1706.03762"}]},
    )
    papers = r.json()["data"]["papers"]
    paper = papers[0]
    assert paper["journal_percentile"] is None or paper["status"] in ("partial", "failed")


# ---- 检索式构造（SEARCH_QUERY_MODE）----


@pytest.mark.integration
def test_search_endpoint_raw_contract(client):
    """外网：默认 raw 模式 → 检索式原样透传，响应带检索式元信息。"""
    r = client.get(
        "/api/search/works",
        params={"q": "waterborne polyurethane coating", "per_page": 5},
    )
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["query"] == "waterborne polyurethane coating"
    assert data["query_mode"] == "raw"
    assert data["query_used"] == ["waterborne polyurethane coating"]
    assert data["total_estimated"] is False
    assert data["total"] and data["total"] > 0
    assert data["results"]
    for k in ("doi", "title", "cited_by_count", "relevance_score", "already_imported", "openalex_score"):
        assert k in data["results"][0]


@pytest.mark.integration
def test_search_endpoint_expanded_mode(client, monkeypatch):
    """外网：expanded 模式 → 同义词展开成 OR 组，命中数应显著增加。"""
    from app.config import Settings
    from app.services import search_service

    monkeypatch.setattr(
        search_service, "get_settings",
        lambda: Settings(search_query_mode="expanded"),
    )
    r = client.get(
        "/api/search/works",
        params={"q": "waterborne polyurethane coating", "per_page": 5},
    )
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["query_mode"] == "expanded"
    assert len(data["query_used"]) == 1
    q = data["query_used"][0]
    assert " OR " in q and "aqueous" in q and "polyurethane" in q and "coating" in q
    assert data["total"] and data["total"] > 0


@pytest.mark.integration
def test_search_endpoint_chinese_not_quoted(client):
    """外网回归：中文检索式绝不能被加引号 —— 加引号会让命中数变成 0。

    实测（2026-09）：`水性聚氨酯涂层` 裸串 1593 命中，加双引号后 0 命中。
    """
    r = client.get("/api/search/works", params={"q": "水性聚氨酯涂层", "per_page": 3})
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["query_used"] == ["水性聚氨酯涂层"]
    assert '"' not in data["query_used"][0]
    assert data["total"] and data["total"] > 0


@pytest.mark.integration
def test_search_endpoint_query_mode_override(client):
    """外网：query_mode 逐次覆盖服务端默认 —— 前端那个"扩展同义词"开关走这条路。

    服务端 .env 默认 SEARCH_QUERY_MODE=raw，这里显式传 expanded 必须生效。
    """
    base = {"q": "waterborne polyurethane coating", "per_page": 5}
    raw = client.get("/api/search/works", params={**base, "query_mode": "raw"}).json()["data"]
    exp = client.get("/api/search/works", params={**base, "query_mode": "expanded"}).json()["data"]

    assert raw["query_mode"] == "raw"
    assert exp["query_mode"] == "expanded"
    assert " OR " in exp["query_used"][0]
    # 两种口径的命中数必须真的不同（否则说明覆盖没生效）
    assert raw["total"] != exp["total"]

    # 非法取值被参数校验挡下（Query pattern）
    bad = client.get("/api/search/works", params={**base, "query_mode": "bogus"})
    assert bad.status_code == 422


def test_search_endpoint_maps_source_unavailable_to_503(client, monkeypatch):
    """上游配额耗尽 → 503 + 人话，而不是 500 裸错。

    实测 2026-09：OpenAlex 免费档 $0.10/天用尽后，原实现把 429 重试到耗尽
    再抛异常 → HTTP 500，界面显示"服务器内部错误"。这条路必须一直是 503。
    """
    from app.services import search_service
    from app.services.fetchers.base import SourceUnavailable

    async def _boom(*args, **kwargs):
        raise SourceUnavailable(
            "openalex", "今日请求额度已用尽", kind="budget_exhausted", retry_after=3600
        )

    monkeypatch.setattr(search_service, "search_works", _boom)
    r = client.get("/api/search/works", params={"q": "waterborne polyurethane"})

    assert r.status_code == 503          # 不是 500
    detail = r.json()["detail"]
    assert "额度已用尽" in detail
    assert "OPENALEX_API_KEY" in detail  # 给出可操作建议
    assert "1.0 小时" in detail          # 带上恢复时间
