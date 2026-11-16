"""Behavioral tests for template application and verification."""

from urllib.parse import parse_qs

import httpx
import pytest
import respx

from mirea_rpd.client import RPDClient
from mirea_rpd.schemas import TEXT_FIELDS
from mirea_rpd.session import SessionStore
from mirea_rpd.templates import RPDTemplate, TemplateManager


@pytest.fixture
async def tpl_mgr(tmp_path):
    client = RPDClient(session_store=SessionStore(path=tmp_path / "session.json"))
    yield TemplateManager(client)
    await client.close()


class TextApi:
    """Native envelopes and form bodies; Initialize reopens persisted values."""

    def __init__(self, *, saved=None, reject_field=None, save_result=None, discard_save=False):
        self.saved = {native: None for _, native in TEXT_FIELDS.values()}
        self.saved.update(saved or {})
        self.staged = dict(self.saved)
        self.reject_field = reject_field
        self.save_result = save_result
        self.discard_save = discard_save
        self.save_calls = 0
        self.version = 0

    def response(self, data):
        return httpx.Response(
            200,
            json={
                "result": True,
                "data": {
                    **data,
                    "commonState": {
                        "hash": f"h{self.version}",
                        "hasChanges": self.staged != self.saved,
                    },
                },
            },
        )

    def __call__(self, request):
        path = request.url.path
        if request.method == "GET" and path == "/Rp/Initialize":
            self.staged = dict(self.saved)
            return self.response({"permissions": {"allowSave": True}})
        values = (
            dict(request.url.params)
            if request.method == "GET"
            else {key: value[0] for key, value in parse_qs(request.content.decode(), keep_blank_values=True).items()}
        )
        assert values.get("hash") == f"h{self.version}"
        if request.method == "GET" and path == "/Rp/Load":
            return self.response(dict(self.staged))
        if request.method == "GET" and path == "/Content/Load":
            return self.response({"terms": [1], "workTypes": [{"id": 101}]})
        for field, (endpoint, native_key) in TEXT_FIELDS.items():
            if request.method == "PUT" and path == endpoint:
                if field == self.reject_field:
                    return httpx.Response(200, json={"success": False, "error": "rejected"})
                self.staged[native_key] = values["text"]
                self.version += 1
                return self.response({})
        if request.method == "POST" and path == "/Rp/Save":
            self.save_calls += 1
            if not self.discard_save and self.save_result is None:
                self.saved = dict(self.staged)
            if self.save_result is not None:
                return httpx.Response(200, json=self.save_result)
            return self.response(
                {"url": f"/Rp?rupRowId={request.url.params['rupRowId']}&rpId={request.url.params.get('rpId', '999')}"}
            )
        pytest.fail(f"Unexpected request: {request.method} {path}")


@pytest.mark.asyncio
async def test_apply_template(tpl_mgr):
    api = TextApi()
    template = RPDTemplate(it_text="Python 3.12", mto_text="Аудитория А-101")
    with respx.mock() as mock:
        mock.route().mock(side_effect=api)
        result = await tpl_mgr.apply_template(2035865, 311500, template)
    assert result["success"] is True
    assert result["verified"] is True
    assert result["saved_to_db"] is True
    assert result["applied_fields"] == ["it_text", "mto_text"]
    assert api.save_calls == 1


@pytest.mark.asyncio
async def test_empty_template_never_saves(tpl_mgr):
    with respx.mock() as mock:
        result = await tpl_mgr.apply_template(1, 2, {})
        assert not mock.calls
    assert result["success"] is False
    assert result["errors"]


@pytest.mark.asyncio
async def test_unknown_fields_never_silently_disappear(tpl_mgr):
    with respx.mock() as mock:
        result = await tpl_mgr.apply_template(1, 2, {"it_text": "Python", "fos": "Questions"})
        assert not mock.calls
    assert result["success"] is False
    assert result["errors"][0]["path"] == "fos"


@pytest.mark.asyncio
async def test_missing_semester_is_rejected_before_mutation(tpl_mgr):
    template = {"sections": [{"name": "Основы", "themes": [{"name": "Тема", "works": [{"name": "Лекция"}]}]}]}
    with respx.mock() as mock:
        mock.route().mock(side_effect=TextApi())
        result = await tpl_mgr.apply_template(1, 2, template)
        assert all(call.request.method == "GET" for call in mock.calls)
    assert result["success"] is False
    assert result["saved_to_db"] is False
    assert "Семестр" in result["errors"][0]["error"]


@pytest.mark.asyncio
async def test_dry_run_does_not_make_requests(tpl_mgr):
    with respx.mock() as mock:
        result = await tpl_mgr.apply_template(1, 2, {"it_text": "Python"}, dry_run=True)
        assert not mock.calls
    assert result["success"] is True
    assert result["dry_run"] is True
    assert result["saved_to_db"] is False
    assert result["would_update"] == ["it_text"]


@pytest.mark.asyncio
async def test_rejected_setter_stops_before_save(tpl_mgr):
    api = TextApi(reject_field="mto_text")
    with respx.mock() as mock:
        mock.route().mock(side_effect=api)
        result = await tpl_mgr.apply_template(1, 2, {"it_text": "Python", "mto_text": "Аудитория"})
    assert result["success"] is False
    assert result["saved_to_db"] is False
    assert api.save_calls == 0
    assert result["staged_fields"] == ["it_text"]
    assert result["applied_fields"] == []


@pytest.mark.asyncio
async def test_rejected_save_is_not_success(tpl_mgr):
    api = TextApi(save_result={"success": False})
    with respx.mock() as mock:
        mock.route().mock(side_effect=api)
        result = await tpl_mgr.apply_template(1, 2, {"it_text": "Python"})
    assert result["success"] is False
    assert result["saved_to_db"] is False
    assert result["verified"] is False


@pytest.mark.asyncio
async def test_successful_http_without_persisted_changes_is_not_success(tpl_mgr):
    api = TextApi(discard_save=True)
    with respx.mock() as mock:
        mock.route().mock(side_effect=api)
        result = await tpl_mgr.apply_template(1, 2, {"it_text": "Python"})
    assert result["saved_to_db"] is True
    assert result["success"] is False
    assert result["verified"] is False
    assert result["applied_fields"] == []


@pytest.mark.asyncio
async def test_repeat_application_does_not_write(tpl_mgr):
    api = TextApi(saved={"itText": "Python"})
    with respx.mock() as mock:
        mock.route().mock(side_effect=api)
        result = await tpl_mgr.apply_template(1, 2, {"it_text": "Python"})
        assert all(call.request.method == "GET" for call in mock.calls)
    assert result["success"] is True
    assert result["unchanged_fields"] == ["it_text"]
    assert api.save_calls == 0


@pytest.mark.asyncio
async def test_explicit_empty_string_clears_field(tpl_mgr):
    api = TextApi(saved={"itText": "Old text"})
    with respx.mock() as mock:
        mock.route().mock(side_effect=api)
        result = await tpl_mgr.apply_template(1, 2, {"it_text": ""})
    assert result["success"] is True
    assert api.saved["itText"] == ""
