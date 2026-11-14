"""HTTP Client for MIREA RPD."""

from __future__ import annotations

import re
from typing import Any

import httpx

from .exceptions import RPDAuthError, RPDRequestError
from .session import SessionStore

BASE_URL = "https://rpd.mirea.ru"
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


class RPDClient:
    """Client for interacting with rpd.mirea.ru API."""

    def __init__(
        self,
        session_store: SessionStore | None = None,
        base_url: str = BASE_URL,
        timeout: float = 30.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.session_store = session_store or SessionStore()
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            cookies = httpx.Cookies()
            stored = self.session_store.load()
            if stored and stored.get("cookies"):
                for k, v in stored["cookies"].items():
                    cookies.set(str(k), str(v), domain="rpd.mirea.ru")

            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                cookies=cookies,
                headers={
                    "User-Agent": DEFAULT_USER_AGENT,
                    "Accept": "application/json, text/plain, */*",
                },
                timeout=httpx.Timeout(self.timeout),
                follow_redirects=False,
            )
        return self._client

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    async def login(self, username: str, password: str) -> bool:
        """Authenticate into rpd.mirea.ru with university credentials."""
        client = await self._get_client()

        # Step 1: GET /Auth/Index
        await client.get("/Auth/Index")

        # Step 2: POST /Auth/LogIn
        data = {
            "username": username,
            "password": password,
            "RememberMe": "true",
        }
        resp = await client.post(
            "/Auth/LogIn",
            data=data,
            headers={"Referer": f"{self.base_url}/Auth/Index"},
        )

        # In ASP.NET Core, successful login returns 302 redirect to ReturnUrl or "/"
        # and sets .AspNetCore.Cookies.
        # Failed login redirects to /Auth with temp data error.
        location = resp.headers.get("location", "")
        if location.startswith("/Auth") or location == f"{self.base_url}/Auth":
            # Check error reason
            err_resp = await client.get(location)
            match = re.search(r"var\s+failReason\s*=\s*'([^']*)';", err_resp.text)
            fail_reason = match.group(1).strip() if match else ""
            if fail_reason:
                raise RPDAuthError(f"Ошибка авторизации: {fail_reason}")
            raise RPDAuthError("Ошибка авторизации: неверный логин или пароль.")

        # Save cookies
        cookie_dict = {c.name: c.value for c in client.cookies.jar}
        self.session_store.save(cookie_dict, {"username": username})
        return True

    async def check_session(self) -> bool:
        """Verify whether current session is valid."""
        client = await self._get_client()
        try:
            resp = await client.get("/RpdManager/GetDepartments?accYear=2026-2027")
            return resp.status_code == 200
        except (httpx.HTTPError, OSError):
            return False

    async def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: Any = None,
        data: Any = None,
    ) -> httpx.Response:
        """Execute request with automatic auth-check."""
        client = await self._get_client()
        url = path if path.startswith("http") else f"{self.base_url}/{path.lstrip('/')}"
        
        resp = await client.request(method, url, params=params, json=json, data=data)
        
        # Check if redirected to Auth
        if resp.status_code in {301, 302, 303, 307, 308}:
            loc = resp.headers.get("location", "")
            if "/Auth" in loc:
                raise RPDAuthError("Сессия РПД истекла или не активна. Выполните вход через `mirea-rpd login`.")
            # Follow redirect if not auth
            return await client.get(loc)

        if resp.status_code >= 400:
            raise RPDRequestError(f"Ошибка API РПД: HTTP {resp.status_code} ({path})")

        return resp

    async def get_json(self, path: str, params: dict[str, Any] | None = None) -> Any:
        resp = await self.request("GET", path, params=params)
        return resp.json()

    async def post_json(self, path: str, data: Any = None, params: dict[str, Any] | None = None) -> Any:
        resp = await self.request("POST", path, params=params, json=data)
        return resp.json()
