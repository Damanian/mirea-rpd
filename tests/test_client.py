"""Tests for RPDClient."""

import pytest
import respx

from mirea_rpd.client import RPDClient
from mirea_rpd.exceptions import RPDAuthError
from mirea_rpd.session import SessionStore


@pytest.fixture
def temp_store(tmp_path):
    return SessionStore(path=tmp_path / "test_session.json")


@pytest.mark.asyncio
async def test_login_success(temp_store):
    client = RPDClient(session_store=temp_store)

    async with respx.mock(base_url="https://rpd.mirea.ru") as respx_mock:
        respx_mock.get("/Auth/Index").respond(200, text="<html>Login</html>")
        respx_mock.post("/Auth/LogIn").respond(
            302,
            headers={
                "Location": "/",
                "Set-Cookie": ".AspNetCore.Cookies=dummy_cookie_value; path=/; httponly",
            },
        )

        success = await client.login("testuser", "secret")
        assert success is True

        stored = temp_store.load()
        assert stored is not None
        assert stored["user_info"]["username"] == "testuser"
        assert ".AspNetCore.Cookies" in stored["cookies"]


@pytest.mark.asyncio
async def test_login_failure(temp_store):
    client = RPDClient(session_store=temp_store)

    async with respx.mock(base_url="https://rpd.mirea.ru") as respx_mock:
        respx_mock.get("/Auth/Index").respond(200, text="<html>Login</html>")
        respx_mock.post("/Auth/LogIn").respond(302, headers={"Location": "/Auth"})
        respx_mock.get("/Auth").respond(
            200,
            text="<html><script>var failReason = 'Введён неверный пароль или пользователь не существует.';</script></html>",
        )

        with pytest.raises(RPDAuthError, match="Введён неверный пароль"):
            await client.login("testuser", "wrong")


@pytest.mark.asyncio
async def test_auth_expired_redirect(temp_store):
    client = RPDClient(session_store=temp_store)

    async with respx.mock(base_url="https://rpd.mirea.ru") as respx_mock:
        respx_mock.get("/RpdManager/GetDepartments?accYear=2026-2027").respond(
            302, headers={"Location": "/Auth/Index?ReturnUrl=..."}
        )

        with pytest.raises(RPDAuthError, match="Сессия РПД истекла"):
            await client.get_json("/RpdManager/GetDepartments?accYear=2026-2027")
