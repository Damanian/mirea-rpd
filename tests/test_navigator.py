"""Tests for RPDNavigator."""

import pytest
import respx

from mirea_rpd.client import RPDClient
from mirea_rpd.navigator import RPDNavigator
from mirea_rpd.session import SessionStore


@pytest.fixture
def nav(tmp_path):
    store = SessionStore(path=tmp_path / "session.json")
    client = RPDClient(session_store=store)
    return RPDNavigator(client)


@pytest.mark.asyncio
async def test_get_departments(nav):
    mock_data = [{"depId": 262, "depName": "Кафедра радиоволновых процессов и технологий"}]

    async with respx.mock(base_url="https://rpd.mirea.ru") as respx_mock:
        respx_mock.get("/RpdManager/GetDepartments?accYear=2026-2027").respond(200, json=mock_data)

        deps = await nav.get_departments("2026-2027")
        assert len(deps) == 1
        assert deps[0]["depId"] == 262


@pytest.mark.asyncio
async def test_get_plan_item_rups(nav):
    mock_data = [{"rupRowId": 2035865, "discName": "Авионика беспилотных систем", "depId": 262}]

    async with respx.mock(base_url="https://rpd.mirea.ru") as respx_mock:
        respx_mock.get(
            "/RpdManager/GetPlanItemRups?accYear=2026-2027&objType=0&hideWithoutStudents=false&showOnlyWithRp=false&depId=262&discName=%D0%90%D0%B2%D0%B8%D0%BE%D0%BD%D0%B8%D0%BA%D0%B0"
        ).respond(200, json=mock_data)

        res = await nav.get_plan_item_rups(
            acc_year="2026-2027",
            dep_id=262,
            disc_name="Авионика",
        )
        assert len(res) == 1
        assert res[0]["rupRowId"] == 2035865


@pytest.mark.asyncio
async def test_download_draft(nav):
    async with respx.mock(base_url="https://rpd.mirea.ru") as respx_mock:
        respx_mock.get("/RpdManager/DownloadDraft?rupRowId=2035865&rpdId=311500").respond(
            200, content=b"PK\x03\x04docx_mock_content"
        )

        content = await nav.download_draft(rup_row_id=2035865, rp_id=311500)
        assert content.startswith(b"PK")
