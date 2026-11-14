"""Command Line Interface for MIREA RPD."""

from __future__ import annotations

import asyncio
import getpass
import sys

import click

from .client import RPDClient
from .exceptions import RPDAuthError
from .navigator import RPDNavigator
from .server import create_server
from .session import SessionStore


@click.group()
def main():
    """MIREA RPD CLI — инструменты для работы с рабочими программами дисциплин РТУ МИРЭА."""


@main.command()
@click.option("--username", "-u", default=None, help="Логин преподавателя в МИРЭА (например, konyashkin или email).")
def login(username: str | None):
    """Войти в сервис rpd.mirea.ru и сохранить сессию."""
    if not username:
        username = input("Логин МИРЭА: ").strip()

    password = getpass.getpass("Пароль: ")
    if not username or not password:
        click.echo("Логин и пароль обязательны.", err=True)
        sys.exit(1)

    async def _do_login():
        store = SessionStore()
        client = RPDClient(session_store=store)
        try:
            await client.login(username, password)
            click.echo(f"✓ Успешный вход в rpd.mirea.ru! Сессия сохранена в {store.path}")
        except RPDAuthError as e:
            click.echo(f"✗ Ошибка входа: {e}", err=True)
            sys.exit(1)
        finally:
            await client.close()

    asyncio.run(_do_login())


@main.command()
def status():
    """Проверить статус сохранённой сессии."""
    async def _check():
        store = SessionStore()
        stored = store.load()
        if not stored:
            click.echo("Сессия не найдена. Выполните: mirea-rpd login")
            return

        user = (stored.get("user_info") or {}).get("username", "Неизвестный")
        click.echo(f"Сохранённый пользователь: {user}")
        click.echo("Проверка связи с rpd.mirea.ru...")

        client = RPDClient(session_store=store)
        try:
            is_valid = await client.check_session()
            if is_valid:
                click.echo("✓ Сессия активна и подтверждена сервером.")
            else:
                click.echo("✗ Сессия истекла. Требуется повторный вход: mirea-rpd login")
        finally:
            await client.close()

    asyncio.run(_check())


@main.command()
def logout():
    """Удалить сохранённую сессию."""
    store = SessionStore()
    store.clear()
    click.echo("Сессия удалена.")


@main.command()
@click.argument("query")
@click.option("--year", "-y", default="2026-2027", help="Учебный год (по умолчанию 2026-2027).")
def search(query: str, year: str):
    """Поиск дисциплины в учебных планах."""
    async def _search():
        store = SessionStore()
        client = RPDClient(session_store=store)
        nav = RPDNavigator(client)
        try:
            results = await nav.get_plan_item_rups(acc_year=year, disc_name=query)
            if not results:
                click.echo(f"По запросу '{query}' ничего не найдено.")
                return

            click.echo(f"Найдено совпадений: {len(results)}\n")
            for item in results[:15]:
                name = item.get("discName", "")
                dep = item.get("depName", "")
                rup_id = item.get("rupRowId", "")
                plan = item.get("rupName", "")
                click.echo(f"• {name}")
                if plan:
                    click.echo(f"  План: {plan}")
                if dep:
                    click.echo(f"  Кафедра: {dep}")
                click.echo(f"  rupRowId: {rup_id}\n")
        finally:
            await client.close()

    asyncio.run(_search())


@main.command()
def mcp():
    """Запустить MCP-сервер (протокол stdio)."""
    server = create_server()
    server.run("stdio")


if __name__ == "__main__":
    main()
