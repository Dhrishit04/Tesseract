"""Skill to launch apps and manage windows."""

import os
import platform
import subprocess
from typing import Any

from skills.skill_base import SkillBase, SkillResult


class AppLauncherLaunchSkill(SkillBase):
    """Skill to launch an application or open a file with its default program."""

    approval_title = "Open an app"

    @property
    def name(self) -> str:
        return "app_launcher.launch"

    @property
    def description(self) -> str:
        return "Launch an application or open a file/URL with its default program."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "target": {
                    "type": "string",
                    "description": "Path to the application executable, file, or URL to open",
                },
            },
            "required": ["target"],
        }

    async def execute(self, **kwargs: Any) -> SkillResult:
        target = kwargs.get("target", "")
        if not target:
            return SkillResult.failure("Target parameter is required")

        try:
            if platform.system() == "Windows":
                os.startfile(target)
            elif platform.system() == "Darwin":
                subprocess.Popen(["open", target])
            else:
                subprocess.Popen(["xdg-open", target])
            
            return SkillResult.success({"target": target, "status": "launched"})
        except Exception as e:
            return SkillResult.failure(f"Error launching target: {e}")


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
