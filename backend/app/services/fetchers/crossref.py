"""Crossref 抓取：元数据兜底（权威 DOI 解析）+ 参考自引率。

自引率口径（v3.1，S2 已移除 selfCitationCount 后改用）：
    self_citation_count / reference_count = 参考文献中含本文作者姓氏的条数 / 参考文献总数。
    反向口径（作者在参考文献中自引），与被引自引不同，UI/报告需标注。
    姓氏匹配（reference.author 首 token）对常见姓（如 Zhang）有假阳性，阈值需适配。
"""
from __future__ import annotations

from app.services.fetchers.base import BaseFetcher, FetchResult

CROSSREF_API = "https://api.crossref.org/works/"


class CrossrefFetcher(BaseFetcher):
    name = "crossref"

    async def fetch(self, doi: str) -> FetchResult:
        try:
            raw = await self._get(CROSSREF_API + doi, params={"mailto": self.mailto})
        except Exception as e:
            return FetchResult(source=self.name, ok=False, error=f"{type(e).__name__}: {e}")
        msg = raw.get("message", {})
        author_families = {a.get("family") for a in msg.get("author", []) if a.get("family")}
        self_cites, ref_total = _reference_self_citation(msg, author_families)
        data = {
            "title": _first(msg.get("title")),
            "journal": _container_title(msg.get("container-title")) or _first(msg.get("short-container-title")),
            "issn": _first(msg.get("ISSN")),
            "publication_year": _year(msg),
            "publication_date": _date(msg),
            "abstract": _clean_abstract(msg.get("abstract", "")),
            "self_citation_count": self_cites,
            "reference_count": ref_total,
            "update_to": msg.get("update-to", []),  # 更正/撤稿记录（争议性检测）
            "authors": [
                {
                    "name": f"{a.get('given', '')} {a.get('family', '')}".strip(),
                    "affiliation": _first_affil(a),
                    "orcid": a.get("ORCID", ""),
                    "is_corresponding": None,  # Crossref 无此信息
                }
                for a in msg.get("author", [])
                if a.get("family")
            ],
        }
        return FetchResult(source=self.name, ok=True, data=data)


def _reference_self_citation(msg: dict, author_families: set[str]) -> tuple[int, int]:
    """统计参考文献中含本文作者姓氏的条数。返回 (self_cites, ref_total)。"""
    refs = msg.get("reference", [])
    total = len(refs)
    if not total or not author_families:
        return 0, total
    self_cites = 0
    for r in refs:
        ra = r.get("author")
        if not ra:
            continue
        surname = _ref_surname(ra)
        if surname and surname in author_families:
            self_cites += 1
    return self_cites, total


def _ref_surname(author_str: str) -> str:
    """Crossref reference.author 通常是 'FamilyName, Initials' 或 'FamilyName Initials'。"""
    part = author_str.split(",")[0].strip()
    return part.split()[0] if part else ""


def _first(v) -> str | None:
    if isinstance(v, list):
        return v[0] if v else None
    return v or None


def _container_title(v) -> str | None:
    if isinstance(v, list):
        return " ".join(v) if v else None
    return v or None


def _year(msg: dict) -> int | None:
    for key in ("published-print", "published-online", "issued", "published"):
        date = msg.get(key, {}).get("date-parts", [[None]])
        try:
            if date and date[0] and date[0][0]:
                return int(date[0][0])
        except (TypeError, ValueError):
            continue
    return None


def _date(msg: dict) -> str | None:
    for key in ("published-print", "published-online", "issued", "published"):
        date = msg.get(key, {}).get("date-parts", [[None]])
        try:
            if date and date[0] and date[0][0]:
                parts = [str(p) for p in date[0] if p]
                if len(parts) == 3:
                    return f"{parts[0]}-{int(parts[1]):02d}-{int(parts[2]):02d}"
                if len(parts) == 2:
                    return f"{parts[0]}-{int(parts[1]):02d}-01"
                if parts:
                    return f"{parts[0]}-01-01"
        except (TypeError, ValueError):
            continue
    return None


def _first_affil(a: dict) -> str | None:
    aff = a.get("affiliation", [])
    if aff and aff[0].get("name"):
        return aff[0]["name"]
    return None


def _clean_abstract(abstract: str) -> str | None:
    import re

    if not abstract:
        return None
    # 去掉 JATS XML 标签
    text = re.sub(r"<[^>]+>", " ", abstract)
    text = re.sub(r"\s+", " ", text).strip()
    return text or None
