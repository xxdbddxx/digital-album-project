param(
    [string] $Port = "COM11"
)

$ErrorActionPreference = "Continue"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$backendDir = Join-Path $projectRoot "backend"
$checks = @()

function Add-Check {
    param(
        [string] $Name,
        [bool] $Ok,
        [string] $Detail
    )
    $status = "FAIL"
    if ($Ok) {
        $status = "OK"
    }
    $script:checks += [PSCustomObject]@{
        Check = $Name
        Status = $status
        Detail = $Detail
    }
}

$idfWrapper = Join-Path $projectRoot "scripts\idf.ps1"
Add-Check "ESP-IDF wrapper" (Test-Path -LiteralPath $idfWrapper) $idfWrapper

$venvPython = Join-Path $backendDir ".venv\Scripts\python.exe"
Add-Check "Backend venv" (Test-Path -LiteralPath $venvPython) $venvPython

$envLocal = Join-Path $backendDir ".env.local"
Add-Check "Backend .env.local" (Test-Path -LiteralPath $envLocal) "$envLocal (local secrets, not committed)"

$sdkconfig = Join-Path $projectRoot "sdkconfig"
$sdkconfigOk = Test-Path -LiteralPath $sdkconfig
Add-Check "sdkconfig" $sdkconfigOk "$sdkconfig (local machine config, not committed)"

$preferredIp = $null
$ipCandidates = Get-NetIPConfiguration |
    Where-Object { $_.IPv4DefaultGateway -and $_.IPv4Address } |
    ForEach-Object {
        $score = 10
        if ($_.InterfaceAlias -match "Wi-?Fi|WLAN|Ethernet") {
            $score = 0
        }
        foreach ($address in $_.IPv4Address) {
            [PSCustomObject]@{
                InterfaceAlias = $_.InterfaceAlias
                IPAddress = $address.IPAddress
                Score = $score
            }
        }
    } |
    Where-Object { $_.IPAddress -notmatch "^(127|169\.254)\." } |
    Sort-Object Score, InterfaceAlias
if (@($ipCandidates).Count -gt 0) {
    $preferredIp = @($ipCandidates)[0].IPAddress
}
Add-Check "LAN IPv4" ($null -ne $preferredIp) ((@($ipCandidates) | ForEach-Object { "$($_.InterfaceAlias)=$($_.IPAddress)" }) -join ", ")

if ($sdkconfigOk) {
    $serverLine = Select-String -Path $sdkconfig -Pattern '^CONFIG_SERVER_URL=' -ErrorAction SilentlyContinue
    $voiceLine = Select-String -Path $sdkconfig -Pattern '^CONFIG_VA_WS_URI=' -ErrorAction SilentlyContinue
    $hasPreferredIp = $preferredIp -and (($serverLine.Line -match [regex]::Escape($preferredIp)) -and ($voiceLine.Line -match [regex]::Escape($preferredIp)))
    Add-Check "sdkconfig IP matches first LAN IP" $hasPreferredIp "server=$($serverLine.Line); voice=$($voiceLine.Line)"
}

foreach ($backendPort in 8765, 8888) {
    $listener = Get-NetTCPConnection -LocalPort $backendPort -State Listen -ErrorAction SilentlyContinue
    Add-Check "Port $backendPort listening" ($null -ne $listener) (($listener | Select-Object -First 1 | ForEach-Object { "PID=$($_.OwningProcess)" }) -join "")
}

$serial = Get-CimInstance Win32_SerialPort -ErrorAction SilentlyContinue | Where-Object { $_.DeviceID -eq $Port }
Add-Check "Serial $Port" ($null -ne $serial) (($serial | Select-Object -First 1 | ForEach-Object { $_.Name }) -join "")

$checks | Format-Table -AutoSize
if ($checks.Status -contains "FAIL") {
    Write-Host ""
    Write-Host "Fix failed checks, then rerun scripts/doctor.ps1. For IP mismatch run: .\scripts\configure_local_ip.ps1" -ForegroundColor Yellow
    exit 1
}

Write-Host ""
Write-Host "Environment checks passed." -ForegroundColor Green
