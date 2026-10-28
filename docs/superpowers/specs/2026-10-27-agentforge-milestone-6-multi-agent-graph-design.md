# AgentForge Milestone 6 Bounded Multi-Agent Graph Design

Date: 2026-10-27
Status: Approved on 2026-10-27

## Intent

Extend the existing support-report product with three explicit model roles:
researcher, writer, and reviewer. Temporal remains the only execution
coordinator. The graph is static, bounded, versioned, and support-report-
specific; it is not a general agent framework or a model-directed scheduler.

The milestone should teach how several specialized roles cooperate without
weakening the properties already proven: deterministic Workflow replay,
immutable model binding, trusted file access, bounded model calls, durable
recovery, exact action-bound human approval, and deduplicated delivery.

## Selected scope

The user selected a support-report-specific graph. Generalization is deferred
until AgentForge has a second concrete workflow whose shared needs can be
observed rather than guessed.

One run will execute this fixed graph:

```text
trusted ticket snapshot
  -> bounded researcher fan-out
  -> writer draft
  -> reviewer verdict
       -> accepted: publish -> human approval -> deliver -> finish
       -> revise: writer revision -> reviewer verdict (bounded)
```

Research roles may run concurrently within a fixed cap. Writing and review are
sequential because each consumes the preceding immutable outputs. A reviewer
accepts report quality only; it cannot authorize publication or delivery.

## Goals

- Give researcher, writer, and reviewer distinct typed responsibilities.
- Schedule a static graph whose shape cannot be expanded by model output.
- Bound research fan-out, concurrent research, revisions, role invocations,
  elapsed execution, Activity retries, tokens, and stored intermediate sizes.
- Keep mock mode deterministic and credential-free for ordinary tests.
- Support the existing optional Ollama/Qwen profile through the same trusted
  role contracts without silent fallback.
- Preserve completed and active version-1 Workflow replay.
- Recover completed graph nodes after Worker loss and retry only unfinished or
  failed Activity work.
- Keep intermediate ticket-derived text out of Temporal history, PostgreSQL
  projections, normal logs, and API responses.
- Preserve the existing exact human approval and inbox deduplication boundary.
- Make role, round, shard, retry, and usage evidence understandable in the
  existing dashboard timeline.

## Non-goals

- No generic DAG builder, dynamic planner, arbitrary role creation, recursive
  delegation, role marketplace, or user-authored graph definition.
- No child Workflow per role, separate scheduler, queue broker, Redis,
  LangGraph, or new framework dependency.
- No model-selected file path, tool, destination, approval, operation ID,
  concurrency value, retry count, or extra task.
- No role-to-role chat transcript, hidden chain-of-thought, or unbounded shared
  memory.
- No new cloud provider, model download, paid resource, public deployment,
  authentication system, or remote storage.
- No claim that logical roles require separate operating-system processes.
- No change to the version-1 Workflow command sequence.

## Approaches considered

### 1. One versioned parent Workflow with typed role Activities — selected

A new version-2 support-report Workflow owns a fixed graph. Separate typed
Activities run research, writing, and review. Intermediate content is stored as
checksummed run-scoped files; Workflow history contains only small references
and verdict metadata. All roles use the run's immutable model-profile queue.

This is the smallest design that visibly demonstrates roles, fan-out, bounded
revision, recovery, and concurrency while retaining Temporal as the execution
authority.

### 2. Child Workflow per role — rejected

Child Workflows would isolate histories and permit independent lifecycle
policies, but the graph has only three fixed roles and small bounded outputs.
They would add identifiers, cancellation propagation, replay surfaces, and
operational overhead without a current scaling or ownership requirement.

### 3. Generic dynamic graph engine — deferred

A graph schema plus model- or user-selected nodes could support unrelated
future workflows. It is deferred because no second workflow exists, dynamic
expansion conflicts with the milestone's fixed limits, and it would recreate a
scheduler beside Temporal.

## Durable version and replay boundary

Milestone 6 is a new support-report agent version, not an in-place rewrite of
`SupportReportWorkflow` version 1.

- Migration `0005` inserts support-report agent version `2` with a stable UUID.
- New submissions resolve version `2`; an existing submission key still returns
  its original run and version when the request and model profile match.
- New start-outbox payloads contain a trusted workflow kind and a complete
  `TaskGraphLimits` snapshot. Existing payloads without those fields default to
  version 1.
- The relay starts either the existing `SupportReportWorkflow` or the new
  `MultiAgentSupportReportWorkflow` from that durable payload. It never chooses
  a version from mutable process configuration after submission.
- Workers register both Workflow definitions. Version-1 Workflow code and
  compatible input defaults remain unchanged so old histories can replay.
- The run's existing stable Workflow ID and model-profile task queue remain the
  execution identity and routing authority.

No database table becomes a graph scheduler. PostgreSQL stores application
records and projections; Temporal history stores which graph nodes completed.

## Fixed graph and exact limits

`TaskGraphLimits` is a strict frozen input snapshot with these version-2
values:

```text
graph_version = 2
max_ticket_files = 16
max_research_tasks = 4
max_concurrent_research = 2
max_revisions = 2
max_role_tasks = 10
max_elapsed_seconds = 900
max_research_note_chars = 4096
max_review_feedback_chars = 2048
```

Version-2 submission accepts at most 16 ticket files. The trusted Workflow
sorts those paths, chooses `min(ticket_count, 4)` shards, and distributes the
paths in sorted round-robin order (`paths[index::shard_count]`). It schedules
shards in ascending two-node batches and waits for each batch before scheduling
the next. The model cannot alter shard membership or add research work.

After research, the writer gets one initial attempt. The reviewer returns
`ACCEPT` or `REVISE`. `REVISE` may trigger at most two writer revisions, each
followed by one review. With four research shards, the maximum model-role count
is therefore ten: four researchers, three writer calls, and three reviewer
calls. Trusted code counts every scheduled role Activity and refuses an
eleventh even if another limit is misconfigured.

The Workflow records a graph start time from `workflow.now()` and derives a
fixed 900-second autonomous-work deadline. The deadline covers ticket reading,
research, writing, review, and stage publication; it stops once the accepted
draft is published and the existing human approval phase begins. Before
scheduling each graph node it checks cancellation, role count, and remaining
time. Each pre-approval Activity timeout is capped by the remaining graph time.
Exhausting role, revision, token, or elapsed budgets projects
`BUDGET_EXHAUSTED`; no report is published or delivered. The later approval
wait keeps its existing independent durable TTL.

Temporal's existing bounded Activity retry policy remains three attempts.
Provider repair calls remain bounded by the immutable model profile and count
inside one role Activity; they do not create graph nodes or reset graph limits.

Token enforcement is per provider request, not based on a cumulative exact-
usage counter. Every request keeps the profile's context, 24,000-character
prompt, and 2,048-output-token ceilings. The fixed graph still produces a
calculable worst case: with ten role tasks, three Temporal Activity attempts,
and one initial plus two repair calls per attempt, at most 90 provider requests
can begin. Each remains subject to the request ceilings. A missing or ambiguous
provider token count stays marked uncertain but cannot grant another graph
node, Activity attempt, repair, or larger request. This structural limit is
enforceable before a call; exact usage remains audit evidence rather than a
second scheduler.

## Role responsibilities and contracts

### Researcher

Input: run ID, goal, the trusted ticket snapshot reference, the exact assigned
ticket paths, shard index/count, graph version, and immutable model profile.

Output: a `ResearchNoteRef` to a strict JSON document containing exactly one
finding per assigned ticket path. Each finding has that path, one factual
observation of at most 512 characters, and one evidence excerpt of at most 512
characters. Trusted validation requires exact assigned-path coverage, rejects
duplicate or outside paths, and caps the serialized note at 4,096 characters.
Research cannot request more files or tools.

### Writer

Input: run ID, goal, ordered research-note references, optional previous draft
reference, optional review-feedback reference, revision number, graph version,
and immutable model profile.

Output: a `DraftRef` containing Markdown. Trusted validation requires nonempty
UTF-8, the goal, every submitted ticket path, no NUL, and the profile's existing
report-size ceiling. A revision writes a new immutable draft; it never mutates a
reference already recorded in Workflow history.

### Reviewer

Input: run ID, goal, ordered research-note references, exact draft reference,
review round, graph version, and immutable model profile.

Output: a small `ReviewResult` containing `ACCEPT` or `REVISE` and, for
`REVISE`, a `ReviewFeedbackRef`. Trusted validation independently checks the
writer contract before accepting the model verdict. Feedback items use only
`MISSING_GOAL`, `MISSING_TICKET`, `UNSUPPORTED_CLAIM`, `UNCLEAR_SUMMARY`, or
`FORMAT`; combined feedback text is capped at 2,048 characters. Feedback cannot
add tasks, change files, or approve delivery. `ACCEPT` carries no feedback
reference.

An `ACCEPT` verdict allows trusted code to publish the exact reviewed draft.
The existing human approval then binds the resulting artifact, target, and
operation ID exactly as before.

## Intermediate storage and privacy

Ticket-derived intermediate bodies do not enter Workflow history. Extend
`ArtifactStorage` with typed, verified, immutable stage files:

```text
research/{run_id}/shard-{index}/{sha256}.json
drafts/{run_id}/revision-{number}/{sha256}.md
reviews/{run_id}/round-{number}/{sha256}.json
```

The SHA-256 is part of the path and reference. A create-once write accepts an
existing path only when its bytes match. A late or retried Activity therefore
cannot overwrite the bytes referenced by a completed Temporal event. The final
publisher accepts only the exact writer draft approved by the final reviewer
and copies it to the existing report location atomically.

PostgreSQL continues to store final artifact metadata and step/attempt
projections, not research notes, drafts, review text, prompts, raw responses,
or hidden reasoning. The API does not expose intermediate file content. Normal
logs contain identifiers, role names, counts, hashes, sizes, and sanitized
failure categories only.

## Model-provider boundary

Replace the version-2 action-selection surface with three dependency-neutral
role methods while retaining the version-1 `DecisionProvider` contract for
replay and old runs:

```text
research(ResearchRoleInput, ModelUsageReporter) -> ResearchNoteBody
write(WriterRoleInput, ModelUsageReporter) -> ReportBody
review(ReviewerRoleInput, ModelUsageReporter) -> ReviewBody
```

The Activities, not the provider, read verified stage files and write returned
bodies. Pydantic strict schemas and trusted semantic checks run before any
reference is returned to the Workflow.

The deterministic mock implements all roles without network access. It emits
stable research notes, a stable report, and `ACCEPT` by default. Tests may
inject scripted role providers to force revision, retry, exhaustion,
cancellation, and Worker-loss paths.

The Ollama adapter uses the existing loopback client, pinned digest check,
prompt ceiling, output ceiling, response-byte ceiling, semantic repair bound,
usage reporter, and sanitized failure classification. Each role has a distinct
strict response schema and prompt. Ticket text, notes, draft text, and review
feedback remain untrusted data and cannot select a tool or side effect. Ollama
never falls back to the mock provider.

## Worker and task-queue topology

All role Activities stay on the immutable model profile's existing task queue.
Role names alone are not security, ownership, hardware, or scaling boundaries,
so creating three queues and three mandatory Worker types would add operations
without isolation value. The Workflow's two-research concurrency cap applies
regardless of how many Worker replicas poll the queue.

For recovery and concurrency proofs, tests may run two replaceable Worker
processes on the same profile queue. Killing one during a research or review
Activity must leave completed nodes in Temporal history and allow the other or
a replacement Worker to finish. The local Qwen path may serialize actual model
execution at the Worker/runtime layer while preserving the graph's upper bound;
the graph never requires parallel GPU calls to be correct.

No new queue, service, container image, Python package, or frontend dependency
is required.

## Projection and dashboard behavior

Every role Activity uses the existing durable step/attempt projection path.
Stable step names encode only bounded graph identity:

```text
research_shard_1 ... research_shard_4
write_report_r0 ... write_report_r2
review_report_r0 ... review_report_r2
```

Ordinals remain unique and deterministic even when research completes out of
order. The dashboard maps these names to readable Researcher, Writer, and
Reviewer labels and shows shard or revision context. Existing attempt usage
fields display per-call identity, requests, tokens, duration, estimates, and
uncertainty for all three model-role step families. Intermediate bodies and
review feedback are never rendered.

Research fan-out may finish out of order, but the timeline remains sorted by
the graph's deterministic ordinal. Existing approval, cancellation, final
artifact, and delivery UI behavior is unchanged.

## Failure and cancellation behavior

- Retryable provider or Worker failure: Temporal retries the same Activity up
  to three attempts; completed sibling research nodes are not repeated.
- Invalid schema, unauthorized path, hash mismatch, oversized stage output, or
  immutable-write conflict: permanent sanitized failure.
- Reviewer requests more work after two revisions: `BUDGET_EXHAUSTED` with no
  published artifact or approval request.
- Graph deadline or role-task ceiling reached: `BUDGET_EXHAUSTED` with no new
  graph node scheduled.
- Cancellation during a role Activity cannot undo an already completed model
  call. Immediately after the Activity returns, the Workflow checks durable
  cancellation and schedules no later role, publication, approval, or delivery.
- Cancellation during research allows the current bounded Activity batch to
  settle, then stops every unscheduled batch and later graph node. It does not
  pretend to interrupt a provider call already in progress; any already written
  immutable stage files remain non-public internal evidence.
- Failure after publication and during delivery retains the existing
  `NEEDS_ATTENTION` and receiver-deduplication semantics.

## Testing strategy

Default tests use deterministic or scripted role providers and require no
Ollama process.

- Domain tests: 16-file submission ceiling, strict graph limits, role
  inputs/outputs, path coverage, revision numbering, structural 90-request
  worst-case calculation, and legacy version-1 defaults.
- Storage tests: immutable content-addressed stages, verification, traversal,
  size bounds, stale-attempt non-overwrite, and exact reviewed-draft publish.
- Workflow tests: deterministic shard partition, at-most-two concurrent
  research nodes, stable ordinals under out-of-order completion, accept path,
  two revisions, third rejection exhaustion, ten-role ceiling, 900-second
  deadline, cancellation between every phase, and no delivery before human
  approval.
- Replay tests: representative pre-Milestone-6 version-1 histories replay
  unchanged while new submissions route to version 2.
- Repository/relay tests: stable agent version 2, durable workflow kind/limit
  snapshot, idempotent old-key retry, legacy outbox default, and correct
  Workflow selection.
- Activity/provider tests: role-specific validation, prompt/output/repair
  bounds, usage evidence for every role, retryable/permanent classification,
  redaction, and no intermediate content in logs or database fields.
- Worker-loss integration: two Workers on one queue, terminate one while a
  research or review Activity is running, start/reuse a replacement, and prove
  one final artifact plus one approved receiver delivery.
- Dashboard tests: readable role labels, deterministic order, retry attempts,
  revision rounds, usage evidence, and absence of intermediate bodies.
- Full regression: all version-1 API, approval, cancellation, recovery,
  dashboard, mock, and optional local-model tests remain green.

One opt-in local Qwen proof exercises the three roles on the existing sample
tickets, accepts within the revision bound, reaches the unchanged human
approval, and produces exactly one receiver delivery. The proof retains the
Milestone 5 privacy and process-cleanup scans.

## Documentation changes

- Update `README.md`, `ARCHITECTURE.md`, and `FLOW.md` with the version-2 graph,
  roles, limits, and unchanged approval boundary.
- Add an architectural decision explaining the static graph, Workflow version,
  shared task queue, and why role-specific queues were rejected.
- Extend `TEST_CHECKLIST.md` with role, concurrency, revision, deadline,
  recovery, privacy, and replay checks.
- Add a learning chapter explaining logical agents versus Workers, fan-out,
  review loops, durable graph history, and bounded autonomy.
- Update `PROJECT_PLAN.md` and replace `HANDOVER.md` only with checks actually
  executed.

## Acceptance

Milestone 6 is complete when a version-2 run executes researcher, writer, and
reviewer roles under the exact graph limits; partial Worker loss and reviewer
rejection cannot exceed those bounds; version-1 histories still replay; the
mock suite remains deterministic; the optional Qwen proof completes through
the unchanged approval and exactly-one delivery path; intermediate content is
absent from Temporal/PostgreSQL/log evidence; and the dashboard visibly
explains each role and retry without becoming an execution authority.

## Builder calls deliberately left open

- Exact class/module splitting may follow the repository's existing size and
  import patterns as long as version-1 Workflow code stays replay-compatible.
- Tests may use Temporal's time-skipping environment or an injected clock guard
  to prove the deadline, provided production authority remains
  `workflow.now()`.
- The local Qwen Worker may serialize role Activities for hardware stability;
  the Workflow-level research concurrency ceiling remains two.
