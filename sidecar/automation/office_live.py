"""Work inside the Office apps themselves through COM, attaching to what the owner already has open.

Nothing here calls Quit or Close: the apps and documents belong to the owner. Nothing saves unless
live_save is called, so changes stay on the open copy until then.
"""

from pathlib import Path
from typing import Any

try:
    import pythoncom
except ImportError:
    pythoncom = None

from automation.office import OfficeAutomationError
from file_access import denied_message, is_allowed

PROG_IDS = {"excel": "Excel.Application", "word": "Word.Application", "powerpoint": "PowerPoint.Application"}
DOCUMENTS = {"excel": "Workbooks", "word": "Documents", "powerpoint": "Presentations"}
ACTIVE_DOCUMENT = {"excel": "ActiveWorkbook", "word": "ActiveDocument", "powerpoint": "ActivePresentation"}
SAVE_AS = {"excel": "SaveAs", "word": "SaveAs2", "powerpoint": "SaveAs"}

# RPC_E_CALL_REJECTED, RPC_E_SERVERCALL_RETRYLATER, VBA_E_IGNORE: the app is in cell edit or showing a dialog.
BUSY_CODES = (-2147418111, -2147417846, -2146777998)
DISP_E_EXCEPTION = -2147352567  # the real code and Office's explanation are then in the details
BUSY_MESSAGE = "Office is busy, usually because a cell is being edited or a dialog is open. Finish that in the app and try again."
# Files opened through COM run their macros by default, unlike files the owner opens.
FORCE_DISABLE_MACROS = 3

MAX_READ_ROWS = 2000
MAX_READ_CHARACTERS = 200_000
MAX_WORD_FIND = 255  # Word's own limit for Find and its replacement

XL_DATABASE = 1
XL_ROW_FIELD = 1
XL_COLUMN_FIELD = 2
PIVOT_FUNCTIONS = {"sum": -4157, "count": -4112, "average": -4106, "max": -4136, "min": -4139}
WD_FIND_CONTINUE = 1
WD_REPLACE_ALL = 2
PP_TITLE_AND_CONTENT = 2


def run(operation: str, *args: Any, **kwargs: Any) -> dict:
    """Run one operation on this thread with COM set up, turning COM failures into plain messages."""
    if pythoncom is None:
        raise OfficeAutomationError("Working in open Office apps needs Windows and Microsoft Office.")
    pythoncom.CoInitialize()
    try:
        return globals()[operation](*args, **kwargs)
    except pythoncom.com_error as exc:
        failure = _plain_message(exc)
    except Exception as exc:
        failure = str(exc)
    finally:
        pythoncom.CoUninitialize()
    # Raised out here so no traceback keeps COM objects alive past CoUninitialize.
    raise OfficeAutomationError(failure)


def _plain_message(error: Any) -> str:
    code, text, details = error.args[0], error.args[1], error.args[2]
    if code == DISP_E_EXCEPTION and details:
        code, text = details[5], details[2] or text
    return BUSY_MESSAGE if code in BUSY_CODES else f"Office refused that: {text}"


def _connect(kind: str) -> Any:
    """The running app, or a new visible one when none is running."""
    import win32com.client

    try:
        return win32com.client.GetActiveObject(PROG_IDS[kind])
    except pythoncom.com_error:
        app = win32com.client.Dispatch(PROG_IDS[kind])
        app.Visible = True
        return app


def _app(kind: str) -> Any:
    if kind not in PROG_IDS:
        raise OfficeAutomationError(f"Unknown Office app: {kind}. Use excel, word or powerpoint.")
    return _connect(kind)


def _is_local_path(name: str) -> bool:
    return "://" not in name and ("\\" in name or "/" in name)


def _matches(document: Any, reference: str) -> bool:
    """True when reference is the document's name or full path. Files in OneDrive report a URL as their path."""
    full_name = document.FullName
    wanted = reference.casefold()
    if wanted in (full_name.casefold(), document.Name.casefold()):
        return True
    if "://" in full_name:
        return Path(reference).name.casefold() == document.Name.casefold()
    return _is_local_path(full_name) and _resolved(reference) == _resolved(full_name)


def _resolved(path: str) -> str:
    return str(Path(path).expanduser().resolve()).casefold()


def _checked(document: Any) -> Any:
    """Being open in Office doesn't widen the access the owner chose."""
    if _is_local_path(document.FullName) and not is_allowed(Path(document.FullName)):
        raise OfficeAutomationError(denied_message(Path(document.FullName)))
    return document


def _open_file(app: Any, kind: str, reference: str) -> Any:
    path = Path(reference).expanduser().resolve()
    if not is_allowed(path):
        raise OfficeAutomationError(denied_message(path))
    if not path.is_file():
        raise OfficeAutomationError(f"File not found: {path}")
    previous = app.AutomationSecurity
    app.AutomationSecurity = FORCE_DISABLE_MACROS
    try:
        return getattr(app, DOCUMENTS[kind]).Open(str(path))
    finally:
        app.AutomationSecurity = previous


def _document(app: Any, kind: str, reference: str | None) -> Any:
    """The open document called reference, the active one without a reference, or the file opened now."""
    if not reference:
        document = getattr(app, ACTIVE_DOCUMENT[kind])
        if document is None:
            raise OfficeAutomationError("No document is open.")
        return _checked(document)
    for document in getattr(app, DOCUMENTS[kind]):
        if _matches(document, reference):
            return _checked(document)
    return _open_file(app, kind, reference)


def _summary(document: Any) -> dict:
    return {"document": document.FullName, "name": document.Name}


def live_open(kind: str, path: str | None = None) -> dict:
    app = _app(kind)
    document = _document(app, kind, path) if path else getattr(app, DOCUMENTS[kind]).Add()
    return _summary(document)


def live_save(kind: str, document: str | None = None, path: str | None = None) -> dict:
    opened = _document(_app(kind), kind, document)
    if opened.ReadOnly:
        raise OfficeAutomationError(f"{opened.Name} is open as read-only, so it can't be saved. Give a new path or reopen it.")
    if not path:
        if not opened.Path:
            raise OfficeAutomationError(f"{opened.Name} has never been saved, so it needs a path.")
        opened.Save()
        return _summary(opened)

    target = Path(path).expanduser().resolve()
    if not is_allowed(target):
        raise OfficeAutomationError(denied_message(target))
    if opened.Path and _resolved(opened.FullName) == _resolved(path):
        opened.Save()
        return _summary(opened)
    if target.exists():
        # Office would stop on a confirmation dialog, and the file isn't Tesseract's to replace.
        raise OfficeAutomationError(f"{target} already exists. Choose another name.")
    getattr(opened, SAVE_AS[kind])(str(target))
    return _summary(opened)


def _split_address(address: str) -> tuple[str | None, str]:
    sheet, separator, cells = address.rpartition("!")
    return (sheet.strip("'"), cells) if separator else (None, address)


def _sheet(workbook: Any, name: str | None) -> Any:
    if not name:
        return workbook.ActiveSheet
    for sheet in workbook.Worksheets:
        if sheet.Name.casefold() == name.casefold():
            return sheet
    raise OfficeAutomationError(f"{workbook.Name} has no sheet called {name}.")


def _sheet_or_new(workbook: Any, name: str | None) -> Any:
    try:
        return _sheet(workbook, name)
    except OfficeAutomationError:
        sheet = workbook.Worksheets.Add()
        sheet.Name = name
        return sheet


def _has_sheet(workbook: Any, name: str | None) -> bool:
    return not name or any(sheet.Name.casefold() == name.casefold() for sheet in workbook.Worksheets)


def _table(value: Any) -> list[list[Any]]:
    """Excel gives a bare value for one cell and tuples of rows for more."""
    return [list(row) for row in value] if isinstance(value, tuple) else [[value]]


def live_read_range(workbook: str | None = None, address: str | None = None, sheet: str | None = None) -> dict:
    book = _document(_app("excel"), "excel", workbook)
    named_sheet, cells = _split_address(address) if address else (None, None)
    target = _sheet(book, named_sheet or sheet)
    area = target.Range(cells) if cells else target.UsedRange
    row_count = area.Rows.Count
    if row_count > MAX_READ_ROWS:
        first = target.Cells(area.Row, area.Column)
        area = target.Range(first, target.Cells(area.Row + MAX_READ_ROWS - 1, area.Column + area.Columns.Count - 1))
    return {"sheet": target.Name, "rows": _table(area.Value), "truncated": row_count > MAX_READ_ROWS}


def live_write_range(workbook: str | None, start: str, rows: list[list[Any]], sheet: str | None = None) -> dict:
    if not rows or not all(isinstance(row, list) for row in rows) or not any(rows):
        raise OfficeAutomationError("rows must be a list of rows with at least one value.")
    width = max(len(row) for row in rows)
    for value in (value for row in rows for value in row):
        # A DDE formula starts another program, which this tool must not be a way to do.
        if isinstance(value, str) and value.startswith(("=", "+", "-", "@")) and "|" in value:
            raise OfficeAutomationError(f"That formula would start another program, so nothing was written: {value}")
    book = _document(_app("excel"), "excel", workbook)
    named_sheet, cell = _split_address(start)
    target = _sheet_or_new(book, named_sheet or sheet)
    first = target.Range(cell)
    last = target.Cells(first.Row + len(rows) - 1, first.Column + width - 1)
    target.Range(first, last).Value = tuple(tuple(row + [None] * (width - len(row))) for row in rows)
    return {"sheet": target.Name, "start": cell, "rows": len(rows), "columns": width}


def live_create_pivot(workbook: str | None, source: str, destination: str = "Pivot!A3", rows: list[str] = (),
                      columns: list[str] = (), values: list[dict] = ()) -> dict:
    """Build a PivotTable. Everything is checked against the header row before the workbook changes."""
    book = _document(_app("excel"), "excel", workbook)
    source_sheet_name, source_cells = _split_address(source)
    source_sheet = _sheet(book, source_sheet_name)
    area = source_sheet.Range(source_cells)
    left, top = area.Column, area.Row
    header = _table(source_sheet.Range(source_sheet.Cells(top, left), source_sheet.Cells(top, left + area.Columns.Count - 1)).Value)[0]
    if area.Rows.Count < 2 or None in header:
        raise OfficeAutomationError("The source needs a header in every column and at least one row of data.")
    if not values:
        raise OfficeAutomationError("A pivot table needs at least one value field.")
    wanted = [*rows, *columns, *(value.get("field") for value in values)]
    missing = [field for field in wanted if field not in header]
    if missing:
        raise OfficeAutomationError(f"Not in the header row: {', '.join(map(str, missing))}. The header row has: {', '.join(map(str, header))}.")
    unknown = [value.get("function") for value in values if value.get("function") not in PIVOT_FUNCTIONS]
    if unknown:
        raise OfficeAutomationError(f"Unknown function: {unknown[0]}. Use {', '.join(PIVOT_FUNCTIONS)}.")

    destination_sheet_name, destination_cell = _split_address(destination)
    if _has_sheet(book, destination_sheet_name):
        if _sheet(book, destination_sheet_name).Range(destination_cell).Value is not None:
            raise OfficeAutomationError(f"{destination} already has data in it. Choose an empty spot.")

    target = _sheet_or_new(book, destination_sheet_name)
    table = book.PivotCaches().Create(XL_DATABASE, area).CreatePivotTable(target.Range(destination_cell))
    for names, orientation in ((rows, XL_ROW_FIELD), (columns, XL_COLUMN_FIELD)):
        for name in names:
            table.PivotFields(name).Orientation = orientation
    for value in values:
        field = value["field"]
        table.AddDataField(table.PivotFields(field), f"{value['function'].capitalize()} of {field}", PIVOT_FUNCTIONS[value["function"]])
    return {"sheet": target.Name, "destination": destination_cell, "name": table.Name, "fields": wanted}


def _slide_text(slide: Any) -> list[str]:
    return [shape.TextFrame.TextRange.Text for shape in slide.Shapes if shape.HasTextFrame]


def live_read(kind: str, document: str | None = None) -> dict:
    if kind == "excel":
        raise OfficeAutomationError("Use office.live_read_range for Excel.")
    opened = _document(_app(kind), kind, document)
    if kind == "word":
        text = opened.Content.Text
        return {**_summary(opened), "text": text[:MAX_READ_CHARACTERS], "truncated": len(text) > MAX_READ_CHARACTERS}
    return {**_summary(opened), "slides": [{"slide": index, "text": _slide_text(slide)} for index, slide in enumerate(opened.Slides, 1)]}


def _check_word_edit(edit: dict) -> None:
    action = edit.get("action")
    if action == "insert" and isinstance(edit.get("text"), str):
        return
    if action == "replace" and edit.get("find") and isinstance(edit.get("replace"), str):
        if max(len(edit["find"]), len(edit["replace"])) > MAX_WORD_FIND:
            raise OfficeAutomationError(f"Word can only find and replace {MAX_WORD_FIND} characters at a time.")
        return
    raise OfficeAutomationError(f"Word edits are insert (text, where) or replace (find, replace), not: {edit}")


def _apply_word_edit(document: Any, edit: dict) -> None:
    if edit["action"] == "insert":
        if edit.get("where") == "start":
            document.Content.InsertBefore(edit["text"])
        else:
            document.Content.InsertAfter(edit["text"])
    elif not document.Content.Find.Execute(edit["find"], False, False, False, False, False, True, WD_FIND_CONTINUE, False,
                                           edit["replace"], WD_REPLACE_ALL):
        raise OfficeAutomationError(f"Didn't find: {edit['find']}")


def _check_slide_edits(presentation: Any, edits: list[dict]) -> None:
    slides = len(list(presentation.Slides))
    for edit in edits:
        if edit.get("action") == "add_slide":
            slides += 1
        elif edit.get("action") == "set_text" and isinstance(edit.get("text"), str):
            if not 1 <= edit.get("slide", 0) <= slides:
                raise OfficeAutomationError(f"There is no slide {edit.get('slide')}.")
        else:
            raise OfficeAutomationError(f"PowerPoint edits are add_slide (title, body) or set_text (slide, shape, text), not: {edit}")


def _add_slide(presentation: Any) -> Any:
    layout = presentation.SlideMaster.CustomLayouts(PP_TITLE_AND_CONTENT)
    return presentation.Slides.AddSlide(len(list(presentation.Slides)) + 1, layout)


def _set_shape_text(slide: Any, index: int, text: str) -> None:
    shapes = list(slide.Shapes)
    if not 1 <= index <= len(shapes) or not shapes[index - 1].HasTextFrame:
        raise OfficeAutomationError(f"That slide has no text shape {index}.")
    shapes[index - 1].TextFrame.TextRange.Text = text


def _apply_slide_edit(presentation: Any, edit: dict) -> None:
    if edit["action"] == "add_slide":
        slide = _add_slide(presentation)
        _set_shape_text(slide, 1, edit.get("title", ""))
        if edit.get("body"):
            _set_shape_text(slide, 2, edit["body"])
    else:
        _set_shape_text(list(presentation.Slides)[edit["slide"] - 1], edit.get("shape", 1), edit["text"])


def live_edit(kind: str, document: str | None, edits: list[dict]) -> dict:
    if kind == "excel":
        raise OfficeAutomationError("Use office.live_write_range for Excel.")
    opened = _document(_app(kind), kind, document)
    if kind == "word":
        for edit in edits:
            _check_word_edit(edit)
        apply = _apply_word_edit
    else:
        _check_slide_edits(opened, edits)
        apply = _apply_slide_edit
    for edit in edits:
        apply(opened, edit)
    return {**_summary(opened), "edits_applied": len(edits)}
