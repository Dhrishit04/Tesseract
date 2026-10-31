"""Find installed Windows apps by name, open them and catch the window they bring up."""

import json
import logging
import os
import subprocess
import time

logger = logging.getLogger(__name__)

CACHE_SECONDS = 60
LIST_COMMAND = "[Console]::OutputEncoding=[Text.Encoding]::UTF8; Get-StartApps | ConvertTo-Json -Compress"
CREATE_NO_WINDOW = 0x0800_0000

_cache: tuple[float, list[dict]] | None = None


def list_apps() -> list[dict]:
    """Every app in the Start menu, Win32 and Store alike, as {"name", "app_id"}."""
    global _cache
    if os.name != "nt":
        return []
    if _cache and time.monotonic() - _cache[0] < CACHE_SECONDS:
        return _cache[1]
    try:
        output = subprocess.run(
            ["powershell", "-NoProfile", "-Command", LIST_COMMAND],
            capture_output=True, encoding="utf-8", errors="replace", timeout=30, check=True, creationflags=CREATE_NO_WINDOW,
        ).stdout
        entries = json.loads(output or "[]")
    except (OSError, ValueError, subprocess.SubprocessError):
        logger.exception("Could not list installed apps")
        return []
    # ConvertTo-Json gives a bare object when there is one app.
    entries = entries if isinstance(entries, list) else [entries]
    apps = [{"name": entry["Name"], "app_id": entry["AppID"]} for entry in entries]
    _cache = (time.monotonic(), apps)
    return apps


def resolve_app(name: str) -> dict | None:
    """The app called name, {"candidates": [...]} when several fit, or None."""
    wanted = name.strip().casefold()
    apps = list_apps()
    by_id = [app for app in apps if app["app_id"].casefold() == wanted]
    if by_id:
        return by_id[0]
    exact = [app for app in apps if app["name"].casefold() == wanted]
    if exact:
        return exact[0] if len(exact) == 1 else {"candidates": exact}
    partial = [app for app in apps if wanted in app["name"].casefold()]
    if len(partial) == 1:
        return partial[0]
    return {"candidates": partial} if partial else None


def open_app(app_id: str) -> None:
    """Open a Start menu app. This route also reaches Store apps, which have no executable path."""
    subprocess.Popen(["explorer.exe", f"shell:AppsFolder\\{app_id}"])


def open_target(target: str) -> None:
    """Open a file, URL or program through the shell.

    Started directly it would be Tesseract's child process, and quitting Tesseract ends the whole
    process tree, taking the owner's Word or Excel and their unsaved work with it.
    """
    if '"' in target:
        raise ValueError("A target can't contain a quotation mark.")
    # A string, because explorer.exe splits unquoted arguments at commas.
    subprocess.Popen(f'explorer.exe "{target}"')


def visible_windows() -> dict[int, str]:
    """Titled top-level windows, by handle."""
    if os.name != "nt":
        return {}
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    windows: dict[int, str] = {}

    def remember(handle, _param):
        length = user32.GetWindowTextLengthW(handle)
        if length and user32.IsWindowVisible(handle):
            title = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(handle, title, length + 1)
            windows[handle] = title.value
        return True

    user32.EnumWindows(ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)(remember), 0)
    return windows


def _new_windows(before: dict[int, str]) -> list[dict]:
    return [{"handle": handle, "title": title} for handle, title in visible_windows().items() if handle not in before]


def wait_for_new_windows(before: dict[int, str], timeout: float = 10, settle: float = 1, poll: float = 0.25) -> list[dict]:
    """Windows that appeared, with their titles: a splash may come first, and the owner may open something too.

    Empty when an app reused a window it already had open.
    """
    deadline = time.monotonic() + timeout
    while True:
        if _new_windows(before):
            time.sleep(settle)
            return _new_windows(before)
        if time.monotonic() >= deadline:
            return []
        time.sleep(poll)
