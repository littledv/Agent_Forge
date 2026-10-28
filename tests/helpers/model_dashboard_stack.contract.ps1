[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "model_dashboard_stack.ps1") -LibraryOnly

$realModelConfig = [System.IO.File]::ReadAllText(
    (Join-Path $repositoryRoot "dashboard\playwright.real.config.ts")
)
foreach ($requiredSetting in @(
    'screenshot: isRealModelRun ? "off"',
    'trace: isRealModelRun ? "off"'
)) {
    if (-not $realModelConfig.Contains($requiredSetting)) {
        throw "Real-model browser artifacts must remain disabled for private-content safety."
    }
}

$privatePrefix = "Report heading`nTicket path"
$serializedLog = @{ content = "$privatePrefix and more" } | ConvertTo-Json -Compress
if (Test-PrivateContentAbsent -Content $serializedLog -Probes @($privatePrefix)) {
    throw "Serialized newline-bearing private content was not detected."
}

$temporaryRoot = Join-Path ([System.IO.Path]::GetTempPath()) (
    "agentforge-model-stack-" + [guid]::NewGuid().ToString("N")
)
$temporaryRoot = Assert-SafeStackLogPath -Path $temporaryRoot
New-Item -ItemType Directory -Path $temporaryRoot | Out-Null
try {
    [System.IO.File]::WriteAllText((Join-Path $temporaryRoot "safe.log"), "synthetic safe log")
    try {
        Remove-StackLogsSafely `
            -Path $temporaryRoot `
            -RemoveAction { param([string]$Path) }
    }
    catch {
        if ($_.Exception.Message -notlike "*remained after cleanup*") {
            throw
        }
    }
    if (-not (Test-Path -LiteralPath $temporaryRoot -PathType Container)) {
        throw "The injected no-op cleanup unexpectedly removed the test directory."
    }
}
finally {
    [void](Assert-SafeStackLogPath -Path $temporaryRoot)
    Remove-Item -LiteralPath $temporaryRoot -Recurse -Force -ErrorAction SilentlyContinue
}

$removalRoot = Join-Path ([System.IO.Path]::GetTempPath()) (
    "agentforge-model-stack-" + [guid]::NewGuid().ToString("N")
)
$removalRoot = Assert-SafeStackLogPath -Path $removalRoot
New-Item -ItemType Directory -Path $removalRoot | Out-Null
[System.IO.File]::WriteAllText((Join-Path $removalRoot "safe.log"), "synthetic safe log")
Remove-StackLogsSafely -Path $removalRoot
if (Test-Path -LiteralPath $removalRoot) {
    throw "Verified stack-log cleanup left the directory behind."
}

Write-Host "Model dashboard stack contract checks passed."
