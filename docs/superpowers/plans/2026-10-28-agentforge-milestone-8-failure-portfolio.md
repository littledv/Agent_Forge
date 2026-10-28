# AgentForge Milestone 8 Failure Portfolio Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build one deterministic, credential-free command that proves the continuous 20-step recovery story and the required failure matrix with authoritative, machine-checked invariants.

**Architecture:** Repair the disposable integration-database bootstrap first so repeated sessions are safe and automatic. Add one composite native integration proof that reuses the existing reviewer-block and post-receiver-commit Worker helpers, then expose it and the existing focused failure cases through a fixed pytest-node manifest. Finish with evidence, architecture, operations, and learning documentation plus an independent whole-change review.

**Tech Stack:** Python 3.13.17, pytest 9.1.1, FastAPI/Uvicorn, httpx, SQLAlchemy 2.1.6, Alembic 1.20.1, PostgreSQL 16.15, Temporal Python SDK 1.33.0, Temporal Server 1.32.1, Windows PowerShell.

**Spec:** `docs/superpowers/specs/2026-10-28-agentforge-milestone-8-failure-portfolio-design.md`

## Global Constraints

- Implement Milestone 8 only; do not begin optional Milestone 9 deployment work.
- Use `mock-v1`, the version-2 bounded role graph, real loopback PostgreSQL/Temporal/inbox boundaries, and native Worker subprocesses.
- Add no dependency, migration, service, scheduler, API, dashboard feature, container image, remote, deployment, or non-loopback listener.
- Keep all failure injection in test-only helpers; do not modify deterministic Workflow behavior to make the portfolio pass.
- PostgreSQL, Temporal history, artifact metadata/bytes, and inbox PostgreSQL are authoritative; logs are bounded diagnostics only.
- Reject any destructive test reset unless the parsed database uses `postgresql+psycopg`, a loopback host, and a database name ending in `_test`.
- Never reset `agentforge`, `agentforge_inbox`, or any Temporal database. Never print configured database URLs or environment values in refusal messages.
- Bound every readiness, marker, Workflow, HTTP, shutdown, and process-reap wait. Clean up in `finally` and verify ephemeral listeners are released.
- Keep synthetic ticket/report probes out of retained diagnostics, PostgreSQL projections, and decoded Temporal history.
- The required portfolio command is `.\.venv\Scripts\python.exe tests\helpers\run_failure_portfolio.py`; it returns pytest's exit code unchanged and performs no hidden retry.

## Review Focus

- A stale prior integration row must be cleared only after the configured URL is proven local and test-only; Task 1 tests refusal before any Alembic or SQL operation.
- Worker markers can be stale or belong to the wrong process; Task 3 validates PID/token/time identity for review and tracked-process launch time for post-commit delivery before termination.
- Starting replacement Workers can create task-queue races; Task 3 proves the same Workflow and stable logical steps/operation ID rather than inferring recovery from process output.
- API reconstruction during pending approval can accidentally use cached client state; Task 3 uses a new Uvicorn instance, port, and SDK client and rereads the exact durable approval.
- A portfolio manifest can silently lose coverage after test renames; Task 2 freezes exact node IDs and Task 4 requires collect-only resolution plus two consecutive full portfolio passes.

---

### Task 1: Make disposable integration database setup safe and repeatable

**Files:**
- Create: `tests/helpers/database_reset.py`
- Create: `tests/unit/test_database_reset.py`
- Modify: `tests/integration/conftest.py`
- Test: `tests/integration/test_migrations.py`

**Interfaces:**
- Consumes: the existing application and inbox Alembic `Config` objects and their configured test URLs.
- Produces: `DatabaseKind = Literal["application", "inbox"]`, `require_disposable_database_url(database_url: str) -> sqlalchemy.engine.URL`, and `reset_disposable_database(config: Config, database_url: str, *, kind: DatabaseKind) -> None` for session-fixture use only.

- [ ] **Step 1: Write URL-safety unit tests**

  Create `test_require_disposable_database_url_accepts_only_local_psycopg_test_database()` with the two default test URLs as accepted inputs. Parameterize rejection for the production names `agentforge` and `agentforge_inbox`, a non-`_test` name, non-loopback hosts, a non-PostgreSQL scheme, missing database name, and malformed input. Assert sanitized errors contain neither password nor full URL.

- [ ] **Step 2: Write reset-order and refusal-before-side-effect tests**

  Add `test_reset_disposable_database_runs_upgrade_cleanup_downgrade_upgrade_in_order()` for both kinds using monkeypatched Alembic commands and engine transaction calls. Add `test_reset_disposable_database_refuses_before_alembic_or_engine_use()` and assert no mocked call occurs for an unsafe URL. The cleanup SQL is fixed by `kind`; callers cannot provide identifiers.

- [ ] **Step 3: Run the safety tests and observe RED**

  ```powershell
  .\.venv\Scripts\python.exe -m pytest tests/unit/test_database_reset.py -q
  ```

  Expected: FAIL during import because `tests/helpers/database_reset.py` does not exist.

- [ ] **Step 4: Implement the test-only reset helper**

  Parse with SQLAlchemy `make_url`. Require driver `postgresql+psycopg`, host `127.0.0.1` or `localhost`, and a database name ending in `_test`. After validation, run `upgrade head`, truncate the fixed application runtime tables with `CASCADE` or the inbox `deliveries` table, then run `downgrade base` and `upgrade head`. Dispose the synchronous engine in `finally`. Error text names only the rejected safety rule.

- [ ] **Step 5: Route both session migration fixtures through the helper**

  Replace the direct first `downgrade`/`upgrade` calls in `migrated_database` and `migrated_inbox_database` with the appropriate `reset_disposable_database(...)` call. Keep per-test async truncation unchanged so tests remain isolated within a session.

- [ ] **Step 6: Prove migrations and consecutive sessions**

  Run twice without manual SQL between commands:

  ```powershell
  .\.venv\Scripts\python.exe -m pytest tests/integration/test_migrations.py tests/integration/test_inbox_migrations.py -q
  .\.venv\Scripts\python.exe -m pytest tests/integration/test_migrations.py tests/integration/test_inbox_migrations.py -q
  ```

  Expected: both runs PASS; the second session does not fail while removing seeded rows.

- [ ] **Step 7: Commit repeatable test bootstrap**

  ```powershell
  git add tests/helpers/database_reset.py tests/unit/test_database_reset.py tests/integration/conftest.py tests/integration/test_migrations.py
  git commit -m "test: make integration database reset repeatable"
  ```

### Task 2: Freeze the one-command portfolio manifest

**Files:**
- Create: `tests/helpers/run_failure_portfolio.py`
- Create: `tests/unit/test_failure_portfolio_runner.py`

**Interfaces:**
- Consumes: pytest and the exact node IDs owned by Tasks 3–4.
- Produces: `PORTFOLIO_NODE_IDS: tuple[str, ...]`, `build_pytest_args(*, collect_only: bool = False) -> list[str]`, and `main(argv: Sequence[str] | None = None) -> int` with only a public `--collect-only` option.

- [ ] **Step 1: Write the failing manifest contract**

  Assert `PORTFOLIO_NODE_IDS` is exactly:

  ```python
  (
      "tests/integration/test_failure_portfolio.py::test_twenty_step_recovery_portfolio",
      "tests/integration/test_workflow.py::test_rejection_or_expiry_denies_delivery[rejected]",
      "tests/integration/test_workflow.py::test_retry_exhaustion_projects_uncertain_delivery",
      "tests/integration/test_multi_agent_workflow.py::test_two_revisions_are_allowed_and_third_rejection_exhausts_budget",
      "tests/integration/test_inbox_api.py::test_concurrent_receiver_attempts_commit_one_delivery",
  )
  ```

  Assert the composite node owns duplicate/changed-body evidence rather than adding a second duplicate-submission node.

- [ ] **Step 2: Write exit-code and argument tests**

  Monkeypatch `pytest.main` and assert normal mode receives the fixed nodes plus `-q`, collect mode additionally receives `--collect-only`, and the runner returns the exact integer from pytest. Assert unknown CLI arguments fail through `argparse` before pytest is called.

- [ ] **Step 3: Run the runner tests and observe RED**

  ```powershell
  .\.venv\Scripts\python.exe -m pytest tests/unit/test_failure_portfolio_runner.py -q
  ```

  Expected: FAIL during import because the runner does not exist.

- [ ] **Step 4: Implement the minimal runner**

  Use `argparse`, the immutable node tuple, and one `pytest.main(...)` call. Do not start Docker, clean databases, retry failures, parse output, or rewrite the exit code; those concerns remain visible to the operator and fixtures.

- [ ] **Step 5: Run the runner tests GREEN**

  Run the command from Step 3.

  Expected: PASS without requiring PostgreSQL or Temporal.

- [ ] **Step 6: Commit the fixed manifest**

  ```powershell
  git add tests/helpers/run_failure_portfolio.py tests/unit/test_failure_portfolio_runner.py
  git commit -m "test: define failure portfolio manifest"
  ```

### Task 3: Build the continuous 20-step recovery proof

**Files:**
- Create: `tests/integration/test_failure_portfolio.py`
- Consume unchanged: `tests/helpers/multi_agent_recovery_worker.py`
- Consume unchanged: `tests/helpers/delivery_worker.py`

**Interfaces:**
- Consumes: `create_app`, `create_inbox_app`, `AgentForgeClient`, `relay_once`, `relay_signals_once`, the two existing Worker helper entry points, real `agentforge-test` Temporal, and the application/inbox test engines.
- Produces: `test_twenty_step_recovery_portfolio(...)` and local bounded helpers for ephemeral Uvicorn serving, tracked process launch/termination, marker validation, durable-state waits, decoded-history privacy checks, and listener-release checks.

- [ ] **Step 1: Add the 20-checkpoint test skeleton**

  Create the integration test with `completed_steps: list[int]`, a local `complete(step: int)` helper that requires the next exact ordinal, and final assertion `completed_steps == list(range(1, 21))`. Use the exact numbered sequence from the approved design. Initially stop with an explicit failing assertion after safe setup and cleanup so the unfinished proof cannot pass accidentally.

- [ ] **Step 2: Run the composite node and observe RED**

  ```powershell
  .\.venv\Scripts\python.exe -m pytest tests/integration/test_failure_portfolio.py::test_twenty_step_recovery_portfolio -vv
  ```

  Expected: FAIL at the explicit incomplete-proof assertion; all started servers/processes are nevertheless stopped and reaped.

- [ ] **Step 3: Implement steps 1–10 through reviewer recovery**

  Use synthetic private probes, ephemeral inbox/API A servers, SDK client A, stable submission key, exact duplicate and changed-goal conflict, one start relay, the identity-token reviewer marker, Worker B startup, identity-matched Worker A termination, and the original Workflow reaching `WAITING_APPROVAL`. Every wait has a named deadline and includes only a redacted diagnostic suffix on failure.

- [ ] **Step 4: Implement steps 11–18 through ambiguous delivery recovery**

  Retain the exact approval metadata, close client/API A, start API B on a different ephemeral port, construct client B, and reread the same approval. Reap idle Worker B, start tracked delivery-blocking Worker C, approve the retained hash, relay one signal, validate the post-commit marker appeared after Worker C launch, terminate/reap Worker C, then start ordinary Worker D.

- [ ] **Step 5: Implement terminal and authoritative assertions for steps 19–20**

  Await the original Workflow and `SUCCEEDED` projection. Retrieve through the SDK and verify artifact bytes. Query both databases and history to assert one run/Workflow/approval/delivery/inbox receipt/artifact, exact stable IDs, completed research attempted once, review attempts `[1, 2]`, delivery attempts `[1, 2]`, bounded role order/count, retry timeout evidence, and no synthetic ticket/report probes in projections, decoded history, or diagnostics. In `finally`, reap all Workers and stop both server generations; after contexts close, assert their ports refuse connection.

- [ ] **Step 6: Run the composite proof GREEN and check cleanup**

  Run the command from Step 2, then:

  ```powershell
  Get-Process python -ErrorAction SilentlyContinue |
    Where-Object { $_.Path -eq (Resolve-Path .\.venv\Scripts\python.exe).Path }
  Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue |
    Where-Object { $_.LocalPort -in @(8000, 8001) }
  ```

  Expected: the test passes with all 20 checkpoints; no helper Worker remains and no portfolio listener remains on 8000/8001. Do not terminate unrelated Python processes during this read-only check.

- [ ] **Step 7: Run adjacent recovery slices**

  ```powershell
  .\.venv\Scripts\python.exe -m pytest tests/integration/test_multi_agent_worker_recovery.py tests/integration/test_delivery_recovery.py tests/integration/test_end_to_end.py -q
  ```

  Expected: PASS with existing focused semantics unchanged.

- [ ] **Step 8: Commit the continuous proof**

  ```powershell
  git add tests/integration/test_failure_portfolio.py
  git commit -m "test: prove the continuous recovery portfolio"
  ```

### Task 4: Prove the fixed failure matrix twice

**Files:**
- Modify if collection requires correction: `tests/helpers/run_failure_portfolio.py`
- Modify only for missing authoritative assertions: the existing selected integration test that owns that behavior
- Test: `tests/unit/test_failure_portfolio_runner.py`

**Interfaces:**
- Consumes: Tasks 1–3 and the existing selected rejection, retry-exhaustion, budget-exhaustion, and receiver-concurrency tests.
- Produces: a fixed, collectable, repeatable one-command portfolio whose every node asserts durable state.

- [ ] **Step 1: Resolve the complete manifest without execution**

  ```powershell
  .\.venv\Scripts\python.exe tests\helpers\run_failure_portfolio.py --collect-only
  ```

  Expected: exactly five nodes collect. If a parameterized node ID differs, update the manifest and its unit expectation together; do not broaden selection with wildcards.

- [ ] **Step 2: Audit each selected test's authority assertions**

  Confirm the selected cases directly assert their expected PostgreSQL, Temporal, inbox, or artifact state. If one relies only on an exception or log line, first add a focused failing assertion in that owning test, run it RED, implement only the missing evidence query, and run it GREEN.

- [ ] **Step 3: Run the portfolio command once**

  ```powershell
  .\.venv\Scripts\python.exe tests\helpers\run_failure_portfolio.py
  ```

  Expected: all five selected nodes pass with no manual SQL or process cleanup.

- [ ] **Step 4: Run the identical command again immediately**

  Run the Step 3 command again without any intervening cleanup.

  Expected: all five nodes pass again, proving the session bootstrap removed stale disposable rows safely and no prior process/listener state is required.

- [ ] **Step 5: Re-run runner unit tests and inspect state**

  ```powershell
  .\.venv\Scripts\python.exe -m pytest tests/unit/test_failure_portfolio_runner.py tests/unit/test_database_reset.py -q
  git diff --check
  ```

  Expected: PASS and clean whitespace.

- [ ] **Step 6: Commit any matrix evidence corrections**

  If Step 2 required changes, commit only those test/runner changes:

  ```powershell
  git add tests/helpers/run_failure_portfolio.py tests/unit/test_failure_portfolio_runner.py tests/integration
  git commit -m "test: complete failure portfolio evidence matrix"
  ```

  If no file changed, record the verified no-op in the execution ledger and do not create an empty commit.

### Task 5: Publish architecture, operations, and proof boundaries

**Files:**
- Create: `docs/FAILURE_PORTFOLIO.md`
- Create: `docs/PORTFOLIO_ARCHITECTURE.md`
- Create: `docs/OPERATIONS.md`
- Create: `docs/learning/11_MILESTONE_8_FAILURE_PORTFOLIO.md`
- Modify: `docs/learning/README.md`
- Modify: `README.md`
- Modify: `ARCHITECTURE.md`
- Modify: `FLOW.md`
- Modify: `TEST_CHECKLIST.md`

**Interfaces:**
- Consumes: the exact commands and observed evidence from Tasks 1–4.
- Produces: an operator-facing runbook, portfolio architecture narrative, evidence/non-claim matrix, plain-language learning chapter, and discoverable links.

- [ ] **Step 1: Write the failure portfolio evidence document**

  Document the exact one-command invocation, prerequisites, expected node count, numbered 20-step sequence, scenario-to-authority table, representative durable invariants, two-consecutive-run requirement, troubleshooting boundaries, and the distinction between assertions and diagnostic logs.

- [ ] **Step 2: Write the portfolio architecture document**

  Add a compact Mermaid component/authority diagram and failure sequence. Explain PostgreSQL versus Temporal versus inbox ownership, stable IDs, replay, retry, receiver deduplication, bounded graph roles, and terminal classifications. State explicitly that arbitrary external exactly-once delivery, multi-host disaster recovery, production security, load capacity, and model quality are not proven.

- [ ] **Step 3: Write the Windows operations runbook**

  Document pinned runtime checks, Docker/service health, migrations, the safe portfolio command, ordinary stop/restart, where durable and generated data lives, bounded diagnostics, port/process cleanup checks, and clearly separated destructive reset instructions. Do not instruct manual test-database truncation; Task 1 owns safe automatic reset.

- [ ] **Step 4: Write the learning chapter and update maps**

  Explain in plain language why evidence must be read from multiple authorities and why one receiver row—not a success log—proves deduplication. Link the four new documents from README and the learning index; add concise portfolio references to Architecture and Flow.

- [ ] **Step 5: Add and verify Milestone 8 checklist items**

  Add unchecked items for the 20 steps, repeatable safe reset, rejection, retry exhaustion, budget exhaustion, duplicate submission, concurrent side effects, authority assertions, cleanup, privacy, and proven/non-proven documentation. Check only items backed by executed Task 4 commands.

- [ ] **Step 6: Verify documentation mechanics**

  ```powershell
  rg -n "run_failure_portfolio|not proven|NEEDS_ATTENTION|BUDGET_EXHAUSTED" README.md docs ARCHITECTURE.md FLOW.md TEST_CHECKLIST.md
  rg -n "C:[/\\]Users|\.codex[/\\]worktrees|E:[/\\]Dev project" README.md docs ARCHITECTURE.md FLOW.md TEST_CHECKLIST.md
  git diff --check
  ```

  Expected: required topics and links are present; no absolute worktree path is introduced; whitespace is clean.

- [ ] **Step 7: Commit portfolio documentation**

  ```powershell
  git add README.md ARCHITECTURE.md FLOW.md TEST_CHECKLIST.md docs
  git commit -m "docs: publish failure portfolio evidence"
  ```

### Task 6: Fully verify, independently review, and checkpoint Milestone 8

**Files:**
- Modify: `PROJECT_PLAN.md`
- Replace: `HANDOVER.md`
- Modify only for accepted findings: files owned by Tasks 1–5

**Interfaces:**
- Consumes: the committed repeatability fix, composite proof, fixed matrix, and published documentation.
- Produces: final native verification evidence, independent review resolution, coherent commits, and the Milestone 8 approval checkpoint without beginning Milestone 9.

- [ ] **Step 1: Run the required portfolio twice on the final implementation**

  ```powershell
  .\.venv\Scripts\python.exe tests\helpers\run_failure_portfolio.py
  .\.venv\Scripts\python.exe tests\helpers\run_failure_portfolio.py
  ```

  Expected: both runs pass consecutively without manual cleanup.

- [ ] **Step 2: Run the complete backend and migration matrix**

  ```powershell
  .\.venv\Scripts\python.exe -m pytest -q
  .\.venv\Scripts\python.exe -m pip check
  .\.venv\Scripts\alembic.exe upgrade head
  .\.venv\Scripts\alembic.exe current
  .\.venv\Scripts\alembic.exe -c inbox_alembic.ini upgrade head
  .\.venv\Scripts\alembic.exe -c inbox_alembic.ini current
  docker compose config --quiet
  git diff --check
  ```

  Expected: all tests pass; no broken requirements; application migration is `0005_support_report_v2 (head)`; inbox migration is at its head; Compose and whitespace checks exit zero.

- [ ] **Step 3: Run deterministic dashboard regression checks**

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

  Expected: every command passes using mock mode; no Ollama process is required.

- [ ] **Step 4: Run privacy, boundary, process, and port checks**

  Search tracked changes and retained diagnostics for high-signal credentials, synthetic private probes, report bodies, absolute worktree paths, and production failure-injection imports. Verify no portfolio helper process remains, ports 8000/8001 are free, and configured Compose ports remain bound only to `127.0.0.1`.

- [ ] **Step 5: Request an independent whole-change review**

  Invoke `superpowers:requesting-code-review` against the approved design and this plan. Require Critical/Important/Minor ranking and explicit review of destructive-reset safety, marker/process identity, false positive recovery claims, direct authority assertions, repeatability, privacy, cleanup, and documentation non-claims. Fix accepted Critical and Important findings with focused RED→GREEN evidence and rerun affected plus complete verification once.

- [ ] **Step 6: Update durable checkpoint records**

  Mark the five Milestone 8 roadmap tasks complete only after their evidence passes. Replace `HANDOVER.md` with exact commands/results, failed attempts and diagnoses, review findings/remediation, changed files, known caveats, coherent commits, and the exact next action. State that Milestone 9 remains optional, blocked, and unstarted.

- [ ] **Step 7: Commit the verified Milestone 8 checkpoint**

  ```powershell
  git add PROJECT_PLAN.md HANDOVER.md TEST_CHECKLIST.md README.md docs ARCHITECTURE.md FLOW.md
  git commit -m "docs: record milestone 8 verification checkpoint"
  ```

- [ ] **Step 8: Ask for checkpoint approval**

  Present the five roadmap tasks, the 20-step and matrix results, repeatability result, full verification totals, independent-review outcome, known caveats, and commit list. Do not begin optional Milestone 9 without a separate explicit request.

## Self-Review Record

- **Spec coverage:** Task 1 owns safe consecutive database sessions; Task 2 freezes the command surface; Task 3 owns the continuous 20 steps; Task 4 owns every required matrix scenario and double-run evidence; Task 5 owns architecture/operations/proven/non-proven documentation; Task 6 owns full verification, review, and checkpointing.
- **Step scan:** Every step creates one named test, helper, proof slice, document, command result, or commit. Algorithm bodies are left to the implementer except fixed safety predicates, manifest node IDs, and the approved 20-step sequence.
- **Type consistency:** Task 1's reset signatures are consumed unchanged by `conftest.py`; Task 2's manifest points to Task 3's exact node; Tasks 4 and 6 invoke the same runner path and CLI option.
- **Review focus:** Unsafe database targets, stale/wrong process markers, task-queue races, cached approval state, and manifest drift each have an owning test or explicit Task 3/4 assertion.
- **Proportion:** Six tasks separate safety, command surface, composite orchestration, matrix proof, documentation, and checkpoint review. No task adds a production subsystem or duplicates existing failure behavior.
