"""Native RPD writes, based on the site's dispatcher and controller contracts."""

from __future__ import annotations

import math
from typing import Any

from .document import RPDDocument
from .exceptions import RPDError
from .fos import build_fos, check_fos
from .schemas import TEXT_FIELDS, RPDTemplate, SemesterHourBudget, WorkItemTemplate
from .validation import validate_template

FOS_FIELDS = {
    "questions": (1, "field1"),
    "coursework_topics": (2, "field2"),
    "assessment_materials": (3, "field3"),
    "assessment_types": (4, "field4"),
    "rating_plan": (5, "field5"),
    "control_work_guidance": (6, "metodRecomendationForControlWork"),
    "coursework_guidance": (7, "metodRecomendationForKursRab"),
}
OUTCOME_FIELDS = {
    "contents": "contents",
    "rating_scale": "ratingScale",
    "mark_scale": "markScale",
    "mark5": "mark5",
    "mark4": "mark4",
    "mark3": "mark3",
    "mark2": "mark2",
    "current_control_task1": "fosCurrentControlTask1",
    "current_control_task2": "fosCurrentControlTask2",
    "intermediate_control_task1": "fosInterControlTask1",
    "intermediate_control_task2": "fosInterControlTask2",
}
TITLE_FIELDS = {
    "disc_name": ("/Title/SetDiscName", "discName", "discName"),
    "head_of_department": ("/Title/SetHeadOfDep", "headOfDep", "headOfDep"),
    "protocol_number": ("/Title/SetProtocoNum", "protocolNum", "approvance.number"),
    "protocol_date": ("/Title/SetProtocolDate", "protocolDate", "approvance.date"),
}
PERSON_FIELDS = {"academic_rank": "academicRank", "department_id": "depId", "professor_id": "profId"}
NATIVE_TYPES = {1: 101, 2: 102, 3: 103, 4: 107}


def curriculum_budgets(summary: dict[str, Any], terms: list[int]) -> list[SemesterHourBudget]:
    labels = {"lectures": "Лекции", "labs": "Лабораторные", "practices": "Практические", "self_study": "Сам. работа"}
    rows = {row.get("ВидЗанятий"): row for row in summary.get("items", [])}
    if not rows:
        raise RPDError("Сайт не вернул распределение часов учебного плана.")
    return [
        SemesterHourBudget(
            semester=term, **{kind: rows.get(label, {}).get(f"УП_{term}", 0) for kind, label in labels.items()}
        )
        for term in terms
    ]


def at(data: dict[str, Any], dotted: str) -> Any:
    for key in dotted.split("."):
        if not isinstance(data, dict):
            return None
        data = data.get(key)
    return data


def same(left: Any, right: Any) -> bool:
    if isinstance(left, str) and isinstance(right, str):
        return left.replace("\r\n", "\n") == right.replace("\r\n", "\n")
    if isinstance(left, (float, int)) and isinstance(right, (float, int)):
        return math.isclose(left, right, rel_tol=0, abs_tol=1e-6)
    return left == right


def goal_text(data: dict[str, Any], key: str) -> Any:
    value = data.get(key)
    return value.get("text") if isinstance(value, dict) else value


class NativeWriter:
    def __init__(self, document: RPDDocument):
        self.doc = document
        self.operations: list[str] = []
        self.expected_works: list[tuple[int, dict[str, Any], list[int]]] = []
        self.part_names: dict[int, str] = {}
        self.theme_names: dict[int, str] = {}
        self.work_indicators: dict[int, list[str]] = {}
        self.validation_report: dict[str, Any] | None = None
        self.outcome_settings: dict[str, Any] = {}
        self.discipline = ""
        self.expected_fos: list[tuple[dict[str, Any], bytes, str]] = []

    async def read(self, path: str, method: str = "GET") -> dict[str, Any]:
        return await self.doc.call(method, path)

    async def op(self, method: str, path: str, values: dict[str, Any], label: str) -> dict[str, Any]:
        result = await self.doc.call(method, path, values)
        if result.get("failed") or result.get("notFoundLitCardIds"):
            raise RPDError(f"Сайт не применил все элементы: {label}.")
        self.operations.append(label)
        return result

    async def preflight(self, model: RPDTemplate, *, complete: bool = False) -> None:
        state = await self.doc.initialize()
        if state.get("permissions", {}).get("allowSave") is False:
            raise RPDError("Нет прав на сохранение этой РПД.")
        if model.comments_text is not None and state.get("permissions", {}).get("canWriteComments") is False:
            raise RPDError("Нет права canWriteComments для записи замечаний проверяющего.")
        requested_text = [key for key in TEXT_FIELDS if getattr(model, key) is not None]
        if requested_text:
            fields = await self.read("/Rp/Load")
            for key in requested_text:
                if TEXT_FIELDS[key][1] not in fields:
                    raise RPDError(f"Текстовое поле {key} недоступно. Используйте соответствующий справочник.")
        if complete:
            settings = state.get("settings", {})
            required = ["sections"]
            required += [
                field
                for flag, field in (
                    ("useRP3", "learning_outcomes"),
                    ("useLitList", "literature"),
                    ("useITList", "software_ids"),
                    ("useISList", "information_system_ids"),
                    ("useMTODComponent", "facilities"),
                )
                if settings.get(flag)
            ]
            missing = [field for field in required if not getattr(model, field)]
            if settings.get("useFOSComponent") and not (model.fos and model.fos.questions):
                missing.append("fos.questions")
            if missing:
                raise RPDError(f"Для полного заполнения не хватает полей: {', '.join(missing)}.")
        if model.learning_outcomes:
            self.outcome_settings = (await self.read("/Rp3/Initialize")).get("settings", {})
            title = await self.read("/Title/GetTitle")
            self.discipline = (model.title.disc_name if model.title else None) or title.get("title", {}).get(
                "discName", ""
            )
            items = (await self.read("/Rp3/Load")).get("items", [])
            available = {(x.get("indiCode"), x.get("categoryId"), x.get("levelId")) for x in items}
            for item in model.learning_outcomes:
                if (item.indicator, item.category, item.level) not in available:
                    raise RPDError(
                        f"В учебном плане нет результата {item.indicator}, категория {item.category}, уровень {item.level}."
                    )
                if self.outcome_settings.get("useExternalFosFiles"):
                    documents = [build_fos(item, kind, self.discipline) for kind in (1, 0)]
                    for kind, attachment in zip((1, 0), documents, strict=True):
                        if attachment:
                            problems = check_fos(
                                attachment[1],
                                item.category,
                                kind,
                                competence=item.indicator.rsplit(".", 1)[0],
                                discipline=self.discipline,
                            )
                            if problems:
                                raise RPDError(
                                    f"ФОС {item.indicator}, категория {item.category}: {'; '.join(problems)}"
                                )
                    if complete and not all(documents):
                        raise RPDError(
                            f"Для {item.indicator}, категория {item.category}, нужны текущий и промежуточный ФОС: задания или готовые файлы."
                        )
                    rubric = any(
                        getattr(item, field) is not None
                        for field in ("rating_scale", "mark_scale", "mark5", "mark4", "mark3", "mark2")
                    )
                    if rubric and not any(documents):
                        raise RPDError(
                            "Критерии в этой конфигурации сохраняются в файле ФОС. Добавьте задания контроля или готовый DOCX."
                        )
                elif item.current_fos or item.intermediate_fos:
                    raise RPDError("Сайт не поддерживает внешние файлы ФОС для этой РПД.")
            if complete:
                requested = {(x.indicator, x.category) for x in model.learning_outcomes}
                categories = {(indicator, category) for indicator, category, _ in available}
                if categories - requested:
                    raise RPDError(
                        "Для полного заполнения нужны результаты обучения по всем индикаторам и категориям учебного плана."
                    )
        if model.sections is not None:
            content = await self.read("/Content/Load")
            types = {item["id"] for item in content.get("workTypes", [])}
            terms = set(content.get("terms", []))
            for section in model.sections:
                for work in [*section.works, *(w for t in section.themes for w in t.works)]:
                    native_type = work.work_type_id or NATIVE_TYPES[work.wtype]
                    if native_type not in types:
                        raise RPDError(f"Недоступный вид занятия: {native_type}.")
                    if work.semester not in terms:
                        raise RPDError(f"Семестр занятия «{work.name}» отсутствует в учебном плане.")
            codes = {x.get("indiCode") for x in (await self.read("/Rp3/Load")).get("items", [])}
            for section in model.sections:
                for work in [*section.works, *(w for t in section.themes for w in t.works)]:
                    for code in work.indicators:
                        if code not in codes and not any(code.startswith(f"{item}-") for item in codes if item):
                            raise RPDError(f"Индикатор {code} отсутствует в учебном плане.")
            if complete:
                summary = await self.read("/Content/GetSummary", "POST")
                budgets = curriculum_budgets(summary, content.get("terms", []))
                self.validation_report = validate_template(
                    model, semester_budgets=budgets, allowed_indicators=sorted(codes)
                )
                if not self.validation_report["valid"]:
                    message = "; ".join(error["message"] for error in self.validation_report["errors"])
                    raise RPDError(f"Шаблон не соответствует учебному плану: {message}")
        for value, path in (
            (model.software_ids, "/ItIndex/GetRefs"),
            (model.information_system_ids, "/IssIndex/GetRefs"),
            (model.facilities, "/MtoIndex/Load"),
        ):
            if value is not None:
                catalog = await self.read(path)
                ids = {x["id"] for x in catalog.get("cards", [])}
                requested = {x.catalog_id if hasattr(x, "catalog_id") else x for x in value}
                if requested - ids:
                    raise RPDError(f"ID отсутствуют в справочнике {path}: {sorted(requested - ids)}.")

    async def apply(self, model: RPDTemplate, *, content_mode: str = "merge") -> None:
        await self._title(model)
        if model.goals:
            current = await self.read("/Rp12/Load")
            endpoints = {
                "aims": "SetAims",
                "tasks": "SetTasks",
                "prerequisites": "SetPrerequisites",
                "outgoings": "SetOutgoings",
            }
            for key, value in model.goals.model_dump(exclude_none=True).items():
                if not same(goal_text(current, key), value):
                    await self.op("PUT", f"/Rp12/{endpoints[key]}", {"text": value}, f"goals.{key}")
        if model.learning_outcomes:
            await self._outcomes(model)
        if model.fos:
            current = await self.read("/Fos/Load")
            for key, value in model.fos.model_dump(exclude_none=True).items():
                number, native = FOS_FIELDS[key]
                if not same(current.get(native), value):
                    await self.op("PUT", "/Fos/SetField", {"fieldNum": number, "value": value}, f"fos.{key}")
        if model.sections is not None:
            await self._content(model, content_mode)
        await self._resources(model)
        data = model.model_dump(exclude_none=True)
        fields = [key for key in TEXT_FIELDS if key in data]
        if fields:
            current = await self.read("/Rp/Load")
            for key in fields:
                endpoint, native = TEXT_FIELDS[key]
                if native not in current:
                    raise RPDError(
                        f"Текстовое поле {key} не доступно в этой конфигурации сайта. Используйте соответствующий справочник."
                    )
                if not same(current.get(native), data[key]):
                    await self.op("PUT", endpoint, {"text": data[key]}, key)

    async def _title(self, model: RPDTemplate) -> None:
        if model.title is None:
            return
        current = await self.read("/Title/GetTitle")
        values = model.title.model_dump(exclude_none=True)
        for key, (path, parameter, native) in TITLE_FIELDS.items():
            actual = at(current.get("title", {}), native)
            if key == "protocol_date" and actual:
                actual = actual[:10]
            if key in values and not same(actual, values[key]):
                await self.op("PUT", path, {parameter: values[key]}, f"title.{key}")
        for key, native_key, action in (("authors", "authors", "Author"), ("reviewers", "reviwers", "Reviewer")):
            for person in getattr(model.title, key) or []:
                values = {PERSON_FIELDS.get(k, k): v for k, v in person.model_dump(exclude_unset=True).items()}
                old = next((x for x in current.get(native_key, []) if x.get("fio") == person.fio), None)
                if old and all(same(old.get(k), v) for k, v in values.items()):
                    continue
                payload = {**(old or {}), **values}
                await self.op(
                    "PUT" if old else "POST",
                    f"/Title/{'Update' if old else 'Add'}{action}",
                    payload,
                    f"title.{key}.{person.fio}",
                )

    async def _outcomes(self, model: RPDTemplate) -> None:
        current = (await self.read("/Rp3/Load")).get("items", [])
        for outcome in model.learning_outcomes or []:
            old = next(
                x
                for x in current
                if x.get("indiCode") == outcome.indicator
                and x.get("categoryId") == outcome.category
                and x.get("levelId") == outcome.level
            )
            values = {
                OUTCOME_FIELDS[key]: value
                for key, value in outcome.model_dump(exclude_none=True).items()
                if key in OUTCOME_FIELDS and (not self.outcome_settings.get("useExternalFosFiles") or key == "contents")
            }
            label = f"learning_outcomes.{outcome.indicator}.{outcome.category}.{outcome.level}"
            if old.get("isMock") or not all(same(old.get(k), v) for k, v in values.items()):
                await self.op("PUT", "/Rp3/UpdateLevel", {**old, **values}, label)
            if self.outcome_settings.get("useExternalFosFiles"):
                for kind, flag in ((1, "hasCurrentFos"), (0, "hasIntermediateFos")):
                    attachment = build_fos(outcome, kind, self.discipline)
                    if attachment is None:
                        continue
                    filename, content = attachment
                    parameters = {
                        "cardId": old["cardId"],
                        "categoryId": outcome.category,
                        "levelId": outcome.level,
                        "fosType": kind,
                    }
                    matches = old.get(flag) and await self.doc.download_fos(parameters) == content
                    if not matches:
                        await self.doc.upload_fos(parameters, filename, content)
                        self.operations.append(f"{label}.fos{kind}")
                    self.expected_fos.append((parameters, content, f"{label}.fos{kind}"))

    async def _resources(self, model: RPDTemplate) -> None:
        for ids, controller in ((model.software_ids, "ItIndex"), (model.information_system_ids, "IssIndex")):
            if ids is not None:
                current = {x["id"] for x in (await self.read(f"/{controller}/GetRefs")).get("refs", [])}
                missing = sorted(set(ids) - current)
                if missing:
                    await self.op("POST", f"/{controller}/AddRef", {"cardIds": missing}, controller)
        if model.facilities is not None:
            current = (await self.read("/MtoIndex/Load")).get("refs", [])
            for facility in model.facilities:
                old = next((x for x in current if x.get("catalogId", x.get("id")) == facility.catalog_id), None)
                if old is None:
                    result = await self.op(
                        "POST", "/MtoIndex/AddRef", {"id": facility.catalog_id}, f"facilities.{facility.catalog_id}"
                    )
                    old = result.get("card", {"id": facility.catalog_id})
                if facility.work_type_id is not None and old.get("wtype") != facility.work_type_id:
                    await self.op(
                        "PUT",
                        "/MtoIndex/UpdateRef",
                        {"id": old["id"], "wtype": facility.work_type_id},
                        f"facilities.{facility.catalog_id}.type",
                    )
        if model.literature is not None:
            current = (await self.read("/LitIndex/GetCards")).get("cards", [])
            keys = {(x.get("litId"), x.get("litType")) for x in current}
            for item in model.literature:
                if (item.catalog_id, item.kind) not in keys:
                    await self.op(
                        "POST",
                        "/LitIndex/AddLitRef",
                        {"litIds": [item.catalog_id], "litType": item.kind},
                        f"literature.{item.catalog_id}",
                    )

    async def _content(self, model: RPDTemplate, mode: str) -> None:
        content = await self.read("/Content/Load")
        cards = (await self.read("/UpResultsIndex/GetCards")).get("cards", [])
        if mode == "replace":
            attestation = {x["id"] for x in content["workTypes"] if x.get("isAttistation")}
            for part in content["parts"]:
                works = [x for x in content["works"] if x.get("partId") == part["id"]]
                if part.get("readOnly") or part.get("autolocated") or part.get("isSpecial"):
                    continue
                if works and all(x.get("wtype") in attestation | {143} for x in works):
                    continue
                if not any(section.name == part["name"] for section in model.sections or []):
                    await self.op(
                        "DELETE", "/Content/DeletePart", {"partId": part["id"]}, f"remove_section.{part['id']}"
                    )
            content = await self.read("/Content/Load")
        for section in model.sections or []:
            part = next((x for x in content["parts"] if x["name"] == section.name), None)
            if part is None:
                result = await self.op(
                    "POST",
                    "/Content/AddPart",
                    {"name": section.name, "comment": section.comment},
                    f"section.{section.number}",
                )
                part = result["part"]
            elif part.get("comment", "") != section.comment:
                result = await self.op(
                    "PUT",
                    "/Content/UpdatePart",
                    {**part, "comment": section.comment},
                    f"section.{section.number}.comment",
                )
                part = result["part"]
            if part.get("number") != section.number:
                order = (await self.read("/Content/Load"))["parts"]
                source_index = next(index for index, item in enumerate(order) if item["id"] == part["id"])
                await self.op(
                    "PUT",
                    "/Content/MovePart",
                    {"srcNum": source_index, "trgNum": section.number - 1},
                    f"section.{section.number}.order",
                )
            self.part_names[part["id"]] = section.name
            if mode == "replace":
                requested_works = [(None, item) for item in section.works]
                requested_works += [(theme.name, item) for theme in section.themes for item in theme.works]
                theme_names = {item["id"]: item["name"] for item in content.get("themes", [])}
                for old_work in [item for item in content["works"] if item.get("partId") == part["id"]]:
                    if old_work.get("wtype") not in NATIVE_TYPES.values() or old_work.get("readOnly"):
                        continue
                    keep = any(
                        theme_names.get(old_work.get("themeId")) == theme_name
                        and old_work.get("wtype") == (item.work_type_id or NATIVE_TYPES[item.wtype])
                        and old_work.get("termNum") == item.semester
                        and old_work.get("name")
                        == (str(item.activity_id) if item.activity_id is not None else item.name)
                        and (item.activity_id is None or old_work.get("comment", "") == (item.comment or item.name))
                        for theme_name, item in requested_works
                    )
                    if not keep:
                        await self.op(
                            "DELETE",
                            "/Content/DeleteWork",
                            {"partId": part["id"], "workId": old_work["id"]},
                            f"remove_work.{old_work['id']}",
                        )
                content = await self.read("/Content/Load")
            for work in section.works:
                await self._work(work, part["id"], None, content, cards)
            for theme in section.themes:
                native_theme = next(
                    (x for x in content["themes"] if x.get("partId") == part["id"] and x["name"] == theme.name), None
                )
                term = theme.works[0].semester if theme.works else None
                if any(work.semester != term for work in theme.works):
                    raise RPDError("Занятия в одной теме должны относиться к одному семестру.")
                if native_theme is None:
                    result = await self.op(
                        "POST",
                        "/Content/AddTheme",
                        {"partId": part["id"], "name": theme.name, "termNum": term, "comment": theme.comment},
                        f"theme.{theme.number}",
                    )
                    native_theme = result["theme"]
                self.theme_names[native_theme["id"]] = theme.name
                for work in theme.works:
                    await self._work(work, part["id"], native_theme["id"], content, cards)
            content = await self.read("/Content/Load")
        # The site generates attestation rows with zero preparation hours. Assign
        # the curriculum's control budget only when one exam/credit row owns it.
        summary = await self.read("/Content/GetSummary", "POST")
        control = next((x for x in summary.get("items", []) if x.get("ВидЗанятий") == "Часы на контроль"), None)
        if control:
            for term in content.get("terms", []):
                rows = [x for x in content["works"] if x.get("termNum") == term and x.get("wtypeControl") == 108]
                if len(rows) == 1 and not same(rows[0].get("hours"), control.get(f"УП_{term}")):
                    await self.op(
                        "PUT",
                        "/Content/UpdateWork",
                        {**rows[0], "hours": control[f"УП_{term}"], "wtypeConfig": 108},
                        f"attestation.{term}",
                    )

    async def _work(
        self,
        item: WorkItemTemplate,
        part_id: int,
        theme_id: int | None,
        content: dict[str, Any],
        cards: list[dict[str, Any]],
    ) -> None:
        native_type = item.work_type_id or NATIVE_TYPES[item.wtype]
        name = str(item.activity_id) if item.activity_id is not None else item.name
        comment = item.comment or (item.name if item.activity_id is not None else "")
        values = {
            "partId": part_id,
            "themeId": theme_id,
            "wtype": native_type,
            "termNum": item.semester,
            "name": name,
            "hours": item.hours,
            "comment": comment,
        }
        if item.control_work_type_id is not None:
            values["wtypeConfig"] = item.control_work_type_id
        old = next(
            (
                x
                for x in content["works"]
                if x.get("partId") == part_id
                and x.get("themeId") == theme_id
                and x.get("wtype") == native_type
                and x.get("termNum") == item.semester
                and x.get("name") == name
                and (item.activity_id is None or x.get("comment", "") == comment)
            ),
            None,
        )
        if old and all(same(old.get(k), v) for k, v in values.items() if k != "wtypeConfig"):
            work = old
        else:
            result = await self.op(
                "PUT" if old else "POST",
                "/Content/UpdateWork" if old else "/Content/AddWork",
                {**(old or {}), **values},
                f"work.{item.name}",
            )
            work = result["work"]
        if work.get("errors"):
            raise RPDError(f"Сайт обнаружил ошибки занятия «{item.name}»: {work['errors']}.")
        expected_refs = {
            card["id"]
            for code in item.indicators
            for card in cards
            if card.get("code") == code or card.get("code", "").startswith(f"{code}-")
        }
        if item.indicators and not expected_refs:
            raise RPDError(f"Для занятия «{item.name}» не созданы результаты обучения.")
        current_refs = {x["id"] for x in work.get("upResRefs", [])}
        if expected_refs - current_refs:
            await self.op(
                "POST",
                "/UpResultsIndex/AddUpResRef",
                {"workIds": [work["id"]], "upResIds": sorted(expected_refs - current_refs)},
                f"work.{item.name}.indicators",
            )
        self.expected_works.append((work["id"], values, sorted(expected_refs)))
        self.work_indicators[work["id"]] = item.indicators

    async def verify(self, model: RPDTemplate, *, complete: bool = False) -> list[dict[str, Any]]:
        errors: list[dict[str, Any]] = []

        def check(path: str, actual: Any, expected: Any) -> None:
            if not same(actual, expected):
                errors.append({"field": path, "error": "Прочитанное значение не совпало с шаблоном."})

        if model.title:
            current = await self.read("/Title/GetTitle")
            values = model.title.model_dump(exclude_none=True)
            for key, (_, _, native) in TITLE_FIELDS.items():
                if key in values:
                    value = at(current.get("title", {}), native)
                    if key == "protocol_date" and value:
                        value = value[:10]
                    check(f"title.{key}", value, values[key])
            for key, native_key in (("authors", "authors"), ("reviewers", "reviwers")):
                for person in getattr(model.title, key) or []:
                    old = next((x for x in current.get(native_key, []) if x.get("fio") == person.fio), {})
                    for k, v in person.model_dump(exclude_unset=True).items():
                        check(f"title.{key}.{k}", old.get(PERSON_FIELDS.get(k, k)), v)
        if model.goals:
            current = await self.read("/Rp12/Load")
            for key, value in model.goals.model_dump(exclude_none=True).items():
                check(f"goals.{key}", goal_text(current, key), value)
        if model.learning_outcomes:
            current = (await self.read("/Rp3/Load")).get("items", [])
            for outcome in model.learning_outcomes:
                old = next(
                    (
                        x
                        for x in current
                        if x.get("indiCode") == outcome.indicator
                        and x.get("categoryId") == outcome.category
                        and x.get("levelId") == outcome.level
                    ),
                    {},
                )
                for key, value in outcome.model_dump(exclude_none=True).items():
                    if key in OUTCOME_FIELDS:
                        if self.outcome_settings.get("useExternalFosFiles") and key != "contents":
                            continue
                        check(
                            f"learning_outcomes.{outcome.indicator}.{outcome.category}.{key}",
                            old.get(OUTCOME_FIELDS[key]),
                            value,
                        )
            for parameters, expected, label in self.expected_fos:
                actual = await self.doc.download_fos(parameters)
                check(label, actual, expected)
        if model.fos:
            current = await self.read("/Fos/Load")
            for key, value in model.fos.model_dump(exclude_none=True).items():
                check(f"fos.{key}", current.get(FOS_FIELDS[key][1]), value)
        for ids, controller in ((model.software_ids, "ItIndex"), (model.information_system_ids, "IssIndex")):
            if ids is not None:
                current = {x["id"] for x in (await self.read(f"/{controller}/GetRefs")).get("refs", [])}
                if set(ids) - current:
                    errors.append({"field": controller, "error": "Не все записи справочника закреплены."})
        if model.facilities is not None:
            current = (await self.read("/MtoIndex/Load")).get("refs", [])
            for item in model.facilities:
                old = next((x for x in current if x.get("catalogId", x.get("id")) == item.catalog_id), {})
                check(f"facilities.{item.catalog_id}", old.get("catalogId", old.get("id")), item.catalog_id)
                if item.work_type_id is not None:
                    check(f"facilities.{item.catalog_id}.type", old.get("wtype"), item.work_type_id)
        if model.literature is not None:
            keys = {
                (x.get("litId"), x.get("litType")) for x in (await self.read("/LitIndex/GetCards")).get("cards", [])
            }
            for item in model.literature:
                if (item.catalog_id, item.kind) not in keys:
                    errors.append(
                        {"field": f"literature.{item.catalog_id}", "error": "Ссылка на издание не закреплена."}
                    )
        if model.sections is not None:
            current = await self.read("/Content/Load")
            for section in model.sections:
                part = next((x for x in current["parts"] if x.get("name") == section.name), {})
                check(f"section.{section.number}.number", part.get("number"), section.number)
                check(f"section.{section.number}.comment", part.get("comment") or "", section.comment)
            works = {x["id"]: x for x in current["works"]}
            for work_id, expected, refs in self.expected_works:
                target_part = next(
                    (x["id"] for x in current["parts"] if x["name"] == self.part_names.get(expected["partId"])), None
                )
                target_theme = (
                    next(
                        (
                            x["id"]
                            for x in current["themes"]
                            if x.get("partId") == target_part and x["name"] == self.theme_names.get(expected["themeId"])
                        ),
                        None,
                    )
                    if expected["themeId"] is not None
                    else None
                )
                expected = {**expected, "partId": target_part, "themeId": target_theme}
                identity = ["partId", "themeId", "name", "wtype", "termNum"]
                if str(expected["name"]).isdigit() and expected["wtype"] in {103, 107}:
                    identity.append("comment")
                work = works.get(work_id) or next(
                    (x for x in current["works"] if all(same(x.get(k), expected[k]) for k in identity)),
                    {},
                )
                for key, value in expected.items():
                    if key != "wtypeConfig":
                        check(f"work.{work_id}.{key}", work.get(key), value)
                actual_codes = {x.get("code", "") for x in work.get("upResRefs", [])}
                if any(
                    not any(code == actual or actual.startswith(f"{code}-") for actual in actual_codes)
                    for code in self.work_indicators[work_id]
                ):
                    errors.append(
                        {"field": f"work.{work_id}.indicators", "error": "Не все результаты обучения закреплены."}
                    )
            if complete:
                summary = await self.read("/Content/GetSummary", "POST")
                for row in summary.get("items", []):
                    for term in current.get("terms", []):
                        planned, actual = row.get(f"УП_{term}"), row.get(f"РПД_{term}")
                        if (
                            isinstance(planned, (int, float))
                            and isinstance(actual, (int, float))
                            and not same(actual, planned)
                        ):
                            errors.append(
                                {
                                    "field": f"hours.{term}.{row.get('ВидЗанятий')}",
                                    "error": f"По плану {planned:g}, в РПД {actual:g}.",
                                }
                            )
        text = model.model_dump(exclude_none=True)
        if any(key in text for key in TEXT_FIELDS):
            current = await self.read("/Rp/Load")
            for key, (_, native) in TEXT_FIELDS.items():
                if key in text:
                    check(key, current.get(native), text[key])
        return errors
