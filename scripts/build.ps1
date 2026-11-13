param([string]$Destination = 'outputs')
$ErrorActionPreference = 'Stop'
Set-Location (Split-Path $PSScriptRoot -Parent)
$taskPython = (python -c "import sys; print(sys.executable)").Trim()
$taskVersion = (& $taskPython -c "from marketplace_transfer import __version__; print(__version__)").Trim()
$taskCompiler = Join-Path $PWD.Path 'work/tools/inno/ISCC.exe'
if (-not (Test-Path -LiteralPath $taskCompiler)) {
    $taskIscc = Get-Command ISCC.exe -ErrorAction SilentlyContinue
    $taskCompiler = if ($taskIscc) { $taskIscc.Source } else { $null }
}
# External tools in PATH (notably Poppler) can shadow Windows' ICU shim.
# Resolve Python before limiting DLL discovery to Python and the operating system.
$env:PATH = "$(Split-Path $taskPython -Parent);$env:SystemRoot\System32;$env:SystemRoot"
& $taskPython -m PyInstaller --noconfirm --clean --windowed --name MarketplaceTransfer --icon assets/app.ico --add-data 'assets;assets' --add-data 'marketplace_transfer/updating/update_config.json;marketplace_transfer/updating' --exclude-module PySide6.QtWebEngineCore --exclude-module PySide6.QtWebEngineWidgets --distpath $Destination --workpath work/pyinstaller run.py
if ($LASTEXITCODE -ne 0) { throw 'GUI build failed' }
& $taskPython -m PyInstaller --noconfirm --clean --onefile --console --name MarketplaceTransferHost --distpath work/host --workpath work/pyinstaller-host native_entry.py
if ($LASTEXITCODE -ne 0) { throw 'Native host build failed' }
Copy-Item -LiteralPath work/host/MarketplaceTransferHost.exe -Destination (Join-Path $Destination 'MarketplaceTransfer')
& $taskPython -m PyInstaller --noconfirm --clean --onefile --windowed --name MarketplaceTransferUpdater --icon assets/app.ico --add-data 'marketplace_transfer/updating/update_config.json;marketplace_transfer/updating' --distpath work/updater --workpath work/pyinstaller-updater updater_entry.py
if ($LASTEXITCODE -ne 0) { throw 'Updater build failed' }
Copy-Item -LiteralPath work/updater/MarketplaceTransferUpdater.exe -Destination (Join-Path $Destination 'MarketplaceTransfer')
Copy-Item -LiteralPath extension -Destination (Join-Path $Destination 'MarketplaceTransfer') -Recurse -Force
& $taskPython scripts/collect_licenses.py (Join-Path $Destination 'MarketplaceTransfer')
if ($LASTEXITCODE -ne 0) { throw 'License collection failed' }
if ($taskCompiler -and $Destination -eq 'outputs') { & $taskCompiler ("/DAppVersion="+$taskVersion) installer/MarketplaceTransfer.iss; if ($LASTEXITCODE -ne 0) { throw 'Installer build failed' } }
else { Write-Host "Portable build is in $Destination/MarketplaceTransfer. Installer was not built." }
