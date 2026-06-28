param(
    [switch] $LibraryOnly,
    [int] $StartupTimeoutSec = 120
)

$ErrorActionPreference = "Stop"

function Get-BackendScriptKind {
    param([AllowNull()][string] $CommandLine)

    if (-not $CommandLine) {
        return $null
    }

    $normalized = $CommandLine -replace "/", "\"
    $voicePattern = '(?i)(?:^|[\s"''])(?:[A-Z]:\\[^"'']*\\)?(?:backend\\)?services\\voice_server\.py(?=$|[\s"''])'
    $webPattern = '(?i)(?:^|[\s"''])(?:[A-Z]:\\[^"'']*\\backend\\server\.py|backend\\server\.py|server\.py)(?=$|[\s"''])'

    if ($normalized -match $voicePattern) {
        return "voice"
    }
    if ($normalized -match $webPattern) {
        return "web"
    }
    return $null
}

function Get-ListeningProcessIds {
    param([int[]] $Ports)

    $ids = @()
    foreach ($port in $Ports) {
        $connections = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
        foreach ($connection in $connections) {
            if ($connection.OwningProcess -gt 0) {
                $ids += [int]$connection.OwningProcess
            }
        }
    }
    return @($ids | Sort-Object -Unique)
}

function Test-IsSafeBackendOrphan {
    param(
        [AllowNull()][string] $CommandLine,
        [string] $ProjectRoot
    )

    $kind = Get-BackendScriptKind $CommandLine
    if (-not $kind) {
        return $false
    }
    if ($kind -eq "voice") {
        return $true
    }

    $normalized = ($CommandLine -replace "/", "\")
    $normalizedRoot = ($ProjectRoot -replace "/", "\").TrimEnd("\")
    return (
        $normalized.IndexOf($normalizedRoot, [StringComparison]::OrdinalIgnoreCase) -ge 0 -or
        $normalized -match '(?i)(?:^|[\s"''])backend\\server\.py(?=$|[\s"''])'
    )
}

function Get-SafeBackendOrphanProcessIds {
    param([string] $ProjectRoot)

    $ids = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
        Where-Object {
            $_.Name -match '^(pythonw?|powershell|cmd)\.exe$' -and
            (Test-IsSafeBackendOrphan $_.CommandLine $ProjectRoot)
        } |
        Select-Object -ExpandProperty ProcessId
    return @($ids | ForEach-Object { [int]$_ } | Sort-Object -Unique)
}

function Get-BackendTreeRoots {
    param([int[]] $ProcessIds)

    $roots = @()
    foreach ($processId in $ProcessIds) {
        $currentId = $processId
        $rootId = $processId
        for ($depth = 0; $depth -lt 8 -and $currentId -gt 0; $depth++) {
            $process = Get-CimInstance Win32_Process -Filter "ProcessId=$currentId" -ErrorAction SilentlyContinue
            if (-not $process) {
                break
            }
            if (Get-BackendScriptKind $process.CommandLine) {
                $rootId = [int]$process.ProcessId
            }
            $currentId = [int]$process.ParentProcessId
        }
        $roots += $rootId
    }
    return @($roots | Sort-Object -Unique)
}

function Stop-ProcessTree {
    param([int] $ProcessId)

    if ($ProcessId -le 0 -or $ProcessId -eq $PID) {
        return
    }
    & taskkill.exe /PID $ProcessId /T /F *> $null
}

function Stop-DedicatedBackendWindows {
    param([string[]] $WindowTitles)

    foreach ($title in $WindowTitles) {
        Get-Process powershell -ErrorAction SilentlyContinue |
            Where-Object { $_.MainWindowTitle -like "$title*" } |
            ForEach-Object { Stop-ProcessTree -ProcessId $_.Id }
    }
}

function Wait-PortsFree {
    param(
        [int[]] $Ports,
        [int] $TimeoutSec = 15
    )

    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    do {
        if ((Get-ListeningProcessIds $Ports).Count -eq 0) {
            return $true
        }
        Start-Sleep -Milliseconds 250
    } while ((Get-Date) -lt $deadline)
    return $false
}

function Wait-PortListening {
    param(
        [int] $Port,
        [int] $TimeoutSec
    )

    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    do {
        if ((Get-ListeningProcessIds @($Port)).Count -gt 0) {
            return $true
        }
        Start-Sleep -Milliseconds 500
    } while ((Get-Date) -lt $deadline)
    return $false
}

function Wait-PortOwnedByScript {
    param(
        [int] $Port,
        [string] $ScriptPath,
        [int] $TimeoutSec
    )

    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    do {
        $connections = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
        foreach ($connection in $connections) {
            $process = Get-CimInstance Win32_Process `
                -Filter "ProcessId=$($connection.OwningProcess)" `
                -ErrorAction SilentlyContinue
            if (
                $process -and $process.CommandLine -and
                $process.CommandLine.IndexOf($ScriptPath, [StringComparison]::OrdinalIgnoreCase) -ge 0
            ) {
                return $true
            }
        }
        Start-Sleep -Milliseconds 500
    } while ((Get-Date) -lt $deadline)
    return $false
}

function Test-SqliteWriteAccess {
    param(
        [string] $PythonExe,
        [string] $DatabasePath
    )

    $probe = @'
import sqlite3
import sys

connection = sqlite3.connect(sys.argv[2])
try:
    connection.execute("BEGIN IMMEDIATE")
    connection.execute("CREATE TABLE __restart_write_probe(id INTEGER)")
    connection.rollback()
finally:
    connection.close()
'@
    $encodedProbe = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($probe))
    $runner = "import base64,sys;exec(base64.b64decode(sys.argv[1]))"
    $previousPreference = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    & $PythonExe -c $runner $encodedProbe $DatabasePath *> $null
    $exitCode = $LASTEXITCODE
    $ErrorActionPreference = $previousPreference
    return $exitCode -eq 0
}

function Get-BasePythonPath {
    param(
        [string] $BackendDir,
        [string] $VenvPython
    )

    $configPath = Join-Path $BackendDir ".venv\pyvenv.cfg"
    if (Test-Path -LiteralPath $configPath) {
        $homeLine = Get-Content -LiteralPath $configPath |
            Where-Object { $_ -match '^\s*home\s*=' } |
            Select-Object -First 1
        if ($homeLine) {
            $pythonHome = ($homeLine -split "=", 2)[1].Trim()
            $candidate = Join-Path $pythonHome "python.exe"
            if (Test-Path -LiteralPath $candidate) {
                return $candidate
            }
        }
    }
    return $VenvPython
}

function Show-ControlledFolderAccessHelp {
    param(
        [string] $VenvPython,
        [string] $BasePython
    )

    Write-Host "[ERROR] Python cannot create an SQLite journal in backend." -ForegroundColor Red
    Write-Host "Windows Defender Controlled Folder Access may be blocking it."
    Write-Host "Run PowerShell as Administrator once:"
    Write-Host "  Add-MpPreference -ControlledFolderAccessAllowedApplications '$VenvPython'"
    if ($BasePython -ne $VenvPython) {
        Write-Host "  Add-MpPreference -ControlledFolderAccessAllowedApplications '$BasePython'"
    }
}

function Start-BackendWindow {
    param(
        [string] $Title,
        [string] $BackendDir,
        [string] $PythonExe,
        [string] $ScriptPath
    )

    $escapedTitle = $Title.Replace("'", "''")
    $escapedBackend = $BackendDir.Replace("'", "''")
    $escapedPython = $PythonExe.Replace("'", "''")
    $escapedScript = $ScriptPath.Replace("'", "''")
    $command = @"
`$Host.UI.RawUI.WindowTitle='$escapedTitle'
Set-Location -LiteralPath '$escapedBackend'
& '$escapedPython' '$escapedScript'
if (`$LASTEXITCODE -ne 0) {
    Write-Host '[ERROR] Backend process exited with code' `$LASTEXITCODE -ForegroundColor Red
}
"@
    $encoded = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($command))
    Start-Process powershell.exe `
        -WorkingDirectory $BackendDir `
        -ArgumentList @("-NoExit", "-NoProfile", "-ExecutionPolicy", "Bypass", "-EncodedCommand", $encoded) |
        Out-Null
}

if ($LibraryOnly) {
    return
}

$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$backendDir = Join-Path $projectRoot "backend"
$pythonExe = Join-Path $backendDir ".venv\Scripts\python.exe"
$webScript = Join-Path $backendDir "server.py"
$voiceScript = Join-Path $backendDir "services\voice_server.py"
$databasePath = Join-Path $backendDir "upload.db"
$ports = @(8765, 8888)
$windowTitles = @("Digital Album - Web Backend", "Digital Album - Voice Backend")

foreach ($requiredPath in @($pythonExe, $webScript, $voiceScript, $databasePath)) {
    if (-not (Test-Path -LiteralPath $requiredPath)) {
        throw "Required backend path not found: $requiredPath"
    }
}

$basePython = Get-BasePythonPath -BackendDir $backendDir -VenvPython $pythonExe
if (-not (Test-SqliteWriteAccess -PythonExe $pythonExe -DatabasePath $databasePath)) {
    Show-ControlledFolderAccessHelp -VenvPython $pythonExe -BasePython $basePython
    exit 2
}

Write-Host "Closing existing backend windows and listeners..."
Stop-DedicatedBackendWindows -WindowTitles $windowTitles
$listenerIds = Get-ListeningProcessIds $ports
$orphanIds = Get-SafeBackendOrphanProcessIds -ProjectRoot $projectRoot
$targetIds = @($listenerIds + $orphanIds | Sort-Object -Unique)
$treeRoots = Get-BackendTreeRoots $targetIds
foreach ($rootId in $treeRoots) {
    Stop-ProcessTree -ProcessId $rootId
}
foreach ($targetId in $targetIds) {
    if (Get-Process -Id $targetId -ErrorAction SilentlyContinue) {
        Stop-ProcessTree -ProcessId $targetId
    }
}

if (-not (Wait-PortsFree -Ports $ports -TimeoutSec 15)) {
    $remaining = Get-ListeningProcessIds $ports
    throw "Backend ports are still occupied by PID(s): $($remaining -join ', ')"
}

Write-Host "Starting Flask backend on port 8765..."
Start-BackendWindow `
    -Title $windowTitles[0] `
    -BackendDir $backendDir `
    -PythonExe $pythonExe `
    -ScriptPath $webScript

Write-Host "Starting voice backend on port 8888..."
Start-BackendWindow `
    -Title $windowTitles[1] `
    -BackendDir $backendDir `
    -PythonExe $pythonExe `
    -ScriptPath $voiceScript

$webReady = Wait-PortOwnedByScript `
    -Port 8765 `
    -ScriptPath $webScript `
    -TimeoutSec $StartupTimeoutSec
$voiceReady = Wait-PortOwnedByScript `
    -Port 8888 `
    -ScriptPath $voiceScript `
    -TimeoutSec $StartupTimeoutSec
if (-not $webReady -or -not $voiceReady) {
    throw "Backend startup failed. Port status: 8765=$webReady, 8888=$voiceReady"
}

Write-Host "Backend restart completed." -ForegroundColor Green
Write-Host "Web UI: http://127.0.0.1:8765"
