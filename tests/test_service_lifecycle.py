"""Exercise PowerShell lifecycle guards with a fake OS process inventory.

No real service, listener, process launch, or process termination is used.
"""
from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]
POWERSHELL = shutil.which("pwsh") or shutil.which("powershell")
pytestmark = pytest.mark.skipif(os.name != "nt" or not POWERSHELL, reason="Windows PowerShell lifecycle scripts")


def literal(value) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def fixture_root(tmp_path: Path) -> Path:
    root = tmp_path / "fixture workspace"
    (root / "scripts").mkdir(parents=True)
    for name in ("start.ps1", "stop.ps1"):
        shutil.copy2(ROOT / "scripts" / name, root / "scripts" / name)
    (root / ".venv/Scripts").mkdir(parents=True)
    (root / ".venv/Scripts/python.exe").write_text("never executed\n", encoding="utf-8")
    (root / ".venv/pyvenv.cfg").write_text("home = " + str(root / "base-python") + "\n", encoding="utf-8")
    (root / "artifacts/service").mkdir(parents=True)
    return root


def powershell(script: str) -> dict:
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    result = subprocess.run([POWERSHELL, "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
                            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=45)
    assert result.returncode == 0, result.stdout + "\n" + result.stderr
    return json.loads(result.stdout.strip().splitlines()[-1])


def mock_prelude(root: Path) -> str:
    return r"""
$ErrorActionPreference = 'Stop'
$fixtureRoot = ROOT_LITERAL
. (Join-Path $fixtureRoot 'scripts/stop.ps1') -FunctionsOnly
function Assert-Fixture { param($Condition, [string]$Message) if (-not $Condition) { throw $Message } }
function Assert-FixtureThrows {
    param([scriptblock]$Action, [string]$Pattern)
    $caught = $false
    try { & $Action | Out-Null } catch {
        if ($_.Exception.Message -notmatch $Pattern) { throw "Wrong rejection: $($_.Exception.Message)" }
        $caught = $true
    }
    Assert-Fixture $caught "Expected rejection: $Pattern"
}
function Reset-Fixture {
    $global:TestfixFakeRoot = $fixtureRoot
    $global:TestfixFakePython = Join-Path $fixtureRoot '.venv/Scripts/python.exe'
    $global:TestfixFakeCommand = '"' + $global:TestfixFakePython + '" -m testfix.cli serve --port 9876'
    $global:TestfixFakeProcesses = @(
        [pscustomobject]@{ProcessId=990001; ParentProcessId=100; ExecutablePath=$global:TestfixFakePython; CommandLine=$global:TestfixFakeCommand; CreationDate=[datetime]'2026-10-15T01:35:24Z'},
        [pscustomobject]@{ProcessId=990002; ParentProcessId=990001; ExecutablePath=(Join-Path $fixtureRoot 'base-python/python.exe'); CommandLine=$global:TestfixFakeCommand; CreationDate=[datetime]'2026-10-15T01:35:25Z'},
        [pscustomobject]@{ProcessId=990003; ParentProcessId=100; ExecutablePath=(Join-Path $fixtureRoot 'base-python/python.exe'); CommandLine='python unrelated.py'; CreationDate=[datetime]'2026-10-15T01:35:26Z'}
    )
    $global:TestfixFakeOwner = 990002
    $global:TestfixFakeListening = $true
    $global:TestfixFakeTerminated = @()
    $global:TestfixFakeLaunches = @()
    $global:TestfixFakeHealth = @()
    $global:TestfixFakeReuseOnRecheck = $false
    $global:TestfixFakeLauncherQueries = 0
    $global:TestfixFakeLauncherDiesBeforeHealth = $false
    '990001' | Set-Content -LiteralPath (Join-Path $fixtureRoot 'artifacts/service/pid.txt')
    $metadata = Join-Path $fixtureRoot 'artifacts/service/service.json'
    if (Test-Path -LiteralPath $metadata) { Remove-Item -LiteralPath $metadata }
}
function Get-CimInstance {
    param($ClassName, $Filter, $ErrorAction)
    if ($Filter -match '^ProcessId=(\d+)$') {
        $requestedId = [int]$Matches[1]
        if ($global:TestfixFakeReuseOnRecheck -and $requestedId -eq 990001) {
            $global:TestfixFakeLauncherQueries += 1
            if ($global:TestfixFakeLauncherQueries -eq 2) { $global:TestfixFakeProcesses[0].CreationDate = ([datetime]$global:TestfixFakeProcesses[0].CreationDate).AddSeconds(1) }
        }
        return $global:TestfixFakeProcesses | Where-Object { $_.ProcessId -eq $requestedId }
    }
    if ($Filter -match '^ParentProcessId=(\d+)$') { return $global:TestfixFakeProcesses | Where-Object { $_.ParentProcessId -eq [int]$Matches[1] } }
    throw "Unexpected OS query: $Filter"
}
function Get-NetTCPConnection {
    param($LocalPort, $State, $ErrorAction)
    if ($global:TestfixFakeListening -and $LocalPort -eq 9876) { return [pscustomobject]@{OwningProcess=$global:TestfixFakeOwner} }
}
function Invoke-TestFixTreeTermination {
    param([int]$ServiceId)
    $global:TestfixFakeTerminated += $ServiceId
    $remove = @($ServiceId)
    foreach ($item in $global:TestfixFakeProcesses) { if ($item.ParentProcessId -in $remove) { $remove += $item.ProcessId } }
    $global:TestfixFakeProcesses = @($global:TestfixFakeProcesses | Where-Object { $_.ProcessId -notin $remove })
    if ($global:TestfixFakeOwner -in $remove) { $global:TestfixFakeListening = $false }
}
function Invoke-RestMethod {
    param($Uri, $TimeoutSec)
    $global:TestfixFakeHealth += $Uri
    if ($global:TestfixFakeLauncherDiesBeforeHealth) {
        $global:TestfixFakeLauncherDiesBeforeHealth = $false
        $global:TestfixFakeProcesses = @($global:TestfixFakeProcesses | Where-Object { $_.ProcessId -ne 990001 })
        throw 'temporary startup health failure'
    }
    return [pscustomobject]@{status='ok'; version='unit-fixture'; providers=@()}
}
function Start-Process {
    param($FilePath, $ArgumentList, $WorkingDirectory, $WindowStyle, $RedirectStandardOutput, $RedirectStandardError, [switch]$PassThru)
    $global:TestfixFakeLaunches += [pscustomobject]@{FilePath=$FilePath; Arguments=@($ArgumentList); WorkingDirectory=$WorkingDirectory; WindowStyle=$WindowStyle; Stdout=$RedirectStandardOutput; Stderr=$RedirectStandardError}
    $global:TestfixFakeProcesses = @(
        [pscustomobject]@{ProcessId=990001; ParentProcessId=100; ExecutablePath=$FilePath; CommandLine=('"' + $FilePath + '" ' + ($ArgumentList -join ' ')); CreationDate=[datetime]'2026-10-15T01:35:24Z'},
        [pscustomobject]@{ProcessId=990002; ParentProcessId=990001; ExecutablePath=(Join-Path $global:TestfixFakeRoot 'base-python/python.exe'); CommandLine=('"' + $FilePath + '" ' + ($ArgumentList -join ' ')); CreationDate=[datetime]'2026-10-15T01:35:25Z'}
    )
    $global:TestfixFakeListening = $true
    $global:TestfixFakeOwner = 990002
    return [pscustomobject]@{Id=990001}
}
Reset-Fixture
""".replace("ROOT_LITERAL", literal(root))


def test_scripts_parse_without_running_them():
    result = powershell(r"""$errorsFound = @()
foreach ($script in SCRIPT_PATHS) {
    $parseTokens = $null
    $parseErrors = $null
    [Management.Automation.Language.Parser]::ParseFile($script,[ref]$parseTokens,[ref]$parseErrors) | Out-Null
    $errorsFound += @($parseErrors | ForEach-Object { $_.Message })
}
@{errors=@($errorsFound)} | ConvertTo-Json -Compress
""".replace("SCRIPT_PATHS", "@(" + ",".join(literal(ROOT / "scripts" / name) for name in ("start.ps1", "stop.ps1")) + ")"))
    assert result == {"errors": []}


def test_complete_command_ancestry_and_saved_creation_identity_are_required(tmp_path):
    root = fixture_root(tmp_path)
    result = powershell(mock_prelude(root) + r"""
$valid = Get-TestFixCommandPort $global:TestfixFakeCommand $global:TestfixFakePython
Assert-Fixture ($valid -eq 9876) 'Quoted workspace Python was not recognized'
foreach ($command in @(
    ($global:TestfixFakeCommand + ' --reload'),
    ('prefix ' + $global:TestfixFakeCommand),
    ($global:TestfixFakeCommand -replace 'testfix\.cli', 'testfixXcli'),
    ($global:TestfixFakeCommand -replace '9876', '0'),
    ($global:TestfixFakeCommand -replace '9876', '65536'),
    ('"' + (Join-Path $fixtureRoot 'other-workspace/python.exe') + '" -m testfix.cli serve --port 9876')
)) { Assert-Fixture ($null -eq (Get-TestFixCommandPort $command $global:TestfixFakePython)) 'An inexact service command was trusted' }
$service = Get-TestFixManagedService $fixtureRoot 990001
Assert-Fixture ($service.Processes.Count -eq 2) 'Real base-interpreter child was not associated'
Assert-Fixture (990003 -notin @($service.Processes.ProcessId)) 'Unrelated Python was associated'
Write-TestFixRecord $fixtureRoot $service 990002
$record = Get-TestFixRecord $fixtureRoot 990001
$global:TestfixFakeProcesses = @($global:TestfixFakeProcesses | Where-Object { $_.ProcessId -ne 990001 })
$orphan = Get-TestFixManagedService $fixtureRoot 990001 $record
Assert-Fixture ($orphan.Processes.Count -eq 1 -and $orphan.Processes[0].ProcessId -eq 990002) 'Saved orphan identity was not recognized'
Write-TestFixRecord $fixtureRoot $orphan 990002
$orphanRecord = Get-TestFixRecord $fixtureRoot 990001
Assert-Fixture ($orphanRecord.launcher_created_utc) 'Refreshing an orphan erased its original launcher identity'
Assert-FixtureThrows { Get-TestFixManagedService $fixtureRoot 990001 } 'orphan.*cannot be verified'
$global:TestfixFakeProcesses[0].CreationDate = ([datetime]$global:TestfixFakeProcesses[0].CreationDate).AddSeconds(1)
Assert-FixtureThrows { Get-TestFixManagedService $fixtureRoot 990001 $record } 'orphan.*cannot be verified'
Reset-Fixture
$global:TestfixFakeProcesses[0].ExecutablePath = Join-Path $fixtureRoot 'other-python/python.exe'
Assert-FixtureThrows { Get-TestFixManagedService $fixtureRoot 990001 } 'no longer belongs'
Reset-Fixture
$global:TestfixFakeProcesses[1].ExecutablePath = Join-Path $fixtureRoot 'other-python/python.exe'
Assert-FixtureThrows { Get-TestFixManagedService $fixtureRoot 990001 } 'unexpected Python'
Reset-Fixture
$global:TestfixFakeProcesses[1].CreationDate = ([datetime]$global:TestfixFakeProcesses[0].CreationDate).AddMinutes(-10)
Assert-FixtureThrows { Get-TestFixManagedService $fixtureRoot 990001 } 'child predates this launcher'
@{exact_command=$true; child_ancestry=$true; reused_pid_rejected=$true} | ConvertTo-Json -Compress
""")
    assert result == {"exact_command": True, "child_ancestry": True, "reused_pid_rejected": True}


def test_stop_targets_verified_tree_or_saved_orphan_and_retains_foreign_processes(tmp_path):
    root = fixture_root(tmp_path)
    result = powershell(mock_prelude(root) + r"""
Invoke-TestFixStop $fixtureRoot | Out-Null
Assert-Fixture ($global:TestfixFakeTerminated.Count -eq 1 -and $global:TestfixFakeTerminated[0] -eq 990001) 'Stop did not target the launcher tree'
Assert-Fixture ($global:TestfixFakeProcesses.Count -eq 1 -and $global:TestfixFakeProcesses[0].ProcessId -eq 990003) 'Stop affected unrelated Python'
Assert-Fixture (-not (Test-Path -LiteralPath (Join-Path $fixtureRoot 'artifacts/service/pid.txt'))) 'Stopped PID record remained'
Reset-Fixture
$service = Get-TestFixManagedService $fixtureRoot 990001
Write-TestFixRecord $fixtureRoot $service 990002
$global:TestfixFakeProcesses = @($global:TestfixFakeProcesses | Where-Object { $_.ProcessId -ne 990001 })
Invoke-TestFixStop $fixtureRoot | Out-Null
Assert-Fixture ($global:TestfixFakeTerminated.Count -eq 1 -and $global:TestfixFakeTerminated[0] -eq 990002) 'Verified orphan tree was not stopped'
Reset-Fixture
$global:TestfixFakeOwner = 990003
Assert-FixtureThrows { Invoke-TestFixStop $fixtureRoot } 'Port 9876 belongs to another'
Assert-Fixture ($global:TestfixFakeTerminated.Count -eq 0) 'Foreign listener caused process termination'
Assert-Fixture (Test-Path -LiteralPath (Join-Path $fixtureRoot 'artifacts/service/pid.txt')) 'Refusal deleted process evidence'
Reset-Fixture
$global:TestfixFakeProcesses[0].CommandLine = 'python unrelated.py'
Assert-FixtureThrows { Invoke-TestFixStop $fixtureRoot } 'no longer belongs'
Assert-Fixture ($global:TestfixFakeTerminated.Count -eq 0) 'A reused PID was terminated'
Reset-Fixture
$global:TestfixFakeReuseOnRecheck = $true
Assert-FixtureThrows { Invoke-TestFixStop $fixtureRoot } 'PID was reused'
Assert-Fixture ($global:TestfixFakeTerminated.Count -eq 0) 'A PID reused between validation passes was terminated'
Reset-Fixture
'990010' | Set-Content -LiteralPath (Join-Path $fixtureRoot 'artifacts/service/pid.txt')
Assert-FixtureThrows { Invoke-TestFixStop -ProjectRoot $fixtureRoot -ExpectedServiceId 990001 } 'refusing cleanup of another launch'
Assert-Fixture ($global:TestfixFakeTerminated.Count -eq 0) 'Startup cleanup terminated another invocation'
Reset-Fixture
Assert-FixtureThrows { Invoke-TestFixStop -ProjectRoot $fixtureRoot -ExpectedServiceId 990001 -ExpectedLauncherStamp ([datetime]'2026-10-15T01:00:00Z') } 'creation identity changed'
Assert-Fixture ($global:TestfixFakeTerminated.Count -eq 0) 'Startup cleanup ignored the launch creation identity'
@{tree_stop=$true; orphan_stop=$true; foreign_listener_retained=$true; reused_pid_retained=$true; recheck_reuse_retained=$true; other_launch_retained=$true} | ConvertTo-Json -Compress
""")
    assert all(result.values()) and len(result) == 6


def test_background_start_is_hidden_waits_for_owned_health_and_refuses_foreign_port(tmp_path):
    root = fixture_root(tmp_path)
    result = powershell(mock_prelude(root) + r"""
$startScript = Join-Path $fixtureRoot 'scripts/start.ps1'
$pidRecord = Join-Path $fixtureRoot 'artifacts/service/pid.txt'
Remove-Item -LiteralPath $pidRecord
$global:TestfixFakeProcesses = @()
$global:TestfixFakeListening = $false
& $startScript -Background -Port 9876 | Out-Null
Assert-Fixture ($global:TestfixFakeLaunches.Count -eq 1) 'Startup did not launch exactly once'
$launch = $global:TestfixFakeLaunches[0]
Assert-Fixture ($launch.WindowStyle -eq 'Hidden') 'Background startup opened a visible window'
Assert-Fixture (Test-TestFixSamePath $launch.FilePath $global:TestfixFakePython) 'Startup used another Python'
Assert-Fixture (($launch.Arguments -join ' ') -eq '-m testfix.cli serve --port 9876') 'Startup did not use registered CLI arguments'
Assert-Fixture ($global:TestfixFakeHealth[-1] -eq 'http://127.0.0.1:9876/api/health') 'Startup did not verify HTTP health'
$record = Get-Content -LiteralPath (Join-Path $fixtureRoot 'artifacts/service/service.json') -Raw | ConvertFrom-Json
Assert-Fixture ($record.launcher_pid -eq 990001 -and $record.server_pid -eq 990002 -and $record.server_created_utc) 'Startup did not save the real child identity'
& $startScript -Background -Port 9876 | Out-Null
Assert-Fixture ($global:TestfixFakeLaunches.Count -eq 1) 'Starting an owned healthy service launched a duplicate'
Reset-Fixture
Remove-Item -LiteralPath $pidRecord
$global:TestfixFakeProcesses = @()
$global:TestfixFakeListening = $false
$global:TestfixFakeLauncherDiesBeforeHealth = $true
& $startScript -Background -Port 9876 | Out-Null
$orphanRecord = Get-Content -LiteralPath (Join-Path $fixtureRoot 'artifacts/service/service.json') -Raw | ConvertFrom-Json
Assert-Fixture ($orphanRecord.server_pid -eq 990002 -and $orphanRecord.launcher_created_utc) 'Startup lost saved child/launcher identity after parent exit'
Assert-Fixture ($global:TestfixFakeHealth.Count -eq 2) 'Startup did not retry the saved child health'
Reset-Fixture
Remove-Item -LiteralPath $pidRecord
$global:TestfixFakeOwner = 990003
Assert-FixtureThrows { & $startScript -Background -Port 9876 } 'already occupied.*left running'
Assert-Fixture ($global:TestfixFakeLaunches.Count -eq 0) 'Foreign listener triggered another process launch'
Assert-Fixture ($global:TestfixFakeTerminated.Count -eq 0) 'Foreign listener was terminated'
@{hidden=$true; owned_health=$true; identities_saved=$true; duplicate_avoided=$true; foreign_port_refused=$true; orphan_startup=$true} | ConvertTo-Json -Compress
""")
    assert all(result.values()) and len(result) == 6
