# Milestone 2: Worker Failure and Durable Recovery

Milestone 2 proves the central durability claim in AgentForge: the Worker
process is replaceable. A support-report run does not depend on one Python
process retaining its in-memory state until completion.

## What the recovery test does

The test uses the real loopback PostgreSQL and Temporal services and launches
Workers as separate native Windows Python processes. Its first Worker uses a
test-only model wrapper. The normal Workflow completes its first model decision
and its `read_tickets` Activity. When the second `decide_action` Activity
starts, the wrapper writes a marker and blocks.

Pytest observes the marker and terminates the entire first Worker process. It
then starts an ordinary replacement Worker on the same Temporal task queue. No
new run or Workflow is submitted.

The relevant sequence is:

```text
Workflow starts
  -> decision 1 succeeds
  -> ticket read succeeds
  -> decision 2 attempt 1 starts
  -> Worker process is terminated
  -> Start-To-Close timeout expires
  -> replacement Worker polls the same task queue
  -> decision 2 attempt 2 succeeds
  -> report publication and finish succeed
```

## Why progress survives

Temporal, not the Worker, owns execution history. The replacement Worker
replays the Workflow from that history. Completed Activities return their
recorded results during replay instead of running again, so the successful
ticket read remains complete. The Activity that was still running when its
process died has no completion acknowledgement. Temporal detects that through
the existing 10-second Start-To-Close timeout and schedules the next attempt
under the existing bounded retry policy.

The closed Temporal history records the successful start of the interrupted
`decide_action` Activity as attempt `2`. Temporal Server 1.32.1 compresses the
intermediate retry timeout rather than retaining a standalone timeout event in
the returned closed history. PostgreSQL provides the complementary
application-facing projection: attempt `1` is `FAILED` with failure kind
`RETRYABLE`, and attempt `2` is `SUCCEEDED`.

## Keeping attempt projections monotonic

Each logical step keeps its deterministic UUIDv5 ID across retries. When a
newer attempt starts, `record_activity_attempt` locks the logical step row and
reclassifies only older attempts that are still `RUNNING` as
`FAILED`/`RETRYABLE`. An older late terminal update cannot overwrite that state
after a higher attempt exists. An older attempt that genuinely reached a
terminal state before the retry began remains terminal.

This logic does not decide whether Temporal should retry. It records what
Temporal already did. Temporal remains the scheduler and execution-history
authority; PostgreSQL remains the application-record and projection authority.

## Observed native demonstration

The verified run on 2026-10-24 produced:

- Run ID: `27557229-c1f0-4955-9939-326c92e471a6`
- Workflow ID: `agentforge/run/27557229-c1f0-4955-9939-326c92e471a6`
- Terminal state: `SUCCEEDED`
- Temporal retry attempt: `2`
- Ticket-read attempts: `1 SUCCEEDED`
- Interrupted decision attempts: `1 FAILED/RETRYABLE`, `2 SUCCEEDED`
- Artifact: `reports/27557229-c1f0-4955-9939-326c92e471a6/support-report.md`
- Artifact SHA-256:
  `b537af694f56b3b16a4c1e33423c0eca6595264ce5828b978474b23176ab705d`
- Artifact size: `268` bytes
- Focused pytest result: `1 passed in 13.84s`
- Remaining recovery Worker processes after cleanup: `0`

Repeat the proof from PowerShell after starting the documented local services:

```powershell
.\.venv\Scripts\python.exe -m pytest `
  tests/integration/test_worker_recovery.py -vv
```

## What this does not prove

This milestone does not add or prove approval persistence, rejection or expiry,
cancellation, inbox delivery, crash-after-external-commit reconciliation, or
exactly-one receiver delivery. Those are Milestone 3 concerns. It also does not
claim that an arbitrary external side effect becomes exactly once merely
because its Activity is retried.
