"""Convert the site's flat data into a reusable typed template."""

from __future__ import annotations

import base64
from typing import Any

from .document import RPDDocument
from .native import FOS_FIELDS, OUTCOME_FIELDS, goal_text
from .schemas import TEXT_FIELDS, RPDTemplate


async def export_program(
    full: dict[str, Any], document: RPDDocument, name: str | None, include_fos_files: bool
) -> RPDTemplate:
    title = full["title"].get("title", {})
    result: dict[str, Any] = {
        "name": name or title.get("discName") or f"Шаблон РПД {document.rp_id}",
        "description": f"Экспортировано из РПД {document.rp_id}, строка плана {document.rup_row_id}",
        "title": {"disc_name": title.get("discName")},
        "goals": {
            key: goal_text(full["goals"], key)
            for key in ("aims", "tasks", "prerequisites", "outgoings")
            if key in full["goals"]
        },
        "fos": {key: full["fos"][native] for key, (_, native) in FOS_FIELDS.items() if native in full["fos"]},
        "literature": [{"catalog_id": item["litId"], "kind": item["litType"]} for item in full["literature"]],
        "software_ids": [item["id"] for item in full["software"].get("refs", [])],
        "information_system_ids": [item["id"] for item in full["information_systems"].get("refs", [])],
        "facilities": [
            {"catalog_id": item.get("catalogId", item["id"]), "work_type_id": item.get("wtype") or None}
            for item in full["facilities"].get("refs", [])
        ],
        **{
            key: full["rp"][native]
            for key, (_, native) in TEXT_FIELDS.items()
            if native in full["rp"] and key != "comments_text"
        },
    }
    outcomes = []
    settings = (await document.call("GET", "/Rp3/Initialize")).get("settings", {})
    for row in full["learning_outcomes"].get("items", []):
        if row.get("isMock") or not row.get("contents"):
            continue
        item = {
            "indicator": row["indiCode"],
            "category": row["categoryId"],
            "level": row["levelId"],
            "contents": row["contents"],
        }
        if settings.get("useExternalFosFiles"):
            if include_fos_files:
                for kind, prefix, flag in ((1, "current", "hasCurrentFos"), (0, "intermediate", "hasIntermediateFos")):
                    if row.get(flag):
                        content = await document.download_fos(
                            {
                                "cardId": row["cardId"],
                                "categoryId": row["categoryId"],
                                "levelId": row["levelId"],
                                "fosType": kind,
                            }
                        )
                        item[f"{prefix}_fos"] = {
                            "filename": f"fos-{prefix}-{row['indiCode']}-{row['categoryId']}-{row['levelId']}.docx",
                            "content_base64": base64.b64encode(content).decode("ascii"),
                        }
        else:
            item.update({key: row[native] for key, native in OUTCOME_FIELDS.items() if row.get(native) is not None})
        outcomes.append(item)
    result["learning_outcomes"] = outcomes
    content = full["content"]
    native_types = {101: 1, 102: 2, 103: 3, 107: 4}

    def works(part_id: int, theme_id: int | None) -> list[dict[str, Any]]:
        values = []
        for work in content.get("works", []):
            if (
                work.get("partId") != part_id
                or work.get("themeId") != theme_id
                or work.get("wtype") not in native_types
                or not work.get("hours")
            ):
                continue
            activity = str(work.get("name", "")).isdigit() and work["wtype"] in {103, 107}
            values.append(
                {
                    "name": (work.get("comment") or work["name"]) if activity else work["name"],
                    "comment": work.get("comment") or "",
                    "wtype": native_types[work["wtype"]],
                    "activity_id": int(work["name"]) if activity else None,
                    "hours": work["hours"],
                    "semester": work["termNum"],
                    "indicators": sorted({ref["code"] for ref in work.get("upResRefs", [])}),
                }
            )
        return values

    sections = []
    for part in sorted(content.get("parts", []), key=lambda item: item["number"]):
        direct = works(part["id"], None)
        themes = []
        for theme in content.get("themes", []):
            if theme.get("partId") == part["id"]:
                items = works(part["id"], theme["id"])
                if items:
                    themes.append(
                        {
                            "name": theme["name"],
                            "number": len(themes) + 1,
                            "comment": theme.get("comment") or "",
                            "works": items,
                        }
                    )
        if direct or themes:
            sections.append(
                {
                    "name": part["name"],
                    "number": len(sections) + 1,
                    "comment": part.get("comment") or "",
                    "works": direct,
                    "themes": themes,
                }
            )
    if sections:
        result["sections"] = sections
    return RPDTemplate.model_validate(result)
