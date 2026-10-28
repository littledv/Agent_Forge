[CmdletBinding()]
param(
    [switch]$TestOnly,
    [switch]$RestartProof
)

$ErrorActionPreference = "Stop"
$repositoryRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$dashboardRoot = Join-Path $repositoryRoot "dashboard"
$pythonScripts = Join-Path $repositoryRoot ".venv\Scripts"
$nodeRoot = Join-Path $env:LOCALAPPDATA "Programs\node-v24.22.1-win-x64"
$nodeExecutable = Join-Path $nodeRoot "node.exe"
$npmExecutable = Join-Path $nodeRoot "npm.cmd"
$nextExecutable = Join-Path $dashboardRoot "node_modules\next\dist\bin\next"
$logRoot = Join-Path ([System.IO.Path]::GetTempPath()) (
    "agentforge-dashboard-stack-" + [guid]::NewGuid().ToString("N")
)
$trackedProcesses = [System.Collections.Generic.List[object]]::new()
$allTrackedProcesses = [System.Collections.Generic.List[object]]::new()
$stackGeneration = 0
$applicationPorts = @(3000, 8000, 8001)
$taskError = $null
$testExitCode = 0

function Assert-FileExists {
    param([string]$Path)
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "Required executable or file is missing: $Path"
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

function Wait-HttpReady {
    param(
        [string]$Name,
        [string]$Uri,
        [int]$TimeoutSeconds = 60
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
    throw "$Name did not become ready at $Uri within $TimeoutSeconds seconds."
}

function Wait-TcpReady {
    param(
        [string]$Name,
        [int]$Port,
        [int]$TimeoutSeconds = 90
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
    throw "$Name did not accept loopback connections on port $Port within $TimeoutSeconds seconds."
}

function Start-TrackedProcess {
    param(
        [string]$Name,
        [string]$FilePath,
        [string[]]$ArgumentList = @(),
        [string]$WorkingDirectory = $repositoryRoot
    )
    $processName = "$Name-$stackGeneration"
    $stdout = Join-Path $logRoot "$processName.stdout.log"
    $stderr = Join-Path $logRoot "$processName.stderr.log"
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
    $tracked = [pscustomobject]@{
        Name = $processName
        Process = $process
        Stdout = $stdout
        Stderr = $stderr
    }
    $trackedProcesses.Add($tracked)
    $allTrackedProcesses.Add($tracked)
    Write-Host "Started $processName (PID $($process.Id))."
}

function Assert-TrackedProcessesRunning {
    foreach ($tracked in $trackedProcesses) {
        if ($tracked.Process.HasExited) {
            throw "$($tracked.Name) exited during stack startup with code $($tracked.Process.ExitCode)."
        }
    }
}

function Show-DiagnosticLogs {
    foreach ($tracked in $allTrackedProcesses) {
        foreach ($path in @($tracked.Stdout, $tracked.Stderr)) {
            if (Test-Path -LiteralPath $path) {
                $tail = @(Get-Content -LiteralPath $path -Tail 30)
                if ($tail.Count -gt 0) {
                    Write-Host "--- $($tracked.Name): $(Split-Path $path -Leaf)"
                    $tail | Write-Host
                }
            }
        }
    }
}

function Start-ApplicationStack {
    $script:stackGeneration += 1
    Start-TrackedProcess -Name "inbox" -FilePath (Join-Path $pythonScripts "agentforge-inbox.exe")
    Start-TrackedProcess -Name "api" -FilePath (Join-Path $pythonScripts "agentforge-api.exe")
    Start-TrackedProcess -Name "relay" -FilePath (Join-Path $pythonScripts "agentforge-relay.exe")
    Start-TrackedProcess -Name "worker" -FilePath (Join-Path $pythonScripts "agentforge-worker.exe")
    Start-TrackedProcess `
        -Name "dashboard" `
        -FilePath $nodeExecutable `
        -ArgumentList @("`"$nextExecutable`"", "dev", "-H", "127.0.0.1", "-p", "3000") `
        -WorkingDirectory $dashboardRoot

    Wait-HttpReady -Name "Inbox" -Uri "http://127.0.0.1:8001/health/ready"
    Wait-HttpReady -Name "API" -Uri "http://127.0.0.1:8000/health/ready"
    Wait-HttpReady -Name "Dashboard" -Uri "http://127.0.0.1:3000/"
    Assert-TrackedProcessesRunning
}

function Stop-ApplicationStack {
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

function Invoke-RealJourney {
    param([string]$TestMatch)
    $env:AGENTFORGE_REAL_TEST = $TestMatch
    Push-Location $dashboardRoot
    try {
        & $npmExecutable run test:e2e:real
        $script:testExitCode = $LASTEXITCODE
    }
    finally {
        Pop-Location
    }
    if ($testExitCode -ne 0) {
        throw "Real dashboard journey '$TestMatch' failed with exit code $testExitCode."
    }
}

foreach ($path in @(
    (Join-Path $pythonScripts "agentforge-api.exe"),
    (Join-Path $pythonScripts "agentforge-relay.exe"),
    (Join-Path $pythonScripts "agentforge-inbox.exe"),
    (Join-Path $pythonScripts "agentforge-worker.exe"),
    (Join-Path $pythonScripts "alembic.exe"),
    $nodeExecutable,
    $npmExecutable,
    $nextExecutable
)) {
    Assert-FileExists -Path $path
}

New-Item -ItemType Directory -Path $logRoot | Out-Null
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
$env:AGENTFORGE_APPROVAL_TTL_SECONDS = "300"
$env:AGENTFORGE_API_BASE_URL = "http://127.0.0.1:8000"

try {
    Assert-ApplicationPortsFree

    if (-not $TestOnly) {
        & docker compose up -d
        if ($LASTEXITCODE -ne 0) {
            throw "docker compose up failed with exit code $LASTEXITCODE."
        }
    }
    Wait-TcpReady -Name "PostgreSQL" -Port 5432
    Wait-TcpReady -Name "Temporal" -Port 7233

    & (Join-Path $pythonScripts "alembic.exe") upgrade head
    if ($LASTEXITCODE -ne 0) {
        throw "AgentForge migration failed with exit code $LASTEXITCODE."
    }
    & (Join-Path $pythonScripts "alembic.exe") -c (Join-Path $repositoryRoot "inbox_alembic.ini") upgrade head
    if ($LASTEXITCODE -ne 0) {
        throw "Inbox migration failed with exit code $LASTEXITCODE."
    }

    Start-ApplicationStack
    if ($RestartProof) {
        $env:AGENTFORGE_RESTART_STATE_PATH = Join-Path $logRoot "restart-state.json"
        Invoke-RealJourney -TestMatch "restart-before.spec.ts"
        Stop-ApplicationStack
        Assert-ApplicationPortsFree
        Wait-TcpReady -Name "PostgreSQL after application stop" -Port 5432
        Wait-TcpReady -Name "Temporal after application stop" -Port 7233
        Write-Host "Application listeners are stopped; PostgreSQL and Temporal remain available."
        Start-ApplicationStack
        Invoke-RealJourney -TestMatch "restart-after.spec.ts"
    }
    else {
        Invoke-RealJourney -TestMatch "real-flow.spec.ts"
    }
}
catch {
    $taskError = $_
}
finally {
    Stop-ApplicationStack
}

$cleanupErrors = @()
foreach ($port in $applicationPorts) {
    $owners = Get-ListeningProcessIds -Port $port
    if ($owners.Count -gt 0) {
        $cleanupErrors += "port $port remains listening (PID: $($owners -join ', '))"
    }
}

if ($null -ne $taskError -or $cleanupErrors.Count -gt 0) {
    Show-DiagnosticLogs
}
if ($cleanupErrors.Count -gt 0) {
    throw "Stack cleanup failed: $($cleanupErrors -join '; ')."
}
if ($null -ne $taskError) {
    throw $taskError
}

Write-Host "Real dashboard journey passed; application listeners were cleaned up."
exit $testExitCode
