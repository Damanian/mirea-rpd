"""MCP Server for MIREA RPD (rpd.mirea.ru)."""

from __future__ import annotations

import functools
import logging
from pathlib import Path
from typing import Any, Literal

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

from .client import RPDClient
from .editor import RPDEditor
from .exceptions import RPDAuthError, RPDError
from .local_login import LocalLoginServer
from .native import curriculum_budgets
from .navigator import RPDNavigator
from .schemas import TEXT_FIELDS, HourBudget, RPDTemplate, SemesterHourBudget
from .session import SessionStore
from .templates import TemplateManager
from .validation import validate_template

READ = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=True)
WRITE = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=True)
ACTION = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=False)

INSTRUCTIONS = (
    "Коннектор к сервису рабочих программ дисциплин (РПД) РТУ МИРЭА (rpd.mirea.ru). "
    "Позволяет искать дисциплины, учебные планы (РУП), кафедры и направления подготовки; "
    "читать структуру утвержденных и разрабатываемых РПД: разделы, темы занятий, распределение часов, "
    "фонды оценочных средств (ФОС), списки литературы и компетенции; "
    "а также заполнять РПД по шаблонам на сайте. "
    "Для автозаполнения сначала вызови rpd_get_autofill_context, найди реальные ID ресурсов "
    "через rpd_search_resources, подготовь RPDTemplate по учебному плану и референсам и вызови "
    "rpd_autofill_program. Без rp_id создаётся новая программа. Для каждой категории индикатора "
    "заполни результат обучения, current_tasks (от 5) и intermediate_tasks (от 10) с типом, ключом и временем. "
    "Для знать используй matching/sequencing, для уметь/владеть — open_response/calculation или выбор с обоснованием. "
    "при файловом ФОС коннектор автоматически создаёт и прикрепляет DOCX. "
    "Проверяй success, saved_to_db, verified и errors. dry_run проверяет только локальную структуру. "
    "Не выдумывай протоколы утверждения, составителей и записи справочников. "
    "До заполнения спроси пользователя дату утверждения и номер протокола и передай title.protocol_date и title.protocol_number. "
    "Не копируй эти сведения из референса. Только если пользователь явно просит пока не заполнять их, передай defer_approval=true. "
    "Одно занятие: лекция не более 2 ч., лабораторная 4 ч., практика 2 ч., СРС 4 ч. Разбивай крупные темы содержательно, "
    "расписывай конкретные вопросы и ожидаемый результат каждого занятия. "
    "Если пользователь просит войти ('войти'), вызови инструмент rpd_login — он откроет страницу "
    "входа в браузере на локальном компьютере, где пользователь безопасно введет учетные данные."
)

logger = logging.getLogger(__name__)


def create_server(
    session_store: SessionStore | None = None,
    client: RPDClient | None = None,
) -> tuple[MCPServer, LocalLoginServer]:
    """Build and configure the MIREA RPD MCP Server."""
    store = session_store or SessionStore()
    rpd_client = client or RPDClient(session_store=store)
    nav = RPDNavigator(rpd_client)
    editor = RPDEditor(rpd_client)
    tpl_mgr = TemplateManager(rpd_client)
    login_srv = LocalLoginServer(store=store)

    server = MCPServer(
        name="mirea-rpd",
        title="MIREA RPD",
        instructions=INSTRUCTIONS,
    )

    def wrap_errors(fn):
        @functools.wraps(fn)
        async def wrapper(*args, **kwargs):
            try:
                kwargs.pop("args", None)
                kwargs.pop("kwargs", None)
                return await fn(*args, **kwargs)
            except RPDAuthError as e:
                raise ToolError(f"{e} Войдите через `rpd_login`.") from e
            except RPDError as e:
                raise ToolError(f"Ошибка РПД: {e}") from e
            except Exception as e:
                raise ToolError(f"Непредвиденная ошибка: {e}") from e

        return wrapper

    @server.tool(annotations=ACTION)
    @wrap_errors
    async def rpd_login(open_browser: bool = True) -> dict[str, Any]:
        """Открыть локальную страницу входа в браузере (127.0.0.1).
        Пароль вводится пользователем в браузере, а не в чате. После входа сессия сохранится."""
        url = await login_srv.start(open_browser=open_browser)
        return {
            "url": url,
            "browser_opened": open_browser,
            "message": "Страница входа открыта в браузере. Введите логин и пароль МИРЭА, а затем скажите ИИ: 'готово'.",
        }

    @server.tool(annotations=READ)
    @wrap_errors
    async def rpd_status() -> dict[str, Any]:
        """Проверить состояние подключения и валидность сессии к rpd.mirea.ru."""
        stored = store.load()
        if not stored:
            return {"authenticated": False, "message": "Сессия не найдена. Требуется вход через `rpd_login`."}

        is_valid = await rpd_client.check_session()
        username = (stored.get("user_info") or {}).get("username", "unknown")
        return {
            "authenticated": is_valid,
            "username": username,
            "message": "Сессия активна" if is_valid else "Сессия истекла. Требуется вход через `rpd_login`.",
        }

    @server.tool(annotations=READ)
    @wrap_errors
    async def rpd_departments(acc_year: str = "2026-2027") -> list[dict[str, Any]]:
        """Получить список кафедр РТУ МИРЭА, актуальных для указанного учебного года."""
        return await nav.get_departments(acc_year)

    @server.tool(annotations=READ)
    @wrap_errors
    async def rpd_plan_entities(
        acc_year: str = "2026-2027",
        dep_id: int | None = None,
        hide_without_students: bool = False,
    ) -> list[dict[str, Any]]:
        """Получить названия дисциплин кафедры и их objectType для поиска в учебных планах."""
        return await nav.get_plan_entities(
            acc_year=acc_year,
            dep_id=dep_id,
            hide_without_students=hide_without_students,
        )

    @server.tool(annotations=READ)
    @wrap_errors
    async def rpd_search_disciplines(
        query: str,
        acc_year: str = "2026-2027",
        dep_id: int | None = None,
    ) -> list[dict[str, Any]]:
        """Найти дисциплины в учебных планах по названию или коду кафедры."""
        return await nav.get_plan_item_rups(
            acc_year=acc_year,
            dep_id=dep_id,
            disc_name=query,
        )

    @server.tool(annotations=READ)
    @wrap_errors
    async def rpd_get_work_programs(
        acc_year: str = "2026-2027",
        disc_name: str | None = None,
        rup_row_id: int | None = None,
        rup_name: str | None = None,
    ) -> list[dict[str, Any]]:
        """Получить список рабочих программ (РПД) для дисциплины или строки плана."""
        return await nav.get_rps(
            acc_year=acc_year,
            disc_name=disc_name,
            rup_row_id=rup_row_id,
            rup_name=rup_name,
        )

    @server.tool(annotations=READ)
    @wrap_errors
    async def rpd_get_title(rup_row_id: int, rp_id: int) -> dict[str, Any]:
        """Получить титульный лист РПД: составители, кафедра, направление, компетенции, часы."""
        return await editor.get_title(rup_row_id=rup_row_id, rp_id=rp_id)

    @server.tool(annotations=READ)
    @wrap_errors
    async def rpd_get_content(rup_row_id: int, rp_id: int) -> dict[str, Any]:
        """Получить содержание дисциплины: разделы, темы лекций, практик, лабораторных работ и СРС."""
        return await editor.get_content(rup_row_id=rup_row_id, rp_id=rp_id)

    @server.tool(annotations=READ)
    @wrap_errors
    async def rpd_get_fos(rup_row_id: int, rp_id: int) -> dict[str, Any]:
        """Получить фонд оценочных средств (ФОС): формы контроля, критерии, типовые задания, вопросы к зачёту/экзамену."""
        return await editor.get_fos(rup_row_id=rup_row_id, rp_id=rp_id)

    @server.tool(annotations=READ)
    @wrap_errors
    async def rpd_get_literature(rup_row_id: int, rp_id: int) -> list[dict[str, Any]]:
        """Получить список рекомендуемой литературы (основная, дополнительная, электронные библиотечные ресурсы)."""
        return await editor.get_literature(rup_row_id=rup_row_id, rp_id=rp_id)

    @server.tool(annotations=READ)
    @wrap_errors
    async def rpd_get_full_program(rup_row_id: int, rp_id: int) -> dict[str, Any]:
        """Получить полный паспорт рабочей программы: титул, содержание, ФОС, литературу."""
        return await editor.get_full_program(rup_row_id=rup_row_id, rp_id=rp_id)

    @server.tool(annotations=WRITE)
    @wrap_errors
    async def rpd_apply_template(
        rup_row_id: int,
        rp_id: int,
        template: RPDTemplate,
        dry_run: bool = False,
        content_mode: Literal["merge", "replace"] = "merge",
    ) -> dict[str, Any]:
        """Применить частичные изменения по типизированному шаблону и проверить сохранение.
        Для полного заполнения с проверкой учебного плана используйте rpd_autofill_program.
        merge добавляет/обновляет занятия, replace также удаляет прежние обычные разделы вне шаблона."""
        return await tpl_mgr.apply_template(
            rup_row_id=rup_row_id, rp_id=rp_id, template=template, dry_run=dry_run, content_mode=content_mode
        )

    @server.tool(annotations=WRITE)
    @wrap_errors
    async def rpd_autofill_program(
        rup_row_id: int,
        template: RPDTemplate,
        rp_id: int | None = None,
        dry_run: bool = False,
        content_mode: Literal["merge", "replace"] = "merge",
    ) -> dict[str, Any]:
        """Полностью заполнить РПД и проверить соответствие учебному плану.
        Если rp_id не указан, создаётся отдельная новая РПД для этой строки плана.
        ИИ должен подготовить шаблон по rpd_get_autofill_context и реальным справочникам.
        Сохраняет только после проверки часов, содержания и запрошенных полей; повторно читает результат."""
        return await tpl_mgr.apply_template(
            rup_row_id, rp_id, template, dry_run=dry_run, content_mode=content_mode, require_complete=True
        )

    @server.tool(annotations=READ)
    @wrap_errors
    async def rpd_search_resources(
        rup_row_id: int,
        kind: Literal["literature", "software", "information_systems", "facilities"],
        query: str,
        rp_id: int | None = None,
        limit: int = 20,
    ) -> dict[str, Any]:
        """Найти реальные ID изданий, ПО, справочных систем или аудиторий для шаблона.
        Используйте найденные ID; не придумывайте записи справочников."""
        if not 1 <= limit <= 100:
            raise ToolError("limit должен быть от 1 до 100.")
        return await editor.search_resources(rup_row_id, rp_id, kind, query, limit)

    @server.tool(annotations=READ)
    @wrap_errors
    async def rpd_validate_template(
        template: RPDTemplate,
        hour_budget: HourBudget | None = None,
        semester_budgets: list[SemesterHourBudget] | None = None,
        allowed_indicators: list[str] | None = None,
    ) -> dict[str, Any]:
        """Проверить шаблон без записи: структуру, номера тем, виды занятий и суммы часов.
        hour_budget — проверенные часы из учебного плана, а не произвольные значения ИИ."""
        return validate_template(template, hour_budget, semester_budgets, allowed_indicators)

    @server.tool(annotations=READ)
    @wrap_errors
    async def rpd_get_autofill_context(rup_row_id: int, rp_id: int | None = None) -> dict[str, Any]:
        """Получить исходную РПД и JSON Schema для подготовки ИИ структурированного шаблона.
        Сначала прочитайте программу и требования учебного плана; затем составьте и проверьте шаблон."""
        program = await editor.get_full_program(rup_row_id=rup_row_id, rp_id=rp_id)
        document = rpd_client.document(rup_row_id, rp_id)
        state = await document.initialize()
        rp3 = await document.call("GET", "/Rp3/Initialize") if state.get("settings", {}).get("useRP3") else {}
        return {
            "program": program,
            "complete": program["complete"],
            "settings": state.get("settings", {}),
            "permissions": state.get("permissions", {}),
            "learning_outcome_settings": rp3.get("settings", {}),
            "required_user_inputs": [
                "Дата утверждения и номер протокола; если пользователь просит отложить, используйте defer_approval=true"
            ],
            "session_hour_limits": {"lectures": 2, "labs": 4, "practices": 2, "self_study": 4},
            "fos_rules": {
                "current_minimum_per_indicator_category": 5,
                "intermediate_minimum_per_indicator_category": 10,
                "know_types": ["matching", "sequencing"],
                "can_and_master_types": [
                    "open_response",
                    "calculation",
                    "single_choice_justified",
                    "multiple_choice_justified",
                ],
                "table_columns": [
                    "Номер задания",
                    "Правильный ответ",
                    "Содержание вопроса",
                    "Компетенция",
                    "Время выполнения задания, мин.",
                    "Наименование дисциплины (практики), формирующей данную компетенцию",
                ],
            },
            "template_schema": RPDTemplate.model_json_schema(),
            "writable_text_fields": [
                field
                for field, (_, native) in TEXT_FIELDS.items()
                if native in program["rp"]
                and (field != "comments_text" or state.get("permissions", {}).get("canWriteComments"))
            ],
            "semester_budgets": [
                item.model_dump()
                for item in curriculum_budgets(program["summary"], program["content"].get("terms", []))
            ]
            if "error" not in program["summary"] and "error" not in program["content"]
            else [],
            "workflow": [
                "Используйте учебный план для определения часов, семестров, компетенций и форм контроля.",
                "Подготовьте шаблон по схеме, используя референсы только как образец содержания.",
                "Проверьте шаблон через rpd_validate_template, передав часы из учебного плана.",
                "Спросите дату утверждения и номер протокола; явный ответ пока не заполнять задаёт defer_approval=true.",
                "Заполните current_tasks (от 5) и intermediate_tasks (от 10) для каждой категории каждого индикатора: тип, условие, ключ ответа, время.",
                "Примените шаблон через rpd_autofill_program и проверьте success, saved_to_db и verified.",
            ],
        }

    @server.tool(annotations=READ)
    @wrap_errors
    async def rpd_export_template(
        rup_row_id: int,
        rp_id: int,
        save_file: str | None = None,
        include_fos_files: bool = True,
    ) -> dict[str, Any]:
        """Экспортировать готовую РПД в структурированный шаблон для тиражирования на другие дисциплины."""
        return await tpl_mgr.export_template(
            rup_row_id=rup_row_id, rp_id=rp_id, save_file=save_file, include_fos_files=include_fos_files
        )

    @server.tool(annotations=WRITE)
    @wrap_errors
    async def rpd_download_draft(
        rup_row_id: int,
        rp_id: int,
        save_path: str | None = None,
    ) -> dict[str, Any]:
        """Скачать резервную копию черновика в собственном XML-формате .rpdx на диск."""
        content = await nav.download_draft(rup_row_id=rup_row_id, rp_id=rp_id)
        path = Path(save_path) if save_path else Path.cwd() / f"RPD_{rp_id}_{rup_row_id}.rpdx"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return {
            "saved": True,
            "path": str(path.resolve()),
            "size_bytes": len(content),
        }

    return server, login_srv
