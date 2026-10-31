"""Web automation skills for Tesseract - navigate, click, type, extract, screenshot, wait."""

import logging
from typing import Any

from skills.skill_base import ScreenshotSkill, SkillBase, SkillResult

logger = logging.getLogger(__name__)

def _get_orchestrator():
    """Use the process-wide orchestrator shared by API and skill calls."""
    from automation.orchestrator import get_automation_orchestrator

    return get_automation_orchestrator()


class WebAutomationNavigateSkill(SkillBase):
    """Skill to navigate to a URL in the browser."""

    # A change: opening a URL can submit a GET form.
    approval_title = "Open a web page"

    @property
    def name(self) -> str:
        return "web_automation.navigate"

    @property
    def description(self) -> str:
        return "Navigate to a URL in the web automation browser. Starts browser session if not already running."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "URL to navigate to (must include http:// or https://)",
                },
                "wait_until": {
                    "type": "string",
                    "description": "When to consider navigation complete",
                    "enum": ["load", "domcontentloaded", "networkidle", "commit"],
                    "default": "networkidle",
                },
                "headless": {
                    "type": "boolean",
                    "description": "Run browser in headless mode (default: true)",
                    "default": True,
                },
            },
            "required": ["url"],
        }

    async def execute(self, **kwargs: Any) -> SkillResult:
        url = kwargs.get("url", "")
        wait_until = kwargs.get("wait_until", "networkidle")
        headless = kwargs.get("headless", True)

        if not url:
            return SkillResult.failure("url is required")

        try:
            orchestrator = _get_orchestrator()
            if not orchestrator.web_automation:
                await orchestrator.start_web(headless=headless)
            result = await orchestrator.execute_web_action("navigate", url=url, wait_until=wait_until)
            if result.get("success"):
                return SkillResult.success(result)
            return SkillResult.failure(result.get("error", "Navigation failed"))
        except Exception as e:
            logger.exception("Error navigating")
            return SkillResult.failure(f"Error navigating: {e}")


class WebAutomationClickSkill(SkillBase):
    """Skill to click an element on the page."""

    approval_title = "Click on a web page"

    @property
    def name(self) -> str:
        return "web_automation.click"

    @property
    def description(self) -> str:
        return "Click an element on the current page using a CSS selector."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "selector": {
                    "type": "string",
                    "description": "CSS selector of the element to click",
                },
            },
            "required": ["selector"],
        }

    async def execute(self, **kwargs: Any) -> SkillResult:
        selector = kwargs.get("selector", "")

        if not selector:
            return SkillResult.failure("selector is required")

        try:
            orchestrator = _get_orchestrator()
            result = await orchestrator.execute_web_action("click", selector=selector)
            if result.get("success"):
                return SkillResult.success(result)
            return SkillResult.failure(result.get("error", "Click failed"))
        except Exception as e:
            logger.exception("Error clicking")
            return SkillResult.failure(f"Error clicking: {e}")


class WebAutomationTypeSkill(SkillBase):
    """Skill to type text into a form field."""

    approval_title = "Type on a web page"

    @property
    def name(self) -> str:
        return "web_automation.type"

    @property
    def description(self) -> str:
        return "Type text into a form field using a CSS selector."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "selector": {
                    "type": "string",
                    "description": "CSS selector of the input element",
                },
                "text": {
                    "type": "string",
                    "description": "Text to type into the field",
                },
            },
            "required": ["selector", "text"],
        }

    async def execute(self, **kwargs: Any) -> SkillResult:
        selector = kwargs.get("selector", "")
        text = kwargs.get("text", "")

        if not selector or not text:
            return SkillResult.failure("selector and text are required")

        try:
            orchestrator = _get_orchestrator()
            result = await orchestrator.execute_web_action("type", selector=selector, text=text)
            if result.get("success"):
                return SkillResult.success(result)
            return SkillResult.failure(result.get("error", "Type failed"))
        except Exception as e:
            logger.exception("Error typing")
            return SkillResult.failure(f"Error typing: {e}")


class WebAutomationExtractSkill(SkillBase):
    """Skill to extract text/content from page elements."""

    kind = "read"

    @property
    def name(self) -> str:
        return "web_automation.extract"

    @property
    def description(self) -> str:
        return "Extract text or attributes from elements on the current page using CSS selectors."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "selector": {
                    "type": "string",
                    "description": "CSS selector of elements to extract",
                },
                "attribute": {
                    "type": "string",
                    "description": "Attribute to extract (default: textContent)",
                    "default": "textContent",
                },
            },
            "required": ["selector"],
        }

    async def execute(self, **kwargs: Any) -> SkillResult:
        selector = kwargs.get("selector", "")
        attribute = kwargs.get("attribute", "textContent")

        if not selector:
            return SkillResult.failure("selector is required")

        try:
            orchestrator = _get_orchestrator()
            result = await orchestrator.execute_web_action("extract", selector=selector, attribute=attribute)
            if result.get("success"):
                return SkillResult.success(result)
            return SkillResult.failure(result.get("error", "Extract failed"))
        except Exception as e:
            logger.exception("Error extracting")
            return SkillResult.failure(f"Error extracting: {e}")


class WebAutomationScreenshotSkill(ScreenshotSkill):
    """Skill to take a screenshot of the current page."""

    @property
    def name(self) -> str:
        return "web_automation.screenshot"

    @property
    def description(self) -> str:
        return ("Take a screenshot of the current page (viewport or full page). "
                "You see the image only when the owner has turned on screenshot sharing in Settings > Automation.")

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "full_page": {
                    "type": "boolean",
                    "description": "Capture full scrollable page (default: false)",
                    "default": False,
                },
            },
        }

    async def execute(self, **kwargs: Any) -> SkillResult:
        full_page = kwargs.get("full_page", False)

        try:
            orchestrator = _get_orchestrator()
            result = await orchestrator.execute_web_action("screenshot", full_page=full_page)
            if result.get("success"):
                return SkillResult.success(result)
            return SkillResult.failure(result.get("error", "Screenshot failed"))
        except Exception as e:
            logger.exception("Error taking screenshot")
            return SkillResult.failure(f"Error taking screenshot: {e}")


class WebAutomationGetContentSkill(SkillBase):
    """Skill to get page content for LLM analysis."""

    kind = "read"

    @property
    def name(self) -> str:
        return "web_automation.get_page_content"

    @property
    def description(self) -> str:
        return "Get the text content of the current page for LLM analysis (truncated to 10000 chars)."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {},
        }

    async def execute(self, **kwargs: Any) -> SkillResult:
        try:
            orchestrator = _get_orchestrator()
            result = await orchestrator.execute_web_action("get_page_content")
            if result.get("success"):
                return SkillResult.success(result)
            return SkillResult.failure(result.get("error", "Get content failed"))
        except Exception as e:
            logger.exception("Error getting page content")
            return SkillResult.failure(f"Error getting page content: {e}")


class WebAutomationWaitForSkill(SkillBase):
    """Skill to wait for an element to appear."""

    kind = "read"

    @property
    def name(self) -> str:
        return "web_automation.wait_for"

    @property
    def description(self) -> str:
        return "Wait for an element to appear on the page before proceeding."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "selector": {
                    "type": "string",
                    "description": "CSS selector of the element to wait for",
                },
                "state": {
                    "type": "string",
                    "description": "State to wait for",
                    "enum": ["attached", "detached", "visible", "hidden"],
                    "default": "visible",
                },
            },
            "required": ["selector"],
        }

    async def execute(self, **kwargs: Any) -> SkillResult:
        selector = kwargs.get("selector", "")
        state = kwargs.get("state", "visible")

        if not selector:
            return SkillResult.failure("selector is required")

        try:
            orchestrator = _get_orchestrator()
            result = await orchestrator.execute_web_action("wait_for", selector=selector, state=state)
            if result.get("success"):
                return SkillResult.success(result)
            return SkillResult.failure(result.get("error", "Wait failed"))
        except Exception as e:
            logger.exception("Error waiting")
            return SkillResult.failure(f"Error waiting: {e}")


class WebAutomationCloseSkill(SkillBase):
    """Skill to close the web automation browser session."""

    # A read: it only ends Tesseract's own browser session.
    kind = "read"

    @property
    def name(self) -> str:
        return "web_automation.close"

    @property
    def description(self) -> str:
        return "Close the web automation browser session and cleanup resources."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {},
        }

    async def execute(self, **kwargs: Any) -> SkillResult:
        try:
            orchestrator = _get_orchestrator()
            await orchestrator.stop_web()
            return SkillResult.success({"status": "closed"})
        except Exception as e:
            logger.exception("Error closing browser")
            return SkillResult.failure(f"Error closing browser: {e}")