# AgentForge Decision Log

This is a lightweight architectural decision record. Do not silently reverse a
decision. Add a new entry that supersedes the old one and explain why.

## D-001: Use Temporal for durable execution

- **Status:** Approved
- **Decision:** Use Temporal with a small Python agent layer.
- **Reason:** Worker recovery, durable timers, signals, retries, and execution
  history are core learning and correctness goals.
- **Rejected alternatives:** LangGraph checkpoints as the main runtime and a
  custom scheduler. Both would require more application-owned recovery logic.
- **Cost:** More local services and deterministic-Workflow constraints.

## D-002: Keep execution and application records separate

- **Status:** Approved
- **Decision:** Temporal owns execution history; PostgreSQL owns AgentForge
  application records and projections.
- **Reason:** Each system has one clear authority.
- **Consequence:** Projection updates must be idempotent and repairable.

## D-003: Start with a deterministic mock model

- **Status:** Approved
- **Decision:** Milestones 1–4 require no model credentials.
- **Reason:** Recovery and safety tests must be deterministic, offline, and
  free from provider cost or availability.
- **Consequence:** A real or local model is an optional later adapter.

## D-004: Use a support-report workflow

- **Status:** Approved
- **Decision:** Read approved local ticket files, create a report artifact, and
  deliver it to a simulated inbox after approval.
- **Reason:** It exercises file permissions, artifacts, approvals, retries, and
  external-side-effect deduplication without real customer data.

## D-005: Use Docker Desktop with WSL2 for local services

- **Status:** Approved
- **Decision:** Use Docker Compose with Docker Desktop's WSL2 Linux-container
  backend while keeping user-facing commands in PowerShell.
- **Reason:** The user is willing to install it, and it offers a reproducible
  local environment for Temporal and PostgreSQL.
- **Constraint:** Keep the service footprint within approximately 10–12 GB RAM
  and do not add Elasticsearch.

## D-006: Use polling before streaming

- **Status:** Approved
- **Decision:** The initial dashboard polls API read endpoints.
- **Reason:** It is simpler and adequate for a single local user.
- **Deferred:** WebSocket or server-sent-event streaming requires evidence of
  clear value.

## D-007: Do not claim universal exactly-once behavior

- **Status:** Approved
- **Decision:** Guarantee one observable delivery only for receivers that
  persistently deduplicate a stable operation ID.
- **Reason:** A crash can occur after an external commit but before local
  acknowledgement.
- **Consequence:** Unsupported destinations may end in `NEEDS_ATTENTION`.

## D-008: Keep the repository private initially

- **Status:** Approved
- **Decision:** Begin as a local Git repository with no remote; add a private
  remote only when requested and review separately before making it public.
- **Reason:** This prevents accidental publication while the project and secret
  handling are immature.

## D-009: Use a bounded guided-run dashboard

- **Status:** Approved and implemented
- **Decision:** Use a three-region Next.js guided workspace with a recent-run
  rail, selected-run timeline, and exact context/actions rail. Access FastAPI
  through a same-origin loopback rewrite.
- **Polling:** Poll the newest 20 runs sequentially every two seconds and one
  selected nonterminal projection every second. Abort and generation-check old
  selections; stop selected polling at terminal states.
- **Authority:** Display PostgreSQL application projections and link to the local
  Temporal UI for execution history. The browser does not query Temporal or make
  scheduling decisions.
- **Artifact safety:** Fetch content only from the fixed run-scoped endpoint and
  render verified report bodies as escaped plain text with preserved whitespace.
- **Reason:** This gives one local operator a clear, low-complexity browser flow
  without weakening durable authority, approval binding, or file boundaries.
- **Deferred:** Streaming, a component framework, remote deployment,
  authentication, and interpreted Markdown require later evidence and approval.

## D-010: Bind every run to an immutable model profile

- **Status:** Approved and implemented
- **Decision:** Persist a complete versioned profile snapshot per run, include it
  in the submission fingerprint and Workflow input, and route model Activities
  to a profile-specific Temporal task queue.
- **Reason:** Retries and replays must not change provider, model digest, queue,
  or generation bounds when environment configuration changes.
- **Consequence:** Workers fail closed on a profile mismatch, and each enabled
  profile needs a matching Worker process.

## D-011: Keep local Qwen optional and trusted-code constrained

- **Status:** Approved and implemented
- **Decision:** Keep `mock-v1` as the default. Add native Windows Ollama
  `0.40.11` and pinned `qwen3:4b-instruct-2507-q4_K_M` only as an opt-in profile
  over loopback, with exact archive/model identity checks and no Ollama SDK.
- **Reason:** A real-model proof is valuable only if it preserves deterministic
  tests, replay-safe routing, approval binding, and privacy boundaries.
- **Bounds:** 8,192 context tokens, 2,048 output tokens, 24,000 prompt
  characters, 16,384 report characters, and at most two semantic repairs.
- **Authority:** The model returns a report body only. Trusted code owns file
  access, artifacts, actions, approval identity, destination, and delivery.
- **Identity enforcement:** Worker startup verifies runtime metadata, and the
  adapter revalidates tag/digest equality immediately before every generation
  request so a mutable tag cannot silently replace the bound model.
- **Cost:** Local provider charge is zero but hardware/electricity are not;
  output quality is nondeterministic, so the real journey is separate from the
  deterministic suite and failures remain bounded.

## D-012: Isolate Temporal integration Workers by namespace

- **Status:** Approved and implemented
- **Decision:** Keep production-like profile task queues fixed, but run real
  Temporal integration Workers and Workflows in `agentforge-test`; the local
  application remains in `default`.
- **Reason:** Queue names are part of immutable model profiles, so varying them
  in tests would weaken routing coverage. Namespace isolation prevents a test
  Worker configured for test databases and temporary storage from consuming a
  live application task.
- **Consequence:** Local compose startup creates both namespaces.

## D-013: Use one static version-2 graph on the existing profile queue

- **Status:** Approved and implemented
- **Decision:** Add a support-report-specific researcher → writer → reviewer
  Workflow as immutable agent version 2. Keep version 1 unchanged and replayable.
- **Bounds:** 16 tickets, four research shards, two concurrent researchers, two
  revisions, ten role Activities, and 900 seconds before publication. Fixed
  ordinals reserve the complete graph, and the provider-request structural
  maximum is 90.
- **Storage:** Keep ticket-derived notes, drafts, and feedback in immutable
  content-addressed files. Temporal history carries references and the bounded
  review decision, never intermediate bodies.
- **Queueing:** All logical roles use the run's immutable model-profile queue.
  Same-queue Worker replicas provide concurrency and recovery; no role-specific
  services, queues, child Workflows, or competing scheduler are introduced.
- **Reason:** The static graph demonstrates multi-role coordination and Worker
  loss without adding speculative orchestration or weakening replay, approval,
  cancellation, or delivery authority.
- **Consequence:** A future graph change requires a new immutable agent version
  and Workflow type rather than editing active version-2 history semantics.
