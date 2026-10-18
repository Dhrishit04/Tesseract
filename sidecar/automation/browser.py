"""Playwright-based web automation for Tesseract."""

import base64
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:
    from playwright.async_api import Browser, BrowserContext, Page

PLAYWRIGHT_MISSING = (
    'Web automation needs Playwright. Run: pip install -e ".[automation]" && python -m playwright install chromium'
)


@dataclass
class WebAutomation:
    """Playwright web automation engine."""
    headless: bool = True
    timeout: int = 30000
    _browser: Optional["Browser"] = field(default=None, init=False)
    _context: Optional["BrowserContext"] = field(default=None, init=False)
    _page: Optional["Page"] = field(default=None, init=False)
    _playwright: Any = field(default=None, init=False)

    async def __aenter__(self) -> "WebAutomation":
        try:
            from playwright.async_api import async_playwright
        except ImportError as exc:
            raise RuntimeError(PLAYWRIGHT_MISSING) from exc
        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(headless=self.headless)
        self._context = await self._browser.new_context()
        self._page = await self._context.new_page()
        self._page.set_default_timeout(self.timeout)
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()

    async def close(self):
        """Close browser and cleanup."""
        if self._page:
            await self._page.close()
        if self._context:
            await self._context.close()
        if self._browser:
            await self._browser.close()
        if self._playwright:
            await self._playwright.stop()
        self._page = self._context = self._browser = self._playwright = None

    async def navigate(self, url: str, wait_until: str = "networkidle") -> dict[str, Any]:
        """Navigate to a URL."""
        if not self._page:
            raise RuntimeError("Browser not started. Use async context manager.")
        await self._page.goto(url, wait_until=wait_until)
        return {"url": self._page.url, "title": await self._page.title()}

    async def click(self, selector: str) -> dict[str, Any]:
        """Click an element."""
        if not self._page:
            raise RuntimeError("Browser not started.")
        await self._page.click(selector)
        return {"success": True, "selector": selector}

    async def type(self, selector: str, text: str) -> dict[str, Any]:
        """Type text into an element."""
        if not self._page:
            raise RuntimeError("Browser not started.")
        await self._page.fill(selector, text)
        return {"success": True, "selector": selector}

    async def select(self, selector: str, value: str) -> dict[str, Any]:
        """Select dropdown option."""
        if not self._page:
            raise RuntimeError("Browser not started.")
        await self._page.select_option(selector, value=value)
        return {"success": True, "selector": selector, "value": value}

    async def extract(self, selector: str, attribute: str = "textContent") -> dict[str, Any]:
        """Extract text/attribute from element(s)."""
        if not self._page:
            raise RuntimeError("Browser not started.")
        elements = await self._page.query_selector_all(selector)
        results = []
        for el in elements:
            if attribute == "textContent":
                val = await el.text_content()
            else:
                val = await el.get_attribute(attribute)
            results.append(val)
        return {"results": results, "count": len(results)}

    async def screenshot(self, full_page: bool = False) -> dict[str, Any]:
        """Take screenshot as base64."""
        if not self._page:
            raise RuntimeError("Browser not started.")
        img = await self._page.screenshot(full_page=full_page)
        return {"image_base64": base64.b64encode(img).decode(), "full_page": full_page}

    async def get_page_content(self) -> dict[str, Any]:
        """Get DOM text snapshot for LLM analysis."""
        if not self._page:
            raise RuntimeError("Browser not started.")
        content = await self._page.evaluate("() => document.body.innerText")
        return {"content": content[:10000], "url": self._page.url, "title": await self._page.title()}

    async def wait_for(self, selector: str, state: str = "visible") -> dict[str, Any]:
        """Wait for element to appear."""
        if not self._page:
            raise RuntimeError("Browser not started.")
        await self._page.wait_for_selector(selector, state=state)
        return {"success": True, "selector": selector}