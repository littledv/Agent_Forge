[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "ollama_runtime.ps1") -LibraryOnly

function Assert-ThrowsLike {
    param(
        [scriptblock]$Action,
        [string]$Pattern
    )
    try {
        & $Action
    }
    catch {
        if ($_.Exception.Message -notlike $Pattern) {
            throw "Expected error like '$Pattern', got '$($_.Exception.Message)'."
        }
        return
    }
    throw "Expected action to fail with '$Pattern'."
}

$temporaryRoot = Join-Path ([System.IO.Path]::GetTempPath()) (
    "agentforge-ollama-contract-" + [guid]::NewGuid().ToString("N")
)
$temporaryRoot = Assert-SafeChildPath `
    -Path $temporaryRoot `
    -Parent ([System.IO.Path]::GetTempPath()) `
    -LeafPrefix "agentforge-ollama-contract-"
New-Item -ItemType Directory -Path $temporaryRoot | Out-Null
try {
    $archive = Join-Path $temporaryRoot "runtime.zip"
    [System.IO.File]::WriteAllText($archive, "not-the-approved-runtime")
    Assert-ThrowsLike {
        Assert-ArchiveHash -Path $archive -ExpectedSha256 ("a" * 64)
    } "*archive checksum mismatch*"

    Assert-LoopbackListenerAddresses -Addresses @("127.0.0.1", "::1")
    Assert-ThrowsLike {
        Assert-LoopbackListenerAddresses -Addresses @("0.0.0.0")
    } "*not loopback-only*"

    $validTags = [pscustomobject]@{
        models = @([pscustomobject]@{
            name = "qwen3:4b-instruct-2507-q4_K_M"
            digest = "d" * 64
        })
    }
    $validShow = [pscustomobject]@{
        details = [pscustomobject]@{ family = "qwen3" }
        model_info = [pscustomobject]@{ architecture = "qwen3" }
    }
    Assert-OllamaIdentity `
        -VersionBody ([pscustomobject]@{ version = "0.40.11" }) `
        -TagsBody $validTags `
        -ShowBody $validShow `
        -ExpectedVersion "0.40.11" `
        -ModelTag "qwen3:4b-instruct-2507-q4_K_M" `
        -ExpectedDigest ("d" * 64) | Out-Null
    Assert-ThrowsLike {
        Assert-OllamaIdentity `
            -VersionBody ([pscustomobject]@{ version = "0.40.10" }) `
            -TagsBody $validTags `
            -ShowBody $validShow `
            -ExpectedVersion "0.40.11" `
            -ModelTag "qwen3:4b-instruct-2507-q4_K_M" `
            -ExpectedDigest ("d" * 64)
    } "*version mismatch*"
    Assert-ThrowsLike {
        Assert-OllamaIdentity `
            -VersionBody ([pscustomobject]@{ version = "0.40.11" }) `
            -TagsBody ([pscustomobject]@{ models = @() }) `
            -ShowBody $validShow `
            -ExpectedVersion "0.40.11" `
            -ModelTag "qwen3:4b-instruct-2507-q4_K_M" `
            -ExpectedDigest ("d" * 64)
    } "*model tag is missing*"
    Assert-ThrowsLike {
        Assert-OllamaIdentity `
            -VersionBody ([pscustomobject]@{ version = "0.40.11" }) `
            -TagsBody $validTags `
            -ShowBody $validShow `
            -ExpectedVersion "0.40.11" `
            -ModelTag "qwen3:4b-instruct-2507-q4_K_M" `
            -ExpectedDigest ("e" * 64)
    } "*model digest mismatch*"

    $expectedStart = [DateTime]::Parse("2026-10-26T01:02:03.0000000Z")
    $expectedIdentity = [pscustomobject]@{
        ProcessId = 101
        StartTimeUtc = $expectedStart.ToUniversalTime().ToString("o")
        ExecutablePath = "C:\approved\ollama.exe"
    }
    $matchingProcess = [pscustomobject]@{
        Id = 101
        StartTime = $expectedStart
        Path = "C:\approved\ollama.exe"
    }
    $stopped = [System.Collections.Generic.List[int]]::new()
    $didStop = Stop-HarnessOwnedProcess `
        -ExpectedIdentity $expectedIdentity `
        -GetProcessAction { param([int]$ProcessId) $matchingProcess } `
        -StopProcessAction { param([object]$Process) $stopped.Add([int]$Process.Id) }
    if (-not $didStop -or $stopped.Count -ne 1 -or $stopped[0] -ne 101) {
        throw "Cleanup did not stop the matching harness-owned process."
    }

    $reusedPidProcess = [pscustomobject]@{
        Id = 101
        StartTime = $expectedStart.AddSeconds(1)
        Path = "C:\unrelated\other.exe"
    }
    Assert-ThrowsLike {
        Stop-HarnessOwnedProcess `
            -ExpectedIdentity $expectedIdentity `
            -GetProcessAction { param([int]$ProcessId) $reusedPidProcess } `
            -StopProcessAction { param([object]$Process) $stopped.Add([int]$Process.Id) }
    } "*identity no longer matches*"
    if ($stopped.Count -ne 1) {
        throw "Cleanup stopped a reused PID with a different process identity."
    }

    Assert-OllamaListenerOwner `
        -Connections @([pscustomobject]@{
            LocalAddress = "127.0.0.1"
            OwningProcess = 101
        }) `
        -ExpectedProcessId 101
    Assert-ThrowsLike {
        Assert-OllamaListenerOwner `
            -Connections @([pscustomobject]@{
                LocalAddress = "127.0.0.1"
                OwningProcess = 202
            }) `
            -ExpectedProcessId 101
    } "*not owned by the harness-started process*"

    $timedOutProcessStops = [System.Collections.Generic.List[int]]::new()
    Assert-ThrowsLike {
        Wait-BoundedProcess `
            -Process ([pscustomobject]@{ Id = 303 }) `
            -TimeoutSeconds 1 `
            -WaitForExitAction { param([object]$Process, [int]$TimeoutMilliseconds) $false } `
            -StopProcessAction {
                param([object]$Process)
                $timedOutProcessStops.Add([int]$Process.Id)
            } `
            -TimeoutMessage "Synthetic process deadline exceeded."
    } "*deadline exceeded*"
    if ($timedOutProcessStops.Count -ne 1 -or $timedOutProcessStops[0] -ne 303) {
        throw "Timed-out process cleanup did not target exactly the started process."
    }

    $externalStops = [System.Collections.Generic.List[int]]::new()
    Assert-ThrowsLike {
        Test-ExistingOllamaListener `
            -Connections @([pscustomobject]@{
                LocalAddress = "127.0.0.1"
                OwningProcess = 202
            }) `
            -ExpectedDigest ("d" * 64) `
            -ProbeIdentity { throw "Ollama version mismatch" } `
            -StopProcessAction { param([int]$ProcessId) $externalStops.Add($ProcessId) }
    } "*version mismatch*"
    if ($externalStops.Count -ne 0) {
        throw "An incompatible external listener was stopped."
    }
}
finally {
    [void](Assert-SafeChildPath `
        -Path $temporaryRoot `
        -Parent ([System.IO.Path]::GetTempPath()) `
        -LeafPrefix "agentforge-ollama-contract-")
    Remove-Item -LiteralPath $temporaryRoot -Recurse -Force -ErrorAction SilentlyContinue
}

Write-Host "Ollama runtime contract checks passed."
