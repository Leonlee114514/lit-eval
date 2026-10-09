"""关键词相关性：论文摘要与用户研究主题的相似度。

- 默认：jieba 分词 + **主题词覆盖率**（摘要中命中主题词的占比，0-1 可解释）。
  2026-08-02 起替代 TF-IDF 余弦——实测余弦被"短主题 vs 长摘要"文档长度差压缩到
  近 0（真正相关的论文也仅 0.01-0.06），绝对阈值无法使用；覆盖率对相关论文给出
  0.4-0.5+、边缘论文 0.1-0.2，阈值化才可解释。
- **领域同义词归一**：匹配前把表层词映射到规范词（如 aqueous→waterborne，Dieterich
  诊断三件套之二——正文用 "aqueous"、主题词是 "waterborne" 时字面 token 对不上）。
  表只收语义等价的替换词，避免宽松误配。
- v4 跨语中性：主题与摘要脚本不一致（中文主题 vs 英文摘要）时字面覆盖率不可靠，
  改用主题中的英文词（用户英文关键词）覆盖率；命中不足 → 中性 0.5
  （method=crosslingual），避免所有英文文献被误判不相关而档位封顶。
- USE_SBERT=1 且已安装 sentence-transformers：paraphrase-multilingual-MiniLM-L12-v2
  （多语语义相似度，中英主题/摘要可跨语匹配）。
- USE_FASTEMBED=1（可选，fastembed ONNX 多语言向量，无需 torch 2GB）：
  paraphrase-multilingual-MiniLM-L12-v2 余弦；import 失败优雅回退覆盖率/跨语中性。
  语义向量天然识别同义/近义/跨语（aqueous↔waterborne 实测余弦 0.65），无需手工词表。
- 输出记 method 字段（coverage / sbert / fastembed / crosslingual / no_abstract / no_topic）。
- 无摘要时（OpenAlex 常见）改用**标题**代替摘要打分——标题本身含语义信号，
  fastembed 对"主题 vs 标题"的余弦有区分度；method 带 _title 后缀
  （fastembed_title / sbert_title / coverage_title），basis 注明"无摘要"。
  标题也没有才中性 0.5（no_abstract）。
"""
from __future__ import annotations

import logging
import re
import threading

from app.config import get_settings

logger = logging.getLogger(__name__)

_sbert_model = None  # 惰性加载
_fastembed_model = None
_fastembed_lock = threading.Lock()  # 防止启动预热与请求并发重复加载模型

# 脚本检测（跨语场景用）：CJK 汉字 vs 拉丁字母
_CJK = re.compile(r"[㐀-鿿]")
_LATIN = re.compile(r"[A-Za-z]")

# 领域同义词归一表：表层词 → 规范词（覆盖度匹配前把两边 token 归一化）。
# 化学/涂层领域；只收语义等价的替换词，不收"相关但不同义"的词（避免宽松误配）。
# 注意：键必须是 jieba 单 token 形态（连字符词会被 jieba 拆开，无法作为整词键）。
_SYNONYMS = {
    # 水性体系（Dieterich 案例：正文 "aqueous" ↔ 主题词 "waterborne"）
    "aqueous": "waterborne",
    "waterbased": "waterborne",
    "water-borne": "waterborne",  # 若 jieba 未拆分则命中，拆分了则由整句归一兜底
    # 交联体系动词形态归一
    "crosslinked": "crosslink",
    "crosslinking": "crosslink",
    "photocrosslinked": "photocrosslink",
    "photocrosslinking": "photocrosslink",
}


def _normalize_token(t: str) -> str:
    """单个 token 同义词归一；无映射则原样返回。"""
    return _SYNONYMS.get(t, t)


def _token_set(text: str) -> set[str]:
    """分词 + 过滤 + 同义词归一，供覆盖率匹配使用。"""
    return {_normalize_token(t) for t in _jieba_tokenize(text.lower()) if t.strip()}


def _get_sbert():
    global _sbert_model
    if _sbert_model is None:
        try:
            from sentence_transformers import SentenceTransformer

            _sbert_model = SentenceTransformer("paraphrase-multilingual-MiniLM-L12-v2")
            logger.info("SBERT 模型已加载")
        except Exception as e:
            logger.warning("SBERT 加载失败（%s），回退覆盖率", e)
            _sbert_model = False
    return _sbert_model


def _get_fastembed(allow_load: bool = True):
    """可选：fastembed（ONNX 多语言，无需 torch 2GB）。加载失败 → False（优雅回退）。

    2026-08-03：模型换为 paraphrase-multilingual-MiniLM-L12-v2（fastembed 0.8 支持且
    多语跨中英；multilingual-e5-small 在 0.8 已不支持）。首次从 HuggingFace 下载 ~220MB，
    国内网络可设 HF_ENDPOINT=https://hf-mirror.com；下载后本地缓存，后续秒载。

    ``allow_load=False``（请求路径用）：模型尚未就绪时直接返回 None，**绝不触发加载**。
    首次加载可能触发模型下载并由 fastembed 内部重试数分钟（实测约 2 分钟），放在请求里
    会撞爆前端超时；就绪与否交给启动时的 ``warm_up()`` 负责。
    """
    global _fastembed_model
    if _fastembed_model is not None:  # 已就绪或已确认失败（False）→ 直接返回
        return _fastembed_model
    if not allow_load:
        return None
    with _fastembed_lock:
        if _fastembed_model is None:  # 双重检查：可能已被预热线程加载完
            try:
                from fastembed import TextEmbedding

                _fastembed_model = TextEmbedding(
                    "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
                )
                logger.info("fastembed 多语言模型已加载")
            except Exception as e:
                logger.warning("fastembed 加载失败（%s），回退覆盖率/跨语中性", e)
                _fastembed_model = False
    return _fastembed_model


def warm_up() -> bool:
    """预热语义模型，供启动时后台线程调用；返回模型是否可用。

    把"首次加载"从请求路径挪到启动阶段：即便失败也只是回退覆盖率，
    不会再让后端重启后的第一次搜索白等几分钟。
    """
    return bool(_get_fastembed(allow_load=True))


def _jieba_tokenize(text: str) -> list[str]:
    import jieba

    jieba.setLogLevel(60)  # 静默
    return [t for t in jieba.lcut(text) if t.strip()]


def _coverage_similarity(topic_text: str, abstract: str) -> float:
    """主题词覆盖率：摘要中命中主题词（jieba token，含同义词归一）的占比。

    对小写化后的 topic 与 abstract 分别取 token 集合并同义词归一，逐一检查主题词
    是否出现在摘要（含简单英文复数：t 或 t+'s'）。相关论文 0.4-0.5+，边缘 0.1-0.2。
    """
    topic = _token_set(topic_text)
    doc = _token_set(abstract)
    if not topic:
        return 0.5
    hits = sum(1 for t in topic if t in doc or t + "s" in doc)
    return hits / len(topic)


def _coverage_english_only(topic_text: str, abstract: str) -> float | None:
    """跨语场景：只用主题里的拉丁 token（用户英文关键词）算覆盖率。

    中文主题 + 英文摘要时，全量覆盖率把中文 token 也算进分母、必然趋 0；
    这里只看英文词，命中率才反映真实语义信号。无英文词 → None。
    """
    toks = [
        _normalize_token(t) for t in _jieba_tokenize(topic_text.lower())
        if _LATIN.search(t) and not _CJK.search(t)
    ]
    if not toks:
        return None
    doc = _token_set(abstract)
    hits = sum(1 for t in toks if t in doc or t + "s" in doc)
    return hits / len(toks)


def _fastembed_hint(score: float) -> str:
    """覆盖率偏低时提示启用 fastembed（语义匹配能识别同义/跨语，超越字面归一）。

    只在 coverage 路径调用；sbert/fastembed 本身已是语义匹配，无需再提示。
    """
    settings = get_settings()
    if score < 0.35 and not settings.use_fastembed:
        return "；覆盖率偏低——若属同义/跨语场景，可设 USE_FASTEMBED=1 启用语义匹配"
    return ""


def _sbert_similarity(topic_text: str, abstract: str) -> float:
    model = _get_sbert()
    if not model:
        return _coverage_similarity(topic_text, abstract)
    from numpy.linalg import norm

    a, b = model.encode([_clean(topic_text), _clean(abstract)])
    cos = float((a @ b) / (norm(a) * norm(b)))
    return max(0.0, min(1.0, cos))


def _fastembed_similarity(topic_text: str, abstract: str, allow_load: bool = True) -> float | None:
    model = _get_fastembed(allow_load=allow_load)
    if not model:
        return None
    import numpy as np

    # fastembed 0.8 的 embed() 返回 list[ndarray]（旧版为生成器）→ list() 兼容两种
    va, vb = list(model.embed([_clean(topic_text), _clean(abstract)]))[:2]
    cos = float((va @ vb) / (np.linalg.norm(va) * np.linalg.norm(vb)))
    return max(0.0, min(1.0, cos))


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _score_by_title(
    topic_text: str, topic: str, title: str | None, allow_load: bool = True
) -> dict:
    """无摘要时用标题代替摘要打分：算法与摘要路径一致，method 加 _title 后缀。

    标题比摘要短、噪音更大，分数仅供参考；basis 明确注明"无摘要，改用标题"。
    标题也没有 → 中性 0.5（no_abstract）。
    """
    if not title or not title.strip():
        return {"score": 0.5, "method": "no_abstract", "topic": topic, "basis": "无摘要且无标题"}

    settings = get_settings()
    if settings.use_fastembed:
        val = _fastembed_similarity(topic_text, title, allow_load=allow_load)
        if val is not None:
            return {"score": round(val, 3), "method": "fastembed_title", "topic": topic,
                    "basis": "无摘要，改用标题语义相似度（fastembed）"}
    if settings.use_sbert:
        val = _sbert_similarity(topic_text, title)
        return {"score": round(val, 3), "method": "sbert_title", "topic": topic,
                "basis": "无摘要，改用标题语义相似度（SBERT）"}
    val = _coverage_similarity(topic_text, title)
    return {"score": round(val, 3), "method": "coverage_title", "topic": topic,
            "basis": f"无摘要，改用标题词覆盖率 {val:.2f}"}


def score(
    topic: str,
    keywords: list[str],
    abstract: str | None,
    title: str | None = None,
    allow_load: bool = True,
) -> dict:
    """返回 {score: 0-1, method, topic, basis}。

    无摘要（OpenAlex 常见）时用 title 代替摘要打分；无摘要也无标题才中性 0.5。

    ``allow_load=False`` 供请求路径使用（如搜索候选重排）：只复用已加载的语义模型，
    自己绝不触发加载 —— 首次加载可能是分钟级，不能放进请求里。
    """
    topic_text = (topic or "") + " " + " ".join(keywords or [])
    if not topic_text.strip():
        return {"score": 0.5, "method": "no_topic", "topic": topic, "basis": "无主题/关键词"}

    if not abstract or not abstract.strip():
        return _score_by_title(topic_text, topic, title, allow_load=allow_load)

    settings = get_settings()
    if settings.use_fastembed:
        val = _fastembed_similarity(topic_text, abstract, allow_load=allow_load)
        if val is not None:
            return {"score": round(val, 3), "method": "fastembed", "topic": topic,
                    "basis": "多语言语义向量（paraphrase-multilingual-MiniLM-L12-v2，fastembed ONNX）"}

    if settings.use_sbert:
        val = _sbert_similarity(topic_text, abstract)
        return {"score": round(val, 3), "method": "sbert", "topic": topic,
                "basis": "多语语义相似度（SBERT）"}

    val = _coverage_similarity(topic_text, abstract)
    # v4 跨语中性：主题含中文、摘要为英文（或反之）→ 字面覆盖率不可靠，
    # 改用主题里的英文词（用户英文关键词）覆盖率，避免被中文 token 稀释分母。
    # 用"是否含 CJK"而非主脚本占比判定——混合主题（中文+英文关键词）字符数上
    # 英文常占优，占比法会漏判跨语场景。
    has_cjk_topic = bool(_CJK.search(topic_text))
    has_cjk_abs = bool(_CJK.search(abstract))
    if has_cjk_topic != has_cjk_abs:
        eng_val = _coverage_english_only(topic_text, abstract)
        if eng_val is None or eng_val < 0.3:
            return {"score": 0.5, "method": "crosslingual", "topic": topic,
                    "basis": "主题与摘要脚本不一致（中文主题 vs 英文摘要），英文关键词命中不足，"
                             "字面覆盖率不可靠，按中性 0.5（建议补充英文关键词或启用 USE_FASTEMBED）"}
        return {"score": round(eng_val, 3), "method": "coverage", "topic": topic,
                "basis": f"跨语场景下英文关键词覆盖率 {eng_val:.2f}{_fastembed_hint(eng_val)}"}
    return {"score": round(val, 3), "method": "coverage", "topic": topic,
            "basis": f"主题词覆盖率{_fastembed_hint(val)}"}
