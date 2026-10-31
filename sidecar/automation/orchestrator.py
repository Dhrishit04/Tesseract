"""Unified automation orchestrator for Tesseract."""

from enum import Enum
import threading
from typing import Any
from automation.policy import load_automation_policy


class AutomationMode(Enum):
    INACTIVE = "inactive"
    WEB = "web"
    DESKTOP = "desktop"
    OFFICE = "office"


class AutomationOrchestrator:
    """Decides which automation subsystem to use and manages sessions."""

    def __init__(self):
        self.web_automation: Any = None
        self.desktop_automation: Any = None
        self.mode: AutomationMode = AutomationMode.WEB
        self._desktop_lock = threading.RLock()

    async def start_web(self, headless: bool = True, timeout: int = 30000):
        """Start web automation session."""
        policy = load_automation_policy()
        if not policy["automation_enabled"]:
            raise RuntimeError("Automation is disabled in settings")
        from automation.browser import WebAutomation
        self.web_automation = WebAutomation(headless=headless, timeout=timeout)
        await self.web_automation.__aenter__()
        self.mode = AutomationMode.WEB

    async def stop_web(self):
        """Stop web automation session."""
        if self.web_automation:
            await self.web_automation.close()
            self.web_automation = None
        self.mode = AutomationMode.DESKTOP if self.desktop_active else AutomationMode.INACTIVE

    async def execute_web_action(self, action: str, **kwargs) -> dict[str, Any]:
        """Execute web automation action."""
        policy = load_automation_policy()
        if not policy["automation_enabled"]:
            return {"success": False, "error": "Automation is disabled in settings"}
        if not self.web_automation:
            return {"success": False, "error": "Web automation not started. Call start_web first."}

        actions = {
            "navigate": self.web_automation.navigate,
            "click": self.web_automation.click,
            "type": self.web_automation.type,
            "select": self.web_automation.select,
            "extract": self.web_automation.extract,
            "screenshot": self.web_automation.screenshot,
            "get_page_content": self.web_automation.get_page_content,
            "wait_for": self.web_automation.wait_for,
        }

        if action not in actions:
            return {"success": False, "error": f"Unknown action: {action}"}

        try:
            result = await actions[action](**kwargs)
            # Wrap successful results in standard format
            if isinstance(result, dict) and "success" not in result:
                return {"success": True, **result}
            return result
        except Exception as e:
            return {"success": False, "error": str(e)}

    def start_desktop(self, ocr_enabled: bool = False):
        """Start desktop automation session."""
        from automation.desktop import DesktopAutomation
        with self._desktop_lock:
            self.desktop_automation = DesktopAutomation(ocr_enabled=ocr_enabled)
            self.mode = AutomationMode.DESKTOP

    def stop_desktop(self):
        """Stop desktop automation session."""
        with self._desktop_lock:
            if self.desktop_automation:
                self.desktop_automation.close()
                self.desktop_automation = None
            self.mode = AutomationMode.WEB if self.web_active else AutomationMode.INACTIVE

    def execute_desktop_action(self, action: str, **kwargs) -> dict[str, Any]:
        """Execute desktop automation action."""
        with self._desktop_lock:
            policy = load_automation_policy()
            if not policy["automation_enabled"]:
                return {"success": False, "error": "Automation is disabled in settings"}
            if action == "ocr" and not policy["ocr_enabled"]:
                return {"success": False, "error": "OCR is disabled in settings"}
            if not self.desktop_automation:
                return {"success": False, "error": "Desktop automation not started. Call start_desktop first."}

            actions = {
                "find_window": self.desktop_automation.find_window,
                "focus_window": self.desktop_automation.focus_window,
                "get_ui_elements": self.desktop_automation.get_ui_elements,
                "click": self.desktop_automation.click_element,
                "type": self.desktop_automation.type_text,
                "get_text": self.desktop_automation.get_element_text,
                "screenshot": self.desktop_automation.screenshot,
                "ocr": self.desktop_automation.ocr,
                "resize_window": self.desktop_automation.resize_window,
                "list_windows": self.desktop_automation.list_windows,
                "press_keys": self.desktop_automation.press_keys,
                "scroll": self.desktop_automation.scroll,
                "wait_check": self.desktop_automation.wait_check,
            }

            if action not in actions:
                return {"success": False, "error": f"Unknown desktop action: {action}"}

            try:
                result = actions[action](**kwargs)
                if isinstance(result, dict) and "success" not in result:
                    return {"success": True, **result}
                return result
            except Exception as e:
                return {"success": False, "error": str(e)}

    @property
    def web_active(self) -> bool:
        """Check if web automation is active."""
        return self.web_automation is not None

    @property
    def desktop_active(self) -> bool:
        """Check if desktop automation is active."""
        return self.desktop_automation is not None

    def get_status(self) -> dict[str, Any]:
        """Get automation status."""
        status = {
            "mode": self.mode.value,
            "web_active": self.web_active,
            "desktop_active": self.desktop_active,
        }
        if self.desktop_automation:
            status["desktop"] = self.desktop_automation.get_status()
        return status


# Global orchestrator instance
_automation_orchestrator: AutomationOrchestrator = None


def get_automation_orchestrator() -> AutomationOrchestrator:
    """Get or create the global automation orchestrator."""
    global _automation_orchestrator
    if _automation_orchestrator is None:
        _automation_orchestrator = AutomationOrchestrator()
    return _automation_orchestrator