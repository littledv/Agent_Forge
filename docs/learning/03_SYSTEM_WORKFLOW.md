# AgentForge System Workflow

Everything in this document is planned until its milestone is marked complete
in `PROJECT_PLAN.md`.

## The high-level idea

```mermaid
flowchart TB
    Goal[1. User submits a goal] --> Record[2. API records a run]
    Record --> Start[3. Temporal starts durable coordination]
    Start --> Decide[4. Mock model chooses a schema-valid action]
    Decide --> Tool[5. Trusted Activity performs allowed work]
    Tool --> Observe[6. Result and progress become inspectable]
    Observe --> More{7. More work?}
    More -->|yes| Decide
    More -->|sensitive action| Approval[Wait for durable approval]
    Approval --> Tool
    More -->|no| Finish[Record terminal outcome]
```

## Who owns what?

```mermaid
flowchart LR
    subgraph Execution authority
        TH[Temporal history]
        WT[Workflow timers and retries]
    end

    subgraph Product authority
        AD[Agent definitions]
        RR[Run and approval records]
        DP[Dashboard projections]
    end

    subgraph External data
        AR[Artifact files]
        IR[Inbox delivery records]
    end

    TH --- WT
    AD --- RR
    RR --- DP
```

Temporal answers “what execution happened and what should be scheduled next?”
PostgreSQL answers “what AgentForge objects should the product display and
query?” Artifact and inbox storage retain their own data.

## Why the API returns early

```mermaid
sequenceDiagram
    actor User
    participant API
    participant DB
    participant Temporal

    User->>API: POST run
    API->>DB: Commit run plus start intent
    API-->>User: 202 Accepted plus run ID
    DB->>Temporal: Start workflow asynchronously
    User->>API: GET run status later
```

Keeping one HTTP request open for an entire agent run is fragile. Returning a
run ID lets the client disconnect and come back while the durable Workflow
continues independently.

## Why a stable operation ID matters

Suppose the inbox commits a delivery, but the worker crashes before telling
Temporal that the Activity finished. Temporal cannot know whether the call
committed, so it retries. The second attempt must use the same operation ID.
The inbox sees the existing ID and returns the original result.

```text
Logical operation: deliver report R to simulated inbox
Operation ID:      delivery/<run-id>/<report-hash>
Physical attempts: attempt 1, attempt 2, ...
Observable result: one inbox delivery
```

## Approval boundary

Approval is not “the user generally trusts the agent.” It is permission for a
specific proposal:

```text
agent version + run + tool + normalized arguments + artifact hash + operation ID
```

If any of these changes, the earlier approval cannot be reused.

## Full planned recovery demonstration

```mermaid
flowchart TD
    A[Submit stable request] --> B[Start durable Workflow]
    B --> C[Complete early steps]
    C --> D[Crash worker]
    D --> E[Restart worker and replay history]
    E --> F[Create report artifact]
    F --> G[Persist approval request]
    G --> H[Restart while approval is pending]
    H --> I[Approve exact delivery]
    I --> J[Inbox commits operation ID]
    J --> K[Crash before Activity acknowledgement]
    K --> L[Temporal retries same operation ID]
    L --> M[Inbox returns original delivery]
    M --> N[Assert one delivery and successful run]
```

Logs help explain this demonstration, but database and receiver assertions are
the proof.
