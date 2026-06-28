$ErrorActionPreference = "Stop"

$scriptPath = Join-Path (Split-Path $PSScriptRoot -Parent) "restart_backend.ps1"
if (-not (Test-Path -LiteralPath $scriptPath)) {
    throw "Missing implementation: $scriptPath"
}

. $scriptPath -LibraryOnly

function Assert-Equal {
    param(
        [Parameter(Mandatory = $true)][AllowNull()] $Expected,
        [Parameter(Mandatory = $true)][AllowNull()] $Actual,
        [Parameter(Mandatory = $true)] [string] $Message
    )
    if ($Expected -ne $Actual) {
        throw "$Message Expected=$Expected Actual=$Actual"
    }
}

Assert-Equal "voice" (Get-BackendScriptKind '"C:\Python310\python.exe" services\voice_server.py') `
    "Relative voice service path must be recognized."
Assert-Equal "voice" (Get-BackendScriptKind '"C:\Python310\python.exe" backend\services\voice_server.py') `
    "Repository-relative voice service path must be recognized."
Assert-Equal "web" (Get-BackendScriptKind '"C:\Python310\python.exe" "C:\repo\backend\server.py"') `
    "Absolute Flask server path must be recognized."
Assert-Equal $null (Get-BackendScriptKind '"C:\Python310\python.exe" other_server.py') `
    "Unrelated Python scripts must not be recognized."
Assert-Equal $null (Get-BackendScriptKind '"C:\Python310\python.exe" tools\server.py') `
    "A generic server.py below another folder must not be recognized."
Assert-Equal $true (Test-IsSafeBackendOrphan '"C:\Python310\python.exe" backend\services\voice_server.py' "C:\repo") `
    "A repository-relative voice process must be safe to clean."
Assert-Equal $true (Test-IsSafeBackendOrphan '"C:\Python310\python.exe" backend\server.py' "C:\repo") `
    "A repository-relative Flask process must be safe to clean."
Assert-Equal $false (Test-IsSafeBackendOrphan '"C:\Python310\python.exe" server.py' "C:\repo") `
    "A bare server.py process from an unknown project must not be cleaned."

$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$pythonExe = Join-Path $projectRoot "backend\.venv\Scripts\python.exe"
$databasePath = Join-Path $projectRoot "backend\upload.db"
Assert-Equal $true (Test-SqliteWriteAccess -PythonExe $pythonExe -DatabasePath $databasePath) `
    "SQLite write probe must succeed when Python is allowed by Controlled Folder Access."

Write-Output "restart backend tests passed"
