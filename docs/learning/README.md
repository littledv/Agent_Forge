# AgentForge Learning Guide

This folder explains the project as it evolves. It is written for learning, not
just for operating the finished software.

## Reading order

1. [01_PROJECT_JOURNEY.md](01_PROJECT_JOURNEY.md) — what we are building, in
   what order, and why.
2. [02_CONCEPTS_GLOSSARY.md](02_CONCEPTS_GLOSSARY.md) — plain-language meanings
   of distributed-systems and AI terms.
3. [03_SYSTEM_WORKFLOW.md](03_SYSTEM_WORKFLOW.md) — diagrams showing how code
   and services cooperate.
4. [04_MILESTONE_1_BUILD.md](04_MILESTONE_1_BUILD.md) — the first complete
   durable support-report path.
5. [05_MILESTONE_2_RECOVERY.md](05_MILESTONE_2_RECOVERY.md) — worker loss and
   replay-safe projections.
6. [06_MILESTONE_3_SAFE_DELIVERY.md](06_MILESTONE_3_SAFE_DELIVERY.md) — exact
   approvals and receiver-side deduplication.
7. [07_MILESTONE_4_DASHBOARD.md](07_MILESTONE_4_DASHBOARD.md) — browser polling,
   durable mutations, safe artifact viewing, and restart proof.
8. [08_MILESTONE_5_LOCAL_MODEL.md](08_MILESTONE_5_LOCAL_MODEL.md) — immutable
   model profiles, bounded local Qwen generation, usage evidence, and privacy.
9. [09_MILESTONE_6_MULTI_AGENT_GRAPH.md](09_MILESTONE_6_MULTI_AGENT_GRAPH.md) —
   fixed researcher/writer/reviewer coordination, bounded revisions, replay,
   recovery, and unchanged human approval.
10. [10_MILESTONE_7_API_SDK.md](10_MILESTONE_7_API_SDK.md) — why a disposable
    typed client can safely reconnect to a durable server run.
11. [11_MILESTONE_8_FAILURE_PORTFOLIO.md](11_MILESTONE_8_FAILURE_PORTFOLIO.md)
    — why recovery evidence must agree across application, execution, receiver,
    artifact, and operating-system authorities.

## Update rule

At the end of every milestone:

- Add what was actually built.
- Explain new terms introduced by that milestone.
- Add or update diagrams when component interactions change.
- Separate verified behavior from planned behavior.
- Prefer a small concrete example over unexplained terminology.

This folder complements the operational documents in the repository root. It
does not replace tests, Git history, or `HANDOVER.md`.
