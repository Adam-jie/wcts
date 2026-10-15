param([ValidateRange(1,65535)][int]$Port = 8765, [switch]$Background)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'stop.ps1') -FunctionsOnly
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$serviceLock = Enter-TestFixServiceLock $projectRoot
try {
Set-Location -LiteralPath $projectRoot
$pythonPath = Join-Path $projectRoot '.venv\Scripts\python.exe'
$pidPath = Join-Path $projectRoot 'artifacts\service\pid.txt'
$existing = $null
if (Test-Path -LiteralPath $pidPath) {
    $serviceText = (Get-Content -LiteralPath $pidPath -Raw).Trim()
    $serviceId = 0
    if (-not [int]::TryParse($serviceText,[ref]$serviceId) -or $serviceId -le 0) { throw 'Invalid managed PID; inspect artifacts/service before starting.' }
    $record = Get-TestFixRecord $projectRoot $serviceId
    $existing = Get-TestFixManagedService $projectRoot $serviceId $record
    if ($existing.Processes.Count -and $existing.Port -ne $Port) { throw "A managed TestFix service is already running on port $($existing.Port); stop it before changing ports." }
}
$listeners = @(Get-TestFixListeners $Port)
if ($listeners.Count) {
    if (-not $existing -or -not $existing.Processes.Count) { throw "Port $Port is already occupied and cannot be linked to this workspace's managed service; it was left running." }
    $null = Assert-TestFixPortOwnership $existing $Port
    $health = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/api/health" -TimeoutSec 5
    if ($health.status -ne 'ok' -or -not $health.version -or $null -eq $health.providers) { throw 'The managed listener did not return a TestFix health response.' }
    Write-TestFixRecord $projectRoot $existing ([int]$listeners[0].OwningProcess)
    Write-Output "TestFix already running: http://127.0.0.1:$Port (verified server PID $($listeners[0].OwningProcess))"
    return
}
if ($existing -and $existing.Processes.Count) { throw 'A verified TestFix process exists but is not listening; inspect artifacts/service logs or stop it before restarting.' }
if (-not (Test-Path -LiteralPath $pythonPath)) {
    uv sync --extra dev --locked
    if ($LASTEXITCODE -ne 0) { throw 'Dependency setup failed' }
}
if ($Background) {
    $logDirectory = Join-Path $projectRoot 'artifacts\service'
    New-Item -ItemType Directory -Path $logDirectory -Force | Out-Null
    $serviceProcess = Start-Process -FilePath $pythonPath -ArgumentList @('-m','testfix.cli','serve','--port',"$Port") -WorkingDirectory $projectRoot -WindowStyle Hidden -RedirectStandardOutput (Join-Path $logDirectory 'stdout.log') -RedirectStandardError (Join-Path $logDirectory 'stderr.log') -PassThru
    $serviceProcess.Id | Set-Content -LiteralPath $pidPath -Encoding Ascii
    $launchedService = $null
    $startupRecord = $null
    try {
        $deadline = [datetime]::UtcNow.AddSeconds(30)
        do {
            $launchedService = Get-TestFixManagedService $projectRoot $serviceProcess.Id $startupRecord
            if (-not $launchedService.Processes.Count) { throw 'The service process exited before becoming healthy.' }
            $listeners = @(Assert-TestFixPortOwnership $launchedService $Port)
            Write-TestFixRecord $projectRoot $launchedService $(if ($listeners.Count) { [int]$listeners[0].OwningProcess } else { 0 })
            $startupRecord = Get-TestFixRecord $projectRoot $serviceProcess.Id
            if ($listeners.Count) {
                try {
                    $health = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/api/health" -TimeoutSec 2
                    if ($health.status -eq 'ok' -and $health.version -and $null -ne $health.providers) {
                        Write-Output "TestFix started: http://127.0.0.1:$Port (launcher $($serviceProcess.Id), server $($listeners[0].OwningProcess), healthy)"
                        return
                    }
                } catch { }
            }
            Start-Sleep -Milliseconds 250
        } while ([datetime]::UtcNow -lt $deadline)
        throw 'The service did not become healthy within 30 seconds.'
    } catch {
        $startupFailure = $_
        # Only the process just launched and revalidated here may be cleaned up.
        # Refused/foreign listeners are never terminated.
        if ($launchedService -and $launchedService.Processes.Count) {
            try { Invoke-TestFixStop -ProjectRoot $projectRoot -ExpectedServiceId $serviceProcess.Id -ExpectedLauncherStamp $launchedService.LauncherCreatedUtc | Out-Null }
            catch { Write-Warning "Startup cleanup was refused or failed; inspect the saved process records: $($_.Exception.Message)" }
        }
        throw "TestFix start failed: $($startupFailure.Exception.Message) Logs: $logDirectory"
    }
} else {
    & $pythonPath -m testfix.cli serve --port $Port
    if ($LASTEXITCODE -ne 0) { throw "TestFix exited with status $LASTEXITCODE" }
}
} finally { Exit-TestFixServiceLock $serviceLock }

