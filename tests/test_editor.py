"""Tests for RPDEditor."""

import pytest
import respx

from mirea_rpd.client import RPDClient
from mirea_rpd.editor import RPDEditor
from mirea_rpd.session import SessionStore


def native_init(mock):
    mock.get(path="/Rp/Initialize").respond(200, json={"result": True, "data": {"commonState": {"hash": "test"}}})


def extra_blocks(mock):
    for path in ("/Rp12/Load", "/Rp3/Load", "/ItIndex/GetRefs", "/IssIndex/GetRefs", "/MtoIndex/Load"):
        mock.get(path=path).respond(200, json={})
    mock.post(path="/Content/GetSummary").respond(200, json={})


@pytest.fixture
def editor(tmp_path):
    store = SessionStore(path=tmp_path / "session.json")
    client = RPDClient(session_store=store)
    return RPDEditor(client)


@pytest.mark.asyncio
async def test_get_title(editor):
    mock_title = {"discName": "Авионика беспилотных систем", "headOfDep": "Иванов И.И."}

    async with respx.mock(base_url="https://rpd.mirea.ru") as respx_mock:
        native_init(respx_mock)
        respx_mock.get(path="/Title/GetTitle").respond(200, json=mock_title)

        data = await editor.get_title(rup_row_id=2035865, rp_id=311500)
        assert data["discName"] == "Авионика беспилотных систем"


@pytest.mark.asyncio
async def test_get_content(editor):
    mock_content = {"parts": [{"id": 1, "name": "Раздел 1. Основы авионики БАС", "themes": []}]}

    async with respx.mock(base_url="https://rpd.mirea.ru") as respx_mock:
        native_init(respx_mock)
        respx_mock.get(path="/Content/Load").respond(200, json=mock_content)

        data = await editor.get_content(rup_row_id=2035865, rp_id=311500)
        assert len(data["parts"]) == 1


@pytest.mark.asyncio
async def test_get_full_program(editor):
    async with respx.mock(base_url="https://rpd.mirea.ru") as respx_mock:
        native_init(respx_mock)
        extra_blocks(respx_mock)
        respx_mock.get(path="/Title/GetTitle").respond(200, json={"title": "ok"})
        respx_mock.get(path="/Content/Load").respond(200, json={"content": "ok"})
        respx_mock.get(path="/Fos/Load").respond(200, json={"fos": "ok"})
        respx_mock.get(path="/LitIndex/GetCards").respond(200, json={"cards": []})
        respx_mock.get(path="/Rp/Load").respond(200, json={"rp": "ok"})

        full = await editor.get_full_program(rup_row_id=2035865, rp_id=311500)
        assert full["title"] == {"title": "ok"}
        assert full["content"] == {"content": "ok"}
        assert full["fos"] == {"fos": "ok"}
        assert full["complete"] is True


@pytest.mark.asyncio
async def test_partial_program_is_marked_incomplete(editor):
    with respx.mock(base_url="https://rpd.mirea.ru") as mock:
        native_init(mock)
        extra_blocks(mock)
        mock.get("/Title/GetTitle").respond(200, json={"discName": "Test"})
        mock.get("/Content/Load").respond(200, json={})
        mock.get("/Fos/Load").respond(403)
        mock.get("/LitIndex/GetCards").respond(200, json={"cards": []})
        mock.get("/Rp/Load").respond(200, json={})
        result = await editor.get_full_program(1, 2)
    assert result["complete"] is False
    assert "fos" in result["errors"]
    assert result["title"] == {"discName": "Test"}
