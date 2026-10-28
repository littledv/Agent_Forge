# AgentForge Recovery and Rollback

Documentation does not perform a rollback. Git history and persistent data
backups are the recovery mechanisms.

## Principles

- Prefer a new corrective commit over rewriting history.
- Preserve evidence before attempting recovery.
- Never use destructive commands such as `git reset --hard` without explicit
  approval and a verified backup or recoverable reference.
- Code rollback and data rollback are separate operations.
- Never delete Docker volumes as part of ordinary shutdown.

## Before a risky change

1. Confirm `git status` is understood.
2. Update `HANDOVER.md` with the current verified state.
3. Run relevant tests.
4. Create a coherent checkpoint commit.
5. Record any database migration and whether it is backward compatible.
6. Back up material local data before a destructive schema operation.

## Find a known-good point

Use PowerShell:

```powershell
git status --short --branch
git log --oneline --decorate -n 20
```

Inspect the suspected change without modifying files:

```powershell
git show --stat <commit-id>
git diff <known-good-commit>..HEAD
```

## Preferred recovery for a committed change

After reviewing the target commit, create a new commit that reverses it:

```powershell
git revert <commit-id>
```

This keeps the history understandable. Do not run it until the exact commit and
its effect have been reviewed.

## Uncommitted changes

Do not discard them automatically. First inspect:

```powershell
git status --short
git diff
```

Copy or commit valuable work before using any restore command. Ask for approval
if ownership of a change is unclear.

## Database and Workflow recovery

- Reverting code does not automatically revert PostgreSQL migrations.
- Reverting Workflow code may break replay for active Temporal runs.
- Before a Workflow change, define whether existing runs finish on old code,
  use Temporal patching/versioning, or are migrated deliberately.
- Restore procedures must be tested before they are described as reliable.

## Docker terminology

- **Stop:** halts containers and should preserve volumes.
- **Down:** removes containers and networks; volumes should remain unless a
  volume-removal option is explicitly used.
- **Reset:** deliberately removes local runtime data and must be separately
  named, documented, and confirmed.
