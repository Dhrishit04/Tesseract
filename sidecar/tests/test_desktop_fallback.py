"""Desktop automation fallback chain: accessibility -> OCR -> user guidance."""

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from PIL import Image

from automation import desktop
from automation.desktop import DesktopAutomation


def window_without_element(left: int = 100, top: int = 50) -> MagicMock:
    """A UIA window whose child lookups never find the requested element."""
    window = MagicMock()
    window.child_window.return_value.exists.return_value = False
    window.rectangle.return_value = SimpleNamespace(left=left, top=top, right=left + 400, bottom=top + 300)
    return window


def automation(ocr: bool) -> DesktopAutomation:
    engine = DesktopAutomation(ocr_enabled=False)
    engine.ocr_enabled = ocr
    engine._current_window = window_without_element()
    return engine


@patch.object(desktop, "PYWINAUTO_AVAILABLE", True)
@patch.object(desktop, "ImageGrab", SimpleNamespace(grab=lambda bbox=None: Image.new("RGB", (400, 300))))
class DesktopFallbackTests(unittest.TestCase):
    def test_get_text_falls_back_to_ocr_when_accessibility_misses(self):
        fake_tesseract = SimpleNamespace(image_to_string=lambda _img: "  Invoice total 42  ")
        with patch.object(desktop, "pytesseract", fake_tesseract):
            result = automation(ocr=True).get_element_text(name="Total")

        self.assertTrue(result["success"])
        self.assertEqual(result["method"], "ocr")
        self.assertEqual(result["text"], "Invoice total 42")

    def test_get_text_without_ocr_returns_guidance(self):
        result = automation(ocr=False).get_element_text(name="Total")

        self.assertFalse(result["success"])
        self.assertIn("guidance", result)

    def test_click_falls_back_to_ocr_text_location(self):
        boxes = {
            "text": ["", "Save", "Cancel"],
            "left": [0, 20, 200],
            "top": [0, 10, 10],
            "width": [0, 40, 60],
            "height": [0, 20, 20],
            "conf": ["-1", "91", "88"],
        }
        fake_tesseract = SimpleNamespace(image_to_data=lambda _img, output_type=None: boxes, Output=SimpleNamespace(DICT="dict"))
        fake_gui = MagicMock()
        with patch.object(desktop, "pytesseract", fake_tesseract), \
                patch.object(desktop, "pyautogui", fake_gui), \
                patch.object(desktop, "PYAUTOGUI_AVAILABLE", True):
            result = automation(ocr=True).click_element(name="Save")

        self.assertTrue(result["success"], result)
        self.assertEqual(result["method"], "ocr")
        # window origin (100, 50) + box centre (20 + 40/2, 10 + 20/2)
        fake_gui.click.assert_called_once_with(140, 70)

    def test_click_matches_multi_word_label_with_ocr(self):
        boxes = {
            "text": ["File", "Save", "As", "Cancel"],
            "left": [0, 100, 140, 300],
            "top": [0, 50, 50, 50],
            "width": [30, 36, 20, 60],
            "height": [20, 20, 20, 20],
            "conf": ["90", "91", "93", "88"],
            "block_num": [1, 2, 2, 2], "par_num": [1, 1, 1, 1], "line_num": [1, 1, 1, 1],
        }
        fake_tesseract = SimpleNamespace(image_to_data=lambda _img, output_type=None: boxes, Output=SimpleNamespace(DICT="dict"))
        fake_gui = MagicMock()
        with patch.object(desktop, "pytesseract", fake_tesseract),                 patch.object(desktop, "pyautogui", fake_gui),                 patch.object(desktop, "PYAUTOGUI_AVAILABLE", True):
            result = automation(ocr=True).click_element(name="Save As")

        self.assertTrue(result["success"], result)
        # window origin (100, 50); phrase spans x 100..160, y 50..70 -> centre (130, 60)
        fake_gui.click.assert_called_once_with(230, 110)

    def test_ocr_click_needs_a_selected_window(self):
        engine = automation(ocr=True)
        engine._current_window = None
        boxes = {"text": ["Delete"], "left": [10], "top": [10], "width": [40], "height": [20], "conf": ["95"]}
        fake_tesseract = SimpleNamespace(image_to_data=lambda _img, output_type=None: boxes, Output=SimpleNamespace(DICT="dict"))
        fake_gui = MagicMock()
        with patch.object(desktop, "pytesseract", fake_tesseract),                 patch.object(desktop, "pyautogui", fake_gui),                 patch.object(desktop, "PYAUTOGUI_AVAILABLE", True):
            result = engine.click_element(name="Delete")

        self.assertFalse(result["success"])
        fake_gui.click.assert_not_called()

    def test_ocr_text_needs_a_selected_window(self):
        engine = automation(ocr=True)
        engine._current_window = None
        fake_tesseract = SimpleNamespace(image_to_string=lambda _img: "Total 42")
        with patch.object(desktop, "pytesseract", fake_tesseract):
            result = engine.get_element_text(name="Total")

        self.assertFalse(result["success"])

    def test_click_without_ocr_returns_guidance(self):
        result = automation(ocr=False).click_element(name="Save")

        self.assertFalse(result["success"])
        self.assertIn("guidance", result)


if __name__ == "__main__":
    unittest.main()
