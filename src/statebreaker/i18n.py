"""Small runtime text helper for beginner-facing CLI messages."""

from __future__ import annotations

import os
from typing import Literal

Language = Literal["en", "zh-CN", "bilingual"]

_DEFAULT_LANGUAGE: Language = "bilingual"
_ENV_NAME = "STATEBREAKER_LANG"


def current_language(value: str | None = None) -> Language:
    """Return the selected CLI language, falling back to bilingual text."""
    raw = (value if value is not None else os.environ.get(_ENV_NAME, _DEFAULT_LANGUAGE)).strip()
    if raw == "en":
        return "en"
    if raw == "zh-CN":
        return "zh-CN"
    if raw == "bilingual":
        return "bilingual"
    return _DEFAULT_LANGUAGE


def bi(zh: str, en: str) -> str:
    """Choose Chinese, English, or an English-first bilingual sentence."""
    language = current_language()
    if language == "en":
        return en
    if language == "zh-CN":
        return zh
    return f"{en} / {zh}"


msg = bi
