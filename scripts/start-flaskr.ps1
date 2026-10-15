param([switch]$Stop, [switch]$Restart)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'flaskr-process.ps1')
$workspacePath = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$flaskPythonPath = Join-Path $workspacePath '.venv-flaskr/Scripts/python.exe'
$flaskWorkerPath = Join-Path $workspacePath 'testfix/project_http_worker.py'
$flaskServicePath = Join-Path $workspacePath 'artifacts/service'
New-Item -ItemType Directory -Path $flaskServicePath -Force | Out-Null
if (-not (Test-Path -LiteralPath $flaskPythonPath)) { throw 'Prepare .venv-flaskr using experiments/flaskr/requirements.lock first.' }
foreach ($target in @(@{Mode='candidate'; Port=8766}, @{Mode='baseline'; Port=8767})) {
    $port = $target.Port
    $projectPath = Join-Path $workspacePath "experiments/flaskr/$($target.Mode)"
    $databasePath = Join-Path $flaskServicePath "flaskr-$($target.Mode).sqlite"
    $listener = Get-NetTCPConnection -LocalAddress 127.0.0.1 -LocalPort $port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($listener) {
        $owned = Get-CimInstance Win32_Process -Filter "ProcessId=$($listener.OwningProcess)"
        if (-not (Test-FlaskrProcessOwnership -CommandLine $owned.CommandLine -WorkerPath $flaskWorkerPath -ProjectPath $projectPath -DatabasePath $databasePath -Port $port)) {
            throw "Port $port belongs to a different application; it was left running."
        }
        if ($Stop -or $Restart) {
            Stop-Process -Id $listener.OwningProcess -Force
        } else {
            Write-Output "$($target.Mode) already running: http://127.0.0.1:$port/"
            continue
        }
    }
    if ($Stop) { continue }
    $workerArgs = @('"' + $flaskWorkerPath + '"', '--project', '"' + $projectPath + '"', '--database', '"' + $databasePath + '"', '--port', "$port", '--seed-demo')
    $launched = Start-Process -FilePath $flaskPythonPath -ArgumentList $workerArgs -WorkingDirectory $workspacePath -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $flaskServicePath "flaskr-$($target.Mode).stdout.log") -RedirectStandardError (Join-Path $flaskServicePath "flaskr-$($target.Mode).stderr.log")
    $launched.Id | Set-Content -LiteralPath (Join-Path $flaskServicePath "flaskr-$($target.Mode).launcher.pid")
    Write-Output "$($target.Mode): http://127.0.0.1:$port/ (launcher $($launched.Id))"
}
