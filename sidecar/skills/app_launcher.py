"""Skill to launch apps and manage windows."""

import asyncio
import os
import platform
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

from automation.apps import list_apps, open_app, open_target, resolve_app, visible_windows, wait_for_new_windows
from file_access import denied_message, is_allowed, is_protected
from skills.skill_base import SkillBase, SkillResult

# Programs live outside the owner's folders, so the access level limits what gets opened, not what runs.
PROGRAM_SUFFIXES = {".exe", ".lnk", ".app"}
# write_file can make these, so opening one would be running code the model wrote.
SCRIPT_SUFFIXES = {".bat", ".cmd", ".com", ".hta", ".js", ".jse", ".msc", ".ps1", ".reg", ".scr", ".url", ".vbe", ".vbs", ".wsf", ".wsh"}


def _refusal(target: str) -> str | None:
    """Why target can't be opened, if the access rules refuse it."""
    # The shell has its own spellings of a folder (shell:Profile\\.tesseract), which no path check would see.
    if ".tesseract" in target.casefold():
        return denied_message(Path.home() / ".tesseract")
    if target.casefold().startswith(("file:", "shell:")):
        return "Access denied: open local files by their path, not a file: or shell: link."
    if re.match(r"[a-z][a-z0-9+.-]+:", target, re.IGNORECASE):
        return None  # a URL, or a scheme like ms-settings:. Two letters at least, so C:\\ isn't one.
    path = Path(os.path.expandvars(target)).expanduser()
    if not path.exists():
        return None  # an app name such as notepad
    suffix = path.suffix.casefold().rstrip(". ")
    if suffix in SCRIPT_SUFFIXES:
        return "Access denied: Tesseract doesn't open scripts."
    if suffix in PROGRAM_SUFFIXES:
        return denied_message(path) if is_protected(path) else None
    return None if is_allowed(path) else denied_message(path)


def _is_app_name(target: str) -> bool:
    """False for URLs, schemes and anything that exists as a path."""
    if re.match(r"[a-z][a-z0-9+.-]+:", target, re.IGNORECASE) or "/" in target or "\\" in target:
        return False
    return not Path(os.path.expandvars(target)).expanduser().exists()


def _open(target: str) -> dict | str:
    """Open target and return what happened, or a sentence saying why nothing was opened."""
    system = platform.system()
    app = resolve_app(target) if system == "Windows" and _is_app_name(target) else None
    if app and "candidates" in app:
        names = ", ".join(f"{candidate['name']} ({candidate['app_id']})" for candidate in app["candidates"])
        return f"Several apps match '{target}': {names}. Nothing was opened; use the exact name or the id in brackets."

    program = shutil.which(target) if system == "Windows" and _is_app_name(target) else None
    if system == "Windows" and not app and not program and _is_app_name(target):
        return f"No app called '{target}' is installed. Nothing was opened."

    before = visible_windows()
    if app:
        open_app(app["app_id"])
    elif system == "Windows":
        open_target(program or os.path.expandvars(target))
    elif system == "Darwin":
        subprocess.Popen(["open", target])
    else:
        subprocess.Popen(["xdg-open", target])
    # The handle lets the model go straight on to click and type in what it opened.
    return {"target": target, "status": "launched", "windows": wait_for_new_windows(before)}


class AppLauncherLaunchSkill(SkillBase):
    """Skill to launch an application or open a file with its default program."""

    approval_title = "Open an app"

    def approval_summary(self, arguments: dict) -> str:
        return f"Open {arguments.get('target', '')}"

    @property
    def name(self) -> str:
        return "app_launcher.launch"

    @property
    def description(self) -> str:
        return ("Open an installed app by name (such as Excel or Calculator), or a file or URL with its default program. "
                "Returns the handle and title of each new window, which is empty if the app reused an open one.")

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "target": {
                    "type": "string",
                    "description": "App name, or a path to a program or file, or a URL",
                },
            },
            "required": ["target"],
        }

    async def execute(self, **kwargs: Any) -> SkillResult:
        target = kwargs.get("target", "")
        if not target:
            return SkillResult.failure("Target parameter is required")

        try:
            refusal = _refusal(target)
            if refusal:
                return SkillResult.failure(refusal)

            opened = await asyncio.to_thread(_open, target)
            return SkillResult.failure(opened) if isinstance(opened, str) else SkillResult.success(opened)
        except Exception as e:
            return SkillResult.failure(f"Error launching target: {e}")


class AppLauncherListAppsSkill(SkillBase):
    """Skill to list the apps installed on this computer."""

    kind = "read"

    @property
    def name(self) -> str:
        return "app_launcher.list_apps"

    @property
    def description(self) -> str:
        return "List installed apps by name (Windows only). Pass query to narrow the list."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Only return apps whose name contains this text",
                },
            },
        }

    async def execute(self, **kwargs: Any) -> SkillResult:
        query = kwargs.get("query", "").casefold()
        apps = await asyncio.to_thread(list_apps)
        return SkillResult.success({"apps": [app for app in apps if query in app["name"].casefold()]})


class AppLauncherListWindowsSkill(SkillBase):
    """Skill to list visible windows."""

    kind = "read"

    @property
    def name(self) -> str:
        return "app_launcher.list_windows"

    @property
    def description(self) -> str:
        return "List all visible windows with their titles and handles (Windows only)."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {},
        }

    async def execute(self, **kwargs: Any) -> SkillResult:
        if platform.system() != "Windows":
            return SkillResult.failure("Listing windows is currently only supported on Windows")

        try:
            import ctypes
            from ctypes import wintypes
            
            EnumWindows = ctypes.windll.user32.EnumWindows
            EnumWindowsProc = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
            GetWindowText = ctypes.windll.user32.GetWindowTextW
            GetWindowTextLength = ctypes.windll.user32.GetWindowTextLengthW
            IsWindowVisible = ctypes.windll.user32.IsWindowVisible

            windows = []
            def foreach_window(hwnd, lParam):
                if IsWindowVisible(hwnd):
                    length = GetWindowTextLength(hwnd)
                    if length > 0:
                        buff = ctypes.create_unicode_buffer(length + 1)
                        GetWindowText(hwnd, buff, length + 1)
                        # hwnd could be a CData object; getting its value
                        windows.append({"hwnd": hwnd, "title": buff.value})
                return True

            EnumWindows(EnumWindowsProc(foreach_window), 0)
            return SkillResult.success({"windows": windows})
        except Exception as e:
            return SkillResult.failure(f"Error listing windows: {e}")


class AppLauncherFocusWindowSkill(SkillBase):
    """Skill to focus a specific window."""

    approval_title = "Switch windows"

    @property
    def name(self) -> str:
        return "app_launcher.focus_window"

    @property
    def description(self) -> str:
        return "Bring a specific window to the foreground using its handle (hwnd) (Windows only)."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "hwnd": {
                    "type": "integer",
                    "description": "The window handle (hwnd) to focus",
                },
            },
            "required": ["hwnd"],
        }

    async def execute(self, **kwargs: Any) -> SkillResult:
        if platform.system() != "Windows":
            return SkillResult.failure("Focusing windows is currently only supported on Windows")

        hwnd = kwargs.get("hwnd")
        if not hwnd:
            return SkillResult.failure("hwnd parameter is required")

        try:
            import ctypes
            
            # SW_RESTORE = 9
            ctypes.windll.user32.ShowWindow(hwnd, 9)
            success = ctypes.windll.user32.SetForegroundWindow(hwnd)
            
            if success:
                return SkillResult.success({"hwnd": hwnd, "status": "focused"})
            else:
                return SkillResult.failure(f"Failed to focus window with hwnd {hwnd}")
        except Exception as e:
            return SkillResult.failure(f"Error focusing window: {e}")
