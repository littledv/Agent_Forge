# AgentForge Working Instructions

These instructions apply to the entire repository.

## Required reading

Before changing the project, read:

1. `README.md`
2. `PROJECT_PLAN.md`
3. `CONSTRAINTS.md`
4. `ARCHITECTURE.md`
5. `DECISIONS.md`
6. `HANDOVER.md`

Read `FLOW.md` and `TEST_CHECKLIST.md` for behavior or test work. Read the
relevant file under `docs/tasks/` when working on a focused feature or bug.

## Working rules

- Work on only the current checkpoint in `PROJECT_PLAN.md`.
- Do not start a later milestone until the user approves the current one.
- Explain important decisions before substantial changes.
- Prefer small, reviewable changes and avoid unrelated refactoring.
- Do not add or upgrade dependencies without explaining the need and obtaining
  approval.
- Never put credentials, tokens, passwords, or private data in source control or
  logs.
- Do not deploy, publish ports beyond loopback, create paid resources, or make
  the repository public without explicit user approval.
- Never add Codex, OpenAI, an AI assistant, or a generated-by trailer as a commit
  author, co-author, contributor, or project-page credit.
- Preserve user-authored changes and do not use destructive Git recovery unless
  the user explicitly requests it.
- Use Windows PowerShell instructions by default. Explain any command that must
  run inside WSL.
- Keep Temporal Workflow coordination deterministic. Put model calls, tool
  calls, database access, and file operations in Activities.
- Treat PostgreSQL as the application-record authority and Temporal as the
  execution-history authority. Do not create a competing scheduler.
- Test state transitions, retries, permissions, approval behavior, and side
  effects in proportion to each change.

## Checkpoint protocol

Before stopping work or when context may be lost:

1. Update milestone/task checkboxes in `PROJECT_PLAN.md`.
2. Replace the contents of `HANDOVER.md` with the latest verified state.
3. Record checks that actually ran, including failures.
4. Record changed files and the exact next action.
5. Commit only coherent, reviewed work with the user's configured Git identity.

On resume, trust the repository and executed checks over chat memory.
