"""Regression tests for the local browser login server."""

import asyncio
from unittest.mock import AsyncMock

import httpx
import pytest
from starlette.applications import Starlette
from starlette.routing import Route

from mirea_rpd.exceptions import RPDRequestError
from mirea_rpd.local_login import LocalLoginServer
from mirea_rpd.session import SessionStore


@pytest.mark.asyncio
async def test_stop_cancels_background_task(tmp_path):
    server = LocalLoginServer(store=SessionStore(tmp_path / "session.json"))
    server._task = asyncio.create_task(asyncio.Event().wait())
    await server.stop()
    assert server._task is None


@pytest.mark.asyncio
async def test_connection_error_renders_form_instead_of_internal_server_error(tmp_path):
    server = LocalLoginServer(store=SessionStore(tmp_path / "session.json"))
    server.client.login = AsyncMock(side_effect=RPDRequestError("Нет доступа к сети"))
    app = Starlette(routes=[Route("/login", server._handle_login, methods=["POST"])])
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://local") as client:
        response = await client.post("/login", data={"username": "user", "password": "not-a-real-password"})
    assert response.status_code == 200
    assert "Нет доступа к сети" in response.text
    assert "not-a-real-password" not in response.text
    assert not server._done.is_set()
