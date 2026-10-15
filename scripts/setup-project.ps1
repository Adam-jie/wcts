param([string]$PythonVersion = '3.13')
$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$environmentPath = Join-Path $projectRoot '.venv-flaskr'
$pythonPath = Join-Path $environmentPath 'Scripts\python.exe'
$lockPath = Join-Path $projectRoot 'experiments\flaskr\requirements.lock'
if (-not (Test-Path -LiteralPath $lockPath -PathType Leaf)) { throw 'Pinned Flaskr dependency lock is missing' }
if (-not (Test-Path -LiteralPath $pythonPath -PathType Leaf)) {
    uv venv --python $PythonVersion --seed $environmentPath
    if ($LASTEXITCODE -ne 0) { throw 'Flaskr environment creation failed' }
}
uv pip install --python $pythonPath --requirement $lockPath
if ($LASTEXITCODE -ne 0) { throw 'Pinned Flaskr dependency installation failed' }
& $pythonPath -c "import flask, pytest, build; print('Flaskr validation environment is ready')"
if ($LASTEXITCODE -ne 0) { throw 'Flaskr dependency verification failed' }
