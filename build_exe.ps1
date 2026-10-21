# Genera en dist\timetrack:
#   timetrack.exe      app de bandeja sin consola (doble clic / inicio con Windows)
#   timetrack-cli.exe  los mismos comandos con consola: report, timeline, ocr-test…
# y copia config.toml, rules.toml y categories.toml al lado si aún no existen allí.
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

.\.venv\Scripts\python.exe -m pip install -q --disable-pip-version-check pyinstaller

$common = @(
    "--noconfirm", "--clean", "--onefile",
    "--distpath", "dist\timetrack", "--workpath", "build",
    "--collect-submodules", "winrt",
    "--collect-submodules", "comtypes",
    "--hidden-import", "pystray._win32",
    "--add-data", "timetrack\static;timetrack\static"
)
.\.venv\Scripts\python.exe -m PyInstaller @common --noconsole --name timetrack run_timetrack.py
.\.venv\Scripts\python.exe -m PyInstaller @common --console --name timetrack-cli run_timetrack.py

# rules.toml es de cada usuario y no está en el repositorio: se parte del ejemplo.
if (-not (Test-Path "rules.toml")) { Copy-Item "rules.example.toml" "rules.toml" }

foreach ($f in "config.toml", "rules.toml", "categories.toml") {
    # No pisar la configuración que ya se haya personalizado en dist.
    if (-not (Test-Path "dist\timetrack\$f")) { Copy-Item $f "dist\timetrack\$f" }
}
Write-Host "Listo: dist\timetrack\timetrack.exe y timetrack-cli.exe"
