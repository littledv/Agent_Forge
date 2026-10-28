# AgentForge API and Python SDK

This document describes the stable local `/api/v1` contract and the synchronous
Python SDK added in Milestone 7. The SDK talks only to the HTTP API. It does not
read PostgreSQL, contact Temporal, or read artifact files directly.

## Client construction and ownership

```python
AgentForgeClient(
    base_url: str = "http://127.0.0.1:8000",
    *,
    timeout_seconds: float = 10.0,
    transport: httpx.BaseTransport | None = None,
    trust_env: bool = False,
)
```

The client always creates and owns one `httpx.Client`. Use it as a context
manager or call `close()` when finished. `close()` is idempotent. Redirects are
not followed, and environment proxy settings are ignored by default so local
traffic is not silently redirected. `transport` is intended for tests and
special transports; a prebuilt `httpx.Client` is not accepted.

```python
close() -> None
```

`timeout_seconds` is the timeout for one HTTP request. It must be finite and
positive. It is separate from the overall deadline used by `wait_for_run`.

## Public methods

All IDs accept either `uuid.UUID` or a UUID string.

```python
list_model_profiles() -> tuple[ModelProfile, ...]
```

Returns the enabled public model profiles.

```python
submit_run(
    *,
    submission_key: str,
    goal: str,
    ticket_files: Sequence[str],
    model_profile_id: str = "mock-v1",
) -> Run
```

Submits a run. `ticket_files` are relative paths beneath the server's configured
ticket root. A new submission returns HTTP 202; an exact duplicate returns the
original run with HTTP 200. The SDK returns `Run` in either case.

```python
list_runs(*, limit: int = 20) -> tuple[RunListItem, ...]
```

Returns a newest-first recent window. `limit` must be an integer from 1 through
50 and is checked before any request is sent. This is not cursor pagination and
must not be treated as an exhaustive audit, export, or backup API.

```python
get_run(run_id: UUID | str) -> Run
get_run_detail(run_id: UUID | str) -> RunDetail
get_approval(run_id: UUID | str) -> Approval
get_artifact(run_id: UUID | str) -> ArtifactContent
```

These methods retrieve the current summary, detailed durable projection,
pending approval, or verified report content.

```python
decide_approval(
    approval_id: UUID | str,
    *,
    action_hash: str,
    decision: ApprovalDecision | str,
) -> Approval
```

Decides the exact approval identified by `approval_id` and `action_hash`.
`decision` is `ApprovalDecision.APPROVE`, `ApprovalDecision.REJECT`, or the
corresponding exact string value. A new decision returns HTTP 202; an identical
repeat returns HTTP 200. A changed hash or conflicting decision fails.

```python
cancel_run(run_id: UUID | str) -> Cancellation
```

Persists a cancellation request. A new request returns HTTP 202; an identical
repeat returns HTTP 200. Cancellation stops future work but cannot undo a side
effect that a receiver has already committed.

```python
wait_for_run(
    run_id: UUID | str,
    *,
    states: Collection[RunState | str] = TERMINAL_RUN_STATES,
    timeout_seconds: float = 300.0,
    poll_interval_seconds: float = 1.0,
) -> Run
```

Polls with read-only `GET` requests until the run reaches a requested state.
`states` must be non-empty. Both timing values must be finite and positive.
`TERMINAL_RUN_STATES` contains `SUCCEEDED`, `FAILED`, `DENIED`, `CANCELLED`,
`TIMED_OUT`, `BUDGET_EXHAUSTED`, and `NEEDS_ATTENTION`.

The overall deadline uses a monotonic clock. Every request timeout and sleep is
capped to the remaining overall budget. If the run reaches an unrequested
terminal state, the client raises `AgentForgeRunTerminated` immediately. If the
deadline expires, it raises `AgentForgePollingTimeout`. Neither case changes or
cancels the server run.

`wait_for_run` does not hide a connection failure, request timeout, HTTP error,
or invalid response by retrying it internally. Call it again with the same run
ID when the caller's own policy says to resume.

## Public models

The package root exports immutable Pydantic result models. Known fields are
strictly validated; unknown response fields are ignored so the v1 server can
add optional fields compatibly.

| Model | Stable information |
|---|---|
| `ModelProfile` | Profile ID, display name, provider, model name, optional digest |
| `Run` | Run/workflow IDs, state, timestamps, optional failure kind and artifact reference |
| `RunListItem` | Run ID, goal, state, failure kind, timestamps |
| `RunDetail` | Run, goal, submitted files, immutable profile snapshot, projection sequence, ordered steps/attempts, approval, cancellation flag, delivery receipt |
| `Approval` | Approval/run/operation IDs, exact action hash and action, state, expiry and decision timestamps |
| `Cancellation` | Cancellation request ID, run ID, creation time |
| `ArtifactContent` | Artifact metadata plus report text |

Artifact references contain a relative path, SHA-256, UTF-8 byte size, and
media type. `get_artifact` re-encodes the returned text as UTF-8 and verifies
both byte length and SHA-256. A mismatch raises `AgentForgeProtocolError`; the
caller must not use the content.

The package root also exports `RunState`, `ApprovalDecision`, and
`TERMINAL_RUN_STATES`.

## Errors and HTTP status mapping

Every SDK exception derives from `AgentForgeClientError`. Exceptions include
the operation name and, when applicable, the HTTP status. They do not retain
request bodies, ticket contents, arbitrary response bodies, or raw structured
FastAPI validation items.

| Condition | SDK error |
|---|---|
| Invalid local argument or HTTP 422 | `AgentForgeValidationError` |
| HTTP 404 | `AgentForgeNotFoundError` |
| HTTP 409 | `AgentForgeConflictError` |
| HTTP 503 | `AgentForgeUnavailableError` |
| Connection or other HTTP transport failure | `AgentForgeTransportError` |
| One HTTP request exceeds its request timeout | `AgentForgeRequestTimeout` |
| Overall polling deadline expires | `AgentForgePollingTimeout` |
| Run reaches an unrequested terminal state | `AgentForgeRunTerminated` |
| Unexpected status, malformed JSON, invalid known response field, or artifact mismatch | `AgentForgeProtocolError` |

`AgentForgePollingTimeout` retains the run ID and the last validated `Run`, if
one was received. `AgentForgeRunTerminated` retains the run ID and terminal
snapshot. HTTP validation lists are reduced to `request validation failed`.
Human-readable server detail is diagnostic only; programs must branch on the
exception class and status, not exact wording.

The documented successful and error statuses are:

| Operation | Success | Documented errors |
|---|---|---|
| Submit | 200, 202 | 409, 422, 503 |
| Profiles | 200 | — |
| Recent runs | 200 | 422, 503 |
| Run, detail, approval | 200 | 404, 422, 503 |
| Approval decision | 200, 202 | 404, 409, 422, 503 |
| Cancellation | 200, 202 | 404, 409, 422, 503 |
| Artifact | 200 | 404, 409, 422, 503 |

## Safe retries and unknown outcomes

The SDK never automatically retries a mutation. A connection failure or
request timeout means the outcome may be unknown: the server might have
committed the request before the response was lost.

Use this recovery pattern:

1. Create and persist a stable submission key before submitting.
2. If submission has an unknown outcome, repeat the exact body with that key.
   The server returns the original run. A changed body raises
   `AgentForgeConflictError`.
3. Persist the returned run ID. Polling can stop at any time; a new process can
   construct a client and call `get_run` or `wait_for_run` with that ID.
4. Fetch the current approval, then decide using its exact approval ID and
   action hash. An identical repeat is safe; never substitute a stale hash.
5. Repeating cancellation is safe, but still does not reverse committed work.

Read-only operations can be repeated according to the caller's policy.

## Complete lifecycle example

The API, relay, inbox, and Worker must already be running on loopback.

```python
from agentforge import AgentForgeClient, ApprovalDecision, RunState

with AgentForgeClient() as client:
    run = client.submit_run(
        submission_key="sdk-demo-001",
        goal="Find recurring support themes",
        ticket_files=["ticket-001.txt", "ticket-002.txt"],
    )
    client.wait_for_run(run.run_id, states={RunState.WAITING_APPROVAL})
    approval = client.get_approval(run.run_id)
    client.decide_approval(
        approval.approval_id,
        action_hash=approval.action_hash,
        decision=ApprovalDecision.APPROVE,
    )
    final = client.wait_for_run(run.run_id)
    if final.state == RunState.SUCCEEDED:
        artifact = client.get_artifact(run.run_id)
        print(artifact.artifact.path, artifact.artifact.sha256)
```

The interactive executable example at
[`examples/python_sdk_lifecycle.py`](../examples/python_sdk_lifecycle.py) prints
IDs, state, path, and hash, but never ticket or report bodies.

## v1 compatibility rules

Existing field names and meanings, request requirements, status meanings, and
enum values are stable within v1. Optional response fields may be added.
Removing or renaming fields, changing their types, adding required request
fields, reinterpreting statuses, or changing enums requires a new API version
or an explicit compatibility decision. Server request models remain strict and
reject unknown input fields.
