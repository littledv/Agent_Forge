# AgentForge Durable Project Plan

This is the project-level source of truth for progress. Detailed implementation
plans are created one milestone at a time after the preceding milestone is
reviewed. `HANDOVER.md` records the most recent session; this file records the
long-lived roadmap.

## How checkpoints work

- `[ ]` means not started.
- `[~]` means actively being worked on.
- `[x]` means implemented and verified.
- `[-]` means intentionally skipped and outside the selected project scope.
- A milestone is not complete until its acceptance criteria were observed.
- Before ending a session, update this file and `HANDOVER.md`.
- On a new chat, read the repository rather than relying on prior conversation.
- Commit only coherent checkpoints. Never commit broken work merely to clear the
  working tree.

## Project gates

- [x] Project idea selected.
- [x] Architecture alternatives compared.
- [x] Temporal architecture approved.
- [x] Local Docker Desktop/WSL2 baseline approved.
- [x] Documentation baseline committed and approved for implementation.
- [x] Documentation baseline created and mechanically verified.
- [x] Local Git commit author and metadata verified.
- [x] Private remote explicitly approved and created.
- [x] Separate sanitized public source repository explicitly approved, created,
      and verified.
- [x] Milestone 1 implementation explicitly approved.
- [x] Milestone 1 checkpoint reviewed and approved.
- [x] Milestone 2 checkpoint reviewed and approved.
- [x] Milestone 3 focused implementation plan prepared.
- [x] Milestone 3 implementation explicitly approved.
- [x] Milestone 3 checkpoint reviewed and approved.
- [x] Milestone 4 dashboard design and focused implementation plan prepared.
- [x] Milestone 4 implementation and pinned frontend dependencies explicitly
      approved.
- [x] Milestone 4 TypeScript/ESLint compatibility pin correction explicitly
      approved.
- [x] Milestone 4 independent review findings remediated and fully reverified.
- [x] Milestone 4 checkpoint reviewed and approved.
- [x] Milestone 5 local Ollama/Qwen direction selected.
- [x] Milestone 5 design prepared and builder-checked.
- [x] Milestone 5 design and dependency set explicitly approved.
- [x] Milestone 5 focused implementation plan prepared.
- [x] Milestone 5 implementation plan explicitly approved.
- [x] Milestone 5 independent review findings remediated and fully reverified.
- [x] Milestone 5 checkpoint reviewed and approved.
- [x] Milestone 6 support-report-specific graph direction selected.
- [x] Milestone 6 design prepared and builder-checked.
- [x] Milestone 6 design explicitly approved.
- [x] Milestone 6 focused implementation plan prepared.
- [x] Milestone 6 implementation plan explicitly approved.
- [x] Milestone 6 implementation completed and fully verified.
- [x] Milestone 6 independent review findings remediated and fully reverified.
- [x] Milestone 6 checkpoint reviewed and approved.
- [x] Milestone 7 synchronous Python SDK direction selected.
- [x] Milestone 7 design prepared and builder-checked.
- [x] Milestone 7 design explicitly approved.
- [x] Milestone 7 focused implementation plan prepared.
- [x] Milestone 7 implementation plan explicitly approved.
- [x] Milestone 7 implementation completed and fully verified.
- [x] Milestone 7 independent review findings remediated and fully reverified.
- [x] Milestone 7 checkpoint reviewed and approved.
- [x] Milestone 8 deterministic mock failure-portfolio direction selected.
- [x] Milestone 8 design prepared and builder-checked.
- [x] Milestone 8 design explicitly approved.
- [x] Milestone 8 focused implementation plan prepared.
- [x] Milestone 8 implementation plan explicitly approved.
- [x] Milestone 8 implementation completed and fully verified.
- [x] Milestone 8 independent review findings remediated and fully reverified.
- [x] Milestone 8 checkpoint reviewed and approved.

## Milestone 0 — Design and scope agreement

**Objective:** Establish vocabulary, boundaries, correctness rules, and the
first demonstration before product code exists.

- [x] Define Agent, Run, Workflow, Activity, and Worker.
- [x] Choose Temporal with a small Python agent layer.
- [x] Define Temporal/PostgreSQL ownership.
- [x] Choose the support-report workflow.
- [x] Define run and step state families.
- [x] Define approval and idempotency principles.
- [x] Separate minimum local scope from later extensions.
- [x] Review repository documentation.
- [x] Approve the Milestone 1 implementation plan.

**Acceptance:** A future implementer can explain the system, its sources of
truth, and its principal failure cases without relying on chat history.

## Milestone 1 — Small complete local workflow

**Depends on:** Milestone 0 approval and verified local Git checkpoint.

**Objective:** Submit a run and produce a deterministic support-report artifact
through Temporal.

**Status:** Implemented, verified, and approved on the target Windows machine.

- [x] Inspect the final repository instructions and current tree.
- [x] Verify and pin compatible versions from official documentation.
- [x] Prepare the exact file-by-file, test-driven implementation plan.
- [x] Install and verify WSL2 and Docker Desktop after explicit approval.
- [x] Define versioned agent, run, step, attempt, and artifact schemas.
- [x] Add PostgreSQL migrations.
- [x] Add retry-safe API submission and start outbox.
- [x] Add deterministic mock model and bounded decision loop.
- [x] Add approved file-read and atomic artifact-write Activities.
- [x] Add a run-status API.
- [x] Document start, stop, logs, health, persistence, and destructive reset.

**Acceptance:** A repeated submission returns the same run, Temporal executes it,
and a deterministic report with a verifiable checksum reaches `SUCCEEDED`.

## Milestone 2 — Persistent execution and worker recovery

**Depends on:** Milestone 1.

**Status:** Implemented, verified, and approved on the target Windows machine.

- [x] Prepare the exact test-driven Milestone 2 implementation plan.

- [x] Add controlled multi-step execution.
- [x] Add attempt and projection records.
- [x] Add failure injection for worker termination.
- [x] Prove recovery after worker restart.
- [x] Prove data survives ordinary service restart.

**Acceptance:** A worker can die mid-run and a replacement completes the run
without losing durable progress.

## Milestone 3 — Permissions, approvals, and safe delivery

**Depends on:** Milestone 2.

**Status:** Implemented, verified, and approved on the target Windows machine.

- [x] Prepare the exact test-driven Milestone 3 implementation plan.

- [x] Enforce trusted tool allowlists and argument validation.
- [x] Persist exact action-bound approval requests and decisions.
- [x] Handle approval, rejection, expiry, duplicates, and conflicts.
- [x] Add the separate simulated inbox service.
- [x] Enforce durable deduplication by stable operation ID.
- [x] Inject crash after receiver commit and before Activity acknowledgement.
- [x] Close final-review gaps in cancellation timing, loopback proxy isolation,
      and delivery-error redaction.

**Acceptance:** Delivery requires approval, survives the ambiguous crash window,
and produces exactly one observable inbox delivery.

## Milestone 4 — Dashboard and artifact inspection

**Depends on:** Milestone 3.

**Status:** Implemented, independently reviewed, verified, and approved on the
target Windows machine. The real Chromium flow and ordinary application-restart
proof both completed with one durable approval and one receiver delivery.

- [x] Prepare the exact test-driven Milestone 4 implementation plan.

- [x] Add the Next.js dashboard.
- [x] Poll run, step, attempt, approval, and artifact projections.
- [x] Display projection lag without presenting it as execution failure.
- [x] Add approve, reject, and cancel controls.
- [x] Add selected Playwright journeys.
- [x] Close final-review gaps in mutation refresh safety, cancellation identity,
      artifact retry, timing, delivery evidence, and terminal manual refresh.

**Acceptance:** The complete local workflow can be demonstrated from the browser
without reading raw logs.

## Milestone 5 — Optional real model integration

**Depends on:** Stable Milestones 1–4 and separate provider approval.

**Status:** Implemented, independently reviewed, fully verified, and approved
on the target Windows machine. Native Ollama/Qwen identity, bounded generation,
durable usage, dashboard evidence, privacy, and cleanup are complete.

- [x] Select one cloud provider or local-model runtime.
- [x] Design immutable model-profile binding and dedicated task queues.
- [x] Prepare the exact test-driven Milestone 5 implementation plan.
- [x] Keep mock mode as the default automated-test path.
- [x] Validate structured output and bound repair attempts.
- [x] Add token accounting, estimates, and uncertainty handling.
- [x] Add credential-safe configuration and redaction.
- [x] Revalidate the pinned Ollama tag/digest before every generation request.
- [x] Isolate real Temporal integration Workers in a dedicated test namespace.

**Acceptance:** The workflow can switch model adapters without weakening the
credential-free deterministic test suite.

## Milestone 6 — Bounded multi-agent task graph

**Status:** Implemented, independently reviewed, and fully verified on the
target Windows machine. The checkpoint was approved on 2026-10-27.

- [x] Define researcher, writer, and reviewer responsibilities.
- [x] Use a bounded static graph with fixed revision and fan-out limits.
- [x] Add multiple workers only where task-queue separation is useful.
- [x] Test partial worker loss and reviewer rejection limits.

**Acceptance:** Multiple roles finish one report under explicit task,
concurrency, and revision limits.

## Milestone 7 — Documented API and Python SDK

**Status:** Implemented, independently reviewed, fully verified, and approved
on 2026-10-28. Milestone 8 proceeded only after its separate design and focused
plan approvals.

- [x] Stabilize versioned API contracts.
- [x] Add a thin typed Python client.
- [x] Document retry, timeout, pagination, and error behavior.
- [x] Test duplicate submissions and interrupted polling.

**Acceptance:** A small Python program can submit, observe, approve, and retrieve
a report safely.

## Milestone 8 — Evaluation and failure portfolio

- [x] Automate the required 20-step recovery demonstration.
- [x] Test rejection, retry exhaustion, budget exhaustion, duplicate
  submissions, and concurrent side-effect attempts.
- [x] Assert database and receiver state instead of relying only on logs.
- [x] Document which properties are and are not proven.
- [x] Produce portfolio-quality architecture and operations documentation.

**Acceptance:** One deterministic command demonstrates the agreed failure
properties with machine-checked invariants.

## Milestone 9 — Optional public deployment

**Skipped by the user on 2026-10-28.** Reopen only after a separate explicit
request to pursue public deployment.

- [-] Compare a simple container deployment with more complex options.
- [-] Add authentication, authorization, TLS, rate limits, secret management,
  backups, restore verification, and appropriate monitoring.
- [-] Replace local artifacts with shared storage when using multiple hosts.
- [-] Define Workflow upgrade and active-run migration behavior.
- [-] Review cost and operational responsibility before provisioning.

**Acceptance if reopened:** Deployment is reviewed in a non-production
environment before anything is made public.
