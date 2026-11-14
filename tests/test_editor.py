"""Tests for RPDEditor."""

import pytest
import respx

from mirea_rpd.client import RPDClient
from mirea_rpd.editor import RPDEditor
from mirea_rpd.session import SessionStore


@pytest.fixture
def editor(tmp_path):
    store = SessionStore(path=tmp_path / "session.json")
    client = RPDClient(session_store=store)
    return RPDEditor(client)


@pytest.mark.asyncio
async def test_get_title(editor):
    mock_title = {"discName": "Авионика беспилотных систем", "headOfDep": "Иванов И.И."}

    async with respx.mock(base_url="https://rpd.mirea.ru") as respx_mock:
        respx_mock.get("/Title/GetTitle?rupRowId=2035865&rpId=311500").respond(200, json=mock_title)

        data = await editor.get_title(rup_row_id=2035865, rp_id=311500)
        assert data["discName"] == "Авионика беспилотных систем"


@pytest.mark.asyncio
async def test_get_content(editor):
    mock_content = {
        "parts": [
            {"id": 1, "name": "Раздел 1. Основы авионики БАС", "themes": []}
        ]
    }

    async with respx.mock(base_url="https://rpd.mirea.ru") as respx_mock:
        respx_mock.get("/Content/Load?rupRowId=2035865&rpId=311500").respond(200, json=mock_content)

        data = await editor.get_content(rup_row_id=2035865, rp_id=311500)
        assert len(data["parts"]) == 1


@pytest.mark.asyncio
async def test_get_full_program(editor):
    async with respx.mock(base_url="https://rpd.mirea.ru") as respx_mock:
        respx_mock.get("/Title/GetTitle?rupRowId=2035865&rpId=311500").respond(200, json={"title": "ok"})
        respx_mock.get("/Content/Load?rupRowId=2035865&rpId=311500").respond(200, json={"content": "ok"})
        respx_mock.get("/Fos/Load?rupRowId=2035865&rpId=311500").respond(200, json={"fos": "ok"})
        respx_mock.get("/LitIndex/GetCards?rupRowId=2035865&rpId=311500").respond(200, json=[])
        respx_mock.get("/Rp/Load?rupRowId=2035865&rpId=311500").respond(200, json={"rp": "ok"})

        full = await editor.get_full_program(rup_row_id=2035865, rp_id=311500)
        assert full["title"] == {"title": "ok"}
        assert full["content"] == {"content": "ok"}
        assert full["fos"] == {"fos": "ok"}
