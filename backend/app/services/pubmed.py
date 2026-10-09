"""PubMed 源：PMID → DOI / 完整元数据（NCBI E-utilities efetch）。

PubMed 直接源用于：
- PMID 导入时即使没有 DOI 也能入库（预印本/部分期刊无 DOI）
- 为生物医学文献补充 PubMed 官方摘要、MeSH 关键词、PublicationType 综述信号
- PMID → DOI 转换仍保留，给旧的前端兼容路径使用

解析用 ElementTree + 少量正则兜底，字段见 pmid_to_meta()。结果缓存 7 天。


PMID 转 DOI 用 efetch 取 XML 里的 ArticleId(IdType=doi)；若 PubMed 无 DOI
（如预印本/部分期刊）返回 None。结果按 PMID 缓存 7 天。
（不用 idconv：那套只覆盖 PMC 收录，Nature 等非 PMC 期刊查不到。）
"""
from __future__ import annotations

import logging
import re

import httpx

logger = logging.getLogger(__name__)

_EFETCH = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
_DOI_RE = re.compile(r'<ArticleId\s+IdType="doi">([^<]+)</ArticleId>', re.IGNORECASE)


async def _efetch_xml(pmid: str, mailto: str) -> str | None:
    """efetch XML，失败返回 None。"""
    try:
        async with httpx.AsyncClient(timeout=25, follow_redirects=True) as client:
            resp = await client.get(
                _EFETCH,
                params={
                    "db": "pubmed", "id": pmid, "retmode": "xml",
                    "tool": "lit-eval", "email": mailto or "user@example.com",
                },
            )
            resp.raise_for_status()
            return resp.text
    except Exception as e:
        logger.warning("PubMed efetch 失败 %s: %s", pmid, e)
        return None


def _parse_pubmed_xml(xml_text: str, pmid: str) -> dict | None:
    """解析 PubmedArticleSet 首条记录 → 抓取层可消费的规范化字段。"""
    import xml.etree.ElementTree as ET

    try:
        root = ET.fromstring(xml_text)
    except Exception as e:
        logger.warning("PubMed XML 解析失败 %s: %s", pmid, e)
        return None

    article = root.find(".//PubmedArticle")
    if article is None:
        return None

    medline = article.find("MedlineCitation")
    art = medline.find("Article") if medline is not None else None
    if art is None:
        return None

    def _t(parent, path):
        node = parent.find(path) if parent is not None else None
        return (node.text or "").strip() if node is not None and node.text else ""

    def _first_text(path):
        node = root.find(path)
        return (node.text or "").strip() if node is not None and node.text else ""

    title = _t(art, "ArticleTitle") or _t(art, "VernacularTitle")
    journal = _t(art, "Journal/Title") or _t(art, "Journal/ISOAbbreviation")

    # 摘要：多段 AbstractText 拼接，保留小节标签（BACKGROUND/METHODS…）
    abstract_parts = []
    for node in art.findall(".//Abstract/AbstractText"):
        text = (node.text or "").strip()
        label = node.attrib.get("Label")
        if label:
            text = f"{label}: {text}"
        if text:
            abstract_parts.append(text)
    abstract = " ".join(abstract_parts) or None

    authors = []
    for node in art.findall(".//AuthorList/Author"):
        last = _t(node, "LastName")
        fore = _t(node, "ForeName")
        collective = _t(node, "CollectiveName")
        if collective:
            name = collective
        elif last:
            name = f"{fore} {last}".strip()
        else:
            name = ""
        aff = _t(node, "AffiliationInfo/Affiliation")
        if name:
            authors.append({"name": name, "affiliation": aff or None})

    keywords = []
    if medline is not None:
        for node in medline.findall(".//MeshHeadingList/MeshHeading/DescriptorName"):
            if node.text and node.text.strip():
                keywords.append(node.text.strip())
    for node in art.findall(".//KeywordList/Keyword"):
        if node.text and node.text.strip():
            keywords.append(node.text.strip())

    year_text = _t(art, "Journal/JournalIssue/PubDate/Year")
    if not year_text:
        medline_date = _t(art, "Journal/JournalIssue/PubDate/MedlineDate")
        m = re.search(r"(18|19|20)\d{2}", medline_date) if medline_date else None
        year_text = m.group(0) if m else ""
    publication_year = int(year_text) if year_text.isdigit() else None

    doi = _first_text(".//PubmedData/ArticleIdList/ArticleId[@IdType='doi']")
    if not doi:
        doi = _t(art, "ELocationID[@EIdType='doi']")
    if not doi:
        m = _DOI_RE.search(xml_text)
        doi = m.group(1).strip() if m else None

    issn = _t(art, "Journal/ISSN") or _t(medline, "MedlineJournalInfo/ISSNLinking")

    pub_types = [n.text.strip().lower() for n in art.findall(".//PublicationTypeList/PublicationType") if n.text]
    work_type = None
    if pub_types:
        joined = " ".join(pub_types)
        work_type = "review" if ("review" in joined or "meta-analysis" in joined) else "article"

    return {
        "pmid": pmid,
        "doi": doi or None,
        "title": title or None,
        "abstract": abstract,
        "journal": journal or None,
        "issn": issn or None,
        "publication_year": publication_year,
        "authors": authors,
        "keywords": keywords,
        "work_type": work_type,
        "volume": _t(art, "Journal/JournalIssue/Volume") or None,
        "issue": _t(art, "Journal/JournalIssue/Issue") or None,
        "pages": _t(art, "Pagination/MedlinePgn") or None,
    }


async def pmid_to_meta(pmid: str, mailto: str = "") -> dict | None:
    """单个 PMID → PubMed 规范化元数据。失败返回 None。"""
    pmid = (pmid or "").strip()
    if not pmid or not pmid.isdigit():
        return None

    from app.cache import cache

    cache_key = f"pmidmeta:{pmid}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached or None

    xml_text = await _efetch_xml(pmid, mailto)
    meta = _parse_pubmed_xml(xml_text, pmid) if xml_text else None
    cache.set(cache_key, meta or "")
    return meta


async def pmid_to_doi(pmid: str, mailto: str = "") -> str | None:
    """单个 PMID → DOI（复用 pmid_to_meta）。失败/无 DOI 返回 None。"""
    meta = await pmid_to_meta(pmid, mailto)
    return (meta or {}).get("doi")


async def batch_pmid_to_doi(pmids: list[str], mailto: str = "") -> dict[str, str]:
    """批量转换，返回 {pmid: doi}，转换失败的键不存在。"""
    out: dict[str, str] = {}
    for p in pmids:
        doi = await pmid_to_doi(p, mailto)
        if doi:
            out[p] = doi
    return out
