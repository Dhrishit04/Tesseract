"""Read and write ~/.tesseract/settings.json, shared with the Tauri app."""

import json
import logging
import os

logger = logging.getLogger(__name__)


def settings_path() -> str:
    """Path of the settings file (resolved per call so tests can redirect HOME)."""
    return os.path.join(os.path.expanduser("~"), ".tesseract", "settings.json")


def load_settings() -> dict:
    """Return the saved settings, or an empty dict when missing or unreadable."""
    try:
        with open(settings_path(), "r", encoding="utf-8") as settings_file:
            settings = json.load(settings_file)
    except FileNotFoundError:
        return {}
    except (OSError, ValueError):
        logger.exception("Could not read settings file")
        return {}
    return settings if isinstance(settings, dict) else {}


def save_settings(settings: dict) -> None:
    """Persist settings atomically so an interrupted write never corrupts the file."""
    path = settings_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    temporary_path = f"{path}.tmp"
    with open(temporary_path, "w", encoding="utf-8") as settings_file:
        json.dump(settings, settings_file, indent=2)
        settings_file.flush()
        os.fsync(settings_file.fileno())
    os.replace(temporary_path, path)


def update_section(section: str, values: dict) -> dict:
    """Merge values into one settings section, keeping every other key, and return the full settings."""
    settings = load_settings()
    current = settings.get(section)
    settings[section] = {**(current if isinstance(current, dict) else {}), **values}
    save_settings(settings)
    return settings
