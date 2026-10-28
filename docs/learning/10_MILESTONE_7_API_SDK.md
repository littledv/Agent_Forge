# Milestone 7: A Disposable Client for a Durable Run

Milestone 7 adds a small typed Python SDK without changing how AgentForge runs
work. The important idea is that the Python process watching a run is not the
run itself.

## Two different lifetimes

When a script submits work, the API first records the run in PostgreSQL. The
relay starts its Temporal Workflow, and Workers carry out Activities. Those
server-side records and the Workflow history remain even if the submitting
script exits, crashes, loses its network connection, or reaches its own polling
deadline.

The SDK's polling loop is only an observer. It repeatedly asks the API for the
latest durable snapshot. It holds no secret cursor and owns no part of Workflow
progress. That is why a replacement process needs only the run ID:

```text
script A submits -> durable run ID -> script A stops
                                      |
script B starts  -> same run ID ------+-> current durable state
```

Stopping polling does not cancel the run. Cancellation is a separate, explicit
mutation.

## Why mutations are not silently retried

Suppose the server commits a submission, but the response is lost. The client
sees a timeout, yet the run may already exist. An automatic retry would hide
that uncertainty.

AgentForge instead uses explicit idempotency material:

- a stable submission key plus the exact submission body;
- an approval ID plus the exact action hash and decision; and
- the server's durable cancellation record.

Repeating the exact submission returns the same run. Changing the body while
reusing the key is a conflict. This makes recovery visible and testable.

## Two timeout layers

The request timeout limits one HTTP exchange. The polling timeout limits the
whole wait. A polling timeout means only "this observer stopped waiting." It
does not mean the run failed, stopped, or was never accepted.

The SDK uses a monotonic deadline and never lets a request or sleep exceed the
remaining polling budget. It also surfaces transport and server failures
immediately instead of turning them into invisible retries.

## Typed does not mean brittle

The client validates every known response field and returns immutable Pydantic
models. It ignores unknown response fields so the v1 API can add optional
information without breaking older clients. Missing known fields, invalid
types, malformed JSON, and unexpected statuses still fail closed.

Artifact retrieval adds one more boundary check: the SDK verifies the UTF-8
byte length and SHA-256 reported by the server before accepting report text.

## What the acceptance test proves

The native lifecycle test submits one run twice and receives one run ID. A
changed submission conflicts. The first SDK client times out while polling and
closes. After the relay starts the durable Workflow, a new SDK client uses the
same run ID, observes approval, approves the exact action, reaches success, and
retrieves a checksum-verified artifact. Database and inbox assertions confirm
one durable run and one delivery.

This proves reconnection through the public HTTP boundary. It does not turn the
SDK into a scheduler, replace Temporal, or make the recent-runs window an audit
export.

See [the API and SDK reference](../API_SDK.md) for the exact public contract.
