[CmdletBinding()]
param(
    [switch]$LibraryOnly,
    [switch]$ValidateOnly,
    [switch]$KeepRunning,
    [string]$LogRoot = "",
    [string]$ExpectedModelDigest = $env:AGENTFORGE_OLLAMA_MODEL_DIGEST
)

$ErrorActionPreference = "Stop"
$script:OllamaVersion = "0.40.11"
$script:OllamaArchiveSha256 = "bda4a493ba8a7c24d57263cd843cd68cee84f44aed4299eeed532acad7eda12a"
$script:OllamaArchiveUri = "https://github.com/ollama/ollama/releases/download/v0.40.11/ollama-windows-amd64.zip"
$script:OllamaModelTag = "qwen3:4b-instruct-2507-q4_K_M"
$script:OllamaBaseUrl = "http://127.0.0.1:11434"
$script:OllamaInstallRoot = Join-Path $env:LOCALAPPDATA "Programs\ollama-v0.40.11-windows-amd64"
$script:OllamaModelsRoot = Join-Path $env:LOCALAPPDATA "AgentForge\ollama-models"
$script:MaxOllamaResponseBytes = 131072

function Assert-SafeChildPath {
    param(
        [Parameter(Mandatory)][string]$Path,
        [Parameter(Mandatory)][string]$Parent,
        [Parameter(Mandatory)][string]$LeafPrefix
    )
    $fullPath = [System.IO.Path]::GetFullPath($Path).TrimEnd('\', '/')
    $fullParent = [System.IO.Path]::GetFullPath($Parent).TrimEnd('\', '/')
    $parentPrefix = $fullParent + [System.IO.Path]::DirectorySeparatorChar
    $leaf = Split-Path $fullPath -Leaf
    if (
        -not $fullPath.StartsWith(
            $parentPrefix,
            [System.StringComparison]::OrdinalIgnoreCase
        ) -or
        -not $leaf.StartsWith(
            $LeafPrefix,
            [System.StringComparison]::OrdinalIgnoreCase
        )
    ) {
        throw "Refusing filesystem operation outside the approved directory."
    }
    return $fullPath
}

function Assert-ArchiveHash {
    param(
        [Parameter(Mandatory)][string]$Path,
        [Parameter(Mandatory)][string]$ExpectedSha256
    )
    $actual = (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($actual -ne $ExpectedSha256.ToLowerInvariant()) {
        throw "Ollama archive checksum mismatch."
    }
    return $actual
}

function Assert-LoopbackListenerAddresses {
    param([Parameter(Mandatory)][object[]]$Addresses)
    $allowed = @("127.0.0.1", "::1")
    foreach ($address in $Addresses) {
        if ([string]$address -notin $allowed) {
            throw "Ollama listener is not loopback-only."
        }
    }
}

function Assert-OllamaIdentity {
    param(
        [Parameter(Mandatory)][object]$VersionBody,
        [Parameter(Mandatory)][object]$TagsBody,
        [Parameter(Mandatory)][object]$ShowBody,
        [Parameter(Mandatory)][string]$ExpectedVersion,
        [Parameter(Mandatory)][string]$ModelTag,
        [AllowEmptyString()][string]$ExpectedDigest = ""
    )
    if ([string]$VersionBody.version -ne $ExpectedVersion) {
        throw "Ollama version mismatch."
    }
    $matchingModels = @($TagsBody.models | Where-Object { [string]$_.name -eq $ModelTag })
    if ($matchingModels.Count -ne 1) {
        throw "Required Ollama model tag is missing."
    }
    $actualDigest = [string]$matchingModels[0].digest
    if ($actualDigest -notmatch '^[0-9a-f]{64}$') {
        throw "Ollama model digest is invalid."
    }
    if ($ExpectedDigest -and $actualDigest -ne $ExpectedDigest) {
        throw "Ollama model digest mismatch."
    }
    if ($null -eq $ShowBody.details -or $null -eq $ShowBody.model_info) {
        throw "Ollama model metadata is unavailable."
    }
    return $actualDigest
}

function New-ProcessIdentity {
    param(
        [Parameter(Mandatory)][object]$Process,
        [Parameter(Mandatory)][string]$ExecutablePath
    )
    return [pscustomobject]@{
        ProcessId = [int]$Process.Id
        StartTimeUtc = $Process.StartTime.ToUniversalTime().ToString("o")
        ExecutablePath = [System.IO.Path]::GetFullPath($ExecutablePath)
    }
}

function Test-ProcessIdentityMatch {
    param(
        [Parameter(Mandatory)][object]$ExpectedIdentity,
        [AllowNull()][object]$ObservedProcess
    )
    if ($null -eq $ObservedProcess -or [int]$ObservedProcess.Id -ne [int]$ExpectedIdentity.ProcessId) {
        return $false
    }
    $observedStart = $ObservedProcess.StartTime.ToUniversalTime().ToString("o")
    $observedPath = [System.IO.Path]::GetFullPath([string]$ObservedProcess.Path)
    return (
        $observedStart -eq [string]$ExpectedIdentity.StartTimeUtc -and
        $observedPath.Equals(
            [string]$ExpectedIdentity.ExecutablePath,
            [System.StringComparison]::OrdinalIgnoreCase
        )
    )
}

function Stop-HarnessOwnedProcess {
    param(
        [Parameter(Mandatory)][object]$ExpectedIdentity,
        [Parameter(Mandatory)][scriptblock]$GetProcessAction,
        [Parameter(Mandatory)][scriptblock]$StopProcessAction
    )
    $observed = & $GetProcessAction ([int]$ExpectedIdentity.ProcessId)
    if ($null -eq $observed) {
        return $false
    }
    if (-not (Test-ProcessIdentityMatch -ExpectedIdentity $ExpectedIdentity -ObservedProcess $observed)) {
        throw "Refusing to stop a process whose identity no longer matches the harness-owned Ollama process."
    }
    & $StopProcessAction $observed
    return $true
}

function Test-ExistingOllamaListener {
    param(
        [object[]]$Connections,
        [AllowEmptyString()][string]$ExpectedDigest,
        [Parameter(Mandatory)][scriptblock]$ProbeIdentity,
        [scriptblock]$StopProcessAction = { param([int]$ProcessId) }
    )
    if ($Connections.Count -eq 0) {
        return $null
    }
    Assert-LoopbackListenerAddresses -Addresses @(
        $Connections | Select-Object -ExpandProperty LocalAddress -Unique
    )
    if (-not $ExpectedDigest) {
        throw "Port 11434 is occupied; an exact expected model digest is required for reuse."
    }
    $digest = & $ProbeIdentity $ExpectedDigest
    return [pscustomobject]@{
        Reused = $true
        Digest = [string]$digest
        OwnerProcessIds = @($Connections | Select-Object -ExpandProperty OwningProcess -Unique)
    }
}

function Invoke-OllamaJson {
    param(
        [ValidateSet("GET", "POST")][string]$Method,
        [Parameter(Mandatory)][string]$Path,
        [object]$Body = $null,
        [int]$TimeoutSeconds = 30
    )
    Add-Type -AssemblyName System.Net.Http
    $handler = [System.Net.Http.HttpClientHandler]::new()
    $handler.UseProxy = $false
    $handler.AllowAutoRedirect = $false
    $client = [System.Net.Http.HttpClient]::new($handler)
    $client.Timeout = [System.Threading.Timeout]::InfiniteTimeSpan
    $cancellation = [System.Threading.CancellationTokenSource]::new(
        [TimeSpan]::FromSeconds($TimeoutSeconds)
    )
    $request = [System.Net.Http.HttpRequestMessage]::new(
        [System.Net.Http.HttpMethod]::$Method,
        "$script:OllamaBaseUrl$Path"
    )
    if ($null -ne $Body) {
        $json = $Body | ConvertTo-Json -Depth 20 -Compress
        $request.Content = [System.Net.Http.StringContent]::new(
            $json,
            [System.Text.Encoding]::UTF8,
            "application/json"
        )
    }
    $response = $null
    try {
        $response = $client.SendAsync(
            $request,
            [System.Net.Http.HttpCompletionOption]::ResponseHeadersRead,
            $cancellation.Token
        ).GetAwaiter().GetResult()
        if (-not $response.IsSuccessStatusCode) {
            throw "Ollama returned HTTP $([int]$response.StatusCode)."
        }
        $stream = $response.Content.ReadAsStreamAsync().GetAwaiter().GetResult()
        $buffer = New-Object byte[] 8192
        $memory = [System.IO.MemoryStream]::new()
        try {
            while (($read = $stream.ReadAsync(
                $buffer,
                0,
                $buffer.Length,
                $cancellation.Token
            ).GetAwaiter().GetResult()) -gt 0) {
                if ($memory.Length + $read -gt $script:MaxOllamaResponseBytes) {
                    throw "Ollama response exceeded the bounded harness limit."
                }
                $memory.Write($buffer, 0, $read)
            }
            $text = [System.Text.Encoding]::UTF8.GetString($memory.ToArray())
        }
        finally {
            $memory.Dispose()
            $stream.Dispose()
        }
        return $text | ConvertFrom-Json
    }
    catch [System.OperationCanceledException] {
        throw "Ollama request exceeded its bounded deadline."
    }
    finally {
        if ($null -ne $response) { $response.Dispose() }
        $request.Dispose()
        $client.Dispose()
        $handler.Dispose()
        $cancellation.Dispose()
    }
}

function Assert-OllamaListenerOwner {
    param(
        [Parameter(Mandatory)][object[]]$Connections,
        [Parameter(Mandatory)][int]$ExpectedProcessId
    )
    if ($Connections.Count -eq 0) {
        throw "Pinned Ollama did not create the expected loopback listener."
    }
    Assert-LoopbackListenerAddresses -Addresses @(
        $Connections | Select-Object -ExpandProperty LocalAddress -Unique
    )
    $owners = @($Connections | Select-Object -ExpandProperty OwningProcess -Unique)
    if ($owners.Count -ne 1 -or [int]$owners[0] -ne $ExpectedProcessId) {
        throw "The Ollama listener is not owned by the harness-started process."
    }
}

function Get-OllamaConnections {
    return @(
        Get-NetTCPConnection -State Listen -LocalPort 11434 -ErrorAction SilentlyContinue
    )
}

function Get-VerifiedOllamaIdentity {
    param([AllowEmptyString()][string]$ExpectedDigest = "")
    $version = Invoke-OllamaJson -Method GET -Path "/api/version"
    $tags = Invoke-OllamaJson -Method GET -Path "/api/tags"
    $show = Invoke-OllamaJson `
        -Method POST `
        -Path "/api/show" `
        -Body @{ model = $script:OllamaModelTag } `
        -TimeoutSeconds 60
    return Assert-OllamaIdentity `
        -VersionBody $version `
        -TagsBody $tags `
        -ShowBody $show `
        -ExpectedVersion $script:OllamaVersion `
        -ModelTag $script:OllamaModelTag `
        -ExpectedDigest $ExpectedDigest
}

function Wait-OllamaReady {
    param([int]$TimeoutSeconds = 90)
    $deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
    do {
        try {
            $version = Invoke-OllamaJson -Method GET -Path "/api/version" -TimeoutSeconds 3
            if ([string]$version.version -eq $script:OllamaVersion) {
                return
            }
            throw "Ollama version mismatch."
        }
        catch {
            if ([DateTime]::UtcNow -ge $deadline) {
                throw "Pinned Ollama did not become ready on loopback."
            }
            Start-Sleep -Milliseconds 500
        }
    } while ($true)
}

function Install-OllamaRuntime {
    $executable = Join-Path $script:OllamaInstallRoot "ollama.exe"
    if (Test-Path -LiteralPath $executable -PathType Leaf) {
        return $executable
    }
    if (Test-Path -LiteralPath $script:OllamaInstallRoot) {
        throw "Pinned Ollama install directory exists but is incomplete."
    }
    $temporaryRoot = Join-Path ([System.IO.Path]::GetTempPath()) (
        "agentforge-ollama-install-" + [guid]::NewGuid().ToString("N")
    )
    $temporaryRoot = Assert-SafeChildPath `
        -Path $temporaryRoot `
        -Parent ([System.IO.Path]::GetTempPath()) `
        -LeafPrefix "agentforge-ollama-install-"
    $archive = Join-Path $temporaryRoot "ollama-windows-amd64.zip"
    $expanded = Join-Path $temporaryRoot "expanded"
    New-Item -ItemType Directory -Path $temporaryRoot | Out-Null
    try {
        Write-Host "Downloading pinned Ollama v$script:OllamaVersion runtime."
        $oldProgress = $ProgressPreference
        $ProgressPreference = "SilentlyContinue"
        try {
            Invoke-WebRequest `
                -UseBasicParsing `
                -Uri $script:OllamaArchiveUri `
                -OutFile $archive `
                -TimeoutSec 1800
        }
        finally {
            $ProgressPreference = $oldProgress
        }
        [void](Assert-ArchiveHash -Path $archive -ExpectedSha256 $script:OllamaArchiveSha256)
        Expand-Archive -LiteralPath $archive -DestinationPath $expanded
        if (-not (Test-Path -LiteralPath (Join-Path $expanded "ollama.exe") -PathType Leaf)) {
            throw "Pinned Ollama archive layout is invalid."
        }
        $installParent = Split-Path $script:OllamaInstallRoot -Parent
        [void](Assert-SafeChildPath `
            -Path $script:OllamaInstallRoot `
            -Parent $installParent `
            -LeafPrefix "ollama-v0.40.11-windows-amd64")
        [void](Assert-SafeChildPath `
            -Path $expanded `
            -Parent $temporaryRoot `
            -LeafPrefix "expanded")
        New-Item -ItemType Directory -Path $installParent -Force | Out-Null
        Move-Item -LiteralPath $expanded -Destination $script:OllamaInstallRoot
        return $executable
    }
    finally {
        Remove-Item -LiteralPath $temporaryRoot -Recurse -Force -ErrorAction SilentlyContinue
    }
}

function Wait-BoundedProcess {
    param(
        [Parameter(Mandatory)][object]$Process,
        [Parameter(Mandatory)][int]$TimeoutSeconds,
        [Parameter(Mandatory)][scriptblock]$WaitForExitAction,
        [Parameter(Mandatory)][scriptblock]$StopProcessAction,
        [Parameter(Mandatory)][string]$TimeoutMessage
    )
    $completed = & $WaitForExitAction $Process ($TimeoutSeconds * 1000)
    if (-not $completed) {
        & $StopProcessAction $Process
        throw $TimeoutMessage
    }
}

function Invoke-OllamaPull {
    param(
        [Parameter(Mandatory)][string]$Executable,
        [Parameter(Mandatory)][string]$LogRoot,
        [int]$TimeoutSeconds = 1800,
        [scriptblock]$WaitForExitAction = {
            param([object]$Process, [int]$TimeoutMilliseconds)
            $Process.WaitForExit($TimeoutMilliseconds)
        },
        [scriptblock]$StopProcessAction = {
            param([object]$Process)
            if (-not $Process.HasExited) {
                $Process.Kill()
                $Process.WaitForExit(15000) | Out-Null
            }
        }
    )
    $stdout = Join-Path $LogRoot "pull.stdout.log"
    $stderr = Join-Path $LogRoot "pull.stderr.log"
    $process = Start-Process `
        -FilePath $Executable `
        -ArgumentList @("pull", $script:OllamaModelTag) `
        -WindowStyle Hidden `
        -RedirectStandardOutput $stdout `
        -RedirectStandardError $stderr `
        -PassThru
    # Windows PowerShell 5 requires the handle to be materialized before the
    # process exits; otherwise ExitCode remains null after a bounded wait.
    $processHandle = $process.Handle
    Wait-BoundedProcess `
        -Process $process `
        -TimeoutSeconds $TimeoutSeconds `
        -WaitForExitAction $WaitForExitAction `
        -StopProcessAction $StopProcessAction `
        -TimeoutMessage "Pinned Ollama model pull exceeded its bounded deadline."
    $process.WaitForExit()
    $process.Refresh()
    if ($process.ExitCode -ne 0) {
        throw "Pinned Ollama model pull failed with exit code $($process.ExitCode)."
    }
}

function Invoke-OllamaSmokeTest {
    $schema = @{
        type = "object"
        required = @("status")
        additionalProperties = $false
        properties = @{ status = @{ type = "string" } }
    }
    $response = Invoke-OllamaJson `
        -Method POST `
        -Path "/api/chat" `
        -TimeoutSeconds 180 `
        -Body @{
            model = $script:OllamaModelTag
            stream = $false
            think = $false
            format = $schema
            keep_alive = "2m"
            options = @{ temperature = 0; num_ctx = 2048; num_predict = 32 }
            messages = @(
                @{ role = "system"; content = "Return only the requested JSON object." }
                @{ role = "user"; content = "Return a short synthetic readiness status." }
            )
        }
    if ($response.done -ne $true -or [string]$response.model -ne $script:OllamaModelTag) {
        throw "Ollama smoke response envelope is invalid."
    }
    try {
        $content = [string]$response.message.content | ConvertFrom-Json
    }
    catch {
        throw "Ollama smoke response schema is invalid."
    }
    if ([string]::IsNullOrWhiteSpace([string]$content.status)) {
        throw "Ollama smoke response schema is invalid."
    }
}

function Stop-OllamaProcess {
    param([Parameter(Mandatory)][object]$Process)
    if (-not $Process.HasExited) {
        $Process.Kill()
        if (-not $Process.WaitForExit(15000)) {
            throw "Harness-owned Ollama did not stop within its bounded deadline."
        }
    }
}

if ($LibraryOnly) {
    return
}

if ($ExpectedModelDigest -and $ExpectedModelDigest -notmatch '^[0-9a-f]{64}$') {
    throw "Expected model digest must be 64 lowercase hexadecimal characters."
}

$oldModels = $env:OLLAMA_MODELS
$oldHost = $env:OLLAMA_HOST
$ownedProcess = $null
$ownedProcessIdentity = $null
$runtimeLogRoot = $null
$ownsLogRoot = $false
$taskError = $null
$result = $null
try {
    $connections = Get-OllamaConnections
    if ($connections.Count -gt 0) {
        $existing = Test-ExistingOllamaListener `
            -Connections $connections `
            -ExpectedDigest $ExpectedModelDigest `
            -ProbeIdentity { param([string]$Digest) Get-VerifiedOllamaIdentity -ExpectedDigest $Digest }
        $digest = $existing.Digest
        Invoke-OllamaSmokeTest
        $result = [pscustomobject]@{
            version = $script:OllamaVersion
            archive_sha256 = $script:OllamaArchiveSha256
            model_tag = $script:OllamaModelTag
            model_digest = $digest
            install_path = $script:OllamaInstallRoot
            models_path = $script:OllamaModelsRoot
            started_by_harness = $false
            process_id = $null
            log_root = $null
        }
    }
    elseif ($ValidateOnly) {
        throw "Pinned Ollama is not listening on 127.0.0.1:11434."
    }
    else {
        $executable = Install-OllamaRuntime
        New-Item -ItemType Directory -Path $script:OllamaModelsRoot -Force | Out-Null
        $env:OLLAMA_MODELS = $script:OllamaModelsRoot
        $env:OLLAMA_HOST = "127.0.0.1:11434"
        if ($LogRoot) {
            $runtimeLogRoot = $LogRoot
        }
        else {
            $runtimeLogRoot = Join-Path ([System.IO.Path]::GetTempPath()) (
                "agentforge-ollama-runtime-" + [guid]::NewGuid().ToString("N")
            )
            $runtimeLogRoot = Assert-SafeChildPath `
                -Path $runtimeLogRoot `
                -Parent ([System.IO.Path]::GetTempPath()) `
                -LeafPrefix "agentforge-ollama-runtime-"
            $ownsLogRoot = $true
        }
        New-Item -ItemType Directory -Path $runtimeLogRoot -Force | Out-Null
        $ownedProcess = Start-Process `
            -FilePath $executable `
            -ArgumentList @("serve") `
            -WindowStyle Hidden `
            -RedirectStandardOutput (Join-Path $runtimeLogRoot "serve.stdout.log") `
            -RedirectStandardError (Join-Path $runtimeLogRoot "serve.stderr.log") `
            -PassThru
        $ownedProcessIdentity = New-ProcessIdentity `
            -Process $ownedProcess `
            -ExecutablePath $executable
        Write-Host "Started harness-owned Ollama PID $($ownedProcessIdentity.ProcessId)."
        Wait-OllamaReady
        Assert-OllamaListenerOwner `
            -Connections (Get-OllamaConnections) `
            -ExpectedProcessId $ownedProcessIdentity.ProcessId
        Invoke-OllamaPull -Executable $executable -LogRoot $runtimeLogRoot
        $digest = Get-VerifiedOllamaIdentity -ExpectedDigest $ExpectedModelDigest
        Invoke-OllamaSmokeTest
        $result = [pscustomobject]@{
            version = $script:OllamaVersion
            archive_sha256 = $script:OllamaArchiveSha256
            model_tag = $script:OllamaModelTag
            model_digest = $digest
            install_path = $script:OllamaInstallRoot
            models_path = $script:OllamaModelsRoot
            started_by_harness = $true
            process_id = $ownedProcessIdentity.ProcessId
            process_started_at_utc = $ownedProcessIdentity.StartTimeUtc
            executable_path = $ownedProcessIdentity.ExecutablePath
            log_root = $runtimeLogRoot
        }
    }
}
catch {
    $taskError = $_
}
finally {
    if ($null -ne $ownedProcess -and (-not $KeepRunning -or $null -ne $taskError)) {
        Stop-OllamaProcess -Process $ownedProcess
    }
    if ($null -eq $oldModels) { Remove-Item Env:OLLAMA_MODELS -ErrorAction SilentlyContinue }
    else { $env:OLLAMA_MODELS = $oldModels }
    if ($null -eq $oldHost) { Remove-Item Env:OLLAMA_HOST -ErrorAction SilentlyContinue }
    else { $env:OLLAMA_HOST = $oldHost }
}

if ($null -ne $taskError) {
    throw $taskError
}
if (
    -not $KeepRunning -and
    $ownsLogRoot -and
    $runtimeLogRoot -and
    (Test-Path -LiteralPath $runtimeLogRoot)
) {
    [void](Assert-SafeChildPath `
        -Path $runtimeLogRoot `
        -Parent ([System.IO.Path]::GetTempPath()) `
        -LeafPrefix "agentforge-ollama-runtime-")
    Remove-Item -LiteralPath $runtimeLogRoot -Recurse -Force -ErrorAction SilentlyContinue
}
$result | ConvertTo-Json -Compress
