# AgentForge Test Checklist

This file records what must be verified before a milestone or feature is called
complete. A checked item must refer to an executed check, not an intended one.

## Every change

- [x] The change belongs to the approved current checkpoint.
- [x] New behavior has an automated test where practical.
- [x] Relevant tests were executed and their result is in `HANDOVER.md`.
- [x] No secret or personal data was added to tracked files or logs.
- [x] No unrelated behavior was changed.
- [x] Documentation reflects user-visible or architectural changes.
- [x] Windows/PowerShell instructions were verified or clearly marked untested.

## Milestone 1 minimum workflow

- [x] Run submission promptly returns a run ID.
- [x] Repeating the same submission key and payload returns the same run.
- [x] Reusing the key with different payload returns a conflict.
- [x] Model output is validated against a schema.
- [x] Invalid mock-model output has bounded handling.
- [x] File reads cannot leave the approved sample directory.
- [x] Artifact writes are atomic and remain inside the artifact directory.
- [x] The final report checksum is stored and can be verified.
- [x] Large artifact contents are not stored in Temporal history.
- [x] The run reaches the expected terminal state.

## Recovery and persistence

- [x] Worker termination does not lose Workflow progress.
- [x] Worker restart resumes the run.
- [x] PostgreSQL and Temporal history survive ordinary shutdown.
- [x] Pending approval survives an application restart.
- [x] Projection retries do not duplicate events or move state backward.
- [x] Workflow-start recovery handles both sides of the database/Temporal gap.

## Approval and permission behavior

- [x] Delivery cannot occur before approval.
- [x] Approval is bound to exact normalized arguments and artifact hash.
- [x] Changed arguments require a new approval.
- [x] Repeated identical decisions are harmless.
- [x] Conflicting decisions return a conflict and preserve the first decision.
- [x] Rejection prevents the proposed action.
- [x] Expiry has an explicit durable outcome.
- [x] Disallowed tools and paths are rejected by trusted code.

## Side effects and limits

- [x] A crash after inbox commit but before Activity acknowledgement produces
      exactly one receiver delivery.
- [x] Concurrent attempts with the same operation ID produce one delivery.
- [x] Retry exhaustion produces a clear terminal outcome.
- [x] Step, elapsed-time, token, retry, and concurrency limits are enforced.
- [x] Cancellation stops future work without claiming to undo committed work.
- [x] Cancellation persisted during the pre-delivery projection prevents the
      delivery, and cancellation during the final model Activity prevents a
      false successful terminal state.
- [x] Loopback inbox delivery ignores environment proxy configuration.
- [x] Oversized report rejection does not expose report content in the error.
- [x] An unverifiable external result becomes uncertain rather than succeeding.

## Dashboard journey

- [x] User can submit a run.
- [x] User can inspect steps, attempts, errors, and timing.
- [x] User can inspect and decide a pending approval.
- [x] User can inspect artifact metadata and safe report content.
- [x] User can distinguish execution state from a lagging projection.
- [x] Critical browser checks pass at 1024 × 768 and 1440 × 1024.

## Optional local-model profile

- [x] Mock mode remains the default and the deterministic suite needs no Ollama.
- [x] Run/start/Workflow data preserve one immutable model-profile snapshot.
- [x] Mock and Ollama model Activities use dedicated task queues.
- [x] Startup rejects a wrong runtime version, archive hash, model tag/digest,
      non-loopback listener, or listener owned by another process.
- [x] Every generation request revalidates the mutable tag against the pinned
      digest before recording usage or calling the chat endpoint.
- [x] Structured output, response size, prompt/report budgets, and semantic
      repair count are bounded.
- [x] Transport, output, configuration, profile, and budget failures have tested
      retryable/permanent classifications.
- [x] Exact provider usage is durable and interrupted usage remains uncertain.
- [x] An earlier unmeasured request cannot be relabeled exact by a later
      response with provider counts.
- [x] The dashboard shows immutable provenance, usage, zero provider charge,
      and neutral incomplete-usage wording without rendering prompt/response data.
- [x] A native Qwen browser run executes researcher, writer, and reviewer roles,
      reaches approval, and produces exactly one receiver delivery within the
      graph and per-request bounds.
- [x] PostgreSQL, decoded Temporal payloads, process logs, screenshots, and
      traces are checked against private ticket/report probes.
- [x] The real harness stops only identity-matched owned processes and verifies
      ports 3000, 8000, 8001, and owned port 11434 are clear.
- [x] Real Temporal integration Workers use `agentforge-test`, not the live
      application's `default` namespace.

## Bounded multi-agent graph

- [x] Agent version 2 routes durably while legacy idempotency returns the
      original version-1 run.
- [x] Saved approved and denied version-1 histories replay unchanged.
- [x] Ticket partitioning is sorted and bounded to four shards; research runs
      in batches of at most two and writer inputs remain in shard order.
- [x] Reviewer rejection allows two revisions and a third rejection produces
      `BUDGET_EXHAUSTED` before publication.
- [x] The eleventh role cannot be scheduled, and the 900-second graph deadline
      prevents publication after exhaustion.
- [x] Cancellation during research allows only the current bounded batch to
      settle and schedules no later work.
- [x] Intermediate note, draft, feedback, prompt, and raw-response bodies are
      absent from PostgreSQL projections, decoded history, and retained logs.
- [x] A token-verified Worker loss during review retries the same logical step
      on a replacement Worker without repeating completed research.
- [x] The dashboard presents deterministic role/shard/revision labels, nested
      retry attempts, and per-attempt usage without exposing intermediate text.

## Versioned API and Python SDK

- [x] The intentional `/api/v1` paths, methods, statuses, schemas, enums, and
      `detail` error envelope have executable contract coverage.
- [x] Public client methods return typed immutable models, validate known
      fields, and tolerate additive response fields, including nested fields.
- [x] Mutating methods perform no hidden retry and expose ambiguous transport
      or request-timeout outcomes to the caller.
- [x] A polling deadline caps requests and sleeps, retains the last validated
      run, and never cancels or mutates server work.
- [x] Polling surfaces request failures immediately and raises when an
      unrequested terminal state makes the requested state unreachable.
- [x] A closed first client can be replaced and polling resumed by the same
      run ID without creating a second durable run.
- [x] Client errors redact request bodies, ticket/report content, arbitrary
      response bodies, and structured validation details.
- [x] The recent-runs client validates the bounded 1..50 newest-first window;
      documentation does not describe it as exhaustive pagination.
- [x] Artifact retrieval verifies UTF-8 byte length and SHA-256 and rejects a
      mismatch without exposing content.
- [x] The complete loopback SDK journey proves exact duplicate submission,
      changed-body conflict, exact approval, one delivery, and verified report
      retrieval.

## Evaluation and failure portfolio

- [x] The continuous proof completes all 20 ordered checkpoints across review
      Worker loss, API reconstruction, and post-inbox-commit Worker loss.
- [x] Disposable application and inbox databases are reset automatically only
      after both configured URLs pass the driver, loopback, `_test` suffix, and
      no-query-parameter safety guards.
- [x] Rejection reaches `DENIED` and prevents receiver delivery.
- [x] Exhausted delivery retries produce an `UNCERTAIN` step and
      `NEEDS_ATTENTION`, rather than false success.
- [x] Exhausted revision budget produces `BUDGET_EXHAUSTED` before publication,
      approval, or delivery.
- [x] Exact duplicate submission returns the original run and a changed request
      with the same key conflicts without creating another run.
- [x] Concurrent identical receiver attempts and the post-commit retry each
      preserve one receiver-visible inbox row.
- [x] Claims are asserted against the owning PostgreSQL, Temporal, inbox,
      artifact, process, and listener authorities rather than success logs.
- [x] Every portfolio-owned child process is reaped, ephemeral listeners are
      released, and two consecutive portfolio runs pass without manual cleanup.
- [x] Synthetic private ticket and report probes are absent from retained
      projections, decoded history, and bounded diagnostics.
- [x] Documentation separates proven local recovery from properties not proven:
      arbitrary external exactly-once delivery, multi-host disaster recovery,
      production security, load capacity, and model quality.

## Before making the repository public

- [x] Entire Git history has been checked for secrets and private data.
- [x] `.env` and local runtime data are ignored.
- [x] A safe `.env.example` exists.
- [x] License and third-party notices are reviewed.
- [ ] README setup commands work from a clean machine.
- [x] Public screenshots and sample data contain no sensitive information.
- [x] Contributor display and commit authorship contain only intended humans.
