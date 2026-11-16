"""Regression contracts discovered against the real MIREA service."""

from copy import deepcopy
from email import policy
from email.parser import BytesParser
from urllib.parse import parse_qs

import httpx
import pytest
import respx

from mirea_rpd.client import RPDClient
from mirea_rpd.session import SessionStore
from mirea_rpd.templates import TemplateManager


class NativeApi:
    def __init__(self, *, corrupt_fos=False):
        self.version = self.saves = self.uploads = self.mto_adds = 0
        self.corrupt_fos = corrupt_fos
        self.budget_kind = "Лекции"
        self.saved = {
            "parts": [{"id": 10, "name": "Экзамен", "number": 1, "readOnly": True}],
            "works": [],
            "fos": "",
            "attachments": {},
            "software": [],
            "systems": [],
            "mto": [],
            "lit": [],
            "approval": {"date": None, "number": None},
            "outcome": {
                "cardId": 77,
                "indiCode": "ОПК-2.1",
                "categoryId": 1,
                "levelId": 1,
                "isMock": True,
                "contents": "",
            },
        }
        self.state = deepcopy(self.saved)

    def response(self, data):
        return httpx.Response(
            200,
            json={
                "result": True,
                "data": {
                    **deepcopy(data),
                    "commonState": {
                        "hash": f"h{self.version}",
                        "hasChanges": self.state != self.saved,
                    },
                },
            },
        )

    def __call__(self, request):
        path = request.url.path
        query = dict(request.url.params)
        if path == "/Rp/Initialize":
            self.state = deepcopy(self.saved)
            return self.response(
                {
                    "permissions": {"allowSave": True, "canWriteComments": False},
                    "settings": {
                        "useRP3": True,
                        "useFOSComponent": True,
                        "useLitList": True,
                        "useITList": True,
                        "useISList": True,
                        "useMTODComponent": True,
                    },
                }
            )
        multipart = "multipart/form-data" in request.headers.get("content-type", "")
        if multipart:
            message = BytesParser(policy=policy.default).parsebytes(
                b"Content-Type: " + request.headers["content-type"].encode() + b"\r\n\r\n" + request.content
            )
            fields = {
                part.get_param("name", header="content-disposition"): part.get_payload(decode=True)
                for part in message.iter_parts()
            }
            values = {"hash": fields["hash"].decode()}
        else:
            fields = {}
            values = (
                query
                if request.method == "GET"
                else {key: item[0] for key, item in parse_qs(request.content.decode(), keep_blank_values=True).items()}
            )
        assert values["hash"] == f"h{self.version}", "All mutations must use the latest server hash"
        s = self.state
        if request.method == "GET":
            if path == "/Rp12/Load":
                return self.response(
                    {"prerequisites": {"text": None, "planItems": []}, "outgoings": {"text": None, "planItems": []}}
                )
            if path == "/Rp/Load":
                return self.response({})
            if path == "/Title/GetTitle":
                return self.response({"title": {"discName": "Схемотехника", "approvance": s["approval"]}})
            if path == "/Rp3/Initialize":
                return self.response({"settings": {"useExternalFosFiles": True}})
            if path == "/Rp3/Load":
                rows = [s["outcome"]]
                if not rows[0]["isMock"]:
                    rows.append({**rows[0], "levelId": 2, "isMock": True, "contents": ""})
                return self.response({"items": rows})
            if path == "/Rp3/DownloadFos":
                return httpx.Response(200, content=s["attachments"][query["fosType"]])
            if path == "/Content/Load":
                return self.response(
                    {
                        "parts": s["parts"],
                        "themes": [],
                        "works": s["works"],
                        "terms": [5],
                        "workTypes": [{"id": 101}, {"id": 103}],
                    }
                )
            if path == "/UpResultsIndex/GetCards":
                return self.response({"cards": [{"id": 700, "code": "ОПК-2.1-З1"}]})
            if path == "/Fos/Load":
                return self.response({"field1": s["fos"]})
            if path == "/ItIndex/GetRefs":
                return self.response({"cards": [{"id": 2}], "refs": s["software"]})
            if path == "/IssIndex/GetRefs":
                return self.response({"cards": [{"id": 3}], "refs": s["systems"]})
            if path == "/MtoIndex/Load":
                return self.response({"cards": [{"id": 5}], "refs": s["mto"]})
            if path == "/LitIndex/GetCards":
                return self.response({"cards": s["lit"]})
        if path == "/Content/GetSummary":
            actual = sum(work["hours"] for work in s["works"])
            return self.response({"items": [{"ВидЗанятий": self.budget_kind, "УП_5": 2, "РПД_5": actual}]})
        self.version += 1
        if path == "/Rp3/UpdateLevel":
            # This site silently ignores text fields for scales and FOS tasks.
            s["outcome"].update(contents=values["contents"], isMock=False)
        elif path == "/Rp3/UploadFos":
            self.uploads += 1
            assert fields["file"].startswith(b"PK")
            s["attachments"][query["fosType"]] = b"PKcorrupt" if self.corrupt_fos else fields["file"]
            flag = "hasCurrentFos" if query["fosType"] == "1" else "hasIntermediateFos"
            s["outcome"][flag] = True
        elif path == "/Fos/SetField":
            s["fos"] = values["value"].replace("\n", "\r\n")
        elif path == "/Content/AddPart":
            part = {"id": -1, "name": values["name"], "comment": values["comment"], "number": 2}
            s["parts"].append(part)
            return self.response({"part": part})
        elif path == "/Content/MovePart":
            # Despite parameter names srcNum/trgNum, the native API uses zero-based indices.
            assert values["srcNum"] == "1" and values["trgNum"] == "0"
            s["parts"].insert(0, s["parts"].pop(1))
            for index, part in enumerate(s["parts"], 1):
                part["number"] = index
        elif path == "/Content/AddWork":
            work = {
                **values,
                "id": -1 - len(s["works"]),
                "partId": int(values["partId"]),
                "themeId": None,
                "wtype": int(values["wtype"]),
                "termNum": int(values["termNum"]),
                "hours": float(values["hours"]),
                "upResRefs": [],
            }
            work.pop("hash")
            s["works"].append(work)
            return self.response({"work": work})
        elif path == "/Content/DeleteWork":
            assert int(values["partId"]) == 11
            s["works"] = [work for work in s["works"] if work["id"] != int(values["workId"])]
        elif path == "/Title/SetProtocolDate":
            s["approval"]["date"] = values["protocolDate"] + "T00:00:00"
        elif path == "/Title/SetProtocoNum":
            s["approval"]["number"] = values["protocolNum"]
        elif path == "/UpResultsIndex/AddUpResRef":
            work = next(work for work in s["works"] if work["id"] == int(values["workIds[]"]))
            work["upResRefs"] = [{"id": 700, "code": "ОПК-2.1-З1"}]
        elif path == "/ItIndex/AddRef":
            s["software"].append({"id": int(values["cardIds[]"])})
        elif path == "/IssIndex/AddRef":
            s["systems"].append({"id": int(values["cardIds[]"])})
        elif path == "/MtoIndex/AddRef":
            self.mto_adds += 1
            assert values["id"] == "5"
            s["mto"].append({"id": 900, "catalogId": 5, "wtype": 0})
            return self.response({"card": s["mto"][0]})
        elif path == "/MtoIndex/UpdateRef":
            assert values["id"] == "900", "Update uses the reference ID, not the catalog ID"
            s["mto"][0]["wtype"] = int(values["wtype"])
        elif path == "/LitIndex/AddLitRef":
            s["lit"].append({"litId": int(values["litIds[]"]), "litType": int(values["litType"])})
        elif path == "/Rp/Save":
            self.saves += 1
            for part in s["parts"]:
                if part["id"] == -1:
                    part["id"] = 11
            for index, work in enumerate(s["works"]):
                work.update(id=21 + index, partId=11)
            self.saved = deepcopy(s)
            return self.response({"url": "/Rp?rupRowId=1&rpId=99"})
        else:
            pytest.fail(f"Unexpected request: {request.method} {path}")
        return self.response({})


def full_template():
    def tasks(count):
        return [
            {
                "type": "sequencing",
                "prompt": f"Порядок проверки каскада {i + 1}",
                "sequence_items": ["Снять характеристику", "Проверить питание"],
                "correct": [2, 1],
                "minutes": 3,
            }
            for i in range(count)
        ]

    return {
        "defer_approval": True,
        "learning_outcomes": [
            {
                "indicator": "ОПК-2.1",
                "category": 1,
                "contents": "Принцип работы усилителя",
                "mark5": "Полное обоснованное решение",
                "current_tasks": tasks(5),
                "intermediate_tasks": tasks(10),
            }
        ],
        "fos": {"questions": "Первый вопрос\nВторой вопрос"},
        "software_ids": [2],
        "information_system_ids": [3],
        "facilities": [{"catalog_id": 5, "work_type_id": 101}],
        "literature": [{"catalog_id": 7}],
        "sections": [
            {
                "name": "Усилители",
                "works": [{"name": "Расчёт усилителей", "hours": 2, "semester": 5, "indicators": ["ОПК-2.1"]}],
            }
        ],
    }


@pytest.mark.asyncio
async def test_full_native_autofill_and_repeat_without_duplicates(tmp_path):
    api = NativeApi()
    client = RPDClient(session_store=SessionStore(tmp_path / "session.json"))
    try:
        with respx.mock() as mock:
            mock.route().mock(side_effect=api)
            manager = TemplateManager(client)
            first = await manager.apply_template(1, None, full_template(), require_complete=True)
            assert first["success"], first["errors"]
            assert first["rp_id"] == 99 and api.saves == 1 and api.uploads == 2
            assert (1, None) not in client._documents
            second = await manager.apply_template(1, 99, full_template(), require_complete=True)
            assert second["success"], second["errors"]
            assert second["staged_fields"] == []
            assert api.saves == 1 and api.uploads == 2 and api.mto_adds == 1
            assert len(api.saved["works"]) == 1
            exported = await manager.export_template(1, 99)
            assert len(exported["sections"]) == 1
            assert exported["learning_outcomes"][0]["current_fos"]["content_base64"]
            exported["defer_approval"] = True
            third = await manager.apply_template(1, 99, exported, require_complete=True)
            assert third["success"], third["errors"]
            assert third["staged_fields"] == [] and api.uploads == 2
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_corrupt_fos_readback_prevents_database_save(tmp_path):
    api = NativeApi(corrupt_fos=True)
    client = RPDClient(session_store=SessionStore(tmp_path / "session.json"))
    try:
        with respx.mock() as mock:
            mock.route().mock(side_effect=api)
            result = await TemplateManager(client).apply_template(1, None, full_template(), require_complete=True)
        assert not result["success"] and not result["saved_to_db"]
        assert api.saves == 0
        assert any("fos" in error["field"] for error in result["errors"])
    finally:
        await client.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["comments", "missing_fos"])
async def test_preflight_rejects_unsupported_changes_before_any_mutation(tmp_path, case):
    api = NativeApi()
    template = full_template()
    if case == "comments":
        template["comments_text"] = "Замечания"
    else:
        template["learning_outcomes"][0].pop("intermediate_tasks")
    client = RPDClient(session_store=SessionStore(tmp_path / "session.json"))
    try:
        with respx.mock() as mock:
            mock.route().mock(side_effect=api)
            result = await TemplateManager(client).apply_template(1, None, template, require_complete=True)
        assert not result["success"]
        assert result["errors"][0]["action"] == "preflight"
        assert api.version == 0 and api.saves == 0
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_autofill_requests_approval_data_without_contacting_site(tmp_path):
    client = RPDClient(session_store=SessionStore(tmp_path / "session.json"))
    template = full_template()
    template.pop("defer_approval")
    try:
        with respx.mock() as mock:
            result = await TemplateManager(client).apply_template(1, None, template, require_complete=True)
            assert not mock.calls
        assert result["requires_user_input"]
        assert set(result["required_user_inputs"]) == {"title.protocol_date", "title.protocol_number"}
        assert not result["saved_to_db"]
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_approval_written_verified_and_not_rewritten_on_repeat(tmp_path):
    api = NativeApi()
    template = full_template()
    template.pop("defer_approval")
    template["title"] = {"protocol_date": "25.06.2026", "protocol_number": "7"}
    client = RPDClient(session_store=SessionStore(tmp_path / "session.json"))
    try:
        with respx.mock() as mock:
            mock.route().mock(side_effect=api)
            manager = TemplateManager(client)
            result = await manager.apply_template(1, None, template, require_complete=True)
            assert result["success"], result["errors"]
            assert api.saved["approval"] == {"date": "2026-06-25T00:00:00", "number": "7"}
            again = await manager.apply_template(1, 99, template, require_complete=True)
            assert again["success"] and again["staged_fields"] == []
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_replace_removes_stale_work_in_retained_section(tmp_path):
    api = NativeApi()
    client = RPDClient(session_store=SessionStore(tmp_path / "session.json"))
    try:
        with respx.mock() as mock:
            mock.route().mock(side_effect=api)
            manager = TemplateManager(client)
            await manager.apply_template(1, None, full_template(), require_complete=True)
            revised = full_template()
            revised["sections"][0]["works"][0]["name"] = "Подробный расчёт с проверкой модели"
            result = await manager.apply_template(1, 99, revised, content_mode="replace", require_complete=True)
        assert result["success"], result["errors"]
        assert len(api.saved["works"]) == 1
        assert api.saved["works"][0]["name"] == "Подробный расчёт с проверкой модели"
        assert "remove_work.21" in result["staged_fields"]
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_multiple_catalog_activities_match_after_ids_change_on_save(tmp_path):
    api = NativeApi()
    api.budget_kind = "Практические"
    template = full_template()
    template["sections"][0]["works"] = [
        {
            "name": "Расчёт делителя",
            "comment": "Рассчитать напряжение нагруженного делителя",
            "wtype": 3,
            "activity_id": 72,
            "hours": 1,
            "semester": 5,
            "indicators": ["ОПК-2.1"],
        },
        {
            "name": "Расчёт усилителя",
            "comment": "Рассчитать усиление и проверить линейный режим",
            "wtype": 3,
            "activity_id": 72,
            "hours": 1,
            "semester": 5,
            "indicators": ["ОПК-2.1"],
        },
    ]
    client = RPDClient(session_store=SessionStore(tmp_path / "session.json"))
    try:
        with respx.mock() as mock:
            mock.route().mock(side_effect=api)
            manager = TemplateManager(client)
            first = await manager.apply_template(1, None, template, require_complete=True)
            assert first["success"] and first["verified"], first["errors"]
            assert len(api.saved["works"]) == 2
            repeated = await manager.apply_template(1, 99, template, require_complete=True)
            assert repeated["success"] and repeated["staged_fields"] == []
    finally:
        await client.close()
