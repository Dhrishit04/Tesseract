"""Shared automation settings policy."""

from settings_store import load_settings


def load_automation_policy() -> dict[str, bool]:
    """Return the configured automation and OCR permissions."""
    automation = load_settings().get("automation", {})
    return {
        "automation_enabled": bool(automation.get("automation_enabled", False)),
        "ocr_enabled": bool(automation.get("ocr_enabled", False)),
        "share_screenshots": bool(automation.get("share_screenshots", False)),
    }
