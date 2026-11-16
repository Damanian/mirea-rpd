"""HTTP Client for MIREA RPD."""

from __future__ import annotations

import asyncio
import re
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx

from .exceptions import RPDAuthError, RPDRequestError
from .session import SessionStore

BASE_URL = "https://rpd.mirea.ru"
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
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
        self._loaded_cookies: dict[str, str] = {}
        self._client_lock = asyncio.Lock()
        self._documents: dict[tuple[int, int | None], Any] = {}

    def document(self, rup_row_id: int, rp_id: int | None):
        """Share the server draft hash across all tools editing the same program."""
        from .document import RPDDocument

        key = (rup_row_id, rp_id)
        if key not in self._documents:
            self._documents[key] = RPDDocument(self, rup_row_id, rp_id)
        return self._documents[key]

    async def _get_client(self) -> httpx.AsyncClient:
        async with self._client_lock:
            return await self._load_client()

    async def _load_client(self) -> httpx.AsyncClient:
        # Browser login uses a separate client. Reload its saved session even if
        # this client has already made unauthenticated requests.
        stored = self.session_store.load() or {}
        saved_cookies = {str(k): str(v) for k, v in (stored.get("cookies") or {}).items()}
        if self._client and not self._client.is_closed and saved_cookies != self._loaded_cookies:
            await self.close()
        if self._client is None or self._client.is_closed:
            cookies = httpx.Cookies()
            for k, v in saved_cookies.items():
                cookies.set(k, v, domain="rpd.mirea.ru")
            self._loaded_cookies = saved_cookies

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
        try:
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
        except httpx.HTTPError as e:
            raise RPDRequestError("Не удалось подключиться к rpd.mirea.ru. Проверьте доступ к сети.") from e

        # In ASP.NET Core, successful login returns 302 redirect to ReturnUrl or "/"
        # and sets .AspNetCore.Cookies.
        # Failed login redirects to /Auth with temp data error.
        location = resp.headers.get("location", "")
        if urlparse(urljoin(self.base_url, location)).path.startswith("/Auth"):
            # Check error reason
            try:
                err_resp = await client.get(urljoin(self.base_url, location))
            except httpx.HTTPError as e:
                raise RPDRequestError("Не удалось прочитать результат входа в РПД.") from e
            match = re.search(r"var\s+failReason\s*=\s*'([^']*)';", err_resp.text)
            fail_reason = match.group(1).strip() if match else ""
            if fail_reason:
                raise RPDAuthError(f"Ошибка авторизации: {fail_reason}")
            raise RPDAuthError("Ошибка авторизации: неверный логин или пароль.")

        if resp.status_code >= 400:
            raise RPDRequestError(f"Ошибка сервиса авторизации: HTTP {resp.status_code}.")
        if resp.status_code not in {301, 302, 303, 307, 308} or not client.cookies:
            raise RPDAuthError("Сервис не подтвердил вход: отсутствует сессионная cookie или переход после входа.")

        # Save cookies
        cookie_dict = {c.name: c.value for c in client.cookies.jar}
        self.session_store.save(cookie_dict, {"username": username})
        return True

    async def check_session(self) -> bool:
        """Verify whether current session is valid."""
        client = await self._get_client()
        try:
            resp = await client.get("/RpdManager/GetDepartments?accYear=2026-2027")
            payload = resp.json()
            return (
                resp.status_code == 200
                and isinstance(payload, (list, dict))
                and not (isinstance(payload, dict) and payload.get("error"))
            )
        except (httpx.HTTPError, OSError, ValueError):
            return False

    async def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: Any = None,
        data: Any = None,
        files: Any = None,
    ) -> httpx.Response:
        """Execute request with automatic auth-check."""
        client = await self._get_client()
        url = path if path.startswith("http") else f"{self.base_url}/{path.lstrip('/')}"

        try:
            resp = await client.request(method, url, params=params, json=json, data=data, files=files)
            for _ in range(5):
                if resp.status_code not in {301, 302, 303, 307, 308}:
                    break
                target = urljoin(str(resp.url), resp.headers.get("location", ""))
                parsed = urlparse(target)
                if parsed.path.startswith("/Auth"):
                    raise RPDAuthError("Сессия РПД истекла или не активна. Выполните вход через `mirea-rpd login`.")
                if (parsed.scheme, parsed.netloc) != (urlparse(self.base_url).scheme, urlparse(self.base_url).netloc):
                    raise RPDRequestError("Неожиданное перенаправление за пределы сервиса РПД.")
                if resp.status_code == 303 or (resp.status_code in {301, 302} and method.upper() == "POST"):
                    method, json, data, files = "GET", None, None, None
                resp = await client.request(method, target, json=json, data=data, files=files)
            else:
                raise RPDRequestError("Слишком много перенаправлений сервиса РПД.")
        except httpx.HTTPError as e:
            raise RPDRequestError(f"Ошибка соединения с РПД ({path}).") from e

        if resp.status_code >= 400:
            raise RPDRequestError(f"Ошибка API РПД: HTTP {resp.status_code} ({path})")

        return resp

    async def get_json(self, path: str, params: dict[str, Any] | None = None) -> Any:
        resp = await self.request("GET", path, params=params)
        return self.check_api_result(self._decode_json(resp, path), path)

    async def post_json(self, path: str, data: Any = None, params: dict[str, Any] | None = None) -> Any:
        resp = await self.request("POST", path, params=params, json=data)
        return self.check_api_result(self._decode_json(resp, path), path)

    @staticmethod
    def _decode_json(response: httpx.Response, path: str) -> Any:
        try:
            return response.json()
        except ValueError as e:
            raise RPDRequestError(f"Сервис РПД вернул некорректный JSON ({path}).") from e

    async def mutate(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: Any = None,
        data: Any = None,
    ) -> Any:
        """Check transport and application errors; callers must verify persisted data."""
        response = await self.request(method, path, params=params, json=json, data=data)
        if not response.content:
            return None
        return self.check_api_result(self._decode_json(response, path), path)

    @staticmethod
    def check_api_result(result: Any, path: str) -> Any:
        """The actual RPD API reports failures as HTTP 200 with result=false."""
        failed = result is False
        if isinstance(result, dict):
            failed = failed or any(result.get(key) is False for key in ("result", "success", "isSuccess", "ok"))
            failed = failed or bool(result.get("error")) or bool(result.get("errors"))
        if failed:
            message = result.get("message") if isinstance(result, dict) else None
            raise RPDRequestError(f"Сервис РПД отклонил изменение ({path})." + (f" {message}" if message else ""))
        if isinstance(result, dict) and "result" in result and "data" in result:
            return result["data"]
        return result
