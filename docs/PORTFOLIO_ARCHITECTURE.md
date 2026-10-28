# Portfolio Architecture

The portfolio reads each durable fact from the component that owns it. Logs are
diagnostics, not an extra authority or scheduler.

```mermaid
flowchart LR
    C[SDK client] --> A[API A or B]
    A --> P[(Application PostgreSQL<br/>run, approval, step, artifact, delivery)]
    P --> R[Outbox relays]
    R --> T[Temporal<br/>execution history]
    T --> W[Workers A-D]
    W --> P
    W --> I[Simulated inbox API]
    I --> D[(Inbox PostgreSQL<br/>receiver receipt)]
    W --> F[(Artifact files)]
```

PostgreSQL is authoritative for application records and projections. Temporal
is authoritative for Workflow execution, timers, Activity attempts, and
replay. Inbox PostgreSQL is authoritative for the receiver-visible side effect.
Artifact files hold report bytes; PostgreSQL stores their relative path, size,
media type, and SHA-256.

```mermaid
sequenceDiagram
    participant T as Temporal
    participant WA as Worker A
    participant WB as Worker B
    participant P as PostgreSQL
    participant API as API A then B
    participant R as Signal relay
    participant WC as Worker C
    participant I as Inbox
    participant WD as Worker D

    T->>WA: review_report_r0 attempt 1
    WA--xT: process lost
    T->>WB: same Activity attempt 2
    WB->>P: project pending approval
    API->>P: read the pending approval
    API--xAPI: reconstruct service
    API->>P: store decision and signal outbox
    R->>P: claim exact signal intent
    R->>T: exact approval signal
    T->>WC: deliver stable operation ID
    WC->>I: commit receiver row
    WC--xT: process lost before acknowledgement
    T->>WD: retry same operation ID
    WD->>I: duplicate request
    I-->>WD: existing stable receipt
    WD-->>T: completed
```

## Identity, replay, and bounds

Stable run, Workflow, approval, action-hash, and operation IDs connect the
authorities across service and Worker reconstruction. Temporal replay restores
Workflow control state; Activities perform model, database, file, and network
I/O. A retried logical step keeps its identity while attempt records increase.
The inbox's unique operation ID and canonical-request equality provide receiver
deduplication after an ambiguous commit.

Version 2 has fixed researcher, writer, and reviewer roles, at most four
research shards, bounded research batches, at most two revisions, at most ten
role executions, and a graph deadline. Ordinary unrecoverable work becomes
`FAILED`; revision or graph bounds become `BUDGET_EXHAUSTED`; an unreconciled
external side effect becomes `NEEDS_ATTENTION` with an `UNCERTAIN` step.

## Claim boundary

The portfolio proves recovery against the pinned local stack and the simulated
inbox. It does not prove:

- exactly-once behavior for an arbitrary external API;
- multi-host disaster recovery;
- production authentication, authorization, or security hardening;
- production load capacity or performance targets;
- real-model correctness or report quality.

These exclusions are deliberate: receiver deduplication, deployment topology,
security controls, capacity, and semantic quality require separate evidence.
