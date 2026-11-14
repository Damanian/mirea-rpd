"""Tests for TemplateManager."""

import pytest
import respx

from mirea_rpd.client import RPDClient
from mirea_rpd.session import SessionStore
from mirea_rpd.templates import RPDTemplate, TemplateManager


@pytest.fixture
def tpl_mgr(tmp_path):
    store = SessionStore(path=tmp_path / "session.json")
    client = RPDClient(session_store=store)
    return TemplateManager(client)


@pytest.mark.asyncio
async def test_apply_template(tpl_mgr):
    template = RPDTemplate(
        name="Тестовый шаблон",
        it_text="Python 3.12, PyCharm, Visual Studio Code",
        mto_text="Аудитория А-101, проектор, 20 рабочих мест",
    )

    async with respx.mock(base_url="https://rpd.mirea.ru") as respx_mock:
        respx_mock.put("/Rp/SetItText?rupRowId=2035865&rpId=311500&text=Python+3.12%2C+PyCharm%2C+Visual+Studio+Code").respond(200, json={})
        respx_mock.put("/Rp/SetMtoText?rupRowId=2035865&rpId=311500&text=%D0%90%D1%83%D0%B4%D0%B8%D1%82%D0%BE%D1%80%D0%B8%D1%8F+%D0%90-101%2C+%D0%BF%D1%80%D0%BE%D0%B5%D0%BA%D1%82%D0%BE%D1%80%2C+20+%D1%80%D0%B0%D0%B1%D0%BE%D1%87%D0%B8%D1%85+%D0%BC%D0%B5%D1%81%D1%82").respond(200, json={})
        respx_mock.post("/Rp/Save?rupRowId=2035865&rpId=311500").respond(200, json={})

        res = await tpl_mgr.apply_template(rup_row_id=2035865, rp_id=311500, template=template)
        assert res["success"] is True
        assert "it_text" in res["applied_fields"]
        assert "mto_text" in res["applied_fields"]
        assert res["saved_to_db"] is True
