"""Structured input exposed to AI clients through the MCP tool schema."""

from __future__ import annotations

import base64
import binascii
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class TemplateModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class WorkItemTemplate(TemplateModel):
    name: str = Field(min_length=1, description="Название занятия или задания для самостоятельной работы")
    wtype: Literal[1, 2, 3, 4] = Field(default=1, description="1 — лекция, 2 — лабораторная, 3 — практика, 4 — СРС")
    hours: float = Field(
        default=2.0, gt=0, description="Часы одного занятия: лекция ≤2, лабораторная ≤4, практика ≤2, СРС ≤4"
    )
    comment: str = Field(
        default="",
        description="Подробное содержание конкретного занятия: вопросы, методы, задания и ожидаемый результат",
    )
    semester: int | None = Field(default=None, gt=0, description="Семестр из учебного плана")
    indicators: list[str] = Field(
        default_factory=list, description="Коды индикаторов из учебного плана, например ПК-2.2"
    )
    work_type_id: int | None = Field(
        default=None, gt=0, description="Реальный ID вида работы из workTypes; имеет приоритет над wtype"
    )
    control_work_type_id: int | None = Field(default=None, gt=0, description="Вид работы при подготовке к аттестации")
    activity_id: int | None = Field(
        default=None, gt=0, description="ID занятия из prItems или srItems, если требуется справочник"
    )

    @model_validator(mode="after")
    def check_session_hours(self):
        native = self.work_type_id or {1: 101, 2: 102, 3: 103, 4: 107}[self.wtype]
        limit = {101: 2, 102: 4, 103: 2, 107: 4}.get(native)
        if limit is not None and self.hours > limit:
            raise ValueError(
                f"Одно занятие вида {native} не может превышать {limit} ч.; разбейте тему на отдельные содержательные занятия"
            )
        return self


class ThemeTemplate(TemplateModel):
    name: str = Field(min_length=1, description="Название темы дисциплины")
    number: int = Field(default=1, ge=1)
    comment: str = ""
    works: list[WorkItemTemplate] = Field(default_factory=list)


class SectionTemplate(TemplateModel):
    name: str = Field(min_length=1, description="Название раздела дисциплины")
    number: int = Field(default=1, ge=1)
    comment: str = ""
    themes: list[ThemeTemplate] = Field(default_factory=list)
    works: list[WorkItemTemplate] = Field(
        default_factory=list, description="Занятия непосредственно в разделе, без промежуточных тем"
    )


class HourBudget(TemplateModel):
    lectures: float = Field(ge=0)
    labs: float = Field(ge=0)
    practices: float = Field(ge=0)
    self_study: float = Field(ge=0)


class SemesterHourBudget(HourBudget):
    semester: int = Field(gt=0)


class PersonTemplate(TemplateModel):
    fio: str = Field(min_length=1)
    position: str = ""
    rank: str = ""
    academic_rank: str = ""
    department_id: int | None = None
    professor_id: int | None = None
    company: str | None = None


class TitleTemplate(TemplateModel):
    disc_name: str | None = None
    head_of_department: str | None = None
    protocol_number: str | None = None
    protocol_date: str | None = Field(default=None, description="Фактическая дата протокола в формате YYYY-MM-DD")
    authors: list[PersonTemplate] | None = None
    reviewers: list[PersonTemplate] | None = None

    @field_validator("protocol_date")
    @classmethod
    def valid_protocol_date(cls, value: str | None) -> str | None:
        if value is None:
            return None
        try:
            return (
                datetime.strptime(value.strip(), "%d.%m.%Y").date()
                if "." in value
                else date.fromisoformat(value.strip())
            ).isoformat()
        except ValueError as exc:
            raise ValueError("Укажите действительную дату утверждения: YYYY-MM-DD или ДД.ММ.ГГГГ") from exc

    @field_validator("protocol_number")
    @classmethod
    def valid_protocol_number(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("Номер протокола не должен быть пустым")
        return value.strip() if value is not None else None


class GoalsTemplate(TemplateModel):
    aims: str | None = None
    tasks: str | None = None
    prerequisites: str | None = None
    outgoings: str | None = None


class FOSAttachment(TemplateModel):
    filename: str = Field(pattern=r"^[^/\\]+\.docx$", description="Имя файла DOCX без пути")
    content_base64: str = Field(max_length=14_000_000, description="Содержимое готового DOCX в base64")

    @field_validator("content_base64")
    @classmethod
    def valid_docx(cls, value: str) -> str:
        from io import BytesIO
        from zipfile import BadZipFile, ZipFile

        try:
            data = base64.b64decode(value, validate=True)
            with ZipFile(BytesIO(data)) as archive:
                if "word/document.xml" not in archive.namelist():
                    raise ValueError("Вложение должно быть документом DOCX")
                if sum(item.file_size for item in archive.infolist()) > 40_000_000:
                    raise ValueError("Распакованный DOCX превышает 40 МБ")
        except (binascii.Error, BadZipFile) as exc:
            raise ValueError("Некорректный DOCX или base64") from exc
        return value


class AssessmentTask(TemplateModel):
    type: Literal[
        "matching", "sequencing", "single_choice_justified", "multiple_choice_justified", "open_response", "calculation"
    ]
    prompt: str = Field(
        min_length=1,
        description="Конкретный вопрос или условие с исходными данными; типовая инструкция добавится автоматически",
    )
    minutes: float = Field(gt=0, description="Реалистичное время выполнения в минутах")
    answer: str | None = Field(
        default=None, description="Эталонный развёрнутый ответ или ход решения для открытого задания"
    )
    explanation: str | None = Field(
        default=None, description="Обоснование правильного выбора для комбинированного задания"
    )
    options: list[str] = Field(default_factory=list, description="Варианты для комбинированного задания")
    left_items: list[str] = Field(default_factory=list, description="Левая группа для соответствия")
    right_items: list[str] = Field(default_factory=list, description="Правая группа для соответствия")
    sequence_items: list[str] = Field(default_factory=list, description="Перемешанные элементы последовательности")
    correct: list[int] = Field(
        default_factory=list,
        description="Номера от 1: соответствия по порядку левой группы, правильная последовательность или выбранные варианты",
    )

    @model_validator(mode="after")
    def validate_task(self):
        if not self.prompt.strip():
            raise ValueError("Условие задания не должно быть пустым")
        for items in (self.options, self.left_items, self.right_items, self.sequence_items):
            if any(not value.strip() for value in items) or len(items) != len(set(items)):
                raise ValueError("Пункты задания должны быть непустыми и не повторяться")
        if self.type == "matching":
            if len(self.left_items) < 2 or len(self.right_items) < 2 or len(self.correct) != len(self.left_items):
                raise ValueError("Для соответствия нужны две группы от двух пунктов и ключ для каждого левого пункта")
            size = len(self.right_items)
        elif self.type == "sequencing":
            size = len(self.sequence_items)
            if size < 2 or sorted(self.correct) != list(range(1, size + 1)):
                raise ValueError("Ключ последовательности должен содержать все номера элементов ровно один раз")
        elif self.type.endswith("_justified"):
            size = len(self.options)
            if (self.type == "single_choice_justified" and (size != 4 or len(self.correct) != 1)) or (
                self.type == "multiple_choice_justified" and (size < 4 or len(self.correct) < 2)
            ):
                raise ValueError(
                    "Для одного ответа нужны 4 варианта и один ключ; для нескольких — от 4 вариантов и от 2 ключей"
                )
            if not self.explanation or not self.explanation.strip():
                raise ValueError("Комбинированное задание требует обоснования правильного ответа")
        else:
            size = 0
            if not self.answer or not self.answer.strip():
                raise ValueError("Открытое задание требует эталонного ответа с обоснованием или ходом решения")
        if size and (
            not self.correct
            or len(self.correct) != len(set(self.correct))
            or any(x < 1 or x > size for x in self.correct)
        ):
            raise ValueError("Ключ содержит повторяющиеся или отсутствующие номера вариантов")
        allowed = {
            "matching": {"left_items", "right_items", "correct"},
            "sequencing": {"sequence_items", "correct"},
            "single_choice_justified": {"options", "correct", "explanation"},
            "multiple_choice_justified": {"options", "correct", "explanation"},
            "open_response": {"answer", "explanation"},
            "calculation": {"answer", "explanation"},
        }[self.type]
        if any(
            getattr(self, key)
            for key in {"options", "left_items", "right_items", "sequence_items", "correct", "answer", "explanation"}
            - allowed
        ):
            raise ValueError("Задание содержит поля другого типа; они не могут быть молча проигнорированы")
        return self


class LearningOutcomeTemplate(TemplateModel):
    indicator: str = Field(min_length=1, description="Код индикатора учебного плана, например ОПК-2.1")
    category: Literal[1, 2, 3] = Field(description="1 — знать, 2 — уметь, 3 — владеть")
    level: int = Field(default=1, ge=1)
    contents: str = Field(min_length=1)
    competency_description: str | None = Field(
        default=None, description="Формулировка родительской компетенции из учебного плана"
    )
    current_tasks: list[AssessmentTask] | None = Field(
        default=None,
        min_length=5,
        description="Не менее 5 заданий текущего контроля для этой категории каждого индикатора",
    )
    intermediate_tasks: list[AssessmentTask] | None = Field(
        default=None,
        min_length=10,
        description="Не менее 10 заданий промежуточного контроля для этой категории каждого индикатора",
    )
    rating_scale: str | None = None
    mark_scale: str | None = None
    mark5: str | None = None
    mark4: str | None = None
    mark3: str | None = None
    mark2: str | None = None
    current_control_task1: str | None = None
    current_control_task2: str | None = None
    intermediate_control_task1: str | None = None
    intermediate_control_task2: str | None = None
    current_fos: FOSAttachment | None = Field(
        default=None, description="Готовый файл текущего ФОС; иначе DOCX создаётся из заданий и критериев"
    )
    intermediate_fos: FOSAttachment | None = Field(
        default=None, description="Готовый файл промежуточного ФОС; иначе DOCX создаётся из заданий и критериев"
    )

    @model_validator(mode="after")
    def validate_assessments(self):
        legacy = (
            "current_control_task1",
            "current_control_task2",
            "intermediate_control_task1",
            "intermediate_control_task2",
        )
        if any(getattr(self, key) is not None for key in legacy):
            raise ValueError(
                "Поля *_control_task1/2 заменены на current_tasks/intermediate_tasks: нужны тип задания, ключ ответа и время"
            )
        for prefix in ("current", "intermediate"):
            tasks = getattr(self, f"{prefix}_tasks")
            if tasks and getattr(self, f"{prefix}_fos"):
                raise ValueError("Передайте либо структурированные задания, либо готовый файл для одного вида контроля")
            for task in tasks or []:
                closed = task.type in {"matching", "sequencing"}
                if (self.category == 1) != closed:
                    raise ValueError(
                        "Для «знать» нужны соответствие/последовательность; для «уметь» и «владеть» — открытые или комбинированные задания с обоснованием"
                    )
            if len({task.model_dump_json() for task in tasks or []}) != len(tasks or []):
                raise ValueError("Задания в одном наборе не должны повторяться")
        return self


class FOSTemplate(TemplateModel):
    questions: str | None = Field(default=None, description="Типовые контрольные вопросы и задания")
    coursework_topics: str | None = None
    assessment_materials: str | None = None
    assessment_types: str | None = None
    rating_plan: str | None = None
    control_work_guidance: str | None = None
    coursework_guidance: str | None = None


class LiteratureReference(TemplateModel):
    catalog_id: int = Field(gt=0, description="ID реального издания в каталоге сервиса")
    kind: Literal[1, 2, 3] = Field(default=1, description="1 — основная, 2 — дополнительная, 3 — методическая")


class MTOReference(TemplateModel):
    catalog_id: int = Field(gt=0)
    work_type_id: int | None = Field(default=None, gt=0)


class RPDTemplate(TemplateModel):
    name: str = "Шаблон РПД"
    description: str = ""
    defer_approval: bool = Field(
        default=False,
        description="Только по явному ответу пользователя: оставить дату утверждения и номер протокола незаполненными",
    )
    comments_text: str | None = Field(
        default=None, description="Замечания проверяющего; требуется право canWriteComments"
    )
    title: TitleTemplate | None = None
    goals: GoalsTemplate | None = None
    learning_outcomes: list[LearningOutcomeTemplate] | None = None
    fos: FOSTemplate | None = None
    literature: list[LiteratureReference] | None = None
    software_ids: list[int] | None = Field(default=None, description="ID программного обеспечения из ItIndex")
    information_system_ids: list[int] | None = Field(default=None, description="ID справочных систем из IssIndex")
    facilities: list[MTOReference] | None = None
    it_text: str | None = Field(default=None, description="Информационные технологии и программное обеспечение")
    iss_text: str | None = Field(default=None, description="Информационные справочные системы")
    mu_text: str | None = Field(default=None, description="Методические указания для обучающихся")
    mup_text: str | None = Field(default=None, description="Методические указания по практической подготовке")
    mto_text: str | None = Field(default=None, description="Материально-техническое обеспечение")
    literature_text: str | None = Field(default=None, description="Текст списка литературы")
    ovz_text: str | None = Field(default=None, description="Обучение лиц с ОВЗ")
    vr_text: str | None = Field(default=None, description="Воспитательная работа")
    sections: list[SectionTemplate] | None = Field(
        default=None,
        description="Разделы, темы и занятия; отсутствие поля означает сохранение существующего содержания",
    )

    @model_validator(mode="after")
    def approval_decision(self):
        if (
            self.defer_approval
            and self.title
            and (self.title.protocol_number is not None or self.title.protocol_date is not None)
        ):
            raise ValueError("При defer_approval=true дата утверждения и номер протокола должны быть незаполненными")
        return self


TEXT_FIELDS = {
    "comments_text": ("/Rp/SetComments", "comments"),
    "it_text": ("/Rp/SetItText", "itText"),
    "iss_text": ("/Rp/SetIssText", "issText"),
    "mu_text": ("/Rp/SetMUText", "muText"),
    "mup_text": ("/Rp/SetMUPText", "mupText"),
    "mto_text": ("/Rp/SetMtoText", "mtoText"),
    "literature_text": ("/Rp/SetLiterature", "literature"),
    "ovz_text": ("/Rp/SetOvz", "ovz"),
    "vr_text": ("/Rp/SetVr", "vr"),
}

WORK_TYPES = {1: "lectures", 2: "labs", 3: "practices", 4: "self_study"}
