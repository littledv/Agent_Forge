# AgentForge Milestone 4 Dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a local Next.js dashboard that submits and observes support-report runs, exposes durable approval/cancellation controls, and safely displays verified artifacts without requiring raw logs.

**Architecture:** FastAPI adds bounded PostgreSQL-backed run-list/detail reads and a checksum-verifying artifact-content endpoint. A native Windows Next.js App Router application polls those projections through a same-origin rewrite, keeps backend state authoritative, and presents the selected guided-run workspace. Playwright covers controlled UI states plus one real local API-to-artifact journey.

**Tech Stack:** Python 3.13.17, FastAPI 0.158.1, SQLAlchemy 2.1.6, PostgreSQL 16.15, Temporal SDK 1.33.0, Node.js 24.22.1 LTS, Next.js 16.4.3, React 19.3.1, TypeScript 6.0.3, ESLint 9.39.5, Lucide React 1.61.0, Playwright 1.64.1.

**Spec:** `docs/superpowers/specs/2026-10-25-agentforge-milestone-4-dashboard-design.md`

## Global Constraints

- Implement Milestone 4 only; do not add real model providers, multi-agent roles, SDK work, deployment, authentication, a remote, or public access.
- Preserve Temporal as execution-history authority and PostgreSQL as application-record/dashboard-projection authority; the dashboard API must not become a scheduler or poll Temporal.
- Keep the dashboard, API, Temporal UI, PostgreSQL, and Temporal ports bound to `127.0.0.1` only.
- Use native Windows PowerShell commands. WSL remains Docker Desktop's Linux-container backend and is not the frontend execution environment.
- Install no runtime or package until the user approves this plan's dependency set.
- Pin Node.js to `24.22.1` LTS. Verify the official Windows x64 MSI SHA-256 `3bda4baf1ee709b33cf920dd633106ddb23951e322bff9c03ec9f64ab8981b01` before installation.
- Pin direct npm dependencies exactly: `next==16.4.3`, `react==19.3.1`, `react-dom==19.3.1`, `lucide-react==1.61.0`, `typescript==6.0.3`, `eslint==9.39.5`, `eslint-config-next==16.4.3`, `@playwright/test==1.64.1`, `@types/node==24.19.2`, `@types/react==19.3.0`, and `@types/react-dom==19.3.0`. TypeScript and ESLint use the separately approved compatibility correction recorded in `PROJECT_PLAN.md`.
- Commit `dashboard/package-lock.json`; use `npm ci` for repeatable installs after the lockfile exists.
- Install only Playwright Chromium for this milestone; do not install Firefox or WebKit browser bundles.
- Use Lucide React as the only icon library. Add no component framework, state-management library, data-fetching library, chart package, CSS framework, or Markdown renderer.
- Render artifact content as escaped plain text with `white-space: pre-wrap`; never inject report content as HTML.
- Poll bounded PostgreSQL read models: one second for the selected nonterminal run and two seconds for the recent list, with no overlapping requests. Stop selected-run polling in terminal states.
- Existing approval binding, cancellation semantics, receiver deduplication, failure classifications, privacy properties, and API contracts remain compatible.
- Use TDD for every backend and browser-visible behavior. Run the narrow RED test before implementation, then the narrow GREEN test, then the task regression set.
- Use the user's configured Git identity. Do not add Codex, OpenAI, generated-by, or AI-assistant authorship or project credit.

Version evidence checked on 2026-10-25: [Node.js 24.22.1 LTS](https://nodejs.org/en/blog/release/v24.22.1/), [Next.js installation requirements](https://nextjs.org/docs/app/getting-started/installation), [Next.js 16.4.3 registry metadata](https://registry.npmjs.org/next/latest), and [Playwright installation requirements](https://playwright.dev/docs/next/intro).

## Review Focus

- A selected run disappearing between polls must clear stale selection, refresh recent runs, and show a neutral not-found message; Task 4 pins this with a mocked `404` browser journey.
- A durable approval or cancellation conflict must refresh the winner and never leave an optimistic local state; Task 6 pins both `409` paths.
- Artifact path, size, media type, checksum, or UTF-8 mismatch must return no content and no private validation detail; Task 3 pins every mismatch.
- A slower older poll response must not overwrite a newer selected-run response; Task 5 pins abort/out-of-order behavior.
- A nonterminal projection older than ten seconds must show neutral lag copy and remain visually distinct from failure; Task 5 pins the exact stale projection state.

---

### Task 1: Pin the native frontend toolchain and build the dashboard shell

**Files:**
- Create: `dashboard/package.json`
- Create: `dashboard/package-lock.json`
- Create: `dashboard/.npmrc`
- Create: `dashboard/.env.example`
- Create: `dashboard/next.config.ts`
- Create: `dashboard/tsconfig.json`
- Create: `dashboard/eslint.config.mjs`
- Create: `dashboard/playwright.config.ts`
- Create: `dashboard/app/layout.tsx`
- Create: `dashboard/app/page.tsx`
- Create: `dashboard/app/globals.css`
- Create: `dashboard/e2e/shell.spec.ts`
- Modify: `.gitignore`

**Interfaces:**
- Consumes: loopback FastAPI base URL `http://127.0.0.1:8000` and approved visual direction from the spec.
- Produces: npm scripts `dev`, `build`, `start`, `lint`, `typecheck`, `test:e2e`, and `test:e2e:real`; a Next.js process bound to `127.0.0.1:3000`; same-origin `/backend/*` rewrite; accessible page shell.

- [x] **Step 1: Verify and install the approved Node.js runtime**

  Confirm the approved dependency gate covers Node.js and the listed npm packages. Download the official `node-v24.22.1-x64.msi`, verify its SHA-256 before execution, install it natively, open a fresh PowerShell process if PATH changed, and record `node --version` plus `npm --version`. Do not use the Codex-bundled Node-only runtime as the project runtime because it has no npm executable.

- [x] **Step 2: Create the pinned package and tool configuration**

  Write `package.json` with exact versions from Global Constraints, `engines.node` set to `24.22.1`, and scripts whose development/start commands pass `-H 127.0.0.1`. Configure Next.js to rewrite `/backend/:path*` to `${AGENTFORGE_API_BASE_URL ?? "http://127.0.0.1:8000"}/:path*`. Configure Playwright for Chromium, base URL `http://127.0.0.1:3000`, screenshots/traces only on failure, and a Next development web server. Run `npm install --package-lock-only`, then `npm ci` and `npx playwright install chromium`.

- [x] **Step 3: Write the failing shell browser test**

  In `shell.spec.ts`, intercept `GET /backend/api/v1/runs?limit=20` with `[]`; open `/`; assert an `AgentForge` heading, `New run` button, explanatory empty state, visible keyboard focus for the button, and no horizontal overflow at 1024 × 768.

- [x] **Step 4: Run the shell test to verify RED**

  Run: `Set-Location dashboard; npm run test:e2e -- e2e/shell.spec.ts`

  Expected: FAIL because the page shell and accessible controls do not exist.

- [x] **Step 5: Implement the minimal shell and visual tokens**

  Build the App Router layout and one page with the selected warm off-white/navy/cobalt/mint/amber system, recent-runs rail, main empty state, and responsive stacking at widths below 1024 px. Use Lucide icons with accessible names; do not draw substitute icons with text, CSS, SVG, or emoji.

- [x] **Step 6: Run the task checks**

  Run:

  ```powershell
  Set-Location dashboard
  npm run test:e2e -- e2e/shell.spec.ts
  npm run typecheck
  npm run lint
  npm run build
  npm audit --audit-level=high
  ```

  Expected: shell journey passes, TypeScript/lint/build exit `0`, and npm reports no high-or-critical production vulnerability. Stop and review rather than upgrading outside the approved pins if audit fails.

- [x] **Step 7: Commit the frontend foundation**

  ```powershell
  git add .gitignore dashboard
  git commit -m "feat: scaffold local dashboard"
  ```

### Task 2: Add bounded recent-run and nested run-detail read models

**Files:**
- Modify: `src/agentforge/domain.py`
- Modify: `src/agentforge/repository.py`
- Modify: `src/agentforge/api.py`
- Modify: `tests/integration/test_repository.py`
- Modify: `tests/integration/test_api.py`

**Interfaces:**
- Consumes: existing `RunRecord.request_payload`, `RunSnapshot`, step/attempt/approval/cancellation/delivery records, and strict Pydantic conventions.
- Produces: `RunListItem`, `AttemptSnapshot`, `StepSnapshot`, `RunDetailSnapshot`; `RunRepository.list_recent(session, limit) -> tuple[RunListItem, ...]`; `RunRepository.get_detail(session, run_id) -> RunDetailSnapshot | None`; `GET /api/v1/runs?limit=20`; `GET /api/v1/runs/{run_id}/detail`.

- [x] **Step 1: Write failing repository read-model tests**

  Add tests that create multiple runs with controlled timestamps, steps, attempts, approval, cancellation, and delivery rows. Assert `list_recent(..., 2)` returns exactly two newest items with goal/state/failure/timestamps; `get_detail` returns original goal/ticket files, projection sequence, steps ordered by ordinal, attempts ordered by attempt number, latest approval, cancellation flag, and accepted receipt; unknown ID returns `None`.

- [x] **Step 2: Run repository tests to verify RED**

  Run: `.venv\Scripts\python.exe -m pytest tests/integration/test_repository.py -k "list_recent or run_detail" -v`

  Expected: FAIL because the models and repository methods do not exist.

- [x] **Step 3: Implement strict dashboard projection contracts**

  Add the four strict immutable models with these shapes:

  ```python
  class RunListItem(StrictModel):
      run_id: UUID
      goal: Goal
      state: RunState
      failure_kind: FailureKind | None
      created_at: datetime
      updated_at: datetime

  class AttemptSnapshot(StrictModel):
      attempt_number: PositiveInt
      state: AttemptState
      failure_kind: FailureKind | None
      created_at: datetime
      updated_at: datetime

  class StepSnapshot(StrictModel):
      ordinal: NonNegativeInt
      name: str
      state: StepState
      created_at: datetime
      updated_at: datetime
      attempts: tuple[AttemptSnapshot, ...]

  class RunDetailSnapshot(StrictModel):
      run: RunSnapshot
      goal: Goal
      ticket_files: tuple[str, ...]
      projection_sequence: NonNegativeInt
      steps: tuple[StepSnapshot, ...]
      approval: ApprovalSnapshot | None
      cancellation_requested: bool
      delivery: DeliveryReceipt | None
  ```

  Validate stored `request_payload` through `SubmitRun` rather than trusting JSON shape. Bound the list at the API and repository boundary; use explicit eager/batched ordered queries and do not issue one attempt query per step.

- [x] **Step 4: Run repository tests to verify GREEN**

  Run: `.venv\Scripts\python.exe -m pytest tests/integration/test_repository.py -k "list_recent or run_detail" -v`

  Expected: PASS.

- [x] **Step 5: Write failing API boundary tests**

  Assert list default/explicit limits, newest-first output, `limit=0` and `51` return `422`, detail returns the nested strict shape, and an unknown run returns sanitized `404`. Include a run deleted between list and detail requests to pin the stale-selection boundary.

- [x] **Step 6: Implement the two GET routes**

  Use FastAPI `Query(default=20, ge=1, le=50)`. Preserve existing database failure handling as sanitized `503`; do not query Temporal or add caching.

- [x] **Step 7: Run API and regression tests**

  Run: `.venv\Scripts\python.exe -m pytest tests/integration/test_api.py tests/integration/test_repository.py -v`

  Expected: PASS with existing submission, approval, and cancellation cases unchanged.

- [x] **Step 8: Commit the read models**

  ```powershell
  git add src/agentforge/domain.py src/agentforge/repository.py src/agentforge/api.py tests/integration/test_repository.py tests/integration/test_api.py
  git commit -m "feat: expose dashboard run projections"
  ```

### Task 3: Expose verified report text without arbitrary file access

**Files:**
- Modify: `src/agentforge/domain.py`
- Modify: `src/agentforge/api.py`
- Modify: `tests/integration/test_api.py`

**Interfaces:**
- Consumes: `RunRepository.get`, `ArtifactStorage.read_verified_artifact(ref) -> bytes`, configured artifact root, recorded `ArtifactRef`.
- Produces: `ArtifactContent(artifact: ArtifactRef, content: DeliveryContent)` and `GET /api/v1/runs/{run_id}/artifact`.

- [x] **Step 1: Write failing artifact API tests**

  Assert: unknown run and a run without an artifact return sanitized `404`; a matching `text/markdown` artifact returns exact metadata/content; path escape, missing file, directory, symlink escape, size mismatch, SHA-256 mismatch, non-Markdown media type, invalid UTF-8, and content beyond 262,144 characters return no content and a constant sanitized `409` detail. Include a sentinel report prefix and assert it is absent from every error body and captured log record.

- [x] **Step 2: Run artifact tests to verify RED**

  Run: `.venv\Scripts\python.exe -m pytest tests/integration/test_api.py -k artifact_content -v`

  Expected: FAIL because the route and contract do not exist.

- [x] **Step 3: Implement `ArtifactContent` and verified retrieval**

  Construct one `ArtifactStorage` in `create_app`. Look up the run by ID, require its recorded artifact and `text/markdown` media type, call `read_verified_artifact`, decode strict UTF-8, then validate `ArtifactContent`. Catch `StorageViolation`, `UnicodeDecodeError`, and Pydantic `ValidationError` and return `409` with exactly `artifact could not be verified` using `from None`. Never accept a path query or return a filesystem root.

- [x] **Step 4: Run artifact and storage tests to verify GREEN**

  Run: `.venv\Scripts\python.exe -m pytest tests/integration/test_api.py -k artifact_content -v; .venv\Scripts\python.exe -m pytest tests/unit/test_storage.py -v`

  Expected: PASS.

- [x] **Step 5: Commit safe artifact inspection**

  ```powershell
  git add src/agentforge/domain.py src/agentforge/api.py tests/integration/test_api.py
  git commit -m "feat: expose verified report content"
  ```

### Task 4: Implement recent runs and retry-safe browser submission

**Files:**
- Create: `dashboard/lib/contracts.ts`
- Create: `dashboard/lib/api.ts`
- Create: `dashboard/lib/format.ts`
- Create: `dashboard/components/run-dashboard.tsx`
- Create: `dashboard/components/run-list.tsx`
- Create: `dashboard/components/run-form.tsx`
- Modify: `dashboard/app/page.tsx`
- Modify: `dashboard/app/globals.css`
- Create: `dashboard/e2e/submission.spec.ts`
- Create: `dashboard/e2e/helpers/backend-fixture.ts`

**Interfaces:**
- Consumes: Task 2 list/detail JSON, existing `POST /api/v1/runs`, exact run states.
- Produces: `listRuns(signal): Promise<RunListItem[]>`, `getRunDetail(runId, signal): Promise<RunDetailSnapshot>`, `submitRun(input): Promise<RunSnapshot>`; selected-run state; retry-stable submission key.

- [x] **Step 1: Write failing mocked browser journeys**

  Use the backend fixture to assert: recent rows render newest first; selecting a row requests and displays that run; `New run` exposes the default goal and two approved filenames; two retries after a simulated network loss send the same UUID submission key; a successful submit selects the returned run; a detail `404` clears selection, refreshes recent runs, and shows `That run is no longer available`.

- [x] **Step 2: Run submission tests to verify RED**

  Run: `Set-Location dashboard; npm run test:e2e -- e2e/submission.spec.ts`

  Expected: FAIL because the contracts, fixture, list, and form do not exist.

- [x] **Step 3: Implement exact TypeScript contracts and API errors**

  Mirror the backend JSON field names and enums without creating an independent state vocabulary. `ApiError` carries only HTTP status and sanitized response detail. Parse responses defensively enough to reject a non-object or missing required top-level keys; do not add a schema package.

- [x] **Step 4: Implement recent-run selection and submission**

  Generate the UUID submission key once when the form opens and replace it only after a successful submission or explicit form reset. Keep the two ticket filenames fixed/read-only in this milestone. Disable submit while pending, preserve values on failure, and expose field-level `422` details without dumping raw JSON.

- [x] **Step 5: Run browser and static checks**

  Run:

  ```powershell
  Set-Location dashboard
  npm run test:e2e -- e2e/submission.spec.ts e2e/shell.spec.ts
  npm run typecheck
  npm run lint
  npm run build
  ```

  Expected: PASS.

- [x] **Step 6: Commit browser submission**

  ```powershell
  git add dashboard
  git commit -m "feat: submit and select dashboard runs"
  ```

### Task 5: Render the durable timeline and projection freshness honestly

**Files:**
- Create: `dashboard/lib/polling.ts`
- Create: `dashboard/components/run-header.tsx`
- Create: `dashboard/components/run-timeline.tsx`
- Create: `dashboard/components/projection-status.tsx`
- Modify: `dashboard/components/run-dashboard.tsx`
- Modify: `dashboard/app/globals.css`
- Create: `dashboard/e2e/run-timeline.spec.ts`

**Interfaces:**
- Consumes: `RunDetailSnapshot`, one-second detail/two-second list intervals, terminal-state set from the Python domain.
- Produces: `useRunPolling(selectedRunId)`, logical-step timeline with nested attempts, neutral projection freshness/lag UI, local Temporal UI link.

- [x] **Step 1: Write failing timeline and polling tests**

  Assert ordered steps and attempts; retry metadata expands automatically while a one-attempt successful step stays collapsed; terminal states stop detail polling; nonterminal state continues at one-second intervals; switching selection aborts the old request; an intentionally delayed older response cannot overwrite the newer selected detail. Return a RUNNING projection updated 11 seconds ago and assert `Projection may be delayed`, `Application projection`, and `Open Temporal UI` appear while no `Failed` heading or failure styling appears.

- [x] **Step 2: Run timeline tests to verify RED**

  Run: `Set-Location dashboard; npm run test:e2e -- e2e/run-timeline.spec.ts`

  Expected: FAIL because polling and timeline components do not exist.

- [x] **Step 3: Implement polling ownership**

  Use one `AbortController` per request generation and ignore completions whose generation no longer matches the selection. Schedule the next poll only after the current request settles. Preserve the last good response on `503`/network failure and show a nonterminal service banner. Stop detail polling for `SUCCEEDED`, `FAILED`, `DENIED`, `CANCELLED`, `TIMED_OUT`, `BUDGET_EXHAUSTED`, and `NEEDS_ATTENTION`.

- [x] **Step 4: Implement the run header, timeline, and freshness copy**

  Render state labels as text plus Lucide symbols. Show failure kind only when present. Compute freshness from server `updated_at` against the browser clock; the 10-second notice is neutral. Link `Open Temporal UI` to `http://127.0.0.1:8080` in a new tab with safe rel attributes; do not invent a deep-link route.

- [x] **Step 5: Run timeline and regression checks**

  Run: `Set-Location dashboard; npm run test:e2e -- e2e/run-timeline.spec.ts e2e/submission.spec.ts; npm run typecheck; npm run lint; npm run build`

  Expected: PASS.

- [x] **Step 6: Commit run observation**

  ```powershell
  git add dashboard
  git commit -m "feat: visualize durable run progress"
  ```

### Task 6: Add exact approval, cancellation, and artifact interactions

**Files:**
- Create: `dashboard/components/run-context.tsx`
- Create: `dashboard/components/approval-panel.tsx`
- Create: `dashboard/components/artifact-panel.tsx`
- Modify: `dashboard/lib/api.ts`
- Modify: `dashboard/components/run-dashboard.tsx`
- Modify: `dashboard/app/globals.css`
- Create: `dashboard/e2e/run-actions.spec.ts`

**Interfaces:**
- Consumes: existing approval-decision/cancel APIs, Task 3 artifact API, Task 4 API error model, Task 5 refresh callback.
- Produces: `decideApproval(approvalId, actionHash, decision)`, `cancelRun(runId)`, `getArtifact(runId)`, exact approval UI, confirmed cancellation UI, lazy safe artifact view.

- [x] **Step 1: Write failing approval and cancellation browser tests**

  Assert pending approval shows target, action hash, artifact path/media type/size/SHA-256 before enabled Approve/Reject buttons; a mutation disables Approve/Reject/Cancel together; success refreshes instead of changing state optimistically; approval `409` displays `Durable state changed; refreshed the winning decision`; cancellation confirmation states that committed delivery cannot be undone; cancellation `409` refreshes and preserves the winner.

- [x] **Step 2: Write failing artifact browser tests**

  Assert artifact metadata appears without fetching content; expanding `Report content` makes exactly one request; returned strings containing `<script>`, Markdown links, and HTML display literally as text; close/reopen uses the cached verified response; `409` shows `Artifact could not be verified` and never displays response internals.

- [x] **Step 3: Run action tests to verify RED**

  Run: `Set-Location dashboard; npm run test:e2e -- e2e/run-actions.spec.ts`

  Expected: FAIL because the context panels and mutations do not exist.

- [x] **Step 4: Implement exact mutations and lazy artifact content**

  Send `APPROVE`/`REJECT` with the currently displayed action hash. Keep controls disabled until mutation plus refresh settles. Require an explicit cancellation confirmation. Render artifact content only in a text container using `white-space: pre-wrap`; never use `dangerouslySetInnerHTML`.

- [x] **Step 5: Run dashboard regression checks**

  Run: `Set-Location dashboard; npm run test:e2e; npm run typecheck; npm run lint; npm run build`

  Expected: all mocked browser journeys and static checks pass.

- [x] **Step 6: Commit run controls and artifact inspection**

  ```powershell
  git add dashboard
  git commit -m "feat: control and inspect dashboard runs"
  ```

### Task 7: Prove the real browser workflow and close the checkpoint

**Files:**
- Create: `dashboard/playwright.real.config.ts`
- Create: `dashboard/e2e/real-flow.spec.ts`
- Create: `tests/helpers/dashboard_stack.ps1`
- Modify: `README.md`
- Modify: `ARCHITECTURE.md`
- Modify: `FLOW.md`
- Modify: `DECISIONS.md`
- Modify: `PROJECT_PLAN.md`
- Modify: `TEST_CHECKLIST.md`
- Modify: `HANDOVER.md`
- Create: `docs/learning/07_MILESTONE_4_DASHBOARD.md`

**Interfaces:**
- Consumes: all Tasks 1–6, native console entry points, loopback infrastructure, existing database migrations, real sample tickets.
- Produces: `tests/helpers/dashboard_stack.ps1` bounded native harness; real Playwright happy path; complete Milestone 4 operating and learning documentation.

- [x] **Step 1: Write the failing real Playwright journey**

  Use a unique submission key. Submit the default goal, wait for the real run to reach `WAITING_APPROVAL`, assert the exact pending target/hash/artifact metadata, approve, wait for `SUCCEEDED`, expand report content, assert `# Support Report` and both sample ticket headings, then query the APIs and inbox database to assert one run, one approval, one accepted delivery, and one receiver row.

- [x] **Step 2: Run the real journey to verify RED**

  Run: `powershell -ExecutionPolicy Bypass -File tests/helpers/dashboard_stack.ps1 -TestOnly`

  Expected: FAIL because the native stack harness and/or real test configuration do not exist yet.

- [x] **Step 3: Implement the bounded native stack harness**

  Use explicit repository paths and `Start-Process -PassThru -WindowStyle Hidden` for API, relay, inbox, Worker, and dashboard. Publish only loopback ports. Wait with deadlines for API/inbox readiness and the dashboard page. Track exact process IDs, stop only those IDs in `finally`, and fail if ports 8000, 8001, or 3000 retain an application listener afterward. Do not stop PostgreSQL or Temporal and do not delete persisted data.

- [x] **Step 4: Make the real journey GREEN**

  Configure the real suite with no request interception and one Chromium worker. Run: `powershell -ExecutionPolicy Bypass -File tests/helpers/dashboard_stack.ps1`

  Expected: PASS with the real browser/API/relay/Temporal/Worker/inbox/artifact path and exact cleanup assertions.

- [x] **Step 5: Run complete verification**

  Run:

  ```powershell
  .venv\Scripts\python.exe -m pytest -v
  .venv\Scripts\python.exe -m pip check
  Set-Location dashboard
  npm ci
  npm run typecheck
  npm run lint
  npm run build
  npm run test:e2e
  npm audit --audit-level=high
  Set-Location ..
  docker compose config --quiet
  .venv\Scripts\alembic.exe current
  .venv\Scripts\alembic.exe -c inbox_alembic.ini current
  git diff --check
  ```

  Expected: backend and mocked/real browser suites pass; dependency, build, Compose, migration, and whitespace checks exit `0`.

- [x] **Step 6: Demonstrate ordinary restart from the browser**

  Start one real run in the dashboard and stop the API, relay, inbox, Worker, and dashboard while it is `WAITING_APPROVAL`. Confirm PostgreSQL/Temporal persist and no application listener remains. Restart the five native processes, reopen the same run, approve it, and verify one final receiver delivery plus verified artifact content. Record run/workflow/approval/action/operation/delivery IDs and process cleanup in `HANDOVER.md`.

- [x] **Step 7: Update documentation and checkpoint state**

  Document the dashboard startup command, local URLs, dependency pins, polling/projection wording, exact approval controls, artifact safety, selected Playwright commands, native recovery evidence, and the Milestone 5 approval boundary. Add D-009 recording the guided workspace, bounded polling, same-origin rewrite, and plain-text artifact rendering decisions. Mark only actually observed checklist items complete.

- [x] **Step 8: Commit the verified Milestone 4 checkpoint**

  ```powershell
  git add dashboard tests/helpers/dashboard_stack.ps1 README.md ARCHITECTURE.md FLOW.md DECISIONS.md PROJECT_PLAN.md TEST_CHECKLIST.md HANDOVER.md docs/learning/07_MILESTONE_4_DASHBOARD.md
  git commit -m "docs: record verified Milestone 4 dashboard"
  ```

## Self-review results

- **Spec coverage:** Every goal, non-goal, API boundary, screen region, state/error behavior, and completion condition maps to Tasks 1–7. No Milestone 5+ work is included.
- **Step scan:** Each task has a narrow failing test, minimal implementation step, explicit verification, and one coherent commit. Setup is folded into the shell deliverable.
- **Type consistency:** Python model names and TypeScript contracts remain identical across backend and frontend tasks. Task 3's `ArtifactContent` is the sole artifact-body contract.
- **Review Focus:** All five high-risk cases map to explicit tests in Tasks 2–6.
- **Proportion:** The plan fixes boundaries, interfaces, exact versions, checks, and failure semantics while leaving component internals and CSS mechanics to the implementer.
