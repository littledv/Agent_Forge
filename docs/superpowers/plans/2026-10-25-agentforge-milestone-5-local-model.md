# AgentForge Milestone 5 Local Model Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an opt-in, natively hosted Ollama/Qwen report generator with immutable per-run model binding, bounded structured output, durable usage evidence, and one verified real browser journey while keeping the deterministic mock as the default.

**Architecture:** FastAPI resolves an enabled model-profile ID into a strict immutable snapshot and persists it with the run and start outbox. The relay routes the Workflow to the snapshot's dedicated Temporal task queue, and a profile-bound Worker fails closed if the Workflow snapshot differs from its configured provider. The Workflow remains deterministic and receives only validated actions; loopback HTTP, verified ticket reads, generation, repairs, draft writes, and usage persistence stay inside `decide_action` Activities. PostgreSQL remains the dashboard authority and Temporal remains the execution-history authority.

**Tech Stack:** Python 3.13.17, Pydantic 2.14.1, httpx 0.28.1, FastAPI 0.158.1, SQLAlchemy 2.1.6, PostgreSQL 16.15, Temporal SDK 1.33.0, native Windows Ollama 0.40.11, `qwen3:4b-instruct-2507-q4_K_M`, Next.js 16.4.3, React 19.3.1, TypeScript 6.0.3, Playwright 1.64.1.

**Spec:** `docs/superpowers/specs/2026-10-25-agentforge-milestone-5-local-model-design.md`

## Global Constraints

- Implement Milestone 5 only. Do not add cloud providers, API keys, remote data transfer, free-form tool calling, arbitrary paths or delivery targets, embeddings, multi-agent roles, deployment, public access, or a competing scheduler.
- Keep `mock-v1` enabled and selected by default. Ordinary Python and browser tests must never require Ollama, a model download, a GPU, or network access.
- Preserve Temporal replay compatibility: old `WorkflowInput` and stored rows resolve to the exact immutable `mock-v1` snapshot, and Workflow code performs no HTTP, database, file, clock, or model I/O.
- Bind each run to the full `ModelProfileSnapshot`. Include the snapshot in the submission fingerprint, start payload, Workflow input, profile-specific task queue, and Worker equality check. Never fall back between providers.
- Keep all model content local. Use only `http://127.0.0.1:11434` or unambiguous `localhost`; disable environment proxies and redirects. Never persist or log prompts, ticket bodies, raw provider responses, generated reasoning, filesystem roots, credentials, or exception bodies.
- Limit the model to a strict `GeneratedReport(report_markdown: str)` response. Trusted Python chooses every `AgentAction`, file reference, approval target, and side effect.
- Enforce: 24,000 prompt characters, 16,384 report characters, 8,192 context tokens, 2,048 predicted tokens, one initial request plus at most two repair requests, and a 131,072-byte response buffer.
- Use a model-specific 180-second Activity timeout around `decide_action`; keep the existing retry count of three. Classify budget, permanent, retryable, and uncertain outcomes exactly as the design specifies.
- Move the already pinned `httpx==0.28.1` from development-only to core runtime dependencies. Add no Ollama Python SDK and no additional npm dependency.
- Install Ollama only in Task 7, after unit/integration behavior is complete. Verify the official archive SHA-256 `bda4a493ba8a7c24d57263cd843cd68cee84f44aed4299eeed532acad7eda12a` before extraction.
- Use native Windows PowerShell. Docker Desktop remains only the Linux-container backend for PostgreSQL and Temporal. Bind application and Ollama listeners to loopback.
- Use TDD for every behavior: run the stated narrow test to observe RED, implement the minimum, rerun it to GREEN, then run the task regression set.
- Preserve user-authored work and use the user's configured Git identity. Add no Codex, OpenAI, AI-assistant, or generated-by authorship or credit.

## Review Focus

- A replayed Milestones 1–4 history must receive `mock-v1` defaults without changing the deterministic command sequence.
- Reusing a submission key with a different profile, Ollama digest, budget, or queue must return `409`; a retry with the identical full snapshot must return the original run.
- The relay must route from the persisted snapshot, not mutable process settings, and a Worker must reject a full-snapshot mismatch before a provider request.
- A prompt or report over budget must make no provider call and no partial artifact. Schema/semantic failures get exactly two repairs; repair prompts contain only trusted error categories.
- Before every Ollama request, durable usage must become uncertain. Only a complete response may replace the estimate with exact counts and clear that call's uncertainty. A Temporal retry must create a separate attempt row.
- No error body, log, database row, Temporal history, or dashboard response may contain a ticket sentinel, prompt, raw response, or hidden reasoning.
- A pre-existing listener on port 11434 is never terminated or modified. It is reused only after exact Ollama version and model digest verification; otherwise the harness aborts safely.

---

### Task 1: Define immutable model profiles and validated settings

**Files:**
- Modify: `pyproject.toml`
- Modify: `.env.example`
- Modify: `src/agentforge/domain.py`
- Modify: `src/agentforge/config.py`
- Create: `src/agentforge/model_profiles.py`
- Modify: `tests/unit/test_domain.py`
- Modify: `tests/unit/test_config.py`
- Create: `tests/unit/test_model_profiles.py`

**Interfaces:**
- `ModelProfileId = Literal["mock-v1", "ollama-qwen3-4b-instruct-q4-v1"]`
- `ModelProvider = Literal["mock", "ollama"]`
- `ModelProfileSnapshot(profile_id, provider, model_name, model_digest, task_queue, context_tokens, max_output_tokens, max_prompt_chars, max_report_chars, max_repairs)`
- `ModelProfileDescriptor(profile_id, display_name, provider, model_name, model_digest)`
- `MOCK_MODEL_PROFILE`: hard-coded replay-safe `mock-v1` snapshot using queue `agentforge-support-report-v1`
- `Settings.enabled_model_profiles`, `Settings.worker_model_profile`, Ollama URL/model/digest and bounded generation settings
- `Settings.model_profiles() -> tuple[ModelProfileSnapshot, ...]`
- `Settings.resolve_model_profile(profile_id: ModelProfileId) -> ModelProfileSnapshot`

- [ ] **Step 1: Write failing strict-contract and configuration tests**

  Cover exact mock defaults, tuple/list input normalization, duplicate enabled IDs, unknown IDs, a selected-but-disabled Worker profile, missing/full invalid digest, nonpositive or excessive budgets, secret-bearing/non-loopback/path/query/fragment Ollama URLs, and public descriptors that expose no URL or secret. Assert enabling Ollama requires its digest while default mock configuration does not.

- [ ] **Step 2: Run the focused tests to verify RED**

  Run: `.venv\Scripts\python.exe -m pytest tests/unit/test_domain.py tests/unit/test_config.py tests/unit/test_model_profiles.py -v`

  Expected: FAIL because profile contracts and settings do not exist.

- [ ] **Step 3: Implement strict profiles and configuration**

  Put versioned constants and pure profile construction in `model_profiles.py`. Keep the mock snapshot independent of mutable Ollama budgets. Rename the loopback validator's inbox-specific messages so both inbox and Ollama fields can reuse it. Parse `AGENTFORGE_ENABLED_MODEL_PROFILES` as a deduplicated comma-separated tuple that always contains `mock-v1`. Validate a 64-character lowercase digest when Ollama is enabled or selected. Do not contact Ollama from configuration parsing.

- [ ] **Step 4: Reclassify the existing pinned HTTP dependency**

  Move `httpx==0.28.1` into `[project].dependencies`, remove its duplicate from `[project.optional-dependencies].dev`, and reinstall the editable development environment with `.venv\Scripts\python.exe -m pip install -e ".[dev]"`. Run `.venv\Scripts\python.exe -m pip check`.

- [ ] **Step 5: Run the task regression set**

  Run:

  ```powershell
  .venv\Scripts\python.exe -m pytest tests/unit/test_domain.py tests/unit/test_config.py tests/unit/test_model_profiles.py tests/unit/test_inbox_client.py -v
  .venv\Scripts\python.exe -m pip check
  ```

  Expected: PASS; no package other than the approved dependency reclassification is introduced.

- [ ] **Step 6: Commit the profile foundation**

  ```powershell
  git add pyproject.toml .env.example src/agentforge/domain.py src/agentforge/config.py src/agentforge/model_profiles.py tests/unit/test_domain.py tests/unit/test_config.py tests/unit/test_model_profiles.py
  git commit -m "feat: define immutable model profiles"
  ```

### Task 2: Persist profile snapshots and model-usage evidence

**Files:**
- Create: `alembic/versions/0003_model_profiles_and_usage.py`
- Modify: `src/agentforge/db_models.py`
- Modify: `src/agentforge/domain.py`
- Modify: `src/agentforge/repository.py`
- Modify: `src/agentforge/api.py`
- Modify: `tests/integration/test_migrations.py`
- Modify: `tests/integration/test_repository.py`
- Modify: `tests/integration/test_api.py`

**Interfaces:**
- `SubmitRun.model_profile_id: ModelProfileId = "mock-v1"`
- `RunDetailSnapshot.model_profile: ModelProfileSnapshot`
- `GET /api/v1/model-profiles -> list[ModelProfileDescriptor]`
- `RunRepository.submit(session, request, agent_version_id, model_profile) -> SubmissionResult`
- `runs.model_profile JSONB NOT NULL`
- Nullable `attempts` columns: `model_provider`, `model_name`, `model_digest`, `model_request_count`, `model_input_tokens`, `model_output_tokens`, `model_duration_ms`, `provider_cost_estimate_usd`, `input_tokens_estimated`, `usage_uncertain`
- `AttemptSnapshot` exposes the same optional model-usage evidence and no content fields.

- [ ] **Step 1: Write failing migration and repository tests**

  Assert upgrade from `0002` backfills the exact `MOCK_MODEL_PROFILE`, recalculates existing request hashes using the new canonical envelope, preserves idempotent resubmission, and reaches one head. Assert downgrade restores the pre-Milestone-5 schema and request hash. Test nonnegative usage constraints, nullable mock usage, full snapshot round-trip, identical-snapshot idempotency, conflicts for profile/digest/budget/queue changes, and isolation of usage across attempt numbers.

- [ ] **Step 2: Run migration/repository tests to verify RED**

  Run: `.venv\Scripts\python.exe -m pytest tests/integration/test_migrations.py tests/integration/test_repository.py -k "model_profile or model_usage or migration" -v`

  Expected: FAIL because migration `0003`, columns, and repository mapping do not exist.

- [ ] **Step 3: Implement the migration and ORM mappings**

  Add `model_profile` with a temporary PostgreSQL JSONB server default equal to the immutable mock snapshot, backfill every row, and recompute `request_hash` using canonical compact sorted JSON over `{request, model_profile}`. Keep the durable non-null mock default for compatibility with older writers. Add nullable usage columns and check constraints for nonnegative request/token/duration/cost values. The downgrade must recompute legacy hashes before dropping the new columns.

- [ ] **Step 4: Implement profile-aware submission and read models**

  Store `request_payload` with default values excluded so an old mock request and a new implicit mock request normalize identically. Hash the canonical request plus the complete trusted snapshot. Put the same snapshot in the start-outbox payload. Validate all stored JSON with strict Pydantic models before returning it. Do not accept a client-supplied snapshot, task queue, digest, or budget.

- [ ] **Step 5: Write failing API profile tests**

  Test that `GET /api/v1/model-profiles` returns only enabled descriptors in configured order; implicit mock submission succeeds; disabled/unknown profile IDs return sanitized `422`; the selected profile appears in detail; and a submission-key conflict across enabled profiles returns the existing sanitized `409`.

- [ ] **Step 6: Implement the API boundary**

  Resolve `model_profile_id` through `Settings` before calling the repository. Return public descriptors without base URLs or internal settings. Preserve existing database error handling and do not probe provider availability in FastAPI.

- [ ] **Step 7: Run the task regression set**

  Run:

  ```powershell
  .venv\Scripts\alembic.exe upgrade head
  .venv\Scripts\python.exe -m pytest tests/integration/test_migrations.py tests/integration/test_repository.py tests/integration/test_api.py -v
  .venv\Scripts\alembic.exe heads
  ```

  Expected: PASS and exactly `0003_model_profiles_and_usage (head)`.

- [ ] **Step 8: Commit durable profile binding**

  ```powershell
  git add alembic/versions/0003_model_profiles_and_usage.py src/agentforge/db_models.py src/agentforge/domain.py src/agentforge/repository.py src/agentforge/api.py tests/integration/test_migrations.py tests/integration/test_repository.py tests/integration/test_api.py
  git commit -m "feat: persist model profiles and usage"
  ```

### Task 3: Route replay-safe Workflows to profile-bound Workers

**Files:**
- Modify: `src/agentforge/domain.py`
- Modify: `src/agentforge/relay.py`
- Modify: `src/agentforge/activities.py`
- Modify: `src/agentforge/mock_model.py`
- Modify: `src/agentforge/workflow.py`
- Modify: `src/agentforge/worker.py`
- Modify: `tests/unit/test_activities.py`
- Modify: `tests/unit/test_mock_model.py`
- Modify: `tests/integration/test_relay.py`
- Modify: `tests/integration/test_workflow.py`
- Modify: `tests/integration/test_worker_recovery.py`
- Modify: `tests/integration/test_end_to_end.py`

**Interfaces:**
- `WorkflowInput.model_profile: ModelProfileSnapshot = MOCK_MODEL_PROFILE`
- `ModelDecisionInput.model_profile: ModelProfileSnapshot = MOCK_MODEL_PROFILE`
- `DecisionProvider.decide(input: ModelDecisionInput, usage: ModelUsageReporter) -> Awaitable[AgentAction]`
- `ModelUsageReporter.request_started(...)` and `response_completed(...)`; Task 3 supplies a no-op implementation for mock mode
- `AgentActivities(..., model_profile: ModelProfileSnapshot, model: DecisionProvider, ...)`
- Relay uses `workflow_input.model_profile.task_queue`; Worker polls only `settings.worker_model_profile`'s queue.

- [ ] **Step 1: Write failing replay, routing, and mismatch tests**

  Deserialize old Workflow/decision payloads without `model_profile` and assert the exact mock snapshot. Assert the relay ignores mutable `settings.temporal_task_queue` for new profile-bound payloads and starts on the persisted snapshot queue. Assert mock and Ollama Workers can be configured concurrently on separate queues. Pass a mismatched digest, budget, and queue to `decide_action` and assert sanitized non-retryable `ModelProfileMismatch` before the fake provider is called.

- [ ] **Step 2: Run the focused tests to verify RED**

  Run: `.venv\Scripts\python.exe -m pytest tests/unit/test_activities.py tests/unit/test_mock_model.py tests/integration/test_relay.py tests/integration/test_workflow.py -k "profile or legacy or async" -v`

  Expected: FAIL because inputs lack replay defaults and providers are synchronous/unbound.

- [ ] **Step 3: Add replay defaults and snapshot-based routing**

  Add immutable defaults without introducing a Workflow-side settings lookup. Build `WorkflowInput` from the outbox payload and route `TemporalWorkflowStarter.start` using the validated input snapshot. Copy the snapshot into every `ModelDecisionInput`.

- [ ] **Step 4: Make decision providers asynchronous and fail closed**

  Convert `DeterministicMockModel.decide` and test fakes to `async def` without changing mock bytes or action ordering. Await the provider in `AgentActivities`. Compare the complete decision snapshot to the Worker's configured snapshot before calling the provider. Register a no-op usage reporter for mock mode, and keep provider/network state outside Workflow code.

- [ ] **Step 5: Give model decisions their own bounded Activity timeout**

  Add `MODEL_ACTIVITY_TIMEOUT = timedelta(seconds=180)` and apply it only to `decide_action`; retain the existing retry policy and 10-second timeout for trusted local tools and projections. Assert Workflow history still contains actions/references but no prompts, reports, or provider payloads.

- [ ] **Step 6: Run compatibility and recovery regressions**

  Run:

  ```powershell
  .venv\Scripts\python.exe -m pytest tests/unit/test_activities.py tests/unit/test_mock_model.py -v
  .venv\Scripts\python.exe -m pytest tests/integration/test_relay.py tests/integration/test_workflow.py tests/integration/test_worker_recovery.py tests/integration/test_end_to_end.py -v
  ```

  Expected: PASS with unchanged mock report bytes, action order, approval behavior, retries, and restart recovery.

- [ ] **Step 7: Commit profile-bound execution**

  ```powershell
  git add src/agentforge/domain.py src/agentforge/relay.py src/agentforge/activities.py src/agentforge/mock_model.py src/agentforge/workflow.py src/agentforge/worker.py tests/unit/test_activities.py tests/unit/test_mock_model.py tests/integration/test_relay.py tests/integration/test_workflow.py tests/integration/test_worker_recovery.py tests/integration/test_end_to_end.py
  git commit -m "feat: bind workflows to model workers"
  ```

### Task 4: Implement bounded structured Ollama generation

**Files:**
- Create: `src/agentforge/ollama_model.py`
- Modify: `src/agentforge/worker.py`
- Create: `tests/unit/test_ollama_model.py`
- Modify: `tests/unit/test_activities.py`

**Interfaces:**
- `GeneratedReport(report_markdown: str)` strict schema
- `OllamaClient.verify_runtime(expected_version, model_name, model_digest) -> Awaitable[None]`
- `OllamaClient.generate_report(request, usage) -> Awaitable[GeneratedReport]`
- `OllamaDecisionProvider(storage, profile, client).decide(input, usage) -> Awaitable[AgentAction]`
- Sanitized exceptions: `ModelConfigurationError`, `ModelTransportError`, `ModelRequestRejected`, `ModelOutputInvalid`, `ModelBudgetExceeded`

- [ ] **Step 1: Write failing trusted-controller tests**

  Test all six state branches from the design. The only provider call occurs for a verified snapshot with no draft. Assert lexical ticket ordering, atomic run-scoped draft creation, exact trusted references, fixed delivery target, matching receipt checks, profile mismatch rejection, storage verification failure, prompt refusal above 24,000 characters, and no call on any invalid state.

- [ ] **Step 2: Write failing HTTP adapter tests with `httpx.MockTransport`**

  Assert the exact `/api/chat` request: `stream:false`, `think:false`, Pydantic JSON schema, temperature `0`, context `8192`, prediction ceiling `2048`, `keep_alive:"2m"`, and no secret/internal fields. Cover valid output, chunked response up to the byte cap, oversized response, invalid envelope/content JSON, empty/NUL/oversized report, missing goal/path, extra fields, missing usage counts, timeout/disconnect, redirect, `4xx`, `5xx`, and sanitized errors/log records.

- [ ] **Step 3: Run Ollama tests to verify RED**

  Run: `.venv\Scripts\python.exe -m pytest tests/unit/test_ollama_model.py tests/unit/test_activities.py -k ollama -v`

  Expected: FAIL because the adapter and trusted controller do not exist.

- [ ] **Step 4: Implement the bounded client and semantic validation**

  Use `httpx.AsyncClient(trust_env=False, follow_redirects=False)` with 5-second connect, 10-second write/pool, and configured read timeout. Read the body incrementally and abort beyond 131,072 bytes before JSON parsing. Generate the schema from `GeneratedReport`; validate the Ollama envelope, then the strict content object, then the goal/path/NUL/length semantics. Never include invalid content or response text in exceptions.

- [ ] **Step 5: Implement exactly two bounded repairs**

  Permit at most three total provider requests. Each repair repeats the original trusted request and adds only a category from a fixed allowlist such as `invalid_schema`, `missing_goal`, `missing_ticket_path`, or `report_too_long`; never echo the rejected response. Treat transport and `5xx` as Activity-retryable rather than consuming another in-call repair; treat `4xx` and repair exhaustion as permanent.

- [ ] **Step 6: Verify runtime identity before polling**

  In Ollama Worker construction only, call loopback `/api/version`, `/api/tags`, and `/api/show` through the bounded client; require version `0.40.11`, the configured tag, and exact full digest. Complete this check before creating/running the Temporal Worker. A mismatch raises sanitized `ModelConfigurationError` and does not start polling.

- [ ] **Step 7: Run the task regression set**

  Run:

  ```powershell
  .venv\Scripts\python.exe -m pytest tests/unit/test_ollama_model.py tests/unit/test_activities.py tests/unit/test_mock_model.py tests/unit/test_storage.py -v
  .venv\Scripts\python.exe -m pytest tests/integration/test_workflow.py -v
  ```

  Expected: PASS without a running Ollama server.

- [ ] **Step 8: Commit bounded local generation**

  ```powershell
  git add src/agentforge/ollama_model.py src/agentforge/worker.py tests/unit/test_ollama_model.py tests/unit/test_activities.py
  git commit -m "feat: generate bounded local reports"
  ```

### Task 5: Persist exact usage and classify model failures

**Files:**
- Modify: `src/agentforge/domain.py`
- Modify: `src/agentforge/repository.py`
- Modify: `src/agentforge/activities.py`
- Modify: `src/agentforge/ollama_model.py`
- Modify: `src/agentforge/workflow.py`
- Modify: `tests/unit/test_activities.py`
- Modify: `tests/unit/test_ollama_model.py`
- Modify: `tests/integration/test_repository.py`
- Modify: `tests/integration/test_workflow.py`

**Interfaces:**
- `ModelIdentity(provider, model_name, model_digest)`
- `ModelResponseUsage(input_tokens, output_tokens, duration_ms)`
- `ModelUsageRepository.request_started(session, run_id, ordinal, attempt_number, identity, estimated_input_tokens)`
- `ModelUsageRepository.response_completed(session, run_id, ordinal, attempt_number, estimated_input_tokens, exact_usage)`
- `DurableModelUsageReporter` implements the Task 3 reporter against those methods.

- [ ] **Step 1: Write failing durable-usage tests**

  Assert `request_started` atomically increments request count, adds a character-based estimate, sets `input_tokens_estimated=true`, `usage_uncertain=true`, and cost exactly `0`. Assert `response_completed` subtracts that call's estimate, adds exact prompt/output tokens and duration, and clears uncertainty only after a complete response. Cover two repairs, missing provider token fields, crash/transport after start, a later Temporal attempt, stale lower-attempt writes, and concurrent-safe row locking.

- [ ] **Step 2: Run usage tests to verify RED**

  Run: `.venv\Scripts\python.exe -m pytest tests/integration/test_repository.py tests/unit/test_ollama_model.py -k usage -v`

  Expected: FAIL because durable usage methods are not wired.

- [ ] **Step 3: Implement the durable usage reporter**

  Key updates by the existing deterministic step/attempt IDs. Lock the attempt row, preserve completed prior-call totals, and reject identity changes within an attempt. Use a documented conservative character-based estimate only until an exact response arrives. Keep unknown token counts nullable rather than coercing them to zero. Never write content fields.

- [ ] **Step 4: Wire usage around every provider call**

  Construct the reporter from `activity.info().attempt`. Commit `request_started` immediately before HTTP and `response_completed` only after a full valid Ollama envelope supplies provider usage. Ensure later `_record` state transitions do not erase usage columns. Mock attempts remain entirely null.

- [ ] **Step 5: Write failing failure-classification tests**

  Assert timeout/disconnect/`5xx` retries up to the existing Activity limit and leaves ambiguous usage; `4xx`, invalid configuration, and repair exhaustion end `FAILED/PERMANENT`; prompt/report budget ends `BUDGET_EXHAUSTED`; storage violations remain permanent; and no case silently switches to mock. Assert only sanitized exception class/type reaches logs and Temporal failure data.

- [ ] **Step 6: Implement Workflow classification**

  Raise a non-retryable `ApplicationError(type="ModelBudgetExceeded")` for bounded-input/output refusal and handle that exact cause around `decide_action` by projecting `RunState.BUDGET_EXHAUSTED`. Map the other model exceptions in `AgentActivities` to retryable or permanent outcomes from the spec. Preserve existing delivery uncertainty handling.

- [ ] **Step 7: Run the task regression set**

  Run:

  ```powershell
  .venv\Scripts\python.exe -m pytest tests/unit/test_activities.py tests/unit/test_ollama_model.py -v
  .venv\Scripts\python.exe -m pytest tests/integration/test_repository.py tests/integration/test_workflow.py tests/integration/test_worker_recovery.py -v
  ```

  Expected: PASS; attempt usage and Workflow terminal states match the design.

- [ ] **Step 8: Commit usage and failure semantics**

  ```powershell
  git add src/agentforge/domain.py src/agentforge/repository.py src/agentforge/activities.py src/agentforge/ollama_model.py src/agentforge/workflow.py tests/unit/test_activities.py tests/unit/test_ollama_model.py tests/integration/test_repository.py tests/integration/test_workflow.py
  git commit -m "feat: record local model usage"
  ```

### Task 6: Expose profile selection and usage in the dashboard

**Files:**
- Modify: `dashboard/lib/contracts.ts`
- Modify: `dashboard/lib/api.ts`
- Modify: `dashboard/components/run-dashboard.tsx`
- Modify: `dashboard/components/run-form.tsx`
- Modify: `dashboard/components/run-context.tsx`
- Modify: `dashboard/components/run-timeline.tsx`
- Modify: `dashboard/app/globals.css`
- Modify: `dashboard/e2e/backend-fixture.ts`
- Modify: `dashboard/e2e/submission.spec.ts`
- Modify: `dashboard/e2e/run-timeline.spec.ts`
- Modify: `dashboard/e2e/run-actions.spec.ts`

**Interfaces:**
- `listModelProfiles(signal?) -> Promise<ModelProfileDescriptor[]>`
- `SubmitRunInput.model_profile_id`
- Run detail includes immutable `model_profile`; attempts include nullable usage fields.
- The form selects `mock-v1` by default and renders only API-enabled profiles.

- [ ] **Step 1: Read the installed Next.js guidance**

  Before editing dashboard code, inspect the relevant local guides under `dashboard/node_modules/next/dist/docs/` for client data fetching and forms, as required by `dashboard/AGENTS.md`. Record no dependency change.

- [ ] **Step 2: Write failing profile-selection browser tests**

  Mock the profile endpoint with mock-only and mock-plus-Ollama responses. Assert default mock selection, local-Qwen visibility only when enabled, explicit selected ID in POST JSON, a disabled-profile refresh that returns to mock, loading/error behavior that does not submit an unlisted ID, keyboard labeling, and responsive layout.

- [ ] **Step 3: Run selection tests to verify RED**

  Run: `Set-Location dashboard; npm run test:e2e -- e2e/submission.spec.ts`

  Expected: FAIL because the dashboard does not load or submit profiles.

- [ ] **Step 4: Implement strict client parsing and the selector**

  Validate every descriptor and all new detail/usage fields at the API boundary. Load profiles without overlapping requests, default to `mock-v1`, and send only the selected profile ID. Show provider/model context before submission without exposing the Ollama URL or budgets as editable controls.

- [ ] **Step 5: Write failing provenance and usage tests**

  Assert the run context shows immutable profile ID, provider, model name, and a short digest. Assert a complete usage row shows request count, exact or estimated token labels, duration, and `Provider charge estimate: $0.000000`; interrupted usage shows the neutral incomplete notice; mock/null usage renders no usage row. Add sentinel prompt/response keys to the fixture and assert the UI never renders them.

- [ ] **Step 6: Implement the minimal usage UI**

  Extend the existing timeline rather than adding a new state source or chart. Keep uncertainty visually neutral and distinct from attempt failure. Format nullable counts as unknown, not zero, and explain that provider charge excludes hardware/electricity.

- [ ] **Step 7: Run dashboard checks**

  Run:

  ```powershell
  Set-Location dashboard
  npm run test:e2e -- e2e/submission.spec.ts e2e/run-timeline.spec.ts e2e/run-actions.spec.ts
  npm run typecheck
  npm run lint
  npm run build
  npm audit --audit-level=high
  ```

  Expected: PASS with no dependency or lockfile change.

- [ ] **Step 8: Commit the model dashboard**

  ```powershell
  git add dashboard/lib/contracts.ts dashboard/lib/api.ts dashboard/components/run-dashboard.tsx dashboard/components/run-form.tsx dashboard/components/run-context.tsx dashboard/components/run-timeline.tsx dashboard/app/globals.css dashboard/e2e/backend-fixture.ts dashboard/e2e/submission.spec.ts dashboard/e2e/run-timeline.spec.ts dashboard/e2e/run-actions.spec.ts
  git commit -m "feat: show model profiles and usage"
  ```

### Task 7: Install the pinned native runtime and prove one real model run

**Files:**
- Create: `tests/helpers/ollama_runtime.ps1`
- Create: `tests/helpers/model_dashboard_stack.ps1`
- Create: `dashboard/e2e/real-model.spec.ts`
- Modify: `dashboard/playwright.real.config.ts`
- Modify: `.gitignore`

**Interfaces:**
- `ollama_runtime.ps1` verifies/installs/starts/stops only harness-owned Ollama and prints the verified full model digest.
- `model_dashboard_stack.ps1` starts API, relay, inbox, dashboard, mock Worker, and Ollama-profile Worker with explicit profile environments, then runs the opt-in Playwright journey.
- Real test runs only when `AGENTFORGE_REAL_MODEL_TEST=1` and receives the verified digest through the harness environment.

- [ ] **Step 1: Write harness contract tests/checks before installation**

  Add PowerShell functions with injectable process/HTTP/download boundaries and a `-ValidateOnly` mode. Prove they reject a wrong archive hash, non-loopback listener, wrong Ollama version, missing/wrong model digest, and an occupied incompatible port without stopping its owner. Prove cleanup targets only PIDs created by the current harness.

- [ ] **Step 2: Implement the pinned installation workflow**

  Download the official `ollama-windows-amd64.zip` for v0.40.11 to a temporary directory, compute SHA-256, and extract only after it equals the approved digest. Install under `%LOCALAPPDATA%\Programs\ollama-v0.40.11-windows-amd64`; set `OLLAMA_MODELS=%LOCALAPPDATA%\AgentForge\ollama-models` and `OLLAMA_HOST=127.0.0.1:11434` only for harness-owned processes. Do not edit machine-wide PATH or stop an existing listener.

- [ ] **Step 3: Start safely, pull Qwen, and record exact identity**

  If port 11434 is free, start `ollama serve` hidden and track its PID. Pull only `qwen3:4b-instruct-2507-q4_K_M`, query `/api/tags` and `/api/show`, record the full digest, and run one bounded schema smoke request with synthetic text only. If a listener exists, reuse it only when version `0.40.11` and the exact required digest match; otherwise abort without mutation.

- [ ] **Step 4: Write the opt-in real browser journey**

  Select the local-Qwen profile, submit the fixed sample tickets, wait for the existing approval boundary, approve the exact displayed action hash/artifact, and assert one terminal `SUCCEEDED` run, one inbox delivery, verified report goal/path coverage, positive exact input/output tokens, request count between one and three, duration, zero provider charge, and no uncertainty after success.

- [ ] **Step 5: Add privacy and exactly-once proof**

  Query PostgreSQL, Temporal history, and captured process logs for a unique ticket sentinel and report prefix. Assert content is absent everywhere except the approved artifact/receiver body. Assert exactly one run, one approval, one AgentForge delivery record, and one receiver delivery for the operation.

- [ ] **Step 6: Execute the real harness**

  Run: `powershell -ExecutionPolicy Bypass -File tests/helpers/model_dashboard_stack.ps1`

  Expected: PASS on the observed 32-GB/GTX-1650 machine. Record Ollama version, archive checksum, exact model tag/digest, elapsed generation evidence, test counts, and process ownership. A model-quality failure must remain bounded to at most two repairs.

- [ ] **Step 7: Prove cleanup**

  In `finally`, stop API/relay/inbox/dashboard/Workers and only the harness-owned Ollama PID. Assert ports 3000, 8000, 8001, and—when the harness started Ollama—11434 are clear. Preserve logs only on failure and ensure they contain no ticket/report content.

- [ ] **Step 8: Commit the operational proof**

  ```powershell
  git add .gitignore tests/helpers/ollama_runtime.ps1 tests/helpers/model_dashboard_stack.ps1 dashboard/e2e/real-model.spec.ts dashboard/playwright.real.config.ts
  git commit -m "test: prove native local model flow"
  ```

### Task 8: Document, verify, and checkpoint Milestone 5

**Files:**
- Modify: `README.md`
- Modify: `ARCHITECTURE.md`
- Modify: `FLOW.md`
- Modify: `DECISIONS.md`
- Modify: `TEST_CHECKLIST.md`
- Modify: `docs/learning/README.md`
- Create: `docs/learning/08_MILESTONE_5_LOCAL_MODEL.md`
- Modify: `PROJECT_PLAN.md`
- Replace: `HANDOVER.md`

- [ ] **Step 1: Document the actual implementation and operating boundary**

  Explain mock-default startup, opt-in Ollama startup/stop, profile immutability, dedicated task queues, structured output and repair, quantization/context/tokens, provider charge versus electricity/hardware, failure classifications, privacy boundaries, and exact recovery/cleanup commands. Record only versions, digest, paths, and checks actually observed.

- [ ] **Step 2: Run the full deterministic verification suite**

  Run:

  ```powershell
  .venv\Scripts\python.exe -m pytest -v
  .venv\Scripts\python.exe -m pip check
  .venv\Scripts\alembic.exe heads
  .venv\Scripts\alembic.exe upgrade head
  .venv\Scripts\python.exe -m pytest tests/integration/test_migrations.py -v
  Set-Location dashboard
  npm ci
  npm audit --audit-level=high
  npm run typecheck
  npm run lint
  npm run build
  npm run test:e2e
  ```

  Expected: every prior mock-path test plus the new deterministic coverage passes; npm reports no high-or-critical production vulnerability.

- [ ] **Step 3: Re-run the opt-in real proof once from a clean application state**

  Run: `powershell -ExecutionPolicy Bypass -File tests/helpers/model_dashboard_stack.ps1`

  Expected: one successful Qwen run through approval and exactly-one delivery, followed by verified cleanup.

- [ ] **Step 4: Audit privacy and scope**

  Run `git diff --check`, inspect `git status --short`, search tracked files for credentials, temporary downloads, model blobs, prompts/responses, ticket sentinels, and generated logs, and confirm no ports remain unexpectedly listening. Confirm the repository contains no Ollama archive or model data.

- [ ] **Step 5: Update the checkpoint**

  Check only completed Milestone 5 boxes in `PROJECT_PLAN.md`. Replace `HANDOVER.md` with the exact commits, changed files, observed checks (including failures), verified runtime/model identities, cleanup state, unresolved risks, and the exact next action. Do not mark Milestone 5 approved until the user reviews the implementation.

- [ ] **Step 6: Commit documentation and checkpoint evidence**

  ```powershell
  git add README.md ARCHITECTURE.md FLOW.md DECISIONS.md TEST_CHECKLIST.md docs/learning/README.md docs/learning/08_MILESTONE_5_LOCAL_MODEL.md PROJECT_PLAN.md HANDOVER.md
  git commit -m "docs: record local model milestone"
  ```

- [ ] **Step 7: Request independent code review and address findings**

  Use `superpowers:requesting-code-review` against the complete Milestone 5 commit range. Treat review findings with `superpowers:receiving-code-review`, rerun proportionate checks after any fix, and update the checkpoint with the final reviewed commit and evidence.

- [ ] **Step 8: Present Milestone 5 for user approval**

  Report the immutable profile/digest, real-run outcome, exact usage evidence, deterministic suite results, cleanup state, and remaining local-model limitations. Stop before any later milestone or deployment work.
