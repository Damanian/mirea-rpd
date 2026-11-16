"""Validation of generated content, including multiple semesters."""

from copy import deepcopy

import pytest
from pydantic import ValidationError

from mirea_rpd.schemas import HourBudget, RPDTemplate, SemesterHourBudget, TitleTemplate, WorkItemTemplate
from mirea_rpd.validation import validate_template


@pytest.mark.parametrize("template", [{"title": {}}, {"goals": {}}, {"fos": {}}, {"software_ids": []}])
def test_empty_nested_template_does_not_count_as_an_edit(template):
    assert not validate_template(template)["valid"]


@pytest.fixture
def content():
    return {
        "sections": [
            {
                "name": "Основы",
                "themes": [
                    {
                        "name": "Введение",
                        "works": [
                            {"name": "Лекция", "wtype": 1, "hours": 2, "semester": 6, "indicators": ["ПК-2.2"]},
                            {"name": "СРС", "wtype": 4, "hours": 3, "semester": 7, "indicators": ["ПК-2.2"]},
                        ],
                    }
                ],
            }
        ],
    }


@pytest.mark.parametrize("hours", [0, -1, float("nan"), float("inf")])
def test_invalid_hours(content, hours):
    content["sections"][0]["themes"][0]["works"][0]["hours"] = hours
    assert validate_template(content)["valid"] is False


def test_invalid_work_type(content):
    content["sections"][0]["themes"][0]["works"][0]["wtype"] = 99
    assert validate_template(content)["valid"] is False


def test_hour_totals_and_budget(content):
    budget = HourBudget(lectures=2, labs=0, practices=0, self_study=3)
    report = validate_template(content, budget)
    assert report["valid"] is True
    assert report["hours_checked"] is True
    assert report["hour_totals"] == {"lectures": 2, "labs": 0, "practices": 0, "self_study": 3}
    assert report["counts"] == {"sections": 1, "themes": 1, "works": 2}


def test_hour_mismatch(content):
    report = validate_template(content, HourBudget(lectures=4, labs=0, practices=0, self_study=3))
    assert report["valid"] is False
    assert report["errors"][0]["path"] == "hours.lectures"


def test_correct_total_does_not_hide_wrong_semester(content):
    budgets = [
        SemesterHourBudget(semester=6, lectures=0, labs=0, practices=0, self_study=3),
        SemesterHourBudget(semester=7, lectures=2, labs=0, practices=0, self_study=0),
    ]
    report = validate_template(content, semester_budgets=budgets)
    assert report["valid"] is False
    assert all(error["path"].startswith("hours_by_semester.") for error in report["errors"])


def test_unknown_competency_is_rejected(content):
    report = validate_template(content, allowed_indicators=["ОПК-1.1"])
    assert report["valid"] is False
    assert "индикаторы" in report["errors"][0]["message"].lower()


def test_duplicate_section_numbers(content):
    content["sections"].append(deepcopy(content["sections"][0]))
    assert any(error["path"] == "sections.1.number" for error in validate_template(content)["errors"])


@pytest.mark.parametrize("template", [{}, {"sections": []}, {"sections": [{"name": " "}]}])
def test_empty_content_is_not_valid(template):
    assert validate_template(template)["valid"] is False


@pytest.mark.parametrize("wtype,limit,native", [(1, 2, 101), (2, 4, 102), (3, 2, 103), (4, 4, 107)])
def test_per_session_limits_cannot_be_bypassed_by_native_type(wtype, limit, native):
    assert WorkItemTemplate(name="Занятие", wtype=wtype, hours=limit).hours == limit
    with pytest.raises(ValidationError):
        WorkItemTemplate(name="Занятие", wtype=wtype, hours=limit + 0.5)
    with pytest.raises(ValidationError):
        WorkItemTemplate(name="Занятие", wtype=2, work_type_id=native, hours=limit + 0.5)


def test_protocol_date_is_real_and_normalized():
    assert TitleTemplate(protocol_date="25.06.2026").protocol_date == "2026-06-25"
    with pytest.raises(ValidationError):
        TitleTemplate(protocol_date="31.02.2026")
    with pytest.raises(ValidationError):
        TitleTemplate(protocol_number="   ")
    with pytest.raises(ValidationError):
        RPDTemplate(defer_approval=True, title={"protocol_number": "7"})
