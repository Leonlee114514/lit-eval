# 文献评估网站（MVP）

基于文献价值评估指标体系（期刊层面 / 论文层面 / 内容质量 / 四步决策）的端到端工具：
输入 DOI → 抓取元数据 → 指标评分 → 内容评估 → 可视化报告。

## 快速开始

### 后端（FastAPI + SQLite）

```bash
cd backend
# 用真实 Python 3.14 创建 venv（避免 PATH 里 lilypond 的 python 干扰）
"/c/Users/Lyonl/AppData/Local/Python/bin/python" -m venv .venv
source .venv/Scripts/activate          # Windows bash；cmd 用 .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env                   # 可选：填 LLM 密钥
uvicorn app.main:app --reload --port 8000
```

### 前端（React + Vite）

```bash
cd frontend
npm install
npm run dev                            # http://127.0.0.1:5173
```

### 一次性校准学科分布（可选，生成化学领域单学科基准）

> 2026-08-02 起收敛为**单学科基准**：只校准化学领域（化工 15/化学 16/材料 25），
> 全部写入分布文件 `global` 键——任何 subfield 打标都回退到该基准，打标错误不再影响分数。
>
> **当前校准基准（2026-08-07，窗口法扩采样）**：
> - 期刊 2yr_mean：样本 **853 个真期刊**，中位 ≈**2.22**，μ_ln=0.456 / σ_ln=1.500
>   （期刊发现用 `--pub-windows 12` 窗口法避免 OpenAlex 深分页重复，同请求量下期刊发现量翻倍；
>   `type=="journal"` 过滤剔除 repository/预印本等非期刊 source）
> - 论文被引（2024）：非零被引对数正态 μ_ln=1.47 / σ_ln=1.08（真实中位仅 ≈2；
>   2026-08-07 破除被引降序偏置，原 217 为虚高）
> - **经典阈值**（决策门"老文献经典"线）：非零被引 p90 ≈ **18**（原 217，用户确认口径）
> - 作者 h-index：中位 **13**（通讯/一作作者代表采样，2026-08-09 破除被引降序精英偏置——原 27 虚高，只采到高被引论文作者）

```bash
cd backend
source .venv/Scripts/activate
python -m app.scripts.calibrate_distributions            # 默认化学领域
python -m app.scripts.calibrate_distributions --fields 15,16,25,22   # 可自定 field 集合
# 只重算期刊分布（省配额）：
python -m app.scripts.calibrate_distributions --journal-only --pub-windows 12 --min-works 1000
```

## 测试

```bash
cd backend
source .venv/Scripts/activate
pytest                 # 含外网集成测试（可用 RUN_NET=0 跳过）
python -m app.scripts.fetch_sample_doi 10.1038/nature14539   # CLI 端到端
```

## 配置（.env）

| 变量 | 说明 |
| --- | --- |
| `FETCHER_MAILTO` | 抓取礼貌邮箱，Crossref 提速 |
| `OPENALEX_API_KEY` | OpenAlex 免费 key（[申请](https://openalex.org/settings/api)），额度提到 **10×**。留空仍可用，但 OpenAlex 的 `search` 请求按 $1/1000 次计费、比 `list+filter`（$0.10/1000）贵 10 倍，无 key 更容易触顶日配额 |
| `SEARCH_QUERY_MODE` | `raw`（默认，原样透传输入）/ `expanded`（同义词 OR 扩展 + 短语引号，超 URL 上限自动切块取并集） |
| `SEARCH_EXTRA_SYNONYMS` | 附加同义词表（`表层词=规范词`，逗号分隔），如 `pu=polyurethane,coat=coating`。只填语义等价的词 |
| `LLM_PROVIDER` | `none`（降级规则档）/ `openai_compat` / `ollama` |
| `LLM_API_KEY` | DeepSeek / Kimi(Moonshot) / OpenRouter 密钥；留空自动降级 |
| `LLM_BASE_URL` / `LLM_MODEL` | OpenAI 兼容端点与模型名（当前配置 DeepSeek `https://api.deepseek.com/v1` + `deepseek-v4-flash`；Kimi/OpenRouter 亦可） |
| `USE_SBERT` | `1` 启用语义相似度（需装 requirements-sbert.txt） |
| `USE_FASTEMBED` | `1` 启用多语言语义向量（需装 requirements-fastembed.txt；ONNX 轻量，无需 torch） |

## 主题搜索的检索式构造

OpenAlex 的 `search=` 覆盖 title/abstract/fulltext，且**未被布尔运算符分隔的词按 `AND` 处理** ——
所以 `waterborne polyurethane coating` 实际是三个词的 AND，召回会被输入措辞锁死。
`SEARCH_QUERY_MODE=expanded` 会用同义词表把它扩成
`(waterborne OR aqueous OR "water-borne" OR waterbased) AND polyurethane AND coating`。

扩展是双刃剑（缩写类词容易引入噪声召回），所以**默认关闭**。开启前先在真实课题上比对：

```bash
cd backend
source .venv/Scripts/activate
# 只比命中数（最省流量）：
python -m app.scripts.compare_search_modes "waterborne polyurethane coating" --per-page 1
# 同时看前 20 条题目、试验附加同义词：
python -m app.scripts.compare_search_modes "waterborne polyurethane coating" \
    --per-page 20 --top 20 --extra-synonyms "pu=polyurethane,coat=coating"
```

实测参考（2026-09，化学/涂层领域）：

| 课题 | raw 命中 | expanded 命中 | 变化 |
| --- | --- |
| `waterborne polyurethane coating` | 10,291 | 38,468 | +273.8% |
| `waterborne polyurethane property synthesis` | 8,682 | 42,679 | +391.6% |
| `水性聚氨酯涂层` | 1,593 | 1,593 | +0%（中文不展开，安全 no-op） |

扩展后前排会浮现更老的奠基文献（如 Dieterich 1981 的 *Aqueous emulsions…* 从榜外进入第 2 位），
这既是同义词召回的效果，也叠加了 OpenAlex「相关性 = 文本相似度 + 被引量」的排序口径，
所以**要人工看前 N 条再决定是否默认开启**。实际使用的检索式会写进后端日志，也随响应
`query_used` 返回、显示在导入页搜索框下方。

## 架构

```
shared/scoring_rules.json   ← 评分规则唯一事实源（前后端共享）
backend/app/
  ├─ services/fetchers/     Crossref · OpenAlex · Semantic Scholar（GS/CNKI 存根）
  ├─ services/metrics/      期刊/被引/活跃度/作者/可重复性/相关性 六路（内容质量由 LLM 出分）
  ├─ services/content/      摘要结构检测 · 相关性 · LLM 抽象层（可降级）
  ├─ services/scoring/      评分聚合：七因子综合分 + 五维雷达
  ├─ services/citation_network.py  单篇 GNN：PageRank/社区发现/文献耦合/教科书式引用
  ├─ services/project_network.py   项目级 GNN：共享参考文献/核心文献/论文耦合社区
  ├─ services/decision.py   四步决策 · report.py 报告生成
  └─ api/                   REST 接口
frontend/src/               React 18 + ECharts（雷达/仪表盘/引用树/GNN）
  UI 方向：Dark Glass Workspace（暗色玻璃默认 + 浅色玻璃切换，见 design-system/lit-eval-glass/）
```

## 已实现功能（P0 + P1 完成）

- **LLM 深度评估**：Kimi/DeepSeek/OpenRouter 真实调用（无密钥自动降级规则档）
- **PDF 全文上传**：源站无摘要 → 上传全文后内容分/图表检测/LLM 评估真实生效
- **引用脉络**：真实引用树（引用本文 top8 + 本文引用 top8）
- **单篇 GNN 引用网络**：PageRank 中心性、社区发现、文献耦合、共被引支持、教科书式引用识别
- **项目级 GNN**：把项目内全部论文投影到共享参考文献网络，输出核心文献、论文 PageRank 排序、耦合社区、教科书式引用候选
- **批量导入**：DOI/标题/CSV/BibTeX/PMID 四种方式；PMID 走 PubMed 直接源（无 DOI 也入库）
- **标准引用格式**：APA / MLA / GB-T 7714 导出
- **图表质量检测**：解析 PDF 图注，区分原始数据图 vs 示意图
- **争议性检测**：OpenAlex / Crossref 免费信号自动标记撤稿 → 决策强制拒绝
- **推荐分组视图**：高优先级/可选/不推荐/待评估 四卡片

## 扩展位（MVP 未实现）

- Google Scholar / CNKI 抓取（存根，合规与反爬原因）
- Celery 任务队列（`tasks.py` 接口已抽象）
- PostgreSQL（`db.py` 换连接串即可）
- CiteScore（Scopus 付费 key）
