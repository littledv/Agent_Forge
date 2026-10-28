# AgentForge Milestone 4 Dashboard Design

## Intent

Milestone 4 turns the existing local support-report workflow into a complete,
understandable browser demonstration. One local developer should be able to
submit a run, follow durable progress, inspect steps and attempts, decide an
exact approval, cancel future work, and read the verified report artifact
without opening raw logs or querying PostgreSQL.

The dashboard is a guided run workspace, not a production operations console.
It should make the system's safety and recovery properties legible while
keeping the common path calm enough to demonstrate live.

## Users and primary outcome

The initial user is the developer running AgentForge locally on Windows. The
primary outcome is a successful live demonstration of one support-report run
from submission through approval and safe artifact inspection. A small recent
run list supports recovery, comparison, and returning to an earlier run, but
multi-user operations are not part of this milestone.

## Goals

- Submit the existing deterministic support-report workflow from the browser.
- Show the selected run's application projection, logical steps, and retry
  attempts with clear state and failure labels.
- Make pending approval exactness visible: destination, action hash, artifact
  path, media type, byte size, and SHA-256.
- Approve, reject, or cancel through the existing durable API semantics.
- Display checksum-verified Markdown artifact content as safe plain text.
- Explain projection freshness without presenting lag as execution failure.
- Cover the critical browser journeys with a small Playwright suite.

## Non-goals

- Authentication, authorization roles, teams, cloud access, or public hosting.
- WebSockets, server-sent events, push notifications, or a second scheduler.
- Editing tickets, reports, actions, approval arguments, or Workflow history.
- General file browsing or arbitrary artifact-path access.
- Rich Markdown HTML rendering, syntax extensions, or downloadable archives.
- A mobile-first experience, analytics dashboard, or infrastructure console.
- Replacing the local Temporal UI as the execution-history authority.

## Anti-goals

- Do not make a stale PostgreSQL projection look like a failed Workflow.
- Do not expose ticket bodies, report contents, secrets, or private data in
  URLs, logs, error messages, or approval payloads beyond the verified artifact
  view the user explicitly opens.
- Do not invent run controls the backend cannot enforce durably.
- Do not make every fact a card, metric, badge, or chart.
- Do not add frontend state that competes with PostgreSQL or Temporal.

## Constraints

- Keep all services local and all published ports bound to loopback.
- PostgreSQL remains authoritative for dashboard projections and application
  records; Temporal remains authoritative for execution history and recovery.
- The dashboard polls bounded API reads. Streaming remains deferred under
  decision D-006.
- Model, database, file, and network side effects stay out of Temporal Workflow
  code.
- Existing approval binding, cancellation semantics, receiver deduplication,
  and uncertainty behavior remain unchanged.
- Direct dependencies and the Node runtime must be pinned and approved before
  installation. Node 24 LTS is the runtime line selected for native Windows
  execution because it is supported by both current Next.js and Playwright.

## Approaches considered

### Guided run workspace — selected

A compact recent-runs sidebar, a spacious logical-step timeline, and a right
context rail for projection freshness, approval controls, and artifact content.
Attempts expand beneath their logical step. This keeps the live demonstration
focused while preserving the recovery evidence that makes AgentForge distinct.

### Evidence ledger

A dense, dark operations table makes every event and attempt immediately
visible. It is efficient for incident investigation but visually overweights
internal mechanics for a first dashboard and makes a healthy approval wait look
like an incident.

### Narrative stage demo

A four-stage Tickets → Report → Approval → Delivery journey is easiest to
explain, but it hides the step/attempt model and weakens the Milestone 2
recovery story. Its progressive-stage clarity is retained in status copy, not
as the primary structure.

## Selected visual direction

Use a full-width desktop application surface at a 1440 × 1024 design target.
The base is warm off-white with navy/ink text, cobalt primary actions, muted
mint success, amber waiting, and reserved red destructive actions. Use a highly
readable system sans-serif and monospace only for identifiers and hashes.
Separation comes from whitespace, alignment, typography, and light dividers;
shadows and raised surfaces are rare.

The dashboard must remain usable at 1024 px wide. Below that width the context
rail stacks after the timeline, but mobile-specific interaction is deferred.
Visible controls have keyboard focus states, labels do not rely on color alone,
and state changes use a polite live region.

## Information architecture

The dashboard has one route, `/`, with three persistent regions:

1. **Recent runs** — at most 20 rows ordered newest first, each with the goal,
   projected state, and relative update time. `New run` opens the submission
   form in the main region.
2. **Run workspace** — goal, run ID, projected state, timestamps, and logical
   steps ordered by ordinal. Attempts are nested and collapsed unless a retry,
   failure, or user expansion makes them relevant.
3. **Run context** — projection sequence/freshness, exact approval controls,
   cancellation, delivery receipt summary, artifact metadata, and verified
   plain-text report content when available.

Empty state: explain the local demonstration and make `New run` the only
primary action. Loading uses stable skeleton rows rather than changing layout.
API unavailability preserves the last good view, labels it unavailable, and
offers retry without relabeling the run as failed.

## Browser flow

### Submit

The form has a goal and the two approved sample ticket names. The goal defaults
to `Find recurring support themes`; the files default to `ticket-001.txt` and
`ticket-002.txt`. The dashboard generates one UUID-based submission key when
the form opens and reuses it for retries of that submission. A successful
`POST /api/v1/runs` selects the returned run immediately.

### Observe

While a run is nonterminal, poll its detail every second. Poll the recent list
every two seconds. Never overlap requests; abort an older request when the
selection changes. Stop detail polling for a terminal state and provide a
manual refresh. Display projected state as `Application projection` with its
sequence and update age. Include a secondary link to the local Temporal UI for
authoritative execution history.

### Approve or reject

Only a pending approval shows active decision buttons. Display the exact
destination and artifact-bound values before the controls. Disable all three
controls while a mutation is pending. On `409`, refresh the detail and explain
that durable state already won; never overwrite or optimistically fabricate a
decision.

### Cancel

Cancellation copy says it stops future work and cannot undo a committed
delivery. A short confirmation names the selected run. On success, refresh the
projection; do not immediately claim `CANCELLED` before the Workflow projects
that state.

### Inspect artifact

Show metadata as soon as the run projection contains an artifact reference.
Load content only when the user expands `Report content`. The API verifies the
recorded path, media type, size, and SHA-256 against disk before returning UTF-8
text. Render it with `white-space: pre-wrap`; never inject it as HTML.

## Backend read model

Milestone 4 adds bounded read contracts without changing execution behavior:

- `GET /api/v1/runs?limit=20` returns newest-first `RunListItem` values. Limit
  is required to be between 1 and 50; no unbounded listing is allowed.
- `GET /api/v1/runs/{run_id}/detail` returns one `RunDetailSnapshot` containing
  the existing run fields, original goal and ticket filenames, projection
  sequence and update time, ordered steps with ordered attempts, latest
  approval if present, whether cancellation has been requested, and the
  accepted delivery receipt if present.
- `GET /api/v1/runs/{run_id}/artifact` returns `ArtifactContent` containing the
  exact `ArtifactRef` and verified UTF-8 content. It returns `404` when no
  artifact exists and a sanitized permanent error when verification fails.

Repositories assemble these views from PostgreSQL with bounded ordered
queries. No endpoint accepts an artifact path from the browser. Existing
submission, approval-decision, cancellation, and run-status endpoints remain
compatible.

The API does not query Temporal during dashboard polling. Projection freshness
is reported honestly from PostgreSQL's `projection_sequence` and `updated_at`.
The dashboard links to the local Temporal UI when authoritative history is
needed.

## Frontend boundary

Create a `dashboard/` Next.js App Router application. It runs natively on
Windows and binds to `127.0.0.1:3000`. Next.js rewrites same-origin
`/backend/*` requests to the loopback FastAPI service using a server-side
`AGENTFORGE_API_BASE_URL`; the browser receives no cross-origin credential or
backend configuration.

Use React and TypeScript with CSS Modules or a small global stylesheet. Lucide
React is the single icon source for visible product symbols. Do not add a
component framework, state-management library, data-fetching library, chart
package, second icon package, or Markdown renderer.

Client state is limited to selected run, form values, expanded rows, last good
responses, and pending mutations. Backend responses remain authoritative.

## Error and state behavior

- `404` for a selected run removes stale selection and refreshes recent runs.
- `409` on approval/cancellation explains the durable conflict and refreshes.
- `422` maps field errors beside the submission input without exposing raw
  backend internals.
- `503` and network failure show `Service unavailable`; existing run state is
  not changed.
- Failed, denied, cancelled, timed-out, budget-exhausted, and
  needs-attention states have distinct text, not color-only differences.
- `NEEDS_ATTENTION` explains that an external result could not be reconciled
  and does not offer a blind retry control.
- A projection update age exceeding 10 seconds while nonterminal shows a
  neutral `Projection may be delayed` notice and the Temporal UI link. It is
  never styled as Workflow failure.

## Testing and completion

Backend tests cover run-list bounds/order, nested detail ordering, missing and
verified artifacts, checksum/size/path mismatch, and sanitized failures.
Frontend checks include TypeScript, lint, and production build.

Playwright covers three Chromium journeys at a desktop viewport:

1. Submit a run, observe steps, approve the exact pending action, reach
   `SUCCEEDED`, expand the verified artifact, and see no raw-log dependency.
2. Reject a pending approval and observe `DENIED` with no delivery-success UI.
3. Feed a delayed nonterminal projection through a controlled API fixture and
   verify the dashboard shows a neutral lag notice rather than failure.

Milestone 4 is complete only when the real local happy-path journey works with
FastAPI, relay, inbox, Worker, PostgreSQL, and Temporal running; the browser
tests pass; ordinary backend tests remain green; and documentation records the
native commands and observed evidence.

## Builder-owned details

The builder may choose exact component names, CSS class names, small responsive
breakpoints, and copy edits that do not change state semantics. The builder may
also choose whether the submission form is inline or a lightweight dialog,
provided it preserves one submission key across retries and remains keyboard
accessible.
