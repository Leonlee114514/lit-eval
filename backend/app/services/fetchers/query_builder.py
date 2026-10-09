"""OpenAlex 检索式构造：同义词 OR 扩展 + 短语引号 + URL 长度切块。

依据 OpenAlex 官方文档（help.openalex.org/api/searching.md，2026-09 核对）：

- ``search=`` 覆盖 title / abstract / fulltext 三个字段。
- **未被布尔运算符分隔的词按 AND 处理** —— 所以 ``waterborne polyurethane coating``
  实际是三个词的 AND，召回被用户措辞锁死。这是本模块要解决的第一个问题。
- 支持 ``AND`` / ``OR`` / ``NOT``（必须大写）、双引号短语、``"a b"~N`` 邻近、
  ``term~N`` 模糊（编辑距离 0-2）、``*`` ``?`` 通配（通配需 ``search.exact``）。
  每次请求只允许一个 search 参数（``search`` / ``search.exact`` / ``search.semantic`` 三选一）。
- 整个请求 URL 上限约 4KB，超出返回 400 ``Request URL too long``。官方解法是把 OR
  列表切块、分别请求、客户端取并集，并给出等价性：
  ``(X AND (a OR b OR c)) == (X AND (a OR b)) ∪ (X AND (c OR d))``。
- 计费：``search`` 请求 $1/1000 次，``list+filter`` $0.10/1000 次 —— **search 贵 10 倍**，
  所以"扩展检索式"是有成本的，默认关闭（``SEARCH_QUERY_MODE=raw``）。

本模块**只做纯字符串构造，不发任何网络请求**，因此可离线单测。
``mode="raw"`` 时输出与引入本模块前逐字一致（原样透传用户输入）。
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from urllib.parse import urlencode

# OpenAlex 的布尔运算符必须大写；查询里一旦出现即视为"专家模式"原样透传，
# 避免我们的扩展破坏用户刻意写的检索式。
_BOOLEAN_RE = re.compile(r"\b(?:AND|OR|NOT)\b")

# 用户手写的双引号短语：视为原子词，保留但不再拆开做同义词扩展（他已明确要精确匹配）
_PHRASE_RE = re.compile(r'"([^"]+)"')

# Lucene 特殊字符：出现任一即必须引号包裹，否则会被当成语法（`-` 被当排除符、
# 空格被当多词、`()` 被当分组等）。
# 注意 CJK 汉字**不在**其中：把中文整串加引号会让 OpenAlex 的（词干化）search
# 返回 0 命中 —— 实测 `水性聚氨酯涂层` 裸串 1593 命中、加引号后 0 命中。
# 这类"ASCII 中心假设"在中文上会静默失效（skill 的 chinese_literature_api_protocol
# §Chinese title matching 记录过同类坑：归一化必须中文感知且保守）。
_LUCENE_SPECIAL_RE = re.compile(r'[\s\-+!(){}\[\]^"~*?:\\/]')
_RESERVED_WORDS = {"and", "or", "not"}

# 请求 URL 上限约 4094 字节，select/其它参数占掉一部分。这里给 search 值留的
# **编码后**预算：引号与括号 URL 编码后会膨胀到 3 字节，所以必须按编码后长度衡量。
DEFAULT_MAX_ENCODED_LEN = 3000

# 切块上限：防止病态的同义词扩展把贵的 search 档配额打爆
MAX_VARIANTS = 8


@dataclass(frozen=True)
class QueryPlan:
    """一次检索的完整计划（纯数据，可日志/可展示/可单测）。"""

    variants: tuple[str, ...]          # search= 取值；多个表示需串行请求后取并集
    mode: str                          # raw | expanded | expert_passthrough
    groups: tuple[tuple[str, ...], ...] = ()   # 概念组（调试与前端展示用）
    note: str = ""

    @property
    def is_multi(self) -> bool:
        return len(self.variants) > 1

    @property
    def digest(self) -> str:
        """检索式指纹，用于缓存键区分不同扩展结果。"""
        return hashlib.sha1("|".join(self.variants).encode("utf-8")).hexdigest()[:12]


def parse_extra_synonyms(raw: str | None) -> dict[str, str]:
    """解析 .env 里的附加同义词表：``pu=polyurethane, aqueous=waterborne``。

    形状与 ``relevance._SYNONYMS`` 一致（表层词 → 规范词），便于合并。
    忽略空项与缺 ``=`` 的项；键值都 strip，键转小写；自映射（a=a）丢弃。
    """
    out: dict[str, str] = {}
    if not raw:
        return out
    for item in raw.split(","):
        item = item.strip()
        if not item or "=" not in item:
            continue
        surface, _, canon = item.partition("=")
        surface, canon = surface.strip().lower(), canon.strip().lower()
        if surface and canon and surface != canon:
            out[surface] = canon
    return out


def invert_synonym_map(synonym_map: dict[str, str] | None) -> dict[str, list[str]]:
    """把"表层→规范"的归一表反转成"规范→[全部表层]"的 OR 扩展组。

    结果含规范词自身；去重并排序，保证输出确定性（便于单测与缓存）。
    注意：只应把**语义等价**的词放进同义词表 —— 相关但不同义的词会引入噪声召回。
    """
    groups: dict[str, set[str]] = {}
    for surface, canon in (synonym_map or {}).items():
        if not surface or not canon:
            continue
        groups.setdefault(canon, set()).add(canon)
        groups.setdefault(canon, set()).add(surface)
    return {canon: sorted(forms) for canon, forms in groups.items()}


def quote_term(term: str) -> str:
    """按需加双引号：只在含 Lucene 特殊字符或本身是保留词时才包裹。

    单 token 加引号在 Lucene 里等价于不加，所以"不加"是默认；但中文等 CJK
    文本一旦被加引号会整串变成一个词条，实测直接归零，故绝不因"非 ASCII"而加引号。
    """
    term = (term or "").strip()
    if not term:
        return ""
    if _LUCENE_SPECIAL_RE.search(term) or term.lower() in _RESERVED_WORDS:
        return f'"{term}"'
    return term


def render_group(terms: list[str]) -> str:
    """渲染一个概念组：单词直接输出，多词用 ``(a OR b)``。"""
    parts = [quote_term(t) for t in terms]
    parts = [p for p in parts if p]
    if not parts:
        return ""
    if len(parts) == 1:
        return parts[0]
    return "(" + " OR ".join(parts) + ")"


def render_query(groups: list[list[str]]) -> str:
    """渲染完整检索式：概念组之间用 AND 连接。"""
    parts = [render_group(g) for g in groups]
    return " AND ".join(p for p in parts if p)


def encoded_len(search_value: str) -> int:
    """search 值在 URL 里的实际字节数（引号/括号会膨胀到 3 字节）。"""
    return len(urlencode({"search": search_value}))


def _split_to_budget(
    groups: list[list[str]], max_encoded_len: int, max_variants: int = MAX_VARIANTS
) -> list[list[list[str]]]:
    """把概念组切成若干"变体"，使每个变体的 search= 编码长度不超预算。

    切法：只拆当前最长的那个 OR 组（``G = (t1..tn)`` → ``(t1..tk)`` 与 ``(t(k+1)..tn)``），
    其余组原样保留。因为 AND 对 OR 可分配：

        B AND (t1 OR .. OR tn)
          == (B AND (t1 OR .. OR tk)) ∪ (B AND (t(k+1) OR .. OR tn))

    所以各变体取并集与原式精确等价 —— **前提是每段命中数不超过 per-page 上限**，
    否则并集只是"封顶后的并集"（见 OpenAlexFetcher.search_works_union 的说明）。
    """
    variants: list[list[list[str]]] = [[list(g) for g in groups]]
    while len(variants) < max_variants:
        worst = max(variants, key=lambda gs: encoded_len(render_query(gs)))
        if encoded_len(render_query(worst)) <= max_encoded_len:
            break
        idx = max(range(len(worst)), key=lambda i: len(worst[i]))
        if len(worst[idx]) < 2:
            break  # 已无可再拆的 OR 组（单 token 组无法拆）
        half = len(worst[idx]) // 2
        left = [list(g) for g in worst]
        right = [list(g) for g in worst]
        left[idx] = list(worst[idx][:half])
        right[idx] = list(worst[idx][half:])
        variants.remove(worst)
        variants.extend([left, right])
    return variants


def build_query_plan(
    query: str,
    mode: str = "raw",
    extra_synonyms: str | dict[str, str] | None = None,
    base_synonyms: dict[str, str] | None = None,
    max_encoded_len: int = DEFAULT_MAX_ENCODED_LEN,
    max_variants: int = MAX_VARIANTS,
) -> QueryPlan:
    """把用户输入编译成 OpenAlex 检索计划。

    - ``mode="raw"``：原样透传（默认，与旧行为逐字一致）
    - ``mode="expanded"``：按同义词表做 OR 扩展；用户手写的引号短语保留为原子词；
      查询里含 ``AND``/``OR``/``NOT`` 时判为专家模式、原样透传不做扩展。
    """
    raw = (query or "").strip()
    if not raw:
        return QueryPlan(variants=(), mode="raw", note="空查询")

    normalized_mode = (mode or "raw").strip().lower()
    if normalized_mode not in ("raw", "expanded"):
        normalized_mode = "raw"

    if normalized_mode == "raw":
        return QueryPlan(variants=(raw,), mode="raw")

    # 专家模式：用户自己写了布尔运算符，不猜他的意图
    if _BOOLEAN_RE.search(raw):
        return QueryPlan(
            variants=(raw,),
            mode="expert_passthrough",
            note="查询含 AND/OR/NOT，按专家模式原样透传（不做同义词扩展）",
        )

    extra = (
        extra_synonyms
        if isinstance(extra_synonyms, dict)
        else parse_extra_synonyms(extra_synonyms)
    )
    merged: dict[str, str] = dict(base_synonyms or {})
    merged.update(extra or {})
    canon_groups = invert_synonym_map(merged)

    # 1) 先摘出用户手写的引号短语，作为原子词保留
    groups: list[list[str]] = []
    for p in _PHRASE_RE.findall(raw):
        p = p.strip()
        if p:
            groups.append([p])
    rest = _PHRASE_RE.sub(" ", raw)

    # 2) 其余按空白切词，命中同义词表则展开成 OR 组
    expanded_any = False
    for tok in rest.split():
        tok = tok.strip()
        if not tok:
            continue
        # 两层查表：先按"表层词 → 规范词"，再按规范词直接取组
        # （后者让用户直接输入规范词时也能展开，如直接搜 "waterborne"）
        canon = merged.get(tok.lower(), tok.lower())
        forms = canon_groups.get(canon)
        if forms and len(forms) > 1:
            # 用户原词排首位，其余按字典序 —— 输出确定性，便于单测与缓存
            ordered = [tok] + [f for f in forms if f.lower() != tok.lower()]
            groups.append(ordered)
            expanded_any = True
        else:
            groups.append([tok])

    if not groups:
        return QueryPlan(variants=(raw,), mode="raw", note="无可用词项，回退原查询")

    variants = _split_to_budget(groups, max_encoded_len, max_variants)
    notes: list[str] = []
    if len(variants) > 1:
        notes.append(
            f"检索式超出 URL 长度预算，切为 {len(variants)} 段串行请求后取并集"
            "（并集与原式等价，见 query_builder 模块注释）"
        )
    if not expanded_any:
        notes.append("未命中同义词表，仅做分词 AND 形式")
    if any(encoded_len(render_query(v)) > max_encoded_len for v in variants):
        notes.append(f"单段仍超 {max_encoded_len} 字节预算，OpenAlex 可能返回 400")

    return QueryPlan(
        variants=tuple(render_query(v) for v in variants),
        mode="expanded",
        groups=tuple(tuple(g) for g in groups),
        note="；".join(notes),
    )
