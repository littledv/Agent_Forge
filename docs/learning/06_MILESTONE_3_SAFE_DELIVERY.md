# Milestone 3: Approval-Gated, Deduplicated Delivery

Milestone 3 adds a sensitive external action to AgentForge without letting
model output authorize itself. The support-report Workflow now pauses after
publishing its artifact, asks for approval of one exact delivery action, and
calls a separate simulated inbox only after trusted code confirms the stored
decision.

## The trust boundary

The model may propose `deliver_report`, but it cannot choose an arbitrary tool,
destination URL, approval identifier, or operation identifier. Trusted code
accepts only the fixed `simulated-inbox` target and requires the proposed
artifact path, media type, size, and SHA-256 to equal the artifact produced by
the publication Activity.

Canonical JSON of that action produces an action hash. UUIDv5 derivation from
the run and action hash produces stable approval and operation IDs. A changed
argument therefore cannot reuse the old approval.

```text
published artifact
  -> exact deliver_report action
  -> canonical action hash
  -> stable approval ID
  -> stable operation ID
```

The URL remains trusted configuration. Report bytes are reread and verified in
the delivery Activity immediately before the HTTP request, so the approved
metadata cannot silently authorize changed file contents.

## Durable decisions and signals

PostgreSQL is authoritative for approval, expiry, cancellation, and accepted
receipt records. Temporal is authoritative for Workflow history, timers,
signals, and Activity retries.

The API locks the approval row when recording a decision. That transaction also
creates a signal-outbox row. The relay may resend after a crash, so Workflow
handlers ignore duplicate message IDs. Signals wake the Workflow; they do not
authorize delivery by themselves. Before scheduling the HTTP Activity, the
Workflow calls PostgreSQL again to resolve the locked approval-versus-expiry
winner and detect a cancellation whose signal may be delayed.

Rejection and expiry produce `DENIED`. Cancellation before delivery produces
`CANCELLED` with no inbox row. Cancellation after a receiver commit keeps the
receipt, stops later work, and produces `CANCELLED`; AgentForge does not claim
that cancellation reverses an external side effect.

## Receiver ownership and deduplication

The simulated inbox is a separate loopback FastAPI service with its own
database and Alembic history. AgentForge can reach it only through HTTP. The
inbox verifies content against artifact metadata and owns the unique constraint
on `operation_id`.

The first canonical request commits a row and returns a receipt. An identical
retry returns that same receipt. Reusing the operation ID with changed content
returns a conflict. AgentForge validates the returned operation ID and artifact
hash, then stores the accepted receipt before the Activity returns to Temporal.

## Crash-after-commit proof

The native recovery test wraps the production inbox client. Attempt 1 performs
the real HTTP request and validates the committed receipt, writes an atomic
test marker, and blocks before returning. Pytest terminates that Worker and
starts an ordinary replacement on the same task queue. After the real
ten-second Activity timeout, Temporal schedules attempt 2 with the same
operation ID. The inbox returns its original receipt and retains one row.

The verified run on 2026-10-24 produced:

- Run ID: `5bde6bae-3d58-41b2-8768-fbc94c88ab6b`
- Workflow ID: `agentforge/run/5bde6bae-3d58-41b2-8768-fbc94c88ab6b`
- Approval ID: `cf364749-b783-5b8f-ada0-622714bf452b`
- Action hash:
  `546979a709abf727295cd950bd249dd6d59a98fd18c5c80a516d5691e7e308c0`
- Operation ID: `0b2c487b-6ad9-575e-8e35-1c76669a4861`
- Receiver delivery ID: `ba695e71-cc97-4c98-8b7a-9424ad4d966e`
- Attempt 1: `FAILED` / `RETRYABLE`
- Attempt 2: `SUCCEEDED`
- Receiver rows for the operation: `1`
- Focused pytest result: `1 passed in 15.88s`
- Measured command elapsed time: `17.046` seconds
- Remaining API/inbox listeners after cleanup: `0`

Repeat the proof from PowerShell with PostgreSQL and Temporal running:

```powershell
.\.venv\Scripts\python.exe -m pytest `
  tests/integration/test_delivery_recovery.py -vv
```

## Ordinary application restart proof

A separate native demonstration stopped the API, relay, inbox, and Worker while
run `ad1aad40-3f4f-496b-8ee0-a9e77ec894a3` was `WAITING_APPROVAL`. PostgreSQL
still held pending approval `f6f206f8-6b75-5389-a935-bd955642e39b`, and
Temporal retained the waiting Workflow. After all four processes restarted,
the same stored action was approved and the original Workflow reached
`SUCCEEDED` with one artifact, one AgentForge receipt, and one inbox row.

## What the result does not prove

This is not universal exactly-once delivery. The observed property depends on
the receiver durably deduplicating a stable operation ID and returning the
original receipt. An arbitrary external API without idempotency or a reliable
reconciliation endpoint can still commit while the caller loses the response.
AgentForge treats an unreconciled result as `NEEDS_ATTENTION` / `UNCERTAIN`
rather than claiming success.
