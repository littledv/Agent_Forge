# AgentForge

AgentForge is a local-first durable AI agent runtime. It is intended to show how
long-running agent workflows can preserve progress, recover after worker
failure, request human approval before sensitive actions, enforce trusted tool
permissions, respect resource limits, and expose an understandable execution
history.

The first end-to-end example reads approved local support-ticket files, builds
a report with either the default deterministic mock or an explicitly enabled
local Qwen profile, saves a checksummed local artifact, waits for approval bound
to that exact artifact, and delivers it to a separate deduplicating simulated
inbox.

## Current status

**Milestones 0 through 8 are complete and checkpoint-approved.** Milestone 9,
the optional public-deployment milestone, was skipped by explicit user decision.
Milestone 7 adds a thin synchronous Python SDK
over the stabilized `/api/v1` contract with no new dependency. The support-report
example uses a versioned, bounded researcher → writer → reviewer graph while
retaining saved version-1 replay compatibility.
The Next.js guided-run dashboard can submit, observe, approve, cancel, and
inspect verified support-report runs without reading raw logs. Delivery remains
gated by a durable, exact action-bound approval. Approval
and cancellation intents reach Temporal through a transactional signal outbox,
and the Workflow always rechecks PostgreSQL before delivery. A separate inbox
service deduplicates retries by stable operation ID. Native recovery tests cover
both an ordinary Worker loss and the ambiguous crash window after the inbox
commits but before Temporal receives the Activity result. Mock execution remains
the default deterministic path. An opt-in native Ollama `0.40.11` profile has
also been proven with the pinned Qwen model, immutable profile/queue routing,
per-generation digest revalidation, bounded structured generation, durable
usage evidence, and privacy/cleanup checks. The v2 graph caps one run at 16
tickets, four research shards, two concurrent researchers, two revisions, ten
role calls, and 900 seconds before publication. This sanitized source copy is
published at [littledv/Agent_Forge](https://github.com/littledv/Agent_Forge);
no service has been deployed and no runtime port has been exposed beyond
loopback.

The current checkpoint is recorded in [PROJECT_PLAN.md](PROJECT_PLAN.md), and
the latest session state is recorded in [HANDOVER.md](HANDOVER.md).

## Stack

- Next.js and TypeScript for the dashboard.
- FastAPI and Python for the application API.
- Temporal and the Temporal Python SDK for durable workflow execution.
- A small Python decision loop rather than a second agent framework.
- PostgreSQL for application records and Temporal persistence.
- Pydantic for API, model-output, and tool-argument validation.
- SQLAlchemy and Alembic for database access and migrations.
- Docker Compose on Docker Desktop with the WSL2 backend.
- pytest for backend, integration, and failure-recovery tests.
- Playwright for a small number of critical browser journeys.
- A deterministic mock model for the credential-free initial implementation.
- Optional native Windows Ollama `0.40.11` with pinned Qwen3 4B identity.
- A configured host directory for local artifacts.

Exact direct dependency and container-image versions are pinned in
`pyproject.toml`, `.env.example`, and `compose.yaml`.

## Documentation map

| File | Purpose |
|---|---|
| [PROJECT_PLAN.md](PROJECT_PLAN.md) | Durable milestone checklist and resume point |
| [ARCHITECTURE.md](ARCHITECTURE.md) | System boundaries, ownership, and component map |
| [CONSTRAINTS.md](CONSTRAINTS.md) | Rules that changes must follow |
| [FLOW.md](FLOW.md) | Support-report execution and state transitions |
| [DECISIONS.md](DECISIONS.md) | Important technical choices and their reasoning |
| [TEST_CHECKLIST.md](TEST_CHECKLIST.md) | Completion and verification requirements |
| [docs/API_SDK.md](docs/API_SDK.md) | Stable v1 API and synchronous Python SDK reference |
| [docs/FAILURE_PORTFOLIO.md](docs/FAILURE_PORTFOLIO.md) | Executable recovery and failure evidence matrix |
| [docs/PORTFOLIO_ARCHITECTURE.md](docs/PORTFOLIO_ARCHITECTURE.md) | Portfolio authorities, retry boundaries, and non-claims |
| [docs/OPERATIONS.md](docs/OPERATIONS.md) | Safe Windows operation and portfolio runbook |
| [docs/learning/11_MILESTONE_8_FAILURE_PORTFOLIO.md](docs/learning/11_MILESTONE_8_FAILURE_PORTFOLIO.md) | Plain-language lesson on cross-authority recovery evidence |
| [ROLLBACK.md](ROLLBACK.md) | Safe Git recovery process |
| [HANDOVER.md](HANDOVER.md) | Latest completed work, checks, issues, and next action |
| [docs/tasks](docs/tasks) | Focused bug and feature templates |
| [docs/learning](docs/learning) | Plain-language project lessons and diagrams |

## Running the project

All commands below run from Windows PowerShell in the repository root. Docker
publishes PostgreSQL, Temporal, and the Temporal UI on loopback only.

### One-time Python setup

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
```

### Start infrastructure and migrate

```powershell
docker compose up -d
docker compose ps
.\.venv\Scripts\alembic.exe upgrade head
.\.venv\Scripts\alembic.exe -c inbox_alembic.ini upgrade head
```

Configure each PowerShell window that runs an AgentForge process:

```powershell
$env:AGENTFORGE_DATABASE_URL = "postgresql+psycopg://temporal:temporal-local-only@127.0.0.1:5432/agentforge"
$env:AGENTFORGE_TEMPORAL_ADDRESS = "127.0.0.1:7233"
$env:AGENTFORGE_TEMPORAL_NAMESPACE = "default"
$env:AGENTFORGE_TEMPORAL_TASK_QUEUE = "agentforge-support-report-v1"
$env:AGENTFORGE_TICKET_ROOT = (Resolve-Path ".\samples\tickets").Path
$env:AGENTFORGE_ARTIFACT_ROOT = (Resolve-Path ".\artifacts").Path
$env:AGENTFORGE_MAX_DECISIONS = "4"
$env:AGENTFORGE_MAX_TICKET_BYTES = "65536"
$env:AGENTFORGE_MAX_TOTAL_TICKET_BYTES = "262144"
$env:AGENTFORGE_INBOX_BASE_URL = "http://127.0.0.1:8001"
$env:AGENTFORGE_INBOX_DATABASE_URL = "postgresql+psycopg://temporal:temporal-local-only@127.0.0.1:5432/agentforge_inbox"
$env:AGENTFORGE_APPROVAL_TTL_SECONDS = "300"
```

Run these in four separate configured PowerShell windows:

```powershell
.\.venv\Scripts\agentforge-api.exe
.\.venv\Scripts\agentforge-relay.exe
.\.venv\Scripts\agentforge-inbox.exe
.\.venv\Scripts\agentforge-worker.exe
```

Run the native dashboard in a fifth PowerShell window. The project runtime is
the pinned per-user Node.js `24.22.1` installation:

```powershell
$nodeRoot = Join-Path $env:LOCALAPPDATA "Programs\node-v24.22.1-win-x64"
$env:Path = "$nodeRoot;$env:Path"
Set-Location dashboard
& (Join-Path $nodeRoot "npm.cmd") ci
& (Join-Path $nodeRoot "npm.cmd") run dev
```

Open `http://127.0.0.1:3000`. The dashboard talks to the loopback API through a
same-origin `/backend/*` rewrite. It polls recent runs every two seconds and the
selected nonterminal run every second, stops selected-run polling at a terminal
state, and labels a projection older than ten seconds as possibly delayed rather
than failed. The Temporal UI remains available at `http://127.0.0.1:8080`.

Approval controls send the displayed action hash and then reread the durable
winner. Cancellation requires an explicit warning that a committed delivery
cannot be undone. Report content is fetched only when expanded, verified by the
API against recorded path/size/checksum/media metadata, and displayed as escaped
plain text rather than interpreted Markdown or HTML.

### Python SDK

The synchronous typed SDK uses the same loopback API as the dashboard. It does
not access PostgreSQL or Temporal directly and adds no dependency beyond the
already pinned `httpx` and Pydantic packages.

```python
from agentforge import AgentForgeClient, ApprovalDecision, RunState

with AgentForgeClient() as client:
    run = client.submit_run(
        submission_key="sdk-demo-001",
        goal="Find recurring support themes",
        ticket_files=["ticket-001.txt", "ticket-002.txt"],
    )
    client.wait_for_run(run.run_id, states={RunState.WAITING_APPROVAL})
    approval = client.get_approval(run.run_id)
    client.decide_approval(
        approval.approval_id,
        action_hash=approval.action_hash,
        decision=ApprovalDecision.APPROVE,
    )
    final = client.wait_for_run(run.run_id)
    if final.state == RunState.SUCCEEDED:
        artifact = client.get_artifact(run.run_id)
        print(artifact.artifact.path, artifact.artifact.sha256)
```

Polling can be interrupted and resumed by run ID. The client never silently
retries mutations: a transport timeout means the outcome may be unknown, so
submission recovery must reuse the stable key and exact body. See
[the API and SDK reference](docs/API_SDK.md) for every method, error, timeout,
and retry rule. A runnable interactive version is in
[`examples/python_sdk_lifecycle.py`](examples/python_sdk_lifecycle.py).

### Optional local Qwen profile

Ordinary startup enables only `mock-v1`; it requires no Ollama process, model
download, or provider credential. The local profile is opt-in and immutable per
run. Its snapshot participates in the submission fingerprint, is copied into
Temporal input, and routes model Activities to the dedicated
`agentforge-support-report-ollama-qwen3-4b-v1` task queue.

The native runtime is installed outside the repository at
`%LOCALAPPDATA%\Programs\ollama-v0.40.11-windows-amd64`; model data remains at
`%LOCALAPPDATA%\AgentForge\ollama-models`. The helper downloads the official
standalone archive only when absent, requires SHA-256
`bda4a493ba8a7c24d57263cd843cd68cee84f44aed4299eeed532acad7eda12a`,
binds `127.0.0.1:11434`, and verifies this exact model identity:

```text
qwen3:4b-instruct-2507-q4_K_M
0edcdef34593eac1aa2be9c7d06c432dcf81945adca5eca2f27662c18f168ba0
```

Run the complete native proof from Windows PowerShell:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass `
  -File tests/helpers/model_dashboard_stack.ps1 `
  -ExpectedModelDigest 0edcdef34593eac1aa2be9c7d06c432dcf81945adca5eca2f27662c18f168ba0
```

The harness starts the inbox, API, relay, dashboard, mock Worker,
profile-bound Ollama Worker, and—only if port 11434 is free—its own Ollama
process. It runs the approval/delivery browser journey, then stops only
processes it started and requires ports 3000, 8000, 8001, and owned port 11434
to clear. It refuses an incompatible existing listener. Failure logs are
retained only after a privacy scan; unprovably safe logs are deleted and their
removal is verified.

The profile bounds context to 8,192 tokens, output to 2,048 tokens, serialized
prompt input to 24,000 characters, report text to 16,384 characters, and at
most two semantic repairs. Immediately before each generation request, the
adapter resolves the configured tag through `/api/tags` and requires the
snapshot's full digest; a repointed tag fails closed before token usage or
generation. Ollama transport failures are retryable; budget, configuration,
profile mismatch, and exhausted semantic validation are permanent. Exact
provider token counts are stored when returned. Aggregate usage remains
estimated and uncertain if any earlier request lacks confirmed exact counts.
Provider charge is `$0.000000` for the local runtime; that excludes hardware,
electricity, and operator time.

Submit and poll from a fourth PowerShell window:

```powershell
$body = @{
  submission_key = "demo-001"
  goal = "Find recurring support themes"
  ticket_files = @("ticket-001.txt", "ticket-002.txt")
} | ConvertTo-Json

$run = Invoke-RestMethod -Method Post -ContentType "application/json" `
  -Uri "http://127.0.0.1:8000/api/v1/runs" -Body $body
do {
  Start-Sleep -Milliseconds 250
  $status = Invoke-RestMethod `
    "http://127.0.0.1:8000/api/v1/runs/$($run.run_id)"
} until ($status.state -eq "WAITING_APPROVAL")

$approval = Invoke-RestMethod `
  "http://127.0.0.1:8000/api/v1/runs/$($run.run_id)/approval"
$decision = @{
  action_hash = $approval.action_hash
  decision = "APPROVE"
} | ConvertTo-Json
Invoke-RestMethod -Method Post -ContentType "application/json" `
  -Uri "http://127.0.0.1:8000/api/v1/approvals/$($approval.approval_id)/decision" `
  -Body $decision

do {
  Start-Sleep -Milliseconds 250
  $status = Invoke-RestMethod `
    "http://127.0.0.1:8000/api/v1/runs/$($run.run_id)"
} until ($status.state -in @("SUCCEEDED", "DENIED", "CANCELLED", "FAILED", "NEEDS_ATTENTION"))

Get-FileHash -Algorithm SHA256 `
  -LiteralPath (Join-Path (Resolve-Path ".\artifacts") $status.artifact.path)
```

### Status, health, logs, and migrations

```powershell
Invoke-RestMethod http://127.0.0.1:8000/health/live
Invoke-RestMethod http://127.0.0.1:8000/health/ready
Invoke-RestMethod http://127.0.0.1:8001/health/live
Invoke-RestMethod http://127.0.0.1:8001/health/ready
docker compose ps
docker compose logs --tail 100 postgresql temporal temporal-ui
.\.venv\Scripts\alembic.exe current
.\.venv\Scripts\alembic.exe -c inbox_alembic.ini current
```

The Temporal UI is available at `http://127.0.0.1:8080` while its container is
running.

### Tests

```powershell
.\.venv\Scripts\python.exe -m pytest -v
.\.venv\Scripts\python.exe -m pip check
docker compose config --quiet
git diff --check
```

Run the mocked dashboard journeys and static checks natively:

```powershell
$nodeRoot = Join-Path $env:LOCALAPPDATA "Programs\node-v24.22.1-win-x64"
$env:Path = "$nodeRoot;$env:Path"
Set-Location dashboard
& (Join-Path $nodeRoot "npm.cmd") run typecheck
& (Join-Path $nodeRoot "npm.cmd") run lint
& (Join-Path $nodeRoot "npm.cmd") run build
& (Join-Path $nodeRoot "npm.cmd") run test:e2e
Set-Location ..
```

The deterministic browser suite does not start Ollama. The opt-in real-model
proof is the separate `model_dashboard_stack.ps1` command above.

Run the credential-free Milestone 8 failure portfolio twice consecutively to
prove the continuous recovery story and fixed failure matrix:

```powershell
.\.venv\Scripts\python.exe tests\helpers\run_failure_portfolio.py
.\.venv\Scripts\python.exe tests\helpers\run_failure_portfolio.py
```

The exact nodes, prerequisites, authority assertions, and properties not proven
are documented in [docs/FAILURE_PORTFOLIO.md](docs/FAILURE_PORTFOLIO.md).

Real Temporal integration tests use the `agentforge-test` namespace while the
application continues to use `default`. The profile-specific task queues remain
fixed inside each namespace, so a test Worker cannot consume a live local run.

With Docker Desktop running, one bounded command starts the five native
application processes, runs the unintercepted Chromium/API/Temporal/inbox flow,
and verifies that ports 3000, 8000, and 8001 are no longer listening afterward:

```powershell
powershell -ExecutionPolicy Bypass -File tests/helpers/dashboard_stack.ps1
```

The ordinary restart proof stops those five processes at `WAITING_APPROVAL`,
keeps PostgreSQL and Temporal running, restarts the applications, reopens the
same run in Chromium, approves it, and checks for one receiver delivery:

```powershell
powershell -ExecutionPolicy Bypass -File tests/helpers/dashboard_stack.ps1 `
  -TestOnly -RestartProof
```

Run the focused worker-termination demonstration against the real local
Temporal and PostgreSQL services:

```powershell
.\.venv\Scripts\python.exe -m pytest `
  tests/integration/test_worker_recovery.py -vv
```

The test starts a native Worker subprocess, waits until the second model
decision Activity is running, terminates that process, starts a replacement on
the same task queue, and verifies the Temporal attempt, PostgreSQL attempts,
single artifact, checksum, and child-process cleanup.

Run the post-receiver-commit proof separately:

```powershell
.\.venv\Scripts\python.exe -m pytest `
  tests/integration/test_delivery_recovery.py -vv
```

This proof uses the real loopback inbox API. It terminates the first Worker
after the receiver has committed, lets Temporal retry on a replacement Worker,
and verifies two Activity attempts but one unchanged receiver receipt.

### Ordinary stop and restart

Stop the API, relay, inbox, and Worker with `Ctrl+C`, then stop or restart
infrastructure services:

```powershell
docker compose stop
docker compose up -d
```

Named Docker volumes preserve PostgreSQL and Temporal data across this ordinary
stop. Files below `artifacts/` remain on the host.

### Destructive local reset

The following commands permanently delete local PostgreSQL/Temporal volumes and
generated reports. Run them only when that loss is intended:

```powershell
docker compose down --volumes --remove-orphans
$generated = @(
  ".\artifacts\reports",
  ".\artifacts\snapshots",
  ".\artifacts\drafts",
  ".\artifacts\.staging"
)
foreach ($path in $generated) {
  if (Test-Path -LiteralPath $path) {
    Remove-Item -LiteralPath $path -Recurse -Force
  }
}
```

## Privacy and publication

The public `littledv/Agent_Forge` repository starts with fresh Git history, uses
a GitHub noreply commit identity, replaces captured replay-worker metadata with
a synthetic identity, and includes the MIT license. Repository publication is
separate from application deployment: no service has been deployed and no
runtime port has been exposed beyond loopback.
