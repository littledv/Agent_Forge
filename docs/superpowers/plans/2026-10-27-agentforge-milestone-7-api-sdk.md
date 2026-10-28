# AgentForge Milestone 7 API and Python SDK Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stabilize AgentForge's existing `/api/v1` contract and add a small synchronous typed Python client that can safely submit, reconnect, observe, approve, cancel, and retrieve a verified report.

**Architecture:** FastAPI remains the only public boundary; focused OpenAPI assertions freeze the intentional v1 surface without snapshotting incidental metadata. A handwritten `AgentForgeClient` owns one `httpx.Client`, validates inputs with existing strict request contracts, validates responses with additive-compatible client models, and exposes explicit errors and polling without becoming a scheduler. PostgreSQL and Temporal ownership remain unchanged.

**Tech Stack:** Python 3.13.17, FastAPI 0.158.1, httpx 0.28.1, Pydantic 2.14.1, pytest 9.1.1, PostgreSQL 16.15, Temporal Python SDK 1.33.0, and the existing loopback-only native test harness.

**Spec:** `docs/superpowers/specs/2026-10-27-agentforge-milestone-7-api-sdk-design.md`

## Global Constraints

- Work only in the existing managed worktree and use Windows PowerShell.
- Add or upgrade no dependency, container image, service, credential, remote, paid resource, deployment, or non-loopback port.
- Preserve every existing `/api/v1` runtime response body, field meaning, success status, and error status; this milestone may add OpenAPI response metadata but no breaking response change.
- Keep server request models strict and make client response models ignore additive unknown fields while validating every known field.
- The SDK calls only HTTP. It must not import repositories, access PostgreSQL, contact Temporal, read artifact paths, or reconstruct Workflow state.
- Use `httpx.Client` synchronously with default base URL `http://127.0.0.1:8000`, request timeout `10.0` seconds, polling timeout `300.0` seconds, polling interval `1.0` second, and `trust_env=False` by default.
- Perform no automatic retry of submission, approval, or cancellation. Preserve stable submission keys, exact approval hashes, and the existing server idempotency semantics.
- Keep recent-run listing newest-first and bounded to `1..50`; do not add cursor pagination.
- Never include request bodies, ticket content, report content, raw structured validation items, credentials, or arbitrary response bodies in exception strings or logs.
- Use TDD for every product change: observe the focused test fail for the intended reason, implement the minimum behavior, rerun focused and relevant regression tests, then commit coherently.
- Preserve the user's Git identity and never add Codex, OpenAI, AI-assistant, or generated-by authorship or project credit.

## File Structure

- Modify `src/agentforge/domain.py` only to define the stable API error-envelope schema used by OpenAPI.
- Modify `src/agentforge/api.py` only to document the existing response statuses and schemas; do not change endpoint behavior.
- Create `src/agentforge/client_models.py` for additive-compatible public response models and nested response adapters.
- Create `src/agentforge/client_errors.py` for the public exception hierarchy and safe diagnostic context.
- Create `src/agentforge/client.py` for transport ownership, HTTP operations, polling, and artifact verification.
- Modify `src/agentforge/__init__.py` only for intentional public SDK exports.
- Create `tests/unit/test_api_contract.py` and `tests/unit/test_client.py`; extend existing integration tests rather than adding a second test harness.
- Create `examples/python_sdk_lifecycle.py`, `docs/API_SDK.md`, and `docs/learning/10_MILESTONE_7_API_SDK.md`; update the existing indexes and checkpoint documents.

## Review Focus

- A valid response with additive fields at both top level and nested levels must parse, while a missing or wrongly typed known field must fail as a protocol error. Task 2 and Task 3 pin both cases.
- A native FastAPI 422 detail list may echo submitted input; the SDK must normalize it to `request validation failed` and retain neither the list nor the response body. Task 3 adds an explicit privacy regression.
- Empty target states, non-positive request/polling timeouts, non-positive polling intervals, and recent-run limits outside `1..50` must fail locally before any request. Tasks 3 and 4 test each boundary.
- Redirects, environment proxies, trailing base-URL slashes, malformed UUID strings, and unexpected HTTP statuses must not silently change the target or outcome. Task 3 covers these transport boundaries.
- Artifact size is a UTF-8 byte count, not a character count; non-ASCII content with either a wrong byte length or SHA-256 must be rejected. Task 4 covers both integrity failures.

---

### Task 1: Freeze the intentional `/api/v1` contract

**Files:**
- Modify: `src/agentforge/domain.py`
- Modify: `src/agentforge/api.py`
- Create: `tests/unit/test_api_contract.py`
- Test: `tests/integration/test_api.py`

**Interfaces:**
- Consumes: existing FastAPI routes and the existing `RunSnapshot`, `RunListItem`, `RunDetailSnapshot`, `ApprovalSnapshot`, `CancellationSnapshot`, `ArtifactContent`, and `ModelProfileDescriptor` response models.
- Produces: `ApiErrorResponse` and stable OpenAPI declarations for every existing v1 success and error status; runtime endpoint behavior remains byte-for-byte compatible.

- [ ] **Step 1: Write the failing OpenAPI surface test**

  Add `test_v1_openapi_contract_has_stable_paths_methods_and_statuses`. Assert the exact routes and methods from the design, their success schema references or array item references, and these documented statuses:

  ```text
  POST /api/v1/runs: 200, 202, 409, 422, 503
  GET /api/v1/model-profiles: 200
  GET /api/v1/runs: 200, 422, 503
  GET /api/v1/runs/{run_id}: 200, 404, 422, 503
  GET /api/v1/runs/{run_id}/detail: 200, 404, 422, 503
  GET /api/v1/runs/{run_id}/approval: 200, 404, 422, 503
  POST /api/v1/approvals/{approval_id}/decision: 200, 202, 404, 409, 422, 503
  POST /api/v1/runs/{run_id}/cancel: 200, 202, 404, 409, 422, 503
  GET /api/v1/runs/{run_id}/artifact: 200, 404, 409, 422, 503
  ```

  Also assert all non-success responses reference `ApiErrorResponse`, whose `detail` accepts either a string or FastAPI's structured validation list.

- [ ] **Step 2: Run the contract test and verify RED**

  Run:

  ```powershell
  .\.venv\Scripts\python.exe -m pytest tests/unit/test_api_contract.py -q
  ```

  Expected: FAIL because application error statuses are not completely documented and `ApiErrorResponse` does not exist.

- [ ] **Step 3: Add the error schema and response metadata**

  Define:

  ```python
  class ApiErrorResponse(StrictModel):
      detail: str | list[dict[str, object]]
  ```

  Add reusable `responses=` mappings to the route decorators with the exact statuses above. Do not add exception handlers or change endpoint code, response bodies, or status selection.

- [ ] **Step 4: Add representative runtime compatibility assertions**

  Extend `tests/integration/test_api.py` with `test_v1_error_envelopes_match_documented_contract`. Assert an application 404 returns `{"detail": "run not found"}`, malformed UUID/query input returns 422 with a list-valued `detail`, a reused changed submission returns 409, and unavailable readiness remains `{"detail": "service unavailable"}` without infrastructure details.

- [ ] **Step 5: Run focused contract and API tests**

  Run:

  ```powershell
  .\.venv\Scripts\python.exe -m pytest tests/unit/test_api_contract.py tests/integration/test_api.py -q
  ```

  Expected: PASS.

- [ ] **Step 6: Commit the contract checkpoint**

  ```powershell
  git add src/agentforge/domain.py src/agentforge/api.py tests/unit/test_api_contract.py tests/integration/test_api.py
  git commit -m "test: stabilize API v1 contract"
  ```

### Task 2: Define additive-compatible client models and safe errors

**Files:**
- Create: `src/agentforge/client_models.py`
- Create: `src/agentforge/client_errors.py`
- Create: `tests/unit/test_client.py`

**Interfaces:**
- Consumes: canonical aliases and enums from `agentforge.domain`, including `RunState`, `ApprovalDecision`, `ApprovalState`, `FailureKind`, `StepState`, and `AttemptState`.
- Produces: public models `ModelProfile`, `Run`, `RunListItem`, `RunDetail`, `Approval`, `Cancellation`, and `ArtifactContent`; internal additive-compatible nested adapters; `TERMINAL_RUN_STATES`; and the complete public error hierarchy.

- [ ] **Step 1: Write failing model compatibility tests**

  Add tests named:

  ```python
  def test_client_models_parse_json_types_and_ignore_additive_nested_fields(): ...
  def test_client_models_reject_missing_or_invalid_known_fields(): ...
  def test_terminal_run_states_contains_every_terminal_state_only(): ...
  ```

  Use a `RunDetail` payload containing unknown fields on the root, `run`, `model_profile`, one step, and one attempt. Assert all known UUID, datetime, enum, tuple, and nested values are typed. Remove `run_id` and replace `updated_at` with an integer in separate cases and assert validation fails.

- [ ] **Step 2: Write failing error-safety tests**

  Add:

  ```python
  def test_client_errors_preserve_safe_context_without_body_or_ticket_content(): ...
  def test_polling_errors_retain_run_id_and_last_validated_run(): ...
  ```

  Assert the string form contains operation/status/safe string detail, excludes supplied raw bodies and ticket probes, and exposes typed attributes for programmatic handling.

- [ ] **Step 3: Run the focused tests and verify RED**

  Run:

  ```powershell
  .\.venv\Scripts\python.exe -m pytest tests/unit/test_client.py -q
  ```

  Expected: FAIL because the client model and error modules do not exist.

- [ ] **Step 4: Implement the client response models**

  Define an internal frozen base model with `extra="ignore"`. Mirror every known field from the seven server response families, including internal tolerant adapters for artifact references, delivery actions/receipts, immutable model-profile snapshots, steps, and attempts. Parse server bodies with `model_validate_json` so strict UUID and datetime types accept their JSON representations without weakening known-field validation.

  Define exactly:

  ```python
  TERMINAL_RUN_STATES: frozenset[RunState]
  ```

  containing `SUCCEEDED`, `FAILED`, `DENIED`, `CANCELLED`, `TIMED_OUT`, `BUDGET_EXHAUSTED`, and `NEEDS_ATTENTION`.

- [ ] **Step 5: Implement the safe exception hierarchy**

  Define `AgentForgeClientError` plus `AgentForgeValidationError`, `AgentForgeNotFoundError`, `AgentForgeConflictError`, `AgentForgeUnavailableError`, `AgentForgeTransportError`, `AgentForgeRequestTimeout`, `AgentForgePollingTimeout`, `AgentForgeRunTerminated`, and `AgentForgeProtocolError`.

  Base errors expose `operation: str`, `status_code: int | None`, and `detail: str | None`. Polling errors additionally expose `run_id: UUID` and `last_run: Run | None`; `AgentForgeRunTerminated` requires a terminal `last_run`. Constructors must accept no raw response-body field.

- [ ] **Step 6: Run focused model and error tests**

  Run:

  ```powershell
  .\.venv\Scripts\python.exe -m pytest tests/unit/test_client.py -q
  ```

  Expected: PASS.

- [ ] **Step 7: Commit typed client contracts**

  ```powershell
  git add src/agentforge/client_models.py src/agentforge/client_errors.py tests/unit/test_client.py
  git commit -m "feat: define typed SDK contracts"
  ```

### Task 3: Implement the synchronous HTTP operations and error mapping

**Files:**
- Create: `src/agentforge/client.py`
- Modify: `src/agentforge/__init__.py`
- Test: `tests/unit/test_client.py`

**Interfaces:**
- Consumes: Task 2 models/errors, existing strict `SubmitRun` and `ApprovalDecisionRequest` request models, and `httpx.BaseTransport`/`httpx.Client`.
- Produces: the exact `AgentForgeClient` constructor and non-polling methods from the design; package-root exports for the client, seven result models, `RunState`, `ApprovalDecision`, `TERMINAL_RUN_STATES`, all public errors, and the existing `__version__`.

- [ ] **Step 1: Write failing construction and lifecycle tests**

  Add:

  ```python
  def test_client_owns_one_transport_and_close_is_idempotent(): ...
  def test_client_context_manager_closes_owned_http_client(): ...
  def test_client_defaults_disable_environment_proxy_and_redirects(monkeypatch): ...
  def test_client_rejects_non_positive_request_timeout_before_network(): ...
  ```

  Spy on `httpx.Client` construction to assert normalized trailing-slash base URL, `timeout=10.0`, `trust_env=False`, `follow_redirects=False`, and the injected transport. Assert a caller cannot inject a prebuilt client because no such parameter exists.

- [ ] **Step 2: Write failing route and serialization tests**

  Use `httpx.MockTransport` to add one test for each method:

  ```python
  def test_list_model_profiles_returns_typed_tuple(): ...
  def test_submit_run_sends_validated_json_and_accepts_200_or_202(): ...
  def test_list_runs_validates_limit_locally_and_sends_query(): ...
  def test_get_run_and_detail_use_canonical_uuid_paths(): ...
  def test_get_approval_and_decide_approval_use_exact_hash_and_decision(): ...
  def test_cancel_run_accepts_200_or_202(): ...
  ```

  Assert invalid submission keys, empty/absolute ticket paths, invalid hashes/decisions, malformed UUIDs, and limits `0`/`51` raise `AgentForgeValidationError` before the mock transport records a request.

- [ ] **Step 3: Write failing response and error-mapping tests**

  Add parametrized coverage proving 404, 409, 422 string detail, 422 structured detail, and 503 map to their exact error classes; structured detail normalizes to `request validation failed` and does not appear in `repr`, `str`, or attributes. Assert malformed JSON, an invalid known response field, redirects, and unexpected 500 map to `AgentForgeProtocolError`; `httpx.TimeoutException` maps to `AgentForgeRequestTimeout`; other `httpx.RequestError` maps to `AgentForgeTransportError`.

- [ ] **Step 4: Run the client tests and verify RED**

  Run:

  ```powershell
  .\.venv\Scripts\python.exe -m pytest tests/unit/test_client.py -q
  ```

  Expected: FAIL because `AgentForgeClient` and package exports do not exist.

- [ ] **Step 5: Implement transport ownership and one request pipeline**

  Implement:

  ```python
  class AgentForgeClient:
      def __init__(
          self,
          base_url: str = "http://127.0.0.1:8000",
          *,
          timeout_seconds: float = 10.0,
          transport: httpx.BaseTransport | None = None,
          trust_env: bool = False,
      ) -> None: ...
      def __enter__(self) -> AgentForgeClient: ...
      def __exit__(self, *exc_info: object) -> None: ...
      def close(self) -> None: ...
  ```

  Add one private request helper that accepts the operation name, method, path, expected statuses, response model, optional JSON/query values, and optional per-call timeout. It must never follow redirects, must map errors before parsing success, must never retain arbitrary bodies, and must validate success bodies with the Task 2 model.

- [ ] **Step 6: Implement exact non-polling public methods**

  Implement the signatures fixed by the design:

  ```python
  def list_model_profiles(self) -> tuple[ModelProfile, ...]: ...
  def submit_run(self, *, submission_key: str, goal: str,
                 ticket_files: Sequence[str],
                 model_profile_id: str = "mock-v1") -> Run: ...
  def list_runs(self, *, limit: int = 20) -> tuple[RunListItem, ...]: ...
  def get_run(self, run_id: UUID | str) -> Run: ...
  def get_run_detail(self, run_id: UUID | str) -> RunDetail: ...
  def get_approval(self, run_id: UUID | str) -> Approval: ...
  def decide_approval(self, approval_id: UUID | str, *, action_hash: str,
                      decision: ApprovalDecision | str) -> Approval: ...
  def cancel_run(self, run_id: UUID | str) -> Cancellation: ...
  ```

  Add a private `_get_run_with_timeout(run_id: UUID, timeout: float) -> Run`
  helper for Task 4 rather than extending the public `get_run` signature.
  Validate outbound payloads with `SubmitRun` and
  `ApprovalDecisionRequest`, then serialize in JSON mode. Convert all local
  Pydantic/UUID/range failures into `AgentForgeValidationError`.

- [ ] **Step 7: Export the intentional public surface**

  Update `agentforge.__init__` with an explicit `__all__` containing only the names required by the design. Keep `__version__ = "0.1.0"`; package-version changes are out of scope.

- [ ] **Step 8: Run focused and nearby regression tests**

  Run:

  ```powershell
  .\.venv\Scripts\python.exe -m pytest tests/unit/test_client.py tests/unit/test_domain.py tests/unit/test_inbox_client.py -q
  ```

  Expected: PASS.

- [ ] **Step 9: Commit the HTTP client**

  ```powershell
  git add src/agentforge/client.py src/agentforge/__init__.py tests/unit/test_client.py
  git commit -m "feat: add synchronous Python SDK"
  ```

### Task 4: Add deadline-bounded polling and artifact verification

**Files:**
- Modify: `src/agentforge/client.py`
- Test: `tests/unit/test_client.py`

**Interfaces:**
- Consumes: Task 3 private `_get_run_with_timeout(...)`, Task 2 `TERMINAL_RUN_STATES`, polling errors, and `ArtifactContent`.
- Produces: `wait_for_run(...) -> Run` and `get_artifact(...) -> ArtifactContent` with local UTF-8 byte-length and SHA-256 verification.

- [ ] **Step 1: Write failing deterministic polling tests**

  Add scripted-transport tests named:

  ```python
  def test_wait_for_run_returns_requested_intermediate_state(): ...
  def test_wait_for_run_defaults_to_any_terminal_state(): ...
  def test_wait_for_run_caps_request_timeout_and_sleep_to_remaining_budget(monkeypatch): ...
  def test_wait_for_run_timeout_retains_last_run_without_cancelling(): ...
  def test_wait_for_run_raises_on_unrequested_terminal_state(): ...
  def test_wait_for_run_propagates_request_failure_without_retry(): ...
  def test_wait_for_run_rejects_empty_states_and_non_positive_timing(): ...
  ```

  Replace `time.monotonic` and `time.sleep` with a deterministic fake. Assert only GET requests occur, a timeout never sends cancel, request timeout equals `min(10.0, remaining_budget)`, and a transport/503 failure records exactly one attempt.

- [ ] **Step 2: Write failing artifact-integrity tests**

  Add:

  ```python
  def test_get_artifact_accepts_matching_utf8_bytes_and_sha256(): ...
  def test_get_artifact_rejects_wrong_unicode_byte_length(): ...
  def test_get_artifact_rejects_wrong_sha256_without_exposing_content(): ...
  ```

  Use non-ASCII content such as `"# Café\n"`; calculate expected values from `content.encode("utf-8")` and assert mismatch errors do not contain the content.

- [ ] **Step 3: Run the focused tests and verify RED**

  Run:

  ```powershell
  .\.venv\Scripts\python.exe -m pytest tests/unit/test_client.py -q
  ```

  Expected: FAIL because polling and artifact retrieval are not implemented.

- [ ] **Step 4: Implement monotonic polling**

  Implement:

  ```python
  def wait_for_run(
      self,
      run_id: UUID | str,
      *,
      states: Collection[RunState | str] = TERMINAL_RUN_STATES,
      timeout_seconds: float = 300.0,
      poll_interval_seconds: float = 1.0,
  ) -> Run: ...
  ```

  Normalize states once, reject an empty set and non-positive timing locally, use one monotonic deadline, cap each GET timeout and sleep to the remaining budget, and retain only the last validated `Run`. Return requested states; raise `AgentForgeRunTerminated` for an unrequested terminal state; propagate any request failure immediately; raise `AgentForgePollingTimeout` at deadline without mutation.

- [ ] **Step 5: Implement verified artifact retrieval**

  Implement:

  ```python
  def get_artifact(self, run_id: UUID | str) -> ArtifactContent: ...
  ```

  Fetch the documented endpoint, encode content as UTF-8, compare `len(bytes)` and `hashlib.sha256(bytes).hexdigest()` with the returned reference, and raise a sanitized `AgentForgeProtocolError` on either mismatch.

- [ ] **Step 6: Run focused and complete client tests**

  Run:

  ```powershell
  .\.venv\Scripts\python.exe -m pytest tests/unit/test_client.py -q
  ```

  Expected: PASS.

- [ ] **Step 7: Commit polling and verification**

  ```powershell
  git add src/agentforge/client.py tests/unit/test_client.py
  git commit -m "feat: add safe SDK polling and artifacts"
  ```

### Task 5: Prove duplicate submission, interrupted polling, and the complete SDK journey

**Files:**
- Modify: `tests/integration/test_end_to_end.py`
- Modify: `tests/integration/test_api.py`
- Create: `examples/python_sdk_lifecycle.py`

**Interfaces:**
- Consumes: the complete public package-root SDK from Tasks 2–4 and the existing loopback FastAPI/inbox/Temporal/Worker/relay harness.
- Produces: one executable SDK example and native integration evidence for the Milestone 7 acceptance journey.

- [ ] **Step 1: Write the SDK example before changing the integration test**

  Create a `main()` program that assumes the documented loopback services are running and reads only public command-line arguments: submission key, goal, and one or more relative ticket files. It must use `with AgentForgeClient()`, submit, wait for `WAITING_APPROVAL`, fetch the exact approval, ask for an explicit interactive `approve` or `reject`, decide with that approval's action hash, wait for a terminal state, retrieve the artifact only on `SUCCEEDED`, and print IDs/state/path/hash but never ticket or report bodies.

- [ ] **Step 2: Convert the existing end-to-end slice to the public SDK**

  Serve the application API through the existing ephemeral `127.0.0.1` Uvicorn helper. Call synchronous SDK methods with `asyncio.to_thread` so the event loop continues running the API and Worker. Preserve all existing database, inbox, role-order, history-privacy, and exact-once assertions.

- [ ] **Step 3: Add duplicate and interrupted-polling acceptance assertions**

  In `test_complete_submission_to_report_slice`:

  - submit once and assert a second exact submission returns the same `run_id`;
  - assert a changed goal with the same key raises `AgentForgeConflictError`;
  - let a first client poll the pre-relay run to a short `AgentForgePollingTimeout`, then close it;
  - start the durable run through the existing relay and use a newly constructed client with the same run ID to reach `WAITING_APPROVAL`;
  - retrieve and approve the exact action, relay the signal, reach `SUCCEEDED`, and retrieve a client-verified artifact;
  - assert one durable run, one delivery, one inbox receipt, and the existing bounded role sequence.

- [ ] **Step 4: Run the focused native integration tests**

  Run:

  ```powershell
  .\.venv\Scripts\python.exe -m pytest tests/integration/test_api.py tests/integration/test_end_to_end.py -q
  ```

  Expected: PASS with the local PostgreSQL and Temporal services available.

- [ ] **Step 5: Run the example's non-interactive checks**

  Run:

  ```powershell
  .\.venv\Scripts\python.exe examples/python_sdk_lifecycle.py --help
  .\.venv\Scripts\python.exe -m compileall -q examples/python_sdk_lifecycle.py
  ```

  Expected: help exits zero and compilation succeeds.

- [ ] **Step 6: Commit acceptance evidence**

  ```powershell
  git add tests/integration/test_api.py tests/integration/test_end_to_end.py examples/python_sdk_lifecycle.py
  git commit -m "test: prove the Python SDK lifecycle"
  ```

### Task 6: Document, verify, independently review, and checkpoint Milestone 7

**Files:**
- Create: `docs/API_SDK.md`
- Create: `docs/learning/10_MILESTONE_7_API_SDK.md`
- Modify: `docs/learning/README.md`
- Modify: `README.md`
- Modify: `TEST_CHECKLIST.md`
- Modify: `PROJECT_PLAN.md`
- Replace: `HANDOVER.md`

**Interfaces:**
- Consumes: all verified behavior and exact command output from Tasks 1–5.
- Produces: user-facing SDK/reference material, plain-language learning documentation, final verification evidence, independent review resolution, and the Milestone 7 approval checkpoint.

- [ ] **Step 1: Write the reference and learning documentation**

  `docs/API_SDK.md` must document every constructor/method signature, public model/error, status mapping, safe mutation retry recipe, request versus polling timeouts, interrupted polling, recent-window semantics, context-manager ownership, additive response compatibility, and artifact verification. Include the small end-to-end example and clearly state that transport timeout means the mutation outcome may be unknown.

  The learning chapter must explain, in plain language, why the durable server run survives a disposable polling process. Link both documents from README and the learning index.

- [ ] **Step 2: Add Milestone 7 verification checklist items**

  Extend `TEST_CHECKLIST.md` with API-contract, typed-client, no-hidden-retry, interrupted-polling, timeout, redaction, bounded-list, additive-response, and artifact-integrity checks. Do not check an item until its cited command has passed.

- [ ] **Step 3: Run the complete relevant verification matrix**

  Run:

  ```powershell
  .\.venv\Scripts\python.exe -m pytest tests/unit/test_api_contract.py tests/unit/test_client.py -q
  .\.venv\Scripts\python.exe -m pytest -q
  .\.venv\Scripts\python.exe -m pip check
  .\.venv\Scripts\alembic.exe upgrade head
  .\.venv\Scripts\alembic.exe current
  docker compose config --quiet
  git diff --check
  ```

  Expected: all tests pass, dependency integrity has no broken requirements, Alembic reports `0005_support_report_v2 (head)`, Compose validation exits zero, and Git whitespace validation is clean. Record any failed attempt and its diagnosis rather than replacing it with only a clean rerun.

- [ ] **Step 4: Run privacy and boundary checks**

  Search tracked source, test output retained for the checkpoint, and changed files for credentials, private-ticket probes, report bodies, absolute worktree paths, and accidental direct repository/Temporal imports from `client*.py`. Verify ports created by the acceptance harness are released and all configured published ports remain loopback-only.

- [ ] **Step 5: Request an independent whole-change review**

  Use `superpowers:requesting-code-review` against the approved design and this plan. The reviewer must rank Critical, Important, and Minor findings and check public compatibility, retry ambiguity, deadline behavior, redaction, artifact validation, and acceptance evidence. Resolve accepted findings with focused RED→GREEN tests and rerun affected plus full verification.

- [ ] **Step 6: Update durable checkpoint records**

  Mark the four Milestone 7 tasks complete only after their acceptance evidence passes. Record exact commands/results, review findings and remediation, changed files, known caveats, coherent commits, and the next action in `HANDOVER.md`. State that Milestone 8 has not started and requires explicit user approval.

- [ ] **Step 7: Commit the verified Milestone 7 checkpoint**

  ```powershell
  git add README.md docs/API_SDK.md docs/learning/10_MILESTONE_7_API_SDK.md docs/learning/README.md TEST_CHECKLIST.md PROJECT_PLAN.md HANDOVER.md
  git commit -m "docs: record milestone 7 verification checkpoint"
  ```

- [ ] **Step 8: Ask for checkpoint approval**

  Present the four milestone tasks, verification totals, independent-review result, known caveats, and commit list. Do not begin Milestone 8 until the user explicitly approves the Milestone 7 checkpoint.
