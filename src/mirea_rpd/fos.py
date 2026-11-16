"""MIREA six-column FOS using the retained template and the June 2026 rules."""

from __future__ import annotations

import base64
import math
from copy import deepcopy
from importlib.resources import files
from io import BytesIO
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

from docx import Document
from lxml import etree

from .schemas import AssessmentTask, LearningOutcomeTemplate

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
INSTRUCTIONS = {
    "matching": "Прочитайте текст и установите соответствие.",
    "sequencing": "Прочитайте текст и установите последовательность.",
    "single_choice_justified": "Прочитайте текст, выберите правильный ответ и запишите аргументы, обосновывающие выбор ответа.",
    "multiple_choice_justified": "Прочитайте текст, выберите правильные ответы и запишите аргументы, обосновывающие выбор ответов.",
    "open_response": "Прочитайте текст и запишите развёрнутый обоснованный ответ.",
    "calculation": "Напишите ответ и ход решения задачи.",
}
HEADERS = (
    "Номер задания",
    "Правильный ответ",
    "Содержание вопроса",
    "Компетенция",
    "Время выполнения задания, мин.",
    "Наименование дисциплины (практики), формирующей данную компетенцию",
)


def _paragraph(prototype, text: str, *, bold: bool | None = None):
    p = deepcopy(prototype)
    for key in list(p.attrib):
        if key.endswith(("paraId", "textId")):
            del p.attrib[key]
    run = prototype.find(f"{W}r")
    r = deepcopy(run) if run is not None else etree.Element(f"{W}r")
    for node in list(p):
        if node.tag != f"{W}pPr":
            p.remove(node)
    for node in list(r):
        if node.tag != f"{W}rPr":
            r.remove(node)
    if bold is not None:
        props = r.find(f"{W}rPr")
        if props is None:
            props = etree.SubElement(r, f"{W}rPr")
        for tag in ("b", "bCs"):
            element = props.find(f"{W}{tag}")
            if element is None:
                element = etree.SubElement(props, f"{W}{tag}")
            element.set(f"{W}val", "1" if bold else "0")
    t = etree.SubElement(r, f"{W}t")
    t.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
    t.text = text
    p.append(r)
    return p


def _set_cell(cell, lines: list[str], *, question: bool = False):
    prototype = cell.find(f"{W}p")
    for node in list(cell):
        if node.tag != f"{W}tcPr":
            cell.remove(node)
    for index, line in enumerate(lines or [""]):
        cell.append(_paragraph(prototype, line, bold=question and index == 0))


def _task_text(task: AssessmentTask) -> tuple[str, str]:
    lines = [INSTRUCTIONS[task.type], task.prompt]
    if task.type == "matching":
        lines.extend(
            [
                "Элементы:",
                *(f"{i}. {text}" for i, text in enumerate(task.left_items, 1)),
                "Характеристики:",
                *(f"{i}. {text}" for i, text in enumerate(task.right_items, 1)),
            ]
        )
        answer = "; ".join(f"{i} – {value}" for i, value in enumerate(task.correct, 1))
    elif task.type == "sequencing":
        lines.extend(f"{i}. {text}" for i, text in enumerate(task.sequence_items, 1))
        answer = " → ".join(map(str, task.correct))
    elif task.type.endswith("_justified"):
        lines.extend(f"{i}. {text}" for i, text in enumerate(task.options, 1))
        answer = ", ".join(map(str, task.correct)) + ". " + task.explanation
    else:
        answer = task.answer + (" " + task.explanation if task.explanation else "")
    return "\n".join(lines), answer


def _package(parts: dict[str, bytes]) -> bytes:
    stable = BytesIO()
    with ZipFile(stable, "w", ZIP_DEFLATED) as target:
        for name, data in sorted(parts.items()):
            info = ZipInfo(name, (2000, 1, 1, 0, 0, 0))
            info.compress_type = ZIP_DEFLATED
            target.writestr(info, data)
    return stable.getvalue()


def build_fos(outcome: LearningOutcomeTemplate, fos_type: int, discipline: str) -> tuple[str, bytes] | None:
    prefix = "current" if fos_type == 1 else "intermediate"
    attachment = getattr(outcome, f"{prefix}_fos")
    if attachment:
        return attachment.filename, base64.b64decode(attachment.content_base64, validate=True)
    tasks = getattr(outcome, f"{prefix}_tasks")
    if not tasks:
        return None
    with ZipFile(BytesIO(files("mirea_rpd").joinpath("assets/fos-template.docx").read_bytes())) as source:
        parts = {name: source.read(name) for name in source.namelist()}
    document = etree.fromstring(parts["word/document.xml"], etree.XMLParser(resolve_entities=False, no_network=True))
    body = document.find(f"{W}body")
    paragraphs = body.findall(f"{W}p")
    competence = outcome.indicator.rsplit(".", 1)[0]
    heading = competence + (" - " + outcome.competency_description if outcome.competency_description else "")
    category = {1: "Знать", 2: "Уметь", 3: "Владеть"}[outcome.category]
    rubric = [value for value in (outcome.rating_scale, outcome.mark_scale) if value]
    rubric += [
        f"Оценка {mark}: {getattr(outcome, f'mark{mark}')}" for mark in (5, 4, 3, 2) if getattr(outcome, f"mark{mark}")
    ]
    for p, text in zip(paragraphs, (heading, f"{category} - {outcome.contents}", " ".join(rubric)), strict=False):
        body.replace(p, _paragraph(p, text))
    table = body.find(f"{W}tbl")
    prototype = table.findall(f"{W}tr")[1]
    table.remove(prototype)
    for number, task in enumerate(tasks, 1):
        row = deepcopy(prototype)
        question, answer = _task_text(task)
        values = (str(number), answer, question, competence, f"{task.minutes:g}", discipline)
        for index, (cell, text) in enumerate(zip(row.findall(f"{W}tc"), values, strict=True)):
            _set_cell(cell, text.splitlines(), question=index == 2)
            if number == len(tasks):
                props = cell.find(f"{W}tcPr")
                borders = props.find(f"{W}tcBorders")
                if borders is None:
                    borders = etree.SubElement(props, f"{W}tcBorders")
                bottom = borders.find(f"{W}bottom")
                if bottom is None:
                    bottom = etree.SubElement(borders, f"{W}bottom")
                for key, value in {"val": "single", "sz": "4", "space": "0", "color": "auto"}.items():
                    bottom.set(f"{W}{key}", value)
        table.append(row)
    parts["word/document.xml"] = etree.tostring(document, xml_declaration=True, encoding="UTF-8", standalone=True)
    code = {1: "З", 2: "У", 3: "В"}[outcome.category]
    return (
        f"{outcome.indicator}-{code}{outcome.level} {'текущий' if fos_type == 1 else 'промежуточный'}.docx",
        _package(parts),
    )


def check_fos(
    content: bytes, category: int, fos_type: int, *, competence: str | None = None, discipline: str | None = None
) -> list[str]:
    """Validate supplied files as well as generated ones before uploading."""
    doc = Document(BytesIO(content))

    def normalize(text):
        return " ".join(text.split()).strip()

    table = next(
        (
            table
            for table in doc.tables
            if len(table.columns) == 6 and tuple(normalize(c.text) for c in table.rows[0].cells) == HEADERS
        ),
        None,
    )
    if table is None:
        return ["ФОС должен содержать таблицу из шести колонок по приложенному шаблону."]
    rows = table.rows[1:]
    minimum = 5 if fos_type == 1 else 10
    errors = []
    if len(rows) < minimum:
        errors.append(f"Нужно не менее {minimum} заданий; в файле {len(rows)}.")
    questions = []
    for index, row in enumerate(rows, 1):
        cells = [c.text.strip() for c in row.cells]
        if cells[0] != str(index) or any(not value for value in cells):
            errors.append(f"В задании {index} нужны порядковый номер и все шесть заполненных колонок.")
        try:
            minutes = float(cells[4].replace(",", "."))
            if not math.isfinite(minutes) or minutes <= 0:
                raise ValueError
        except ValueError:
            errors.append(f"В задании {index} некорректное время выполнения.")
        question = cells[2].casefold()
        questions.append(question)
        instruction = question.splitlines()[0] if question else ""
        closed = "установит" in instruction and ("соответстви" in instruction or "последовательност" in instruction)
        if category == 1 and not closed:
            errors.append(f"Задание {index}: для «знать» используйте соответствие или последовательность.")
        if category != 1 and (
            closed or ("выбер" in question and "обосн" not in question and "аргумент" not in question)
        ):
            errors.append(f"Задание {index}: нужен открытый ответ/решение или выбор с обоснованием.")
        if competence and cells[3] != competence:
            errors.append(f"Задание {index}: код компетенции должен быть {competence}.")
        if discipline and normalize(cells[5]) != normalize(discipline):
            errors.append(f"Задание {index}: указана другая дисциплина.")
    if len(set(questions)) != len(questions):
        errors.append("В файле повторяются задания.")
    return errors
