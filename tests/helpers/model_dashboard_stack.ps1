[CmdletBinding()]
param(
    [string]$ExpectedModelDigest = "",
    [switch]$LibraryOnly
)

$ErrorActionPreference = "Stop"
$repositoryRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$dashboardRoot = Join-Path $repositoryRoot "dashboard"
$pythonScripts = Join-Path $repositoryRoot ".venv\Scripts"
$nodeRoot = Join-Path $env:LOCALAPPDATA "Programs\node-v24.22.1-win-x64"
$nodeExecutable = Join-Path $nodeRoot "node.exe"
$npmExecutable = Join-Path $nodeRoot "npm.cmd"
$nextExecutable = Join-Path $dashboardRoot "node_modules\next\dist\bin\next"
$runtimeScript = Join-Path $PSScriptRoot "ollama_runtime.ps1"
$logRoot = Join-Path ([System.IO.Path]::GetTempPath()) (
    "agentforge-model-stack-" + [guid]::NewGuid().ToString("N")
)
$ollamaLogRoot = Join-Path $logRoot "ollama"
$trackedProcesses = [System.Collections.Generic.List[object]]::new()
$applicationPorts = @(3000, 8000, 8001)
$harnessStartedAt = [DateTime]::UtcNow
$taskError = $null
$testExitCode = 0
$runtime = $null
$cleanupErrors = @()

function Assert-SafeStackLogPath {
    param([Parameter(Mandatory)][string]$Path)
    $fullPath = [System.IO.Path]::GetFullPath($Path).TrimEnd('\', '/')
    $temporaryRoot = [System.IO.Path]::GetFullPath(
        [System.IO.Path]::GetTempPath()
    ).TrimEnd('\', '/')
    $prefix = $temporaryRoot + [System.IO.Path]::DirectorySeparatorChar
    if (
        -not $fullPath.StartsWith(
            $prefix,
            [System.StringComparison]::OrdinalIgnoreCase
        ) -or
        -not (Split-Path $fullPath -Leaf).StartsWith(
            "agentforge-model-stack-",
            [System.StringComparison]::OrdinalIgnoreCase
        )
    ) {
        throw "Refusing log cleanup outside the approved temporary directory."
    }
    return $fullPath
}

function Assert-FileExists {
    param([string]$Path)
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "Required executable or file is missing: $Path"
    }
}

function Get-PrivateContentProbes {
    $probes = [System.Collections.Generic.List[string]]::new()
    $ticketRoot = Join-Path $repositoryRoot "samples\tickets"
    foreach ($ticket in Get-ChildItem -LiteralPath $ticketRoot -File -ErrorAction Stop) {
        $ticketText = [System.IO.File]::ReadAllText($ticket.FullName)
        foreach ($line in $ticketText -split "`r?`n") {
            $trimmed = $line.Trim()
            if ($trimmed.Length -ge 8) {
                $probes.Add($trimmed)
            }
        }
    }
    $artifactRoot = Join-Path $repositoryRoot "artifacts"
    foreach ($artifact in Get-ChildItem -LiteralPath $artifactRoot -File -Recurse -ErrorAction Stop) {
        if ($artifact.LastWriteTimeUtc -lt $harnessStartedAt) {
            continue
        }
        $artifactText = [System.IO.File]::ReadAllText($artifact.FullName).Trim()
        if ($artifactText.Length -ge 8) {
            $probes.Add($artifactText.Substring(0, [Math]::Min(32, $artifactText.Length)))
        }
    }
    return @($probes | Select-Object -Unique)
}

function Get-PrivateContentVariants {
    param([Parameter(Mandatory)][string]$PrivateText)
    $normalized = $PrivateText.Replace("`r`n", "`n")
    $jsonLiteral = $normalized | ConvertTo-Json -Compress
    $jsonEscaped = $jsonLiteral
    if (
        $jsonLiteral.Length -ge 2 -and
        $jsonLiteral.StartsWith('"') -and
        $jsonLiteral.EndsWith('"')
    ) {
        $jsonEscaped = $jsonLiteral.Substring(1, $jsonLiteral.Length - 2)
    }
    return @($PrivateText, $normalized, $jsonEscaped) | Select-Object -Unique
}

function Test-PrivateContentAbsent {
    param(
        [Parameter(Mandatory)][string]$Content,
        [Parameter(Mandatory)][string[]]$Probes
    )
    foreach ($probe in $Probes) {
        foreach ($variant in Get-PrivateContentVariants -PrivateText $probe) {
            if ($Content.Contains($variant)) {
                return $false
            }
        }
    }
    return $true
}

function Test-LogsSafeForRetention {
    if (-not (Test-Path -LiteralPath $logRoot -PathType Container)) {
        return $true
    }
    try {
        $probes = @(Get-PrivateContentProbes)
        foreach ($log in Get-ChildItem -LiteralPath $logRoot -File -Recurse -ErrorAction Stop) {
            $content = [System.IO.File]::ReadAllText($log.FullName)
            if (-not (Test-PrivateContentAbsent -Content $content -Probes $probes)) {
                return $false
            }
        }
        return $true
    }
    catch {
        return $false
    }
}

function Assert-NoRealModelBrowserArtifacts {
    $resultsRoot = Join-Path $dashboardRoot "test-results"
    if (-not (Test-Path -LiteralPath $resultsRoot -PathType Container)) {
        return
    }
    $privateArtifacts = @(
        Get-ChildItem -LiteralPath $resultsRoot -File -Recurse -ErrorAction Stop |
            Where-Object { $_.Extension -in @(".png", ".zip", ".trace") }
    )
    if ($privateArtifacts.Count -gt 0) {
        throw "Real-model browser run produced a screenshot or trace that could contain private content."
    }
}

function Remove-StackLogsSafely {
    param(
        [Parameter(Mandatory)][string]$Path,
        [scriptblock]$RemoveAction = {
            param([string]$CandidatePath)
            Remove-Item `
                -LiteralPath $CandidatePath `
                -Recurse `
                -Force `
                -ErrorAction Stop
        }
    )
    $safePath = Assert-SafeStackLogPath -Path $Path
    & $RemoveAction $safePath
    if (Test-Path -LiteralPath $safePath) {
        throw "Stack log directory remained after cleanup."
    }
}

function Get-ListeningProcessIds {
    param([int]$Port)
    return @(
        Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue |
            Select-Object -ExpandProperty OwningProcess -Unique
    )
}

function Assert-ApplicationPortsFree {
    foreach ($port in $applicationPorts) {
        $owners = Get-ListeningProcessIds -Port $port
        if ($owners.Count -gt 0) {
            throw "Loopback application port $port is already listening (PID: $($owners -join ', '))."
        }
    }
}

function Wait-PortClear {
    param(
        [int]$Port,
        [int]$TimeoutSeconds = 30
    )
    $deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
    do {
        if ((Get-ListeningProcessIds -Port $Port).Count -eq 0) {
            return
        }
        Start-Sleep -Milliseconds 250
    } while ([DateTime]::UtcNow -lt $deadline)
    throw "Port $Port remained listening after harness cleanup."
}

function Wait-TcpReady {
    param(
        [string]$Name,
        [int]$Port,
        [int]$TimeoutSeconds = 120
    )
    $deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
    do {
        $client = [System.Net.Sockets.TcpClient]::new()
        try {
            $connect = $client.BeginConnect("127.0.0.1", $Port, $null, $null)
            if ($connect.AsyncWaitHandle.WaitOne(500) -and $client.Connected) {
                $client.EndConnect($connect)
                return
            }
        }
        catch {
        }
        finally {
            $client.Dispose()
        }
        Start-Sleep -Milliseconds 250
    } while ([DateTime]::UtcNow -lt $deadline)
    throw "$Name did not accept loopback connections on port $Port."
}

function Wait-HttpReady {
    param(
        [string]$Name,
        [string]$Uri,
        [int]$TimeoutSeconds = 90
    )
    $deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
    do {
        try {
            $response = Invoke-WebRequest -UseBasicParsing -Uri $Uri -TimeoutSec 2
            if ($response.StatusCode -ge 200 -and $response.StatusCode -lt 300) {
                return
            }
        }
        catch {
            Start-Sleep -Milliseconds 250
        }
    } while ([DateTime]::UtcNow -lt $deadline)
    throw "$Name did not become ready at $Uri."
}

function Ensure-DockerEngine {
    & docker info *> $null
    if ($LASTEXITCODE -eq 0) {
        return
    }
    $desktop = Join-Path $env:LOCALAPPDATA "Programs\DockerDesktop\Docker Desktop.exe"
    Assert-FileExists -Path $desktop
    Write-Host "Starting Docker Desktop for PostgreSQL and Temporal."
    Start-Process -FilePath $desktop -WindowStyle Hidden | Out-Null
    $deadline = [DateTime]::UtcNow.AddMinutes(3)
    do {
        Start-Sleep -Seconds 2
        & docker info *> $null
        if ($LASTEXITCODE -eq 0) {
            return
        }
    } while ([DateTime]::UtcNow -lt $deadline)
    throw "Docker Desktop did not become ready."
}

function Start-TrackedProcess {
    param(
        [string]$Name,
        [string]$FilePath,
        [string[]]$ArgumentList = @(),
        [string]$WorkingDirectory = $repositoryRoot
    )
    $stdout = Join-Path $logRoot "$Name.stdout.log"
    $stderr = Join-Path $logRoot "$Name.stderr.log"
    $startParameters = @{
        FilePath = $FilePath
        WorkingDirectory = $WorkingDirectory
        WindowStyle = "Hidden"
        RedirectStandardOutput = $stdout
        RedirectStandardError = $stderr
        PassThru = $true
    }
    if ($ArgumentList.Count -gt 0) {
        $startParameters.ArgumentList = $ArgumentList
    }
    $process = Start-Process @startParameters
    $trackedProcesses.Add([pscustomobject]@{
        Name = $Name
        Process = $process
        Stdout = $stdout
        Stderr = $stderr
    })
    Write-Host "Started $Name (PID $($process.Id))."
}

function Assert-TrackedProcessesRunning {
    foreach ($tracked in $trackedProcesses) {
        if ($tracked.Process.HasExited) {
            throw "$($tracked.Name) exited during stack startup with code $($tracked.Process.ExitCode)."
        }
    }
}

function Stop-TrackedProcesses {
    $reverse = @($trackedProcesses)
    [array]::Reverse($reverse)
    foreach ($tracked in $reverse) {
        if (-not $tracked.Process.HasExited) {
            Stop-Process -Id $tracked.Process.Id -ErrorAction SilentlyContinue
            try {
                Wait-Process -Id $tracked.Process.Id -Timeout 10 -ErrorAction Stop
            }
            catch {
                Stop-Process -Id $tracked.Process.Id -Force -ErrorAction SilentlyContinue
            }
        }
        Write-Host "Stopped $($tracked.Name) (PID $($tracked.Process.Id))."
    }
    $trackedProcesses.Clear()
}

function Stop-OwnedOllama {
    if ($null -eq $runtime -or -not $runtime.started_by_harness) {
        return
    }
    $ownedProcessId = [int]$runtime.process_id
    $process = Get-Process -Id $ownedProcessId -ErrorAction SilentlyContinue
    if ($null -ne $process) {
        $observedStart = $process.StartTime.ToUniversalTime().ToString("o")
        $observedPath = [System.IO.Path]::GetFullPath([string]$process.Path)
        $expectedPath = [System.IO.Path]::GetFullPath([string]$runtime.executable_path)
        if (
            $observedStart -ne [string]$runtime.process_started_at_utc -or
            -not $observedPath.Equals(
                $expectedPath,
                [System.StringComparison]::OrdinalIgnoreCase
            )
        ) {
            throw "Refusing to stop a process whose identity no longer matches the harness-owned Ollama process."
        }
        $process.Kill()
        if (-not $process.WaitForExit(15000)) {
            throw "Harness-owned Ollama did not stop within its bounded deadline."
        }
    }
    Write-Host "Stopped harness-owned Ollama PID $ownedProcessId."
}

if ($LibraryOnly) {
    return
}

foreach ($path in @(
    (Join-Path $pythonScripts "agentforge-api.exe"),
    (Join-Path $pythonScripts "agentforge-relay.exe"),
    (Join-Path $pythonScripts "agentforge-inbox.exe"),
    (Join-Path $pythonScripts "agentforge-worker.exe"),
    (Join-Path $pythonScripts "alembic.exe"),
    $nodeExecutable,
    $npmExecutable,
    $nextExecutable,
    $runtimeScript
)) {
    Assert-FileExists -Path $path
}

$logRoot = Assert-SafeStackLogPath -Path $logRoot
$ollamaLogRoot = Join-Path $logRoot "ollama"
New-Item -ItemType Directory -Path $ollamaLogRoot -Force | Out-Null
$env:Path = "$nodeRoot;$env:Path"
$env:AGENTFORGE_DATABASE_URL = "postgresql+psycopg://temporal:temporal-local-only@127.0.0.1:5432/agentforge"
$env:AGENTFORGE_TEMPORAL_ADDRESS = "127.0.0.1:7233"
$env:AGENTFORGE_TEMPORAL_NAMESPACE = "default"
$env:AGENTFORGE_TEMPORAL_TASK_QUEUE = "agentforge-support-report-v1"
$env:AGENTFORGE_TICKET_ROOT = (Resolve-Path (Join-Path $repositoryRoot "samples\tickets")).Path
$env:AGENTFORGE_ARTIFACT_ROOT = (Resolve-Path (Join-Path $repositoryRoot "artifacts")).Path
$env:AGENTFORGE_MAX_DECISIONS = "4"
$env:AGENTFORGE_MAX_TICKET_BYTES = "65536"
$env:AGENTFORGE_MAX_TOTAL_TICKET_BYTES = "262144"
$env:AGENTFORGE_INBOX_BASE_URL = "http://127.0.0.1:8001"
$env:AGENTFORGE_INBOX_DATABASE_URL = "postgresql+psycopg://temporal:temporal-local-only@127.0.0.1:5432/agentforge_inbox"
$env:AGENTFORGE_APPROVAL_TTL_SECONDS = "600"
$env:AGENTFORGE_API_BASE_URL = "http://127.0.0.1:8000"
$env:AGENTFORGE_ENABLED_MODEL_PROFILES = "mock-v1,ollama-qwen3-4b-instruct-q4-v1"
$env:AGENTFORGE_OLLAMA_BASE_URL = "http://127.0.0.1:11434"
$env:AGENTFORGE_OLLAMA_MODEL = "qwen3:4b-instruct-2507-q4_K_M"
$env:AGENTFORGE_MODEL_TIMEOUT_SECONDS = "150"
$env:AGENTFORGE_REAL_MODEL_TEST = "1"
$env:AGENTFORGE_REAL_TEST = "real-model.spec.ts"
$env:AGENTFORGE_PROCESS_LOG_ROOT = $logRoot

try {
    Assert-ApplicationPortsFree
    Ensure-DockerEngine
    & docker compose up -d
    if ($LASTEXITCODE -ne 0) {
        throw "docker compose up failed with exit code $LASTEXITCODE."
    }
    Wait-TcpReady -Name "PostgreSQL" -Port 5432
    Wait-TcpReady -Name "Temporal" -Port 7233

    $runtimeOutput = @(
        & $runtimeScript `
            -KeepRunning `
            -LogRoot $ollamaLogRoot `
            -ExpectedModelDigest $ExpectedModelDigest
    )
    $runtime = $runtimeOutput[-1] | ConvertFrom-Json
    $env:AGENTFORGE_OLLAMA_MODEL_DIGEST = [string]$runtime.model_digest
    $env:AGENTFORGE_REAL_MODEL_DIGEST = [string]$runtime.model_digest
    Write-Host "Verified Ollama $($runtime.version), model $($runtime.model_tag), digest $($runtime.model_digest)."

    & (Join-Path $pythonScripts "alembic.exe") upgrade head
    if ($LASTEXITCODE -ne 0) {
        throw "AgentForge migration failed with exit code $LASTEXITCODE."
    }
    & (Join-Path $pythonScripts "alembic.exe") `
        -c (Join-Path $repositoryRoot "inbox_alembic.ini") upgrade head
    if ($LASTEXITCODE -ne 0) {
        throw "Inbox migration failed with exit code $LASTEXITCODE."
    }

    Push-Location $dashboardRoot
    try {
        & $npmExecutable run build
    }
    finally {
        Pop-Location
    }
    if ($LASTEXITCODE -ne 0) {
        throw "Dashboard build failed with exit code $LASTEXITCODE."
    }

    $env:AGENTFORGE_WORKER_MODEL_PROFILE = "mock-v1"
    Start-TrackedProcess -Name "inbox" -FilePath (Join-Path $pythonScripts "agentforge-inbox.exe")
    Start-TrackedProcess -Name "api" -FilePath (Join-Path $pythonScripts "agentforge-api.exe")
    Start-TrackedProcess -Name "relay" -FilePath (Join-Path $pythonScripts "agentforge-relay.exe")
    Start-TrackedProcess -Name "mock-worker" -FilePath (Join-Path $pythonScripts "agentforge-worker.exe")
    $env:AGENTFORGE_WORKER_MODEL_PROFILE = "ollama-qwen3-4b-instruct-q4-v1"
    Start-TrackedProcess -Name "ollama-worker" -FilePath (Join-Path $pythonScripts "agentforge-worker.exe")
    Start-TrackedProcess `
        -Name "dashboard" `
        -FilePath $nodeExecutable `
        -ArgumentList @("`"$nextExecutable`"", "start", "-H", "127.0.0.1", "-p", "3000") `
        -WorkingDirectory $dashboardRoot

    Wait-HttpReady -Name "Inbox" -Uri "http://127.0.0.1:8001/health/ready"
    Wait-HttpReady -Name "API" -Uri "http://127.0.0.1:8000/health/ready"
    Wait-HttpReady -Name "Dashboard" -Uri "http://127.0.0.1:3000/"
    Assert-TrackedProcessesRunning

    Push-Location $dashboardRoot
    try {
        & $npmExecutable run test:e2e:real
        $testExitCode = $LASTEXITCODE
    }
    finally {
        Pop-Location
    }
    if ($testExitCode -ne 0) {
        throw "Real model dashboard journey failed with exit code $testExitCode."
    }
    Assert-NoRealModelBrowserArtifacts
}
catch {
    $taskError = $_
}
finally {
    Stop-TrackedProcesses
    try { Stop-OwnedOllama }
    catch { $cleanupErrors += $_.Exception.Message }
}

foreach ($port in $applicationPorts) {
    try { Wait-PortClear -Port $port }
    catch { $cleanupErrors += $_.Exception.Message }
}
if ($null -ne $runtime -and $runtime.started_by_harness) {
    try { Wait-PortClear -Port 11434 }
    catch { $cleanupErrors += $_.Exception.Message }
}

$failed = $null -ne $taskError -or $cleanupErrors.Count -gt 0
$retainSafeFailureLogs = $failed -and (Test-LogsSafeForRetention)
if ($retainSafeFailureLogs) {
    Write-Host "Privacy-scanned harness logs were preserved at $logRoot."
}
else {
    try {
        Remove-StackLogsSafely -Path $logRoot
    }
    catch {
        $cleanupErrors += "Stack log cleanup failed without proving private-content removal."
    }
    if ($failed -and $cleanupErrors.Count -eq 0) {
        Write-Host "Harness logs were deleted because safe retention could not be proven."
    }
}
if ($cleanupErrors.Count -gt 0) {
    throw "Stack cleanup failed: $($cleanupErrors -join '; ')"
}
if ($null -ne $taskError) {
    throw $taskError
}

Write-Host "Real local-model journey passed; harness-owned listeners were cleaned up."
exit $testExitCode
