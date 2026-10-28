# AgentForge Milestone 8 Evaluation and Failure Portfolio Design

Date: 2026-10-28
Status: Approved on 2026-10-28

## Intent

Milestone 8 turns AgentForge's existing failure tests into one repeatable,
portfolio-quality proof. A reviewer should be able to run one credential-free
command and see machine-checked evidence that one durable support-report run
survives both an ordinary Worker loss and the ambiguous post-receiver-commit
window, while a focused matrix proves the other promised failure outcomes.

This milestone evaluates the runtime already built. It does not add a new
scheduler, change production Workflow behavior, or make logs the source of
truth.

## Goals

- Automate one continuous, deterministic 20-step recovery demonstration using
  the existing mock model, real loopback PostgreSQL, real Temporal, the real
  inbox boundary, and native Worker subprocesses.
- Prove the run survives a role Worker loss, an API service reconstruction
  while approval is pending, and a delivery Worker loss after the receiver
  commits but before Temporal receives the Activity result.
- Make one short Windows PowerShell command execute the composite demonstration
  plus rejection, retry-exhaustion, budget-exhaustion, duplicate-submission,
  and concurrent-side-effect cases.
- Assert durable PostgreSQL, Temporal-history, artifact, and inbox invariants;
  use child-process output only for bounded failure diagnostics.
- Publish a clear evidence matrix, architecture narrative, operations runbook,
  and explicit list of properties that are not proven.

## Non-goals

- Real-model quality evaluation, prompt benchmarking, semantic report scoring,
  load testing, fuzzing, chaos infrastructure, performance certification, or a
  general evaluation framework.
- A new API, dashboard page, database migration, service, dependency, container
  image, authentication scheme, remote, deployment, or non-loopback listener.
- Claiming exactly-once delivery for arbitrary external systems. The proof is
  limited to the simulated inbox's durable operation-ID deduplication.
- Replacing the existing focused recovery and invariant tests. The portfolio
  composes and selects them; the focused tests remain useful independently.
- Making Ollama part of the required command. The already verified local-Qwen
  journey remains optional evidence because hardware and model runtime state
  would weaken determinism.

## Constraints

- Use Python 3.13.17, the existing pinned dependencies, PostgreSQL 16.15,
  Temporal Server 1.32.1, and the `agentforge-test` namespace.
- Use the deterministic `mock-v1` profile and version-2 bounded role graph.
- Publish only ephemeral or existing configured loopback ports. Do not use
  environment proxy settings for inbox or SDK traffic.
- Keep Workflow code deterministic. All failure injection remains in test-only
  helpers and native child processes.
- PostgreSQL remains the application-record authority; Temporal remains the
  execution-history authority; the inbox database remains the receiver-record
  authority.
- Never put ticket, note, draft, feedback, prompt, raw response, or report
  bodies in retained logs, evidence summaries, PostgreSQL projections, or
  decoded Temporal history.
- Bound every readiness wait, marker wait, Workflow wait, process shutdown, and
  HTTP call. Always terminate and reap every child process in `finally`.
- The runner may clear only database names ending in `_test`, after parsing and
  validating the configured URLs. It must refuse an application database or an
  unprovably disposable target before issuing any cleanup statement.
- Add no dependencies. Reuse pytest, FastAPI/Uvicorn, httpx, SQLAlchemy, and the
  existing Worker helpers.

## Approaches considered

### Selected: one composite run plus a focused failure matrix

Add one native integration test in which a single version-2 run crosses the
two important Worker-crash windows and an API reconstruction. A thin test-only
runner invokes that composite test and the existing focused cases for
rejection, retry exhaustion, budget exhaustion, duplicate submission, and
concurrent receiver attempts.

This gives the strongest narrative proof without moving failure injection into
production code. It costs one substantial integration test, but most runtime
behavior and helpers already exist.

### Rejected: meta-suite only

A runner could merely invoke the existing independent tests. That is cheap and
still useful, but it would not prove that one durable run crosses the complete
recovery story or make the 20-step demonstration concrete.

### Rejected: full PowerShell and browser orchestration

The existing dashboard restart harness proves the browser/application restart
journey. Extending it with both Worker-crash windows would be visually
impressive but slower and more fragile, and browser state would obscure the
database, history, and receiver invariants that Milestone 8 is meant to teach.

## Evaluation architecture

The required command is a test-only Python entry point, run from Windows
PowerShell:

```powershell
.\.venv\Scripts\python.exe tests\helpers\run_failure_portfolio.py
```

The entry point owns a fixed tuple of pytest node IDs and calls pytest once. It
does not interpret console text as evidence. Each selected test exits only
after its database, receiver, history, artifact, and process invariants pass.
The runner returns pytest's exit code unchanged and adds no hidden retry.

The selected node set contains:

1. the new continuous 20-step recovery demonstration;
2. exact duplicate submission and changed-body conflict, also asserted inside
   the composite run;
3. rejection preventing delivery;
4. delivery retry exhaustion producing `NEEDS_ATTENTION`/`UNCERTAIN`;
5. graph revision exhaustion producing `BUDGET_EXHAUSTED` before publication;
6. concurrent identical receiver attempts committing one inbox row.

The runner's manifest is unit-tested so a renamed or removed test cannot
silently reduce the portfolio. `--collect-only` must resolve every node before
the native demonstration is considered runnable.

## Continuous 20-step recovery demonstration

The composite test uses synthetic private-content sentinels and fresh isolated
database fixtures. It performs these observable steps in order:

1. Create bounded version-2 settings, synthetic tickets, artifact storage, and
   isolated application/inbox database fixtures.
2. Start the simulated inbox on an ephemeral `127.0.0.1` port and wait for
   readiness.
3. Start Worker A with the existing reviewer-block marker and a unique process
   identity token.
4. Start API instance A on an ephemeral `127.0.0.1` port and create SDK client
   A.
5. Submit a stable request and persist its run and Workflow IDs.
6. Repeat the exact submission and assert it returns the same run ID; reuse the
   key with a changed goal and assert conflict.
7. Relay the start outbox and assert exactly one Temporal Workflow starts.
8. Wait until Worker A reaches `review_report_r0`, proving the research shards
   and writer already completed.
9. Start ordinary Worker B on the same task queue, terminate identity-matched
   Worker A, and reap it.
10. Let Temporal time out and retry the same review Activity on Worker B; wait
    for the original run to reach `WAITING_APPROVAL`.
11. Fetch and retain the exact pending approval ID, operation ID, action hash,
    and artifact metadata.
12. Close SDK client A and stop API instance A while the approval remains
    durably pending.
13. Start API instance B on a new ephemeral loopback port, construct SDK client
    B, and assert the same run and approval are observable unchanged.
14. Stop and reap Worker B while no Activity is running.
15. Start Worker C with the existing post-inbox-commit delivery marker.
16. Approve the exact retained action through SDK client B and relay exactly one
    approval signal.
17. Wait until the inbox commits the operation and tracked Worker C writes its
    post-commit marker, verify that the marker appeared after that process was
    launched, then terminate and reap Worker C before Activity
    acknowledgement.
18. Start ordinary Worker D on the same task queue and let Temporal retry the
    delivery Activity with the unchanged operation ID.
19. Wait for the original Workflow and PostgreSQL run projection to reach
    `SUCCEEDED`, then retrieve and SDK-verify the artifact.
20. Assert the complete evidence set and clean up: one run, one Workflow, one
    approval, one delivery record, one inbox receipt, one artifact, exact
    checksum/size, one attempt for completed research roles, two review
    attempts, two delivery attempts, bounded role order/count, no private body
    in durable evidence or diagnostics, all child processes reaped, and all
    ephemeral listeners released.

Each step is represented by an assertion or a bounded wait whose result feeds
an assertion. The documentation may number and explain the steps, but their
pass/fail truth comes from the test.

## Test-only process and server boundaries

The existing `multi_agent_recovery_worker.py` remains the Worker A helper. The
existing `delivery_worker.py` remains the Worker C helper. Their ordinary modes
can run Workers B and D. The composite test must not weaken either helper's
identity/marker validation.

The inbox and both API instances run through an ephemeral-loopback Uvicorn
context helper in the pytest event loop. Stopping and reconstructing the API
proves there is no client/API-session ownership of durable approval state,
without claiming an operating-system or Docker restart. The already verified
dashboard `-RestartProof` remains the full native application-process restart
evidence and is linked as complementary proof, not rerun by the required
portfolio command.

## Evidence and assertions

The composite proof reads each authority directly:

- PostgreSQL: run, ordered steps/attempts, approval, signal intent, artifact,
  and delivery records.
- Temporal: one Workflow handle, timeout/retry attempts, unchanged scheduled
  Activity identity, and terminal result.
- Inbox PostgreSQL: one receipt for the stable operation ID.
- Artifact storage plus SDK: recorded relative path, UTF-8 byte size, SHA-256,
  and verified content retrieval.
- Operating system: identity-matched child PIDs exit and the Uvicorn contexts
  release their ephemeral listeners.

Logs and captured stderr are diagnostics only. A test may include a bounded,
redacted suffix after a process exits unexpectedly, but successful claims must
not depend on matching log text. Diagnostics are scanned for the synthetic
ticket/report probes before the test returns.

## Repeatable disposable-database setup

The current integration session fixture downgrades migrations before its
per-test truncation runs. A prior successful lifecycle can therefore leave one
`runs` row that prevents the next session from deleting seeded agent versions.
Milestone 8 closes this test-harness gap rather than requiring a manual `psql`
repair before a portfolio demonstration.

Before any migration reset, a shared test-only preflight parses the configured
application and inbox test URLs and requires each database name to end in
`_test`. It upgrades that disposable database to the known head, truncates only
the runtime-owned test tables using the established dependency-safe order,
then performs the existing downgrade-to-base and upgrade-to-head migration
proof. A non-test database name fails before connecting for destructive work.

Focused tests prove both refusal and consecutive clean sessions. This cleanup
is never exposed through production settings, APIs, or commands and never
targets `agentforge`, `agentforge_inbox`, or Temporal's databases.

## Failure matrix

The portfolio documentation maps each promised property to a selected test and
the authoritative assertion it makes:

| Scenario | Expected durable outcome | Primary authority |
|---|---|---|
| Exact duplicate submission | Same run and Workflow; one run row | PostgreSQL |
| Changed body with same key | HTTP 409; original run unchanged | API + PostgreSQL |
| Rejection | `DENIED`; no delivery | PostgreSQL + inbox DB |
| Delivery retry exhaustion | `NEEDS_ATTENTION`; delivery step `UNCERTAIN` | Temporal + PostgreSQL |
| Revision budget exhaustion | `BUDGET_EXHAUSTED`; no publication/approval/delivery | Temporal + PostgreSQL |
| Concurrent identical side effects | One receiver row and one stable receipt | Inbox DB |
| Worker loss during review | Same Workflow; completed research not repeated | Temporal + PostgreSQL |
| Crash after receiver commit | Two delivery attempts; one receiver row | Temporal + both databases |

## Documentation deliverables

- `docs/FAILURE_PORTFOLIO.md`: the 20-step narrative, command, evidence matrix,
  expected runtime prerequisites, troubleshooting boundaries, and exact
  proven/not-proven claims.
- `docs/PORTFOLIO_ARCHITECTURE.md`: a concise component/authority diagram,
  retry and idempotency boundaries, deterministic graph bounds, and where
  failures become `FAILED`, `BUDGET_EXHAUSTED`, or `NEEDS_ATTENTION`.
- `docs/OPERATIONS.md`: Windows setup validation, start/stop/health commands,
  safe portfolio execution, retained data, ordinary restart, diagnostic
  collection, and destructive-reset warnings.
- `docs/learning/11_MILESTONE_8_FAILURE_PORTFOLIO.md`: a plain-language lesson
  explaining why recovery claims need assertions across multiple authorities.
- README, learning index, Flow, Test Checklist, Project Plan, and Handover links
  and checkpoint records.

Documentation distinguishes the composite Uvicorn reconstruction from the
separate full application-process restart proof and never claims arbitrary
external exactly-once delivery, production security, disaster recovery,
multi-host operation, or real-model correctness.

## Verification strategy

1. Unit-test the runner manifest and its unchanged pytest exit-code behavior.
2. Run the portfolio entry point in collect-only mode and require every fixed
   node ID to resolve.
3. Develop the composite test RED-to-GREEN around the two Worker failures and
   API reconstruction.
4. Run the one-command portfolio twice consecutively, without manual database
   cleanup between runs, to detect leaked rows, child processes, listeners, or
   reliance on prior state.
5. Run the complete backend suite and the existing deterministic dashboard
   static/browser checks because documentation and helpers cross those
   boundaries.
6. Run dependency, migration, Compose, whitespace, port, process, and privacy
   checks.
7. Obtain one independent whole-change review focused on false claims,
   accidentally log-based evidence, cleanup gaps, race conditions, and
   authority mismatches. Remediate Critical and Important findings before the
   checkpoint is presented.

## Acceptance

Milestone 8 is acceptable when one documented PowerShell command, with Docker
Desktop and the pinned loopback services available, exits zero only after:

- the continuous 20-step run crosses both Worker-crash windows and API
  reconstruction while preserving one run and one Workflow;
- every required failure-matrix case reaches its specified durable outcome;
- application, Temporal, artifact, receiver, process, port, and privacy
  invariants are machine-checked rather than inferred from logs;
- the same command passes twice from clean disposable test state; and
- the architecture, operations, evidence, and non-claim documentation matches
  the executed proof.

No Milestone 9 deployment work begins without a separate explicit request.
