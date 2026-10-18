# -*- mode: python ; coding: utf-8 -*-

import os
import subprocess
from pathlib import Path
from PyInstaller.utils.hooks import collect_submodules


def target_triple() -> str:
    """Tauri's externalBin expects the Rust target triple in the file name."""
    if os.environ.get("TARGET_TRIPLE"):
        return os.environ["TARGET_TRIPLE"]
    rustc = subprocess.run(["rustc", "-vV"], capture_output=True, text=True, check=True).stdout
    return next(line.split(": ", 1)[1] for line in rustc.splitlines() if line.startswith("host: "))


sidecar_root = Path(SPECPATH)
hiddenimports = []
for package in ("automation", "auth", "llm", "skills", "voice"):
    hiddenimports.extend(collect_submodules(package))

analysis = Analysis(
    [str(sidecar_root / "main.py")],
    pathex=[str(sidecar_root)],
    binaries=[],
    datas=[],
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

pyz = PYZ(analysis.pure)
exe = EXE(
    pyz,
    analysis.scripts,
    analysis.binaries,
    analysis.datas,
    [],
    name=f"tesseract-sidecar-{target_triple()}",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
)
