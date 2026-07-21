from __future__ import annotations

from statebreaker.i18n import bi, current_language


def test_current_language_accepts_supported_values(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("STATEBREAKER_LANG", "en")
    assert current_language() == "en"

    monkeypatch.setenv("STATEBREAKER_LANG", "zh-CN")
    assert current_language() == "zh-CN"

    monkeypatch.setenv("STATEBREAKER_LANG", "bilingual")
    assert current_language() == "bilingual"


def test_current_language_falls_back_to_bilingual(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("STATEBREAKER_LANG", "fr")

    assert current_language() == "bilingual"


def test_bi_renders_selected_language(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("STATEBREAKER_LANG", "en")
    assert bi("中文", "English") == "English"

    monkeypatch.setenv("STATEBREAKER_LANG", "zh-CN")
    assert bi("中文", "English") == "中文"

    monkeypatch.setenv("STATEBREAKER_LANG", "bilingual")
    assert bi("中文", "English") == "English / 中文"
