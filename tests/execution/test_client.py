"""HTTP sender URL rendering tests."""

from __future__ import annotations

from statebreaker.config.loader import ScopeGuard
from statebreaker.config.models import ProjectConfig
from statebreaker.execution.client import HttpSender
from statebreaker.execution.sessions import SessionManager


def test_absolute_path_uses_origin_when_base_url_has_path_and_query() -> None:
    sessions = SessionManager("https://example.test/app/page?section=training&lab=demo")
    sender = HttpSender(sessions, scope=_scope())

    assert sender.absolute_url("/app/api/run") == "https://example.test/app/api/run"


def test_session_headers_tolerate_duplicate_cookie_names() -> None:
    sessions = SessionManager("https://example.test")
    client = sessions.client_for("default")
    client.cookies.set("PHPSESSID", "root", domain="example.test", path="/")
    client.cookies.set("PHPSESSID", "app", domain="example.test", path="/app")

    headers = sessions.session_headers("default")

    assert "PHPSESSID=" in headers["Cookie"]


def _scope() -> ScopeGuard:
    return ScopeGuard(
        ProjectConfig.model_validate(
            {
                "project": {"name": "url-test", "base_url": "https://example.test/app"},
                "scope": {"allowed_hosts": ["example.test"]},
            }
        )
    )
