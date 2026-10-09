"""学科分布校准 v4：单学科（化学领域）收敛版。

用法（需外网，耗时几分钟）：
    python -m app.scripts.calibrate_distributions [--fields 15,16,25]

## 2026-08-02 决策：跨学科可比 → 单学科基准

用户只评估自己学科（化学化工材料）的文献，跨学科等值无意义；且 OpenAlex
subfield 打标对涂层/高分子这类边界论文不稳定——同一篇论文被标到不同 subfield
会得到不同分数（期刊/被引/冻结线四处漂移）。

因此本脚本只校准**一个合并分布**：目标学科集合（默认化学领域 15 化工 /
16 化学 / 25 材料）的全部期刊与论文被引，合并后全部写入各分布文件的
"global" 键。运行期 `distributions.py` 按 subfield_id 查不到就回退
"global"，于是**任何打标都命中化学领域基准**，打标错误不再影响分数。

生成（每个文件只有 "global" 键）：
- journal_lognormal.json      化学领域期刊 2yr_mean 的对数正态 (μ_ln, σ_ln)
- citation_lognormal.json     化学领域论文被引的对数正态 (μ_ln, σ_ln)
- journal_2yr_citedness.json  化学领域期刊 2yr_mean 分布数组（经验分位兜底）
- year_citation_median.json   化学领域近期被引中位数
- field_h_index.json          化学领域活跃作者 h-index 中位数（v4 补上）
"""
from __future__ import annotations

import argparse
import asyncio
import calendar
import json
import logging
import math
import random
import statistics
from datetime import date

import httpx

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

API = "https://api.openalex.org"
# 化学领域：Chemical Engineering / Chemistry / Materials Science（覆盖涂层/高分子）
DEFAULT_FIELDS = ["15", "16", "25"]
REF_YEAR = 2024                 # 被引参考年份（已积累的近期完整年份）
WORKS_PER_FIELD = 2000          # 每 field 抽样论文数（期刊发现 + 被引分布 + 作者发现）
AUTHOR_LIMIT_PER_FIELD = 600    # 每 field 最多收集的去重作者数（h-index 采样，控 API 量）
SOURCE_BATCH = 50               # sources 端点单次批量上限
AUTHOR_BATCH = 50               # authors 端点单次批量上限
_CONCURRENCY = 6                # 并发限流（礼貌抓取）

# v5 活跃度：老文献同龄段位近5年被引分布 / 新文献热点词表
# 2026-08-03 教训：cites 逐篇查询是 OpenAlex 限流触发点（并发 3 也曾触发 ~14.5h 列表端点封禁）。
# 改为串行 + 查询间隔；遇 429 温和中止（保留已完成段位写盘），重跑时续跑。
COHORT_SAMPLES_PER_BAND = 60    # 每个段位采样的论文数（近5年被引分布，逐篇 cites 查询）
COHORT_WINDOWS_PER_FIELD = 3    # 每 field 采样的随机窄日期窗口数（窗口内全量遍历，无排序依赖）。
                                # 老段每周论文少（1985 年 chem-eng 仅 ~12 篇/周），3 窗口 × 3 field 够采 60
CITES_INTERVAL = 0.3            # cites 查询间隔秒数（串行压低请求速率，防 429）
HOT_SAMPLES_PER_FIELD = 200     # 热点词表每 field 采样论文数（按被引降序）
HOT_TOP_TERMS = 200             # TF-IDF 保留的热点词条数
HOT_YEARS = (date.today().year - 1, date.today().year)  # 近2年热点窗口


class _RateLimited(Exception):
    """OpenAlex 429 限流：中止当前阶段、保留已完成进度，稍后重跑续跑。

    尊重 retry-after，不做规避；由 main 捕获后结束该阶段，不中断整个脚本。
    """


def _year_windows(year: int, n: int) -> list[tuple[str, str]]:
    """把某年切成 n 个日期窗口（按月边界）→ 窗口法扩采样。

    n<=1 时返回整年单窗口；n=12 即每月一窗。窗口内日期范围窄、publication_date:asc
    排序稳定，避免深分页重复（8/07 诊断：同请求量下窗口法期刊发现量翻倍）。
    """
    if n <= 1:
        return [(f"{year}-01-01", f"{year}-12-31")]
    wins: list[tuple[str, str]] = []
    step = 12 / n
    for i in range(n):
        start = round(i * step) + 1
        end = min(12, round((i + 1) * step))
        last_day = calendar.monthrange(year, end)[1]
        wins.append((f"{year}-{start:02d}-01", f"{year}-{end:02d}-{last_day:02d}"))
    return wins


def _lognormal_params(values: list[float]) -> dict | None:
    """从正数样本算 ln 分布的 μ/σ；样本 <2 或 σ=0 返回 None（不校准）。"""
    logs = [math.log(v) for v in values if v is not None and v > 0]
    if len(logs) < 2:
        return None
    sigma = statistics.stdev(logs)
    if sigma <= 0:
        return None
    return {"mu": round(statistics.mean(logs), 4), "sigma": round(sigma, 4)}


def _load_existing(out_dir, name: str) -> dict:
    """读取已存在的分布文件（续跑用）；缺省/损坏返回空 dict（不阻断）。"""
    path = out_dir / name
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return data.get("global", {}) if isinstance(data, dict) else {}
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def _write(out_dir, name: str, data: dict) -> None:
    """写分布文件（各阶段增量落盘，崩盘/限流中止不丢已完成部分）。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    with open(out_dir / name, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    logger.info("写入 %s", name)


async def _harvest_field(
    client: httpx.AsyncClient,
    fid: str,
    mailto: str,
    works_n: int = WORKS_PER_FIELD,
    sort_by: str | None = None,
    windows: list[tuple[str, str]] | None = None,
    wpages: int = 1,
    extra_filter: str = "",
    author_roles: str | None = None,
) -> tuple[list[str], list[float], list[str]]:
    """抓取某 field 的近期论文，收集期刊 id / 被引数 / 作者 id。

    author_roles：None 收全部作者；"lead" 只收通讯或一作作者（与运行时
    author.py 以通讯为主、一作混合的打分对象一致——8/09 作者 h-index 重算用）。

    sort_by：OpenAlex 默认按被引降序返回 → 采样会聚焦头部期刊（精英偏置），
    期刊发现集合窄。传 "publication_date:asc" 打破被引排序，覆盖普通期刊。

    windows：日期窗口列表（窗口法扩采样，8/07 诊断确认）。publication_date:asc
    深分页高度重复（大量同日期记录 → 跨页排序不稳定，加大页数新 source 增长极慢，
    实测 --works 8000 只发现 76 期刊）；改为按窄日期窗口各采前几页（窗口内日期
    唯一、排序稳定），相同请求量下期刊发现量翻倍（实测 6 窗口×1 页 = 647 期刊 vs
    直采 6 页 = 280）。无 windows 时走原逻辑（单年 + 连续翻页）。
    """
    source_ids: list[str] = []
    cites: list[float] = []
    author_ids: list[str] = []
    seen_src: set[str] = set()
    seen_author: set[str] = set()

    for win in windows or [None]:
        if win:
            base_filter = f"primary_topic.field.id:{fid},from_publication_date:{win[0]},to_publication_date:{win[1]}"
            pages = wpages
        else:
            base_filter = f"primary_topic.field.id:{fid},publication_year:{REF_YEAR}"
            pages = math.ceil(works_n / 100)
        if extra_filter:
            base_filter += f",{extra_filter}"
        for page in range(pages):
            params = {
                "filter": base_filter,
                "per-page": 100,
                "select": "id,primary_location,cited_by_count,authorships",
                "mailto": mailto,
            }
            if sort_by:
                params["sort"] = sort_by
            resp = await client.get(
                f"{API}/works",
                params=params,
            )
            if resp.status_code == 429:
                raise _RateLimited(int(resp.headers.get("retry-after") or 30))
            resp.raise_for_status()
            for w in resp.json()["results"]:
                src = (w.get("primary_location") or {}).get("source") or {}
                sid = src.get("id")
                if sid and sid not in seen_src:
                    seen_src.add(sid)
                    source_ids.append(sid)
                cites.append(float(w.get("cited_by_count") or 0))
                for a in w.get("authorships") or []:
                    if author_roles == "lead" and not (
                        a.get("is_corresponding") or a.get("author_position") == "first"
                    ):
                        continue  # 只收通讯/一作（与运行时 author.py 打分对象一致）
                    aid = (a.get("author") or {}).get("id")
                    if aid and aid not in seen_author and len(seen_author) < AUTHOR_LIMIT_PER_FIELD:
                        seen_author.add(aid)
                        author_ids.append(aid)
    logger.info("  field %s: %d 期刊 / %d 论文被引 / %d 作者", fid, len(source_ids), len(cites), len(author_ids))
    return source_ids, cites, author_ids


async def _fetch_sources(
    client: httpx.AsyncClient, source_ids: list[str], mailto: str, min_works: int = 0
) -> list[float]:
    """批量查询期刊 2yr_mean_citedness（works 列表响应不裁剪，sources 端点才带）。

    过滤（8/06 诊断确认）：
    - type != "journal" 一律丢弃——代表采样 sort=publication_date:asc 会带入大量
      repository（预印本/聚合平台，works 数百万但 2yr_mean≈0）、book series、
      ebook/conference 等非期刊 source，2yr_mean 趋近 0，会把分布中位拉爆失真。
    - min_works：第二道防线，过滤 works_count 过低的伪期刊/边缘记录。
    """
    vals: list[float] = []
    dropped_nonjournal: int = 0
    dropped_lowworks: int = 0

    async def _batch(chunk: list[str]) -> None:
        nonlocal dropped_nonjournal, dropped_lowworks
        resp = await client.get(
            f"{API}/sources",
            params={
                "filter": f"openalex_id:{'|'.join(chunk)}",
                "per-page": SOURCE_BATCH,
                "select": "id,type,works_count,summary_stats",
                "mailto": mailto,
            },
        )
        resp.raise_for_status()
        for s in resp.json()["results"]:
            v = (s.get("summary_stats") or {}).get("2yr_mean_citedness")
            if v is None or v <= 0:
                continue
            if s.get("type") != "journal":
                dropped_nonjournal += 1
                continue
            if (s.get("works_count") or 0) >= min_works:
                vals.append(float(v))
            else:
                dropped_lowworks += 1

    logger.info("  sources 过滤: type=journal 且 works_count≥%d", min_works)
    if dropped_nonjournal or dropped_lowworks:
        logger.info(
            "  sources 过滤: 丢弃非期刊 %d 个 / works_count<%d 的期刊 %d 个",
            dropped_nonjournal, min_works, dropped_lowworks,
        )

    sem = asyncio.Semaphore(_CONCURRENCY)

    async def _gated(chunk: list[str]) -> None:
        async with sem:
            try:
                await _batch(chunk)
            except httpx.HTTPStatusError as e:
                if e.response.status_code == 429:
                    logger.warning("  sources 批量查询 429 限流，跳过 %d 个期刊", len(chunk))
                else:
                    logger.warning("  sources 批量查询失败 %s: %s", e.response.status_code, e)
            except httpx.HTTPError as e:
                logger.warning("  sources 批量查询异常: %s", e)

    await asyncio.gather(*(_gated(source_ids[i : i + SOURCE_BATCH]) for i in range(0, len(source_ids), SOURCE_BATCH)))
    return vals


async def _fetch_authors(client: httpx.AsyncClient, author_ids: list[str], mailto: str) -> list[float]:
    """批量查询作者 h-index（authors 端点 summary_stats.h_index）。"""
    vals: list[float] = []

    async def _batch(chunk: list[str]) -> None:
        resp = await client.get(
            f"{API}/authors",
            params={
                "filter": f"openalex_id:{'|'.join(chunk)}",
                "per-page": AUTHOR_BATCH,
                "select": "id,summary_stats",
                "mailto": mailto,
            },
        )
        resp.raise_for_status()
        for a in resp.json()["results"]:
            h = (a.get("summary_stats") or {}).get("h_index")
            if h is not None:
                vals.append(float(h))

    sem = asyncio.Semaphore(_CONCURRENCY)

    async def _gated(chunk: list[str]) -> None:
        async with sem:
            try:
                await _batch(chunk)
            except httpx.HTTPStatusError as e:
                if e.response.status_code == 429:
                    logger.warning("  authors 批量查询 429 限流，跳过 %d 位作者", len(chunk))
                else:
                    logger.warning("  authors 批量查询失败 %s: %s", e.response.status_code, e)
            except httpx.HTTPError as e:
                logger.warning("  authors 批量查询异常: %s", e)

    await asyncio.gather(*(_gated(author_ids[i : i + AUTHOR_BATCH]) for i in range(0, len(author_ids), AUTHOR_BATCH)))
    return vals


# ---- v5 活跃度：老文献同龄段位近5年被引分布 ----

async def _get_recent5yr(client: httpx.AsyncClient, work_id: str, mailto: str) -> int | None:
    """单篇论文近5年被引：filter=cites:{id},publication_year:{now-4}-{now} → meta.count。

    2026-08-03 起手动处理重试（原 tenacity 重试耗尽抛 RetryError 会击穿 _gated 的
    httpx.HTTPError 捕获、拖垮整轮校准、丢已完成进度）：
    - 429：首次等 retry-after（封顶 120s）重试一次；仍 429 → raise _RateLimited 中止该阶段
    - 其他 HTTP 错误/超时：首次等 2s 重试一次；仍失败 → 返回 None（单篇跳过，不中断）
    """
    now = date.today().year
    for attempt in (0, 1):
        try:
            resp = await client.get(
                f"{API}/works",
                params={
                    "filter": f"cites:{work_id},publication_year:{now - 4}-{now}",
                    "per-page": 1,
                    "mailto": mailto,
                },
            )
        except httpx.HTTPError as e:
            if attempt == 0:
                await asyncio.sleep(2)
                continue
            logger.warning("  近5年被引查询失败 %s: %s", work_id, e)
            return None
        if resp.status_code == 429:
            ra = min(int(resp.headers.get("retry-after") or 30), 120)
            if attempt == 0:
                logger.warning("  429 限流，等 %ds 重试一次…", ra)
                await asyncio.sleep(ra)
                continue
            raise _RateLimited(ra)
        try:
            resp.raise_for_status()
        except httpx.HTTPStatusError:
            if attempt == 0:
                await asyncio.sleep(2)
                continue
            logger.warning("  近5年被引查询失败 %s: HTTP %d", work_id, resp.status_code)
            return None
        return int(resp.json()["meta"]["count"])
    return None


async def _harvest_cohort_recent5yr(
    client: httpx.AsyncClient, fields: list[str], start: int, end: int, mailto: str
) -> list[float]:
    """某发表年份段位（老文献）的化学论文近5年被引分布（升序 ln(1+x)）。

    start/end 为 publication_year 范围；采样 COHORT_SAMPLES_PER_BAND 篇，
    逐篇串行查近5年被引（filter=cites，CITES_INTERVAL 间隔压速率），产出 ln 升序数组。
    触发 _RateLimited → 向上传播，main 保留已完成段位并中止本阶段（不再整轮崩溃）。

    代表采样（四轮修复）：
    ① 2026-08-04：默认 filter 按被引降序 → 精英偏置。改 date-asc。
    ② 2026-08-05：分页上限 10000 条 + date-asc → 只采到段首/默认日期垃圾记录。
    ③ 2026-08-05：改随机窄日期窗口 filter + type:article。但 `while len<目标` 提前停止导致
       只取了窗口默认排序第 1 页（=窗口内最高被引的 100 篇）→ 精英偏置仍在（6-9 段中位虚高 44）。
    ④ 2026-08-06：**窗口全量遍历 → 池 → 随机抽 COHORT_SAMPLES_PER_BAND 篇**——
       先遍历窗口内全部论文（无排序截断，池代表窗口），再从池随机抽子集做 cites（子集代表池）。
    """
    from datetime import date, timedelta

    seen: set[str] = set()
    pool: list[str] = []
    band_start, band_end = date(start, 1, 1), date(end, 12, 31)
    span_days = max(0, (band_end - band_start).days)
    for fid in fields:
        for _ in range(COHORT_WINDOWS_PER_FIELD):
            # 池已足够（≥3 倍目标）就不再开新窗口——老段窗口小要多开几个，新段一个就够（省配额）
            if len(pool) >= COHORT_SAMPLES_PER_BAND * 3:
                break
            # 随机 7 天窗口起点（避开边缘，避免与跨段边界重叠）
            offset = random.randint(0, max(0, span_days - 6))
            wstart = band_start + timedelta(days=offset)
            wend = wstart + timedelta(days=6)
            flt = (
                f"primary_topic.field.id:{fid},type:article,"
                f"from_publication_date:{wstart.isoformat()},to_publication_date:{wend.isoformat()}"
            )
            page = 1
            while True:
                resp = await client.get(
                    f"{API}/works",
                    params={"filter": flt, "per-page": 100, "page": page, "select": "id", "mailto": mailto},
                )
                if resp.status_code == 429:
                    raise _RateLimited(int(resp.headers.get("retry-after") or 30))
                resp.raise_for_status()
                results = resp.json()["results"]
                if not results:
                    break
                for w in results:
                    wid = w.get("id")
                    if wid and wid not in seen:
                        seen.add(wid)
                        pool.append(wid)
                if len(results) < 100:  # 窗口遍历完毕（不足一页）
                    break
                page += 1
                if page > 20:  # 罕见超大窗口（>2000 篇）防失控
                    break
    # 全量遍历窗口后，随机抽 COHORT_SAMPLES_PER_BAND 篇做 cites（池无排序偏置，随机子集代表池）
    work_ids = random.sample(pool, min(COHORT_SAMPLES_PER_BAND, len(pool)))
    logger.info("  cohort %s-%s: 池 %d 篇 → 随机抽 %d 篇", start, end, len(pool), len(work_ids))

    vals: list[float] = []
    for wid in work_ids:
        try:
            n = await _get_recent5yr(client, wid, mailto)
            if n is not None:
                vals.append(math.log(1 + n))
        except _RateLimited:
            raise  # 429：中止本阶段，main 负责保留已完成段位
        except Exception as e:  # noqa: BLE001 — 兜底：单篇异常跳过，不中断整轮
            logger.warning("  近5年被引异常 %s: %s", wid, e)
        await asyncio.sleep(CITES_INTERVAL)
    return sorted(vals)


# ---- v5 活跃度：新文献热点词表 ----

def _restore_abstract(raw: dict | None) -> str:
    """OpenAlex abstract_inverted_index（{词:[位置]}）→ 还原句子文本。"""
    if not raw:
        return ""
    pos: dict[int, str] = {}
    for word, positions in raw.items():
        for p in positions:
            pos[p] = word
    return " ".join(pos[i] for i in sorted(pos))


async def _fetch_hot_page(client: httpx.AsyncClient, fid: str, page: int, mailto: str) -> list[dict]:
    """抓一页近2年高被引化学论文（标题/摘要/关键词）。

    429 → raise _RateLimited（中止热点阶段，保留已完成产物）；其余错误由调用方跳过该页。
    """
    now = date.today().year
    resp = await client.get(
        f"{API}/works",
        params={
            "filter": f"primary_topic.field.id:{fid},publication_year:{now - 1}-{now}",
            "sort": "cited_by_count:desc",
            "per-page": 100,
            "page": page + 1,
            "select": "id,display_name,abstract_inverted_index,keywords",
            "mailto": mailto,
        },
    )
    if resp.status_code == 429:
        raise _RateLimited(int(resp.headers.get("retry-after") or 30))
    resp.raise_for_status()
    return resp.json()["results"]


async def _build_hot_keywords(client: httpx.AsyncClient, fields: list[str], mailto: str) -> dict:
    """近2年高被引化学论文 → TF-IDF 热点词表（top 200 词 + idf 权重 + top_text）。

    top_text 供运行期语义余弦（fastembed/SBERT）embed；terms 供 TF-IDF 余弦。
    """
    docs: list[str] = []
    seen: set[str] = set()
    for fid in fields:
        for page in range(math.ceil(HOT_SAMPLES_PER_FIELD / 100)):
            try:
                results = await _fetch_hot_page(client, fid, page, mailto)
            except (httpx.HTTPError, httpx.TimeoutException) as e:
                logger.warning("  热点论文页失败 field=%s page=%s: %s", fid, page, e)
                continue
            for w in results:
                wid = w.get("id")
                if wid and wid not in seen:
                    seen.add(wid)
                    text = " ".join(
                        filter(
                            None,
                            [
                                w.get("display_name") or "",
                                _restore_abstract(w.get("abstract_inverted_index")),
                                " ".join(kw.get("display_name", "") for kw in (w.get("keywords") or [])),
                            ],
                        )
                    )
                    if text.strip():
                        docs.append(text)
                    if len(docs) >= HOT_SAMPLES_PER_FIELD * len(fields):
                        break
            if len(docs) >= HOT_SAMPLES_PER_FIELD * len(fields):
                break
    logger.info("  热点词表语料: %d 篇", len(docs))
    if not docs:
        return {}

    from sklearn.feature_extraction.text import TfidfVectorizer

    vec = TfidfVectorizer(max_features=HOT_TOP_TERMS, stop_words="english", ngram_range=(1, 2), lowercase=True)
    vec.fit(docs)
    terms = vec.get_feature_names_out().tolist()
    idfs = vec.idf_.tolist()
    return {
        "global": {
            "terms": [{"term": t, "idf": round(float(w), 4)} for t, w in zip(terms, idfs)],
            "top_text": " ".join(terms),
        }
    }


async def main() -> None:
    parser = argparse.ArgumentParser(description="校准化学领域单学科分布基准")
    parser.add_argument("--fields", default=",".join(DEFAULT_FIELDS), help="OpenAlex field id，逗号分隔，默认化学领域 15,16,25")
    parser.add_argument(
        "--cohort-only",
        action="store_true",
        help="只重算 v5 活跃度段位分布（跳过期刊/被引/h-index harvest）。"
             "用于限流窗口内最小化请求量：期刊/被引/h-index 已校准过时，只需补/重算段位。",
    )
    parser.add_argument(
        "--journal-only",
        action="store_true",
        help="只重算期刊 2yr_mean 分布（跳过被引/h-index/活跃度段位）。"
             "用于拓宽期刊采样、校正期刊中位——不触发被引补查等大配额阶段。",
    )
    parser.add_argument(
        "--works",
        type=int,
        default=WORKS_PER_FIELD,
        help=f"每 field 采样论文数（默认 {WORKS_PER_FIELD}；加大可发现更多期刊，扩大期刊分布样本）。",
    )
    parser.add_argument(
        "--min-works",
        type=int,
        default=1000,
        help="期刊 works_count 下限，过滤代表采样带进的伪期刊/垃圾记录（默认 1000）。",
    )
    parser.add_argument(
        "--citation-only",
        action="store_true",
        help="只重算被引分布（窗口法+日期序+type:article 代表采样，破除被引降序偏置——旧 217 是虚高中位）。"
             "写 citation_lognormal.json + year_citation_median.json（决策门经典阈值 = 非零被引 --median-quantile 分位）。",
    )
    parser.add_argument(
        "--median-quantile",
        type=float,
        default=0.9,
        help="year_citation_median 的分位（默认 0.9 = 非零被引 p90，用户确认 2026-08-07 取代虚高 217）。",
    )
    parser.add_argument(
        "--author-only",
        action="store_true",
        help="只重算作者 h-index 中位（窗口法+日期序+type:article 代表采样，仅通讯/一作作者）。"
             "破除被引降序偏置——旧中位 27 是精英偏置虚高（只采高被引论文的作者）。写 field_h_index.json。",
    )
    parser.add_argument(
        "--pub-windows",
        type=int,
        default=1,
        help="窗口法扩采样：把 REF_YEAR 分成 N 个日期窗口，每窗口按 publication_date:asc "
             "抓 --wpages 页。默认 1（原单年连续翻页）；12 = 每月一窗。诊断确认窗口法"
             "避免深分页重复，相同请求量下期刊发现量翻倍。",
    )
    parser.add_argument(
        "--wpages",
        type=int,
        default=2,
        help="窗口法每窗口抓取页数（默认 2，即每窗 200 篇）。",
    )
    args = parser.parse_args()
    fields = [f.strip() for f in args.fields.split(",") if f.strip()]

    from app.config import get_settings

    settings = get_settings()
    mailto = settings.fetcher_mailto
    out_dir = settings.distributions_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    logger.info("校准目标: %s（默认化学领域：化工 15 / 化学 16 / 材料 25）", fields)

    async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
        # 解析 field 名称（日志用）
        field_names: dict[str, str] = {}
        try:
            resp = await client.get(
                f"{API}/fields", params={"per-page": 50, "select": "id,display_name", "mailto": mailto}
            )
            resp.raise_for_status()
            for f in resp.json()["results"]:
                fid = str(int(f["id"].rstrip("/").split("/")[-1]))
                if fid in fields:
                    field_names[fid] = f["display_name"]
        except (httpx.HTTPError, ValueError):
            pass
        logger.info("解析学科名: %s", ", ".join(f"{fid} {field_names.get(fid, '?')}" for fid in fields))

        # 1)+2) 期刊/被引/h-index harvest（--cohort-only 时跳过：这些已校准过，限流窗口内不重跑）
        if args.journal_only:
            # 只重算期刊 2yr_mean 分布：拓宽期刊采样 → 校正期刊中位（方案1）。
            # 跳过被引/h-index/活跃度段位，把请求量压在最小窗口内。
            # sort=publication_date:asc 打破默认被引降序偏置，让期刊发现覆盖普通期刊而非只采头部顶刊。
            # --pub-windows>1 时用窗口法（避免深分页重复，8/07 诊断确认期刊发现量翻倍）。
            windows = _year_windows(REF_YEAR, args.pub_windows) if args.pub_windows > 1 else None
            try:
                results = await asyncio.gather(
                    *(_harvest_field(
                        client, fid, mailto, args.works,
                        sort_by="publication_date:asc", windows=windows, wpages=args.wpages,
                    ) for fid in fields)
                )
            except _RateLimited as e:
                logger.error("期刊发现触发限流（retry-after %ss），未更新分布文件（保留现有）", e)
                return
            all_sources: list[str] = []
            seen_src: set[str] = set()
            for source_ids, _, _ in results:
                for sid in source_ids:
                    if sid not in seen_src:
                        seen_src.add(sid)
                        all_sources.append(sid)
            logger.info("期刊发现（works/field=%d）: %d 个去重期刊", args.works, len(all_sources))
            jvals = await _fetch_sources(client, all_sources, mailto, min_works=args.min_works)
            if not jvals or len(jvals) < 50:
                logger.warning(
                    "期刊 2yr_mean 有效样本过少（%d），未更新分布文件（保留现有）",
                    len(jvals) if jvals else 0,
                )
                return
            new_median = statistics.median(jvals)
            old = _load_existing(out_dir, "journal_2yr_citedness.json") or []
            old_median = statistics.median(old) if len(old) >= 2 else float("nan")
            if new_median < 0.5:
                # 中位 < 0.5 说明代表采样混入大量伪期刊/垃圾记录（8/06 实测 publication_date:asc 会带 1月1日低质记录）
                logger.warning(
                    "⚠ 新期刊中位过低（%.2f），疑似采样混入垃圾记录，放弃写入保留现有分布（现有中位 %.2f）",
                    new_median, old_median,
                )
                return
            params = _lognormal_params(jvals)
            _write(out_dir, "journal_lognormal.json", {"global": params})
            _write(out_dir, "journal_2yr_citedness.json", {"global": sorted(jvals)})
            logger.info(
                "✅ 期刊 2yr_mean 重算: n=%d 中位 %.2f μ_ln=%.3f σ_ln=%.3f（旧 n=%d 中位 %.2f）",
                len(jvals), new_median, params["mu"], params["sigma"], len(old), old_median,
            )
            return
        if args.citation_only:
            # 只重算被引分布（评分兜底）。窗口法 + 日期序 + type:article 代表采样，
            # 破除被引降序偏置（8/07 诊断：真实中位 2-5 vs 旧 217，虚高 100 倍）。
            # 不更新 year_citation_median（决策门经典阈值），避免静默改变决策语义。
            windows = _year_windows(REF_YEAR, args.pub_windows) if args.pub_windows > 1 else None
            try:
                results = await asyncio.gather(
                    *(_harvest_field(
                        client, fid, mailto, args.works,
                        sort_by="publication_date:asc", windows=windows, wpages=args.wpages,
                        extra_filter="type:article",
                    ) for fid in fields)
                )
            except _RateLimited as e:
                logger.error("被引采样触发限流（retry-after %ss），未更新分布", e)
                return
            all_cites: list[float] = []
            for _, cites, _ in results:
                all_cites.extend(cites)
            pos = [c for c in all_cites if c > 0]
            if all_cites:
                logger.info(
                    "被引代表采样: n=%d 中位 %.0f 非零中位 %.0f 零被引占比 %.0f%%",
                    len(all_cites), statistics.median(all_cites), statistics.median(pos) if pos else 0,
                    100 * (len(all_cites) - len(pos)) / len(all_cites),
                )
            params = _lognormal_params(pos)
            if params:
                _write(out_dir, "citation_lognormal.json", {"global": params})
                logger.info(
                    "✅ citation_lognormal 重算: n=%d μ_ln=%.3f σ_ln=%.3f（旧 μ_ln=5.4548 σ_ln=0.5198）",
                    len(pos), params["mu"], params["sigma"],
                )
            else:
                logger.warning("非零被引样本过少，未更新 citation_lognormal")
            # year_citation_median：决策门"老文献经典阈值"。
            # 用户确认（2026-08-07）用非零被引的 --median-quantile 分位（默认 p90 ≈17），
            # 取代旧值 217（被引降序偏置的虚高中位，真实全量中位仅 2）。
            if pos:
                sv = sorted(pos)
                q = max(0.5, min(args.median_quantile, 0.99))
                pval = float(sv[min(len(sv) - 1, int(q * len(sv)))])
                _write(out_dir, "year_citation_median.json", {"global": pval})
                logger.info(
                    "✅ year_citation_median 更新为非零被引 p%.0f = %.0f（旧 217）",
                    100 * q, pval,
                )
            else:
                logger.warning("非零被引样本为空，未更新 year_citation_median")
            return
        if args.author_only:
            # 只重算作者 h-index 中位：代表采样（窗口法+日期序+type:article）破除被引降序偏置。
            # 旧中位 27 来自默认被引降序采样 → 只采高被引论文的作者（高产/高引学者），虚高。
            # 仅收集通讯/一作作者（用户确认，与运行时 author.py 以通讯为主、一作混合的打分对象一致）。
            windows = _year_windows(REF_YEAR, args.pub_windows) if args.pub_windows > 1 else None
            try:
                results = await asyncio.gather(
                    *(_harvest_field(
                        client, fid, mailto, args.works,
                        sort_by="publication_date:asc", windows=windows, wpages=args.wpages,
                        extra_filter="type:article", author_roles="lead",
                    ) for fid in fields)
                )
            except _RateLimited as e:
                logger.error("作者采样触发限流（retry-after %ss），未更新 field_h_index.json", e)
                return
            all_authors: list[str] = []
            seen_author: set[str] = set()
            for _, _, author_ids in results:
                for aid in author_ids:
                    if aid not in seen_author:
                        seen_author.add(aid)
                        all_authors.append(aid)
            logger.info("作者发现（通讯/一作，代表采样）: %d 位去重作者", len(all_authors))
            hvals = await _fetch_authors(client, all_authors, mailto)
            if len(hvals) < 50:
                logger.warning("作者 h-index 有效样本过少（%d），未更新分布（保留现有）", len(hvals))
                return
            new_median = statistics.median(hvals)
            if new_median < 1:
                # 中位 < 1 说明代表采样异常（h-index 缺失率过高/数据质量问题），拒写保留现有
                logger.warning("⚠ 新 h-index 中位过低（%.1f），疑似采样异常，放弃写入保留现有", new_median)
                return
            old_raw = _load_existing(out_dir, "field_h_index.json")
            old_median = float(old_raw) if isinstance(old_raw, (int, float)) else float("nan")
            _write(out_dir, "field_h_index.json", {"global": new_median})
            logger.info(
                "✅ 作者 h-index 重算: n=%d 中位 %.1f（旧中位 %.1f）",
                len(hvals), new_median, old_median,
            )
            return
        if not args.cohort_only:
            try:
                results = await asyncio.gather(*(_harvest_field(client, fid, mailto, args.works) for fid in fields))
            except _RateLimited as e:
                logger.error("harvest 触发限流（retry-after %ss），跳过本次校准（已写盘产物保留）", e)
                return
            all_sources: list[str] = []
            all_cites: list[float] = []
            all_authors: list[str] = []
            seen_src, seen_author = set(), set()
            for source_ids, cites, author_ids in results:
                for sid in source_ids:
                    if sid not in seen_src:
                        seen_src.add(sid)
                        all_sources.append(sid)
                all_cites.extend(cites)
                for aid in author_ids:
                    if aid not in seen_author:
                        seen_author.add(aid)
                        all_authors.append(aid)
            logger.info("合并: %d 期刊 / %d 论文 / %d 作者", len(all_sources), len(all_cites), len(all_authors))

            # 2) 期刊 2yr_mean + 作者 h-index（可靠阶段，立即增量落盘）
            jvals = await _fetch_sources(client, all_sources, mailto)
            hvals = await _fetch_authors(client, all_authors, mailto)
            logger.info(
                "期刊 2yr_mean: n=%d 中位 %.2f | 作者 h-index: n=%d 中位 %.1f",
                len(jvals), statistics.median(jvals) if jvals else 0,
                len(hvals), statistics.median(hvals) if hvals else 0,
            )
            if jvals:
                _write(out_dir, "journal_lognormal.json", {"global": _lognormal_params(jvals)})
                _write(out_dir, "journal_2yr_citedness.json", {"global": sorted(jvals)})
            if all_cites:
                _write(out_dir, "citation_lognormal.json", {"global": _lognormal_params(all_cites)})
                _write(out_dir, "year_citation_median.json", {"global": statistics.median(all_cites)})
            if hvals:
                _write(out_dir, "field_h_index.json", {"global": statistics.median(hvals)})

        # 3) v5 活跃度：老文献同龄段位近5年被引分布（升序 ln(1+x)）
        now = date.today().year
        bands: list[tuple[str, int, int]] = [
            ("6-9", now - 9, now - 6),
            ("10-19", now - 19, now - 10),
            ("20-29", now - 29, now - 20),
            ("30+", 1970, now - 30),
        ]
        # 续跑：已完成的段位从上次落盘文件恢复，跳过重查（省 OpenAlex 负载）
        recent_by_age: dict[str, list[float]] = _load_existing(out_dir, "recent_citation_by_age.json")
        for band, start, end in bands:
            if band in recent_by_age:
                logger.info("  段位 %s: 已有 %d 样本，续跑跳过", band, len(recent_by_age[band]))
                continue
            try:
                vals = await _harvest_cohort_recent5yr(client, fields, start, end, mailto)
            except _RateLimited as e:
                logger.error("  cites 查询触发限流（retry-after %ss），已保留已完成段位；稍后重跑续跑剩余段位", e)
                break  # 中止 cohort 阶段，但已完成段位已写盘
            if vals:
                recent_by_age[band] = vals
                _write(out_dir, "recent_citation_by_age.json", {"global": recent_by_age})
                logger.info(
                    "  段位 %s: n=%d 近5年被引 ln 中位 %.2f（≈原始 %d）",
                    band, len(vals), statistics.median(vals), round(math.e ** statistics.median(vals) - 1),
                )

        # 4) v5 活跃度：新文献热点词表（近2年高被引论文 TF-IDF）
        if (out_dir / "hot_keywords.json").exists():
            logger.info("  热点词表已存在，跳过")
        else:
            try:
                hot = await _build_hot_keywords(client, fields, mailto)
            except _RateLimited as e:
                hot = {}
                logger.error("  热点词表触发限流（retry-after %ss），跳过；稍后重跑补齐", e)
            if hot.get("global", {}).get("terms"):
                _write(out_dir, "hot_keywords.json", hot)
                logger.info("  热点词表: %d 词", len(hot["global"]["terms"]))

    # 5) 各阶段已增量落盘，收尾列出产物
    logger.info("校准结束。产物: %s", ", ".join(sorted(p.name for p in out_dir.glob("*.json"))))


if __name__ == "__main__":
    asyncio.run(main())
