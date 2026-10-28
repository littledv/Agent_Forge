# AgentForge Milestone 7 API and Python SDK Design

Date: 2026-10-27  
Status: Approved on 2026-10-27

## Intent

Milestone 7 turns the existing local HTTP API into a stable, documented v1
contract and adds a small typed Python client. A caller should be able to submit
a support-report run, reconnect after interruption, observe progress, make an
exact approval decision, and retrieve the verified report without learning the
server's persistence or Temporal internals.

This milestone is a compatibility and usability layer over the proven runtime.
It does not change Workflow coordination, ownership boundaries, model behavior,
or the dashboard.

## Goals

- Stabilize the existing `/api/v1` routes, request and response shapes, status
  meanings, and error envelope with executable contract tests.
- Provide a synchronous, typed `AgentForgeClient` for the complete supported
  run lifecycle.
- Define timeout, retry, idempotency, bounded-list, polling, and error behavior
  precisely enough that callers do not have to guess.
- Prove duplicate submission and interrupted polling behavior.
- Keep the client local-first, credential-free, and dependency-neutral.

## Non-goals

- An asynchronous client, command-line application, generated multi-language
  SDK, streaming transport, callbacks, authentication, or remote deployment.
- A new scheduler or any client-side workflow state machine.
- Cursor pagination or an exhaustive historical export API.
- Automatic retries of mutating requests.
- Breaking changes to the current v1 response bodies or FastAPI
  `{"detail": "..."}` error envelope.

## Approaches considered

### Selected: handwritten synchronous client

Build a narrow client on the already pinned `httpx.Client` and existing
Pydantic contracts. This is the smallest surface that satisfies the milestone,
fits ordinary scripts, permits deterministic transport tests, and introduces no
dependency change.

### Deferred: paired synchronous and asynchronous clients

An async client could suit service integrations, but it would roughly double
the lifecycle, polling, transport, and documentation surface before there is a
proven consumer. Its public contract can be designed later from observed use.

### Rejected: generated OpenAPI client

Generation is attractive for a broad external API, but here it would add a
generator toolchain and a much larger generated surface than the eight small
operations need. The OpenAPI document remains useful as executable evidence,
not as a code-generation dependency.

## Stable v1 HTTP contract

Milestone 7 preserves these existing operations:

| Operation | Route | Successful result |
|---|---|---|
| Liveness | `GET /health/live` | health body |
| Readiness | `GET /health/ready` | health body |
| Model profiles | `GET /api/v1/model-profiles` | profile list |
| Submit run | `POST /api/v1/runs` | `202` new or `200` exact duplicate |
| Recent runs | `GET /api/v1/runs?limit=N` | newest-first run list |
| Run summary | `GET /api/v1/runs/{run_id}` | run snapshot |
| Run detail | `GET /api/v1/runs/{run_id}/detail` | detailed projection |
| Approval | `GET /api/v1/runs/{run_id}/approval` | approval snapshot |
| Decision | `POST /api/v1/approvals/{approval_id}/decision` | `202` new or `200` duplicate |
| Cancel | `POST /api/v1/runs/{run_id}/cancel` | `202` new or `200` duplicate |
| Artifact | `GET /api/v1/runs/{run_id}/artifact` | verified artifact content |

Contract tests will assert route/method availability, documented success and
error statuses, schema references, required fields, enum values, and the
`detail` error envelope. They will target the intentional public surface rather
than snapshotting the whole OpenAPI document, which would make harmless
metadata changes breaking.

Existing v1 field names and meanings are frozen. Additive response fields are
allowed within v1, but removal, renaming, type changes, new required request
fields, status reinterpretation, or enum changes require a new version or an
explicit compatibility decision. To support additive evolution, SDK response
adapters will ignore unknown response fields while continuing to validate all
known fields. Server-side request models remain strict and reject unknown
inputs.

Errors retain the current JSON envelope. The stable contract is the HTTP status
and operation context; human-readable `detail` text is exposed but callers must
not branch on its exact wording.

## Python client surface

The package will expose a synchronous `AgentForgeClient` that owns an
`httpx.Client`, supports `with`, and has an idempotent `close()` method. The
default base URL is `http://127.0.0.1:8000`. Callers may supply another URL,
request timeout, or an injected transport for tests. Environment proxy
settings will not be trusted by default, so loopback traffic cannot be silently
redirected; an advanced caller may opt in explicitly.

The exact public methods are:

- `list_model_profiles() -> tuple[ModelProfile, ...]`
- `submit_run(*, submission_key, goal, ticket_files, model_profile_id="mock-v1") -> Run`
- `list_runs(*, limit=20) -> tuple[RunListItem, ...]`
- `get_run(run_id) -> Run`
- `get_run_detail(run_id) -> RunDetail`
- `get_approval(run_id) -> Approval`
- `decide_approval(approval_id, *, action_hash, decision) -> Approval`
- `cancel_run(run_id) -> Cancellation`
- `get_artifact(run_id) -> ArtifactContent`
- `wait_for_run(run_id, *, states=TERMINAL_RUN_STATES,
  timeout_seconds=300.0, poll_interval_seconds=1.0) -> Run`

Inputs use these explicit keyword forms rather than accepting two competing
model-or-keyword call styles. `ticket_files` accepts any finite sequence of
strings and is serialized as a JSON list. Run and approval IDs accept UUID
objects or canonical UUID strings. Decisions accept the public decision enum or
its exact string value. Public results are the named, client-facing Pydantic
models. Those response adapters are deliberately tolerant of unknown fields;
the server's existing strict domain models remain the source of semantic field
definitions.

The package root will export `AgentForgeClient`, the seven named result types,
`RunState`, `ApprovalDecision`, `TERMINAL_RUN_STATES`, and every public error
class listed below. Request-only adapters and internal transport helpers remain
private.

The constructor is
`AgentForgeClient(base_url="http://127.0.0.1:8000", *, timeout_seconds=10.0,
transport=None, trust_env=False)`. It always creates and owns its underlying
`httpx.Client`; an injected `httpx.BaseTransport` exists only for tests and
special transports. Supplying a prebuilt `httpx.Client` is not supported, so
ownership and setting precedence cannot be ambiguous. `close()` always closes
the owned client and is idempotent.

## Polling and reconnection

`wait_for_run` performs only repeated `GET` requests. The caller supplies one
or more target states, which permits both the approval pause and terminal-state
workflows. A convenience terminal-state constant includes `SUCCEEDED`,
`FAILED`, `DENIED`, `CANCELLED`, `TIMED_OUT`, `BUDGET_EXHAUSTED`, and
`NEEDS_ATTENTION`.

The overall polling deadline uses a monotonic clock. Each HTTP request also has
the configured request timeout. Every poll uses the lesser of that timeout and
the remaining overall budget, and every sleep is capped at the remaining
budget. JSON parsing and local model validation may add only local processing
time after the network deadline. When the overall wait expires, the client
raises a polling-timeout error containing the run ID and most recent validated
snapshot, if one was observed. It does not cancel or mutate the run.

A transport failure, per-request timeout, `503`, or other request error is
raised immediately. `wait_for_run` does not conceal failures with an internal
retry policy; the caller can invoke it again with the same run ID. If the run
reaches any terminal state that was not requested, polling raises
`AgentForgeRunTerminated` immediately with the terminal snapshot instead of
waiting for a state that can no longer occur. Reaching a requested state,
including a requested terminal state, returns its snapshot.

Polling has no cursor or session token. A process interrupted at any point
resumes safely by constructing a client and polling the same run ID. A test
will stop one polling attempt, advance the server projection, and confirm a new
client observes the eventual state.

## Retries and idempotency

The SDK performs no automatic retry of submissions, approval decisions, or
cancellations. This prevents a transport ambiguity from being hidden and keeps
the caller in control of timing.

Safe recovery is explicit:

- The caller creates and persists a stable submission key before submitting.
- Repeating the same submission key and exact body returns the original run.
- Reusing the key with a changed body raises a conflict error.
- Approval decisions remain bound to the exact approval ID and action hash;
  repeating the exact decision is safe, while a conflicting decision fails.
- Cancellation uses the server's existing deduplication behavior.
- Read-only GET requests and polling may be repeated by the caller at any time.

Transport and request timeouts therefore say that the outcome may be unknown;
they never claim the server did not receive the request. Documentation will
show how to retry with the same idempotency material or reconnect by run ID.

## List behavior

The v1 runs endpoint is a bounded recent window, not full pagination. The
client preserves its existing `limit` range of 1 through 50 and returns results
newest first. It validates the range before sending a request. Documentation
will state that callers must not use this endpoint as an exhaustive audit or
backup mechanism. Cursor pagination is deferred until a real consumer needs a
stable historical traversal contract.

## Error model

All SDK failures derive from `AgentForgeClientError` and preserve useful
operation context without including request bodies or private ticket content.
The public categories are:

- `AgentForgeValidationError` for local input failures or HTTP 422.
- `AgentForgeNotFoundError` for HTTP 404.
- `AgentForgeConflictError` for HTTP 409.
- `AgentForgeUnavailableError` for HTTP 503.
- `AgentForgeTransportError` for connection and other HTTP transport failures.
- `AgentForgeRequestTimeout` for a single request timeout.
- `AgentForgePollingTimeout` for an exhausted overall wait.
- `AgentForgeRunTerminated` when polling reaches a terminal state outside the
  caller's requested states.
- `AgentForgeProtocolError` for malformed JSON, an invalid documented response,
  or an unexpected HTTP status.

FastAPI validation errors may use either an application string detail or its
native structured list detail. String detail is available as diagnostic
context. Structured validation detail is normalized to the safe message
`request validation failed`; raw validation items and arbitrary response bodies
are neither retained nor exposed because they may echo submitted values.
Exceptions carry the status code when one exists.

`get_artifact` does not merely trust a syntactically valid response. It encodes
the returned text as UTF-8 and checks both the declared byte length and SHA-256
against the artifact reference. A mismatch raises `AgentForgeProtocolError`.

## Files and boundaries

Expected implementation stays small:

- `src/agentforge/client.py` for the public client and polling behavior.
- `src/agentforge/client_models.py` for additive-compatible response adapters.
- `src/agentforge/client_errors.py` for the exception hierarchy.
- `src/agentforge/__init__.py` for intentional public exports.
- Focused client and API-contract tests under `tests/`.
- API/SDK usage documentation and a Milestone 7 learning chapter.

The client calls only the HTTP API. It never imports repositories, touches
PostgreSQL, contacts Temporal, reads artifact files directly, or reconstructs
Workflow state. PostgreSQL remains the application-record authority and
Temporal remains the execution-history authority.

## Verification strategy

1. Contract tests exercise FastAPI's generated OpenAPI surface and representative
   live responses, including error envelopes and duplicate status codes.
2. Unit tests use `httpx.MockTransport` to prove URL construction,
   serialization, response validation, error mapping, timeout handling,
   redaction, and client lifecycle.
3. Polling tests use a fake monotonic clock/sleeper or zero-delay scripted
   transport to remain fast and deterministic.
4. API integration tests prove an exact duplicate submission returns one run
   and a changed payload for the same key conflicts.
5. An interrupted-polling test reconstructs the client and resumes from the
   same run ID without duplicate execution or cancellation.
6. Artifact tests prove valid content and reject size or SHA-256 mismatches.
7. A small acceptance program uses the real loopback stack to submit, observe,
   approve the exact pending action, wait for success, and retrieve the verified
   report.
8. The existing backend, migration, replay/recovery, dashboard, and privacy
   checks run in proportion to affected boundaries before completion.

## Documentation deliverables

The README will gain a concise Python example. A focused API/SDK reference will
document every method, status/error mapping, bounded list semantics, request
versus polling timeouts, safe retry recipes, and reconnection. The learning
chapter will explain the design in plain language and distinguish a durable
server run from a disposable client polling session.

## Acceptance

Milestone 7 is acceptable when a short, documented Python program can use the
typed client against the loopback deployment to:

1. submit a run with a stable submission key;
2. recover the same run from an exact duplicate submission;
3. observe it through an interrupted and resumed polling session;
4. retrieve the pending approval and approve the exact action hash;
5. wait for a successful terminal state; and
6. retrieve and validate the report artifact.

All contract, client, integration, and relevant regression checks must pass,
and no external deployment, credential, paid resource, or non-loopback port is
introduced.
