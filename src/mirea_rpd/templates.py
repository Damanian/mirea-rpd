"""Template management and automatic population for MIREA RPD."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from .client import RPDClient
from .editor import RPDEditor
from .exceptions import RPDError


class WorkItemTemplate(BaseModel):
    name: str
    wtype: int = 1  # 1: Лекция, 2: Лабораторная, 3: Практическое занятие, 4: СРС
    hours: float = 2.0
    comment: str = ""


class ThemeTemplate(BaseModel):
    name: str
    number: int = 1
    comment: str = ""
    works: list[WorkItemTemplate] = Field(default_factory=list)


class SectionTemplate(BaseModel):
    name: str
    number: int = 1
    comment: str = ""
    themes: list[ThemeTemplate] = Field(default_factory=list)


class RPDTemplate(BaseModel):
    name: str = "Шаблон РПД"
    description: str = ""
    it_text: str | None = None
    iss_text: str | None = None
    mu_text: str | None = None
    mup_text: str | None = None
    mto_text: str | None = None
    literature_text: str | None = None
    ovz_text: str | None = None
    vr_text: str | None = None
    sections: list[SectionTemplate] = Field(default_factory=list)


class TemplateManager:
    """Applies templates to an RPD or exports existing programs as templates."""

    def __init__(self, client: RPDClient) -> None:
        self.client = client
        self.editor = RPDEditor(client)

    async def apply_template(
        self,
        rup_row_id: int,
        rp_id: int,
        template: dict[str, Any] | RPDTemplate,
    ) -> dict[str, Any]:
        """Apply text blocks, sections, and parameters from template to the target RPD."""
        data = template.model_dump() if isinstance(template, RPDTemplate) else template
        results: dict[str, Any] = {"applied_fields": [], "errors": []}

        # 1. Update text fields
        text_fields_map = [
            ("it_text", "/Rp/SetItText"),
            ("iss_text", "/Rp/SetIssText"),
            ("mu_text", "/Rp/SetMUText"),
            ("mup_text", "/Rp/SetMUPText"),
            ("mto_text", "/Rp/SetMtoText"),
            ("literature_text", "/Rp/SetLiterature"),
            ("ovz_text", "/Rp/SetOvz"),
            ("vr_text", "/Rp/SetVr"),
        ]

        for field_key, endpoint in text_fields_map:
            val = data.get(field_key)
            if val:
                try:
                    await self.client.request(
                        "PUT",
                        endpoint,
                        params={"rupRowId": rup_row_id, "rpId": rp_id, "text": val},
                    )
                    results["applied_fields"].append(field_key)
                except (RPDError, OSError) as e:
                    results["errors"].append({"field": field_key, "error": str(e)})

        # 2. Save changes to DB
        try:
            await self.client.request(
                "POST",
                "/Rp/Save",
                params={"rupRowId": rup_row_id, "rpId": rp_id},
            )
            results["saved_to_db"] = True
        except (RPDError, OSError) as e:
            results["saved_to_db"] = False
            results["errors"].append({"action": "save_to_db", "error": str(e)})

        results["success"] = len(results["errors"]) == 0
        return results

    async def export_template(
        self,
        rup_row_id: int,
        rp_id: int,
        name: str | None = None,
        save_file: Path | str | None = None,
    ) -> dict[str, Any]:
        """Export an existing RPD as a reusable template."""
        full = await self.editor.get_full_program(rup_row_id=rup_row_id, rp_id=rp_id)
        title = full.get("title") or {}
        rp_fields = full.get("rp") or {}

        template_name = name or title.get("discName") or f"Шаблон РПД {rp_id}"

        template = {
            "name": template_name,
            "description": f"Экспортировано из программы ID {rp_id} (rupRowId {rup_row_id})",
            "it_text": rp_fields.get("itText"),
            "iss_text": rp_fields.get("issText"),
            "mu_text": rp_fields.get("muText"),
            "mup_text": rp_fields.get("mupText"),
            "mto_text": rp_fields.get("mtoText"),
            "literature_text": rp_fields.get("literature"),
            "ovz_text": rp_fields.get("ovz"),
            "vr_text": rp_fields.get("vr"),
        }

        if save_file:
            path = Path(save_file)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(template, ensure_ascii=False, indent=2), encoding="utf-8")

        return template
