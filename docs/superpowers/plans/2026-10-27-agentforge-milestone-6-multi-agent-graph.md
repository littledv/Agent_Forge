# AgentForge Milestone 6 Bounded Multi-Agent Graph Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a versioned, support-report-specific researcher → writer → reviewer graph with fixed fan-out, concurrency, revision, role-task, request, and elapsed-time bounds while preserving version-1 replay and the existing human approval/delivery guarantees.

**Architecture:** New runs use a version-2 Temporal Workflow and strict graph snapshot; legacy start payloads and `SupportReportWorkflow` remain version 1. Typed role Activities keep ticket-derived bodies in verified content-addressed stage files and put references only in Temporal history. All roles use the run's immutable model-profile queue, while PostgreSQL remains the application-record authority and Temporal remains the only execution scheduler.

**Tech Stack:** Python 3.13.17, Pydantic 2.14.1, httpx 0.28.1, FastAPI 0.158.1, SQLAlchemy 2.1.6, Alembic 1.18.4, PostgreSQL 16.15, Temporal Python SDK 1.33.0, pytest 9.0.2, native Windows Ollama 0.40.11 with `qwen3:4b-instruct-2507-q4_K_M`, Next.js 16.4.3, React 19.3.1, TypeScript 6.0.3, and Playwright 1.64.1.

**Spec:** `docs/superpowers/specs/2026-10-27-agentforge-milestone-6-multi-agent-graph-design.md`

**Status:** Tasks 1–10 implemented, independently reviewed, and fully verified
on 2026-10-27. The Milestone 6 checkpoint was user-approved on 2026-10-27.

## Global Constraints

- Work only in the existing managed worktree and use Windows PowerShell unless a command is explicitly identified as WSL-only.
- Add no dependency, service, container image, cloud provider, credential, remote, paid resource, or public port.
- Do not modify the deterministic command sequence of `SupportReportWorkflow`; register `MultiAgentSupportReportWorkflow` separately and replay saved version-1 histories.
- Keep Temporal as execution-history and scheduling authority and PostgreSQL as application-record/projection authority; do not add a graph scheduler table or process.
- Keep `mock-v1` deterministic and credential-free by default; Ollama remains opt-in, pinned, loopback-only, and fail-closed with no mock fallback.
- Enforce version-2 limits exactly: 16 ticket files, 4 research shards, 2 concurrent research Activities, 2 writer revisions, 10 role tasks, 900 seconds of autonomous pre-approval work, 4,096 research-note characters, and 2,048 review-feedback characters.
- Preserve model-profile request ceilings: 24,000 prompt characters, 2,048 output tokens, at most 2 semantic repairs, and 3 Temporal Activity attempts; the structural maximum is 90 provider requests.
- Never put ticket text, research-note bodies, draft bodies, review-feedback bodies, prompts, raw provider responses, or hidden reasoning in Temporal history, PostgreSQL projections, API payloads, or ordinary logs.
- Reviewer acceptance is content review only. Exact human approval remains required before the existing stable operation ID can reach the simulated inbox.
- Use TDD for every product change: observe the focused test fail for the intended reason before implementing, then run the focused and relevant regression sets before committing.
- Preserve the user's Git identity and never add Codex/OpenAI/generated-by authorship or project-page credit.

## File Structure

- Create `src/agentforge/multi_agent.py` for strict history-safe graph limits, references, bounded verdict metadata, Workflow input, partitioning, ordinals, and structural budget helpers.
- Extend `src/agentforge/storage.py` only with immutable content-addressed stage storage; retain all version-1 methods unchanged.
- Create `src/agentforge/role_runtime.py` for ticket-derived role bodies, Activity-local role requests, and the provider protocol; these types never cross the Temporal boundary.
- Extend `src/agentforge/mock_model.py` with deterministic role methods so one mock object supports version-1 decisions and version-2 roles.
- Refactor only the common structured-call core in `src/agentforge/ollama_model.py`; create `src/agentforge/ollama_roles.py` for role prompts, validation, and bounded repair.
- Create `src/agentforge/role_activities.py` for role Activities, cancellation lookup, usage recording, validation, and stage writes.
- Create `src/agentforge/multi_agent_workflow.py` for the new graph and duplicated replay-safe approval/delivery coordination; do not refactor version-1 Workflow commands.
- Modify repository/API/relay/worker code only where version selection, durable start input, usage allowlisting, and registration require it.
- Modify `dashboard/components/run-timeline.tsx` only for role labels; keep the dashboard a projection client.
- Create focused unit/integration tests beside existing suites and one sanitized version-1 Temporal history fixture for replay.

## Review Focus

- An idempotent retry of a pre-version-2 submission key, including a legacy run with more than 16 files, must return that original run; only a new version-2 submission is subject to the 16-file ceiling. Task 3 adds this repository/API regression.
- A late or retried model Activity must never overwrite bytes referenced by a completed history event. Task 2 adds different-content, same-stage immutable reference tests.
- Cancellation or the 900-second deadline during an in-flight research batch must allow only that bounded batch to settle and must schedule no later role, publication, approval, or delivery. Task 7 adds both paths.
- Research completion order must not change stable ordinals, shard order passed to the writer, or the final report. Tasks 1 and 7 add out-of-order completion regressions.
- Missing or ambiguous provider usage must stay uncertain and must not grant another node, repair, Activity attempt, or larger request. Tasks 5 and 6 test the request bounds and durable evidence independently.

---

### Task 1: Define strict graph contracts, bounds, partitioning, and ordinals

**Files:**
- Create: `src/agentforge/multi_agent.py`
- Create: `tests/unit/test_multi_agent.py`
- Test: `tests/unit/test_domain.py`

**Interfaces:**
- Consumes: `StrictModel`, `FileReference`, `Goal`, `ModelProfileSnapshot`, `TicketSnapshotRef`, and existing safe relative-path validation from `agentforge.domain`.
- Produces: `WorkflowKind`, `TaskGraphLimits`, `SUPPORT_REPORT_GRAPH_V2`, `ResearchNoteRef`, `ReviewFeedbackRef`, `ReviewDecision`, `ReviewResult`, `MultiAgentWorkflowInput`, `partition_ticket_paths(...)`, `research_ordinal(...)`, `writer_ordinal(...)`, `reviewer_ordinal(...)`, and `structural_provider_request_limit(...)`.

- [x] **Step 1: Write failing strict-contract and partition tests**

  Add tests named:

  ```python
  def test_graph_v2_limits_are_exact_strict_and_frozen(): ...
  def test_partition_ticket_paths_is_sorted_round_robin_and_bounded(): ...
  def test_role_ordinals_are_stable_for_missing_and_out_of_order_shards(): ...
  def test_review_result_requires_feedback_only_for_revise(): ...
  def test_structural_request_limit_is_ninety_for_approved_profile(): ...
  def test_multi_agent_workflow_input_rejects_seventeenth_ticket(): ...
  ```

  Assert the exact spec values and partition `("d", "b", "a", "c", "e")` into `(("a", "e"), ("b",), ("c",), ("d",))`. Assert fixed ordinals: read `0`, research `1..4`, writer `5/7/9`, reviewer `6/8/10`, publication `11`, approval `12`, pre-delivery gate `13`, delivery `14`, post-delivery gate `15`.

- [x] **Step 2: Run the focused tests and verify RED**

  Run:

  ```powershell
  .\.venv\Scripts\python.exe -m pytest tests/unit/test_multi_agent.py tests/unit/test_domain.py -q
  ```

  Expected: FAIL because the version-2 graph contracts and helpers do not exist.

- [x] **Step 3: Implement the minimal strict graph module**

  Implement:

  ```python
  WorkflowKind = Literal["support-report-v1", "support-report-v2"]

  class TaskGraphLimits(StrictModel):
      graph_version: Literal[2]
      max_ticket_files: PositiveInt
      max_research_tasks: PositiveInt
      max_concurrent_research: PositiveInt
      max_revisions: NonNegativeInt
      max_role_tasks: PositiveInt
      max_elapsed_seconds: PositiveInt
      max_research_note_chars: PositiveInt
      max_review_feedback_chars: PositiveInt

  def partition_ticket_paths(
      ticket_files: tuple[str, ...], limits: TaskGraphLimits
  ) -> tuple[tuple[str, ...], ...]: ...

  def structural_provider_request_limit(
      limits: TaskGraphLimits, *, activity_attempts: int, max_repairs: int
  ) -> int: ...
  ```

  Use a frozen constant `SUPPORT_REPORT_GRAPH_V2` with the exact approved values. Keep `MultiAgentWorkflowInput` history-safe: IDs, goal, paths, immutable model profile, limits, and references only—never content bodies.

- [x] **Step 4: Run focused and domain regression tests**

  Run:

  ```powershell
  .\.venv\Scripts\python.exe -m pytest tests/unit/test_multi_agent.py tests/unit/test_domain.py tests/unit/test_model_profiles.py -q
  ```

  Expected: PASS.

- [x] **Step 5: Commit Task 1**

  ```powershell
  git add src/agentforge/multi_agent.py tests/unit/test_multi_agent.py
  git commit -m "feat: define bounded multi-agent graph"
  ```

### Task 2: Add immutable content-addressed role storage

**Files:**
- Modify: `src/agentforge/storage.py:19-164`
- Modify: `tests/unit/test_storage.py`

**Interfaces:**
- Consumes: `ResearchNoteRef`, `ReviewFeedbackRef`, `DraftRef`, `ArtifactRef`, and exact character ceilings from Task 1.
- Produces: `ArtifactStorage.write_research_note(...)`, `read_research_note(...)`, `write_revision_draft(...)`, `read_revision_draft(...)`, `write_review_feedback(...)`, `read_review_feedback(...)`, and `publish_reviewed_report(...)`.

- [x] **Step 1: Write failing immutable-stage tests**

  Add:

  ```python
  def test_role_stages_use_content_hash_paths_and_round_trip_exact_bytes(): ...
  def test_late_different_content_cannot_overwrite_completed_stage_reference(): ...
  def test_stage_reads_reject_wrong_run_kind_index_hash_size_and_traversal(): ...
  def test_stage_writes_enforce_utf8_and_character_ceilings_without_partial_files(): ...
  def test_publish_reviewed_report_copies_only_exact_reviewed_revision(): ...
  def test_version_one_draft_and_publication_paths_are_unchanged(): ...
  ```

  For the stale-attempt case, write two different bodies for the same run/shard, retain the first reference, and prove both content-addressed files remain exact and the first bytes cannot change.

- [x] **Step 2: Run storage tests and verify RED**

  ```powershell
  .\.venv\Scripts\python.exe -m pytest tests/unit/test_storage.py -q
  ```

  Expected: FAIL because role-stage methods do not exist.

- [x] **Step 3: Implement create-once content-addressed stages**

  Add the exact public signatures:

  ```python
  def write_research_note(
      self, run_id: UUID, shard_index: int, content: bytes, *, max_chars: int
  ) -> ResearchNoteRef: ...
  def read_research_note(self, run_id: UUID, ref: ResearchNoteRef) -> bytes: ...
  def write_revision_draft(
      self, run_id: UUID, revision: int, content: bytes, *, max_chars: int
  ) -> DraftRef: ...
  def read_revision_draft(self, run_id: UUID, ref: DraftRef) -> bytes: ...
  def write_review_feedback(
      self, run_id: UUID, round_number: int, content: bytes, *, max_chars: int
  ) -> ReviewFeedbackRef: ...
  def read_review_feedback(
      self, run_id: UUID, ref: ReviewFeedbackRef
  ) -> bytes: ...
  def publish_reviewed_report(self, run_id: UUID, draft: DraftRef) -> ArtifactRef: ...
  ```

  Paths must be exactly those in the design. Implement a private create-once helper that atomically writes a hash-addressed file and accepts an existing destination only when its bytes are identical. Do not alter version-1 `write_draft` or `publish_report` behavior.

- [x] **Step 4: Run storage and domain tests**

  ```powershell
  .\.venv\Scripts\python.exe -m pytest tests/unit/test_storage.py tests/unit/test_multi_agent.py tests/unit/test_domain.py -q
  ```

  Expected: PASS.

- [x] **Step 5: Commit Task 2**

  ```powershell
  git add src/agentforge/storage.py tests/unit/test_storage.py
  git commit -m "feat: store immutable role outputs"
  ```

### Task 3: Persist agent version 2 and route durable starts without breaking idempotency

**Files:**
- Create: `alembic/versions/0005_support_report_v2.py`
- Modify: `src/agentforge/repository.py:126-258`
- Modify: `src/agentforge/api.py:74-130`
- Modify: `src/agentforge/relay.py:28-153`
- Modify: `tests/integration/test_migrations.py`
- Modify: `tests/integration/test_repository.py`
- Modify: `tests/integration/test_api.py`
- Modify: `tests/integration/test_relay.py`

**Interfaces:**
- Consumes: `WorkflowKind`, `TaskGraphLimits`, `SUPPORT_REPORT_GRAPH_V2`, `MultiAgentWorkflowInput`, legacy `WorkflowInput`, and immutable model profiles.
- Produces: version-2 seed UUID `00000000-0000-0000-0000-000000000103`, `GraphLimitExceeded`, version-aware `RunRepository.submit(...)`, `ResolvedWorkflowStart`, and `_resolve_workflow_start(...)`.

- [x] **Step 1: Write failing migration, idempotency, API, and relay tests**

  Add tests that assert:

  ```python
  def test_head_seeds_support_report_versions_one_and_two(): ...
  async def test_new_v2_submission_persists_exact_workflow_kind_and_limits(): ...
  async def test_old_key_retry_returns_legacy_run_even_above_v2_ticket_limit(): ...
  async def test_new_seventeen_file_submission_is_rejected_without_rows(): ...
  async def test_api_selects_agent_version_two_and_maps_graph_limit_to_422(): ...
  async def test_legacy_outbox_defaults_to_v1_and_process_max_decisions(): ...
  async def test_v2_outbox_uses_stored_limits_and_multi_agent_workflow_type(): ...
  async def test_invalid_or_mixed_workflow_kind_payload_fails_closed(): ...
  ```

  Preserve the existing concurrent equal-submission test and assert the request hash remains client/profile-based so server-selected version changes do not break an old idempotency key.

- [x] **Step 2: Run focused integration tests and verify RED**

  ```powershell
  .\.venv\Scripts\python.exe -m pytest tests/integration/test_migrations.py tests/integration/test_repository.py tests/integration/test_api.py tests/integration/test_relay.py -q
  ```

  Expected: FAIL because version 2 and durable workflow-kind routing do not exist.

- [x] **Step 3: Add migration and version-aware submission**

  Migration `0005_support_report_v2` inserts version `2` for the existing definition with UUID `00000000-0000-0000-0000-000000000103`; downgrade deletes only that version row.

  Change the repository signature to:

  ```python
  async def submit(
      session: AsyncSession,
      request: SubmitRun,
      agent_version_id: UUID,
      model_profile: ModelProfileSnapshot = MOCK_MODEL_PROFILE,
      *,
      workflow_kind: WorkflowKind = "support-report-v1",
      graph_limits: TaskGraphLimits | None = None,
  ) -> SubmissionResult: ...
  ```

  First resolve an existing submission key and preserve its version when request/profile match. Apply the 16-file check only before creating a new v2 run. Store `workflow_kind` and exact `graph_limits` only in new v2 start payloads. Select agent version 2 in the API and map `GraphLimitExceeded` to sanitized `422`.

- [x] **Step 4: Resolve v1/v2 starts from durable payloads**

  Implement:

  ```python
  @dataclass(frozen=True)
  class ResolvedWorkflowStart:
      workflow_type: str
      task_queue: str
      input: WorkflowInput | MultiAgentWorkflowInput

  def _resolve_workflow_start(
      item: ClaimedStart, settings: Settings
  ) -> ResolvedWorkflowStart: ...
  ```

  An absent `workflow_kind` means v1 and receives the existing process `max_decisions` default. A v2 payload must contain the exact stored `TaskGraphLimits`; never rebuild it from mutable settings. Extend `WorkflowStarter.start(...)` with `workflow_type` and pass it directly to `Client.start_workflow(...)`.

- [x] **Step 5: Run migration/repository/API/relay regression tests**

  ```powershell
  .\.venv\Scripts\python.exe -m pytest tests/integration/test_migrations.py tests/integration/test_repository.py tests/integration/test_api.py tests/integration/test_relay.py -q
  ```

  Expected: PASS.

- [x] **Step 6: Commit Task 3**

  ```powershell
  git add alembic/versions/0005_support_report_v2.py src/agentforge/repository.py src/agentforge/api.py src/agentforge/relay.py tests/integration/test_migrations.py tests/integration/test_repository.py tests/integration/test_api.py tests/integration/test_relay.py
  git commit -m "feat: route versioned support report graphs"
  ```

### Task 4: Implement Activity-local role contracts and deterministic mock roles

**Files:**
- Create: `src/agentforge/role_runtime.py`
- Create: `tests/unit/test_role_runtime.py`
- Modify: `src/agentforge/mock_model.py`
- Modify: `tests/unit/test_mock_model.py`

**Interfaces:**
- Consumes: Task 1 bodies/references, `ModelUsageReporter`, immutable model profile, and verified strings loaded inside Activities.
- Produces: `VerifiedTicket`, `ResearchFinding`, `ResearchNoteBody`, `LoadedResearchNote`, `ReportBody`, `ReviewCategory`, `ReviewFeedbackItem`, `ReviewFeedbackBody`, `ResearchRoleRequest`, `WriterRoleRequest`, `ReviewerRoleRequest`, `ReviewBody`, `RoleProvider`, and deterministic `research(...)`, `write(...)`, and `review(...)` methods on `DeterministicMockModel`.

- [x] **Step 1: Write failing provider-contract and deterministic-role tests**

  Add:

  ```python
  def test_activity_local_role_requests_are_strict_and_never_reference_paths_outside_inputs(): ...
  def test_research_note_requires_exact_unique_assigned_path_coverage(): ...
  async def test_mock_research_emits_one_stable_finding_per_assigned_ticket(): ...
  async def test_mock_writer_is_stable_and_covers_goal_and_every_ticket_path(): ...
  async def test_mock_writer_applies_bounded_feedback_on_revision(): ...
  async def test_mock_reviewer_accepts_valid_report_and_revises_invalid_report(): ...
  async def test_mock_roles_never_record_provider_usage(): ...
  ```

  Assert role requests may contain ticket-derived bodies because they remain inside an Activity, while `MultiAgentWorkflowInput` and role Activity inputs may not.

- [x] **Step 2: Run focused tests and verify RED**

  ```powershell
  .\.venv\Scripts\python.exe -m pytest tests/unit/test_role_runtime.py tests/unit/test_mock_model.py -q
  ```

  Expected: FAIL because role runtime types and methods do not exist.

- [x] **Step 3: Implement the role protocol and deterministic mock**

  Define:

  ```python
  class RoleProvider(Protocol):
      async def research(
          self, request: ResearchRoleRequest, usage: ModelUsageReporter
      ) -> ResearchNoteBody: ...
      async def write(
          self, request: WriterRoleRequest, usage: ModelUsageReporter
      ) -> ReportBody: ...
      async def review(
          self, request: ReviewerRoleRequest, usage: ModelUsageReporter
      ) -> ReviewBody: ...
  ```

  Make local request models strict/frozen and enforce 512-character finding fields, exact assigned paths, exact ordered notes, revision number, and accepted review shape. Extend the existing mock object without changing version-1 `decide(...)` output.

- [x] **Step 4: Run mock, role, and version-1 model regressions**

  ```powershell
  .\.venv\Scripts\python.exe -m pytest tests/unit/test_role_runtime.py tests/unit/test_mock_model.py tests/unit/test_model_profiles.py -q
  ```

  Expected: PASS.

- [x] **Step 5: Commit Task 4**

  ```powershell
  git add src/agentforge/role_runtime.py src/agentforge/mock_model.py tests/unit/test_role_runtime.py tests/unit/test_mock_model.py
  git commit -m "feat: add deterministic report roles"
  ```

### Task 5: Add bounded Ollama researcher, writer, and reviewer generation

**Files:**
- Create: `src/agentforge/ollama_roles.py`
- Create: `tests/unit/test_ollama_roles.py`
- Modify: `src/agentforge/ollama_model.py:79-285`
- Modify: `tests/unit/test_ollama_model.py`

**Interfaces:**
- Consumes: `RoleProvider` requests/results, existing `OllamaClient`, immutable profile bounds, full-digest revalidation, and `ModelUsageReporter`.
- Produces: generic `OllamaClient.generate_structured(...)` used by the existing report path and new `OllamaRoleProvider.research(...)`, `write(...)`, and `review(...)`.

- [x] **Step 1: Write failing role-schema, prompt, repair, and uncertainty tests**

  Add tests named:

  ```python
  async def test_research_prompt_contains_only_goal_assigned_tickets_and_strict_schema(): ...
  async def test_writer_prompt_uses_ordered_notes_and_bounded_revision_feedback(): ...
  async def test_reviewer_prompt_cannot_authorize_delivery_or_add_tasks(): ...
  async def test_role_validation_rejects_missing_duplicate_and_outside_paths(): ...
  async def test_each_role_allows_two_sanitized_repairs_then_stops(): ...
  async def test_missing_usage_stays_uncertain_and_does_not_expand_request_count(): ...
  async def test_role_errors_never_include_ticket_note_draft_feedback_or_raw_response(): ...
  async def test_version_one_generate_report_request_is_byte_equivalent_after_refactor(): ...
  ```

  Include a mutable-tag regression for every generic structured call, not only version-1 report generation.

- [x] **Step 2: Run Ollama unit tests and verify RED**

  ```powershell
  .\.venv\Scripts\python.exe -m pytest tests/unit/test_ollama_roles.py tests/unit/test_ollama_model.py -q
  ```

  Expected: FAIL because generic structured generation and role prompts do not exist.

- [x] **Step 3: Extract the exact common structured-call boundary**

  Add a typed method whose public behavior is:

  ```python
  async def generate_structured(
      self,
      *,
      system_message: str,
      user_document: dict[str, object],
      output_type: type[StrictModelT],
      usage: ModelUsageReporter,
  ) -> StrictModelT: ...
  ```

  It must apply the existing prompt-character limit, full digest check immediately before `/api/chat`, bounded response bytes, exact schema, token/duration evidence, no environment proxy, and sanitized error classes. Reimplement `generate_report(...)` as a compatibility wrapper and prove its payload contract unchanged.

- [x] **Step 4: Implement role-specific trusted validation and repairs**

  `OllamaRoleProvider` builds compact sorted JSON documents, treats all supplied bodies as untrusted data, and uses only the five approved review categories. Each method validates semantics after strict schema parsing and allows at most `profile.max_repairs`; repair prompts include a trusted category only, never invalid raw content. Output/prompt exhaustion raises `ModelBudgetExceeded`; transport stays retryable; schema/semantic exhaustion stays permanent.

- [x] **Step 5: Run Ollama, role-runtime, and mock regressions**

  ```powershell
  .\.venv\Scripts\python.exe -m pytest tests/unit/test_ollama_roles.py tests/unit/test_ollama_model.py tests/unit/test_role_runtime.py tests/unit/test_mock_model.py -q
  ```

  Expected: PASS.

- [x] **Step 6: Commit Task 5**

  ```powershell
  git add src/agentforge/ollama_roles.py src/agentforge/ollama_model.py tests/unit/test_ollama_roles.py tests/unit/test_ollama_model.py
  git commit -m "feat: generate bounded multi-agent outputs"
  ```

### Task 6: Execute role Activities with durable attempts, usage, cancellation, and immutable outputs

**Files:**
- Create: `src/agentforge/role_activities.py`
- Create: `tests/unit/test_role_activities.py`
- Modify: `src/agentforge/repository.py:980-1137`
- Modify: `tests/integration/test_repository.py`
- Modify: `tests/unit/test_activities.py`

**Interfaces:**
- Consumes: Task 1 graph/ref types, Task 2 storage, Task 4 `RoleProvider`, existing `DurableModelUsageReporter`, `record_activity_attempt(...)`, and cancellation repository.
- Produces: `ResearchActivityInput`, `WriteReportActivityInput`, `ReviewReportActivityInput`, `PublishReviewedReportInput`, `CheckCancellationInput`, and `RoleActivities` methods named `research_role`, `write_report_role`, `review_report_role`, `publish_reviewed_report`, and `check_cancellation`.

- [x] **Step 1: Write failing Activity and usage-allowlist tests**

  Add:

  ```python
  async def test_research_activity_reads_only_assigned_snapshot_paths_and_writes_ref(): ...
  async def test_writer_and_reviewer_load_verified_refs_without_returning_bodies(): ...
  async def test_role_activity_names_and_ordinals_are_exact_and_attempt_scoped(): ...
  async def test_role_usage_is_durable_for_all_allowed_role_step_names(): ...
  async def test_usage_rejects_near_match_or_out_of_range_role_step_names(): ...
  async def test_role_failures_are_classified_and_sanitized_like_version_one(): ...
  async def test_ambiguous_usage_cannot_increase_graph_repairs_or_activity_attempts(): ...
  async def test_check_cancellation_reads_postgresql_authority(): ...
  ```

  Assert returned Activity results contain only references/verdict metadata and that private sentinels are absent from errors, captured logs, and database JSON/text columns.

- [x] **Step 2: Run focused Activity/repository tests and verify RED**

  ```powershell
  .\.venv\Scripts\python.exe -m pytest tests/unit/test_role_activities.py tests/unit/test_activities.py tests/integration/test_repository.py -q
  ```

  Expected: FAIL because role Activities and usage-step allowlisting do not exist.

- [x] **Step 3: Implement history-safe role Activities**

  Each Activity must:

  1. record `RUNNING` with the exact supplied ordinal/name;
  2. verify model profile and every stage reference;
  3. load bodies only after entering the Activity;
  4. call one role-provider method with `DurableModelUsageReporter` for Ollama or `NoOpModelUsageReporter` for mock;
  5. validate the strict result and write one immutable stage file;
  6. record terminal attempt/step state; and
  7. return a reference or bounded verdict only.

  Extend `ModelUsageRepository._locked_attempt(...)` to accept only `decide_action`, `research_shard_[1-4]`, `write_report_r[0-2]`, and `review_report_r[0-2]`. Do not accept a broad prefix.

- [x] **Step 4: Implement trusted cancellation lookup and reviewed publication**

  `check_cancellation` returns `CancellationRepository.exists(...)` without creating a projected step. `publish_reviewed_report` verifies the exact final `DraftRef`, calls Task 2 storage, and uses existing artifact persistence/projection behavior without accepting report bytes in Workflow input.

- [x] **Step 5: Run focused and broader Activity/storage/repository tests**

  ```powershell
  .\.venv\Scripts\python.exe -m pytest tests/unit/test_role_activities.py tests/unit/test_activities.py tests/unit/test_storage.py tests/integration/test_repository.py -q
  ```

  Expected: PASS.

- [x] **Step 6: Commit Task 6**

  ```powershell
  git add src/agentforge/role_activities.py src/agentforge/repository.py tests/unit/test_role_activities.py tests/unit/test_activities.py tests/integration/test_repository.py
  git commit -m "feat: execute durable report roles"
  ```

### Task 7: Coordinate the version-2 graph and preserve version-1 replay

**Files:**
- Create: `src/agentforge/multi_agent_workflow.py`
- Create: `tests/integration/test_multi_agent_workflow.py`
- Create: `tests/integration/test_workflow_replay.py`
- Create: `tests/fixtures/temporal/support_report_v1_approved.json`
- Create: `tests/fixtures/temporal/support_report_v1_denied.json`
- Modify: `src/agentforge/worker.py:26-82`
- Modify: `src/agentforge/relay.py`
- Modify: `tests/unit/test_activities.py`
- Modify: `tests/integration/test_workflow.py`
- Modify: `tests/integration/test_relay.py`

**Interfaces:**
- Consumes: `MultiAgentWorkflowInput`, fixed partition/ordinal helpers, role Activities, existing trusted approval/delivery Activities, and version-aware relay starts.
- Produces: Temporal Workflow type `MultiAgentSupportReportWorkflow` and Workers registering both v1 and v2 Workflows on the same immutable profile queue.

- [x] **Step 1: Save sanitized v1 replay fixtures before Workflow registration changes**

  Use the current version-1 integration harness to produce one approved and one denied history with only repository sample data, then save `WorkflowHistory.to_json()` under `tests/fixtures/temporal/`. Scan the fixtures for ticket/report bodies, credentials, local absolute paths, and private sentinels before staging.

- [x] **Step 2: Write failing graph, deadline, cancellation, and replay tests**

  Add tests named:

  ```python
  async def test_graph_runs_research_in_two_node_batches_and_preserves_ordinals(): ...
  async def test_out_of_order_research_completion_keeps_writer_note_order(): ...
  async def test_accept_path_publishes_then_waits_for_exact_human_approval(): ...
  async def test_two_revisions_are_allowed_and_third_rejection_exhausts_budget(): ...
  async def test_ten_role_ceiling_refuses_an_eleventh_schedule(): ...
  async def test_nine_hundred_second_deadline_ends_before_publication(): ...
  async def test_cancellation_during_batch_allows_batch_to_settle_but_schedules_nothing_later(): ...
  async def test_worker_registers_both_workflows_on_one_profile_queue(): ...
  async def test_saved_v1_histories_replay_with_support_report_workflow(): ...
  ```

  Inspect Temporal history to assert no ticket, note, draft, feedback, prompt, or raw response bodies and no more than two simultaneously started incomplete research Activities.

- [x] **Step 3: Run focused tests and verify RED**

  ```powershell
  .\.venv\Scripts\python.exe -m pytest tests/integration/test_multi_agent_workflow.py tests/integration/test_workflow_replay.py tests/integration/test_workflow.py tests/integration/test_relay.py tests/unit/test_activities.py -q
  ```

  Expected: FAIL because the v2 Workflow is not implemented or registered.

- [x] **Step 4: Implement the fixed graph with deadline and cancellation guards**

  Create `@workflow.defn(name="MultiAgentSupportReportWorkflow")`. Use `workflow.now()` for one pre-approval deadline. Reserve the exact ordinals from Task 1. Schedule sorted research shards in ascending batches of at most two using deterministic `asyncio.gather`; pass writer notes in shard order regardless of completion order.

  Before every batch/role/publication and after every in-flight batch/role, run the trusted cancellation check and remaining-time guard. Cap each pre-approval Activity timeout by remaining graph time. Count a role when scheduled; refuse role 11. Project `BUDGET_EXHAUSTED` for role/revision/deadline/model budget and `CANCELLED` for durable cancellation.

- [x] **Step 5: Duplicate the trusted post-review approval/delivery sequence**

  After `ACCEPT`, publish the exact reviewed draft, build the fixed `DeliverReportAction`, authorize it with existing policy code, create the exact approval, wait durably, reread PostgreSQL before delivery, use the stable operation ID, recheck cancellation after receiver commit, and finish with the exact artifact. Deliberately duplicate this coordination in v2 rather than extracting/changing version-1 commands.

- [x] **Step 6: Register providers, role Activities, and both Workflow types**

  Extend `create_worker(...)` with optional `roles: RoleProvider | None = None`. Mock defaults use the deterministic object; Ollama setup creates `OllamaRoleProvider` over the verified existing client. Register both Workflow classes and both Activity sets on `model_profile.task_queue`. Keep runtime identity verification before polling.

- [x] **Step 7: Run Workflow, replay, worker, and relay regressions**

  ```powershell
  .\.venv\Scripts\python.exe -m pytest tests/integration/test_multi_agent_workflow.py tests/integration/test_workflow_replay.py tests/integration/test_workflow.py tests/integration/test_relay.py tests/unit/test_activities.py -q
  ```

  Expected: PASS, including both saved v1 replays.

- [x] **Step 8: Commit Task 7**

  ```powershell
  git add src/agentforge/multi_agent_workflow.py src/agentforge/worker.py src/agentforge/relay.py tests/integration/test_multi_agent_workflow.py tests/integration/test_workflow_replay.py tests/integration/test_workflow.py tests/integration/test_relay.py tests/unit/test_activities.py tests/fixtures/temporal/support_report_v1_approved.json tests/fixtures/temporal/support_report_v1_denied.json
  git commit -m "feat: coordinate bounded report graph"
  ```

### Task 8: Explain roles, shards, revisions, retries, and usage in the dashboard

**Files:**
- Modify: `dashboard/components/run-timeline.tsx`
- Modify: `dashboard/e2e/run-timeline.spec.ts`
- Modify: `dashboard/e2e/submission.spec.ts`
- Modify: `dashboard/e2e/real-model.spec.ts`

**Interfaces:**
- Consumes: existing `StepSnapshot.name`, deterministic version-2 step names, and existing nullable usage fields.
- Produces: `formatStepLabel(name: string) -> string` and readable timeline labels without changing API contracts or execution authority.

- [x] **Step 1: Write failing role-label and privacy browser tests**

  Add assertions for:

  ```text
  research_shard_1 -> Researcher · shard 1
  write_report_r0  -> Writer · initial draft
  write_report_r2  -> Writer · revision 2
  review_report_r1 -> Reviewer · review 2
  ```

  Test deterministic ordinal rendering after out-of-order mock API data, retry attempts under one role step, usage cards for researcher/writer/reviewer attempts, unchanged unknown-name fallback, and absence of note/draft/feedback sentinels.

- [x] **Step 2: Run Playwright tests and verify RED**

  ```powershell
  $nodeRoot = Join-Path $env:LOCALAPPDATA "Programs\node-v24.22.1-win-x64"
  $env:Path = "$nodeRoot;$env:Path"
  Set-Location dashboard
  & (Join-Path $nodeRoot "npm.cmd") run test:e2e -- --grep "timeline|role|usage"
  Set-Location ..
  ```

  Expected: FAIL because raw step names are still rendered.

- [x] **Step 3: Implement the exact label mapper**

  Add a pure `formatStepLabel(name: string): string` with anchored patterns limited to approved shard/revision ranges. Return existing human-friendly labels for known v1 names and the original string for unknown names. Do not expose intermediate references or add graph controls.

- [x] **Step 4: Run dashboard static and browser checks**

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

  Expected: all static checks pass; deterministic browser suite passes with only the explicitly opt-in real-model test skipped.

- [x] **Step 5: Commit Task 8**

  ```powershell
  git add dashboard/components/run-timeline.tsx dashboard/e2e/run-timeline.spec.ts dashboard/e2e/submission.spec.ts dashboard/e2e/real-model.spec.ts
  git commit -m "feat: show multi-agent role progress"
  ```

### Task 9: Prove partial Worker loss, bounded review, end-to-end delivery, and local Qwen roles

**Files:**
- Create: `tests/helpers/multi_agent_recovery_worker.py`
- Create: `tests/integration/test_multi_agent_worker_recovery.py`
- Modify: `tests/integration/test_end_to_end.py`
- Modify: `tests/helpers/model_dashboard_stack.ps1`
- Modify: `tests/helpers/model_dashboard_stack.contract.ps1`
- Modify: `dashboard/e2e/real-model.spec.ts`

**Interfaces:**
- Consumes: registered v2 Worker, stable same-queue replacement behavior, scripted blocking roles, existing native stack harness, and exact approval/delivery APIs.
- Produces: one deterministic Worker-loss proof, one v2 end-to-end test, and one opt-in native Qwen researcher/writer/reviewer browser proof.

- [x] **Step 1: Write failing Worker-loss and v2 end-to-end tests**

  Add tests that:

  - start two Worker processes on the same `agentforge-test` namespace/profile queue;
  - block one identity-marked Worker during `review_report_r0` after all research steps completed;
  - terminate only that process, let the second/replacement Worker retry the same review step, and preserve completed research attempts;
  - approve the exact final artifact and assert one AgentForge delivery plus one inbox receipt;
  - assert no role count/revision/concurrency limit was exceeded; and
  - scan PostgreSQL and decoded history for private intermediate sentinels.

- [x] **Step 2: Run focused recovery/end-to-end tests and verify RED**

  ```powershell
  .\.venv\Scripts\python.exe -m pytest tests/integration/test_multi_agent_worker_recovery.py tests/integration/test_end_to_end.py -q
  ```

  Expected: FAIL because the v2 recovery helper/proof does not exist.

- [x] **Step 3: Implement the identity-safe recovery helper and proof**

  Reuse the existing subprocess-marker and cleanup patterns. Never terminate by image name or broad command line. Record PIDs/start times, verify ownership before termination, start/reuse the replacement on the same queue, and require all harness-owned processes to exit. Assert completed shard steps are not rerun and the interrupted review has retry attempts under one stable step.

- [x] **Step 4: Extend the native Qwen harness and browser assertion**

  Run one version-2 local-model submission with the existing pinned runtime/tag/digest. Assert visible researcher/writer/reviewer timeline entries, one to four research role calls for the sample set, at most two revisions, positive aggregate provider usage across role attempts, exact approval, one delivery, no private bodies in PostgreSQL/decoded history/logs/traces/screenshots, and cleanup of ports 3000/8000/8001 plus harness-owned 11434.

- [x] **Step 5: Run deterministic recovery plus native Qwen proof**

  ```powershell
  .\.venv\Scripts\python.exe -m pytest tests/integration/test_multi_agent_worker_recovery.py tests/integration/test_end_to_end.py -q
  powershell -NoProfile -ExecutionPolicy Bypass -File tests/helpers/model_dashboard_stack.contract.ps1
  powershell -NoProfile -ExecutionPolicy Bypass -File tests/helpers/model_dashboard_stack.ps1 -ExpectedModelDigest 0edcdef34593eac1aa2be9c7d06c432dcf81945adca5eca2f27662c18f168ba0
  ```

  Expected: deterministic tests pass; contract script passes; native browser proof reaches `SUCCEEDED` with one delivery and cleans up only owned processes/listeners.

- [x] **Step 6: Commit Task 9**

  ```powershell
  git add tests/helpers/multi_agent_recovery_worker.py tests/integration/test_multi_agent_worker_recovery.py tests/integration/test_end_to_end.py tests/helpers/model_dashboard_stack.ps1 tests/helpers/model_dashboard_stack.contract.ps1 dashboard/e2e/real-model.spec.ts
  git commit -m "test: prove bounded multi-agent recovery"
  ```

### Task 10: Document, verify, independently review, and checkpoint Milestone 6

**Files:**
- Create: `docs/learning/09_MILESTONE_6_MULTI_AGENT_GRAPH.md`
- Modify: `README.md`
- Modify: `ARCHITECTURE.md`
- Modify: `FLOW.md`
- Modify: `DECISIONS.md`
- Modify: `TEST_CHECKLIST.md`
- Modify: `PROJECT_PLAN.md`
- Replace: `HANDOVER.md`
- Modify: `docs/superpowers/plans/2026-10-27-agentforge-milestone-6-multi-agent-graph.md`

**Interfaces:**
- Consumes: verified implementation/test evidence from Tasks 1–9.
- Produces: accurate operator/architecture/learning documentation, final executed-check ledger, checked plan tasks, and an approval-ready checkpoint.

- [x] **Step 1: Update documentation from observed behavior only**

  Document logical roles versus Worker processes, fixed graph/ordinals, content-addressed intermediate storage, graph/version authority, 16/4/2/2/10/900 limits, structural 90-request worst case, replay boundary, cancellation timing, same-queue replicas, unchanged human approval, and exactly-once claim boundary. Add a decision record for static v2 graph plus shared queue. Do not claim tests or native proof before they execute.

- [x] **Step 2: Run the complete backend and migration verification**

  ```powershell
  .\.venv\Scripts\python.exe -m pytest -q
  .\.venv\Scripts\python.exe -m pip check
  .\.venv\Scripts\alembic.exe upgrade head
  .\.venv\Scripts\alembic.exe current
  .\.venv\Scripts\python.exe -m pytest tests/integration/test_migrations.py -q
  docker compose config --quiet
  ```

  Expected: all tests pass, dependency check is clean, Alembic reports `0005_support_report_v2 (head)`, migration tests pass, and Compose validates.

- [x] **Step 3: Run the complete dashboard verification**

  ```powershell
  $nodeRoot = Join-Path $env:LOCALAPPDATA "Programs\node-v24.22.1-win-x64"
  $env:Path = "$nodeRoot;$env:Path"
  Set-Location dashboard
  & (Join-Path $nodeRoot "npm.cmd") ci
  & (Join-Path $nodeRoot "npm.cmd") audit --audit-level=high
  & (Join-Path $nodeRoot "npm.cmd") run typecheck
  & (Join-Path $nodeRoot "npm.cmd") run lint
  & (Join-Path $nodeRoot "npm.cmd") run build
  & (Join-Path $nodeRoot "npm.cmd") run test:e2e
  Set-Location ..
  ```

  Expected: clean install, zero high-or-worse vulnerabilities, and all deterministic frontend checks pass with only the opt-in real-model case skipped.

- [x] **Step 4: Run replay, recovery, native proof, privacy, and repository checks**

  ```powershell
  .\.venv\Scripts\python.exe -m pytest tests/integration/test_workflow_replay.py tests/integration/test_multi_agent_worker_recovery.py -q
  powershell -NoProfile -ExecutionPolicy Bypass -File tests/helpers/model_dashboard_stack.contract.ps1
  powershell -NoProfile -ExecutionPolicy Bypass -File tests/helpers/model_dashboard_stack.ps1 -ExpectedModelDigest 0edcdef34593eac1aa2be9c7d06c432dcf81945adca5eca2f27662c18f168ba0
  git diff --check
  git status --short
  ```

  Scan tracked files plus generated safe logs/history/database evidence for credentials, absolute local secrets, model blobs, and the test privacy sentinels. Verify application ports 3000/8000/8001 and owned 11434 are clear; infrastructure ports remain loopback-only.

- [x] **Step 5: Request independent code review and remediate before closure**

  Use `superpowers:requesting-code-review` against the complete Milestone 6 commit range. Treat Critical/Important findings as blockers, apply `superpowers:receiving-code-review` before changes, add RED→GREEN regressions for accepted findings, and rerun every affected broader suite. Record Minor deferrals explicitly.

  The first reviewer attempt hit an account usage limit. The successful retry
  reviewed `ef332a6..c5f3ae2`, reported one Critical, two Important, and one
  Minor issue, and reviewed the remediation twice. Commit `b23ca56` preserves
  uncertain delivery, bounds the complete prepublication schedule/retry
  lifetime, stops deadline authority at publication, prevents approval after
  cancellation during publication, and rejects trusted provider requests
  without semantic repairs. The final reviewer verdict reported no remaining
  Critical, Important, or Minor issue and marked the change ready to merge.

- [x] **Step 6: Update the checkpoint ledger with actual evidence**

  Check Milestone 6 tasks only after their acceptance evidence exists. Replace `HANDOVER.md` with exact commits, changed files, commands, pass/fail counts, native run/workflow/operation IDs, model digest/usage, privacy results, known caveats, and the next action: user checkpoint review. Do not begin Milestone 7.

- [x] **Step 7: Commit Task 10**

  ```powershell
  git add README.md ARCHITECTURE.md FLOW.md DECISIONS.md TEST_CHECKLIST.md PROJECT_PLAN.md HANDOVER.md docs/learning/09_MILESTONE_6_MULTI_AGENT_GRAPH.md docs/superpowers/plans/2026-10-27-agentforge-milestone-6-multi-agent-graph.md
  git commit -m "docs: record multi-agent graph milestone"
  ```

## Final self-review results

- Spec coverage: every design section maps to Tasks 1–10; no generic graph engine, child Workflow, new queue, or dependency is introduced.
- Step scan: every step has one checkable action, exact files/interfaces, a focused RED command, a GREEN command, or a coherent commit.
- Type consistency: Workflow history uses only `MultiAgentWorkflowInput`, references, and `ReviewResult`; Activity-local bodies use `role_runtime.py`; providers never receive authority-bearing actions.
- Review focus coverage: legacy idempotency is Task 3, stale stage writes Task 2, cancellation/deadline Task 7, out-of-order research Tasks 1/7, and uncertain usage Tasks 5/6.
- Proportion: ten tasks align with independently reviewable durable boundaries and keep setup/documentation folded into the deliverable that needs them.
