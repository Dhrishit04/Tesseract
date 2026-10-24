"""Office document processing skills."""

import asyncio
from typing import Any

from automation.office import get_office_automation
from settings_store import load_settings
from skills.skill_base import SkillBase, SkillResult


def _office() -> Any:
    settings = load_settings().get("automation", {})
    if not settings.get("automation_enabled", False):
        raise RuntimeError("Automation is disabled in settings")
    return get_office_automation(
        bool(settings.get("office_use_com", False)),
        settings.get("office_root"),
    )


def _run_office(operation: str, *args: Any) -> Any:
    office = _office()
    pythoncom = None
    if office.use_com:
        try:
            import pythoncom
        except ImportError:
            pass
        else:
            pythoncom.CoInitialize()
    try:
        return getattr(office, operation)(*args)
    finally:
        if pythoncom is not None:
            pythoncom.CoUninitialize()


class OfficeReadDocumentSkill(SkillBase):
    kind = "read"

    @property
    def name(self) -> str:
        return "office.read_document"

    @property
    def description(self) -> str:
        return "Read paragraphs and tables from a Word document."

    @property
    def parameters(self) -> dict[str, Any]:
        return {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}

    async def execute(self, **kwargs: Any) -> SkillResult:
        try:
            result = await asyncio.to_thread(_run_office, "read_doc", kwargs.get("path", ""))
            return SkillResult.success(result)
        except Exception as exc:
            return SkillResult.failure(str(exc))


class OfficeEditDocumentSkill(SkillBase):
    approval_title = "Edit a document"

    @property
    def name(self) -> str:
        return "office.edit_document"

    @property
    def description(self) -> str:
        return "Apply replace, append, or prepend edits to a Word document."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {"path": {"type": "string"}, "edits": {"type": "array", "items": {"type": "object"}}},
            "required": ["path", "edits"],
        }

    async def execute(self, **kwargs: Any) -> SkillResult:
        try:
            result = await asyncio.to_thread(
                _run_office, "edit_doc", kwargs.get("path", ""), kwargs.get("edits", [])
            )
            return SkillResult.success(result)
        except Exception as exc:
            return SkillResult.failure(str(exc))


class OfficeCreatePivotSkill(SkillBase):
    approval_title = "Build a pivot table"

    @property
    def name(self) -> str:
        return "office.create_pivot_table"

    @property
    def description(self) -> str:
        return "Create an Excel pivot table workbook from a CSV file."

    @property
    def parameters(self) -> dict[str, Any]:
        return {"type": "object", "properties": {"csv_path": {"type": "string"}, "config": {"type": "object"}}, "required": ["csv_path", "config"]}

    async def execute(self, **kwargs: Any) -> SkillResult:
        try:
            result = await asyncio.to_thread(
                _run_office, "create_pivot", kwargs.get("csv_path", ""), kwargs.get("config", {})
            )
            return SkillResult.success(result)
        except Exception as exc:
            return SkillResult.failure(str(exc))


class OfficeReadExcelSkill(SkillBase):
    kind = "read"

    @property
    def name(self) -> str:
        return "office.read_excel"

    @property
    def description(self) -> str:
        return "Read all worksheets and rows from an Excel workbook."

    @property
    def parameters(self) -> dict[str, Any]:
        return {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}

    async def execute(self, **kwargs: Any) -> SkillResult:
        try:
            result = await asyncio.to_thread(_run_office, "read_excel", kwargs.get("path", ""))
            return SkillResult.success(result)
        except Exception as exc:
            return SkillResult.failure(str(exc))


class OfficeCreatePresentationSkill(SkillBase):
    approval_title = "Create a presentation"

    @property
    def name(self) -> str:
        return "office.create_presentation"

    @property
    def description(self) -> str:
        return "Create a PowerPoint presentation from slide definitions."

    @property
    def parameters(self) -> dict[str, Any]:
        return {"type": "object", "properties": {"template": {"type": ["string", "null"]}, "slides": {"type": "array"}, "output_path": {"type": "string"}}, "required": ["slides", "output_path"]}

    async def execute(self, **kwargs: Any) -> SkillResult:
        try:
            result = await asyncio.to_thread(
                _run_office,
                "create_presentation",
                kwargs.get("template"),
                kwargs.get("slides", []),
                kwargs.get("output_path", ""),
            )
            return SkillResult.success(result)
        except Exception as exc:
            return SkillResult.failure(str(exc))
