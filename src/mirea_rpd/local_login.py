"""Local web server for password-safe browser login into rpd.mirea.ru."""

from __future__ import annotations

import asyncio
import html
import logging
import secrets
import socket
import webbrowser
from contextlib import suppress

import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import HTMLResponse, Response
from starlette.routing import Route

from .client import RPDClient
from .exceptions import RPDAuthError, RPDError
from .session import SessionStore

logger = logging.getLogger(__name__)

LOGIN_HTML = """<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Вход в РПД МИРЭА (rpd.mirea.ru)</title>
    <style>
        :root {{
            --bg: #0f172a;
            --card: #1e293b;
            --text: #f8fafc;
            --muted: #94a3b8;
            --primary: #3b82f6;
            --primary-hover: #2563eb;
            --danger: #ef4444;
            --success: #22c55e;
            --border: #334155;
        }}
        body {{
            margin: 0;
            padding: 0;
            min-height: 100vh;
            display: flex;
            align-items: center;
            justify-content: center;
            background: var(--bg);
            color: var(--text);
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
        }}
        .card {{
            background: var(--card);
            border: 1px solid var(--border);
            border-radius: 16px;
            padding: 36px;
            width: 100%;
            max-width: 420px;
            box-shadow: 0 20px 25px -5px rgba(0, 0, 0, 0.4);
        }}
        h1 {{
            font-size: 22px;
            margin: 0 0 8px 0;
            font-weight: 700;
            text-align: center;
        }}
        .subtitle {{
            color: var(--muted);
            font-size: 14px;
            text-align: center;
            margin-bottom: 24px;
        }}
        .form-group {{
            margin-bottom: 18px;
        }}
        label {{
            display: block;
            font-size: 13px;
            font-weight: 500;
            margin-bottom: 6px;
            color: var(--text);
        }}
        input {{
            width: 100%;
            padding: 12px 14px;
            border-radius: 8px;
            border: 1px solid var(--border);
            background: #0b1120;
            color: var(--text);
            font-size: 15px;
            box-sizing: border-box;
            outline: none;
            transition: border-color 0.2s;
        }}
        input:focus {{
            border-color: var(--primary);
        }}
        button {{
            width: 100%;
            padding: 14px;
            border-radius: 8px;
            border: none;
            background: var(--primary);
            color: white;
            font-size: 15px;
            font-weight: 600;
            cursor: pointer;
            transition: background 0.2s;
            margin-top: 10px;
        }}
        button:hover {{
            background: var(--primary-hover);
        }}
        .error {{
            background: rgba(239, 68, 68, 0.15);
            border: 1px solid var(--danger);
            color: #fca5a5;
            padding: 12px;
            border-radius: 8px;
            font-size: 13px;
            margin-bottom: 18px;
        }}
        .success {{
            background: rgba(34, 197, 94, 0.15);
            border: 1px solid var(--success);
            color: #86efac;
            padding: 24px;
            border-radius: 12px;
            text-align: center;
        }}
        .success h2 {{
            margin: 0 0 10px 0;
            font-size: 20px;
            color: var(--success);
        }}
        .success p {{
            margin: 0;
            font-size: 14px;
            color: var(--text);
        }}
    </style>
</head>
<body>
    <div class="card">
        {content}
    </div>
</body>
</html>
"""


class LocalLoginServer:
    """Spins up a one-time local web server on 127.0.0.1 for browser login."""

    def __init__(self, store: SessionStore | None = None) -> None:
        self.store = store or SessionStore()
        self.client = RPDClient(session_store=self.store)
        self.url: str | None = None
        self._secret = ""
        self._server: uvicorn.Server | None = None
        self._task: asyncio.Task | None = None
        self._done = asyncio.Event()

    def _get_free_port(self) -> int:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", 0))
            return s.getsockname()[1]

    async def _render_form(self, error: str = "", username: str = "") -> HTMLResponse:
        err_block = f'<div class="error">{html.escape(error)}</div>' if error else ""
        content = f"""
            <h1>Вход в РПД МИРЭА</h1>
            <div class="subtitle">rpd.mirea.ru</div>
            {err_block}
            <form method="POST" action="/{self._secret}/login">
                <div class="form-group">
                    <label for="username">Логин преподавателя</label>
                    <input id="username" name="username" type="text" required autofocus value="{html.escape(username)}" placeholder="konyashkin или email">
                </div>
                <div class="form-group">
                    <label for="password">Пароль</label>
                    <input id="password" name="password" type="password" required placeholder="Ваш пароль МИРЭА">
                </div>
                <button type="submit">Войти</button>
            </form>
        """
        return HTMLResponse(LOGIN_HTML.format(content=content))

    async def _handle_get(self, request: Request) -> Response:
        return await self._render_form()

    async def _handle_login(self, request: Request) -> Response:
        form = await request.form()
        username = str(form.get("username") or "").strip()
        password = str(form.get("password") or "")

        if not username or not password:
            return await self._render_form(error="Введите логин и пароль.", username=username)

        try:
            await self.client.login(username, password)
            self._done.set()
            loop = asyncio.get_running_loop()
            loop.call_later(2.0, lambda: asyncio.ensure_future(self.stop()))

            success_content = """
                <div class="success">
                    <h2>✓ Вход успешно выполнен!</h2>
                    <p>Сессия сохранена. Теперь вы можете закрыть эту вкладку браузера и написать в чат с ИИ: <b>готово</b>.</p>
                </div>
            """
            return HTMLResponse(LOGIN_HTML.format(content=success_content))
        except RPDAuthError as e:
            return await self._render_form(error=str(e), username=username)
        except (RPDError, OSError) as e:
            return await self._render_form(error=f"Ошибка соединения: {e}", username=username)

    async def start(self, open_browser: bool = True) -> str:
        """Start local web server and open browser."""
        if self._task and not self._task.done() and self.url:
            if open_browser:
                webbrowser.open(self.url)
            return self.url

        port = self._get_free_port()
        self._secret = secrets.token_urlsafe(16)
        base = f"/{self._secret}"
        self.url = f"http://127.0.0.1:{port}{base}"
        self._done.clear()

        app = Starlette(
            routes=[
                Route(base, self._handle_get, methods=["GET"]),
                Route(f"{base}/login", self._handle_login, methods=["POST"]),
            ]
        )

        config = uvicorn.Config(
            app=app,
            host="127.0.0.1",
            port=port,
            log_level="error",
        )
        self._server = uvicorn.Server(config)
        self._task = asyncio.create_task(self._server.serve())

        # Give it a fraction of a second to bind
        await asyncio.sleep(0.1)

        if open_browser:
            webbrowser.open(self.url)

        return self.url

    async def stop(self) -> None:
        """Shutdown server."""
        if self._server:
            self._server.should_exit = True
        if self._task:
            self._task.cancel()
            with suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        self._server = None
        await self.client.close()
