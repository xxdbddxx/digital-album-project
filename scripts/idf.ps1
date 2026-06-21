$ErrorActionPreference = "Stop"
$IdfArgs = @($args)
if ($IdfArgs.Count -eq 0) {
    $IdfArgs = @("build")
}

$PythonHome = "C:\Users\sxxy4\AppData\Local\Programs\Python\Python310"
$IdfPath = "C:\esp\v6.0.1\esp-idf"
$IdfToolsPath = "C:\Espressif"
$IdfPythonEnv = "C:\Espressif\tools\python\v6.0.1\venv"

$requiredPaths = @(
    (Join-Path $PythonHome "python.exe"),
    (Join-Path $IdfPath "export.ps1"),
    (Join-Path $IdfPythonEnv "Scripts\python.exe")
)

foreach ($path in $requiredPaths) {
    if (-not (Test-Path -LiteralPath $path)) {
        throw "Required ESP-IDF dependency not found: $path"
    }
}

$env:IDF_PATH = $IdfPath
$env:IDF_TOOLS_PATH = $IdfToolsPath
$env:IDF_PYTHON_ENV_PATH = $IdfPythonEnv
$env:PATH = "$PythonHome;$(Join-Path $PythonHome 'Scripts');$env:PATH"

Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass -Force
. (Join-Path $IdfPath "export.ps1")

& idf.py @IdfArgs
exit $LASTEXITCODE
