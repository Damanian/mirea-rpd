"""Tests for RPDClient."""

import httpx
import pytest
import respx

from mirea_rpd.client import RPDClient
from mirea_rpd.exceptions import RPDAuthError, RPDRequestError
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


@pytest.mark.asyncio
async def test_reload_session_after_browser_login(temp_store):
    client = RPDClient(session_store=temp_store)
    async with respx.mock(base_url="https://rpd.mirea.ru") as mock:
        route = mock.get("/test").respond(200, json={})
        await client.get_json("/test")
        assert "cookie" not in route.calls.last.request.headers

        temp_store.save({".AspNetCore.Cookies": "new-session"})
        await client.get_json("/test")
        assert ".AspNetCore.Cookies=new-session" in route.calls.last.request.headers["cookie"]
    await client.close()


@pytest.mark.asyncio
async def test_login_network_error_is_reported(temp_store):
    client = RPDClient(session_store=temp_store)
    async with respx.mock(base_url="https://rpd.mirea.ru") as mock:
        mock.get("/Auth/Index").mock(side_effect=httpx.ConnectError("offline"))
        with pytest.raises(RPDRequestError, match="Проверьте доступ к сети"):
            await client.login("testuser", "secret")
    assert temp_store.load() is None
    await client.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [False, {"success": False}, {"isSuccess": False}, {"errors": ["invalid"]}])
async def test_http_200_business_errors_are_not_success(temp_store, payload):
    client = RPDClient(session_store=temp_store)
    with respx.mock(base_url="https://rpd.mirea.ru") as mock:
        mock.post("/Rp/Save").respond(200, json=payload)
        with pytest.raises(RPDRequestError, match="отклонил изменение"):
            await client.mutate("POST", "/Rp/Save")
    await client.close()


@pytest.mark.asyncio
async def test_login_does_not_accept_http_200_without_session(temp_store):
    client = RPDClient(session_store=temp_store)
    with respx.mock(base_url="https://rpd.mirea.ru") as mock:
        mock.get("/Auth/Index").respond(200, text="login")
        mock.post("/Auth/LogIn").respond(200, text="server error")
        with pytest.raises(RPDAuthError, match="не подтвердил вход"):
            await client.login("user", "password")
    assert temp_store.load() is None
    await client.close()


@pytest.mark.asyncio
async def test_html_is_not_an_active_session(temp_store):
    client = RPDClient(session_store=temp_store)
    with respx.mock(base_url="https://rpd.mirea.ru") as mock:
        mock.get("/RpdManager/GetDepartments?accYear=2026-2027").respond(200, text="<html>Log in</html>")
        assert await client.check_session() is False
    await client.close()


@pytest.mark.asyncio
async def test_redirect_does_not_hide_api_error(temp_store):
    client = RPDClient(session_store=temp_store)
    with respx.mock(base_url="https://rpd.mirea.ru") as mock:
        mock.get("/first").respond(302, headers={"Location": "/failed"})
        mock.get("/failed").respond(500)
        with pytest.raises(RPDRequestError, match="HTTP 500"):
            await client.get_json("/first")
    await client.close()
