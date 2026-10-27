param([string]$Node = '', [int]$Port = 5173)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
if (-not $Node) {
    $nodeCommand = Get-Command node -ErrorAction SilentlyContinue
    if ($nodeCommand) { $Node = $nodeCommand.Source }
    else { $Node = Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe' }
}
if (-not (Test-Path -LiteralPath $Node)) { throw 'Node.js not found. Supply -Node with the installed node.exe path.' }
Push-Location $projectRoot
try {
    & $Node node_modules/vite/bin/vite.js --host 127.0.0.1 --port $Port --strictPort
    if ($LASTEXITCODE -ne 0) { throw "Frontend exited with code $LASTEXITCODE" }
} finally { Pop-Location }
