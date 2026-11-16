"""Template management and automatic population for MIREA RPD."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

from .client import RPDClient
from .editor import RPDEditor
from .exceptions import RPDError
from .exporting import export_program
from .native import NativeWriter
from .schemas import RPDTemplate, SectionTemplate, ThemeTemplate, WorkItemTemplate
from .validation import validate_template

__all__ = ["RPDTemplate", "SectionTemplate", "TemplateManager", "ThemeTemplate", "WorkItemTemplate"]


class TemplateManager:
    """Applies templates to an RPD or exports existing programs as templates."""

    def __init__(self, client: RPDClient) -> None:
        self.client = client
        self.editor = RPDEditor(client)

    async def apply_template(
        self,
        rup_row_id: int,
        rp_id: int | None,
        template: dict[str, Any] | RPDTemplate,
        *,
        dry_run: bool = False,
        content_mode: Literal["merge", "replace"] = "merge",
        require_complete: bool = False,
    ) -> dict[str, Any]:
        """Validate, stage, save and read back the requested changes."""
        validation = validate_template(template)
        results: dict[str, Any] = {
            "success": False,
            "dry_run": dry_run,
            "validation": validation,
            "applied_fields": [],
            "staged_fields": [],
            "unchanged_fields": [],
            "errors": list(validation["errors"]),
            "saved_to_db": False,
            "verified": False,
        }
        if not validation["valid"]:
            return results
        model = RPDTemplate.model_validate(template)
        if require_complete and not model.defer_approval:
            missing = [
                key for key in ("protocol_date", "protocol_number") if not model.title or not getattr(model.title, key)
            ]
            if missing:
                results["requires_user_input"] = True
                results["required_user_inputs"] = [f"title.{key}" for key in missing]
                results["question"] = (
                    "Укажите дату утверждения и номер протокола либо явно попросите пока оставить их незаполненными."
                )
                results["errors"].append({"action": "approval_metadata", "error": results["question"]})
                return results
        results["approval_deferred"] = model.defer_approval
        requested = [*validation["text_fields"], *validation["structured_fields"]]
        if model.sections is not None:
            requested.append("sections")
        if dry_run:
            results["success"] = True
            results["would_update"] = requested
            results["site_checked"] = False
            return results

        document = self.client.document(rup_row_id, rp_id)
        writer = NativeWriter(document)
        action = "preflight"
        async with document.edit_lock:
            try:
                await writer.preflight(model, complete=require_complete)
                if writer.validation_report:
                    results["validation"] = writer.validation_report
                results["site_checked"] = True
                action = "stage"
                await writer.apply(model, content_mode=content_mode)
                action = "verify_before_save"
                results["errors"].extend(await writer.verify(model, complete=require_complete))
                if not results["errors"]:
                    action = "save_to_db"
                    if writer.operations or document.common_state.get("hasChanges") or document.rp_id is None:
                        saved = await document.save()
                        results["url"] = saved.get("url")
                    if document.rp_id is None:
                        raise RPDError("Сайт не вернул ID созданной РПД.")
                    results["saved_to_db"] = True
                    results["rp_id"] = document.rp_id
                    results["rup_row_id"] = rup_row_id
                    action = "verify_after_save"
                    await document.initialize(force=True)
                    results["errors"].extend(await writer.verify(model, complete=require_complete))
                    results["verified"] = not results["errors"]
                    if results["verified"]:
                        aliases = {"software_ids": "ItIndex", "information_system_ids": "IssIndex"}
                        for field in requested:
                            prefix = aliases.get(field, field)
                            changed = any(op == prefix or op.startswith(f"{prefix}.") for op in writer.operations)
                            if field == "sections":
                                changed = any(
                                    op.startswith(
                                        (
                                            "section.",
                                            "theme.",
                                            "work.",
                                            "attestation.",
                                            "remove_section.",
                                            "remove_work.",
                                        )
                                    )
                                    for op in writer.operations
                                )
                            results["applied_fields" if changed else "unchanged_fields"].append(field)
            except (RPDError, OSError) as e:
                results["errors"].append({"action": action, "error": str(e)})
            finally:
                results["staged_fields"] = writer.operations
                results["draft_has_unsaved_changes"] = document.common_state.get("hasChanges", bool(writer.operations))

        results["success"] = results["saved_to_db"] and results["verified"] and not results["errors"]
        return results

    async def export_template(
        self,
        rup_row_id: int,
        rp_id: int,
        name: str | None = None,
        save_file: Path | str | None = None,
        include_fos_files: bool = True,
    ) -> dict[str, Any]:
        """Export an existing RPD as a reusable template."""
        full = await self.editor.get_full_program(rup_row_id=rup_row_id, rp_id=rp_id)
        if not full["complete"]:
            raise RPDError(f"Нельзя экспортировать неполную РПД: {full['errors']}")
        model = await export_program(full, self.client.document(rup_row_id, rp_id), name, include_fos_files)
        template = model.model_dump(exclude_none=True)

        if save_file:
            path = Path(save_file)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(template, ensure_ascii=False, indent=2), encoding="utf-8")

        return template
