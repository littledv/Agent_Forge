# Milestone 4: A Guided Durable-Run Dashboard

Milestone 4 turns AgentForge's durable records into a browser workflow without
moving authority into the browser. A local operator can submit a support report,
watch its logical steps and retries, inspect an exact delivery approval, approve
or reject it, request cancellation, and read the verified report.

## One screen, two authorities

The dashboard reads application state from FastAPI and PostgreSQL. It links to
the Temporal UI for execution history, but it never asks Temporal to build the
application projection and never schedules work itself.

```text
browser -> same-origin /backend rewrite -> FastAPI -> PostgreSQL projection
   |                                              |
   +---- local Temporal UI link ------------------+-> Temporal history
```

This preserves the existing rule: PostgreSQL owns application records; Temporal
owns Workflow history and recovery.

## Polling without races

The recent list polls after each request settles, then waits two seconds. The
selected run does the same with a one-second interval. There is no interval that
can stack a second request on top of a slow first request.

Every selected-run request belongs to a generation. Changing selection aborts
the old request and advances the generation. Even if an old network response
arrives late, its generation no longer matches and it cannot overwrite the new
selection. Terminal run states stop selected-run polling.

Projection age is presentation evidence, not execution truth. A nonterminal
projection older than ten seconds says `Projection may be delayed`; it is not
colored or described as a failed Workflow.

## Mutations reread the winner

Approve, Reject, and Cancel share one lock. Approval sends the exact displayed
action hash. Cancellation first states that a committed delivery cannot be
undone. The UI does not invent a successful state after either request.

Instead, it waits for the API mutation and then rereads the run detail. A `409`
means another durable state won; the same refresh displays that winner. Controls
remain disabled through the refresh so the operator cannot race the mutation
with another local click.

## Safe report inspection

The browser never supplies a file path to read. It asks for the selected run's
artifact. FastAPI uses the artifact reference already recorded for that run and
checks root containment, ordinary-file status, media type, byte length, SHA-256,
strict UTF-8, and content length.

The UI verifies that returned metadata still matches the displayed reference,
caches one successful response for that reference, and renders the report in a
plain-text `<pre>` block. Script, HTML, and Markdown syntax remain literal.

## What the real proof established

The native harness starts the API, relay, inbox, Worker, and dashboard on
loopback, runs one unintercepted Chromium journey, queries both PostgreSQL
databases for the resulting identifiers, and stops only the processes it
started. The final run had one approval, one AgentForge delivery, and one inbox
row.

The restart proof stopped all five application processes at
`WAITING_APPROVAL`. Ports 3000, 8000, and 8001 were closed while PostgreSQL and
Temporal remained available. After restart, Chromium reopened the same run and
action hash, approved it, displayed verified report content, and observed one
receiver delivery.

This proves the local guided workflow and ordinary restart boundary. It does
not add authentication, public access, streaming, or a real model provider;
those remain later, separately approved work.
