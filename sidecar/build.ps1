param(
    [switch]$Clean
)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$python = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    $python = (Get-Command python -ErrorAction Stop).Source
}

if ($Clean) {
    Remove-Item -Recurse -Force -ErrorAction SilentlyContinue build, dist
}

& $python -m pip install --upgrade pyinstaller
if ($LASTEXITCODE -ne 0) { throw "Failed to install PyInstaller" }

& $python -m PyInstaller --clean --noconfirm pyinstaller.spec
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }

$triple = ((& rustc -vV) | Select-String "^host: ").ToString().Split(" ")[1]
$artifact = Join-Path $PSScriptRoot "dist\tesseract-sidecar-$triple.exe"
if (-not (Test-Path $artifact)) { throw "Expected sidecar artifact was not created: $artifact" }
Write-Host "Built $artifact"
