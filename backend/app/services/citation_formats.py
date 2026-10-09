"""标准引用格式导出：APA / MLA / GB-T 7714（顺序编码制）。

输入 Paper 元数据（title/authors/journal/year/volume/issue/pages/doi），
输出三种通用期刊引文格式字符串。作者名解析启发式：
- "Family, Given" 直接拆
- "Given Family"（英文）取最后一个词为姓
- 单段无空格（中文名）整体作为姓
缺失字段（卷/期/页/DOI）自动省略对应片段，不产生损坏的引用。
"""
from __future__ import annotations

import re

_PUNC_END = re.compile(r"[,.:]\s*$")


def _surname(name: str) -> str:
    """提取姓氏：启发式。"""
    name = (name or "").strip()
    if not name:
        return ""
    if "," in name:  # "Family, Given" 形式
        return name.split(",")[0].strip()
    parts = name.split()
    if len(parts) >= 2:
        return parts[-1].strip(" .")
    return name


def _given(name: str) -> str:
    """提取名的首字母缩写（APA/MLA 用）。返回如 'Y.'. 无法拆分返回 ''。"""
    name = (name or "").strip()
    if not name:
        return ""
    if "," in name:
        given = name.split(",", 1)[1].strip()
    else:
        parts = name.split()
        given = " ".join(parts[:-1]) if len(parts) >= 2 else ""
    if not given:
        return ""
    initials = [p[0].upper() + "." for p in re.split(r"[\s\-]+", given) if p]
    return " ".join(initials)


def _clean_title(title: str | None) -> str:
    return (title or "").strip().rstrip(".")


def _clean_journal(journal: str | None) -> str:
    return (journal or "").strip()


def _doi_part(doi: str | None) -> str:
    if not doi:
        return ""
    return f"https://doi.org/{doi}"


def _bib_details(paper) -> tuple[str, str, str]:
    """(volume/issue, pages, 缺失提示)。属性可能缺失（测试桩/旧记录）。"""
    volume = getattr(paper, "volume", None)
    issue = getattr(paper, "issue", None)
    pages = getattr(paper, "pages", None) or ""
    vol_issue = ""
    if volume:
        vol_issue = f"Vol. {volume}"
        if issue:
            vol_issue += f", No. {issue}"
    return vol_issue, pages, (volume or pages or "")


def _authors_apa(paper) -> str:
    """APA：作者们. 最多 20 个，7 个以上前 6 后省略号。"""
    names = [a.get("name") for a in (paper.authors or []) if a.get("name")]
    if not names:
        return "Anonymous."
    out = []
    for n in names:
        surname = _surname(n)
        given = _given(n)
        out.append(f"{surname}, {given}" if given else surname)
    if len(out) == 1:
        return out[0]
    if len(out) == 2:
        return " & ".join(out)
    if len(out) <= 6:
        return ", ".join(out[:-1]) + f", & {out[-1]}"
    return ", ".join(out[:6]) + ", …"
    # return ", ".join(out[:6]) + ", … " + out[-1] + "."


def _authors_mla(paper) -> str:
    """MLA：作者们. 3 个以上用 et al.。"""
    names = [a.get("name") for a in (paper.authors or []) if a.get("name")]
    if not names:
        return ""
    out = []
    for n in names:
        surname = _surname(n)
        given = _given(n)
        out.append(f"{surname}, {given}" if given else surname)
    if len(out) == 1:
        return out[0] + "."
    if len(out) == 2:
        return f"{out[0]} and {out[1]}."
    return f"{out[0]} et al."


def _authors_gbt(paper) -> str:
    """GB/T 7714：作者1, 作者2, 作者3, 等. 前 3 个。"""
    names = [a.get("name") for a in (paper.authors or []) if a.get("name")]
    if not names:
        return ""
    if len(names) <= 3:
        return ", ".join(names) + "."
    return ", ".join(names[:3]) + ", 等."


def apa(paper) -> str:
    """APA 7th。"""
    year = paper.publication_year or ""
    title = _clean_title(paper.title)
    journal = _clean_journal(paper.journal)
    vol_issue, pages, _ = _bib_details(paper)

    parts = [_authors_apa(paper)]
    parts.append(f"({year})." if year else "(n.d.).")
    parts.append(f"{title}." if title else "")
    if journal:
        seg = f"*{journal}*"
        if vol_issue:
            seg += f", {vol_issue}"
        if pages:
            seg += f", {pages}"
        parts.append(seg + ".")
    doi = _doi_part(paper.doi)
    if doi:
        parts.append(doi)
    return " ".join(p for p in parts if p)


def mla(paper) -> str:
    """MLA 9th。"""
    year = paper.publication_year or ""
    title = _clean_title(paper.title)
    journal = _clean_journal(paper.journal)
    vol_issue, pages, _ = _bib_details(paper)

    parts = [_authors_mla(paper)]
    if title:
        parts.append(f'"{title}."')
    if journal:
        seg = f"*{journal}*"
        if vol_issue:
            seg += f", {vol_issue}"
        parts.append(seg + ",")
    if pages:
        parts.append(f"pp. {pages},")
    if year:
        parts.append(f"{year}.")
    doi = _doi_part(paper.doi)
    if doi:
        parts.append(doi)
    return " ".join(p for p in parts if p)


def gbt7714(paper) -> str:
    """GB/T 7714-2015 顺序编码制：作者. 题名[J]. 刊名, 年, 卷(期): 页码. DOI."""
    year = paper.publication_year or ""
    title = _clean_title(paper.title)
    journal = _clean_journal(paper.journal)
    vol_issue, pages, _ = _bib_details(paper)

    parts = [_authors_gbt(paper)]
    if title:
        parts.append(f"{title}[J].")
    if journal:
        seg = journal
        if vol_issue:
            vol_num = paper.volume
            issue_num = paper.issue
            seg += f", {year}, {vol_num}({issue_num})" if issue_num else f", {year}, {vol_num}"
        else:
            seg += f", {year}"
        if pages:
            seg += f": {pages}"
        parts.append(seg + ".")
    elif year:
        parts.append(f"{year}.")
    doi = _doi_part(paper.doi)
    if doi:
        parts.append(doi)
    return " ".join(p for p in parts if p)


def format_citation(paper, style: str = "apa") -> str | None:
    """按样式生成引用。style ∈ apa|mla|gbt7714。"""
    if not (getattr(paper, "title", None) or getattr(paper, "doi", None)):
        return None
    fn = {"apa": apa, "mla": mla, "gbt7714": gbt7714}.get(style)
    if not fn:
        return None
    return fn(paper)


def format_all(paper) -> dict:
    return {
        "apa": format_citation(paper, "apa"),
        "mla": format_citation(paper, "mla"),
        "gbt7714": format_citation(paper, "gbt7714"),
    }
