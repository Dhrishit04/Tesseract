"""Working in open Office documents, against fake COM objects so no real Office starts."""

import asyncio
import os
import tempfile
import unittest
import weakref
from pathlib import Path
from unittest.mock import MagicMock, patch

import pythoncom

from automation import office_live
from automation.office import OfficeAutomation, OfficeAutomationError
from skills.office_live import OfficeLiveCreatePivotSkill, OfficeLiveReadRangeSkill, OfficeLiveWriteRangeSkill


class Collection(list):
    """A COM collection that also creates and opens documents."""

    def __init__(self, items=(), make=None):
        super().__init__(items)
        self.make = make
        self.opened = []

    def Open(self, path):
        self.opened.append(path)
        document = self.make(path)
        self.append(document)
        return document

    def Add(self):
        document = self.make("Book2")
        self.append(document)
        return document


def fail_if_called(*_args, **_kwargs):
    raise AssertionError("must never close or quit what the owner has open")


class FakeSheet:
    def __init__(self, name, cells=None):
        self.Name = name
        self.ranges = {}
        self.cells = cells or {}
        self.written = None

    def Range(self, first, last=None):
        if last is not None:
            return FakeRange(self, f"{first.address}:{last.address}", first.Row, first.Column, last.Row - first.Row + 1, last.Column - first.Column + 1)
        return self.ranges.setdefault(first, FakeRange(self, first))

    def Cells(self, row, column):
        return FakeRange(self, f"R{row}C{column}", row, column)


class FakeRange:
    def __init__(self, sheet, address, row=1, column=1, rows=1, columns=1):
        self.sheet = sheet
        self.address = address
        self.Row, self.Column = row, column
        self.Rows = MagicMock(Count=rows)
        self.Columns = MagicMock(Count=columns)
        self.stored = sheet.cells.get(address)

    @property
    def Value(self):
        return self.stored

    @Value.setter
    def Value(self, rows):
        self.sheet.written = (self.address, rows)


def fake_workbook(full_name, sheets=("Data",)):
    workbook = MagicMock()
    workbook.FullName = full_name
    workbook.Name = Path(full_name).name
    workbook.Path = str(Path(full_name).parent) if "\\" in full_name or "/" in full_name else ""
    workbook.ReadOnly = False
    workbook.Worksheets = Collection(FakeSheet(name) for name in sheets)
    workbook.Worksheets.Add = MagicMock(side_effect=lambda: workbook.Worksheets.append(FakeSheet("Sheet2")) or workbook.Worksheets[-1])
    workbook.ActiveSheet = workbook.Worksheets[0]
    workbook.Close.side_effect = fail_if_called
    return workbook


def fake_excel(*workbooks):
    excel = MagicMock()
    excel.Workbooks = Collection(workbooks, make=fake_workbook)
    excel.Quit.side_effect = fail_if_called
    excel.AutomationSecurity = 1
    return excel


class LiveTestCase(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.TemporaryDirectory()
        self.addCleanup(self.root.cleanup)
        self.folder = Path(self.root.name)
        for patcher in (patch("automation.office_live.is_allowed", side_effect=lambda path: "blocked" not in str(path)),):
            patcher.start()
            self.addCleanup(patcher.stop)

    def connect_to(self, app):
        patcher = patch("automation.office_live._connect", return_value=app)
        patcher.start()
        self.addCleanup(patcher.stop)
        return app


class AttachTests(LiveTestCase):
    def test_an_open_workbook_is_reused_with_its_unsaved_changes(self):
        path = str(self.folder / "book.xlsx")
        Path(path).write_text("x")
        workbook = fake_workbook(path)
        excel = self.connect_to(fake_excel(workbook))
        result = office_live.live_open("excel", path)
        self.assertEqual(excel.Workbooks.opened, [])
        self.assertEqual(result["document"], path)
        workbook.Close.assert_not_called()

    def test_a_workbook_is_found_by_its_window_name(self):
        workbook = fake_workbook("Book1")
        self.connect_to(fake_excel(workbook))
        self.assertEqual(office_live.live_open("excel", "book1")["document"], "Book1")

    def test_a_closed_file_is_opened(self):
        path = self.folder / "book.xlsx"
        path.write_text("x")
        excel = self.connect_to(fake_excel())
        office_live.live_open("excel", str(path))
        self.assertEqual(excel.Workbooks.opened, [str(path.resolve())])

    def test_macros_stay_off_while_opening_and_the_setting_comes_back(self):
        path = self.folder / "book.xlsm"
        path.write_text("x")
        excel = self.connect_to(fake_excel())
        seen = []
        original = excel.Workbooks.Open
        excel.Workbooks.Open = lambda p: (seen.append(excel.AutomationSecurity), original(p))[1]
        office_live.live_open("excel", str(path))
        self.assertEqual(seen, [office_live.FORCE_DISABLE_MACROS])
        self.assertEqual(excel.AutomationSecurity, 1)

    def test_a_missing_file_is_a_plain_error(self):
        self.connect_to(fake_excel())
        with self.assertRaisesRegex(OfficeAutomationError, "not found"):
            office_live.live_open("excel", str(self.folder / "nope.xlsx"))

    def test_a_refused_path_is_never_opened(self):
        path = self.folder / "blocked.xlsx"
        path.write_text("x")
        excel = self.connect_to(fake_excel())
        with self.assertRaises(OfficeAutomationError):
            office_live.live_open("excel", str(path))
        self.assertEqual(excel.Workbooks.opened, [])

    def test_an_open_workbook_outside_the_level_is_refused_too(self):
        self.connect_to(fake_excel(fake_workbook("C:\\blocked\\book.xlsx")))
        with self.assertRaises(OfficeAutomationError):
            office_live.live_read_range("book.xlsx")

    def test_no_path_makes_a_new_document(self):
        excel = self.connect_to(fake_excel())
        self.assertEqual(office_live.live_open("excel")["document"], "Book2")
        self.assertEqual(len(excel.Workbooks), 1)


class RangeTests(LiveTestCase):
    def setUp(self):
        super().setUp()
        self.workbook = fake_workbook("Book1")
        self.sheet = self.workbook.Worksheets[0]
        self.connect_to(fake_excel(self.workbook))

    def test_writes_a_table(self):
        result = office_live.live_write_range("Book1", "A1", [["Region", "Units"], ["EU", 3]], sheet="Data")
        address, rows = self.sheet.written
        self.assertEqual(rows, (("Region", "Units"), ("EU", 3)))
        self.assertEqual(result["rows"], 2)
        self.assertEqual(result["columns"], 2)

    def test_short_rows_are_padded(self):
        office_live.live_write_range("Book1", "A1", [["a", "b"], ["c"]])
        self.assertEqual(self.sheet.written[1], (("a", "b"), ("c", None)))

    def test_a_missing_sheet_is_made(self):
        office_live.live_write_range("Book1", "A1", [["a"]], sheet="Notes")
        self.assertEqual(self.workbook.Worksheets[-1].Name, "Notes")

    def test_nothing_to_write_is_refused(self):
        with self.assertRaises(OfficeAutomationError):
            office_live.live_write_range("Book1", "A1", [])

    def test_a_formula_that_runs_a_program_is_refused(self):
        with self.assertRaises(OfficeAutomationError):
            office_live.live_write_range("Book1", "A1", [["=cmd|' /c calc'!A1"]])
        self.assertIsNone(self.sheet.written)

    def test_reads_a_table(self):
        self.sheet.Range("A1:B2").stored = (("Region", "Units"), ("EU", 3.0))
        self.assertEqual(office_live.live_read_range("Book1", "A1:B2")["rows"], [["Region", "Units"], ["EU", 3.0]])

    def test_a_single_cell_is_still_a_table(self):
        self.sheet.Range("A1").stored = "only"
        self.assertEqual(office_live.live_read_range("Book1", "A1")["rows"], [["only"]])

    def test_the_sheet_can_come_from_the_address(self):
        self.sheet.Range("A1").stored = 1
        self.assertEqual(office_live.live_read_range("Book1", "'Data'!A1")["sheet"], "Data")

    def test_a_huge_range_is_cut_off_and_says_so(self):
        big = self.sheet.ranges["A:A"] = FakeRange(self.sheet, "A:A", rows=1048576)
        big.Rows.Count = 1048576
        result = office_live.live_read_range("Book1", "A:A")
        self.assertTrue(result["truncated"])


class PivotTests(LiveTestCase):
    def setUp(self):
        super().setUp()
        self.workbook = fake_workbook("Book1")
        self.sheet = self.workbook.Worksheets[0]
        self.sheet.Range("A1:C1").stored = (("Region", "Model", "Units"),)
        source = self.sheet.ranges["A1:C9"] = FakeRange(self.sheet, "A1:C9", rows=9, columns=3)
        source.Rows.Count = 9
        self.sheet.Range = self.with_header(self.sheet.Range)
        self.table = self.workbook.PivotCaches.return_value.Create.return_value.CreatePivotTable.return_value
        self.fields = {}
        self.table.PivotFields.side_effect = lambda name: self.fields.setdefault(name, MagicMock())
        self.connect_to(fake_excel(self.workbook))

    def with_header(self, original):
        def lookup(first, last=None):
            if last is not None:
                header = original("A1:C1")
                return header
            return original(first)
        return lookup

    def pivot(self, **overrides):
        arguments = {"source": "Data!A1:C9", "destination": "Pivot!A3", "rows": ["Region"], "columns": ["Model"],
                     "values": [{"field": "Units", "function": "sum"}]}
        return office_live.live_create_pivot("Book1", **{**arguments, **overrides})

    def test_builds_a_real_pivot_table(self):
        self.pivot()
        create = self.workbook.PivotCaches.return_value.Create
        self.assertEqual(create.call_args.args[0], office_live.XL_DATABASE)
        self.assertEqual(self.fields["Region"].Orientation, office_live.XL_ROW_FIELD)
        self.assertEqual(self.fields["Model"].Orientation, office_live.XL_COLUMN_FIELD)
        self.table.AddDataField.assert_called_once_with(self.fields["Units"], "Sum of Units", office_live.PIVOT_FUNCTIONS["sum"])

    def test_the_destination_sheet_is_made(self):
        self.pivot()
        self.assertEqual(self.workbook.Worksheets[-1].Name, "Pivot")

    def test_a_field_that_isnt_in_the_header_changes_nothing(self):
        with self.assertRaisesRegex(OfficeAutomationError, "Sales"):
            self.pivot(values=[{"field": "Sales", "function": "sum"}])
        self.workbook.PivotCaches.assert_not_called()
        self.assertEqual(len(self.workbook.Worksheets), 1)

    def test_an_unknown_function_changes_nothing(self):
        with self.assertRaises(OfficeAutomationError):
            self.pivot(values=[{"field": "Units", "function": "median"}])
        self.workbook.PivotCaches.assert_not_called()

    def test_a_destination_with_data_is_refused(self):
        self.sheet.Range("B2").stored = "keep me"
        with self.assertRaisesRegex(OfficeAutomationError, "already"):
            self.pivot(destination="Data!B2")
        self.workbook.PivotCaches.assert_not_called()


class SaveTests(LiveTestCase):
    def test_saves_in_place(self):
        workbook = fake_workbook("C:\\folder\\book.xlsx")
        self.connect_to(fake_excel(workbook))
        office_live.live_save("excel", "book.xlsx")
        workbook.Save.assert_called_once()

    def test_a_new_workbook_needs_a_path(self):
        self.connect_to(fake_excel(fake_workbook("Book1")))
        with self.assertRaisesRegex(OfficeAutomationError, "path"):
            office_live.live_save("excel", "Book1")

    def test_a_new_workbook_is_saved_to_its_path(self):
        workbook = fake_workbook("Book1")
        self.connect_to(fake_excel(workbook))
        target = self.folder / "new.xlsx"
        office_live.live_save("excel", "Book1", str(target))
        workbook.SaveAs.assert_called_once_with(str(target.resolve()))

    def test_saving_over_another_file_is_refused(self):
        target = self.folder / "other.xlsx"
        target.write_text("x")
        workbook = fake_workbook("Book1")
        self.connect_to(fake_excel(workbook))
        with self.assertRaisesRegex(OfficeAutomationError, "already exists"):
            office_live.live_save("excel", "Book1", str(target))
        workbook.SaveAs.assert_not_called()

    def test_a_refused_path_is_not_saved_to(self):
        workbook = fake_workbook("Book1")
        self.connect_to(fake_excel(workbook))
        with self.assertRaises(OfficeAutomationError):
            office_live.live_save("excel", "Book1", str(self.folder / "blocked.xlsx"))
        workbook.SaveAs.assert_not_called()

    def test_a_read_only_copy_is_a_plain_error(self):
        workbook = fake_workbook("C:\\folder\\book.xlsx")
        workbook.ReadOnly = True
        self.connect_to(fake_excel(workbook))
        with self.assertRaisesRegex(OfficeAutomationError, "read-only"):
            office_live.live_save("excel", "book.xlsx")
        workbook.Save.assert_not_called()

    def test_writing_never_saves(self):
        workbook = fake_workbook("C:\\folder\\book.xlsx")
        self.connect_to(fake_excel(workbook))
        office_live.live_write_range("book.xlsx", "A1", [["a"]])
        workbook.Save.assert_not_called()


class BusyTests(LiveTestCase):
    def test_a_rejected_call_becomes_a_plain_message(self):
        for code in office_live.BUSY_CODES:
            with self.subTest(code=code):
                def rejected(*_args):
                    raise pythoncom.com_error(code, "Call was rejected", None, None)

                with patch("automation.office_live.live_open", rejected), self.assertRaisesRegex(OfficeAutomationError, "busy"):
                    office_live.run("live_open", "excel")

    def test_excel_can_report_busy_inside_the_exception_details(self):
        def rejected(*_args):
            details = (0, "Excel", "Call rejected", None, 0, office_live.BUSY_CODES[0])
            raise pythoncom.com_error(office_live.DISP_E_EXCEPTION, "Exception occurred.", details, None)

        with patch("automation.office_live.live_open", rejected), self.assertRaisesRegex(OfficeAutomationError, "busy"):
            office_live.run("live_open", "excel")

    def test_office_s_own_explanation_is_passed_on(self):
        def refused(*_args):
            details = (0, "Word", "This command is not available because no document is open.", None, 0, -2146824040)
            raise pythoncom.com_error(office_live.DISP_E_EXCEPTION, "Exception occurred.", details, None)

        with patch("automation.office_live.live_open", refused), self.assertRaisesRegex(OfficeAutomationError, "no document is open"):
            office_live.run("live_open", "word")

    def test_a_failure_keeps_no_com_objects_alive_after_com_closes(self):
        class Tracked:
            pass

        alive = []
        released = []

        def failing(*_args):
            app = Tracked()
            alive.append(weakref.ref(app))
            raise OfficeAutomationError("no")

        with patch("automation.office_live.live_open", failing), \
                patch.object(office_live.pythoncom, "CoUninitialize", side_effect=lambda: released.append(alive[0]() is None)):
            with self.assertRaises(OfficeAutomationError):
                office_live.run("live_open", "excel")
        self.assertEqual(released, [True])

    def test_a_badly_shaped_argument_is_a_plain_error(self):
        with patch("automation.office_live.live_open", side_effect=AttributeError("'str' object has no attribute 'get'")), \
                self.assertRaises(OfficeAutomationError):
            office_live.run("live_open", "excel")

    def test_com_is_set_up_and_released_on_the_same_call(self):
        with patch.object(office_live.pythoncom, "CoInitialize") as start, patch.object(office_live.pythoncom, "CoUninitialize") as stop, \
                patch("automation.office_live.live_open", return_value={}):
            office_live.run("live_open", "excel")
        start.assert_called_once()
        stop.assert_called_once()


def fake_word(*documents):
    word = MagicMock()
    word.Documents = Collection(documents, make=fake_document)
    word.Quit.side_effect = fail_if_called
    return word


def fake_document(full_name):
    document = fake_workbook(full_name)
    document.Content.Text = "Dear team\r"
    return document


class WordTests(LiveTestCase):
    def setUp(self):
        super().setUp()
        self.document = fake_document("Document1")
        self.connect_to(fake_word(self.document))

    def test_reads_the_open_document(self):
        self.assertEqual(office_live.live_read("word", "Document1")["text"], "Dear team\r")

    def test_inserts_at_the_end(self):
        office_live.live_edit("word", "Document1", [{"action": "insert", "text": "Hello"}])
        self.document.Content.InsertAfter.assert_called_once_with("Hello")

    def test_inserts_at_the_start(self):
        office_live.live_edit("word", "Document1", [{"action": "insert", "text": "Hello", "where": "start"}])
        self.document.Content.InsertBefore.assert_called_once_with("Hello")

    def test_replaces_every_match(self):
        self.document.Content.Find.Execute.return_value = True
        office_live.live_edit("word", "Document1", [{"action": "replace", "find": "team", "replace": "all"}])
        arguments = self.document.Content.Find.Execute.call_args.args
        self.assertEqual((arguments[0], arguments[9], arguments[10]), ("team", "all", office_live.WD_REPLACE_ALL))

    def test_a_replace_that_finds_nothing_says_so(self):
        self.document.Content.Find.Execute.return_value = False
        with self.assertRaisesRegex(OfficeAutomationError, "team"):
            office_live.live_edit("word", "Document1", [{"action": "replace", "find": "team", "replace": "all"}])

    def test_a_bad_edit_stops_the_whole_batch_first(self):
        with self.assertRaises(OfficeAutomationError):
            office_live.live_edit("word", "Document1", [{"action": "insert", "text": "a"}, {"action": "explode"}])
        self.document.Content.InsertAfter.assert_not_called()

    def test_saves_with_word_s_own_call(self):
        target = self.folder / "letter.docx"
        office_live.live_save("word", "Document1", str(target))
        self.document.SaveAs2.assert_called_once_with(str(target.resolve()))


def fake_slide(*texts):
    slide = MagicMock()
    shapes = []
    for text in texts:
        shape = MagicMock(HasTextFrame=True)
        shape.TextFrame.TextRange.Text = text
        shapes.append(shape)
    slide.Shapes = Collection(shapes)
    slide.Shapes.__call__ = None
    return slide


class SlideList(list):
    """COM collections are called with a 1-based index, and PowerPoint slides are too."""

    def __call__(self, index):
        return self[index - 1]


def fake_powerpoint():
    powerpoint = MagicMock()
    presentation = fake_workbook("Deck1")
    slide = fake_slide("Title", "Body")
    slide.Shapes = SlideList(slide.Shapes)
    presentation.Slides = SlideList([slide])
    added = fake_slide("", "")
    added.Shapes = SlideList(added.Shapes)

    def add_slide(_index, _layout):
        presentation.Slides.append(added)
        return added

    presentation.Slides_add = add_slide
    powerpoint.Presentations = Collection([presentation], make=fake_workbook)
    powerpoint.Quit.side_effect = fail_if_called
    return powerpoint, presentation, added


class PowerPointTests(LiveTestCase):
    def setUp(self):
        super().setUp()
        self.powerpoint, self.presentation, self.added = fake_powerpoint()
        self.connect_to(self.powerpoint)

    def test_reads_every_slide(self):
        self.assertEqual(office_live.live_read("powerpoint", "Deck1")["slides"], [{"slide": 1, "text": ["Title", "Body"]}])

    def test_sets_a_shape_s_text(self):
        office_live.live_edit("powerpoint", "Deck1", [{"action": "set_text", "slide": 1, "shape": 2, "text": "New"}])
        self.assertEqual(self.presentation.Slides(1).Shapes(2).TextFrame.TextRange.Text, "New")

    def test_a_slide_that_doesnt_exist_changes_nothing(self):
        with self.assertRaisesRegex(OfficeAutomationError, "slide 5"):
            office_live.live_edit("powerpoint", "Deck1", [{"action": "set_text", "slide": 5, "shape": 1, "text": "x"}])

    def test_adds_a_slide(self):
        with patch("automation.office_live._add_slide", side_effect=lambda presentation: self.presentation.Slides_add(1, None)):
            office_live.live_edit("powerpoint", "Deck1", [{"action": "add_slide", "title": "T", "body": "B"}])
        self.assertEqual(self.added.Shapes(1).TextFrame.TextRange.Text, "T")
        self.assertEqual(self.added.Shapes(2).TextFrame.TextRange.Text, "B")


class OldComPathTests(unittest.TestCase):
    def test_the_quitting_path_gets_its_own_word(self):
        """It ends with Quit, so sharing the owner's running Word would close their documents."""
        fake_client = MagicMock()
        with patch.dict("sys.modules", {"win32com": MagicMock(client=fake_client), "win32com.client": fake_client}):
            OfficeAutomation()._read_doc_com(Path("a.doc"))
        fake_client.DispatchEx.assert_called_once_with("Word.Application")
        fake_client.Dispatch.assert_not_called()


class SkillTests(unittest.TestCase):
    def test_reading_is_free_and_changing_asks(self):
        self.assertEqual(OfficeLiveReadRangeSkill.kind, "read")
        self.assertEqual(OfficeLiveWriteRangeSkill.kind, "change")

    def test_the_card_says_what_will_change(self):
        summary = OfficeLiveWriteRangeSkill().approval_summary({"workbook": "sales.xlsx", "start": "B2", "rows": [[1], [2]]})
        self.assertIn("2 rows", summary)
        self.assertIn("sales.xlsx", summary)
        self.assertIn("Region", OfficeLiveCreatePivotSkill().approval_summary({"workbook": "b", "rows": ["Region"], "values": []}))

    def test_a_silent_office_app_doesnt_hang_the_chat(self):
        def stuck(*_args, **_kwargs):
            import time
            time.sleep(0.2)

        with patch("skills.office_live.run", stuck), patch("skills.office_live.TIMEOUT_SECONDS", 0.01), \
                patch("skills.office_live.load_automation_policy", return_value={"automation_enabled": True}):
            result = asyncio.run(OfficeLiveReadRangeSkill().execute(workbook="b"))
        self.assertFalse(result.success)
        self.assertIn("dialog", result.error)
        self.assertIn("may still", result.error)

    def test_automation_off_stops_it(self):
        with patch("skills.office_live.load_automation_policy", return_value={"automation_enabled": False}):
            result = asyncio.run(OfficeLiveReadRangeSkill().execute(workbook="b"))
        self.assertFalse(result.success)


if __name__ == "__main__":
    unittest.main()
