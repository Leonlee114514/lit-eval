"""加载 shared/scoring_rules.json（唯一事实源）。"""
from __future__ import annotations

import json
from functools import lru_cache
from typing import Any

from app.config import get_settings


@lru_cache
def load_rules() -> dict[str, Any]:
    settings = get_settings()
    with open(settings.rules_path, encoding="utf-8") as f:
        return json.load(f)


def get_rules() -> dict[str, Any]:
    return load_rules()
