$ErrorActionPreference = "Stop"

$scriptPath = Join-Path (Split-Path $PSScriptRoot -Parent) "configure_local_ip.ps1"
if (-not (Test-Path -LiteralPath $scriptPath)) {
    throw "Missing implementation: $scriptPath"
}

$tmp = Join-Path $env:TEMP ("sdkconfig-test-" + [Guid]::NewGuid().ToString("N"))
try {
    @(
        "CONFIG_SERVER_URL=`"http://old.example:8765`"",
        "CONFIG_VA_WS_URI=`"ws://old.example:8888`"",
        "CONFIG_OTHER_VALUE=y"
    ) | Set-Content -LiteralPath $tmp -Encoding UTF8

    & $scriptPath -IpAddress "192.168.50.23" -ServerPort 8765 -VoicePort 8888 -SdkconfigPath $tmp *> $null

    $content = Get-Content -LiteralPath $tmp
    if ($content -notcontains 'CONFIG_SERVER_URL="http://192.168.50.23:8765"') {
        throw "CONFIG_SERVER_URL was not rewritten."
    }
    if ($content -notcontains 'CONFIG_VA_WS_URI="ws://192.168.50.23:8888"') {
        throw "CONFIG_VA_WS_URI was not rewritten."
    }
    if ($content -notcontains "CONFIG_OTHER_VALUE=y") {
        throw "Unrelated sdkconfig value was not preserved."
    }

    Write-Output "configure_local_ip tests passed"
} finally {
    Remove-Item -LiteralPath $tmp -Force -ErrorAction SilentlyContinue
}
