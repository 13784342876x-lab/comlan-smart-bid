param([string]$Python = 'D:\miniconda3\envs\comlan\python.exe', [int]$Port = 8000)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
if (-not (Test-Path -LiteralPath $Python)) { throw "Python not found: $Python. Supply -Python with the existing project environment." }
Push-Location $projectRoot
try {
    & $Python -m uvicorn web_api:app --app-dir backend --host 127.0.0.1 --port $Port
    if ($LASTEXITCODE -ne 0) { throw "Backend exited with code $LASTEXITCODE" }
} finally { Pop-Location }
