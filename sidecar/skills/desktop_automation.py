"""Desktop automation skills for Tesseract - find_window, click, type, get_elements, screenshot, ocr."""

import asyncio
import logging
import time
from typing import Any

from skills.skill_base import ScreenshotSkill, SkillBase, SkillResult
from automation.orchestrator import get_automation_orchestrator
from automation.policy import load_automation_policy

logger = logging.getLogger(__name__)

WAIT_POLL = 0.25
MAX_WAIT_SECONDS = 60


def _session() -> Any:
    """The orchestrator with a desktop session running, so a handle from opening an app works straight away."""
    orchestrator = get_automation_orchestrator()
    if not orchestrator.desktop_active:
        policy = load_automation_policy()
        if not policy["automation_enabled"]:
            raise RuntimeError("Automation is disabled in settings")
        orchestrator.start_desktop(ocr_enabled=policy["ocr_enabled"])
    return orchestrator


class DesktopAutomationFindWindowSkill(SkillBase):
    """Skill to find and activate a window by title, class, or process name."""

    # A change: finding a window also brings it to the front.
    approval_title = "Find and switch to a window"

    @property
    def name(self) -> str:
        return "desktop_automation.find_window"

    @property
    def description(self) -> str:
        return "Find and activate a window by title, class name, or process name. Starts desktop automation session if not already running."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "title": {
                    "type": "string",
                    "description": "Window title to search for (supports partial match/regex)",
                },
                "class_name": {
                    "type": "string",
                    "description": "Window class name to search for",
                },
                "process_name": {
                    "type": "string",
                    "description": "Process name to search for (e.g., 'notepad.exe')",
                },
            },
        }

    async def execute(self, **kwargs: Any) -> SkillResult:
        title = kwargs.get("title", "")
        class_name = kwargs.get("class_name")
        process_name = kwargs.get("process_name")

        if not title and not class_name and not process_name:
            return SkillResult.failure("At least one of title, class_name, or process_name is required")

        try:
            policy = load_automation_policy()
            if not policy["automation_enabled"]:
                return SkillResult.failure("Automation is disabled in settings")
            orchestrator = get_automation_orchestrator()
            if not orchestrator.desktop_active:
                orchestrator.start_desktop(ocr_enabled=policy["ocr_enabled"])

            result = orchestrator.execute_desktop_action("find_window", title=title, class_name=class_name, process_name=process_name)
            if result.get("success"):
                return SkillResult.success(result)
            return SkillResult.failure(result.get("error", "Find window failed"))
        except Exception as e:
            logger.exception("Error finding window")
            return SkillResult.failure(f"Error finding window: {e}")


class DesktopAutomationFocusWindowSkill(SkillBase):
    """Skill to focus a window by handle."""

    approval_title = "Bring a window to the front"

    @property
    def name(self) -> str:
        return "desktop_automation.focus_window"

    @property
    def description(self) -> str:
        return "Focus a window by its handle (HWND)."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "handle": {
                    "type": "integer",
                    "description": "Window handle (HWND) to focus. If not provided, focuses the current window.",
                },
            },
        }

    async def execute(self, **kwargs: Any) -> SkillResult:
        handle = kwargs.get("handle")

        try:
            orchestrator = get_automation_orchestrator()
            if not orchestrator.desktop_active:
                return SkillResult.failure("Desktop automation not started. Call desktop_automation.find_window first.")

            result = orchestrator.execute_desktop_action("focus_window", handle=handle)
            if result.get("success"):
                return SkillResult.success(result)
            return SkillResult.failure(result.get("error", "Focus window failed"))
        except Exception as e:
            logger.exception("Error focusing window")
            return SkillResult.failure(f"Error focusing window: {e}")


class DesktopAutomationGetElementsSkill(SkillBase):
    """Skill to get the accessibility tree for the current window."""

    kind = "read"

    @property
    def name(self) -> str:
        return "desktop_automation.get_elements"

    @property
    def description(self) -> str:
        return "Get the UI accessibility tree for the current window (up to specified depth)."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "max_depth": {
                    "type": "integer",
                    "description": "Maximum depth of the element tree (default: 3)",
                    "default": 3,
                },
                "include_children": {
                    "type": "boolean",
                    "description": "Whether to include child elements (default: true)",
                    "default": True,
                },
            },
        }

    async def execute(self, **kwargs: Any) -> SkillResult:
        max_depth = kwargs.get("max_depth", 3)
        include_children = kwargs.get("include_children", True)

        try:
            orchestrator = get_automation_orchestrator()
            if not orchestrator.desktop_active:
                return SkillResult.failure("Desktop automation not started. Call desktop_automation.find_window first.")

            result = orchestrator.execute_desktop_action("get_ui_elements", max_depth=max_depth, include_children=include_children)
            if result.get("success"):
                return SkillResult.success(result)
            return SkillResult.failure(result.get("error", "Get elements failed"))
        except Exception as e:
            logger.exception("Error getting UI elements")
            return SkillResult.failure(f"Error getting UI elements: {e}")


class DesktopAutomationClickSkill(SkillBase):
    """Skill to click a UI element by name, type, automation_id, or coordinates."""

    approval_title = "Click in an app"

    @property
    def name(self) -> str:
        return "desktop_automation.click"

    @property
    def description(self) -> str:
        return "Click a UI element by name, control type, automation ID, or screen coordinates."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Name/title of the element to click",
                },
                "control_type": {
                    "type": "string",
                    "description": "Control type of the element (e.g., 'Button', 'Edit', 'ComboBox')",
                },
                "automation_id": {
                    "type": "string",
                    "description": "Automation ID of the element",
                },
                "coordinates": {
                    "type": "array",
                    "items": {"type": "integer"},
                    "minItems": 2,
                    "maxItems": 2,
                    "description": "Screen coordinates [x, y] to click (fallback to PyAutoGUI)",
                },
                "handle": {
                    "type": "integer",
                    "description": "Window handle from opening an app or find_window. Defaults to the current window.",
                },
            },
        }

    async def execute(self, **kwargs: Any) -> SkillResult:
        name = kwargs.get("name")
        control_type = kwargs.get("control_type")
        automation_id = kwargs.get("automation_id")
        coordinates = kwargs.get("coordinates")

        if not name and not control_type and not automation_id and not coordinates:
            return SkillResult.failure("At least one search criterion (name, control_type, automation_id) or coordinates is required")

        try:
            orchestrator = _session()
            result = orchestrator.execute_desktop_action(
                "click",
                handle=kwargs.get("handle"),
                name=name,
                control_type=control_type,
                automation_id=automation_id,
                coordinates=tuple(coordinates) if coordinates else None,
            )
            if result.get("success"):
                return SkillResult.success(result)
            return SkillResult.failure(result.get("error", "Click failed"))
        except Exception as e:
            logger.exception("Error clicking element")
            return SkillResult.failure(f"Error clicking element: {e}")


class DesktopAutomationTypeSkill(SkillBase):
    """Skill to type text into a UI element."""

    approval_title = "Type in an app"

    @property
    def name(self) -> str:
        return "desktop_automation.type"

    @property
    def description(self) -> str:
        return "Type text into a UI element by name, control type, automation ID, or at screen coordinates."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "text": {
                    "type": "string",
                    "description": "Text to type",
                },
                "name": {
                    "type": "string",
                    "description": "Name/title of the element to type into",
                },
                "control_type": {
                    "type": "string",
                    "description": "Control type of the element (e.g., 'Edit', 'Text')",
                },
                "automation_id": {
                    "type": "string",
                    "description": "Automation ID of the element",
                },
                "coordinates": {
                    "type": "array",
                    "items": {"type": "integer"},
                    "minItems": 2,
                    "maxItems": 2,
                    "description": "Screen coordinates [x, y] to click before typing (fallback to PyAutoGUI)",
                },
                "handle": {
                    "type": "integer",
                    "description": "Window handle from opening an app or find_window. Defaults to the current window.",
                },
            },
            "required": ["text"],
        }

    async def execute(self, **kwargs: Any) -> SkillResult:
        text = kwargs.get("text", "")
        name = kwargs.get("name")
        control_type = kwargs.get("control_type")
        automation_id = kwargs.get("automation_id")
        coordinates = kwargs.get("coordinates")

        if not text:
            return SkillResult.failure("text is required")

        if not name and not control_type and not automation_id and not coordinates:
            return SkillResult.failure("At least one search criterion (name, control_type, automation_id) or coordinates is required")

        try:
            orchestrator = _session()
            result = orchestrator.execute_desktop_action(
                "type",
                handle=kwargs.get("handle"),
                text=text,
                name=name,
                control_type=control_type,
                automation_id=automation_id,
                coordinates=tuple(coordinates) if coordinates else None,
            )
            if result.get("success"):
                return SkillResult.success(result)
            return SkillResult.failure(result.get("error", "Type failed"))
        except Exception as e:
            logger.exception("Error typing text")
            return SkillResult.failure(f"Error typing text: {e}")


class DesktopAutomationPressKeysSkill(SkillBase):
    """Skill to press keyboard shortcuts in a window."""

    approval_title = "Press keys in an app"

    def approval_summary(self, arguments: dict) -> str:
        return f"Press {arguments.get('keys', '')}"

    @property
    def name(self) -> str:
        return "desktop_automation.press_keys"

    @property
    def description(self) -> str:
        return ("Press a keyboard shortcut or a sequence of keys in the current window, such as 'ctrl+s', 'enter' or 'alt h v'. "
                "Use desktop_automation.type for text.")

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "keys": {
                    "type": "string",
                    "description": "Join keys held together with +, and separate steps with spaces",
                },
                "handle": {
                    "type": "integer",
                    "description": "Window handle from opening an app or find_window. Defaults to the current window.",
                },
            },
            "required": ["keys"],
        }

    async def execute(self, **kwargs: Any) -> SkillResult:
        try:
            result = _session().execute_desktop_action("press_keys", keys=kwargs.get("keys", ""), handle=kwargs.get("handle"))
            if result.get("success"):
                return SkillResult.success(result)
            return SkillResult.failure(result.get("error", "Press keys failed"))
        except Exception as e:
            logger.exception("Error pressing keys")
            return SkillResult.failure(f"Error pressing keys: {e}")


class DesktopAutomationScrollSkill(SkillBase):
    """Skill to scroll in a window."""

    approval_title = "Scroll in an app"

    @property
    def name(self) -> str:
        return "desktop_automation.scroll"

    @property
    def description(self) -> str:
        return "Scroll the current window, or over a named element. Positive amounts scroll up, negative down."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "amount": {
                    "type": "integer",
                    "description": "Wheel clicks; positive scrolls up, negative down",
                },
                "name": {
                    "type": "string",
                    "description": "Name of the element to scroll over",
                },
                "handle": {
                    "type": "integer",
                    "description": "Window handle from opening an app or find_window. Defaults to the current window.",
                },
            },
            "required": ["amount"],
        }

    async def execute(self, **kwargs: Any) -> SkillResult:
        try:
            result = _session().execute_desktop_action("scroll", amount=kwargs.get("amount", 0), name=kwargs.get("name"), handle=kwargs.get("handle"))
            if result.get("success"):
                return SkillResult.success(result)
            return SkillResult.failure(result.get("error", "Scroll failed"))
        except Exception as e:
            logger.exception("Error scrolling")
            return SkillResult.failure(f"Error scrolling: {e}")


class DesktopAutomationWaitForSkill(SkillBase):
    """Skill to wait for a window or element to appear."""

    kind = "read"

    @property
    def name(self) -> str:
        return "desktop_automation.wait_for"

    @property
    def description(self) -> str:
        return "Wait until a window title or an element in the current window appears, for up to 60 seconds."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Name of an element in the current window",
                },
                "title": {
                    "type": "string",
                    "description": "Part of a window title",
                },
                "timeout": {
                    "type": "number",
                    "description": "Seconds to wait (default: 10)",
                    "default": 10,
                },
            },
        }

    async def execute(self, **kwargs: Any) -> SkillResult:
        name = kwargs.get("name")
        title = kwargs.get("title")
        if not name and not title:
            return SkillResult.failure("Either name or title is required")

        timeout = min(kwargs.get("timeout", 10), MAX_WAIT_SECONDS)
        try:
            orchestrator = _session()
            deadline = time.monotonic() + timeout
            while True:
                result = orchestrator.execute_desktop_action("wait_check", name=name, title=title)
                if not result.get("success"):
                    return SkillResult.failure(result.get("error", "Wait failed"))
                if result.get("found"):
                    return SkillResult.success(result)
                if time.monotonic() >= deadline:
                    return SkillResult.failure(f"{name or title} didn't appear within {timeout:g} seconds.")
                await asyncio.sleep(WAIT_POLL)
        except Exception as e:
            logger.exception("Error waiting")
            return SkillResult.failure(f"Error waiting: {e}")


class DesktopAutomationGetTextSkill(SkillBase):
    """Skill to get text from a UI element."""

    kind = "read"

    @property
    def name(self) -> str:
        return "desktop_automation.get_text"

    @property
    def description(self) -> str:
        return "Get text content from a UI element by name, control type, or automation ID."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Name/title of the element",
                },
                "control_type": {
                    "type": "string",
                    "description": "Control type of the element (e.g., 'Text', 'Edit', 'Static')",
                },
                "automation_id": {
                    "type": "string",
                    "description": "Automation ID of the element",
                },
            },
        }

    async def execute(self, **kwargs: Any) -> SkillResult:
        name = kwargs.get("name")
        control_type = kwargs.get("control_type")
        automation_id = kwargs.get("automation_id")

        if not name and not control_type and not automation_id:
            return SkillResult.failure("At least one search criterion (name, control_type, automation_id) is required")

        try:
            orchestrator = get_automation_orchestrator()
            if not orchestrator.desktop_active:
                return SkillResult.failure("Desktop automation not started. Call desktop_automation.find_window first.")

            result = orchestrator.execute_desktop_action(
                "get_text",
                name=name,
                control_type=control_type,
                automation_id=automation_id,
            )
            if result.get("success"):
                return SkillResult.success(result)
            return SkillResult.failure(result.get("error", "Get text failed"))
        except Exception as e:
            logger.exception("Error getting element text")
            return SkillResult.failure(f"Error getting element text: {e}")


class DesktopAutomationScreenshotSkill(ScreenshotSkill):
    """Skill to take a screenshot of the screen, window, or region."""

    @property
    def name(self) -> str:
        return "desktop_automation.screenshot"

    @property
    def description(self) -> str:
        return ("Take a screenshot of the screen, current window, or a specific region. "
                "You see the image only when the owner has turned on screenshot sharing in Settings > Automation.")

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "region": {
                    "type": "array",
                    "items": {"type": "integer"},
                    "minItems": 4,
                    "maxItems": 4,
                    "description": "Region to capture as [left, top, right, bottom]",
                },
                "full_screen": {
                    "type": "boolean",
                    "description": "Capture full screen (default: false)",
                    "default": False,
                },
            },
        }

    async def execute(self, **kwargs: Any) -> SkillResult:
        region = kwargs.get("region")
        full_screen = kwargs.get("full_screen", False)

        try:
            orchestrator = _session()
            result = orchestrator.execute_desktop_action("screenshot", region=tuple(region) if region else None, full_screen=full_screen)
            if result.get("success"):
                return SkillResult.success(result)
            return SkillResult.failure(result.get("error", "Screenshot failed"))
        except Exception as e:
            logger.exception("Error taking screenshot")
            return SkillResult.failure(f"Error taking screenshot: {e}")


class DesktopAutomationOCRSkill(SkillBase):
    """Skill to extract text from screen region using OCR."""

    kind = "read"

    @property
    def name(self) -> str:
        return "desktop_automation.ocr"

    @property
    def description(self) -> str:
        return "Extract text from a screen region using Tesseract OCR. Requires OCR to be enabled in settings."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "region": {
                    "type": "array",
                    "items": {"type": "integer"},
                    "minItems": 4,
                    "maxItems": 4,
                    "description": "Region to OCR as [left, top, right, bottom]",
                },
                "image_base64": {
                    "type": "string",
                    "description": "Base64 encoded image to OCR (alternative to region)",
                },
            },
        }

    async def execute(self, **kwargs: Any) -> SkillResult:
        region = kwargs.get("region")
        image_base64 = kwargs.get("image_base64")

        if not region and not image_base64:
            return SkillResult.failure("Either region or image_base64 is required")

        try:
            orchestrator = get_automation_orchestrator()
            if not orchestrator.desktop_active:
                return SkillResult.failure("Desktop automation not started. Call desktop_automation.find_window first.")

            result = orchestrator.execute_desktop_action("ocr", region=tuple(region) if region else None, image_base64=image_base64)
            if result.get("success"):
                return SkillResult.success(result)
            return SkillResult.failure(result.get("error", "OCR failed"))
        except Exception as e:
            logger.exception("Error running OCR")
            return SkillResult.failure(f"Error running OCR: {e}")


class DesktopAutomationResizeWindowSkill(SkillBase):
    """Skill to resize a window."""

    approval_title = "Resize a window"

    @property
    def name(self) -> str:
        return "desktop_automation.resize_window"

    @property
    def description(self) -> str:
        return "Resize the current window or a window by handle."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "width": {
                    "type": "integer",
                    "description": "New width in pixels",
                },
                "height": {
                    "type": "integer",
                    "description": "New height in pixels",
                },
                "handle": {
                    "type": "integer",
                    "description": "Window handle (HWND). If not provided, resizes current window.",
                },
            },
            "required": ["width", "height"],
        }

    async def execute(self, **kwargs: Any) -> SkillResult:
        width = kwargs.get("width")
        height = kwargs.get("height")
        handle = kwargs.get("handle")

        if width is None or height is None:
            return SkillResult.failure("width and height are required")

        try:
            orchestrator = get_automation_orchestrator()
            if not orchestrator.desktop_active:
                return SkillResult.failure("Desktop automation not started. Call desktop_automation.find_window first.")

            result = orchestrator.execute_desktop_action("resize_window", handle=handle, width=width, height=height)
            if result.get("success"):
                return SkillResult.success(result)
            return SkillResult.failure(result.get("error", "Resize window failed"))
        except Exception as e:
            logger.exception("Error resizing window")
            return SkillResult.failure(f"Error resizing window: {e}")


class DesktopAutomationListWindowsSkill(SkillBase):
    """Skill to list all visible windows."""

    kind = "read"

    @property
    def name(self) -> str:
        return "desktop_automation.list_windows"

    @property
    def description(self) -> str:
        return "List all visible windows with their titles, class names, and process IDs."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {},
        }

    async def execute(self, **kwargs: Any) -> SkillResult:
        try:
            policy = load_automation_policy()
            if not policy["automation_enabled"]:
                return SkillResult.failure("Automation is disabled in settings")
            orchestrator = get_automation_orchestrator()
            if not orchestrator.desktop_active:
                # Can still list windows without an active session
                from automation.desktop import get_desktop_automation
                da = get_desktop_automation()
                result = da.list_windows()
            else:
                result = orchestrator.execute_desktop_action("list_windows")

            if result.get("success"):
                return SkillResult.success(result)
            return SkillResult.failure(result.get("error", "List windows failed"))
        except Exception as e:
            logger.exception("Error listing windows")
            return SkillResult.failure(f"Error listing windows: {e}")


class DesktopAutomationCloseSkill(SkillBase):
    """Skill to close the desktop automation session."""

    # A read: it only ends Tesseract's own desktop session.
    kind = "read"

    @property
    def name(self) -> str:
        return "desktop_automation.close"

    @property
    def description(self) -> str:
        return "Close the desktop automation session and cleanup resources."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {},
        }

    async def execute(self, **kwargs: Any) -> SkillResult:
        try:
            orchestrator = get_automation_orchestrator()
            if orchestrator.desktop_active:
                orchestrator.stop_desktop()
            return SkillResult.success({"status": "closed"})
        except Exception as e:
            logger.exception("Error closing desktop automation")
            return SkillResult.failure(f"Error closing desktop automation: {e}")