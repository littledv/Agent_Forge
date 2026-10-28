# Milestone 1: From Submission to Durable Report

Milestone 1 turns the AgentForge design into one small complete local path. A
client submits a goal and an ordered set of approved ticket names. The API
returns a run ID promptly; a separate relay starts a Temporal Workflow; a
Worker produces a deterministic report; and PostgreSQL exposes the resulting
state and artifact metadata.

## Why there are two durable systems

PostgreSQL is the authority for application records: submissions, run state,
steps, attempts, artifact metadata, and the start outbox. Temporal is the
authority for execution history: which Workflow and Activity tasks were
scheduled, retried, and completed. Neither is treated as a replacement for the
other.

The API transaction inserts both the run and a `workflow_start_outbox` row.
This avoids trying to atomically commit to PostgreSQL and Temporal. The relay
claims an outbox row, starts the stable Workflow ID
`agentforge/run/<run-id>`, and marks the row started. If Temporal accepted the
start but the database acknowledgement failed, a retry receives
`WorkflowAlreadyStartedError` for that same ID and safely completes the mark.

## Workflow and Activity boundary

`SupportReportWorkflow` contains deterministic coordination only. It remembers
small references and chooses which typed Activity runs next. Database access,
model execution, file reads, and file writes happen in Activities.

The four registered Activities are:

- `project_event`, which applies an idempotent, sequence-gated PostgreSQL
  projection;
- `decide_action`, which runs the deterministic mock model and validates its
  discriminated action schema;
- `read_tickets`, which reads only approved UTF-8 `.txt` and `.md` files under
  the configured root; and
- `publish_report`, which verifies a draft reference and atomically publishes
  the final Markdown artifact.

The decision loop is capped at four model decisions. Invalid paths and invalid
model output are non-retryable application failures. Other Activity failures
use a short three-attempt policy. Step IDs are UUIDv5 values derived from run ID
and ordinal; attempt IDs are derived from step ID and Temporal attempt number.
The Workflow also binds model actions back to trusted state: a read action must
match the submitted ticket allowlist, a publish action must carry the trusted
snapshot, and a finish action must carry the artifact returned by publication.

## Schema migration and projections

Alembic revision `0001_initial_records` creates versioned agent definitions,
runs, steps, attempts, artifacts, start-outbox rows, and projection-event rows.
It seeds `support-report` version `1`. State values are checked strings, UUIDs
use PostgreSQL UUID columns, timestamps include time zones, and durable payloads
use JSONB.

Each projection event has a unique event ID and monotonic sequence. Replaying
the same event is harmless. A lower sequence is recorded for audit but cannot
replace a newer run projection. PostgreSQL remains a view of execution state;
Temporal remains the execution-history authority.

## File safety, atomicity, and checksums

Trusted storage code rejects absolute paths, parent traversal, missing files,
directories, unsupported extensions, symlink components, invalid UTF-8, and
configured byte-limit violations. It reads and snapshots tickets in lexical
path order.

Drafts and reports are first flushed to a temporary file under
`artifacts/.staging/<run-id>/` and then moved with `os.replace` on the same
filesystem. Failed replacement leaves no partial final report. References
carry a lowercase SHA-256, byte size, and relative path; publication verifies
the draft before copying it. Ticket and report bodies never cross the Workflow
boundary, so Temporal history contains compact references rather than large
content.

## Idempotency demonstrated

The canonical submission JSON is serialized with sorted keys and compact
separators, then hashed with SHA-256. Repeating the same submission key and
canonical payload returns the original run. Reusing that key for different
content returns HTTP `409`. Concurrent equal submissions converge on the same
database row. This is retry-safe submission, not a claim of universal
exactly-once behavior.

## What was observed

Automated tests exercised the real local PostgreSQL and Temporal services,
including the complete API-to-artifact slice, retry identity, decision-budget
exhaustion, invalid model output, path denial, projection replay, and stale
projection rejection.

A separate PowerShell demonstration produced run
`e1727d88-cdea-4e96-ad32-4d6d7719b812`, report
`reports/e1727d88-cdea-4e96-ad32-4d6d7719b812/support-report.md`, and SHA-256
`f28ea1bf431929c3cf2ec702be511eb93051c6bfaa13959b6102555de51a4ee9`.
After `docker compose stop` followed by `docker compose up -d`, PostgreSQL still
reported `SUCCEEDED`, the artifact checksum matched, and Temporal returned 47
history events.

Worker termination during an active run, approval, simulated delivery, browser
UI behavior, and external exactly-once effects were not claimed here. They
remain later milestones.
