"""Navigator module for MIREA RPD."""

from __future__ import annotations

from typing import Any

from .client import RPDClient
from .exceptions import RPDError


class RPDNavigator:
    """Methods for navigating academic years, departments, curricula (RUP), and disciplines."""

    def __init__(self, client: RPDClient) -> None:
        self.client = client
        self._rows: dict[int, dict[str, Any]] = {}

    @staticmethod
    def _items(data: Any, key: str) -> list[dict[str, Any]]:
        if isinstance(data, list):
            return data
        values = data.get(key, {}).get("items") if isinstance(data, dict) else None
        if not isinstance(values, list):
            raise RPDError(f"Некорректный список {key} в ответе менеджера РПД.")
        return values

    async def get_departments(self, acc_year: str = "2026-2027") -> list[dict[str, Any]]:
        """Get list of active university departments for given academic year."""
        params = {"accYear": acc_year}
        return self._items(await self.client.get_json("/RpdManager/GetDepartments", params=params), "department")

    async def get_plan_entities(
        self,
        acc_year: str = "2026-2027",
        dep_id: int | None = None,
        hide_without_students: bool = False,
        show_only_with_rp: bool = False,
    ) -> list[dict[str, Any]]:
        """Get discipline entities, including the native objectType required for curriculum lookup."""
        params: dict[str, Any] = {
            "accYear": acc_year,
            "hideWithoutStudents": str(hide_without_students).lower(),
            "showOnlyWithRp": str(show_only_with_rp).lower(),
        }
        if dep_id is not None:
            params["depId"] = dep_id
        return self._items(await self.client.get_json("/RpdManager/GetPlanEntities", params=params), "planItems")

    async def get_plan_item_rups(
        self,
        acc_year: str = "2026-2027",
        dep_id: int | None = None,
        disc_name: str | None = None,
        obj_type: int | None = None,
        hide_without_students: bool = False,
        show_only_with_rp: bool = False,
    ) -> list[dict[str, Any]]:
        """Get list of discipline rows in curricula with RP links."""
        if obj_type is None:
            entities = await self.get_plan_entities(acc_year, dep_id, hide_without_students, show_only_with_rp)
            matches = [item for item in entities if not disc_name or disc_name.casefold() in item["name"].casefold()]
            rows = []
            for entity in matches:
                rows.extend(
                    await self.get_plan_item_rups(
                        acc_year,
                        entity.get("depId", dep_id),
                        entity["name"],
                        entity["objectType"],
                        hide_without_students,
                        show_only_with_rp,
                    )
                )
            return rows
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
        rows = self._items(await self.client.get_json("/RpdManager/GetPlanItemRups", params=params), "rup")
        for row in rows:
            row.update({"discName": disc_name, "depId": dep_id, "objectType": obj_type})
            self._rows[row["rupRowId"]] = row
        return rows

    async def get_rups(self, acc_year: str = "2026-2027") -> list[dict[str, Any]]:
        """Get list of study plans (РУП)."""
        params = {"accYear": acc_year}
        return self._items(await self.client.get_json("/RpdManager/GetRups", params=params), "rup")

    async def get_plan_items(self, rup_id: int) -> list[dict[str, Any]]:
        """Get list of discipline items for a specific study plan."""
        params = {"rupId": rup_id}
        return self._items(await self.client.get_json("/RpdManager/GetPlanItems", params=params), "planItem")

    async def get_rps(
        self,
        acc_year: str = "2026-2027",
        disc_name: str | None = None,
        rup_row_id: int | None = None,
        rup_name: str | None = None,
        show_hidden: bool = False,
    ) -> list[dict[str, Any]]:
        """Get work programs (РПД)."""
        if rup_row_id in self._rows:
            row = self._rows[rup_row_id]
            disc_name = disc_name or row.get("discName")
            rup_name = rup_name or row.get("rupName")
        if not rup_name or not disc_name or rup_row_id is None:
            if not disc_name:
                raise RPDError(
                    "Для поиска РПД сначала вызовите rpd_search_disciplines, затем передайте disc_name и rup_row_id найденной строки. Можно также передать rup_name явно."
                )
            rows = await self.get_plan_item_rups(acc_year=acc_year, disc_name=disc_name)
            rows = [row for row in rows if rup_row_id is None or row["rupRowId"] == rup_row_id]
            programs = []
            for row in rows:
                programs.extend(
                    await self.get_rps(acc_year, row["discName"], row["rupRowId"], row["rupName"], show_hidden)
                )
            return programs
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
        return self._items(await self.client.get_json("/RpdManager/GetRps", params=params), "rps")

    async def download_draft(self, rup_row_id: int, rp_id: int) -> bytes:
        """Download the native XML .rpdx draft, rejecting HTTP-200 JSON errors."""
        params = {"rupRowId": rup_row_id, "rpdId": rp_id}
        resp = await self.client.request("GET", "/RpdManager/DownloadDraft", params=params)
        if "json" in resp.headers.get("content-type", ""):
            self.client.check_api_result(
                self.client._decode_json(resp, "/RpdManager/DownloadDraft"), "/RpdManager/DownloadDraft"
            )
            raise RPDError("Сайт не вернул файл черновика РПД.")
        return resp.content
