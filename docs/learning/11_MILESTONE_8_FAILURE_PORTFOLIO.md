# Milestone 8: Proving Recovery Across Authorities

A recovery claim is stronger than a success message. AgentForge coordinates
facts owned by several systems, so the portfolio checks each fact where it is
durable.

PostgreSQL answers: which run, steps, attempts, approval, artifact, and delivery
does the application record? Temporal answers: which Workflow and Activity
attempts actually executed, timed out, retried, and completed? The inbox
database answers: how many receiver-visible deliveries committed? The artifact
file and its metadata answer: are the report bytes intact? Process and socket
checks answer: did the test release what it started?

This matters most after an ambiguous failure. Worker C sends a delivery, the
receiver commits it, and the Worker disappears before Temporal sees the
Activity result. Temporal correctly retries. A log saying "sent" cannot prove
whether the receiver committed once, twice, or not at all. One inbox row for
the stable operation ID, with the same receipt returned to the retry, is the
evidence that deduplication worked.

The same idea applies to Worker A's review crash. Temporal history proves two
Activity attempts. PostgreSQL proves completed research and writing were not
repeated. Reconstructing the API while approval is pending proves the approval
belongs to durable storage, not to one client or server process.

The fixed five-node portfolio also exercises rejection, exhausted delivery
retries, exhausted revision budget, and concurrent receiver attempts. Running
it twice consecutively catches a different class of bug: stale database rows,
leaked listeners, orphaned processes, or hidden reliance on the previous run.

The portfolio stays honest about its boundary. It proves the pinned local stack
and a receiver that supports durable operation-ID deduplication. It does not
turn every external API into an exactly-once system, prove production security
or capacity, test multi-host disaster recovery, or judge model quality.

See [FAILURE_PORTFOLIO.md](../FAILURE_PORTFOLIO.md) for the executable proof,
[PORTFOLIO_ARCHITECTURE.md](../PORTFOLIO_ARCHITECTURE.md) for ownership and
retry boundaries, and [OPERATIONS.md](../OPERATIONS.md) for safe local commands.
