"""Skills that work inside open Excel, Word and PowerPoint documents."""

import asyncio
from typing import Any

from automation.office_live import run
from automation.policy import load_automation_policy
from skills.skill_base import SkillBase, SkillResult

TIMEOUT_SECONDS = 60
KIND = {"type": "string", "enum": ["excel", "word", "powerpoint"], "description": "Which Office app"}
WORKBOOK = {"type": "string", "description": "Path or name of the workbook. Defaults to the active one."}
DOCUMENT = {"type": "string", "description": "Path or name of the document. Defaults to the active one."}


async def _call(operation: str, *args: Any, **kwargs: Any) -> SkillResult:
    if not load_automation_policy()["automation_enabled"]:
        return SkillResult.failure("Automation is disabled in settings")
    try:
        # A modal dialog in Office can hold a COM call forever; the chat must not wait with it.
        return SkillResult.success(await asyncio.wait_for(asyncio.to_thread(run, operation, *args, **kwargs), TIMEOUT_SECONDS))
    except TimeoutError:
        return SkillResult.failure(f"Office didn't answer within {TIMEOUT_SECONDS} seconds. A dialog may be open in it. The action may still finish once that is closed, so check before trying again.")
    except Exception as exc:
        return SkillResult.failure(str(exc))


class OfficeLiveOpenSkill(SkillBase):
    approval_title = "Open a document in Office"

    def approval_summary(self, arguments: dict) -> str:
        return f"Open {arguments.get('path') or 'a new document'} in {arguments.get('app', 'Office')}"

    @property
    def name(self) -> str:
        return "office.live_open"

    @property
    def description(self) -> str:
        return ("Open a document in Excel, Word or PowerPoint, reusing it if it is already open, with its unsaved changes. "
                "Without a path, starts a new one. Macros in opened files stay off.")

    @property
    def parameters(self) -> dict[str, Any]:
        return {"type": "object", "properties": {"app": KIND, "path": {"type": "string"}}, "required": ["app"]}

    async def execute(self, **kwargs: Any) -> SkillResult:
        return await _call("live_open", kwargs.get("app", ""), kwargs.get("path"))


class OfficeLiveReadRangeSkill(SkillBase):
    kind = "read"

    @property
    def name(self) -> str:
        return "office.live_read_range"

    @property
    def description(self) -> str:
        return "Read cells from a workbook open in Excel, including unsaved changes. Without an address, reads the used range."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "workbook": WORKBOOK,
                "address": {"type": "string", "description": "Range such as A1:D20 or Data!A1:D20"},
                "sheet": {"type": "string"},
            },
        }

    async def execute(self, **kwargs: Any) -> SkillResult:
        return await _call("live_read_range", kwargs.get("workbook"), kwargs.get("address"), kwargs.get("sheet"))


class OfficeLiveWriteRangeSkill(SkillBase):
    approval_title = "Write to a workbook"

    def approval_summary(self, arguments: dict) -> str:
        return f"Write {len(arguments.get('rows', []))} rows at {arguments.get('start', '')} in {arguments.get('workbook') or 'the active workbook'}, not saved yet"

    @property
    def name(self) -> str:
        return "office.live_write_range"

    @property
    def description(self) -> str:
        return "Write rows of values or formulas into a workbook open in Excel, replacing what is in those cells. A missing sheet is created. Does not save."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "workbook": WORKBOOK,
                "start": {"type": "string", "description": "Top-left cell, such as A1 or Data!A1"},
                "rows": {"type": "array", "items": {"type": "array", "items": {}}, "description": "Rows of cell values"},
                "sheet": {"type": "string"},
            },
            "required": ["start", "rows"],
        }

    async def execute(self, **kwargs: Any) -> SkillResult:
        return await _call("live_write_range", kwargs.get("workbook"), kwargs.get("start", ""), kwargs.get("rows", []), kwargs.get("sheet"))


class OfficeLiveCreatePivotSkill(SkillBase):
    approval_title = "Build a PivotTable"

    def approval_summary(self, arguments: dict) -> str:
        fields = ", ".join([*arguments.get("rows", []), *arguments.get("columns", [])])
        return f"Add a PivotTable by {fields} to {arguments.get('workbook') or 'the active workbook'}, not saved yet"

    @property
    def name(self) -> str:
        return "office.live_create_pivot"

    @property
    def description(self) -> str:
        return "Build a real Excel PivotTable from a range with a header row, in a workbook open in Excel. Does not save."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "workbook": WORKBOOK,
                "source": {"type": "string", "description": "Range with its header row, such as Data!A1:F200"},
                "destination": {"type": "string", "description": "Top-left cell for the table (default Pivot!A3). A missing sheet is created."},
                "rows": {"type": "array", "items": {"type": "string"}, "description": "Header names for row fields"},
                "columns": {"type": "array", "items": {"type": "string"}, "description": "Header names for column fields"},
                "values": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {"field": {"type": "string"}, "function": {"type": "string", "enum": ["sum", "count", "average", "max", "min"]}},
                        "required": ["field", "function"],
                    },
                },
            },
            "required": ["source", "values"],
        }

    async def execute(self, **kwargs: Any) -> SkillResult:
        return await _call("live_create_pivot", kwargs.get("workbook"), kwargs.get("source", ""), kwargs.get("destination", "Pivot!A3"),
                           kwargs.get("rows", []), kwargs.get("columns", []), kwargs.get("values", []))


class OfficeLiveSaveSkill(SkillBase):
    approval_title = "Save a document"

    def approval_summary(self, arguments: dict) -> str:
        return f"Save {arguments.get('document') or 'the active document'}" + (f" as {arguments['path']}" if arguments.get("path") else "")

    @property
    def name(self) -> str:
        return "office.live_save"

    @property
    def description(self) -> str:
        return "Save a document open in Excel, Word or PowerPoint. Give a path for a document that has never been saved."

    @property
    def parameters(self) -> dict[str, Any]:
        return {"type": "object", "properties": {"app": KIND, "document": DOCUMENT, "path": {"type": "string"}}, "required": ["app"]}

    async def execute(self, **kwargs: Any) -> SkillResult:
        return await _call("live_save", kwargs.get("app", ""), kwargs.get("document"), kwargs.get("path"))


class OfficeLiveReadSkill(SkillBase):
    kind = "read"

    @property
    def name(self) -> str:
        return "office.live_read"

    @property
    def description(self) -> str:
        return "Read the text of a document open in Word or the slides open in PowerPoint, including unsaved changes."

    @property
    def parameters(self) -> dict[str, Any]:
        return {"type": "object", "properties": {"app": {**KIND, "enum": ["word", "powerpoint"]}, "document": DOCUMENT}, "required": ["app"]}

    async def execute(self, **kwargs: Any) -> SkillResult:
        return await _call("live_read", kwargs.get("app", ""), kwargs.get("document"))


class OfficeLiveEditSkill(SkillBase):
    approval_title = "Edit a document in Office"

    def approval_summary(self, arguments: dict) -> str:
        return f"Make {len(arguments.get('edits', []))} edits in {arguments.get('document') or 'the active document'}, not saved yet"

    @property
    def name(self) -> str:
        return "office.live_edit"

    @property
    def description(self) -> str:
        return ("Edit a document open in Word or PowerPoint. Does not save. Word edits: {action: insert, text, where: start or end} "
                "and {action: replace, find, replace}. PowerPoint edits: {action: add_slide, title, body} and {action: set_text, slide, shape, text}.")

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {"app": {**KIND, "enum": ["word", "powerpoint"]}, "document": DOCUMENT, "edits": {"type": "array", "items": {"type": "object"}}},
            "required": ["app", "edits"],
        }

    async def execute(self, **kwargs: Any) -> SkillResult:
        return await _call("live_edit", kwargs.get("app", ""), kwargs.get("document"), kwargs.get("edits", []))
