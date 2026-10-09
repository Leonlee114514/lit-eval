"""Ollama 本地模型 Provider：复用 OpenAI 兼容逻辑，仅端点不同。"""
from __future__ import annotations

import httpx

from app.services.content.llm.openai_compat import OpenAICompatProvider


class OllamaProvider(OpenAICompatProvider):
    """Ollama 的 /v1/chat/completions 与 OpenAI 兼容，免 API key。"""

    name = "ollama"

    def __init__(self, base_url: str = "http://localhost:11434", model: str = "qwen2.5:7b", timeout: float = 120.0):
        self.base_url = base_url
        self.api_key = "ollama"  # 占位
        self.model = model
        self._client = httpx.AsyncClient(timeout=timeout)
