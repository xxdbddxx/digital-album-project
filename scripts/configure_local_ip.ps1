param(
    [string] $IpAddress,
    [int] $ServerPort = 8765,
    [int] $VoicePort = 8888,
    [string] $SdkconfigPath
)

$ErrorActionPreference = "Stop"

function Get-ProjectRoot {
    return (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
}

function Get-PreferredIPv4Address {
    $candidates = Get-NetIPConfiguration |
        Where-Object { $_.IPv4DefaultGateway -and $_.IPv4Address } |
        ForEach-Object {
            foreach ($address in $_.IPv4Address) {
                $score = 10
                if ($_.InterfaceAlias -match "Wi-?Fi|WLAN|Ethernet") {
                    $score = 0
                }
                [PSCustomObject]@{
                    InterfaceAlias = $_.InterfaceAlias
                    IPAddress = $address.IPAddress
                    Score = $score
                }
            }
        } |
        Where-Object { $_.IPAddress -notmatch "^(127|169\.254)\." } |
        Sort-Object Score, InterfaceAlias

    $selected = @($candidates)[0]
    if (-not $selected) {
        throw "No usable IPv4 address with a default gateway was found. Pass -IpAddress explicitly."
    }
    return $selected.IPAddress
}

function Set-SdkconfigValue {
    param(
        [Parameter(Mandatory = $true)] [string[]] $Lines,
        [Parameter(Mandatory = $true)] [string] $Key,
        [Parameter(Mandatory = $true)] [string] $Value
    )

    $entry = "$Key=`"$Value`""
    $pattern = "^$([regex]::Escape($Key))="
    $updated = $false
    $result = foreach ($line in $Lines) {
        if ($line -match $pattern) {
            $updated = $true
            $entry
        } else {
            $line
        }
    }
    if (-not $updated) {
        $result += $entry
    }
    return @($result)
}

if (-not $SdkconfigPath) {
    $SdkconfigPath = Join-Path (Get-ProjectRoot) "sdkconfig"
}
if (-not (Test-Path -LiteralPath $SdkconfigPath)) {
    throw "Missing sdkconfig: $SdkconfigPath. Run '.\scripts\idf.ps1 build' once or copy sdkconfig.defaults first."
}
if (-not $IpAddress) {
    $IpAddress = Get-PreferredIPv4Address
}

$lines = Get-Content -LiteralPath $SdkconfigPath
$lines = Set-SdkconfigValue -Lines $lines -Key "CONFIG_SERVER_URL" -Value "http://$IpAddress`:$ServerPort"
$lines = Set-SdkconfigValue -Lines $lines -Key "CONFIG_VA_WS_URI" -Value "ws://$IpAddress`:$VoicePort"
Set-Content -LiteralPath $SdkconfigPath -Value $lines -Encoding UTF8

Write-Host "Updated firmware backend endpoints in $SdkconfigPath"
Write-Host "CONFIG_SERVER_URL=http://$IpAddress`:$ServerPort"
Write-Host "CONFIG_VA_WS_URI=ws://$IpAddress`:$VoicePort"
Write-Host "Next: .\scripts\idf.ps1 build ; .\scripts\idf.ps1 -p COM11 flash monitor"
