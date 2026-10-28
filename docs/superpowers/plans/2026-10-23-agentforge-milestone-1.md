# AgentForge Milestone 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> superpowers:subagent-driven-development (recommended) or
> superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build one complete local AgentForge run that accepts an idempotent
support-report request, starts a Temporal Workflow, reads approved sample
tickets, writes a deterministic checksummed report, and exposes the terminal
run state through an API.

**Architecture:** A host-run FastAPI application writes each run and its
Workflow-start intent to one application PostgreSQL database transaction. A
separate relay starts a stable Temporal Workflow ID, a Python Worker performs a
bounded mock-model loop through Activities, and idempotent projection Activities
update PostgreSQL. Ticket and report bodies stay in configured local storage;
only bounded metadata and references enter Workflow history.

**Tech Stack:** Python 3.13, FastAPI, Pydantic 2, SQLAlchemy 2, Alembic,
Psycopg 3, Temporal Python SDK, PostgreSQL 16, Docker Compose, pytest.

**Spec:** `PROJECT_PLAN.md` (Milestone 1), with `ARCHITECTURE.md`, `FLOW.md`,
`CONSTRAINTS.md`, and `TEST_CHECKLIST.md` as normative supporting documents.

## Global Constraints

- Implement Milestone 1 only; no dashboard, approvals, inbox delivery, real
  model provider, multi-agent graph, public deployment, or private remote.
- Run user-facing commands in Windows PowerShell. Shell scripts under
  `infra/temporal/` execute only inside Linux containers.
- Publish PostgreSQL, Temporal, and Temporal UI ports on `127.0.0.1` only.
- Keep Workflow code deterministic. Database, model, clock, filesystem, and
  network access belong in Activities or host processes.
- PostgreSQL owns application records; Temporal owns execution history.
- Use stable submission, run, Workflow, step, attempt, projection-event, and
  artifact identifiers.
- Keep ticket and report bodies out of Temporal history; pass bounded references,
  hashes, counts, and sizes through Workflow inputs and results.
- Enforce a four-decision maximum, 64 KiB per ticket, 256 KiB total ticket
  input, and one final Markdown report per run.
- Keep credentials out of tracked files. `.env.example` contains local-only
  placeholders and `.env` remains ignored.
- Do not install tools or dependencies until the user approves the matrix below.

## Pinned Version Matrix

The versions below were checked on 2026-10-23. Exact Python package pins go in
`pyproject.toml`; exact container tags go in `.env.example`. Record pulled image
digests in `HANDOVER.md` during implementation rather than inventing them in the
plan.

| Component | Pin | Reason |
|---|---:|---|
| Python | 3.13.17 | Current patched 3.13 Windows build; conservative ecosystem target rather than newly released 3.15 |
| FastAPI | 0.158.1 | Current patch release; pin exactly because FastAPI minor releases may break compatibility |
| Uvicorn | 0.53.0 | Current stable PyPI release |
| Pydantic | 2.14.1 | Current Pydantic 2 patch release |
| SQLAlchemy | 2.1.6 | Current stable release; requires Python 3.11+ |
| Alembic | 1.20.1 | Current migration release |
| Psycopg | 3.3.6 | Current Psycopg 3 release with binary and pool extras |
| Temporal Python SDK | 1.33.0 | Current SDK release |
| pytest | 9.1.1 | Current stable test runner |
| pytest-asyncio | 1.4.0 | Current stable asyncio plugin |
| HTTPX | 0.28.1 | Current stable release used by API tests |
| PostgreSQL image | 16.15 | Supported PostgreSQL line and current patch; matches Temporal's current sample baseline major |
| Temporal server/admin images | 1.32.1 | Current patched Temporal server line |
| Temporal UI image | 2.54.1 | UI version bundled with the current Temporal CLI development server |

Primary references:

- Python Windows releases: <https://www.python.org/downloads/windows/>
- FastAPI version guidance and releases:
  <https://fastapi.tiangolo.com/deployment/versions/> and
  <https://fastapi.tiangolo.com/release-notes/>
- Temporal Python SDK: <https://pypi.org/project/temporalio/>
- Temporal server releases: <https://github.com/temporalio/temporal/releases>
- Current Temporal Compose samples:
  <https://github.com/temporalio/samples-server/tree/main/compose>
- PostgreSQL supported versions: <https://www.postgresql.org/support/versioning/>
- Other Python pins: their official PyPI project pages.

## File Structure

- `pyproject.toml` — package metadata, exact direct dependency pins, pytest
  configuration, and CLI entry points.
- `.env.example` — safe local defaults and exact container tags.
- `compose.yaml` — PostgreSQL, Temporal schema setup, Temporal server, and UI.
- `infra/postgres/init/01-create-app-databases.sql` — creates application and
  test databases inside the one local PostgreSQL container.
- `infra/temporal/` — pinned, locally adapted schema and namespace setup scripts
  based on the official `samples-server` PostgreSQL Compose example.
- `src/agentforge/config.py` — validated environment configuration.
- `src/agentforge/domain.py` — durable enums and bounded cross-component data
  contracts.
- `src/agentforge/database.py` — SQLAlchemy engine and session construction.
- `src/agentforge/db_models.py` — application-record tables only.
- `src/agentforge/repository.py` — transactional submission, outbox, projection,
  and read operations.
- `src/agentforge/api.py` — FastAPI application and run endpoints.
- `src/agentforge/relay.py` — Workflow-start outbox relay.
- `src/agentforge/storage.py` — approved reads, staging files, and atomic report
  publication.
- `src/agentforge/mock_model.py` — deterministic action selection and report
  drafting.
- `src/agentforge/activities.py` — Temporal Activities and attempt/projection
  recording.
- `src/agentforge/workflow.py` — deterministic bounded coordinator.
- `src/agentforge/worker.py` — Temporal Worker process entry point.
- `alembic/` — database migration environment and initial schema revision.
- `samples/tickets/` — synthetic, non-sensitive ticket fixtures.
- `artifacts/.gitkeep` — tracked empty artifact root; runtime contents ignored.
- `tests/unit/` — configuration, domain, model, and storage tests.
- `tests/integration/` — PostgreSQL, API, relay, Temporal, and end-to-end tests.
- `docs/learning/04_MILESTONE_1_BUILD.md` — beginner-oriented explanation of
  what was actually built and how data moves.

## Review Focus

- Reusing one submission key with different normalized content returns `409`
  and does not alter the first run; Task 4 pins this behavior.
- Absolute paths, `..` traversal, and symlink escapes never leave the approved
  ticket or artifact roots; Task 6 pins these cases.
- Oversized individual or aggregate ticket input fails permanently without a
  partial final artifact; Task 6 pins both limits.
- A relay crash after Temporal accepts the Workflow but before PostgreSQL marks
  the outbox row is recovered as an already-started success; Task 5 pins this
  ambiguous boundary.
- Duplicate or stale projection events never duplicate records or move a run
  backward; Tasks 3 and 7 pin event-ID and sequence guards.

---

### Task 1: Establish the approved local runtime

**Files:**

- Create: `pyproject.toml`
- Create: `.env.example`
- Create: `compose.yaml`
- Create: `infra/postgres/init/01-create-app-databases.sql`
- Create: `infra/temporal/setup-postgres.sh`
- Create: `infra/temporal/create-namespace.sh`
- Create: `infra/temporal/development-sql.yaml`
- Create: `src/agentforge/__init__.py`
- Modify: `.gitignore`

**Interfaces:**

- Consumes: approved installation of Python 3.13.17, WSL2, and Docker Desktop.
- Produces: `.venv`, a validated Compose model, healthy local PostgreSQL and
  Temporal services, `agentforge` and `agentforge_test` databases, and the
  `default` Temporal namespace.

- [ ] **Step 1: Install prerequisites only after explicit user approval**

  Install Python 3.13.17, enable WSL2, install Docker Desktop with its WSL2
  Linux-container backend, then verify from PowerShell:

  ```powershell
  python --version
  wsl --status
  docker --version
  docker compose version
  ```

  Expected: Python reports `3.13.17`; WSL and Docker report healthy installed
  versions. A reboot is allowed if the installers require it, but record it in
  `HANDOVER.md` first.

- [ ] **Step 2: Define exact Python dependencies and commands**

  In `pyproject.toml`, require `>=3.13,<3.14`, add the exact runtime and test
  pins from the version matrix, and expose these scripts:

  ```text
  agentforge-api = agentforge.api:main
  agentforge-relay = agentforge.relay:main
  agentforge-worker = agentforge.worker:main
  ```

- [ ] **Step 3: Create and populate the project virtual environment**

  ```powershell
  python -m venv .venv
  .\.venv\Scripts\python.exe -m pip install --upgrade pip
  .\.venv\Scripts\python.exe -m pip install -e ".[dev]"
  .\.venv\Scripts\python.exe -m pip check
  ```

  Expected: installation and `pip check` succeed. Save the resolved environment
  with `python -m pip freeze` in `HANDOVER.md`; do not commit `.venv`.

- [ ] **Step 4: Define local service isolation**

  Adapt the official Temporal PostgreSQL sample so Compose exposes only
  `127.0.0.1:5432`, `127.0.0.1:7233`, and `127.0.0.1:8080`; use one PostgreSQL
  container with separate `agentforge`, `agentforge_test`, `temporal`, and
  `temporal_visibility` databases and a named volume.

- [ ] **Step 5: Validate and start the infrastructure**

  ```powershell
  docker compose config --quiet
  docker compose up -d postgresql temporal-admin-tools temporal temporal-create-namespace temporal-ui
  docker compose ps
  ```

  Expected: configuration validation succeeds and all long-running services
  become healthy; one-shot setup services exit successfully.

- [ ] **Step 6: Verify loopback-only access and persistence setup**

  Inspect `docker compose port` for all published ports, describe the Temporal
  namespace with the admin-tools container, and list PostgreSQL databases.
  Expected: every host binding starts with `127.0.0.1`, `default` exists, and
  all four databases exist.

- [ ] **Step 7: Commit the runtime baseline**

  ```powershell
  git add pyproject.toml .env.example compose.yaml .gitignore infra src/agentforge/__init__.py
  git commit -m "build: add pinned local runtime"
  ```

### Task 2: Define configuration and durable contracts

**Files:**

- Create: `src/agentforge/config.py`
- Create: `src/agentforge/domain.py`
- Create: `tests/unit/test_config.py`
- Create: `tests/unit/test_domain.py`

**Interfaces:**

- Consumes: environment variables documented by `.env.example`.
- Produces: `Settings.from_env(env: Mapping[str, str] | None = None) -> Settings`;
  `RunState`, `StepState`, `AttemptState`, and `FailureKind`; `SubmitRun`,
  `RunSnapshot`, `WorkflowInput`, `ModelDecisionInput`, discriminated
  `AgentAction`, `TicketSnapshotRef`, `DraftRef`, `ArtifactRef`, and
  `ProjectionEvent` Pydantic models.

- [ ] **Step 1: Write failing configuration tests**

  Test that `Settings.from_env()` accepts the documented local values, resolves
  ticket and artifact roots, defaults `max_decisions` to `4`, and rejects
  non-positive size/decision limits.

- [ ] **Step 2: Run configuration tests and confirm failure**

  ```powershell
  .\.venv\Scripts\python.exe -m pytest tests/unit/test_config.py -v
  ```

  Expected: FAIL because `agentforge.config` does not exist.

- [ ] **Step 3: Implement `Settings` and `Settings.from_env`**

  Parse an injected mapping for testability; do not read `.env` files or print
  secrets. Configuration fields are `database_url`, `temporal_address`,
  `temporal_namespace`, `temporal_task_queue`, `ticket_root`, `artifact_root`,
  `max_decisions`, `max_ticket_bytes`, and `max_total_ticket_bytes`.

- [ ] **Step 4: Write failing durable-contract tests**

  Assert exact enum values from `FLOW.md`; assert malformed or unknown model
  action types fail validation; assert reference models reject negative sizes,
  non-SHA-256 hashes, and absolute paths.

- [ ] **Step 5: Implement the durable contracts**

  Use string enums and strict Pydantic models with forbidden extra fields.
  `AgentAction` has exactly `ReadTicketsAction`, `PublishReportAction`, and
  `FinishAction`, discriminated by `type`.

- [ ] **Step 6: Run the unit tests**

  ```powershell
  .\.venv\Scripts\python.exe -m pytest tests/unit/test_config.py tests/unit/test_domain.py -v
  ```

  Expected: PASS.

- [ ] **Step 7: Commit contracts**

  ```powershell
  git add src/agentforge/config.py src/agentforge/domain.py tests/unit
  git commit -m "feat: define AgentForge runtime contracts"
  ```

### Task 3: Add the application-record database and migration

**Files:**

- Create: `src/agentforge/database.py`
- Create: `src/agentforge/db_models.py`
- Create: `src/agentforge/repository.py`
- Create: `alembic.ini`
- Create: `alembic/env.py`
- Create: `alembic/script.py.mako`
- Create: `alembic/versions/0001_initial_records.py`
- Create: `tests/integration/conftest.py`
- Create: `tests/integration/test_migrations.py`
- Create: `tests/integration/test_repository.py`

**Interfaces:**

- Consumes: `Settings`, domain states, PostgreSQL `agentforge_test` database.
- Produces: `create_engine_and_sessionmaker(settings)`;
  `RunRepository.submit(session, request, agent_version_id) -> SubmissionResult`;
  `RunRepository.get(session, run_id) -> RunSnapshot | None`;
  `OutboxRepository.claim_batch(...)`, `mark_started(...)`, and
  `record_failure(...)`; `ProjectionRepository.apply(event) -> bool`.

- [ ] **Step 1: Write the failing migration shape test**

  Migrate an empty test database to `head` and assert tables for
  `agent_definitions`, `agent_versions`, `runs`, `steps`, `attempts`,
  `artifacts`, `workflow_start_outbox`, and `projection_events`, including
  unique constraints for submission keys, run/step ordinals, step/attempt
  numbers, outbox run IDs, artifact run/path pairs, and projection event IDs.

- [ ] **Step 2: Run the migration test and confirm failure**

  ```powershell
  .\.venv\Scripts\python.exe -m pytest tests/integration/test_migrations.py -v
  ```

  Expected: FAIL because Alembic is not configured.

- [ ] **Step 3: Implement models and the initial migration**

  Store state values as checked strings, UUIDs as PostgreSQL UUIDs, timestamps
  as timezone-aware values, request/projection payloads as JSONB, hashes as
  lowercase 64-character text, and sizes/sequences as non-negative integers.
  Seed one `support-report` agent definition and version `1` in the migration.

- [ ] **Step 4: Write failing repository transaction tests**

  Assert a first submission creates exactly one run and one outbox row; the
  same key and canonical payload returns that run; a different payload raises
  `SubmissionConflict`; concurrent equal submissions converge on one run; a
  duplicate projection event is harmless; and a lower sequence cannot replace
  a higher sequence.

- [ ] **Step 5: Implement repository transactions**

  Canonicalize `SubmitRun` with sorted JSON keys and compact separators, hash
  with SHA-256, derive `workflow_id` as `agentforge/run/<run-id>`, and resolve
  uniqueness races by re-reading the winning row inside a fresh transaction.
  Claim outbox work with `FOR UPDATE SKIP LOCKED` and a bounded batch.

- [ ] **Step 6: Run database tests**

  ```powershell
  .\.venv\Scripts\python.exe -m pytest tests/integration/test_migrations.py tests/integration/test_repository.py -v
  ```

  Expected: PASS.

- [ ] **Step 7: Commit persistence**

  ```powershell
  git add src/agentforge/database.py src/agentforge/db_models.py src/agentforge/repository.py alembic tests/integration
  git commit -m "feat: add durable application records"
  ```

### Task 4: Add retry-safe submission and run-status APIs

**Files:**

- Create: `src/agentforge/api.py`
- Create: `tests/integration/test_api.py`

**Interfaces:**

- Consumes: `Settings`, `RunRepository`.
- Produces: `create_app(settings: Settings) -> FastAPI`; `POST /api/v1/runs`;
  `GET /api/v1/runs/{run_id}`; `GET /health/live`; `GET /health/ready`.

- [ ] **Step 1: Write failing API tests**

  Assert the first valid `POST` returns `202`; an identical retry returns `200`
  with the same run ID; a changed payload with the same submission key returns
  `409`; malformed keys, empty goals, empty ticket lists, absolute ticket paths,
  and unknown fields return `422`; unknown run IDs return `404`; readiness
  fails when PostgreSQL is unavailable.

- [ ] **Step 2: Run API tests and confirm failure**

  ```powershell
  .\.venv\Scripts\python.exe -m pytest tests/integration/test_api.py -v
  ```

  Expected: FAIL because `agentforge.api` does not exist.

- [ ] **Step 3: Implement `create_app` and endpoints**

  Accept `submission_key`, `goal`, and ordered `ticket_files` in JSON. Return a
  bounded public snapshot containing IDs, state, timestamps, terminal failure
  classification, and artifact metadata, never request bodies, filesystem root
  paths, or database errors.

- [ ] **Step 4: Run API tests**

  Run the command from Step 2. Expected: PASS.

- [ ] **Step 5: Commit the API**

  ```powershell
  git add src/agentforge/api.py tests/integration/test_api.py
  git commit -m "feat: add retry-safe run API"
  ```

### Task 5: Relay start intents to stable Temporal Workflows

**Files:**

- Create: `src/agentforge/relay.py`
- Create: `tests/integration/test_relay.py`

**Interfaces:**

- Consumes: claimed outbox records and a Temporal client.
- Produces: `WorkflowStarter` protocol;
  `relay_once(settings: Settings, starter: WorkflowStarter, limit: int = 10) -> RelayStats`;
  `run_forever(settings: Settings) -> None`.

- [ ] **Step 1: Write failing relay tests**

  With a fake starter, assert one pending row starts with its stored Workflow ID
  and configured task queue, success marks it started, retryable errors increase
  attempts and schedule bounded exponential backoff, and permanent validation
  errors mark the run `FAILED`.

- [ ] **Step 2: Add the ambiguous-start recovery test**

  Simulate Temporal accepting the start and the database mark failing. On the
  next relay pass, make the starter raise `WorkflowAlreadyStartedError`; assert
  the relay treats the matching stable Workflow ID as success and marks the
  outbox row started without creating another run or Workflow ID.

- [ ] **Step 3: Run relay tests and confirm failure**

  ```powershell
  .\.venv\Scripts\python.exe -m pytest tests/integration/test_relay.py -v
  ```

  Expected: FAIL because `agentforge.relay` does not exist.

- [ ] **Step 4: Implement the one-pass relay and polling loop**

  Keep the relay a bridge only: it may claim, start, and mark start intents, but
  it may not schedule Workflow steps. Limit backoff to five minutes and avoid
  holding a database transaction open across the Temporal call.

- [ ] **Step 5: Run relay tests**

  Run the command from Step 3. Expected: PASS.

- [ ] **Step 6: Commit the relay**

  ```powershell
  git add src/agentforge/relay.py tests/integration/test_relay.py
  git commit -m "feat: relay durable workflow starts"
  ```

### Task 6: Build safe storage and the deterministic mock model

**Files:**

- Create: `src/agentforge/storage.py`
- Create: `src/agentforge/mock_model.py`
- Create: `tests/unit/test_storage.py`
- Create: `tests/unit/test_mock_model.py`
- Create: `samples/tickets/ticket-001.txt`
- Create: `samples/tickets/ticket-002.txt`
- Create: `artifacts/.gitkeep`

**Interfaces:**

- Consumes: approved relative ticket names, configured roots, and compact
  Workflow state.
- Produces: `ArtifactStorage.snapshot_tickets(...) -> TicketSnapshotRef`;
  `ArtifactStorage.publish_report(...) -> ArtifactRef`;
  `DeterministicMockModel.decide(input: ModelDecisionInput) -> AgentAction`.

- [ ] **Step 1: Write failing approved-read tests**

  Assert normal `.txt` and `.md` files are snapshotted in lexical path order;
  absolute paths, `..`, missing files, directories, unsupported extensions, and
  symlink escapes are rejected; 65,537-byte individual input and aggregate
  input above 262,144 bytes fail without a snapshot.

- [ ] **Step 2: Write failing atomic-publication tests**

  Assert publication creates exactly
  `reports/<run-id>/support-report.md`, returns byte size and lowercase SHA-256,
  verifies the checksum from disk, leaves no temporary file after success, and
  leaves no partial final file when the atomic replacement is forced to fail.

- [ ] **Step 3: Implement `ArtifactStorage`**

  Resolve every existing source with `Path.resolve(strict=True)`, reject any
  path outside the approved root or containing a symlink component, write
  staging files below `.staging/<run-id>/`, and finalize on the same filesystem
  with a flushed temporary file plus `os.replace`.

- [ ] **Step 4: Write failing mock-model tests**

  Assert the same input always produces the same action and report bytes; state
  with no ticket snapshot requests `ReadTicketsAction`; a ticket snapshot but
  no report requests `PublishReportAction` and creates a bounded draft; a final
  artifact requests `FinishAction`; malformed state fails rather than guessing.

- [ ] **Step 5: Implement `DeterministicMockModel.decide`**

  The report format is fixed: title, goal, ticket count, one lexically ordered
  section per ticket, and a final deterministic summary. Do not include current
  time, random values, absolute paths, credentials, or hidden reasoning.

- [ ] **Step 6: Run storage and model tests**

  ```powershell
  .\.venv\Scripts\python.exe -m pytest tests/unit/test_storage.py tests/unit/test_mock_model.py -v
  ```

  Expected: PASS.

- [ ] **Step 7: Commit safe tools and mock model**

  ```powershell
  git add src/agentforge/storage.py src/agentforge/mock_model.py tests/unit samples/tickets artifacts/.gitkeep
  git commit -m "feat: add deterministic report tools"
  ```

### Task 7: Execute the bounded Temporal Workflow

**Files:**

- Create: `src/agentforge/activities.py`
- Create: `src/agentforge/workflow.py`
- Create: `src/agentforge/worker.py`
- Create: `tests/integration/test_workflow.py`

**Interfaces:**

- Consumes: `WorkflowInput`, repository, storage, and mock-model interfaces.
- Produces: `SupportReportWorkflow.run(input: WorkflowInput) -> ArtifactRef`;
  registered Activities `project_event`, `decide_action`, `read_tickets`, and
  `publish_report`; `create_worker(settings: Settings) -> Worker`.

- [ ] **Step 1: Write the failing happy-path Workflow test**

  Against the local Temporal service, start a real test Worker, execute a
  Workflow with the two sample tickets, and assert `SUCCEEDED`, exactly one final
  artifact, a matching disk/database checksum, no ticket/report body in fetched
  Workflow history, and no more than four model decisions.

- [ ] **Step 2: Write failure and projection tests**

  Assert a fifth requested decision produces `BUDGET_EXHAUSTED`; a denied path
  produces a permanent `FAILED` outcome; an Activity retry keeps the same step
  ID but adds an attempt; replayed projection events do not duplicate attempts;
  and a stale lower sequence cannot replace terminal state.

- [ ] **Step 3: Run Workflow tests and confirm failure**

  ```powershell
  .\.venv\Scripts\python.exe -m pytest tests/integration/test_workflow.py -v
  ```

  Expected: FAIL because Workflow and Activities do not exist.

- [ ] **Step 4: Implement Activities with bounded retries**

  Derive step IDs from UUIDv5 of run ID plus ordinal and attempt IDs from step ID
  plus Temporal attempt number. Use short bounded retry policies, explicit
  start-to-close timeouts, and non-retryable application errors for invalid
  paths, invalid model output, and exceeded limits.

- [ ] **Step 5: Implement the deterministic Workflow loop**

  The Workflow may inspect only its input, prior Activity results, and Temporal
  APIs. It projects `RUNNING`, dispatches the action-specific Activity, stops on
  `FinishAction`, and projects exactly one terminal state. Unknown actions fail
  permanently. Report/ticket bodies never appear in input, output, memo, search
  attributes, logs, or exceptions.

- [ ] **Step 6: Implement and run the Worker entry point**

  Register exactly the Milestone 1 Workflow and four Activities on the
  configured task queue. Handle process shutdown without cancelling active
  Workflows.

- [ ] **Step 7: Run Workflow tests**

  Run the command from Step 3. Expected: PASS.

- [ ] **Step 8: Commit Temporal execution**

  ```powershell
  git add src/agentforge/activities.py src/agentforge/workflow.py src/agentforge/worker.py tests/integration/test_workflow.py
  git commit -m "feat: execute support reports with Temporal"
  ```

### Task 8: Prove the complete Milestone 1 slice and document it

**Files:**

- Create: `tests/integration/test_end_to_end.py`
- Create: `docs/learning/04_MILESTONE_1_BUILD.md`
- Modify: `README.md`
- Modify: `TEST_CHECKLIST.md`
- Modify: `PROJECT_PLAN.md`
- Replace: `HANDOVER.md`

**Interfaces:**

- Consumes: all Milestone 1 processes and local infrastructure.
- Produces: one repeatable end-to-end verification command, current operations
  instructions, checked acceptance evidence, and the next exact checkpoint.

- [ ] **Step 1: Write the failing end-to-end test**

  Submit through the ASGI app, execute one relay pass, run a Worker, poll the
  status endpoint to `SUCCEEDED`, verify report bytes and checksum, repeat the
  same submission and receive the same run, then submit changed content under
  the same key and receive `409`.

- [ ] **Step 2: Run the end-to-end test and fix only integration defects**

  ```powershell
  .\.venv\Scripts\python.exe -m pytest tests/integration/test_end_to_end.py -v
  ```

  Expected: PASS. Do not add Milestone 2 recovery features to solve Milestone 1
  defects.

- [ ] **Step 3: Run the complete verification suite**

  ```powershell
  .\.venv\Scripts\python.exe -m pytest -v
  .\.venv\Scripts\python.exe -m pip check
  docker compose config --quiet
  git diff --check
  ```

  Expected: all commands exit `0`.

- [ ] **Step 4: Perform the manual PowerShell demonstration**

  Document and execute separate API, relay, and Worker commands; submit the
  sample payload with `Invoke-RestMethod`; poll status; verify the report with
  `Get-FileHash -Algorithm SHA256`; stop with `docker compose stop`; restart and
  confirm the successful run and artifact remain. Mark persistence across
  ordinary service restart as observed only if it actually passes.

- [ ] **Step 5: Update operating and learning documentation**

  Add exact start, stop, status, logs, health, migration, test, and separately
  labeled destructive-reset commands to `README.md`. Explain API, outbox,
  Workflow, Activity, schema migration, checksum, and idempotency in
  `docs/learning/04_MILESTONE_1_BUILD.md`, distinguishing observed behavior from
  later plans.

- [ ] **Step 6: Close the checkpoint records**

  Check only acceptance items actually observed, update Milestone 1 state in
  `PROJECT_PLAN.md`, and replace `HANDOVER.md` with exact commits, changed files,
  command results, failures, limitations, and the first Milestone 2 action.

- [ ] **Step 7: Commit the verified milestone**

  ```powershell
  git add README.md TEST_CHECKLIST.md PROJECT_PLAN.md HANDOVER.md docs/learning/04_MILESTONE_1_BUILD.md tests/integration/test_end_to_end.py
  git commit -m "docs: record verified Milestone 1 workflow"
  git status --short --branch
  ```

  Expected: the commit uses only the configured human identity and the working
  tree is clean.

## Self-Review Result

- **Spec coverage:** Every Milestone 1 checklist item maps to Tasks 1–8. Approval
  and simulated inbox behavior remain correctly deferred to Milestone 3; the
  browser dashboard remains deferred to Milestone 4.
- **Step scan:** Each implementation task has a failing check, one bounded code
  responsibility, a passing check, and a coherent commit. Infrastructure and
  documentation tasks use explicit validation commands instead of artificial
  unit tests.
- **Type consistency:** `WorkflowInput`, `AgentAction`, storage references,
  `ProjectionEvent`, `RunSnapshot`, and repository method names are defined once
  and consumed under the same names in later tasks.
- **Review Focus:** All five high-risk input/failure classes have explicit tests
  in Tasks 3–7.
- **Proportion:** The plan fixes boundaries, interfaces, values, and evidence;
  it deliberately leaves function bodies to implementation.
