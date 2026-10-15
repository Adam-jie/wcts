param([switch]$FunctionsOnly)
$ErrorActionPreference = 'Stop'

function Test-TestFixSamePath {
    param([string]$First, [string]$Second)
    if ([string]::IsNullOrWhiteSpace($First) -or [string]::IsNullOrWhiteSpace($Second)) { return $false }
    try { return [IO.Path]::GetFullPath($First).Equals([IO.Path]::GetFullPath($Second), [StringComparison]::OrdinalIgnoreCase) }
    catch { return $false }
}

function Get-TestFixCommandPort {
    param([string]$CommandLine, [string]$PythonPath)
    # Accept only the complete invocation emitted by start.ps1. Substrings,
    # extra arguments and another workspace's venv do not establish ownership.
    if ($CommandLine -notmatch '^\s*(?:"(?<python>[^"]+)"|(?<python>\S+))\s+-m\s+testfix\.cli\s+serve\s+--port\s+(?<port>[0-9]{1,5})\s*$') { return $null }
    if (-not (Test-TestFixSamePath $Matches.python $PythonPath)) { return $null }
    $parsedPort = [int]$Matches.port
    if ($parsedPort -lt 1 -or $parsedPort -gt 65535) { return $null }
    return $parsedPort
}

function Get-TestFixProcessStamp {
    param($Process)
    if ($null -eq $Process -or $null -eq $Process.CreationDate) { return $null }
    return ([datetime]$Process.CreationDate).ToUniversalTime().ToString('o')
}

function Test-TestFixCreationIdentity {
    param($Expected, $Process)
    if (-not $Expected -or $null -eq $Process -or $null -eq $Process.CreationDate) { return $false }
    # ConvertFrom-Json may materialize ISO timestamps as DateTime objects in
    # newer PowerShell. Compare UTC instants, never a locale-formatted string.
    try { return ([datetime]$Expected).ToUniversalTime().Ticks -eq ([datetime]$Process.CreationDate).ToUniversalTime().Ticks }
    catch { return $false }
}

function Enter-TestFixServiceLock {
    param([string]$ProjectRoot)
    $hashAlgorithm = [Security.Cryptography.SHA256]::Create()
    try { $hash = $hashAlgorithm.ComputeHash([Text.Encoding]::UTF8.GetBytes(([IO.Path]::GetFullPath($ProjectRoot)).ToLowerInvariant())) }
    finally { $hashAlgorithm.Dispose() }
    $lockName = 'Local\TestFix-Service-' + ([BitConverter]::ToString($hash)).Replace('-','')
    $mutex = [Threading.Mutex]::new($false, $lockName)
    try {
        try { $acquired = $mutex.WaitOne(35000) }
        catch [Threading.AbandonedMutexException] { $acquired = $true }
        if (-not $acquired) { throw 'Another start/stop operation is still controlling this workspace service.' }
        return $mutex
    } catch { $mutex.Dispose(); throw }
}

function Exit-TestFixServiceLock {
    param($Mutex)
    try { $Mutex.ReleaseMutex() } finally { $Mutex.Dispose() }
}

function Get-TestFixPythonPaths {
    param([string]$ProjectRoot)
    $venvPython = Join-Path $ProjectRoot '.venv\Scripts\python.exe'
    $paths = @($venvPython)
    $configuration = Join-Path $ProjectRoot '.venv\pyvenv.cfg'
    if (Test-Path -LiteralPath $configuration) {
        foreach ($line in Get-Content -LiteralPath $configuration) {
            if ($line -match '^\s*home\s*=\s*(.+?)\s*$') { $paths += Join-Path $Matches[1] 'python.exe' }
            elseif ($line -match '^\s*executable\s*=\s*(.+?)\s*$') { $paths += $Matches[1] }
        }
    }
    return @($paths | Select-Object -Unique)
}

function Get-TestFixRecord {
    param([string]$ProjectRoot, [int]$ServiceId)
    $recordPath = Join-Path $ProjectRoot 'artifacts\service\service.json'
    if (-not (Test-Path -LiteralPath $recordPath)) { return $null }
    $record = Get-Content -LiteralPath $recordPath -Raw | ConvertFrom-Json
    $pythonPath = Join-Path $ProjectRoot '.venv\Scripts\python.exe'
    if ($record.launcher_pid -ne $ServiceId -or -not (Test-TestFixSamePath $record.project_root $ProjectRoot) -or -not (Test-TestFixSamePath $record.python_path $pythonPath) -or $record.port -lt 1 -or $record.port -gt 65535) {
        throw 'Managed service record does not match this workspace and PID; refusing process control.'
    }
    return $record
}

function Get-TestFixManagedService {
    param([string]$ProjectRoot, [int]$ServiceId, $Record = $null)
    $pythonPath = Join-Path $ProjectRoot '.venv\Scripts\python.exe'
    $pythonPaths = @(Get-TestFixPythonPaths $ProjectRoot)
    $launcher = Get-CimInstance Win32_Process -Filter "ProcessId=$ServiceId" -ErrorAction SilentlyContinue
    $children = @(Get-CimInstance Win32_Process -Filter "ParentProcessId=$ServiceId" -ErrorAction SilentlyContinue)
    $owned = @()
    $servicePort = $null
    if ($launcher) {
        $servicePort = Get-TestFixCommandPort $launcher.CommandLine $pythonPath
        if ($null -eq $servicePort -or -not (Test-TestFixSamePath $launcher.ExecutablePath $pythonPath)) {
            throw 'PID no longer belongs to this workspace TestFix launcher; refusing to stop it.'
        }
        if ($Record -and ($Record.port -ne $servicePort -or ($Record.launcher_created_utc -and -not (Test-TestFixCreationIdentity $Record.launcher_created_utc $launcher)))) {
            throw 'Managed launcher PID was reused or its command changed; refusing process control.'
        }
        $owned += $launcher
    } elseif ($Record) { $servicePort = [int]$Record.port }

    foreach ($child in $children) {
        $childPort = Get-TestFixCommandPort $child.CommandLine $pythonPath
        if ($null -eq $childPort) { continue }
        if ($null -ne $servicePort -and $childPort -ne $servicePort) { continue }
        $knownExecutable = @($pythonPaths | Where-Object { Test-TestFixSamePath $_ $child.ExecutablePath }).Count -gt 0
        if (-not $knownExecutable) { throw 'Associated service child uses an unexpected Python executable; refusing process control.' }
        $parentBirth = $(if ($launcher) { Get-TestFixProcessStamp $launcher } elseif ($Record) { $Record.launcher_created_utc } else { $null })
        if ($parentBirth -and $child.CreationDate -and ([datetime]$child.CreationDate).ToUniversalTime().Ticks -lt ([datetime]$parentBirth).ToUniversalTime().Ticks) {
            throw 'Associated child predates this launcher; its parent PID was reused and it was left running.'
        }
        if (-not $launcher) {
            # A saved creation identity is required when the launcher has gone;
            # ParentProcessId alone could refer to a subsequently reused PID.
            if (-not $Record -or $Record.server_pid -ne $child.ProcessId -or -not (Test-TestFixCreationIdentity $Record.server_created_utc $child)) {
                throw 'An orphan service child cannot be verified from the saved identity; it was left running.'
            }
        }
        $servicePort = $childPort
        $owned += $child
    }
    $launcherStamp = $(if ($launcher) { Get-TestFixProcessStamp $launcher } elseif ($Record) { $Record.launcher_created_utc } else { $null })
    return [pscustomobject]@{ Launcher = $launcher; Processes = @($owned); Port = $servicePort; LauncherId = $ServiceId; LauncherCreatedUtc = $launcherStamp }
}

function Get-TestFixListeners {
    param([int]$Port)
    return @(Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue)
}

function Assert-TestFixPortOwnership {
    param($Service, [int]$Port)
    $ownedIds = @($Service.Processes | ForEach-Object { [int]$_.ProcessId })
    $listeners = @(Get-TestFixListeners $Port)
    foreach ($listener in $listeners) {
        if ([int]$listener.OwningProcess -notin $ownedIds) {
            throw "Port $Port belongs to another process (PID $($listener.OwningProcess)); it was left running."
        }
    }
    return $listeners
}

function Write-TestFixRecord {
    param([string]$ProjectRoot, $Service, [int]$ServerId = 0)
    $server = $Service.Processes | Where-Object { $_.ProcessId -eq $ServerId } | Select-Object -First 1
    if (-not $server -and $ServerId -eq 0) {
        $server = $Service.Processes | Where-Object { $_.ProcessId -ne $Service.LauncherId } | Select-Object -First 1
        if (-not $server) { $server = $Service.Launcher }
    }
    $record = [ordered]@{
        project_root = $ProjectRoot
        python_path = (Join-Path $ProjectRoot '.venv\Scripts\python.exe')
        launcher_pid = $Service.LauncherId
        launcher_created_utc = $Service.LauncherCreatedUtc
        server_pid = $(if ($server) { [int]$server.ProcessId } else { $null })
        server_created_utc = (Get-TestFixProcessStamp $server)
        port = $Service.Port
    }
    $record | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $ProjectRoot 'artifacts\service\service.json') -Encoding UTF8
}

function Invoke-TestFixTreeTermination {
    param([int]$ServiceId)
    $taskkillPath = Join-Path $env:SystemRoot 'System32\taskkill.exe'
    $killOutput = & $taskkillPath /PID "$ServiceId" /T /F 2>&1
    if ($LASTEXITCODE -ne 0) { throw "Failed to stop the verified service tree (PID $ServiceId): $killOutput" }
}

function Invoke-TestFixStop {
    param([string]$ProjectRoot, [int]$ExpectedServiceId = 0, $ExpectedLauncherStamp = $null)
    $serviceLock = Enter-TestFixServiceLock $ProjectRoot
    try {
    $pidPath = Join-Path $ProjectRoot 'artifacts\service\pid.txt'
    if (-not (Test-Path -LiteralPath $pidPath)) { Write-Output 'No managed service PID'; return }
    $serviceText = (Get-Content -LiteralPath $pidPath -Raw).Trim()
    $serviceId = 0
    if (-not [int]::TryParse($serviceText, [ref]$serviceId) -or $serviceId -le 0) { throw 'Invalid managed service PID; refusing process control.' }
    if ($ExpectedServiceId -gt 0 -and $serviceId -ne $ExpectedServiceId) { throw 'Managed PID changed since this startup; refusing cleanup of another launch.' }
    $record = Get-TestFixRecord $ProjectRoot $serviceId
    $service = Get-TestFixManagedService $ProjectRoot $serviceId $record
    if ($ExpectedLauncherStamp -and -not (Test-TestFixCreationIdentity $ExpectedLauncherStamp ([pscustomobject]@{CreationDate=$service.LauncherCreatedUtc}))) {
        throw 'Launcher creation identity changed since this startup; refusing cleanup.'
    }
    if ($service.Port) { $null = Assert-TestFixPortOwnership $service $service.Port }
    if ($service.Processes.Count) {
        # Resolve and validate again immediately before termination. A launcher
        # tree stop includes the real base-interpreter child used by Windows venvs.
        $recheckRecord = $record
        if (-not $recheckRecord) {
            $knownServer = $service.Processes | Where-Object { $_.ProcessId -ne $serviceId } | Select-Object -First 1
            if (-not $knownServer) { $knownServer = $service.Launcher }
            $recheckRecord = [pscustomobject]@{port=$service.Port; launcher_created_utc=$service.LauncherCreatedUtc; server_pid=$knownServer.ProcessId; server_created_utc=(Get-TestFixProcessStamp $knownServer)}
        }
        $service = Get-TestFixManagedService $ProjectRoot $serviceId $recheckRecord
        if ($service.Port) { $null = Assert-TestFixPortOwnership $service $service.Port }
        $treeRoot = $(if ($service.Launcher) { $serviceId } else { [int]$service.Processes[0].ProcessId })
        if ($treeRoot -gt 0) { Invoke-TestFixTreeTermination $treeRoot }
        $deadline = [datetime]::UtcNow.AddSeconds(8)
        do {
            $remaining = @()
            foreach ($prior in $service.Processes) {
                $current = Get-CimInstance Win32_Process -Filter "ProcessId=$($prior.ProcessId)" -ErrorAction SilentlyContinue
                if ($current -and (Get-TestFixProcessStamp $current) -eq (Get-TestFixProcessStamp $prior)) { $remaining += $current }
            }
            if (-not $remaining.Count) { break }
            Start-Sleep -Milliseconds 100
        } while ([datetime]::UtcNow -lt $deadline)
        if ($remaining.Count) { throw 'The verified TestFix process tree is still running; PID records were retained.' }
    }
    if ((Get-Content -LiteralPath $pidPath -Raw).Trim() -ne "$serviceId") { throw 'Managed PID changed during stop; new records were retained.' }
    Remove-Item -LiteralPath $pidPath
    $recordPath = Join-Path $ProjectRoot 'artifacts\service\service.json'
    if (Test-Path -LiteralPath $recordPath) { Remove-Item -LiteralPath $recordPath }
    Write-Output 'TestFix stopped (verified launcher and server tree)'
    } finally { Exit-TestFixServiceLock $serviceLock }
}

if (-not $FunctionsOnly) {
    $projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
    Invoke-TestFixStop $projectRoot
}
