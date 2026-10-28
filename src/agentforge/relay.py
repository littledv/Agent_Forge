"""Relay durable start intents from PostgreSQL to Temporal."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from datetime import timedelta
from typing import Protocol

from pydantic import ValidationError
from temporalio.client import Client
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.exceptions import WorkflowAlreadyStartedError

from agentforge.config import Settings
from agentforge.database import create_engine_and_sessionmaker
from agentforge.domain import ApprovalSignal, CancellationSignal
from agentforge.domain import WorkflowInput
from agentforge.multi_agent import (
    MultiAgentWorkflowInput,
    SUPPORT_REPORT_GRAPH_V2,
    TaskGraphLimits,
)
from agentforge.repository import (
    ClaimedSignal,
    ClaimedStart,
    OutboxRepository,
    SignalOutboxRepository,
)


class WorkflowStarter(Protocol):
    async def start(
        self,
        *,
        workflow_type: str,
        workflow_id: str,
        task_queue: str,
        input: WorkflowInput | MultiAgentWorkflowInput,
    ) -> None: ...


class WorkflowSignaler(Protocol):
    async def signal(
        self,
        *,
        workflow_id: str,
        signal_name: str,
        payload: ApprovalSignal | CancellationSignal,
    ) -> None: ...


@dataclass(frozen=True)
class RelayStats:
    claimed: int = 0
    started: int = 0
    retried: int = 0
    failed: int = 0


@dataclass(frozen=True)
class SignalRelayStats:
    claimed: int = 0
    sent: int = 0
    retried: int = 0
    failed: int = 0


@dataclass(frozen=True)
class ResolvedWorkflowStart:
    workflow_type: str
    task_queue: str
    input: WorkflowInput | MultiAgentWorkflowInput


class TemporalWorkflowStarter:
    def __init__(self, client: Client) -> None:
        self._client = client

    async def start(
        self,
        *,
        workflow_type: str,
        workflow_id: str,
        task_queue: str,
        input: WorkflowInput | MultiAgentWorkflowInput,
    ) -> None:
        await self._client.start_workflow(
            workflow_type,
            input,
            id=workflow_id,
            task_queue=task_queue,
        )


class TemporalWorkflowSignaler:
    def __init__(self, client: Client) -> None:
        self._client = client

    async def signal(
        self,
        *,
        workflow_id: str,
        signal_name: str,
        payload: ApprovalSignal | CancellationSignal,
    ) -> None:
        handle = self._client.get_workflow_handle(workflow_id)
        await handle.signal(signal_name, payload)


def _resolve_workflow_start(
    item: ClaimedStart, settings: Settings
) -> ResolvedWorkflowStart:
    payload = dict(item.payload)
    workflow_kind = payload.pop("workflow_kind", None)
    stored_limits = payload.pop("graph_limits", None)

    if workflow_kind is None:
        if stored_limits is not None:
            raise ValueError("legacy workflow payload must not contain graph limits")
        input = WorkflowInput.model_validate_json(
            json.dumps({**payload, "max_decisions": settings.max_decisions})
        )
        return ResolvedWorkflowStart(
            workflow_type="SupportReportWorkflow",
            task_queue=input.model_profile.task_queue,
            input=input,
        )

    if workflow_kind != "support-report-v2" or stored_limits is None:
        raise ValueError("invalid workflow kind and graph limit combination")
    limits = TaskGraphLimits.model_validate(stored_limits)
    if limits != SUPPORT_REPORT_GRAPH_V2:
        raise ValueError("stored graph limits do not match the approved graph")
    input = MultiAgentWorkflowInput.model_validate_json(
        json.dumps({**payload, "limits": stored_limits})
    )
    return ResolvedWorkflowStart(
        workflow_type="MultiAgentSupportReportWorkflow",
        task_queue=input.model_profile.task_queue,
        input=input,
    )


def _retry_delay(attempt_count: int) -> timedelta:
    return timedelta(seconds=min(2**attempt_count, 300))


async def relay_once(
    settings: Settings, starter: WorkflowStarter, limit: int = 10
) -> RelayStats:
    """Claim a bounded batch without holding a transaction across Temporal calls."""

    engine, sessions = create_engine_and_sessionmaker(settings)
    try:
        async with sessions() as session:
            claimed = await OutboxRepository.claim_batch(session, limit)

        started = retried = failed = 0
        for item in claimed:
            try:
                resolved = _resolve_workflow_start(item, settings)
            except (ValidationError, ValueError):
                async with sessions() as session:
                    await OutboxRepository.mark_permanent_failure(
                        session, item.id, item.run_id
                    )
                failed += 1
                continue

            try:
                await starter.start(
                    workflow_type=resolved.workflow_type,
                    workflow_id=item.workflow_id,
                    task_queue=resolved.task_queue,
                    input=resolved.input,
                )
            except WorkflowAlreadyStartedError as error:
                if error.workflow_id != item.workflow_id:
                    async with sessions() as session:
                        await OutboxRepository.record_failure(
                            session,
                            item.id,
                            type(error).__name__,
                            _retry_delay(item.attempt_count),
                        )
                    retried += 1
                    continue
            except Exception as error:
                async with sessions() as session:
                    await OutboxRepository.record_failure(
                        session,
                        item.id,
                        type(error).__name__,
                        _retry_delay(item.attempt_count),
                    )
                retried += 1
                continue

            async with sessions() as session:
                await OutboxRepository.mark_started(session, item.id)
            started += 1

        return RelayStats(
            claimed=len(claimed), started=started, retried=retried, failed=failed
        )
    finally:
        await engine.dispose()


def _signal_payload(item: ClaimedSignal) -> ApprovalSignal | CancellationSignal:
    encoded = json.dumps(item.payload)
    if item.signal_name == "approval_decided":
        return ApprovalSignal.model_validate_json(encoded)
    if item.signal_name == "cancellation_requested":
        return CancellationSignal.model_validate_json(encoded)
    raise ValueError("unsupported workflow signal name")


async def relay_signals_once(
    settings: Settings, signaler: WorkflowSignaler, limit: int = 10
) -> SignalRelayStats:
    """Claim and send a bounded signal batch without holding a DB transaction."""

    engine, sessions = create_engine_and_sessionmaker(settings)
    try:
        async with sessions() as session:
            claimed = await SignalOutboxRepository.claim_batch(session, limit)

        sent = retried = failed = 0
        for item in claimed:
            try:
                payload = _signal_payload(item)
            except (ValidationError, ValueError):
                async with sessions() as session:
                    await SignalOutboxRepository.mark_permanent_failure(
                        session, item.message_id
                    )
                failed += 1
                continue

            try:
                await signaler.signal(
                    workflow_id=item.workflow_id,
                    signal_name=item.signal_name,
                    payload=payload,
                )
            except Exception as error:
                async with sessions() as session:
                    await SignalOutboxRepository.record_failure(
                        session,
                        item.message_id,
                        type(error).__name__,
                        _retry_delay(item.attempt_count),
                    )
                retried += 1
                continue

            async with sessions() as session:
                await SignalOutboxRepository.mark_sent(session, item.message_id)
            sent += 1

        return SignalRelayStats(
            claimed=len(claimed), sent=sent, retried=retried, failed=failed
        )
    finally:
        await engine.dispose()


async def run_forever(settings: Settings) -> None:
    client = await Client.connect(
        settings.temporal_address,
        namespace=settings.temporal_namespace,
        data_converter=pydantic_data_converter,
    )
    starter = TemporalWorkflowStarter(client)
    signaler = TemporalWorkflowSignaler(client)
    while True:
        await relay_once(settings, starter)
        await relay_signals_once(settings, signaler)
        await asyncio.sleep(1)


def main() -> None:
    settings = Settings.from_env()
    asyncio.run(run_forever(settings), loop_factory=asyncio.SelectorEventLoop)
