"""应用配置：从 .env 读取，路径基于仓库根解析。"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# 仓库根：backend/app/config.py -> backend -> 仓库根
BACKEND_DIR = Path(__file__).resolve().parent.parent
PROJECT_ROOT = BACKEND_DIR.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=BACKEND_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # 服务
    port: int = 8000

    # 抓取礼貌邮箱（Crossref 提速到 50 req/s 礼貌池）
    fetcher_mailto: str = ""

    # OpenAlex 免费 API key：额度提到 10×（openalex.org/settings/api）。
    # 留空仍可用（mailto 认证档），但 OpenAlex 的 search 请求按 $1/1000 次计费，
    # 比 list+filter（$0.10/1000）贵 10 倍，无 key 时更容易触顶日配额。
    openalex_api_key: str = ""

    # ---- 检索式构造（OpenAlex search= 的查询侧优化，见 fetchers/query_builder.py）----
    # raw      = 原样透传用户输入（OpenAlex 会把未分隔的词按 AND 处理）—— 默认，与旧行为一致
    # expanded = 同义词 OR 扩展 + 短语引号；超出 URL 长度上限时自动切块取并集
    search_query_mode: str = "raw"
    # 附加同义词表（表层词=规范词，逗号分隔），如 pu=polyurethane,coat=coating。
    # 只填"语义等价"的词；填"相关但不同义"的词会引入噪声召回。
    search_extra_synonyms: str = ""

    # LLM 评估：none | openai_compat | ollama
    llm_provider: str = "none"
    llm_api_key: str = ""
    llm_base_url: str = "https://api.deepseek.com/v1"
    llm_model: str = "deepseek-v4-flash"

    # 相关性：USE_SBERT=1 才加载语义模型（需 requirements-sbert.txt）
    use_sbert: bool = False

    # 相关性：USE_FASTEMBED=1 启用多语言语义向量（fastembed ONNX 轻量，无需 torch 2GB；
    # 首次运行从 HuggingFace 下载模型，国内网络可设 HF_ENDPOINT=https://hf-mirror.com）
    use_fastembed: bool = False

    # ---- PDF 下载（网站内批量下载全文）----
    unpaywall_email: str = ""        # Unpaywall 必填；留空则回退 fetcher_mailto
    scihub_proxy: str = "http://127.0.0.1:7890"   # Sci-Hub 通道走 Clash 代理；留空 = 不走代理
    use_webbridge: bool = True       # 允许尝试浏览器腿；运行时探测 daemon，不在线自动降级跳过
    webbridge_url: str = "http://127.0.0.1:10086"
    webbridge_session: str = "scihub"

    # ---- 派生 ----
    @property
    def openalex_headers(self) -> dict[str, str]:
        """OpenAlex 请求头（含 API key）。

        **所有直连 OpenAlex 的地方都必须带上它。** 只传 mailto 的请求按"无 key 额度"
        计费（$0.10/天，约 100 次 search），极易被 429 —— 一旦如此，期刊 2yr_mean、
        近5年被引、参考年份等维度会整批缺失，档位被"缺数据封顶"卡死。
        """
        if not self.openalex_api_key:
            return {}
        return {"Authorization": f"Bearer {self.openalex_api_key}"}

    @property
    def shared_dir(self) -> Path:
        return PROJECT_ROOT / "shared"

    @property
    def rules_path(self) -> Path:
        return self.shared_dir / "scoring_rules.json"

    @property
    def distributions_dir(self) -> Path:
        return self.shared_dir / "distributions"

    @property
    def data_dir(self) -> Path:
        return BACKEND_DIR / "data"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "lit_eval.db"

    def ensure_dirs(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.distributions_dir.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    return Settings()
