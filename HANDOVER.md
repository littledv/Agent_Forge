# AgentForge Handover

Updated: 2026-10-28 (Asia/Calcutta)

## Current checkpoint

Milestone 8 is implemented, independently reviewed, remediated, fully verified,
and user-approved. Its five roadmap tasks and checkpoint gate are complete.
The user explicitly skipped optional Milestone 9 on 2026-10-28. The selected
local portfolio-project scope is therefore complete.

This is the sanitized publication copy at `E:\Agent_Forge`. It was exported
from the latest private source snapshot without the original Git history. The
two Temporal replay fixtures use a synthetic worker identity, the repository
includes the MIT license, and the new Git history uses the authenticated
GitHub account's noreply address. The original `littledv/agentforge` repository
was left unchanged except for its separately requested MIT license commit.
Nothing was deployed, no paid resource was created, and no port was published
beyond loopback.

## Delivered behavior

- A guarded test-only reset accepts only direct `postgresql+psycopg` loopback
  URLs whose database ends in `_test`, rejects all query parameters, verifies
  the Alembic target is identical, and refuses unsafe input before any side
  effect.
- `tests/helpers/run_failure_portfolio.py` invokes one fixed five-node pytest
  manifest once, supports only `--collect-only`, and returns pytest's exit code
  unchanged.
- One continuous 20-step version-2 run survives review Worker loss, API
  reconstruction during pending approval, and delivery Worker loss after the
  inbox commits but before Temporal acknowledges the Activity.
- The matrix proves rejection, delivery retry exhaustion, revision budget
  exhaustion, duplicate submission, and concurrent receiver attempts against
  their owning PostgreSQL, Temporal, and inbox authorities.
- The post-commit proof retains an immutable pre-crash receiver receipt and
  verifies its identity, timestamp, request/action hashes, and artifact metadata
  are unchanged after retry.
- Cleanup uses bounded waits, attempts every owned Workflow/client/Worker/server
  resource even after an earlier failure, reaps children, and releases
  listeners.
- Evidence, architecture, Windows operations, learning, and explicit
  proven/not-proven boundaries are published under `docs/`.

## Independent review

The whole change from `3b6601a` through `6e26cc2` received one independent,
read-only review. Its initial assessment was **not ready** and reported:

- Critical: PostgreSQL URL query parameters could override the validated reset
  host/database.
- Important: rejection and revision-budget matrix cases were weaker than their
  documented durable-authority claims.
- Important: the composite did not compare the pre-crash inbox receipt with the
  final receipt.
- Important: one teardown failure could skip cleanup of later owned resources.
- Minor: the portfolio sequence diagram bypassed the PostgreSQL/signal-relay
  boundary, and the writer non-repeat claim lacked an attempt assertion.

All findings were accepted and fixed in `b65ba22`. RED evidence reproduced the
query bypass (three reset failures), missing durable budget projection
(`PENDING_START` instead of `BUDGET_EXHAUSTED`), and missing all-resources
cleanup helper. The focused remediation suite then passed 19 tests, and the
strengthened composite passed in 44.24 seconds. No production Workflow or
dependency changed.

## Final verification

Commands were run from Windows PowerShell in the repository root.

- `.\.venv\Scripts\python.exe tests\helpers\run_failure_portfolio.py`:
  5 passed in 50.36 seconds.
- The identical command immediately again without cleanup: 5 passed in 50.31
  seconds.
- `.\.venv\Scripts\python.exe -m pytest -q`: first attempt had 420 passed and
  only the known one-second Temporal deadline test failed with `FAILED` instead
  of `BUDGET_EXHAUSTED`; the isolated test immediately passed in 2.57 seconds.
- Fresh `.\.venv\Scripts\python.exe -m pytest -q`: 421 passed in 187.29 seconds.
- `.\.venv\Scripts\python.exe -m pip check`: no broken requirements.
- Application Alembic upgrade/current: `0005_support_report_v2 (head)`.
- Inbox Alembic upgrade/current: `0001_inbox_deliveries (head)`.
- `docker compose config --quiet` and `git diff --check`: passed.
- Dashboard `typecheck`, `lint`, and production `build`: passed.
- Dashboard `test:e2e`: 32 passed; the explicit real-model test was skipped.
- Credential, absolute-path, production failure-injection, helper-process, and
  port 8000/8001 scans: clean.
- Compose inspection confirmed ports 5432, 7233, and 8080 publish only on
  `127.0.0.1`.
- The first pre-publish suite attempt encountered fixture setup errors because
  Docker Desktop was stopped. That run was interrupted, Docker Desktop and the
  pinned loopback services were restarted, and the fresh suite passed 421 tests
  in 174.83 seconds.
- Private repository `littledv/agentforge` was created without generated starter
  files. The first push verified `origin/main` exactly matched local commit
  `cf633a3`; the remote-record commit was then verified at `a9947eb`.
- README status and privacy text was aligned with the completed milestones,
  skipped deployment milestone, and private remote; `git diff --check` passed.

The unavailable optional formatter checks (`ruff` and `black`) were not added as
dependencies; the pinned project verification commands above all passed.

The sanitized publication copy was freshly verified on 2026-10-28:

- `python -m pytest tests/integration/test_workflow_replay.py -q`: 2 passed in
  1.34 seconds.
- `python -m pytest -q`: 421 passed in 185.13 seconds.
- `python -m pip check`: no broken requirements.
- Structural comparison preserved exactly 115 approved-history events and 66
  denied-history events. The only 150 value changes were worker `identity`
  fields and sticky task-queue names; no other history value changed.
- The captured workstation identity, the original personal commit email, and
  personal Windows user paths were absent from the sanitized tree.
- The fresh repository has no imported `.git` directory, refs, reflogs, or
  alternates from the private source repository.

## Known caveat

`test_graph_deadline_bounds_activity_retries_not_only_each_attempt` has a known
one-second Temporal timing boundary that can intermittently project `FAILED`
instead of `BUDGET_EXHAUSTED`. It is unrelated to the Milestone 8 reset and
portfolio changes, passed immediately in isolation, and the required fresh full
suite passed. This variance remains recorded rather than being hidden.

## Coherent commits

- `cd3f268` — `test: make integration database reset repeatable`
- `f9f4d2d` — `test: define failure portfolio manifest`
- `73d3bf3` — `test: prove the continuous recovery portfolio`
- `eb41d64` — `test: complete failure portfolio evidence matrix`
- `6e26cc2` — `docs: publish failure portfolio evidence`
- `b65ba22` — `test: harden failure portfolio evidence`
- `5166369` — `docs: record milestone 8 verification checkpoint`
- `c77c861` — `docs: approve milestone 8 checkpoint`
- `cf633a3` — `docs: record milestone 9 skipped`
- `a9947eb` — `docs: record private GitHub remote`
- `docs: align README with private publication state` — current publication
  status commit

## Changed files

- `tests/helpers/database_reset.py`
- `tests/helpers/run_failure_portfolio.py`
- `tests/integration/conftest.py`
- `tests/integration/test_failure_portfolio.py`
- `tests/integration/test_multi_agent_workflow.py`
- `tests/integration/test_workflow.py`
- `tests/unit/test_database_reset.py`
- `tests/unit/test_failure_portfolio_runner.py`
- `docs/FAILURE_PORTFOLIO.md`
- `docs/PORTFOLIO_ARCHITECTURE.md`
- `docs/OPERATIONS.md`
- `docs/learning/11_MILESTONE_8_FAILURE_PORTFOLIO.md`
- `docs/learning/README.md`
- `README.md`
- `ARCHITECTURE.md`
- `FLOW.md`
- `TEST_CHECKLIST.md`
- `PROJECT_PLAN.md`
- `HANDOVER.md`

## Exact next action

Create the separate `littledv/Agent_Forge` GitHub repository as private, push
the verified sanitized commit, and perform the final repository-visibility
change only after action-time user confirmation. Milestone 9 remains skipped;
source publication does not authorize application deployment.
