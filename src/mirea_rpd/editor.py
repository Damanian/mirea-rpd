"""Editor module for MIREA RPD."""

from __future__ import annotations

import asyncio
from typing import Any

from .client import RPDClient


class RPDEditor:
    """Methods for inspecting and managing details of a Work Program (РПД)."""

    def __init__(self, client: RPDClient) -> None:
        self.client = client

    async def get_title(self, rup_row_id: int, rp_id: int) -> dict[str, Any]:
        """Get title page data (discipline name, department, authors, reviewers, competencies)."""
        params = {"rupRowId": rup_row_id, "rpId": rp_id}
        return await self.client.get_json("/Title/GetTitle", params=params)

    async def get_content(self, rup_row_id: int, rp_id: int) -> dict[str, Any]:
        """Get discipline content (sections, themes, hours for lectures, labs, practices, and independent work)."""
        params = {"rupRowId": rup_row_id, "rpId": rp_id}
        return await self.client.get_json("/Content/Load", params=params)

    async def get_fos(self, rup_row_id: int, rp_id: int) -> dict[str, Any]:
        """Get evaluation tools (ФОС: assessment methods, exam/credit questions, evaluation scales)."""
        params = {"rupRowId": rup_row_id, "rpId": rp_id}
        return await self.client.get_json("/Fos/Load", params=params)

    async def get_literature(self, rup_row_id: int, rp_id: int) -> list[dict[str, Any]]:
        """Get literature references (main and supplementary literature, EBS sources)."""
        params = {"rupRowId": rup_row_id, "rpId": rp_id}
        return await self.client.get_json("/LitIndex/GetCards", params=params)

    async def get_rp(self, rup_row_id: int, rp_id: int) -> dict[str, Any]:
        """Get general fields and settings of the Work Program."""
        params = {"rupRowId": rup_row_id, "rpId": rp_id}
        return await self.client.get_json("/Rp/Load", params=params)

    async def get_full_program(self, rup_row_id: int, rp_id: int) -> dict[str, Any]:
        """Fetch title, content, evaluation tools, literature, and general info in parallel."""
        title_task = self.get_title(rup_row_id, rp_id)
        content_task = self.get_content(rup_row_id, rp_id)
        fos_task = self.get_fos(rup_row_id, rp_id)
        lit_task = self.get_literature(rup_row_id, rp_id)
        rp_task = self.get_rp(rup_row_id, rp_id)

        title, content, fos, literature, rp = await asyncio.gather(
            title_task, content_task, fos_task, lit_task, rp_task, return_exceptions=True
        )

        return {
            "rup_row_id": rup_row_id,
            "rp_id": rp_id,
            "title": title if not isinstance(title, Exception) else {"error": str(title)},
            "content": content if not isinstance(content, Exception) else {"error": str(content)},
            "fos": fos if not isinstance(fos, Exception) else {"error": str(fos)},
            "literature": literature if not isinstance(literature, Exception) else {"error": str(literature)},
            "rp": rp if not isinstance(rp, Exception) else {"error": str(rp)},
        }
