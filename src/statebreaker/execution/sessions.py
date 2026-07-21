"""Per-identity HTTP sessions: isolated cookie jars, headers, and clients."""

from __future__ import annotations

import httpx

from statebreaker.config.models import SessionConfig


class SessionManager:
    """Owns one ``httpx.AsyncClient`` per test identity.

    Each identity gets an isolated cookie jar and default headers so that
    cross-user experiments never share ambient state.
    """

    def __init__(
        self,
        base_url: str,
        sessions: dict[str, SessionConfig] | None = None,
        *,
        timeout_seconds: float = 15.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._configs = dict(sessions or {})
        self._timeout = timeout_seconds
        self._transport = transport
        self._clients: dict[str, httpx.AsyncClient] = {}

    @property
    def base_url(self) -> str:
        return self._base_url

    def known_sessions(self) -> list[str]:
        return sorted(set(self._configs) | set(self._clients))

    def client_for(self, session_id: str) -> httpx.AsyncClient:
        if session_id not in self._clients:
            config = self._configs.get(session_id, SessionConfig())
            cookies = httpx.Cookies()
            for name, value in config.cookies.items():
                cookies.set(name, value)
            self._clients[session_id] = httpx.AsyncClient(
                base_url=self._base_url,
                headers=dict(config.headers),
                cookies=cookies,
                timeout=self._timeout,
                follow_redirects=True,
                transport=self._transport,
            )
        return self._clients[session_id]

    def snapshot_cookies(self, session_id: str) -> dict[str, str]:
        client = self._clients.get(session_id)
        if client is None:
            return {}
        return {name: value for name, value in client.cookies.items()}

    def session_headers(self, session_id: str) -> dict[str, str]:
        """Everything a request for this identity must carry: default headers
        plus a Cookie header rendered from the session jar."""
        config = self._configs.get(session_id, SessionConfig())
        headers = dict(config.headers)
        cookies: dict[str, str] = dict(config.cookies)
        client = self._clients.get(session_id)
        if client is not None:
            cookies.update({name: value for name, value in client.cookies.items()})
        if cookies and "cookie" not in {name.lower() for name in headers}:
            headers["Cookie"] = "; ".join(f"{name}={value}" for name, value in cookies.items())
        return headers

    async def aclose(self) -> None:
        for client in self._clients.values():
            await client.aclose()
        self._clients.clear()

    async def __aenter__(self) -> SessionManager:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.aclose()
