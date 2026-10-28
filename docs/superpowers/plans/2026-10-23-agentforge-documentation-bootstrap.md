# AgentForge Documentation Bootstrap Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> superpowers:subagent-driven-development (recommended) or
> superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Create a durable project handbook and checkpoint system that allows a
future session to resume AgentForge without relying on chat history.

**Architecture:** Root documents separate project overview, architecture,
constraints, behavior, decisions, testing, recovery, progress, and handover.
Focused templates live under `docs/tasks`, while plain-language lessons and
diagrams live under `docs/learning`.

**Tech Stack:** Markdown, Mermaid diagrams, Git.

**Spec:** `PROJECT_PLAN.md`

## Global Constraints

- Do not create application code or install dependencies in this task.
- Keep the Git repository local with no remote.
- Use only the human user's configured Git identity.
- Do not add AI attribution to commits or project documentation.
- Preserve separate documents rather than collapsing every purpose into one
  large file.
- Make the checkpoint and resume process explicit.

## Review Focus

- A new chat must be able to identify the exact current checkpoint.
- Planned behavior must not be represented as already implemented.
- Recovery instructions must avoid destructive Git commands by default.
- Learning documentation must distinguish general AI terms from selected
  AgentForge technology.
- Publication guidance must not accidentally authorize a public repository.

---

### Task 1: Initialize the local documentation repository

**Files:**

- Create: `.gitignore`
- Create: `AGENTS.md`

**Interfaces:**

- Consumes: User-configured Git author name and email.
- Produces: Repository-wide working rules and safe ignore patterns.

- [x] Verify Git is installed and the human author identity is configured.
- [x] Initialize a local repository without a remote.
- [x] Add secret, runtime-data, Python, and Node ignore patterns.
- [x] Add repository-wide reading and checkpoint instructions.

### Task 2: Create the project operating handbook

**Files:**

- Create: `README.md`
- Create: `ARCHITECTURE.md`
- Create: `CONSTRAINTS.md`
- Create: `FLOW.md`
- Create: `DECISIONS.md`
- Create: `TEST_CHECKLIST.md`
- Create: `ROLLBACK.md`

**Interfaces:**

- Consumes: Approved AgentForge design and architecture choice.
- Produces: Separate authoritative documents for overview, system design,
  constraints, flow, decisions, checks, and recovery.

- [x] Mark the project as planning-only.
- [x] Document component and data ownership.
- [x] Document support-report flow and state families.
- [x] Record approved decisions and rejected alternatives.
- [x] Define verification checklists without claiming unexecuted checks passed.
- [x] Define non-destructive Git and data recovery principles.

### Task 3: Create durable progress and focused task records

**Files:**

- Create: `PROJECT_PLAN.md`
- Create: `HANDOVER.md`
- Create: `docs/tasks/BUG_TEMPLATE.md`
- Create: `docs/tasks/FEATURE_TEMPLATE.md`

**Interfaces:**

- Consumes: Milestones 0–9 and current repository state.
- Produces: Long-lived milestone state, short-lived resume state, and reusable
  focused-task templates.

- [x] Add milestone checkboxes and acceptance criteria.
- [x] Add the end-of-session checkpoint protocol.
- [x] Record current completed work, limitations, checks, and exact next action.
- [x] Separate bug evidence from feature requirements.

### Task 4: Create the plain-language learning path

**Files:**

- Create: `docs/learning/README.md`
- Create: `docs/learning/01_PROJECT_JOURNEY.md`
- Create: `docs/learning/02_CONCEPTS_GLOSSARY.md`
- Create: `docs/learning/03_SYSTEM_WORKFLOW.md`

**Interfaces:**

- Consumes: Project architecture and the user's request for accessible
  explanations.
- Produces: A maintained learning sequence, glossary, and Mermaid diagrams.

- [x] Explain the purpose and build sequence in plain language.
- [x] Explain Temporal, durability, retries, approvals, Qwen, quantization, and
  local/cloud distinctions.
- [x] Add architecture, sequence, state, and recovery diagrams.
- [x] Label planned behavior as planned.

### Task 5: Verify and commit the documentation checkpoint

**Files:**

- Modify: `HANDOVER.md`
- Modify: `PROJECT_PLAN.md`

**Interfaces:**

- Consumes: All documentation created by Tasks 1–4.
- Produces: One coherent local Git checkpoint authored only by the configured
  human identity.

- [x] Check Markdown links and Mermaid fence balance.
- [x] Inspect `git diff --check` and `git status`.
- [x] Update handover with actual verification results.
- [x] Commit with message `docs: add AgentForge project handbook`.
- [x] Inspect the commit author and confirm there is no AI co-author trailer.
