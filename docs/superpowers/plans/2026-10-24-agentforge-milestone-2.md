# AgentForge Milestone 2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> superpowers:subagent-driven-development (recommended) or
> superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prove that a real AgentForge support-report run survives forced
termination of its Windows Worker during an Activity and completes on a
replacement Worker without losing durable progress.

**Architecture:** Keep Temporal as the execution-history authority and use its
existing Activity Start-To-Close timeout and retry policy to detect the dead
Worker. A test-only native worker process blocks during the second model
decision, the integration test terminates that process, and a replacement
worker on the same task queue replays the Workflow and completes it. PostgreSQL
continues to project stable logical steps and records the abandoned and
replacement Activity attempts without becoming a scheduler.

**Tech Stack:** Python 3.13.17, Temporal Python SDK 1.33.0, Temporal Server
1.32.1, PostgreSQL 16.15, SQLAlchemy 2.1.6, pytest 9.1.1, Windows PowerShell.

**Spec:** `PROJECT_PLAN.md` (Milestone 2), with `ARCHITECTURE.md`, `FLOW.md`,
`CONSTRAINTS.md`, and `TEST_CHECKLIST.md` as normative supporting documents.

## Global Constraints

- Implement Milestone 2 only; do not add approvals, delivery, cancellation,
  dashboard behavior, a real model provider, deployment, or a remote.
- Add no dependency, container image, database migration, service, or published
  port; the pinned Milestone 1 runtime is sufficient.
- Run the recovery demonstration as native Windows Python processes against the
  existing loopback-only Temporal and PostgreSQL services.
- Keep Workflow code deterministic; failure injection belongs in test support,
  not in the production Workflow or public API.
- Temporal remains the execution-history authority and decides when an Activity
  retry is eligible; PostgreSQL records application-facing step and attempt
  projections only.
- Preserve the existing stable run, Workflow, step, attempt, projection-event,
  and artifact identifiers.
- Use the existing 10-second Activity Start-To-Close timeout, three-attempt
  Activity retry policy, and 30-second graceful Worker shutdown setting. The
  recovery test must terminate the process rather than invoking graceful
  shutdown.
- Keep ticket and artifact bodies out of Temporal history and keep credentials,
  private data, and raw database errors out of logs and fixtures.
- Do not claim exactly-once external side effects; simulated inbox delivery and
  its crash-after-commit window remain Milestone 3.

## File Structure

- `src/agentforge/repository.py` — serialize attempt-state updates per logical
  step, reconcile superseded running attempts, and ignore stale completions.
- `tests/integration/test_repository.py` — pin attempt reconciliation and stale
  update behavior at the PostgreSQL boundary.
- `tests/helpers/recovery_worker.py` — test-only Worker process whose injected
  model can expose and block at a deterministic Activity checkpoint.
- `tests/integration/test_worker_recovery.py` — start, terminate, replace, and
  verify native Worker processes against real Temporal and PostgreSQL.
- `README.md` — document the verified recovery behavior and repeatable command.
- `docs/learning/05_MILESTONE_2_RECOVERY.md` — explain replay, Activity retry,
  and the limits of the proof in plain language.
- `PROJECT_PLAN.md`, `TEST_CHECKLIST.md`, and `HANDOVER.md` — record the approved
  checkpoint and only checks that actually ran.

## Review Focus

- A Worker terminated after an Activity starts but before it reports completion
  must produce a Temporal timeout and a later attempt on the replacement Worker;
  Task 2 asserts the timeout event and attempts `1` then `2`.
- The replacement Worker must continue the original Workflow execution on the
  original task queue, not start a second run; Task 2 asserts one run ID, one
  Workflow ID, and one terminal `SUCCEEDED` projection.
- Workflow replay must reuse completed Activity results instead of reading the
  tickets again; Task 2 asserts the earlier `read_tickets` logical step still
  has exactly one attempt.
- Retrying after process death must not create duplicate final artifacts; Task 2
  asserts one artifact row and verifies its bytes against its SHA-256 metadata.
- A late terminal update from an older attempt must not overwrite the abandoned
  classification after a newer attempt exists; Task 1 pins both transaction
  orderings under a per-step row lock.

---

### Task 1: Reconcile abandoned Activity attempts

**Files:**

- Modify: `src/agentforge/repository.py`
- Modify: `tests/integration/test_repository.py`

**Interfaces:**

- Consumes: `record_activity_attempt(...)`, stable `step_id_for(...)` and
  `attempt_id_for(...)`, and existing `StepRecord`/`AttemptRecord` rows.
- Produces: the unchanged
  `record_activity_attempt(session, *, run_id, ordinal, name, attempt_number,
  attempt_state, step_state, failure_kind=None) -> tuple[UUID, UUID]` interface
  with serialized, monotonic attempt reconciliation.

- [ ] **Step 1: Write the failing superseded-attempt test**

  Add
  `test_starting_retry_marks_older_running_attempt_failed_retryable()`.
  Record attempt `1` as `RUNNING`, then attempt `2` as `RUNNING` for the same
  run and ordinal. Assert one logical step remains, attempt `1` is `FAILED` with
  `RETRYABLE`, and attempt `2` is `RUNNING`.

- [ ] **Step 2: Write the failing stale-completion tests**

  Add parameterized coverage for both orderings around the step lock:
  `test_older_terminal_update_cannot_override_newer_attempt()`. Assert that an
  attempt `1` success recorded after attempt `2` exists is ignored and the
  older attempt remains `FAILED`/`RETRYABLE`. If attempt `1` reaches
  `SUCCEEDED` before attempt `2` starts, preserve that terminal fact when the
  retry begins; only abandoned `RUNNING` attempts are reclassified.

- [ ] **Step 3: Run the focused repository tests and confirm failure**

  ```powershell
  .\.venv\Scripts\python.exe -m pytest tests/integration/test_repository.py -v
  ```

  Expected: the new tests fail because older attempts remain `RUNNING` or can be
  changed after a higher attempt is present; all pre-existing repository tests
  still pass.

- [ ] **Step 4: Implement serialized attempt reconciliation**

  In `record_activity_attempt(...)`, upsert the logical step, lock that step row
  with `SELECT ... FOR UPDATE`, and inspect whether a higher attempt number
  already exists. Ignore a stale terminal update when it targets a lower
  attempt. When a new `RUNNING` attempt is recorded, atomically change every
  lower `RUNNING` attempt for that step to `FAILED` with failure kind
  `RETRYABLE` while preserving lower attempts that are already terminal, then
  upsert the current attempt. Keep IDs and the public function signature
  unchanged.

- [ ] **Step 5: Run the repository tests**

  Run the command from Step 3.

  Expected: all repository tests pass, with one step row and deterministic
  attempt IDs preserved.

- [ ] **Step 6: Commit attempt reconciliation**

  ```powershell
  git add src/agentforge/repository.py tests/integration/test_repository.py
  git commit -m "feat: reconcile retried activity attempts"
  ```

### Task 2: Prove recovery with terminated and replacement Worker processes

**Files:**

- Create: `tests/helpers/recovery_worker.py`
- Create: `tests/integration/test_worker_recovery.py`

**Interfaces:**

- Consumes: `Settings.from_env()`, `create_worker(settings, model=...)`,
  `DeterministicMockModel`, `RunRepository.submit(...)`, `relay_once(...)`, the
  existing Temporal task queue, and the Task 1 attempt invariant.
- Produces: test-only
  `BlockingSecondDecisionModel(delegate, marker_path).decide(input) ->
  AgentAction`; a native child-process entry point; and
  `test_worker_termination_replays_and_completes_on_replacement()`.

- [ ] **Step 1: Add the test-only controlled Worker helper**

  Build `tests/helpers/recovery_worker.py` as an executable Python file. It
  reads normal AgentForge settings from the environment. When
  `AGENTFORGE_RECOVERY_BLOCK_MARKER` is present, wrap the deterministic model so
  decision `2` writes that marker only after the Activity has recorded its
  `RUNNING` attempt, then blocks indefinitely. With no marker variable, create
  the ordinary Worker. Use `asyncio.SelectorEventLoop` on Windows and do not add
  the marker setting to production `Settings`.

- [ ] **Step 2: Write the failing native recovery test**

  In `tests/integration/test_worker_recovery.py`, submit one support-report run
  and relay its stable Workflow start. Launch the helper with
  `asyncio.create_subprocess_exec(sys.executable, helper_path, ...)`, wait with a
  bounded deadline for the second-decision marker, terminate and reap that
  process, then launch a normal replacement helper on the same task queue.
  Always terminate and reap every child in `finally`, including assertion and
  timeout paths.

  Await the original Workflow handle and assert:

  - its result and PostgreSQL run projection reach `SUCCEEDED` under the
    original run and Workflow IDs;
  - Temporal history contains an Activity timeout followed by a successful
    retry;
  - the second `decide_action` step keeps one stable step ID with attempt `1`
    `FAILED`/`RETRYABLE` and attempt `2` `SUCCEEDED`;
  - the earlier `read_tickets` step has one attempt, proving completed progress
    was replayed rather than repeated;
  - exactly one artifact row exists and its file bytes match the recorded
    SHA-256 and size.

- [ ] **Step 3: Run the recovery test and confirm failure**

  ```powershell
  .\.venv\Scripts\python.exe -m pytest tests/integration/test_worker_recovery.py -v
  ```

  Expected: FAIL before the helper/reconciliation behavior is complete; no
  child `recovery_worker.py` process remains after pytest exits.

- [ ] **Step 4: Complete the minimum test harness behavior**

  Add bounded polling helpers for the marker, run projection, and process exit;
  include captured child stderr in timeout diagnostics without printing
  environment values. Do not change production Workflow behavior to make the
  test pass.

- [ ] **Step 5: Run the recovery and existing Workflow slices**

  ```powershell
  .\.venv\Scripts\python.exe -m pytest tests/integration/test_worker_recovery.py tests/integration/test_workflow.py tests/integration/test_end_to_end.py -v
  ```

  Expected: PASS. The recovery case includes one killed process, one Activity
  timeout, one replacement Worker, one successful run, and no orphan helper
  process.

- [ ] **Step 6: Commit the executable recovery proof**

  ```powershell
  git add tests/helpers/recovery_worker.py tests/integration/test_worker_recovery.py
  git commit -m "test: prove Temporal worker recovery"
  ```

### Task 3: Verify and record the Milestone 2 checkpoint

**Files:**

- Modify: `README.md`
- Create: `docs/learning/05_MILESTONE_2_RECOVERY.md`
- Modify: `PROJECT_PLAN.md`
- Modify: `TEST_CHECKLIST.md`
- Replace: `HANDOVER.md`

**Interfaces:**

- Consumes: the Task 2 recovery proof and the existing Milestone 1 restart
  persistence evidence.
- Produces: repeatable PowerShell verification instructions, a precise statement
  of what recovery is and is not proven, completed Milestone 2 checklist state,
  and the exact Milestone 3 approval boundary.

- [ ] **Step 1: Run the full automated suite**

  ```powershell
  .\.venv\Scripts\python.exe -m pytest -v
  ```

  Expected: PASS, including the real worker-termination recovery test.

- [ ] **Step 2: Run environment and repository checks**

  ```powershell
  .\.venv\Scripts\python.exe -m pip check
  docker compose config --quiet
  git diff --check
  ```

  Expected: no broken requirements and zero exits from Compose validation and
  the diff check.

- [ ] **Step 3: Re-run the native recovery demonstration**

  ```powershell
  .\.venv\Scripts\python.exe -m pytest tests/integration/test_worker_recovery.py -vv
  ```

  Expected: PASS against the real loopback Temporal/PostgreSQL services. Record
  the run ID, Workflow ID, timeout/retry evidence, attempt states, artifact
  checksum, elapsed time, and child-process cleanup result in `HANDOVER.md`.

- [ ] **Step 4: Document the verified behavior and boundary**

  Explain that Workflow replay reconstructs state from Temporal history, the
  Start-To-Close timeout detects a dead Activity Worker, and PostgreSQL is a
  projection rather than the retry scheduler. State plainly that this milestone
  does not prove approval durability or exactly-one receiver delivery. Update
  `PROJECT_PLAN.md` and `TEST_CHECKLIST.md` only for observations backed by the
  executed checks.

- [ ] **Step 5: Commit the verified checkpoint**

  ```powershell
  git add README.md docs/learning/05_MILESTONE_2_RECOVERY.md PROJECT_PLAN.md TEST_CHECKLIST.md HANDOVER.md
  git commit -m "docs: record verified Milestone 2 recovery"
  ```

- [ ] **Step 6: Verify the committed tree**

  ```powershell
  git status --short --branch
  git log -4 --oneline
  ```

  Expected: the Milestone 2 branch is clean; the three coherent Milestone 2
  commits follow the approved plan commit. Stop for user review before planning
  or implementing Milestone 3.

## Self-Review Record

- **Spec coverage:** Controlled multi-step execution and attempt/projection
  records already exist; Tasks 1–2 make their crash semantics observable and
  prove Worker replacement. Ordinary service persistence is already verified
  by Milestone 1 and is re-stated, not reimplemented.
- **Step scan:** Every implementation step names an exact file, interface,
  assertion, command, and expected outcome without prescribing routine bodies.
- **Type consistency:** Task 1 preserves the current repository signature;
  Task 2 consumes the current `Settings`, Worker, relay, and domain interfaces.
- **Review focus:** All five listed failure classes are pinned by Task 1 or the
  real Task 2 recovery test.
- **Proportion:** The plan adds one narrow persistence invariant and one
  end-to-end failure proof; it intentionally avoids schema, dependency,
  Workflow, API, and service expansion.
