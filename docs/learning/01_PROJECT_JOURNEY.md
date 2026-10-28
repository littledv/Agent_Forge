# AgentForge Project Journey

## The project in plain language

An ordinary script remembers progress only in its process memory. If the
process dies, the script may forget what it completed and repeat work. That is
dangerous when a step sends an email, charges a card, or publishes something.

AgentForge treats an AI-driven task as a durable run. A separate execution
system records which decisions were made and which work was requested. Workers
can disappear and return without becoming the owner of progress.

## Why the first example is intentionally small

The first agent reads local sample support tickets and writes a report. This is
not meant to demonstrate advanced artificial intelligence. It is meant to make
failure behavior visible:

- Can we resume after a worker crash?
- Does an approval survive a restart?
- Can retrying delivery avoid a duplicate?
- Can we explain the final outcome from durable records?

If those answers are unreliable, adding more agents or a larger model only
makes the system harder to understand.

## Stage-by-stage journey

### Stage 0: Agree on the rules

We define system boundaries, state transitions, failure classifications,
security limits, and what success means. No product code is required.

### Stage 1: Make one complete run work

A deterministic mock model chooses scripted actions. Temporal coordinates the
run, PostgreSQL stores application records, and a report is written to the
artifact directory. The goal is a complete vertical slice, not feature breadth.

### Stage 2: Break the worker

We deliberately stop a worker during a run and verify that another worker can
continue. This demonstrates why execution history must not live only in process
memory.

### Stage 3: Require permission and survive ambiguity

Delivery pauses for approval. The simulated inbox records a stable operation
identifier. We crash after its database commit but before the worker reports
success, then prove the retry does not create a second delivery.

### Stage 4: Make the system understandable

The dashboard displays states, attempts, approvals, artifacts, errors, and
timing. It is a view of durable records, not the execution engine itself.

### Stage 5 and later

Only after the deterministic system is reliable do we consider a real model,
multiple agent roles, an SDK, public deployment, or advanced operations.

## What to record after each stage

1. What now works.
2. Which files and components changed.
3. How to run the demonstration.
4. Which commands and tests actually passed.
5. Known limitations.
6. The exact next checkpoint.
