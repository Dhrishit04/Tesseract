"""Which locations the file, Office and app tools may touch: the level from Settings > Permissions, never Tesseract's own data."""

import os
import unicodedata
from pathlib import Path, PurePath

from settings_store import load_settings

DEFAULT_LEVEL = "profile"
# Each level includes the ones below it. The wording goes into the model's instructions.
LEVELS = {
    "full": "the entire device",
    "profile": "their user profile, their Desktop, Documents and Downloads folders and the working folder",
    "personal": "their Desktop, Documents and Downloads folders and the working folder",
    "workspace": "the working folder only",
}
SHELL_FOLDERS_KEY = r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders"
SHELL_FOLDER_VALUES = {
    "Desktop": "Desktop",
    "Documents": "Personal",
    "Downloads": "{374DE290-123F-4565-9164-39C4925E467B}",
}


def known_folders() -> dict[str, Path]:
    """Desktop, Documents and Downloads, which Windows lets the owner move to another drive."""
    folders = {name: Path.home() / name for name in SHELL_FOLDER_VALUES}
    if os.name != "nt":
        return folders
    import winreg

    for name, value in SHELL_FOLDER_VALUES.items():
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, SHELL_FOLDERS_KEY) as key:
                folders[name] = Path(os.path.expandvars(winreg.QueryValueEx(key, value)[0]))
        except OSError:
            pass  # keep the default location
    return folders


def access_level() -> str:
    """The saved level. A value this version doesn't know gets the narrowest level, not a wider one."""
    level = load_settings().get("file_access", DEFAULT_LEVEL)
    return level if isinstance(level, str) and level in LEVELS else "workspace"


def _folded(path: PurePath) -> list[str]:
    """Path parts as a case-insensitive disk sees them (Windows and macOS), so .TESSERACT, .tesseract. and .tesseract::$DATA match."""
    return [unicodedata.normalize("NFC", part.split(":")[0]).casefold().rstrip(". ") for part in path.parts]


def is_protected(path: Path) -> bool:
    """True for ~/.tesseract and anything in it, at every access level, however the path is spelled."""
    data = Path.home() / ".tesseract"
    resolved = path.resolve()
    folded_data = _folded(data.resolve())
    if _folded(resolved)[: len(folded_data)] == folded_data:
        return True
    # Other names for the same folder, such as \\\\localhost\\C$\\..., only match by identity.
    for candidate in (resolved, *resolved.parents):
        try:
            if os.path.samefile(candidate, data):
                return True
        except OSError:
            pass  # doesn't exist yet, so it can't be the folder
    return False


def is_allowed(path: Path) -> bool:
    """True for a path the chosen access level covers, outside ~/.tesseract."""
    if is_protected(path):
        return False
    level = access_level()
    if level == "full":
        return True
    roots = [Path.cwd()]
    if level in ("profile", "personal"):
        roots.extend(known_folders().values())
    if level == "profile":
        roots.append(Path.home())
    resolved = path.resolve()
    return any(resolved.is_relative_to(root.resolve()) for root in roots)


def denied_message(path: Path) -> str:
    """Why a path was refused, in words the model can pass on to the owner."""
    if is_protected(path):
        return f"Access denied: Tesseract's own data can't be used by any tool: {path}"
    return f"Access denied: outside the access chosen in Settings > Permissions: {path}"
