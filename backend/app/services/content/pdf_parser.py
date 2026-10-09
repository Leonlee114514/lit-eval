"""PDF 文本抽取：pypdf 提取正文，供可重复性检测与 LLM 深度评估。"""
from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def extract_text(pdf_path: str | Path | None) -> str | None:
    """从本地 PDF 提取纯文本。返回 None 表示不可用。"""
    if not pdf_path:
        return None
    path = Path(pdf_path)
    if not path.exists():
        logger.warning("PDF 不存在: %s", path)
        return None
    try:
        from pypdf import PdfReader

        reader = PdfReader(str(path))
        parts = [page.extract_text() or "" for page in reader.pages]
        text = "\n".join(parts)
        return text[:200_000] if text.strip() else None
    except Exception as e:
        logger.warning("PDF 解析失败 %s: %s", path, e)
        return None
