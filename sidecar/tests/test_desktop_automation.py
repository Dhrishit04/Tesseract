"""Keyboard, scrolling, waiting and the check that input only reaches the intended window."""

import asyncio
import itertools
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from automation import desktop
from automation.desktop import DesktopAutomation, parse_keys
from skills.desktop_automation import DesktopAutomationPressKeysSkill, DesktopAutomationScrollSkill, DesktopAutomationWaitForSkill


def engine_on_window(handle=7) -> DesktopAutomation:
    engine = DesktopAutomation()
    engine._window_handle = handle
    engine._current_window = MagicMock()
    engine._current_window.rectangle.return_value = SimpleNamespace(left=0, top=0, right=100, bottom=50)
    return engine


class ParseKeysTests(unittest.TestCase):
    def test_a_combination(self):
        self.assertEqual(parse_keys("Ctrl+Shift+S"), [["ctrl", "shift", "s"]])

    def test_a_sequence(self):
        self.assertEqual(parse_keys("alt h v"), [["alt"], ["h"], ["v"]])

    def test_common_names(self):
        self.assertEqual(parse_keys("control+escape pagedown return"), [["ctrl", "esc"], ["pgdn"], ["enter"]])

    def test_unknown_keys_are_refused(self):
        with self.assertRaises(ValueError):
            parse_keys("ctrl+nonsense")

    def test_empty_is_refused(self):
        with self.assertRaises(ValueError):
            parse_keys("  ")

    def test_system_combinations_are_refused(self):
        for keys in ("ctrl+alt+delete", "alt+ctrl+del", "win+l", "L+WINDOWS", "winleft+l", "winright+l"):
            with self.subTest(keys=keys), self.assertRaises(ValueError):
                parse_keys(keys)


@patch.object(desktop, "PYWINAUTO_AVAILABLE", True)
@patch.object(desktop, "PYAUTOGUI_AVAILABLE", True)
@patch.object(desktop, "window_exists", return_value=True)
class TargetWindowTests(unittest.TestCase):
    def setUp(self):
        self.gui = MagicMock(KEYBOARD_KEYS=desktop.pyautogui.KEYBOARD_KEYS)
        patcher = patch.object(desktop, "pyautogui", self.gui)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_keys_go_to_the_window_in_front(self, _exists):
        with patch.object(desktop, "is_in_front", return_value=True):
            result = engine_on_window().press_keys("ctrl+s")
        self.assertTrue(result["success"])
        self.gui.hotkey.assert_called_once_with("ctrl", "s")

    def test_focus_is_taken_back_when_it_moved(self, _exists):
        # An approval card puts Tesseract in front just before every action.
        engine = engine_on_window()
        with patch.object(desktop, "is_in_front", side_effect=[False, False, True]), patch.object(desktop.time, "sleep"):
            self.assertTrue(engine.press_keys("enter")["success"])
        engine._current_window.set_focus.assert_called_once()
        self.gui.hotkey.assert_called_once_with("enter")

    def test_nothing_is_sent_when_the_window_cant_come_back(self, _exists):
        with patch.object(desktop, "is_in_front", return_value=False), patch.object(desktop.time, "sleep"):
            result = engine_on_window().press_keys("ctrl+s")
        self.assertFalse(result["success"])
        self.assertIn("nothing was sent", result["error"])
        self.gui.hotkey.assert_not_called()

    def test_every_step_of_a_sequence_is_checked(self, _exists):
        with patch.object(desktop, "is_in_front", side_effect=itertools.chain([True], itertools.repeat(False))), patch.object(desktop.time, "sleep"):
            result = engine_on_window().press_keys("alt h")
        self.assertFalse(result["success"])
        self.gui.hotkey.assert_called_once_with("alt")

    def test_coordinates_need_a_chosen_window(self, _exists):
        engine = DesktopAutomation()
        self.assertFalse(engine.click_element(coordinates=(1, 2))["success"])
        self.assertFalse(engine.type_text("secret", coordinates=(1, 2))["success"])
        self.gui.click.assert_not_called()

    def test_a_point_over_another_window_is_refused(self, _exists):
        engine = engine_on_window()
        with patch.object(desktop, "is_in_front", return_value=True), patch.object(desktop, "owns_point", return_value=False):
            typed = engine.type_text("secret", coordinates=(1, 2))
            clicked = engine.click_element(coordinates=(1, 2))
        self.assertFalse(typed["success"])
        self.assertFalse(clicked["success"])
        self.gui.click.assert_not_called()

    def test_focus_is_checked_again_after_the_click_before_typing(self, _exists):
        engine = engine_on_window()
        answers = itertools.chain([True], itertools.repeat(False))
        with patch.object(desktop, "is_in_front", side_effect=answers), patch.object(desktop, "owns_point", return_value=True), \
                patch.object(desktop.time, "sleep"), patch.object(desktop, "send_keys", create=True) as send:
            result = engine.type_text("secret", coordinates=(1, 2))
        self.assertFalse(result["success"])
        send.assert_not_called()

    def test_typed_text_keeps_symbols_new_lines_and_accents(self, _exists):
        engine = engine_on_window()
        with patch.object(desktop, "is_in_front", return_value=True), patch.object(desktop, "owns_point", return_value=True), \
                patch.object(desktop.time, "sleep"), patch.object(desktop, "send_keys", create=True) as send:
            engine.type_text("50% (\u00e9)\nnext", coordinates=(1, 2))
        send.assert_called_once_with("50{%} {(}\u00e9{)}\nnext", with_spaces=True, with_tabs=True, with_newlines=True)

    def test_a_window_screenshot_shows_the_chosen_window(self, _exists):
        engine = engine_on_window()
        with patch.object(desktop, "is_in_front", return_value=False), patch.object(desktop.time, "sleep"), \
                patch.object(desktop, "ImageGrab") as grab:
            result = engine.screenshot()
        self.assertFalse(result["success"])
        grab.grab.assert_not_called()

    def test_typing_and_clicking_stop_on_the_wrong_window(self, _exists):
        engine = engine_on_window()
        with patch.object(desktop, "is_in_front", return_value=False), patch.object(desktop.time, "sleep"):
            typed = engine.type_text("secret", coordinates=(1, 2))
            clicked = engine.click_element(coordinates=(1, 2))
        self.assertFalse(typed["success"])
        self.assertFalse(clicked["success"])
        self.gui.write.assert_not_called()
        self.gui.click.assert_not_called()

    def test_a_closed_window_is_reported(self, exists):
        exists.return_value = False
        with patch.object(desktop, "is_in_front", return_value=False):
            result = engine_on_window().press_keys("enter")
        self.assertIn("closed", result["error"])
        self.gui.hotkey.assert_not_called()

    def test_keys_need_a_chosen_window(self, _exists):
        result = DesktopAutomation().press_keys("enter")
        self.assertFalse(result["success"])
        self.gui.hotkey.assert_not_called()

    def test_a_handle_selects_that_window(self, _exists):
        engine = DesktopAutomation()
        fake_desktop = MagicMock()
        with patch.object(desktop, "Desktop", return_value=fake_desktop), patch.object(desktop, "is_in_front", return_value=True):
            engine.press_keys("enter", handle=9)
        fake_desktop.window.assert_called_once_with(handle=9)
        self.assertEqual(engine._window_handle, 9)

    def test_scroll_lands_in_the_middle_of_the_window(self, _exists):
        with patch.object(desktop, "is_in_front", return_value=True):
            engine_on_window().scroll(-3)
        self.gui.scroll.assert_called_once_with(-3, 50, 25)


@patch.object(desktop, "PYWINAUTO_AVAILABLE", True)
class FindWindowTests(unittest.TestCase):
    def fake_window(self, handle, title):
        window = MagicMock(handle=handle)
        window.window_text.return_value = title
        window.rectangle.return_value = SimpleNamespace(left=0, top=0, right=1, bottom=1)
        return window

    def find(self, title, windows):
        fake_desktop = MagicMock()
        fake_desktop.windows.return_value = windows
        engine = DesktopAutomation()
        with patch.object(desktop, "Desktop", return_value=fake_desktop):
            return engine.find_window(title), fake_desktop

    def test_a_title_matches_anywhere_and_in_any_case(self):
        _, fake_desktop = self.find("excel", [self.fake_window(1, "Book1 - Excel")])
        self.assertEqual(fake_desktop.windows.call_args.kwargs["title_re"], "(?i).*excel.*")

    def test_text_that_isnt_a_valid_pattern_is_matched_literally(self):
        _, fake_desktop = self.find("Report (final", [self.fake_window(1, "Report (final")])
        self.assertEqual(fake_desktop.windows.call_args.kwargs["title_re"], r"(?i).*Report\ \(final.*")

    def test_the_window_comes_to_the_front(self):
        window = self.fake_window(1, "Notepad")
        self.find("Notepad", [window])
        window.set_focus.assert_called_once()

    def test_same_named_windows_are_all_reported(self):
        result, _ = self.find("Budget", [self.fake_window(1, "Budget - Excel"), self.fake_window(2, "Budget - Word")])
        self.assertEqual(result["window"]["handle"], 1)
        self.assertEqual(result["other_matches"], [{"handle": 2, "title": "Budget - Word"}])


class WaitCheckTests(unittest.TestCase):
    @patch.object(desktop, "PYWINAUTO_AVAILABLE", True)
    def test_finds_an_element(self):
        engine = engine_on_window()
        engine._current_window.child_window.return_value.exists.return_value = True
        self.assertTrue(engine.wait_check(name="Save")["found"])

    @patch.object(desktop, "PYWINAUTO_AVAILABLE", True)
    def test_finds_a_window_title(self):
        fake_desktop = MagicMock()
        fake_desktop.windows.return_value = []
        with patch.object(desktop, "Desktop", return_value=fake_desktop):
            self.assertFalse(DesktopAutomation().wait_check(title="Save As")["found"])


class SkillTests(unittest.TestCase):
    def setUp(self):
        self.orchestrator = MagicMock(desktop_active=True)
        self.addCleanup(patch.stopall)
        patch("skills.desktop_automation.get_automation_orchestrator", return_value=self.orchestrator).start()
        patch("skills.desktop_automation.load_automation_policy", return_value={"automation_enabled": True, "ocr_enabled": False}).start()

    def test_wait_for_gives_up_in_plain_words(self):
        self.orchestrator.execute_desktop_action.return_value = {"success": True, "found": False}
        result = asyncio.run(DesktopAutomationWaitForSkill().execute(name="Save", timeout=0))
        self.assertFalse(result.success)
        self.assertIn("didn't appear", result.error)

    def test_wait_for_returns_once_found(self):
        self.orchestrator.execute_desktop_action.side_effect = [{"success": True, "found": False}, {"success": True, "found": True}]
        with patch("skills.desktop_automation.WAIT_POLL", 0):
            result = asyncio.run(DesktopAutomationWaitForSkill().execute(title="Save As", timeout=5))
        self.assertTrue(result.success)

    def test_wait_for_needs_something_to_wait_for(self):
        self.assertFalse(asyncio.run(DesktopAutomationWaitForSkill().execute()).success)

    def test_wait_for_only_reads(self):
        self.assertEqual(DesktopAutomationWaitForSkill.kind, "read")

    def test_keys_and_scrolling_ask_first(self):
        self.assertEqual(DesktopAutomationPressKeysSkill.kind, "change")
        self.assertEqual(DesktopAutomationScrollSkill.kind, "change")
        self.assertIn("ctrl+s", DesktopAutomationPressKeysSkill().approval_summary({"keys": "ctrl+s"}))

    def test_a_handle_from_launch_starts_the_session(self):
        self.orchestrator.desktop_active = False
        self.orchestrator.execute_desktop_action.return_value = {"success": True}
        asyncio.run(DesktopAutomationPressKeysSkill().execute(keys="enter", handle=4))
        self.orchestrator.start_desktop.assert_called_once()


if __name__ == "__main__":
    unittest.main()
