# Local Operations

All commands run in Windows PowerShell from the repository root. Services bind
to loopback only.

## Validate the pinned environment

```powershell
py -3.13 --version
.\.venv\Scripts\python.exe --version
.\.venv\Scripts\python.exe -m pip check
docker compose config --quiet
docker compose images
```

Expected Python is 3.13.17. Direct dependencies and container images are pinned
in `pyproject.toml`, `.env.example`, and `compose.yaml`.

## Start and inspect services

```powershell
docker compose up -d
docker compose ps
docker compose logs --tail 100 postgresql temporal temporal-ui
.\.venv\Scripts\alembic.exe upgrade head
.\.venv\Scripts\alembic.exe current
.\.venv\Scripts\alembic.exe -c inbox_alembic.ini upgrade head
.\.venv\Scripts\alembic.exe -c inbox_alembic.ini current
```

Do not run the application against an integration-test database. The failure
portfolio owns the disposable `_test` reset and migration lifecycle.

## Run the safe portfolio

```powershell
.\.venv\Scripts\python.exe tests\helpers\run_failure_portfolio.py --collect-only
.\.venv\Scripts\python.exe tests\helpers\run_failure_portfolio.py
.\.venv\Scripts\python.exe tests\helpers\run_failure_portfolio.py
```

Both normal runs must pass consecutively without manual cleanup. The runner
returns pytest's exit code unchanged and performs no hidden retry.

## Ordinary stop and restart

Stop native API, relay, inbox, Worker, or dashboard processes with `Ctrl+C`,
then use:

```powershell
docker compose stop
docker compose up -d
```

Named Docker volumes retain PostgreSQL and Temporal data. Generated report
files remain below `artifacts/`; application records remain in PostgreSQL and
execution histories remain in Temporal. Test-only markers live in pytest's
temporary directory and should not be treated as durable evidence.

## Bounded diagnostics and cleanup checks

```powershell
docker compose ps
docker compose logs --tail 100 postgresql temporal temporal-ui
Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue |
  Where-Object { $_.LocalPort -in @(8000, 8001) }
Get-Process python -ErrorAction SilentlyContinue |
  Where-Object { $_.Path -eq (Resolve-Path .\.venv\Scripts\python.exe).Path }
```

These are read-only observations. Portfolio API and inbox listeners use
ephemeral ports, and the test reaps every helper it starts. Diagnose an
unexpected survivor by its recorded identity token and command line; never
terminate unrelated Python processes. Inspect or terminate only the exact
failed test Workflow, never every execution in `agentforge-test`. Do not paste
private ticket, report, prompt, response, or artifact content into logs.

## Destructive reset warning

The following operation deletes local Compose volumes and therefore local
PostgreSQL and Temporal history. It is not needed for the portfolio:

```powershell
docker compose down --volumes --remove-orphans
```

Deleting generated reports under `artifacts/reports` is also destructive. Use
the repository's root README reset procedure only when that data loss is
intended. Never substitute manual SQL truncation for the guarded automatic test
database reset. Its URL must have a `_test` database suffix, a direct loopback
host, the `postgresql+psycopg` driver, and no query parameters that could
override the validated target.
