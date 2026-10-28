# AgentForge Concepts Glossary

This glossary will grow only when the project introduces a concept.

## Agent

A versioned definition containing instructions, allowed tools, limits, and
schemas. It is a recipe, not a running process.

## Run

One execution of one agent version for one submitted goal. A run has durable
identity, state, steps, artifacts, approvals, and a final outcome.

## Workflow

The durable coordinator in Temporal. It decides which Activity comes next and
can reconstruct its state from recorded history after a worker restarts.

## Activity

A unit of real-world work such as reading a file, calling a model, updating a
database, or contacting another service. Activities may be retried, so their
side effects need careful identifiers and deduplication.

## Worker

A replaceable process that asks Temporal for work and executes Workflow or
Activity code. A worker is not the durable source of truth.

## Determinism

Given the same recorded Workflow history, the coordinator must make the same
decisions. Uncontrolled time, randomness, file reads, database queries, and
network calls therefore belong outside Workflow code, normally in Activities.

## Idempotency

The property that repeating the same logical request has the same observable
result as performing it once. A stable operation ID lets the receiver recognize
a retry.

## Deduplication

Remembering previously processed identifiers so a retry does not create a
second effect. The simulated inbox enforces this with durable storage and a
unique constraint.

## At-least-once execution

An Activity may run more than once when completion acknowledgement is lost.
This is why Activity code cannot assume one physical attempt per logical step.

## Exactly once

A phrase that is often overused. AgentForge can prove one observable inbox
delivery when the receiver deduplicates a stable operation ID. It cannot promise
exactly-once behavior for every external system.

## Durable approval

A human decision stored in persistent data and bound to one exact action. A
changed destination, artifact, or argument requires a new approval.

## Projection

A database representation optimized for the dashboard. It reflects Workflow
events but may briefly lag behind Temporal. It must be repairable and must not
become a second execution engine.

## Heartbeat

A progress signal sent by a long-running Activity. It helps Temporal notice
lost workers and may allow work to resume from a recorded Activity checkpoint.

## Backoff

Waiting progressively longer between retries so a failing dependency is not
hammered continuously.

## Structured model output

Model output that must match a schema—for example, a specific action name and
validated arguments—instead of being treated as arbitrary executable text.

## Token

A chunk of text processed by a language model. Token counts affect context
limits and often cost, but they do not map perfectly to words.

## Qwen

Qwen is a family of language models originally developed by Alibaba. It may be
used through local runtimes or hosted services depending on the model and
license. AgentForge has not selected Qwen; it is included here because it is a
common term in local-model discussions.

## Quantization

Model weights are numbers. Quantization stores or computes those numbers with
lower precision to reduce memory and often improve local inference speed. The
tradeoff can include reduced quality or different hardware/runtime support.

## 4-bit quantization

A compressed representation that uses roughly four bits per weight instead of
common 16-bit or 32-bit formats. It can make a model fit into much less RAM or
VRAM, but total memory is not simply model-size times four bits: runtime caches,
activations, metadata, context length, and software overhead also consume
memory. AgentForge does not need a quantized model in its initial milestones.

## Local model versus cloud model

A local model runs on hardware you control and can work without sending prompts
to a provider, although downloading it initially may require internet access. A
cloud model is accessed through an external API. Running AgentForge locally
with a cloud model is not fully offline.

## WSL2

Windows Subsystem for Linux version 2 provides a lightweight Linux virtual
machine. Docker Desktop commonly uses it to run Linux containers on Windows.
PowerShell can still be the main user interface for this project.

## Container versus security sandbox

A container packages a process and its dependencies with isolation features.
It is useful operationally, but Docker alone is not a complete security design
for running arbitrary hostile code.
