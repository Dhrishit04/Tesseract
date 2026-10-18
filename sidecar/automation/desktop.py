"""Desktop GUI automation for Tesseract using Windows UIA (pywinauto) and OCR fallback."""

import base64
import logging
import subprocess
import time
from dataclasses import dataclass, field
from typing import Any, Optional
from PIL import Image, ImageGrab
import io

# Try to import Windows UIA automation
try:
    import pywinauto
    from pywinauto import Desktop, Application
    from pywinauto.findwindows import ElementNotFoundError
    PYWINAUTO_AVAILABLE = True
except ImportError:
    PYWINAUTO_AVAILABLE = False
    pywinauto = None
    Desktop = None
    Application = None
    ElementNotFoundError = Exception

# Try to import OCR
try:
    import pytesseract
    TESSERACT_AVAILABLE = True
except ImportError:
    TESSERACT_AVAILABLE = False
    pytesseract = None

# Try to import PyAutoGUI for fallback
try:
    import pyautogui
    PYAUTOGUI_AVAILABLE = True
except ImportError:
    PYAUTOGUI_AVAILABLE = False
    pyautogui = None

logger = logging.getLogger(__name__)

PYWINAUTO_MISSING = "pywinauto not available"
NO_WINDOW_SELECTED = "No window selected. Call find_window first."
NO_CRITERIA = "At least one search criterion required"


@dataclass
class UIElement:
    """Represents a UI element from the accessibility tree."""
    name: str
    control_type: str
    rectangle: dict[str, int]  # left, top, right, bottom
    class_name: str
    automation_id: str
    is_enabled: bool
    is_visible: bool
    children: list["UIElement"] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "control_type": self.control_type,
            "rectangle": self.rectangle,
            "class_name": self.class_name,
            "automation_id": self.automation_id,
            "is_enabled": self.is_enabled,
            "is_visible": self.is_visible,
            "children": [c.to_dict() for c in self.children],
        }


@dataclass
class WindowInfo:
    """Information about a window."""
    handle: int
    title: str
    class_name: str
    process_id: int
    rectangle: dict[str, int]
    is_visible: bool
    is_minimized: bool
    is_maximized: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "handle": self.handle,
            "title": self.title,
            "class_name": self.class_name,
            "process_id": self.process_id,
            "rectangle": self.rectangle,
            "is_visible": self.is_visible,
            "is_minimized": self.is_minimized,
            "is_maximized": self.is_maximized,
        }


class DesktopAutomation:
    """Desktop GUI automation engine using Windows UIA with OCR fallback."""

    def __init__(self, ocr_enabled: bool = False):
        self.ocr_enabled = ocr_enabled and TESSERACT_AVAILABLE
        self._app: Optional[Any] = None
        self._current_window: Optional[Any] = None
        self._window_handle: Optional[int] = None

    def find_window(self, title: str, class_name: str = None, process_name: str = None) -> dict[str, Any]:
        """Find and optionally activate a window by title, class, or process name."""
        if not PYWINAUTO_AVAILABLE:
            return {"success": False, "error": "pywinauto not available. Install with: pip install pywinauto"}

        try:
            # Build search criteria.
            criteria = {}
            if title:
                criteria["title_re"] = title
            if class_name:
                criteria["class_name"] = class_name
            if process_name:
                process_ids = self._resolve_process_ids(process_name)
                if not process_ids:
                    return {"success": False, "error": f"No process found matching name: {process_name}"}
                criteria["process"] = process_ids[0]

            if not criteria:
                return {"success": False, "error": "Window selection criteria cannot be empty; provide title, class_name, or process_name."}

            if process_name and len(process_ids) > 1:
                windows = []
                for process_id in process_ids:
                    windows.extend(Desktop(backend="uia").windows(**{**criteria, "process": process_id}))
            else:
                windows = Desktop(backend="uia").windows(**criteria)
            if not windows:
                return {"success": False, "error": f"No window found matching criteria: {criteria}"}

            # Use the first match - get a WindowSpecification for element searching
            window = windows[0]
            handle = window.handle
            self._window_handle = handle

            # Create a WindowSpecification for element searching (this supports child_window)
            self._current_window = Desktop(backend="uia").window(handle=handle)

            # Get window info
            rect = window.rectangle()
            info = WindowInfo(
                handle=handle,
                title=window.window_text(),
                class_name=window.class_name(),
                process_id=window.process_id(),
                rectangle={"left": rect.left, "top": rect.top, "right": rect.right, "bottom": rect.bottom},
                is_visible=window.is_visible(),
                is_minimized=window.is_minimized(),
                is_maximized=window.is_maximized(),
            )

            return {"success": True, "window": info.to_dict()}

        except ElementNotFoundError:
            return {"success": False, "error": f"Window not found: {criteria}"}
        except Exception as e:
            logger.exception("Error finding window")
            return {"success": False, "error": str(e)}

    @staticmethod
    def _resolve_process_ids(process_name: str) -> list[int]:
        """Resolve an executable name to process IDs accepted by pywinauto."""
        result = subprocess.run(
            ["tasklist", "/FI", f"IMAGENAME eq {process_name}", "/FO", "CSV", "/NH"],
            capture_output=True,
            text=True,
            check=True,
        )
        process_ids = []
        for line in result.stdout.splitlines():
            fields = [field.strip('"') for field in line.split('","')]
            if len(fields) >= 2 and fields[1].isdigit():
                process_ids.append(int(fields[1]))
        return process_ids

    def focus_window(self, handle: int = None) -> dict[str, Any]:
        """Focus a window by handle (or current window if not specified)."""
        if not PYWINAUTO_AVAILABLE:
            return {"success": False, "error": PYWINAUTO_MISSING}

        try:
            hwnd = handle or self._window_handle
            if not hwnd:
                return {"success": False, "error": "No window handle specified"}

            window = Desktop(backend="uia").window(handle=hwnd)
            window.set_focus()
            self._current_window = window
            self._window_handle = hwnd

            return {"success": True, "handle": hwnd}

        except Exception as e:
            logger.exception("Error focusing window")
            return {"success": False, "error": str(e)}

    @staticmethod
    def _describe_element(element) -> UIElement:
        rect = element.rectangle()
        return UIElement(
            name=element.window_text() or "",
            control_type=element.element_info.control_type,
            rectangle={"left": rect.left, "top": rect.top, "right": rect.right, "bottom": rect.bottom},
            class_name=element.class_name(),
            automation_id=element.element_info.automation_id or "",
            is_enabled=element.is_enabled(),
            is_visible=element.is_visible(),
        )

    def _build_element_tree(self, element, depth: int, max_depth: int, include_children: bool) -> Optional[UIElement]:
        """Describe element and (optionally) its descendants; unreadable nodes are skipped."""
        try:
            node = self._describe_element(element)
        except Exception:
            return None
        if not include_children or depth >= max_depth:
            return node
        try:
            children = element.children()
        except Exception:
            return node
        for child in children:
            child_node = self._build_element_tree(child, depth + 1, max_depth, include_children)
            if child_node:
                node.children.append(child_node)
        return node

    def get_ui_elements(self, max_depth: int = 3, include_children: bool = True) -> dict[str, Any]:
        """Get the accessibility tree for the current window."""
        if not PYWINAUTO_AVAILABLE:
            return {"success": False, "error": PYWINAUTO_MISSING}
        if not self._current_window:
            return {"success": False, "error": NO_WINDOW_SELECTED}

        root = self._build_element_tree(self._current_window, 0, max_depth, include_children)
        if not root:
            return {"success": False, "error": "Failed to build element tree"}
        return {"success": True, "elements": root.to_dict()}

    @staticmethod
    def _element_criteria(name: str = None, control_type: str = None, automation_id: str = None) -> dict[str, str]:
        criteria = {}
        if name:
            criteria["title"] = name
        if control_type:
            criteria["control_type"] = control_type
        if automation_id:
            criteria["automation_id"] = automation_id
        return criteria

    def _find_element(self, criteria: dict[str, str]) -> Optional[Any]:
        """Return the UIA element for criteria, or None when accessibility can't see it."""
        if not (PYWINAUTO_AVAILABLE and self._current_window and criteria):
            return None
        try:
            element = self._current_window.child_window(**criteria)
            return element if element.exists() else None
        except ElementNotFoundError:
            return None

    def _capture_target(self) -> tuple[Any, tuple[int, int]]:
        """Capture the current window (or whole screen) and its screen origin."""
        if self._current_window is not None:
            rect = self._current_window.rectangle()
            return ImageGrab.grab(bbox=(rect.left, rect.top, rect.right, rect.bottom)), (rect.left, rect.top)
        return ImageGrab.grab(), (0, 0)

    @staticmethod
    def _guidance(reason: str, action: str) -> dict[str, Any]:
        """Last step of the fallback chain: tell the user what to do."""
        return {
            "success": False,
            "error": reason,
            "guidance": (
                f"Tesseract could not {action} automatically. Make sure the window is visible and not minimised, "
                "or enable OCR fallback in Settings > Automation (requires the Tesseract OCR engine). "
                "You can also do this step yourself and ask Tesseract to continue."
            ),
        }

    @staticmethod
    def _ocr_lines(boxes: dict) -> list[list[int]]:
        """Group OCR word indexes into text lines, in reading order."""
        lines: dict[tuple, list[int]] = {}
        for index, word in enumerate(boxes["text"]):
            if not word.strip() or float(boxes["conf"][index]) < 0:
                continue
            key = tuple(boxes.get(field, [0] * len(boxes["text"]))[index] for field in ("block_num", "par_num", "line_num"))
            lines.setdefault(key, []).append(index)
        return list(lines.values())

    def _ocr_locate(self, label: str) -> Optional[tuple[int, int]]:
        """Find the on-screen centre of the first run of OCR words that reads `label`."""
        wanted = label.lower().split()
        if not wanted:
            return None
        image, (origin_x, origin_y) = self._capture_target()
        boxes = pytesseract.image_to_data(image, output_type=pytesseract.Output.DICT)
        for line in self._ocr_lines(boxes):
            words = [boxes["text"][index].strip().lower() for index in line]
            for start in range(len(words) - len(wanted) + 1):
                if words[start:start + len(wanted)] != wanted:
                    continue
                run = line[start:start + len(wanted)]
                left = min(boxes["left"][i] for i in run)
                top = min(boxes["top"][i] for i in run)
                right = max(boxes["left"][i] + boxes["width"][i] for i in run)
                bottom = max(boxes["top"][i] + boxes["height"][i] for i in run)
                return origin_x + (left + right) // 2, origin_y + (top + bottom) // 2
        return None

    def click_element(self, name: str = None, control_type: str = None, automation_id: str = None,
                      coordinates: tuple[int, int] = None) -> dict[str, Any]:
        """Click by coordinates, then accessibility lookup, then OCR text location, then guidance."""
        if not PYWINAUTO_AVAILABLE and not PYAUTOGUI_AVAILABLE:
            return {"success": False, "error": "No automation backend available (need pywinauto or pyautogui)"}

        try:
            if coordinates and PYAUTOGUI_AVAILABLE:
                x, y = coordinates
                pyautogui.click(x, y)
                return {"success": True, "method": "pyautogui", "coordinates": coordinates}

            criteria = self._element_criteria(name, control_type, automation_id)
            if not criteria:
                return {"success": False, "error": NO_CRITERIA}

            element = self._find_element(criteria)
            if element is not None:
                element.click_input()
                return {"success": True, "method": "pywinauto", "criteria": criteria}

            if self._current_window is None:
                return self._guidance(NO_WINDOW_SELECTED, f"click '{name or criteria}'")
            reason = f"Element not found: {criteria}"
            if self.ocr_enabled and name and PYAUTOGUI_AVAILABLE:
                location = self._ocr_locate(name)
                if location:
                    pyautogui.click(*location)
                    return {"success": True, "method": "ocr", "coordinates": location, "note": reason}
                reason = f"{reason}; OCR did not find the text '{name}' either"
            return self._guidance(reason, f"click '{name or criteria}'")

        except Exception as e:
            logger.exception("Error clicking element")
            return {"success": False, "error": str(e)}

    def type_text(self, text: str, name: str = None, control_type: str = None,
                  automation_id: str = None, coordinates: tuple[int, int] = None) -> dict[str, Any]:
        """Type text into a UI element."""
        if not PYWINAUTO_AVAILABLE and not PYAUTOGUI_AVAILABLE:
            return {"success": False, "error": "No automation backend available"}

        try:
            # If coordinates provided, use PyAutoGUI
            if coordinates and PYAUTOGUI_AVAILABLE:
                x, y = coordinates
                pyautogui.click(x, y)
                time.sleep(0.1)
                pyautogui.write(text)
                return {"success": True, "method": "pyautogui", "text": text}

            if not self._current_window:
                return {"success": False, "error": NO_WINDOW_SELECTED}

            criteria = self._element_criteria(name, control_type, automation_id)
            if not criteria:
                return {"success": False, "error": NO_CRITERIA}

            element = self._find_element(criteria)
            if element is None:
                return self._guidance(f"Element not found: {criteria}", "find the field to type into")

            element.set_focus()
            set_edit_text = getattr(element, "set_edit_text", None)
            if callable(set_edit_text):
                set_edit_text(text)
            else:
                escaped_text = text
                for token in ("%", "+", "^", "~", "(", ")", "{", "}"):
                    escaped_text = escaped_text.replace(token, "{" + token + "}")
                element.type_keys(escaped_text, with_spaces=True)
            return {"success": True, "method": "pywinauto", "text": text}

        except Exception as e:
            logger.exception("Error typing text")
            return {"success": False, "error": str(e)}

    def get_element_text(self, name: str = None, control_type: str = None,
                         automation_id: str = None) -> dict[str, Any]:
        """Read text via accessibility, then OCR of the window, then guidance."""
        criteria = self._element_criteria(name, control_type, automation_id)
        if not criteria:
            return {"success": False, "error": NO_CRITERIA}

        try:
            element = self._find_element(criteria)
            if element is not None:
                return {"success": True, "method": "pywinauto", "text": element.window_text()}

            if self._current_window is None:
                return self._guidance(NO_WINDOW_SELECTED, "read that text")
            reason = f"Element not found: {criteria}"
            if not PYWINAUTO_AVAILABLE:
                reason = "Accessibility automation (pywinauto) is not available"

            if self.ocr_enabled:
                image, _ = self._capture_target()
                text = pytesseract.image_to_string(image).strip()
                return {"success": True, "method": "ocr", "text": text, "note": f"{reason}; returned OCR text of the window"}
            return self._guidance(reason, "read that text")

        except Exception as e:
            logger.exception("Error getting element text")
            return {"success": False, "error": str(e)}

    def screenshot(self, region: tuple[int, int, int, int] = None, full_screen: bool = False) -> dict[str, Any]:
        """Take a screenshot of the screen, window, or region."""
        try:
            if full_screen:
                img = ImageGrab.grab()
            elif region:
                img = ImageGrab.grab(bbox=region)
            elif self._current_window and PYWINAUTO_AVAILABLE:
                rect = self._current_window.rectangle()
                img = ImageGrab.grab(bbox=(rect.left, rect.top, rect.right, rect.bottom))
            else:
                img = ImageGrab.grab()

            # Convert to base64
            buffered = io.BytesIO()
            img.save(buffered, format="PNG")
            img_base64 = base64.b64encode(buffered.getvalue()).decode()

            return {
                "success": True,
                "image_base64": img_base64,
                "width": img.width,
                "height": img.height,
                "region": region,
            }

        except Exception as e:
            logger.exception("Error taking screenshot")
            return {"success": False, "error": str(e)}

    def ocr(self, region: tuple[int, int, int, int] = None, image_base64: str = None) -> dict[str, Any]:
        """Extract text from an image region using Tesseract OCR."""
        if not self.ocr_enabled:
            return {"success": False, "error": "OCR not enabled or Tesseract not available"}

        try:
            if image_base64:
                # Decode base64 image
                img_data = base64.b64decode(image_base64)
                img = Image.open(io.BytesIO(img_data))
            elif region:
                img = ImageGrab.grab(bbox=region)
            elif self._current_window and PYWINAUTO_AVAILABLE:
                rect = self._current_window.rectangle()
                img = ImageGrab.grab(bbox=(rect.left, rect.top, rect.right, rect.bottom))
            else:
                img = ImageGrab.grab()

            # Run OCR
            text = pytesseract.image_to_string(img)
            return {"success": True, "text": text.strip()}

        except Exception as e:
            logger.exception("Error running OCR")
            return {"success": False, "error": str(e)}

    def resize_window(self, handle: int = None, width: int = None, height: int = None) -> dict[str, Any]:
        """Resize a window."""
        if not PYWINAUTO_AVAILABLE:
            return {"success": False, "error": PYWINAUTO_MISSING}

        try:
            hwnd = handle or self._window_handle
            if not hwnd:
                return {"success": False, "error": "No window handle specified"}

            # Use win32gui to resize the window directly
            try:
                import win32gui
                import win32con
            except ImportError:
                return {"success": False, "error": "pywin32 not available. Install with: pip install pywin32"}

            if width is None or height is None or width <= 0 or height <= 0:
                return {"success": False, "error": "Window width and height must be positive"}

            rect = win32gui.GetWindowRect(hwnd)
            win32gui.SetWindowPos(hwnd, win32con.HWND_TOP, rect[0], rect[1], width, height, win32con.SWP_NOZORDER | win32con.SWP_NOACTIVATE)
            return {"success": True, "handle": hwnd, "width": width, "height": height}

        except Exception as e:
            logger.exception("Error resizing window")
            return {"success": False, "error": str(e)}

    def list_windows(self) -> dict[str, Any]:
        """List all visible windows."""
        if not PYWINAUTO_AVAILABLE:
            return {"success": False, "error": PYWINAUTO_MISSING}

        try:
            windows = Desktop(backend="uia").windows()
            result = []
            for w in windows:
                try:
                    if w.is_visible():
                        rect = w.rectangle()
                        # Some windows don't support is_minimized/is_maximized
                        try:
                            is_minimized = w.is_minimized()
                        except Exception:
                            is_minimized = False
                        try:
                            is_maximized = w.is_maximized()
                        except Exception:
                            is_maximized = False

                        result.append(WindowInfo(
                            handle=w.handle,
                            title=w.window_text(),
                            class_name=w.class_name(),
                            process_id=w.process_id(),
                            rectangle={"left": rect.left, "top": rect.top, "right": rect.right, "bottom": rect.bottom},
                            is_visible=w.is_visible(),
                            is_minimized=is_minimized,
                            is_maximized=is_maximized,
                        ).to_dict())
                except Exception as e:
                    # Skip windows that can't be queried
                    logger.debug(f"Skipping window {w.handle}: {e}")
                    continue

            return {"success": True, "windows": result}

        except Exception as e:
            logger.exception("Error listing windows")
            return {"success": False, "error": str(e)}

    def get_status(self) -> dict[str, Any]:
        """Get automation status."""
        return {
            "pywinauto_available": PYWINAUTO_AVAILABLE,
            "tesseract_available": TESSERACT_AVAILABLE,
            "pyautogui_available": PYAUTOGUI_AVAILABLE,
            "ocr_enabled": self.ocr_enabled,
            "current_window": self._window_handle,
        }

    def close(self):
        """Cleanup resources."""
        self._current_window = None
        self._window_handle = None
        self._app = None


# Convenience functions for skill integration
_desktop_automation: Optional[DesktopAutomation] = None


def get_desktop_automation(ocr_enabled: bool = False) -> DesktopAutomation:
    """Get or create the global desktop automation instance."""
    global _desktop_automation
    if _desktop_automation is None:
        _desktop_automation = DesktopAutomation(ocr_enabled=ocr_enabled)
    return _desktop_automation


def reset_desktop_automation():
    """Reset the global desktop automation instance."""
    global _desktop_automation
    if _desktop_automation:
        _desktop_automation.close()
    _desktop_automation = None