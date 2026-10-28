from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select, update
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker
from temporalio.exceptions import WorkflowAlreadyStartedError

from agentforge.config import Settings
from agentforge.db_models import (
    AgentVersionRecord,
    ApprovalRecord,
    RunRecord,
    WorkflowSignalOutboxRecord,
    WorkflowStartOutboxRecord,
)
from agentforge.domain import (
    ApprovalDecision,
    ApprovalDecisionRequest,
    ApprovalSignal,
    ArtifactRef,
    CancellationSignal,
    DeliverReportAction,
    RunState,
    SubmitRun,
    WorkflowInput,
)
from agentforge.model_profiles import MOCK_MODEL_PROFILE, build_ollama_profile
from agentforge.multi_agent import MultiAgentWorkflowInput, SUPPORT_REPORT_GRAPH_V2
from agentforge.policy import authorize_delivery
from agentforge.relay import RelayStats, relay_once
import agentforge.relay as relay_module
from agentforge.repository import ApprovalRepository, OutboxRepository, RunRepository


def relay_settings(database_url: str) -> Settings:
    return Settings(
        database_url=database_url,
        temporal_address="127.0.0.1:7233",
        temporal_namespace="default",
        temporal_task_queue="agentforge-support-report-v1",
        ticket_root=Path("samples/tickets").resolve(),
        artifact_root=Path("artifacts").resolve(),
        max_decisions=4,
        max_ticket_bytes=65_536,
        max_total_ticket_bytes=262_144,
    )


class FakeStarter:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.calls: list[
            tuple[str, str, str, WorkflowInput | MultiAgentWorkflowInput]
        ] = []

    async def start(
        self,
        *,
        workflow_type: str,
        workflow_id: str,
        task_queue: str,
        input: WorkflowInput | MultiAgentWorkflowInput,
    ) -> None:
        self.calls.append((workflow_type, workflow_id, task_queue, input))
        if self.error is not None:
            raise self.error


class FakeSignaler:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.calls: list[tuple[str, str, ApprovalSignal | CancellationSignal]] = []

    async def signal(
        self,
        *,
        workflow_id: str,
        signal_name: str,
        payload: ApprovalSignal | CancellationSignal,
    ) -> None:
        self.calls.append((workflow_id, signal_name, payload))
        if self.error is not None:
            raise self.error


async def submit_pending_run(engine: AsyncEngine, key: str = "relay-001") -> UUID:
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions() as session:
        version_id = (
            await session.scalars(
                select(AgentVersionRecord.id).where(
                    AgentVersionRecord.version == 1
                )
            )
        ).one()
    async with sessions() as session:
        result = await RunRepository.submit(
            session,
            SubmitRun(
                submission_key=key,
                goal="Summarize tickets",
                ticket_files=("ticket-001.txt",),
            ),
            version_id,
        )
    return result.run.run_id


@pytest.mark.integration
async def test_legacy_outbox_defaults_to_v1_and_process_max_decisions(
    database_engine: AsyncEngine, test_database_url: str
) -> None:
    run_id = await submit_pending_run(database_engine)
    starter = FakeStarter()

    stats = await relay_once(relay_settings(test_database_url), starter)

    sessions = async_sessionmaker(database_engine, expire_on_commit=False)
    async with sessions() as session:
        outbox = (
            await session.scalars(select(WorkflowStartOutboxRecord))
        ).one()
    assert stats == RelayStats(claimed=1, started=1, retried=0, failed=0)
    assert starter.calls == [
        (
            "SupportReportWorkflow",
            f"agentforge/run/{run_id}",
            "agentforge-support-report-v1",
            WorkflowInput(
                run_id=run_id,
                goal="Summarize tickets",
                ticket_files=("ticket-001.txt",),
                max_decisions=4,
            ),
        )
    ]
    assert outbox.started_at is not None


@pytest.mark.integration
async def test_profile_bound_relay_uses_persisted_queue_not_process_setting(
    database_engine: AsyncEngine, test_database_url: str
) -> None:
    sessions = async_sessionmaker(database_engine, expire_on_commit=False)
    async with sessions() as session:
        version_id = (
            await session.scalars(
                select(AgentVersionRecord.id).where(
                    AgentVersionRecord.version == 1
                )
            )
        ).one()
    profile = build_ollama_profile(
        model_name="qwen3:4b-instruct-2507-q4_K_M",
        model_digest="a" * 64,
        context_tokens=8_192,
        max_output_tokens=2_048,
        max_prompt_chars=24_000,
        max_report_chars=16_384,
        max_repairs=2,
    )
    async with sessions() as session:
        submitted = await RunRepository.submit(
            session,
            SubmitRun(
                submission_key="relay-profile-queue",
                goal="Summarize tickets",
                ticket_files=("ticket-001.txt",),
                model_profile_id=profile.profile_id,
            ),
            version_id,
            model_profile=profile,
        )
    starter = FakeStarter()
    settings = relay_settings(test_database_url).model_copy(
        update={"temporal_task_queue": "mutable-wrong-queue"}
    )

    stats = await relay_once(settings, starter)

    assert stats.started == 1
    assert starter.calls[0][0] == "SupportReportWorkflow"
    assert starter.calls[0][1] == submitted.run.workflow_id
    assert starter.calls[0][2] == profile.task_queue
    assert starter.calls[0][3].model_profile == profile


@pytest.mark.integration
async def test_v2_outbox_uses_stored_limits_and_multi_agent_workflow_type(
    database_engine: AsyncEngine, test_database_url: str
) -> None:
    sessions = async_sessionmaker(database_engine, expire_on_commit=False)
    async with sessions() as session:
        version_id = (
            await session.scalars(
                select(AgentVersionRecord.id).where(
                    AgentVersionRecord.version == 2
                )
            )
        ).one()
    async with sessions() as session:
        submitted = await RunRepository.submit(
            session,
            SubmitRun(
                submission_key="relay-version-two",
                goal="Summarize tickets",
                ticket_files=("ticket-001.txt", "ticket-002.txt"),
            ),
            version_id,
            workflow_kind="support-report-v2",
            graph_limits=SUPPORT_REPORT_GRAPH_V2,
        )
    starter = FakeStarter()

    stats = await relay_once(relay_settings(test_database_url), starter)

    assert stats.started == 1
    assert starter.calls == [
        (
            "MultiAgentSupportReportWorkflow",
            submitted.run.workflow_id,
            "agentforge-support-report-v1",
            MultiAgentWorkflowInput(
                run_id=submitted.run.run_id,
                goal="Summarize tickets",
                ticket_files=("ticket-001.txt", "ticket-002.txt"),
                model_profile=MOCK_MODEL_PROFILE,
                limits=SUPPORT_REPORT_GRAPH_V2,
            ),
        )
    ]


@pytest.mark.integration
@pytest.mark.parametrize(
    "invalid_fields",
    [
        {"workflow_kind": "unknown"},
        {
            "workflow_kind": "support-report-v1",
            "graph_limits": SUPPORT_REPORT_GRAPH_V2.model_dump(mode="json"),
        },
        {"workflow_kind": "support-report-v2"},
    ],
)
async def test_invalid_or_mixed_workflow_kind_payload_fails_closed(
    database_engine: AsyncEngine,
    test_database_url: str,
    invalid_fields: dict[str, object],
) -> None:
    run_id = await submit_pending_run(database_engine, key="relay-invalid-kind")
    sessions = async_sessionmaker(database_engine, expire_on_commit=False)
    async with sessions.begin() as session:
        outbox = (
            await session.scalars(select(WorkflowStartOutboxRecord))
        ).one()
        outbox.payload = {**outbox.payload, **invalid_fields}

    stats = await relay_once(relay_settings(test_database_url), FakeStarter())

    async with sessions() as session:
        run = await session.get(RunRecord, run_id)
    assert stats == RelayStats(claimed=1, started=0, retried=0, failed=1)
    assert run is not None and run.state == RunState.FAILED.value


@pytest.mark.integration
async def test_retryable_start_error_schedules_bounded_backoff(
    database_engine: AsyncEngine, test_database_url: str
) -> None:
    await submit_pending_run(database_engine)
    starter = FakeStarter(RuntimeError("Temporal temporarily unavailable"))

    stats = await relay_once(relay_settings(test_database_url), starter)

    sessions = async_sessionmaker(database_engine, expire_on_commit=False)
    async with sessions() as session:
        outbox = (
            await session.scalars(select(WorkflowStartOutboxRecord))
        ).one()
    assert stats == RelayStats(claimed=1, started=0, retried=1, failed=0)
    assert outbox.attempt_count == 1
    assert outbox.claimed_at is None
    assert datetime.now(UTC) < outbox.available_at <= datetime.now(UTC) + timedelta(
        minutes=5
    )


@pytest.mark.integration
async def test_invalid_stored_input_permanently_fails_the_run(
    database_engine: AsyncEngine, test_database_url: str
) -> None:
    run_id = await submit_pending_run(database_engine)
    sessions = async_sessionmaker(database_engine, expire_on_commit=False)
    async with sessions.begin() as session:
        await session.execute(
            update(WorkflowStartOutboxRecord).values(payload={"run_id": str(run_id)})
        )

    stats = await relay_once(relay_settings(test_database_url), FakeStarter())

    async with sessions() as session:
        run = await session.get(RunRecord, run_id)
        outbox = (
            await session.scalars(select(WorkflowStartOutboxRecord))
        ).one()
    assert stats == RelayStats(claimed=1, started=0, retried=0, failed=1)
    assert run is not None and run.state == RunState.FAILED.value
    assert outbox.started_at is not None
    assert "goal" not in (outbox.last_error or "")


@pytest.mark.integration
async def test_already_started_recovers_after_database_mark_failure(
    database_engine: AsyncEngine,
    test_database_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_id = await submit_pending_run(database_engine)
    workflow_id = f"agentforge/run/{run_id}"
    original_mark_started = OutboxRepository.mark_started

    async def fail_mark(*args, **kwargs) -> None:
        raise OperationalError("UPDATE", {}, Exception("database interrupted"))

    monkeypatch.setattr(OutboxRepository, "mark_started", fail_mark)
    with pytest.raises(OperationalError):
        await relay_once(relay_settings(test_database_url), FakeStarter())

    sessions = async_sessionmaker(database_engine, expire_on_commit=False)
    async with sessions.begin() as session:
        await session.execute(
            update(WorkflowStartOutboxRecord).values(
                claimed_at=datetime.now(UTC) - timedelta(minutes=6)
            )
        )

    monkeypatch.setattr(OutboxRepository, "mark_started", original_mark_started)
    starter = FakeStarter(
        WorkflowAlreadyStartedError(workflow_id, "SupportReportWorkflow")
    )
    stats = await relay_once(relay_settings(test_database_url), starter)

    async with sessions() as session:
        outbox = (
            await session.scalars(select(WorkflowStartOutboxRecord))
        ).one()
    assert stats == RelayStats(claimed=1, started=1, retried=0, failed=0)
    assert outbox.started_at is not None


async def enqueue_approval_signal(engine: AsyncEngine, key: str) -> tuple[UUID, str]:
    run_id = await submit_pending_run(engine, key=key)
    artifact = ArtifactRef(
        path="reports/run/support-report.md",
        sha256="a" * 64,
        size_bytes=20,
        media_type="text/markdown",
    )
    authorization = authorize_delivery(
        run_id,
        DeliverReportAction(
            type="deliver_report", target="simulated-inbox", artifact=artifact
        ),
        artifact,
    )
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions() as session:
        await ApprovalRepository.create_pending(
            session,
            run_id=run_id,
            authorization=authorization,
            expires_at=datetime.now(UTC) + timedelta(minutes=5),
        )
    async with sessions() as session:
        await ApprovalRepository.decide(
            session,
            authorization.approval_id,
            ApprovalDecisionRequest(
                action_hash=authorization.action_hash,
                decision=ApprovalDecision.APPROVE,
            ),
        )
    return run_id, authorization.action_hash


@pytest.mark.integration
async def test_pending_signal_is_relayed_with_strict_payload(
    database_engine: AsyncEngine, test_database_url: str
) -> None:
    assert hasattr(relay_module, "relay_signals_once")
    run_id, action_hash = await enqueue_approval_signal(
        database_engine, "signal-relay"
    )
    signaler = FakeSignaler()

    stats = await relay_module.relay_signals_once(
        relay_settings(test_database_url), signaler
    )

    assert stats == relay_module.SignalRelayStats(
        claimed=1, sent=1, retried=0, failed=0
    )
    assert len(signaler.calls) == 1
    workflow_id, signal_name, payload = signaler.calls[0]
    assert workflow_id == f"agentforge/run/{run_id}"
    assert signal_name == "approval_decided"
    assert isinstance(payload, ApprovalSignal)
    assert payload.action_hash == action_hash
    sessions = async_sessionmaker(database_engine, expire_on_commit=False)
    async with sessions() as session:
        outbox = (await session.scalars(select(WorkflowSignalOutboxRecord))).one()
    assert outbox.sent_at is not None


@pytest.mark.integration
async def test_signal_retry_is_bounded_and_redacts_error_text(
    database_engine: AsyncEngine, test_database_url: str
) -> None:
    await enqueue_approval_signal(database_engine, "signal-retry")
    stats = await relay_module.relay_signals_once(
        relay_settings(test_database_url),
        FakeSignaler(RuntimeError("secret receiver detail")),
    )

    sessions = async_sessionmaker(database_engine, expire_on_commit=False)
    async with sessions() as session:
        outbox = (await session.scalars(select(WorkflowSignalOutboxRecord))).one()
    assert stats == relay_module.SignalRelayStats(
        claimed=1, sent=0, retried=1, failed=0
    )
    assert outbox.attempt_count == 1
    assert outbox.claimed_at is None
    assert outbox.last_error == "RuntimeError"
    assert datetime.now(UTC) < outbox.available_at <= datetime.now(UTC) + timedelta(
        minutes=5
    )


@pytest.mark.integration
async def test_invalid_signal_payload_is_permanently_failed(
    database_engine: AsyncEngine, test_database_url: str
) -> None:
    await enqueue_approval_signal(database_engine, "signal-invalid")
    sessions = async_sessionmaker(database_engine, expire_on_commit=False)
    async with sessions.begin() as session:
        await session.execute(
            update(WorkflowSignalOutboxRecord).values(payload={"message_id": "bad"})
        )

    stats = await relay_module.relay_signals_once(
        relay_settings(test_database_url), FakeSignaler()
    )

    async with sessions() as session:
        outbox = (await session.scalars(select(WorkflowSignalOutboxRecord))).one()
    assert stats == relay_module.SignalRelayStats(
        claimed=1, sent=0, retried=0, failed=1
    )
    assert outbox.sent_at is not None
    assert "message_id" not in (outbox.last_error or "")


@pytest.mark.integration
async def test_signal_is_resent_unchanged_after_mark_sent_failure(
    database_engine: AsyncEngine,
    test_database_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await enqueue_approval_signal(database_engine, "signal-reclaim")
    original_mark_sent = relay_module.SignalOutboxRepository.mark_sent

    async def fail_mark(*args, **kwargs) -> None:
        raise OperationalError("UPDATE", {}, Exception("database interrupted"))

    monkeypatch.setattr(relay_module.SignalOutboxRepository, "mark_sent", fail_mark)
    first_signaler = FakeSignaler()
    with pytest.raises(OperationalError):
        await relay_module.relay_signals_once(
            relay_settings(test_database_url), first_signaler
        )

    sessions = async_sessionmaker(database_engine, expire_on_commit=False)
    async with sessions.begin() as session:
        await session.execute(
            update(WorkflowSignalOutboxRecord).values(
                claimed_at=datetime.now(UTC) - timedelta(minutes=6)
            )
        )
    monkeypatch.setattr(
        relay_module.SignalOutboxRepository, "mark_sent", original_mark_sent
    )
    second_signaler = FakeSignaler()
    stats = await relay_module.relay_signals_once(
        relay_settings(test_database_url), second_signaler
    )

    assert stats.sent == 1
    assert len(first_signaler.calls) == len(second_signaler.calls) == 1
    assert first_signaler.calls[0] == second_signaler.calls[0]
