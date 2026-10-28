# AgentForge Constraints

## Scope control

- Implement only the approved current milestone.
- Stop at a useful milestone boundary for review.
- Do not refactor unrelated code or add speculative abstractions.
- Do not add Redis, LangGraph, Kubernetes, a vector database, or a full
  monitoring stack without a concrete approved requirement.
- Start with one agent. Add researcher, writer, and reviewer roles only after
  single-agent recovery and approval behavior are proven.

## Dependency and environment rules

- Verify supported versions from official documentation before implementation.
- Pin direct dependencies and explain important compatibility choices.
- Do not install or upgrade dependencies without approval.
- Assume Windows and PowerShell. Identify commands that require WSL explicitly.
- Use Docker Desktop with the WSL2 Linux-container backend for the approved
  local baseline.
- Keep total local service usage within the agreed approximate 10–12 GB RAM
  budget.
- Publish required host ports on `127.0.0.1` only.

## Execution correctness

- Temporal owns execution and recovery history.
- PostgreSQL owns application records and dashboard projections.
- Do not implement a competing scheduler.
- Keep Workflow code deterministic.
- Put model calls, tool calls, database access, and file operations in
  Activities.
- Use stable identifiers for submissions, workflows, steps, projection events,
  approvals, and side effects.
- Make retries bounded and classify failures as retryable, permanent, denied,
  cancelled, or uncertain.
- Never claim universal exactly-once side effects.
- Cancellation stops future work; it does not undo an action that already
  committed.

## Agent and tool safety

- Validate model output and tool arguments against schemas.
- Enforce permissions in trusted code, not prompts.
- Bind approvals to the exact proposed action and canonical arguments.
- Changed arguments require a new approval.
- Arbitrary code execution is out of scope until a separate isolation design is
  approved. Docker alone is not a complete security sandbox.
- Limit steps, elapsed time, tokens, retries, and concurrency.
- Do not request or store hidden model chain-of-thought. Record concise decision
  summaries instead.

## Data and secrets

- Keep credentials out of source control, logs, artifacts, and test fixtures.
- Commit an `.env.example` later only with safe placeholder values.
- Redact sensitive inputs and outputs in observability records.
- Persist application data, Temporal history, inbox deduplication state, and
  artifacts across ordinary shutdown.
- Make destructive reset a separate, clearly labeled action.

## Git and publication

- Use the user's configured Git name and email.
- Do not add Codex, OpenAI, an AI assistant, or generated-by text to commit
  authorship, co-authorship, contributor lists, or the GitHub front page.
- Keep the repository local until a private remote is explicitly requested.
- Never make the repository public automatically.
- Do not rewrite shared history or use destructive recovery without approval.
