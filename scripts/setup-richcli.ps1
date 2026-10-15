param([string]$PythonVersion = '3.13')
$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$environmentPath = Join-Path $projectRoot '.venv-richcli'
$pythonPath = Join-Path $environmentPath 'Scripts\python.exe'
$controllerPython = Join-Path $projectRoot '.venv\Scripts\python.exe'
$lockPath = Join-Path $projectRoot 'experiments\rich-cli\requirements.lock'
if (-not (Test-Path -LiteralPath $lockPath -PathType Leaf)) { throw 'Pinned rich-cli dependency lock is missing' }
if (-not (Test-Path -LiteralPath $controllerPython -PathType Leaf)) { throw 'Initialize the main TestFix environment first' }
if (-not (Test-Path -LiteralPath $pythonPath -PathType Leaf)) {
    uv venv --python $PythonVersion --seed $environmentPath
    if ($LASTEXITCODE -ne 0) { throw 'rich-cli environment creation failed' }
}
uv pip install --python $pythonPath --requirement $lockPath
if ($LASTEXITCODE -ne 0) { throw 'Pinned rich-cli dependency installation failed' }
& $pythonPath -X utf8 -c "import rich, click, pytest, build, poetry.core; print('rich-cli validation environment ready')"
if ($LASTEXITCODE -ne 0) { throw 'rich-cli dependency verification failed' }
Push-Location -LiteralPath $projectRoot
try {
    & $controllerPython -X utf8 -m scripts.register_richcli
    if ($LASTEXITCODE -ne 0) { throw 'rich-cli registration or seal verification failed' }
} finally { Pop-Location }
