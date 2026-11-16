"""Local, side-effect-free validation of an AI-generated autofill template."""

from __future__ import annotations

import math
from typing import Any

from pydantic import ValidationError

from .schemas import TEXT_FIELDS, WORK_TYPES, HourBudget, RPDTemplate, SemesterHourBudget, WorkItemTemplate


def work_kind(work: WorkItemTemplate) -> str | None:
    if work.work_type_id is not None:
        return {101: "lectures", 102: "labs", 103: "practices", 107: "self_study"}.get(work.work_type_id)
    return WORK_TYPES[work.wtype]


def validate_template(
    template: dict[str, Any] | RPDTemplate,
    hour_budget: HourBudget | None = None,
    semester_budgets: list[SemesterHourBudget] | None = None,
    allowed_indicators: list[str] | None = None,
) -> dict[str, Any]:
    """Validate structure and hours without making HTTP requests or changing a program."""
    errors: list[dict[str, str]] = []
    warnings: list[str] = []
    totals = dict.fromkeys(WORK_TYPES.values(), 0.0)
    counts = {"sections": 0, "themes": 0, "works": 0}
    by_semester: dict[int, dict[str, float]] = {}
    try:
        model = RPDTemplate.model_validate(template)
    except ValidationError as e:
        return {
            "valid": False,
            "errors": [
                {"path": ".".join(map(str, error["loc"])), "message": error["msg"]}
                for error in e.errors(include_input=False, include_url=False)
            ],
            "warnings": [],
            "hour_totals": totals,
            "counts": counts,
            "hours_checked": False,
        }

    text_fields = [key for key in TEXT_FIELDS if getattr(model, key) is not None]
    structured_fields = [
        key
        for key in (
            "title",
            "goals",
            "learning_outcomes",
            "fos",
            "literature",
            "software_ids",
            "information_system_ids",
            "facilities",
        )
        if (
            getattr(model, key).model_dump(exclude_none=True)
            if hasattr(getattr(model, key), "model_dump")
            else getattr(model, key)
        )
    ]
    if not text_fields and model.sections is None and not structured_fields:
        errors.append({"path": "template", "message": "Шаблон не содержит изменений."})

    outcome_keys = set()
    for index, outcome in enumerate(model.learning_outcomes or []):
        key = (outcome.indicator, outcome.category, outcome.level)
        if key in outcome_keys:
            errors.append(
                {
                    "path": f"learning_outcomes.{index}",
                    "message": "Индикатор, категория и уровень результата повторяются.",
                }
            )
        outcome_keys.add(key)

    section_numbers: set[int] = set()
    section_names: set[str] = set()
    if model.sections is not None:
        if not model.sections:
            errors.append({"path": "sections", "message": "Список разделов не должен быть пустым."})
        for section_index, section in enumerate(model.sections):
            section_path = f"sections.{section_index}"
            counts["sections"] += 1
            if not section.name.strip():
                errors.append({"path": f"{section_path}.name", "message": "Название не должно состоять из пробелов."})
            if section.number in section_numbers:
                errors.append({"path": f"{section_path}.number", "message": "Номер раздела повторяется."})
            section_numbers.add(section.number)
            if section.name in section_names:
                errors.append({"path": f"{section_path}.name", "message": "Название раздела повторяется."})
            section_names.add(section.name)
            if not section.themes and not section.works:
                errors.append({"path": f"{section_path}.themes", "message": "В разделе отсутствуют темы."})
            theme_numbers: set[int] = set()
            theme_names: set[str] = set()
            work_groups = [(f"{section_path}.works", section.works)] if section.works else []
            for theme_index, theme in enumerate(section.themes):
                theme_path = f"{section_path}.themes.{theme_index}"
                counts["themes"] += 1
                if not theme.name.strip():
                    errors.append({"path": f"{theme_path}.name", "message": "Название не должно состоять из пробелов."})
                if theme.number in theme_numbers:
                    errors.append({"path": f"{theme_path}.number", "message": "Номер темы в разделе повторяется."})
                theme_numbers.add(theme.number)
                if theme.name in theme_names:
                    errors.append({"path": f"{theme_path}.name", "message": "Название темы в разделе повторяется."})
                theme_names.add(theme.name)
                if not theme.works:
                    errors.append({"path": f"{theme_path}.works", "message": "В теме отсутствуют занятия."})
                work_groups.append((f"{theme_path}.works", theme.works))
            for works_path, works in work_groups:
                work_keys = set()
                for work_index, work in enumerate(works):
                    counts["works"] += 1
                    if not work.name.strip():
                        errors.append(
                            {
                                "path": f"{works_path}.{work_index}.name",
                                "message": "Название не должно состоять из пробелов.",
                            }
                        )
                    kind = work_kind(work)
                    if kind:
                        totals[kind] += work.hours
                    work_path = f"{works_path}.{work_index}"
                    identity = (work.name, work.work_type_id or (100 + work.wtype), work.semester)
                    if identity in work_keys:
                        errors.append({"path": work_path, "message": "Название, вид и семестр занятия повторяются."})
                    work_keys.add(identity)
                    if work.semester is not None:
                        by_semester.setdefault(work.semester, dict.fromkeys(WORK_TYPES.values(), 0.0))
                        if kind:
                            by_semester[work.semester][kind] += work.hours
                    elif semester_budgets is not None:
                        errors.append({"path": f"{work_path}.semester", "message": "Не указан семестр занятия."})
                    if allowed_indicators is not None:
                        unknown = sorted(
                            code
                            for code in set(work.indicators)
                            if not any(
                                code == allowed or code.startswith(f"{allowed}-") for allowed in allowed_indicators
                            )
                        )
                        if unknown:
                            errors.append(
                                {
                                    "path": f"{work_path}.indicators",
                                    "message": f"Индикаторы отсутствуют в учебном плане: {', '.join(unknown)}.",
                                }
                            )
                        if allowed_indicators and not work.indicators:
                            errors.append(
                                {"path": f"{work_path}.indicators", "message": "Не указаны индикаторы занятия."}
                            )

    if hour_budget is not None and model.sections is not None:
        for kind, expected in hour_budget.model_dump().items():
            actual = totals[kind]
            if not math.isclose(actual, expected, rel_tol=0, abs_tol=1e-6):
                errors.append(
                    {
                        "path": f"hours.{kind}",
                        "message": f"Сумма часов {actual:g} не совпадает с ожидаемой {expected:g}.",
                    }
                )
    elif model.sections is not None and semester_budgets is None:
        warnings.append("Часы рассчитаны, но не сверены с учебным планом: бюджет часов не передан.")

    if semester_budgets is not None and model.sections is not None:
        budgets = {budget.semester: budget for budget in semester_budgets}
        if len(budgets) != len(semester_budgets):
            errors.append({"path": "semester_budgets", "message": "Семестр в бюджете часов повторяется."})
        for semester in sorted(set(by_semester) - set(budgets)):
            errors.append({"path": f"hours_by_semester.{semester}", "message": "Семестр отсутствует в учебном плане."})
        for semester, budget in budgets.items():
            actual = by_semester.get(semester, dict.fromkeys(WORK_TYPES.values(), 0.0))
            for kind, expected in budget.model_dump(exclude={"semester"}).items():
                if not math.isclose(actual[kind], expected, rel_tol=0, abs_tol=1e-6):
                    errors.append(
                        {
                            "path": f"hours_by_semester.{semester}.{kind}",
                            "message": f"Сумма часов {actual[kind]:g} не совпадает с ожидаемой {expected:g}.",
                        }
                    )

    return {
        "valid": not errors,
        "errors": errors,
        "warnings": warnings,
        "text_fields": text_fields,
        "structured_fields": structured_fields,
        "hour_totals": totals,
        "hours_by_semester": by_semester,
        "counts": counts,
        "hours_checked": (hour_budget is not None or semester_budgets is not None) and model.sections is not None,
    }
