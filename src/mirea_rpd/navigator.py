"""Navigator module for MIREA RPD."""

from __future__ import annotations

from typing import Any

from .client import RPDClient


class RPDNavigator:
    """Methods for navigating academic years, departments, curricula (RUP), and disciplines."""

    def __init__(self, client: RPDClient) -> None:
        self.client = client

    async def get_departments(self, acc_year: str = "2026-2027") -> list[dict[str, Any]]:
        """Get list of active university departments for given academic year."""
        params = {"accYear": acc_year}
        return await self.client.get_json("/RpdManager/GetDepartments", params=params)

    async def get_plan_entities(
        self,
        acc_year: str = "2026-2027",
        dep_id: int | None = None,
        hide_without_students: bool = False,
        show_only_with_rp: bool = False,
    ) -> list[dict[str, Any]]:
        """Get list of degree programs / plan entities."""
        params: dict[str, Any] = {
            "accYear": acc_year,
            "hideWithoutStudents": str(hide_without_students).lower(),
            "showOnlyWithRp": str(show_only_with_rp).lower(),
        }
        if dep_id is not None:
            params["depId"] = dep_id
        return await self.client.get_json("/RpdManager/GetPlanEntities", params=params)

    async def get_plan_item_rups(
        self,
        acc_year: str = "2026-2027",
        dep_id: int | None = None,
        disc_name: str | None = None,
        obj_type: int = 0,
        hide_without_students: bool = False,
        show_only_with_rp: bool = False,
    ) -> list[dict[str, Any]]:
        """Get list of discipline rows in curricula with RP links."""
        params: dict[str, Any] = {
            "accYear": acc_year,
            "objType": obj_type,
            "hideWithoutStudents": str(hide_without_students).lower(),
            "showOnlyWithRp": str(show_only_with_rp).lower(),
        }
        if dep_id is not None:
            params["depId"] = dep_id
        if disc_name:
            params["discName"] = disc_name
        return await self.client.get_json("/RpdManager/GetPlanItemRups", params=params)

    async def get_rups(self, acc_year: str = "2026-2027") -> list[dict[str, Any]]:
        """Get list of study plans (РУП)."""
        params = {"accYear": acc_year}
        return await self.client.get_json("/RpdManager/GetRups", params=params)

    async def get_plan_items(self, rup_id: int) -> list[dict[str, Any]]:
        """Get list of discipline items for a specific study plan."""
        params = {"rupId": rup_id}
        return await self.client.get_json("/RpdManager/GetPlanItems", params=params)

    async def get_rps(
        self,
        acc_year: str = "2026-2027",
        disc_name: str | None = None,
        rup_row_id: int | None = None,
        rup_name: str | None = None,
        show_hidden: bool = False,
    ) -> list[dict[str, Any]]:
        """Get work programs (РПД)."""
        params: dict[str, Any] = {
            "accYear": acc_year,
            "showHidden": str(show_hidden).lower(),
        }
        if disc_name:
            params["discName"] = disc_name
        if rup_row_id is not None:
            params["rupRowId"] = rup_row_id
        if rup_name:
            params["rupName"] = rup_name
        return await self.client.get_json("/RpdManager/GetRps", params=params)

    async def download_draft(self, rup_row_id: int, rp_id: int) -> bytes:
        """Download work program draft file."""
        params = {"rupRowId": rup_row_id, "rpdId": rp_id}
        resp = await self.client.request("GET", "/RpdManager/DownloadDraft", params=params)
        return resp.content
