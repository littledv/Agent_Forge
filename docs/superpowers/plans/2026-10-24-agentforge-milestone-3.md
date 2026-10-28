# AgentForge Milestone 3 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> superpowers:subagent-driven-development (recommended) or
> superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Require an exact, durable human approval before report delivery and
prove that retrying after a Worker dies in the receiver-commit ambiguity window
produces one observable simulated-inbox delivery.

**Architecture:** PostgreSQL stores exact action-bound approval, cancellation,
and signal-outbox records in the AgentForge database; the existing relay sends
idempotent Temporal signals without becoming a scheduler. The Workflow waits
durably, rechecks the authoritative database at decision/expiry boundaries, and
calls a loopback-only simulated inbox whose separate PostgreSQL database
deduplicates a stable operation ID. Delivery remains an Activity, and a
test-only client wrapper forces process death after the inbox commits but before
Temporal receives the Activity acknowledgement.

**Tech Stack:** Python 3.13.17, FastAPI 0.158.1, Temporal Python SDK 1.33.0,
Temporal Server 1.32.1, PostgreSQL 16.15, SQLAlchemy 2.1.6, Alembic 1.20.1,
Pydantic 2.14.1, pytest 9.1.1, Windows PowerShell.

**Spec:** `PROJECT_PLAN.md` (Milestone 3), with `ARCHITECTURE.md`, `FLOW.md`,
`CONSTRAINTS.md`, `DECISIONS.md`, and `TEST_CHECKLIST.md` as normative
supporting documents.

## Global Constraints

- Implement Milestone 3 only; do not add the dashboard, a real model provider,
  authentication, deployment, a remote, or public network access.
- Add or upgrade no Python dependency and add no container image. Reuse the
  pinned FastAPI, SQLAlchemy, Alembic, Temporal, PostgreSQL, and pytest stack;
  use the Python standard library for the production inbox HTTP client.
- Bind every approval to canonical `deliver_report` arguments: target
  `simulated-inbox` plus the exact artifact path, media type, size, and SHA-256.
  The model cannot supply a URL, approval ID, action hash, or operation ID.
- Derive stable approval and operation UUIDs from the run ID and canonical
  action hash. Any argument change therefore requires a distinct approval and
  operation.
- Persist an approval or cancellation decision and its Temporal signal-outbox
  row in one AgentForge PostgreSQL transaction. Signal retries must be harmless.
- Keep Workflow code deterministic. Database access, artifact reads, HTTP
  calls, and all other side effects remain in Activities or external services.
- The simulated inbox is a separate loopback-only FastAPI process on
  `127.0.0.1:8001`; it owns `agentforge_inbox` and `agentforge_inbox_test` and
  AgentForge may reach it only through HTTP.
- The receiver returns the original receipt for an identical repeated
  operation ID and returns `409` when the same operation ID carries different
  canonical content.
- Cancellation prevents work that has not started. It never claims to undo a
  delivery that may already have committed.
- A delivery result that cannot be verified after bounded retries becomes
  `NEEDS_ATTENTION` with `UNCERTAIN`, never `SUCCEEDED`.
- Preserve the existing ten-second Activity Start-To-Close timeout, bounded
  three-attempt side-effect retry policy, stable step IDs, and monotonic
  PostgreSQL projections.
- Keep report bodies out of Temporal history. Store the body only in the host
  artifact directory and the inbox-owned delivery record.
- Keep credentials, private data, absolute host paths, raw database errors, and
  report bodies out of ordinary logs and API error responses.

## File Structure

- `src/agentforge/domain.py` — approval, signal, delivery-action, request, and
  receipt contracts shared across trusted components.
- `src/agentforge/policy.py` — pure canonicalization, exact artifact matching,
  action hashing, and stable approval/operation identity.
- `src/agentforge/config.py` — bounded approval TTL and loopback inbox URL for
  AgentForge, plus an isolated inbox-database settings model for the receiver.
- `src/agentforge/mock_model.py` — in Task 5, add the deterministic
  `deliver_report` action after publication and `finish` only after a receipt
  exists.
- `src/agentforge/db_models.py` and
  `alembic/versions/0002_approval_and_signal_records.py` — AgentForge-owned
  approvals, cancellation requests, accepted delivery receipts, and signal
  outbox.
- `src/agentforge/repository.py` — transactional approval, cancellation,
  accepted-delivery, and signal-outbox operations.
- `src/agentforge/inbox_db_models.py`, `src/agentforge/inbox_repository.py`, and
  `src/agentforge/inbox_api.py` — receiver-owned storage and loopback HTTP API.
- `inbox_alembic.ini` and `inbox_alembic/` — an independent migration history
  for the inbox-owned database.
- `src/agentforge/api.py` — approval read/decision and cancellation endpoints.
- `src/agentforge/relay.py` — bounded signal-outbox delivery in addition to the
  existing Workflow-start relay.
- `src/agentforge/inbox_client.py` — bounded standard-library HTTP adapter and
  exact receipt validation.
- `src/agentforge/activities.py`, `src/agentforge/workflow.py`, and
  `src/agentforge/worker.py` — durable approval coordination and delivery.
- `tests/helpers/delivery_worker.py` — test-only Worker whose inbox client
  blocks after a committed receiver response.
- Unit and integration tests under `tests/` — policy, persistence, API, relay,
  Workflow outcomes, receiver concurrency, and native crash recovery.
- `README.md`, `FLOW.md`, `ARCHITECTURE.md`, `TEST_CHECKLIST.md`,
  `PROJECT_PLAN.md`, `docs/learning/06_MILESTONE_3_SAFE_DELIVERY.md`, and
  `HANDOVER.md` — operating instructions, proof boundary, and checkpoint state.

## Review Focus

- A decision racing expiry must have one durable winner: the locked approval
  row remains approved/rejected or becomes expired, and the Workflow follows
  that stored result. Task 2 and Task 5 test both transaction orderings.
- A relay crash after Temporal accepts a signal but before PostgreSQL records
  success must resend the same message without changing the Workflow outcome.
  Task 4 tests exact duplicates and rejects mismatched message reuse.
- A cancellation persisted before delivery but signalled late must still stop
  the future inbox call when the delivery gate rechecks PostgreSQL. Task 5
  asserts zero receiver records.
- Concurrent receiver requests with one operation ID must commit one row,
  return one receipt for identical payloads, and conflict on changed payload.
  Task 3 exercises concurrent database sessions.
- A Worker killed after inbox commit but before Activity acknowledgement must
  retry the same operation ID, receive the original receipt, and finish with
  one receiver row. Task 6 performs the native process-termination proof.

---

### Task 1: Define exact delivery contracts and trusted policy

**Files:**

- Modify: `src/agentforge/domain.py`
- Create: `src/agentforge/policy.py`
- Modify: `src/agentforge/config.py`
- Modify: `.env.example`
- Modify: `tests/unit/test_domain.py`
- Create: `tests/unit/test_policy.py`
- Modify: `tests/unit/test_config.py`

**Interfaces:**

- Consumes: existing `StrictModel`, `ArtifactRef`, `ModelDecisionInput`,
  `AgentAction`, and `Settings`.
- Produces: `ApprovalState`, `ApprovalDecision`, `DeliverReportAction`,
  `DeliveryAuthorization`, `ApprovalSnapshot`, `ApprovalDecisionRequest`,
  `ApprovalSignal`, `CancellationSignal`, `DeliveryRequest`, and
  `DeliveryReceipt`; `Settings.inbox_base_url`,
  `Settings.approval_ttl_seconds`, and `InboxSettings.from_env()`; plus
  `authorize_delivery(run_id: UUID, action: DeliverReportAction,
  artifact: ArtifactRef) -> DeliveryAuthorization`.

- [ ] **Step 1: Write failing contract and policy tests**

  Add tests proving that the fourth action variant is exactly
  `{"type":"deliver_report","target":"simulated-inbox","artifact":...}`;
  unknown targets/fields fail Pydantic validation; authorization rejects an
  artifact mismatch; canonical key order does not affect the SHA-256; and the
  same run/action produces the same approval ID, operation ID, and action hash.

- [ ] **Step 2: Write failing configuration tests**

  Add safe local defaults for `AGENTFORGE_INBOX_BASE_URL` and
  `AGENTFORGE_APPROVAL_TTL_SECONDS` on `Settings`, and define separate
  `InboxSettings` from `AGENTFORGE_INBOX_DATABASE_URL`, while accepting explicit
  environment overrides. Assert that only an `http` loopback base URL with no
  credentials, query, or fragment is accepted and TTL is positive.

- [ ] **Step 3: Run the focused unit tests and confirm RED**

  ```powershell
  .\.venv\Scripts\python.exe -m pytest tests/unit/test_domain.py tests/unit/test_policy.py tests/unit/test_config.py -v
  ```

  Expected: FAIL because the delivery contracts, policy, and settings do not
  exist.

- [ ] **Step 4: Implement the contracts and pure policy**

  Add the named strict models and enums. In `authorize_delivery(...)`, compare
  the complete trusted `ArtifactRef`, canonicalize the action with sorted,
  compact JSON, hash it with SHA-256, and derive UUID5 identities from the run
  ID plus `approval:<hash>` and `delivery:<hash>`. Extend
  `ModelDecisionInput` with `delivery: DeliveryReceipt | None`; keep URLs and
  generated identities out of `AgentAction`.

- [ ] **Step 5: Implement bounded local settings**

  Add the three environment-backed settings with local defaults, the loopback
  URL validator, and `.env.example` values. Keep existing direct `Settings(...)`
  test builders compatible and do not add a dependency.

- [ ] **Step 6: Run the focused contract tests**

  ```powershell
  .\.venv\Scripts\python.exe -m pytest tests/unit/test_domain.py tests/unit/test_policy.py tests/unit/test_config.py -v
  ```

  Expected: PASS without changing the current Workflow behavior.

- [ ] **Step 7: Commit the trusted contracts**

  ```powershell
  git add src/agentforge/domain.py src/agentforge/policy.py src/agentforge/config.py .env.example tests/unit/test_domain.py tests/unit/test_policy.py tests/unit/test_config.py
  git commit -m "feat: define approved delivery contracts"
  ```

### Task 2: Persist approvals, cancellations, and signal intents

**Files:**

- Modify: `src/agentforge/db_models.py`
- Create: `alembic/versions/0002_approval_and_signal_records.py`
- Modify: `src/agentforge/repository.py`
- Modify: `tests/integration/conftest.py`
- Modify: `tests/integration/test_migrations.py`
- Modify: `tests/integration/test_repository.py`

**Interfaces:**

- Consumes: Task 1 `DeliveryAuthorization`, `ApprovalSnapshot`,
  `ApprovalDecisionRequest`, `ApprovalSignal`, and `CancellationSignal`.
- Produces: `ApprovalRecord`, `CancellationRequestRecord`, `DeliveryRecord`,
  `WorkflowSignalOutboxRecord`; `ApprovalRepository.create_pending(...)`,
  `ApprovalRepository.decide(...)`, `ApprovalRepository.resolve_or_expire(...)`,
  `CancellationRepository.request(...)`, and
  `DeliveryRepository.record_receipt(...)`; plus
  `SignalOutboxRepository.claim_batch(...)`/`mark_sent(...)`/
  `record_failure(...)`/`mark_permanent_failure(...)`.

- [ ] **Step 1: Write the failing migration test**

  Upgrade from `0001_initial_records` to head and assert the approval,
  cancellation, accepted-delivery, and signal-outbox tables, named uniqueness
  constraints, state checks, SHA-256 checks, positive attempt checks, foreign
  keys, and pending outbox index. Downgrade to `0001_initial_records`, assert
  the new tables are gone, then restore head.

- [ ] **Step 2: Write failing approval transaction tests**

  Assert pending creation is idempotent for one exact authorization; changed
  canonical action gets a distinct approval; approve/reject persists the first
  decision and a matching signal-outbox row atomically; an identical repeat is
  harmless; a conflicting repeat raises `ApprovalConflict`; and an unknown or
  mismatched action hash cannot decide the row.

- [ ] **Step 3: Write failing expiry-race and cancellation tests**

  Use two independent sessions to pin both lock orderings: a committed decision
  wins over expiry, while committed expiry rejects a later decision. Assert one
  stable cancellation request and signal intent per run, identical repeats are
  harmless, and terminal runs reject new cancellation requests. Assert an
  accepted receiver receipt is idempotently recorded once under its operation
  ID and conflicting receipt metadata is rejected.

- [ ] **Step 4: Run the persistence tests and confirm RED**

  ```powershell
  .\.venv\Scripts\python.exe -m pytest tests/integration/test_migrations.py tests/integration/test_repository.py -v
  ```

  Expected: FAIL because revision `0002` and the repositories do not exist.

- [ ] **Step 5: Implement revision `0002` and transactional repositories**

  Store canonical action JSON, hash, operation ID, state, expiry, and decision
  times on approvals. Store a stable request per cancelled run. Store each
  accepted receiver receipt under its unique operation ID and bind it to the
  run, approval, artifact hash, receiver delivery ID, and committed time. Store
  signal name, stable message ID, workflow ID, strict payload, lease, attempts,
  and send/error timestamps in the outbox. Lock the approval row when decision
  and expiry compete, and insert the matching signal row in the same
  transaction that persists a decision or cancellation.

- [ ] **Step 6: Run the persistence slice**

  Run the command from Step 4.

  Expected: PASS, including the coordinated two-session race tests and a clean
  upgrade/downgrade/upgrade cycle.

- [ ] **Step 7: Commit application safety persistence**

  ```powershell
  git add src/agentforge/db_models.py alembic/versions/0002_approval_and_signal_records.py src/agentforge/repository.py tests/integration/conftest.py tests/integration/test_migrations.py tests/integration/test_repository.py
  git commit -m "feat: persist approval and control intents"
  ```

### Task 3: Build the separate deduplicating inbox service

**Files:**

- Create: `src/agentforge/inbox_db_models.py`
- Create: `src/agentforge/inbox_repository.py`
- Create: `src/agentforge/inbox_api.py`
- Create: `inbox_alembic.ini`
- Create: `inbox_alembic/env.py`
- Create: `inbox_alembic/script.py.mako`
- Create: `inbox_alembic/versions/0001_inbox_deliveries.py`
- Modify: `infra/postgres/init/01-create-app-databases.sql`
- Modify: `pyproject.toml`
- Modify: `tests/integration/conftest.py`
- Create: `tests/integration/test_inbox_migrations.py`
- Create: `tests/integration/test_inbox_api.py`

**Interfaces:**

- Consumes: Task 1 `DeliveryRequest`, `DeliveryReceipt`, and `InboxSettings`.
- Produces: `InboxRepository.deliver(session, request) -> InboxDeliveryResult`,
  `InboxRepository.get(session, operation_id) -> DeliveryReceipt | None`,
  `create_inbox_app(settings: InboxSettings) -> FastAPI`, and the
  `agentforge-inbox` console command.

- [ ] **Step 1: Write the failing inbox migration test**

  Target `agentforge_inbox_test` with the independent Alembic configuration.
  Assert a `deliveries` table with unique `operation_id`, canonical request
  hash, action hash, artifact metadata, report body, receipt ID, and committed
  timestamp; verify downgrade to base and restoration to head.

- [ ] **Step 2: Write failing receiver behavior tests**

  POST `/api/v1/deliveries` and assert `201` for the first commit, `200` with
  the byte-for-byte same receipt for an identical retry, `409` for changed
  canonical content under the same operation ID, `422` for malformed content,
  and no database details in errors. GET by operation ID returns the receipt or
  `404`.

- [ ] **Step 3: Write the failing concurrent-deduplication test**

  Submit the same validated request concurrently through two independent
  sessions. Assert both calls return the same receipt, one reports creation,
  and exactly one delivery row exists. Repeat with changed content and assert
  one success plus one conflict without replacing the committed row.

- [ ] **Step 4: Run the inbox tests and confirm RED**

  ```powershell
  .\.venv\Scripts\python.exe -m pytest tests/integration/test_inbox_migrations.py tests/integration/test_inbox_api.py -v
  ```

  Expected: FAIL because the independent schema and service do not exist.

- [ ] **Step 5: Implement the receiver-owned schema, repository, and API**

  Canonically hash the validated request before insert. Use PostgreSQL's unique
  operation constraint as the concurrency authority; after an integrity race,
  reload the winner and compare its request hash. Return its stored receipt for
  an exact duplicate and raise `InboxDeliveryConflict` for changed content.
  Bind Uvicorn to `127.0.0.1:8001`; do not expose or import AgentForge
  application repositories from this service.

- [ ] **Step 6: Run the inbox slice and package check**

  ```powershell
  docker compose exec -T postgresql psql -U temporal -d postgres -f /docker-entrypoint-initdb.d/01-create-app-databases.sql
  .\.venv\Scripts\python.exe -m pytest tests/integration/test_inbox_migrations.py tests/integration/test_inbox_api.py -v
  .\.venv\Scripts\python.exe -m pip check
  ```

  Expected: the idempotent SQL creates the two inbox database names even on an
  existing named volume, all inbox tests pass, and no dependency is broken or
  added.

- [ ] **Step 7: Commit the simulated inbox**

  ```powershell
  git add src/agentforge/inbox_db_models.py src/agentforge/inbox_repository.py src/agentforge/inbox_api.py inbox_alembic.ini inbox_alembic infra/postgres/init/01-create-app-databases.sql pyproject.toml tests/integration/conftest.py tests/integration/test_inbox_migrations.py tests/integration/test_inbox_api.py
  git commit -m "feat: add deduplicating simulated inbox"
  ```

### Task 4: Expose decisions and relay durable signals

**Files:**

- Modify: `src/agentforge/api.py`
- Modify: `src/agentforge/relay.py`
- Modify: `tests/integration/test_api.py`
- Modify: `tests/integration/test_relay.py`

**Interfaces:**

- Consumes: Task 2 repositories and `WorkflowSignalOutboxRecord` payloads.
- Produces: `GET /api/v1/runs/{run_id}/approval`,
  `POST /api/v1/approvals/{approval_id}/decision`,
  `POST /api/v1/runs/{run_id}/cancel`,
  `WorkflowSignaler.signal(*, workflow_id: str, signal_name: str,
  payload: ApprovalSignal | CancellationSignal) -> None`, and
  `relay_signals_once(settings, signaler, limit=10) -> SignalRelayStats`.

- [ ] **Step 1: Write failing approval and cancellation API tests**

  Assert approval GET returns the exact canonical payload and hash; first
  approve/reject returns `202`; identical retry returns `200`; changed hash,
  opposite decision, expired approval, and terminal-run cancellation return
  `409`; missing records return `404`; malformed bodies return `422`; and every
  accepted first decision creates exactly one matching signal intent.

- [ ] **Step 2: Write failing signal-relay tests**

  With a fake `WorkflowSignaler`, assert bounded leasing, stable workflow ID,
  strict payload deserialization, retry backoff without raw error text, reclaim
  after a stale lease, and permanent failure for invalid stored payload. Model
  the crash-after-signal case by leaving the row unsent, reclaim it, and assert
  the exact same message ID and payload are sent again.

- [ ] **Step 3: Run API and relay tests and confirm RED**

  ```powershell
  .\.venv\Scripts\python.exe -m pytest tests/integration/test_api.py tests/integration/test_relay.py -v
  ```

  Expected: FAIL because the routes and signal relay are absent.

- [ ] **Step 4: Implement the bounded API routes**

  Map repository outcomes to the status codes above. Do not call Temporal in an
  API request: return after the application record and signal intent commit.
  Keep the response body limited to strict approval/cancellation snapshots and
  safe conflict text.

- [ ] **Step 5: Implement signal delivery in the existing relay process**

  Resolve the stable Workflow handle with `Client.get_workflow_handle(...)` and
  call `handle.signal(signal_name, payload)`. Run one start-outbox batch and one
  signal-outbox batch per loop. Mark a row sent only after Temporal acknowledges
  the signal; let a reclaimed row resend the unchanged message.

- [ ] **Step 6: Run the API and relay slice**

  Run the command from Step 3.

  Expected: PASS with no direct API-to-Temporal gap and no duplicate decision
  records or message identities.

- [ ] **Step 7: Commit durable control delivery**

  ```powershell
  git add src/agentforge/api.py src/agentforge/relay.py tests/integration/test_api.py tests/integration/test_relay.py
  git commit -m "feat: relay durable approval signals"
  ```

### Task 5: Gate Workflow delivery on the stored decision

**Files:**

- Create: `src/agentforge/inbox_client.py`
- Modify: `src/agentforge/storage.py`
- Modify: `src/agentforge/mock_model.py`
- Modify: `src/agentforge/activities.py`
- Modify: `src/agentforge/workflow.py`
- Modify: `src/agentforge/worker.py`
- Modify: `tests/unit/test_storage.py`
- Modify: `tests/unit/test_mock_model.py`
- Create: `tests/unit/test_inbox_client.py`
- Modify: `tests/integration/test_workflow.py`
- Modify: `tests/integration/test_end_to_end.py`

**Interfaces:**

- Consumes: Task 1 authorization/contracts, Task 2 approval/cancellation and
  accepted-delivery repositories, Task 3 inbox HTTP contract, and Task 4 signal
  payloads.
- Produces: the deterministic model transition
  `artifact -> deliver_report -> receipt -> finish`,
  `InboxClient.deliver(request: DeliveryRequest) -> DeliveryReceipt`,
  `ArtifactStorage.read_verified_artifact(ref: ArtifactRef) -> bytes`, Activity
  inputs and methods `create_delivery_approval`, `resolve_delivery_gate`, and
  `deliver_report`, plus Workflow signals `approval_decided` and
  `cancellation_requested`.

- [ ] **Step 1: Write failing model, storage, and HTTP-adapter tests**

  Assert the mock model proposes delivery after publication and finish only
  after the matching `DeliveryReceipt` exists. Assert the storage helper
  returns only bytes matching path, size, media type, and SHA-256; changed files
  are rejected. With a loopback test server, assert the client accepts
  `200`/`201` only when the strict receipt matches the operation ID and artifact
  hash, classifies timeout/`5xx` as retryable, classifies `409`/malformed
  receipts as permanent uncertainty, enforces a bounded response size, and
  never follows redirects.

- [ ] **Step 2: Write failing Workflow approval-outcome tests**

  Drive real Temporal Workflows and assert: no inbox call occurs while state is
  `WAITING_APPROVAL`; approval resumes and produces one delivery; rejection and
  expiry end in `DENIED`; duplicate identical signals are harmless; a signal
  with the wrong approval ID/hash is ignored; and the expiry/decision race
  follows the locked database winner.

- [ ] **Step 3: Write failing cancellation and uncertain-result tests**

  Persist cancellation but delay its signal, then release approval and assert
  the pre-delivery database gate produces `CANCELLED` with zero inbox rows.
  Exhaust an unreachable/malformed receiver result and assert run
  `NEEDS_ATTENTION`, delivery step `UNCERTAIN`, and no false success. A
  cancellation arriving after a committed receipt projects `CANCELLED`, keeps
  the accepted delivery record and receiver row, and performs no later work; it
  does not remove or deny that receipt.

- [ ] **Step 4: Run the focused tests and confirm RED**

  ```powershell
  .\.venv\Scripts\python.exe -m pytest tests/unit/test_mock_model.py tests/unit/test_storage.py tests/unit/test_inbox_client.py tests/integration/test_workflow.py tests/integration/test_end_to_end.py -v
  ```

  Expected: FAIL because the Workflow still finishes immediately after
  publication and has no signal handlers or delivery Activity.

- [ ] **Step 5: Implement the model transition, verified client, and Activities**

  Add the deterministic `artifact -> deliver_report -> receipt -> finish`
  transition. Read and verify report bytes immediately before constructing
  `DeliveryRequest`. Execute the blocking standard-library request with
  `asyncio.to_thread`, fixed method/content type, short connect/read timeout,
  redirect rejection, and bounded JSON response. Record stable delivery-step
  attempts using the existing Activity attempt machinery, then idempotently
  persist the validated receiver receipt in AgentForge PostgreSQL before the
  Activity returns.

- [ ] **Step 6: Implement deterministic approval coordination**

  Register synchronous `@workflow.signal` handlers that accept strict payloads,
  ignore exact duplicate message IDs, and retain only the current matching
  approval/cancellation state. After creating and projecting the approval,
  `workflow.wait_condition(...)` until a signal/cancellation or the recorded
  expiry. In every branch call `resolve_delivery_gate` so PostgreSQL decides
  approval-versus-expiry and catches a delayed cancellation before scheduling
  delivery. Map reject/expire to `DENIED`, pre-delivery cancellation to
  `CANCELLED`, recheck cancellation after a committed delivery before the next
  model decision, and map unreconciled delivery exhaustion to
  `NEEDS_ATTENTION`/`UNCERTAIN`.

- [ ] **Step 7: Update the existing end-to-end path and run the slice**

  The end-to-end test must start both local apps, wait for the exact approval,
  decide it through the API, relay its signal, then assert `SUCCEEDED`, one
  artifact, one inbox receipt, and no report body in Temporal history. Run the
  command from Step 4.

  Expected: PASS for approval, rejection, expiry, duplicates, cancellation,
  uncertainty, and the approved happy path.

- [ ] **Step 8: Commit the approval-gated Workflow**

  ```powershell
  git add src/agentforge/inbox_client.py src/agentforge/storage.py src/agentforge/mock_model.py src/agentforge/activities.py src/agentforge/workflow.py src/agentforge/worker.py tests/unit/test_mock_model.py tests/unit/test_storage.py tests/unit/test_inbox_client.py tests/integration/test_workflow.py tests/integration/test_end_to_end.py
  git commit -m "feat: gate report delivery on approval"
  ```

### Task 6: Prove recovery after receiver commit

**Files:**

- Create: `tests/helpers/delivery_worker.py`
- Create: `tests/integration/test_delivery_recovery.py`

**Interfaces:**

- Consumes: `create_worker(settings, inbox_client=...)`, production
  `InboxClient`, Task 4 signal relay, and the real inbox HTTP/database boundary.
- Produces: test-only
  `BlockingCommittedInboxClient(delegate, marker_path).deliver(request) ->
  DeliveryReceipt` and
  `test_worker_death_after_inbox_commit_retries_one_operation()`.

- [ ] **Step 1: Add the test-only controlled Worker**

  Build an executable native Windows helper. With
  `AGENTFORGE_DELIVERY_COMMIT_MARKER`, wrap the production inbox client so it
  performs the real HTTP request, validates the committed receipt, atomically
  writes the marker, and then blocks indefinitely before returning to the
  Activity. Without the variable, run the ordinary replacement Worker. Do not
  add this setting or failure path to production configuration.

- [ ] **Step 2: Write the failing native ambiguity-window test**

  Start a loopback inbox server against `agentforge_inbox_test`; submit and
  relay one run; launch the blocking Worker; wait for the exact approval;
  approve through the API and relay the signal. After the commit marker exists,
  terminate and reap the Worker, start a normal replacement on the same task
  queue, and await the original Workflow. Always stop/reap both Worker and
  inbox processes in `finally`.

  Assert:

  - one original run ID, Workflow ID, approval ID, action hash, and operation ID;
  - delivery Activity attempt `1` becomes `FAILED`/`RETRYABLE` and attempt `2`
    becomes `SUCCEEDED` on the same logical step;
  - the receiver retains exactly one row and returns one unchanged receipt;
  - the run reaches `SUCCEEDED` with the original artifact and receipt;
  - no report body enters Temporal history or process diagnostics; and
  - no helper Worker or inbox server remains after cleanup.

- [ ] **Step 3: Run the recovery test and confirm RED**

  ```powershell
  .\.venv\Scripts\python.exe -m pytest tests/integration/test_delivery_recovery.py -vv
  ```

  Expected: FAIL before the helper and recovery assertions are complete; all
  child processes are still reaped.

- [ ] **Step 4: Complete the bounded native harness**

  Add deadline-based readiness, approval, marker, projection, and process-exit
  waits. Include only safe child stderr in timeout diagnostics. Use the real
  ten-second Activity timeout; do not shorten production retry behavior or
  bypass the HTTP boundary.

- [ ] **Step 5: Run recovery and regression slices**

  ```powershell
  .\.venv\Scripts\python.exe -m pytest tests/integration/test_delivery_recovery.py tests/integration/test_worker_recovery.py tests/integration/test_end_to_end.py -v
  ```

  Expected: both the Milestone 2 mid-Activity recovery and Milestone 3
  post-receiver-commit recovery pass with no orphan process and one delivery.

- [ ] **Step 6: Commit the ambiguity-window proof**

  ```powershell
  git add tests/helpers/delivery_worker.py tests/integration/test_delivery_recovery.py
  git commit -m "test: prove deduplicated delivery recovery"
  ```

### Task 7: Verify and record the Milestone 3 checkpoint

**Files:**

- Modify: `README.md`
- Modify: `ARCHITECTURE.md`
- Modify: `FLOW.md`
- Create: `docs/learning/06_MILESTONE_3_SAFE_DELIVERY.md`
- Modify: `PROJECT_PLAN.md`
- Modify: `TEST_CHECKLIST.md`
- Replace: `HANDOVER.md`

**Interfaces:**

- Consumes: Tasks 1–6 and their observed database, Temporal-history, HTTP, and
  process evidence.
- Produces: repeatable native PowerShell operations, a precise safety claim,
  completed Milestone 3 checklist state, and the Milestone 4 approval boundary.

- [ ] **Step 1: Run the complete automated suite**

  ```powershell
  .\.venv\Scripts\python.exe -m pytest -v
  ```

  Expected: PASS, including migrations, approval races, receiver concurrency,
  cancellation, uncertainty, Worker recovery, and post-commit deduplication.

- [ ] **Step 2: Run environment and repository checks**

  ```powershell
  .\.venv\Scripts\python.exe -m pip check
  docker compose config --quiet
  .\.venv\Scripts\alembic.exe upgrade head
  .\.venv\Scripts\alembic.exe -c inbox_alembic.ini upgrade head
  git diff --check
  ```

  Expected: no broken requirements, valid Compose, both migration heads
  applied, and no whitespace errors.

- [ ] **Step 3: Demonstrate ordinary restart while awaiting approval**

  Start API, relay, inbox, and Worker natively; submit a run; wait for
  `WAITING_APPROVAL`; stop those four application processes without stopping
  PostgreSQL or Temporal; restart them; approve the same stored action; and
  assert the original Workflow reaches `SUCCEEDED` with one inbox delivery.
  Record stable IDs and timestamps in `HANDOVER.md`.

- [ ] **Step 4: Re-run the native ambiguity-window proof**

  ```powershell
  .\.venv\Scripts\python.exe -m pytest tests/integration/test_delivery_recovery.py -vv
  ```

  Expected: PASS. Record the run, Workflow, approval, action hash, operation,
  Activity-attempt, receipt, receiver-row-count, elapsed-time, and child-process
  cleanup evidence.

- [ ] **Step 5: Document the verified claim and boundary**

  Explain exact action binding, signal-outbox recovery, database-versus-Temporal
  ownership, approval expiry, cancellation semantics, receiver deduplication,
  and why the proof is not universal exactly-once delivery. Update checkboxes
  only for checks actually observed. Add loopback inbox startup, migrations,
  health, logs, ordinary stop, and destructive reset commands.

- [ ] **Step 6: Commit the verified checkpoint**

  ```powershell
  git add README.md ARCHITECTURE.md FLOW.md docs/learning/06_MILESTONE_3_SAFE_DELIVERY.md PROJECT_PLAN.md TEST_CHECKLIST.md HANDOVER.md
  git commit -m "docs: record verified Milestone 3 delivery"
  ```

- [ ] **Step 7: Verify the committed tree and stop for review**

  ```powershell
  git status --short --branch
  git log -8 --oneline
  ```

  Expected: the branch is clean and the coherent Milestone 3 commits follow
  this approved plan. Do not plan or implement Milestone 4 until the user
  reviews and approves the checkpoint.

## Self-Review Record

- **Spec coverage:** Tasks 1–5 implement trusted permission checks, exact
  approval binding, durable approve/reject/expiry/cancellation semantics, a
  separate receiver, and stable-operation deduplication. Task 6 proves the
  required post-commit crash window; Task 7 proves restart durability and
  records the boundary.
- **Step scan:** Every step names one checkable action, its exact files or
  interface, the command that demonstrates it, and the expected result. Routine
  implementations are left to the implementer rather than transcribed.
- **Type consistency:** `DeliveryAuthorization` and the Task 1 signal/request
  models flow unchanged through repository, API, relay, Workflow, Activity,
  client, and receiver tasks. `operation_id` is one UUID from authorization to
  the inbox uniqueness constraint.
- **Review focus:** All five listed races and failure modes have explicit tests
  in Tasks 2–6, including coordinated database sessions and native process
  termination.
- **Proportion:** The seven tasks separate contracts, two independently owned
  persistence domains, control delivery, Workflow behavior, crash proof, and
  final operations evidence. No later dashboard or provider work is included.
