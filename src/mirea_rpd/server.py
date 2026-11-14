"""MCP Server for MIREA RPD (rpd.mirea.ru)."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

from .client import RPDClient
from .editor import RPDEditor
from .exceptions import RPDAuthError, RPDError
from .navigator import RPDNavigator
from .session import SessionStore

READ = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=True)
WRITE = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=True)

INSTRUCTIONS = (
    "Коннектор к сервису рабочих программ дисциплин (РПД) РТУ МИРЭА (rpd.mirea.ru). "
    "Позволяет искать дисциплины, учебные планы (РУП), кафедры и направления подготовки; "
    "читать структуру утвержденных и разрабатываемых РПД: разделы, темы занятий, распределение часов, "
    "фонды оценочных средств (ФОС), списки литературы и компетенции."
)

logger = logging.getLogger(__name__)


def create_server(
    session_store: SessionStore | None = None,
    client: RPDClient | None = None,
) -> MCPServer:
    """Build and configure the MIREA RPD MCP Server."""
    store = session_store or SessionStore()
    rpd_client = client or RPDClient(session_store=store)
    nav = RPDNavigator(rpd_client)
    editor = RPDEditor(rpd_client)

    server = MCPServer(
        name="mirea-rpd",
        title="MIREA RPD",
        instructions=INSTRUCTIONS,
    )

    def wrap_errors(fn):
        async def wrapper(*args, **kwargs):
            try:
                return await fn(*args, **kwargs)
            except RPDAuthError as e:
                raise ToolError(f"{e} Войдите через `mirea-rpd login`.") from e
            except RPDError as e:
                raise ToolError(f"Ошибка РПД: {e}") from e
            except Exception as e:
                raise ToolError(f"Непредвиденная ошибка: {e}") from e

        wrapper.__name__ = fn.__name__
        wrapper.__doc__ = fn.__doc__
        return wrapper

    @server.tool(annotations=READ)
    @wrap_errors
    async def rpd_status() -> dict[str, Any]:
        """Проверить состояние подключения и валидность сессии к rpd.mirea.ru."""
        stored = store.load()
        if not stored:
            return {"authenticated": False, "message": "Сессия не найдена. Требуется вход через `mirea-rpd login`."}

        is_valid = await rpd_client.check_session()
        username = (stored.get("user_info") or {}).get("username", "unknown")
        return {
            "authenticated": is_valid,
            "username": username,
            "message": "Сессия активна" if is_valid else "Сессия истекла. Требуется вход через `mirea-rpd login`.",
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
        """Получить список направлений подготовки и профилей (сущностей планов)."""
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
    ) -> list[dict[str, Any]]:
        """Получить список рабочих программ (РПД) для дисциплины или строки плана."""
        return await nav.get_rps(
            acc_year=acc_year,
            disc_name=disc_name,
            rup_row_id=rup_row_id,
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
    async def rpd_download_draft(
        rup_row_id: int,
        rp_id: int,
        save_path: str | None = None,
    ) -> dict[str, Any]:
        """Скачать файл черновика РПД на диск."""
        content = await nav.download_draft(rup_row_id=rup_row_id, rp_id=rp_id)
        path = Path(save_path) if save_path else Path.cwd() / f"RPD_{rp_id}_{rup_row_id}.docx"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return {
            "saved": True,
            "path": str(path.resolve()),
            "size_bytes": len(content),
        }

    return server
