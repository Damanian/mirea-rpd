"""Editor module for MIREA RPD."""

from __future__ import annotations

import asyncio
from datetime import date
from typing import Any

from .client import RPDClient


class RPDEditor:
    """Methods for inspecting and managing details of a Work Program (РПД)."""

    def __init__(self, client: RPDClient) -> None:
        self.client = client

    async def _read(self, path: str, rup_row_id: int, rp_id: int | None, method: str = "GET") -> dict[str, Any]:
        data = await self.client.document(rup_row_id, rp_id).call(method, path)
        return {key: value for key, value in data.items() if key not in {"commonState", "employees"}}

    async def get_title(self, rup_row_id: int, rp_id: int) -> dict[str, Any]:
        """Get title page data (discipline name, department, authors, reviewers, competencies)."""
        return await self._read("/Title/GetTitle", rup_row_id, rp_id)

    async def get_content(self, rup_row_id: int, rp_id: int) -> dict[str, Any]:
        """Get discipline content (sections, themes, hours for lectures, labs, practices, and independent work)."""
        return await self._read("/Content/Load", rup_row_id, rp_id)

    async def get_fos(self, rup_row_id: int, rp_id: int) -> dict[str, Any]:
        """Get evaluation tools (ФОС: assessment methods, exam/credit questions, evaluation scales)."""
        return await self._read("/Fos/Load", rup_row_id, rp_id)

    async def get_literature(self, rup_row_id: int, rp_id: int) -> list[dict[str, Any]]:
        """Get literature references (main and supplementary literature, EBS sources)."""
        data = await self._read("/LitIndex/GetCards", rup_row_id, rp_id)
        return data.get("cards", [])

    async def get_rp(self, rup_row_id: int, rp_id: int) -> dict[str, Any]:
        """Get general fields and settings of the Work Program."""
        return await self._read("/Rp/Load", rup_row_id, rp_id)

    async def get_goals(self, rup_row_id: int, rp_id: int | None) -> dict[str, Any]:
        return await self._read("/Rp12/Load", rup_row_id, rp_id)

    async def get_learning_outcomes(self, rup_row_id: int, rp_id: int | None) -> dict[str, Any]:
        return await self._read("/Rp3/Load", rup_row_id, rp_id)

    async def get_summary(self, rup_row_id: int, rp_id: int | None) -> dict[str, Any]:
        return await self._read("/Content/GetSummary", rup_row_id, rp_id, "POST")

    async def get_resources(self, rup_row_id: int, rp_id: int | None, kind: str) -> dict[str, Any]:
        paths = {
            "software": "/ItIndex/GetRefs",
            "information_systems": "/IssIndex/GetRefs",
            "facilities": "/MtoIndex/Load",
        }
        data = await self._read(paths[kind], rup_row_id, rp_id)
        return {key: value for key, value in data.items() if key != "cards"}

    async def search_resources(
        self, rup_row_id: int, rp_id: int | None, kind: str, query: str, limit: int = 20
    ) -> dict[str, Any]:
        document = self.client.document(rup_row_id, rp_id)
        if kind == "literature":
            await document.call("GET", "/LitManager/Initialize")
            data = await document.call("POST", "/LitManager/GetLitCards", {"filter": {"name": query}})
            cards = data.get("cards", [])
            cards.sort(
                key=lambda card: (
                    int(card["year"])
                    if str(card.get("year") or "").isdigit() and 1500 <= int(card["year"]) <= date.today().year + 1
                    else 0
                ),
                reverse=True,
            )
        else:
            paths = {
                "software": "/ItIndex/GetRefs",
                "information_systems": "/IssIndex/GetRefs",
                "facilities": "/MtoIndex/Load",
            }
            data = await document.call("GET", paths[kind])
            cards = [
                card
                for card in data.get("cards", [])
                if query.casefold()
                in " ".join(
                    str(card.get(key, "")) for key in ("name", "description", "appointment", "aud", "equipment")
                ).casefold()
            ]
        return {"kind": kind, "total": len(cards), "cards": cards[:limit]}

    async def get_full_program(self, rup_row_id: int, rp_id: int | None) -> dict[str, Any]:
        """Fetch title, content, evaluation tools, literature, and general info in parallel."""
        calls = {
            "title": self.get_title(rup_row_id, rp_id),
            "content": self.get_content(rup_row_id, rp_id),
            "fos": self.get_fos(rup_row_id, rp_id),
            "literature": self.get_literature(rup_row_id, rp_id),
            "rp": self.get_rp(rup_row_id, rp_id),
            "goals": self.get_goals(rup_row_id, rp_id),
            "learning_outcomes": self.get_learning_outcomes(rup_row_id, rp_id),
            "summary": self.get_summary(rup_row_id, rp_id),
            **{
                kind: self.get_resources(rup_row_id, rp_id, kind)
                for kind in ("software", "information_systems", "facilities")
            },
        }
        values = await asyncio.gather(*calls.values(), return_exceptions=True)
        blocks = dict(zip(calls, values, strict=True))
        errors = {
            key: str(value) if isinstance(value, Exception) else str(value["error"])
            for key, value in blocks.items()
            if isinstance(value, Exception) or (isinstance(value, dict) and value.get("error"))
        }

        return {
            "rup_row_id": rup_row_id,
            "rp_id": rp_id,
            "complete": not errors,
            "errors": errors,
            **{
                key: value if not isinstance(value, Exception) else {"error": str(value)}
                for key, value in blocks.items()
            },
        }
