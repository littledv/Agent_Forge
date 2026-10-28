# Failure Portfolio

AgentForge's failure portfolio is one credential-free, machine-checked proof of
the recovery and safety behavior implemented through Milestone 8. Run it from
Windows PowerShell in the repository root:

```powershell
.\.venv\Scripts\python.exe tests\helpers\run_failure_portfolio.py
```

## Prerequisites

- Python 3.13.17 and the repository's pinned virtual environment.
- Docker Desktop with PostgreSQL 16.15 and Temporal Server 1.32.1 healthy.
- The configured application and inbox test database names must end in
  `_test`, use a direct loopback `postgresql+psycopg` URL, and contain no query
  parameters; the runner refuses other targets before destructive work.
- Loopback networking must be available. The proof uses the `agentforge-test`
  Temporal namespace, the deterministic `mock-v1` profile, ephemeral API and
  inbox ports, and no API credential.

The integration fixtures automatically prepare and reset the disposable test
databases. Do not manually truncate them before or between runs.

## Expected nodes

The runner invokes pytest once with exactly these five nodes:

1. `test_twenty_step_recovery_portfolio`
2. `test_rejection_or_expiry_denies_delivery[rejected]`
3. `test_retry_exhaustion_projects_uncertain_delivery`
4. `test_two_revisions_are_allowed_and_third_rejection_exhausts_budget`
5. `test_concurrent_receiver_attempts_commit_one_delivery`

Use `--collect-only` to verify the fixed manifest without executing it. A
successful checkpoint requires the normal command to pass twice consecutively,
with no manual database, process, or port cleanup between runs.

## Continuous 20-step proof

1. Build bounded version-2 settings, synthetic private probes, artifact storage,
   and isolated application and inbox database fixtures.
2. Start the simulated inbox on an ephemeral loopback port and prove readiness.
3. Start Worker A with a reviewer marker and unique process identity.
4. Start API A on another ephemeral loopback port and create SDK client A.
5. Submit one stable request and retain its run and Workflow IDs.
6. Prove an exact duplicate returns the same run, while a changed request using
   the same idempotency key conflicts.
7. Relay the start outbox and prove exactly one Workflow starts.
8. Wait for Worker A to enter the first review, after both research shards and
   the first writer step have succeeded.
9. Start Worker B, terminate identity-matched Worker A, and reap Worker A.
10. Let Temporal retry the same review Activity on Worker B and reach
    `WAITING_APPROVAL`.
11. Retain the exact approval ID, operation ID, action hash, and artifact
    metadata.
12. Close client A and stop API A while approval remains pending.
13. Start API B at a different port and prove the same run and approval are
    reconstructed unchanged.
14. Stop and reap idle Worker B.
15. Start Worker C with a post-receiver-commit marker.
16. Approve the retained action and relay exactly one approval signal.
17. Wait for the inbox commit, validate Worker C's marker, then terminate and
    reap Worker C before Activity acknowledgement.
18. Start Worker D and let Temporal retry delivery with the same operation ID.
19. Prove the original Workflow and PostgreSQL projection reach `SUCCEEDED`,
    then retrieve and verify the artifact through the SDK.
20. Prove the complete durable evidence and cleanup set: one run, Workflow,
    approval, delivery, inbox receipt, and artifact; exact checksum and size;
    bounded role order; review and delivery attempts `[1, 2]`; no private probe
    in retained evidence; reaped children; and released listeners.

## Evidence matrix

| Scenario | Durable expectation | Authority read by the test |
|---|---|---|
| Exact duplicate | Same run and Workflow; one run row | PostgreSQL |
| Changed body, same key | Conflict; original run unchanged | API and PostgreSQL |
| Rejection | `DENIED`; no delivery | PostgreSQL and inbox DB |
| Delivery retries exhausted | `NEEDS_ATTENTION`; delivery `UNCERTAIN` | Temporal and PostgreSQL |
| Revision budget exhausted | `BUDGET_EXHAUSTED`; no publication | Temporal and PostgreSQL |
| Concurrent receiver calls | One receipt for the stable operation ID | Inbox DB |
| Worker lost during review | Same Workflow; completed research not repeated | Temporal and PostgreSQL |
| Worker lost after inbox commit | Two Activity attempts; one receipt | Temporal, PostgreSQL, inbox DB |

Representative invariants include stable run, Workflow, approval, and operation
IDs; one start and signal outbox effect; exactly one artifact reference matching
its bytes; one receiver row; bounded role count; and Temporal timeout/retry
history. Successful claims come from these authorities, not console text.
Captured process output is only bounded diagnostic context for an unexpected
exit and is scanned to prevent private probes from being retained.

## Troubleshooting boundaries

If collection fails, first check that the fixed node names still exist. If
database setup is refused, correct the configured URLs rather than weakening
the `_test` guard. If a service is unavailable, use the health and migration
commands in [OPERATIONS.md](OPERATIONS.md). After a failed interrupted test,
inspect only identity-matched helper processes and its exact Workflow; do not
kill unrelated Python processes or bulk-terminate Temporal executions.

The portfolio proves the documented local, simulated-receiver boundary. It does
not prove arbitrary external exactly-once delivery, multi-host disaster
recovery, production security, load capacity, or model quality.
