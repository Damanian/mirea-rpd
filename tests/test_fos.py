import base64
from importlib.resources import files
from io import BytesIO
from zipfile import ZipFile

import pytest
from docx import Document
from pydantic import ValidationError

from mirea_rpd.fos import HEADERS, build_fos, check_fos
from mirea_rpd.schemas import AssessmentTask, FOSAttachment, LearningOutcomeTemplate


def test_generated_fos_is_reproducible_and_preserves_tasks_and_rubric():
    outcome = LearningOutcomeTemplate(
        indicator="ОПК-2.1",
        category=1,
        contents="Закон Ома",
        current_tasks=[
            AssessmentTask(
                type="sequencing",
                prompt=f"Порядок исследования цепи {i + 1}",
                sequence_items=["Измерить ток", "Подключить источник"],
                correct=[2, 1],
                minutes=3,
            )
            for i in range(5)
        ],
        mark5="Расчёт обоснован",
    )
    name, content = build_fos(outcome, 1, "Схемотехника")
    assert build_fos(outcome, 1, "Схемотехника") == (name, content)
    doc = Document(BytesIO(content))
    text = "\n".join(paragraph.text for paragraph in doc.paragraphs)
    text += "\n".join(cell.text for table in doc.tables for row in table.rows for cell in row.cells)
    assert all(value in text for value in (outcome.contents, outcome.current_tasks[0].prompt, outcome.mark5, "ОПК-2"))
    assert tuple(cell.text for cell in doc.tables[0].rows[0].cells) == HEADERS
    assert len(doc.tables[0].rows) == 6
    assert doc.tables[0].rows[1].cells[1].text == "2 → 1"
    assert check_fos(content, 1, 1) == []
    assert check_fos(content, 1, 0)  # Five tasks cannot pass as intermediate control.
    with (
        ZipFile(BytesIO(content)) as generated,
        ZipFile(BytesIO(files("mirea_rpd").joinpath("assets/fos-template.docx").read_bytes())) as template,
    ):
        for part in template.namelist():
            if part != "word/document.xml":
                assert generated.read(part) == template.read(part), part
    assert (
        doc.tables[0].rows[0]._tr.trPr.find("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}tblHeader")
        is not None
    )
    assert build_fos(outcome, 0, "Схемотехника") is None


@pytest.mark.parametrize("content", ["not base64!", base64.b64encode(b"not docx").decode()])
def test_invalid_fos_attachment_is_rejected(content):
    with pytest.raises(ValidationError):
        FOSAttachment(filename="fos.docx", content_base64=content)


def test_insufficient_tasks_wrong_category_and_invalid_keys_are_rejected():
    task = {
        "type": "sequencing",
        "prompt": "Проверка схемы",
        "sequence_items": ["Расчёт", "Сборка"],
        "correct": [1, 2],
        "minutes": 3,
    }
    with pytest.raises(ValidationError):
        AssessmentTask.model_validate({**task, "correct": [1, 1]})
    with pytest.raises(ValidationError):
        LearningOutcomeTemplate(indicator="ОПК-2.1", category=1, contents="Закон Ома", current_tasks=[task])
    with pytest.raises(ValidationError):
        LearningOutcomeTemplate(
            indicator="ОПК-2.1",
            category=2,
            contents="Рассчитывать цепь",
            current_tasks=[{**task, "prompt": str(i)} for i in range(5)],
        )
    with pytest.raises(ValidationError):
        AssessmentTask(
            type="single_choice_justified",
            prompt="Выберите решение",
            options=["A", "B", "C", "D"],
            correct=[1],
            minutes=3,
        )


def test_open_task_requires_solution_and_plain_choice_is_not_supported():
    with pytest.raises(ValidationError):
        AssessmentTask(type="calculation", prompt="Рассчитайте ток", minutes=5)
    with pytest.raises(ValidationError):
        AssessmentTask(type="single_choice", prompt="Выберите ответ", minutes=1)
