$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $Root
if (-not (Test-Path -LiteralPath '.venv\Scripts\python.exe')) {
    py -3 -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw 'Virtual environment creation failed' }
}
& '.\.venv\Scripts\python.exe' -m pip install --upgrade 'pip>=26.2'
if ($LASTEXITCODE -ne 0) { throw 'Package installer update failed' }
& '.\.venv\Scripts\python.exe' -m pip install -r requirements.lock
if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed' }
& '.\.venv\Scripts\python.exe' -X utf8 manage.py migrate --noinput
if ($LASTEXITCODE -ne 0) { throw 'Database migration failed' }
& '.\.venv\Scripts\python.exe' -X utf8 manage.py seed_references
if ($LASTEXITCODE -ne 0) { throw 'Reference setup failed' }
Write-Host 'Ready: .venv\Scripts\python.exe scripts\run_local.py --demo'
