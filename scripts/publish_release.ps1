param([Parameter(Mandatory=$true)][string]$Repository,[string]$Notes='RELEASE_NOTES.md')
$ErrorActionPreference='Stop'
Set-Location (Split-Path $PSScriptRoot -Parent)
python scripts/configure_updates.py ("https://github.com/"+$Repository)
if ($LASTEXITCODE -ne 0) {throw 'Invalid repository'}
& $PSScriptRoot/build.ps1
if ($LASTEXITCODE -ne 0) {throw 'Build failed'}
python scripts/sign_release.py --notes $Notes
if ($LASTEXITCODE -ne 0) {throw 'Signature failed'}
$taskVersion=(python -c 'from marketplace_transfer import __version__; print(__version__)').Trim()
if (-not (Get-Command gh -ErrorAction SilentlyContinue)) {throw 'Установите официальный GitHub CLI и выполните gh auth login. Готовые подписанные файлы уже находятся в outputs.'}
gh release create ("v"+$taskVersion) outputs/MarketplaceTransfer_Setup.exe outputs/MarketplaceTransfer-update.json --repo $Repository --title ("Marketplace Transfer "+$taskVersion) --notes-file $Notes --verify-tag
if ($LASTEXITCODE -ne 0) {throw 'Публикация не выполнена. Проверьте тег, права и авторизацию GitHub CLI.'}
