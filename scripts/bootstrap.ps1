$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
Set-Location -LiteralPath $projectRoot
uv sync --extra dev --locked
if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed' }
$pythonPath = Join-Path $projectRoot '.venv\Scripts\python.exe'
& $pythonPath -m testfix.cli import-humaneval
if ($LASTEXITCODE -ne 0) { throw 'Dataset import failed' }
& $pythonPath -m testfix.cli import-repo examples/order-feature/task.json
if ($LASTEXITCODE -ne 0) { throw 'Repository example import failed' }
& (Join-Path $PSScriptRoot 'setup-project.ps1')

