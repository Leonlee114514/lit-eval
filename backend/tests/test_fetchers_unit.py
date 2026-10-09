"""抓取侧单测：检索式构造、重试与降级语义、PubMed 解析、OpenAlex key 覆盖、网络构建。"""

from __future__ import annotations


def test_citation_network_build_network():
    from app.services.citation_network import _build_network

    base = {
        "root": {"id": "root", "title": "Root paper", "cited_by_count": 50, "publication_year": 2020},
        "references": [
            {"id": "R1", "title": "Foundational old paper", "cited_by_count": 900, "publication_year": 1995},
            {"id": "R2", "title": "Recent method", "cited_by_count": 30, "publication_year": 2022},
        ],
        "citing": [
            {"id": "C1", "title": "Citing one", "cited_by_count": 10, "publication_year": 2023},
            {"id": "C2", "title": "Citing two", "cited_by_count": 5, "publication_year": 2024},
        ],
    }
    cfg = {"textbook_min_citations": 500, "textbook_min_co_cited": 2, "textbook_min_age": 10}
    net = _build_network(base, root_ref_ids={"R1", "R2", "R9"},
                         citing_ref_sets={"C1": {"R1", "R2"}, "C2": {"R1"}}, cfg=cfg)

    assert net["stats"]["node_count"] == 5
    assert net["stats"]["edge_count"] == 7  # C1->root, C2->root, root->R1, root->R2, C1->R1, C1->R2, C2->R1
    by_id = {n["id"]: n for n in net["nodes"]}
    assert by_id["R1"]["co_cited_count"] == 2
    assert by_id["R1"]["is_textbook"] is True  # 1995、900 被引、2 篇施引文献同时引用
    assert by_id["R2"]["is_textbook"] is False
    assert by_id["C1"]["bibliographic_coupling"] == 2
    assert by_id["C2"]["bibliographic_coupling"] == 1
    assert set(by_id["root"]) >= {"pagerank", "community", "role"}
    assert net["root"]["id"] == "root"
    # 所有边都落在节点集合内
    ids = set(by_id)
    assert all(e["source"] in ids and e["target"] in ids for e in net["edges"])
    # 教科书式引用进入统计
    assert net["stats"]["textbook_citations"][0]["id"] == "R1"


# ---- PubMed 直接源（P2 大项） ----


def test_parse_pubmed_xml_fields():
    from app.services.pubmed import _parse_pubmed_xml

    xml = """<?xml version="1.0"?>
<PubmedArticleSet><PubmedArticle><MedlineCitation>
<PMID>123456</PMID>
<Article>
<Journal><ISSN>1234-5678</ISSN><JournalIssue><Volume>10</Volume><Issue>2</Issue>
<PubDate><Year>2021</Year></PubDate></JournalIssue><Title>Test Journal</Title></Journal>
<ArticleTitle>Test article title.</ArticleTitle>
<Abstract><AbstractText Label="BACKGROUND">Some background.</AbstractText></Abstract>
<AuthorList><Author><LastName>Smith</LastName><ForeName>John</ForeName></Author></AuthorList>
<PublicationTypeList><PublicationType>Journal Article</PublicationType><PublicationType>Review</PublicationType></PublicationTypeList>
</Article><MeshHeadingList><MeshHeading><DescriptorName>Test Topic</DescriptorName></MeshHeading></MeshHeadingList>
</MedlineCitation><PubmedData><ArticleIdList>
<ArticleId IdType="doi">10.1000/test</ArticleId></ArticleIdList></PubmedData></PubmedArticle></PubmedArticleSet>"""

    meta = _parse_pubmed_xml(xml, "123456")
    assert meta is not None
    assert meta["pmid"] == "123456"
    assert meta["doi"] == "10.1000/test"
    assert meta["title"] == "Test article title."
    assert meta["journal"] == "Test Journal"
    assert meta["publication_year"] == 2021
    assert meta["volume"] == "10" and meta["issue"] == "2"
    assert meta["authors"][0]["name"] == "John Smith"
    assert meta["keywords"] == ["Test Topic"]
    assert meta["work_type"] == "review"
    assert "BACKGROUND" in meta["abstract"]


def test_parse_pubmed_xml_no_doi():
    from app.services.pubmed import _parse_pubmed_xml

    xml = """<PubmedArticleSet><PubmedArticle><MedlineCitation><Article>
<Journal><JournalIssue><PubDate><MedlineDate>2020 Jan-Feb</MedlineDate></PubDate></JournalIssue><Title>J</Title></Journal>
<ArticleTitle>T</ArticleTitle></Article></MedlineCitation></PubmedArticle></PubmedArticleSet>"""
    meta = _parse_pubmed_xml(xml, "9")
    assert meta is not None
    assert meta["doi"] is None
    assert meta["publication_year"] == 2020


# ---- 项目级 GNN 引用网络 ----


def test_project_network_build():
    from app.services.project_network import _build_project_network

    papers = [
        {"id": "P1", "openalex_id": "W1", "title": "Paper one", "cited_by_count": 10, "publication_year": 2020},
        {"id": "P2", "openalex_id": "W2", "title": "Paper two", "cited_by_count": 20, "publication_year": 2021},
        {"id": "P3", "openalex_id": "W3", "title": "Paper three", "cited_by_count": 30, "publication_year": 2022},
    ]
    ref_meta = {
        "R1": {"id": "R1", "title": "Old core reference", "cited_by_count": 900, "publication_year": 1990},
        "R2": {"id": "R2", "title": "Shared method", "cited_by_count": 40, "publication_year": 2021},
    }
    paper_refs = {
        "P1": {"R1", "R2"}, "P2": {"R1", "R2"}, "P3": {"R1"},
    }
    ref_papers = {"R1": {"P1", "P2", "P3"}, "R2": {"P1", "P2"}}
    cfg = {
        "min_co_cited": 2, "max_references": 60, "coupling_min_shared": 1,
        "core_reference_limit": 10,
        "textbook_min_citations": 500, "textbook_min_co_cited": 2, "textbook_min_age": 10,
    }
    net = _build_project_network(papers, ref_meta, paper_refs, ref_papers, cfg)

    assert net["stats"]["paper_count"] == 3
    assert net["stats"]["shared_reference_count"] == 2
    assert net["stats"]["coupling_edge_count"] == 3  # P1-P2, P1-P3, P2-P3
    by_id = {n["id"]: n for n in net["nodes"]}
    assert by_id["R1"]["co_cited_count"] == 3
    assert by_id["R1"]["is_textbook"] is True
    assert by_id["R2"]["is_textbook"] is False
    assert by_id["P1"]["role"] == "project_paper"
    assert by_id["P1"]["pagerank"] > 0
    assert net["stats"]["textbook_citations"][0]["id"] == "R1"
    assert net["stats"]["top_coupling_pairs"][0]["shared_references"] == 2
    assert set(net["stats"]["core_references"][0]) >= {"title", "co_cited_count", "cited_by_count"}


# ---- 检索式构造（fetchers/query_builder.py，纯函数、离线）----


def _group_terms(variant: str) -> list[str]:
    """把渲染后的检索式拆回词项列表（测试辅助：仅处理本用例的形态）。"""
    terms: list[str] = []
    for grp in variant.split(" AND "):
        grp = grp.strip()
        if grp.startswith("(") and grp.endswith(")"):
            terms.extend(t.strip() for t in grp[1:-1].split(" OR "))
        elif grp:
            terms.append(grp)
    return terms


def test_query_builder_invert_synonyms():
    from app.services.fetchers.query_builder import invert_synonym_map

    inverted = invert_synonym_map({"aqueous": "waterborne", "waterbased": "waterborne"})
    # 规范词自身也进组，且排序确定
    assert inverted == {"waterborne": ["aqueous", "waterbased", "waterborne"]}
    assert invert_synonym_map(None) == {}
    # 空值项被忽略
    assert invert_synonym_map({"": "b", "c": ""}) == {}
    # 自映射只产生单形态组，不会形成 (x OR x)；实际扩展由 len(forms) > 1 兜住
    assert invert_synonym_map({"a": "a"}) == {"a": ["a"]}


def test_query_builder_parse_extra_synonyms():
    from app.services.fetchers.query_builder import parse_extra_synonyms

    parsed = parse_extra_synonyms(" PU=polyurethane , coat=coating ,broken, ,x=x")
    assert parsed == {"pu": "polyurethane", "coat": "coating"}
    assert parse_extra_synonyms("") == {}
    assert parse_extra_synonyms(None) == {}


def test_query_builder_raw_mode_is_verbatim():
    """默认 raw 模式必须逐字透传——这是与引入本模块前行为一致的保证。"""
    from app.services.fetchers.query_builder import build_query_plan

    # 含布尔运算符也不解释，raw 模式短路在最前面
    for q in ("waterborne polyurethane", 'elmo AND "sesame street"', "  spaced  out  "):
        plan = build_query_plan(q, mode="raw", base_synonyms={"aqueous": "waterborne"})
        assert plan.variants == (q.strip(),)
        assert plan.mode == "raw"
        assert plan.is_multi is False
    # 未知 mode 一律回落 raw，不静默变激进
    assert build_query_plan("x", mode="whatever").mode == "raw"
    assert build_query_plan("", mode="expanded").variants == ()


def test_query_builder_expanded_builds_or_groups():
    """同义词命中 → 该词展开成 OR 组；未命中的词保持单词，组间 AND。"""
    from app.services.content.relevance import _SYNONYMS
    from app.services.fetchers.query_builder import build_query_plan

    plan = build_query_plan("aqueous polyurethane coating", mode="expanded", base_synonyms=_SYNONYMS)
    assert plan.mode == "expanded"
    assert plan.variants == (
        '(aqueous OR "water-borne" OR waterbased OR waterborne) AND polyurethane AND coating',
    )
    # 用户原词排首位，便于肉眼确认扩展没有丢掉他写的词
    assert _group_terms(plan.variants[0])[0] == "aqueous"


def test_query_builder_boolean_is_expert_passthrough():
    """用户自己写了布尔运算符 → 不猜意图，原样透传。"""
    from app.services.fetchers.query_builder import build_query_plan

    q = '(elmo AND "sesame street") NOT (cookie OR monster)'
    plan = build_query_plan(q, mode="expanded", base_synonyms={"elmo": "elmo2"})
    assert plan.mode == "expert_passthrough"
    assert plan.variants == (q,)
    assert "专家模式" in plan.note


def test_query_builder_keeps_user_quoted_phrase():
    """用户手写的引号短语是原子词：保留引号、不拆开做同义词扩展。"""
    from app.services.fetchers.query_builder import build_query_plan

    plan = build_query_plan('"waterborne polyurethane" coating', mode="expanded", base_synonyms={})
    assert plan.variants == ('"waterborne polyurethane" AND coating',)


def test_query_builder_quotes_unsafe_tokens():
    from app.services.fetchers.query_builder import quote_term

    assert quote_term("polyurethane") == "polyurethane"
    assert quote_term("water-borne") == '"water-borne"'
    assert quote_term("multi word") == '"multi word"'
    assert quote_term("OR") == '"OR"'          # 保留词必须引号，否则被当语法
    assert quote_term("  ") == ""


def test_query_builder_never_quotes_cjk():
    """中文绝不加引号。

    实测（2026-09，真实 OpenAlex）：`水性聚氨酯涂层` 裸串 1593 命中，
    加双引号后 **0 命中** —— 引号让整串变成一个词条，在词干化 search 里匹配不到。
    这是"ASCII 中心假设"在中文上的静默失效，必须锁回归。
    """
    from app.services.fetchers.query_builder import build_query_plan, quote_term

    assert quote_term("水性聚氨酯涂层") == "水性聚氨酯涂层"
    assert quote_term("聚氨酯") == "聚氨酯"

    # 未命中同义词表 → 扩展开关对中文是安全 no-op（与 raw 完全同串）
    plan = build_query_plan("水性聚氨酯涂层", mode="expanded", base_synonyms={})
    assert plan.variants == ("水性聚氨酯涂层",)
    assert build_query_plan("水性聚氨酯涂层", mode="raw").variants == plan.variants


def test_query_builder_splits_over_url_budget():
    """超 URL 预算时切块，且各变体的词项并集与原式完全一致（等价性）。"""
    from app.services.fetchers.query_builder import (
        build_query_plan,
        encoded_len,
        render_query,
    )

    syn = {f"syn{i}": "base" for i in range(20)}
    expected = sorted(["base"] + [f"syn{i}" for i in range(20)])
    plan = build_query_plan("base", mode="expanded", base_synonyms=syn, max_encoded_len=60, max_variants=16)

    assert len(plan.variants) > 1          # 确实切了
    assert "并集" in plan.note
    for v in plan.variants:
        assert encoded_len(v) <= 60        # 每段都在预算内
    # 并集等价：把所有变体拆回词项，应恰好还原原 OR 组
    terms: list[str] = []
    for v in plan.variants:
        terms.extend(_group_terms(v))
    assert sorted(terms) == expected
    # 且任一变体都不是原式（确实拆分过）
    assert render_query([expected]) not in plan.variants


def test_query_builder_digest_tracks_synonym_changes():
    """digest 用于缓存键：同义词表变了、结果变了，digest 必须跟着变。"""
    from app.services.fetchers.query_builder import build_query_plan

    a = build_query_plan("aqueous coating", mode="expanded", base_synonyms={})
    b = build_query_plan("aqueous coating", mode="expanded", base_synonyms={"aqueous": "waterborne"})
    assert a.digest != b.digest
    # 同输入重复构造必须稳定（否则缓存永不命中）
    assert b.digest == build_query_plan(
        "aqueous coating", mode="expanded", base_synonyms={"aqueous": "waterborne"}
    ).digest


def test_search_settings_default_to_old_behaviour():
    """默认必须是 raw + 空扩展表 —— 升级后不改配置就等于行为不变。"""
    from app.config import Settings

    s = Settings(_env_file=None)
    assert s.search_query_mode == "raw"
    assert s.search_extra_synonyms == ""
    assert s.openalex_api_key == ""


# ---- 源不可用的降级语义（fetchers/base.py）----


def _offline_fetcher(handler):
    """构造一个把请求交给 handler 的 OpenAlexFetcher（离线，不发真请求）。"""
    import httpx

    from app.services.fetchers.openalex import OpenAlexFetcher

    f = OpenAlexFetcher(mailto="t@example.com")
    f._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return f


def test_source_unavailable_user_messages():
    """每种不可用都要有一句能直接显示给用户的人话。"""
    from app.services.fetchers.base import SourceUnavailable

    budget = SourceUnavailable(
        "openalex", "今日请求额度已用尽", kind="budget_exhausted", retry_after=3600
    )
    assert "额度已用尽" in budget.user_message()
    assert "1.0 小时" in budget.user_message()
    assert "OPENALEX_API_KEY" in budget.user_message()

    # 拿不到 retry-after 时也要给恢复提示且不崩
    assert "UTC 午夜" in SourceUnavailable("openalex", "x", kind="budget_exhausted").user_message()

    assert "限流" in SourceUnavailable("openalex", "x", kind="rate_limited").user_message()
    assert "服务端" in SourceUnavailable("openalex", "x", kind="server_error").user_message()
    assert "连接失败" in SourceUnavailable("openalex", "x", kind="timeout").user_message()
    assert "没有这条记录" in SourceUnavailable("openalex", "x", kind="not_found").user_message()
    assert "不可用" in SourceUnavailable("crossref", "什么都没说").user_message()


def test_retry_after_parsing_accepts_seconds_and_http_date():
    import time
    from email.utils import formatdate

    import httpx

    from app.services.fetchers.base import _retry_after_seconds

    assert _retry_after_seconds(httpx.Response(429, headers={"retry-after": "37222"})) == 37222
    assert _retry_after_seconds(httpx.Response(429, headers={})) is None
    # 头值不可解析（httpx 只收 ASCII 头值，故用 ASCII 垃圾串）
    assert _retry_after_seconds(httpx.Response(429, headers={"retry-after": "garbage"})) is None
    # HTTP-date 形态也要能解析
    future = formatdate(time.time() + 600, usegmt=True)
    v = _retry_after_seconds(httpx.Response(429, headers={"retry-after": future}))
    assert v is not None and 500 < v <= 600


def test_fetcher_get_returns_json_on_success():
    import asyncio

    import httpx

    f = _offline_fetcher(lambda r: httpx.Response(200, json={"ok": 1}))
    try:
        assert asyncio.run(f._get("https://api.openalex.org/works")) == {"ok": 1}
    finally:
        asyncio.run(f._client.aclose())


def test_fetcher_budget_exhausted_does_not_retry():
    """配额耗尽（429 + x-ratelimit-remaining:0）必须立即失败、不重试。

    依据 OpenAlex 官方降级表：日配额耗尽时本地重试不可能成功，只会白烧额度。
    实测 2026-09：免费档 $0.10/天用尽后 retry-after ≈ 37222 秒。
    """
    import asyncio

    import httpx

    from app.services.fetchers.base import SourceUnavailable

    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(
            429,
            headers={"x-ratelimit-remaining": "0", "retry-after": "37222"},
            json={"error": "Rate limit exceeded"},
        )

    f = _offline_fetcher(handler)
    try:
        try:
            asyncio.run(f._get("https://api.openalex.org/works", {"search": "x"}))
            raise AssertionError("应抛 SourceUnavailable")
        except SourceUnavailable as e:
            assert e.kind == "budget_exhausted"
            assert e.retry_after == 37222
            assert "约 10.3 小时" in e.user_message()
        assert calls["n"] == 1, f"配额耗尽不应重试，实际发了 {calls['n']} 次"
    finally:
        asyncio.run(f._client.aclose())


def test_fetcher_burst_429_retries_then_reports_rate_limited():
    """突发限流（429 但没有 remaining:0）应退避重试，用尽后归类 rate_limited。"""
    import asyncio

    import httpx

    from app.services.fetchers.base import SourceUnavailable

    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(429, json={"error": "too many"})

    f = _offline_fetcher(handler)
    try:
        try:
            asyncio.run(f._get("https://api.openalex.org/works", {"search": "x"}))
            raise AssertionError("应抛 SourceUnavailable")
        except SourceUnavailable as e:
            assert e.kind == "rate_limited"
        assert calls["n"] == 3, f"突发限流应重试到 3 次，实际 {calls['n']}"
    finally:
        asyncio.run(f._client.aclose())


def test_fetcher_404_becomes_not_found():
    """404 单独归类：别把"没这条记录"说成上游故障。"""
    import asyncio

    import httpx

    from app.services.fetchers.base import SourceUnavailable

    f = _offline_fetcher(lambda r: httpx.Response(404, json={}))
    try:
        try:
            asyncio.run(f._get("https://api.openalex.org/works/doi:10.1/x"))
            raise AssertionError("应抛 SourceUnavailable")
        except SourceUnavailable as e:
            assert e.kind == "not_found"
    finally:
        asyncio.run(f._client.aclose())


# ---- 语义模型预热：请求路径不得触发加载（relevance.warm_up / allow_load）----


def test_openalex_headers_property():
    from app.config import Settings

    assert Settings(openalex_api_key="").openalex_headers == {}
    assert Settings(openalex_api_key="K123").openalex_headers == {
        "Authorization": "Bearer K123"
    }


def test_journal_stats_sends_api_key_header(monkeypatch):
    """期刊 2yr_mean 的直连请求必须带 key。

    踩过的坑（2026-09-16 端到端复跑）：只有抓取器带了 key，journal_stats /
    _fetch_cited_5yr / _fetch_ref_years 这些裸 httpx 调用没带 → 走无 key 额度被 429
    → journal_2yr_mean 51/51 全空 → 期刊维度（权重 0.18）整批"缺数据"
    → 档位全部被"缺数据封顶"卡在"谨慎引用"，没有一篇能到"可选引用"。
    """
    import httpx

    from app.config import Settings
    from app.services import journal_stats

    seen: dict = {}

    class _Resp:
        def raise_for_status(self):
            return None

        def json(self):
            return {"summary_stats": {"2yr_mean_citedness": 3.3}}

    def _fake_get(url, params=None, headers=None, timeout=None):
        seen["headers"] = headers
        return _Resp()

    monkeypatch.setattr(httpx, "get", _fake_get)
    monkeypatch.setattr(journal_stats, "get_settings", lambda: Settings(openalex_api_key="K"))
    journal_stats.cache.clear()

    assert journal_stats.get_2yr_mean("https://openalex.org/S1") == 3.3
    assert seen["headers"] == {"Authorization": "Bearer K"}


def test_citation_graph_and_project_network_send_api_key(monkeypatch):
    """引用网络的两处直连也要带 key（否则引用树/项目网络在无 key 额度下静默缺失）。"""
    from app.config import Settings
    from app.services import citation_graph, project_network

    for mod in (citation_graph, project_network):
        monkeypatch.setattr(mod, "get_settings", lambda: Settings(openalex_api_key="K"))
        assert mod.get_settings().openalex_headers == {"Authorization": "Bearer K"}


# ---- 数据溯源：指纹（数据变没变）+ 完整性（算的时候够不够）----
